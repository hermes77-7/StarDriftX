# SPHEREx real-data spike

The QR3 wide-survey query at ICRS RA 150.12°, Dec 2.34° returned eight Level 2 image records for four pointings, each observed by detectors 1 and 4. The SIA2 query took 3.6 seconds. The four Detector 1 cutouts were 70 × 70 science pixels and about 4 MB each, including the full effective PSF table. Individual downloads took 6.3–15.6 seconds in this run. These measurements cover archive requests and show why the first interactive latency target needs a cache and a separate cold-path benchmark.

## Results

Both outputs use a 36 × 36 north-up tangent-plane grid at 6.15 arcsec per pixel, centered on the requested position. The spike selects the nominal channel using the archive's detector channel map, reads calibrated wavelength and bandwidth, subtracts `ZODI`, and reprojects science pixels with overlap-area weighting. It excludes a documented set of quality bits while retaining the known-source marker. The coadd preserves surface brightness in MJy/sr, contribution counts per pixel, and input filenames and observation times.

| Field | Detector and nominal channel | Input MJD (UTC) | Coverage |
|---|---|---|---|
| Scope (150.12°, 2.34°) | D1 channel 4, 0.809 µm | 61186.359738, 61186.361248 | 1,296 of 1,296 pixels covered, including 108 with two contributions |
| Deep (270°, 66.56°) | D1 channel 14, 1.025 µm | 61242.214488, 61242.821925 | 1,296 of 1,296 pixels covered, including 1,273 with two contributions |

The nominal channel can cover different parts of adjacent frames. The scope field samples channels 3–6 at its center across the four D1 pointings. The two channel-4 frames contribute mostly complementary coverage there. The deep field independently exercises averaging across nearly the entire image. Both fixtures passed FITS checksum, coverage, exposure-count, and WCS-center checks. The spike follows the IRSA Mosaic Tool documentation for nominal channels, grid scale, and per-pixel hit counts. A pixel-by-pixel comparison against an independently generated archive mosaic and photometric validation remain open.

## Findings for implementation

- An archived exposure is a calibrated per-pointing, per-detector image. The sampled QR3 file contains `IMAGE`, `FLAGS`, `VARIANCE`, `ZODI`, `EPSF`, and `WCS-WAVE` extensions. The instrument has six detectors. The QR3 channel table contains 102 rows, with 17 nominal channels per detector. The detector map also contains edge values 0 and 18. For scientific wavelength and bandwidth, use the matching `CWAVE` and `CBAND` calibration arrays. The embedded `WCS-WAVE` is approximate.
- The QR3 sample's flag image is 32-bit. It declares flags with `MSKNxxxx` and `MSKDxxxx` header cards and contains bit 28 (`HALO`). Bit 21 (`SOURCE`) is a known-source marker. The future payload must preserve high bits, and the masking policy needs an explicit distinction between quality exclusions and informational flags.
- Use the archive collection and the processing version as provenance. `spherex_qr3` selects the wide survey and `spherex_qr3_deep` selects the deep survey. IRSA reports that DR1 replaces QR2 in searches and is processed consistently with QR3. These fixtures use QR3. SIA2 provides `t_min` and `t_max`, and the files provide `MJD-OBS`, `MJD-BEG`, and `MJD-END`. This run did not establish the four survey epoch windows. Keep the original times and validate any epoch mapping separately. [IRSA release overview](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/overview_qr.html)
- SIA2 can search by sky region and time. On-premises FITS URLs accept cutout center and angular size. A bounded deep-field search returned 30 rows using `MAXREC=30`, and some candidate deep-field cutouts returned HTTP 503. Production request quotas and sustained concurrency limits were not established. The app should use bounded requests and cache results while those limits are characterized.

## Reproduce

Run from the repository root in a Bash shell, after installing the backend requirements. The downloads and generated products go under the ignored `data/` directory.

