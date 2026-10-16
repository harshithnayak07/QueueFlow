"""One token per phone number per queue session.

- Contacts normalize to canonical form (+91… for Indian mobiles,
  lowercase for emails) before comparison AND storage.
- Any existing row (waiting/called/served/cancelled/expired) blocks a
  second token with the same number: HTTP 409 with a clear message.
- Different sessions are independent; concurrent same-number joins
  cannot both succeed (per-session lock + DB unique constraint).
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
from app.services.queue_service import normalize_customer_contact


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


def _ready_session(client, headers, physical=10):
    sid = client.post("/api/queue-sessions", json={}, headers=headers).json()["id"]
    r = client.patch(
        f"/api/queue-sessions/{sid}/settings",
        json={"physical_people_remaining": physical},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/queue-sessions/{sid}/activate-virtual-queue", headers=headers
    )
    assert r.status_code == 200, r.text
    return sid


def _join(client, sid, contact):
    return client.post(
        f"/api/public/queue-sessions/{sid}/tokens",
        json={"customer_contact": contact},
    )


# --- normalization ---


def test_normalize_indian_mobile_variants_converge():
    assert normalize_customer_contact("9972581651") == "+919972581651"
    assert normalize_customer_contact("+91 99725-81651") == "+919972581651"
    assert normalize_customer_contact("+919972581651") == "+919972581651"
    assert normalize_customer_contact("09972581651") == "+919972581651"
    # Idempotent (required for backfills).
    assert normalize_customer_contact("+919972581651") == "+919972581651"


def test_normalize_emails_lowercased_format_kept():
    assert normalize_customer_contact("  A@X.com ") == "a@x.com"


def test_normalize_rejects_garbage():
    for bad in ("", "   ", "abc", "123", "123456", "1" * 16, "+"):
        with pytest.raises(ValueError):
            normalize_customer_contact(bad)


# --- duplicate enforcement ---


def test_same_number_twice_is_409_with_token_number(client):
    headers = _setup_owner(client)
    sid = _ready_session(client, headers)

    first = _join(client, sid, "+919972581651")
    assert first.status_code == 201
    assert first.json()["token_number"] == 1

    r = _join(client, sid, "+919972581651")
    assert r.status_code == 409
    assert "already has token #1" in r.json()["detail"]


def test_formatting_variants_hit_the_same_number(client):
    headers = _setup_owner(client)
    sid = _ready_session(client, headers)
    assert _join(client, sid, "9972581651").status_code == 201
    for variant in ("+91 99725-81651", "+919972581651", "09972581651"):
        r = _join(client, sid, variant)
        assert r.status_code == 409, variant
        assert "already has token #1" in r.json()["detail"]


def test_served_number_still_blocked(client):
    headers = _setup_owner(client)
    sid = _ready_session(client, headers)
    assert _join(client, sid, "9972581651").status_code == 201
    tid = client.get(
        f"/api/queue-sessions/{sid}/tokens", headers=headers
    ).json()[0]["id"]
    assert client.patch(
        f"/api/owner/tokens/{tid}/status",
        json={"status": "served"}, headers=headers,
    ).status_code == 200
    r = _join(client, sid, "+919972581651")
    assert r.status_code == 409


def test_same_number_ok_in_different_session(client):
    headers = _setup_owner(client)
    sid1 = _ready_session(client, headers)
    sid2 = _ready_session(client, headers)
    assert _join(client, sid1, "9972581651").status_code == 201
    assert _join(client, sid2, "9972581651").status_code == 201


def test_email_case_variants_hit_the_same_contact(client):
    headers = _setup_owner(client)
    sid = _ready_session(client, headers)
    assert _join(client, sid, "A@X.com").status_code == 201
    r = _join(client, sid, "a@x.com")
    assert r.status_code == 409


def test_garbage_contact_is_422(client):
    headers = _setup_owner(client)
    sid = _ready_session(client, headers)
    assert _join(client, sid, "abc").status_code == 422
    assert _join(client, sid, "123").status_code == 422


def test_concurrent_same_number_joins_yield_one_token(tmp_path):
    import threading

    from fastapi import HTTPException

    from app.api.routes.public import create_token
    from app.core.security import hash_password
    from app.models.owner import Owner
    from app.models.queue_session import QueueSession
    from app.models.token import Token
    from app.schemas.token import TokenCreate

    engine = create_engine(
        f"sqlite:///{tmp_path}/race_contact.db",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    setup = Session()
    owner = Owner(shop_name="R", email="r@x.com",
                  password_hash=hash_password("secret123"))
    setup.add(owner)
    setup.commit()
    qs = QueueSession(owner_id=owner.id, physical_people_remaining=10,
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
            t = create_token(
                sid, TokenCreate(customer_contact="+91 99725-81651"), db)
            outcome = ("ok", t.token_number)
        except HTTPException as exc:
            outcome = (exc.status_code, None)
        finally:
            db.close()
        with results_lock:
            results.append(outcome)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    oks = [r for r in results if r[0] == "ok"]
    rejected = [r for r in results if r[0] != "ok"]
    assert len(oks) == 1, results
    assert all(code == 409 for code, _ in rejected)

    check = Session()
    try:
        rows = check.query(Token).filter(Token.session_id == sid).all()
        assert len(rows) == 1
        assert rows[0].customer_contact == "+919972581651"
    finally:
        check.close()
