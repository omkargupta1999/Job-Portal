"""FastAPI application factory and lifecycle."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.backup import create_backup
from app.config import Settings, get_settings, set_settings
from app.db import dispose, init_engine
from app.migrations import migrate
from app.scheduler import Scheduler, set_scheduler
from app.scoring import rescore_if_outdated
from app.security import SecurityMiddleware
from app.seed import seed_profile, sync_sources_from_config
from app.sync import recover_interrupted_runs

log = logging.getLogger("jobhub")
BASE_DIR = Path(__file__).resolve().parent


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def init_storage(settings: Settings) -> None:
    """Create directories, open the DB, run migrations and seed. Idempotent."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    engine = init_engine(settings.db_path)
    migrate(engine, settings.data_dir,
            backup_fn=lambda label: create_backup(settings.db_path, settings.backups_dir, label))
    seed_profile()
    sync_sources_from_config(settings)
    recover_interrupted_runs()
    rescore_if_outdated()


def create_app(settings: Settings | None = None, *, start_scheduler: bool | None = None) -> FastAPI:
    settings = settings or get_settings()
    set_settings(settings)
    configure_logging(settings.log_level)
    run_scheduler = settings.scheduler_enabled if start_scheduler is None else start_scheduler

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_storage(settings)
        scheduler = None
        if run_scheduler:
            scheduler = Scheduler(settings.data_dir, settings.fetch_on_startup)
            if scheduler.start():
                set_scheduler(scheduler)
        log.info("JobHub %s ready (data: %s)", __version__, settings.data_dir)
        try:
            yield
        finally:
            if scheduler is not None:
                scheduler.stop()
                set_scheduler(None)
            dispose()

    app = FastAPI(title="JobHub", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.settings = settings

    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(SecurityMiddleware, auth_user=settings.auth_user, auth_password=settings.auth_password)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    from app.web import admin, applications, jobs, pages, profile

    for module in (pages, jobs, applications, profile, admin):
        app.include_router(module.router)

    from app.web.common import render

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/") or "application/json" in request.headers.get("accept", ""):
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        try:
            return render(request, "error.html", status=exc.status_code, detail=exc.detail,
                          status_code=exc.status_code)
        except Exception:  # noqa: BLE001 - never fail while rendering an error
            return HTMLResponse(f"<h1>{exc.status_code}</h1>", status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("Unhandled error on %s", request.url.path)
        return HTMLResponse("<h1>Something went wrong</h1><p>The error was logged.</p>", status_code=500)

    return app
