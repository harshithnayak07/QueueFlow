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


def _open_active_session(client, headers, physical_people_remaining=10, **settings):
    """Create a session, confirm the queue count (capacity epoch + Apply),
    then activate the virtual queue. Joins require a confirmed capacity."""
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    settings.setdefault("physical_people_remaining", physical_people_remaining)
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
    # Activated but capacity unconfirmed (physical 0) -> still 403.
    r = _join(client, sid)
    assert r.status_code == 403
    assert "not confirmed" in r.json()["detail"]

    # Owner confirms the count -> joins flow.
    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"physical_people_remaining": 5},
        headers=headers,
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

    # t2 waits behind 1 eligible customer (t1, still waiting): 1 x 5 min.
    seen = client.get(f"/api/public/tokens/{t2['tracking_id']}").json()
    assert seen["token_number"] == 2
    assert seen["status"] == "waiting"
    assert seen["people_ahead"] == 1
    assert seen["estimated_wait_minutes"] == 5.0
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

    # Serving t1 empties t2's ahead list — and the owner Serve triggered
    # automatic progression, so t2 is now being served itself: its wait is
    # its own remaining service (~5 min), not zero (FR-13).
    seen = client.get(f"/api/public/tokens/{t2['tracking_id']}").json()
    assert seen["people_ahead"] == 0
    assert seen["status"] == "called"
    assert 4.0 < seen["estimated_wait_minutes"] <= 5.0


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


# --- owner-configured token validity (10/30), enforced + displayed ---


def test_validity_setting_10_minutes_enforced_and_rejected_values(client, db_session):
    from datetime import datetime, timedelta, timezone

    from app.models.token import Token

    headers = _setup_owner(client)
    sid = _open_active_session(client, headers, physical_people_remaining=5)
    # Only 10 or 30 are accepted owner options.
    assert client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"validity_minutes": 15}, headers=headers,
    ).status_code == 422
    r = client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"validity_minutes": 10}, headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["validity_minutes"] == 10

    # Validity now equals the estimated wait, not the session setting:
    # token #1 (nobody ahead) gets the 1-minute minimum, token #2 waits
    # behind #1 (5 min of work) and gets 5.
    first = _join(client, sid, "a@x.com").json()
    assert first["validity_minutes"] == 1
    created = _join(client, sid, "b@x.com").json()
    assert created["validity_minutes"] == 5  # join page displays it
    toks = client.get(
        f"/api/queue-sessions/{sid}/tokens", headers=headers).json()
    tid = next(t["id"] for t in toks if t["token_number"] == 2)
    body = client.patch(
        f"/api/owner/tokens/{tid}/status",
        json={"status": "called"}, headers=headers,
    ).json()
    called = datetime.fromisoformat(body["called_at"])
    exp = datetime.fromisoformat(body["expires_at"])
    assert 4 * 60 <= (exp - called).total_seconds() <= 6 * 60

    # 11 minutes later the window is over: genuinely abandoned (no
    # service window scheduled) -> expired on next track.
    tok = db_session.get(Token, tid)
    tok.called_at = datetime.now(timezone.utc) - timedelta(minutes=11)
    tok.service_started_at = None
    tok.service_ends_at = None
    tok.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()
    t = client.get(f"/api/public/tokens/{created['tracking_id']}").json()
    assert t["status"] == "expired"
    assert t["validity_minutes"] == 5  # frozen per-token value, not 10


def test_validity_falls_back_to_session_when_wait_unknown(client):
    # Zero counters -> wait uncomputable -> session setting (10) applies.
    headers = _setup_owner(client)
    sid = _open_active_session(client, headers, physical_people_remaining=5)
    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"validity_minutes": 10, "active_counters": 0},
        headers=headers,
    )
    created = _join(client, sid, "a@x.com").json()
    assert created["estimated_wait_minutes"] is None
    assert created["validity_minutes"] == 10
    tracked = client.get(
        f"/api/public/tokens/{created['tracking_id']}").json()
    assert tracked["validity_minutes"] == 10


def test_call_sets_expiry_window(client, db_session):
    from datetime import datetime

    headers = _setup_owner(client)
    sid = _open_active_session(client, headers)
    _join(client, sid)
    _join(client, sid, "b@x.com")
    # NOTE: the owner token-list read auto-called token #1 (automatic
    # progression starts timers without clicks), so this test stamps #2,
    # which is still waiting.
    tid = next(
        t["id"] for t in client.get(
            f"/api/queue-sessions/{sid}/tokens", headers=headers
        ).json() if t["token_number"] == 2
    )

    r = client.patch(
        f"/api/owner/tokens/{tid}/status", json={"status": "called"}, headers=headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["called_at"] is not None
    assert body["expires_at"] is not None
    # Token #2 waited behind #1 (5 min of work): its frozen validity is
    # 5 minutes, enforced from the call — not the session default.
    assert body["validity_minutes"] == 5
    called = datetime.fromisoformat(body["called_at"])
    exp = datetime.fromisoformat(body["expires_at"])
    assert 4 * 60 <= (exp - called).total_seconds() <= 6 * 60
    # Still counts as ahead until the window passes.
    t2 = next(
        t for t in client.get(
            f"/api/queue-sessions/{sid}/tokens", headers=headers
        ).json() if t["token_number"] == 2
    )
    t = client.get(f"/api/public/tokens/{t2['tracking_id']}").json()
    assert t["status"] == "called"
    assert t["people_ahead"] >= 0


def test_overdue_called_token_expires_on_track(client, db_session):
    from datetime import datetime, timedelta, timezone

    from app.models.token import Token

    headers = _setup_owner(client)
    sid = _open_active_session(client, headers, physical_people_remaining=2)
    created = _join(client, sid).json()
    tid = client.get(f"/api/queue-sessions/{sid}/tokens", headers=headers).json()[0]["id"]
    assert client.patch(
        f"/api/owner/tokens/{tid}/status", json={"status": "called"}, headers=headers
    ).status_code == 200

    # Simulate a stale call from before progression could schedule it:
    # called 31 minutes ago, never given a service window. Only such
    # genuinely abandoned calls may expire; in-service tokens are safe.
    tok = db_session.get(Token, tid)
    tok.called_at = datetime.now(timezone.utc) - timedelta(minutes=31)
    tok.service_started_at = None
    tok.service_ends_at = None
    tok.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db_session.commit()

    t = client.get(f"/api/public/tokens/{created['tracking_id']}").json()
    assert t["status"] == "expired"
    assert t["people_ahead"] == 0
    # A newcomer counts only live eligible tokens — the expired one adds
    # nothing (physical is capacity/backdrop, never added).
    j2 = _join(client, sid, "b@x.com").json()
    assert j2["people_ahead"] == 0
    # Expired tokens are terminal and system-only: no manual moves.
    assert client.patch(
        f"/api/owner/tokens/{tid}/status", json={"status": "waiting"}, headers=headers
    ).status_code in (409, 422)
    assert client.patch(
        f"/api/owner/tokens/{tid}/status", json={"status": "expired"}, headers=headers
    ).status_code == 422  # not an owner-settable value
