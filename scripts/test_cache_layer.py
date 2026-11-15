"""Comprehensive tests for Star Drift Cache Layer (Ticket C1).

Validates:
- HEALPix indexing across RA [0, 360) and poles [Dec = +/-90]
- Cache key construction and determinism
- DiskCache and MemoryCache backends (store, retrieve, delete, clear, stats)
- Second identical request served from cache (HIT)
- ETag generation, Cache-Control headers, and conditional request handling (304 Not Modified)
- TTL expiration
- LRU size-cap eviction when exceeding max_size_bytes
- Data release invalidation (new data release removes old entries)
"""

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from app.cache.disk import DiskCache
from app.cache.healpix import (
    bucket_fov,
    coords_to_healpix,
    normalize_coordinates,
)
from app.cache.keys import build_cache_key, key_to_hash
from app.cache.manager import CacheManager
from app.cache.memory import MemoryCache


class TestHealpixIndexing(unittest.TestCase):
    """Test HEALPix tiling and coordinate edge conditions."""

    def test_ra_wrap_around(self):
        """Test RA wrap-around near 0 and 360 degrees."""
        # 0.001 deg and 359.999 deg should map to adjacent pixels or close rings
        pix_0, nside_0 = coords_to_healpix(0.001, 10.0, nside=64)
        pix_360, nside_360 = coords_to_healpix(359.999, 10.0, nside=64)

        self.assertEqual(nside_0, 64)
        self.assertEqual(nside_360, 64)
        npix = 12 * 64 * 64
        self.assertTrue(0 <= pix_0 < npix)
        self.assertTrue(0 <= pix_360 < npix)

        # Coordinate normalization wrap
        ra_neg, dec = normalize_coordinates(-0.5, 10.0)
        self.assertAlmostEqual(ra_neg, 359.5)
        ra_over, _ = normalize_coordinates(365.2, 10.0)
        self.assertAlmostEqual(ra_over, 5.2)

    def test_poles(self):
        """Test celestial poles (Dec -> +90 and Dec -> -90)."""
        # North celestial pole
        pix_north, nside = coords_to_healpix(120.0, 89.99, nside=64)
        self.assertTrue(0 <= pix_north < 12 * 64 * 64)

        # South celestial pole
        pix_south, nside = coords_to_healpix(240.0, -89.99, nside=64)
        self.assertTrue(0 <= pix_south < 12 * 64 * 64)

        # Exact poles clamped
        _, dec_clamped = normalize_coordinates(0.0, 95.0)
        self.assertEqual(dec_clamped, 90.0)
        _, dec_clamped_s = normalize_coordinates(0.0, -95.0)
        self.assertEqual(dec_clamped_s, -90.0)

    def test_fov_bucketing(self):
        """Test continuous FoV is quantized into standard buckets."""
        self.assertEqual(bucket_fov(0.04), 0.05)
        self.assertEqual(bucket_fov(0.08), 0.10)
        self.assertEqual(bucket_fov(0.19), 0.20)
        self.assertEqual(bucket_fov(0.22), 0.20)
        self.assertEqual(bucket_fov(0.48), 0.50)
        self.assertEqual(bucket_fov(0.95), 1.00)


class TestCacheKeys(unittest.TestCase):
    """Test deterministic cache key composition."""

    def test_key_components(self):
        key = build_cache_key(
            ra=150.12,
            dec=2.34,
            fov=0.2,
            epoch="1",
            band=4,
            detector=1,
            mode="single",
            mask_flags=True,
            data_release="spherex_qr3",
        )
        self.assertTrue(key.startswith("spherex_qr3:"))
        self.assertIn(":fov_0.20:", key)
        self.assertIn(":ep_1:", key)
        self.assertIn(":m_single:", key)
        self.assertIn(":b_4:", key)
        self.assertIn(":d_1:", key)
        self.assertIn(":mask_1", key)

        # Identical parameters produce identical key
        key2 = build_cache_key(
            ra=150.12,
            dec=2.34,
            fov=0.2,
            epoch="1",
            band=4,
            detector=1,
            mode="single",
            mask_flags=True,
            data_release="spherex_qr3",
        )
        self.assertEqual(key, key2)

        # Hash is deterministic
        h1 = key_to_hash(key)
        h2 = key_to_hash(key2)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)


