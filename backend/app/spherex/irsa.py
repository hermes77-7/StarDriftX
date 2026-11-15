"""Bounded asynchronous access to IRSA SIA2 and on-premises FITS cutouts."""

import asyncio
import json
import logging
import re
import sqlite3
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from io import BytesIO
from math import ceil, hypot, isfinite
from urllib.parse import urlsplit
from xml.sax import SAXException

import httpx
import numpy as np
from astropy.io import fits
from astropy.io.votable import parse
from astropy.wcs import WCS, NoConvergence

from app.shared_work import SharedWork
from app.spherex.cutouts import (
    FITS_BLOCK_SIZE,
    PIXEL_SCALE_DEG,
    SCIENCE_PLANES,
    Cutout,
    cropped_hdu,
    detector_bounds,
    image_header,
    pointing_angle,
    read_cutout,
)
from app.spherex.epochs import (
    SPECTRAL_COLLECTIONS,
    SURVEYS,
    WIDE_COLLECTIONS,
    Epoch,
    Survey,
    TimeWindow,
)
from app.spherex.search_cache import ExposureSearchCache

logger = logging.getLogger("uvicorn.error")

IRSA_HOST = "irsa.ipac.caltech.edu"
SIA_URL = f"https://{IRSA_HOST}/SIA"
MIRROR = "https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com"
# Interior wavelengths of the six detector passbands, as SIA BAND in meters.
DETECTOR_BANDS_M = ("9e-7", "1.4e-6", "2e-6", "3e-6", "4.1e-6", "4.7e-6")
MISSION_START_MJD = 60745  # March 11, 2025 UTC, before launch and first light.
PRODUCT_NAME = re.compile(r"level2_.+D([1-6])_spx_(.+)\.fits$", re.IGNORECASE)
RETRYABLE = {408, 429, 500, 502, 503, 504}


class ArchiveError(RuntimeError):
    """IRSA failed to return a complete, usable result."""


class ArchiveTimeout(ArchiveError):
    """The operation exceeded its time budget."""


class ArchiveGatewayError(ArchiveError):
    """The IRSA gateway could not complete the request."""


class SearchLimitExceeded(ArchiveError):
    """The result could not be retrieved within the configured search budget."""


FITS_ERRORS = (OSError, KeyError, ValueError, IndexError, TypeError, NoConvergence)
MIRROR_ERRORS = (ArchiveError, TimeoutError, *FITS_ERRORS)


@dataclass(frozen=True)
class SkyRegion:
    ra: float
    dec: float
    fov: float

    def __post_init__(self) -> None:
        if not (0 <= self.ra < 360 and -90 <= self.dec <= 90 and 0 < self.fov <= 1):
            raise ValueError("Expected RA in [0, 360), Dec in [-90, 90], and FoV in (0, 1].")


@dataclass(frozen=True)
class Exposure:
    observation_id: str
    detector: int
    collection: str
    access_url: str
    mjd_start: float
    mjd_end: float
    wavelength_um: tuple[float, float]
    processing_version: str

    @property
    def id(self) -> str:
        return f"{self.observation_id}D{self.detector}"


@dataclass(frozen=True)
class SearchResult:
    exposures: tuple[Exposure, ...]
    warnings: tuple[str, ...] = ()

    @property
    def no_coverage(self) -> bool:
        return not self.exposures


@dataclass(frozen=True)
class CutoutResult:
    exposure: Exposure
    cutout: Cutout | None
    warning: str | None = None


@dataclass(frozen=True)
class ArchiveLimits:
    concurrency: int = 8
    attempts: int = 3
    request_seconds: float = 18
    search_request_seconds: float = 26
    search_seconds: float = 55
    download_seconds: float = 60
    page_size: int = 256
    max_pages: int = 64
    max_search_bytes: int = 8 * 1024 * 1024
    max_fits_bytes: int = 32 * 1024 * 1024

    def __post_init__(self) -> None:
        limits = (
            self.concurrency, self.attempts, self.request_seconds, self.search_request_seconds,
            self.search_seconds,
            self.download_seconds, self.page_size, self.max_pages, self.max_search_bytes,
            self.max_fits_bytes,
        )
        if any(not isfinite(limit) or limit <= 0 for limit in limits):
            raise ValueError("Archive limits must be finite and positive.")


