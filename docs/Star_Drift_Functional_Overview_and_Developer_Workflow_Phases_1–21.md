# **Star Drift: Functional Overview and Developer Workflow (Phases 1–2)** 

Handoff document for the developer building the first two phases. Prepared November 12, 2026. 

## **1. What we are building** 

Star Drift is a public web tool that shows images of the sky from NASA's SPHEREx mission and makes it easy to see how a patch of sky changes over time. 

SPHEREx scans the whole sky repeatedly in near-infrared light, split into 102 wavelength bands. Its raw archive data (FITS files) is hard to look at: low resolution (about 6 arcseconds per pixel), no labels, and invisible wavelengths. Star Drift is the translation layer. A user enters or opens a sky position and gets a clear, labelled, interactive view they can compare across observation times and wavelengths. 

**Primary audience:** people who want to hunt for moving or changing objects and inspect spectra (amateur and professional astronomers, students, citizen scientists). The tool is not a casual picture gallery. It is an analysis viewer with sensible defaults. 

**Core promise:** open a link, see a clean image of that sky position, switch between times and bands, click a source to see what it is and what its spectrum looks like. 

## **2. Scope** 

|Phase|Included|Status|
|---|---|---|
|Phase 1: core viewer|URL coordinates, field of view control,<br>epoch switching and blink, difference<br>view, clean image stretch, Gaia and<br>SIMBAD labels, metadata panel|Build now|
|Phase 2: spectral features|102-band slider, per-detector stacks,<br>click-to-spectrum, WISE overlay,<br>contamination flags|Build now|
|Phase 3|Free-form band math, automated<br>ingestion of new data, own database<br>and storage layer|Deferred, not a priority|



Design choices should not block Phase 3, but nothing in Phase 3 should be built now. 

## **3. Key concepts the developer must understand** 

These terms appear throughout the document. 

- **Exposure:** one SPHEREx observation, delivered as a calibrated spectral image (FITS) with an image plane, a quality-flag plane, and headers. 

- **Wavelength varies across the detector:** SPHEREx uses linear variable filters, so within a single exposure each pixel sees a different wavelength, set by its position. A single exposure is not a 102-band cube. 

- **Detector:** the instrument has six detector arrays. The 102 bands come from six detectors with 17 channels each (to be confirmed against the IRSA documentation). 

- **Band (in this project):** a wavelength bin. To show a band at a sky position, we gather every exposure whose pixels at that position fall in that wavelength bin, align them, and combine them. 

- **Epoch:** one of the mission's full-sky survey passes (planned: four over about two years). Individual exposures also carry their own timestamps (MJD), and these must be kept for movingobject work. 

- **Coadd:** several aligned images combined (averaged) to improve signal-to-noise. 

- **WCS (World Coordinate System):** the header information that maps pixel positions to sky coordinates (RA, Dec) and, for SPHEREx, pixels to wavelength. 

- **Cutout:** a small region extracted around a sky position instead of the whole frame. 

**_Verify first:_** _the details above are my understanding of the instrument. Task M0 below makes the developer confirm them against the IRSA SPHEREx documentation and real files before anything else is built._ 

## **4. System overview** 

Browser (frontend) - Viewer canvas (WebGL or Canvas 2D): stretch, colormap, pan, zoom, blink, difference - Overlay layer (labels, catalog markers, flags) 

- Panels: controls, metadata, spectrum plot - Optional Aladin Lite context panel (sky background, catalog layers) | v Backend API (Python, FastAPI) 

- /api/cutout, /api/spectrum, /api/flags, /api/catalog, /api/meta - Astropy-based processing: cutout, alignment, coadd, flag decoding - Cache (disk or object store, keyed by request and data release) | v External services 

- NASA/IPAC IRSA: SPHEREx image data and exposure search - CDS: Gaia, SIMBAD, VizieR; IRSA: WISE - Optional: JPL Horizons or SkyBoT for known solar-system objects Principle for version one: **fetch from IRSA on demand and cache the processed result.** Do not build a full database or ingest the whole archive. This keeps the first version small, and new data releases appear automatically once the cache for the affected region expires. 

## **5. End-to-end workflow** 

### **5.1 User flow** 

1. The user opens a link such as <mark>/?ra=150.12&dec=2.34&fov=0.2&epoch=1&band=42</mark> , or types coordinates or an object name into the search box. 

2. The page parses the URL, sets the initial state, and requests the image data for that view. 

3. A clean, stretched, labelled image appears with the controls and metadata panel. 

4. The user changes something (epoch, band, field of view, overlay, stretch). The view updates and the URL updates to match, so the current view is always shareable. 

5. The user clicks a source. A spectrum plot and a source card appear. 

6. The user blinks between epochs or turns on difference mode to look for movement or change. 

7. The user copies the link or an embed snippet to share the exact view. 

### **5.2 Data flow for one view request** 

1. The frontend sends the view state to <mark>/api/cutout</mark> (position, field of view, epoch or epoch pair, band or detector stack). 

2. The backend computes a cache key from the request plus the current data release identifier. On a hit, it returns the stored result. 

3. On a miss, the backend asks IRSA which exposures cover the position (and the requested epoch window). 

