"""FastAPI application: /api/v1, health, metrics, and the web UI."""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from .. import __version__, metrics
from ..config import get_settings
from ..db import session_scope
from ..logging_setup import configure_logging, request_id
from . import errors
from .routes import admin, analysis, auth, checks, discovery, inventory, reports_sched, search, snapshots

log = logging.getLogger(__name__)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
    ),
}


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    app = FastAPI(
        title="Assurance API",
        version=__version__,
        description="Network snapshot and change assurance for Cisco IOS-XE and NX-OS.",
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )
    errors.install(app)
    if s.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["*"],
                           allow_headers=["Authorization", "Content-Type"])

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id.set(rid)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id.reset(token)
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        elapsed = time.perf_counter() - start
        metrics.HTTP_REQUESTS.labels(request.method, path, response.status_code).inc()
        metrics.HTTP_LATENCY.labels(request.method, path).observe(elapsed)
        response.headers["X-Request-ID"] = rid
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if path.startswith("/api/docs") or path.startswith("/api/redoc"):
            # Swagger UI loads its assets from a CDN.
            del response.headers["Content-Security-Policy"]
        return response

    api = APIRouter(prefix="/api/v1")
    for r in (auth.router, inventory.router, snapshots.router, checks.router, analysis.router, search.router,
              discovery.router, reports_sched.router, admin.router):
        api.include_router(r)
    app.include_router(api)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok", "version": __version__}

    @app.get("/readyz", include_in_schema=False)
    def readyz():
        checks = {}
        try:
            with session_scope() as db:
                db.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as e:
            checks["database"] = f"error: {type(e).__name__}"
        if s.job_backend == "rq" and s.env != "test":
            try:
                from ..jobs import redis_conn

                redis_conn().ping()
                checks["redis"] = "ok"
            except Exception as e:
                checks["redis"] = f"error: {type(e).__name__}"
        try:
            from ..storage import get_store

            get_store().ping()
            checks["object_store"] = "ok"
        except Exception as e:
            checks["object_store"] = f"error: {type(e).__name__}"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)

    @app.get("/metrics", include_in_schema=False)
    def prom():
        try:
            from sqlalchemy import func, select

            from ..models import Device

            with session_scope() as db:
                for status, n in db.execute(select(Device.status, func.count()).group_by(Device.status)):
                    metrics.DEVICES.labels(status).set(n)
        except Exception:
            pass
        return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)

    _mount_ui(app, s.ui_dist)
    return app


def _mount_ui(app: FastAPI, dist) -> None:
    index = dist / "index.html"
    if not index.exists():
        log.info("web UI not built (%s missing); serving API only", index)
        return
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            return JSONResponse({"title": "Not Found", "status": 404}, status_code=404,
                                media_type=errors.PROBLEM)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
