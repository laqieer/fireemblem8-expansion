#!/usr/bin/env python3
"""Bind starter scenario probes to linked EWRAM probe symbols."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from probe_bindings import (
    ElfSymbolResolver,
    ProbeBindingError,
    resolve_elf_symbol,
    resolve_probe_expression,
)

PROBE_SYMBOL = "gExpansionMechanicsProbe"
PROBE_SIZE = 7 * 4
PROBE_CHECKPOINT_MARKERS = ("mechanics-probe", "hook-probe")
PROBE_SPECS = {
    "gExpansionMechanicsProbe": (7 * 4, PROBE_CHECKPOINT_MARKERS),
    "gExpansionDangerOverlayProbe": (5 * 4, ("overlay",)),
}


def resolve_symbol(
    elf: Path,
    symbol: str = PROBE_SYMBOL,
    nm: str | None = None,
) -> tuple[int, int]:
    return resolve_elf_symbol(elf, symbol, nm)


def _probe_groups_from_data(
    data: dict,
    source: str,
    markers: tuple[str, ...],
    probe_count: int,
) -> list[list[dict]]:
    if not any(
        any(marker in checkpoint.get("name", "") for marker in markers)
        for checkpoint in data.get("checkpoints", [])
    ):
        raise ProbeBindingError(f"{source} has no matching starter probe checkpoint")

    groups = []
    for checkpoint in data.get("checkpoints", []):
        probes = [
            probe
            for probe in checkpoint.get("probes", [])
            if probe.get("size") == 4
        ]
        if not probes:
            continue
        if len(probes) != probe_count:
            raise ProbeBindingError(
                f"{source} checkpoint {checkpoint.get('name', '<unnamed>')!r} has "
                f"{len(probes)} 4-byte probes, expected {probe_count}"
            )
        groups.append(probes)
    if not groups:
        raise ProbeBindingError(f"{source} has no 4-byte starter probe groups")
    return groups


def _probe_groups(
    path: Path,
    markers: tuple[str, ...],
    probe_count: int,
) -> list[list[dict]]:
    return _probe_groups_from_data(
        json.loads(path.read_text(encoding="utf-8")),
        str(path),
        markers,
        probe_count,
    )


def _expected_bindings(symbol: str, probe_count: int) -> list[str]:
    return [f"{symbol}+0x{4 * index:02x}" for index in range(probe_count)]


def migrate_bindings(
    path: Path,
    symbol: str,
    probe_size: int,
    checkpoint_markers: tuple[str, ...],
) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    probe_count = probe_size // 4
    groups = _probe_groups_from_data(
        data,
        str(path),
        checkpoint_markers,
        probe_count,
    )
    expected = _expected_bindings(symbol, probe_count)
    changed = 0
    for probes in groups:
        for probe, binding in zip(probes, expected):
            if probe["address"] != binding:
                probe["address"] = binding
                changed += 1
    if changed:
        path.write_text(
            json.dumps(
                data,
                indent=2,
                sort_keys=path.parent.name == "fingerprints",
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return changed


def check_bindings(
    elf: Path,
    scenario: Path,
    fingerprint: Path,
    symbol: str = PROBE_SYMBOL,
    probe_size: int = PROBE_SIZE,
    checkpoint_markers: tuple[str, ...] = PROBE_CHECKPOINT_MARKERS,
    nm: str | None = None,
) -> tuple[int, int]:
    base, size = resolve_symbol(elf, symbol, nm)
    if size != probe_size:
        raise ProbeBindingError(
            f"{symbol} is 0x{size:x} bytes in {elf}, expected 0x{probe_size:x}"
        )

    probe_count = probe_size // 4
    expected_bindings = _expected_bindings(symbol, probe_count)
    expected_addresses = [base + 4 * index for index in range(probe_count)]
    resolver = ElfSymbolResolver(elf, nm)
    for path in (scenario, fingerprint):
        for probes in _probe_groups(path, checkpoint_markers, probe_count):
            bindings = [probe["address"] for probe in probes]
            if bindings != expected_bindings:
                raise ProbeBindingError(
                    f"{path} uses {', '.join(bindings)}; expected relocation-independent "
                    f"{', '.join(expected_bindings)}"
                )
            addresses = [
                resolve_probe_expression(
                    probe["address"],
                    4,
                    resolver,
                    f"{path}:{probe['address']}",
                )
                for probe in probes
            ]
            if addresses != expected_addresses:
                raise ProbeBindingError(
                    f"{path} resolves to "
                    f"{', '.join('0x%08x' % address for address in addresses)}; "
                    f"{symbol} in {elf} requires "
                    f"{', '.join('0x%08x' % address for address in expected_addresses)}"
                )
    return base, size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--fingerprint", required=True, type=Path)
    parser.add_argument("--symbol", default=PROBE_SYMBOL)
    parser.add_argument("--checkpoint-marker", action="append")
    parser.add_argument("--nm")
    parser.add_argument(
        "--migrate-symbolic",
        action="store_true",
        help="rewrite only the selected starter probe address fields to symbol+offset",
    )
    args = parser.parse_args(argv)

    try:
        default_size, default_markers = PROBE_SPECS.get(
            args.symbol, (PROBE_SIZE, PROBE_CHECKPOINT_MARKERS)
        )
        markers = tuple(args.checkpoint_marker or default_markers)
        migrated = 0
        if args.migrate_symbolic:
            migrated += migrate_bindings(
                args.scenario,
                args.symbol,
                default_size,
                markers,
            )
            migrated += migrate_bindings(
                args.fingerprint,
                args.symbol,
                default_size,
                markers,
            )
        base, size = check_bindings(
            args.elf,
            args.scenario,
            args.fingerprint,
            symbol=args.symbol,
            probe_size=default_size,
            checkpoint_markers=markers,
            nm=args.nm,
        )
    except (OSError, ProbeBindingError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(
        "starter probe binding passed: "
        f"symbol={args.symbol} address=0x{base:08x} size={size}"
        + (f" migrated_addresses={migrated}" if args.migrate_symbolic else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
