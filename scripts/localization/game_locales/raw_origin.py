"""Offline-verifiable range proof for FE8J baserom-backed raw strings."""

from __future__ import annotations

import hashlib
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ORIGIN_PROOF_KIND = "fe8j-raw-origin-range-proof"
ORIGIN_PROOF_SCHEMA_VERSION = 1
ORIGIN_PROOF_FILENAME = "goal_strings.origin.json"

PINNED_FE8J_ROM_SHA256 = (
    "44fd343625ab9e6b90f63a80758c15066d526e6873fae91474006314a5ead464"
)
PINNED_FE8J_ROM_SIZE = 0x1000000
PINNED_FE8J_RANGE_MERKLE_ROOT = (
    "a12049be5f9a6fc6e2fbb913a03c7a548fcc6e70c540ed1a485c327506b9851b"
)

RANGE_LEAF_SIZE = 8
RANGE_LEAF_COUNT = PINNED_FE8J_ROM_SIZE // RANGE_LEAF_SIZE
RANGE_TREE_DEPTH = 21
_LEAF_DOMAIN = b"FE8J-RANGE-LEAF-v1\0"
_NODE_DOMAIN = b"FE8J-RANGE-NODE-v1\0"

PINNED_GOAL_RANGES = {
    "0x01C1": {
        "length": 5,
        "rom_offset": 0x1F5528,
        "symbol": "GoalString_UnitsLeft",
    },
    "0x01C2": {
        "length": 7,
        "rom_offset": 0x1F553C,
        "symbol": "GoalString_Turn",
    },
    "0x01C3": {
        "length": 11,
        "rom_offset": 0x1F5530,
        "symbol": "GoalString_LastTurn",
    },
}


class RawOriginError(ValueError):
    """Raised when the committed FE8J range proof is invalid."""


@dataclass(frozen=True)
class OriginRange:
    target: str
    symbol: str
    rom_offset: int
    raw: bytes

    @property
    def length(self) -> int:
        return len(self.raw)


