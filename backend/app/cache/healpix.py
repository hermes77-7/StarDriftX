"""HEALPix sky indexing and Field of View bucketing for Star Drift caching.

HEALPix provides equal-area spherical pixelization, preventing coordinate
singularities and edge discontinuities at the celestial poles (Dec = +/-90)
and the RA wrap-around (RA = 0 / 360).
"""

import math
from typing import Optional, Tuple

# Standard FoV quantization buckets (in degrees)
FOV_BUCKETS = (0.05, 0.10, 0.20, 0.50, 1.00)


def normalize_coordinates(ra_deg: float, dec_deg: float) -> Tuple[float, float]:
    """Normalize RA to [0, 360) and clamp Dec to [-90, 90]."""
    ra = float(ra_deg) % 360.0
    if ra < 0.0:
        ra += 360.0

    dec = max(-90.0, min(90.0, float(dec_deg)))
    return ra, dec


def bucket_fov(fov_deg: float) -> float:
    """Bucket a continuous field of view into the closest standard FoV bucket."""
    fov = max(0.05, min(1.0, float(fov_deg)))
    # Pick the closest bucket
    best_bucket = min(FOV_BUCKETS, key=lambda b: abs(b - fov))
    return best_bucket


def get_nside_for_fov(fov_deg: float) -> int:
    """Select appropriate HEALPix NSIDE power-of-2 matching FoV angular scale."""
    bucket = bucket_fov(fov_deg)
    if bucket >= 0.5:
        return 64   # ~0.92 deg resolution
    elif bucket >= 0.2:
        return 256  # ~0.23 deg resolution
    elif bucket >= 0.1:
        return 512  # ~0.11 deg resolution
    else:
        return 1024 # ~0.057 deg resolution


def _pure_python_ang2pix_ring(nside: int, theta_rad: float, phi_rad: float) -> int:
    """Compute HEALPix RING pixel index in pure Python (Górski et al. 2005).

    Args:
        nside: HEALPix resolution parameter (power of 2).
        theta_rad: Colatitude in radians [0, pi] (where 0 is North Pole).
        phi_rad: Azimuth in radians [0, 2*pi).
    """
    z = math.cos(theta_rad)
    phi = phi_rad % (2.0 * math.pi)
    if phi < 0.0:
        phi += 2.0 * math.pi

    tt = phi / (0.5 * math.pi)  # [0, 4)

    if abs(z) <= 2.0 / 3.0:
        ascending = int(nside * (0.5 + tt - 0.75 * z))
        descending = int(nside * (0.5 + tt + 0.75 * z))
        ring = nside + 1 + ascending - descending
        longitude = (ascending + descending - nside + (1 - ring % 2)) // 2
        return 2 * nside * (nside - 1) + (ring - 1) * 4 * nside + longitude % (4 * nside)

    fraction = tt % 1
    radius = nside * math.sqrt(3 * (1 - abs(z)))
    ring = int(fraction * radius) + int((1 - fraction) * radius) + 1
    longitude = int(tt * ring)
    if z > 0:
        return 2 * ring * (ring - 1) + longitude
    return 12 * nside * nside - 2 * ring * (ring + 1) + longitude


def coords_to_healpix(
    ra_deg: float,
    dec_deg: float,
    nside: Optional[int] = None,
    fov_deg: Optional[float] = None,
) -> Tuple[int, int]:
    """Convert RA and Dec (degrees) to HEALPix RING pixel index.

    Returns:
        (pixel_index, nside_used)
    """
    ra, dec = normalize_coordinates(ra_deg, dec_deg)

    if nside is None:
        if fov_deg is not None:
            nside = get_nside_for_fov(fov_deg)
        else:
            nside = 256

    # Convert RA/Dec to spherical colatitude and azimuth
    theta = math.radians(90.0 - dec)
    phi = math.radians(ra)

    # Use one implementation so boundary conventions are stable across installations.
    return _pure_python_ang2pix_ring(nside, theta, phi), nside
