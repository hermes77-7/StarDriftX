"""Generate mock fixtures from real spike FITS data and synthetic test vectors.

Outputs in shared/fixtures/mock/:
- cutout_scope.bin (SPX1 binary test vector)
- cutout_scope.json
- flags_scope.json
- spectrum_scope.json
- catalog_scope.json
- meta.json
- resolve_samples.json
"""

import json
import math
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shared.payload import encode_payload

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "shared" / "fixtures" / "mock"
FIXTURE_DIR.mkdir(parents=True, exist_ok=True)


def mock_time_range(start: float, end: float) -> dict:
    origin = datetime(1858, 11, 17, tzinfo=UTC)
    return {
        "mjd_start": start,
        "mjd_end": end,
        "utc_start": (origin + timedelta(days=start)).isoformat().replace("+00:00", "Z"),
        "utc_end": (origin + timedelta(days=end)).isoformat().replace("+00:00", "Z"),
    }


def generate():
    # 1. Read real spike FITS file
    fits_path = ROOT / "shared" / "fixtures" / "spherex" / "scope-d1-ch04.fits"
    with open(fits_path, "rb") as f:
        # FITS Primary HDU header is 2880 bytes
        f.seek(2880)
        raw_floats = f.read(36 * 36 * 4)
        # FITS is big-endian IEEE-754 float32
        floats_big = struct.unpack(f">{36*36}f", raw_floats)

    grid_2d = [
        list(floats_big[i * 36 : (i + 1) * 36]) for i in range(36)
    ]

    header = {
        "version": "1.0",
        "data_release": "spherex_qr3",
        "wcs": {
            "crval": [150.12, 2.34],
            "crpix": [18.5, 18.5],
            "cdelt": [-0.0017083333333333, 0.0017083333333333],
            "ctype": ["RA---TAN", "DEC--TAN"],
            "radesys": "ICRS",
            "shape": [36, 36],
        },
        "bunit": "MJy/sr",
        "detector": 1,
        "channel": 4,
        "band": 4,
        "wavelength_um": 0.809,
        "bandwidth_um": 0.021,
        "nexp": 2,
        "epoch": "4",
        "mjd_range": [61186.359738, 61186.361248],
    }

    # Generate synthetic flags for the 36x36 grid
    flags_2d = [[0 for _ in range(36)] for _ in range(36)]
    # Mark known source in center
    for r in range(16, 20):
        for c in range(16, 20):
            flags_2d[r][c] = 1 << 21  # Known source marker (bit 21)
    flags_2d[16][16] |= 1 << 28
    flag_definitions = {
        21: ("known_source", "SOURCE: Known catalog source"),
        28: ("halo", "HALO: Optical halo artifact"),
    }
    summary = {
        name: sum(bool(flags & (1 << bit)) for row in flags_2d for flags in row)
        for bit, (name, _) in flag_definitions.items()
    }
    header["flags_summary"] = summary

    # 1. Cutout JSON
    cutout_json = {"header": header, "data": grid_2d}
    with open(FIXTURE_DIR / "cutout_scope.json", "w") as f:
        json.dump(cutout_json, f, indent=2)

    # 2. Cutout Binary SPX1
    payload_bin = encode_payload(header, grid_2d, flags_2d)
    with open(FIXTURE_DIR / "cutout_scope.bin", "wb") as f:
        f.write(payload_bin)

    # 3. Flags JSON
    flags_json = {
        "categories_present": list(summary),
        "total_pixels": 1296,
        "flagged_pixels": sum(bool(flags) for row in flags_2d for flags in row),
        "flag_summary": summary,
        "bitmask_definitions": {str(bit): description for bit, (_, description)
                                in flag_definitions.items()},
    }
    with open(FIXTURE_DIR / "flags_scope.json", "w") as f:
        json.dump(flags_json, f, indent=2)

    # 4. 102-point Spectrum JSON
    # SPHEREx covers 0.75 - 5.0 µm in 6 detectors with 17 channels each
    detector_bounds = [
        (1, 0.75, 1.11),
        (2, 1.11, 1.64),
        (3, 1.64, 2.42),
        (4, 2.42, 3.82),
        (5, 3.82, 4.42),
        (6, 4.42, 5.00),
    ]
    points = []
    band_idx = 1
    for det, min_w, max_w in detector_bounds:
        step = (max_w - min_w) / 17.0
        for ch in range(1, 18):
            w = round(min_w + (ch - 0.5) * step, 4)
            # Simulated stellar continuum (Rayleigh-Jeans / blackbody-like curve)
            base_flux = 10.0 / (w**2) + 0.5 * math.sin(w * 3)
            unc = round(0.05 * base_flux + 0.01, 3)
            flux = round(base_flux, 3)
            points.append(
                {
                    "wavelength_um": w,
                    "flux": flux,
                    "flux_unit": "MJy/sr",
                    "uncertainty": unc,
                    "exposure_id": f"level2_2026W22_1A_{det:02d}{ch:02d}_spx",
                    "detector": det,
                    "channel": ch,
                    "band_index": band_idx,
                    "flagged": False,
                    "flags": [],
                }
            )
            band_idx += 1

    spectrum_json = {
        "ra": 150.12,
        "dec": 2.34,
        "aperture_arcsec": 6.15,
        "epoch": "all",
        "points": points,
    }
    with open(FIXTURE_DIR / "spectrum_scope.json", "w") as f:
        json.dump(spectrum_json, f, indent=2)

    # 5. Catalog sources JSON
    catalog_json = {
        "catalog": "gaia",
        "count": 4,
        "sources": [
            {
                "id": "Gaia DR3 381928471928371",
                "name": "HD 87152",
                "ra": 150.1208,
                "dec": 2.3402,
                "catalog": "gaia",
                "magnitude": 10.45,
                "mag_band": "G",
                "details": {"pmra": -14.2, "pmdec": 3.8, "parallax": 5.12},
            },
            {
                "id": "Gaia DR3 381928471928399",
                "name": "TYC 241-119-1",
                "ra": 150.1450,
                "dec": 2.3610,
                "catalog": "gaia",
                "magnitude": 12.18,
                "mag_band": "G",
                "details": {"pmra": -2.1, "pmdec": -8.4, "parallax": 2.31},
            },
            {
                "id": "WISE J100028.9+022024",
                "name": "WISE J100028.9+022024",
                "ra": 150.1205,
                "dec": 2.3401,
                "catalog": "wise",
                "magnitude": 9.82,
                "mag_band": "W1",
                "details": {"w1": 9.82, "w2": 9.75, "w3": 9.60, "w4": 8.90},
            },
            {
                "id": "SIMBAD-HD87152",
                "name": "HD 87152",
                "ra": 150.1208,
                "dec": 2.3402,
                "catalog": "simbad",
                "magnitude": 10.45,
                "mag_band": "V",
                "details": {"otype": "Star", "sp_type": "G5V"},
            },
        ],
    }
    with open(FIXTURE_DIR / "catalog_scope.json", "w") as f:
        json.dump(catalog_json, f, indent=2)

    # 6. Meta JSON
    meta_json = {
        "data_release": "spherex_qr3",
        "coverage_notes": (
            "Mock metadata for the bundled QR3 Scope cutout. "
            "The time range describes the fixture exposures."
        ),
        "fov_limits": {"min_deg": 0.05, "max_deg": 1.0, "default_deg": 0.2},
        "supported_bands": {
            "total_bands": 102,
            "detectors": 6,
            "channels_per_detector": 17,
            "wavelength_range_um": [0.75, 5.0],
        },
        "epochs": [
            {
                "id": "4",
                "name": "Survey 4 (Scope fixture)",
                "time_range": mock_time_range(*header["mjd_range"]),
            },
            {
                "id": "all",
                "name": "All Epochs Combined",
                "time_range": mock_time_range(*header["mjd_range"]),
            },
        ],
    }
    with open(FIXTURE_DIR / "meta.json", "w") as f:
        json.dump(meta_json, f, indent=2)

    # 7. Resolve samples
    resolve_json = {
        "Vega": {"name": "Vega", "ra": 279.23473, "dec": 38.78369, "resolver": "SIMBAD"},
        "M31": {"name": "M31", "ra": 10.68471, "dec": 41.26875, "resolver": "SIMBAD"},
        "Crab Nebula": {
            "name": "Crab Nebula",
            "ra": 83.63308,
            "dec": 22.0145,
            "resolver": "SIMBAD",
        },
        "HD 87152": {"name": "HD 87152", "ra": 150.1208, "dec": 2.3402, "resolver": "SIMBAD"},
    }
    with open(FIXTURE_DIR / "resolve_samples.json", "w") as f:
        json.dump(resolve_json, f, indent=2)

    print(f"Generated all fixtures in {FIXTURE_DIR}")


if __name__ == "__main__":
    generate()
