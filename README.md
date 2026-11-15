# Star Drift

Star Drift is a public web viewer for NASA SPHEREx sky images. The current explorer renders live single-band cutouts and includes two bundled observations for offline viewing.

## Run locally with Docker

From the repository root, with Docker Desktop or the Docker Engine running:

```sh
docker compose up --build
```

Open <http://localhost:5173> and choose **Explore now**. The same-origin API smoke check is available at <http://localhost:5173/api/health>. The backend is also reachable directly at <http://localhost:8000/api/health>.

The default setup needs no environment file. To change the host ports, copy `.env.example` to `.env` and edit it. Source edits in `backend/app/`, `frontend/src/`, and `frontend/public/` reload automatically. Re-run with `--build` after dependency or configuration changes.

The explorer opens a known live SPHEREx field. It accepts decimal RA and Dec and a field width from 0.05° to 1°. Scope and M31 are also available as bundled samples. Live requests need access to IRSA and the public SPHEREx S3 mirror for FITS files and detector calibration. The first request for an uncached view can take tens of seconds. Display adjustments and local pan and zoom work after the image loads.

FastAPI checks the public Level 2 directory and calibration listings at startup and then hourly. The archive fingerprint separates cached grids across publication changes. Set `STARDRIFT_RELEASE_CHECK_SECONDS=0` to turn off polling in an offline test. `STARDRIFT_REQUESTS_PER_MINUTE` defaults to 60 per client address in each backend process. Cache stats and manual invalidation require an `Authorization: Bearer …` header with the configured `STARDRIFT_CACHE_ADMIN_TOKEN`. The routes return 403 when the token is unset. Start the backend with those variables in its environment. Compose takes them from its environment or root `.env`.

Disk caching is the default. A backend configured with `CACHE_TYPE=s3` and `S3_BUCKET` uses the configured bucket and `S3_PREFIX` (default `stardrift`) for cutouts. Use a prefix dedicated to this app and grant the service access to list, read, write, and delete its cache objects.

Select an epoch under the image to view that survey, or choose **All surveys** to see the combined image. **Start blink** alternates between the selected A and B epochs at the chosen frame interval. **Difference** shows A minus B and carries the selected pair in the share link. Coverage depends on the field, band, and epoch.

For **All surveys**, Star Drift starts the live cutout first. If it fails or is still pending after 12 seconds, Star Drift also checks its DR1 cutout cache and can read the [SPHEREx DR1 All-Sky Cube](https://www.ipac.caltech.edu/doi/10.26131/IRSA679) directly from the [public AWS mirror](https://irsa.ipac.caltech.edu/cloud_access/). The fallback shows Surveys 1 and 2 combined, with the cube's zodiacal-light subtraction and 12.9″ pixel scale. Bands 51 and 52 are blank in DR1. Detector stacks from the cube average available bands weighted by the number of input pixels. Star Drift labels fallback images and caches successful cube views separately from live data so they remain available during a mirror outage. The bundled wavelength table serves band metadata for the matching published calibration version.

Numbered surveys, blink, and difference use individual exposures. When an exposure search fails, the backend can reuse its last complete search for the same field, detector, and date bounds if the exposure files are still reachable. The last 256 distinct searches are kept in `exposure-searches.sqlite3` under `CACHE_DIR` (default `data/cache` inside the backend). Docker Compose preserves that directory in a named volume.

Select **Try live again** on a fallback image to give the live archive its full time budget before falling back.

The default epoch selection uses the published survey collection and planning angle. Set `STARDRIFT_EPOCH_WINDOWS` to a JSON object of optional inclusive UTC MJD bounds by epoch, such as `{"4":[61186,61200]}`. The configured bounds appear in `/api/meta`, and the cache keys for that epoch include the bounds.

The position search accepts names such as M31, decimal coordinates, and sexagesimal coordinates. M31 and its common aliases resolve locally to one position. Other names use SIMBAD with CDS Sesame mirrors as a fallback. Gaia DR3, SIMBAD, and AllWISE overlays can be toggled and filtered separately. Select a marker to inspect its catalog record. The **Sky Context** tab opens Aladin Lite in the main viewing area, with a background survey selector below it. Select **Image** or **Difference** to bring the current map position and field width into the SPHEREx viewer. Recent complete cutouts, catalog results, and successful name searches can be reused in the browser. SIMBAD queries use a secondary mirror if the primary service is temporarily unavailable.

Use the wavelength slider to choose any of the 102 published bands or switch to **Detector stack** and choose a detector. Single bands use a nominal wavelength from the spectral calibration. A stack reports the wavelength range of its contributing samples and the number of exposures combined. Shared URLs use either the global band number or the stack mode and detector.

## Run locally without Docker

Install Python 3.14 and Node 24.15.0, as recorded in `.python-version` and `.node-version`. In one terminal, from the repository root:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

On macOS and Linux, use `.venv/bin/python` in place of `.\.venv\Scripts\python.exe`. In a second terminal, from the repository root:

```sh
cd frontend
npm ci
npm run dev
```

On Windows PowerShell, use `npm.cmd` if the PowerShell npm wrapper is blocked. Open <http://localhost:5173>. The bundled viewer also works with only the frontend running. If the backend runs elsewhere, copy `frontend/.env.example` to `frontend/.env`, set `API_PROXY_TARGET`, and restart Vite.

The viewer includes the original scope coadd and a 0.809 µm M31 preview. Run `scripts/export_viewer_fixture.py` with the backend's Python environment to regenerate the original Scope sample. The optional M31 DR1 bundle joins the published DR1 FITS cubes `Npix655_cube.fits` and `Npix677_cube.fits` on the preview's sky grid. Put both cubes in `data/` and run this command from the repository root.

```powershell
.\backend\.venv\Scripts\python.exe scripts/build_m31_bundle.py
```

The script writes the manifest and individual SPX1 bands to the ignored `frontend/public/data/m31-dr1/` directory. Generate the files before building the frontend for distribution. The M31 preset loads each available band on demand from these files and uses the one-band preview when the bundle is absent. The bundle represents Surveys 1 and 2 combined. Epoch comparisons are available through the live archive where individual exposures are available.

## Checks

From `backend/`, run `python -m ruff check .` and `python -m pytest` using the virtual environment. From `frontend/`, run `npm run lint`, `npm test`, and `npm run build`.

## Repository layout

- `backend/app/` — FastAPI application, IRSA cutout pipeline, and cache.
- `frontend/src/` — React application and frontend tests.
- `shared/` — checked-in test data and the home for the API and payload contracts.
- `scripts/` — standalone developer scripts, including the real-data spike.
- `docs/` — [Phase 1–2 scope](docs/Star_Drift_Functional_Overview_and_Developer_Workflow_Phases_1%E2%80%9321.md) and [SPHEREx data spike](docs/spherex-data-spike.md).
