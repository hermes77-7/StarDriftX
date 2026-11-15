import asyncio
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from astropy.io import fits
from astropy.io.votable import from_table, writeto
from astropy.io.votable.tree import Info
from astropy.table import Table

from app.spherex.coadd import output_grid
from app.spherex.cutouts import detector_bounds, read_cutout
from app.spherex.epochs import DR1, QR3, TimeWindow
from app.spherex.irsa import (
    ArchiveError,
    ArchiveLimits,
    ArchiveTimeout,
    Exposure,
    IrsaClient,
    SkyRegion,
)
from app.spherex.search_cache import ExposureSearchCache

REGION = SkyRegion(150.12, 2.34, 0.06)
IMAGE = "level2_2026W22_1A_0214_2D1_spx_l2b-v27-2026-269.fits"
URL = f"https://irsa.ipac.caltech.edu/ibe/data/spherex/qr3/level2/test/1/{IMAGE}"
EXPOSURE = Exposure("2026W22_1A_0214_2", 1, QR3, URL, 61186.361248, 61186.36258,
                    (0.7455, 1.1167), "l2b-v27-2026-269")


def page(*times: float, status: str = "OK", collection: str = QR3) -> bytes:
    table = Table(
        rows=[
            (f"obs-{mjd}", collection, URL, mjd, mjd + 0.1, 0.7455e-6, 1.1167e-6)
            for mjd in times
        ],
        names=("obs_id", "obs_collection", "access_url", "t_min", "t_max", "em_min", "em_max"),
        dtype=("U64", "U64", "U256", "f8", "f8", "f8", "f8"),
    )
    votable = from_table(table)
    votable.resources[0].infos.append(Info(name="QUERY_STATUS", value=status))
    dest = BytesIO()
    writeto(votable, dest)
    return dest.getvalue()


def test_search_pages_are_merged_and_overlaps_deduplicated():
    def respond(request):
        query = request.url.params
        assert query["COLLECTION"] == QR3
        assert query["POS"].startswith("CIRCLE 150.12 2.34 ")
        assert query["MAXREC"] == "3"
        pages = {
            "61186 61188": page(status="OVERFLOW"),
            "61186 61187.0": page(61186.2, 61186.95),
            "61187.0 61188": page(61186.95, 61187.2),
        }
        return httpx.Response(200, content=pages[query["TIME"]])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = IrsaClient(http, ArchiveLimits(page_size=3))
            result = await client.search(
                REGION, window=TimeWindow(61186, 61188), collections=(QR3,)
            )
            assert not result.no_coverage
            assert [item.mjd_start for item in result.exposures] == [61186.2, 61186.95, 61187.2]
            assert result.exposures[0].wavelength_um == pytest.approx((0.7455, 1.1167))

    asyncio.run(run())


@pytest.mark.parametrize("gateway_status", [502, 504])
def test_wide_search_recovers_with_parallel_date_ranges(gateway_status):
    async def run():
        second_range_started = asyncio.Event()

        async def respond(request):
            query = request.url.params
            assert query["COLLECTION"] == QR3
            assert query["BAND"] == "9e-7"
            assert query["POS"].startswith("CIRCLE 266.4168 -29.0078 0.70")
            if query["TIME"] == "61186 61188":
                return httpx.Response(gateway_status)
            if query["TIME"] == "61186 61187.0":
                await asyncio.wait_for(second_range_started.wait(), timeout=1)
                return httpx.Response(200, content=page(61186.2, 61186.95))
            assert query["TIME"] == "61187.0 61188"
            second_range_started.set()
            return httpx.Response(200, content=page(61186.95, 61187.2))

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            result = await IrsaClient(http).search(
                SkyRegion(266.4168, -29.0078, 1), window=TimeWindow(61186, 61188),
                collections=(QR3,), detector=1,
            )
        assert [item.mjd_start for item in result.exposures] == [61186.2, 61186.95, 61187.2]
        assert not result.warnings

    asyncio.run(run())


def test_search_keeps_available_rows_when_a_date_range_times_out():
    def respond(request):
        pages = {
            "61186 61188": httpx.Response(200, content=page(61186.1, status="OVERFLOW")),
            "61186 61187.0": httpx.Response(200, content=page(61186.2)),
            "61187.0 61188": httpx.Response(504),
        }
        return pages[request.url.params["TIME"]]

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            result = await IrsaClient(http, ArchiveLimits(attempts=1)).search(
                REGION, window=TimeWindow(61186, 61188), collections=(QR3,),
            )
        assert [item.mjd_start for item in result.exposures] == [61186.1, 61186.2]
        assert result.warnings == ("IRSA returned HTTP 504.",)

    asyncio.run(run())


