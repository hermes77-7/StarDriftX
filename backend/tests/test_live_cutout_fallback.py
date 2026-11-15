import asyncio
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import live_router
from app.cache.disk import DiskCache
from app.cache.manager import CacheManager
from app.cache.memory import MemoryCache
from app.shared_work import SharedWork
from app.spherex.coadd import NoCoverageError
from app.spherex.epochs import SURVEYS
from app.spherex.irsa import ArchiveError, ArchiveTimeout
from app.spherex.releases import BOOTSTRAP_RELEASE


@pytest.mark.parametrize("failure", [
    ArchiveError("unavailable"), ArchiveTimeout("timed out"), NoCoverageError("empty"),
])
def test_dr1_fallback_is_limited_to_all_surveys_and_yields_to_live(monkeypatch, tmp_path, failure):
    app = FastAPI()
    app.include_router(live_router.router)
    app.state.cutout_work = SharedWork()
    app.state.dr1_cache = CacheManager(DiskCache(tmp_path))
    images = np.ones((1, 1))
    flags = np.zeros((1, 1), dtype=np.uint32)
    calls = []
    live_available = False

    async def live(release, region, selection, epoch, mask_flags):
        calls.append(epoch)
        if not live_available:
            raise failure
        return SimpleNamespace(brightness=images, flags=flags), []

    async def cube(region, selection, mask_flags):
        calls.append("cube")
        assert selection.channel == 4 and mask_flags
        header = {"source": "dr1-cube", "version": "1.0", "data_release": "spherex_dr1",
                  "wcs": {"shape": [1, 1]}}
        return header, images, flags

    app.state.cutouts = SimpleNamespace(
        archive=SimpleNamespace(surveys=SURVEYS),
        release=BOOTSTRAP_RELEASE, make_cutout=live, dr1_cube=SimpleNamespace(make_cutout=cube),
    )
    monkeypatch.setattr(live_router, "default_cache_manager", CacheManager(MemoryCache()))
    monkeypatch.setattr(live_router, "cutout_header", lambda *_: {
        "source": "irsa", "version": "1.0", "data_release": BOOTSTRAP_RELEASE.namespace,
        "wcs": {"shape": [1, 1]},
    })

    with TestClient(app) as client:
        url = "/api/cutout?ra=150.12&dec=2.34&band=4&format=json"
        response = client.get(url + "&epoch=1")
        assert response.status_code in (404, 503, 504)
        assert calls == ["1"]

        response = client.get(url)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json()["header"]["source"] == "dr1-cube"

        app.state.dr1_cache = CacheManager(DiskCache(tmp_path))
        saved = client.get(url)
        assert saved.headers["X-Cache"] == "HIT"
        assert saved.headers["Cache-Control"] == "no-store"
        assert saved.json() == response.json()

        live_available = True
        response = client.get(url, headers={"If-None-Match": response.headers["ETag"]})
        assert response.status_code == 200
        assert response.json()["header"]["source"] == "irsa"
        assert calls == ["1", "all", "cube", "all", "all"]


def test_slow_live_request_can_use_dr1_before_the_live_deadline(monkeypatch):
    monkeypatch.setattr(live_router, "LIVE_BROWSE_HEAD_START", 0)

    async def run():
        started, stopped = asyncio.Event(), asyncio.Event()

        async def live():
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()

        async def fallback():
            assert started.is_set()
            return "cube"

        assert await live_router.prefer_live_cutout(live, fallback) == "cube"
        assert stopped.is_set()

        async def delayed_live():
            await asyncio.sleep(0.01)
            return "live"

        assert await live_router.prefer_live_cutout(
            delayed_live, fallback, wait_for_live=True,
        ) == "live"

    asyncio.run(asyncio.wait_for(run(), 1))
