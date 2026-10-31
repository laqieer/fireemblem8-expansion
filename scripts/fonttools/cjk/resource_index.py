"""Bounded plain/packed JSON storage for the existing CJK resource indexes."""

from __future__ import annotations

import base64
import binascii
import json
import lzma
from pathlib import Path


MAX_INDEX_BYTES = 4 * 1024 * 1024
ENVELOPE_KIND = "cjk-resource-index"
ENVELOPE_KEYS = {"kind", "version", "encoding", "decoded_bytes", "payload"}


class ResourceIndexError(ValueError):
    """An index cannot be decoded without violating its storage contract."""


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ResourceIndexError(f"duplicate resource index key {key!r}")
        value[key] = item
    return value


def _json(data: bytes) -> dict[str, object]:
    if len(data) > MAX_INDEX_BYTES:
        raise ResourceIndexError("resource index exceeds 4 MiB")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ResourceIndexError(f"invalid resource index JSON: {error}") from error
    if not isinstance(value, dict):
        raise ResourceIndexError("resource index must be an object")
    return value


def _decode(data: bytes) -> tuple[dict[str, object], bool]:
    value = _json(data)
    if value.get("kind") != ENVELOPE_KIND:
        return value, False
    if (
        set(value) != ENVELOPE_KEYS
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["encoding"] != "xz-base64"
        or type(value["decoded_bytes"]) is not int
        or not 0 <= value["decoded_bytes"] <= MAX_INDEX_BYTES
        or not isinstance(value["payload"], str)
    ):
        raise ResourceIndexError("unsupported resource index envelope")
    try:
        packed = base64.b64decode(value["payload"], validate=True)
        decoder = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=64 * 1024 * 1024)
        decoded = decoder.decompress(packed, max_length=MAX_INDEX_BYTES + 1)
    except (binascii.Error, ValueError, lzma.LZMAError) as error:
        raise ResourceIndexError(f"invalid packed resource index: {error}") from error
    if (
        len(decoded) > MAX_INDEX_BYTES
        or len(decoded) != value["decoded_bytes"]
        or not decoder.eof
        or decoder.unused_data
    ):
        raise ResourceIndexError("resource index size, checksum or stream boundary differs")
    result = _json(decoded)
    if result.get("kind") == ENVELOPE_KIND:
        raise ResourceIndexError("nested resource index envelopes are unsupported")
    return result, True


def decode_index(data: bytes) -> dict[str, object]:
    return _decode(data)[0]


def encode_index(value: dict[str, object], *, packed: bool = False) -> bytes:
    if not isinstance(value, dict) or value.get("kind") == ENVELOPE_KIND:
        raise ResourceIndexError("resource index must be a non-envelope object")
    data = json_bytes(value)
    _json(data)
    if not packed:
        return data
    result = json_bytes({
        "kind": ENVELOPE_KIND,
        "version": 1,
        "encoding": "xz-base64",
        "decoded_bytes": len(data),
        "payload": base64.b64encode(
            lzma.compress(data, format=lzma.FORMAT_XZ, preset=6)
        ).decode("ascii"),
    })
    if len(result) > MAX_INDEX_BYTES:
        raise ResourceIndexError("stored resource index exceeds 4 MiB")
    return result


def read_index_bytes(path: Path) -> bytes:
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_INDEX_BYTES + 1)
    if len(data) > MAX_INDEX_BYTES:
        raise ResourceIndexError(f"{path}: stored resource index exceeds 4 MiB")
    return data


def load_index(path: Path) -> dict[str, object]:
    return decode_index(read_index_bytes(path))


def encode_like_index(value: dict[str, object], path: Path) -> bytes:
    try:
        previous = read_index_bytes(path)
    except FileNotFoundError:
        return encode_index(value)
    _, packed = _decode(previous)
    return encode_index(value, packed=packed)