def test_no_coverage_and_upstream_errors_are_distinct():
    results = iter((httpx.Response(200, content=page()), httpx.Response(503)))

    async def run():
        transport = httpx.MockTransport(lambda _: next(results))
        async with httpx.AsyncClient(transport=transport) as http:
            client = IrsaClient(http, ArchiveLimits(attempts=1))
            assert (await client.search(REGION, collections=(QR3,))).no_coverage
            with pytest.raises(ArchiveError, match="503"):
                await client.search(REGION, collections=(QR3,))

    asyncio.run(run())


def test_search_recovers_indexed_exposures_after_restart(tmp_path):
    async def run():
        path = tmp_path / "exposure-searches.sqlite3"
        options = dict(epoch="4", detector=1, collections=(QR3,))
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(200, content=page(61186.2)),
        )) as http:
            original = await IrsaClient(http, search_cache=ExposureSearchCache(path)).search(
                REGION, release="published", **options,
            )
        async with httpx.AsyncClient(transport=httpx.MockTransport(
            lambda _: httpx.Response(503),
        )) as http:
            client = IrsaClient(http, ArchiveLimits(attempts=1),
                                search_cache=ExposureSearchCache(path))
            saved = await client.search(REGION, release="bootstrap", **options)
            assert saved.exposures == original.exposures
            assert "Previously indexed exposure results were used." in saved.warnings
            with pytest.raises(ArchiveError, match="503"):
                await client.search(
                    REGION, release="bootstrap", window=TimeWindow(61186, 61187), **options,
                )

    asyncio.run(run())


def test_search_total_deadline_applies_to_retry_after():
    async def run():
        transport = httpx.MockTransport(
            lambda _: httpx.Response(429, headers={"Retry-After": "3600"})
        )
        async with httpx.AsyncClient(transport=transport) as http:
            client = IrsaClient(http, ArchiveLimits(search_seconds=0.1))
            with pytest.raises(ArchiveTimeout, match="time budget"):
                await client.search(REGION, collections=(QR3,))

    asyncio.run(run())


def test_sia_has_a_separate_read_budget_and_reuses_complete_release_searches():
    calls = []

    async def respond(request):
        assert request.extensions["timeout"]["read"] == 26
        collection = request.url.params["COLLECTION"]
        calls.append(collection)
        return httpx.Response(200, content=page(61186.2, collection=collection))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = IrsaClient(http)
            combined, epoch = await asyncio.gather(
                client.search(REGION, detector=1, release="release-a"),
                client.search(REGION, detector=1, epoch="2", release="release-a"),
            )
            assert len(combined.exposures) == 2
            assert [row.collection for row in epoch.exposures] == [DR1]
            assert sorted(calls) == sorted((DR1, QR3))
            await client.search(REGION, detector=1, epoch="2", release="release-b")
            assert calls.count(DR1) == 2

    asyncio.run(run())


def test_malformed_votable_is_an_archive_error():
    async def run():
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=b"<broken"))
        async with httpx.AsyncClient(transport=transport) as http:
            with pytest.raises(ArchiveError, match="unreadable exposure table"):
                await IrsaClient(http).search(REGION, collections=(QR3,))

    asyncio.run(run())


def test_real_fits_reader_and_partial_results():
    raw_fits = Path(__file__).resolve().parents[2] / "shared/fixtures/spherex" / IMAGE
    attempts = 0

    async def respond(request):
        nonlocal attempts
        attempts += 1
        assert request.url.params["center"] == "150.12,2.34"
        assert 0.06 < float(request.url.params["size"]) < 0.12
        return httpx.Response(503 if attempts == 1 else 200, content=raw_fits.read_bytes())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            client = IrsaClient(http)
            results = [
                row async for row in client.cutouts(
                    [EXPOSURE, replace(EXPOSURE, access_url="https://example.org/no.fits")],
                    REGION, epoch="4",
                )
            ]
            assert attempts == 2
            cutout = next(row.cutout for row in results if row.cutout is not None)
            assert cutout is not None
            assert cutout.wcs.has_celestial
            assert cutout.mjd == pytest.approx(61186.361248021)
            assert cutout.pointing_angle_deg == 180
            assert cutout.flags.dtype.name == "uint32"
            assert cutout.flag_names[28] == "HALO"
            failure = next(row for row in results if row.cutout is None)
            assert failure.warning
            second = [row async for row in client.cutouts([EXPOSURE], REGION)]
            assert second[0].cutout is cutout
            assert attempts == 2

    asyncio.run(run())


