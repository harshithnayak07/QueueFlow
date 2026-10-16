from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner
from app.core.config import settings
from app.core.security import create_access_token, hash_password, verify_password
from app.database.session import get_db
from app.models.owner import Owner
from app.schemas.auth import (
    GoogleLogin,
    OwnerLogin,
    OwnerOut,
    OwnerRegister,
    TokenResponse,
)

import secrets

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post(
    "/register", response_model=OwnerOut, status_code=status.HTTP_201_CREATED
)
def register(data: OwnerRegister, db: Session = Depends(get_db)) -> Owner:
    existing = db.query(Owner).filter(Owner.email == data.email).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    owner = Owner(
        shop_name=data.shop_name,
        email=data.email,
        password_hash=hash_password(data.password),
    )
    db.add(owner)
    try:
        db.commit()
    except IntegrityError:
        # Race between the check above and insert: same 409 outcome.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    db.refresh(owner)
    return owner


@router.post("/login", response_model=TokenResponse)
def login(data: OwnerLogin, db: Session = Depends(get_db)) -> TokenResponse:
    owner = (
        db.query(Owner).filter(Owner.email == data.email.strip().lower()).first()
    )
    if owner is None or not verify_password(data.password, owner.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    return TokenResponse(access_token=create_access_token(subject=str(owner.id)))


def verify_google_id_token(id_token_str: str) -> dict:
    """Verify a Google Identity Services ID token (signature + aud/iss/exp)
    against our client ID. Returns the verified claims. Kept as a separate
    module-level function so tests can monkeypatch it (no network in CI)."""
    return google_id_token.verify_oauth2_token(
        id_token_str, google_requests.Request(), settings.google_client_id
    )


@router.post("/google", response_model=TokenResponse)
def login_with_google(data: GoogleLogin, db: Session = Depends(get_db)) -> TokenResponse:
    try:
        claims = verify_google_id_token(data.id_token)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google sign-in failed: invalid token.",
        ) from exc
    except Exception as exc:
        # e.g. Google certs unreachable from the server: honest 503, not 500.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google verification is temporarily unavailable. Try again later.",
        ) from exc

    email = (claims.get("email") or "").strip().lower()
    if not email or not claims.get("email_verified"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Google sign-in failed: unverified email.",
        )

    # Returning Google user: matched by stable subject claim.
    sub = (claims.get("sub") or "").strip() or None
    if sub:
        owner = db.query(Owner).filter(Owner.google_sub == sub).first()
        if owner is not None:
            return TokenResponse(
                access_token=create_access_token(subject=str(owner.id))
            )

    owner = db.query(Owner).filter(Owner.email == email).first()
    if owner is not None:
        # Decision E1 (owner-approved): never silently link a Google
        # identity into an existing password account. New Google users get
        # an account; existing emails must use the Login page instead.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email is already registered. "
            "Please log in with your email and password instead.",
        )
    # New Google user: create the account. Google users never set a
    # password here; store a random unusable hash so the password-login
    # path stays safe.
    name = (claims.get("name") or "").strip()[:120]
    shop_name = name or email.split("@")[0][:120]
    owner = Owner(
        shop_name=shop_name,
        email=email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        google_sub=sub,
    )
    db.add(owner)
    try:
        db.commit()
    except IntegrityError as exc:
        # Lost a creation race: that email now exists -> same 409 outcome.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This email is already registered. "
            "Please log in with your email and password instead.",
        ) from exc
    db.refresh(owner)
    return TokenResponse(access_token=create_access_token(subject=str(owner.id)))


@router.get("/me", response_model=OwnerOut)
def me(current_owner: Owner = Depends(get_current_owner)) -> Owner:
    return current_owner
