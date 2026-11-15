#!/usr/bin/env python3
"""Reference encoder/decoder for the Star Drift cutout payload, format 1.0.

See payload-format-v1.md for the specification. Requires numpy; zstd needs the
`zstandard` package (pip install zstandard). Without it, use codec="gzip".

    python sdcutout.py make-testvector OUTDIR   # writes the test vector files
    python sdcutout.py selftest                 # round-trip checks
"""
import gzip
import hashlib
import json
import math
import struct
import sys
import zlib

import numpy as np

try:
    import zstandard
except ImportError:  # gzip still works
    zstandard = None

MAGIC = b"SDCO"
VERSION = (1, 0)
PREFIX = struct.Struct("<4sBBHI")  # magic, major, minor, reserved, header_len (12 bytes)
MAX_HEADER = 1 << 20          # 1 MiB
MAX_SECTION_RAW = 256 << 20   # 256 MiB
DTYPES = {"float32": "<f4", "uint16": "<u2"}


# ---------- helpers ----------
def shuffle(raw: bytes, itemsize: int) -> bytes:
    a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, itemsize)
    return a.T.copy().tobytes()


def unshuffle(data: bytes, itemsize: int) -> bytes:
    a = np.frombuffer(data, dtype=np.uint8).reshape(itemsize, -1)
    return a.T.copy().tobytes()


def compress(raw: bytes, codec: str, level: int) -> bytes:
    if codec == "none":
        return raw
    if codec == "gzip":
        return gzip.compress(raw, compresslevel=min(level, 9), mtime=0)
    if codec == "zstd":
        if zstandard is None:
            raise RuntimeError("pip install zstandard, or use codec='gzip'")
        return zstandard.ZstdCompressor(level=level, write_content_size=True).compress(raw)
    raise ValueError("unknown codec " + codec)


def decompress(data: bytes, codec: str, raw_length: int) -> bytes:
    if codec == "none":
        return data
    if codec == "gzip":
        out = gzip.decompress(data)
    elif codec == "zstd":
        if zstandard is None:
            raise RuntimeError("pip install zstandard to decode zstd sections")
        out = zstandard.ZstdDecompressor().decompress(data, max_output_size=raw_length)
    else:
        raise ValueError("unknown codec " + codec)
    return out


# ---------- grid and WCS ----------
def make_wcs(ra, dec, shape, scale_arcsec=6.2):
    """North-up gnomonic (TAN) grid. shape = (height, width). FITS conventions:
    1-based CRPIX, row 0 is the SOUTH edge, x increases toward the WEST."""
    h, w = shape
    s = scale_arcsec / 3600.0
    return {"ctype": ["RA---TAN", "DEC--TAN"], "crval": [ra, dec],
            "crpix": [(w + 1) / 2.0, (h + 1) / 2.0], "cdelt": [-s, s],
            "pc": [[1.0, 0.0], [0.0, 1.0]], "lonpole": 180.0}


def grid_size(fov_deg, scale_arcsec=6.2):
    return math.ceil(fov_deg * 3600.0 / scale_arcsec)


def pix_to_sky(wcs, x, y):
    """0-based pixel (x = column, y = row) -> (RA, Dec) in degrees."""
    xi = math.radians(wcs["cdelt"][0] * (x + 1 - wcs["crpix"][0]))
    eta = math.radians(wcs["cdelt"][1] * (y + 1 - wcs["crpix"][1]))
    a0, d0 = math.radians(wcs["crval"][0]), math.radians(wcs["crval"][1])
    rho = math.hypot(xi, eta)
    if rho == 0:
        return wcs["crval"][0], wcs["crval"][1]
    c = math.atan(rho)
    dec = math.asin(math.cos(c) * math.sin(d0) + eta * math.sin(c) * math.cos(d0) / rho)
    ra = a0 + math.atan2(
        xi * math.sin(c), rho * math.cos(d0) * math.cos(c) - eta * math.sin(d0) * math.sin(c),
    )
    return math.degrees(ra) % 360.0, math.degrees(dec)