4. For each exposure it extracts a cutout around the position, and reads the quality-flag plane. 

5. It selects the pixels whose wavelength falls in the requested band, reprojects them onto a common north-up grid (flux-conserving for science pixels, nearest-neighbour or bitwise-OR for flags), and combines them into a coadd. 

6. It returns a compact binary payload (float32 image, compressed) plus a JSON header: grid WCS, wavelength range, epoch and time info, exposure count, and a flag summary. 

7. The frontend uploads the array to the GPU, applies the stretch and colormap, and draws it. Overlays are drawn on top using the returned WCS. 

8. Catalog overlays are requested separately from <mark>/api/catalog</mark> (Gaia, WISE, SIMBAD) so the image shows up first. 

### **5.3 Spectrum flow** 

1. The user clicks a position. 

2. The frontend sends the position (and optionally an aperture size and epoch selection) to <mark>/api/spectrum</mark> . 

3. The backend finds every exposure covering the position, reads the flux at that position (single pixel or small aperture) along with its wavelength from the spectral WCS, discards flagged measurements, and returns a list of wavelength, flux, uncertainty, and exposure identifiers. 

4. The frontend plots flux against wavelength, with the six detector ranges shaded and the currently selected band marked. 

## **6. Functional requirements** 

Each item has acceptance criteria the developer can test against. 

### **Phase 1: core viewer** 

|ID|Feature|Acceptance criteria|
|---|---|---|
|P1-1|URL-driven state|Loading a URL with<br>ra,<br>dec,<br>fov,<br>epochrestores that<br>exact view. Changing any control updates the URL<br>without a page reload. Invalid or missing parameters<br>fall back to defaults and show a short message.|
|P1-2|Coordinate and name search|A box accepts decimal degrees, sexagesimal<br>coordinates, and a SIMBAD-resolvable name, then<br>moves the view there.|
|P1-3|Field of view|The user can set the field of view (minimum and<br>maximum defined in config, for example 0.05° to 1°).<br>The backend returns a correspondingly larger or<br>smaller cutout. The UI shows an estimate of data size.<br>Requests above the maximum are rejected with a clear<br>message.|
|P1-4|Clean rendering|Image data is rendered client-side from float values.<br>Stretch options: linear, log, asinh, with adjustable<br>limits and strength. At least two colormaps (greyscale<br>and one perceptual). Optional background subtraction.<br>Display scaling is smooth, not blocky. Contrast changes<br>respond instantly (no new network request).|
|P1-5|Epoch switching and blink|Epoch buttons switch the displayed epoch. A blink<br>control cycles epochs at an adjustable rate. All epochs<br>for a view are aligned to the same pixel grid so<br>features do not jump.|
|P1-6|Difference view|The user picks epoch A and epoch B and sees A minus<br>B on a diverging colormap. Static sources cancel to<br>near zero. The legend explains the colours.|
|P1-7|Gaia and SIMBAD overlay|Markers for Gaia sources with a magnitude cut. Labels<br>(name and magnitude) appear on hover or click, and<br>are thinned automatically at wide fields of view so they<br>do not overlap. Overlay toggles are independent.|
|P1-8|Metadata panel|Shows view centre, field of view, epoch (with time<br>range), wavelength or band, number of exposures<br>combined, and data release identifier.|



|ID|Feature|Acceptance criteria|
|---|---|---|
|P1-9|Navigation|Drag to pan, scroll or pinch to zoom, coordinate<br>readout under the cursor, compass and scale bar.|
|P1-10|Errors and empty states|Positions with no SPHEREx coverage, service timeouts,<br>and oversized requests each show a specific message<br>with what the user can do next.|



**Phase 2: spectral features** 

|ID|Feature|Acceptance criteria|
|---|---|---|
|P2-1|Band selector|A slider selects any of the 102 bands and shows its<br>wavelength and detector number. Changing it<br>fetches or computes the coadd for that band.|
|P2-2|Detector stacks|A mode combines all bands of one detector (17<br>channels) into a single higher-signal-to-noise image.<br>The UI shows how many frames were combined.|
|P2-3|Click-to-spectrum|Clicking a source returns a plot of flux against<br>wavelength from 0.75 to 5.0 µm with uncertainties,<br>detector ranges shaded, and the current band<br>marked. Flagged measurements are excluded or<br>visibly marked. Response time target: under 5<br>seconds for a typical position.|
|P2-4|Source card|Clicking shows position, nearest catalogued source<br>(name, Gaia magnitude, WISE magnitude where<br>available), and the spectrum. A click on empty sky<br>says so.|
|P2-5|WISE overlay|WISE sources shown as a separate marker style<br>from Gaia, independently toggleable.|
|P2-6|Contamination flags|The quality-flag bits are decoded into readable<br>categories (for example cosmic ray, satellite streak,<br>saturation, scattered light). A flag overlay shows<br>affected areas, the metadata panel lists which flags<br>are present in the view, and a "mask flagged pixels"<br>toggle removes them from the image.|
|P2-7|Solar-system overlay (optional within<br>Phase 2)|Known solar-system objects at the observation time<br>are marked, so unmatched moving sources stand<br>out. Include only if time allows.|



## **7. URL specification** 

All parameters are optional. Defaults apply when missing. 

