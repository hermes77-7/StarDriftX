"""Coadd a single calibrated band or one detector on a common celestial grid."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from math import ceil, isfinite

import numpy as np
from astropy.wcs import WCS
from numpy.typing import NDArray
from reproject import reproject_exact, reproject_interp

from app.spherex.cutouts import BAD_PIXEL_FLAGS, PIXEL_SCALE_DEG, Cutout, DetectorCalibration
from app.spherex.epochs import SURVEYS, Epoch, Survey
from app.spherex.irsa import SkyRegion


class NoCoverageError(ValueError):
    """No usable pixels contribute to the requested grid and selection."""


@dataclass(frozen=True)
class BandSelection:
    detector: int
    channel: int | None = None

    def __post_init__(self) -> None:
        if (
            not 1 <= self.detector <= 6
            or (self.channel is not None and not 1 <= self.channel <= 17)
        ):
            raise ValueError("Expected detector 1–6 and, for a single band, channel 1–17.")

    @classmethod
    def from_band(cls, band: int) -> "BandSelection":
        if not 1 <= band <= 102:
            raise ValueError("Expected SPHEREx band 1–102.")
        detector, channel = divmod(band - 1, 17)
        return cls(detector + 1, channel + 1)


@dataclass(frozen=True)
class Contribution:
    exposure_id: str
    collection: str
    processing_version: str | None
    mjd: float
    wavelength_range_um: tuple[float, float]


@dataclass(frozen=True)
class Coadd:
    wcs: WCS
    brightness: NDArray
    uncertainty: NDArray
    hits: NDArray
    flags: NDArray
    contributions: tuple[Contribution, ...]
    nominal_wavelength_um: float | None
    flag_names: dict[int, str]
    band: BandSelection
    epoch: Epoch
    bad_pixel_mask: int


def output_grid(region: SkyRegion, pixel_scale_deg: float = PIXEL_SCALE_DEG) -> WCS:
    if not isfinite(pixel_scale_deg) or pixel_scale_deg <= 0:
        raise ValueError("The output pixel scale must be finite and positive.")
    side = ceil(region.fov / pixel_scale_deg)
    if side > 2048:
        raise ValueError("The output grid exceeds 2048 pixels per side.")
    grid = WCS(naxis=2)
    grid.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    grid.wcs.crval = [region.ra, region.dec]
    grid.wcs.crpix = [(side + 1) / 2] * 2
    grid.wcs.cdelt = [-pixel_scale_deg, pixel_scale_deg]
    grid.wcs.radesys = "ICRS"
    grid.array_shape = (side, side)
    return grid


def coadd(
    region: SkyRegion,
    cutouts: Iterable[Cutout],
    calibration: DetectorCalibration | Mapping[str, DetectorCalibration],
    band: BandSelection,
    *,
    epoch: Epoch = "all",
    mask_flags: bool = True,
    pixel_scale_deg: float = PIXEL_SCALE_DEG,
    surveys: Mapping[str, Survey] | None = None,
) -> Coadd:
    """Combine exposures with fractional-pixel coverage weights.

    A calibration mapping uses collection IDs as keys so a combined-epoch
    request can supply the appropriate detector calibration for each release.
    """
    grid = output_grid(region, pixel_scale_deg)
    shape = grid.array_shape
    total = np.zeros(shape)
    weights = np.zeros(shape)
    variance_sum = np.zeros(shape)
    variance_unknown = np.zeros(shape, dtype=bool)
    hits = np.zeros(shape, dtype=np.int32)
    flags = np.zeros(shape, dtype=np.uint32)
    flag_names: dict[int, str] = {}
    contributions = []
    seen = set()
    nominal_wavelengths = []
    bad_pixel_mask = BAD_PIXEL_FLAGS if mask_flags else 0
    survey = None if epoch == "all" else (surveys or {}).get(epoch, SURVEYS[epoch])

    for cutout in cutouts:
        if cutout.detector != band.detector:
            continue
        if survey and not survey.includes(cutout.collection, cutout.pointing_angle_deg, cutout.mjd):
            continue
        identity = cutout.exposure_id
        if identity in seen:
            continue
        seen.add(identity)
        detector = calibration if isinstance(calibration, DetectorCalibration) else calibration[
            cutout.collection
        ]
        channel, wavelength, bandwidth = detector.planes_for(cutout)
        select_channel = (channel >= 1) & (channel <= 17)
        if band.channel is not None:
            select_channel &= channel == band.channel
        selected = (
            select_channel & np.isfinite(wavelength) & (wavelength > 0)
            & np.isfinite(bandwidth) & (bandwidth > 0)
        )
        if not selected.any():
            continue

        # Only the selected detector strip can contribute. Project its bounding
        # rectangle with a one-pixel border to retain the sampling at its edges.
        y, x = np.nonzero(selected)
        height, width = selected.shape
        section = np.s_[
            max(0, y.min() - 1) : min(height, y.max() + 2),
            max(0, x.min() - 1) : min(width, x.max() + 2),
        ]
        source_wcs = cutout.wcs.slice(section)
        selected = selected[section]
        wavelength = wavelength[section]
        source_flags = cutout.flags[section]
        source_brightness = cutout.brightness[section]
        source_variance = cutout.variance[section]

        # Flags use nearest-neighbor sampling and bitwise OR. Include rejected
        # samples so callers can still locate masked artifacts in the footprint.
        projected_flags, flag_footprint = reproject_interp(
            (np.where(selected, source_flags, 0), source_wcs),
            grid, shape_out=shape, order="nearest-neighbor",
        )
        flag_coverage = flag_footprint > 0
        flags[flag_coverage] |= projected_flags[flag_coverage].astype(np.uint32)
        flag_names.update(cutout.flag_names)

        valid = (
            selected & np.isfinite(source_brightness) & ((source_flags & bad_pixel_mask) == 0)
        )
        if not valid.any():
            continue
        known_variance = valid & np.isfinite(source_variance) & (source_variance >= 0)
        planes = np.stack([
            np.where(valid, source_brightness, 0),
            valid.astype(float),
            np.where(known_variance, np.sqrt(np.maximum(source_variance, 0)), 0),
            (valid & ~known_variance).astype(float),
        ])
        projected, footprints = reproject_exact(
            (planes, source_wcs), grid, shape_out=(len(planes), *shape)
        )
        values, coverage, sigma, missing_variance = projected
        footprint = footprints[0]
        covered = (footprint > 0) & (coverage > 0)
        if not covered.any():
            continue

        total[covered] += values[covered] * footprint[covered]
        weights[covered] += coverage[covered] * footprint[covered]
        # Within an exposure, use a conservative fully correlated estimate.
        # Between independent exposures, add the weighted variances in quadrature.
        variance_sum[covered] += np.square(sigma[covered] * footprint[covered])
        variance_unknown[covered] |= missing_variance[covered] > 0
        hits[covered] += 1
        wavelengths = wavelength[valid]
        contributions.append(Contribution(
            cutout.exposure_id, cutout.collection, cutout.processing_version, cutout.mjd,
            (float(wavelengths.min()), float(wavelengths.max())),
        ))
        if band.channel is not None:
            nominal_wavelengths.append(detector.nominal_wavelength_um[band.channel])

    if not contributions:
        raise NoCoverageError("No usable SPHEREx pixels overlap the requested grid and selection.")
    brightness = np.full(shape, np.nan, dtype=np.float32)
    uncertainty = np.full(shape, np.nan, dtype=np.float32)
    np.divide(total, weights, out=brightness, where=weights > 0)
    np.divide(
        np.sqrt(variance_sum), weights, out=uncertainty,
        where=(weights > 0) & ~variance_unknown,
    )
    nominal = nominal_wavelengths[0] if nominal_wavelengths else None
    if any(value != nominal for value in nominal_wavelengths):
        raise ValueError("Nominal channel wavelengths differ across the supplied releases.")
    return Coadd(
        grid, brightness, uncertainty, hits, flags, tuple(contributions), nominal, flag_names,
        band, epoch, bad_pixel_mask,
    )
