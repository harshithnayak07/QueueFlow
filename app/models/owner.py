from datetime import datetime, timezone

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Owner(Base):
    """Shop/counter owner. Passwords stored as bcrypt hash only."""

    __tablename__ = "owners"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    shop_name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Google subject claim for Google-created accounts (E1). NULL for
    # password accounts. Lets returning Google users back in while a
    # password account with the same email gets a 409 instead of a silent
    # link. Added by ensure_schema_updates() (no migration framework here).
    google_sub: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    sessions: Mapped[list["QueueSession"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
