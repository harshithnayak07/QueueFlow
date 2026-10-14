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
        v = v.strip()
        if len(v) < 3:
            raise ValueError("customer_contact must not be blank")
        return v


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
