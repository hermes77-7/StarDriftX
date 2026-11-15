"""In-memory cache backend for testing and ephemeral execution."""

import time
from collections import OrderedDict
from typing import Any, Dict, Optional

from app.cache.base import BaseCache, CacheEntry, CacheMetadata, compute_etag


class MemoryCache(BaseCache):
    """In-memory LRU cache storing entries in an OrderedDict."""

    def __init__(
        self,
        max_entries: int = 1000,
        default_ttl_seconds: int = 86400 * 7,
    ):
        self.max_entries = max_entries
        self.default_ttl = default_ttl_seconds
        self._store: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[CacheEntry]:
        entry = self._store.get(key)
        if entry is None:
            self._misses += 1
            return None

        if entry.metadata.is_expired():
            del self._store[key]
            self._misses += 1
            return None

        # Move to end for LRU
        self._store.move_to_end(key)
        entry.metadata.last_accessed_at = time.time()
        self._hits += 1
        return entry

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

        # Evict oldest if full
        if len(self._store) >= self.max_entries and key not in self._store:
            self._store.popitem(last=False)

        etag = compute_etag(data, key)
        meta = CacheMetadata(
            key=key,
            data_release=data_release,
            size_bytes=len(data),
            content_type=content_type,
            etag=etag,
            created_at=time.time(),
            last_accessed_at=time.time(),
            ttl_seconds=ttl,
            extra=extra or {},
        )
        entry = CacheEntry(data=data, metadata=meta)
        self._store[key] = entry
        self._store.move_to_end(key)
        return entry

    def delete(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            return True
        return False

    def has(self, key: str) -> bool:
        entry = self._store.get(key)
        if entry is None:
            return False
        if entry.metadata.is_expired():
            del self._store[key]
            return False
        return True

    def clear(self) -> None:
        self._store.clear()
        self._hits = 0
        self._misses = 0

    def get_stats(self) -> Dict[str, Any]:
        total_bytes = sum(entry.metadata.size_bytes for entry in self._store.values())
        return {
            "type": "memory",
            "hits": self._hits,
            "misses": self._misses,
            "entry_count": len(self._store),
            "total_size_bytes": total_bytes,
            "max_entries": self.max_entries,
        }

    def invalidate_release(self, current_release: str) -> int:
        keys_to_del = [
            k for k, v in self._store.items() if v.metadata.data_release != current_release
        ]
        for k in keys_to_del:
            del self._store[k]
        return len(keys_to_del)
