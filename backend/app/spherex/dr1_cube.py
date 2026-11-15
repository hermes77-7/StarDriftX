"""Read published DR1 FITS tiles directly from IRSA's public AWS mirror."""

import asyncio
import json
from collections import OrderedDict
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
from astropy import units as u
from astropy.io import fits
from astropy.wcs import WCS, WcsError
from astropy_healpix import HEALPix
from numpy.typing import NDArray

from app.shared_work import SharedWork
from app.spherex.coadd import BandSelection, NoCoverageError, output_grid
from app.spherex.cutouts import BAD_PIXEL_FLAGS, FITS_BLOCK_SIZE, image_header
from app.spherex.epochs import DR1
from app.spherex.irsa import MIRROR, ArchiveError, ArchiveTimeout, IrsaClient, SkyRegion

CUBE_COLLECTION = "spherex_dr1_cube"
CUBE_SOURCE = "dr1-cube"
CUBE_REVISION = "hips_20261026"
CUBE_ROOT = f"{MIRROR}/dr1/hips5_cube/{CUBE_REVISION}/Norder5"
CUBE_CACHE_RELEASE = f"spherex-dr1-cube-v1-{CUBE_REVISION}"
CUBE_PIXEL_SCALE_DEG = 12.9 / 3600
TILE_SIZE = 512
BAND_COUNT = 102
BLANK_BANDS = {51, 52}
HEALPIX = HEALPix(nside=32, order="nested")
FITS_ERRORS = (OSError, KeyError, IndexError, TypeError, ValueError, WcsError)
_WAVELENGTHS = json.loads(
    Path(__file__).with_name("dr1_wavelengths.json").read_text(encoding="utf-8"),
)
DR1_BANDS = _WAVELENGTHS["bands"]
DR1_SPECTRAL_CALIBRATION = _WAVELENGTHS["calibration"]


def tile_url(tile: int) -> str:
    return f"{CUBE_ROOT}/Dir{tile // 10000 * 10000}/Npix{tile}_cube.fits"


def padded(size: int) -> int:
    return (size + FITS_BLOCK_SIZE - 1) // FITS_BLOCK_SIZE * FITS_BLOCK_SIZE


@dataclass(frozen=True)
class Plane:
    header: fits.Header
    offset: int
    dtype: str

    @property
    def stride(self) -> int:
        return TILE_SIZE**2 * np.dtype(self.dtype).itemsize

    def sample(self, raw: bytes, count: int, y: NDArray, x: NDArray) -> NDArray:
        data = np.frombuffer(raw, dtype=self.dtype).reshape(count, -1, TILE_SIZE)[:, y, x]
        scale, zero = self.header.get("BSCALE", 1), self.header.get("BZERO", 0)
        if scale != 1 or zero != 0:
            data = data.astype(np.float64) * scale + zero
        return data


def field_grid(region: SkyRegion):
    grid = output_grid(region, CUBE_PIXEL_SCALE_DEG)
    y, x = np.indices(grid.array_shape)
    ra, dec = grid.pixel_to_world_values(x.ravel(), y.ravel())
    tiles = HEALPIX.lonlat_to_healpix(ra * u.deg, dec * u.deg)
    return grid, ra, dec, tiles


def tile_pixels(header: fits.Header, ra: NDArray, dec: NDArray):
    spatial = header.copy()
    spatial["CTYPE3"] = "LINEAR"  # The spatial axes are independent of the wavelength table.
    wcs = WCS(spatial, naxis=[1, 2])
    x, y = wcs.world_to_pixel_values(ra, dec)
    inside = (
        (x >= -0.5) & (x <= TILE_SIZE - 0.5) & (y >= -0.5) & (y <= TILE_SIZE - 0.5)
    )
    x = np.clip(np.floor(x[inside] + 0.5).astype(int), 0, TILE_SIZE - 1)
    y = np.clip(np.floor(y[inside] + 0.5).astype(int), 0, TILE_SIZE - 1)
    return inside, y, x