# ---------- encode / decode ----------
def encode(header: dict, arrays: dict, codec="zstd", level=3, do_shuffle=True) -> bytes:
    """arrays: name -> 2-D numpy array (float32 or uint16), all the same shape.
    'image' is required. Row 0 is the south edge."""
    if "image" not in arrays:
        raise ValueError("'image' section is required")
    shape = list(arrays["image"].shape)
    header = dict(header)
    header["grid"] = dict(header.get("grid", {}), shape=shape)
    metas, blobs, offset = [], [], 0
    for name, arr in arrays.items():
        if list(arr.shape) != shape:
            raise ValueError("section %s has shape %s, expected %s" % (name, arr.shape, shape))
        dt = {"float32": "float32", "uint16": "uint16"}[arr.dtype.name]
        raw = np.ascontiguousarray(arr.astype(DTYPES[dt])).tobytes()
        itemsize = np.dtype(DTYPES[dt]).itemsize
        body = shuffle(raw, itemsize) if do_shuffle else raw
        blob = compress(body, codec, level)
        metas.append({"name": name, "dtype": dt, "shape": shape, "codec": codec,
                      "shuffle": bool(do_shuffle), "offset": offset,
                      "compressed_length": len(blob), "raw_length": len(raw),
                      "crc32": zlib.crc32(raw) & 0xFFFFFFFF})
        blobs.append(blob)
        offset += len(blob)
    header["sections"] = metas
    hjson = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(hjson) > MAX_HEADER:
        raise ValueError("header too large")
    return PREFIX.pack(MAGIC, VERSION[0], VERSION[1], 0, len(hjson)) + hjson + b"".join(blobs)


def decode(buf: bytes):
    """Returns (header dict, {name: numpy array}). Raises ValueError on bad input."""
    if len(buf) < PREFIX.size:
        raise ValueError("payload too short")
    magic, major, minor, _reserved, hlen = PREFIX.unpack_from(buf, 0)
    if magic != MAGIC:
        raise ValueError("bad magic")
    if major != VERSION[0]:
        raise ValueError("unsupported major version %d" % major)
    if hlen > MAX_HEADER or PREFIX.size + hlen > len(buf):
        raise ValueError("bad header length")
    header = json.loads(buf[PREFIX.size:PREFIX.size + hlen].decode("utf-8"))
    data = memoryview(buf)[PREFIX.size + hlen:]
    out = {}
    for s in header["sections"]:
        if s["dtype"] not in DTYPES:
            continue  # unknown dtype from a newer minor version: skip
        if s["raw_length"] > MAX_SECTION_RAW:
            raise ValueError("section too large")
        blob = bytes(data[s["offset"]:s["offset"] + s["compressed_length"]])
        if len(blob) != s["compressed_length"]:
            raise ValueError("section %s truncated" % s["name"])
        body = decompress(blob, s["codec"], s["raw_length"])
        itemsize = np.dtype(DTYPES[s["dtype"]]).itemsize
        raw = unshuffle(body, itemsize) if s["shuffle"] else body
        if len(raw) != s["raw_length"]:
            raise ValueError("section %s has wrong length" % s["name"])
        if (zlib.crc32(raw) & 0xFFFFFFFF) != s["crc32"]:
            raise ValueError("section %s failed its checksum" % s["name"])
        h, w = s["shape"]
        out[s["name"]] = np.frombuffer(raw, dtype=DTYPES[s["dtype"]]).reshape(h, w)
    return header, out


