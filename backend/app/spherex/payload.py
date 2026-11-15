"""SPX1 encoder and decoder. See docs/spx1.md for the byte layout."""

import gzip
import json
import struct

MAGIC = b"SPX1"


def _shape(header: dict) -> tuple[int, int]:
    shape = header.get("wcs", {}).get("shape")
    if (
        header.get("version") != "1.0" or not isinstance(shape, (list, tuple)) or len(shape) != 2
        or any(type(size) is not int or size <= 0 for size in shape)
    ):
        raise ValueError("Expected a version 1.0 header with positive image dimensions.")
    return shape[0], shape[1]


def encode_payload(
    header: dict, data: list[list[float]], flags: list[list[int]] | None = None,
) -> bytes:
    """Pack row-major float32 pixels and optional uint32 quality flags."""
    height, width = _shape(header)
    sections = [json.dumps(header, allow_nan=False).encode("utf-8")]
    for plane, value_type in ((data, "f"), (flags, "I")):
        if plane is None:
            continue
        if len(plane) != height or any(len(row) != width for row in plane):
            raise ValueError("Payload plane does not match the declared image dimensions.")
        packed = struct.pack(f"<{height * width}{value_type}", *(v for row in plane for v in row))
        sections.append(gzip.compress(packed, compresslevel=6, mtime=0))
    return MAGIC + b"".join(struct.pack(">I", len(section)) + section for section in sections)


def decode_payload(
    payload: bytes,
) -> tuple[dict, list[list[float]], list[list[int]] | None]:
    """Decode an SPX1 header, image, and optional flags."""
    if payload[:4] != MAGIC:
        raise ValueError("Invalid magic bytes, expected SPX1.")
    offset = 4

    def section() -> bytes:
        nonlocal offset
        if offset + 4 > len(payload):
            raise ValueError("Truncated SPX1 section length.")
        (length,) = struct.unpack_from(">I", payload, offset)
        offset += 4
        end = offset + length
        if end > len(payload):
            raise ValueError("Truncated SPX1 section.")
        part = payload[offset:end]
        offset = end
        return part

    header = json.loads(section().decode("utf-8"))
    height, width = _shape(header)

    def plane(value_type: str) -> list[list]:
        pixel_bytes = gzip.decompress(section())
        if len(pixel_bytes) != height * width * 4:
            raise ValueError("Payload plane does not match the declared image dimensions.")
        values = struct.unpack(f"<{height * width}{value_type}", pixel_bytes)
        return [list(values[i * width : (i + 1) * width]) for i in range(height)]

    data = plane("f")
    flags = plane("I") if offset < len(payload) else None
    if offset != len(payload):
        raise ValueError("Unexpected bytes after SPX1 payload.")
    return header, data, flags
