"""Export the scope coadd or an IRSA SPHEREx mosaic for the frontend."""

import argparse
import json
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shared/fixtures/spherex/scope-d1-ch04.fits"
DESTINATION = ROOT / "frontend/src/viewer/fixture"


def write_fixture(name: str, pixels: np.ndarray, wcs: WCS, observation: dict) -> None:
    scale = wcs.pixel_scale_matrix
    if (
        list(wcs.wcs.ctype) != ["RA---TAN", "DEC--TAN"]
        or wcs.wcs.radesys != "ICRS"
        or not np.isfinite(scale).all()
        or not np.allclose(scale, np.diag(np.diag(scale)), rtol=0, atol=1e-12)
        or not scale[0, 0] < 0 < scale[1, 1]
    ):
        raise ValueError("The viewer requires a north-up, east-left ICRS TAN image.")
    if not np.isfinite(pixels).any():
        raise ValueError("The selected plane has no covered pixels.")

    height, width = pixels.shape
    metadata = {
        "width": width,
        "height": height,
        "wcs": {
            "projection": "TAN",
            "frame": wcs.wcs.radesys,
            "referencePixel": wcs.wcs.crpix.tolist(),
            "referenceDegrees": wcs.wcs.crval.tolist(),
            "degreesPerPixel": np.diag(scale).tolist(),
        },
        **observation,
    }
    DESTINATION.mkdir(parents=True, exist_ok=True)
    (DESTINATION / f"{name}.json").write_text(
        json.dumps(metadata, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (DESTINATION / f"{name}.f32").write_bytes(pixels.astype("<f4").tobytes(order="C"))
    print(f"Exported {name}: {width} x {height} pixels")


def export_scope() -> None:
    with fits.open(SOURCE) as coadd:
        image = coadd[0]
        header = image.header
        write_fixture("scope", image.data, WCS(header).celestial, {
            "release": header["RELEASE"],
            "unit": header["BUNIT"],
            "detector": header["DETECTOR"],
            "channel": header["SUBCHAN"],
            "wavelengthMicrons": round(header["WAVECEN"], 3),
            "exposureMjd": sorted(float(value) for value in coadd["EXPOSURES"].data["MJD_OBS"]),
        })


def export_mosaic(path: Path, wavelength: float) -> None:
    with fits.open(path) as mosaic:
        image = mosaic["IMAGE"]
        channels = mosaic["SPECTRAL_CHANNELS"].data
        wave_table = mosaic["WCS-WAVE"].data
        wavelengths = np.ravel(wave_table["WAVELENGTHS"])
        if image.data.ndim != 3 or len(wavelengths) != image.data.shape[0]:
            raise ValueError("The mosaic wavelength table must describe every IMAGE plane.")

        matches = np.flatnonzero(np.isclose(wavelengths, wavelength, rtol=0, atol=0.0005))
        if matches.size != 1:
            raise ValueError(f"Expected one plane at {wavelength:g} µm, found {matches.size}.")
        index = int(matches[0])
        channel_match = np.isclose(channels["WAVELENGTH"], wavelengths[index], rtol=0, atol=1e-6)
        channel = channels[channel_match]
        if len(channel) != 1:
            raise ValueError("The selected wavelength must have one spectral channel record.")
        if mosaic["NHITS"].data.shape != image.data.shape:
            raise ValueError("NHITS must match the shape of the mosaic IMAGE cube.")

        pixels = np.where(mosaic["NHITS"].data[index] > 0, image.data[index], np.nan)
        write_fixture("m31", pixels, WCS(image.header, fobj=mosaic, naxis=[1, 2]), {
            "release": image.header.get("RELEASE") or mosaic[0].header.get("RELEASE"),
            "unit": image.header["BUNIT"],
            "detector": int(channel[0]["DETECTOR"]),
            "channel": int(channel[0]["SUBCHAN"]),
            "wavelengthMicrons": round(float(wavelengths[index]), 3),
            "exposureMjd": [],
        })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mosaic", type=Path, help="IRSA Mosaic Tool FITS for the M31 sample")
    parser.add_argument("--wavelength", type=float, default=0.809, help="Mosaic plane in microns")
    args = parser.parse_args()
    if args.mosaic is None:
        export_scope()
    else:
        try:
            export_mosaic(args.mosaic, args.wavelength)
        except (OSError, KeyError, ValueError) as error:
            parser.error(str(error))


if __name__ == "__main__":
    main()
