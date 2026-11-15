"""Star Drift backend entry point.

Run from the backend/ folder with:
    uvicorn app.main:app --reload --port 8000
"""

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.cache.disk import DiskCache
from app.cache.manager import CacheManager, default_cache_manager
from app.catalogs import SkyCatalogs
from app.live_router import error_response
from app.live_router import router as live_router
from app.mock_router import FIXTURE_DIR
from app.mock_router import router as mock_router
from app.shared_work import SharedWork
from app.spherex.dr1_cube import Dr1Cube
from app.spherex.epochs import configure_surveys
from app.spherex.irsa import ArchiveError, ArchiveLimits, IrsaClient
from app.spherex.search_cache import ExposureSearchCache
from app.spherex.service import CutoutService

logger = logging.getLogger("uvicorn.error")


@dataclass
class RequestWindow:
    start: float
    count: int = 0


async def watch_release(service: CutoutService) -> None:
    interval = max(60, int(os.getenv("STARDRIFT_RELEASE_CHECK_SECONDS", "3600")))
    purged_revision = None
    while True:
        previous = service.release
        delay = interval
        try:
            await service.refresh_release()
            if service.release != previous:
                logger.info("archive_release_changed release=%s", service.release.namespace)
            if purged_revision != service.release.namespace:
                await asyncio.to_thread(
                    default_cache_manager.invalidate_old_releases, service.release.namespace,
                )
                purged_revision = service.release.namespace
        except (ArchiveError, TimeoutError):
            logger.warning("archive_release_check_failed")
            delay = min(interval, 120)
        except Exception:
            logger.exception("archive_release_maintenance_failed")
        await asyncio.sleep(delay)


@asynccontextmanager
async def lifespan(application: FastAPI):
    async with httpx.AsyncClient(limits=httpx.Limits(
        max_connections=24, max_keepalive_connections=16, keepalive_expiry=30,
    )) as http:
        surveys = configure_surveys(os.getenv("STARDRIFT_EPOCH_WINDOWS") or "{}")
        cache_dir = Path(os.getenv("CACHE_DIR", "data/cache"))
        search_cache = ExposureSearchCache(cache_dir / "exposure-searches.sqlite3")
        archive = IrsaClient(http, surveys=surveys, search_cache=search_cache)
        cube_archive = IrsaClient(
            http, ArchiveLimits(concurrency=6, attempts=2, request_seconds=10),
        )
        service = CutoutService(archive, cube=Dr1Cube(cube_archive))
        application.state.cutouts = service
        application.state.dr1_cache = CacheManager(DiskCache(cache_dir / "dr1-cutouts"))
        application.state.catalogs = SkyCatalogs(http)
        application.state.cutout_work = SharedWork()
        application.state.request_windows = {}
        watch = None
        if os.getenv("STARDRIFT_RELEASE_CHECK_SECONDS", "3600") != "0":
            watch = asyncio.create_task(watch_release(service))
        try:
            yield
        finally:
            if watch is not None:
                watch.cancel()
                with suppress(asyncio.CancelledError):
                    await watch


app = FastAPI(title="Star Drift API", version="0.1.0", lifespan=lifespan)

# CORS lets the browser app (on port 5173) call this API (on port 8000).
# Without it the browser blocks the requests during local development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["ETag", "X-Cache", "X-Data-Release", "X-Request-ID", "Retry-After"],
)

# Mount mock API endpoints for Phase 1/2 developer workflows (Ticket S3)
app.include_router(mock_router)
app.include_router(live_router)


@app.middleware("http")
async def request_metrics(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)

    started = time.monotonic()
    request_id = uuid4().hex
    limit = max(1, int(os.getenv("STARDRIFT_REQUESTS_PER_MINUTE", "60")))
    if request.url.path == "/api/health" or request.method == "OPTIONS":
        response = await call_next(request)
    else:
        windows = request.app.state.request_windows
        client = request.client.host if request.client else "unknown"
        window = windows.get(client)
        if window is None or started - window.start >= 60:
            if len(windows) >= 4096:
                for key, value in list(windows.items()):
                    if started - value.start >= 60:
                        del windows[key]
            if len(windows) < 4096 or client in windows:
                window = windows[client] = RequestWindow(started)
            else:
                window = None
        if window is None or window.count >= limit:
            remaining = max(1, 60 - int(started - window.start)) if window else 60
            response = error_response(
                429, "RATE_LIMIT_EXCEEDED", "Too many requests. Try again shortly.",
                {"Retry-After": str(remaining)},
            )
        else:
            window.count += 1
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("api_request_failed request_id=%s", request_id)
                response = error_response(
                    500, "INTERNAL_ERROR", "The request could not be completed.",
                )
    response.headers["X-Request-ID"] = request_id
    logger.info("%s", json.dumps({
        "event": "api_request", "request_id": request_id, "path": request.url.path,
        "method": request.method, "status": response.status_code,
        "view": getattr(request.state, "view", None),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "upstream_ms": getattr(request.state, "upstream_ms", None),
        "cache": getattr(request.state, "cache_status", None),
        "archive_error": getattr(request.state, "archive_error", None),
    }))
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
    field_codes = {
        "ra": "INVALID_COORDINATES", "dec": "INVALID_COORDINATES",
        "fov": "FOV_OUT_OF_RANGE", "band": "INVALID_BAND",
        "detector": "INVALID_DETECTOR", "epoch": "INVALID_EPOCH",
    }
    issue = error.errors()[0]
    field = str(issue["loc"][-1])
    return JSONResponse(
        status_code=400,
        content={"error": {
            "code": field_codes.get(field, "INVALID_PARAMETER"),
            "message": f"{field}: {issue['msg']}",
            "details": {"field": field},
        }},
    )


def contract() -> dict:
    if app.openapi_schema is None:
        path = FIXTURE_DIR.parent.parent / "openapi.json"
        app.openapi_schema = json.loads(path.read_text(encoding="utf-8"))
    return app.openapi_schema


app.openapi = contract


@app.get("/api/health")
def health() -> dict:
    """Health check, used later by deployment monitoring (ticket C11)."""
    return {"status": "ok"}

