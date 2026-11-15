"""Make one single-detector, single-channel QR3 coadd from local IRSA FITS files."""

import argparse
from pathlib import Path

import numpy as np
from astropy.io import fits

from app.spherex.coadd import BandSelection
from app.spherex.coadd import coadd as combine_cutouts
from app.spherex.cutouts import read_calibration, read_cutout
from app.spherex.epochs import QR3
from app.spherex.irsa import SkyRegion


def coadd(args: argparse.Namespace) -> None:
    calibration = read_calibration(args.channels, args.wavelengths, args.detector)
    cutouts = (read_cutout(path, exposure_id=path.name, collection=QR3) for path in args.cutouts)
    product = combine_cutouts(
        SkyRegion(args.ra, args.dec, args.fov), cutouts, calibration,
        BandSelection(args.detector, args.channel),
    )
    result, grid, hits = product.brightness, product.wcs, product.hits
    exposures = [
        (row.exposure_id, row.mjd, *row.wavelength_range_um) for row in product.contributions
    ]
    header = grid.to_header()
    header["BUNIT"] = "MJy/sr"
    header["RELEASE"] = "QR3"
    header["DETECTOR"] = args.detector
    header["SUBCHAN"] = args.channel
    header["WAVECEN"] = (product.nominal_wavelength_um, "Nominal wavelength in microns")
    header["NEXP"] = len(exposures)
    header["MASKBITS"] = product.bad_pixel_mask
    header["HISTORY"] = "IRSA QR3 prototype. IMAGE minus ZODI, overlap-area-weighted mean."
    exposure_table = fits.BinTableHDU.from_columns(
        [
            fits.Column(name="FILE", format="96A", array=[row[0] for row in exposures]),
            fits.Column(name="MJD_OBS", format="D", unit="d", array=[row[1] for row in exposures]),
            fits.Column(
                name="CWAVE_MIN", format="E", unit="um", array=[row[2] for row in exposures]
            ),
            fits.Column(
                name="CWAVE_MAX", format="E", unit="um", array=[row[3] for row in exposures]
            ),
        ],
        name="EXPOSURES",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fits.HDUList(
        [
            fits.PrimaryHDU(result, header),
            fits.ImageHDU(hits, grid.to_header(), name="NHITS"),
            exposure_table,
        ]
    ).writeto(args.output, overwrite=True, checksum=True)
    print(
        f"{args.output}: {len(exposures)} exposures, "
        f"{np.count_nonzero(hits)}/{hits.size} covered pixels, "
        f"{np.count_nonzero(hits > 1)} pixels with multiple hits"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ra", type=float, required=True)
    parser.add_argument("--dec", type=float, required=True)
    parser.add_argument("--fov", type=float, default=0.06, help="Output width in degrees")
    parser.add_argument("--detector", type=int, choices=range(1, 7), default=1)
    parser.add_argument("--channel", type=int, choices=range(1, 18), required=True)
    parser.add_argument("--channels", type=Path, required=True, help="QR3 channel-map FITS file")
    parser.add_argument(
        "--wavelengths", type=Path, required=True, help="QR3 detector CWAVE/CBAND FITS file"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("cutouts", type=Path, nargs="+")
    args = parser.parse_args()
    if not 0 <= args.ra < 360 or not -90 <= args.dec <= 90 or not 0 < args.fov <= 1:
        parser.error("RA must be in [0, 360), Dec in [-90, 90], and FoV in (0, 1] degrees")
    coadd(args)


if __name__ == "__main__":
    main()
