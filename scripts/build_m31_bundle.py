"""Build the browser-ready M31 DR1 band files from two locally downloaded FITS cubes."""

import argparse
import json
import sys
from contextlib import ExitStack
from hashlib import sha256
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from reproject import reproject_interp

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.spherex.payload import encode_payload  # noqa: E402

RELEASE = "spherex_dr1"
TILES = (655, 677)
DESTINATION = ROOT / "frontend/public/data/m31-dr1"


def viewer_grid() -> tuple[dict, WCS]:
    sample = ROOT / "frontend/src/viewer/fixture/m31.json"
    fixture = json.loads(sample.read_text(encoding="utf-8"))
    metadata = fixture["wcs"]
    grid = {
        "crval": metadata["referenceDegrees"],
        "crpix": metadata["referencePixel"],
        "cdelt": metadata["degreesPerPixel"],
        "ctype": ["RA---TAN", "DEC--TAN"],
        "radesys": metadata["frame"],
        "shape": [fixture["height"], fixture["width"]],
    }
    wcs = WCS(naxis=2)
    wcs.wcs.crval, wcs.wcs.crpix, wcs.wcs.cdelt = grid["crval"], grid["crpix"], grid["cdelt"]
    wcs.wcs.ctype, wcs.wcs.radesys = grid["ctype"], grid["radesys"]
    wcs.array_shape = tuple(grid["shape"])
    return grid, wcs


def combine_band(cubes, projections, index: int, target: WCS):
    shape = target.array_shape
    total = np.zeros(shape)
    weight = np.zeros(shape)
    flags = np.zeros(shape, dtype=np.uint32)
    for cube, projection in zip(cubes, projections, strict=True):
        image = cube["IMAGE"].data[index]
        valid = np.isfinite(image) & (cube["NHITS"].data[index] > 0)
        if not valid.any():
            continue
        # Interpolate the image and its valid-pixel weights on the same grid.
        # Zero-hit pixels must not dilute the brightness along the tile boundary.
        projected = reproject_interp(
            (np.stack([np.where(valid, image, 0), valid]), projection),
            target, shape_out=(2, *shape), return_footprint=False,
        )
        total += np.nan_to_num(projected[0])
        weight += np.nan_to_num(projected[1])
        quality = np.where(valid, cube["FLAGS"].data[index], 0).astype(np.float64)
        mapped = reproject_interp(
            (quality, projection), target, shape_out=shape,
            order="nearest-neighbor", return_footprint=False,
        )
        flags |= np.nan_to_num(mapped).astype(np.uint32)
    covered = weight > 0
    pixels = np.full(shape, np.nan, dtype=np.float32)
    np.divide(total, weight, out=pixels, where=covered)
    return pixels, flags, covered


def build(input_dir: Path, destination: Path) -> None:
    grid, wcs = viewer_grid()
    destination.mkdir(parents=True, exist_ok=True)
    bands = []
    with ExitStack() as stack:
        cubes = [
            stack.enter_context(fits.open(input_dir / f"Npix{tile}_cube.fits", memmap=True))
            for tile in TILES
        ]
        channels = cubes[0]["SPECTRAL_CHANNELS"].data
        wavelengths = np.ravel(cubes[0]["WCS-WAVE"].data["WAVELENGTHS"])
        unit = cubes[0]["IMAGE"].header["BUNIT"]
        projections = []
        for cube in cubes:
            image = cube["IMAGE"]
            if (
                cube[0].header.get("TELESCOP") != "SPHEREx" or image.header["BUNIT"] != unit
                or len(wavelengths) != 102 or image.data.shape != (102, 512, 512)
                or any(cube[name].data.shape != image.data.shape for name in ("NHITS", "FLAGS"))
                or not np.array_equal(channels, cube["SPECTRAL_CHANNELS"].data)
                or not np.array_equal(wavelengths, np.ravel(cube["WCS-WAVE"].data["WAVELENGTHS"]))
            ):
                raise ValueError("Expected matching SPHEREx DR1 cube dimensions and calibration.")
            projections.append(WCS(image.header, fobj=cube, naxis=[1, 2]))

        for index, wavelength in enumerate(wavelengths):
            row = channels[np.isclose(channels["WAVELENGTH"], wavelength, rtol=0, atol=1e-6)]
            if len(row) != 1:
                raise ValueError(f"Expected one channel for {wavelength:g} µm.")
            detector, channel = int(row["DETECTOR"][0]), int(row["SUBCHAN"][0])
            band = (detector - 1) * 17 + channel
            pixels, flags, covered = combine_band(cubes, projections, index, wcs)
            if not covered.any():
                continue
            selection = {
                "band": band, "detector": detector, "channel": channel,
                "wavelength_um": float(wavelength),
            }
            header = {
                **selection, "version": "1.0", "source": "m31-dr1", "data_release": RELEASE,
                "collections": [RELEASE], "wcs": grid, "bunit": unit,
                "wavelength_range_um": [float(row["WL_MIN"][0]), float(row["WL_MAX"][0])],
                "coverage_fraction": float(covered.mean()),
                "flagged_fraction": float(np.count_nonzero(flags) / flags.size),
            }
            payload = encode_payload(header, pixels.tolist(), flags.tolist())
            name = f"band-{band:03d}-{sha256(payload).hexdigest()[:16]}.spx1"
            path = destination / name
            if not path.exists():
                path.write_bytes(payload)
            bands.append({**selection, "file": name})
    if not any(band["band"] == 4 for band in bands):
        raise ValueError("The M31 bundle is missing its default band.")
    manifest = {"release": RELEASE, "tiles": TILES, "bands": sorted(bands, key=lambda b: b["band"])}
    temporary = destination / "manifest.tmp"
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination / "manifest.json")
    print(f"Prepared {len(bands)} M31 DR1 bands in {destination}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--output-dir", type=Path, default=DESTINATION)
    args = parser.parse_args()
    try:
        build(args.input_dir, args.output_dir)
    except (OSError, KeyError, ValueError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
