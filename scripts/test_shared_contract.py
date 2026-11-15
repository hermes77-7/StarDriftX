"""Unit tests for shared payload encoder/decoder and fixture contracts.
Can be executed with standard Python 3 library without external dependencies.
"""

import json
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from shared.payload import decode_payload, encode_payload

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "shared" / "fixtures" / "mock"


class TestSharedContract(unittest.TestCase):
    def test_payload_roundtrip(self):
        header = {
            "version": "1.0",
            "data_release": "spherex_qr3",
            "wcs": {"shape": [2, 2], "crval": [150.12, 2.34]},
            "bunit": "MJy/sr",
            "nexp": 2,
            "wavelength_um": 0.809,
        }
        data = [[1.25, 2.5], [3.75, 5.0]]
        flags = [[0, 1 << 28], [0, 1 << 31]]

        encoded = encode_payload(header, data, flags)
        decoded_header, decoded_data, decoded_flags = decode_payload(encoded)

        self.assertEqual(decoded_header["version"], "1.0")
        self.assertEqual(decoded_header["wavelength_um"], 0.809)
        self.assertAlmostEqual(decoded_data[0][0], 1.25, places=4)
        self.assertAlmostEqual(decoded_data[1][1], 5.0, places=4)
        self.assertEqual(decoded_flags, flags)

    def test_mock_fixtures_exist_and_parse(self):
        # 1. Meta
        with open(FIXTURE_DIR / "meta.json") as f:
            meta = json.load(f)
            self.assertEqual(meta["data_release"], "spherex_qr3")
            self.assertEqual(meta["supported_bands"]["total_bands"], 102)
            self.assertEqual(meta["supported_bands"]["detectors"], 6)
            origin = datetime(1858, 11, 17, tzinfo=UTC)
            for entry in meta["epochs"]:
                for bound in ("start", "end"):
                    times = entry["time_range"]
                    self.assertEqual(
                        origin + timedelta(days=times[f"mjd_{bound}"]),
                        datetime.fromisoformat(times[f"utc_{bound}"]),
                    )

        # 2. Cutout JSON
        with open(FIXTURE_DIR / "cutout_scope.json") as f:
            cutout = json.load(f)
            self.assertEqual(cutout["header"]["wcs"]["shape"], [36, 36])
            self.assertEqual(len(cutout["data"]), 36)

        # 3. Cutout Binary SPX1
        with open(FIXTURE_DIR / "cutout_scope.bin", "rb") as f:
            raw_bin = f.read()
            h, d, fl = decode_payload(raw_bin)
            self.assertEqual(h["version"], "1.0")
            self.assertEqual(len(d), 36)
            self.assertEqual(len(d[0]), 36)
            self.assertIsNotNone(fl)
            self.assertEqual(fl[16][16], (1 << 21) | (1 << 28))
            reencoded = encode_payload(h, d, fl)
            self.assertEqual(decode_payload(reencoded), (h, d, fl))

        # 4. Flags
        with open(FIXTURE_DIR / "flags_scope.json") as f:
            flags = json.load(f)
            self.assertEqual(flags["total_pixels"], 1296)
            self.assertEqual(h["flags_summary"], flags["flag_summary"])

        # 5. Spectrum
        with open(FIXTURE_DIR / "spectrum_scope.json") as f:
            spectrum = json.load(f)
            self.assertEqual(len(spectrum["points"]), 102)

        # 6. Catalog
        with open(FIXTURE_DIR / "catalog_scope.json") as f:
            cat = json.load(f)
            self.assertGreater(cat["count"], 0)

        # 7. Resolve
        with open(FIXTURE_DIR / "resolve_samples.json") as f:
            resolve = json.load(f)
            self.assertIn("Vega", resolve)


if __name__ == "__main__":
    unittest.main()
