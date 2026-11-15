"""Cache key construction and parsing for Star Drift views.

Combines:
- Data release identifier (provenance)
- HEALPix pixel index and NSIDE of the view center
- Quantized Field-of-View bucket
- Survey epoch window
- Band number or detector index
- Stacking mode ('single' vs 'stack')
- Flag masking state
"""

import hashlib
import re
from typing import Optional, Tuple

from app.cache.healpix import bucket_fov, coords_to_healpix

CACHE_SEGMENT = re.compile(r"[a-zA-Z0-9_-]+")


def build_cache_key(
    ra: float,
    dec: float,
    fov: float = 0.2,
    epoch: str = "all",
    band: Optional[int] = None,
    detector: Optional[int] = None,
    mode: str = "single",
    mask_flags: bool = True,
    data_release: str = "spherex_qr3",
    *,
    mjd_window: tuple[float, float] | None = None,
) -> str:
    """Build a deterministic, release-scoped cache key for a view request."""
    fov_b = bucket_fov(fov)
    pixel, nside = coords_to_healpix(ra, dec, fov_deg=fov_b)

    band_str = str(band) if band is not None else "none"
    det_str = str(detector) if detector is not None else "none"
    mask_str = "1" if mask_flags else "0"
    epoch_str = str(epoch).strip().lower()
    mode_str = str(mode).strip().lower()

    key = (
        f"{data_release}:"
        f"hp_{nside}_{pixel}:"
        f"fov_{fov_b:.2f}:"
        f"ep_{epoch_str}:"
        f"m_{mode_str}:"
        f"b_{band_str}:"
        f"d_{det_str}:"
        f"mask_{mask_str}:"
        f"grid_{float(ra % 360).hex()}_{float(dec or 0).hex()}_{float(fov).hex()}"
    )
    if mjd_window is not None:
        start, end = mjd_window
        key += f":mjd_{float(start).hex()}_{float(end).hex()}"
    return key


def key_to_hash(key: str) -> str:
    """Generate SHA-256 hash of the cache key for filesystem and S3 safety."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def key_to_filepath_parts(key: str) -> Tuple[str, str, str]:
    """Convert cache key to hierarchical filesystem path components.

    Returns:
        (data_release, healpix_dir, filename_prefix)
    """
    parts = key.split(":")
    release = parts[0] if parts else "default"
    hp_part = parts[1] if len(parts) > 1 else "hp_0_0"
    if not CACHE_SEGMENT.fullmatch(release) or not CACHE_SEGMENT.fullmatch(hp_part):
        raise ValueError("Invalid cache namespace or tile.")
    khash = key_to_hash(key)
    return release, hp_part, khash
