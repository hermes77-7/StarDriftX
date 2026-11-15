"""Live SPHEREx API and protected cache operations."""

import asyncio
import hmac
import logging
import math
import os
import time
from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import JSONResponse

from app.cache.base import CacheEntry, CacheMetadata, compute_etag
from app.cache.keys import build_cache_key
from app.cache.manager import default_cache_manager
from app.catalogs import Catalog, CatalogUnavailable
from app.spherex.coadd import BandSelection, NoCoverageError
from app.spherex.cutouts import PIXEL_SCALE_DEG
from app.spherex.dr1_cube import CUBE_CACHE_RELEASE
from app.spherex.epochs import Epoch
from app.spherex.irsa import ArchiveError, ArchiveTimeout, SkyRegion
from app.spherex.payload import decode_payload, encode_payload
from app.spherex.service import cutout_header

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/api")
FOV_LIMITS = {"min_deg": 0.05, "max_deg": 1.0, "default_deg": 0.2}
LIVE_BROWSE_HEAD_START = 12.0
CUTOUT_ERRORS = (ArchiveError, NoCoverageError)


class ClientDisconnected(Exception):
    pass


def error_response(status: int, code: str, message: str, headers=None) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}, headers=headers,
    )


@router.get("/meta")
async def get_meta(request: Request, include_bands: bool = False, epoch: Epoch = "all"):
    service = request.app.state.cutouts
    release = service.release
    metadata = {
        "data_release": release.namespace,
        "collections": list(release.calibrations),
        "coverage_notes": "Published SPHEREx wide-survey data from NASA/IPAC IRSA.",
        "fov_limits": FOV_LIMITS,
        "pixel_scale_arcsec": PIXEL_SCALE_DEG * 3600,
        "supported_bands": {
            "total_bands": 102, "detectors": 6, "channels_per_detector": 17,
            "wavelength_range_um": [0.75, 5.0],
        },
        "epochs": [
            {
                "id": key, "name": f"Survey {key}", "collection": survey.collection,
                "pointing_angle_deg": survey.pointing_angle_deg,
                "time_range": (
                    {"mjd_start": survey.window.start, "mjd_end": survey.window.end}
                    if survey.window else {}
                ),
            }
            for key, survey in service.archive.surveys.items()
            if survey.collection in release.calibrations
        ] + [{"id": "all", "name": "All available surveys", "time_range": {}}],
    }
    if include_bands:
        try:
            async with asyncio.timeout(20):
                metadata["supported_bands"]["bands"] = await while_connected(
                    request, service.band_metadata(release, epoch),
                )
        except ClientDisconnected:
            return Response(status_code=499)
        except (ArchiveError, TimeoutError) as error:
            logger.warning("band_metadata_unavailable epoch=%s reason=%s", epoch, error)
            return error_response(
                503, "SERVICE_UNAVAILABLE", "Spectral calibration is unavailable.",
            )
    return metadata


def is_cache_admin(authorization: str | None) -> bool:
    token = os.getenv("STARDRIFT_CACHE_ADMIN_TOKEN")
    return bool(token and authorization and hmac.compare_digest(
        authorization.encode(), f"Bearer {token}".encode(),
    ))


@router.get("/catalog")
async def get_catalog(
    request: Request,
    ra: Annotated[float, Query(ge=0, lt=360, allow_inf_nan=False)],
    dec: Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)],
    radius: Annotated[float, Query(ge=0.01, le=1, allow_inf_nan=False)] = 0.1,
    catalog: Catalog = "gaia",
    mag_limit: Annotated[float | None, Query(ge=-30, le=40, allow_inf_nan=False)] = None,
):
    try:
        return await while_connected(
            request, request.app.state.catalogs.cone(catalog, ra, dec, radius, mag_limit),
        )
    except ClientDisconnected:
        return Response(status_code=499)
    except CatalogUnavailable:
        return error_response(503, "SERVICE_UNAVAILABLE", f"{catalog.upper()} is unavailable.")


@router.get("/resolve")
async def resolve_name(request: Request, name: Annotated[str, Query(min_length=1, max_length=120)]):
    name = " ".join(name.split())
    if not name:
        return error_response(400, "INVALID_PARAMETER", "Enter an object name.")
    try:
        result = await while_connected(request, request.app.state.catalogs.resolve(name))
    except ClientDisconnected:
        return Response(status_code=499)
    except CatalogUnavailable:
        return error_response(503, "SERVICE_UNAVAILABLE", "The name resolver is unavailable.")
    return result or error_response(404, "OBJECT_NOT_FOUND", f"Object '{name}' was not found.")


