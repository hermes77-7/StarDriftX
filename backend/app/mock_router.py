"""Star Drift Mock API Router (Sprint 0-1 - Tickets S3 & C1).

Provides mock responses using real spike FITS fixtures and calibrated synthetic data,
enabling Developer B (frontend) to develop the viewer, time features, and overlays.
Integrated with the HEALPix-indexed cache layer (Ticket C1).
"""

import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.spherex.epochs import Epoch

router = APIRouter(prefix="/api", tags=["mock"])

FIXTURE_DIR = Path(__file__).resolve().parent.parent.parent / "shared" / "fixtures" / "mock"


def _load_json(filename: str) -> dict:
    with open(FIXTURE_DIR / filename, "r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/flags")
def get_flags(
    ra: float = Query(..., allow_inf_nan=False),
    dec: float = Query(..., allow_inf_nan=False),
    fov: float = Query(0.2, ge=0.05, le=1.0),
    epoch: Epoch = "all",
    band: Optional[int] = Query(None, ge=1, le=102),
    detector: Optional[int] = Query(None, ge=1, le=6),
):
    """Return decoded quality flags for the requested field."""
    if not (0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "INVALID_COORDINATES",
                    "message": "Coordinates out of bounds.",
                }
            },
        )
    return _load_json("flags_scope.json")


@router.get("/spectrum")
def get_spectrum(
    ra: float = Query(..., allow_inf_nan=False),
    dec: float = Query(..., allow_inf_nan=False),
    aperture: float = Query(6.15, allow_inf_nan=False),
    epoch: Epoch = "all",
):
    """Return 102-band spectral flux extraction for a position."""
    if not (0.0 <= ra < 360.0 and -90.0 <= dec <= 90.0):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "INVALID_COORDINATES",
                    "message": "Coordinates out of bounds.",
                }
            },
        )
    data = _load_json("spectrum_scope.json")
    data["ra"] = ra
    data["dec"] = dec
    data["aperture_arcsec"] = aperture
    data["epoch"] = epoch
    return data
