"""Read calibrated SPHEREx spectral image MEFs and their detector maps."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

import numpy as np
from astropy import units as u
from astropy.io import fits
from astropy.wcs import WCS
from numpy.typing import NDArray

PIXEL_SCALE_DEG = 6.15 / 3600
BAD_BITS = (0, 1, 2, 6, 7, 9, 10, 11, 14, 15, 17, 19, 20, *range(22, 29))
BAD_PIXEL_FLAGS = sum(1 << bit for bit in BAD_BITS)
FITS_BLOCK_SIZE = 2880
SCIENCE_PLANES = ("IMAGE", "FLAGS", "VARIANCE", "ZODI")


def image_header(prefix: bytes) -> tuple[fits.Header, int]:
    with BytesIO(prefix) as source_file:
        primary = fits.Header.fromfile(source_file)
        header = fits.Header.fromfile(source_file)
        if primary["NAXIS"] != 0 or header.get("EXTNAME") != "IMAGE":
            raise ValueError("Expected an empty primary HDU followed by the IMAGE extension.")
        return header, source_file.tell()


def detector_bounds(
    prefix: bytes, target: WCS, shape: tuple[int, int],
) -> tuple[float, tuple[int, int, int, int]]:
    """Estimate the detector rectangle for a grid from an uncompressed FITS prefix."""
    header, _ = image_header(prefix)
    source = WCS(header).celestial
    xs, ys = np.meshgrid(
        np.linspace(-0.5, shape[1] - 0.5, 9),
        np.linspace(-0.5, shape[0] - 0.5, 9),
    )
    ra, dec = target.pixel_to_world_values(xs, ys)
    x, y = source.world_to_pixel_values(ra, dec)
    x += 1 - float(header["CRPIX1A"])
    y += 1 - float(header["CRPIX2A"])
    if not (np.isfinite(x).all() and np.isfinite(y).all()):
        raise ValueError("The footprint could not be mapped to detector pixels.")
    # Margin for SIP curvature between sampled points and fractional pixel overlap.
    margin = 16
    return (
        float(header["MJD-OBS"]),
        (int(np.floor(x.min())) - margin, int(np.ceil(x.max())) + margin + 1,
         int(np.floor(y.min())) - margin, int(np.ceil(y.max())) + margin + 1),
    )


def pointing_angle(header: fits.Header) -> float | None:
    value = header.get("SPS_EPA", header.get("SPS_PA_ECL"))
    return float(value) if value is not None else None


def cropped_hdu(
    header: fits.Header, pixels: NDArray, x0: int, y0: int,
) -> fits.ImageHDU:
    header = header.copy()
    for key in ("BSCALE", "BZERO", "CHECKSUM", "DATASUM"):
        header.remove(key, ignore_missing=True)
    for axis, offset in enumerate((x0, y0), 1):
        for suffix in ("", "A", "W"):
            key = f"CRPIX{axis}{suffix}"
            if key in header:
                header[key] -= offset
    return fits.ImageHDU(pixels, header)


@dataclass(frozen=True)
class Cutout:
    exposure_id: str
    detector: int
    collection: str
    processing_version: str | None
    mjd: float
    pointing_angle_deg: float | None
    wcs: WCS
    x_start: int
    y_start: int
    brightness: NDArray
    variance: NDArray
    flags: NDArray
    flag_names: dict[int, str]


@dataclass(frozen=True)
class DetectorCalibration:
    detector: int
    channel_map: NDArray
    wavelength_um: NDArray
    bandwidth_um: NDArray
    nominal_wavelength_um: dict[int, float]

    def __post_init__(self) -> None:
        shape = self.channel_map.shape
        if len(shape) != 2 or self.wavelength_um.shape != shape or self.bandwidth_um.shape != shape:
            raise ValueError("Detector calibration planes must share a two-dimensional shape.")
        if not 1 <= self.detector <= 6 or set(self.nominal_wavelength_um) != set(range(1, 18)):
            raise ValueError("Expected nominal channels 1–17 for a SPHEREx detector.")

    def planes_for(self, cutout: Cutout) -> tuple[NDArray, NDArray, NDArray]:
        if self.detector != cutout.detector:
            raise ValueError("Cutout and calibration refer to different detectors.")
        height, width = cutout.brightness.shape
        x, y = cutout.x_start, cutout.y_start
        rows, columns = self.channel_map.shape
        if x < 0 or y < 0 or x + width > columns or y + height > rows:
            raise ValueError("Cutout falls outside the calibrated detector.")
        region = np.s_[y : y + height, x : x + width]
        return self.channel_map[region], self.wavelength_um[region], self.bandwidth_um[region]


def read_calibration(
    channels_path: Path | BinaryIO, wavelength_path: Path | BinaryIO, detector: int,
) -> DetectorCalibration:
    with fits.open(channels_path) as channels, fits.open(wavelength_path) as spectral:
        wave = spectral["CWAVE"]
        if int(wave.header["DETECTOR"]) != detector:
            raise ValueError("Wavelength calibration is for a different detector.")
        nominal = nominal_channels(channels["SPECTRAL_CHANNELS"].data)
        return DetectorCalibration(
            detector,
            channels["CHANNEL_MAP"].data.copy(),
            wave.data.copy(),
            spectral["CBAND"].data.copy(),
            nominal[detector],
        )


def nominal_channels(table: fits.FITS_rec) -> dict[int, dict[int, float]]:
    wavelengths = {detector: {} for detector in range(1, 7)}
    for row in table:
        detector, channel = int(row["DETECTOR"]), int(row["SUBCHAN"])
        wavelength = float(row["WAVELENGTH"])
        if not (detector in wavelengths and 1 <= channel <= 17 and
                np.isfinite(wavelength) and wavelength > 0):
            raise ValueError("Invalid nominal SPHEREx wavelength.")
        if channel in wavelengths[detector]:
            raise ValueError("Duplicate SPHEREx spectral channel.")
        wavelengths[detector][channel] = wavelength
    if any(len(channels) != 17 for channels in wavelengths.values()):
        raise ValueError("Expected 17 spectral channels per SPHEREx detector.")
    return wavelengths


def read_nominal_bands(source: BinaryIO) -> dict[int, dict[int, float]]:
    with fits.open(source) as hdus:
        return nominal_channels(hdus["SPECTRAL_CHANNELS"].data)


def read_cutout(
    source: Path | BinaryIO | fits.HDUList, *, exposure_id: str, collection: str,
    max_pixels: int = 1_000_000,
    processing_version: str | None = None,
) -> Cutout:
    with (source if isinstance(source, fits.HDUList) else fits.open(source, memmap=False)) as hdus:
        image = hdus["IMAGE"]
        header = image.header
        width, height = int(header.get("NAXIS1", 0)), int(header.get("NAXIS2", 0))
        if header["NAXIS"] != 2 or min(width, height) < 1 or width * height > max_pixels:
            raise ValueError("SPHEREx cutout dimensions exceed the configured pixel limit.")
        for name in ("ZODI", "FLAGS", "VARIANCE"):
            if hdus[name].shape != (height, width):
                raise ValueError(f"{name} does not match the science image dimensions.")
        detector, mjd = int(header["DETECTOR"]), float(header["MJD-OBS"])
        if not 1 <= detector <= 6 or not np.isfinite(mjd):
            raise ValueError("Invalid detector or MJD-OBS in the cutout.")
        unit = u.Unit(header["BUNIT"])
        if not unit.is_equivalent(u.MJy / u.sr):
            raise ValueError("Expected calibrated surface brightness in the SPHEREx cutout.")
        unit_factor = unit.to(u.MJy / u.sr)
        zodi_unit = u.Unit(hdus["ZODI"].header["BUNIT"])
        variance_unit = u.Unit(hdus["VARIANCE"].header["BUNIT"])
        zodi_factor = zodi_unit.to(unit)
        wcs = WCS(header).celestial
        if wcs.pixel_n_dim != 2 or not wcs.has_celestial:
            raise ValueError("Cutout has no two-dimensional celestial WCS.")
        origin = [1 - float(header[f"CRPIX{axis}A"]) for axis in (1, 2)]
        if any(not offset.is_integer() for offset in origin):
            raise ValueError("Expected integer offsets in the detector pixel WCS.")
        version = processing_version or header.get("PROCVER", header.get("DATA_VER"))
        flags = hdus["FLAGS"]
        return Cutout(
            exposure_id=exposure_id,
            detector=detector,
            collection=collection,
            processing_version=str(version) if version is not None else None,
            mjd=mjd,
            pointing_angle_deg=pointing_angle(header),
            wcs=wcs,
            x_start=int(origin[0]),
            y_start=int(origin[1]),
            brightness=(image.data - hdus["ZODI"].data * zodi_factor) * unit_factor,
            variance=hdus["VARIANCE"].data.astype(np.float64)
            * variance_unit.to((u.MJy / u.sr)**2),
            flags=flags.data.astype(np.uint32),
            flag_names={
                int(key[4:]): str(value)
                for key, value in flags.header.items()
                if key.startswith("MSKN") and key[4:].isdigit()
            },
        )
