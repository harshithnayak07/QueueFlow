"""Google Sign-In: POST /api/auth/google (find-or-create via verified email).

The Google verifier is monkeypatched — no network in CI. Live failure paths
(bad signature, unreachable Google) are covered by the 401/503 branches.
"""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.api.routes.auth as auth_routes
from app.database.base import Base
from app.database.session import get_db
from app.main import app
from app.models.owner import Owner


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


def _fake_claims(email="guser@gmail.com", name="G User", verified=True,
                 sub="google-sub-123"):
    return {"email": email, "email_verified": verified, "name": name,
            "sub": sub}


def test_google_login_creates_owner_on_first_sign_in(client, db_session, monkeypatch):
    monkeypatch.setattr(
        auth_routes, "verify_google_id_token", lambda tok: _fake_claims()
    )
    r = client.post("/api/auth/google", json={"id_token": "fake.valid.token"})
    assert r.status_code == 200, r.text
    assert r.json()["token_type"] == "bearer"

    owner = db_session.query(Owner).filter_by(email="guser@gmail.com").one()
    assert owner.shop_name == "G User"
    assert owner.google_sub == "google-sub-123"
    # Random unusable password hash — password login with anything fails.
    assert owner.password_hash != "fake.valid.token"


def test_google_login_reuses_existing_owner(client, db_session, monkeypatch):
    # Same Google subject twice: returning user, one row, two logins.
    monkeypatch.setattr(
        auth_routes, "verify_google_id_token", lambda tok: _fake_claims()
    )
    assert client.post("/api/auth/google", json={"id_token": "a"}).status_code == 200
    assert client.post("/api/auth/google", json={"id_token": "b"}).status_code == 200
    assert db_session.query(Owner).filter_by(email="guser@gmail.com").count() == 1


def test_google_login_rejects_password_account_by_email(client, db_session, monkeypatch):
    # E1: a password account with this email exists -> 409, never a
    # silent link. The owner must use the Login page instead.
    client.post(
        "/api/auth/register",
        json={"shop_name": "Shop", "email": "guser@gmail.com", "password": "secret123"},
    )
    monkeypatch.setattr(
        auth_routes, "verify_google_id_token",
        lambda tok: _fake_claims(sub="some-other-sub"),
    )
    r = client.post("/api/auth/google", json={"id_token": "x"})
    assert r.status_code == 409
    assert "already registered" in r.json()["detail"]
    # Password login still works; nothing was overwritten.
    assert client.post(
        "/api/auth/login",
        json={"email": "guser@gmail.com", "password": "secret123"},
    ).status_code == 200
    assert db_session.query(Owner).filter_by(email="guser@gmail.com").count() == 1


def test_google_login_bad_signature_is_401(client, monkeypatch):
    def boom(tok):
        raise ValueError("bad signature")

    monkeypatch.setattr(auth_routes, "verify_google_id_token", boom)
    assert client.post("/api/auth/google", json={"id_token": "bad"}).status_code == 401


def test_google_login_unverified_email_is_401(client, monkeypatch):
    monkeypatch.setattr(
        auth_routes, "verify_google_id_token", lambda tok: _fake_claims(verified=False)
    )
    assert client.post("/api/auth/google", json={"id_token": "x"}).status_code == 401


def test_google_login_missing_token_is_422(client):
    assert client.post("/api/auth/google", json={}).status_code == 422
