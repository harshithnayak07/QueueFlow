"""S5: public customer token endpoints (no auth).

POST /api/public/queue-sessions/{session_id}/tokens — join the virtual
    queue (FR-09/FR-10). Requires an open session with the virtual queue
    enabled; otherwise 403/409. Issues the next sequential token_number
    within the session plus an unguessable tracking_id (model default).
GET /api/public/tokens/{tracking_id} — tracking page payload (FR-11):
    token number, status, people ahead, approximate wait, recommended
    return time. Estimates are recomputed live on every call (FR-13).

Privacy: neither response carries customer_contact or internal token
ids. Unknown tracking ids are a bare 404.
"""

from datetime import datetime
import threading

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.queue_session import QueueSession
from app.models.token import TOKEN_STATUS_CALLED, Token
from app.schemas.token import (
    PublicTokenCreatedOut,
    PublicTokenTrackingOut,
    TokenCreate,
)
from app.services.queue_service import (
    TERMINAL_TOKEN_STATUSES,
    expire_overdue_tokens,
    progress_session,
    queue_ahead_estimate,
    recommended_return_at,
    validity_for_estimate,
)
from app.services.waiting_time_service import (
    ACTIVE_TOKEN_STATUSES,
    ESTIMATE_NOTE,
)

router = APIRouter(prefix="/api/public", tags=["public"])


# Per-session join locks: the capacity check + insert + commit in
# create_token runs inside one of these, so concurrent joins in this
# process cannot both pass the check. Single-process MVP scope — a
# multi-worker deploy needs a DB-level guard instead.
_JOIN_LOCKS: dict[int, threading.Lock] = {}
_JOIN_LOCKS_GUARD = threading.Lock()


def _session_join_lock(session_id: int) -> threading.Lock:
    with _JOIN_LOCKS_GUARD:
        lock = _JOIN_LOCKS.get(session_id)
        if lock is None:
            lock = threading.Lock()
            _JOIN_LOCKS[session_id] = lock
        return lock


def _earlier_active_rows(
    db: Session, session_id: int, token_number: int
) -> list[tuple[str, object]]:
    """(status, service_ends_at) of active tokens ahead of one token."""
    return [
        (status, ends_at)
        for status, ends_at in db.query(Token.status, Token.service_ends_at)
        .filter(
            Token.session_id == session_id,
            Token.status.in_(ACTIVE_TOKEN_STATUSES),
            Token.token_number < token_number,
        )
        .order_by(Token.token_number.asc())
        .all()
    ]


def _live_estimate(
    db: Session, queue_session: QueueSession, token: Token
) -> tuple[int, float | None, datetime | None]:
    """(people_ahead, estimated_wait, recommended_return_at) via the one
    shared queue_ahead_estimate rule: eligible customers ahead, remaining
    service of in-service ones plus full durations for the rest."""
    if token.status in TERMINAL_TOKEN_STATUSES:
        # Nothing to return for: the visit is over (or cancelled).
        ahead, wait = queue_ahead_estimate(
            [],
            queue_session.average_service_minutes,
            queue_session.active_counters,
        )
        return ahead, wait, None
    earlier = _earlier_active_rows(db, token.session_id, token.token_number)
    ahead, wait = queue_ahead_estimate(
        earlier,
        queue_session.average_service_minutes,
        queue_session.active_counters,
        None,
        token.service_ends_at if token.status == TOKEN_STATUS_CALLED else None,
    )
    return ahead, wait, recommended_return_at(wait)


