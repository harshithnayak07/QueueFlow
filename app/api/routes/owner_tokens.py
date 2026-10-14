"""S5: owner token status management (FR-12).

PATCH /api/owner/tokens/{token_id}/status — owner marks a token called /
served / cancelled. Auth + ownership guarded: foreign or missing tokens
are a bare 404 (no existence leak, same as get_owned_session).

Transitions are forward-only (queue_service.ALLOWED_TRANSITIONS);
served/cancelled are terminal. Illegal moves are 409, unknown statuses
422 (schema), same-status updates a no-op 200. called_at / served_at are
stamped on first entry into those states.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner
from app.database.session import get_db
from app.models.owner import Owner
from app.models.queue_session import QueueSession
from app.models.token import (
    TOKEN_STATUS_CALLED,
    TOKEN_STATUS_SERVED,
    Token,
)
from app.schemas.token import OwnerTokenOut, TokenStatusUpdate
from app.services.queue_service import validate_status_transition

router = APIRouter(prefix="/api/owner", tags=["owner-tokens"])


@router.patch("/tokens/{token_id}/status", response_model=OwnerTokenOut)
def update_token_status(
    token_id: int,
    data: TokenStatusUpdate,
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
) -> Token:
    token = db.get(Token, token_id)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )
    queue_session = db.get(QueueSession, token.session_id)
    if queue_session is None or queue_session.owner_id != current_owner.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )

    if token.status != data.status:
        try:
            validate_status_transition(token.status, data.status)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        token.status = data.status
        now = datetime.now(timezone.utc)
        if data.status == TOKEN_STATUS_CALLED and token.called_at is None:
            token.called_at = now
        if data.status == TOKEN_STATUS_SERVED and token.served_at is None:
            token.served_at = now
        db.commit()
        db.refresh(token)
    return token
