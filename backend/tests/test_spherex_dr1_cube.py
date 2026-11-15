import asyncio
from types import SimpleNamespace

import pytest
from astropy.io import fits

from app.spherex.dr1_cube import TILE_SIZE, Dr1Cube, Plane
from app.spherex.irsa import ArchiveTimeout


def test_narrow_stack_fetches_only_the_selected_rows_of_each_band():
    plane = Plane(fits.Header(), 8640, ">f4")
    requests = []

    async def read(url, offset, length):
        requests.append((offset, length))
        return b"\0" * length

    cube = Dr1Cube(SimpleNamespace(archive_range=read))
    result = asyncio.run(cube._science_bytes("tile", (plane,), 3, 2, 19, 23))
    row_bytes = TILE_SIZE * 4
    assert requests == [
        (plane.offset + index * plane.stride + 19 * row_bytes, 4 * row_bytes)
        for index in (3, 4)
    ]
    assert len(result[0]) == 8 * row_bytes


def test_failed_science_range_reports_the_upstream_timeout():
    async def read(*_):
        raise ArchiveTimeout("timed out")

    cube = Dr1Cube(SimpleNamespace(archive_range=read))
    plane = Plane(fits.Header(), 8640, ">f4")
    with pytest.raises(ArchiveTimeout, match="timed out"):
        asyncio.run(cube._science_bytes("tile", (plane,), 3, 2, 19, 23))
