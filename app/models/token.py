import secrets
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def generate_tracking_id() -> str:
    # Unguessable public credential. Never use sequential id for tracking.
    return secrets.token_urlsafe(32)


# MVP token statuses (spec §11).
TOKEN_STATUS_WAITING = "waiting"
TOKEN_STATUS_CALLED = "called"
TOKEN_STATUS_SERVED = "served"
TOKEN_STATUS_CANCELLED = "cancelled"


class Token(Base):
    """One virtual-queue token within a session.

    token_number is sequential per session (human-facing, e.g. shown on
    screen); tracking_id is the unguessable browser credential.
    """

    __tablename__ = "tokens"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "token_number", name="uq_token_session_number"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("queue_sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    token_number: Mapped[int] = mapped_column(Integer, nullable=False)
    tracking_id: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        default=generate_tracking_id,
        nullable=False,
    )
    customer_contact: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=TOKEN_STATUS_WAITING, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    called_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    served_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    session: Mapped["QueueSession"] = relationship(back_populates="tokens")
