"""S3: queue-session CRUD, settings, live wait estimates, activation."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import get_db
from app.main import app
from app.models.token import TOKEN_STATUS_SERVED, TOKEN_STATUS_WAITING, Token


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


def _create(client, headers, **body):
    return client.post("/api/queue-sessions", json=body, headers=headers)


# --- create / list ---


def test_create_session_defaults_and_requires_auth(client):
    assert client.post("/api/queue-sessions", json={}).status_code == 401

    headers = _setup_owner(client)
    r = _create(client, headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "open"
    assert body["virtual_queue_enabled"] is False
    assert body["people_count"] is None
    assert body["active_counters"] == 1
    assert body["average_service_minutes"] == 5.0
    assert body["physical_people_remaining"] == 0


def test_create_rejects_invalid_settings(client):
    headers = _setup_owner(client)
    assert _create(client, headers, active_counters=-1).status_code == 422
    assert _create(client, headers, average_service_minutes=-2).status_code == 422


def test_list_is_scoped_to_owner(client):
    h1 = _setup_owner(client, "a@shop.com")
    h2 = _setup_owner(client, "b@shop.com")
    assert _create(client, h1).status_code == 201
    assert _create(client, h1).status_code == 201
    assert _create(client, h2).status_code == 201

    assert len(client.get("/api/queue-sessions", headers=h1).json()) == 2
    assert len(client.get("/api/queue-sessions", headers=h2).json()) == 1
    assert client.get("/api/queue-sessions").status_code == 401


# --- status + live wait ---


def test_status_computes_spec_example_wait(client):
    headers = _setup_owner(client)
    sid = _create(client, headers).json()["id"]
    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={
            "physical_people_remaining": 12,
            "active_counters": 2,
            "average_service_minutes": 4.0,
        },
        headers=headers,
    )

    r = client.get(f"/api/queue-sessions/{sid}/status", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # Session view = whole-queue crowd estimate: 12 x 4 / 2 = 24.
    assert body["people_ahead"] == 12
    assert body["estimated_wait_minutes"] == 24.0
    assert "pproximate" in body["estimate_note"]
    # 12 >= default threshold 10 -> recommended, still disabled (advisory only).
    assert body["virtual_queue_recommended"] is True
    assert body["session"]["virtual_queue_enabled"] is False


def test_status_zero_counters_is_not_computable(client):
    headers = _setup_owner(client)
    sid = _create(client, headers).json()["id"]
    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"physical_people_remaining": 12, "active_counters": 0},
        headers=headers,
    )
    body = client.get(f"/api/queue-sessions/{sid}/status", headers=headers).json()
    assert body["estimated_wait_minutes"] is None
    assert body["people_ahead"] == 12  # crowd still reported, wait unknown


def test_status_counts_active_virtual_tokens_ahead(client, db_session):
    headers = _setup_owner(client)
    sid = _create(client, headers).json()["id"]
    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={
            "physical_people_remaining": 8,
            "active_counters": 1,
            "average_service_minutes": 5.0,
        },
        headers=headers,
    )
    db_session.add_all(
        [
            Token(session_id=sid, token_number=1, customer_contact="a@x.com",
                  status=TOKEN_STATUS_WAITING),
            Token(session_id=sid, token_number=2, customer_contact="b@x.com",
                  status=TOKEN_STATUS_WAITING),
            # Served tokens no longer occupy queue positions.
            Token(session_id=sid, token_number=3, customer_contact="c@x.com",
                  status=TOKEN_STATUS_SERVED),
        ]
    )
    db_session.commit()

    body = client.get(f"/api/queue-sessions/{sid}/status", headers=headers).json()
    assert body["waiting_virtual_count"] == 2
    # Session crowd estimate stays physical-based: 8 x 5 / 1 = 40, even
    # with virtual tokens present (never added together).
    assert body["people_ahead"] == 8
    assert body["estimated_wait_minutes"] == 40.0
    assert body["token_capacity"] == 8
    assert body["tokens_issued"] == 0  # direct DB inserts bypass issuance
    assert body["tokens_remaining"] == 8


def test_status_hidden_for_other_owner(client):
    h1 = _setup_owner(client, "a@shop.com")
    h2 = _setup_owner(client, "b@shop.com")
    sid = _create(client, h1).json()["id"]
    r = client.get(f"/api/queue-sessions/{sid}/status", headers=h2)
    assert r.status_code == 404


# --- settings (incl. manual count correction) ---


def test_settings_update_and_manual_correction(client):
    headers = _setup_owner(client)
    sid = _create(client, headers).json()["id"]

    r = client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"physical_people_remaining": 7, "people_count": 9},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["physical_people_remaining"] == 7
    assert r.json()["people_count"] == 9  # owner-corrected vision estimate

    assert client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"physical_people_remaining": -1},
        headers=headers,
    ).status_code == 422

    h2 = _setup_owner(client, "other@shop.com")
    assert client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"physical_people_remaining": 1},
        headers=h2,
    ).status_code == 404


# --- activation (explicit owner action) ---


def test_activate_virtual_queue(client):
    headers = _setup_owner(client)
    sid = _create(client, headers).json()["id"]

    r = client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["virtual_queue_enabled"] is True

    # Idempotent re-activation.
    r2 = client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=headers
    )
    assert r2.status_code == 200
    assert r2.json()["virtual_queue_enabled"] is True

    # After activation the status no longer recommends (already active).
    status_body = client.get(
        f"/api/queue-sessions/{sid}/status", headers=headers
    ).json()
    assert status_body["virtual_queue_recommended"] is False


def test_activate_guards_closed_and_foreign_sessions(client):
    h1 = _setup_owner(client, "a@shop.com")
    h2 = _setup_owner(client, "b@shop.com")
    sid = _create(client, h1).json()["id"]

    assert client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=h2
    ).status_code == 404

    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"status": "closed"},
        headers=h1,
    )
    r = client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=h1
    )
    assert r.status_code == 409


# --- E3 deactivate: stop joins without closing the session ---


def test_deactivate_stops_joins_without_closing(client):
    headers = _setup_owner(client)
    sid = _create(client, headers, physical_people_remaining=5).json()["id"]

    # Inactive by default: deactivation is a no-op success.
    assert client.post(
        f"/api/queue-sessions/{sid}/deactivate-virtual-queue", headers=headers
    ).status_code == 200

    assert client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=headers
    ).status_code == 200
    r = client.post(
        f"/api/queue-sessions/{sid}/deactivate-virtual-queue", headers=headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["virtual_queue_enabled"] is False
    assert r.json()["status"] == "open"  # session NOT closed, tokens kept

    # New joins stop...
    j = client.post(
        f"/api/public/queue-sessions/{sid}/tokens",
        json={"customer_contact": "a@x.com"},
    )
    assert j.status_code == 403
    # ...and re-activation works without losing the session.
    assert client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=headers
    ).status_code == 200
    assert client.post(
        f"/api/public/queue-sessions/{sid}/tokens",
        json={"customer_contact": "a@x.com"},
    ).status_code == 201


def test_deactivate_guards(client):
    h1 = _setup_owner(client, "d1@shop.com")
    h2 = _setup_owner(client, "d2@shop.com")
    sid = _create(client, h1).json()["id"]

    assert client.post(
        f"/api/queue-sessions/{sid}/deactivate-virtual-queue", headers=h2
    ).status_code == 404  # foreign session hidden
    assert client.post(
        f"/api/queue-sessions/{sid}/deactivate-virtual-queue"
    ).status_code in (401, 403)

    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"status": "closed"},
        headers=h1,
    )
    assert client.post(
        f"/api/queue-sessions/{sid}/deactivate-virtual-queue", headers=h1
    ).status_code == 409


# --- E4 recommendation threshold is explicit and overridable ---


def test_status_threshold_param(client):
    headers = _setup_owner(client)
    sid = client.post(
        "/api/queue-sessions",
        json={"physical_people_remaining": 6},
        headers=headers,
    ).json()["id"]
    # Threshold compares against the session crowd estimate (physical 6):
    # below default 10, above an override of 5.
    default = client.get(
        f"/api/queue-sessions/{sid}/status", headers=headers
    ).json()
    assert default["recommendation_threshold"] == 10
    assert default["virtual_queue_recommended"] is False

    custom = client.get(
        f"/api/queue-sessions/{sid}/status",
        params={"threshold": 5},
        headers=headers,
    ).json()
    assert custom["recommendation_threshold"] == 5
    assert custom["virtual_queue_recommended"] is True

    assert client.get(
        f"/api/queue-sessions/{sid}/status",
        params={"threshold": 0},
        headers=headers,
    ).status_code == 422


# --- D2 QR code encodes this session's real join URL ---


def test_qr_returns_scannable_png_for_session_url(client):
    import qrcode
    from io import BytesIO

    headers = _setup_owner(client)
    sid = _create(client, headers).json()["id"]

    r = client.get(f"/api/queue-sessions/{sid}/qr", headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(r.content) > 100

    # Byte-identical to a locally generated QR of the exact join URL the
    # dashboard shows (same lib, deterministic encoding) — proves content.
    expected = BytesIO()
    qrcode.make(f"http://testserver/join.html?session={sid}").save(
        expected, format="PNG"
    )
    assert r.content == expected.getvalue()


def test_qr_guards(client):
    h1 = _setup_owner(client, "q1@shop.com")
    h2 = _setup_owner(client, "q2@shop.com")
    sid = _create(client, h1).json()["id"]

    assert client.get(f"/api/queue-sessions/{sid}/qr", headers=h2).status_code == 404
    assert client.get(f"/api/queue-sessions/{sid}/qr").status_code in (401, 403)
    assert client.get("/api/queue-sessions/999999/qr", headers=h1).status_code == 404
