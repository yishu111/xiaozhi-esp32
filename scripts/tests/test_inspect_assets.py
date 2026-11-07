"""Assets parser keeps wake compatibility to names and hashes."""

import importlib.util
import json
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "inspect_assets", ROOT / "scripts/inspect_assets.py"
)
inspect_assets = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = inspect_assets
SPEC.loader.exec_module(inspect_assets)


def pack_srmodels(models):
    """models: list of (name, [(filename, bytes)])."""
    file_count = sum(len(files) for _name, files in models)
    header_len = 4 + len(models) * (32 + 4) + file_count * (32 + 4 + 4)
    header = struct.pack("<I", len(models))
    body = b""
    for name, files in models:
        header += name.encode("ascii").ljust(32, b"\x00")
        header += struct.pack("<I", len(files))
        for filename, contents in files:
            header += filename.encode("ascii").ljust(32, b"\x00")
            header += struct.pack("<II", header_len + len(body), len(contents))
            body += contents
    assert len(header) == header_len
    return header + body


def pack_assets(files):
    entry = struct.Struct("<32sIIHH")
    table = b""
    merged = b""
    for name, contents in files:
        table += entry.pack(name.encode("ascii"), len(contents), len(merged), 0, 0)
        merged += b"ZZ" + contents
    payload = table + merged
    checksum = sum(payload) & 0xFFFF
    return struct.pack("<III", len(files), checksum, len(payload)) + payload


class InspectAssetsTests(unittest.TestCase):
    def test_wake_model_is_compatible_and_multinet_is_not(self):
        info = "wakenet9_tts_nihaoxiaozhi\n".encode()
        good = pack_assets([
            ("index.json", json.dumps({"version": 1, "srmodels": "srmodels.bin"}).encode()),
            ("srmodels.bin", pack_srmodels([
                ("wn9_nihaoxiaozhi_tts", [("_MODEL_INFO_", info), ("wn9_data", b"weights")]),
            ])),
        ])
        report = inspect_assets.inspect_assets(good)
        self.assertTrue(report["wake_compatible_with_this_app"])
        self.assertEqual(report["models"][0]["files"][0]["info"], "wakenet9_tts_nihaoxiaozhi")
        mixed = pack_assets([
            ("index.json", json.dumps({"version": 1, "srmodels": "srmodels.bin"}).encode()),
            ("srmodels.bin", pack_srmodels([
                ("wn9_nihaoxiaozhi_tts", [("wn9_data", b"weights")]),
                ("mn7_cn", [("mn7_data", b"commands")]),
            ])),
        ])
        self.assertFalse(inspect_assets.inspect_assets(mixed)["wake_compatible_with_this_app"])
        newer = pack_assets([
            ("index.json", json.dumps({"version": 2, "srmodels": "srmodels.bin"}).encode()),
            ("srmodels.bin", pack_srmodels([
                ("wn9_nihaoxiaozhi_tts", [("wn9_data", b"weights")]),
            ])),
        ])
        self.assertFalse(inspect_assets.inspect_assets(newer)["wake_compatible_with_this_app"])

    def test_generated_cloud_assets_keep_only_the_baseline_wake_model(self):
        path = ROOT / "build/generated_assets.bin"
        if not path.exists():
            self.skipTest("cloud assets image is not in this checkout")
        report = inspect_assets.inspect_assets(path.read_bytes())
        self.assertTrue(report["wake_compatible_with_this_app"])
        self.assertEqual([model["name"] for model in report["models"]], ["wn9_nihaoxiaozhi_tts"])
        self.assertEqual(report["index_version"], 1)
        self.assertEqual(report["srmodels_file"], "srmodels.bin")
        names = {item["name"] for item in report["files"]}
        self.assertIn("index.json", names)
        self.assertNotIn("wifi", names)
