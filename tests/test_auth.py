"""S2: auth endpoints, JWT protection, ownership guard, password security."""

import os
import tempfile

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_owned_session
from app.core.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from app.database.base import Base
from app.database.session import get_db
from app.main import app
from app.models.owner import Owner
from app.models.queue_session import QueueSession


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


def _register(client, email="owner@shop.com", password="secret123", shop="Shop"):
    return client.post(
        "/api/auth/register",
        json={"shop_name": shop, "email": email, "password": password},
    )


def _login(client, email="owner@shop.com", password="secret123"):
    return client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )


def _auth_header(client, email="owner@shop.com", password="secret123"):
    token = _login(client, email, password).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# --- registration ---


def test_register_creates_owner_and_hashes_password(client, db_session):
    r = _register(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "owner@shop.com"
    assert body["shop_name"] == "Shop"
    assert "password_hash" not in body
    assert "password" not in body

    owner = db_session.query(Owner).filter_by(email="owner@shop.com").one()
    assert owner.password_hash != "secret123"
    assert verify_password("secret123", owner.password_hash)


def test_register_duplicate_email_is_409(client):
    assert _register(client).status_code == 201
    r = _register(client)
    assert r.status_code == 409


def test_register_rejects_bad_input(client):
    assert _register(client, email="not-an-email").status_code == 422
    assert _register(client, email="other@shop.com", password="short").status_code == 422


# --- login ---


def test_login_returns_bearer_token(client):
    assert _register(client).status_code == 201
    r = _login(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert decode_access_token(body["access_token"]) is not None


def test_login_wrong_password_or_unknown_email_is_401(client):
    assert _register(client).status_code == 201
    assert _login(client, password="wrongpass1").status_code == 401
    assert _login(client, email="nobody@shop.com").status_code == 401


# --- protected route ---


def test_me_requires_valid_token(client):
    assert _register(client).status_code == 201

    assert client.get("/api/auth/me").status_code == 401
    assert (
        client.get(
            "/api/auth/me", headers={"Authorization": "Bearer bogus.token.here"}
        ).status_code
        == 401
    )

    r = client.get("/api/auth/me", headers=_auth_header(client))
    assert r.status_code == 200
    assert r.json()["email"] == "owner@shop.com"


# --- ownership guard ---


def test_owned_session_guard_blocks_other_owner(db_session):
    owner1 = Owner(
        shop_name="A", email="a@shop.com", password_hash=hash_password("password1")
    )
    owner2 = Owner(
        shop_name="B", email="b@shop.com", password_hash=hash_password("password2")
    )
    db_session.add_all([owner1, owner2])
    db_session.commit()
    session = QueueSession(owner_id=owner1.id)
    db_session.add(session)
    db_session.commit()

    # Owner sees own session.
    assert get_owned_session(session.id, db_session, owner1).id == session.id
    # Other owner gets 404 (not 403: no existence leak).
    with pytest.raises(HTTPException) as exc:
        get_owned_session(session.id, db_session, owner2)
    assert exc.value.status_code == 404
    # Missing session is also 404.
    with pytest.raises(HTTPException) as exc:
        get_owned_session(999999, db_session, owner1)
    assert exc.value.status_code == 404


# --- security primitives ---


def test_password_helpers():
    hashed = hash_password("secret123")
    assert hashed != "secret123"
    assert verify_password("secret123", hashed)
    assert not verify_password("wrong", hashed)
    assert not verify_password("secret123", "not-a-valid-hash")


def test_token_roundtrip_and_tamper():
    token = create_access_token(subject="42")
    assert decode_access_token(token) == "42"
    assert decode_access_token(token + "x") is None
    assert decode_access_token("garbage") is None