|Parameter|Meaning|Example|
|---|---|---|
|ra|Centre right ascension, decimal degrees|150.12|
|dec|Centre declination, decimal degrees|2.34|
|fov|Field of view width, degrees|0.2|
|epoch|Epoch number 1–4, or<br>allfor a coadd|2|
|band|Band number 1–102|42|
|mode|singleor<br>stack(detector stack)|stack|
|dif|Epoch pair for difference view|2-1|
|overlay|Comma-separated overlays|gaia,wise,flags|
|stretch|linear,<br>log, or<br>asinh|asinh|



Rules: keep keys, tokens, and private values out of the URL. The page must work inside an iframe, so provide an embed mode ( <mark>embed=1</mark> ) that hides the header and uses a compact control layout. 

## **8. Backend API (draft)** 

|Endpoint|Purpose|Key inputs|Returns|
|---|---|---|---|
|GET /api/cutout|Aligned<br>image for a<br>view|ra,<br>dec,<br>fov,<br>epoch,<br>bandor<br>detector,<br>mode|Binary float32 image (compressed) plus<br>a JSON header with grid WCS,<br>wavelength range, time info, exposure<br>count, flag summary|
|GET /api/flags|Decoded<br>quality data<br>for a view|same as cutout|Per-pixel flag array and the list of<br>categories present|
|GET /api/spectrum|Spectrum at<br>a position|ra,<br>dec,<br>aperture,<br>epoch|List of wavelength, flux, uncertainty,<br>exposure id, flag status|
|GET /api/catalog|Overlay<br>sources|ra,<br>dec,<br>radius,<br>catalog (<br>gaia,<br>wise,<br>simbad),<br>magnitude limit|List of sources with coordinates, name,<br>magnitudes|
|GET /api/meta|Data status|none|Current data release, coverage notes,<br>field-of-view limits|



|Endpoint|Purpose|Key inputs|Returns|
|---|---|---|---|
|GET /api/resolve|Name lookup|name|Coordinates|



All endpoints: JSON errors with a code and a human-readable message, input validation with documented limits, caching headers, and rate limiting. 

## **9. Image processing rules** 

These rules prevent subtle scientific mistakes. 

1. **Use sky coordinates as the reference.** Every exposure is mapped to a common north-up grid using its WCS. Pixel positions are never compared directly between exposures. 

2. **Resample science pixels with a flux-conserving method** (for example <mark>reproject_exact</mark> in the <mark>reproject</mark> package). 

3. **Never interpolate quality flags.** Use nearest-neighbour or bitwise OR. 

4. **Match wavelengths before subtracting.** For time differences, use the same wavelength bin in both epochs. Subtracting images at different wavelengths shows spectral differences, not motion. 

5. **Keep exposure timestamps.** Epoch labels are for the interface only. Store each exposure's observation time so moving-object work stays possible. 

6. **Photometric consistency across epochs.** Phase 1 may skip photometric normalisation but must show the user whether it was applied. Normalising to stable reference stars can be added after the core works. 

7. **Return float data and stretch on the client.** Do not pre-render 8-bit images. That is the main weakness of the tools we are improving on. 

8. **Coadd by averaging with flagged pixels excluded** , and report how many frames contributed to each pixel or to the view. 

## **10. Frontend requirements** 

- Rendering with WebGL (preferred) or optimised Canvas 2D. The float array is uploaded once per view, and stretch, colormap, and contrast run in a shader or a fast loop so sliders feel instant. 

- A separate overlay layer for markers, labels, flags, compass, and scale bar, drawn from the returned WCS so it stays aligned during pan and zoom. 

- Aladin Lite may be used as a context panel or as the source of sky backgrounds and catalog layers. It does not replace the SPHEREx viewer, because it cannot apply custom float-data processing. 

- Spectrum plot: a standard charting library is fine. Detector ranges are shaded, error bars are shown, and the current band is marked. 

- Responsive layout and a usable embed mode. Keyboard-accessible controls, visible focus, and sufficient colour contrast. 

- A reference mock-up of the intended layout and interactions is attached separately as <mark>star-driftpreview.html</mark> . It uses synthetic data and is a design reference only, not production code. 

## **11. Suggested technology** 

These are recommendations. Substitute equivalents if the developer has strong preferences. 

|Layer|Suggestion|
|---|---|
|Backend|Python with FastAPI|
|Astronomy processing|astropy,<br>reproject,<br>photutils(if photometry is added),<br>astroqueryor<br>pyvofor IRSA and VO services|
|Sky indexing|HEALPix via<br>astropy-healpixfor cache keys and tile lookup<br>(avoids the RA wrap-around problems of a plain latitude-longitude<br>box query)|
|Cache|Local disk or object storage (S3-compatible), keyed by view<br>parameters and data release|
|Frontend|TypeScript with React or a lightweight framework; WebGL; Aladin<br>Lite for overlays and context|
|Deployment|A container with the API behind a CDN for cached image payloads|



A PostgreSQL/PostGIS database is not needed for Phases 1–2. It becomes useful in Phase 3 if you ingest and index the archive yourself. 

## **12. Non-functional requirements** 