@router.post(
    "/queue-sessions/{session_id}/tokens",
    response_model=PublicTokenCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
def create_token(
    session_id: int,
    data: TokenCreate,
    db: Session = Depends(get_db),
) -> PublicTokenCreatedOut:
    queue_session = db.get(QueueSession, session_id)
    if queue_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Queue session not found",
        )
    if queue_session.status == "closed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This queue session is closed and no longer takes tokens.",
        )
    if not queue_session.virtual_queue_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The virtual queue is not active for this session yet.",
        )

    # E2 lazy expiry: overdue called tokens stop counting as ahead, so a
    # newcomer's estimate is correct even if nobody refreshed lately.
    expire_overdue_tokens(db, session_id)

    # Core capacity rule: the confirmed physical count IS the maximum
    # virtual tokens per snapshot epoch. Served/cancelled/expired tokens
    # do NOT free slots — only a new owner Apply starts a new epoch.
    # The whole check-and-insert below runs under a per-session lock so
    # concurrent joins cannot slip past the limit (single-process MVP;
    # multi-worker deploys need a DB-level guard instead).
    with _session_join_lock(session_id):
        db.refresh(queue_session)
        capacity = queue_session.physical_people_remaining
        if capacity <= 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The queue capacity is not confirmed yet. "
                "Please wait for the counter to confirm the queue.",
            )
        # One token per phone number per session (contact already in
        # canonical form from TokenCreate). Any existing row — waiting,
        # called, served, cancelled or expired — blocks a second token.
        # Inside the join lock with the insert below, so concurrent
        # same-number joins cannot both pass.
        existing = (
            db.query(Token)
            .filter(
                Token.session_id == session_id,
                Token.customer_contact == data.customer_contact,
            )
            .first()
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"This phone number already has token "
                f"#{existing.token_number} in this session.",
            )
        if queue_session.tokens_issued >= capacity:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Queue is full for this session. "
                "No more tokens can be issued.",
            )

        # Next human-facing number within this session (1-based). The unique
        # constraint uq_token_session_number stays as the final arbiter.
        top = (
            db.query(func.max(Token.token_number))
            .filter(Token.session_id == session_id)
            .scalar()
        )
        next_number = (top or 0) + 1

        # Per-token validity frozen at creation: it IS the estimated wait
        # just computed for this newcomer (whole minutes, min 1), so the
        # number shown on the join page and the enforced window are one
        # and the same. Uncomputable waits fall back to the session setting.
        earlier_rows = [
            (status, ends_at)
            for status, ends_at in db.query(Token.status, Token.service_ends_at)
            .filter(
                Token.session_id == session_id,
                Token.status.in_(ACTIVE_TOKEN_STATUSES),
            )
            .order_by(Token.token_number.asc())
            .all()
        ]
        _, join_wait = queue_ahead_estimate(
            earlier_rows,
            queue_session.average_service_minutes,
            queue_session.active_counters,
        )
        validity = validity_for_estimate(
            join_wait, queue_session.validity_minutes
        )

        token = Token(
            session_id=session_id,
            token_number=next_number,
            customer_contact=data.customer_contact,
            status="waiting",
            validity_minutes=validity,
        )
        db.add(token)
        # Epoch consumption: every issuance counts, even if this token is
        # later served/cancelled/expired. Only a new owner Apply resets it.
        queue_session.tokens_issued = queue_session.tokens_issued + 1
        try:
            db.commit()
        except IntegrityError as exc:
            # Lost a race despite the lock (e.g. multi-worker deploy):
            # re-read to report WHICH rule fired instead of guessing.
            db.rollback()
            dupe = (
                db.query(Token)
                .filter(
                    Token.session_id == session_id,
                    Token.customer_contact == data.customer_contact,
                )
                .first()
            )
            if dupe is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"This phone number already has token "
                    f"#{dupe.token_number} in this session.",
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Could not issue a token number; please retry.",
            ) from exc
        db.refresh(token)

    ahead, wait, return_at = _live_estimate(db, queue_session, token)
    return PublicTokenCreatedOut(
        session_id=session_id,
        token_number=token.token_number,
        tracking_id=token.tracking_id,
        status=token.status,
        people_ahead=ahead,
        estimated_wait_minutes=wait,
        estimate_note=ESTIMATE_NOTE,
        recommended_return_at=return_at,
        validity_minutes=token.validity_minutes,
    )


@router.get("/tokens/{tracking_id}", response_model=PublicTokenTrackingOut)
def track_token(
    tracking_id: str,
    db: Session = Depends(get_db),
) -> PublicTokenTrackingOut:
    token = (
        db.query(Token).filter(Token.tracking_id == tracking_id).first()
    )
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )
    queue_session = db.get(QueueSession, token.session_id)
    if queue_session is None:  # session deleted after issue; nothing to track
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )
    # E2 lazy expiry before computing this token's own numbers, so an
    # overdue "called" flips to "expired" on the next poll. Progression
    # runs first so a finished service wins over the validity sweep.
    progress_session(db, queue_session)
    db.refresh(token)
    ahead, wait, return_at = _live_estimate(db, queue_session, token)
    return PublicTokenTrackingOut(
        session_id=token.session_id,
        token_number=token.token_number,
        status=token.status,
        people_ahead=ahead,
        estimated_wait_minutes=wait,
        estimate_note=ESTIMATE_NOTE,
        recommended_return_at=return_at,
        validity_minutes=(
            token.validity_minutes or queue_session.validity_minutes
        ),
    )
