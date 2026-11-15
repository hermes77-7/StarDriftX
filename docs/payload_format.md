# Star Drift cutout payload, format 1.0

Specification for the binary response of `GET /api/cutout` (ticket S4). Reference encoder, decoder and test vector: `sdcutout.py`, `testvector-v1.sdco`, `testvector-v1.expected.json`.

Status: draft for review by Developers A and B. Items marked **[S2]** depend on the data spike and must be confirmed against real SPHEREx files before this is frozen.

## 1. Design decisions

| Decision | Choice | Reason |
|---|---|---|
| Container | One binary body: 12-byte prefix, JSON header, then compressed data sections | One request, self-describing, easy to extend |
| Byte order | **Little-endian** for every integer and float | All browsers and servers in practice are little-endian, so typed arrays work without swapping |
| Image dtype | float32, `NaN` where there is no data | Matches the source precision, and shaders can test for NaN |
| Flags dtype | uint16 | Holds up to 16 decoded flag bits per pixel |
| Compression | **zstd** per section (level 3) after a **byte shuffle**; `gzip` and `none` also allowed | Shuffle groups similar bytes of floats and compresses far better. gzip is a fallback the browser can decode natively |
| Row order | **Row 0 is the south edge** (FITS order) | Uploads straight into a WebGL texture and appears north-up with no flip. Canvas 2D needs one vertical flip |
| Grid | North-up gnomonic (TAN) projection, no rotation | The client can compute sky positions with simple formulas |
| Integrity | CRC-32 of each section's raw bytes | Catches truncated or corrupted transfers |

## 2. Container layout

```
offset  size  field
0       4     magic: ASCII "SDCO"
4       1     format major version (uint8)      = 1
5       1     format minor version (uint8)      = 0
6       2     reserved (uint16), must be 0
8       4     header_length N (uint32)
12      N     JSON header, UTF-8
12+N    ...   data region: the compressed sections, back to back
```

Section locations are given in the header, so the order in the data region is not significant. `offset` is counted from the start of the data region (byte 12+N).

## 3. JSON header

Unknown fields must be ignored by clients. Fields marked required must be present.

| Field | Type | Req. | Meaning |
|---|---|---|---|
| `format` | string | yes | Always `"stardrift-cutout"` |
| `version` | string | yes | `"1.0"` (matches the prefix bytes) |
| `request` | object | yes | Echo of the request: `ra`, `dec`, `fov_deg`, `epoch`, `mode`, `band` or `detector`, `mask` |
| `grid.shape` | [int, int] | yes | `[rows, cols]` of every section |
| `grid.wcs` | object | yes | World coordinate system, see section 5 |
| `grid.pixel_scale_arcsec` | number | yes | Output pixel size (default 6.2) |
| `grid.row_order` | string | yes | Always `"south_first"` in 1.x |
| `spectral.mode` | string | yes | `"single"` or `"stack"` |
| `spectral.bands` | [int] | yes | Band numbers (1 to 102) that were combined |
| `spectral.detectors` | [int] | yes | Detector numbers (1 to 6) involved |
| `spectral.wavelength_um` | object | yes | `min`, `max`, `effective` wavelength in micrometres |
| `time.epoch` | int or `"all"` | yes | Epoch (1 to 4) or `"all"` for a coadd over every epoch |
| `time.mjd_start`, `time.mjd_end` | number | yes | Range of observation times (MJD) of the contributing exposures |
| `exposure_count` | int | yes | Number of distinct exposures contributing |
| `frames_combined` | int | yes | Number of band-epoch frames combined per pixel (typical) |
| `exposures` | [object] | no | `{id, mjd, detector}` per exposure. Capped at 200 entries. If cut, `exposures_truncated: true` |
| `processing.coadd` | string | yes | `"mean"` in 1.0 |
| `processing.flagged_excluded` | bool | yes | True if masked pixels were excluded from the coadd |
| `processing.photometric_normalisation` | string | yes | `"none"` or `"applied"` |
| `processing.background_subtracted` | bool | yes | Whether the server removed a background |
| `processing.resampling` | object | yes | `science`: `"flux_conserving"`; `flags`: `"bitwise_or"` |
| `units.image` | string | yes | Unit of the image values, taken from the source files **[S2]** |
| `flags.present` | [object] | yes | `{name, bit, pixel_count}` for each flag category found in this view (empty list if none) |
| `flags.masked` | bool | yes | Same meaning as `processing.flagged_excluded` |
| `flags.definitions_version` | string | yes | Which bit-definition table was used **[S2]** |
| `data_release` | string | yes | Data release identifier, also part of the cache key |
| `server_version` | string | yes | Backend build, for debugging |
| `sections` | [object] | yes | Section table, see section 4 |
| `notes` | string | no | Free text, for example "Synthetic test vector" |

