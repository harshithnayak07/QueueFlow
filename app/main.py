from fastapi import FastAPI

from app.api.routes.auth import router as auth_router
from app.core.config import settings
from app.database.base import Base
from app.database.session import engine

import app.models  # noqa: F401  (register table metadata)

app = FastAPI(title=settings.app_name)

Base.metadata.create_all(bind=engine)

app.include_router(auth_router)


@app.get("/health")
def health_check():
    return {"status": "ok", "app": settings.app_name, "env": settings.env}
