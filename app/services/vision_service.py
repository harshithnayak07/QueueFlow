"""S4: computer-vision service — recorded-video person-count estimate.

Separation of concerns: this module owns ALL video/detection logic.
API routes only validate ownership, persist results, and translate
VisionError subclasses into HTTP responses. Nothing here touches the DB.

MVP counting rule (spec section 4, step 3):
- Sample frames (every ``frame_stride``-th frame, up to ``max_frames``).
- Run a person detector on each sampled frame -> per-frame count.
- Aggregate with the MEDIAN of per-frame counts — never the sum, so the
  same person appearing in many frames cannot inflate the estimate and
  the result does not grow with video length.

Estimate, not a guarantee: every result carries ESTIMATE_NOTE and the
API layer flags ``needs_owner_review``. Analysis writes
``session.people_count`` (reviewable estimate) but NEVER
``physical_people_remaining`` — the owner confirms that via PATCH
settings (owner in control). YOLO cannot map a detected person to a
token ID; coordination stays owner-rules + token status (S5).

Backends:
- Tests / CI inject a ``detector`` callable (frame -> int) so the suite
  runs without heavy ML deps.
- Production default lazily loads Ultralytics YOLOv8n (``yolov8n.pt``,
  person class only). cv2 / ultralytics / model-download failures raise
  VisionUnavailableError -> HTTP 503 with a plain message, never a crash.
"""

from __future__ import annotations

import os
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Sequence

# Recorded-upload validation (mirrored by the route, which also enforces
# the byte limit while streaming to disk).
ALLOWED_VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm"})
MAX_VIDEO_BYTES = 50 * 1024 * 1024  # 50 MB MVP cap (assumption, S4)

ESTIMATE_NOTE = (
    "Approximate computer-vision estimate only — detection errors, "
    "occlusion, and crowded scenes affect accuracy. Owner review required; "
    "not a guaranteed queue count."
)

# YOLO choice (S4 decision): YOLOv8n (nano) — smallest Ultralytics YOLOv8
# model, CPU-runnable for an MVP. License: Ultralytics YOLOv8 is AGPL-3.0
# (commercial license available) — fine for local MVP use, must be
# re-checked before any commercial hosting. First run downloads
# ``yolov8n.pt`` (~6 MB) from Ultralytics; offline hosts must pre-seed it.
YOLO_MODEL_NAME = "yolov8n.pt"
YOLO_PERSON_CLASS_ID = 0


class VisionError(Exception):
    """Base for all vision failures."""


class InvalidVideoError(VisionError):
    """Upload is not a usable video (bad type, corrupt, unreadable). -> 422."""


class VisionUnavailableError(VisionError):
    """Detector stack unavailable (no cv2/ultralytics/model). -> 503."""


@dataclass
class VisionResult:
    """Outcome of one recorded-video analysis."""

    estimated_people: int
    frames_sampled: int
    per_frame_counts: list[int] = field(default_factory=list)
    method: str = "yolov8n-median"
    confidence_threshold: float = 0.5
    analyzed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


def validate_video_extension(filename: str | None) -> str:
    """Return the lowercase extension, or raise InvalidVideoError.

    The route saves uploads under a server-generated safe name; only the
    extension is taken from the client filename, so path traversal via the
    filename is impossible by construction.
    """
    if not filename or "." not in os.path.basename(filename):
        raise InvalidVideoError(
            "A video file with a supported extension is required "
            f"({sorted(ALLOWED_VIDEO_EXTENSIONS)})."
        )
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        raise InvalidVideoError(
            f"Unsupported video type '{ext}'. "
            f"Allowed: {sorted(ALLOWED_VIDEO_EXTENSIONS)}."
        )
    return ext


def aggregate_counts(per_frame_counts: Sequence[int]) -> int:
    """Median of per-frame person counts -> single queue-size estimate.

    Why median (not sum, not mean): summing across frames counts the same
    person many times (spec forbids this); the mean is skewed by a few
    crowded frames. The median is robust and length-invariant. Empty input
    (no frames readable) -> 0.
    """
    counts = [c for c in per_frame_counts if c is not None and c >= 0]
    if not counts:
        return 0
    return int(statistics.median(counts))


def count_boxes_in_roi(
    boxes: Sequence[Sequence[float]],
    roi: tuple[float, float, float, float] | None,
) -> int:
    """Count detections whose center falls inside ``roi``.

    Boxes are [x1, y1, x2, y2] in NORMALIZED 0-1 coordinates; ``roi`` is
    (x_min, y_min, x_max, y_max), also normalized. ``roi=None`` counts all
    boxes (whole-frame estimate — MVP default).
    """
    if roi is None:
        return len(boxes)
    x_min, y_min, x_max, y_max = roi
    n = 0
    for box in boxes:
        if len(box) < 4:
            continue
        cx = (box[0] + box[2]) / 2.0
        cy = (box[1] + box[3]) / 2.0
        if x_min <= cx <= x_max and y_min <= cy <= y_max:
            n += 1
    return n