- **Performance:** first image visible within about 3 seconds for a typical view on a cache hit, and within about 10 seconds on a miss. Stretch and colormap changes under 100 ms. 

- **Payload size:** compress image payloads (zstd or gzip). Document the data size per view and per field of view. 

- **Caching:** cache keys include a data release identifier, so a new release invalidates stale results. Add HTTP cache headers. 

- **Politeness to upstream services:** respect IRSA and CDS usage limits, cache catalog queries, and back off on errors. 

- **Reliability:** every external call has a timeout and a clear failure message. The image view should still work if a catalog service is down. 

- **Security:** validate and bound all inputs (coordinates, field of view, band numbers), rate-limit per client, and never expose internal paths or credentials. 

- **Observability:** log request parameters, cache hits and misses, upstream latency, and errors. 

- **Accessibility:** keyboard operation for all controls, text alternatives for non-visual information, and a colour scheme that works for common colour-vision differences. 

## **13. Build order** 

Each milestone has a clear "done" condition so progress is easy to check. 

|Milestone|Work|Done when|
|---|---|---|
|M0: Data spike|Retrieve real SPHEREx data for one<br>position through IRSA. Confirm file<br>structure, how wavelength is stored<br>per pixel, how the 102 bands map<br>to detectors, what the quality flags<br>mean, which cutout or search<br>services exist, and what the request<br>limits are. Write a one-page findings<br>note.|The developer can produce a<br>coadd image for one band at<br>one position from real files by<br>script, and the note confirms or<br>corrects the assumptions in<br>Section 3.|
|M1: Static viewer|Frontend that displays one real<br>cutout with stretch, colormap, pan,<br>zoom, and URL coordinates.|A shared URL opens the same<br>image for another person.|
|M2: Backend cutouts|/api/cutoutand<br>/api/metawith<br>caching, field-of-view control, and<br>metadata panel.|Any position within coverage<br>returns a clean image through<br>the API.|
|M3: Time features|Epoch switching, blink, difference<br>view, shared alignment.|Blinking shows no spurious<br>jumps in static stars. Difference<br>view cancels static sources.|
|M4: Catalogs and labels|Gaia and SIMBAD overlays with<br>decluttering and name search.|Source markers stay aligned<br>through pan and zoom.|
|M5: Bands|Band selector and detector stacks.|The same sky position can be<br>viewed in any band, and stacks<br>visibly improve signal-to-noise.|
|M6: Spectra|/api/spectrum, click-to-spectrum<br>plot, source card.|Clicking a bright known star<br>returns a plausible spectrum<br>within the time target.|
|M7: Flags and WISE|Flag decoding, overlay and masking,<br>WISE overlay.|Known contaminated frames<br>show the correct flags, and<br>masking removes them.|
|M8: Hardening|Error states, rate limits, embed<br>mode, performance pass,<br>accessibility pass, documentation.|All acceptance criteria in<br>Section 6 pass.|



## **14. Risks and open questions** 

|Risk or question|Why it matters|How to handle it|
|---|---|---|
|IRSA cutout and search|The "no database"|M0 confirms this. If on-demand cutouts are|



|Risk or question|Why it matters|How to handle it|
|---|---|---|
|services may not offer exactly<br>what the plan assumes.|shortcut depends on<br>it.|too slow or limited, introduce a small<br>processing and storage layer earlier.|
|Wavelength-per-pixel<br>handling is more complex<br>than a simple band cube.|The band, stack, and<br>spectrum features<br>all depend on it.|M0 confirms exact behaviour. Design the<br>coadd step around the real structure.|
|Request latency when<br>assembling many exposures<br>for one view.|Slow views hurt the<br>experience.|Cache aggressively, limit maximum field of<br>view, and return a fast preview first if<br>needed.|
|Photometric differences<br>between epochs create false<br>changes in difference view.|False discoveries<br>damage trust.|Show clear warnings, add reference-star<br>normalisation after the core works.|
|Short time baseline between<br>SPHEREx epochs.|Slow movers will<br>barely shift.|Set expectations in the interface. Fast movers<br>and spectra are the strength. Longer<br>baselines can come from WISE overlays.|
|Usage limits and politeness of<br>upstream services.|Public traffic could<br>be throttled or<br>blocked.|Cache, rate-limit, and contact the services if<br>usage grows.|
|Data release changes.|Files, headers, or<br>flag definitions may<br>change.|Version cache keys by release, and keep<br>processing code defensive about header<br>differences.|



## **15. Out of scope for now (Phase 3)** 

- Free-form band math (subtract or ratio any two bands, with continuum scaling) and shareable band-math recipes. 

- Automated ingestion: a scheduled job that discovers new exposures and processes only what changed. 

- A self-hosted database and storage layer indexing the full archive. 

- Accounts, saved collections, or candidate-marking workflows. 

The Phase 1–2 design keeps these possible: processing code is separated from the API, cache keys carry the data release, and exposure timestamps are preserved. 

## **16. Work breakdown for three developers** 

### **16.1 How the work is split** 

Three full-stack developers, each owning one vertical area and crossing between front end and back end where that helps. 

- **Developer A: data and processing.** Everything between IRSA and the cutout, band, flag, and spectrum APIs. This is the critical path. 

