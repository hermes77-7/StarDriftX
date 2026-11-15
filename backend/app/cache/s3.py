"""S3-compatible cache adapter for Star Drift cloud object storage."""

import json
import logging
import re
import time
from typing import Any, Dict, Optional

from app.cache.base import BaseCache, CacheEntry, CacheMetadata, compute_etag
from app.cache.keys import key_to_filepath_parts

logger = logging.getLogger(__name__)
CACHE_OBJECT = re.compile(r"[a-zA-Z0-9_-]+/hp_\d+_\d+/[0-9a-f]{64}\.(bin|json)")


class S3Cache(BaseCache):
    """S3-compatible cache backend (AWS S3, MinIO, Cloudflare R2, Ceph).

    If boto3 is unavailable or credentials are missing, falls back to a supplied
    fallback cache (e.g. DiskCache) so the application remains fully functional.
    """

    def __init__(
        self,
        bucket_name: str,
        prefix: str = "cache",
        endpoint_url: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region_name: Optional[str] = None,
        fallback_cache: Optional[BaseCache] = None,
        default_ttl_seconds: int = 86400 * 7,
    ):
        self.bucket_name = bucket_name
        self.prefix = prefix.strip("/")
        self.fallback = fallback_cache
        self.default_ttl = default_ttl_seconds

        self._hits = 0
        self._misses = 0
        self._client = None

        try:
            import boto3
            session = boto3.session.Session()
            self._client = session.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
                region_name=region_name,
            )
        except (ImportError, Exception) as e:
            logger.warning(
                "S3 client could not be initialized (%s). Operating with fallback cache.",
                e,
            )
            self._client = None

    def _s3_key(self, key: str) -> str:
        release, hp_dir, khash = key_to_filepath_parts(key)
        return "/".join(part for part in (self.prefix, release, hp_dir, f"{khash}.bin") if part)

    def _meta_key(self, key: str) -> str:
        release, hp_dir, khash = key_to_filepath_parts(key)
        return "/".join(part for part in (self.prefix, release, hp_dir, f"{khash}.json") if part)

    def get(self, key: str) -> Optional[CacheEntry]:
        if self._client is None:
            if self.fallback:
                return self.fallback.get(key)
            self._misses += 1
            return None

        try:
            s3_k = self._s3_key(key)
            meta_k = self._meta_key(key)

            # Retrieve metadata
            meta_obj = self._client.get_object(Bucket=self.bucket_name, Key=meta_k)
            meta_dict = json.loads(meta_obj["Body"].read().decode("utf-8"))
            meta = CacheMetadata.from_dict(meta_dict)

            if meta.is_expired():
                self.delete(key)
                self._misses += 1
                return None

            data_obj = self._client.get_object(Bucket=self.bucket_name, Key=s3_k)
            data = data_obj["Body"].read()

            self._hits += 1
            return CacheEntry(data=data, metadata=meta)
        except Exception as e:
            logger.debug("S3 cache get error: %s", e)
            if self.fallback:
                return self.fallback.get(key)
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

        if self._client is not None:
            try:
                s3_k = self._s3_key(key)
                meta_k = self._meta_key(key)

                self._client.put_object(
                    Bucket=self.bucket_name,
                    Key=s3_k,
                    Body=data,
                    ContentType=content_type,
                )
                self._client.put_object(
                    Bucket=self.bucket_name,
                    Key=meta_k,
                    Body=json.dumps(meta.to_dict()).encode("utf-8"),
                    ContentType="application/json",
                )
            except Exception as e:
                logger.warning("Failed to store entry in S3: %s", e)
                if self.fallback:
                    return self.fallback.set(
                        key, data, data_release, content_type, ttl_seconds, extra
                    )

        if self.fallback:
            self.fallback.set(key, data, data_release, content_type, ttl_seconds, extra)

        return CacheEntry(data=data, metadata=meta)

    def delete(self, key: str) -> bool:
        deleted = False
        if self._client is not None:
            try:
                s3_k = self._s3_key(key)
                meta_k = self._meta_key(key)
                self._client.delete_object(Bucket=self.bucket_name, Key=s3_k)
                self._client.delete_object(Bucket=self.bucket_name, Key=meta_k)
                deleted = True
            except Exception:
                pass

        if self.fallback:
            fb_del = self.fallback.delete(key)
            deleted = deleted or fb_del
        return deleted

    def has(self, key: str) -> bool:
        if self._client is not None:
            try:
                meta_k = self._meta_key(key)
                self._client.head_object(Bucket=self.bucket_name, Key=meta_k)
                return True
            except Exception:
                pass
        if self.fallback:
            return self.fallback.has(key)
        return False

    def clear(self) -> None:
        if self.fallback:
            self.fallback.clear()
        self._hits = 0
        self._misses = 0

    def get_stats(self) -> Dict[str, Any]:
        stats = {
            "type": "s3",
            "bucket": self.bucket_name,
            "prefix": self.prefix,
            "connected": self._client is not None,
            "hits": self._hits,
            "misses": self._misses,
        }
        if self.fallback:
            stats["fallback"] = self.fallback.get_stats()
        return stats

    def invalidate_release(self, current_release: str) -> int:
        if self._client is None:
            return self.fallback.invalidate_release(current_release) if self.fallback else 0

        base = f"{self.prefix}/" if self.prefix else ""
        removed = 0
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket_name, Prefix=base):
            objects = []
            for item in page.get("Contents", []):
                key = item["Key"]
                relative = key.removeprefix(base)
                namespace = relative.split("/", 1)[0]
                if CACHE_OBJECT.fullmatch(relative) and namespace != current_release:
                    objects.append({"Key": key})
            if objects:
                result = self._client.delete_objects(
                    Bucket=self.bucket_name, Delete={"Objects": objects},
                )
                if result.get("Errors"):
                    raise OSError("S3 failed to delete one or more cache objects.")
                removed += sum(item["Key"].endswith(".bin") for item in result.get("Deleted", []))
        if self.fallback:
            fallback_removed = self.fallback.invalidate_release(current_release)
            return max(removed, fallback_removed)
        return removed
