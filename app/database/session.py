from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

import logging

from app.core.config import settings
from app.services.queue_service import normalize_customer_contact

migrate_log = logging.getLogger("queueflow.migrate")

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def ensure_schema_updates(bind=engine) -> None:
    """Local-MVP schema shim (no Alembic here): add columns that
    create_all() cannot add to pre-existing SQLite tables.

    - tokens.expires_at (E2 expiry)
    - owners.google_sub (E1 Google re-entry)
    - tokens.service_started_at / tokens.service_ends_at (auto-progression)
    - queue_sessions.tokens_issued (capacity epoch) + backfill
    - queue_sessions.validity_minutes (owner 10/30 setting)
    - tokens.validity_minutes (per-token frozen validity)
    Fresh databases already include them via models; this only patches
    databases created before the columns existed. Production must adopt
    real migrations instead of this shim.
    """
    if not str(getattr(bind, "url", "")).startswith("sqlite"):
        return
    with bind.begin() as conn:
        inspector = inspect(conn)
        try:
            token_cols = {c["name"] for c in inspector.get_columns("tokens")}
        except Exception:
            return  # tables not created yet; create_all handles it
        if "expires_at" not in token_cols:
            conn.execute(text("ALTER TABLE tokens ADD COLUMN expires_at DATETIME"))
        if "service_started_at" not in token_cols:
            conn.execute(
                text("ALTER TABLE tokens ADD COLUMN service_started_at DATETIME")
            )
        if "service_ends_at" not in token_cols:
            conn.execute(
                text("ALTER TABLE tokens ADD COLUMN service_ends_at DATETIME")
            )
        # Canonicalize legacy contacts (format-only; rows otherwise
        # untouched) so duplicate detection compares like with like.
        # Idempotent: re-running changes nothing.
        try:
            rows = conn.execute(
                text("SELECT id, customer_contact FROM tokens")
            ).all()
        except Exception:
            rows = []
        for tid, contact in rows:
            try:
                canonical = normalize_customer_contact(contact)
            except (ValueError, TypeError):
                continue  # leave unparseable legacy values as-is
            if canonical != contact:
                conn.execute(
                    text("UPDATE tokens SET customer_contact = :c WHERE id = :i"),
                    {"c": canonical, "i": tid},
                )
        # DB-level one-token-per-number (fresh DBs get the same rule from
        # the model constraint). Skipped with a warning if legacy
        # duplicates already exist — those rows are preserved and the
        # app-level check still guards every new join.
        try:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_token_session_contact "
                    "ON tokens (session_id, customer_contact)"
                )
            )
        except Exception as exc:
            migrate_log.warning(
                "contact dedup index skipped (legacy duplicates?): %s", exc
            )
        try:
            owner_cols = {c["name"] for c in inspector.get_columns("owners")}
        except Exception:
            return
        if "google_sub" not in owner_cols:
            conn.execute(text("ALTER TABLE owners ADD COLUMN google_sub VARCHAR(64)"))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ix_owners_google_sub ON owners (google_sub)"
                )
            )
        try:
            session_cols = {c["name"] for c in inspector.get_columns("queue_sessions")}
        except Exception:
            return
        if "tokens_issued" not in session_cols:
            conn.execute(
                text("ALTER TABLE queue_sessions ADD COLUMN tokens_issued INTEGER DEFAULT 0")
            )
            # Grandfather legacy sessions: every token ever issued counts
            # toward the first epoch, so already-over-capacity sessions
            # reject new joins while keeping all existing tokens valid.
            conn.execute(
                text(
                    "UPDATE queue_sessions SET tokens_issued = "
                    "(SELECT COUNT(*) FROM tokens "
                    "WHERE tokens.session_id = queue_sessions.id)"
                )
            )
        if "validity_minutes" not in session_cols:
            conn.execute(
                text("ALTER TABLE queue_sessions ADD COLUMN validity_minutes INTEGER DEFAULT 30")
            )
        # Per-token validity (NULL on legacy rows = fall back to session).
        if "validity_minutes" not in token_cols:
            conn.execute(
                text("ALTER TABLE tokens ADD COLUMN validity_minutes INTEGER")
            )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