def default_yolo_detector(
    confidence: float = 0.5,
    roi: tuple[float, float, float, float] | None = None,
) -> Callable[[object], int]:
    """Build the production detector: YOLOv8n, person class only.

    Lazy import so ``import app.services.vision_service`` never requires
    torch/ultralytics. Raises VisionUnavailableError with an actionable
    message when the stack or model cannot load.
    """

    try:
        from ultralytics import YOLO  # type: ignore[import-not-found]
    except ImportError as exc:
        raise VisionUnavailableError(
            "Person detection is unavailable: the YOLO backend "
            "(ultralytics) is not installed. Install vision requirements "
            "to enable video analysis."
        ) from exc

    try:
        model = YOLO(YOLO_MODEL_NAME)
    except Exception as exc:  # model download / load failure
        raise VisionUnavailableError(
            f"Person detection is unavailable: could not load YOLO model "
            f"'{YOLO_MODEL_NAME}' ({exc}). Check network access for the "
            "first-time model download or pre-seed the weights file."
        ) from exc

    def detect(frame: object) -> int:
        results = model.predict(
            frame, conf=confidence, classes=[YOLO_PERSON_CLASS_ID],
            verbose=False,
        )
        if not results:
            return 0
        boxes = results[0].boxes
        if boxes is None or boxes.xyxy is None:
            return 0
        try:
            import numpy as np  # type: ignore[import-not-found]

            raw = boxes.xyxy.cpu().numpy()
            h, w = getattr(frame, "shape", (1, 1, 1))[:2]
            normed = [
                [b[0] / w, b[1] / h, b[2] / w, b[3] / h] for b in raw
            ]
        except Exception:
            # If normalization fails, fall back to whole-frame box count.
            return len(boxes)
        return count_boxes_in_roi(normed, roi)

    return detect


def analyze_video(
    video_path: str,
    detector: Callable[[object], int] | None = None,
    frame_stride: int = 30,
    max_frames: int = 60,
    confidence: float = 0.5,
    roi: tuple[float, float, float, float] | None = None,
) -> VisionResult:
    """Analyze a recorded video file -> VisionResult estimate.

    ``detector`` maps an OpenCV frame (numpy array) to a person count;
    when None, the YOLOv8n backend is loaded. ``frame_stride`` samples
    every Nth frame (~1 fps at 30 fps video); ``max_frames`` bounds CPU
    work per upload. Raises InvalidVideoError (unreadable) or
    VisionUnavailableError (no cv2 / no backend).
    """
    if frame_stride < 1:
        raise ValueError("frame_stride must be >= 1")
    if max_frames < 1:
        raise ValueError("max_frames must be >= 1")
    if not 0 < confidence <= 1:
        raise ValueError("confidence must be in (0, 1]")

    try:
        import cv2  # type: ignore[import-not-found]
    except ImportError as exc:
        raise VisionUnavailableError(
            "Video analysis is unavailable: OpenCV (cv2) is not installed. "
            "Install vision requirements to enable video analysis."
        ) from exc

    if detector is None:
        detector = default_yolo_detector(confidence=confidence, roi=roi)

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise InvalidVideoError(
            "Could not open the uploaded video. It may be corrupt or in "
            "an unsupported encoding — try re-exporting as .mp4 (H.264)."
        )
    try:
        per_frame: list[int] = []
        position = 0  # 0-based frame position in the video
        while len(per_frame) < max_frames:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if position % frame_stride == 0:
                try:
                    count = detector(frame)
                except VisionUnavailableError:
                    raise
                except Exception as exc:
                    raise InvalidVideoError(
                        f"Detection failed during analysis ({exc})."
                    ) from exc
                if count is None or count < 0:
                    count = 0
                per_frame.append(int(count))
                if len(per_frame) >= max_frames:
                    break
                # Skip ahead without decoding: grab() advances the
                # position cheaply; the next read() decodes the sample.
                for _ in range(frame_stride - 1):
                    if not cap.grab():
                        break
                    position += 1
            position += 1
        if not per_frame:
            raise InvalidVideoError(
                "No readable frames found in the uploaded video."
            )
        return VisionResult(
            estimated_people=aggregate_counts(per_frame),
            frames_sampled=len(per_frame),
            per_frame_counts=per_frame,
            method="yolov8n-median",
            confidence_threshold=confidence,
        )
    finally:
        cap.release()
