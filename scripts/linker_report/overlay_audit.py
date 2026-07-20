#!/usr/bin/env python3
"""Overlay bounds and cross-overlay relocation audit."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from budget import parse_map


SCHEMA_VERSION = 1
DEFAULT_EWRAM_CAPACITY = 0x40000

_OVERLAY_OBJECT_RE = re.compile(
    r"^\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)\s+(\S+)\s*$"
)
_OVERLAY_SYMBOL_RE = re.compile(
    r"^\s+(0x[0-9a-fA-F]+)\s+(\S+)\s*$"
)
_RELOCATION_SECTION_RE = re.compile(r"^Relocation section '([^']+)'")
_RELOCATION_ENTRY_RE = re.compile(
    r"^\s*([0-9A-Fa-f]+)\s+[0-9A-Fa-f]+\s+(\S+)\s+([0-9A-Fa-f]+)\s+(.+?)\s*$"
)
_NM_SYMBOL_RE = re.compile(r"^([0-9A-Fa-f]+)\s+\S+\s+(\S+)$")


@dataclass(frozen=True)
class OverlayInfo:
    name: str
    address: int
    size: int
    region: str | None
    objects: tuple[str, ...]
    symbols: tuple[str, ...]

    @property
    def end(self) -> int:
        return self.address + self.size


@dataclass(frozen=True)
class RelocationRecord:
    section_name: str
    offset: int
    type_name: str
    symbol_value: int | None
    symbol_name: str


def _classify_region(address: int, regions: list[Any]) -> str | None:
    for region in regions:
        if region.origin <= address < region.origin + region.length:
            return region.name
    return None


def _ewram_capacity(regions: list[Any]) -> int:
    for region in regions:
        if region.name == "ewram":
            return region.length
    return DEFAULT_EWRAM_CAPACITY


def _round_percent(size: int, capacity: int) -> float:
    if capacity <= 0:
        return 0.0
    return round(size * 100.0 / capacity, 2)


def parse_overlay_map(
    map_text: str,
) -> tuple[list[Any], list[OverlayInfo], dict[str, set[str]]]:
    """Parse overlays, their object membership, and overlay-defined symbols."""
    regions, sections, _assignments = parse_map(map_text)

    overlay_sections = sorted(
        (section for section in sections if "overlay" in section.name),
        key=lambda section: (section.address, section.name),
    )
    overlay_names = {section.name for section in overlay_sections}

    overlay_objects: dict[str, set[str]] = defaultdict(set)
    overlay_symbols: dict[str, set[str]] = defaultdict(set)

    current_overlay: str | None = None
    for line in map_text.splitlines():
        stripped = line.strip()

        if current_overlay is None:
            if stripped in overlay_names and line == stripped:
                current_overlay = stripped
            continue

        if line and not line[:1].isspace():
            current_overlay = None
            if stripped in overlay_names and line == stripped:
                current_overlay = stripped
            continue

        object_match = _OVERLAY_OBJECT_RE.match(line)
        if object_match:
            object_name = object_match.group(3).replace("\\", "/")
            if not object_name.startswith("*("):
                overlay_objects[current_overlay].add(object_name)
            continue

        symbol_match = _OVERLAY_SYMBOL_RE.match(line)
        if not symbol_match or "=" in line:
            continue

        symbol_name = symbol_match.group(2)
        if "/" in symbol_name or symbol_name.startswith("*("):
            continue

        overlay_symbols[current_overlay].add(symbol_name)

    overlays: list[OverlayInfo] = []
    symbol_to_overlays: dict[str, set[str]] = defaultdict(set)

    for section in overlay_sections:
        overlay = OverlayInfo(
            name=section.name,
            address=section.address,
            size=section.size,
            region=_classify_region(section.address, regions),
            objects=tuple(sorted(overlay_objects.get(section.name, set()))),
            symbols=tuple(sorted(overlay_symbols.get(section.name, set()))),
        )
        overlays.append(overlay)

        for symbol_name in overlay.symbols:
            symbol_to_overlays[symbol_name].add(overlay.name)

    return regions, overlays, symbol_to_overlays


def build_bounds_report(regions: list[Any], overlays: list[OverlayInfo]) -> dict[str, Any]:
    """Build overlay capacity data."""
    capacity = _ewram_capacity(regions)
    overlay_entries = []

    for overlay in sorted(overlays, key=lambda item: item.name):
        overlay_entries.append({
            "name": overlay.name,
            "address": overlay.address,
            "size_bytes": overlay.size,
            "region": overlay.region,
            "fits_within_ewram": overlay.size <= capacity,
            "utilization_percent": _round_percent(overlay.size, capacity),
        })

    overflow = any(not entry["fits_within_ewram"] for entry in overlay_entries)

    peak_overlay = None
    if overlays:
        peak = sorted(overlays, key=lambda item: (-item.size, item.name))[0]
        peak_overlay = {
            "name": peak.name,
            "size_bytes": peak.size,
            "utilization_percent": _round_percent(peak.size, capacity),
        }

    return {
        "capacity_bytes": capacity,
        "overlays": overlay_entries,
        "peak_overlay": peak_overlay,
        "overflow": overflow,
    }


def parse_readelf_relocations(text: str) -> list[RelocationRecord]:
    """Parse `arm-none-eabi-readelf -r` output."""
    records: list[RelocationRecord] = []
    current_section: str | None = None

    for line in text.splitlines():
        section_match = _RELOCATION_SECTION_RE.match(line)
        if section_match:
            current_section = section_match.group(1)
            continue

        entry_match = _RELOCATION_ENTRY_RE.match(line)
        if not entry_match or current_section is None:
            continue

        symbol_value_text = entry_match.group(3)
        symbol_tail = entry_match.group(4).strip()
        symbol_name = re.split(r"\s+[+-]\s+", symbol_tail, maxsplit=1)[0].split()[0]

        records.append(RelocationRecord(
            section_name=current_section,
            offset=int(entry_match.group(1), 16),
            type_name=entry_match.group(2),
            symbol_value=int(symbol_value_text, 16),
            symbol_name=symbol_name,
        ))

    return records


def parse_nm_symbols(text: str) -> dict[str, tuple[int, ...]]:
    """Parse `arm-none-eabi-nm` output into a symbol->values map."""
    symbol_values: dict[str, list[int]] = defaultdict(list)

    for line in text.splitlines():
        match = _NM_SYMBOL_RE.match(line.strip())
        if not match:
            continue
        symbol_values[match.group(2)].append(int(match.group(1), 16))

    return {
        name: tuple(sorted(values))
        for name, values in sorted(symbol_values.items())
    }


def _contains_token(haystack: str, token: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9_]){re.escape(token)}(?![A-Za-z0-9_])"
    return re.search(pattern, haystack) is not None


def infer_origin_overlay(section_name: str, overlays: list[OverlayInfo]) -> str | None:
    """Best-effort origin overlay inference from a relocation section name."""
    overlay_names = {overlay.name for overlay in overlays}
    normalized = section_name.replace("\\", "/")

    for prefix in (".rel.", ".rela.", ".rel", ".rela"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break

    if normalized in overlay_names:
        return normalized

    name_matches = {
        overlay.name
        for overlay in overlays
        if _contains_token(section_name, overlay.name)
    }
    if len(name_matches) == 1:
        return next(iter(name_matches))

    object_matches = set()
    for overlay in overlays:
        for object_name in overlay.objects:
            basename = Path(object_name).name
            if _contains_token(section_name, object_name) or _contains_token(section_name, basename):
                object_matches.add(overlay.name)

    if len(object_matches) == 1:
        return next(iter(object_matches))

    return None


def infer_target_overlay(
    symbol_name: str,
    symbol_value: int | None,
    overlays: list[OverlayInfo],
    symbol_to_overlays: dict[str, set[str]],
    nm_symbols: dict[str, tuple[int, ...]],
) -> str | None:
    """Best-effort target overlay inference from map symbols and nm values."""
    direct_matches = symbol_to_overlays.get(symbol_name, set())
    if len(direct_matches) == 1:
        return next(iter(direct_matches))

    candidate_names = set()
    values = list(nm_symbols.get(symbol_name, ()))
    if symbol_value is not None:
        values.append(symbol_value)

    for value in values:
        matches = {
            overlay.name
            for overlay in overlays
            if overlay.address <= value < overlay.end
        }
        if len(matches) == 1:
            candidate_names.update(matches)

    if len(candidate_names) == 1:
        return next(iter(candidate_names))

    return None


def scan_cross_overlay_relocations(
    elf_path: str | None,
    overlays: list[OverlayInfo],
    symbol_to_overlays: dict[str, set[str]],
) -> dict[str, Any]:
    """Scan relocations for references from one overlay to another."""
    if not elf_path:
        return {
            "status": "skipped",
            "reason": "no ELF provided",
            "relocation_count": 0,
            "overlay_scoped_relocation_count": 0,
            "count": 0,
            "references": [],
        }

    path = Path(elf_path)
    if not path.is_file():
        return {
            "status": "skipped",
            "reason": "ELF not found",
            "relocation_count": 0,
            "overlay_scoped_relocation_count": 0,
            "count": 0,
            "references": [],
        }

    try:
        readelf_result = subprocess.run(
            ["arm-none-eabi-readelf", "-r", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {
            "status": "skipped",
            "reason": "arm-none-eabi-readelf unavailable",
            "relocation_count": 0,
            "overlay_scoped_relocation_count": 0,
            "count": 0,
            "references": [],
        }

    if readelf_result.returncode != 0:
        return {
            "status": "skipped",
            "reason": "arm-none-eabi-readelf failed",
            "relocation_count": 0,
            "overlay_scoped_relocation_count": 0,
            "count": 0,
            "references": [],
        }

    relocations = parse_readelf_relocations(readelf_result.stdout)
    if not relocations:
        return {
            "status": "skipped",
            "reason": "no relocations in ELF",
            "relocation_count": 0,
            "overlay_scoped_relocation_count": 0,
            "count": 0,
            "references": [],
        }

    try:
        nm_result = subprocess.run(
            ["arm-none-eabi-nm", "-n", "--defined-only", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return {
            "status": "skipped",
            "reason": "arm-none-eabi-nm unavailable",
            "relocation_count": len(relocations),
            "overlay_scoped_relocation_count": 0,
            "count": 0,
            "references": [],
        }

    if nm_result.returncode != 0:
        return {
            "status": "skipped",
            "reason": "arm-none-eabi-nm failed",
            "relocation_count": len(relocations),
            "overlay_scoped_relocation_count": 0,
            "count": 0,
            "references": [],
        }

    nm_symbols = parse_nm_symbols(nm_result.stdout)

    references = []
    overlay_scoped_count = 0

    for relocation in relocations:
        origin_overlay = infer_origin_overlay(relocation.section_name, overlays)
        if origin_overlay is None:
            continue

        overlay_scoped_count += 1
        target_overlay = infer_target_overlay(
            relocation.symbol_name,
            relocation.symbol_value,
            overlays,
            symbol_to_overlays,
            nm_symbols,
        )

        if target_overlay is None or target_overlay == origin_overlay:
            continue

        references.append({
            "origin_overlay": origin_overlay,
            "target_overlay": target_overlay,
            "relocation_section": relocation.section_name,
            "offset": relocation.offset,
            "type": relocation.type_name,
            "symbol": relocation.symbol_name,
            "symbol_value": relocation.symbol_value,
        })

    references.sort(
        key=lambda entry: (
            entry["origin_overlay"],
            entry["target_overlay"],
            entry["symbol"],
            entry["relocation_section"],
            entry["offset"],
            entry["type"],
        )
    )

    return {
        "status": "found" if references else "ok",
        "relocation_count": len(relocations),
        "overlay_scoped_relocation_count": overlay_scoped_count,
        "count": len(references),
        "references": references,
    }


def build_report(
    regions: list[Any],
    overlays: list[OverlayInfo],
    cross_overlay_report: dict[str, Any],
) -> dict[str, Any]:
    """Build the final deterministic JSON report."""
    bounds = build_bounds_report(regions, overlays)
    return {
        "schema_version": SCHEMA_VERSION,
        "capacity_bytes": bounds["capacity_bytes"],
        "overlays": bounds["overlays"],
        "peak_overlay": bounds["peak_overlay"],
        "overflow": bounds["overflow"],
        "cross_overlay_relocations": cross_overlay_report,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit overlay bounds and cross-overlay relocations."
    )
    parser.add_argument("--map", required=True, help="Path to the linker map")
    parser.add_argument("--elf", default=None, help="Path to the ELF (optional)")
    parser.add_argument("--output", required=True, help="Path to write JSON report")
    args = parser.parse_args(argv)

    map_path = Path(args.map)
    if not map_path.is_file():
        print(f"error: map file not found: {args.map}", file=sys.stderr)
        return 2

    try:
        map_text = map_path.read_text(errors="replace")
    except OSError as exc:
        print(f"error: cannot read map file: {exc}", file=sys.stderr)
        return 2

    try:
        regions, overlays, symbol_to_overlays = parse_overlay_map(map_text)
    except ValueError as exc:
        print(f"error: malformed map file: {exc}", file=sys.stderr)
        return 2

    cross_overlay_report = scan_cross_overlay_relocations(
        args.elf, overlays, symbol_to_overlays
    )
    report = build_report(regions, overlays, cross_overlay_report)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2) + "\n")

    has_cross_overlay_refs = cross_overlay_report["count"] > 0
    return 1 if report["overflow"] or has_cross_overlay_refs else 0


if __name__ == "__main__":
    sys.exit(main())
