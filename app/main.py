from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.review import router as review_router
from app.api.routes import router
from app.core.config import get_settings


def create_app() -> FastAPI:
    app = FastAPI(title="Sheets YouTube Automation", version="0.1.0")
    app.include_router(router)
    app.include_router(review_router)
    settings = get_settings()
    settings.local_storage_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/files", StaticFiles(directory=settings.local_storage_dir), name="files")
    return app


app = create_app()