@router.get("/cache/stats")
async def cache_stats(authorization: Annotated[str | None, Header()] = None):
    if not is_cache_admin(authorization):
        return error_response(403, "FORBIDDEN", "Cache administration requires authorization.")
    return await asyncio.to_thread(default_cache_manager.get_stats)


@router.post("/cache/invalidate")
async def invalidate_cache(
    request: Request, release: str | None = None,
    authorization: Annotated[str | None, Header()] = None,
):
    if not is_cache_admin(authorization):
        return error_response(403, "FORBIDDEN", "Cache administration requires authorization.")
    current = request.app.state.cutouts.release.namespace
    if release is not None and release != current:
        return error_response(400, "INVALID_PARAMETER", "Only the current release can be retained.")
    try:
        count = await asyncio.to_thread(default_cache_manager.invalidate_old_releases, current)
    except Exception:
        logger.exception("cache_invalidation_failed")
        return error_response(503, "SERVICE_UNAVAILABLE", "Cache invalidation failed.")
    return {"status": "ok", "purged_entries": count, "current_release": current}


@router.get("/cutout")
async def get_cutout(
    request: Request,
    ra: Annotated[float, Query(ge=0, lt=360, allow_inf_nan=False)],
    dec: Annotated[float, Query(ge=-90, le=90, allow_inf_nan=False)],
    fov: Annotated[float, Query(ge=0.05, le=1, allow_inf_nan=False)] = 0.2,
    epoch: Epoch = "all",
    band: Annotated[int | None, Query(ge=1, le=102)] = None,
    detector: Annotated[int | None, Query(ge=1, le=6)] = None,
    mode: Literal["single", "stack"] = "single",
    mask_flags: bool = True,
    format: Literal["binary", "json"] = "binary",
    if_none_match: Annotated[str | None, Header()] = None,
    wait_for_live: bool = False,
):
    if mode == "stack":
        if detector is None or band is not None:
            return error_response(400, "INVALID_PARAMETER", "A stack requires a detector only.")
        selection = BandSelection(detector)
    else:
        selection = BandSelection.from_band(4 if band is None else band)
        if detector is not None and detector != selection.detector:
            return error_response(400, "INVALID_DETECTOR", "The band belongs to another detector.")

    service = request.app.state.cutouts
    release = service.release
    region = SkyRegion(ra, dec, fov)
    request.state.view = {
        "ra": ra, "dec": dec, "fov": fov, "epoch": epoch,
        "band": band, "detector": selection.detector, "mode": mode,
    }
    window = service.archive.surveys[epoch].window if epoch != "all" else None
    key = build_cache_key(
        ra, dec, fov, epoch, selection.channel, selection.detector, mode, mask_flags,
        release.namespace,
        mjd_window=(window.start, window.end) if window else None,
    )
    cache = default_cache_manager

    async def load_cutout():
        entry = await asyncio.to_thread(cache.get, key)
        if entry is not None:
            return entry, "HIT", None
        start = time.monotonic()

        def elapsed():
            return round((time.monotonic() - start) * 1000)

        async def load_live():
            product, failures = await service.make_cutout(
                release, region, selection, epoch, mask_flags,
            )
            header = cutout_header(product, release, failures)
            data = await asyncio.to_thread(
                lambda: encode_payload(header, product.brightness.tolist(), product.flags.tolist())
            )
            if failures or release != service.release:
                entry = CacheEntry(data, CacheMetadata(
                    key, release.namespace, len(data), etag=compute_etag(data), ttl_seconds=0,
                ))
            else:
                entry = await asyncio.to_thread(cache.set, key, data, release.namespace)
            return entry, "MISS", elapsed()

        async def load_cube():
            cube_cache = getattr(request.app.state, "dr1_cache", None)
            cube_key = CUBE_CACHE_RELEASE + key.removeprefix(release.namespace)
            if cube_cache is not None:
                saved = await asyncio.to_thread(cube_cache.get, cube_key)
                if saved is not None:
                    entry = CacheEntry(saved.data, replace(saved.metadata, ttl_seconds=0))
                    return entry, "HIT", elapsed()
            header, brightness, flags = await service.dr1_cube.make_cutout(
                region, selection, mask_flags,
            )
            data = await asyncio.to_thread(
                lambda: encode_payload(header, brightness.tolist(), flags.tolist())
            )
            if cube_cache is not None:
                try:
                    await asyncio.to_thread(cube_cache.set, cube_key, data, header["data_release"])
                except OSError as error:
                    logger.warning("dr1_cube_cache_write_failed reason=%s", error)
            entry = CacheEntry(data, CacheMetadata(
                cube_key, header["data_release"], len(data), etag=compute_etag(data), ttl_seconds=0,
            ))
            return entry, "MISS", elapsed()

        if epoch == "all":
            return await prefer_live_cutout(load_live, load_cube, wait_for_live=wait_for_live)
        return await load_live()

    start = time.monotonic()
    request.state.cache_status = "MISS"
    try:
        entry, cache_status, upstream_ms = await while_connected(
            request, request.app.state.cutout_work.run((key, wait_for_live), load_cutout),
        )
    except ClientDisconnected:
        return Response(status_code=499)
    except NoCoverageError as exc:
        return error_response(404, "NO_COVERAGE", str(exc))
    except ArchiveTimeout as exc:
        request.state.archive_error = str(exc)
        return error_response(504, "UPSTREAM_TIMEOUT", str(exc))
    except ArchiveError as exc:
        request.state.archive_error = str(exc)
        return error_response(503, "SERVICE_UNAVAILABLE", str(exc))
    finally:
        request.state.upstream_ms = round((time.monotonic() - start) * 1000)
    request.state.upstream_ms = upstream_ms
    request.state.cache_status = cache_status
    headers = default_cache_manager.get_http_headers(entry)
    headers.update({"X-Cache": cache_status, "Cache-Control": "public, max-age=0, must-revalidate"})
    if entry.metadata.ttl_seconds == 0:
        headers["Cache-Control"] = "no-store"
    if format == "json":
        header, pixels, _ = await asyncio.to_thread(decode_payload, entry.data)
        headers["Content-Type"] = "application/json"
        headers["ETag"] = compute_etag(b"json:" + entry.data)
        if _matches(if_none_match, headers["ETag"]):
            return Response(status_code=304, headers=headers)
        return JSONResponse(
            content={"header": header, "data": [
                [value if math.isfinite(value) else None for value in row] for row in pixels
            ]}, headers=headers,
        )
    if default_cache_manager.is_not_modified(entry, if_none_match):
        return Response(status_code=304, headers=headers)
    return Response(content=entry.data, headers=headers)