## 4. Sections

Each entry in `sections` describes one array.

| Field | Meaning |
|---|---|
| `name` | `"image"` (required), then optional `"flags"`, `"frames"`, `"uncertainty"` |
| `dtype` | `"float32"` or `"uint16"` |
| `shape` | `[rows, cols]`, identical for all sections |
| `codec` | `"zstd"`, `"gzip"` or `"none"` |
| `shuffle` | true if bytes were shuffled before compression (default for the encoder) |
| `offset` | Start of this section in the data region |
| `compressed_length` | Bytes stored |
| `raw_length` | Bytes after decompression and unshuffling (`rows * cols * itemsize`) |
| `crc32` | CRC-32 (unsigned) of the raw bytes after unshuffling |

Meaning of each named section:

- **`image`** (float32): the combined flux. `NaN` means no valid data.
- **`flags`** (uint16): for each output pixel, the bitwise OR of the flag bits of all input pixels that contributed to it (or would have contributed, if masking removed them). Bit numbers are listed in `flags.present` **[S2]**.
- **`frames`** (uint16): how many frames contributed to each pixel. 0 means no coverage.
- **`uncertainty`** (float32): one-sigma uncertainty of `image`.

`flags`, `frames` and `uncertainty` are present only when the client asked for them (query parameter `include=flags,frames,uncertainty`). A client that does not find a section must continue without it.

## 5. Grid and WCS conventions

- The array is stored row by row, row 0 first. **Row 0 is the south edge, and column 0 is the east edge** (so the image appears with north up and east left once drawn).
- `grid.wcs` uses FITS conventions:
  - `ctype` = `["RA---TAN", "DEC--TAN"]`, `crval` = `[ra, dec]` of the grid centre (degrees)
  - `crpix` = `[(cols+1)/2, (rows+1)/2]`, which is **1-based** (the first pixel centre is at 1.0)
  - `cdelt` = `[-s, +s]` with `s = pixel_scale_arcsec / 3600` degrees per pixel (negative for x, because RA grows toward the left)
  - `pc` = identity, `lonpole` = 180
- Grid size: `rows = cols = ceil(fov_deg * 3600 / pixel_scale_arcsec)` for requests the server builds from `fov`. The format itself allows non-square grids, and the test vector is deliberately non-square.

**Pixel to sky** (0-based column `x`, row `y`), exact for the TAN projection:

```
xi  = radians(cdelt1 * (x + 1 - crpix1))
eta = radians(cdelt2 * (y + 1 - crpix2))
rho = hypot(xi, eta);  c = atan(rho)
dec = asin( cos(c) sin(dec0) + eta sin(c) cos(dec0) / rho )
ra  = ra0 + atan2( xi sin(c), rho cos(dec0) cos(c) - eta sin(dec0) sin(c) )
```

At `rho = 0` the result is `(ra0, dec0)`. This was cross-checked against `astropy.wcs` and agrees to better than 1e-13 degrees. The sky to pixel direction is the standard TAN projection; any WCS library can do it from the fields above.

## 6. Compression and byte order

1. Take the raw bytes of the array in little-endian order.
2. If `shuffle` is true, reorder them so that byte 0 of every value comes first, then byte 1 of every value, and so on (for float32, four planes of `rows*cols` bytes).
3. Compress with `codec`. For zstd use independent frames with the content size written, level 3 by default.

Decoding is the reverse: decompress, unshuffle, check `raw_length` and `crc32`, then view the bytes as `Float32Array` or `Uint16Array`. The decompressed buffer must start at an aligned offset, which is the case when it is a fresh allocation.

Do not add HTTP-level compression (`Content-Encoding`) on top of this payload. It is already compressed.

## 7. Versioning and limits

