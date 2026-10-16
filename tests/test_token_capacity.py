"""Core queue business rule: the confirmed physical count IS the token capacity.

- At most `physical` tokens per snapshot epoch; served/cancelled/expired
  tokens do NOT free slots (only a new owner Apply starts a new epoch).
- Waits never add virtuals: 7 x 3 / 1 = 21 even with 7 tokens present.
- Concurrent joins cannot exceed capacity (per-session lock + test).
- Legacy over-capacity sessions keep every token; only new joins stop.
"""

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


def _setup_owner(client, email="owner@shop.com"):
    assert client.post(
        "/api/auth/register",
        json={"shop_name": "Shop", "email": email, "password": "secret123"},
    ).status_code == 201
    token = client.post(
        "/api/auth/login",
        json={"email": email, "password": "secret123"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _confirm_and_activate(client, headers, sid, physical,
                          avg=3.0, counters=1):
    r = client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={
            "physical_people_remaining": physical,
            "average_service_minutes": avg,
            "active_counters": counters,
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=headers
    )
    assert r.status_code == 200, r.text
    return sid


def _new_session(client, headers):
    return client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]


def _join(client, sid, contact):
    return client.post(
        f"/api/queue-sessions/{sid}/tokens".replace(
            "/api/queue-sessions/", "/api/public/queue-sessions/"),
        json={"customer_contact": contact},
    )


def _owner_tokens(client, headers, sid):
    return client.get(
        f"/api/queue-sessions/{sid}/tokens", headers=headers
    ).json()


def _call(client, headers, tid):
    return client.patch(
        f"/api/owner/tokens/{tid}/status",
        json={"status": "called"},
        headers=headers,
    )


def _serve(client, headers, tid):
    return client.patch(
        f"/api/owner/tokens/{tid}/status",
        json={"status": "served"},
        headers=headers,
    )


# --- 1+2: capacity 7 allows exactly 7, the 8th is "Queue is full" ---


def test_confirmed_count_7_allows_at_most_7_tokens(client):
    headers = _setup_owner(client)
    sid = _confirm_and_activate(client, headers, _new_session(client, headers), 7)

    numbers = []
    for i in range(7):
        r = _join(client, sid, f"c{i}@x.com")
        assert r.status_code == 201, r.text
        numbers.append(r.json()["token_number"])
    assert numbers == [1, 2, 3, 4, 5, 6, 7]

    body = client.get(
        f"/api/queue-sessions/{sid}/status", headers=headers
    ).json()
    assert body["token_capacity"] == 7
    assert body["tokens_issued"] == 7
    assert body["tokens_remaining"] == 0


def test_eighth_join_rejected_queue_full(client):
    headers = _setup_owner(client)
    sid = _confirm_and_activate(client, headers, _new_session(client, headers), 7)
    for i in range(7):
        assert _join(client, sid, f"c{i}@x.com").status_code == 201

    r = _join(client, sid, "late@x.com")
    assert r.status_code == 403
    assert "Queue is full" in r.json()["detail"]


# --- 3+4: 7x3/1 = 21 with 7 tokens; dashboard and tracking agree ---


def test_wait_21_with_7_tokens_dashboard_and_tracking_agree(client):
    headers = _setup_owner(client)
    sid = _confirm_and_activate(client, headers, _new_session(client, headers), 7,
                                avg=3.0, counters=1)
    joined = [_join(client, sid, f"c{i}@x.com").json() for i in range(7)]

    status = client.get(
        f"/api/queue-sessions/{sid}/status", headers=headers
    ).json()
    # Session view: all 7 live tokens -> 7 x 3 / 1 = 21 min.
    assert status["people_ahead"] == 7
    assert status["estimated_wait_minutes"] == 21.0

    # Same rule on the tracking page, from each token's own position:
    # token #7 waits behind #1-#6 -> 6 x 3 = 18; token #1 waits behind
    # nobody -> ~0.
    last = client.get(
        f"/api/public/tokens/{joined[6]['tracking_id']}"
    ).json()
    assert last["people_ahead"] == 6
    assert last["estimated_wait_minutes"] == 18.0
    first = client.get(
        f"/api/public/tokens/{joined[0]['tracking_id']}"
    ).json()
    # Nobody ahead of #1 — and the status read auto-started its service,
    # so its wait is its own remaining time (~3 min), not zero.
    assert first["people_ahead"] == 0
    assert first["status"] == "called"
    assert 2.0 < first["estimated_wait_minutes"] <= 3.0


# --- 5: concurrency ---


def test_concurrent_joins_cannot_exceed_capacity(tmp_path):
    import threading

    from fastapi import HTTPException

    from app.api.routes.public import create_token
    from app.core.security import hash_password
    from app.models.owner import Owner
    from app.models.queue_session import QueueSession
    from app.models.token import Token
    from app.schemas.token import TokenCreate

    engine = create_engine(
        f"sqlite:///{tmp_path}/race.db",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    setup = Session()
    owner = Owner(shop_name="R", email="r@x.com",
                  password_hash=hash_password("secret123"))
    setup.add(owner)
    setup.commit()
    qs = QueueSession(owner_id=owner.id, physical_people_remaining=7,
                      virtual_queue_enabled=True)
    setup.add(qs)
    setup.commit()
    sid = qs.id
    setup.close()

    tls = threading.local()

    def session_for_thread():
        s = getattr(tls, "s", None)
        if s is None:
            s = Session()
            tls.s = s
        return s

    results = []
    results_lock = threading.Lock()

    def attempt(i):
        db = session_for_thread()
        try:
            t = create_token(sid, TokenCreate(customer_contact=f"c{i}@x.com"), db)
            outcome = ("ok", t.token_number)
        except HTTPException as exc:
            outcome = (exc.status_code, None)
        finally:
            db.close()
        with results_lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    oks = [r for r in results if r[0] == "ok"]
    rejected = [r for r in results if r[0] != "ok"]
    assert len(oks) == 7, results
    assert all(code == 403 for code, _ in rejected)
    assert len({n for _, n in oks}) == 7  # unique token numbers

    check = Session()
    try:
        assert check.query(Token).filter(Token.session_id == sid).count() == 7
    finally:
        check.close()


# --- 6: legacy over-capacity sessions keep every token ---


def test_legacy_over_capacity_keeps_tokens_rejects_joins(client, db_session):
    headers = _setup_owner(client)
    sid = _new_session(client, headers)
    # Legacy shape: 9 tokens issued against a count later confirmed as 7.
    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"physical_people_remaining": 9},
        headers=headers,
    )
    client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=headers
    )
    created = [_join(client, sid, f"c{i}@x.com").json() for i in range(9)]
    # Owner re-confirms a smaller count (new epoch, 9 active carried over).
    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"physical_people_remaining": 7},
        headers=headers,
    )

    # Nothing deleted or cancelled by the fix.
    assert len(_owner_tokens(client, headers, sid)) == 9
    # Every existing token still tracks.
    for c in created:
        assert client.get(f"/api/public/tokens/{c['tracking_id']}").status_code == 200
    # New joins stop until lifecycle rules permit them again.
    r = _join(client, sid, "late@x.com")
    assert r.status_code == 403
    assert "Queue is full" in r.json()["detail"]
    # Owner can still serve the existing queue.
    tid = _owner_tokens(client, headers, sid)[0]["id"]
    assert client.patch(
        f"/api/owner/tokens/{tid}/status",
        json={"status": "served"},
        headers=headers,
    ).status_code == 200


