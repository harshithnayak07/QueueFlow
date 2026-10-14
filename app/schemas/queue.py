"""S3: queue-session request/response schemas.

people_count vs physical_people_remaining (S2 decision, kept):
- people_count: last vision estimate, owner-correctable via settings.
- physical_people_remaining: owner-confirmed remainder, authoritative for waits.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SESSION_STATUSES = ("open", "closed")


class SessionCreate(BaseModel):
    active_counters: int = Field(default=1, ge=0, le=100)
    average_service_minutes: float = Field(default=5.0, ge=0, le=1440)
    physical_people_remaining: int = Field(default=0, ge=0)


class SessionSettingsUpdate(BaseModel):
    """PATCH body: every field optional; only provided fields change.

    Includes people_count so the owner can manually correct the last vision
    estimate (FR-05). Does NOT touch virtual_queue_enabled — activation goes
    through the explicit activate endpoint (owner in control).
    """

    active_counters: int | None = Field(default=None, ge=0, le=100)
    average_service_minutes: float | None = Field(default=None, ge=0, le=1440)
    physical_people_remaining: int | None = Field(default=None, ge=0)
    people_count: int | None = Field(default=None, ge=0)
    status: Literal["open", "closed"] | None = None


class SessionOut(BaseModel):
    id: int
    owner_id: int
    people_count: int | None
    physical_people_remaining: int
    active_counters: int
    average_service_minutes: float
    status: str
    virtual_queue_enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SessionStatusOut(BaseModel):
    """GET .../status: stored session + live-computed estimates.

    estimated_wait_minutes is None when not computable (e.g. zero active
    counters) — clients must show "unavailable", never 0 or a guess.
    """

    session: SessionOut
    people_ahead: int
    waiting_virtual_count: int
    estimated_wait_minutes: float | None
    estimate_note: str
    virtual_queue_recommended: bool
    recommendation_reason: str
    recommendation_threshold: int

    model_config = {"from_attributes": True}