- **Developer B: the viewer.** In-browser rendering, navigation, time and band controls, and the overlay layer. 

- **Developer C: platform, catalogs, and panels.** Caching, catalog and search services, deployment, and the surrounding interface (search box, metadata, spectrum plot, embed). 

Why this split: turning raw exposures into clean coadds is the hardest and riskiest work, so one person owns it end to end. B and C build against a mock server until it is ready, so nobody waits. 

#### **Ground rules** 

1. **Contract first.** The API schema and the binary payload format (S3, S4) are merged before feature work starts. Changes to either need approval from A and B. 

2. **Size guide.** S is up to about 1 day, M about 2–3 days, L about 4–5 days for one developer, including tests and review. These are rough, to be corrected after the first sprint. 

3. **Every ticket is merged by pull request** with one review from another developer, tests, and its acceptance criteria demonstrated. 

4. **Report spike findings the same day.** Anything that contradicts Section 3 may change tickets A2 to A5 and A9. Everyone adjusts together. 

5. **Phase 3 is out of scope.** If a ticket seems to need free-form band math or automated ingestion, stop and raise it. 

### **16.2 Sprint plan (two-week sprints)** 

|Sprint|Goal|Developer<br>A|Developer<br>B|Developer C|
|---|---|---|---|---|
|0 (week 1)|Setup, data spike, API contract|S2, S4|B1 (start)|S1, S3|
|1|Real coadd from a script; viewer<br>renders real sample data|A1–A5|B1 (finish),<br>B2–B4|C1–C3|
|2|First real end-to-end view; time<br>features; search|A6, A7, A10|B5–B8|C4–C7|
|3|Overlays, bands, context panel|A8|B9, B10|C8, C9|
|4|Spectra and flags in the interface|A9|B11, B12|C10 (embed),<br>plugging<br>spectrum plot<br>into B11|
|5|Hardening and launch|A11<br>(stretch),<br>bug fixes|B13|C11, C13, C12<br>(optional)|



A is heavily loaded in Sprint 1. If A falls behind, C pairs with A on A6 (payload encoding and endpoint wiring), since C1 is small. 

### **16.3 Shared setup tickets** 

**S1 · Repository, CI, and conventions** (Owner: C · Size: S · Depends on: none) 

Create the monorepo ( <mark>backend/</mark> , <mark>frontend/</mark> , <mark>shared/</mark> , <mark>docs/</mark> ), linting and formatting, pre-commit hooks, a CI pipeline that runs tests, a pull-request template, environment-variable handling, and a dockercompose file for local development. 

- A fresh clone starts the backend, frontend, and mock server with one command. 

- CI runs and passes with at least one trivial test in each of the backend and frontend. 

**S2 · Data spike on real SPHEREx data** (Owner: A · Size: M · Depends on: none) 

Retrieve real SPHEREx data for one position (use RA 150.12, Dec 2.34) through IRSA. Document: the file structure and planes, how wavelength is stored per pixel, how the 102 bands map to the six detectors, the meaning of the quality-flag bits, which search and cutout services exist and their request limits, how epochs and exposure times are identified, and how a data release is identified. Write a script that produces one coadd for one band. 

- A one-page findings note is committed to <mark>docs/</mark> and shared with the team. 

- Sample files (a real cutout and a script-made coadd) are available to B and C. 

- Every assumption in Section 3 is marked confirmed or corrected, with proposed ticket changes posted within a day. 

**S3 · API contract and mock server** (Owner: C, reviewed by A and B · Size: M · Depends on: draft starts now, final shape after S2) 

Write the OpenAPI schema for the endpoints in Section 8 ( <mark>cutout</mark> , <mark>fags</mark> l , <mark>spectrum</mark> , <mark>catalog</mark> , <mark>meta</mark> , <mark>resolve</mark> ), including a shared JSON error format with error codes. Build a mock server that returns fixture data (synthetic or from S2). Generate TypeScript types from the schema. 

- B can fetch a mock cutout and render it without any real backend. 

- Schema changes require a pull request approved by A and B. 

**S4 · Binary payload format** (Owner: A, reviewed by B · Size: S · Depends on: S2 for header fields) 

Define the response layout for a cutout: a JSON header (grid WCS on a north-up tangent-plane grid, array shape, wavelength range, epoch and time information, exposure count, flag summary, data release id, format version), followed by compressed float32 image data and an optional uint16 flag array. Specify byte order and compression. 

- The format is documented in <mark>docs/</mark> with a version field. 

- A Python encoder with a unit test, plus a test-vector file that B uses to verify the browser decoder. 

### **16.4 Developer A: data and processing** 

**A1 · IRSA exposure search** (Size: M · Depends on: S2) 

Given RA, Dec, field of view, and an optional time window, return the list of covering exposures with id, observation time (MJD), detector, wavelength information, and access URL. Handle pagination, timeouts, and no-coverage. 

- The spike position returns the expected exposures. 

- No coverage returns a typed "no coverage" result, not an exception. 

#### **A2 · Cutout extraction** (Size: M · Depends on: A1) 

For each exposure, obtain only the needed region (using an IRSA cutout service if the spike shows one is suitable, otherwise efficient partial reads). Read the image plane, the quality-flag plane, the sky WCS, and the per-pixel wavelength information. Keep exposure timestamps. 

