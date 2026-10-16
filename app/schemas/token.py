"""S5: token request/response schemas.

Privacy rule (spec section 14): public tracking responses must NEVER
carry customer_contact, password data, or internal database ids. The
owner view (OwnerTokenOut) may include the contact because it sits
behind owner auth + the ownership guard.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

TokenStatus = Literal["waiting", "called", "served", "cancelled"]


class TokenCreate(BaseModel):
    """Customer join request: phone or email, nothing more (FR-09)."""

    customer_contact: str = Field(min_length=3, max_length=255)

    @field_validator("customer_contact")
    @classmethod
    def contact_must_not_be_blank(cls, v: str) -> str:
        # Canonical form (phones → +91…, emails → lowercase) so the
        # one-token-per-number rule compares like with like.
        from app.services.queue_service import normalize_customer_contact

        return normalize_customer_contact(v)


class TokenStatusUpdate(BaseModel):
    """Owner status change body (FR-12)."""

    status: TokenStatus


class OwnerTokenOut(BaseModel):
    """Full token for the owner dashboard (auth + ownership guarded)."""

    id: int
    session_id: int
    token_number: int
    tracking_id: str
    customer_contact: str
    status: str
    created_at: datetime
    called_at: datetime | None
    served_at: datetime | None
    # End of the post-call validity window (None until called).
    expires_at: datetime | None = None
    # Per-token frozen validity in whole minutes (None on legacy rows).
    validity_minutes: int | None = None
    # Automatic progression windows (None until scheduled).
    service_started_at: datetime | None = None
    service_ends_at: datetime | None = None

    model_config = {"from_attributes": True}


class PublicTokenCreatedOut(BaseModel):
    """Creation response: hands the customer their tracking credential
    plus a first live estimate. No contact echo, no internal token id."""

    session_id: int
    token_number: int
    tracking_id: str
    status: str
    people_ahead: int
    estimated_wait_minutes: float | None
    estimate_note: str
    recommended_return_at: datetime | None
    # Owner-configured validity window (minutes after called) for display.
    validity_minutes: int


class PublicTokenTrackingOut(BaseModel):
    """Tracking page payload (FR-11). Same as creation but without the
    tracking_id echo — the caller already presented it in the URL."""

    session_id: int
    token_number: int
    status: str
    people_ahead: int
    estimated_wait_minutes: float | None
    estimate_note: str
    recommended_return_at: datetime | None
    # Owner-configured validity window (minutes after called) for display.
    validity_minutes: int