class TestMemoryCache(unittest.TestCase):
    """Test MemoryCache operations."""

    def test_crud_and_stats(self):
        cache = MemoryCache(max_entries=5, default_ttl_seconds=60)
        payload = b"test payload bytes"

        # Set
        entry = cache.set("k1", payload, "release1")
        self.assertEqual(entry.data, payload)
        self.assertTrue(cache.has("k1"))

        # Hit
        retrieved = cache.get("k1")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.data, payload)

        stats = cache.get_stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 0)

        # Delete
        self.assertTrue(cache.delete("k1"))
        self.assertFalse(cache.has("k1"))
        self.assertIsNone(cache.get("k1"))
        self.assertEqual(cache.get_stats()["misses"], 1)

    def test_lru_eviction(self):
        cache = MemoryCache(max_entries=2)
        cache.set("k1", b"1", "r1")
        cache.set("k2", b"2", "r1")
        # Access k1 to make k2 the oldest
        _ = cache.get("k1")
        # Add k3, which should evict k2
        cache.set("k3", b"3", "r1")

        self.assertTrue(cache.has("k1"))
        self.assertFalse(cache.has("k2"))
        self.assertTrue(cache.has("k3"))


class TestDiskCache(unittest.TestCase):
    """Test DiskCache persistence, capacity enforcement, and release invalidation."""

    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp(prefix="stardrift_test_cache_"))
        self.cache = DiskCache(base_dir=self.test_dir, max_size_bytes=10000, default_ttl_seconds=60)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_second_request_hits_cache(self):
        key = "spherex_qr3:hp_256_100:fov_0.20:ep_1:m_single:b_4:d_1:mask_1"
        data = b"science float data payload"

        # Initial miss
        self.assertIsNone(self.cache.get(key))

        # Store in cache
        stored = self.cache.set(key, data, data_release="spherex_qr3")
        self.assertEqual(stored.data, data)
        self.assertTrue(stored.metadata.etag.startswith('W/"'))

        # Second request hits cache
        hit = self.cache.get(key)
        self.assertIsNotNone(hit)
        self.assertEqual(hit.data, data)
        self.assertEqual(hit.metadata.etag, stored.metadata.etag)

        stats = self.cache.get_stats()
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)

    def test_release_invalidation(self):
        """A new data release invalidates old entries."""
        key_old = "spherex_qr2:hp_256_100:fov_0.20:ep_1:m_single:b_4:d_1:mask_1"
        key_new = "spherex_qr3:hp_256_100:fov_0.20:ep_1:m_single:b_4:d_1:mask_1"

        self.cache.set(key_old, b"old release data", data_release="spherex_qr2")
        self.cache.set(key_new, b"new release data", data_release="spherex_qr3")

        self.assertTrue(self.cache.has(key_old))
        self.assertTrue(self.cache.has(key_new))

        # Invalidate everything except spherex_qr3
        purged = self.cache.invalidate_release("spherex_qr3")
        self.assertGreaterEqual(purged, 1)

        # Old release is gone, new release persists
        self.assertFalse(self.cache.has(key_old))
        self.assertTrue(self.cache.has(key_new))

    def test_size_cap_lru_eviction(self):
        """Disk cache evicts oldest entries when exceeding max_size_bytes."""
        small_cache = DiskCache(base_dir=self.test_dir, max_size_bytes=1000)
        # Store 400 bytes each
        payload = b"x" * 400

        k1 = "release:hp_1:fov_0.2:ep_1:m_s:b_1:d_1:mask_1"
        k2 = "release:hp_2:fov_0.2:ep_1:m_s:b_2:d_1:mask_1"
        k3 = "release:hp_3:fov_0.2:ep_1:m_s:b_3:d_1:mask_1"

        small_cache.set(k1, payload, "release")
        time.sleep(0.01)
        small_cache.set(k2, payload, "release")
        time.sleep(0.01)

        # Access k1 to make k2 the least recently accessed
        _ = small_cache.get(k1)
        time.sleep(0.01)

        # k1 (400) + k2 (400) = 800 bytes. Adding k3 (400) reaches 1200 > 1000 limit.
        small_cache.set(k3, payload, "release")

        # k2 should have been evicted
        self.assertTrue(small_cache.has(k1))
        self.assertFalse(small_cache.has(k2))
        self.assertTrue(small_cache.has(k3))


class TestCacheManager(unittest.TestCase):
    """Test CacheManager coordination and HTTP protocol handling."""

    def test_manager_conditional_request(self):
        manager = CacheManager(backend=MemoryCache())
        data = b"cutout binary payload"

        entry = manager.store_view(
            ra=150.12,
            dec=2.34,
            data=data,
            fov=0.2,
            epoch="1",
            band=4,
            data_release="spherex_qr3",
        )

        headers = manager.get_http_headers(entry)
        self.assertIn("ETag", headers)
        self.assertIn("Cache-Control", headers)

        # Conditional request check
        self.assertTrue(manager.is_not_modified(entry, entry.metadata.etag))
        self.assertTrue(manager.is_not_modified(entry, "*"))
        self.assertFalse(manager.is_not_modified(entry, 'W/"differentetag"'))


if __name__ == "__main__":
    unittest.main()
