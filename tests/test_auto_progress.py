"""Automatic time-based queue progression (backend-authoritative).

Fake clock throughout: progress_session(db, session, now) is driven
directly with explicit datetimes — no real minutes elapse and no
frontend polling or customer activity is involved. `T0` is anchored at
the real current time so API reads (which reconcile lazily with real
time) stay stable during each fast test.
"""

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.database.base import Base
from app.database.session import get_db
from app.main import app
from app.models.owner import Owner
from app.models.queue_session import QueueSession
from app.models.token import Token
from app.services.queue_service import progress_session


def t0():
    return datetime.now(timezone.utc).replace(microsecond=0)


def aware(dt):
    return dt if dt is None or dt.tzinfo is not None else dt.replace(
        tzinfo=timezone.utc)


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


def _seed(db_session, email="auto@shop.com", physical=7, avg=2.0,
          counters=1, tokens=3, virtual=True):
    owner = Owner(shop_name="Auto", email=email,
                  password_hash=hash_password("secret123"))
    db_session.add(owner)
    db_session.commit()
    qs = QueueSession(
        owner_id=owner.id,
        physical_people_remaining=physical,
        average_service_minutes=avg,
        active_counters=counters,
        virtual_queue_enabled=virtual,
    )
    db_session.add(qs)
    db_session.commit()
    toks = []
    for i in range(1, tokens + 1):
        t = Token(session_id=qs.id, token_number=i,
                  customer_contact=f"c{i}@x.com")
        db_session.add(t)
        toks.append(t)
    db_session.commit()
    return owner, qs, toks


def _statuses(db_session, sid):
    rows = db_session.query(Token).filter(
        Token.session_id == sid).order_by(Token.token_number).all()
    return [(t.token_number, t.status) for t in rows]


def _api_owner(client, email="api@shop.com"):
    assert client.post(
        "/api/auth/register",
        json={"shop_name": "API", "email": email, "password": "secret123"},
    ).status_code == 201
    token = client.post(
        "/api/auth/login",
        json={"email": email, "password": "secret123"},
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# --- 1: activation starts the first timer ---


def test_activation_starts_first_eligible_timer(client):
    headers = _api_owner(client)
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"physical_people_remaining": 7, "average_service_minutes": 2.0,
              "active_counters": 1},
        headers=headers,
    )
    assert client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=headers
    ).status_code == 200
    for i in range(3):
        assert client.post(
            f"/api/public/queue-sessions/{sid}/tokens",
            json={"customer_contact": f"c{i}@x.com"},
        ).status_code == 201
    # First status read reconciles lazily with real time: token #1 starts.
    # Session ahead is the confirmed crowd (7); per-token views differ.
    before = datetime.now(timezone.utc)
    status = client.get(
        f"/api/queue-sessions/{sid}/status", headers=headers).json()
    assert status["people_ahead"] == 7

    toks = client.get(
        f"/api/queue-sessions/{sid}/tokens", headers=headers
    ).json()
    first = next(t for t in toks if t["token_number"] == 1)
    assert first["status"] == "called"
    assert first["service_started_at"] is not None
    assert first["service_ends_at"] is not None
    start = aware(datetime.fromisoformat(first["service_started_at"]))
    end = aware(datetime.fromisoformat(first["service_ends_at"]))
    assert (end - start).total_seconds() == 120
    assert abs((start - before).total_seconds()) < 120
    assert all(t["status"] == "waiting"
               for t in toks if t["token_number"] != 1)


# --- 2+3: timing respected, then serve + advance ---


def test_current_unchanged_before_duration_then_advances(db_session):
    _, qs, _ = _seed(db_session, avg=2.0, counters=1, tokens=3)
    base = t0()

    assert progress_session(db_session, qs, base) == 0
    assert _statuses(db_session, qs.id) == [
        (1, "called"), (2, "waiting"), (3, "waiting")]
    t1 = db_session.query(Token).filter(
        Token.session_id == qs.id, Token.token_number == 1).one()
    assert aware(t1.service_started_at) == base
    assert aware(t1.service_ends_at) == base + timedelta(minutes=2)

    # One minute in: nothing may change.
    assert progress_session(db_session, qs, base + timedelta(minutes=1)) == 0
    assert _statuses(db_session, qs.id) == [
        (1, "called"), (2, "waiting"), (3, "waiting")]

    # At the scheduled completion: #1 served (at ends_at, never early),
    # #2 becomes current with the chained window.
    assert progress_session(db_session, qs, base + timedelta(minutes=2)) == 1
    assert _statuses(db_session, qs.id) == [
        (1, "served"), (2, "called"), (3, "waiting")]
    db_session.expire_all()
    t1 = db_session.query(Token).filter(
        Token.session_id == qs.id, Token.token_number == 1).one()
    t2 = db_session.query(Token).filter(
        Token.session_id == qs.id, Token.token_number == 2).one()
    assert aware(t1.served_at) == base + timedelta(minutes=2)
    assert aware(t2.service_started_at) == base + timedelta(minutes=2)
    assert aware(t2.service_ends_at) == base + timedelta(minutes=4)