def _parse_page(payload: bytes) -> tuple[list[Exposure], bool]:
    votable = parse(BytesIO(payload))
    resources = votable.resources
    statuses = [
        str(info.value).upper()
        for info in [*votable.infos, *(info for resource in resources for info in resource.infos)]
        if info.name == "QUERY_STATUS"
    ]
    if "ERROR" in statuses:
        raise ArchiveError("IRSA rejected the exposure query.")
    if not {"OK", "OVERFLOW"}.intersection(statuses):
        raise ArchiveError("IRSA response is missing a successful query status.")
    table = votable.get_first_table().to_table(use_names_over_ids=True)
    required = {"obs_id", "obs_collection", "access_url", "t_min", "t_max", "em_min", "em_max"}
    if not required.issubset(table.colnames):
        raise ArchiveError("IRSA returned an incomplete exposure table.")
    exposures = []
    for row in table:
        url = str(row["access_url"])
        product = PRODUCT_NAME.search(urlsplit(url).path.rsplit("/", 1)[-1])
        collection = str(row["obs_collection"])
        start, end = float(row["t_min"]), float(row["t_max"])
        low, high = float(row["em_min"]) * 1e6, float(row["em_max"]) * 1e6
        if (
            not product or collection not in SPECTRAL_COLLECTIONS
            or not all(map(isfinite, (start, end, low, high)))
            or start > end or not 0 < low <= high
        ):
            raise ArchiveError("IRSA returned an invalid spectral exposure record.")
        exposures.append(
            Exposure(
                str(row["obs_id"]), int(product[1]), collection, url, start, end,
                (low, high), product[2],
            )
        )
    return exposures, "OVERFLOW" in statuses


