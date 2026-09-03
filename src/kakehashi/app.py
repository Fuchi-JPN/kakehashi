"""FastAPI application factory."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import ConfigStore
from .routes import anthropic_ingress, health, models_proxy, openai_ingress

log = logging.getLogger("kakehashi.app")


def create_app(config_path=None) -> FastAPI:
    store = ConfigStore(config_path) if config_path else ConfigStore()
    app = FastAPI(title="Kakehashi", version=__version__)
    app.state.store = store

    app.include_router(health.router)
    app.include_router(models_proxy.router)
    app.include_router(openai_ingress.router)
    app.include_router(anthropic_ingress.router)

    # Web UI API (lazy import to avoid cycles)
    from .webui.api import router as webui_router
    app.include_router(webui_router)

    # Static SPA at /ui
    cfg = store.get()
    if cfg.webui.enabled:
        static_dir = Path(__file__).parent / "webui" / "static"
        static_dir.mkdir(parents=True, exist_ok=True)
        # ensure index exists (fallback if missing)
        if not (static_dir / "index.html").exists():
            (static_dir / "index.html").write_text("<h1>Kakehashi</h1>", encoding="utf-8")
        app.mount("/ui", StaticFiles(directory=str(static_dir), html=True), name="ui")

    @app.exception_handler(Exception)
    async def _unhandled(request, exc):
        log.exception("unhandled error")
        return JSONResponse({"error": {"message": "internal error"}}, status_code=500)

    return app


def get_store(app: FastAPI) -> ConfigStore:
    return app.state.store