# --- 4+5: full sequential run with correct counts ---


def test_full_run_serves_in_order_with_correct_counts(db_session):
    _, qs, _ = _seed(db_session, avg=2.0, counters=1, tokens=3)
    base = t0()

    progress_session(db_session, qs, base)
    assert _statuses(db_session, qs.id) == [
        (1, "called"), (2, "waiting"), (3, "waiting")]
    progress_session(db_session, qs, base + timedelta(minutes=2))
    assert _statuses(db_session, qs.id) == [
        (1, "served"), (2, "called"), (3, "waiting")]
    progress_session(db_session, qs, base + timedelta(minutes=4))
    assert _statuses(db_session, qs.id) == [
        (1, "served"), (2, "served"), (3, "called")]
    assert progress_session(db_session, qs, base + timedelta(minutes=6)) == 1
    assert _statuses(db_session, qs.id) == [
        (1, "served"), (2, "served"), (3, "served")]
    # Steady state: further passes are no-ops.
    assert progress_session(db_session, qs, base + timedelta(minutes=99)) == 0

    served = db_session.query(Token).filter(
        Token.session_id == qs.id, Token.status == "served").count()
    waiting = db_session.query(Token).filter(
        Token.session_id == qs.id, Token.status == "waiting").count()
    assert (served, waiting) == (3, 0)


# --- 6+7: dashboard and tracking agree; no frontend involved ---


def test_dashboard_and_tracking_report_same_progressed_state(client, db_session):
    headers = _api_owner(client, email="prog@shop.com")
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"physical_people_remaining": 7, "average_service_minutes": 2.0,
              "active_counters": 1},
        headers=headers,
    )
    client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=headers
    )
    created = [client.post(
        f"/api/public/queue-sessions/{sid}/tokens",
        json={"customer_contact": f"c{i}@x.com"},
    ).json() for i in range(2)]

    # Drive progression purely in the backend with a fake clock.
    qs = db_session.query(QueueSession).filter_by(id=sid).one()
    base = t0()
    progress_session(db_session, qs, base)
    progress_session(db_session, qs, base + timedelta(minutes=2))

    # Both APIs report the identical persisted truth (lazy reads with real
    # time stay behind the fake-clock windows, so nothing shifts).
    status = client.get(
        f"/api/queue-sessions/{sid}/status", headers=headers).json()
    mine = client.get(
        f"/api/public/tokens/{created[1]['tracking_id']}").json()
    assert mine["status"] == "called"
    # Two views, same backend: #2's own position (nobody eligible ahead
    # of it) vs the session crowd estimate (7 x 2 / 1 = 14).
    assert mine["people_ahead"] == 0
    assert status["people_ahead"] == 7
    assert status["estimated_wait_minutes"] == 14.0
    assert 3.0 < mine["estimated_wait_minutes"] <= 4.0
    owner_list = client.get(
        f"/api/queue-sessions/{sid}/tokens", headers=headers).json()
    assert [t["status"] for t in owner_list] == ["served", "called"]


# --- 8: restart reconciles overdue tokens without duplicates ---


def test_restart_reconciles_overdue_without_duplicates(db_session):
    _, qs, _ = _seed(db_session, avg=2.0, counters=1, tokens=3)
    base = t0()
    sid = qs.id
    progress_session(db_session, qs, base)

    # "Restart": drop all in-memory state, work purely from persisted rows.
    db_session.expunge_all()
    fresh = db_session.query(QueueSession).filter_by(id=sid).one()
    # Three periods elapse with nobody watching: one pass settles all.
    assert progress_session(
        db_session, fresh, base + timedelta(minutes=7)) == 3
    assert _statuses(db_session, sid) == [
        (1, "served"), (2, "served"), (3, "served")]

    db_session.expunge_all()
    served_ats = [
        aware(t.served_at) for t in db_session.query(Token).filter(
            Token.session_id == sid).order_by(Token.token_number).all()]
    assert served_ats == [base + timedelta(minutes=2 * i) for i in (1, 2, 3)]


# --- 9: repeat reconciliation is idempotent ---


