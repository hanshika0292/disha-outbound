"""Disha REST API entry point."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from disha.api.errors import register_handlers
from disha.api.routes import (
    activity,
    admin,
    auth_routes,
    dashboard,
    drafts,
    gmail,
    health,
    inbox,
    leads,
    linkedin,
    public,
    reports,
    sequence,
    settings as settings_routes,
    sourcing,
)
from disha.config import get_settings
from disha.logging_config import configure_logging


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Stamp every response with an X-Request-ID header (or echo client's)."""

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("X-Request-ID", uuid4().hex)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.disha_log_level, settings.disha_log_fmt)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Disha API",
        version="0.7.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS — dev defaults to Next.js local hosts; override in prod via env.
    allowed_origins = os.getenv(
        "DISHA_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:3001",
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in allowed_origins if o.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIdMiddleware)

    register_handlers(app)

    # Health: no /api/v1 prefix, no auth
    app.include_router(health.router)

    # Business routes under /api/v1
    api_v1 = "/api/v1"
    app.include_router(dashboard.router, prefix=api_v1)
    app.include_router(leads.router, prefix=api_v1)
    app.include_router(sourcing.router, prefix=api_v1)
    app.include_router(drafts.router, prefix=api_v1)
    app.include_router(sequence.router, prefix=api_v1)
    app.include_router(linkedin.router, prefix=api_v1)
    app.include_router(reports.router, prefix=api_v1)
    app.include_router(settings_routes.router, prefix=api_v1)
    app.include_router(gmail.router, prefix=api_v1)
    app.include_router(inbox.router, prefix=api_v1)
    app.include_router(activity.router, prefix=api_v1)
    app.include_router(auth_routes.router, prefix=api_v1)
    app.include_router(admin.router, prefix=api_v1)
    app.include_router(public.router, prefix=api_v1)

    # Optional static mount: if Next.js was exported to ui/out, serve it at /.
    # Lets `uvicorn disha.api.app:app` serve the whole stack in production-ish runs.
    ui_out = Path(__file__).resolve().parents[3] / "ui" / "out"
    if ui_out.is_dir():
        app.mount("/", StaticFiles(directory=ui_out, html=True), name="ui")

    return app


app = create_app()
