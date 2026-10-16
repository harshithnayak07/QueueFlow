"""S4 API tests: recorded-video upload -> estimate -> owner review.

The real YOLO stack is NOT required: vision_service.analyze_video is
monkeypatched with a stub. What IS tested end-to-end: auth + ownership,
upload validation (type/size), safe storage, session.people_count
update, QueueSnapshot history row, and the owner-review contract
(physical_people_remaining untouched until PATCH settings).
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
    InvalidVideoError,
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
    """Isolate on-disk uploads per test (route reads settings.upload_dir)."""
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    return tmp_path / "uploads"


def _stub_result(people: int = 7, frames: int = 4) -> VisionResult:
    return VisionResult(
        estimated_people=people,
        frames_sampled=frames,
        per_frame_counts=[people] * frames,
        method="stub-median",
        confidence_threshold=0.5,
        analyzed_at=datetime.now(timezone.utc),
    )


def _register(client, email="owner@shop.com"):
    return client.post(
        "/api/auth/register",
        json={"shop_name": "Shop", "email": email, "password": "secret123"},
    )


def _headers(client, email="owner@shop.com"):
    token = client.post(
        "/api/auth/login",
        json={"email": email, "password": "secret123"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _setup_owner(client, email="owner@shop.com"):
    assert _register(client, email).status_code == 201
    return _headers(client, email)


def _analyze(client, headers, sid, filename="queue.mp4",
             content=b"\x00" * 2048, **params):
    return client.post(
        f"/api/queue-sessions/{sid}/analyze-video",
        files={"file": (filename, content, "video/mp4")},
        headers=headers,
        params=params or None,
    )


# --- happy path + owner review ---


def test_analyze_updates_estimate_and_snapshot(client, db_session,
                                               upload_dir, monkeypatch):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    monkeypatch.setattr(
        vision_service, "analyze_video",
        lambda *a, **k: _stub_result(people=7, frames=4),
    )

    r = _analyze(client, headers, sid)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estimated_people"] == 7
    assert body["frames_sampled"] == 4
    assert body["needs_owner_review"] is True
    assert "pproximate" in body["estimate_note"]
    assert body["session"]["people_count"] == 7
    # Authoritative value untouched — owner must confirm it (FR-05).
    assert body["physical_people_remaining"] == 0
    assert body["session"]["physical_people_remaining"] == 0
    # Upload stored under a server-generated safe name (not client name).
    assert body["video_filename"].endswith(".mp4")
    assert body["video_filename"] != "queue.mp4"
    assert body["video_filename"].startswith(f"session_{sid}_")
    assert (upload_dir / body["video_filename"]).exists()

    snapshots = db_session.query(QueueSnapshot).all()
    assert len(snapshots) == 1
    assert snapshots[0].session_id == sid
    assert snapshots[0].people_count == 7
    assert snapshots[0].source == "vision"
    assert body["snapshot_id"] == snapshots[0].id


def test_owner_review_flow_confirm_physical(client, upload_dir, monkeypatch):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    monkeypatch.setattr(
        vision_service, "analyze_video",
        lambda *a, **k: _stub_result(people=9, frames=2),
    )
    assert _analyze(client, headers, sid).status_code == 200

    # Owner reviews the estimate, then confirms the physical remainder
    # (which may differ from the vision estimate).
    r = client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"physical_people_remaining": 6, "people_count": 8},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["physical_people_remaining"] == 6
    assert r.json()["people_count"] == 8  # owner-corrected estimate

    status = client.get(
        f"/api/queue-sessions/{sid}/status", headers=headers
    ).json()
    # Confirmed crowd estimate drives the session view: 6 people ahead.
    assert status["people_ahead"] == 6


# --- auth / ownership ---


def test_analyze_requires_auth_and_ownership(client, upload_dir):
    h1 = _setup_owner(client, "a@shop.com")
    h2 = _setup_owner(client, "b@shop.com")
    sid = client.post("/api/queue-sessions", json={}, headers=h1).json()["id"]

    assert _analyze(client, {}, sid).status_code in (401, 403)
    r = _analyze(client, h2, sid)
    assert r.status_code == 404  # foreign session hidden (no leak)
    r = _analyze(client, h1, 999999)
    assert r.status_code == 404


# --- upload validation ---


def test_analyze_rejects_bad_extension(client, upload_dir):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    r = _analyze(client, headers, sid, filename="notes.txt",
                 content=b"hello")
    assert r.status_code == 422


def test_analyze_rejects_empty_file(client, upload_dir):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    r = _analyze(client, headers, sid, content=b"")
    assert r.status_code == 422


def test_analyze_rejects_oversize(client, upload_dir, monkeypatch):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    monkeypatch.setattr(settings, "max_video_mb", 0)  # any bytes exceed
    r = _analyze(client, headers, sid, content=b"\x00" * 16)
    assert r.status_code == 413


def test_analyze_rejects_bad_params(client, upload_dir):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    r = _analyze(client, headers, sid, frame_stride=0)
    assert r.status_code == 422
    r = _analyze(client, headers, sid, confidence=0.0)
    assert r.status_code == 422


# --- backend failure mapping ---


def test_analyze_maps_backend_errors(client, upload_dir, monkeypatch):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]

    def _corrupt(*a, **k):
        raise InvalidVideoError("Could not open the uploaded video.")

    monkeypatch.setattr(vision_service, "analyze_video", _corrupt)
    assert _analyze(client, headers, sid).status_code == 422

    def _down(*a, **k):
        raise VisionUnavailableError("YOLO backend not installed.")

    monkeypatch.setattr(vision_service, "analyze_video", _down)
    r = _analyze(client, headers, sid)
    assert r.status_code == 503
    assert "YOLO" in r.json()["detail"]