# ---------- test vector ----------
def build_test_vector():
    h, w = 97, 129  # deliberately non-square and odd
    rng = np.random.default_rng(20261112)
    yy, xx = np.mgrid[0:h, 0:w]
    img = 4.0 + 0.01 * xx + 0.02 * yy + rng.normal(0, 0.3, (h, w))
    for _ in range(25):
        cx, cy, f = rng.uniform(5, w - 5), rng.uniform(5, h - 5), rng.uniform(20, 400)
        distance_squared = (xx - cx) ** 2 + (yy - cy) ** 2
        img += f / (2 * math.pi * 1.2 ** 2) * np.exp(-distance_squared / (2 * 1.2 ** 2))
    img = img.astype(np.float32)
    img[0:6, 0:10] = np.nan                       # no coverage in the south-west corner
    flags = np.zeros((h, w), np.uint16)
    flags[40, 20:90] |= 1 << 3                    # a streak along one row
    flags[10:13, 100:103] |= 1 << 1               # a small cluster
    frames = np.full((h, w), 6, np.uint16)
    frames[0:6, 0:10] = 0
    wcs = make_wcs(150.12, 2.34, (h, w))
    header = {
        "format": "stardrift-cutout", "version": "1.0",
        "notes": "Synthetic test vector. Not real SPHEREx data.",
        "request": {"ra": 150.12, "dec": 2.34, "fov_deg": 0.2, "epoch": "2",
                    "mode": "single", "band": 42, "mask": False},
        "grid": {"wcs": wcs, "pixel_scale_arcsec": 6.2, "row_order": "south_first"},
        "spectral": {"mode": "single", "bands": [42], "detectors": [3],
                     "wavelength_um": {"min": 2.454, "max": 2.496, "effective": 2.475}},
        "time": {"epoch": 2, "mjd_start": 61000.0, "mjd_end": 61004.0},
        "exposure_count": 3, "frames_combined": 6,
        "exposures": [{"id": "synthetic-0001", "mjd": 61000.5, "detector": 3},
                      {"id": "synthetic-0002", "mjd": 61002.1, "detector": 3},
                      {"id": "synthetic-0003", "mjd": 61003.8, "detector": 3}],
        "processing": {"coadd": "mean", "flagged_excluded": False,
                       "photometric_normalisation": "none", "background_subtracted": False,
                       "resampling": {"science": "flux_conserving", "flags": "bitwise_or"}},
        "units": {"image": "arbitrary"},
        "flags": {"masked": False, "definitions_version": "synthetic",
                  "present": [
                      {"name": "streak", "bit": 3,
                       "pixel_count": int(((flags >> 3) & 1).sum())},
                      {"name": "cluster", "bit": 1,
                       "pixel_count": int(((flags >> 1) & 1).sum())},
                  ]},
        "data_release": "synthetic-0", "server_version": "reference-1.0",
    }
    return header, {"image": img, "flags": flags, "frames": frames}


def make_testvector(outdir):
    import os
    header, arrays = build_test_vector()
    payload = encode(header, arrays, codec="zstd" if zstandard else "gzip")
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "testvector-v1.sdco"), "wb") as f:
        f.write(payload)
    hdr, got = decode(payload)
    img = got["image"]
    pts = [(0, 0), (5, 9), (6, 10), (48, 64), (40, 50), (96, 128), (11, 101)]
    wcs = hdr["grid"]["wcs"]
    expected = {
        "file": "testvector-v1.sdco", "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload), "codec": hdr["sections"][0]["codec"],
        "shape_rows_cols": list(img.shape), "nan_count": int(np.isnan(img).sum()),
        "image_sum_ignoring_nan": float(np.nansum(img.astype(np.float64))),
        "flags_nonzero_pixels": int((got["flags"] != 0).sum()),
        "section_crc32": {s["name"]: s["crc32"] for s in hdr["sections"]},
        "image_samples": [
            {"row": r, "col": c, "value": None if np.isnan(img[r, c]) else float(img[r, c])}
            for r, c in pts
        ],
        "flag_samples": [{"row": 40, "col": 50, "value": int(got["flags"][40, 50])},
                         {"row": 11, "col": 101, "value": int(got["flags"][11, 101])}],
        "sky_samples": [
            {"row": r, "col": c, "ra": pix_to_sky(wcs, c, r)[0],
             "dec": pix_to_sky(wcs, c, r)[1]}
                        for r, c in [(48, 64), (0, 0), (96, 128), (0, 128), (96, 0)]],
        "tolerance_deg": 1e-9,
    }
    with open(os.path.join(outdir, "testvector-v1.expected.json"), "w") as f:
        json.dump(expected, f, indent=2)
    print("wrote", outdir, "-", len(payload), "bytes,", expected["codec"])


def selftest():
    header, arrays = build_test_vector()
    codecs = ["none", "gzip"] + (["zstd"] if zstandard else [])
    for codec in codecs:
        for sh in (True, False):
            hdr, got = decode(encode(header, arrays, codec=codec, do_shuffle=sh))
            for name, arr in arrays.items():
                assert got[name].dtype.kind == arr.dtype.kind
                assert np.array_equal(got[name], arr, equal_nan=True), (codec, sh, name)
    good = encode(header, arrays, codec="gzip")
    for bad in (b"XXXX" + good[4:], good[:20], good[:-5] + b"\x00" * 5):
        try:
            decode(bad)
        except Exception:
            continue
        raise AssertionError("corrupt payload was accepted")
    assert pix_to_sky(make_wcs(150.12, 2.34, (97, 129)), 64, 48) == (150.12, 2.34)
    print("selftest ok:", ", ".join(codecs))


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "selftest":
        selftest()
    elif len(sys.argv) == 3 and sys.argv[1] == "make-testvector":
        make_testvector(sys.argv[2])
    else:
        print(__doc__)