class IrsaClient:
    """Share one HTTP client and request budget across searches and cutouts."""

    def __init__(
        self, http: httpx.AsyncClient, limits: ArchiveLimits | None = None,
        surveys: Mapping[str, Survey] | None = None,
        search_cache: ExposureSearchCache | None = None,
    ):
        self.http = http
        self.limits = ArchiveLimits() if limits is None else limits
        self.surveys = SURVEYS if surveys is None else {**SURVEYS, **surveys}
        self.search_cache = search_cache
        self._slots = asyncio.Semaphore(self.limits.concurrency)
        self._prefixes: OrderedDict[str, bytes] = OrderedDict()
        self._prefix_work = SharedWork[bytes]()
        self._searches: OrderedDict[tuple, tuple[float, list[Exposure]]] = OrderedDict()
        self._search_work = SharedWork[tuple[list[Exposure], list[ArchiveError]]]()
        self._download_work = SharedWork[Cutout]()
        self._downloads: OrderedDict[tuple, tuple[Cutout, int]] = OrderedDict()
        self._download_bytes = 0

    async def _get(
        self, url: str, params: dict, max_bytes: int, *,
        byte_range: tuple[int, int] | None = None, attempts: int | None = None,
        split_on_gateway_error: bool = False,
        split_on_timeout: bool = False,
        request_seconds: float | None = None,
    ) -> bytes:
        max_attempts = self.limits.attempts if attempts is None else attempts
        timeout = self.limits.request_seconds if request_seconds is None else request_seconds
        headers = {"Range": f"bytes={byte_range[0]}-{byte_range[1]}"} if byte_range else {}
        for attempt in range(max_attempts):
            retry_after = None
            try:
                async with self._slots, asyncio.timeout(timeout):
                    async with self.http.stream(
                        "GET", url, params=params, headers=headers, follow_redirects=False,
                        timeout=timeout,
                    ) as response:
                        if response.status_code in RETRYABLE:
                            retry_after = response.headers.get("Retry-After")
                        response.raise_for_status()
                        if byte_range:
                            expected = f"bytes {byte_range[0]}-{byte_range[1]}/"
                            if response.status_code != 206 or not response.headers.get(
                                "Content-Range", ""
                            ).startswith(expected):
                                raise ArchiveError("Mirror returned an unexpected FITS byte range.")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(body) + len(chunk) > max_bytes:
                                raise ArchiveError("IRSA response exceeded the size limit.")
                            body.extend(chunk)
                        if byte_range and len(body) != max_bytes:
                            raise ArchiveError("Mirror returned a truncated FITS byte range.")
                        return bytes(body)
            except (TimeoutError, httpx.TimeoutException) as error:
                if split_on_timeout or attempt + 1 == max_attempts:
                    raise ArchiveTimeout("IRSA request timed out.") from error
            except (httpx.TransportError, httpx.DecodingError) as error:
                if attempt + 1 == max_attempts:
                    raise ArchiveError("IRSA could not be reached.") from error
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                if status in (408, 504) and (split_on_timeout or attempt + 1 == max_attempts):
                    raise ArchiveTimeout(f"IRSA returned HTTP {status}.") from error
                if status == 502 and (split_on_gateway_error or attempt + 1 == max_attempts):
                    raise ArchiveGatewayError("IRSA returned HTTP 502.") from error
                if status not in RETRYABLE or attempt + 1 == max_attempts:
                    raise ArchiveError(f"IRSA returned HTTP {status}.") from error
            delay = 0.25 * 2**attempt
            if retry_after:
                try:
                    seconds = float(retry_after)
                except ValueError:
                    try:
                        date = parsedate_to_datetime(retry_after)
                        seconds = (date.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds()
                    except (ValueError, TypeError, OverflowError):
                        seconds = 0
                delay = max(delay, seconds)
            await asyncio.sleep(delay)
        raise AssertionError("Archive request exited without a result.")

    async def archive_bytes(self, url: str, params: dict, max_bytes: int) -> bytes:
        try:
            async with asyncio.timeout(self.limits.download_seconds):
                return await self._get(url, params, max_bytes)
        except TimeoutError as exc:
            raise ArchiveTimeout("IRSA archive request exceeded its time budget.") from exc

    async def archive_range(self, url: str, offset: int, length: int) -> bytes:
        if offset < 0 or not 0 < length <= self.limits.max_fits_bytes:
            raise ValueError("FITS byte range exceeds the configured limit.")
        timeout = self.limits.request_seconds
        if length >= 8 * 1024 * 1024:
            timeout = max(timeout, 24)
        return await self._get(
            url, {}, length, byte_range=(offset, offset + length - 1), request_seconds=timeout,
        )

    async def exposure_prefix(self, exposure: Exposure) -> bytes:
        mirror_url = self._mirror_url(exposure)
        if (cached := self._prefixes.get(mirror_url)) is not None:
            self._prefixes.move_to_end(mirror_url)
            return cached

        async def load():
            prefix = await self._get(mirror_url, {}, 65536, byte_range=(0, 65535), attempts=1)
            self._prefixes[mirror_url] = prefix
            if len(self._prefixes) > 256:
                self._prefixes.popitem(last=False)
            return prefix

        return await self._prefix_work.run(mirror_url, load)

    def _mirror_url(self, exposure: Exposure) -> str:
        path = self._exposure_url(exposure).path.removeprefix("/ibe/data/spherex/")
        return f"{MIRROR}/{path}"

    async def _mirror_cutout(self, exposure: Exposure, grid: WCS, max_pixels: int) -> Cutout:
        url = self._mirror_url(exposure)

        async def span(start: int, length: int) -> bytes:
            if not 0 < length <= self.limits.max_fits_bytes:
                raise ValueError("FITS byte range exceeds the configured limit.")
            return await self._get(url, {}, length, byte_range=(start, start + length - 1),
                                   attempts=1)

        prefix = await self.exposure_prefix(exposure)
        image, offset = image_header(prefix)
        _, (left, right, bottom, top) = await asyncio.to_thread(
            detector_bounds, prefix, grid, grid.array_shape,
        )
        width, height = image["NAXIS1"], image["NAXIS2"]
        dx, dy = (int(1 - image[f"CRPIX{axis}A"]) for axis in (1, 2))
        x0, x1 = max(0, left - dx), min(width, right - dx)
        y0, y1 = max(0, bottom - dy), min(height, top - dy)
        if not (x0 < x1 and y0 < y1 and (x1 - x0) * (y1 - y0) <= max_pixels):
            raise ValueError("The mirrored exposure does not cover the requested grid.")

        async def next_header(start: int) -> tuple[fits.Header, bytes]:
            buffer = BytesIO(await span(start, 20 * FITS_BLOCK_SIZE))
            header = fits.Header.fromfile(buffer)
            return header, buffer.getvalue()[:buffer.tell()]

        async def crop(
            header: fits.Header, header_bytes: bytes, start: int, size: int,
        ) -> fits.ImageHDU:
            if header.get("ZIMAGE"):
                # Preserve the archive's compressed flags and their full 32-bit values.
                data = await span(start, ceil(size / FITS_BLOCK_SIZE) * FITS_BLOCK_SIZE)

                def decode_flags() -> fits.ImageHDU:
                    primary = fits.PrimaryHDU().header.tostring().encode("ascii")
                    with fits.open(BytesIO(primary + header_bytes + data)) as hdus:
                        flags = hdus["FLAGS"]
                        return cropped_hdu(flags.header, flags.section[y0:y1, x0:x1], x0, y0)

                return await asyncio.to_thread(decode_flags)
            row_size = width * 4
            raw = await span(start + y0 * row_size, (y1 - y0) * row_size)
            dtype = ">f4" if header["BITPIX"] == -32 else ">i4"
            pixels = np.frombuffer(raw, dtype=dtype).reshape(y1 - y0, width)[:, x0:x1].copy()
            scale, zero = header.get("BSCALE", 1), header.get("BZERO", 0)
            if scale != 1 or zero != 0:
                pixels = pixels.astype(np.float64) * scale + zero
            return cropped_hdu(header, pixels, x0, y0)

        tasks = []
        header, header_bytes = image, b""
        async with asyncio.TaskGroup() as group:
            for name in SCIENCE_PLANES:
                compressed = header.get("ZIMAGE")
                valid = header.get("EXTNAME") == name and header.get("NAXIS") == 2
                if compressed:
                    valid = valid and name == "FLAGS" and header.get("ZBITPIX") == 32 and (
                        header.get("ZNAXIS1"), header.get("ZNAXIS2")
                    ) == (width, height)
                else:
                    valid = valid and header.get("BITPIX") == (32 if name == "FLAGS" else -32)
                    valid = valid and (header["NAXIS1"], header["NAXIS2"]) == (width, height)
                if not valid:
                    raise ValueError(f"Unexpected layout for the SPHEREx {name} plane.")
                size = (abs(header["BITPIX"]) // 8 * header["NAXIS1"] * header["NAXIS2"]
                        + header.get("PCOUNT", 0))
                tasks.append(group.create_task(crop(header, header_bytes, offset, size)))
                offset += ceil(size / FITS_BLOCK_SIZE) * FITS_BLOCK_SIZE
                if name != SCIENCE_PLANES[-1]:
                    header, header_bytes = await next_header(offset)
                    offset += len(header_bytes)
        hdus = fits.HDUList([fits.PrimaryHDU(), *(task.result() for task in tasks)])
        return await asyncio.to_thread(
            read_cutout, hdus, exposure_id=exposure.id, collection=exposure.collection,
            max_pixels=max_pixels, processing_version=exposure.processing_version,
        )

    async def search(
        self, region: SkyRegion, *, window: TimeWindow | None = None,
        epoch: Epoch = "all", collections: Sequence[str] = WIDE_COLLECTIONS,
        detector: int | None = None, release: str | None = None,
    ) -> SearchResult:
        """Split overflowing pages and recover usable rows from bounded searches."""
        if not collections or any(value not in SPECTRAL_COLLECTIONS for value in collections):
            raise ValueError("Select one or more SPHEREx spectral image collections.")
        if detector is not None and not 1 <= detector <= 6:
            raise ValueError("Expected detector 1–6.")
        if epoch != "all":
            survey = self.surveys[epoch]
            collections = [value for value in collections if value == survey.collection]
            if survey.window:
                start = max(window.start, survey.window.start) if window else survey.window.start
                end = min(window.end, survey.window.end) if window else survey.window.end
                if start > end:
                    return SearchResult(())
                window = TimeWindow(start, end)
        # A circumscribed cone includes all four corners of the north-up output grid.
        # Round only the discovery cone outward. The requested WCS stays exact.
        radius = ceil((hypot(region.fov, region.fov) / 2 + PIXEL_SCALE_DEG + 1e-7) * 1e4) / 1e4
        position = f"CIRCLE {round(region.ra, 7) % 360} {round(region.dec, 7)} {radius}"
        wide_search = region.fov >= 0.5

        def split_period(period: TimeWindow | None) -> list[TimeWindow]:
            today_mjd = datetime.now(UTC).timestamp() / 86400 + 40587
            # Start before launch and end after the most recent possible exposure.
            bounds = period or TimeWindow(MISSION_START_MJD, ceil(today_mjd) + 1)
            if bounds.end - bounds.start < 1 / 86400:
                return []
            middle = (bounds.start + bounds.end) / 2
            return [TimeWindow(bounds.start, middle), TimeWindow(middle, bounds.end)]

        async def search_collection(collection: str) -> tuple[list[Exposure], list[ArchiveError]]:
            exposures: dict[str, Exposure] = {}
            errors: list[ArchiveError] = []
            pages = 0
            can_split_failure = True

            async def search_periods(first_period: TimeWindow | None) -> None:
                nonlocal pages, can_split_failure
                pending = [first_period]
                try:
                    while pending:
                        period = pending.pop()
                        pages += 1
                        if pages > self.limits.max_pages:
                            raise SearchLimitExceeded("IRSA search exceeded the page limit.")
                        params = {
                            "COLLECTION": collection,
                            "POS": position,
                            "MAXREC": self.limits.page_size, "RESPONSEFORMAT": "VOTABLE",
                        }
                        if detector is not None:
                            params["BAND"] = DETECTOR_BANDS_M[detector - 1]
                        if period:
                            params["TIME"] = f"{period.start} {period.end}"
                        try:
                            payload = await self._get(
                                SIA_URL, params, self.limits.max_search_bytes,
                                split_on_gateway_error=True, split_on_timeout=wide_search,
                                request_seconds=self.limits.search_request_seconds,
                            )
                        except (ArchiveGatewayError, ArchiveTimeout) as error:
                            split_failure = wide_search or isinstance(error, ArchiveGatewayError)
                            subperiods = (
                                split_period(period) if can_split_failure and split_failure else []
                            )
                            if subperiods:
                                # Try both date ranges within the remaining search budget.
                                can_split_failure = False
                                async with asyncio.TaskGroup() as tasks:
                                    for part in subperiods:
                                        tasks.create_task(search_periods(part))
                            else:
                                errors.append(error)
                            continue
                        try:
                            rows, overflow = await asyncio.to_thread(_parse_page, payload)
                        except (ValueError, OSError, IndexError, TypeError, SAXException) as error:
                            raise ArchiveError(
                                "IRSA returned an unreadable exposure table."
                            ) from error
                        for item in rows:
                            if item.collection != collection:
                                raise ArchiveError("IRSA returned an unexpected collection.")
                            if detector is None or item.detector == detector:
                                exposures[item.id] = item
                        if overflow or len(rows) >= self.limits.page_size:
                            can_split_failure = False
                            subperiods = split_period(period)
                            if not subperiods:
                                raise SearchLimitExceeded("IRSA results exceed the page size.")
                            pending.extend(reversed(subperiods))
                except ArchiveError as error:
                    errors.append(error)

            try:
                async with asyncio.timeout(self.limits.search_seconds):
                    await search_periods(window)
            except TimeoutError:
                errors.append(ArchiveTimeout("IRSA exposure search exceeded its time budget."))
            return list(exposures.values()), errors

        async def cached_collection(collection: str) -> tuple[list[Exposure], list[ArchiveError]]:
            if release is None:
                return await search_collection(collection)
            key = (release, collection, region, window, detector)
            loop = asyncio.get_running_loop()
            cached = self._searches.get(key)
            if cached and cached[0] > loop.time():
                self._searches.move_to_end(key)
                return cached[1], []

            async def load():
                rows, errors = await search_collection(collection)
                if self.search_cache is not None:
                    # Query identity survives a restart with the bootstrap release.
                    disk_key = json.dumps([
                        collection, position, detector,
                        [float(window.start), float(window.end)] if window else None,
                    ])
                    try:
                        if not errors:
                            await asyncio.to_thread(
                                self.search_cache.set, disk_key,
                                json.dumps([asdict(row) for row in rows]),
                            )
                        elif (saved := await asyncio.to_thread(
                            self.search_cache.get, disk_key,
                        )) is not None:
                            indexed = [
                                Exposure(**{**row, "wavelength_um": tuple(row["wavelength_um"])})
                                for row in json.loads(saved)
                            ]
                            if indexed:
                                # Use the most recent record of each exposure.
                                combined = {row.id: row for row in indexed}
                                combined.update((row.id, row) for row in rows)
                                rows = list(combined.values())
                                errors.append(ArchiveError(
                                    "Previously indexed exposure results were used.",
                                ))
                    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as error:
                        logger.warning("exposure_search_cache_failed reason=%s", error)
                if not errors:
                    self._searches[key] = (loop.time() + 300, rows)
                    self._searches.move_to_end(key)
                    if len(self._searches) > 128:
                        self._searches.popitem(last=False)
                return rows, errors

            return await self._search_work.run(key, load)

        responses = await asyncio.gather(
            *(cached_collection(collection) for collection in dict.fromkeys(collections)),
            return_exceptions=True,
        )
        found = []
        errors = []
        for response in responses:
            if isinstance(response, BaseException):
                raise response
            rows, incomplete = response
            found.extend(rows)
            errors.extend(incomplete)
        if errors and not found:
            raise errors[0]
        return SearchResult(
            tuple(sorted(found, key=lambda e: (e.mjd_start, e.id))),
            tuple(str(error) for error in errors),
        )

    @staticmethod
    def _exposure_url(exposure: Exposure):
        url = urlsplit(exposure.access_url)
        if (
            url.scheme != "https" or url.netloc != IRSA_HOST
            or not url.path.startswith("/ibe/data/spherex/")
            or not PRODUCT_NAME.search(url.path.rsplit("/", 1)[-1])
            or ".." in url.path.split("/") or url.query or url.fragment
        ):
            raise ArchiveError("Expected an on-premises IRSA spectral image URL.")
        return url

    async def download(
        self, exposure: Exposure, region: SkyRegion, *, grid: WCS | None = None,
    ) -> Cutout:
        self._exposure_url(exposure)
        # IBE crops in native detector axes. Pad for rotation and edge pixels.
        size = hypot(region.fov, region.fov) + 4 * PIXEL_SCALE_DEG
        max_pixels = ceil(size / PIXEL_SCALE_DEG + 16) ** 2
        params = {"center": f"{region.ra},{region.dec}", "size": f"{size:.10f}"}

        def validate(cutout: Cutout) -> Cutout:
            if cutout.detector != exposure.detector or abs(cutout.mjd - exposure.mjd_start) > 1e-5:
                raise ArchiveError("IRSA cutout metadata does not match the exposure record.")
            return cutout

        if grid is not None:
            try:
                async with asyncio.timeout(min(self.limits.download_seconds, 18)):
                    mirror_pixels = ceil(size / PIXEL_SCALE_DEG + 40) ** 2
                    return validate(await self._mirror_cutout(exposure, grid, mirror_pixels))
            except MIRROR_ERRORS:
                pass
            except ExceptionGroup as error:
                _, unexpected = error.split(MIRROR_ERRORS)
                if unexpected is not None:
                    raise unexpected from error
        try:
            async with asyncio.timeout(self.limits.request_seconds * self.limits.attempts + 3):
                payload = await self._get(
                    exposure.access_url, params, self.limits.max_fits_bytes,
                    attempts=None if grid is None else 1,
                )
                cutout = await asyncio.to_thread(
                    read_cutout, BytesIO(payload), exposure_id=exposure.id,
                    collection=exposure.collection, max_pixels=max_pixels,
                    processing_version=exposure.processing_version,
                )
        except TimeoutError as error:
            raise ArchiveTimeout("IRSA cutout exceeded its time budget.") from error
        except FITS_ERRORS as error:
            raise ArchiveError("IRSA returned an invalid FITS cutout.") from error
        return validate(cutout)

    async def _download_cached(
        self, exposure: Exposure, region: SkyRegion, grid: WCS | None, grid_key: tuple | None,
    ) -> Cutout:
        key = (exposure, region, grid_key)
        if (cached := self._downloads.get(key)) is not None:
            self._downloads.move_to_end(key)
            return cached[0]

        async def load():
            cutout = await self.download(exposure, region, grid=grid)
            size = cutout.brightness.nbytes + cutout.variance.nbytes + cutout.flags.nbytes
            self._downloads[key] = (cutout, size)
            self._download_bytes += size
            while self._download_bytes > 64 * 1024 * 1024 or len(self._downloads) > 256:
                _, (_, discarded) = self._downloads.popitem(last=False)
                self._download_bytes -= discarded
            return cutout

        return await self._download_work.run(key, load)

    async def cutouts(
        self, exposures: Sequence[Exposure], region: SkyRegion, *, epoch: Epoch = "all",
        prefilter: Callable[[Exposure], Awaitable[bool]] | None = None, grid: WCS | None = None,
    ) -> AsyncIterator[CutoutResult]:
        """Yield bounded batches, retaining per-exposure failures as warnings."""
        grid_key = None if grid is None else (grid.to_header_string(relax=True), grid.array_shape)

        async def fetch(exposure: Exposure) -> CutoutResult | None:
            try:
                if epoch != "all" and grid is not None:
                    try:
                        async with asyncio.timeout(4):
                            header, _ = image_header(await self.exposure_prefix(exposure))
                            mjd = float(header["MJD-OBS"])
                            angle = pointing_angle(header)
                        if (
                            angle is not None and abs(mjd - exposure.mjd_start) <= 1e-5
                            and not self.surveys[epoch].includes(exposure.collection, angle, mjd)
                        ):
                            return None
                    except MIRROR_ERRORS:
                        # Verify the survey after download if the header is unavailable.
                        pass
                if prefilter is not None and not await prefilter(exposure):
                    return None
                cutout = await self._download_cached(exposure, region, grid, grid_key)
                return CutoutResult(exposure, cutout)
            except ArchiveError as error:
                return CutoutResult(exposure, None, str(error))

        deadline = asyncio.get_running_loop().time() + self.limits.download_seconds
        first = min(self.limits.concurrency, len(exposures))
        remaining = iter(exposures[first:])
        tasks = {asyncio.create_task(fetch(item)) for item in exposures[:first]}
        try:
            while tasks:
                timeout = max(0, deadline - asyncio.get_running_loop().time())
                done, _ = await asyncio.wait(tasks, timeout=timeout,
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    raise ArchiveTimeout("IRSA cutout batch exceeded its time budget.")
                for task in done:
                    tasks.remove(task)
                    if (item := next(remaining, None)) is not None:
                        tasks.add(asyncio.create_task(fetch(item)))
                    result = task.result()
                    if result is None:
                        continue
                    cutout = result.cutout
                    if epoch == "all" or cutout is None:
                        yield result
                    elif cutout.pointing_angle_deg is None:
                        yield CutoutResult(result.exposure, None, "Exposure has no survey angle.")
                    elif self.surveys[epoch].includes(
                        cutout.collection, cutout.pointing_angle_deg, cutout.mjd
                    ):
                        yield result
        finally:
            for task in tasks:
                task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)
