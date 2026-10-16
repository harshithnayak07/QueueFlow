"""S4: recorded-video upload + analysis endpoint.

POST /api/queue-sessions/{session_id}/analyze-video (owner auth +
get_owned_session 404 guard, like all owner routes).

Owner-review contract enforced here:
- Analysis sets session.people_count (reviewable vision estimate) and
  appends a QueueSnapshot(source="vision").
- Analysis NEVER touches physical_people_remaining — the authoritative
  wait input stays owner-confirmed via PATCH settings (FR-05).
- Uploads are validated (extension, streamed size cap, safe name) and
  stored under settings.upload_dir; the client filename is never used
  as a path.

Error mapping: InvalidVideoError -> 422, VisionUnavailableError -> 503
(stack/model missing), oversize -> 413.
"""

import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_owner, get_owned_session
from app.core.config import settings
from app.database.session import get_db
from app.models.owner import Owner
from app.models.queue_session import QueueSession
from app.models.queue_snapshot import QueueSnapshot
from app.models.token import Token
from app.schemas.queue import SessionOut
from app.schemas.vision import AnalyzeImageOut, AnalyzeVideoOut
from app.services import vision_service
from app.services.queue_service import queue_ahead_estimate
from app.services.vision_service import (
    ESTIMATE_NOTE,
    InvalidImageError,
    InvalidVideoError,
    VisionUnavailableError,
)
from app.services.waiting_time_service import (
    ACTIVE_TOKEN_STATUSES,
)

router = APIRouter(prefix="/api/queue-sessions", tags=["vision"])


def _resolve_upload_dir() -> str:
    upload_dir = settings.upload_dir
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


@router.post("/{session_id}/analyze-video", response_model=AnalyzeVideoOut)
async def analyze_video(
    session_id: int,
    file: UploadFile = File(...),
    frame_stride: int = Query(
        default=30, ge=1, le=300, description="Analyze every Nth frame"
    ),
    max_frames: int = Query(
        default=60, ge=1, le=300, description="Max sampled frames per upload"
    ),
    confidence: float = Query(
        default=0.5, gt=0, le=1, description="YOLO confidence threshold"
    ),
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
) -> AnalyzeVideoOut:
    queue_session: QueueSession = get_owned_session(
        session_id, db, current_owner
    )

    # 1. Validate type BEFORE touching disk (extension only; the saved
    #    name is server-generated so the client filename can never
    #    become a path).
    try:
        ext = vision_service.validate_video_extension(file.filename)
    except InvalidVideoError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    # 2. Stream to disk with a live size cap (never trust Content-Length).
    max_bytes = settings.max_video_mb * 1024 * 1024
    upload_dir = _resolve_upload_dir()
    stored_name = f"session_{queue_session.id}_{uuid.uuid4().hex}{ext}"
    stored_path = os.path.join(upload_dir, stored_name)
    if not os.path.abspath(stored_path).startswith(
        os.path.abspath(upload_dir) + os.sep
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Upload storage misconfigured.",
        )
    try:
        written = 0
        with open(stored_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    out.close()
                    os.unlink(stored_path)
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=(
                            f"Video exceeds the {settings.max_video_mb} MB "
                            "limit. Trim or re-compress and retry."
                        ),
                    )
                out.write(chunk)
        if written == 0:
            os.unlink(stored_path)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Uploaded file is empty.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        if os.path.exists(stored_path):
            os.unlink(stored_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store the uploaded video ({exc}).",
        ) from exc
    finally:
        await file.close()

    # 3. Analyze (blocking CV work runs in a threadpool). The production
    #    detector is the lazy YOLO backend (None); tests monkeypatch
    #    vision_service.analyze_video.
    try:
        result = await run_in_threadpool(
            vision_service.analyze_video,
            stored_path,
            None,
            frame_stride,
            max_frames,
            confidence,
            None,
        )
    except InvalidVideoError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except VisionUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    # 4. Persist: reviewable estimate + history snapshot. physical_*
    #    deliberately untouched — owner confirms via PATCH settings.
    #    Snapshot wait uses the same remaining-work rule (vision count is
    #    a crowd estimate, never added to the token queue).
    queue_session.people_count = result.estimated_people
    active_virtual = (
        db.query(Token)
        .filter(
            Token.session_id == queue_session.id,
            Token.status.in_(ACTIVE_TOKEN_STATUSES),
        )
        .count()
    )
    _, snapshot_wait = queue_ahead_estimate(
        [("waiting", None)] * active_virtual,
        queue_session.average_service_minutes,
        queue_session.active_counters,
    )
    snapshot = QueueSnapshot(
        session_id=queue_session.id,
        people_count=result.estimated_people,
        estimated_wait_minutes=snapshot_wait,
        source="vision",
    )
    db.add(snapshot)
    db.commit()
    db.refresh(queue_session)
    db.refresh(snapshot)

    return AnalyzeVideoOut(
        session=SessionOut.model_validate(queue_session),
        estimated_people=result.estimated_people,
        frames_sampled=result.frames_sampled,
        method=result.method,
        confidence_threshold=result.confidence_threshold,
        analyzed_at=result.analyzed_at,
        estimate_note=ESTIMATE_NOTE,
        needs_owner_review=True,
        snapshot_id=snapshot.id,
        video_filename=stored_name,
        physical_people_remaining=queue_session.physical_people_remaining,
    )