```bash
set -e
mkdir -p data/spherex-spike
cd data/spherex-spike

irsa=https://irsa.ipac.caltech.edu
mirror=https://nasa-irsa-spherex.s3.us-east-1.amazonaws.com
channels=spectral_channels_spx_cal-sch-v2-2026-229.fits
wavelengths=spectral_wcs_D1_spx_cal-swcs-v5-2026-191.fits
curl -fsSG "$irsa/SIA" --data-urlencode 'COLLECTION=spherex_qr3' \
  --data-urlencode 'POS=CIRCLE 150.12 2.34 0.06' \
  --data-urlencode 'RESPONSEFORMAT=VOTABLE' -o scope-search.xml
curl -fsS "$mirror/qr3/spectral_channels/cal-sch-v2-2026-229/$channels" -o "$channels"
curl -fsS "$mirror/qr3/spectral_wcs/cal-swcs-v5-2026-191/1/$wavelengths" -o "$wavelengths"

scope_base="$irsa/ibe/data/spherex/qr3/level2/2026W22_1A/l2b-v27-2026-269/1"
deep_base="$irsa/ibe/data/spherex/qr3/level2/2026W30_1B/l2b-v27-2026-222/1"
scope_1=level2_2026W22_1A_0214_1D1_spx_l2b-v27-2026-269.fits
scope_2=level2_2026W22_1A_0214_2D1_spx_l2b-v27-2026-269.fits
deep_1=level2_2026W30_1B_0222_3D1_spx_l2b-v27-2026-222.fits
deep_2=level2_2026W30_1B_0340_1D1_spx_l2b-v27-2026-222.fits
for name in "$scope_1" "$scope_2"; do
  curl -fsS --retry 2 "$scope_base/$name?center=150.12,2.34&size=0.12" -o "$name"
done
for name in "$deep_1" "$deep_2"; do
  curl -fsS --retry 2 "$deep_base/$name?center=270,66.56&size=0.12" -o "$name"
done
```

Return to the repository root and run the following in Bash. On Windows with Git Bash, use `backend/.venv/Scripts/python.exe` in place of `backend/.venv/bin/python`. The spike now imports the reusable backend pipeline.

```bash
inputs=data/spherex-spike
export PYTHONPATH=backend
python=backend/.venv/bin/python
channels="$inputs/spectral_channels_spx_cal-sch-v2-2026-229.fits"
wavelengths="$inputs/spectral_wcs_D1_spx_cal-swcs-v5-2026-191.fits"

"$python" scripts/spherex_spike.py --ra 150.12 --dec 2.34 --channel 4 \
  --channels "$channels" --wavelengths "$wavelengths" \
  --output "$inputs/scope-d1-ch04.fits" "$inputs"/level2_2026W22_1A_0214_*D1_*.fits

"$python" scripts/spherex_spike.py --ra 270 --dec 66.56 --channel 14 \
  --channels "$channels" --wavelengths "$wavelengths" \
  --output "$inputs/deep-d1-ch14.fits" "$inputs"/level2_2026W30_1B_*D1_*.fits
```

The [shared fixtures](../shared/fixtures/spherex/) include the original scope cutout ending in `_2D1` and both derived coadds. The coadds store a float32 image in the primary HDU, integer hit counts in `NHITS`, and observation provenance in `EXPOSURES`.

## Reusable backend