- A change to the prefix layout or to the meaning of an existing field increases the **major** version. Clients must reject a major version they do not know.
- Added optional header fields or new sections increase the **minor** version. Clients ignore unknown fields and skip sections with an unknown `dtype` or `name`.
- Safety limits a decoder must enforce: header length at most 1 MiB, any section `raw_length` at most 256 MiB, and `compressed_length` and `offset` staying inside the received bytes.

## 8. HTTP usage

- `Content-Type: application/x-stardrift-cutout`
- `ETag` and `Cache-Control` follow ticket C1. The data release is part of the cache key.
- Errors are returned as `application/json` using the shared error format, never as this binary format.

## 9. Browser decoding sketch (untested)

```ts
import { decompress as unzstd } from "fzstd";

async function gunzip(b: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([b]).stream().pipeThrough(new DecompressionStream("gzip"));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}
function unshuffle(src: Uint8Array, itemsize: number): Uint8Array {
  const n = src.length / itemsize, out = new Uint8Array(src.length);
  for (let b = 0; b < itemsize; b++) for (let i = 0; i < n; i++) out[i * itemsize + b] = src[b * n + i];
  return out;
}
export async function decodeCutout(buf: ArrayBuffer) {
  const dv = new DataView(buf);
  if (String.fromCharCode(...new Uint8Array(buf, 0, 4)) !== "SDCO") throw new Error("bad magic");
  if (dv.getUint8(4) !== 1) throw new Error("unsupported major version");
  const hlen = dv.getUint32(8, true);
  if (hlen > (1 << 20) || 12 + hlen > buf.byteLength) throw new Error("bad header length");
  const header = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 12, hlen)));
  const base = 12 + hlen, arrays: Record<string, Float32Array | Uint16Array> = {};
  for (const s of header.sections) {
    if (s.dtype !== "float32" && s.dtype !== "uint16") continue;
    let bytes = new Uint8Array(buf, base + s.offset, s.compressed_length);
    if (s.codec === "zstd") bytes = unzstd(bytes);
    else if (s.codec === "gzip") bytes = await gunzip(bytes);
    const itemsize = s.dtype === "float32" ? 4 : 2;
    const raw = s.shuffle ? unshuffle(bytes, itemsize) : bytes.slice();
    if (raw.length !== s.raw_length) throw new Error("bad section length");
    arrays[s.name] = s.dtype === "float32" ? new Float32Array(raw.buffer) : new Uint16Array(raw.buffer);
  }
  return { header, arrays };
}
```

The CRC-32 check is left out of the sketch to keep it short. Add it if corrupted transfers matter to you.

## 10. Test vector

`testvector-v1.sdco` is a synthetic payload (not real SPHEREx data): 97 rows by 129 columns, with an `image`, a `flags` and a `frames` section, zstd with shuffle, 39,814 bytes. It contains a block of `NaN` pixels at the south-east... see below, a streak and a small cluster of flagged pixels, and a coverage gap where `frames` is 0.

Layout facts the browser decoder must reproduce (all repeated in `testvector-v1.expected.json`):

- Shape is 97 rows by 129 columns (a swapped width and height is the most likely bug).
- The `NaN` block is the first 6 rows and the first 10 columns of the arrays, that is the **south, column 0 side**.
- 60 `NaN` pixels in total, 79 non-zero flag pixels.
- Sample pixel values at listed (row, column) positions, the section CRC-32 values, and the sky coordinates of the centre and four corners (tolerance 1e-9 degrees). With the grid centred on RA 150.12, Dec 2.34, the south-east corner is at about RA 150.2303, Dec 2.2573 and the north-west at about RA 150.0097, Dec 2.4227 (column 0 is the high-RA side).

Acceptance for S4, as written in the ticket: this document in `docs/`, a Python encoder with a unit test (`python sdcutout.py selftest`), and the test vector for the browser decoder.

## 11. Open items to confirm in the data spike (S2)

1. **Units** of the image values in the source files (`units.image`).
2. **Flag bit definitions**, their names, and which bits mean "exclude from coadd". Fill in `flags.definitions_version`.
3. **How `frames_combined` should be reported** when different pixels have different numbers of contributing frames. The per-pixel `frames` section is the exact answer. The header value is a typical number for display.
4. **Whether the native pixel scale really is about 6.2 arcseconds** for the grid default.
5. **Payload sizes on real data.** On the synthetic test vector the payload is about 40% of the raw size, but noise-dominated real data may compress less. Measure it and set the field-of-view limit from the result.