- For 10 exposures, cutout sky coordinates match Gaia star positions within 1 pixel. 

- Memory use per request is bounded and documented. 

#### **A3 · Pixel-to-band mapping** (Size: M · Depends on: A2) 

For each cutout, compute each pixel's wavelength and assign it to one of the 102 bands and to its detector. Expose a function that selects the pixels belonging to a band, or to all bands of a detector. 

- Unit tests cover band edges, with edges documented. 

- Pixels outside the requested band are excluded. 

#### **A4 · Common-grid reprojection** (Size: M · Depends on: A2) 

Build the output grid for the requested position and field of view (north-up tangent plane, configurable pixel scale). Reproject science pixels with a flux-conserving method. Reproject flags with nearestneighbour or bitwise OR, never interpolation. 

- The same star lands on the same output pixel (within 0.3 pixel) in two different exposures. 

- Total flux is conserved within 1% on test data. 

- A test confirms that flag values are never blended. 

#### **A5 · Coadd engine** (Size: M · Depends on: A3, A4) 

Combine aligned pixels by averaging with flagged pixels excluded (optional sigma clipping). Return the image, a frames-combined count, and an uncertainty estimate. Support four modes: single band, detector stack (all 17 bands of one detector), a single epoch, and all epochs combined. 

- Stacking N frames of test data reduces noise by roughly the square root of N. 

- The frames-combined count matches the number of frames used. 

- Each mode has a test. 

#### **A6 ·** **<mark>/api/cutout</mark> endpoint** (Size: M · Depends on: A5, S3, S4, C1, C3) 

Implement the endpoint to the OpenAPI schema. Validate inputs, build the cache key using C1, run A1 to A5, and encode the response using S4. Apply the field-of-view limits from configuration. 

- The response matches the schema and the format specification. 

- Oversized fields of view return 400 with a clear code. No coverage returns 404 with a readable message. 

- A repeated request is served from cache. 

#### **A7 · Epoch windows and timestamps** (Size: S · Depends on: S2, A1) 

Define how epochs 1 to 4 map to time ranges (configuration, not hard-coded), support <mark>epoch=all</mark> , and return per-exposure observation times in the response metadata. 

- Each epoch selects the expected exposures for the test position. 

- Metadata lists the epoch time range and the exposure ids used. 

#### **A8 · Flag decoder and** **<mark>/api/f</mark> l** **<mark>ags</mark>** (Size: M · Depends on: S2, A4) 

Decode the quality bitmask into named categories (for example cosmic ray, satellite streak, saturation, scattered light, subject to the SPHEREx documentation). Return a flag array aligned with the cutout grid and a summary of categories present. Add a mask option that makes the coadd (A5) exclude flagged pixels. 

- Test frames with known flagged pixels decode to the correct categories. 

- With masking on, flagged pixels do not contribute to the coadd. 

#### **A9 ·** **<mark>/api/spectrum</mark>** (Size: L · Depends on: A1, A2, A3, S3) 

Given a position, aperture size, and epoch selection, find every covering exposure, read flux and wavelength at that position, estimate and subtract local background, exclude flagged measurements, and return wavelength, flux, uncertainty, exposure id, and flag status per point. 

- A bright Gaia star returns a smooth, plausible spectrum across the six detectors. 

- Typical response time is under 5 seconds. 

- Blank sky returns values near zero with error bars, not an error. 

#### **A10 · Latency and reliability** (Size: M · Depends on: A6) 

Fetch exposures in parallel with a concurrency limit, apply per-call timeouts, retry with backoff, and return partial results with a warning when some exposures fail. Optionally return a quick low-resolution preview first. 

- A load test documents typical and 95th-percentile times against the Section 12 targets. 

- Simulating an upstream outage gives a clear error rather than a hang. 

**A11 · Photometric consistency between epochs** (Stretch ticket · Size: M · Depends on: A5, A7) 

Normalise brightness between epochs using stable reference stars so that difference images cancel static sources more cleanly. Report in the metadata whether normalisation was applied. 

- Static stars cancel better in a difference test than without normalisation. 

- The response metadata states the normalisation status. 

### **16.5 Developer B: the viewer** 

#### **B1 · App shell and URL state** (Size: M · Depends on: S1) 

Build the React and TypeScript shell: layout (controls, viewer, side panels), a central state store, and URL handling per Section 7 (parse, validate, serialise). The URL updates without a page reload. 

- Loading a URL restores the exact view. Changing any control updates the URL. 

- Invalid or missing parameters fall back to defaults and show a short message. 

- The parser and serialiser have unit tests. 

#### **B2 · WebGL renderer** (Size: L · Depends on: S3 mock, S4 test vector) 

Upload the float32 image as a texture. A shader applies linear, log, and asinh stretches with adjustable limits and strength, plus a colormap lookup (greyscale, one perceptual map, and a diverging map for differences). Handle NaN values. Provide a Canvas 2D fallback. 

- The sample payload renders correctly. 

- Changing stretch or limits redraws smoothly (about 60 fps at 512 by 512 on a mid-range laptop) with no network request. 

- The fallback renders the same image. 

