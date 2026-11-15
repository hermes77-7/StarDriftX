"""Track the published archive and its versioned calibration products."""

import asyncio
import hashlib
import re
from dataclasses import dataclass
from xml.etree import ElementTree

from app.spherex.epochs import DR1, QR3, WIDE_COLLECTIONS
from app.spherex.irsa import MIRROR, ArchiveError, IrsaClient

CHANNELS = "cal-sch-v2-2026-229"
SPECTRAL_WCS = "cal-swcs-v5-2026-191"


@dataclass(frozen=True)
class ArchiveRelease:
    revision: str
    calibrations: dict[str, tuple[str, str]]

    @property
    def namespace(self) -> str:
        return f"spherex-live-v1-{self.revision}"


BOOTSTRAP_RELEASE = ArchiveRelease(
    "dr1-qr3-bootstrap", {release: (CHANNELS, SPECTRAL_WCS) for release in (DR1, QR3)},
)


async def list_prefixes(irsa: IrsaClient, prefix: str) -> list[str]:
    params = {"list-type": "2", "prefix": prefix, "delimiter": "/"}
    results: list[str] = []
    for _ in range(10):
        data = await irsa.archive_bytes(MIRROR, params, 2 * 1024 * 1024)
        try:
            root = ElementTree.fromstring(data)
        except ElementTree.ParseError as exc:
            raise ArchiveError("IRSA mirror returned an invalid listing.") from exc
        if root.tag.rsplit("}", 1)[-1] != "ListBucketResult":
            raise ArchiveError("IRSA mirror returned an unexpected directory listing.")
        results.extend(p.text for p in root.findall("{*}CommonPrefixes/{*}Prefix") if p.text)
        if root.findtext("{*}IsTruncated") != "true":
            return sorted(results)
        token = root.findtext("{*}NextContinuationToken")
        if not token:
            break
        params["continuation-token"] = token
    raise ArchiveError("IRSA mirror directory listing exceeded its page limit.")


def latest_calibration(prefixes: list[str], product: str) -> str:
    versions = []
    pattern = re.compile(rf"cal-{product}-v(\d+)-(\d{{4}})-(\d{{3}})")
    for prefix in prefixes:
        name = prefix.rstrip("/").rsplit("/", 1)[-1]
        if match := pattern.fullmatch(name):
            versions.append((tuple(map(int, match.groups())), name))
    if not versions:
        raise ArchiveError(f"Expected a published {product} calibration directory.")
    return max(versions)[1]


async def detect_release(irsa: IrsaClient) -> ArchiveRelease:
    async def inspect_collection(collection):
        folder = collection.removeprefix("spherex_")
        periods, channels, spectral = await asyncio.gather(
            list_prefixes(irsa, f"{folder}/level2/"),
            list_prefixes(irsa, f"{folder}/spectral_channels/"),
            list_prefixes(irsa, f"{folder}/spectral_wcs/"),
        )
        if not periods:
            raise ArchiveError("IRSA mirror has no published observing periods.")
        versions = await list_prefixes(irsa, periods[-1])
        if not versions:
            raise ArchiveError("IRSA mirror has no processing versions for its newest period.")
        calibration = latest_calibration(channels, "sch"), latest_calibration(spectral, "swcs")
        return calibration, (*periods, *versions, *calibration)

    async with asyncio.timeout(25):
        results = await asyncio.gather(
            *(inspect_collection(collection) for collection in WIDE_COLLECTIONS),
            return_exceptions=True,
        )
    calibrations = {}
    fingerprint = []
    for collection, result in zip(WIDE_COLLECTIONS, results, strict=True):
        if isinstance(result, BaseException):
            raise result
        calibration, products = result
        calibrations[collection] = calibration
        fingerprint.extend(products)
    revision = hashlib.sha256("\n".join(fingerprint).encode()).hexdigest()[:20]
    return ArchiveRelease(revision, calibrations)
