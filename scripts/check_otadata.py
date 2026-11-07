#!/usr/bin/env python3
"""Decide whether an ESP32-S3 otadata dump identifies one app slot.

Layout is taken from ESP-IDF v6.0.1 and release/v6.1
``bootloader_common_read_otadata()``: the two ``esp_ota_select_entry_t``
copies are at offsets 0 and ``SPI_SEC_SIZE`` (0x1000) inside the otadata
partition. They are not adjacent 32-byte records.

``bootloader_common_ota_select_crc()`` is
``esp_rom_crc32_le(UINT32_MAX, &ota_seq, 4)``. A CRC of the whole 32-byte
record is a different number and must not be used.

This project's sdkconfig enables ``CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE``.
On that path the bootloader rewrites ``PENDING_VERIFY`` to ``ABORTED``
before it selects a copy, and it rewrites ``NEW`` to ``PENDING_VERIFY``
after selecting it. A slot is authorized for an in-place app write only
when that post-rollback selection is stable and the boot log names the
same partition. Corrupt or non-erased invalid records do not authorize
``ota_0``.

The tool only reads the file you pass. It does not write flash.
"""

from __future__ import annotations

import argparse
import re
import struct
import sys
from dataclasses import dataclass

PARTITION_SIZE = 0x2000
SECTOR_SIZE = 0x1000
ENTRY_SIZE = 32
APP_COUNT = 2
SLOTS = (
    {"name": "ota_0", "offset": 0x20000, "size": 0x3F0000},
    {"name": "ota_1", "offset": 0x410000, "size": 0x3F0000},
)

STATE_NEW = 0x0
STATE_PENDING = 0x1
STATE_VALID = 0x2
STATE_INVALID = 0x3
STATE_ABORTED = 0x4
STATE_UNDEFINED = 0xFFFFFFFF
STABLE_STATES = {STATE_VALID, STATE_UNDEFINED}

RUNNING_PARTITION = re.compile(r"Running partition:\s*(ota_\d+)\b")


def crc32_le(crc: int, data: bytes) -> int:
    """``esp_rom_crc32_le`` from ESP-IDF components/esp_rom/linux/esp_rom_crc.c."""
    crc = (~crc) & 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
    return (~crc) & 0xFFFFFFFF


def slot_for_seq(ota_seq: int) -> dict:
    return SLOTS[(ota_seq - 1) % APP_COUNT]


@dataclass
class Entry:
    index: int
    offset: int
    raw: bytes
    ota_seq: int
    ota_state: int
    crc: int
    crc_ok: bool
    erased: bool

    def invalid_for_selection(self, state: int) -> bool:
        # bootloader_common_ota_select_invalid(), after any rollback rewrite.
        return self.ota_seq == 0xFFFFFFFF or state in (STATE_INVALID, STATE_ABORTED)

    def selectable(self, state: int) -> bool:
        # bootloader_common_ota_select_valid().
        return not self.invalid_for_selection(state) and self.crc_ok


def parse_entry(blob: bytes, index: int) -> Entry:
    offset = index * SECTOR_SIZE
    raw = blob[offset:offset + ENTRY_SIZE]
    ota_seq, _label, ota_state, crc = struct.unpack("<I20sII", raw)
    expected = crc32_le(0xFFFFFFFF, struct.pack("<I", ota_seq))
    return Entry(
        index=index,
        offset=offset,
        raw=raw,
        ota_seq=ota_seq,
        ota_state=ota_state,
        crc=crc,
        crc_ok=crc == expected,
        erased=raw == b"\xff" * ENTRY_SIZE,
    )


def likely_copy_ignoring_crc(entries: list[Entry], states: list[int]) -> int | None:
    """Same max-seq choice, but only the CRC requirement is ignored."""
    usable = [
        not entry.erased and not entry.invalid_for_selection(state)
        for entry, state in zip(entries, states)
    ]
    if usable[0] and usable[1]:
        return 1 if entries[1].ota_seq > entries[0].ota_seq else 0
    for index, ok in enumerate(usable):
        if ok:
            return index
    return None