#### **B3 · Display controls** (Size: M · Depends on: B2) 

Controls for stretch type, limits (default percentile clipping), strength, colormap, client-side background subtraction, and smooth display scaling. 

- Defaults give a clean, readable view of the real sample from S2. 

- Background subtraction removes a smooth gradient on test data. 

#### **B4 · Navigation** (Size: M · Depends on: B2) 

Pan, zoom (wheel and pinch), a coordinate readout under the cursor computed from the returned WCS, a compass, and a scale bar. When zoom crosses a field-of-view step, trigger a new fetch. 

- Readout matches known WCS test values. 

- The scale bar is correct at several fields of view. 

**B5 · Data client** (Size: M · Depends on: S4, S3, A6 for real data) 

Fetch cutouts, decode the payload (decompress, parse header, read arrays), cancel stale requests, show loading and progress states, map each API error code to a readable message, and keep a small inmemory cache of recent views. 

- The decoder reproduces the S4 test vector exactly. 

- Rapid control changes cancel in-flight requests. 

- Each error code produces its message. 

#### **B6 · Epoch switching and blink** (Size: M · Depends on: B5) 

Epoch selector, background prefetch of the other epochs, and a blink control with adjustable rate. Check that all epochs share the same grid and warn if they do not. 

- Blinking shows no flicker or jumping of static stars on the sample. 

- After prefetch, switching epochs is instant. 

**B7 · Difference view** (Size: M · Depends on: B6, B2) 

Client-side subtraction of epoch B from epoch A on the aligned arrays, with a diverging colormap centred on zero, a legend, and a visible note on whether photometric normalisation was applied. Block or warn when the two images use different wavelength bins. 

- Static sources cancel to noise on test data. 

- The legend explains what each colour means. 

**B8 · Field-of-view control** (Size: S · Depends on: B5, C2) 

Slider and numeric input with limits from <mark>/api/meta</mark> , a live data-size estimate, and debounced fetches. 

- Out-of-range values are clamped with a message. 

- The estimate updates as the slider moves, and only the final value triggers a request. 

#### **B9 · Overlay layer** (Size: L · Depends on: B4, C4) 

A layer above the viewer, positioned from the WCS, drawing Gaia and WISE markers in distinct styles, labels thinned by magnitude and zoom, hover and click picking, independent toggles, and a selection ring. 

- Markers stay aligned with the image within 1 pixel while panning and zooming. 

- Labels do not overlap in a crowded test field. 

- Clicking a marker returns the underlying source record. 

**B10 · Band selector and detector stack** (Size: M · Depends on: B5, A5) 

Band slider (1 to 102) showing wavelength and detector, a detector bar, a single-band or detector-stack mode switch, and a display of how many frames were combined. Debounce requests. 

- Changing the band fetches the correct data and updates the wavelength label. 

- Stack mode shows the number of frames combined. 

#### **B11 · Click-to-spectrum** (Size: M · Depends on: B9, C9, A9) 

On click, find the nearest source or use the clicked position, call <mark>/api/spectrum</mark> , show a loading state, and feed the result to the spectrum plot (C9) and source card (C7). Cancel the request if the user clicks elsewhere. 

- Clicking a star populates the card and plot. 

- Clicking empty sky shows a clear message. 

#### **B12 · Flag overlay and mask toggle** (Size: M · Depends on: A8, B2) 

Fetch <mark>/api/f</mark> l <mark>ags</mark> , draw a colour-coded overlay by category with a legend, and add a mask toggle that refetches the image with masking on. 

- Categories are visually distinct and match the legend. 

- Masking removes the satellite streak from the test frame. 

- The overlay stays aligned with the image. 

**B13 · Responsive layout, accessibility, and polish** (Size: M · Depends on: most of B) 

Layouts for tablet, phone, and embed. Keyboard operation for every control, visible focus, sufficient contrast, reduced-motion support, and colour palettes that work for common colour-vision differences. 

- An automated accessibility audit reports no critical issues. 

- Every control is usable from the keyboard. 

- The layout works at 360 pixels wide. 

### **16.6 Developer C: platform, catalogs, and panels** 

#### **C1 · Cache layer** (Size: M · Depends on: S4) 

A cache interface with a disk implementation and an S3-compatible adapter. The key combines the HEALPix tile of the view centre, a field-of-view bucket, epoch, band or detector mode, mask setting, and data release id. Set ETag and Cache-Control headers, a time-to-live, and a size cap with eviction. 

- A second identical request is served from cache. 

- A new data release invalidates old entries. 

- Key-building tests include positions near RA 0 and 360 degrees and near the poles. 

#### **C2 ·** **<mark>/api/meta</mark> and release detection** (Size: S · Depends on: S2) 

Return the current data release id, field-of-view limits, supported bands, epoch definitions, and notes. Check for a new release on a schedule (for example hourly) and update the id. 

- The endpoint matches the schema. 

- Changing the detected release id changes cache keys. 

#### **C3 · Validation, errors, rate limiting, logging** (Size: M · Depends on: S3) 

Shared validators for RA, Dec, field of view, band, and epoch. A consistent JSON error format with codes. Per-client rate limiting. Structured logs with a request id, cache hit or miss, and upstream latency. 

