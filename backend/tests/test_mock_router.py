import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import yaml
from fastapi.openapi.utils import get_openapi
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import live_router
from app.cache.manager import CacheManager
from app.cache.memory import MemoryCache
from app.cache.s3 import S3Cache
from app.main import app
from app.shared_work import SharedWork
from app.spherex.coadd import Coadd, Contribution, NoCoverageError, output_grid
from app.spherex.epochs import configure_surveys
from app.spherex.irsa import ArchiveTimeout
from app.spherex.payload import decode_payload
from app.spherex.releases import BOOTSTRAP_RELEASE


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STARDRIFT_RELEASE_CHECK_SECONDS", "0")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path))
    manager = CacheManager(backend=MemoryCache())
    monkeypatch.setattr(live_router, "default_cache_manager", manager)
    with TestClient(app) as test_client:
        yield test_client


@pytest.mark.parametrize(
    ("path", "parameter", "value"),
    [
        ("cutout", "mode", "invalid"),
        ("cutout", "format", "invalid"),
        ("catalog", "catalog", "invalid"),
        ("cutout", "ra", "nan"),
        ("cutout", "dec", "inf"),
        ("spectrum", "aperture", "nan"),
        ("cutout", "fov", "1.1"),
        ("cutout", "epoch", "9"),
    ],
)
def test_invalid_query_returns_validation_error(client, path, parameter, value):
    params = {"ra": "150.12", "dec": "2.34"}
    params[parameter] = value

    response = client.get(f"/api/{path}", params=params)

    assert response.status_code == 400
    assert response.json()["error"]["details"]["field"] == parameter


def test_openapi_describes_the_actual_routes_and_validation(client):
    spec = client.get("/openapi.json").json()
    assert spec["servers"] == [{"description": "Star Drift API base path", "url": "/api"}]
    paths = {"/api" + path for path in spec["paths"]}
    assert "/api/cutout" in paths
    runtime = get_openapi(title=app.title, version=app.version, routes=app.routes)
    assert paths == set(runtime["paths"])
    yaml_path = Path(__file__).resolve().parents[2] / "shared/openapi.yaml"
    assert spec == yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    for response in ("200", "304", "400"):
        assert response in spec["paths"]["/cutout"]["get"]["responses"]


@pytest.fixture
def archive(client, monkeypatch):
    requests = []

    async def make_cutout(release, region, selection, epoch, mask):
        requests.append((region, release.namespace))
        if region.ra == 5:
            raise NoCoverageError("No exposures cover the request.")
        if region.ra == 6:
            raise ArchiveTimeout("The archive timed out.")
        grid = output_grid(region)
        shape = grid.array_shape
        brightness = np.ones(shape, dtype=np.float32)
        brightness[0, 0] = np.nan
        return Coadd(
            grid, brightness, np.zeros(shape), np.isfinite(brightness),
            np.zeros(shape, dtype=np.uint32),
            (Contribution("sample", "spherex_dr1", "v27", 60900, (0.8, 0.82)),),
            0.809, {}, selection, epoch, int(mask),
        ), []

    monkeypatch.setattr(client.app.state.cutouts, "make_cutout", make_cutout)

    async def cube_cutout(*_):
        raise ArchiveTimeout("The cube mirror timed out.")

    monkeypatch.setattr(client.app.state.cutouts.dr1_cube, "make_cutout", cube_cutout)
    return requests


def test_live_cutout_cache_respects_wcs_and_release(client, archive):
    url = "/api/cutout?ra=150.12&dec=2.34&fov=0.06"
    first = client.get(url)
    assert first.status_code == 200
    assert first.headers["x-cache"] == "MISS"
    header, _, _ = decode_payload(first.content)
    assert header["source"] == "irsa"
    assert header["wcs"]["crval"] == [150.12, 2.34]
    warm = client.get(url)
    assert warm.headers["x-cache"] == "HIT"
    assert client.get(url, headers={"If-None-Match": warm.headers["ETag"]}).status_code == 304
    assert len(archive) == 1

    alternate = client.get(url.replace("150.12", "150.12001") + "&format=json")
    assert alternate.headers["x-cache"] == "MISS"
    assert alternate.json()["header"]["wcs"]["crval"] == [150.12001, 2.34]
    assert alternate.json()["data"][0][0] is None

    client.app.state.cutouts.release = replace(BOOTSTRAP_RELEASE, revision="new")
    refreshed = client.get(url, headers={"If-None-Match": warm.headers["ETag"]})
    assert refreshed.status_code == 200
    assert refreshed.headers["x-cache"] == "MISS"
    assert refreshed.headers["x-data-release"] == "spherex-live-v1-new"
    assert client.get("/api/meta").json()["data_release"] == refreshed.headers["x-data-release"]


def test_cancelled_cutout_releases_archive_worker(client):
    async def run():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def make_cutout(*_):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        async def receive():
            await started.wait()
            return {"type": "http.disconnect"}

        state = SimpleNamespace(
            cutouts=SimpleNamespace(make_cutout=make_cutout, release=BOOTSTRAP_RELEASE),
            cutout_work=SharedWork(),
        )
        request = Request({"type": "http", "app": SimpleNamespace(state=state)}, receive)
        result = await asyncio.wait_for(live_router.get_cutout(request, 150.12, 2.34), 2)
        assert result.status_code == 499
        assert cancelled.is_set()
        assert not state.cutout_work._jobs

    asyncio.run(run())


