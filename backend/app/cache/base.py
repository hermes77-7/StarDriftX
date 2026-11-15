"""Abstract base definitions and metadata for the Star Drift cache layer."""

import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class CacheMetadata:
    key: str
    data_release: str
    size_bytes: int
    content_type: str = "application/octet-stream"
    etag: str = ""
    created_at: float = field(default_factory=time.time)
    last_accessed_at: float = field(default_factory=time.time)
    ttl_seconds: int = 86400 * 7  # 7 days default
    extra: Dict[str, Any] = field(default_factory=dict)

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        now = current_time if current_time is not None else time.time()
        return (now - self.created_at) > self.ttl_seconds

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CacheMetadata":
        return cls(**data)


@dataclass
class CacheEntry:
    data: bytes
    metadata: CacheMetadata


def compute_etag(data: bytes, key: str = "") -> str:
    """Compute an HTTP ETag for binary payload and key."""
    digest = hashlib.sha256(data).hexdigest()[:16]
    return f'W/"{digest}"'


class BaseCache(ABC):
    """Abstract interface for Star Drift cache backends."""

    @abstractmethod
    def get(self, key: str) -> Optional[CacheEntry]:
        """Retrieve a cached entry by key, or None if expired/not found."""
        pass

    @abstractmethod
    def set(
        self,
        key: str,
        data: bytes,
        data_release: str,
        content_type: str = "application/octet-stream",
        ttl_seconds: int = 86400 * 7,
        extra: Optional[Dict[str, Any]] = None,
    ) -> CacheEntry:
        """Store an entry in the cache."""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete an entry by key. Returns True if deleted, False otherwise."""
        pass

    @abstractmethod
    def has(self, key: str) -> bool:
        """Check if an unexpired entry exists for the key."""
        pass

    @abstractmethod
    def clear(self) -> None:
        """Clear all entries in the cache."""
        pass

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """Return cache statistics (hits, misses, total size, entries count)."""
        pass

    @abstractmethod
    def invalidate_release(self, current_release: str) -> int:
        """Invalidate and remove all entries whose data_release is not current_release."""
        pass