- Invalid inputs return 400 with a code and readable message. 

- Over-limit requests return 429 with a Retry-After header. 

- Logs contain the fields listed above. 

#### **C4 ·** **<mark>/api/catalog</mark>** (Size: M · Depends on: S3, C1) 

Cone searches for Gaia (through a CDS or VO service), WISE (through IRSA), and SIMBAD, with a magnitude limit, a normalised response format, caching, timeouts, and graceful handling of one service being down. 

- Results for a test position match direct queries to the archives. 

- The magnitude cut works. 

- A service outage returns a clear message and does not affect the image view. 

#### **C5 ·** **<mark>/api/resolve</mark>** (Size: S · Depends on: S3) 

Resolve an object name to coordinates (SIMBAD or Sesame), with caching and clear errors. 

- A well-known name resolves to the right coordinates. 

- An unknown name returns 404 with a readable message. 

#### **C6 · Search box** (Size: S · Depends on: B1, C5) 

An input that accepts decimal degrees, sexagesimal coordinates, and object names. Parse coordinates in the browser, resolve names through C5, move the view, and show examples. 

- All supported formats parse correctly in unit tests. 

- Invalid input shows a message that says what formats are accepted. 

**C7 · Metadata panel and source card** (Size: S · Depends on: B1, A6 header fields) 

Show centre, field of view, epoch and its time range, band and wavelength, number of exposures combined, data release id, and flags present. The source card shows name, position, Gaia and WISE magnitudes, and nearest catalogue match. 

- Every field comes from the response header or catalog data. 

- Panels update with the view and have a sensible empty state. 

#### **C8 · Aladin Lite context panel** (Size: M · Depends on: B1) 

Embed Aladin Lite as an optional panel. Keep its centre and field of view in sync with the viewer in both directions. Allow the background survey to be changed, and the panel to be hidden. 

- Panning either view updates the other within about 200 ms. 

- Changing the background survey works. 

#### **C9 · Spectrum plot** (Size: M · Depends on: S3 mock) 

A plot component: wavelength from 0.75 to 5.0 µm, error bars, shaded detector ranges, markers for the bands in use, distinct styling for flagged points, a hover readout, and CSV export. 

- Renders the mock spectrum and resizes correctly. 

- The CSV export matches the plotted data. 

- A text summary is available for screen readers. 

#### **C10 · Embed mode and sharing** (Size: S · Depends on: B1) 

An <mark>embed=1</mark> layout, a generated iframe snippet, a copy-link button, and server headers that permit embedding. 

- The page works inside an iframe on a separate test site. 

- Copying the link reproduces the same view elsewhere. 

#### **C11 · Deployment and CDN** (Size: M · Depends on: S1, A6) 

Container images, environment configuration, a deploy pipeline to a staging environment, HTTPS, CDN rules for cached payloads, health checks, and basic monitoring. 

- A merge to main deploys to staging automatically. 

- A repeated request is served from the CDN. 

- A failed health check raises an alert. 

**C12 · Solar-system ephemeris** (Optional · Size: M · Depends on: C4) 

An endpoint that returns known solar-system objects at the observation times of a view (JPL Horizons or SkyBoT), cached, for B9 to display. 

- A known asteroid appears at its expected position within tolerance. 

- B9 can show it with a distinct marker. 

**C13 · End-to-end, performance, and accessibility tests** (Owner: C, with A and B contributing · Size: M · Depends on: most tickets) 

Automated browser tests for the main flows (URL load, blink, difference, band change, spectrum click), a load test of the API, and performance budget checks. 

- Tests run in CI. 

- Results are documented against the Section 12 targets. 

### **16.7 Key handoffs between developers** 

|From|To|What is handed over|Needed by|
|---|---|---|---|
|A|B and C|S2 findings note and sample files|End of week 1|
|A|B|S4 format specification and test vector|Sprint 1 start|
|C|B|S3 mock server and generated types|Sprint 1 start|
|A|C|Cache key inputs needed from the processing code (for<br>C1)|Sprint 1|
|C|B|/api/catalog(C4) for overlays|Sprint 2 end|
|A|B|Working<br>/api/cutout(A6) to replace the mock|Sprint 2 start|
|C|B|Spectrum plot component (C9) for click-to-spectrum|Sprint 3 end|
|A|B|/api/spectrum(A9) and<br>/api/flags(A8)|Sprint 3 end|



### **16.8 Definition of done (every ticket)** 

- Merged by pull request with one review from another developer. 

- Unit tests added, and CI passes. 

- Acceptance criteria demonstrated in a short demo or screen recording. 

- No console errors or unhandled exceptions in the demonstrated flow. 

- Documentation or the API schema updated if behaviour changed. 

- Performance stays within the Section 12 targets, or the gap is raised. 

### **16.9 What could reshape this plan** 

- **The spike (S2) may change Developer A's work.** If IRSA cutouts are not available or are slow, A2 and A10 grow, and C1 should gain a small processing and storage layer sooner. 

- • **Band and wavelength handling is the biggest unknown.** If it differs from Section 3, A3, A5, A9, and B10 need revising together. 

- **Differences between epochs depend on A11.** Until it exists, B7 must keep its warning about photometric normalisation visible. 

