"""S3: owner queue-session management + live wait estimates.

All routes require owner auth and the get_owned_session guard (404 for
foreign/missing sessions — no existence leak). Nothing here auto-activates
the virtual queue; activation is an explicit owner action.
"""

from fastapi import APIRouter, Depends, HTTPException, status
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
from app.services.waiting_time_service import (
    ACTIVE_TOKEN_STATUSES,
    ESTIMATE_NOTE,
    VIRTUAL_QUEUE_THRESHOLD_DEFAULT,
    estimate_wait_minutes,
    recommend_virtual_queue,
    session_people_ahead,
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


def _build_status(db: Session, queue_session: QueueSession) -> SessionStatusOut:
    virtual_count = _waiting_virtual_count(db, queue_session.id)
    ahead = session_people_ahead(
        queue_session.physical_people_remaining, virtual_count
    )
    wait = estimate_wait_minutes(
        ahead,
        queue_session.average_service_minutes,
        queue_session.active_counters,
    )
    recommended, reason = recommend_virtual_queue(
        ahead,
        queue_session.virtual_queue_enabled,
        VIRTUAL_QUEUE_THRESHOLD_DEFAULT,
    )
    return SessionStatusOut(
        session=SessionOut.model_validate(queue_session),
        people_ahead=ahead,
        waiting_virtual_count=virtual_count,
        estimated_wait_minutes=wait,
        estimate_note=ESTIMATE_NOTE,
        virtual_queue_recommended=recommended,
        recommendation_reason=reason,
        recommendation_threshold=VIRTUAL_QUEUE_THRESHOLD_DEFAULT,
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
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
) -> SessionStatusOut:
    queue_session = get_owned_session(session_id, db, current_owner)
    return _build_status(db, queue_session)


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
    db.commit()
    db.refresh(queue_session)
    return queue_session


@router.get("/{session_id}/tokens", response_model=list[OwnerTokenOut])
def list_tokens(
    session_id: int,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
) -> list[Token]:
    """Owner token list for the dashboard (S5 addition: the proposed API
    list had no way to SEE virtual tokens). Same ownership guard as the
    other session routes; ordered by token_number."""
    queue_session = get_owned_session(session_id, db, current_owner)
    return (
        db.query(Token)
        .filter(Token.session_id == queue_session.id)
        .order_by(Token.token_number.asc())
        .all()
    )
