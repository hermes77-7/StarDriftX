
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.wcs import WCS

from app.spherex.coadd import BandSelection, NoCoverageError, coadd, output_grid
from app.spherex.cutouts import PIXEL_SCALE_DEG, Cutout, DetectorCalibration
from app.spherex.epochs import DR1, QR3, SURVEYS, Survey, TimeWindow
from app.spherex.irsa import ArchiveTimeout, CutoutResult, SearchResult, SkyRegion
from app.spherex.service import CutoutService, cutout_header

REGION = SkyRegion(150.12, 2.34, 10 * PIXEL_SCALE_DEG)


def exposure(identifier: str, wcs: WCS, image: np.ndarray, angle: float = 180) -> Cutout:
    return Cutout(
        identifier, 1, QR3, "v27", 61186, angle, wcs, 0, 0, image,
        np.full(image.shape, 4.0), np.zeros(image.shape, dtype=np.uint32),
        {21: "SOURCE", 28: "HALO"},
    )


def calibration(channel_map: np.ndarray) -> DetectorCalibration:
    return DetectorCalibration(
        1, channel_map, np.full(channel_map.shape, 0.809), np.full(channel_map.shape, 0.021),
        {channel: 0.75 + 0.015 * channel for channel in range(1, 18)},
    )


def test_cutout_uses_available_release_when_other_calibration_times_out(monkeypatch):
    grid = output_grid(REGION)
    cutout = exposure("available", grid, np.full(grid.array_shape, 10.0))
    available = SimpleNamespace(collection=QR3)
    unavailable = SimpleNamespace(collection=DR1)

    async def search(*args, **kwargs):
        return SearchResult((unavailable, available))

    async def cutouts(selected, *args, **kwargs):
        assert selected == [available]
        yield CutoutResult(available, cutout)

    async def load_calibration(collection, versions, detector):
        if collection == DR1:
            raise ArchiveTimeout("DR1 calibration timed out.")
        return calibration(np.full(grid.array_shape, 4))

    service = CutoutService(SimpleNamespace(search=search, cutouts=cutouts, surveys=SURVEYS))
    monkeypatch.setattr(service, "calibration", load_calibration)
    product, failures = asyncio.run(service.make_cutout(
        service.release, REGION, BandSelection(1), "all", True,
    ))
    assert np.nanmax(product.brightness) == pytest.approx(10.0)
    assert failures == ["DR1 calibration timed out."]
    header = cutout_header(product, service.release, failures)
    assert header["collections"] == [QR3]
    assert header["warnings"]


@pytest.mark.parametrize(
    ("band", "detector", "channel"),
    [(1, 1, 1), (17, 1, 17), (18, 2, 1), (51, 3, 17), (52, 4, 1), (102, 6, 17)],
)
def test_global_band_boundaries(band, detector, channel):
    assert BandSelection.from_band(band) == BandSelection(detector, channel)


def test_coadd_masks_quality_bits_and_selects_channels_and_epochs():
    grid = output_grid(REGION)
    shape = grid.array_shape
    channels = np.full(shape, 4)
    channels[:, :4] = 5
    channels[:, 0] = 0
    channels[:, -1] = 18
    detector = calibration(channels)
    first = exposure("a", grid, np.full(shape, 10.0))
    first.flags[5, 5] = 1 << 28
    first.flags[6, 5] = 1 << 21
    second = exposure("b", grid, np.full(shape, 20.0), 0)

    single = coadd(REGION, [first, second], detector, BandSelection(1, 4), epoch="4")
    assert [row.exposure_id for row in single.contributions] == ["a"]
    assert single.nominal_wavelength_um == detector.nominal_wavelength_um[4]
    assert np.isnan(single.brightness[5, 5])
    assert single.flags[5, 5] == 1 << 28
    assert single.brightness[6, 5] == 10
    assert single.flags[6, 5] == 1 << 21
    assert np.isnan(single.brightness[5, 2])

    stack = coadd(REGION, [first, second, second], detector, BandSelection(1), mask_flags=False)
    assert stack.nominal_wavelength_um is None
    assert stack.brightness[5, 2] == pytest.approx(15)
    assert stack.brightness[5, 5] == pytest.approx(15)
    assert stack.uncertainty[5, 5] == pytest.approx(2 / np.sqrt(2))
    assert stack.hits[5, 5] == 2
    assert not stack.hits[:, [0, -1]].any()
    assert [row.collection for row in stack.contributions] == [QR3, QR3]

    with pytest.raises(NoCoverageError):
        coadd(REGION, [first], detector, BandSelection(1, 17))
    surveys = SURVEYS | {"4": Survey(QR3, 180, TimeWindow(61190, 61200))}
    with pytest.raises(NoCoverageError):
        coadd(REGION, [first], detector, BandSelection(1, 4), epoch="4", surveys=surveys)