# --- 7: new snapshot Apply updates capacity ---


def test_new_snapshot_apply_updates_capacity(client):
    headers = _setup_owner(client)
    sid = _confirm_and_activate(client, headers, _new_session(client, headers), 7)
    for i in range(7):
        assert _join(client, sid, f"c{i}@x.com").status_code == 201
    assert _join(client, sid, "full@x.com").status_code == 403

    # Owner serves 2 (active 5), then applies a fresh count of 9:
    # new epoch, counter restarts from the 5 carried-over active tokens.
    owner_tokens = _owner_tokens(client, headers, sid)
    assert _serve(client, headers, owner_tokens[0]["id"]).status_code == 200
    assert _serve(client, headers, owner_tokens[1]["id"]).status_code == 200
    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"physical_people_remaining": 9},
        headers=headers,
    )
    body = client.get(
        f"/api/queue-sessions/{sid}/status", headers=headers
    ).json()
    assert body["token_capacity"] == 9
    assert body["tokens_issued"] == 5
    assert body["tokens_remaining"] == 4

    for i, n in enumerate((8, 9, 10, 11)):
        assert _join(client, sid, f"n{i}@x.com").json()["token_number"] == n
    assert _join(client, sid, "over@x.com").status_code == 403


# --- 8: served tokens do not free slots; active never exceeds capacity ---


def test_served_does_not_free_capacity(client, db_session):
    from app.models.token import Token

    headers = _setup_owner(client)
    sid = _confirm_and_activate(client, headers, _new_session(client, headers), 4)
    for i in range(4):
        assert _join(client, sid, f"c{i}@x.com").status_code == 201
    owner_tokens = _owner_tokens(client, headers, sid)
    assert _serve(client, headers, owner_tokens[0]["id"]).status_code == 200
    assert _serve(client, headers, owner_tokens[1]["id"]).status_code == 200

    # Only 2 active, yet the epoch is spent: no reuse by serving.
    assert _join(client, sid, "extra@x.com").status_code == 403
    active = db_session.query(Token).filter(
        Token.session_id == sid,
        Token.status.in_(("waiting", "called")),
    ).count()
    assert active == 2
    assert active <= 4


# --- 9: zero counters never divide by zero ---


def test_zero_counters_no_divide_by_zero_with_tokens(client):
    headers = _setup_owner(client)
    sid = _new_session(client, headers)
    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"physical_people_remaining": 7, "active_counters": 0},
        headers=headers,
    )
    client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=headers
    )
    created = _join(client, sid, "c@x.com").json()
    assert created["estimated_wait_minutes"] is None

    body = client.get(
        f"/api/queue-sessions/{sid}/status", headers=headers
    ).json()
    assert body["people_ahead"] == 7  # confirmed crowd, one live token
    assert body["estimated_wait_minutes"] is None
