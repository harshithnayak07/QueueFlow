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

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.queue_session import QueueSession
from app.models.token import Token
from app.schemas.token import (
    PublicTokenCreatedOut,
    PublicTokenTrackingOut,
    TokenCreate,
)
from app.services.queue_service import (
    TERMINAL_TOKEN_STATUSES,
    recommended_return_at,
    token_people_ahead,
)
from app.services.waiting_time_service import (
    ACTIVE_TOKEN_STATUSES,
    ESTIMATE_NOTE,
    estimate_wait_minutes,
)

router = APIRouter(prefix="/api/public", tags=["public"])


def _earlier_active_count(db: Session, session_id: int, token_number: int) -> int:
    return (
        db.query(Token)
        .filter(
            Token.session_id == session_id,
            Token.status.in_(ACTIVE_TOKEN_STATUSES),
            Token.token_number < token_number,
        )
        .count()
    )


def _live_estimate(
    queue_session: QueueSession, own_status: str, earlier_active: int
) -> tuple[int, float | None, datetime | None]:
    """(people_ahead, estimated_wait, recommended_return_at)."""
    ahead = token_people_ahead(
        queue_session.physical_people_remaining, earlier_active, own_status
    )
    wait = estimate_wait_minutes(
        ahead,
        queue_session.average_service_minutes,
        queue_session.active_counters,
    )
    if own_status in TERMINAL_TOKEN_STATUSES:
        # Nothing to return for: the visit is over (or cancelled).
        return ahead, wait, None
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

    # Next human-facing number within this session (1-based). The unique
    # constraint uq_token_session_number is the final arbiter under races.
    top = (
        db.query(func.max(Token.token_number))
        .filter(Token.session_id == session_id)
        .scalar()
    )
    next_number = (top or 0) + 1
    earlier_active = (
        db.query(Token)
        .filter(
            Token.session_id == session_id,
            Token.status.in_(ACTIVE_TOKEN_STATUSES),
        )
        .count()
    )

    token = Token(
        session_id=session_id,
        token_number=next_number,
        customer_contact=data.customer_contact,
        status="waiting",
    )
    db.add(token)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not issue a token number; please retry.",
        ) from exc
    db.refresh(token)

    ahead, wait, return_at = _live_estimate(
        queue_session, token.status, earlier_active
    )
    return PublicTokenCreatedOut(
        session_id=session_id,
        token_number=token.token_number,
        tracking_id=token.tracking_id,
        status=token.status,
        people_ahead=ahead,
        estimated_wait_minutes=wait,
        estimate_note=ESTIMATE_NOTE,
        recommended_return_at=return_at,
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
    earlier_active = _earlier_active_count(
        db, token.session_id, token.token_number
    )
    ahead, wait, return_at = _live_estimate(
        queue_session, token.status, earlier_active
    )
    return PublicTokenTrackingOut(
        session_id=token.session_id,
        token_number=token.token_number,
        status=token.status,
        people_ahead=ahead,
        estimated_wait_minutes=wait,
        estimate_note=ESTIMATE_NOTE,
        recommended_return_at=return_at,
    )