def select_copy(entries: list[Entry], states: list[int]) -> int | None:
    """``bootloader_common_select_otadata(..., max=true)``."""
    selectable = [entry.selectable(state) for entry, state in zip(entries, states)]
    if selectable[0] and selectable[1]:
        if entries[1].ota_seq > entries[0].ota_seq:
            return 1
        return 0
    for index, ok in enumerate(selectable):
        if ok:
            return index
    return None


def running_from_boot_log(text: str) -> str:
    found = RUNNING_PARTITION.findall(text)
    unique = list(dict.fromkeys(found))
    if len(unique) != 1:
        raise ValueError(
            "boot log must contain exactly one 'Running partition: ota_N' line"
        )
    if unique[0] not in {slot["name"] for slot in SLOTS}:
        raise ValueError(f"unsupported running partition {unique[0]}")
    return unique[0]


def evaluate(blob: bytes, running: str | None = None) -> dict:
    """Return a JSON-ready decision. ``flash_offset`` is set only when authorized."""
    result = {
        "authorized": False,
        "flash_offset": None,
        "flash_size": None,
        "next_boot_slot": None,
        "bootloader_first_candidate": None,
        "running_partition": running,
        "reason": "",
        "entries": [],
    }
    if running is not None and running not in {slot["name"] for slot in SLOTS}:
        result["reason"] = "running partition must be ota_0 or ota_1 from the boot log"
        return result
    if len(blob) != PARTITION_SIZE:
        result["reason"] = (
            f"otadata length is {len(blob)}, partition is {PARTITION_SIZE} bytes; "
            "refusing a short or padded read"
        )
        return result

    entries = [parse_entry(blob, index) for index in (0, 1)]
    # Rollback runs before selection and changes PENDING_VERIFY into ABORTED.
    states = [
        STATE_ABORTED if entry.ota_state == STATE_PENDING else entry.ota_state
        for entry in entries
    ]
    for entry, state in zip(entries, states):
        expected_crc = crc32_le(0xFFFFFFFF, struct.pack("<I", entry.ota_seq))
        result["entries"].append({
            "index": entry.index,
            "offset": entry.offset,
            "ota_seq": entry.ota_seq,
            "stored_state": entry.ota_state,
            "selection_state": state,
            "stored_crc": entry.crc,
            "expected_crc": expected_crc,
            "crc_ok": entry.crc_ok,
            "erased": entry.erased,
            "selectable": entry.selectable(state),
        })

    chosen = select_copy(entries, states)
    if chosen is not None:
        entry = entries[chosen]
        state = states[chosen]
        slot = slot_for_seq(entry.ota_seq)
        result["next_boot_slot"] = slot["name"]
        result["bootloader_first_candidate"] = slot["name"]
        if entry.ota_state == STATE_NEW:
            result["reason"] = (
                "selected copy is NEW; this boot would mark it PENDING_VERIFY, "
                "and the following boot would abort it"
            )
            return result
        if entry.ota_state == STATE_PENDING:
            result["reason"] = "internal error: pending copy remained selectable"
            return result
        if state not in STABLE_STATES:
            result["reason"] = (
                f"selected state 0x{state:x} is not a stable boot selection"
            )
            return result
        if running is None:
            result["reason"] = (
                "otadata selects "
                f"{slot['name']}; pass the boot log 'Running partition' "
                "before any offset is authorized"
            )
            return result
        if running != slot["name"]:
            result["reason"] = (
                f"boot log says {running} but the next boot selects {slot['name']}"
            )
            return result
        result["authorized"] = True
        result["flash_offset"] = slot["offset"]
        result["flash_size"] = slot["size"]
        result["reason"] = "boot log and stable otadata selection agree"
        return result

    both_erased = all(entry.erased for entry in entries)
    marked_invalid = all(
        entry.invalid_for_selection(state) for entry, state in zip(entries, states)
    )
    if both_erased:
        # No factory app in partitions/v2/16m.csv. Both erased copies make
        # bootloader_utility_get_selected_boot_partition() start at ota_0.
        result["bootloader_first_candidate"] = "ota_0"
        result["next_boot_slot"] = "ota_0"
        if running is None:
            result["reason"] = (
                "both otadata sectors are erased; the bootloader tries ota_0 "
                "first and writes a sequence only if that image loads. "
                "The running partition from the boot log is still required"
            )
            return result
        if running != "ota_0":
            result["reason"] = (
                f"erased otadata would try ota_0 first, boot log says {running}"
            )
            result["next_boot_slot"] = None
            return result
        result["authorized"] = True
        result["flash_offset"] = SLOTS[0]["offset"]
        result["flash_size"] = SLOTS[0]["size"]
        result["reason"] = (
            "erased otadata and the boot log both identify ota_0; "
            "a failed ota_0 image would make the bootloader try later slots"
        )
        return result

    likely = likely_copy_ignoring_crc(entries, states)
    if likely is not None and not entries[likely].crc_ok:
        slot = slot_for_seq(entries[likely].ota_seq)
        expected_crc = crc32_le(0xFFFFFFFF, struct.pack("<I", entries[likely].ota_seq))
        result["likely_slot_if_crc_ok"] = slot["name"]
        result["reason"] = (
            f"seq {entries[likely].ota_seq} state 0x{states[likely]:x} would select "
            f"{slot['name']} only when the stored CRC equals esp_rom_crc32_le"
            f"(0xFFFFFFFF, ota_seq) = 0x{expected_crc:08x}. Stored CRC is "
            f"0x{entries[likely].crc:08x}. A CRC of the whole 32-byte record is "
            "not this value. The slot stays unverified and no offset is authorized"
        )
        return result

    if marked_invalid:
        result["reason"] = (
            "both copies are INVALID, ABORTED, pending-aborted, or have an "
            "empty sequence, and the sectors are not fully erased. IDF v6.0.1 "
            "and release/v6.1 then start at ota_0 and continue to later images if that "
            "load fails. No flash offset is authorized"
        )
    else:
        result["reason"] = (
            "no copy has a matching CRC and a selectable state. IDF v6.0.1 "
            "and release/v6.1 do not treat that as an empty otadata partition; they walk app "
            "images. No flash offset is authorized"
        )
    return result