def sample_tile(layout, raw, indices, count, y, x, mask_flags):
    image, hits, quality = layout
    brightness = image.sample(raw[0], count, y, x)[indices]
    weights = hits.sample(raw[1], count, y, x)[indices]
    flags = quality.sample(raw[2], count, y, x)[indices].astype(np.uint32)
    valid = np.isfinite(brightness) & (weights > 0)
    combined_flags = np.bitwise_or.reduce(np.where(valid, flags, 0), axis=0)
    if mask_flags:
        valid &= (flags & BAD_PIXEL_FLAGS) == 0
    weights = np.where(valid, weights, 0)
    total = weights.sum(axis=0)
    pixels = np.full(total.shape, np.nan, dtype=np.float32)
    np.divide(
        (np.where(valid, brightness, 0) * weights).sum(axis=0), total,
        out=pixels, where=total > 0,
    )
    return pixels, combined_flags


class Dr1Cube:
    def __init__(self, archive: IrsaClient):
        self.archive = archive
        self._layouts: OrderedDict[int, tuple[Plane, ...]] = OrderedDict()
        self._layout_work = SharedWork[tuple[Plane, ...]]()
        self._jobs = asyncio.Semaphore(2)

    async def _header(self, url: str, offset: int, blocks: int) -> tuple[fits.Header, int]:
        raw = await self.archive.archive_range(url, offset, blocks * FITS_BLOCK_SIZE)
        for _ in range(blocks, 17):
            if any(raw[i:i + 8] == b"END     " for i in range(0, len(raw), 80)):
                buffer = BytesIO(raw)
                header = fits.Header.fromfile(buffer)
                return header, offset + buffer.tell()
            raw += await self.archive.archive_range(url, offset + len(raw), FITS_BLOCK_SIZE)
        raise ValueError("DR1 cube header exceeded its size limit.")

    async def _layout(self, tile: int) -> tuple[Plane, ...]:
        if (cached := self._layouts.get(tile)) is not None:
            self._layouts.move_to_end(tile)
            return cached

        async def load():
            url = tile_url(tile)
            prefix = await self.archive.archive_range(url, 0, 3 * FITS_BLOCK_SIZE)
            header, offset = image_header(prefix)
            planes = []
            for name, bitpix, dtype, blocks in (
                ("IMAGE", -32, ">f4", 0), ("NHITS", 32, ">i4", 2), ("FLAGS", 32, ">i4", 4),
            ):
                if blocks:
                    header, offset = await self._header(url, offset, blocks)
                if (
                    header.get("EXTNAME") != name or header["BITPIX"] != bitpix
                    or header["NAXIS"] != 3 or header["NAXIS3"] != BAND_COUNT
                    or (header["NAXIS1"], header["NAXIS2"]) != (TILE_SIZE, TILE_SIZE)
                    or header.get("PCOUNT") != 0 or header["NPIX"] != tile
                ):
                    raise ValueError(f"Unexpected DR1 {name} cube layout.")
                plane = Plane(header, offset, dtype)
                planes.append(plane)
                offset += padded(plane.stride * BAND_COUNT)
            layout = tuple(planes)
            self._layouts[tile] = layout
            if len(self._layouts) > 128:
                self._layouts.popitem(last=False)
            return layout

        return await self._layout_work.run(tile, load)

    async def _science_bytes(
        self, url: str, layout: tuple[Plane, ...], first: int, count: int,
        row_start: int, row_stop: int,
    ) -> list[bytes]:
        # A narrow stack needs a slice of each band. A wide stack uses one
        # contiguous range per HDU to avoid dozens of almost-full requests.
        full_planes = row_start == 0 and row_stop == TILE_SIZE
        batches = []
        try:
            async with asyncio.TaskGroup() as group:
                for plane in layout:
                    row_bytes = plane.stride // TILE_SIZE
                    start = plane.offset + first * plane.stride + row_start * row_bytes
                    if full_planes:
                        spans = [(start, count * plane.stride)]
                    else:
                        spans = [
                            (start + band * plane.stride, (row_stop - row_start) * row_bytes)
                            for band in range(count)
                        ]
                    batches.append([
                        group.create_task(self.archive.archive_range(url, offset, length))
                        for offset, length in spans
                    ])
        except* ArchiveError as errors:
            raise errors.exceptions[0] from None
        return [b"".join(task.result() for task in batch) for batch in batches]

    async def make_cutout(self, region: SkyRegion, selection: BandSelection, mask_flags: bool):
        first_band = (selection.detector - 1) * 17 + (selection.channel or 1)
        bands = [first_band] if selection.channel else range(first_band, first_band + 17)
        bands = [band for band in bands if band not in BLANK_BANDS]
        if not bands:
            raise NoCoverageError("This band is blank in the SPHEREx DR1 All-Sky Cube.")
        try:
            async with asyncio.timeout(35), self._jobs:
                return await self._make_cutout(region, selection, mask_flags, bands)
        except TimeoutError as error:
            raise ArchiveTimeout("The DR1 cube cutout exceeded its time budget.") from error
        except ArchiveTimeout as error:
            raise ArchiveTimeout(f"DR1 AWS mirror timed out: {error}") from error
        except ArchiveError as error:
            raise ArchiveError(f"DR1 AWS mirror failed: {error}") from error
        except NoCoverageError:
            raise
        except FITS_ERRORS as error:
            raise ArchiveError("Could not read the published DR1 cube data.") from error

    async def _make_cutout(
        self, region: SkyRegion, selection: BandSelection, mask_flags: bool, bands: list[int],
    ):
        grid, ra, dec, tiles = await asyncio.to_thread(field_grid, region)
        tile_ids = [int(tile) for tile in np.unique(tiles)]
        wavelengths, lows, highs = zip(*(DR1_BANDS[band - 1] for band in bands), strict=True)
        first, last = bands[0] - 1, bands[-1] - 1
        count = last - first + 1
        indices = np.array(bands) - 1 - first
        pixels = np.full(tiles.shape, np.nan, dtype=np.float32)
        flags = np.zeros(tiles.shape, dtype=np.uint32)

        async def project(tile: int):
            layout = await self._layout(tile)
            url = tile_url(tile)
            target = np.flatnonzero(tiles == tile)
            inside, y, x = await asyncio.to_thread(
                tile_pixels, layout[0].header, ra[target], dec[target],
            )
            if not y.size:
                return
            row_start, row_stop = int(y.min()), int(y.max()) + 1
            if count > 1 and row_stop - row_start > TILE_SIZE // 4:
                row_start, row_stop = 0, TILE_SIZE
            raw = await self._science_bytes(url, layout, first, count, row_start, row_stop)
            values, quality = await asyncio.to_thread(
                sample_tile, layout, raw, indices, count, y - row_start, x, mask_flags,
            )
            pixels[target[inside]] = values
            flags[target[inside]] = quality

        # Limit the memory needed for detector stacks while retaining parallel single bands.
        results = []
        if selection.channel is None:
            for tile in tile_ids:
                await project(tile)
        else:
            results = await asyncio.gather(
                *(project(tile) for tile in tile_ids), return_exceptions=True,
            )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        covered = np.isfinite(pixels)
        if not covered.any():
            raise NoCoverageError("No usable DR1 cube pixels cover this selection.")
        shape = grid.array_shape
        header = {
            "version": "1.0", "source": CUBE_SOURCE, "data_release": DR1,
            "collections": [CUBE_COLLECTION], "bunit": "MJy/sr", "epoch": "all",
            "wcs": {
                "crval": grid.wcs.crval.tolist(), "crpix": grid.wcs.crpix.tolist(),
                "cdelt": grid.wcs.cdelt.tolist(), "ctype": list(grid.wcs.ctype),
                "radesys": grid.wcs.radesys, "shape": list(shape),
            },
            "detector": selection.detector, "wavelength_um": float(np.mean(wavelengths)),
            "wavelength_range_um": [min(lows), max(highs)],
            "mask_flags": mask_flags, "photometric_normalized": False,
            "coverage_fraction": float(covered.mean()),
            "flagged_fraction": float(np.count_nonzero(flags) / flags.size),
        }
        if selection.channel is not None:
            header.update(band=bands[0], channel=selection.channel)
        return header, pixels.reshape(shape), flags.reshape(shape)
