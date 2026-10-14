"""S5: virtual-queue token APIs — public join/tracking, owner transitions."""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.base import Base
from app.database.session import get_db
from app.main import app


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


def _open_active_session(client, headers, **settings):
    """Create a session, set queue settings, activate the virtual queue."""
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    if settings:
        r = client.patch(
            f"/api/queue-sessions/{sid}/settings", json=settings, headers=headers
        )
        assert r.status_code == 200, r.text
    r = client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=headers
    )
    assert r.status_code == 200, r.text
    return sid


def _join(client, sid, contact="cust@example.com"):
    return client.post(
        f"/api/public/queue-sessions/{sid}/tokens",
        json={"customer_contact": contact},
    )


# --- public join ---


def test_join_requires_active_virtual_queue(client):
    headers = _setup_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]

    # Virtual queue not yet activated -> 403, no token leaked.
    r = _join(client, sid)
    assert r.status_code == 403

    client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=headers
    )
    r = _join(client, sid)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token_number"] == 1
    assert body["status"] == "waiting"
    assert len(body["tracking_id"]) >= 32
    assert body["tracking_id"] != str(body["token_number"])
    # Privacy: no contact, no internal token id.
    assert "customer_contact" not in body
    assert "id" not in body
    assert "pproximate" in body["estimate_note"]


def test_join_numbers_increment_and_reject_bad_input(client):
    headers = _setup_owner(client)
    sid = _open_active_session(client, headers)
    assert _join(client, sid, "a@x.com").json()["token_number"] == 1
    assert _join(client, sid, "b@x.com").json()["token_number"] == 2
    # Blank contact rejected; unknown session is 404.
    assert _join(client, sid, "  ").status_code == 422
    assert _join(client, 999999, "c@x.com").status_code == 404


def test_join_closed_session_is_409(client):
    headers = _setup_owner(client)
    sid = _open_active_session(client, headers)
    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"status": "closed"},
        headers=headers,
    )
    assert _join(client, sid).status_code == 409


# --- public tracking + live recalculation ---


def test_tracking_shows_wait_and_recalculates(client):
    headers = _setup_owner(client)
    sid = _open_active_session(
        client,
        headers,
        physical_people_remaining=8,
        active_counters=1,
        average_service_minutes=5.0,
    )
    t1 = _join(client, sid, "one@x.com").json()
    t2 = _join(client, sid, "two@x.com").json()

    # t2 sits behind 8 physical + t1: 9 ahead -> ~45 min.
    seen = client.get(f"/api/public/tokens/{t2['tracking_id']}").json()
    assert seen["token_number"] == 2
    assert seen["status"] == "waiting"
    assert seen["people_ahead"] == 9
    assert seen["estimated_wait_minutes"] == 45.0
    assert seen["recommended_return_at"] is not None
    assert "customer_contact" not in seen
    assert "tracking_id" not in seen

    # Owner serves t1 (need its internal id from the owner list).
    owner_tokens = client.get(
        f"/api/queue-sessions/{sid}/tokens", headers=headers
    ).json()
    tid1 = next(t["id"] for t in owner_tokens if t["token_number"] == 1)
    r = client.patch(
        f"/api/owner/tokens/{tid1}/status",
        json={"status": "served"},
        headers=headers,
    )
    assert r.status_code == 200

    # t2's estimate drops live: 8 physical + 0 earlier -> ~40 min (FR-13).
    seen = client.get(f"/api/public/tokens/{t2['tracking_id']}").json()
    assert seen["people_ahead"] == 8
    assert seen["estimated_wait_minutes"] == 40.0


def test_tracking_unknown_id_is_404(client):
    assert client.get("/api/public/tokens/does-not-exist").status_code == 404


def test_tracking_terminal_token_has_no_wait(client):
    headers = _setup_owner(client)
    sid = _open_active_session(client, headers, physical_people_remaining=5)
    created = _join(client, sid, "gone@x.com").json()
    owner_tokens = client.get(
        f"/api/queue-sessions/{sid}/tokens", headers=headers
    ).json()
    tid = owner_tokens[0]["id"]
    assert (
        client.patch(
            f"/api/owner/tokens/{tid}/status",
            json={"status": "cancelled"},
            headers=headers,
        ).status_code
        == 200
    )
    seen = client.get(f"/api/public/tokens/{created['tracking_id']}").json()
    assert seen["status"] == "cancelled"
    assert seen["people_ahead"] == 0
    assert seen["recommended_return_at"] is None


# --- owner transitions ---


def test_owner_transition_called_then_served_stamps_times(client):
    headers = _setup_owner(client)
    sid = _open_active_session(client, headers)
    _join(client, sid, "a@x.com")
    tid = client.get(
        f"/api/queue-sessions/{sid}/tokens", headers=headers
    ).json()[0]["id"]

    r = client.patch(
        f"/api/owner/tokens/{tid}/status",
        json={"status": "called"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "called"
    assert r.json()["called_at"] is not None

    r = client.patch(
        f"/api/owner/tokens/{tid}/status",
        json={"status": "served"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "served"
    assert r.json()["served_at"] is not None
    # Contact IS visible to the owner (auth + ownership guarded).
    assert r.json()["customer_contact"] == "a@x.com"


def test_owner_transition_guards(client):
    h1 = _setup_owner(client, "a@shop.com")
    h2 = _setup_owner(client, "b@shop.com")
    sid = _open_active_session(client, h1)
    _join(client, sid, "a@x.com")
    tid = client.get(
        f"/api/queue-sessions/{sid}/tokens", headers=h1
    ).json()[0]["id"]

    # Unauthenticated and foreign owner.
    assert (
        client.patch(
            f"/api/owner/tokens/{tid}/status", json={"status": "called"}
        ).status_code
        == 401
    )
    assert (
        client.patch(
            f"/api/owner/tokens/{tid}/status",
            json={"status": "called"},
            headers=h2,
        ).status_code
        == 404
    )
    assert client.patch(
        "/api/owner/tokens/999999/status",
        json={"status": "called"},
        headers=h1,
    ).status_code == 404
    # Unknown status value is a schema 422.
    assert (
        client.patch(
            f"/api/owner/tokens/{tid}/status",
            json={"status": "teleported"},
            headers=h1,
        ).status_code
        == 422
    )

    # Serve, then try to resurrect -> 409. Same-status retry -> 200 noop.
    assert (
        client.patch(
            f"/api/owner/tokens/{tid}/status",
            json={"status": "served"},
            headers=h1,
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/owner/tokens/{tid}/status",
            json={"status": "waiting"},
            headers=h1,
        ).status_code
        == 409
    )
    assert (
        client.patch(
            f"/api/owner/tokens/{tid}/status",
            json={"status": "served"},
            headers=h1,
        ).status_code
        == 200
    )


# --- owner list ---


def test_owner_token_list_scoped_and_ordered(client, db_session):
    h1 = _setup_owner(client, "a@shop.com")
    h2 = _setup_owner(client, "b@shop.com")
    sid = _open_active_session(client, h1)
    _join(client, sid, "one@x.com")
    _join(client, sid, "two@x.com")

    body = client.get(f"/api/queue-sessions/{sid}/tokens", headers=h1).json()
    assert [t["token_number"] for t in body] == [1, 2]
    assert all(t["customer_contact"] for t in body)

    assert (
        client.get(f"/api/queue-sessions/{sid}/tokens", headers=h2).status_code
        == 404
    )
    assert client.get(f"/api/queue-sessions/{sid}/tokens").status_code == 401
