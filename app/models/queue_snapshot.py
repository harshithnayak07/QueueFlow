from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class QueueSnapshot(Base):
    """Point-in-time history row: what the queue looked like and what
    wait was estimated then. Live waits are always recomputed (S3);
    only snapshots persist estimated_wait_minutes (None = not computable,
    e.g. zero active counters)."""

    __tablename__ = "queue_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("queue_sessions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    people_count: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_wait_minutes: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    # Where the count came from: "vision" (video), "vision-image", or
    # "manual" (owner correction).
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    session: Mapped["QueueSession"] = relationship(back_populates="snapshots")