@router.post("/{session_id}/analyze-image", response_model=AnalyzeImageOut)
async def analyze_image(
    session_id: int,
    file: UploadFile = File(...),
    confidence: float = Query(
        default=0.5, gt=0, le=1, description="YOLO confidence threshold"
    ),
    db: Session = Depends(get_db),
    current_owner: Owner = Depends(get_current_owner),
) -> AnalyzeImageOut:
    """Single queue-image analysis. Same owner-review contract as video:
    sets session.people_count + a QueueSnapshot(source="vision-image") but
    NEVER physical_people_remaining. Error mapping: InvalidImageError ->
    422, VisionUnavailableError -> 503, oversize -> 413."""
    queue_session: QueueSession = get_owned_session(
        session_id, db, current_owner
    )

    try:
        ext = vision_service.validate_image_extension(file.filename)
    except InvalidImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    max_bytes = settings.max_image_mb * 1024 * 1024
    upload_dir = _resolve_upload_dir()
    stored_name = f"session_{queue_session.id}_{uuid.uuid4().hex}{ext}"
    stored_path = os.path.join(upload_dir, stored_name)
    if not os.path.abspath(stored_path).startswith(
        os.path.abspath(upload_dir) + os.sep
    ):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Upload storage misconfigured.",
        )
    try:
        written = 0
        with open(stored_path, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    out.close()
                    os.unlink(stored_path)
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail=(
                            f"Image exceeds the {settings.max_image_mb} MB "
                            "limit. Compress and retry."
                        ),
                    )
                out.write(chunk)
        if written == 0:
            os.unlink(stored_path)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Uploaded file is empty.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        if os.path.exists(stored_path):
            os.unlink(stored_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to store the uploaded image ({exc}).",
        ) from exc
    finally:
        await file.close()

    try:
        result = await run_in_threadpool(
            vision_service.analyze_image,
            stored_path,
            None,
            confidence,
            None,
        )
    except InvalidImageError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    except VisionUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    queue_session.people_count = result.estimated_people
    active_virtual = (
        db.query(Token)
        .filter(
            Token.session_id == queue_session.id,
            Token.status.in_(ACTIVE_TOKEN_STATUSES),
        )
        .count()
    )
    # Same remaining-work rule as everywhere else (see video route).
    _, snapshot_wait = queue_ahead_estimate(
        [("waiting", None)] * active_virtual,
        queue_session.average_service_minutes,
        queue_session.active_counters,
    )
    snapshot = QueueSnapshot(
        session_id=queue_session.id,
        people_count=result.estimated_people,
        estimated_wait_minutes=snapshot_wait,
        source="vision-image",
    )
    db.add(snapshot)
    db.commit()
    db.refresh(queue_session)
    db.refresh(snapshot)

    return AnalyzeImageOut(
        session=SessionOut.model_validate(queue_session),
        estimated_people=result.estimated_people,
        frames_sampled=result.frames_sampled,
        method=result.method,
        confidence_threshold=result.confidence_threshold,
        analyzed_at=result.analyzed_at,
        estimate_note=ESTIMATE_NOTE,
        needs_owner_review=True,
        snapshot_id=snapshot.id,
        image_filename=stored_name,
        physical_people_remaining=queue_session.physical_people_remaining,
    )
