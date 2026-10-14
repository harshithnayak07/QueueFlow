from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.database.session import get_db
from app.models.owner import Owner
from app.models.queue_session import QueueSession

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_owner(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> Owner:
    owner_id = decode_access_token(token)
    if owner_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    owner = db.get(Owner, int(owner_id)) if owner_id.isdigit() else None
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return owner


def get_owned_session(
    session_id: int,
    db: Session,
    current_owner: Owner,
) -> QueueSession:
    """Ownership guard for all owner session routes (wired in from S3).

    Returns 404 both when the session does not exist and when it belongs
    to another owner, so ownership cannot be probed.
    """
    queue_session = db.get(QueueSession, session_id)
    if queue_session is None or queue_session.owner_id != current_owner.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Queue session not found",
        )
    return queue_session
