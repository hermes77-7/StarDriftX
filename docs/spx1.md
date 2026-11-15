# SPX1 cutout format, version 1.0

A binary cutout has media type `application/octet-stream`. It contains the following sections in order.

| Section | Encoding |
| --- | --- |
| Magic | Four ASCII bytes `SPX1` |
| Header | Four-byte length followed by that many bytes of UTF-8 JSON |
| Image | Four-byte length followed by one gzip stream of IEEE 754 float32 brightness values |
| Flags, when present | Four-byte length followed by one gzip stream of unsigned 32-bit quality words |

Lengths are unsigned big-endian integers that count the following bytes, including the compressed bytes for the two array sections. Each decompressed array uses little-endian values in row-major order, with exactly `height × width` elements from `wcs.shape`. Rows follow FITS pixel order. Missing brightness samples use NaN in the binary image and `null` in a JSON cutout. Each flag word retains its full bit pattern, including QR3 bit 28 (`HALO`). The optional flag section is absent when the image section ends at EOF.

The header declares `version: "1.0"`, data release, brightness units, WCS and shape, wavelength, and exposure count. It can also include epoch, observation range in UTC MJD, band and detector information, and flag summary. [OpenAPI schema](../shared/openapi.yaml)

Live cutouts identify their source as `irsa`. The header lists the contributing collections, exposure IDs, processing versions, and UTC MJD times. It also records the fraction of the grid with coverage or quality bits, whether masking and photometric normalization were applied, and warnings for incomplete upstream downloads.

The Python encoder and the browser decoder share the [Scope test vector](../shared/fixtures/mock/cutout_scope.bin). Its flags are synthetic and include a pixel with both the known-source and halo bits. Its brightness samples come from the real Scope coadd. [Fixture generator](../scripts/generate_mock_fixtures.py)
