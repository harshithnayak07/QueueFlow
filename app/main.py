from fastapi import FastAPI

from app.api.routes.auth import router as auth_router
from app.api.routes.owner_tokens import router as owner_tokens_router
from app.api.routes.public import router as public_router
from app.api.routes.queue_sessions import router as queue_sessions_router
from app.api.routes.vision import router as vision_router
from app.core.config import settings
from app.database.base import Base
from app.database.session import engine

import app.models  # noqa: F401  (register table metadata)

app = FastAPI(title=settings.app_name)

Base.metadata.create_all(bind=engine)

app.include_router(auth_router)
app.include_router(queue_sessions_router)
app.include_router(owner_tokens_router)
app.include_router(public_router)
app.include_router(vision_router)


@app.get("/health")
def health_check():
    return {"status": "ok", "app": settings.app_name, "env": settings.env}
