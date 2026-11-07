"""Sector layout and fail-closed decisions for release/v6.1 otadata."""

import importlib.util
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "check_otadata", ROOT / "scripts/check_otadata.py"
)
check_otadata = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = check_otadata
SPEC.loader.exec_module(check_otadata)

PARTITION_SIZE = check_otadata.PARTITION_SIZE
SECTOR_SIZE = check_otadata.SECTOR_SIZE
STATE_ABORTED = check_otadata.STATE_ABORTED
STATE_INVALID = check_otadata.STATE_INVALID
STATE_NEW = check_otadata.STATE_NEW
STATE_PENDING = check_otadata.STATE_PENDING
STATE_UNDEFINED = check_otadata.STATE_UNDEFINED
STATE_VALID = check_otadata.STATE_VALID
crc32_le = check_otadata.crc32_le
evaluate = check_otadata.evaluate


def entry(seq, state, crc=None):
    if crc is None:
        crc = crc32_le(0xFFFFFFFF, struct.pack("<I", seq))
    return struct.pack("<I20sII", seq, b"\xff" * 20, state, crc)


def image(first, second, fill=b"\xff"):
    blob = bytearray([fill[0]]) * PARTITION_SIZE
    blob[0:32] = first
    blob[SECTOR_SIZE:SECTOR_SIZE + 32] = second
    return bytes(blob)


