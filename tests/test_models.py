"""S2: model relationships, constraints, and tracking-id invariants."""

import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.database.base import Base
from app.models.owner import Owner
from app.models.queue_session import QueueSession
from app.models.queue_snapshot import QueueSnapshot
from app.models.token import Token


@pytest.fixture()
def db():
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


def _owner(db, email="owner@shop.com"):
    owner = Owner(
        shop_name="Shop", email=email, password_hash=hash_password("secret123")
    )
    db.add(owner)
    db.commit()
    db.refresh(owner)
    return owner


def _session(db, owner):
    s = QueueSession(owner_id=owner.id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def test_owner_session_token_snapshot_relationships(db):
    owner = _owner(db)
    s = _session(db, owner)
    assert s.people_count is None  # no vision analysis yet
    assert s.status == "open"
    assert s.virtual_queue_enabled is False

    token = Token(session_id=s.id, token_number=1, customer_contact="+911234567890")
    snap = QueueSnapshot(
        session_id=s.id, people_count=12, estimated_wait_minutes=24.0, source="vision"
    )
    db.add_all([token, snap])
    db.commit()

    assert owner.sessions[0].id == s.id
    assert s.tokens[0].tracking_id == token.tracking_id
    assert s.snapshots[0].source == "vision"


def test_tracking_ids_unique_and_unguessable(db):
    owner = _owner(db)
    s = _session(db, owner)
    tokens = [
        Token(session_id=s.id, token_number=i, customer_contact=f"c{i}@x.com")
        for i in (1, 2)
    ]
    db.add_all(tokens)
    db.commit()
    assert tokens[0].tracking_id != tokens[1].tracking_id
    assert len(tokens[0].tracking_id) >= 32
    assert tokens[0].tracking_id != str(tokens[0].id)


def test_token_number_unique_per_session_only(db):
    owner = _owner(db)
    s1 = _session(db, owner)
    s2 = _session(db, owner)
    db.add(Token(session_id=s1.id, token_number=1, customer_contact="a@x.com"))
    db.add(Token(session_id=s2.id, token_number=1, customer_contact="b@x.com"))
    db.commit()  # same number in different sessions is fine

    db.add(Token(session_id=s1.id, token_number=1, customer_contact="c@x.com"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_owner_email_unique(db):
    _owner(db, email="dup@shop.com")
    db.add(
        Owner(
            shop_name="Other",
            email="dup@shop.com",
            password_hash=hash_password("secret123"),
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
