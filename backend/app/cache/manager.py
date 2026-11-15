"""Cache manager for Star Drift API."""

import os
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

from app.cache.base import BaseCache, CacheEntry
from app.cache.disk import DiskCache
from app.cache.keys import build_cache_key
from app.cache.memory import MemoryCache
from app.cache.s3 import S3Cache


class CacheManager:
    """Coordinates key generation, storage backend, HTTP headers, and invalidation."""

    def __init__(self, backend: Optional[BaseCache] = None):
        self.backend = backend or self._init_from_env()
        self._lock = RLock()

    @staticmethod
    def _init_from_env() -> BaseCache:
        cache_type = os.getenv("CACHE_TYPE", "disk").lower()
        ttl_days = int(os.getenv("CACHE_TTL_DAYS", "7"))
        ttl_seconds = ttl_days * 86400

        if cache_type == "memory":
            max_entries = int(os.getenv("CACHE_MAX_ENTRIES", "1000"))
            return MemoryCache(max_entries=max_entries, default_ttl_seconds=ttl_seconds)

        # Default: Disk Cache
        cache_dir = Path(os.getenv("CACHE_DIR", "data/cache"))
        max_size_mb = int(os.getenv("CACHE_MAX_SIZE_MB", "500"))
        disk_cache = DiskCache(
            base_dir=cache_dir,
            max_size_bytes=max_size_mb * 1024 * 1024,
            default_ttl_seconds=ttl_seconds,
        )

        if cache_type == "s3":
            bucket = os.getenv("S3_BUCKET", "")
            if bucket:
                return S3Cache(
                    bucket_name=bucket,
                    prefix=os.getenv("S3_PREFIX", "stardrift"),
                    endpoint_url=os.getenv("S3_ENDPOINT_URL"),
                    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
                    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
                    region_name=os.getenv("AWS_REGION"),
                    fallback_cache=disk_cache,
                    default_ttl_seconds=ttl_seconds,
                )

        return disk_cache

    def get_view(
        self,
        ra: float,
        dec: float,
        fov: float = 0.2,
        epoch: str = "all",
        band: Optional[int] = None,
        detector: Optional[int] = None,
        mode: str = "single",
        mask_flags: bool = True,
        data_release: str = "spherex_qr3",
    ) -> Optional[CacheEntry]:
        """Look up a cached cutout view using HEALPix-indexed view parameters."""
        key = build_cache_key(
            ra=ra,
            dec=dec,
            fov=fov,
            epoch=epoch,
            band=band,
            detector=detector,
            mode=mode,
            mask_flags=mask_flags,
            data_release=data_release,
        )
        return self.get(key)

    def store_view(
        self,
        ra: float,
        dec: float,
        data: bytes,
        fov: float = 0.2,
        epoch: str = "all",
        band: Optional[int] = None,
        detector: Optional[int] = None,
        mode: str = "single",
        mask_flags: bool = True,
        data_release: str = "spherex_qr3",
        content_type: str = "application/octet-stream",
        extra: Optional[Dict[str, Any]] = None,
    ) -> CacheEntry:
        """Store a cutout view in the cache."""
        key = build_cache_key(
            ra=ra,
            dec=dec,
            fov=fov,
            epoch=epoch,
            band=band,
            detector=detector,
            mode=mode,
            mask_flags=mask_flags,
            data_release=data_release,
        )
        with self._lock:
            return self.backend.set(
                key=key,
                data=data,
                data_release=data_release,
                content_type=content_type,
                extra=extra,
            )

    def get(self, key: str) -> CacheEntry | None:
        with self._lock:
            return self.backend.get(key)

    def set(self, key: str, data: bytes, release: str) -> CacheEntry:
        with self._lock:
            return self.backend.set(key, data, release)

    @staticmethod
    def is_not_modified(entry: CacheEntry, if_none_match: Optional[str]) -> bool:
        """Check if HTTP client's If-None-Match matches cached entry ETag."""
        if not if_none_match or not entry.metadata.etag:
            return False
        # If-None-Match uses weak comparison, ignoring the W/ prefix.
        req_etags = [tag.strip().removeprefix("W/") for tag in if_none_match.split(",")]
        entry_etag = entry.metadata.etag.removeprefix("W/")
        return entry_etag in req_etags or "*" in req_etags

    @staticmethod
    def get_http_headers(entry: CacheEntry) -> Dict[str, str]:
        """Construct standard caching headers for HTTP response."""
        return {
            "ETag": entry.metadata.etag,
            "Cache-Control": f"public, max-age={entry.metadata.ttl_seconds}",
            "X-Data-Release": entry.metadata.data_release,
            "Content-Type": entry.metadata.content_type,
        }

    def invalidate_old_releases(self, current_release: str) -> int:
        """Invalidate entries from older data releases."""
        with self._lock:
            return self.backend.invalidate_release(current_release)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return self.backend.get_stats()


# Global default cache manager instance
default_cache_manager = CacheManager()