def test_disconnect_does_not_cancel_an_identical_cutout_requested_by_another_client(client):
    async def run():
        started = asyncio.Event()
        leave = asyncio.Event()
        complete = asyncio.Event()
        calls = 0

        async def make_cutout(*_):
            nonlocal calls
            calls += 1
            started.set()
            await complete.wait()
            raise ArchiveTimeout("simulated timeout")

        async def receive():
            await leave.wait()
            return {"type": "http.disconnect"}

        async def stay_connected():
            await asyncio.Future()

        async def cube_cutout(*_):
            raise ArchiveTimeout("simulated cube timeout")

        state = SimpleNamespace(
            cutouts=SimpleNamespace(
                make_cutout=make_cutout, release=BOOTSTRAP_RELEASE,
                dr1_cube=SimpleNamespace(make_cutout=cube_cutout),
            ),
            cutout_work=SharedWork(),
        )
        scope = {"type": "http", "app": SimpleNamespace(state=state)}
        async with asyncio.TaskGroup() as group:
            first = group.create_task(
                live_router.get_cutout(Request(scope.copy(), receive), 82, 14),
            )
            second = group.create_task(
                live_router.get_cutout(Request(scope.copy(), stay_connected), 82, 14),
            )
            await started.wait()
            leave.set()
            assert (await first).status_code == 499
            complete.set()
        assert second.result().status_code == 504
        assert calls == 1

    asyncio.run(asyncio.wait_for(run(), 2))


def test_live_errors_and_cache_access(client, archive, monkeypatch):
    for ra, status, code in [(5, 404, "NO_COVERAGE"), (6, 504, "UPSTREAM_TIMEOUT")]:
        response = client.get(f"/api/cutout?ra={ra}&dec=0")
        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert response.headers["X-Request-ID"]
    assert client.get("/api/cache/stats").status_code == 403
    monkeypatch.setenv("STARDRIFT_CACHE_ADMIN_TOKEN", "test")
    headers = {"Authorization": "Bearer test"}
    assert client.get("/api/cache/stats", headers=headers).status_code == 200
    assert client.post("/api/cache/invalidate", headers=headers).status_code == 200
    monkeypatch.setenv("STARDRIFT_REQUESTS_PER_MINUTE", "1")
    response = client.get("/api/meta")
    assert response.status_code == 429 and int(response.headers["Retry-After"]) > 0


def test_configured_epoch_window_is_reported_and_changes_cutout_cache_key(client, archive):
    url = "/api/cutout?ra=150.12&dec=2.34&epoch=4"
    assert client.get(url).headers["x-cache"] == "MISS"
    assert client.get(url).headers["x-cache"] == "HIT"
    service = client.app.state.cutouts
    service.archive.surveys = configure_surveys('{"4":[61186,61200]}')
    survey = next(epoch for epoch in client.get("/api/meta").json()["epochs"] if epoch["id"] == "4")
    assert survey["time_range"] == {"mjd_start": 61186, "mjd_end": 61200}
    response = client.get(url)
    assert response.headers["x-cache"] == "MISS"
    header, _, _ = decode_payload(response.content)
    assert header["epoch"] == "4"
    assert header["exposure_mjd"] == [60900]
    assert header["exposures"][0]["id"] == "sample"
    assert len(archive) == 2


def test_meta_exposes_all_published_spectral_channels_on_request(client, monkeypatch):
    async def load_channels(*_):
        raise ArchiveTimeout("The calibration mirror timed out.")

    monkeypatch.setattr(client.app.state.cutouts, "spectral_channels", load_channels)
    assert "bands" not in client.get("/api/meta").json()["supported_bands"]
    bands = client.get("/api/meta?include_bands=true&epoch=2").json()["supported_bands"]["bands"]
    assert len(bands) == 102
    assert bands[3]["wavelength_um"] == 0.809
    assert bands[17]["detector"] == 2 and bands[17]["channel"] == 1
    assert bands[-1] == {"band": 102, "detector": 6, "channel": 17, "wavelength_um": 4.988}


def test_s3_invalidation_is_scoped_and_paginated(monkeypatch):
    monkeypatch.setattr(S3Cache, "__init__", lambda self: None)
    cache = S3Cache()
    cache.prefix = "cutouts"
    cache.bucket_name = "test"
    cache.fallback = None
    deleted = []
    digest = "a" * 64

    class Client:
        def get_paginator(self, operation):
            assert operation == "list_objects_v2"
            return self

        def paginate(self, *, Bucket, Prefix):
            assert (Bucket, Prefix) == ("test", "cutouts/")
            for suffix in ("bin", "json"):
                yield {"Contents": [
                    {"Key": f"cutouts/old/hp_64_1/{digest}.{suffix}"},
                    {"Key": f"cutouts/current/hp_64_1/{digest}.{suffix}"},
                    {"Key": f"cutouts/other/hp_64_1/readme.{suffix}"},
                ]}

        def delete_objects(self, *, Bucket, Delete):
            assert Bucket == "test"
            deleted.extend(Delete["Objects"])
            return {"Deleted": Delete["Objects"]}

    cache._client = Client()
    assert cache.invalidate_release("current") == 1
    assert deleted == [
        {"Key": f"cutouts/old/hp_64_1/{digest}.bin"}, {"Key": f"cutouts/old/hp_64_1/{digest}.json"},
    ]