def _print_result(result: dict) -> int:
    for entry in result["entries"]:
        print(
            "otadata[{index}] offset=0x{offset:x} seq=0x{ota_seq:08x} "
            "stored_state=0x{stored_state:x} selection_state=0x{selection_state:x} "
            "stored_crc=0x{stored_crc:08x} expected_crc=0x{expected_crc:08x} "
            "crc_ok={crc_ok} erased={erased} selectable={selectable}".format(**entry)
        )
    print(f"authorized={str(result['authorized']).lower()}")
    print(f"reason={result['reason']}")
    if result["running_partition"]:
        print(f"running_partition={result['running_partition']}")
    if result.get("likely_slot_if_crc_ok"):
        print(f"likely_slot_if_crc_ok={result['likely_slot_if_crc_ok']}")
    if result["next_boot_slot"]:
        print(f"next_boot_slot={result['next_boot_slot']}")
    if result["bootloader_first_candidate"]:
        print(f"bootloader_first_candidate={result['bootloader_first_candidate']}")
    if result["authorized"] and result["flash_offset"] is not None:
        print(f"flash_offset=0x{result['flash_offset']:x}")
        print(f"flash_size=0x{result['flash_size']:x}")
        return 0
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("otadata", help="raw 0x2000-byte otadata partition read")
    parser.add_argument(
        "--running",
        choices=["ota_0", "ota_1"],
        help="partition label from the app log line 'Running partition: ota_N'",
    )
    parser.add_argument(
        "--boot-log",
        help="serial log containing exactly one 'Running partition: ota_N' line",
    )
    args = parser.parse_args(argv)
    running = args.running
    if args.boot_log:
        try:
            logged = running_from_boot_log(open(args.boot_log, encoding="utf-8", errors="replace").read())
        except ValueError as error:
            print(f"reason={error}", file=sys.stderr)
            return 2
        if running is not None and running != logged:
            print("reason=--running does not match the boot log", file=sys.stderr)
            return 2
        running = logged
    blob = open(args.otadata, "rb").read()
    return _print_result(evaluate(blob, running))


if __name__ == "__main__":
    raise SystemExit(main())
