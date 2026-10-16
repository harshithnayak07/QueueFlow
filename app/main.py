from pathlib import Path

import logging
import os
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes.auth import router as auth_router
from app.api.routes.owner_tokens import router as owner_tokens_router
from app.api.routes.public import router as public_router
from app.api.routes.queue_sessions import router as queue_sessions_router
from app.api.routes.vision import router as vision_router
from app.core.config import settings
from app.database.base import Base
from app.database.session import SessionLocal, engine, ensure_schema_updates
from app.services.queue_service import progress_session

import app.models  # noqa: F401  (register table metadata)

_progression_log = logging.getLogger("queueflow.progression")


def _reconcile_all_open_sessions() -> int:
    """Run automatic progression once over every open session.

    Used at startup (restart recovery: overdue tokens reconciled from
    persisted windows) and by the background sweeper. Returns total
    served transitions. Never raises — a failing session must not stop
    the others or kill the sweeper thread.
    """
    from app.models.queue_session import QueueSession

    total = 0
    db = SessionLocal()
    try:
        sessions = (
            db.query(QueueSession).filter(QueueSession.status == "open").all()
        )
        for qs in sessions:
            try:
                total += progress_session(db, qs)
            except Exception:
                db.rollback()
                _progression_log.exception(
                    "progression failed for session %s", qs.id
                )
        return total
    finally:
        db.close()


def _sweeper_loop(stop: threading.Event, interval_seconds: int = 30) -> None:
    while not stop.wait(interval_seconds):
        try:
            _reconcile_all_open_sessions()
        except Exception:
            _progression_log.exception("progression sweep failed")


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    # Background progression is disabled under pytest: the suite drives
    # progress_session directly with a fake clock for determinism
    # (PYTEST_CURRENT_TEST is set automatically while tests run).
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        _reconcile_all_open_sessions()
        stop = threading.Event()
        thread = threading.Thread(
            target=_sweeper_loop, args=(stop,), daemon=True
        )
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=5)
    else:
        yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)

# Deployment posture: dev stays permissive (file:// iteration + same-origin
# serving); production requires an explicit origin list AND a real secret.
# Refusing to boot is safer than running an open, forgeable-token server.
if settings.env == "prod":
    if settings.secret_key == "change-me-in-env" or len(settings.secret_key) < 32:
        raise RuntimeError(
            "Refusing to start with ENV=prod and an insecure SECRET_KEY. "
            "Set a random SECRET_KEY (>= 32 chars) in the environment."
        )
    _PROD_ORIGINS = [
        origin.strip()
        for origin in settings.cors_origins.split(",")
        if origin.strip()
    ]
    if not _PROD_ORIGINS:
        raise RuntimeError(
            "Refusing to start with ENV=prod and empty CORS_ORIGINS. "
            "Set CORS_ORIGINS to the exact deployed origin(s)."
        )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_PROD_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

Base.metadata.create_all(bind=engine)
ensure_schema_updates(engine)

app.include_router(auth_router)
app.include_router(queue_sessions_router)
app.include_router(owner_tokens_router)
app.include_router(public_router)
app.include_router(vision_router)


@app.get("/health")
def health_check():
    return {"status": "ok", "app": settings.app_name, "env": settings.env}


# Serve the plain HTML/CSS/JS frontend from the same origin so pages + API
# share http://127.0.0.1:8000 (also what Google Sign-In requires for its
# authorized JavaScript origin). Mounted last: API routes take precedence.
_frontend_dir = Path(__file__).resolve().parent.parent / "frontend"
if _frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")
