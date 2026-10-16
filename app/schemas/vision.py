"""S4: vision-analysis response schemas.

AnalyzeVideoOut is the owner-review contract: the estimate plus an
explicit needs_owner_review flag. The client must show the estimate as
approximate and route the owner to confirm physical_people_remaining
via PATCH settings — analysis never confirms the queue by itself.
"""

from datetime import datetime

from pydantic import BaseModel

from app.schemas.queue import SessionOut


class AnalyzeVideoOut(BaseModel):
    session: SessionOut
    estimated_people: int
    frames_sampled: int
    method: str
    confidence_threshold: float
    analyzed_at: datetime
    estimate_note: str
    needs_owner_review: bool = True
    snapshot_id: int
    video_filename: str
    # Echoed so the UI can stress that this authoritative value is
    # UNCHANGED by analysis until the owner confirms it.
    physical_people_remaining: int

    model_config = {"from_attributes": True}


class AnalyzeImageOut(BaseModel):
    """Single-image analysis: same owner-review contract as video
    (estimate + needs_owner_review flag, physical_* untouched)."""

    session: SessionOut
    estimated_people: int
    frames_sampled: int = 1
    method: str
    confidence_threshold: float
    analyzed_at: datetime
    estimate_note: str
    needs_owner_review: bool = True
    snapshot_id: int
    image_filename: str
    physical_people_remaining: int

    model_config = {"from_attributes": True}
