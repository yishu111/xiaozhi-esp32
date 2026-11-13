import importlib.util
import json
import re
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "build_default_assets", ROOT / "scripts" / "build_default_assets.py"
)
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


class BuildDefaultAssetsTest(unittest.TestCase):
    def test_text_font_metadata_uses_bundle_charset_size_and_bpp(self):
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory)
            BUILD.generate_index_json(
                str(assets),
                None,
                "font_noto_sans_common_20_4.bin",
                None,
                font_bundle_id="noto-v1",
            )
            index = json.loads((assets / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(
                index["text_font_meta"],
                {"charset": "common", "size": 20, "bpp": 4, "bundle": "noto-v1"},
            )

    def test_text_font_requires_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                BUILD.generate_index_json(
                    directory, None, "font_noto_sans_common_20_4.bin", None
                )

    def test_max_size_rejects_oversized_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extra = root / "extra"
            extra.mkdir()
            (extra / "big.bin").write_bytes(b"\x5a" * 4096)
            output = root / "assets.bin"

            ok = BUILD.build_assets_integrated(
                None,
                None,
                None,
                None,
                str(extra),
                str(output),
                max_size=100,
            )
            self.assertFalse(ok)

    def test_max_size_allows_fitting_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extra = root / "extra"
            extra.mkdir()
            (extra / "small.bin").write_bytes(b"hello")
            output = root / "assets.bin"

            ok = BUILD.build_assets_integrated(
                None,
                None,
                None,
                None,
                str(extra),
                str(output),
                max_size=64 * 1024,
            )
            self.assertTrue(ok)
            self.assertTrue(output.exists())
            self.assertLessEqual(output.stat().st_size, 64 * 1024)

    def test_wakenet10_copy_keeps_s3_p1_slice(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "wn10_nihaoxiaozhi"
            dst = Path(directory) / "out"
            src.mkdir()
            (src / "_MODEL_INFO_p1").write_text("p1-info", encoding="utf-8")
            (src / "_MODEL_INFO_p2").write_text("p2-info", encoding="utf-8")
            (src / "wn10_data_p1").write_bytes(b"s3-data")
            (src / "wn10_data_p2").write_bytes(b"p4-data")
            (src / "extra.bin").write_bytes(b"keep")

            self.assertTrue(BUILD.copy_wakenet_model(str(src), str(dst), "esp32s3"))
            self.assertEqual((dst / "wn10_data").read_bytes(), b"s3-data")
            self.assertEqual((dst / "_MODEL_INFO_").read_text(encoding="utf-8"), "p1-info")
            self.assertEqual((dst / "extra.bin").read_bytes(), b"keep")
            self.assertFalse((dst / "wn10_data_p1").exists())
            self.assertFalse((dst / "wn10_data_p2").exists())

    def test_wakenet10_copy_keeps_p4_p2_slice(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "wn10_nihaoxiaozhi"
            dst = Path(directory) / "out"
            src.mkdir()
            (src / "_MODEL_INFO_p2").write_text("p2-info", encoding="utf-8")
            (src / "wn10_data_p2").write_bytes(b"p4-data")

            self.assertTrue(BUILD.copy_wakenet_model(str(src), str(dst), "esp32p4"))
            self.assertEqual((dst / "wn10_data").read_bytes(), b"p4-data")
            self.assertEqual((dst / "_MODEL_INFO_").read_text(encoding="utf-8"), "p2-info")

    def test_wakenet10_copy_rejects_unknown_target(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "wn10_nihaoxiaozhi"
            src.mkdir()
            (src / "wn10_data_p1").write_bytes(b"s3-data")
            with self.assertRaises(ValueError):
                BUILD.copy_wakenet_model(str(src), str(Path(directory) / "out"), "esp32c3")

    def test_esp_hi_emoji_pack_matches_runtime_assets(self):
        """ESP-HI must only pack AAF files that emoji_display actually loads."""
        cmake = (ROOT / "main" / "CMakeLists.txt").read_text(encoding="utf-8")
        display = (
            ROOT / "main" / "boards" / "espressif" / "esp-hi" / "emoji_display.cc"
        ).read_text(encoding="utf-8")

        # Extract FILES_TO_DOWNLOAD block for ESP_HI.
        match = re.search(
            r"if\(CONFIG_BOARD_TYPE_ESP_HI\).*?set\(FILES_TO_DOWNLOAD(.*?)\)\n",
            cmake,
            re.S,
        )
        self.assertIsNotNone(match, "ESP_HI FILES_TO_DOWNLOAD not found in CMakeLists")
        cmake_files = set(re.findall(r'"([^"]+\.aaf)"', match.group(1)))

        runtime_files = set(re.findall(r'"([^"]+\.aaf)"', display))
        self.assertTrue(runtime_files, "No AAF names found in emoji_display.cc")
        self.assertEqual(
            cmake_files,
            runtime_files,
            "ESP-HI CMake AAF pack list must match emoji_display runtime assets",
        )
        self.assertRegex(
            cmake,
            r"image_player/raw/[0-9a-f]{40}/test_apps/test_8bit",
            "ESP-HI emoji URL should be pinned to a full image_player commit SHA",
        )


class ConfiguredAssetsTest(unittest.TestCase):
    def base_pack(self, root, name_length=32):
        index = {"version": 1, "srmodels": "old-speech.bin", "text_font": "font.bin",
                 "emoji_collection": [{"name": "happy", "file": "image.bin"}],
                 "layout": "layout.json", "multinet_model": {"commands": ["old"]}}
        files = {
            "index.json": (json.dumps(index).encode(), 0, 0),
            "old-speech.bin": (b"old Chinese model", 0, 0),
            "font.bin": (b"opaque font bytes", 0, 0),
            "image.bin": (b"opaque image bytes", 284, 240),
            "layout.json": (b'{"keep":"layout"}', 0, 0),
        }
        table, data = bytearray(), bytearray()
        for name, (contents, width, height) in files.items():
            table.extend(struct.pack(f"<{name_length}sIIHH", name.encode(), len(contents),
                                     len(data), width, height))
            data.extend(b"ZZ" + contents)
        payload = table + data
        base = root / "base.bin"
        base.write_bytes(struct.pack("<III", len(files), sum(payload) & 0xFFFF, len(payload)) + payload)
        return base, files

    def run_builder(self, root, model=None, name_length=32, max_size=None):
        sdkconfig = root / "sdkconfig"
        sdkconfig.write_text(
            'CONFIG_IDF_TARGET="esp32s3"\n' + (
                f"CONFIG_USE_AFE_WAKE_WORD=y\nCONFIG_SR_WN_{model.upper()}=y\n"
                if model else "CONFIG_WAKE_WORD_DISABLED=y\n"
            ), encoding="utf-8",
        )
        if model:
            model_dir = root / "models/wakenet_model" / model
            model_dir.mkdir(parents=True)
            (model_dir / "_MODEL_INFO_").write_bytes(b"model info")
            (model_dir / "weights").write_bytes(b"selected model weights")
        args = [sys.executable, str(ROOT / "scripts/build_default_assets.py"),
                "--sdkconfig", str(sdkconfig), "--base_assets", str(root / "base.bin"),
                "--output", str(root / "configured.bin"),
                "--esp_sr_model_path", str(root / "models"),
                "--name_length", str(name_length)]
        if max_size is not None:
            args.extend(["--max_size", str(max_size)])
        return subprocess.run(args, text=True, capture_output=True)

    def test_cli_replaces_models_and_preserves_custom_ui(self):
        for name_length, model in ((16, "wn9s_hiesp"), (32, "wn9_hiesp"),
                                   (64, "wn9s_nihaoxiaozhi")):
            with self.subTest(name_length=name_length), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                base, original = self.base_pack(root, name_length)
                before = base.read_bytes()
                result = self.run_builder(root, model, name_length)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                files = BUILD.read_assets_entries(root / "configured.bin", name_length)
                self.assertEqual(base.read_bytes(), before)
                for resource in ("font.bin", "image.bin", "layout.json"):
                    self.assertEqual(files[resource], original[resource])
                self.assertNotIn("old-speech.bin", files)
                index = json.loads(files["index.json"][0])
                original_index = json.loads(original["index.json"][0])
                self.assertEqual(index["emoji_collection"], original_index["emoji_collection"])
                self.assertEqual(index["text_font"], "font.bin")
                self.assertEqual(index["layout"], "layout.json")
                self.assertNotIn("multinet_model", index)
                models = files[index["srmodels"]][0]
                self.assertEqual(struct.unpack_from("<I", models)[0], 1)
                self.assertEqual(models[4:36].rstrip(b"\0").decode(), model)
                self.assertIn(b"selected model weights", models)
                self.assertNotIn(b"old Chinese model", models)

    def test_cli_disabled_removes_prebuilt_models_and_preserves_ui(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, original = self.base_pack(root)
            result = self.run_builder(root)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            files = BUILD.read_assets_entries(root / "configured.bin")
            self.assertNotIn("old-speech.bin", files)
            self.assertNotIn("srmodels.bin", files)
            self.assertNotIn("srmodels", json.loads(files["index.json"][0]))
            self.assertEqual(files["image.bin"], original["image.bin"])

    def test_cli_rejects_assets_that_exceed_partition_size(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.base_pack(root)
            result = self.run_builder(root, "wn9_hiesp", max_size=100)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("exceeds partition limit", result.stderr)
            self.assertFalse((root / "configured.bin").exists())

    def test_reader_rejects_corrupt_and_truncated_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base, _ = self.base_pack(root)
            valid = base.read_bytes()
            for corrupt in (b"short", valid[:-1], valid[:4] + b"\0" * 4 + valid[8:]):
                with self.subTest(corrupt=corrupt[:12]):
                    base.write_bytes(corrupt)
                    with self.assertRaises(ValueError):
                        BUILD.read_assets_entries(base)

    def test_cli_rejects_enabled_wake_word_without_a_selected_model(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.base_pack(root)
            self.run_builder(root)
            sdkconfig = root / "sdkconfig"
            sdkconfig.write_text("CONFIG_USE_AFE_WAKE_WORD=y\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/build_default_assets.py"),
                 "--sdkconfig", str(sdkconfig), "--output", str(root / "unused.bin")],
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("requires selected, available", result.stderr)
            self.assertFalse((root / "unused.bin").exists())


class WakeCommandAssetsTest(unittest.TestCase):
    COMMANDS = ROOT / "main/boards/bread-compact-wifi-lcd/gc9a01_wake_commands.json"

    def sdkconfig(self, root, custom=True, english=False):
        path = root / "sdkconfig"
        if english:
            path.write_text(
                "\n".join([
                    'CONFIG_IDF_TARGET="esp32s3"',
                    "CONFIG_USE_CUSTOM_WAKE_WORD=y",
                    "CONFIG_USE_AFE_WAKE_WORD=n",
                    "CONFIG_SR_MN_CN_NONE=y",
                    "CONFIG_SR_MN_CN_MULTINET7_QUANT=n",
                    "CONFIG_SR_MN_EN_MULTINET7_QUANT=y",
                    "CONFIG_SR_WN_WN9_NIHAOXIAOZHI_TTS=y",
                    'CONFIG_CUSTOM_WAKE_WORD="hi taky"',
                    'CONFIG_CUSTOM_WAKE_WORD_DISPLAY="hi Taky"',
                    "CONFIG_CUSTOM_WAKE_WORD_THRESHOLD=20",
                    "",
                ]),
                encoding="utf-8",
            )
        elif custom:
            path.write_text(
                "\n".join([
                    'CONFIG_IDF_TARGET="esp32s3"',
                    "CONFIG_USE_CUSTOM_WAKE_WORD=y",
                    "CONFIG_USE_AFE_WAKE_WORD=n",
                    "CONFIG_SR_MN_CN_MULTINET7_QUANT=y",
                    'CONFIG_CUSTOM_WAKE_WORD="xiao tu dou"',
                    'CONFIG_CUSTOM_WAKE_WORD_DISPLAY="小土豆"',
                    "CONFIG_CUSTOM_WAKE_WORD_THRESHOLD=20",
                    "",
                ]),
                encoding="utf-8",
            )
        else:
            path.write_text(
                'CONFIG_IDF_TARGET="esp32s3"\n'
                "CONFIG_USE_AFE_WAKE_WORD=y\n"
                "CONFIG_SR_WN_WN9_NIHAOXIAOZHI_TTS=y\n",
                encoding="utf-8",
            )
        return path

    def fake_models(self, root, names):
        for name in names:
            model_dir = root / "models/multinet_model" / name
            model_dir.mkdir(parents=True)
            (model_dir / "weights").write_bytes(name.encode() + b"-weights")
        wakenet = root / "models/wakenet_model/wn9_nihaoxiaozhi_tts"
        wakenet.mkdir(parents=True)
        (wakenet / "_MODEL_INFO_").write_bytes(b"wn9")
        (wakenet / "wn9_data").write_bytes(b"wakenet-weights")

    def run_builder(self, root, sdkconfig, output, wake_commands=None):
        args = [
            sys.executable, str(ROOT / "scripts/build_default_assets.py"),
            "--sdkconfig", str(sdkconfig),
            "--output", str(output),
            "--esp_sr_model_path", str(root / "models"),
        ]
        if wake_commands is not None:
            args.extend(["--wake_commands", str(wake_commands)])
        return subprocess.run(args, text=True, capture_output=True)

    def test_single_kconfig_phrase_is_unchanged_without_a_variant_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fake_models(root, ["mn7_cn", "fst"])
            result = self.run_builder(root, self.sdkconfig(root), root / "assets.bin")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            index = json.loads(BUILD.read_assets_entries(root / "assets.bin")["index.json"][0])
            self.assertEqual(index["multinet_model"]["commands"], [{
                "command": "xiao tu dou",
                "text": "小土豆",
                "action": "wake",
            }])
            self.assertEqual(index["multinet_model"]["threshold"], 0.2)
            self.assertEqual(index["multinet_model"]["duration"], 3000)

    def test_gc9a01_file_packs_english_multinet_phonemes_and_wakenet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # mn7_cn is present on disk so a "=n" symbol cannot sneak it into the pack.
            self.fake_models(root, ["mn7_en", "mn7_cn", "fst"])
            result = self.run_builder(
                root, self.sdkconfig(root, english=True), root / "assets.bin", self.COMMANDS,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            files = BUILD.read_assets_entries(root / "assets.bin")
            index = json.loads(files["index.json"][0])
            spec = json.loads(self.COMMANDS.read_text(encoding="utf-8"))
            self.assertEqual(index["multinet_model"], {
                "language": "en",
                "duration": 3000,
                "threshold": 0.2,
                "commands": spec["commands"],
            })
            self.assertNotIn("xiao tu dou", files["index.json"][0].decode())
            self.assertNotIn("你好Taky", files["index.json"][0].decode())
            words = []
            for command in index["multinet_model"]["commands"]:
                if command["action"] == "wake" and command["text"] not in words:
                    words.append(command["text"])
            self.assertEqual(words, ["hi Taky"])
            self.assertEqual(
                [item["phonemes"] for item in index["multinet_model"]["commands"]],
                ["hi TdKm", "hd TdKm"],
            )
            models = files["srmodels.bin"][0]
            self.assertEqual(struct.unpack_from("<I", models)[0], 3)
            names = []
            offset = 4
            for _ in range(3):
                names.append(models[offset:offset + 32].split(b"\0", 1)[0].decode())
                file_count = struct.unpack_from("<I", models, offset + 32)[0]
                offset += 36 + file_count * 40
            self.assertCountEqual(names, ["fst", "mn7_en", "wn9_nihaoxiaozhi_tts"])
            self.assertNotIn(b"mn7_cn", models)

    def test_wake_commands_file_is_ignored_for_wakenet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fake_models(root, ["mn7_cn"])
            result = self.run_builder(
                root, self.sdkconfig(root, custom=False), root / "assets.bin", self.COMMANDS,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            index = json.loads(BUILD.read_assets_entries(root / "assets.bin")["index.json"][0])
            self.assertNotIn("multinet_model", index)
            self.assertIn("ignored because USE_CUSTOM_WAKE_WORD is disabled", result.stdout)

    def test_cmake_passes_the_command_file_only_for_gc9a01(self):
        cmake = (ROOT / "main/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn(
            "CONFIG_BOARD_TYPE_BREAD_COMPACT_WIFI_LCD AND CONFIG_LCD_GC9A01_240X240",
            cmake,
        )
        self.assertIn("gc9a01_wake_commands.json", cmake)
        self.assertEqual(cmake.count("--wake_commands"), 2)

    def test_hi_taky_phonemes_are_teiki_not_tacky(self):
        # Same single-character classes as esp-sr tool/multinet_g2p.py.
        alphabet = {
            "HH": "h", "AY1": "i", "T": "T", "EY1": "d", "AE1": "a", "K": "K", "IY1": "m",
        }

        def encode(phones):
            return "".join(alphabet[phone] for phone in phones)

        hi = encode(["HH", "AY1"])
        taky = encode(["T", "EY1", "K", "IY1"])
        hey = encode(["HH", "EY1"])
        tacky = encode(["T", "AE1", "K", "IY1"])
        self.assertEqual(f"{hi} {taky}", "hi TdKm")
        self.assertEqual(f"{hey} {taky}", "hd TdKm")
        self.assertEqual(tacky, "TaKm")
        spec = json.loads(self.COMMANDS.read_text(encoding="utf-8"))
        self.assertEqual(spec["language"], "en")
        self.assertEqual(spec["commands"][0]["command"], "hi taky")
        self.assertEqual(spec["commands"][0]["phonemes"], f"{hi} {taky}")
        self.assertNotEqual(spec["commands"][0]["phonemes"], f"{hi} {tacky}")
        self.assertNotIn("TaKm", spec["commands"][0]["phonemes"])
        self.assertEqual(spec["commands"][1]["command"], "hey taky")
        self.assertEqual(spec["commands"][1]["phonemes"], f"{hey} {taky}")
        self.assertEqual(spec["commands"][1]["text"], "hi Taky")
        self.assertEqual(len(spec["commands"]), 2)
        self.assertEqual(spec["threshold"], 0.2)
        self.assertEqual(spec["duration"], 3000)
        for item in spec["commands"]:
            self.assertNotIn("ni", item["command"].split(" "))
            self.assertNotRegex(item["command"], r"[A-Z]")

    def test_rejected_command_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps({
                "language": "cn",
                "duration": 3000,
                "threshold": 0.2,
                "commands": [{
                    "command": "ni hao Taky",
                    "text": "你好Taky",
                    "action": "wake",
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "lowercase pinyin"):
                BUILD.load_wake_commands(path)

            path.write_text(json.dumps({
                "language": "en",
                "duration": 3000,
                "threshold": 0.2,
                "commands": [{
                    "command": "hi taky",
                    "text": "hi Taky",
                    "action": "wake",
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "phonemes"):
                BUILD.load_wake_commands(path)

            path.write_text(json.dumps({
                "language": "en",
                "duration": 3000,
                "threshold": 0.2,
                "commands": [{
                    "command": "HI TAKY",
                    "text": "hi Taky",
                    "action": "wake",
                    "phonemes": "hi TdKm",
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "lowercase English"):
                BUILD.load_wake_commands(path)

            path.write_text(json.dumps({
                "language": "cn",
                "duration": 3000,
                "threshold": 0.2,
                "commands": [{
                    "command": "ni hao xiao zhi",
                    "text": "你好小智",
                    "action": "wake",
                    "phonemes": "hi TdKm",
                }],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "only valid for language en"):
                BUILD.load_wake_commands(path)


if __name__ == "__main__":
    unittest.main()
