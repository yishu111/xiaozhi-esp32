"""Evidence checks for the GC9A01 self-host variant and wake phrases.

These tests read this tree and the esp-sr 2.4.7 wake-word table snapshot.
They do not claim that a microphone recognized any phrase.
"""

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PHRASES = ROOT / "scripts/tests/data/esp_sr_2_4_7_wakenet_phrases.txt"
BOARD = ROOT / "main/boards/bread-compact-wifi-lcd"


def wakenet_rows():
    rows = []
    for line in PHRASES.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        phrase, wakenet9s, wakenet9 = line.split("\t")
        rows.append((phrase, wakenet9s, wakenet9))
    return rows


class WakePhraseAndVariantTests(unittest.TestCase):
    def test_esp_sr_2_4_7_has_no_taky_wakenet_model(self):
        rows = wakenet_rows()
        phrases = [phrase.casefold() for phrase, _, _ in rows]
        models = [model for _, left, right in rows for model in (left, right) if model]
        for wanted in ("你好 taky", "hi taky", "taky", "tei ki", "你好taky"):
            self.assertNotIn(wanted, phrases)
        self.assertTrue(all("taky" not in phrase for phrase in phrases))
        self.assertTrue(all("taky" not in model for model in models))
        nihao = [row for row in rows if row[0] == "你好小智"]
        self.assertEqual(nihao, [("你好小智", "wn9s_nihaoxiaozhi", "wn9_nihaoxiaozhi_tts")])
        self.assertIn("wn9_nihaoxiaozhi_tts", models)
        self.assertNotIn("wn9_nihaoxiaozhi", models)

    def test_custom_wake_path_is_pinyin_and_not_used_for_taky(self):
        kconfig = (ROOT / "main/Kconfig.projbuild").read_text(encoding="utf-8")
        self.assertIn("use pinyin for Chinese, separated by spaces", kconfig)
        custom = (ROOT / "main/audio/wake_words/custom_wake_word.cc").read_text(encoding="utf-8")
        self.assertIn("esp_mn_commands_add", custom)
        engine = (ROOT / "main/audio/engines/afe_audio_engine.cc").read_text(encoding="utf-8")
        multinet = engine.index("if (multinet_model_name != nullptr)")
        wakenet = engine.index("} else if (wakenet_model_name != nullptr)")
        self.assertLess(multinet, wakenet)
        build = (ROOT / "scripts/build.py").read_text(encoding="utf-8")
        self.assertIn('else "wn9_nihaoxiaozhi_tts"', build)
        for path in (ROOT / "main").rglob("*"):
            if path.suffix not in {".cc", ".h", ".json", ".projbuild"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace").casefold()
            self.assertNotIn("ni hao tei ki", text, path)
            self.assertNotIn("你好 taky", text, path)
            self.assertNotIn("hi taky", text, path)

    def test_gc9a01_variant_keeps_selfhost_ota_and_original_pins(self):
        config = json.loads((BOARD / "config.json").read_text(encoding="utf-8"))
        variants = {item["name"]: item for item in config["builds"]}
        self.assertIn("bread-compact-wifi-lcd", variants)
        variant = variants["bread-compact-wifi-lcd-gc9a01"]
        options = variant["sdkconfig_append"]
        self.assertIn("CONFIG_LCD_GC9A01_240X240=y", options)
        self.assertIn(
            'CONFIG_OTA_URL="http://192.168.31.73:8003/xiaozhi/ota/"',
            options,
        )
        joined = "\n".join(options)
        self.assertNotIn("xiaozhi.me", joined)
        self.assertNotIn("tenclass.net", joined)
        self.assertNotIn("CONFIG_FLASHMODE_", joined)
        self.assertNotIn('CONFIG_ESPTOOLPY_FLASHMODE="qio"', joined)
        self.assertNotIn("CONFIG_USE_CUSTOM_WAKE_WORD", joined)
        header = (BOARD / "config.h").read_text(encoding="utf-8")
        for needle in (
            "AUDIO_I2S_MIC_GPIO_WS   GPIO_NUM_4",
            "AUDIO_I2S_MIC_GPIO_SCK  GPIO_NUM_5",
            "AUDIO_I2S_MIC_GPIO_DIN  GPIO_NUM_6",
            "AUDIO_I2S_SPK_GPIO_DOUT GPIO_NUM_7",
            "AUDIO_I2S_SPK_GPIO_BCLK GPIO_NUM_15",
            "AUDIO_I2S_SPK_GPIO_LRCK GPIO_NUM_16",
            "DISPLAY_CLK_PIN       GPIO_NUM_21",
            "DISPLAY_MOSI_PIN      GPIO_NUM_47",
            "DISPLAY_DC_PIN        GPIO_NUM_40",
            "DISPLAY_RST_PIN       GPIO_NUM_45",
            "DISPLAY_CS_PIN        GPIO_NUM_41",
            "BOOT_BUTTON_GPIO        GPIO_NUM_0",
            "DISPLAY_BACKLIGHT_PIN GPIO_NUM_42",
        ):
            self.assertIn(needle, header)
        self.assertIn("ota_0,    app,  ota_0,   0x20000,   0x3f0000,",
                      (ROOT / "partitions/v2/16m.csv").read_text(encoding="utf-8"))

    def test_xmc_patch_matches_release_v6_1_strict_check(self):
        patch = (ROOT / "scripts/patches/esp-idf-v6.1-xmc-mfid-0x70.patch").read_text(
            encoding="utf-8"
        )
        self.assertIn("bootloader_flash.c", patch)
        self.assertIn("mfid == 0x70", patch)
        self.assertIn("cpid >= 0x17 && cpid <= 0x20", patch)
        self.assertNotIn("merged-binary", patch)