async def prefer_live_cutout[T](
    live: Callable[[], Coroutine[Any, Any, T]], fallback: Callable[[], Coroutine[Any, Any, T]],
    *, wait_for_live: bool = False,
) -> T:
    """Give live browsing a head start and let either source recover the view."""
    primary = asyncio.create_task(live())
    backup = None
    try:
        pending = {primary}
        errors = {}
        await asyncio.wait({primary}, timeout=None if wait_for_live else LIVE_BROWSE_HEAD_START)
        if primary.done():
            try:
                return primary.result()
            except CUTOUT_ERRORS as error:
                errors[primary] = error
                pending.remove(primary)
                logger.info("dr1_cube_fallback reason=%s", error)
        else:
            logger.info("dr1_cube_fallback reason=live cutout still pending")
        backup = asyncio.create_task(fallback())
        pending.add(backup)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in (primary, backup):
                if task in done:
                    try:
                        return task.result()
                    except CUTOUT_ERRORS as error:
                        errors[task] = error
        logger.warning("dr1_cube_fallback_failed reason=%s", errors[backup])
        if isinstance(errors[backup], NoCoverageError):
            raise errors[backup]
        raise errors[primary] from errors[backup]
    finally:
        primary.cancel()
        if backup is not None:
            backup.cancel()
            await asyncio.gather(primary, backup, return_exceptions=True)
        else:
            await asyncio.gather(primary, return_exceptions=True)


async def _wait_for_disconnect(request: Request) -> None:
    while (await request.receive())["type"] != "http.disconnect":
        pass


async def while_connected[T](request: Request, load: Coroutine[Any, Any, T]) -> T:
    work = asyncio.create_task(load)
    disconnect = asyncio.create_task(_wait_for_disconnect(request))
    try:
        await asyncio.wait((work, disconnect), return_when=asyncio.FIRST_COMPLETED)
        if not work.done():
            raise ClientDisconnected()
        return work.result()
    finally:
        work.cancel()
        disconnect.cancel()
        await asyncio.gather(work, disconnect, return_exceptions=True)


def _matches(request_tag: str | None, tag: str) -> bool:
    return bool(request_tag) and (
        request_tag.strip() == "*" or tag.removeprefix("W/") in [
            value.strip().removeprefix("W/") for value in request_tag.split(",")
        ]
    )

