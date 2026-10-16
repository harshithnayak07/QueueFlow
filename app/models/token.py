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


# MVP token statuses (spec §11) + "expired" (30-min post-call validity, E2).
# served/cancelled/expired are terminal and occupy no queue position.
TOKEN_STATUS_WAITING = "waiting"
TOKEN_STATUS_CALLED = "called"
TOKEN_STATUS_SERVED = "served"
TOKEN_STATUS_CANCELLED = "cancelled"
TOKEN_STATUS_EXPIRED = "expired"


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
        # One token per phone number per session (contacts are stored in
        # canonical form by TokenCreate). Fresh databases enforce this at
        # the DB level; legacy tables get the equivalent index from the
        # migration shim (skipped if legacy duplicates already exist —
        # those rows are preserved and the app-level check still guards).
        UniqueConstraint(
            "session_id", "customer_contact", name="uq_token_session_contact"
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
    # 30-minute post-call validity window (E2). Set when the token is
    # called; the lazy sweep flips overdue called tokens to "expired".
    # NULL for tokens never called. No migration framework in local MVP:
    # added by ensure_schema_updates() (see app/database/session.py).
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Per-token validity duration in whole minutes, frozen at creation
    # from the estimated wait then (validity == estimated wait, min 1;
    # session setting only when the wait was uncomputable). The window
    # starts at called_at; expires_at enforces it. NULL on legacy rows =
    # fall back to the session setting. Added by shim for old DBs.
    validity_minutes: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    # Automatic progression windows (no migration framework: added by the
    # same shim). A called token currently being served carries
    # [service_started_at, service_ends_at); the reconciler marks it
    # served at ends_at and starts the next eligible token. NULL = not
    # scheduled. History is preserved on the row after serving.
    service_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    service_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    session: Mapped["QueueSession"] = relationship(back_populates="tokens")