def canonical_json_bytes(data: Any) -> bytes:
    return (
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _leaf_hash(index: int, raw: bytes) -> bytes:
    return _sha256(_LEAF_DOMAIN + struct.pack(">I", index) + raw)


def _node_hash(level: int, left: bytes, right: bytes) -> bytes:
    return _sha256(_NODE_DOMAIN + bytes((level,)) + left + right)


def _validate_ranges(ranges: Sequence[OriginRange]) -> tuple[OriginRange, ...]:
    ordered = tuple(sorted(ranges, key=lambda item: item.target))
    if {item.target for item in ordered} != set(PINNED_GOAL_RANGES):
        raise RawOriginError(
            "FE8J raw origin ranges must exactly cover the pinned goal targets"
        )
    seen_offsets = set()
    for item in ordered:
        expected = PINNED_GOAL_RANGES[item.target]
        if (
            item.symbol != expected["symbol"]
            or item.rom_offset != expected["rom_offset"]
            or item.length != expected["length"]
        ):
            raise RawOriginError(
                f"FE8J raw origin range {item.target} differs from the "
                "independently pinned offset/length/symbol"
            )
        for offset in range(item.rom_offset, item.rom_offset + item.length):
            if offset in seen_offsets:
                raise RawOriginError("FE8J raw origin ranges overlap")
            seen_offsets.add(offset)
    return ordered


def _source_binding(ranges: Sequence[OriginRange]) -> list[dict[str, Any]]:
    return [
        {
            "bytes_sha256": hashlib.sha256(item.raw).hexdigest(),
            "length": item.length,
            "rom_offset": item.rom_offset,
            "symbol": item.symbol,
            "target": item.target,
        }
        for item in _validate_ranges(ranges)
    ]


def _required_leaf_indices(ranges: Sequence[OriginRange]) -> frozenset[int]:
    indices = set()
    for item in _validate_ranges(ranges):
        first = item.rom_offset // RANGE_LEAF_SIZE
        last = (item.rom_offset + item.length - 1) // RANGE_LEAF_SIZE
        indices.update(range(first, last + 1))
    return frozenset(indices)


def _tree_root_and_proof(
    rom: bytes,
    *,
    required_indices: Iterable[int],
) -> tuple[str, dict[tuple[int, int], str]]:
    if len(rom) != PINNED_FE8J_ROM_SIZE:
        raise RawOriginError("FE8J baserom size mismatch")
    required = set(required_indices)
    if not required or min(required) < 0 or max(required) >= RANGE_LEAF_COUNT:
        raise RawOriginError("FE8J raw origin required leaf set is invalid")

    current_count = RANGE_LEAF_COUNT
    current = bytearray(current_count * 32)
    for index in range(current_count):
        start = index * RANGE_LEAF_SIZE
        current[index * 32 : (index + 1) * 32] = _leaf_hash(
            index,
            rom[start : start + RANGE_LEAF_SIZE],
        )

    proof: dict[tuple[int, int], str] = {}
    current_required = required
    for level in range(RANGE_TREE_DEPTH):
        for index in sorted(current_required):
            sibling = index ^ 1
            if sibling not in current_required:
                start = sibling * 32
                proof[(level, sibling)] = bytes(
                    current[start : start + 32]
                ).hex()

        next_count = current_count // 2
        next_level = bytearray(next_count * 32)
        for index in range(next_count):
            left_start = index * 64
            digest = _node_hash(
                level + 1,
                bytes(current[left_start : left_start + 32]),
                bytes(current[left_start + 32 : left_start + 64]),
            )
            next_level[index * 32 : (index + 1) * 32] = digest
        current = next_level
        current_count = next_count
        current_required = {index // 2 for index in current_required}

    if current_count != 1:
        raise RawOriginError("FE8J raw origin Merkle tree did not converge")
    return bytes(current).hex(), proof


def _leaf_blocks(rom: bytes, indices: Iterable[int]) -> dict[str, str]:
    result = {}
    for index in sorted(indices):
        start = index * RANGE_LEAF_SIZE
        result[str(index)] = rom[start : start + RANGE_LEAF_SIZE].hex()
    return result


def build_origin_proof(
    rom: bytes,
    *,
    ranges: Sequence[OriginRange],
) -> dict[str, Any]:
    ranges = _validate_ranges(ranges)
    if len(rom) != PINNED_FE8J_ROM_SIZE:
        raise RawOriginError("FE8J baserom size mismatch")
    if hashlib.sha256(rom).hexdigest() != PINNED_FE8J_ROM_SHA256:
        raise RawOriginError("FE8J baserom SHA-256 mismatch")
    for item in ranges:
        actual = rom[item.rom_offset : item.rom_offset + item.length]
        if actual != item.raw:
            raise RawOriginError(
                f"FE8J baserom bytes mismatch for {item.target}"
            )

    required_indices = _required_leaf_indices(ranges)
    root, proof = _tree_root_and_proof(
        rom,
        required_indices=required_indices,
    )
    if root != PINNED_FE8J_RANGE_MERKLE_ROOT:
        raise RawOriginError(
            "FE8J baserom range Merkle root differs from the independently "
            "pinned known-ROM root"
        )
    return {
        "algorithm": {
            "hash": "sha256",
            "leaf_count": RANGE_LEAF_COUNT,
            "leaf_domain": _LEAF_DOMAIN.decode("ascii"),
            "leaf_size": RANGE_LEAF_SIZE,
            "node_domain": _NODE_DOMAIN.decode("ascii"),
            "tree_depth": RANGE_TREE_DEPTH,
        },
        "kind": ORIGIN_PROOF_KIND,
        "leaf_blocks": _leaf_blocks(rom, required_indices),
        "proof_nodes": [
            {
                "index": index,
                "level": level,
                "sha256": digest,
            }
            for (level, index), digest in sorted(proof.items())
        ],
        "ranges": _source_binding(ranges),
        "rom": {
            "range_merkle_root": root,
            "sha256": PINNED_FE8J_ROM_SHA256,
            "size": PINNED_FE8J_ROM_SIZE,
        },
        "schema_version": ORIGIN_PROOF_SCHEMA_VERSION,
    }


def _load_proof(path: Path) -> tuple[Mapping[str, Any], bytes]:
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise RawOriginError(
            f"FE8J raw origin proof is unavailable: {path}"
        ) from error
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RawOriginError("FE8J raw origin proof is not valid UTF-8 JSON") from error
    if not isinstance(data, dict):
        raise RawOriginError("FE8J raw origin proof root must be an object")
    if canonical_json_bytes(data) != raw:
        raise RawOriginError("FE8J raw origin proof is not canonical JSON")
    return data, raw


def verify_origin_proof(
    path: Path,
    *,
    ranges: Sequence[OriginRange],
) -> bytes:
    ranges = _validate_ranges(ranges)
    proof, raw = _load_proof(path)
    if set(proof) != {
        "algorithm",
        "kind",
        "leaf_blocks",
        "proof_nodes",
        "ranges",
        "rom",
        "schema_version",
    }:
        raise RawOriginError("FE8J raw origin proof fields are invalid")
    if (
        proof["schema_version"] != ORIGIN_PROOF_SCHEMA_VERSION
        or proof["kind"] != ORIGIN_PROOF_KIND
    ):
        raise RawOriginError("FE8J raw origin proof identity is invalid")
    if proof["algorithm"] != {
        "hash": "sha256",
        "leaf_count": RANGE_LEAF_COUNT,
        "leaf_domain": _LEAF_DOMAIN.decode("ascii"),
        "leaf_size": RANGE_LEAF_SIZE,
        "node_domain": _NODE_DOMAIN.decode("ascii"),
        "tree_depth": RANGE_TREE_DEPTH,
    }:
        raise RawOriginError("FE8J raw origin proof algorithm drifted")
    if proof["rom"] != {
        "range_merkle_root": PINNED_FE8J_RANGE_MERKLE_ROOT,
        "sha256": PINNED_FE8J_ROM_SHA256,
        "size": PINNED_FE8J_ROM_SIZE,
    }:
        raise RawOriginError(
            "FE8J raw origin proof ROM identity/root is not independently pinned"
        )
    if proof["ranges"] != _source_binding(ranges):
        raise RawOriginError(
            "FE8J raw origin proof ranges differ from the committed source artifact"
        )

    required_indices = _required_leaf_indices(ranges)
    leaf_blocks = proof["leaf_blocks"]
    if (
        not isinstance(leaf_blocks, dict)
        or set(leaf_blocks) != {str(index) for index in required_indices}
    ):
        raise RawOriginError("FE8J raw origin proof leaf blocks are incomplete")
    decoded_blocks = {}
    for key, value in leaf_blocks.items():
        if not isinstance(value, str):
            raise RawOriginError("FE8J raw origin proof leaf block is invalid")
        try:
            block = bytes.fromhex(value)
        except ValueError as error:
            raise RawOriginError(
                "FE8J raw origin proof leaf block is not hexadecimal"
            ) from error
        if len(block) != RANGE_LEAF_SIZE or value != block.hex():
            raise RawOriginError("FE8J raw origin proof leaf block size is invalid")
        decoded_blocks[int(key)] = block

    for item in ranges:
        actual = bytearray()
        for offset in range(item.rom_offset, item.rom_offset + item.length):
            block_index, block_offset = divmod(offset, RANGE_LEAF_SIZE)
            actual.append(decoded_blocks[block_index][block_offset])
        if bytes(actual) != item.raw:
            raise RawOriginError(
                f"FE8J raw origin proof bytes mismatch for {item.target}"
            )

    raw_nodes = proof["proof_nodes"]
    if not isinstance(raw_nodes, list):
        raise RawOriginError("FE8J raw origin proof nodes must be an array")
    proof_nodes = {}
    for node in raw_nodes:
        if not isinstance(node, dict) or set(node) != {
            "index",
            "level",
            "sha256",
        }:
            raise RawOriginError("FE8J raw origin proof node is malformed")
        level = node["level"]
        index = node["index"]
        digest = node["sha256"]
        if (
            not isinstance(level, int)
            or isinstance(level, bool)
            or not 0 <= level < RANGE_TREE_DEPTH
            or not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < RANGE_LEAF_COUNT >> level
            or not isinstance(digest, str)
            or len(digest) != 64
        ):
            raise RawOriginError("FE8J raw origin proof node values are invalid")
        try:
            digest_bytes = bytes.fromhex(digest)
        except ValueError as error:
            raise RawOriginError(
                "FE8J raw origin proof node hash is not hexadecimal"
            ) from error
        key = (level, index)
        if key in proof_nodes or digest != digest_bytes.hex():
            raise RawOriginError("FE8J raw origin proof node is duplicated")
        proof_nodes[key] = digest_bytes

    current = {
        index: _leaf_hash(index, block)
        for index, block in decoded_blocks.items()
    }
    used_nodes = set()
    for level in range(RANGE_TREE_DEPTH):
        next_level = {}
        for index in sorted(current):
            parent = index // 2
            if parent in next_level:
                continue
            sibling = index ^ 1
            if sibling in current:
                sibling_hash = current[sibling]
            else:
                key = (level, sibling)
                sibling_hash = proof_nodes.get(key)
                if sibling_hash is None:
                    raise RawOriginError(
                        "FE8J raw origin proof is missing a sibling node"
                    )
                used_nodes.add(key)
            if index & 1:
                left, right = sibling_hash, current[index]
            else:
                left, right = current[index], sibling_hash
            next_level[parent] = _node_hash(level + 1, left, right)
        current = next_level

    if set(current) != {0}:
        raise RawOriginError("FE8J raw origin proof did not converge")
    if current[0].hex() != PINNED_FE8J_RANGE_MERKLE_ROOT:
        raise RawOriginError(
            "FE8J raw origin proof does not match the pinned known-ROM root"
        )
    if used_nodes != set(proof_nodes):
        raise RawOriginError("FE8J raw origin proof contains unused nodes")
    return raw