class OtadataLayoutTests(unittest.TestCase):
    def test_crc_matches_esp_rom_crc32_le_vectors(self):
        # Compiled from components/esp_rom/linux/esp_rom_crc.c on release/v6.1.
        self.assertEqual(crc32_le(0xFFFFFFFF, struct.pack("<I", 1)), 0x4743989A)
        self.assertEqual(crc32_le(0xFFFFFFFF, struct.pack("<I", 2)), 0x55F63774)

    def test_second_sector_higher_valid_seq_selects_ota_1(self):
        blob = image(entry(1, STATE_VALID), entry(2, STATE_VALID))
        # A decoy at the old adjacent offset must not be read as copy 1.
        blob = bytearray(blob)
        blob[32:64] = entry(9, STATE_VALID)
        blob = bytes(blob)
        refused = evaluate(blob, None)
        self.assertFalse(refused["authorized"])
        self.assertIsNone(refused["flash_offset"])
        self.assertEqual(refused["next_boot_slot"], "ota_1")
        agreed = evaluate(blob, "ota_1")
        self.assertTrue(agreed["authorized"])
        self.assertEqual(agreed["flash_offset"], 0x410000)
        self.assertFalse(evaluate(blob, "ota_0")["authorized"])

    def test_first_invalid_second_valid(self):
        blob = image(entry(1, STATE_VALID, crc=0), entry(4, STATE_VALID))
        result = evaluate(blob, "ota_1")
        self.assertTrue(result["entries"][0]["crc_ok"] is False)
        self.assertEqual(result["entries"][1]["offset"], 0x1000)
        self.assertTrue(result["authorized"])
        self.assertEqual(result["flash_offset"], 0x410000)

    def test_corrupt_and_truncated_do_not_authorize_ota_0(self):
        corrupt = image(entry(1, STATE_VALID, crc=1), entry(2, STATE_VALID, crc=2), fill=b"\x00")
        result = evaluate(corrupt, "ota_0")
        self.assertFalse(result["authorized"])
        self.assertIsNone(result["flash_offset"])
        self.assertNotEqual(result["bootloader_first_candidate"], "ota_0")
        short = evaluate(b"\xff" * 64, "ota_0")
        self.assertFalse(short["authorized"])
        self.assertIsNone(short["flash_offset"])
        padded = evaluate(b"\xff" * (PARTITION_SIZE + 1), "ota_0")
        self.assertFalse(padded["authorized"])

    def test_erased_partition_requires_matching_boot_log(self):
        erased = b"\xff" * PARTITION_SIZE
        missing_log = evaluate(erased, None)
        self.assertFalse(missing_log["authorized"])
        self.assertEqual(missing_log["bootloader_first_candidate"], "ota_0")
        self.assertIsNone(missing_log["flash_offset"])
        agreed = evaluate(erased, "ota_0")
        self.assertTrue(agreed["authorized"])
        self.assertEqual(agreed["flash_offset"], 0x20000)
        other = evaluate(erased, "ota_1")
        self.assertFalse(other["authorized"])
        self.assertIsNone(other["flash_offset"])

    def test_both_invalid_non_erased_records_fail_closed(self):
        blob = image(entry(1, STATE_ABORTED), entry(2, STATE_INVALID), fill=b"\xa5")
        for running in (None, "ota_0", "ota_1"):
            result = evaluate(blob, running)
            self.assertFalse(result["authorized"], running)
            self.assertIsNone(result["flash_offset"])
            self.assertIsNone(result["next_boot_slot"])

    def test_pending_is_aborted_before_selection_and_new_is_not_stable(self):
        pending = image(entry(1, STATE_VALID), entry(2, STATE_PENDING))
        # seq 2 would be ota_1, but PENDING is aborted first, leaving seq 1.
        selected = evaluate(pending, "ota_0")
        self.assertEqual(selected["entries"][1]["selection_state"], STATE_ABORTED)
        self.assertTrue(selected["authorized"])
        self.assertEqual(selected["flash_offset"], 0x20000)
        still_running_pending_slot = evaluate(pending, "ota_1")
        self.assertFalse(still_running_pending_slot["authorized"])
        fresh = image(entry(1, STATE_NEW), b"\xff" * 32)
        refused = evaluate(fresh, "ota_0")
        self.assertEqual(refused["next_boot_slot"], "ota_0")
        self.assertFalse(refused["authorized"])
        self.assertIsNone(refused["flash_offset"])

    def test_undefined_state_can_match_the_boot_log(self):
        blob = image(entry(3, STATE_UNDEFINED), b"\xff" * 32)
        # (3 - 1) % 2 == 0, so this stable state is ota_0.
        result = evaluate(blob, "ota_0")
        self.assertTrue(result["authorized"])
        self.assertEqual(result["flash_offset"], 0x20000)

    def test_observed_device_seq1_valid_and_erased_second_sector(self):
        # Read-only preflight: first sector seq 1 state VALID, second sector erased.
        # CRC is only esp_rom_crc32_le of the 4-byte sequence. A whole-record CRC
        # does not match, so a naive check must not be treated as proof.
        import binascii
        correct = image(entry(1, STATE_VALID), b"\xff" * 32)
        unverified = evaluate(correct, None)
        self.assertEqual(unverified["next_boot_slot"], "ota_0")
        self.assertFalse(unverified["authorized"])
        self.assertIsNone(unverified["flash_offset"])
        self.assertEqual(unverified["entries"][0]["expected_crc"], 0x4743989A)
        self.assertTrue(unverified["entries"][0]["crc_ok"])
        self.assertTrue(unverified["entries"][1]["erased"])
        agreed = evaluate(correct, "ota_0")
        self.assertTrue(agreed["authorized"])
        self.assertEqual(agreed["flash_offset"], 0x20000)
        whole = binascii.crc32(correct[0:32]) & 0xFFFFFFFF
        self.assertNotEqual(whole, 0x4743989A)
        bad = image(entry(1, STATE_VALID, crc=whole), b"\xff" * 32)
        refused = evaluate(bad, "ota_0")
        self.assertFalse(refused["authorized"])
        self.assertIsNone(refused["flash_offset"])
        self.assertIsNone(refused["next_boot_slot"])
        self.assertEqual(refused["likely_slot_if_crc_ok"], "ota_0")
        self.assertEqual(refused["entries"][0]["expected_crc"], 0x4743989A)

    def test_helper_source_documents_the_sector_offsets(self):
        source = (ROOT / "scripts/check_otadata.py").read_text(encoding="utf-8")
        self.assertIn("SECTOR_SIZE = 0x1000", source)
        self.assertIn("PARTITION_SIZE = 0x2000", source)
        self.assertNotIn("i * ENTRY_SIZE", source)
