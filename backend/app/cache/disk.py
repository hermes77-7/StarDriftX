"""Disk-based cache backend with LRU size-capped eviction and TTL support."""

import json
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.cache.base import BaseCache, CacheEntry, CacheMetadata, compute_etag
from app.cache.keys import key_to_filepath_parts


class DiskCache(BaseCache):
    """Local disk cache with size-bounded LRU eviction and release namespace partitioning."""

    def __init__(
        self,
        base_dir: Path,
        max_size_bytes: int = 500 * 1024 * 1024,  # 500 MB
        default_ttl_seconds: int = 86400 * 7,      # 7 days
    ):
        self.base_dir = Path(base_dir).resolve()
        self.max_size_bytes = max_size_bytes
        self.default_ttl = default_ttl_seconds
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self._hits = 0
        self._misses = 0

    def _get_paths(self, key: str) -> Tuple[Path, Path]:
        release, hp_dir, khash = key_to_filepath_parts(key)
        target_dir = self.base_dir / release / hp_dir
        bin_path = target_dir / f"{khash}.bin"
        meta_path = target_dir / f"{khash}.json"
        return bin_path, meta_path

    def get(self, key: str) -> Optional[CacheEntry]:
        bin_path, meta_path = self._get_paths(key)
        if not bin_path.exists() or not meta_path.exists():
            self._misses += 1
            return None

        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_dict = json.load(f)
            meta = CacheMetadata.from_dict(meta_dict)

            # Check expiration
            if meta.is_expired():
                self.delete(key)
                self._misses += 1
                return None

            with open(bin_path, "rb") as f:
                data = f.read()

            # Update last accessed time for LRU tracking
            meta.last_accessed_at = time.time()
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta.to_dict(), f)

            self._hits += 1
            return CacheEntry(data=data, metadata=meta)
        except Exception:
            self._misses += 1
            return None

    def set(
        self,
        key: str,
        data: bytes,
        data_release: str,
        content_type: str = "application/octet-stream",
        ttl_seconds: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> CacheEntry:
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        bin_path, meta_path = self._get_paths(key)
        bin_path.parent.mkdir(parents=True, exist_ok=True)

        needed_space = len(data)
        self._ensure_capacity(needed_space)

        etag = compute_etag(data, key)
        meta = CacheMetadata(
            key=key,
            data_release=data_release,
            size_bytes=needed_space,
            content_type=content_type,
            etag=etag,
            created_at=time.time(),
            last_accessed_at=time.time(),
            ttl_seconds=ttl,
            extra=extra or {},
        )

        with open(bin_path, "wb") as f:
            f.write(data)

        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta.to_dict(), f)

        return CacheEntry(data=data, metadata=meta)

    def delete(self, key: str) -> bool:
        bin_path, meta_path = self._get_paths(key)
        deleted = False
        if bin_path.exists():
            try:
                bin_path.unlink()
                deleted = True
            except OSError:
                pass
        if meta_path.exists():
            try:
                meta_path.unlink()
                deleted = True
            except OSError:
                pass
        return deleted

    def has(self, key: str) -> bool:
        bin_path, meta_path = self._get_paths(key)
        if not bin_path.exists() or not meta_path.exists():
            return False
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta_dict = json.load(f)
            meta = CacheMetadata.from_dict(meta_dict)
            return not meta.is_expired()
        except Exception:
            return False

    def clear(self) -> None:
        if self.base_dir.exists():
            for child in self.base_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        self._hits = 0
        self._misses = 0

    def _collect_entries(self) -> List[Tuple[Path, Path, CacheMetadata]]:
        entries = []
        for meta_file in self.base_dir.glob("*/*/*.json"):
            bin_file = meta_file.with_suffix(".bin")
            if bin_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = CacheMetadata.from_dict(json.load(f))
                    entries.append((bin_file, meta_file, meta))
                except Exception:
                    continue
        return entries

    def _ensure_capacity(self, needed_bytes: int) -> None:
        entries = self._collect_entries()
        total_size = sum(m.size_bytes for _, _, m in entries)

        if total_size + needed_bytes <= self.max_size_bytes:
            return

        # Target 85% of max capacity after eviction to prevent constant thrashing
        target_size = int(self.max_size_bytes * 0.85) - needed_bytes
        # Sort oldest accessed first
        entries.sort(key=lambda item: item[2].last_accessed_at)

        for bin_path, meta_path, meta in entries:
            try:
                bin_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                total_size -= meta.size_bytes
            except OSError:
                pass
            if total_size <= target_size:
                break

    def get_stats(self) -> Dict[str, Any]:
        entries = self._collect_entries()
        total_size = sum(m.size_bytes for _, _, m in entries)
        return {
            "type": "disk",
            "base_dir": str(self.base_dir),
            "hits": self._hits,
            "misses": self._misses,
            "entry_count": len(entries),
            "total_size_bytes": total_size,
            "max_size_bytes": self.max_size_bytes,
        }

    def invalidate_release(self, current_release: str) -> int:
        """Purge all entries whose data_release does not match current_release."""
        count = 0
        for bin_path, meta_path, metadata in self._collect_entries():
            if metadata.data_release == current_release:
                continue
            bin_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            count += 1
            # Remove only empty cache directories and leave unrelated files alone.
            for directory in (bin_path.parent, bin_path.parent.parent):
                try:
                    directory.rmdir()
                except OSError:
                    break
        return count