def test_repeat_reconcile_never_reserves_or_resets(db_session):
    _, qs, _ = _seed(db_session, avg=2.0, counters=1, tokens=2)
    base = t0()
    # First pass only starts the timer; the second completes it.
    assert progress_session(db_session, qs, base) == 0
    assert progress_session(db_session, qs, base + timedelta(minutes=2)) == 1
    first = [(aware(t.service_started_at), aware(t.service_ends_at),
              aware(t.served_at), t.status)
             for t in db_session.query(Token).filter(
                 Token.session_id == qs.id).order_by(Token.token_number)]
    assert progress_session(db_session, qs, base + timedelta(minutes=2)) == 0
    assert progress_session(db_session, qs, base + timedelta(minutes=3)) == 0
    second = [(aware(t.service_started_at), aware(t.service_ends_at),
               aware(t.served_at), t.status)
              for t in db_session.query(Token).filter(
                  Token.session_id == qs.id).order_by(Token.token_number)]
    assert first == second


# --- multi-counter: concurrent service, never collapsed to one ---


def test_two_counters_serve_concurrently(db_session):
    _, qs, _ = _seed(db_session, avg=2.0, counters=2, tokens=4)
    base = t0()

    progress_session(db_session, qs, base)
    assert _statuses(db_session, qs.id) == [
        (1, "called"), (2, "called"), (3, "waiting"), (4, "waiting")]
    progress_session(db_session, qs, base + timedelta(minutes=2))
    assert _statuses(db_session, qs.id) == [
        (1, "served"), (2, "served"), (3, "called"), (4, "called")]
    progress_session(db_session, qs, base + timedelta(minutes=4))
    assert _statuses(db_session, qs.id) == [
        (1, "served"), (2, "served"), (3, "served"), (4, "served")]


def test_zero_counters_never_progress(db_session):
    _, qs, _ = _seed(db_session, avg=2.0, counters=0, tokens=2)
    base = t0()
    assert progress_session(db_session, qs, base) == 0
    assert progress_session(db_session, qs, base + timedelta(hours=5)) == 0
    assert _statuses(db_session, qs.id) == [(1, "waiting"), (2, "waiting")]


# --- session lifecycle: closed frozen, deactivated still serves ---


def test_closed_session_does_not_progress(db_session):
    _, qs, _ = _seed(db_session, avg=2.0, counters=1, tokens=2)
    qs.status = "closed"
    db_session.commit()
    assert progress_session(db_session, qs, t0()) == 0
    assert _statuses(db_session, qs.id) == [(1, "waiting"), (2, "waiting")]


def test_deactivated_open_session_still_progresses(db_session):
    _, qs, _ = _seed(db_session, avg=2.0, counters=1, tokens=2, virtual=False)
    base = t0()
    progress_session(db_session, qs, base)
    assert _statuses(db_session, qs.id)[0] == (1, "called")
    progress_session(db_session, qs, base + timedelta(minutes=2))
    assert _statuses(db_session, qs.id)[0] == (1, "served")


# --- owner manual Call joins the automatic flow ---


def test_manual_call_gets_window_and_flow_continues(db_session):
    _, qs, _ = _seed(db_session, avg=2.0, counters=1, tokens=3)
    base = t0()
    manual = db_session.query(Token).filter(
        Token.session_id == qs.id, Token.token_number == 3).one()
    manual.status = "called"  # owner jumps the queue; no window yet
    db_session.commit()

    progress_session(db_session, qs, base)
    db_session.expire_all()
    # Owner intent wins over FIFO: #3 serves first with a fresh window.
    first = db_session.query(Token).filter(
        Token.session_id == qs.id, Token.token_number == 3).one()
    assert first.status == "called"
    assert aware(first.service_started_at) == base
    progress_session(db_session, qs, base + timedelta(minutes=2))
    assert _statuses(db_session, qs.id) == [
        (1, "called"), (2, "waiting"), (3, "served")]


# --- in-service tokens are never expired mid-service ---


def test_in_service_token_survives_validity_sweep(db_session):
    from app.services.queue_service import expire_overdue_tokens

    _, qs, _ = _seed(db_session, avg=60.0, counters=1, tokens=1)
    base = t0()
    progress_session(db_session, qs, base)  # window [base, base+60m]
    # 35 minutes later: past the 30-min validity, still in service.
    assert expire_overdue_tokens(
        db_session, qs.id, base + timedelta(minutes=35)) == 0
    assert _statuses(db_session, qs.id) == [(1, "called")]
    # Service completes normally at 60 minutes.
    assert progress_session(
        db_session, qs, base + timedelta(minutes=60)) == 1
    assert _statuses(db_session, qs.id) == [(1, "served")]
