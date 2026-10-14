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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    sessions: Mapped[list["QueueSession"]] = relationship(
        back_populates="owner", cascade="all, delete-orphan"
    )
