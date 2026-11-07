#!/usr/bin/env python3
"""Read model names from a Xiaozhi mmap assets image.

The image is the assets partition or ``generated_assets.bin``. This parser
prints filenames, sizes, and hashes. It does not print file bodies, NVS,
Wi-Fi settings, tokens, or the WebSocket URL. Those are not stored in this
image; values other than the index version and the srmodels filename are
omitted anyway.

The app loads ``index.json``, then the file named by its ``srmodels`` field,
through ``srmodel_load()`` (esp-sr 2.4.7, 32-byte names). A MultiNet model
whose name starts with ``mn`` makes ``AfeAudioEngine`` skip WakeNet. This
firmware expects ``wn9_nihaoxiaozhi_tts`` and no ``mn`` model. ``index.json``
version greater than 1 is rejected by ``Assets::LvglStrategy::Apply``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys

NAME_LENGTH = 32
ENTRY = struct.Struct("<32sIIHH")
SR_NAME = 32
EXPECTED_WAKE_MODEL = "wn9_nihaoxiaozhi_tts"
MULTINET_PREFIX = "mn"


def _cstring(raw: bytes) -> str:
    text = raw.split(b"\x00", 1)[0]
    try:
        return text.decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("asset or model name is not ASCII") from error


def parse_assets(blob: bytes, name_length: int = NAME_LENGTH) -> dict:
    if name_length != NAME_LENGTH:
        raise ValueError("this firmware's mmap table uses 32-byte names")
    if len(blob) < 12:
        raise ValueError("assets image is shorter than the 12-byte header")
    file_count, stored_checksum, stored_len = struct.unpack_from("<III", blob, 0)
    if stored_len == 0 or 12 + stored_len > len(blob):
        raise ValueError("assets length field does not fit the image")
    payload = blob[12:12 + stored_len]
    calculated = sum(payload) & 0xFFFF
    if calculated != (stored_checksum & 0xFFFF) or stored_checksum > 0xFFFF:
        raise ValueError(
            f"assets checksum 0x{stored_checksum:x} does not match 0x{calculated:x}"
        )
    table_bytes = file_count * ENTRY.size
    if table_bytes > len(payload):
        raise ValueError("assets file table does not fit the payload")
    files = []
    for index in range(file_count):
        raw_name, size, offset, width, height = ENTRY.unpack_from(payload, index * ENTRY.size)
        name = _cstring(raw_name)
        absolute = 12 + table_bytes + offset
        if absolute + 2 + size > len(blob):
            raise ValueError(f"{name} extends past the image")
        if blob[absolute:absolute + 2] != b"ZZ":
            raise ValueError(f"{name} is missing the ZZ asset marker")
        body = blob[absolute + 2:absolute + 2 + size]
        files.append({
            "name": name,
            "size": size,
            "width": width,
            "height": height,
            "sha256": hashlib.sha256(body).hexdigest(),
            "body": body,
        })
    return {
        "file_count": file_count,
        "checksum": calculated,
        "image_sha256": hashlib.sha256(blob).hexdigest(),
        "files": files,
    }


def parse_srmodels(blob: bytes) -> list[dict]:
    if len(blob) < 4:
        raise ValueError("srmodels image is truncated")
    model_count = struct.unpack_from("<I", blob, 0)[0]
    if model_count > 32:
        raise ValueError(f"srmodels model count {model_count} is not plausible")
    cursor = 4
    models = []
    for _ in range(model_count):
        if cursor + SR_NAME + 4 > len(blob):
            raise ValueError("srmodels header is truncated")
        name = _cstring(blob[cursor:cursor + SR_NAME])
        cursor += SR_NAME
        file_count = struct.unpack_from("<I", blob, cursor)[0]
        cursor += 4
        if file_count > 64:
            raise ValueError(f"{name} file count {file_count} is not plausible")
        files = []
        for _file_index in range(file_count):
            if cursor + SR_NAME + 8 > len(blob):
                raise ValueError(f"{name} file table is truncated")
            file_name = _cstring(blob[cursor:cursor + SR_NAME])
            cursor += SR_NAME
            start, size = struct.unpack_from("<II", blob, cursor)
            cursor += 8
            if start + size > len(blob):
                raise ValueError(f"{name}/{file_name} extends past srmodels")
            body = blob[start:start + size]
            info = None
            if file_name == "_MODEL_INFO_":
                # esp-sr stores one UTF-8 description line here, including the
                # wake phrase. It is not a credential. Reject anything that is
                # not a short single line of text.
                try:
                    text = body.decode("utf-8")
                except UnicodeDecodeError:
                    text = ""
                if text.endswith("\n"):
                    text = text[:-1]
                if text and "\n" not in text and len(text) <= 200 and all(
                    char == "\t" or ord(char) >= 32 for char in text
                ):
                    info = text
            files.append({
                "name": file_name,
                "size": size,
                "sha256": hashlib.sha256(body).hexdigest(),
                "info": info,
            })
        models.append({"name": name, "files": files})
    return models


def inspect_assets(blob: bytes) -> dict:
    parsed = parse_assets(blob)
    public_files = []
    index = None
    models = []
    srmodels_name = None
    for item in parsed["files"]:
        public = {
            "name": item["name"],
            "size": item["size"],
            "sha256": item["sha256"],
        }
        if item["name"] == "index.json":
            try:
                index = json.loads(item["body"].decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ValueError("index.json is not UTF-8 JSON") from error
            version = index.get("version")
            srmodels_name = index.get("srmodels")
            public["index_version"] = version if isinstance(version, int) else None
            public["srmodels"] = srmodels_name if isinstance(srmodels_name, str) else None
        public_files.append(public)
    if isinstance(srmodels_name, str):
        match = next((item for item in parsed["files"] if item["name"] == srmodels_name), None)
        if match is None:
            raise ValueError(f"index names missing srmodels file {srmodels_name}")
        models = parse_srmodels(match["body"])
    version = index.get("version") if isinstance(index, dict) else None
    model_names = [model["name"] for model in models]
    multinet = [name for name in model_names if name.startswith(MULTINET_PREFIX)]
    compatible = (
        EXPECTED_WAKE_MODEL in model_names
        and not multinet
        and (version is None or (isinstance(version, int) and version <= 1))
    )
    return {
        "image_sha256": parsed["image_sha256"],
        "checksum": parsed["checksum"],
        "file_count": parsed["file_count"],
        "files": public_files,
        "index_version": version if isinstance(version, int) else None,
        "srmodels_file": srmodels_name if isinstance(srmodels_name, str) else None,
        "models": [
            {
                "name": model["name"],
                "files": [
                    {"name": item["name"], "size": item["size"], "sha256": item["sha256"]}
                    | ({"info": item["info"]} if item["info"] else {})
                    for item in model["files"]
                ],
            }
            for model in models
        ],
        "wake_compatible_with_this_app": compatible,
        "compatibility": (
            "wn9_nihaoxiaozhi_tts is present, no mn model is present, "
            "and index.json version is absent or at most 1"
            if compatible else
            "this image would not keep the app on WakeNet wn9_nihaoxiaozhi_tts"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", help="assets.bin or a raw assets-partition read")
    parser.add_argument(
        "--expect",
        help="JSON written by an earlier run; compare wake-model file hashes",
    )
    args = parser.parse_args(argv)
    try:
        report = inspect_assets(open(args.assets, "rb").read())
    except ValueError as error:
        print(f"assets_error={error}", file=sys.stderr)
        return 2
    if args.expect:
        expected = json.load(open(args.expect, encoding="utf-8"))
        expected_models = {model["name"]: model for model in expected.get("models", [])}
        actual_models = {model["name"]: model for model in report["models"]}
        wake = EXPECTED_WAKE_MODEL
        same = (
            wake in expected_models
            and wake in actual_models
            and expected_models[wake]["files"] == actual_models[wake]["files"]
        )
        report["wake_model_bytes_match_expect"] = same
        report["image_sha256_matches_expect"] = report["image_sha256"] == expected.get("image_sha256")
        if not same:
            report["wake_compatible_with_this_app"] = False
            report["compatibility"] = (
                "wake model name or file hash differs from the recorded esp-sr 2.4.7 blob"
            )
    json.dump(report, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if report["wake_compatible_with_this_app"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
