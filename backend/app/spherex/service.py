"""Create and encode real cutouts from the published SPHEREx archive."""

import asyncio
import logging
from contextlib import aclosing
from io import BytesIO

import numpy as np
from astropy.wcs import NoConvergence

from app.shared_work import SharedWork
from app.spherex.coadd import BandSelection, Coadd, NoCoverageError, coadd, output_grid
from app.spherex.cutouts import (
    DetectorCalibration,
    detector_bounds,
    read_calibration,
    read_nominal_bands,
)
from app.spherex.dr1_cube import DR1_BANDS, DR1_SPECTRAL_CALIBRATION, Dr1Cube
from app.spherex.epochs import Epoch
from app.spherex.irsa import ArchiveError, ArchiveTimeout, Exposure, IrsaClient, SkyRegion
from app.spherex.releases import BOOTSTRAP_RELEASE, MIRROR, ArchiveRelease, detect_release

logger = logging.getLogger("uvicorn.error")
MAX_INPUT_PIXELS = 16_000_000


class CutoutService:
    def __init__(self, archive: IrsaClient, *, cube: Dr1Cube | None = None):
        self.archive = archive
        self.dr1_cube = cube if cube is not None else Dr1Cube(archive)
        self.release = BOOTSTRAP_RELEASE
        self._calibrations: dict[tuple, DetectorCalibration] = {}
        self._calibration_work = SharedWork[DetectorCalibration]()
        self._channel_files: dict[str, bytes] = {}
        self._channel_work = SharedWork[bytes]()
        self._nominal_bands: dict[str, list[dict]] = {}
        self._band_work = SharedWork[list[dict]]()
        self._jobs = asyncio.Semaphore(2)

    async def refresh_release(self) -> None:
        self.release = await detect_release(self.archive)

    async def spectral_channels(self, collection: str, version: str) -> bytes:
        if (content := self._channel_files.get(version)) is not None:
            return content

        async def load():
            folder = collection.removeprefix("spherex_")
            url = (
                f"{MIRROR}/{folder}/spectral_channels/{version}/"
                f"spectral_channels_spx_{version}.fits"
            )
            content = await self.archive.archive_bytes(url, {}, 24 * 1024 * 1024)
            if len(self._channel_files) >= 3:
                self._channel_files.pop(next(iter(self._channel_files)))
            self._channel_files[version] = content
            return content

        return await self._channel_work.run(version, load)

    async def nominal_bands(self, collection: str, version: str) -> list[dict]:
        if version == DR1_SPECTRAL_CALIBRATION:
            # This calibration version matches the wavelength table in the published DR1 cube.
            return [
                {"detector": index // 17 + 1, "channel": index % 17 + 1,
                 "band": index + 1, "wavelength_um": wavelength}
                for index, (wavelength, _, _) in enumerate(DR1_BANDS)
            ]
        if (bands := self._nominal_bands.get(version)) is not None:
            return bands

        async def load():
            try:
                raw = await self.spectral_channels(collection, version)
                wavelengths = await asyncio.to_thread(read_nominal_bands, BytesIO(raw))
            except (OSError, ValueError, KeyError, IndexError) as error:
                raise ArchiveError("Could not read the published spectral bands.") from error
            bands = [
                {"detector": detector, "channel": channel,
                 "band": (detector - 1) * 17 + channel, "wavelength_um": wavelength}
                for detector, channels in wavelengths.items()
                for channel, wavelength in sorted(channels.items())
            ]
            if len(self._nominal_bands) >= 6:
                self._nominal_bands.pop(next(iter(self._nominal_bands)))
            self._nominal_bands[version] = bands
            return bands

        return await self._band_work.run(version, load)

    async def band_metadata(self, release: ArchiveRelease, epoch: Epoch) -> list[dict]:
        all_bands = []
        seen = set()
        for collection, (sch, _) in release.calibrations.items():
            if epoch != "all" and collection != self.archive.surveys[epoch].collection:
                continue
            if sch in seen:
                continue
            seen.add(sch)
            bands = await self.nominal_bands(collection, sch)
            if all_bands and bands != all_bands:
                raise ArchiveError("The archive collections use different nominal wavelengths.")
            all_bands = bands
        if not all_bands:
            raise ArchiveError("No published spectral bands are available for this survey.")
        return all_bands

    async def calibration(
        self, release: str, versions: tuple[str, str], detector: int,
    ) -> DetectorCalibration:
        key = (*versions, detector)
        if (calibration := self._calibrations.get(key)) is not None:
            return calibration

        async def load():
            folder = f"{MIRROR}/{release.removeprefix('spherex_')}"
            sch, swcs = versions
            wavelength = (
                f"{folder}/spectral_wcs/{swcs}/{detector}/"
                f"spectral_wcs_D{detector}_spx_{swcs}.fits"
            )
            maps = await asyncio.gather(
                self.spectral_channels(release, sch),
                self.archive.archive_bytes(wavelength, {}, 64 * 1024 * 1024),
                return_exceptions=True,
            )
            for result in maps:
                if isinstance(result, BaseException):
                    raise result
            try:
                calibration = await asyncio.to_thread(
                    read_calibration, BytesIO(maps[0]), BytesIO(maps[1]), detector,
                )
            except (OSError, ValueError, KeyError, IndexError) as exc:
                raise ArchiveError("Could not read the public detector calibration.") from exc
            if len(self._calibrations) >= 6:
                self._calibrations.pop(next(iter(self._calibrations)))
            self._calibrations[key] = calibration
            return calibration

        return await self._calibration_work.run(key, load)

    async def make_cutout(
        self, release: ArchiveRelease, region: SkyRegion, selection: BandSelection,
        epoch: Epoch, mask_flags: bool,
    ) -> tuple[Coadd, list[str]]:
        try:
            async with asyncio.timeout(130), self._jobs:
                search = await self.archive.search(
                    region, epoch=epoch, detector=selection.detector,
                    collections=tuple(release.calibrations), release=release.namespace,
                )
                if search.no_coverage:
                    raise NoCoverageError("No SPHEREx exposures cover this selection.")
                collections = sorted({item.collection for item in search.exposures})
                maps = await asyncio.gather(
                    *(self.calibration(collection, release.calibrations[collection],
                                       selection.detector) for collection in collections),
                    return_exceptions=True,
                )
                calibrations = {}
                calibration_errors = []
                for collection, result in zip(collections, maps, strict=True):
                    if isinstance(result, ArchiveError):
                        calibration_errors.append(result)
                        logger.warning("archive_calibration_failed collection=%s reason=%s",
                                       collection, result)
                    elif isinstance(result, BaseException):
                        raise result
                    else:
                        calibrations[collection] = result
                if not calibrations:
                    raise calibration_errors[0]
                exposures = [row for row in search.exposures if row.collection in calibrations]
                cutouts = []
                failures = [*search.warnings, *(str(error) for error in calibration_errors)]
                pixels = 0
                grid = output_grid(region)

                async def overlaps_band(exposure: Exposure) -> bool:
                    if selection.channel is None:
                        return True
                    try:
                        async with asyncio.timeout(4):
                            prefix = await self.archive.exposure_prefix(exposure)
                            mjd, bounds = await asyncio.to_thread(
                                detector_bounds, prefix, grid, grid.array_shape,
                            )
                        if abs(mjd - exposure.mjd_start) > 1e-5:
                            return True
                        x0, x1, y0, y1 = bounds
                        detector = calibrations[exposure.collection].channel_map
                        height, width = detector.shape
                        x0, x1 = max(x0, 0), min(x1, width)
                        y0, y1 = max(y0, 0), min(y1, height)
                        return x0 < x1 and y0 < y1 and bool(
                            np.any(detector[y0:y1, x0:x1] == selection.channel)
                        )
                    except (
                        ArchiveError, TimeoutError, OSError, KeyError, ValueError, TypeError,
                        NoConvergence,
                    ):
                        # Discovery is an optimization. Fall back to the original FITS request.
                        return True

                try:
                    rows = self.archive.cutouts(
                        exposures, region, epoch=epoch, prefilter=overlaps_band, grid=grid,
                    )
                    async with aclosing(rows):
                        async for row in rows:
                            if row.cutout is None:
                                failures.append(row.exposure.id)
                                logger.warning(
                                    "archive_cutout_failed exposure=%s reason=%s",
                                    row.exposure.id, row.warning,
                                )
                                continue
                            pixels += row.cutout.brightness.size
                            if pixels > MAX_INPUT_PIXELS:
                                failures.append("input pixel budget")
                                break
                            cutouts.append(row.cutout)
                except ArchiveTimeout:
                    if not cutouts:
                        raise
                    failures.append("download deadline")
                try:
                    product = await asyncio.to_thread(
                        coadd, region, cutouts, calibrations, selection,
                        epoch=epoch, mask_flags=mask_flags, surveys=self.archive.surveys,
                    )
                except NoCoverageError as exc:
                    if failures:
                        raise ArchiveError(
                            "The archive did not return enough data to build this cutout."
                        ) from exc
                    raise
                except ValueError as exc:
                    raise ArchiveError(
                        "Could not combine the published detector calibrations."
                    ) from exc
                return product, failures
        except TimeoutError as exc:
            raise ArchiveTimeout("The cutout exceeded its processing time budget.") from exc


