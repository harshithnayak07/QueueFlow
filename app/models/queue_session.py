from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# MVP status values: "open" (default) / "closed".
# Virtual queue is a separate flag so a session can be open
# with the virtual queue still disabled (owner activates it explicitly).
SESSION_STATUS_OPEN = "open"
SESSION_STATUS_CLOSED = "closed"


class QueueSession(Base):
    """One owner-managed queue (e.g. a hospital counter for the day).

    people_count vs physical_people_remaining (S2 decision):
    - people_count: last computer-vision estimate (system-produced,
      owner-reviewable, None until first analysis).
    - physical_people_remaining: owner-confirmed remaining physical queue;
      authoritative input for wait calculation.
    """

    __tablename__ = "queue_sessions"
    __table_args__ = (
        CheckConstraint("active_counters >= 0", name="ck_session_counters_nonneg"),
        CheckConstraint(
            "average_service_minutes >= 0", name="ck_session_service_nonneg"
        ),
        CheckConstraint(
            "physical_people_remaining >= 0", name="ck_session_physical_nonneg"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("owners.id", ondelete="CASCADE"), index=True, nullable=False
    )
    people_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    physical_people_remaining: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    active_counters: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    average_service_minutes: Mapped[float] = mapped_column(
        Float, default=5.0, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default=SESSION_STATUS_OPEN, nullable=False
    )
    virtual_queue_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    owner: Mapped["Owner"] = relationship(back_populates="sessions")
    tokens: Mapped[list["Token"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list["QueueSnapshot"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
