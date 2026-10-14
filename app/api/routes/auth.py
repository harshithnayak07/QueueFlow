from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_owner
from app.core.security import create_access_token, hash_password, verify_password
from app.database.session import get_db
from app.models.owner import Owner
from app.schemas.auth import OwnerLogin, OwnerOut, OwnerRegister, TokenResponse

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


@router.get("/me", response_model=OwnerOut)
def me(current_owner: Owner = Depends(get_current_owner)) -> Owner:
    return current_owner