def test_detector_query_and_partial_release_search():
    def respond(request):
        assert request.url.params["BAND"] == "9e-7"
        if request.url.params["COLLECTION"] == DR1:
            return httpx.Response(504)
        return httpx.Response(200, content=page(61186))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            result = await IrsaClient(http, ArchiveLimits(attempts=1)).search(REGION, detector=1)
            assert len(result.exposures) == 1
            assert result.warnings == ("IRSA returned HTTP 504.",)

    asyncio.run(run())


def test_successful_cutout_is_yielded_even_if_peer_times_out(monkeypatch):
    async def download(exposure, region, *, grid=None):
        if exposure.observation_id == "slow":
            await asyncio.sleep(1)
        pixels = np.zeros((1, 1))
        return SimpleNamespace(brightness=pixels, variance=pixels, flags=pixels)

    async def run():
        async with httpx.AsyncClient() as http:
            client = IrsaClient(http, ArchiveLimits(download_seconds=0.05))
            monkeypatch.setattr(client, "download", download)
            fast = EXPOSURE
            slow = replace(fast, observation_id="slow")
            seen = []
            with pytest.raises(ArchiveTimeout, match="batch"):
                async for result in client.cutouts([slow, fast], REGION):
                    seen.append(result.exposure.id)
            assert seen == [fast.id]

    asyncio.run(run())


def test_mirror_header_maps_the_grid_without_reading_image_data():
    raw_fits = Path(__file__).resolve().parents[2] / "shared/fixtures/spherex" / IMAGE
    with fits.open(raw_fits) as hdus:
        offset = hdus["IMAGE"].fileinfo()["datLoc"]
    mjd, (x0, x1, y0, y1) = detector_bounds(
        raw_fits.read_bytes()[:offset], output_grid(REGION), (36, 36),
    )
    assert mjd == pytest.approx(61186.361248021)
    assert x0 < 66 + 35 < x1
    assert y0 < 1535 + 35 < y1


def test_mirror_reads_science_planes_and_compressed_flags_with_exact_wcs():
    raw_fits = Path(__file__).resolve().parents[2] / "shared/fixtures/spherex" / IMAGE
    with fits.open(raw_fits) as hdus:
        planes = [fits.PrimaryHDU()]
        for name in ("IMAGE", "FLAGS", "VARIANCE", "ZODI"):
            original = hdus[name]
            pixels = np.pad(original.data, ((45, 70), (45, 70)), mode="edge")
            header = original.header.copy()
            for axis in (1, 2):
                for suffix in ("", "A", "W"):
                    key = f"CRPIX{axis}{suffix}"
                    if key in header:
                        header[key] += 45
            if name == "FLAGS":
                planes.append(fits.CompImageHDU(pixels.astype("int32"), header, name=name))
            else:
                planes.append(fits.ImageHDU(pixels, header, name=name))
        payload = BytesIO()
        fits.HDUList(planes).writeto(payload)
    content = payload.getvalue()
    full = read_cutout(BytesIO(content), exposure_id=EXPOSURE.id, collection=QR3)
    grid = output_grid(REGION)

    def respond(request):
        assert request.url.host == "nasa-irsa-spherex.s3.us-east-1.amazonaws.com"
        start, end = map(int, request.headers["Range"].removeprefix("bytes=").split("-"))
        return httpx.Response(206, headers={"Content-Range": f"bytes {start}-{end}/{len(content)}"},
                              content=content[start:end + 1])

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            result = await IrsaClient(http).download(EXPOSURE, REGION, grid=grid)
        x, y = result.x_start - full.x_start, result.y_start - full.y_start
        height, width = result.flags.shape
        assert 0 < height < full.flags.shape[0]
        assert 0 < width < full.flags.shape[1]
        roi = np.s_[y:y + height, x:x + width]
        np.testing.assert_array_equal(result.flags, full.flags[roi])
        np.testing.assert_allclose(result.brightness, full.brightness[roi])
        np.testing.assert_allclose(result.variance, full.variance[roi])
        np.testing.assert_allclose(
            result.wcs.pixel_to_world_values(3, 7), full.wcs.pixel_to_world_values(x + 3, y + 7),
            atol=1e-10, rtol=0,
        )

    asyncio.run(run())
