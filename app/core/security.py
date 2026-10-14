from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings

# bcrypt operates on bytes; 72-byte limit is inherent to the algorithm,
# enforced at the schema layer (password max_length=72).
_ENCODING = "utf-8"


def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(password.encode(_ENCODING), bcrypt.gensalt())
    return hashed.decode(_ENCODING)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode(_ENCODING), password_hash.encode(_ENCODING)
        )
    except (ValueError, TypeError):
        # Malformed hash or input: treat as non-match, never raise.
        return False


def create_access_token(subject: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    """Return the subject (owner id) or None if invalid/expired."""
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return str(sub) if sub is not None else None