def cutout_header(product: Coadd, release: ArchiveRelease, failures: list[str]) -> dict:
    wcs = product.wcs.wcs
    contributors = product.contributions
    times = sorted({row.mjd for row in contributors})
    low = min(item.wavelength_range_um[0] for item in contributors)
    high = max(item.wavelength_range_um[1] for item in contributors)
    header = {
        "version": "1.0",
        "data_release": release.namespace,
        "collections": sorted({item.collection for item in contributors}),
        "wcs": {
            "crval": wcs.crval.tolist(), "crpix": wcs.crpix.tolist(),
            "cdelt": wcs.cdelt.tolist(), "ctype": list(wcs.ctype),
            "radesys": wcs.radesys, "shape": list(product.brightness.shape),
        },
        "bunit": "MJy/sr",
        "detector": product.band.detector,
        "wavelength_um": product.nominal_wavelength_um or (low + high) / 2,
        "wavelength_range_um": [low, high],
        "nexp": len(contributors),
        "epoch": product.epoch,
        "mjd_range": [times[0], times[-1]],
        "exposure_mjd": times,
        "exposure_ids": [row.exposure_id for row in contributors],
        "exposures": [{
            "id": row.exposure_id, "collection": row.collection,
            "processing_version": row.processing_version, "mjd": row.mjd,
        } for row in contributors],
        "flags_summary": {
            name: count for bit, name in product.flag_names.items() if 0 <= bit < 32
            if (count := int(np.count_nonzero(product.flags & (1 << bit))))
        },
        "flagged_fraction": float(np.count_nonzero(product.flags) / product.flags.size),
        "coverage_fraction": float(np.count_nonzero(product.hits) / product.hits.size),
        "mask_flags": bool(product.bad_pixel_mask),
        "photometric_normalized": False,
        "source": "irsa",
        "warnings": (
            ["Some archive data could not be retrieved. This cutout uses the available exposures."]
            if failures else []
        ),
    }
    if product.band.channel is not None:
        header["channel"] = product.band.channel
        header["band"] = (product.band.detector - 1) * 17 + product.band.channel
    return header

