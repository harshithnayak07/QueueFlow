"""S4 unit tests: vision aggregation, validation, ROI, video sampling.

No YOLO weights needed: analyze_video is exercised with an injected
stub detector over a tiny real video file written with OpenCV.
"""

import pytest

from app.services.vision_service import (
    InvalidVideoError,
    VisionUnavailableError,
    aggregate_counts,
    analyze_video,
    count_boxes_in_roi,
    validate_video_extension,
)


# --- aggregation: median, never a sum ---


def test_aggregate_uses_median_not_sum():
    # 100 frames of 5 people must estimate 5, never 500.
    assert aggregate_counts([5] * 100) == 5


def test_aggregate_median_even_and_spikes():
    assert aggregate_counts([3, 5, 4]) == 4
    # A few crowded frames must not drag the estimate (mean would be 6).
    assert aggregate_counts([4, 4, 4, 4, 14]) == 4


def test_aggregate_empty_is_zero():
    assert aggregate_counts([]) == 0


def test_aggregate_rejects_bad_params():
    with pytest.raises(ValueError):
        analyze_video("whatever.mp4", detector=lambda f: 0, frame_stride=0)
    with pytest.raises(ValueError):
        analyze_video("whatever.mp4", detector=lambda f: 0, max_frames=0)
    with pytest.raises(ValueError):
        analyze_video(
            "whatever.mp4", detector=lambda f: 0, confidence=0.0
        )


# --- upload validation ---


def test_validate_video_extension_ok():
    assert validate_video_extension("queue.mp4") == ".mp4"
    assert validate_video_extension("QUEUE.MOV") == ".mov"
    assert validate_video_extension("clip.webm") == ".webm"


def test_validate_video_extension_rejects():
    for bad in (None, "", "noextension", "photo.jpg", "run.exe",
                "notes.txt", "../evil.mp4.exe"):
        with pytest.raises(InvalidVideoError):
            validate_video_extension(bad)


# --- ROI filtering (normalized 0-1 boxes) ---


def test_roi_none_counts_all():
    boxes = [[0.1, 0.1, 0.2, 0.3], [0.8, 0.8, 0.9, 0.95]]
    assert count_boxes_in_roi(boxes, None) == 2


def test_roi_filters_by_box_center():
    boxes = [
        [0.1, 0.1, 0.2, 0.2],   # center (0.15, 0.15) — inside
        [0.7, 0.7, 0.9, 0.9],   # center (0.8, 0.8) — outside
        [0.0, 0.0],             # malformed — skipped
    ]
    assert count_boxes_in_roi(boxes, (0.0, 0.0, 0.5, 0.5)) == 1


# --- analyze_video over a real (tiny) video file ---


def _write_tiny_video(path: str, frames: int = 5) -> None:
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    writer = cv2.VideoWriter(
        path, cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (64, 64)
    )
    assert writer.isOpened(), "OpenCV mp4v writer unavailable"
    for i in range(frames):
        writer.write(np.full((64, 64, 3), i * 10, dtype=np.uint8))
    writer.release()


def test_analyze_video_samples_and_medians(tmp_path):
    video = str(tmp_path / "queue.mp4")
    _write_tiny_video(video, frames=5)

    cycling = iter([3, 5, 4, 5, 3])
    seen = []

    def stub_detector(frame):
        seen.append(frame.shape)
        return next(cycling)

    result = analyze_video(
        video, detector=stub_detector, frame_stride=1, max_frames=5
    )
    assert result.frames_sampled == 5
    assert result.per_frame_counts == [3, 5, 4, 5, 3]
    assert result.estimated_people == 4  # median, not sum (20)
    assert len(seen) == 5


def test_analyze_video_respects_max_frames(tmp_path):
    video = str(tmp_path / "queue.mp4")
    _write_tiny_video(video, frames=10)
    result = analyze_video(
        video, detector=lambda f: 2, frame_stride=1, max_frames=3
    )
    assert result.frames_sampled == 3
    assert result.estimated_people == 2


def test_analyze_video_unreadable_file(tmp_path):
    with pytest.raises(InvalidVideoError):
        analyze_video(
            str(tmp_path / "missing.mp4"), detector=lambda f: 1
        )
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"not a video at all")
    with pytest.raises(InvalidVideoError):
        analyze_video(str(corrupt), detector=lambda f: 1)


def test_analyze_video_without_cv2(monkeypatch, tmp_path):
    import sys

    monkeypatch.setitem(sys.modules, "cv2", None)
    # Force re-import failure: analyze_video imports cv2 lazily, and
    # sys.modules["cv2"] = None makes `import cv2` raise ImportError.
    with pytest.raises(VisionUnavailableError):
        analyze_video(str(tmp_path / "x.mp4"), detector=lambda f: 1)


def test_default_yolo_detector_missing_backend():
    try:
        import ultralytics  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip(
            "ultralytics installed — live YOLO load covered outside unit tests"
        )
    from app.services.vision_service import default_yolo_detector

    with pytest.raises(VisionUnavailableError):
        default_yolo_detector()


# --- analyze_image: single still image ---


def _write_tiny_image(path: str) -> None:
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    assert cv2.imwrite(path, np.full((64, 64, 3), 128, dtype=np.uint8))


def test_validate_image_extension_ok():
    from app.services.vision_service import validate_image_extension

    assert validate_image_extension("queue.jpg") == ".jpg"
    assert validate_image_extension("QUEUE.PNG") == ".png"
    assert validate_image_extension("photo.webp") == ".webp"


def test_validate_image_extension_rejects():
    from app.services.vision_service import (
        InvalidImageError,
        validate_image_extension,
    )

    for bad in (None, "", "noextension", "clip.mp4", "run.exe",
                "notes.txt", "../evil.jpg.exe"):
        with pytest.raises(InvalidImageError):
            validate_image_extension(bad)


def test_analyze_image_counts_single_frame(tmp_path):
    from app.services.vision_service import analyze_image

    image = str(tmp_path / "queue.jpg")
    _write_tiny_image(image)
    seen = []

    def stub_detector(frame):
        seen.append(frame.shape)
        return 6

    result = analyze_image(image, detector=stub_detector)
    assert result.frames_sampled == 1
    assert result.per_frame_counts == [6]
    assert result.estimated_people == 6
    assert result.method == "yolov8n-single"
    assert len(seen) == 1


def test_analyze_image_unreadable(tmp_path):
    from app.services.vision_service import InvalidImageError, analyze_image

    with pytest.raises(InvalidImageError):
        analyze_image(str(tmp_path / "missing.jpg"), detector=lambda f: 1)
    corrupt = tmp_path / "corrupt.jpg"
    corrupt.write_bytes(b"not an image at all")
    with pytest.raises(InvalidImageError):
        analyze_image(str(corrupt), detector=lambda f: 1)
