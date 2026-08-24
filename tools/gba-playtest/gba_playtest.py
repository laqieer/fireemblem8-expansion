#!/usr/bin/env python3
"""Deterministic, headless GBA scenario capture and verification."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from probe_bindings import (
    ElfSymbolResolver,
    ProbeBindingError,
    SYMBOL_EXPRESSION_RE,
    resolve_probe_expression,
)


FIXED_SCENARIO_SCHEMA_VERSION = 1
RUN_UNTIL_SCENARIO_SCHEMA_VERSION = 2
ACCELERATED_FIDELITY_SCENARIO_SCHEMA_VERSION = 3
SCENARIO_SCHEMA_VERSION = FIXED_SCENARIO_SCHEMA_VERSION
FIXED_FINGERPRINT_FORMAT_VERSION = 2
RUN_UNTIL_FINGERPRINT_FORMAT_VERSION = 3
ACCELERATED_FIDELITY_FINGERPRINT_FORMAT_VERSION = 4
FINGERPRINT_FORMAT_VERSION = FIXED_FINGERPRINT_FORMAT_VERSION
PKG_CONFIG_TIMEOUT_SECONDS = 10
COMPILER_TIMEOUT_SECONDS = 60
MIN_BACKEND_TIMEOUT_SECONDS = 10
MAX_BACKEND_TIMEOUT_SECONDS = 300
# Hard ceiling on --retries regardless of the caller-requested value, so a
# typo or a misguided "just retry more" edit can never turn this into an
# unbounded/effectively-silent retry loop. 0 (the default everywhere) means
# exactly one attempt -- retrying is always an explicit, capped opt-in.
MAX_RETRIES_CAP = 5
KEY_BITS = {
    "A": 1 << 0,
    "B": 1 << 1,
    "SELECT": 1 << 2,
    "START": 1 << 3,
    "RIGHT": 1 << 4,
    "LEFT": 1 << 5,
    "UP": 1 << 6,
    "DOWN": 1 << 7,
    "R": 1 << 8,
    "L": 1 << 9,
}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
HEX_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{8}$")
HASH_RE = re.compile(r"^fnv1a64-rgb24:[0-9a-f]{16}$")
# Screen-region hash (same FNV-1a-over-canonical-R,G,B construction as
# HASH_RE, restricted to a checkpoint-declared rectangular sub-region of
# the 240x160 framebuffer -- see backend.c's hash_region()). Distinct
# algorithm name so a region hash and a whole-frame hash can never be
# silently confused downstream.
REGION_HASH_RE = re.compile(r"^fnv1a64-region:[0-9a-f]{16}$")
# A single captured/expected pixel's 24-bit R,G,B value (backend.c's
# read_pixel()) -- lowercase 0x plus exactly 6 hex digits, no alpha/padding.
PIXEL_RE = re.compile(r"^0x[0-9a-f]{6}$")
GBA_SCREEN_WIDTH = 240
GBA_SCREEN_HEIGHT = 160
# Matches backend.c's read_plan() rejection of checkpoint->region_count > 64
# / checkpoint->pixel_probe_count > 256.
MAX_REGIONS_PER_CHECKPOINT = 64
MAX_PIXEL_PROBES_PER_CHECKPOINT = 256
# "fnv1a64-sram:" is the exact whole-0x8000-byte hash (default, required for
# any checkpoint that must prove byte-for-byte SRAM preservation, e.g. the
# non-destructive Back scenarios). "fnv1a64-sram-normalized:" is the same
# FNV-1a construction with `sram_hash_exclude_ranges` bytes skipped -- used
# only for checkpoints whose SRAM is expected to contain intentionally
# build-variable diagnostic bytes (see docs/save_format.md's "SRAM hash
# policy: exact vs. normalized" section). The algorithm name in the hash
# text always identifies which policy produced it.
SRAM_HASH_RE = re.compile(r"^fnv1a64-sram(?:-normalized)?:[0-9a-f]{16}$")
RAM_RANGES = (
    (0x02000000, 0x02040000),
    (0x03000000, 0x03008000),
    (0x05000000, 0x05000400),
    (0x06000000, 0x06018000),
    (0x07000000, 0x07000400),
    (0x08000000, 0x0A000000),
    (0x0E000000, 0x0E008000),
)
SRAM_IMAGE_SIZE = 0x8000
# Matches backend.c's read_plan() rejection of checkpoint->exclude_range_count
# > 64 -- validated here too so a scenario with too many ranges fails fast
# with a clear PlaytestError instead of being rejected deep inside the
# backend after a plan file has already been generated.
MAX_SRAM_HASH_EXCLUDE_RANGES = 64
MAX_PROBES_PER_CHECKPOINT = 1536
MAX_RUN_UNTIL_COMPARISONS = 64
MAX_RUN_UNTIL_PROBES = 128
MAX_PROFILE_TRACE_PROBES = 512
# Matches backend.c's MAX_TRACE_RECORDS. A trace emits each probe whenever
# any trace value changes, so max_frames * trace probe count bounds both the
# backend's stdout and the host's captured semantic trace.
MAX_PROFILE_TRACE_RECORDS = 450_000
COMPARISON_OPERATORS = ("eq", "ne", "lt", "le", "gt", "ge")
TERMINAL_CONDITION_REASONS = (
    "success",
    "objective_failure",
    "controller_exhausted",
)
TERMINAL_REASONS = TERMINAL_CONDITION_REASONS + (
    "engine_stall",
    "max_frames",
    "max_turns",
    "max_actions",
)
PLAN_OPERATOR_CODES = {
    operator: index for index, operator in enumerate(COMPARISON_OPERATORS)
}
PLAN_TERMINAL_REASON_CODES = {
    reason: index + 1 for index, reason in enumerate(TERMINAL_CONDITION_REASONS)
}
EXECUTION_PROFILE_NORMAL_FIDELITY = "normal-fidelity"
EXECUTION_PROFILE_ACCELERATED_FIDELITY = "accelerated-fidelity"
EXECUTION_PROFILE_NAMES = (
    EXECUTION_PROFILE_NORMAL_FIDELITY,
    EXECUTION_PROFILE_ACCELERATED_FIDELITY,
)
PLAN_EXECUTION_PROFILE_CODES = {
    EXECUTION_PROFILE_NORMAL_FIDELITY: 0,
    EXECUTION_PROFILE_ACCELERATED_FIDELITY: 1,
}
# These are the documented PlaySt.config bit positions in include/types.h.
# The accelerated profile changes no engine switch of its own: it applies the
# existing game-speed preference and the animation option selected by
# BANIM_PRESENTATION_POLICY_OFF to the disposable emulator core.
PLAYST_CONFIG_GAME_SPEED_MASK = 1 << 7
PLAYST_CONFIG_ANIMATION_TYPE_MASK = 0x3 << 17
PLAYST_CONFIG_ANIMATION_TYPE_OFF = 0x1 << 17


class PlaytestError(Exception):
    """A user-actionable setup or input error."""


class DuplicateKeyError(ValueError):
    pass


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _read_json(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlaytestError(f"cannot read {path}: {exc}") from exc
    try:
        return json.loads(text, object_pairs_hook=_object_no_duplicates)
    except (json.JSONDecodeError, DuplicateKeyError) as exc:
        raise PlaytestError(f"invalid JSON in {path}: {exc}") from exc


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _expect_object(
    value: Any, path: str, required: set[str], optional: set[str] = frozenset()
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlaytestError(f"{path} must be an object")
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required - optional)
    if missing:
        raise PlaytestError(f"{path} is missing required field(s): {', '.join(missing)}")
    if unknown:
        raise PlaytestError(f"{path} has unknown field(s): {', '.join(unknown)}")
    return value


def _expect_name(value: Any, path: str) -> str:
    if not isinstance(value, str) or not NAME_RE.fullmatch(value):
        raise PlaytestError(
            f"{path} must match {NAME_RE.pattern!r} (1-64 ASCII characters)"
        )
    return value


def _expect_frame(value: Any, path: str) -> int:
    if not _is_int(value) or value < 0 or value > 10_000_000:
        raise PlaytestError(f"{path} must be an integer from 0 through 10000000")
    return value


def _validate_resolved_address(address: int, size: int, path: str) -> None:
    containing = next(
        ((start, end) for start, end in RAM_RANGES if start <= address < end),
        None,
    )
    if containing is None:
        raise PlaytestError(
            f"{path} must be in EWRAM, IWRAM, palette RAM, VRAM, OAM, "
            "cartridge ROM, or cart SRAM"
        )
    _, end = containing
    if address + size > end:
        raise PlaytestError(f"{path} plus size {size} crosses the address region boundary")
    if address % size:
        raise PlaytestError(f"{path} must be aligned to probe size {size}")


def _parse_address(
    value: Any,
    size: int,
    path: str,
    symbol_resolver: Callable[[str], tuple[int, int]] | None = None,
) -> tuple[str, int | None]:
    if not isinstance(value, str):
        raise PlaytestError(
            f"{path} must be an 8-digit hexadecimal string or ELF symbol expression"
        )
    if HEX_ADDRESS_RE.fullmatch(value):
        address = int(value, 16)
        binding = f"0x{address:08x}"
    elif SYMBOL_EXPRESSION_RE.fullmatch(value):
        if symbol_resolver is None:
            return value, None
        else:
            try:
                address = resolve_probe_expression(value, size, symbol_resolver, path)
            except ProbeBindingError as exc:
                raise PlaytestError(str(exc)) from exc
            binding = value
    else:
        raise PlaytestError(
            f"{path} must be an 8-digit hexadecimal string such as 0x02000000 "
            "or a symbol expression such as gExpansionLanguageMenuProbe+0x04"
        )
    _validate_resolved_address(address, size, path)
    return binding, address


def _probe_binding_sort_key(binding: str, size: int) -> tuple[Any, ...]:
    if HEX_ADDRESS_RE.fullmatch(binding):
        address = int(binding, 16)
        if 0x02000000 <= address < 0x02040000:
            region = 0
        elif 0x03000000 <= address < 0x03008000:
            region = 2
        else:
            region = 3
        return (region, address, size)
    match = SYMBOL_EXPRESSION_RE.fullmatch(binding)
    if match is None:
        raise ValueError(f"invalid probe binding {binding!r}")
    symbol, offset_text = match.groups()
    offset = int(offset_text, 16) if offset_text is not None else 0
    return (1, symbol, offset, size)


@dataclass(frozen=True)
class InputRange:
    start: int
    end: int
    key_mask: int


@dataclass(frozen=True)
class Probe:
    binding: str
    address: int | None
    size: int
    expected: str | None


@dataclass(frozen=True)
class Comparison:
    probe: Probe
    operator: str
    value: int


@dataclass(frozen=True)
class TerminalCondition:
    reason: str
    comparisons: tuple[Comparison, ...]


@dataclass(frozen=True)
class CounterLimit:
    maximum: int
    probe: Probe


@dataclass(frozen=True)
class StallLimit:
    max_unchanged_frames: int
    progress: Probe
    work_expected: Comparison


@dataclass(frozen=True)
class RunUntil:
    max_frames: int
    terminal_conditions: tuple[TerminalCondition, ...]
    stall: StallLimit | None
    turn_limit: CounterLimit | None
    action_limit: CounterLimit | None


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    config_apply_frame: int | None
    play_state_config: Probe | None
    trace_probes: tuple[Probe, ...]


@dataclass(frozen=True)
class Region:
    name: str
    x: int
    y: int
    width: int
    height: int
    expected_hash: str | None


@dataclass(frozen=True)
class PixelProbe:
    x: int
    y: int
    expected: str | None


@dataclass(frozen=True)
class Checkpoint:
    name: str
    frame: int
    framebuffer: bool
    expected_framebuffer_hash: str | None
    sram_hash: bool
    expected_sram_hash: str | None
    # Byte ranges within the 0x8000-byte SRAM image excluded from
    # `sram_hash`'s computation (each an (offset, length) pair, ascending
    # and non-overlapping). Empty means the exact whole-image hash;
    # non-empty selects the normalized ("fnv1a64-sram-normalized:") hash --
    # see docs/save_format.md's "SRAM hash policy: exact vs. normalized".
    sram_hash_exclude_ranges: tuple[tuple[int, int], ...]
    probes: tuple[Probe, ...]
    # Named rectangular sub-regions of the 240x160 framebuffer, each hashed
    # independently of the whole-frame hash (backend.c's hash_region()) --
    # real, targeted proof a specific on-screen area changed/differs.
    regions: tuple[Region, ...]
    # Individual (x, y) framebuffer coordinates read back as an exact
    # 24-bit R,G,B color value (backend.c's read_pixel()) -- the finest-
    # grained real visible-pixel assertion available.
    pixel_probes: tuple[PixelProbe, ...]


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    disabled: bool
    blocker: str | None
    inputs: tuple[InputRange, ...]
    checkpoints: tuple[Checkpoint, ...]
    schema_version: int = FIXED_SCENARIO_SCHEMA_VERSION
    run_until: RunUntil | None = None
    execution_profile: ExecutionProfile | None = None


def _parse_fixed_scenario_data(
    data: Any,
    source: str = "<scenario>",
    symbol_resolver: Callable[[str], tuple[int, int]] | None = None,
) -> Scenario:
    root = _expect_object(
        data,
        source,
        {"schema_version", "name", "frames", "checkpoints"},
        {"description", "disabled", "blocker"},
    )
    if (
        not _is_int(root["schema_version"])
        or root["schema_version"] != SCENARIO_SCHEMA_VERSION
    ):
        raise PlaytestError(
            f"{source}.schema_version must be integer {SCENARIO_SCHEMA_VERSION}"
        )
    name = _expect_name(root["name"], f"{source}.name")
    description = root.get("description", "")
    if not isinstance(description, str):
        raise PlaytestError(f"{source}.description must be a string")
    disabled = root.get("disabled", False)
    if not isinstance(disabled, bool):
        raise PlaytestError(f"{source}.disabled must be a boolean")
    blocker = root.get("blocker")
    if blocker is not None and (not isinstance(blocker, str) or not blocker.strip()):
        raise PlaytestError(f"{source}.blocker must be a non-empty string")
    if disabled and not blocker:
        raise PlaytestError(f"{source}.blocker is required when disabled is true")
    if not disabled and blocker is not None:
        raise PlaytestError(f"{source}.blocker is only allowed when disabled is true")

    frames_data = root["frames"]
    if not isinstance(frames_data, list):
        raise PlaytestError(f"{source}.frames must be an array")
    inputs: list[InputRange] = []
    previous_end = -1
    for index, raw in enumerate(frames_data):
        path = f"{source}.frames[{index}]"
        item = _expect_object(raw, path, {"start", "end", "keys"})
        start = _expect_frame(item["start"], f"{path}.start")
        end = _expect_frame(item["end"], f"{path}.end")
        if end < start:
            raise PlaytestError(f"{path}.end must be greater than or equal to start")
        if start <= previous_end:
            raise PlaytestError(f"{path} overlaps or is not ordered after the previous frame range")
        keys = item["keys"]
        if not isinstance(keys, list):
            raise PlaytestError(f"{path}.keys must be an array")
        if not all(isinstance(key, str) for key in keys):
            raise PlaytestError(f"{path}.keys must contain only string key names")
        if len(keys) != len(set(keys)):
            raise PlaytestError(f"{path}.keys must contain unique key names")
        invalid = [key for key in keys if key not in KEY_BITS]
        if invalid:
            rendered = ", ".join(repr(key) for key in invalid)
            raise PlaytestError(
                f"{path}.keys contains invalid key name(s): {rendered}; "
                f"valid names: {', '.join(KEY_BITS)}"
            )
        mask = 0
        for key in keys:
            mask |= KEY_BITS[key]
        inputs.append(InputRange(start, end, mask))
        previous_end = end

    checkpoints_data = root["checkpoints"]
    if not isinstance(checkpoints_data, list):
        raise PlaytestError(f"{source}.checkpoints must be an array")
    if not disabled and not checkpoints_data:
        raise PlaytestError(f"{source}.checkpoints must not be empty for an active scenario")
    checkpoints: list[Checkpoint] = []
    checkpoint_names: set[str] = set()
    checkpoint_frames: set[int] = set()
    for index, raw in enumerate(checkpoints_data):
        path = f"{source}.checkpoints[{index}]"
        item = _expect_object(
            raw,
            path,
            {"name", "frame", "framebuffer", "probes"},
            {
                "expected_framebuffer_hash",
                "sram_hash",
                "expected_sram_hash",
                "sram_hash_exclude_ranges",
                "regions",
                "pixel_probes",
            },
        )
        checkpoint_name = _expect_name(item["name"], f"{path}.name")
        if checkpoint_name in checkpoint_names:
            raise PlaytestError(f"{path}.name duplicates checkpoint {checkpoint_name!r}")
        checkpoint_names.add(checkpoint_name)
        frame = _expect_frame(item["frame"], f"{path}.frame")
        if frame in checkpoint_frames:
            raise PlaytestError(f"{path}.frame duplicates checkpoint frame {frame}")
        checkpoint_frames.add(frame)
        framebuffer = item["framebuffer"]
        if not isinstance(framebuffer, bool):
            raise PlaytestError(f"{path}.framebuffer must be a boolean")
        expected_hash = item.get("expected_framebuffer_hash")
        if expected_hash is not None and (
            not isinstance(expected_hash, str) or not HASH_RE.fullmatch(expected_hash)
        ):
            raise PlaytestError(
                f"{path}.expected_framebuffer_hash must look like "
                "'fnv1a64-rgb24:0123456789abcdef'"
            )
        if expected_hash is not None and not framebuffer:
            raise PlaytestError(f"{path}.expected_framebuffer_hash requires framebuffer true")
        sram_hash = item.get("sram_hash", False)
        if not isinstance(sram_hash, bool):
            raise PlaytestError(f"{path}.sram_hash must be a boolean")
        expected_sram_hash = item.get("expected_sram_hash")
        if expected_sram_hash is not None and (
            not isinstance(expected_sram_hash, str)
            or not SRAM_HASH_RE.fullmatch(expected_sram_hash)
        ):
            raise PlaytestError(
                f"{path}.expected_sram_hash must look like "
                "'fnv1a64-sram:0123456789abcdef'"
            )
        if expected_sram_hash is not None and not sram_hash:
            raise PlaytestError(f"{path}.expected_sram_hash requires sram_hash true")
        exclude_ranges_data = item.get("sram_hash_exclude_ranges")
        sram_hash_exclude_ranges: list[tuple[int, int]] = []
        if exclude_ranges_data is not None:
            if not sram_hash:
                raise PlaytestError(f"{path}.sram_hash_exclude_ranges requires sram_hash true")
            if not isinstance(exclude_ranges_data, list) or not exclude_ranges_data:
                raise PlaytestError(f"{path}.sram_hash_exclude_ranges must be a non-empty array")
            if len(exclude_ranges_data) > MAX_SRAM_HASH_EXCLUDE_RANGES:
                raise PlaytestError(
                    f"{path}.sram_hash_exclude_ranges for checkpoint {checkpoint_name!r} has "
                    f"{len(exclude_ranges_data)} ranges, exceeding the "
                    f"{MAX_SRAM_HASH_EXCLUDE_RANGES}-range limit per checkpoint "
                    "(matches backend.c's plan-format cap)"
                )
            previous_range_end = -1
            for range_index, raw_range in enumerate(exclude_ranges_data):
                range_path = f"{path}.sram_hash_exclude_ranges[{range_index}]"
                range_item = _expect_object(raw_range, range_path, {"offset", "length"})
                offset = range_item["offset"]
                length = range_item["length"]
                if not _is_int(offset) or offset < 0 or offset >= SRAM_IMAGE_SIZE:
                    raise PlaytestError(
                        f"{range_path}.offset must be an integer from 0 through "
                        f"{SRAM_IMAGE_SIZE - 1}"
                    )
                if not _is_int(length) or length < 1 or length > SRAM_IMAGE_SIZE:
                    raise PlaytestError(
                        f"{range_path}.length must be an integer from 1 through {SRAM_IMAGE_SIZE}"
                    )
                if offset + length > SRAM_IMAGE_SIZE:
                    raise PlaytestError(
                        f"{range_path} (offset {offset} + length {length}) exceeds the "
                        f"{SRAM_IMAGE_SIZE}-byte SRAM image"
                    )
                if offset <= previous_range_end:
                    raise PlaytestError(
                        f"{range_path} overlaps or is not ordered strictly after the "
                        "previous exclude range"
                    )
                previous_range_end = offset + length - 1
                sram_hash_exclude_ranges.append((offset, length))
        probes_data = item["probes"]
        if not isinstance(probes_data, list):
            raise PlaytestError(f"{path}.probes must be an array")
        if len(probes_data) > MAX_PROBES_PER_CHECKPOINT:
            raise PlaytestError(
                f"{path}.probes has {len(probes_data)} entries, exceeding the "
                f"{MAX_PROBES_PER_CHECKPOINT}-probe limit per checkpoint "
                "(matches backend.c's plan-format cap)"
            )
        probes: list[Probe] = []
        seen_bindings: set[tuple[str, int]] = set()
        seen_addresses: set[tuple[int, int]] = set()
        for probe_index, raw_probe in enumerate(probes_data):
            probe_path = f"{path}.probes[{probe_index}]"
            probe_data = _expect_object(
                raw_probe, probe_path, {"address", "size"}, {"expected"}
            )
            size = probe_data["size"]
            if not _is_int(size) or size not in (1, 2, 4):
                raise PlaytestError(f"{probe_path}.size must be integer 1, 2, or 4")
            binding, address = _parse_address(
                probe_data["address"],
                size,
                f"{probe_path}.address",
                symbol_resolver,
            )
            binding_identity = (binding, size)
            if binding_identity in seen_bindings:
                raise PlaytestError(
                    f"{probe_path} duplicates symbolic address/size in this checkpoint"
                )
            seen_bindings.add(binding_identity)
            if address is not None:
                address_identity = (address, size)
                if address_identity in seen_addresses:
                    raise PlaytestError(
                        f"{probe_path} resolves to a duplicate address/size in this checkpoint"
                    )
                seen_addresses.add(address_identity)
            expected = probe_data.get("expected")
            if expected is not None:
                pattern = re.compile(rf"^0x[0-9a-f]{{{size * 2}}}$")
                if not isinstance(expected, str) or not pattern.fullmatch(expected):
                    raise PlaytestError(
                        f"{probe_path}.expected must be lowercase 0x plus {size * 2} hex digits"
                    )
            probes.append(Probe(binding, address, size, expected))

        regions_data = item.get("regions", [])
        if not isinstance(regions_data, list):
            raise PlaytestError(f"{path}.regions must be an array")
        if len(regions_data) > MAX_REGIONS_PER_CHECKPOINT:
            raise PlaytestError(
                f"{path}.regions for checkpoint {checkpoint_name!r} has "
                f"{len(regions_data)} entries, exceeding the "
                f"{MAX_REGIONS_PER_CHECKPOINT}-region limit per checkpoint "
                "(matches backend.c's plan-format cap)"
            )
        regions: list[Region] = []
        seen_region_names: set[str] = set()
        for region_index, raw_region in enumerate(regions_data):
            region_path = f"{path}.regions[{region_index}]"
            region_data = _expect_object(
                raw_region,
                region_path,
                {"name", "x", "y", "width", "height"},
                {"expected_hash"},
            )
            region_name = _expect_name(region_data["name"], f"{region_path}.name")
            if region_name in seen_region_names:
                raise PlaytestError(f"{region_path}.name duplicates {region_name!r}")
            seen_region_names.add(region_name)
            x = region_data["x"]
            y = region_data["y"]
            width = region_data["width"]
            height = region_data["height"]
            if not _is_int(x) or x < 0 or x >= GBA_SCREEN_WIDTH:
                raise PlaytestError(
                    f"{region_path}.x must be an integer from 0 through {GBA_SCREEN_WIDTH - 1}"
                )
            if not _is_int(y) or y < 0 or y >= GBA_SCREEN_HEIGHT:
                raise PlaytestError(
                    f"{region_path}.y must be an integer from 0 through {GBA_SCREEN_HEIGHT - 1}"
                )
            if not _is_int(width) or width < 1 or width > GBA_SCREEN_WIDTH - x:
                raise PlaytestError(
                    f"{region_path}.width must be an integer from 1 through {GBA_SCREEN_WIDTH - x}"
                )
            if not _is_int(height) or height < 1 or height > GBA_SCREEN_HEIGHT - y:
                raise PlaytestError(
                    f"{region_path}.height must be an integer from 1 through {GBA_SCREEN_HEIGHT - y}"
                )
            if not framebuffer:
                raise PlaytestError(f"{region_path} requires the checkpoint's framebuffer to be true")
            expected_region_hash = region_data.get("expected_hash")
            if expected_region_hash is not None and (
                not isinstance(expected_region_hash, str)
                or not REGION_HASH_RE.fullmatch(expected_region_hash)
            ):
                raise PlaytestError(
                    f"{region_path}.expected_hash must look like "
                    "'fnv1a64-region:0123456789abcdef'"
                )
            regions.append(Region(region_name, x, y, width, height, expected_region_hash))

        pixel_probes_data = item.get("pixel_probes", [])
        if not isinstance(pixel_probes_data, list):
            raise PlaytestError(f"{path}.pixel_probes must be an array")
        if len(pixel_probes_data) > MAX_PIXEL_PROBES_PER_CHECKPOINT:
            raise PlaytestError(
                f"{path}.pixel_probes for checkpoint {checkpoint_name!r} has "
                f"{len(pixel_probes_data)} entries, exceeding the "
                f"{MAX_PIXEL_PROBES_PER_CHECKPOINT}-pixel-probe limit per checkpoint "
                "(matches backend.c's plan-format cap)"
            )
        pixel_probes: list[PixelProbe] = []
        seen_pixels: set[tuple[int, int]] = set()
        for pixel_index, raw_pixel in enumerate(pixel_probes_data):
            pixel_path = f"{path}.pixel_probes[{pixel_index}]"
            pixel_data = _expect_object(raw_pixel, pixel_path, {"x", "y"}, {"expected"})
            x = pixel_data["x"]
            y = pixel_data["y"]
            if not _is_int(x) or x < 0 or x >= GBA_SCREEN_WIDTH:
                raise PlaytestError(
                    f"{pixel_path}.x must be an integer from 0 through {GBA_SCREEN_WIDTH - 1}"
                )
            if not _is_int(y) or y < 0 or y >= GBA_SCREEN_HEIGHT:
                raise PlaytestError(
                    f"{pixel_path}.y must be an integer from 0 through {GBA_SCREEN_HEIGHT - 1}"
                )
            identity = (x, y)
            if identity in seen_pixels:
                raise PlaytestError(f"{pixel_path} duplicates coordinate ({x}, {y}) in this checkpoint")
            seen_pixels.add(identity)
            if not framebuffer:
                raise PlaytestError(f"{pixel_path} requires the checkpoint's framebuffer to be true")
            expected_pixel = pixel_data.get("expected")
            if expected_pixel is not None and (
                not isinstance(expected_pixel, str) or not PIXEL_RE.fullmatch(expected_pixel)
            ):
                raise PlaytestError(
                    f"{pixel_path}.expected must be lowercase 0x plus 6 hex digits (RRGGBB)"
                )
            pixel_probes.append(PixelProbe(x, y, expected_pixel))

        if not framebuffer and not probes and not sram_hash:
            raise PlaytestError(
                f"{path} must capture a framebuffer, sram_hash, or at least one probe"
            )
        checkpoints.append(
            Checkpoint(
                checkpoint_name,
                frame,
                framebuffer,
                expected_hash,
                sram_hash,
                expected_sram_hash,
                tuple(sram_hash_exclude_ranges),
                tuple(
                    sorted(
                        probes,
                        key=lambda probe: _probe_binding_sort_key(
                            probe.binding,
                            probe.size,
                        ),
                    )
                ),
                tuple(sorted(regions, key=lambda region: region.name)),
                tuple(sorted(pixel_probes, key=lambda pixel: (pixel.x, pixel.y))),
            )
        )
    if not disabled and inputs:
        last_checkpoint_frame = max(checkpoint.frame for checkpoint in checkpoints)
        if inputs[-1].end > last_checkpoint_frame:
            raise PlaytestError(
                f"{source}.frames[-1].end is after the final checkpoint frame "
                f"{last_checkpoint_frame}"
            )
    return Scenario(
        name,
        description,
        disabled,
        blocker,
        tuple(inputs),
        tuple(sorted(checkpoints, key=lambda checkpoint: (checkpoint.frame, checkpoint.name))),
        FIXED_SCENARIO_SCHEMA_VERSION,
        None,
    )


def _parse_run_probe(
    raw: Any,
    path: str,
    symbol_resolver: Callable[[str], tuple[int, int]] | None,
) -> Probe:
    data = _expect_object(raw, path, {"address", "size"})
    size = data["size"]
    if not _is_int(size) or size not in (1, 2, 4):
        raise PlaytestError(f"{path}.size must be integer 1, 2, or 4")
    binding, address = _parse_address(
        data["address"],
        size,
        f"{path}.address",
        symbol_resolver,
    )
    return Probe(binding, address, size, None)


def _parse_comparison(
    raw: Any,
    path: str,
    symbol_resolver: Callable[[str], tuple[int, int]] | None,
) -> Comparison:
    data = _expect_object(raw, path, {"address", "size", "operator", "value"})
    probe = _parse_run_probe(
        {"address": data["address"], "size": data["size"]},
        path,
        symbol_resolver,
    )
    operator = data["operator"]
    if not isinstance(operator, str) or operator not in COMPARISON_OPERATORS:
        raise PlaytestError(
            f"{path}.operator must be one of {', '.join(COMPARISON_OPERATORS)}"
        )
    pattern = re.compile(rf"^0x[0-9a-f]{{{probe.size * 2}}}$")
    value = data["value"]
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise PlaytestError(
            f"{path}.value must be lowercase 0x plus {probe.size * 2} hex digits"
        )
    return Comparison(probe, operator, int(value, 16))


def _parse_counter_limit(
    raw: Any,
    path: str,
    symbol_resolver: Callable[[str], tuple[int, int]] | None,
) -> CounterLimit:
    data = _expect_object(raw, path, {"maximum", "address", "size"})
    probe = _parse_run_probe(
        {"address": data["address"], "size": data["size"]},
        path,
        symbol_resolver,
    )
    maximum = data["maximum"]
    maximum_value = (1 << (probe.size * 8)) - 1
    if not _is_int(maximum) or maximum < 1 or maximum > maximum_value:
        raise PlaytestError(
            f"{path}.maximum must be an integer from 1 through {maximum_value} "
            f"for a {probe.size}-byte probe"
        )
    return CounterLimit(maximum, probe)


def _comparison_identity(comparison: Comparison) -> tuple[str, int, str, int]:
    return (
        comparison.probe.binding,
        comparison.probe.size,
        comparison.operator,
        comparison.value,
    )


def _comparisons_satisfiable(
    comparisons: Iterable[Comparison],
    limits: dict[tuple[str, int], int] | None = None,
) -> bool:
    constraints: dict[tuple[str, int], list[Any]] = {}
    for comparison in comparisons:
        identity = (comparison.probe.binding, comparison.probe.size)
        if identity not in constraints:
            constraints[identity] = [
                0,
                (1 << (comparison.probe.size * 8)) - 1,
                set(),
            ]
        lower, upper, excluded = constraints[identity]
        value = comparison.value
        if comparison.operator == "eq":
            lower = max(lower, value)
            upper = min(upper, value)
        elif comparison.operator == "ne":
            excluded.add(value)
        elif comparison.operator == "lt":
            upper = min(upper, value - 1)
        elif comparison.operator == "le":
            upper = min(upper, value)
        elif comparison.operator == "gt":
            lower = max(lower, value + 1)
        else:
            lower = max(lower, value)
        constraints[identity] = [lower, upper, excluded]

    for identity, maximum in (limits or {}).items():
        size = identity[1]
        if identity not in constraints:
            constraints[identity] = [0, (1 << (size * 8)) - 1, set()]
        constraints[identity][1] = min(constraints[identity][1], maximum)

    for lower, upper, excluded in constraints.values():
        if lower > upper:
            return False
        excluded_count = sum(lower <= value <= upper for value in excluded)
        if upper - lower + 1 <= excluded_count:
            return False
    return True


def _parse_run_until_scenario_data(
    data: Any,
    source: str,
    symbol_resolver: Callable[[str], tuple[int, int]] | None,
) -> Scenario:
    root = _expect_object(
        data,
        source,
        {"schema_version", "name", "frames", "run_until"},
        {"description"},
    )
    name = _expect_name(root["name"], f"{source}.name")
    description = root.get("description", "")
    if not isinstance(description, str):
        raise PlaytestError(f"{source}.description must be a string")
    run_path = f"{source}.run_until"
    run_data = _expect_object(
        root["run_until"],
        run_path,
        {"max_frames", "terminal_conditions", "checkpoint"},
        {"stall", "turn_limit", "action_limit"},
    )
    max_frames = run_data["max_frames"]
    if not _is_int(max_frames) or max_frames < 1 or max_frames > 10_000_001:
        raise PlaytestError(
            f"{run_path}.max_frames must be an integer from 1 through 10000001"
        )

    conditions_data = run_data["terminal_conditions"]
    if not isinstance(conditions_data, list) or not conditions_data:
        raise PlaytestError(f"{run_path}.terminal_conditions must be a non-empty array")
    if len(conditions_data) > len(TERMINAL_CONDITION_REASONS):
        raise PlaytestError(
            f"{run_path}.terminal_conditions has {len(conditions_data)} entries; "
            f"at most {len(TERMINAL_CONDITION_REASONS)} unique terminal reasons are supported"
        )
    terminal_conditions: list[TerminalCondition] = []
    seen_reasons: set[str] = set()
    comparison_count = 0
    for index, raw_condition in enumerate(conditions_data):
        condition_path = f"{run_path}.terminal_conditions[{index}]"
        condition_data = _expect_object(
            raw_condition, condition_path, {"reason", "all"}
        )
        reason = condition_data["reason"]
        if not isinstance(reason, str) or reason not in TERMINAL_CONDITION_REASONS:
            raise PlaytestError(
                f"{condition_path}.reason must be one of "
                f"{', '.join(TERMINAL_CONDITION_REASONS)}"
            )
        if reason in seen_reasons:
            raise PlaytestError(
                f"{condition_path}.reason duplicates terminal reason {reason!r}"
            )
        seen_reasons.add(reason)
        all_data = condition_data["all"]
        if not isinstance(all_data, list) or not all_data:
            raise PlaytestError(f"{condition_path}.all must be a non-empty array")
        comparisons = [
            _parse_comparison(
                raw_comparison,
                f"{condition_path}.all[{comparison_index}]",
                symbol_resolver,
            )
            for comparison_index, raw_comparison in enumerate(all_data)
        ]
        comparison_count += len(comparisons)
        identities = [_comparison_identity(comparison) for comparison in comparisons]
        if len(identities) != len(set(identities)):
            raise PlaytestError(f"{condition_path}.all contains a duplicate condition")
        if not _comparisons_satisfiable(comparisons):
            raise PlaytestError(
                f"{condition_path}.all is internally contradictory and can never match"
            )
        terminal_conditions.append(
            TerminalCondition(
                reason,
                tuple(
                    sorted(
                        comparisons,
                        key=lambda comparison: (
                            _probe_binding_sort_key(
                                comparison.probe.binding,
                                comparison.probe.size,
                            ),
                            COMPARISON_OPERATORS.index(comparison.operator),
                            comparison.value,
                        ),
                    )
                ),
            )
        )
    if "success" not in seen_reasons:
        raise PlaytestError(
            f"{run_path}.terminal_conditions must define exactly one success condition"
        )
    if comparison_count > MAX_RUN_UNTIL_COMPARISONS:
        raise PlaytestError(
            f"{run_path}.terminal_conditions contains {comparison_count} comparisons, "
            f"exceeding the {MAX_RUN_UNTIL_COMPARISONS}-comparison limit"
        )

    reason_order = {
        reason: index for index, reason in enumerate(TERMINAL_CONDITION_REASONS)
    }
    terminal_conditions.sort(key=lambda condition: reason_order[condition.reason])
    for left_index, left in enumerate(terminal_conditions):
        for right in terminal_conditions[left_index + 1 :]:
            if _comparisons_satisfiable(left.comparisons + right.comparisons):
                raise PlaytestError(
                    f"{run_path}.terminal_conditions for {left.reason!r} and "
                    f"{right.reason!r} overlap; terminal definitions must be "
                    "mutually exclusive"
                )

    stall: StallLimit | None = None
    if "stall" in run_data:
        stall_path = f"{run_path}.stall"
        stall_data = _expect_object(
            run_data["stall"],
            stall_path,
            {"max_unchanged_frames", "progress", "work_expected"},
        )
        max_unchanged_frames = stall_data["max_unchanged_frames"]
        if (
            not _is_int(max_unchanged_frames)
            or max_unchanged_frames < 1
            or max_unchanged_frames >= max_frames
        ):
            raise PlaytestError(
                f"{stall_path}.max_unchanged_frames must be an integer from 1 "
                f"through {max_frames - 1}, strictly below max_frames"
            )
        stall = StallLimit(
            max_unchanged_frames,
            _parse_run_probe(
                stall_data["progress"],
                f"{stall_path}.progress",
                symbol_resolver,
            ),
            _parse_comparison(
                stall_data["work_expected"],
                f"{stall_path}.work_expected",
                symbol_resolver,
            ),
        )

    turn_limit = (
        _parse_counter_limit(
            run_data["turn_limit"],
            f"{run_path}.turn_limit",
            symbol_resolver,
        )
        if "turn_limit" in run_data
        else None
    )
    action_limit = (
        _parse_counter_limit(
            run_data["action_limit"],
            f"{run_path}.action_limit",
            symbol_resolver,
        )
        if "action_limit" in run_data
        else None
    )

    success = next(
        condition for condition in terminal_conditions if condition.reason == "success"
    )
    counter_limits: dict[tuple[str, int], int] = {}
    for counter in (turn_limit, action_limit):
        if counter is None:
            continue
        identity = (counter.probe.binding, counter.probe.size)
        counter_limits[identity] = min(
            counter.maximum,
            counter_limits.get(identity, counter.maximum),
        )
    if not _comparisons_satisfiable(success.comparisons, counter_limits):
        raise PlaytestError(
            f"{run_path} success condition cannot occur within the declared "
            "turn/action bounds"
        )

    semantic_probes = [
        comparison.probe
        for condition in terminal_conditions
        for comparison in condition.comparisons
    ]
    if stall is not None:
        semantic_probes.extend((stall.progress, stall.work_expected.probe))
    semantic_probes.extend(
        counter.probe
        for counter in (turn_limit, action_limit)
        if counter is not None
    )
    unique_bindings = {
        (probe.binding, probe.size) for probe in semantic_probes
    }
    if len(unique_bindings) > MAX_RUN_UNTIL_PROBES:
        raise PlaytestError(
            f"{run_path} uses {len(unique_bindings)} semantic probes, exceeding "
            f"the {MAX_RUN_UNTIL_PROBES}-probe limit"
        )
    resolved_spans: dict[tuple[str, int], tuple[int, int]] = {}
    for probe in semantic_probes:
        if probe.address is None:
            continue
        identity = (probe.binding, probe.size)
        start = probe.address
        end = start + probe.size
        previous_span = resolved_spans.get(identity)
        if previous_span is not None:
            if previous_span != (start, end):
                raise PlaytestError(
                    f"{run_path} semantic probe {probe.binding!r}/{probe.size} "
                    "resolved inconsistently"
                )
            continue
        for (previous_binding, previous_size), (
            previous_start,
            previous_end,
        ) in resolved_spans.items():
            if start < previous_end and previous_start < end:
                raise PlaytestError(
                    f"{run_path} semantic probe {probe.binding!r}/{probe.size} "
                    f"span [0x{start:08x}, 0x{end:08x}) overlaps "
                    f"{previous_binding!r}/{previous_size} span "
                    f"[0x{previous_start:08x}, 0x{previous_end:08x}); "
                    "overlapping resolved byte spans require an explicit model"
                )
        resolved_spans[identity] = (start, end)

    checkpoint_data = _expect_object(
        run_data["checkpoint"],
        f"{run_path}.checkpoint",
        {"name", "framebuffer", "probes"},
        {
            "expected_framebuffer_hash",
            "sram_hash",
            "expected_sram_hash",
            "sram_hash_exclude_ranges",
            "regions",
            "pixel_probes",
        },
    )
    fixed_data = {
        "schema_version": FIXED_SCENARIO_SCHEMA_VERSION,
        "name": name,
        "description": description,
        "frames": root["frames"],
        "checkpoints": [
            {
                **checkpoint_data,
                "frame": max_frames - 1,
            }
        ],
    }
    fixed = _parse_fixed_scenario_data(fixed_data, source, symbol_resolver)
    return Scenario(
        fixed.name,
        fixed.description,
        False,
        None,
        fixed.inputs,
        fixed.checkpoints,
        RUN_UNTIL_SCENARIO_SCHEMA_VERSION,
        RunUntil(
            max_frames,
            tuple(terminal_conditions),
            stall,
            turn_limit,
            action_limit,
        ),
    )


def _parse_execution_profile(
    data: Any,
    source: str,
    run_until: RunUntil,
    symbol_resolver: Callable[[str], tuple[int, int]] | None,
) -> ExecutionProfile:
    path = f"{source}.execution_profile"
    profile_data = _expect_object(
        data,
        path,
        {"name", "trace"},
        {"config_apply_frame", "play_state_config"},
    )
    name = profile_data["name"]
    if not isinstance(name, str) or name not in EXECUTION_PROFILE_NAMES:
        raise PlaytestError(
            f"{path}.name must be one of {', '.join(EXECUTION_PROFILE_NAMES)}"
        )
    trace_data = profile_data["trace"]
    if not isinstance(trace_data, list) or not trace_data:
        raise PlaytestError(f"{path}.trace must be a non-empty array")
    if len(trace_data) > MAX_PROFILE_TRACE_PROBES:
        raise PlaytestError(
            f"{path}.trace has {len(trace_data)} probes, exceeding the "
            f"{MAX_PROFILE_TRACE_PROBES}-probe limit"
        )
    trace_probes = tuple(
        _parse_run_probe(
            raw_probe,
            f"{path}.trace[{index}]",
            symbol_resolver,
        )
        for index, raw_probe in enumerate(trace_data)
    )
    bindings = [(probe.binding, probe.size) for probe in trace_probes]
    if len(bindings) != len(set(bindings)):
        raise PlaytestError(f"{path}.trace contains duplicate probes")
    resolved: dict[tuple[int, int], str] = {}
    for probe in trace_probes:
        if probe.address is None:
            continue
        identity = (probe.address, probe.size)
        previous = resolved.get(identity)
        if previous is not None and previous != probe.binding:
            raise PlaytestError(
                f"{path}.trace probes {previous!r} and {probe.binding!r} "
                "resolve to the same address/size"
            )
        resolved[identity] = probe.binding
    trace_probes = tuple(
        sorted(
            trace_probes,
            key=lambda probe: _probe_binding_sort_key(probe.binding, probe.size),
        )
    )
    trace_record_count = run_until.max_frames * len(trace_probes)
    if trace_record_count > MAX_PROFILE_TRACE_RECORDS:
        raise PlaytestError(
            f"{path}.trace can emit {trace_record_count} records, exceeding the "
            f"{MAX_PROFILE_TRACE_RECORDS}-record aggregate limit"
        )

    if name == EXECUTION_PROFILE_NORMAL_FIDELITY:
        if "config_apply_frame" in profile_data or "play_state_config" in profile_data:
            raise PlaytestError(
                f"{path} normal-fidelity must not apply runtime configuration"
            )
        return ExecutionProfile(name, None, None, trace_probes)

    if "config_apply_frame" not in profile_data or "play_state_config" not in profile_data:
        raise PlaytestError(
            f"{path} accelerated-fidelity requires config_apply_frame and "
            "play_state_config"
        )
    config_apply_frame = _expect_frame(
        profile_data["config_apply_frame"],
        f"{path}.config_apply_frame",
    )
    if config_apply_frame >= run_until.max_frames:
        raise PlaytestError(
            f"{path}.config_apply_frame must be below run_until.max_frames"
        )
    play_state_config = _parse_run_probe(
        profile_data["play_state_config"],
        f"{path}.play_state_config",
        symbol_resolver,
    )
    if play_state_config.size != 4:
        raise PlaytestError(f"{path}.play_state_config.size must be 4")
    return ExecutionProfile(
        name,
        config_apply_frame,
        play_state_config,
        trace_probes,
    )


def _parse_accelerated_fidelity_scenario_data(
    data: Any,
    source: str,
    symbol_resolver: Callable[[str], tuple[int, int]] | None,
) -> Scenario:
    root = _expect_object(
        data,
        source,
        {"schema_version", "name", "frames", "run_until", "execution_profile"},
        {"description"},
    )
    base_data = {
        "schema_version": RUN_UNTIL_SCENARIO_SCHEMA_VERSION,
        "name": root["name"],
        "frames": root["frames"],
        "run_until": root["run_until"],
    }
    if "description" in root:
        base_data["description"] = root["description"]
    bounded = _parse_run_until_scenario_data(base_data, source, symbol_resolver)
    assert bounded.run_until is not None
    profile = _parse_execution_profile(
        root["execution_profile"],
        source,
        bounded.run_until,
        symbol_resolver,
    )
    if profile.name == EXECUTION_PROFILE_ACCELERATED_FIDELITY:
        for checkpoint in bounded.checkpoints:
            if checkpoint.framebuffer or checkpoint.regions or checkpoint.pixel_probes:
                raise PlaytestError(
                    f"{source} accelerated-fidelity requires semantic-only "
                    f"checkpoint {checkpoint.name!r} without framebuffer, "
                    "region, or pixel evidence"
                )
    return Scenario(
        bounded.name,
        bounded.description,
        bounded.disabled,
        bounded.blocker,
        bounded.inputs,
        bounded.checkpoints,
        ACCELERATED_FIDELITY_SCENARIO_SCHEMA_VERSION,
        bounded.run_until,
        profile,
    )


def parse_scenario_data(
    data: Any,
    source: str = "<scenario>",
    symbol_resolver: Callable[[str], tuple[int, int]] | None = None,
) -> Scenario:
    if not isinstance(data, dict):
        raise PlaytestError(f"{source} must be an object")
    schema_version = data.get("schema_version")
    if not _is_int(schema_version):
        raise PlaytestError(
            f"{source}.schema_version must be integer "
            f"{FIXED_SCENARIO_SCHEMA_VERSION} or "
            f"{RUN_UNTIL_SCENARIO_SCHEMA_VERSION} or "
            f"{ACCELERATED_FIDELITY_SCENARIO_SCHEMA_VERSION}"
        )
    if schema_version == FIXED_SCENARIO_SCHEMA_VERSION:
        return _parse_fixed_scenario_data(data, source, symbol_resolver)
    if schema_version == RUN_UNTIL_SCENARIO_SCHEMA_VERSION:
        return _parse_run_until_scenario_data(data, source, symbol_resolver)
    if schema_version == ACCELERATED_FIDELITY_SCENARIO_SCHEMA_VERSION:
        return _parse_accelerated_fidelity_scenario_data(
            data,
            source,
            symbol_resolver,
        )
    raise PlaytestError(
        f"{source}.schema_version must be integer "
        f"{FIXED_SCENARIO_SCHEMA_VERSION}, {RUN_UNTIL_SCENARIO_SCHEMA_VERSION}, "
        f"or {ACCELERATED_FIDELITY_SCENARIO_SCHEMA_VERSION}"
    )


def load_scenario(
    path: Path,
    symbol_resolver: Callable[[str], tuple[int, int]] | None = None,
) -> Scenario:
    return parse_scenario_data(_read_json(path), str(path), symbol_resolver)


def serialize_fingerprint(fingerprint: dict[str, Any]) -> str:
    return json.dumps(fingerprint, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _header_text(raw: bytes) -> str:
    raw = raw.rstrip(b"\x00 ")
    return "".join(
        chr(byte) if 0x20 <= byte <= 0x7E else f"\\x{byte:02x}" for byte in raw
    )


def rom_provenance(path: Path) -> dict[str, Any]:
    digest = hashlib.sha1()
    try:
        size = path.stat().st_size
        with path.open("rb") as rom:
            header = rom.read(0xB0)
            digest.update(header)
            while chunk := rom.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise PlaytestError(f"cannot fingerprint ROM {path}: {exc}") from exc
    if len(header) < 0xB0:
        raise PlaytestError(f"ROM is too small to contain a GBA header: {path}")
    return {
        "game_code": _header_text(header[0xAC:0xB0]),
        "sha1": digest.hexdigest(),
        "size": size,
        "title": _header_text(header[0xA0:0xAC]),
    }


def _bounded_retry_count(retries: int) -> int:
    """Clamps a caller-requested retry count into [0, MAX_RETRIES_CAP]."""
    return max(0, min(retries, MAX_RETRIES_CAP))


def _run_transient_retryable(
    command: list[str], *, timeout: float, retries: int, operation: str
) -> subprocess.CompletedProcess[str]:
    """Runs `command`, retrying only a process time-out -- the one condition
    here that can plausibly be transient host scheduling/load rather than a
    deterministic outcome. A non-zero exit code is returned as-is (the
    caller decides what it means) and is never retried here, and callers
    must never retry a fingerprint mismatch or malformed-output diagnostic
    either: retrying those would silently launder a real, reproducible
    failure into intermittent-looking flake. `retries` is bounded by
    `MAX_RETRIES_CAP` regardless of the caller-supplied value. Every timed
    out attempt that will be retried is reported on stderr with its 1-based
    attempt number out of the total planned attempts, so a flaky time out
    is always visible in the log even when a later attempt succeeds; the
    final attempt's time-out is left for the caller to turn into an
    actionable `PlaytestError` exactly as before (naming the same total
    attempt count).
    """
    attempts = _bounded_retry_count(retries) + 1
    for attempt in range(1, attempts + 1):
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            if attempt >= attempts:
                raise
            print(
                f"gba-playtest: {operation} attempt {attempt}/{attempts} timed out "
                f"after {timeout:g}s -- retrying (transient timeout only; a "
                "non-zero exit or mismatched/malformed output is never retried)",
                file=sys.stderr,
            )
    raise AssertionError("unreachable: loop always returns or raises")


def _compiler_command(
    source: Path, output: Path, retries: int = 0
) -> list[str]:
    cc = os.environ.get("CC", "cc")
    if not shutil.which(cc):
        raise PlaytestError(f"libmGBA backend unavailable: C compiler {cc!r} was not found")
    command = [
        cc,
        "-std=gnu11",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        str(source),
        "-o",
        str(output),
    ]
    pkg_config = shutil.which("pkg-config")
    if pkg_config:
        try:
            flags = _run_transient_retryable(
                [pkg_config, "--cflags", "--libs", "mgba"],
                timeout=PKG_CONFIG_TIMEOUT_SECONDS,
                retries=retries,
                operation="pkg-config",
            )
        except subprocess.TimeoutExpired as exc:
            attempts = _bounded_retry_count(retries) + 1
            raise PlaytestError(
                f"pkg-config timed out after {PKG_CONFIG_TIMEOUT_SECONDS}s "
                f"while locating libmGBA (attempt {attempts}/{attempts}, "
                "no attempts remaining)"
            ) from exc
        if flags.returncode == 0:
            import shlex

            command.extend(shlex.split(flags.stdout))
            return command
    command.append("-lmgba")
    return command


def build_backend(output: Path, retries: int = 0) -> None:
    source = Path(__file__).with_name("backend.c")
    command = _compiler_command(source, output, retries)
    try:
        result = _run_transient_retryable(
            command,
            timeout=COMPILER_TIMEOUT_SECONDS,
            retries=retries,
            operation="libmGBA backend compilation",
        )
    except subprocess.TimeoutExpired as exc:
        attempts = _bounded_retry_count(retries) + 1
        raise PlaytestError(
            f"libmGBA backend compilation timed out after "
            f"{COMPILER_TIMEOUT_SECONDS}s (attempt {attempts}/{attempts}, "
            "no attempts remaining)"
        ) from exc
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "compiler returned no diagnostics"
        raise PlaytestError(
            "libmGBA backend unavailable: compilation failed.\n"
            "Install libmGBA development headers (Linux: libmgba-dev; macOS: brew install mgba).\n"
            f"Compiler diagnostic:\n{detail}"
        )


def _write_plan(path: Path, scenario: Scenario) -> None:
    # Fixed scenarios retain plan format 3 exactly. Format 4 appends bounded
    # run-until records. Format 5 is exclusive to schema-v3 fidelity profiles:
    # it permits semantic-only checkpoints to omit an unused framebuffer hash.
    # Plans are generated and consumed within one capture/verify invocation;
    # scenario and fingerprint compatibility lives in their JSON versions.
    if scenario.execution_profile is not None:
        plan_version = 5
    elif scenario.run_until is not None:
        plan_version = 4
    else:
        plan_version = 3
    lines = [f"GBA_PLAYTEST_PLAN {plan_version}", f"RANGES {len(scenario.inputs)}"]
    lines.extend(
        f"{frame_range.start} {frame_range.end} {frame_range.key_mask}"
        for frame_range in scenario.inputs
    )
    lines.append(f"CHECKPOINTS {len(scenario.checkpoints)}")
    for checkpoint in scenario.checkpoints:
        checkpoint_record = (
            f"{checkpoint.frame} {len(checkpoint.probes)} {int(checkpoint.sram_hash)} "
            f"{len(checkpoint.sram_hash_exclude_ranges)} {len(checkpoint.regions)} "
            f"{len(checkpoint.pixel_probes)}"
        )
        if plan_version == 5:
            checkpoint_record += f" {int(checkpoint.framebuffer)}"
        lines.append(checkpoint_record)
        lines.extend(
            f"{offset} {length}"
            for offset, length in checkpoint.sram_hash_exclude_ranges
        )
        for probe in checkpoint.probes:
            if probe.address is None:
                raise PlaytestError(
                    f"scenario {scenario.name!r} probe {probe.binding!r} has no "
                    "resolved execution address; supply the exact linked ELF with --elf"
                )
            lines.append(f"{probe.address} {probe.size}")
        lines.extend(
            f"{region.x} {region.y} {region.width} {region.height}"
            for region in checkpoint.regions
        )
        lines.extend(f"{pixel.x} {pixel.y}" for pixel in checkpoint.pixel_probes)
    if scenario.run_until is not None:
        run_until = scenario.run_until
        run_probes: list[Probe] = []
        probe_indexes: dict[tuple[str, int], int] = {}

        def add_probe(probe: Probe) -> int:
            identity = (probe.binding, probe.size)
            if identity not in probe_indexes:
                if probe.address is None:
                    raise PlaytestError(
                        f"scenario {scenario.name!r} run-until probe "
                        f"{probe.binding!r} has no resolved execution address; "
                        "supply the exact linked ELF with --elf"
                    )
                probe_indexes[identity] = len(run_probes)
                run_probes.append(probe)
            return probe_indexes[identity]

        for condition in run_until.terminal_conditions:
            for comparison in condition.comparisons:
                add_probe(comparison.probe)
        if run_until.stall is not None:
            add_probe(run_until.stall.progress)
            add_probe(run_until.stall.work_expected.probe)
        for counter in (run_until.turn_limit, run_until.action_limit):
            if counter is not None:
                add_probe(counter.probe)

        lines.append(f"RUN_UNTIL {run_until.max_frames}")
        lines.append(f"RUN_PROBES {len(run_probes)}")
        lines.extend(f"{probe.address} {probe.size}" for probe in run_probes)
        lines.append(f"TERMINALS {len(run_until.terminal_conditions)}")
        for condition in run_until.terminal_conditions:
            lines.append(
                f"{PLAN_TERMINAL_REASON_CODES[condition.reason]} "
                f"{len(condition.comparisons)}"
            )
            for comparison in condition.comparisons:
                lines.append(
                    f"{add_probe(comparison.probe)} "
                    f"{PLAN_OPERATOR_CODES[comparison.operator]} "
                    f"{comparison.value}"
                )
        if run_until.stall is None:
            lines.append("STALL 0")
        else:
            stall = run_until.stall
            lines.extend(
                (
                    "STALL 1",
                    f"{add_probe(stall.progress)} "
                    f"{add_probe(stall.work_expected.probe)} "
                    f"{PLAN_OPERATOR_CODES[stall.work_expected.operator]} "
                    f"{stall.work_expected.value} {stall.max_unchanged_frames}",
                )
            )
        for label, counter in (
            ("TURN_LIMIT", run_until.turn_limit),
            ("ACTION_LIMIT", run_until.action_limit),
        ):
            if counter is None:
                lines.append(f"{label} 0")
            else:
                lines.extend(
                    (
                        f"{label} 1",
                        f"{add_probe(counter.probe)} {counter.maximum}",
                    )
                )
    if scenario.execution_profile is not None:
        profile = scenario.execution_profile
        profile_code = PLAN_EXECUTION_PROFILE_CODES[profile.name]
        if profile.play_state_config is None:
            config_apply_frame = 0
            config_address = 0
        else:
            if profile.play_state_config.address is None:
                raise PlaytestError(
                    f"scenario {scenario.name!r} profile config "
                    "has no resolved execution address; supply the exact linked ELF with --elf"
                )
            assert profile.config_apply_frame is not None
            config_apply_frame = profile.config_apply_frame
            config_address = profile.play_state_config.address
        lines.append(f"PROFILE {profile_code} {config_apply_frame} {config_address}")
        lines.append(f"TRACE {len(profile.trace_probes)}")
        for probe in profile.trace_probes:
            if probe.address is None:
                raise PlaytestError(
                    f"scenario {scenario.name!r} trace probe {probe.binding!r} "
                    "has no resolved execution address; supply the exact linked ELF with --elf"
                )
            lines.append(f"{probe.address} {probe.size}")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def _parse_backend_output(stdout: str, scenario: Scenario) -> dict[str, Any]:
    hashes: dict[int, str] = {}
    checkpoint_frames: dict[int, int] = {}
    sram_hashes: dict[int, str] = {}
    values: dict[tuple[int, int], int] = {}
    region_hashes: dict[tuple[int, int], str] = {}
    pixel_values: dict[tuple[int, int], int] = {}
    terminal: tuple[str, int, bool, int, bool, int] | None = None
    profile_record: tuple[int, int, int] | None = None
    trace_snapshots: list[tuple[int, dict[int, int]]] = []
    trace_record_count = 0
    for line_number, line in enumerate(stdout.splitlines(), 1):
        fields = line.split("\t")
        try:
            if fields[0] == "CHECKPOINT" and len(fields) in (3, 4):
                checkpoint_index = int(fields[1])
                frame = int(fields[2])
                checkpoint = scenario.checkpoints[checkpoint_index] if (
                    0 <= checkpoint_index < len(scenario.checkpoints)
                ) else None
                if checkpoint is None:
                    raise ValueError("checkpoint index out of range")
                if checkpoint_index in checkpoint_frames:
                    raise ValueError("duplicate checkpoint")
                if scenario.run_until is None:
                    if frame != checkpoint.frame:
                        raise ValueError("checkpoint frame does not match plan")
                elif (
                    checkpoint_index != 0
                    or frame < 0
                    or frame >= scenario.run_until.max_frames
                ):
                    raise ValueError("terminal checkpoint frame is outside run-until bounds")
                checkpoint_frames[checkpoint_index] = frame
                # Plan formats 3/4 always emitted a whole-frame hash, even
                # when Python did not consume it. Schema-v3 profile plans use
                # format 5 and omit that work for semantic-only checkpoints.
                expects_framebuffer_hash = (
                    checkpoint.framebuffer or scenario.execution_profile is None
                )
                if expects_framebuffer_hash:
                    if len(fields) != 4 or not re.fullmatch(r"[0-9a-f]{16}", fields[3]):
                        raise ValueError("malformed hash")
                    hashes[checkpoint_index] = f"fnv1a64-rgb24:{fields[3]}"
                elif len(fields) != 3:
                    raise ValueError("unexpected framebuffer hash")
            elif len(fields) == 7 and fields[0] == "TERMINAL":
                if scenario.run_until is None:
                    raise ValueError("unexpected terminal record for fixed-frame scenario")
                if terminal is not None:
                    raise ValueError("duplicate terminal record")
                reason = fields[1]
                frame = int(fields[2])
                turn_present = int(fields[3])
                turn_value = int(fields[4])
                action_present = int(fields[5])
                action_value = int(fields[6])
                if reason not in TERMINAL_REASONS:
                    raise ValueError("unknown terminal reason")
                if frame < 0 or frame >= scenario.run_until.max_frames:
                    raise ValueError("terminal frame is outside run-until bounds")
                if turn_present not in (0, 1) or action_present not in (0, 1):
                    raise ValueError("terminal counter presence flag is not 0 or 1")
                if bool(turn_present) != (scenario.run_until.turn_limit is not None):
                    raise ValueError("terminal turn presence does not match the plan")
                if bool(action_present) != (
                    scenario.run_until.action_limit is not None
                ):
                    raise ValueError("terminal action presence does not match the plan")
                for present, value, limit, label in (
                    (
                        turn_present,
                        turn_value,
                        scenario.run_until.turn_limit,
                        "turn",
                    ),
                    (
                        action_present,
                        action_value,
                        scenario.run_until.action_limit,
                        "action",
                    ),
                ):
                    if not present and value != 0:
                        raise ValueError(f"absent terminal {label} value must be zero")
                    if limit is not None and not (
                        0 <= value < 1 << (limit.probe.size * 8)
                    ):
                        raise ValueError(f"terminal {label} value exceeds probe width")
                declared_reasons = {
                    condition.reason
                    for condition in scenario.run_until.terminal_conditions
                }
                if reason in TERMINAL_CONDITION_REASONS and reason not in declared_reasons:
                    raise ValueError("terminal reason is not declared by the scenario")
                if reason == "engine_stall" and scenario.run_until.stall is None:
                    raise ValueError("engine_stall is not configured by the scenario")
                if reason == "max_frames" and frame != scenario.run_until.max_frames - 1:
                    raise ValueError("max_frames did not occur at the final bounded frame")
                if reason == "max_turns":
                    if scenario.run_until.turn_limit is None:
                        raise ValueError("max_turns is not configured by the scenario")
                    if turn_value < scenario.run_until.turn_limit.maximum:
                        raise ValueError("max_turns did not reach the configured limit")
                if reason == "max_actions":
                    if scenario.run_until.action_limit is None:
                        raise ValueError("max_actions is not configured by the scenario")
                    if action_value < scenario.run_until.action_limit.maximum:
                        raise ValueError("max_actions did not reach the configured limit")
                terminal = (
                    reason,
                    frame,
                    bool(turn_present),
                    turn_value,
                    bool(action_present),
                    action_value,
                )
            elif len(fields) == 4 and fields[0] == "PROFILE":
                if scenario.execution_profile is None:
                    raise ValueError("unexpected profile record")
                if (
                    scenario.execution_profile.name
                    != EXECUTION_PROFILE_ACCELERATED_FIDELITY
                ):
                    raise ValueError("normal-fidelity profile emitted a configuration record")
                if profile_record is not None:
                    raise ValueError("duplicate profile record")
                frame = int(fields[1])
                before = int(fields[2], 16)
                after = int(fields[3], 16)
                if (
                    scenario.execution_profile.config_apply_frame is None
                    or frame != scenario.execution_profile.config_apply_frame
                    or not (0 <= before <= 0xFFFFFFFF)
                    or not (0 <= after <= 0xFFFFFFFF)
                ):
                    raise ValueError("malformed profile record")
                profile_record = (frame, before, after)
            elif len(fields) == 4 and fields[0] == "TRACE":
                if scenario.execution_profile is None:
                    raise ValueError("unexpected trace record")
                trace_record_count += 1
                if trace_record_count > MAX_PROFILE_TRACE_RECORDS:
                    raise ValueError("trace records exceed aggregate budget")
                frame = int(fields[1])
                probe_index = int(fields[2])
                value = int(fields[3])
                profile = scenario.execution_profile
                if (
                    frame < 0
                    or scenario.run_until is None
                    or frame >= scenario.run_until.max_frames
                ):
                    raise ValueError("trace frame is outside run-until bounds")
                if not (0 <= probe_index < len(profile.trace_probes)):
                    raise ValueError("trace probe index out of range")
                probe = profile.trace_probes[probe_index]
                if not (0 <= value < 1 << (probe.size * 8)):
                    raise ValueError("trace value exceeds declared width")
                if not trace_snapshots or trace_snapshots[-1][0] != frame:
                    if trace_snapshots and frame <= trace_snapshots[-1][0]:
                        raise ValueError("trace frames are not strictly increasing")
                    trace_snapshots.append((frame, {}))
                values_at_frame = trace_snapshots[-1][1]
                if probe_index in values_at_frame:
                    raise ValueError("duplicate trace probe")
                values_at_frame[probe_index] = value
            elif len(fields) == 3 and fields[0] == "SRAMHASH":
                checkpoint_index = int(fields[1])
                if checkpoint_index in sram_hashes:
                    raise ValueError("duplicate sram hash")
                if not (0 <= checkpoint_index < len(scenario.checkpoints)):
                    raise ValueError("sram hash checkpoint index out of range")
                sram_checkpoint = scenario.checkpoints[checkpoint_index]
                if not sram_checkpoint.sram_hash:
                    raise ValueError("unexpected sram hash for checkpoint without sram_hash")
                if not re.fullmatch(r"[0-9a-f]{16}", fields[2]):
                    raise ValueError("malformed sram hash")
                # The algorithm name always identifies whether any bytes
                # were excluded, so "exact" and "normalized" hashes can
                # never be silently confused with each other downstream.
                algorithm = (
                    "fnv1a64-sram-normalized"
                    if sram_checkpoint.sram_hash_exclude_ranges
                    else "fnv1a64-sram"
                )
                sram_hashes[checkpoint_index] = f"{algorithm}:{fields[2]}"
            elif len(fields) == 4 and fields[0] == "PROBE":
                checkpoint_index = int(fields[1])
                probe_index = int(fields[2])
                value = int(fields[3])
                identity = (checkpoint_index, probe_index)
                if identity in values:
                    raise ValueError("duplicate probe")
                if not (0 <= checkpoint_index < len(scenario.checkpoints)):
                    raise ValueError("probe checkpoint index out of range")
                if not (0 <= probe_index < len(scenario.checkpoints[checkpoint_index].probes)):
                    raise ValueError("probe index out of range")
                probe_size = scenario.checkpoints[checkpoint_index].probes[probe_index].size
                if not (0 <= value < 1 << (probe_size * 8)):
                    raise ValueError("probe value exceeds declared width")
                values[identity] = value
            elif len(fields) == 4 and fields[0] == "REGIONHASH":
                checkpoint_index = int(fields[1])
                region_index = int(fields[2])
                identity = (checkpoint_index, region_index)
                if identity in region_hashes:
                    raise ValueError("duplicate region hash")
                if not (0 <= checkpoint_index < len(scenario.checkpoints)):
                    raise ValueError("region hash checkpoint index out of range")
                if not (
                    0
                    <= region_index
                    < len(scenario.checkpoints[checkpoint_index].regions)
                ):
                    raise ValueError("region hash index out of range")
                if not re.fullmatch(r"[0-9a-f]{16}", fields[3]):
                    raise ValueError("malformed region hash")
                region_hashes[identity] = f"fnv1a64-region:{fields[3]}"
            elif len(fields) == 4 and fields[0] == "PIXEL":
                checkpoint_index = int(fields[1])
                pixel_index = int(fields[2])
                identity = (checkpoint_index, pixel_index)
                if identity in pixel_values:
                    raise ValueError("duplicate pixel probe")
                if not (0 <= checkpoint_index < len(scenario.checkpoints)):
                    raise ValueError("pixel probe checkpoint index out of range")
                if not (
                    0
                    <= pixel_index
                    < len(scenario.checkpoints[checkpoint_index].pixel_probes)
                ):
                    raise ValueError("pixel probe index out of range")
                if not re.fullmatch(r"[0-9a-f]{6}", fields[3]):
                    raise ValueError("malformed pixel value")
                pixel_values[identity] = int(fields[3], 16)
            else:
                raise ValueError("unknown record")
        except ValueError as exc:
            raise PlaytestError(
                f"malformed backend output at line {line_number}: {line!r} ({exc})"
            ) from exc
    if len(checkpoint_frames) != len(scenario.checkpoints):
        raise PlaytestError(
            f"backend returned {len(checkpoint_frames)} of "
            f"{len(scenario.checkpoints)} checkpoints"
        )
    expected_framebuffer_hash_count = (
        len(scenario.checkpoints)
        if scenario.execution_profile is None
        else sum(1 for checkpoint in scenario.checkpoints if checkpoint.framebuffer)
    )
    if len(hashes) != expected_framebuffer_hash_count:
        raise PlaytestError(
            f"backend returned {len(hashes)} of {expected_framebuffer_hash_count} "
            "framebuffer hashes"
        )
    if scenario.run_until is None:
        if terminal is not None:
            raise PlaytestError("backend returned a terminal record for a fixed scenario")
    else:
        if terminal is None:
            raise PlaytestError("backend returned no run-until terminal record")
        if len(scenario.checkpoints) != 1:
            raise PlaytestError("run-until scenario must have exactly one checkpoint")
        if checkpoint_frames.get(0) != terminal[1]:
            raise PlaytestError(
                "backend terminal and checkpoint frames do not match exactly"
            )
    if scenario.execution_profile is None:
        if profile_record is not None or trace_snapshots:
            raise PlaytestError("backend returned profile output for a normal scenario")
    else:
        profile = scenario.execution_profile
        if profile.name == EXECUTION_PROFILE_ACCELERATED_FIDELITY:
            if profile_record is None:
                raise PlaytestError("backend returned no accelerated profile record")
        elif profile_record is not None:
            raise PlaytestError("normal-fidelity profile returned a configuration record")
        if not trace_snapshots:
            raise PlaytestError("backend returned no semantic trace")
        expected_trace_indexes = set(range(len(profile.trace_probes)))
        for frame, values_at_frame in trace_snapshots:
            if set(values_at_frame) != expected_trace_indexes:
                raise PlaytestError(
                    f"backend trace at frame {frame} does not contain every profile probe"
                )
    expected_sram_hash_count = sum(
        1 for checkpoint in scenario.checkpoints if checkpoint.sram_hash
    )
    if len(sram_hashes) != expected_sram_hash_count:
        raise PlaytestError(
            f"backend returned {len(sram_hashes)} of {expected_sram_hash_count} SRAM hashes"
        )
    expected_probe_count = sum(len(checkpoint.probes) for checkpoint in scenario.checkpoints)
    if len(values) != expected_probe_count:
        raise PlaytestError(
            f"backend returned {len(values)} of {expected_probe_count} probes"
        )
    expected_region_count = sum(len(checkpoint.regions) for checkpoint in scenario.checkpoints)
    if len(region_hashes) != expected_region_count:
        raise PlaytestError(
            f"backend returned {len(region_hashes)} of {expected_region_count} region hashes"
        )
    expected_pixel_probe_count = sum(
        len(checkpoint.pixel_probes) for checkpoint in scenario.checkpoints
    )
    if len(pixel_values) != expected_pixel_probe_count:
        raise PlaytestError(
            f"backend returned {len(pixel_values)} of {expected_pixel_probe_count} pixel probes"
        )
    checkpoints: list[dict[str, Any]] = []
    for checkpoint_index, checkpoint in enumerate(scenario.checkpoints):
        captured: dict[str, Any] = {
            "frame": checkpoint_frames[checkpoint_index],
            "name": checkpoint.name,
            "probes": [],
        }
        if checkpoint.framebuffer:
            captured["framebuffer_hash"] = hashes[checkpoint_index]
        if checkpoint.sram_hash:
            captured["sram_hash"] = sram_hashes[checkpoint_index]
        captured["probes"] = [
            {
                "address": probe.binding,
                "size": probe.size,
                "value": f"0x{values[(checkpoint_index, probe_index)]:0{probe.size * 2}x}",
            }
            for probe_index, probe in enumerate(checkpoint.probes)
        ]
        if checkpoint.regions:
            captured["regions"] = [
                {
                    "name": region.name,
                    "x": region.x,
                    "y": region.y,
                    "width": region.width,
                    "height": region.height,
                    "hash": region_hashes[(checkpoint_index, region_index)],
                }
                for region_index, region in enumerate(checkpoint.regions)
            ]
        if checkpoint.pixel_probes:
            captured["pixel_probes"] = [
                {
                    "x": pixel.x,
                    "y": pixel.y,
                    "value": f"0x{pixel_values[(checkpoint_index, pixel_index)]:06x}",
                }
                for pixel_index, pixel in enumerate(checkpoint.pixel_probes)
            ]
        checkpoints.append(captured)
    fingerprint: dict[str, Any] = {
        "checkpoints": checkpoints,
        "format_version": (
            ACCELERATED_FIDELITY_FINGERPRINT_FORMAT_VERSION
            if scenario.execution_profile is not None
            else RUN_UNTIL_FINGERPRINT_FORMAT_VERSION
            if scenario.run_until is not None
            else FIXED_FINGERPRINT_FORMAT_VERSION
        ),
        "scenario": scenario.name,
    }
    if scenario.run_until is not None:
        assert terminal is not None
        reason, frame, turn_present, turn_value, action_present, action_value = terminal

        def captured_counter(
            limit: CounterLimit | None, present: bool, value: int
        ) -> dict[str, Any] | None:
            if limit is None:
                if present:
                    raise AssertionError("backend counter presence was already validated")
                return None
            if not present:
                raise AssertionError("backend counter presence was already validated")
            return {
                "address": limit.probe.binding,
                "size": limit.probe.size,
                "value": f"0x{value:0{limit.probe.size * 2}x}",
            }

        fingerprint["terminal"] = {
            "reason": reason,
            "frame": frame,
            "turn": captured_counter(
                scenario.run_until.turn_limit, turn_present, turn_value
            ),
            "actions": captured_counter(
                scenario.run_until.action_limit, action_present, action_value
            ),
        }
    if scenario.execution_profile is not None:
        profile = scenario.execution_profile
        captured_profile: dict[str, Any] = {"name": profile.name}
        if profile_record is not None:
            frame, before, after = profile_record
            captured_profile.update(
                config_apply_frame=frame,
                config_before=f"0x{before:08x}",
                config_after=f"0x{after:08x}",
            )
        fingerprint["profile"] = captured_profile
        fingerprint["trace"] = [
            {
                "frame": frame,
                "probes": [
                    {
                        "address": probe.binding,
                        "size": probe.size,
                        "value": f"0x{values_at_frame[index]:0{probe.size * 2}x}",
                    }
                    for index, probe in enumerate(profile.trace_probes)
                ],
            }
            for frame, values_at_frame in trace_snapshots
        ]
    return fingerprint


def capture(
    rom: Path,
    scenario: Scenario,
    sram_image: Path | None = None,
    retries: int = 0,
    backend_path: Path | None = None,
) -> dict[str, Any]:
    if scenario.disabled:
        raise PlaytestError(f"scenario {scenario.name!r} is disabled: {scenario.blocker}")
    if not rom.is_file():
        raise PlaytestError(f"ROM does not exist or is not a regular file: {rom}")
    if sram_image is not None:
        if not sram_image.is_file():
            raise PlaytestError(f"SRAM image does not exist or is not a regular file: {sram_image}")
        actual_size = sram_image.stat().st_size
        if actual_size != SRAM_IMAGE_SIZE:
            raise PlaytestError(
                f"SRAM image {sram_image} must be exactly {SRAM_IMAGE_SIZE} (0x8000) bytes, "
                f"got {actual_size}"
            )
    with tempfile.TemporaryDirectory(prefix="gba-playtest-") as temporary:
        temporary_path = Path(temporary)
        backend = (
            backend_path
            if backend_path is not None
            else temporary_path / "gba-playtest-backend"
        )
        plan = temporary_path / "plan.txt"
        execution_rom = temporary_path / "input.gba"
        try:
            shutil.copyfile(rom, execution_rom)
            execution_rom.chmod(0o400)
        except OSError as exc:
            raise PlaytestError(f"cannot stage ROM {rom} for deterministic execution: {exc}") from exc
        execution_sram: Path | None = None
        if sram_image is not None:
            execution_sram = temporary_path / "input.sav"
            try:
                shutil.copyfile(sram_image, execution_sram)
                # Left writable (unlike the ROM copy above): libmGBA opens
                # save data read-write since gameplay may legitimately
                # write to it, and this is a disposable temporary copy the
                # original sram_image is never mutated through.
            except OSError as exc:
                raise PlaytestError(
                    f"cannot stage SRAM image {sram_image} for deterministic execution: {exc}"
                ) from exc
        # The identity is computed from the immutable temporary copy passed to
        # libmGBA, avoiding a path-replacement race between hashing and loading.
        provenance = rom_provenance(execution_rom)
        if backend_path is None:
            build_backend(backend, retries)
        elif not backend.is_file():
            raise PlaytestError(f"prebuilt libmGBA backend does not exist: {backend}")
        _write_plan(plan, scenario)
        last_frame = (
            scenario.run_until.max_frames - 1
            if scenario.run_until is not None
            else scenario.checkpoints[-1].frame
        )
        backend_timeout = min(
            MAX_BACKEND_TIMEOUT_SECONDS,
            max(MIN_BACKEND_TIMEOUT_SECONDS, 10 + last_frame / 30),
        )
        backend_args = [str(backend), str(execution_rom), str(plan)]
        if execution_sram is not None:
            backend_args.append(str(execution_sram))
        try:
            result = _run_transient_retryable(
                backend_args,
                timeout=backend_timeout,
                retries=retries,
                operation="libmGBA backend",
            )
        except subprocess.TimeoutExpired as exc:
            attempts = _bounded_retry_count(retries) + 1
            raise PlaytestError(
                f"libmGBA backend timed out after {backend_timeout:g}s "
                f"while running through frame {last_frame} "
                f"(attempt {attempts}/{attempts}, no attempts remaining)"
            ) from exc
        if result.returncode:
            diagnostic = result.stderr.strip() or "backend returned no diagnostic"
            raise PlaytestError(
                f"libmGBA backend failed with exit {result.returncode}: {diagnostic}"
            )
        fingerprint = _parse_backend_output(result.stdout, scenario)
        fingerprint["rom"] = provenance
    inline_differences = compare_inline_expectations(scenario, fingerprint)
    if inline_differences:
        raise PlaytestError(
            "scenario inline expectation mismatch:\n"
            + "\n".join(f"  - {difference}" for difference in inline_differences)
        )
    return fingerprint


def compare_inline_expectations(
    scenario: Scenario, fingerprint: dict[str, Any]
) -> list[str]:
    differences: list[str] = []
    actual_checkpoints = fingerprint["checkpoints"]
    for checkpoint, actual in zip(scenario.checkpoints, actual_checkpoints):
        prefix = f"checkpoint {checkpoint.name!r} (frame {actual['frame']})"
        if (
            checkpoint.expected_framebuffer_hash is not None
            and actual.get("framebuffer_hash") != checkpoint.expected_framebuffer_hash
        ):
            differences.append(
                f"{prefix} framebuffer_hash: expected "
                f"{checkpoint.expected_framebuffer_hash!r}, actual "
                f"{actual.get('framebuffer_hash')!r}"
            )
        if (
            checkpoint.expected_sram_hash is not None
            and actual.get("sram_hash") != checkpoint.expected_sram_hash
        ):
            differences.append(
                f"{prefix} sram_hash: expected "
                f"{checkpoint.expected_sram_hash!r}, actual "
                f"{actual.get('sram_hash')!r}"
            )
        for probe_index, probe in enumerate(checkpoint.probes):
            if probe.expected is None:
                continue
            actual_value = actual["probes"][probe_index]["value"]
            if actual_value != probe.expected:
                differences.append(
                    f"{prefix} probe {probe.binding}/{probe.size}: "
                    f"expected {probe.expected!r}, actual {actual_value!r}"
                )
        for region_index, region in enumerate(checkpoint.regions):
            if region.expected_hash is None:
                continue
            actual_hash = actual["regions"][region_index]["hash"]
            if actual_hash != region.expected_hash:
                differences.append(
                    f"{prefix} region {region.name!r}: "
                    f"expected {region.expected_hash!r}, actual {actual_hash!r}"
                )
        for pixel_index, pixel in enumerate(checkpoint.pixel_probes):
            if pixel.expected is None:
                continue
            actual_pixel = actual["pixel_probes"][pixel_index]["value"]
            if actual_pixel != pixel.expected:
                differences.append(
                    f"{prefix} pixel ({pixel.x}, {pixel.y}): "
                    f"expected {pixel.expected!r}, actual {actual_pixel!r}"
                )
    return differences


def validate_fingerprint(
    data: Any,
    source: str,
    symbol_resolver: Callable[[str], tuple[int, int]] | None = None,
    policy: str = "exact-rom",
) -> dict[str, Any]:
    if policy not in ("exact-rom", "behavior"):
        raise ValueError(f"unknown verification policy: {policy}")
    if not isinstance(data, dict):
        raise PlaytestError(f"{source} must be an object")
    format_version = data.get("format_version")
    if format_version == FIXED_FINGERPRINT_FORMAT_VERSION:
        return _validate_fixed_fingerprint(data, source, symbol_resolver, policy)
    if format_version == RUN_UNTIL_FINGERPRINT_FORMAT_VERSION:
        return _validate_run_until_fingerprint(data, source, symbol_resolver, policy)
    if format_version == ACCELERATED_FIDELITY_FINGERPRINT_FORMAT_VERSION:
        return _validate_accelerated_fidelity_fingerprint(
            data,
            source,
            symbol_resolver,
            policy,
        )
    raise PlaytestError(
        f"{source}.format_version must be integer "
        f"{FIXED_FINGERPRINT_FORMAT_VERSION} or "
        f"{RUN_UNTIL_FINGERPRINT_FORMAT_VERSION} or "
        f"{ACCELERATED_FIDELITY_FINGERPRINT_FORMAT_VERSION}"
    )


def _validate_fixed_fingerprint(
    data: Any,
    source: str,
    symbol_resolver: Callable[[str], tuple[int, int]] | None = None,
    policy: str = "exact-rom",
) -> dict[str, Any]:
    if policy not in ("exact-rom", "behavior"):
        raise ValueError(f"unknown verification policy: {policy}")
    required_fields = {"format_version", "scenario", "checkpoints"}
    optional_fields: set[str] = set()
    if policy == "exact-rom":
        required_fields.add("rom")
    else:
        optional_fields.add("rom")
    root = _expect_object(data, source, required_fields, optional_fields)
    if (
        not _is_int(root["format_version"])
        or root["format_version"] != FIXED_FINGERPRINT_FORMAT_VERSION
    ):
        raise PlaytestError(
            f"{source}.format_version must be integer "
            f"{FIXED_FINGERPRINT_FORMAT_VERSION}"
        )
    _expect_name(root["scenario"], f"{source}.scenario")
    if "rom" in root:
        rom = _expect_object(
            root["rom"], f"{source}.rom", {"sha1", "size", "title", "game_code"}
        )
        if not isinstance(rom["sha1"], str) or not re.fullmatch(
            r"[0-9a-f]{40}", rom["sha1"]
        ):
            raise PlaytestError(f"{source}.rom.sha1 must be 40 lowercase hex digits")
        if not _is_int(rom["size"]) or rom["size"] <= 0:
            raise PlaytestError(f"{source}.rom.size must be a positive integer")
        for field, limit in (("title", 48), ("game_code", 16)):
            if not isinstance(rom[field], str) or len(rom[field]) > limit:
                raise PlaytestError(
                    f"{source}.rom.{field} must be a string no longer than {limit} characters"
                )
    if not isinstance(root["checkpoints"], list):
        raise PlaytestError(f"{source}.checkpoints must be an array")
    previous_frame = -1
    names: set[str] = set()
    for index, raw in enumerate(root["checkpoints"]):
        path = f"{source}.checkpoints[{index}]"
        checkpoint = _expect_object(
            raw,
            path,
            {"frame", "name", "probes"},
            {"framebuffer_hash", "sram_hash", "regions", "pixel_probes"},
        )
        frame = _expect_frame(checkpoint["frame"], f"{path}.frame")
        if frame <= previous_frame:
            raise PlaytestError(f"{path}.frame must be strictly increasing")
        previous_frame = frame
        name = _expect_name(checkpoint["name"], f"{path}.name")
        if name in names:
            raise PlaytestError(f"{path}.name duplicates {name!r}")
        names.add(name)
        framebuffer_hash = checkpoint.get("framebuffer_hash")
        if framebuffer_hash is not None and (
            not isinstance(framebuffer_hash, str) or not HASH_RE.fullmatch(framebuffer_hash)
        ):
            raise PlaytestError(f"{path}.framebuffer_hash is malformed")
        sram_hash = checkpoint.get("sram_hash")
        if sram_hash is not None and (
            not isinstance(sram_hash, str) or not SRAM_HASH_RE.fullmatch(sram_hash)
        ):
            raise PlaytestError(f"{path}.sram_hash is malformed")
        if not isinstance(checkpoint["probes"], list):
            raise PlaytestError(f"{path}.probes must be an array")
        seen_bindings: set[tuple[str, int]] = set()
        seen_addresses: set[tuple[int, int]] = set()
        previous_sort_key: tuple[Any, ...] | None = None
        for probe_index, raw_probe in enumerate(checkpoint["probes"]):
            probe_path = f"{path}.probes[{probe_index}]"
            probe = _expect_object(raw_probe, probe_path, {"address", "size", "value"})
            size = probe["size"]
            if not _is_int(size) or size not in (1, 2, 4):
                raise PlaytestError(f"{probe_path}.size must be integer 1, 2, or 4")
            binding, address = _parse_address(
                probe["address"],
                size,
                f"{probe_path}.address",
                symbol_resolver,
            )
            binding_identity = (binding, size)
            if binding_identity in seen_bindings:
                raise PlaytestError(
                    f"{probe_path} duplicates symbolic address/size in this checkpoint"
                )
            seen_bindings.add(binding_identity)
            sort_key = _probe_binding_sort_key(binding, size)
            if previous_sort_key is not None and sort_key <= previous_sort_key:
                raise PlaytestError(
                    f"{probe_path} must be sorted and unique by semantic address/size"
                )
            previous_sort_key = sort_key
            if address is not None:
                address_identity = (address, size)
                if address_identity in seen_addresses:
                    raise PlaytestError(
                        f"{probe_path} resolves to a duplicate address/size in this checkpoint"
                    )
                seen_addresses.add(address_identity)
            pattern = re.compile(rf"^0x[0-9a-f]{{{size * 2}}}$")
            if not isinstance(probe["value"], str) or not pattern.fullmatch(probe["value"]):
                raise PlaytestError(f"{probe_path}.value is malformed for size {size}")
        regions = checkpoint.get("regions")
        if regions is not None:
            if not isinstance(regions, list) or not regions:
                raise PlaytestError(f"{path}.regions must be a non-empty array")
            seen_region_names: set[str] = set()
            for region_index, raw_region in enumerate(regions):
                region_path = f"{path}.regions[{region_index}]"
                region = _expect_object(
                    raw_region, region_path, {"name", "x", "y", "width", "height", "hash"}
                )
                region_name = _expect_name(region["name"], f"{region_path}.name")
                if region_name in seen_region_names:
                    raise PlaytestError(f"{region_path}.name duplicates {region_name!r}")
                seen_region_names.add(region_name)
                for field, limit in (("x", GBA_SCREEN_WIDTH), ("y", GBA_SCREEN_HEIGHT)):
                    if not _is_int(region[field]) or region[field] < 0 or region[field] >= limit:
                        raise PlaytestError(
                            f"{region_path}.{field} must be an integer from 0 through {limit - 1}"
                        )
                for field in ("width", "height"):
                    if not _is_int(region[field]) or region[field] < 1:
                        raise PlaytestError(f"{region_path}.{field} must be a positive integer")
                if region["x"] + region["width"] > GBA_SCREEN_WIDTH:
                    raise PlaytestError(f"{region_path} exceeds the framebuffer width")
                if region["y"] + region["height"] > GBA_SCREEN_HEIGHT:
                    raise PlaytestError(f"{region_path} exceeds the framebuffer height")
                if not isinstance(region["hash"], str) or not REGION_HASH_RE.fullmatch(
                    region["hash"]
                ):
                    raise PlaytestError(f"{region_path}.hash is malformed")
        pixel_probes = checkpoint.get("pixel_probes")
        if pixel_probes is not None:
            if not isinstance(pixel_probes, list) or not pixel_probes:
                raise PlaytestError(f"{path}.pixel_probes must be a non-empty array")
            seen_pixel_coords: set[tuple[int, int]] = set()
            for pixel_index, raw_pixel in enumerate(pixel_probes):
                pixel_path = f"{path}.pixel_probes[{pixel_index}]"
                pixel = _expect_object(raw_pixel, pixel_path, {"x", "y", "value"})
                if (
                    not _is_int(pixel["x"])
                    or pixel["x"] < 0
                    or pixel["x"] >= GBA_SCREEN_WIDTH
                ):
                    raise PlaytestError(
                        f"{pixel_path}.x must be an integer from 0 through {GBA_SCREEN_WIDTH - 1}"
                    )
                if (
                    not _is_int(pixel["y"])
                    or pixel["y"] < 0
                    or pixel["y"] >= GBA_SCREEN_HEIGHT
                ):
                    raise PlaytestError(
                        f"{pixel_path}.y must be an integer from 0 through {GBA_SCREEN_HEIGHT - 1}"
                    )
                identity = (pixel["x"], pixel["y"])
                if identity in seen_pixel_coords:
                    raise PlaytestError(f"{pixel_path} duplicates coordinate {identity}")
                seen_pixel_coords.add(identity)
                if not isinstance(pixel["value"], str) or not PIXEL_RE.fullmatch(pixel["value"]):
                    raise PlaytestError(f"{pixel_path}.value is malformed")
    return root


def _validate_run_until_fingerprint(
    data: Any,
    source: str,
    symbol_resolver: Callable[[str], tuple[int, int]] | None,
    policy: str,
) -> dict[str, Any]:
    required_fields = {"format_version", "scenario", "terminal", "checkpoints"}
    optional_fields: set[str] = set()
    if policy == "exact-rom":
        required_fields.add("rom")
    else:
        optional_fields.add("rom")
    root = _expect_object(data, source, required_fields, optional_fields)
    if (
        not _is_int(root["format_version"])
        or root["format_version"] != RUN_UNTIL_FINGERPRINT_FORMAT_VERSION
    ):
        raise PlaytestError(
            f"{source}.format_version must be integer "
            f"{RUN_UNTIL_FINGERPRINT_FORMAT_VERSION}"
        )

    fixed_shape = {
        "format_version": FIXED_FINGERPRINT_FORMAT_VERSION,
        "scenario": root["scenario"],
        "checkpoints": root["checkpoints"],
    }
    if "rom" in root:
        fixed_shape["rom"] = root["rom"]
    _validate_fixed_fingerprint(
        fixed_shape,
        source,
        symbol_resolver,
        policy,
    )
    if len(root["checkpoints"]) != 1:
        raise PlaytestError(
            f"{source}.checkpoints must contain exactly one terminal checkpoint"
        )

    terminal_path = f"{source}.terminal"
    terminal = _expect_object(
        root["terminal"],
        terminal_path,
        {"reason", "frame", "turn", "actions"},
    )
    reason = terminal["reason"]
    if not isinstance(reason, str) or reason not in TERMINAL_REASONS:
        raise PlaytestError(
            f"{terminal_path}.reason must be one of {', '.join(TERMINAL_REASONS)}"
        )
    frame = _expect_frame(terminal["frame"], f"{terminal_path}.frame")
    if root["checkpoints"][0]["frame"] != frame:
        raise PlaytestError(
            f"{source}.checkpoints[0].frame must equal {terminal_path}.frame"
        )

    def validate_counter(value: Any, path: str) -> None:
        if value is None:
            return
        counter = _expect_object(value, path, {"address", "size", "value"})
        size = counter["size"]
        if not _is_int(size) or size not in (1, 2, 4):
            raise PlaytestError(f"{path}.size must be integer 1, 2, or 4")
        _parse_address(
            counter["address"],
            size,
            f"{path}.address",
            symbol_resolver,
        )
        pattern = re.compile(rf"^0x[0-9a-f]{{{size * 2}}}$")
        if not isinstance(counter["value"], str) or not pattern.fullmatch(
            counter["value"]
        ):
            raise PlaytestError(f"{path}.value is malformed for size {size}")

    validate_counter(terminal["turn"], f"{terminal_path}.turn")
    validate_counter(terminal["actions"], f"{terminal_path}.actions")
    if reason == "max_turns" and terminal["turn"] is None:
        raise PlaytestError(
            f"{terminal_path}.turn must be non-null when reason is 'max_turns'"
        )
    if reason == "max_actions" and terminal["actions"] is None:
        raise PlaytestError(
            f"{terminal_path}.actions must be non-null when reason is 'max_actions'"
        )
    return root


def _validate_accelerated_fidelity_fingerprint(
    data: Any,
    source: str,
    symbol_resolver: Callable[[str], tuple[int, int]] | None,
    policy: str,
) -> dict[str, Any]:
    required_fields = {
        "format_version",
        "scenario",
        "profile",
        "terminal",
        "checkpoints",
        "trace",
    }
    optional_fields: set[str] = set()
    if policy == "exact-rom":
        required_fields.add("rom")
    else:
        optional_fields.add("rom")
    root = _expect_object(data, source, required_fields, optional_fields)
    if (
        not _is_int(root["format_version"])
        or root["format_version"] != ACCELERATED_FIDELITY_FINGERPRINT_FORMAT_VERSION
    ):
        raise PlaytestError(
            f"{source}.format_version must be integer "
            f"{ACCELERATED_FIDELITY_FINGERPRINT_FORMAT_VERSION}"
        )
    run_until_shape = dict(root)
    run_until_shape["format_version"] = RUN_UNTIL_FINGERPRINT_FORMAT_VERSION
    run_until_shape.pop("profile")
    run_until_shape.pop("trace")
    _validate_run_until_fingerprint(
        run_until_shape,
        source,
        symbol_resolver,
        policy,
    )

    profile = root["profile"]
    if not isinstance(profile, dict):
        raise PlaytestError(f"{source}.profile must be an object")
    profile_name = profile.get("name")
    if not isinstance(profile_name, str) or profile_name not in EXECUTION_PROFILE_NAMES:
        raise PlaytestError(
            f"{source}.profile.name must be one of "
            f"{', '.join(EXECUTION_PROFILE_NAMES)}"
        )
    if profile_name == EXECUTION_PROFILE_NORMAL_FIDELITY:
        _expect_object(profile, f"{source}.profile", {"name"})
    else:
        profile = _expect_object(
            profile,
            f"{source}.profile",
            {"name", "config_apply_frame", "config_before", "config_after"},
        )
        config_apply_frame = _expect_frame(
            profile["config_apply_frame"],
            f"{source}.profile.config_apply_frame",
        )
        if config_apply_frame > root["terminal"]["frame"]:
            raise PlaytestError(
                f"{source}.profile.config_apply_frame must not exceed "
                f"{source}.terminal.frame"
            )
        for field in ("config_before", "config_after"):
            if not isinstance(profile[field], str) or not re.fullmatch(
                r"0x[0-9a-f]{8}", profile[field]
            ):
                raise PlaytestError(f"{source}.profile.{field} must be 32-bit lowercase hex")

    trace = root["trace"]
    if not isinstance(trace, list) or not trace:
        raise PlaytestError(f"{source}.trace must be a non-empty array")
    trace_checkpoints: list[dict[str, Any]] = []
    trace_record_count = 0
    previous_frame = -1
    expected_shape: list[tuple[str, int]] | None = None
    for index, snapshot in enumerate(trace):
        path = f"{source}.trace[{index}]"
        snapshot = _expect_object(snapshot, path, {"frame", "probes"})
        frame = _expect_frame(snapshot["frame"], f"{path}.frame")
        if frame <= previous_frame:
            raise PlaytestError(f"{path}.frame must be strictly increasing")
        if frame > root["terminal"]["frame"]:
            raise PlaytestError(
                f"{path}.frame must not exceed {source}.terminal.frame"
            )
        previous_frame = frame
        trace_checkpoints.append(
            {
                "frame": frame,
                "name": f"trace-{index}",
                "probes": snapshot["probes"],
            }
        )
        if not isinstance(snapshot["probes"], list) or not snapshot["probes"]:
            raise PlaytestError(f"{path}.probes must be a non-empty array")
        trace_record_count += len(snapshot["probes"])
        if trace_record_count > MAX_PROFILE_TRACE_RECORDS:
            raise PlaytestError(
                f"{source}.trace exceeds the {MAX_PROFILE_TRACE_RECORDS}-record "
                "aggregate limit"
            )
        shape = [
            (probe.get("address"), probe.get("size"))
            for probe in snapshot["probes"]
            if isinstance(probe, dict)
        ]
        if len(shape) != len(snapshot["probes"]):
            raise PlaytestError(f"{path}.probes entries must be objects")
        if expected_shape is None:
            expected_shape = shape
        elif shape != expected_shape:
            raise PlaytestError(
                f"{path}.probes must preserve the first trace snapshot's probe shape"
            )
    _validate_fixed_fingerprint(
        {
            "format_version": FIXED_FINGERPRINT_FORMAT_VERSION,
            "scenario": root["scenario"],
            "checkpoints": trace_checkpoints,
        },
        f"{source}.trace",
        symbol_resolver,
        "behavior",
    )
    return root


def _recursive_differences(expected: Any, actual: Any, path: str = "") -> Iterable[str]:
    if type(expected) is not type(actual):
        yield f"{path or '<root>'}: expected type {type(expected).__name__}, actual {type(actual).__name__}"
        return
    if isinstance(expected, dict):
        for key in sorted(expected.keys() - actual.keys()):
            yield f"{path + '.' if path else ''}{key}: missing from actual capture"
        for key in sorted(actual.keys() - expected.keys()):
            yield f"{path + '.' if path else ''}{key}: unexpected in actual capture"
        for key in sorted(expected.keys() & actual.keys()):
            child = f"{path}.{key}" if path else key
            yield from _recursive_differences(expected[key], actual[key], child)
    elif isinstance(expected, list):
        if len(expected) != len(actual):
            yield f"{path}: expected {len(expected)} item(s), actual {len(actual)}"
        for index, (expected_item, actual_item) in enumerate(zip(expected, actual)):
            yield from _recursive_differences(
                expected_item, actual_item, f"{path}[{index}]"
            )
    elif expected != actual:
        yield f"{path}: expected {expected!r}, actual {actual!r}"


def compare_fingerprints(
    expected: dict[str, Any], actual: dict[str, Any], policy: str = "exact-rom"
) -> list[str]:
    if policy == "exact-rom":
        return list(_recursive_differences(expected, actual))
    if policy == "behavior":
        expected_behavior = {
            key: value for key, value in expected.items() if key != "rom"
        }
        actual_behavior = {
            key: value for key, value in actual.items() if key != "rom"
        }
        return list(_recursive_differences(expected_behavior, actual_behavior))
    raise ValueError(f"unknown verification policy: {policy}")


def format_rom_identity(provenance: dict[str, Any]) -> str:
    return (
        f"sha1={provenance['sha1']} size={provenance['size']} "
        f"title={provenance['title']!r} game_code={provenance['game_code']!r}"
    )


def format_baseline_rom_identity(fingerprint: dict[str, Any]) -> str:
    if "rom" not in fingerprint:
        return "not recorded (behavior-policy baseline)"
    return format_rom_identity(fingerprint["rom"])


def _write_output(path: str, text: str) -> None:
    if path == "-":
        sys.stdout.write(text)
        return
    output = Path(path)
    try:
        output.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise PlaytestError(f"cannot write {output}: {exc}") from exc


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    capture_parser = subparsers.add_parser(
        "capture", help="run a scenario and emit its deterministic fingerprint"
    )
    capture_parser.add_argument("--rom", required=True, type=Path)
    capture_parser.add_argument(
        "--elf",
        type=Path,
        help="exact linked ELF used to resolve symbolic probe addresses",
    )
    capture_parser.add_argument(
        "--nm",
        help=(
            "nm executable used with --elf (default: MODERN_NM, then "
            "MODERN_TOOLCHAIN_ROOT/bin/arm-none-eabi-nm, then NM)"
        ),
    )
    capture_parser.add_argument("--scenario", required=True, type=Path)
    capture_parser.add_argument(
        "--sram-image",
        type=Path,
        default=None,
        help="optional exact 0x8000-byte raw SRAM image loaded before frame 0",
    )
    capture_parser.add_argument(
        "--output", "-o", default="-", help="output JSON path (default: stdout)"
    )
    capture_parser.add_argument(
        "--retries", type=int, default=0, metavar="N", help='bounded number of additional attempts (capped at MAX_RETRIES_CAP=5) after a libmGBA backend or compiler process time-out only -- never for a non-zero exit code or a mismatched/malformed-output diagnostic, which are deterministic and must never be silently retried. Default 0 (a single attempt, no retry). Every retried attempt is reported on stderr.'
    )

    verify_parser = subparsers.add_parser(
        "verify", help="capture and compare against an expected fingerprint"
    )
    verify_parser.add_argument("--rom", required=True, type=Path)
    verify_parser.add_argument(
        "--elf",
        type=Path,
        help="exact linked ELF used to resolve symbolic probe addresses",
    )
    verify_parser.add_argument(
        "--nm",
        help=(
            "nm executable used with --elf (default: MODERN_NM, then "
            "MODERN_TOOLCHAIN_ROOT/bin/arm-none-eabi-nm, then NM)"
        ),
    )
    verify_parser.add_argument("--scenario", required=True, type=Path)
    verify_parser.add_argument("--expected", required=True, type=Path)
    verify_parser.add_argument(
        "--sram-image",
        type=Path,
        default=None,
        help="optional exact 0x8000-byte raw SRAM image loaded before frame 0",
    )
    verify_parser.add_argument(
        "--policy",
        choices=("exact-rom", "behavior"),
        default="exact-rom",
        help=(
            "exact-rom (safe default) compares ROM identity and behavior; "
            "behavior explicitly compares checkpoints across different ROM identities"
        ),
    )
    verify_parser.add_argument(
        "--retries", type=int, default=0, metavar="N", help='bounded number of additional attempts (capped at MAX_RETRIES_CAP=5) after a libmGBA backend or compiler process time-out only -- never for a non-zero exit code or a mismatched/malformed-output diagnostic, which are deterministic and must never be silently retried. Default 0 (a single attempt, no retry). Every retried attempt is reported on stderr.'
    )

    backend_check_parser = subparsers.add_parser(
        "backend-check", help="compile the libmGBA backend without running a ROM"
    )
    backend_check_parser.add_argument(
        "--retries", type=int, default=0, metavar="N", help='bounded number of additional attempts (capped at MAX_RETRIES_CAP=5) after a libmGBA backend or compiler process time-out only -- never for a non-zero exit code or a mismatched/malformed-output diagnostic, which are deterministic and must never be silently retried. Default 0 (a single attempt, no retry). Every retried attempt is reported on stderr.'
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _make_parser().parse_args(argv)
    try:
        if args.retries < 0:
            raise PlaytestError(
                f"--retries must be a non-negative integer, got {args.retries}"
            )
        if args.mode == "backend-check":
            with tempfile.TemporaryDirectory(prefix="gba-playtest-check-") as temporary:
                build_backend(Path(temporary) / "gba-playtest-backend", args.retries)
            print("libmGBA backend: available")
            return 0
        symbol_resolver = (
            ElfSymbolResolver(args.elf, args.nm) if args.elf is not None else None
        )
        scenario = load_scenario(args.scenario, symbol_resolver)
        actual = capture(args.rom, scenario, args.sram_image, args.retries)
        if args.mode == "capture":
            _write_output(args.output, serialize_fingerprint(actual))
            return 0
        expected = validate_fingerprint(
            _read_json(args.expected),
            str(args.expected),
            symbol_resolver,
            args.policy,
        )
        differences = compare_fingerprints(expected, actual, args.policy)
        baseline_rom_identity = format_baseline_rom_identity(expected)
        if differences:
            print(
                f"fingerprint mismatch for scenario {scenario.name!r} "
                f"under policy {args.policy!r}:",
                file=sys.stderr,
            )
            print(
                f"  baseline ROM: {baseline_rom_identity}",
                file=sys.stderr,
            )
            print(
                f"  candidate ROM: {format_rom_identity(actual['rom'])}",
                file=sys.stderr,
            )
            for difference in differences:
                print(f"  - {difference}", file=sys.stderr)
            return 1
        print(
            f"fingerprint verified: policy={args.policy} scenario={scenario.name} "
            f"checkpoints={len(scenario.checkpoints)}\n"
            f"  baseline ROM: {baseline_rom_identity}\n"
            f"  candidate ROM: {format_rom_identity(actual['rom'])}"
        )
        return 0
    except PlaytestError as exc:
        print(f"gba-playtest: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