The [IRSA client](../backend/app/spherex/irsa.py) searches the DR1 and QR3 wide-survey collections by default. It can also search explicit deep collections. It uses the documented SIA2 `MAXREC` and `TIME` parameters. When a result is full, it splits the time range, merges the results, and deduplicates overlapping records. An exhausted page or time budget raises a typed archive error. A completed search with zero rows returns a `SearchResult` with `no_coverage=True`. [IRSA SIA2 parameters](https://irsa.ipac.caltech.edu/ibe/sia.html)

Default client limits include eight simultaneous upstream requests, 26 seconds per SIA search attempt within a 55-second budget per collection, 18 seconds per FITS request attempt, a 60-second download batch budget, and 32 MiB per FITS response. Searches for a detector pass a wavelength in its passband to IRSA and check the detector again in the result. Complete exposure lists can be reused for five minutes within the same release, field, detector, collection, and time window. For single-band cutouts, a 64 KiB FITS header from the public mirror can rule out exposures whose detector footprints miss the calibrated channel. The live pipeline reads only the needed science planes from the mirror and falls back to the IRSA cutout service when that read fails. Completed downloads remain usable when other exposures time out or the input pixel budget is reached. Incomplete results carry a warning. [SPHEREx archive access](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-access/)

The [epoch rules](../backend/app/spherex/epochs.py) use DR1 for Surveys 1–2 and QR3 for Surveys 3–4. The planned ecliptic position angle identifies the pass within each pair. A numbered selection applies this filter to downloaded wide-survey exposures. Callers can add explicit MJD bounds with a `TimeWindow` and override the survey configuration. The Scope QR3 cutouts have a planned angle of 180° and classify as Survey 4 under this rule. [IRSA data releases](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-releases/) · [Explanatory supplement](https://irsa.ipac.caltech.edu/data/SPHEREx/docs/SPHEREx_Expsupp_QR.pdf)

The [coadd pipeline](../backend/app/spherex/coadd.py) takes an iterable of FITS cutouts, detector calibration, and a band or detector selection. The archive channel-map values 0 and 18 are treated as detector edges. The pipeline outputs WCS, zodi-subtracted brightness, per-pixel hit counts and uncertainty, combined uint32 flags, and exposure provenance. It uses overlap-area weights for brightness. Its uncertainty estimate treats reprojection samples within each exposure as fully correlated and independent exposures in quadrature. Pixels with missing input variance keep an unknown uncertainty. Flags use nearest-neighbor reprojection and bitwise OR. [IRSA data products](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-products/)

To try the live path from the repository root, set `PYTHONPATH=backend` and run the following with the backend Python. Use the calibration files downloaded in the reproduction section.

```python
import asyncio
from pathlib import Path

import httpx

from app.spherex.coadd import BandSelection, coadd
from app.spherex.cutouts import read_calibration
from app.spherex.epochs import QR3, TimeWindow
from app.spherex.irsa import IrsaClient, SkyRegion


async def main():
    root = Path("data/spherex-spike")
    calibration = read_calibration(
        root / "spectral_channels_spx_cal-sch-v2-2026-229.fits",
        root / "spectral_wcs_D1_spx_cal-swcs-v5-2026-191.fits",
        detector=1,
    )
    region = SkyRegion(150.12, 2.34, 0.06)
    async with httpx.AsyncClient() as http:
        irsa = IrsaClient(http)
        found = await irsa.search(
            region, collections=(QR3,), detector=1, epoch="4",
            window=TimeWindow(61186, 61187),
        )
        cutouts = []
        async for result in irsa.cutouts(found.exposures, region, epoch="4"):
            if result.cutout is None:
                print(result.exposure.id, result.warning)
            else:
                cutouts.append(result.cutout)
    image = coadd(region, cutouts, calibration, BandSelection.from_band(4), epoch="4")
    print(image.brightness.shape, len(image.contributions))


asyncio.run(main())
```

The FastAPI `/api/cutout` endpoint now uses this path. The Node mock continues to expose the Scope fixture for development and contract tests.

Data are from NASA's SPHEREx mission and the NASA/IPAC Infrared Science Archive. Cite [SPHEREx Quick Release 3, DOI 10.26131/IRSA662](https://doi.org/10.26131/IRSA662). See [IRSA archive access](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-access/), [data products and calibrations](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-products/), and the [IRSA Mosaic Tool](https://irsa.ipac.caltech.edu/onlinehelp/spherex/spherex/mosaic.html).
