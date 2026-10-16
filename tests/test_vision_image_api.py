"""Image-analysis API tests: queue-image upload -> estimate -> owner review.

Mirrors the video suite (tests/test_vision_api.py): the real YOLO stack
is NOT required — vision_service.analyze_image is monkeypatched with a
stub. Covered end-to-end: auth + ownership, upload validation, safe
storage, people_count update, vision-image snapshot, and the owner-review
contract (physical_people_remaining untouched until PATCH settings).
"""

import os
import tempfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.database.base import Base
from app.database.session import get_db
from app.main import app
from app.models.queue_snapshot import QueueSnapshot
from app.services import vision_service
from app.services.vision_service import (
    InvalidImageError,
    VisionResult,
    VisionUnavailableError,
)


@pytest.fixture()
def db_session():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        os.unlink(path)


@pytest.fixture()
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture()
def upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    return tmp_path / "uploads"


def _stub_result(people: int = 4) -> VisionResult:
    return VisionResult(
        estimated_people=people,
        frames_sampled=1,
        per_frame_counts=[people],
        method="yolov8n-single",
        confidence_threshold=0.5,
        analyzed_at=datetime.now(timezone.utc),
    )


def _setup_owner(client, email="owner@shop.com"):
    assert client.post(
        "/api/auth/register",
        json={"shop_name": "Shop", "email": email, "password": "secret123"},
    ).status_code == 201
    login = client.post(
        "/api/auth/login",
        json={"email": email, "password": "secret123"},
    ).json()
    return {"Authorization": f"Bearer {login['access_token']}"}


def _analyze(client, headers, sid, filename="queue.jpg",
             content=b"\xff\xd8" + b"\x00" * 2048, **params):
    return client.post(
        f"/api/queue-sessions/{sid}/analyze-image",
        files={"file": (filename, content, "image/jpeg")},
        headers=headers,
        params=params or None,
    )


def test_image_analyze_updates_estimate_and_snapshot(
        client, db_session, upload_dir, monkeypatch):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    monkeypatch.setattr(
        vision_service, "analyze_image",
        lambda *a, **k: _stub_result(people=4),
    )

    r = _analyze(client, headers, sid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estimated_people"] == 4
    assert body["frames_sampled"] == 1
    assert body["method"] == "yolov8n-single"
    assert body["needs_owner_review"] is True
    assert "pproximate" in body["estimate_note"]
    assert body["session"]["people_count"] == 4
    assert body["physical_people_remaining"] == 0
    assert body["session"]["physical_people_remaining"] == 0
    assert body["image_filename"].endswith(".jpg")
    assert body["image_filename"] != "queue.jpg"
    assert body["image_filename"].startswith(f"session_{sid}_")
    assert (upload_dir / body["image_filename"]).exists()

    snapshots = db_session.query(QueueSnapshot).all()
    assert len(snapshots) == 1
    assert snapshots[0].session_id == sid
    assert snapshots[0].people_count == 4
    assert snapshots[0].source == "vision-image"
    assert body["snapshot_id"] == snapshots[0].id


def test_image_analyze_requires_auth_and_ownership(client, upload_dir):
    h1 = _setup_owner(client, "a@shop.com")
    h2 = _setup_owner(client, "b@shop.com")
    sid = client.post("/api/queue-sessions", json={}, headers=h1).json()["id"]

    assert _analyze(client, {}, sid).status_code in (401, 403)
    assert _analyze(client, h2, sid).status_code == 404
    assert _analyze(client, h1, 999999).status_code == 404


def test_image_analyze_rejects_bad_extension(client, upload_dir):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    assert _analyze(client, headers, sid, filename="notes.txt",
                    content=b"hello").status_code == 422
    assert _analyze(client, headers, sid, filename="clip.mp4",
                    content=b"\x00" * 64).status_code == 422


def test_image_analyze_rejects_empty_file(client, upload_dir):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    assert _analyze(client, headers, sid, content=b"").status_code == 422


def test_image_analyze_rejects_oversize(client, upload_dir, monkeypatch):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    monkeypatch.setattr(settings, "max_image_mb", 0)
    assert _analyze(client, headers, sid, content=b"\x00" * 16).status_code == 413


def test_image_analyze_rejects_bad_confidence(client, upload_dir):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    assert _analyze(client, headers, sid, confidence=0.0).status_code == 422


def test_image_analyze_maps_backend_errors(client, upload_dir, monkeypatch):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]

    def _corrupt(*a, **k):
        raise InvalidImageError("Could not read the uploaded image.")

    monkeypatch.setattr(vision_service, "analyze_image", _corrupt)
    assert _analyze(client, headers, sid).status_code == 422

    def _down(*a, **k):
        raise VisionUnavailableError("YOLO backend not installed.")

    monkeypatch.setattr(vision_service, "analyze_image", _down)
    r = _analyze(client, headers, sid)
    assert r.status_code == 503
    assert "YOLO" in r.json()["detail"]
