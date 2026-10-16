"""S3: owner queue-session management + live wait estimates.

All routes require owner auth and the get_owned_session guard (404 for
foreign/missing sessions — no existence leak). Nothing here auto-activates
the virtual queue; activation is an explicit owner action.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner, get_owned_session
from app.database.session import get_db
from app.models.owner import Owner
from app.models.queue_session import QueueSession
from app.models.token import Token
from app.schemas.queue import (
    SessionCreate,
    SessionOut,
    SessionSettingsUpdate,
    SessionStatusOut,
)
from app.schemas.token import OwnerTokenOut
from app.services.queue_service import expire_overdue_tokens, progress_session
from app.services.waiting_time_service import (
    ACTIVE_TOKEN_STATUSES,
    ESTIMATE_NOTE,
    VIRTUAL_QUEUE_THRESHOLD_DEFAULT,
    estimate_wait_minutes,
    recommend_virtual_queue,
)

router = APIRouter(prefix="/api/queue-sessions", tags=["queue-sessions"])


def _waiting_virtual_count(db: Session, session_id: int) -> int:
    return (
        db.query(Token)
        .filter(
            Token.session_id == session_id,
            Token.status.in_(ACTIVE_TOKEN_STATUSES),
        )
        .count()
    )


def _build_status(
    db: Session,
    queue_session: QueueSession,
    threshold: int = VIRTUAL_QUEUE_THRESHOLD_DEFAULT,
) -> SessionStatusOut:
    # E2 lazy expiry: overdue called tokens stop counting before any math.
    expire_overdue_tokens(db, queue_session.id)
    virtual_count = _waiting_virtual_count(db, queue_session.id)
    # Session view = whole-queue crowd estimate from the confirmed
    # physical count (7 x 5 / 1 = 35). Per-token tracking pages use
    # positional remaining-work instead — different question (whole queue
    # vs my turn), same live backend data, never added together.
    ahead = queue_session.physical_people_remaining
    wait = estimate_wait_minutes(
        ahead,
        queue_session.average_service_minutes,
        queue_session.active_counters,
    )
    recommended, reason = recommend_virtual_queue(
        ahead,
        queue_session.virtual_queue_enabled,
        threshold,
    )
    return SessionStatusOut(
        session=SessionOut.model_validate(queue_session),
        people_ahead=ahead,
        waiting_virtual_count=virtual_count,
        estimated_wait_minutes=wait,
        estimate_note=ESTIMATE_NOTE,
        virtual_queue_recommended=recommended,
        recommendation_reason=reason,
        recommendation_threshold=threshold,
        token_capacity=queue_session.physical_people_remaining,
        tokens_issued=queue_session.tokens_issued,
        tokens_remaining=max(
            0, queue_session.physical_people_remaining - queue_session.tokens_issued
        ),
    )


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def create_session(
    data: SessionCreate,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
) -> QueueSession:
    queue_session = QueueSession(
        owner_id=current_owner.id,
        active_counters=data.active_counters,
        average_service_minutes=data.average_service_minutes,
        physical_people_remaining=data.physical_people_remaining,
    )
    db.add(queue_session)
    db.commit()
    db.refresh(queue_session)
    return queue_session


@router.get("", response_model=list[SessionOut])
def list_sessions(
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
) -> list[QueueSession]:
    return (
        db.query(QueueSession)
        .filter(QueueSession.owner_id == current_owner.id)
        .order_by(QueueSession.id.desc())
        .all()
    )


@router.get("/{session_id}/status", response_model=SessionStatusOut)
def session_status(
    session_id: int,
    threshold: int = Query(
        default=VIRTUAL_QUEUE_THRESHOLD_DEFAULT,
        ge=1,
        description="People-ahead count that triggers the advisory recommendation (E4)",
    ),
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
) -> SessionStatusOut:
    queue_session = get_owned_session(session_id, db, current_owner)
    # Lazy reconcile so the numbers reflect elapsed service time even if
    # the background sweeper hasn't run yet (idempotent; commits only on
    # actual transitions — same precedent as the expiry sweep).
    progress_session(db, queue_session)
    return _build_status(db, queue_session, threshold)


@router.patch("/{session_id}/settings", response_model=SessionOut)
def update_settings(
    session_id: int,
    data: SessionSettingsUpdate,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
) -> QueueSession:
    queue_session = get_owned_session(session_id, db, current_owner)
    patch = data.model_dump(exclude_unset=True)
    for field, value in patch.items():
        setattr(queue_session, field, value)
    if "physical_people_remaining" in patch:
        # New snapshot epoch: capacity becomes the newly confirmed count
        # and issuance restarts from the currently active (waiting/called)
        # tokens, which carry over as part of the confirmed queue.
        active_now = (
            db.query(Token)
            .filter(
                Token.session_id == queue_session.id,
                Token.status.in_(ACTIVE_TOKEN_STATUSES),
            )
            .count()
        )
        queue_session.tokens_issued = active_now
    db.commit()
    db.refresh(queue_session)
    return queue_session


@router.post("/{session_id}/activate-virtual-queue", response_model=SessionOut)
def activate_virtual_queue(
    session_id: int,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
) -> QueueSession:
    queue_session = get_owned_session(session_id, db, current_owner)
    if queue_session.status == "closed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot activate the virtual queue on a closed session",
        )
    # Idempotent: re-activating an active session is a no-op success.
    queue_session.virtual_queue_enabled = True
    # Automatic progression starts now: the first eligible token(s) get
    # serving windows immediately (up to active_counters of them).
    progress_session(db, queue_session)
    db.commit()
    db.refresh(queue_session)
    return queue_session


@router.post("/{session_id}/deactivate-virtual-queue", response_model=SessionOut)
def deactivate_virtual_queue(
    session_id: int,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
) -> QueueSession:
    """E3: stop new joins WITHOUT closing the session or touching tokens.

    Distinct from closing (PATCH settings status): the session stays open,
    existing tokens keep tracking, and the owner can re-activate any time.
    Idempotent: deactivating an inactive session is a no-op success.
    """
    queue_session = get_owned_session(session_id, db, current_owner)
    if queue_session.status == "closed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot change the virtual queue on a closed session",
        )
    queue_session.virtual_queue_enabled = False
    db.commit()
    db.refresh(queue_session)
    return queue_session


@router.get("/{session_id}/qr")
def session_qr(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
):
    """D2: scannable QR code (PNG) encoding this session's real join URL.

    Owner-only like all session routes: foreign/missing sessions are a
    bare 404, so session ids cannot be probed for QR codes. The encoded
    URL is the same join.html link the dashboard shows and copies.
    """
    queue_session = get_owned_session(session_id, db, current_owner)
    join_url = f"{request.base_url}join.html?session={queue_session.id}"
    try:
        import qrcode
        from io import BytesIO

        img = qrcode.make(join_url)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"QR generation is unavailable ({exc}).",
        ) from exc


@router.get("/{session_id}/tokens", response_model=list[OwnerTokenOut])
def list_tokens(
    session_id: int,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
) -> list[Token]:
    """Owner token list for the dashboard (S5 addition: the proposed API
    list had no way to SEE virtual tokens). Same ownership guard as the
    other session routes; ordered by token_number. Sweeps overdue called
    tokens first so the list never shows a stale "called" past expiry."""
    queue_session = get_owned_session(session_id, db, current_owner)
    # progress_session ends with the expiry sweep; no separate call needed.
    progress_session(db, queue_session)
    return (
        db.query(Token)
        .filter(Token.session_id == queue_session.id)
        .order_by(Token.token_number.asc())
        .all()
    )