def test_detector_stack_improves_signal_to_noise_for_equal_signal_in_two_channels():
    grid = output_grid(REGION)
    shape = grid.array_shape
    channel_map = np.full((shape[0] * 2, shape[1]), 4)
    channel_map[shape[0]:] = 5
    detector = calibration(channel_map)
    detector = replace(detector, wavelength_um=np.where(channel_map == 4, 0.81, 0.83))
    signal = np.full(shape, 10.0)
    first = exposure("band-4", grid, signal)
    second = replace(exposure("band-5", grid, signal), y_start=shape[0])
    single = coadd(REGION, [first, second], detector, BandSelection.from_band(4))
    changed = coadd(REGION, [first, second], detector, BandSelection.from_band(5))
    combined = coadd(REGION, [first, second], detector, BandSelection(1))
    assert changed.nominal_wavelength_um == detector.nominal_wavelength_um[5]
    assert changed.nominal_wavelength_um != single.nominal_wavelength_um
    assert len(combined.contributions) == 2
    assert [row.wavelength_range_um for row in combined.contributions] == [
        (0.81, 0.81), (0.83, 0.83),
    ]
    pixel = (5, 5)
    assert combined.brightness[pixel] == pytest.approx(single.brightness[pixel])
    assert single.uncertainty[pixel] / combined.uncertainty[pixel] == pytest.approx(np.sqrt(2))


def test_shifted_star_alignment_and_integrated_flux():
    grid = output_grid(REGION)
    shape = grid.array_shape
    detector = calibration(np.full(shape, 4))
    star = np.zeros(shape)
    star[5, 5] = 10
    shifted_grid = grid.deepcopy()
    shifted_grid.wcs.crpix += [1, -1]
    shifted_star = np.roll(star, (-1, 1), axis=(0, 1))
    first = exposure("a", grid, star)
    second = exposure("b", shifted_grid, shifted_star)

    for inputs in ([first], [second], [first, second]):
        result = coadd(REGION, inputs, detector, BandSelection(1, 4))
        image = np.nan_to_num(result.brightness)
        integrated = image.sum()
        y, x = np.indices(shape)
        np.testing.assert_allclose(
            [(image * x).sum() / integrated, (image * y).sum() / integrated],
            [5, 5], rtol=0, atol=0.3,
        )
        assert integrated == pytest.approx(10, rel=0.01)


def test_subpixel_reprojection_preserves_star_centroid_and_flux():
    region = SkyRegion(150.12, 2.34, 24 * PIXEL_SCALE_DEG)
    grid = output_grid(region)
    shape = grid.array_shape
    detector = calibration(np.full(shape, 4))
    y, x = np.indices(shape)
    reference = np.exp(-((x - 12)**2 + (y - 12)**2) / (2 * 1.5**2))
    shift_x, shift_y = 0.45, -0.37
    shifted_grid = grid.deepcopy()
    shifted_grid.wcs.crpix += [shift_x, shift_y]
    shifted = np.exp(-((x - 12 - shift_x)**2 + (y - 12 - shift_y)**2) / (2 * 1.5**2))
    product = coadd(
        region, [exposure("b", shifted_grid, shifted)], detector, BandSelection(1, 4)
    )
    image = np.nan_to_num(product.brightness)
    flux = image.sum()
    assert flux == pytest.approx(reference.sum(), rel=0.01)
    np.testing.assert_allclose(
        [(image * x).sum() / flux, (image * y).sum() / flux], [12, 12], rtol=0, atol=0.3,
    )
