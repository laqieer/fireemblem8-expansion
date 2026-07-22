"""Command-line entry point: ``python3 -m scripts.generated_data <command> ...``

Commands:

* ``validate`` -- load + validate a table's JSON source; report every
  diagnostic (not just the first) with ``file:line:column`` locations.
* ``generate`` -- validate, then write C89 output under ``build/generated/data``
  (write-if-changed), the committed inventory/summary report, and (for
  tables that have one) run the hand-written round-trip comparison.
* ``check`` -- like ``generate``, self-healing the ephemeral ``build/``
  output (write-if-changed, exactly like ``generate``), but never writing
  the committed inventory: it only compares against what's on disk and
  reports drift (with a non-zero exit) if the committed inventory,
  validation, or hand-written round-trip has gone stale. Suitable for
  CI/pre-commit use.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import registry  # noqa: F401  (import registers table schemas)
from .cgen import write_if_changed
from .diagnostics import DiagnosticCollector, GeneratedDataError
from .schema import REGISTRY
from .supports import parser as supports_roundtrip
from .supports.generate import generate_c_source
from .supports.inventory import build_inventory

DEFAULT_SOURCES = {
    "supports": "src/data/supports.json",
}
DEFAULT_HAND_SOURCES = {
    "supports": "src/data_supports.c",
}
DEFAULT_OUTPUT_NAMES = {
    "supports": "data_supports.c",
}
DEFAULT_INVENTORY_PATHS = {
    "supports": "reports/generated_data_supports_inventory.md",
}
DEFAULT_OUT_DIR = os.path.join("build", "generated", "data")


def _load_and_validate(table, source_path):
    schema = REGISTRY.resolve(table)
    records = schema.load_records(source_path)
    diagnostics = DiagnosticCollector()
    schema.validate(records, diagnostics)
    return schema, records, diagnostics


def _roundtrip_errors(table, records, hand_source):
    if table != "supports" or not hand_source:
        return []
    if not os.path.exists(hand_source):
        return []
    hand_records = supports_roundtrip.parse_hand_written(hand_source)
    return supports_roundtrip.compare_records(records, hand_records, hand_path=hand_source)


def _generate_output(table, records, source_path):
    if table == "supports":
        return generate_c_source(records, source_path)
    raise GeneratedDataError("no generator registered for table '{}'".format(table))


def _build_inventory(table, records):
    if table == "supports":
        return build_inventory(records)
    raise GeneratedDataError("no inventory builder registered for table '{}'".format(table))


def cmd_validate(args):
    table = args.table
    source_path = args.source or DEFAULT_SOURCES[table]
    try:
        schema, records, diagnostics = _load_and_validate(table, source_path)
    except GeneratedDataError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    roundtrip_errors = []
    hand_source = args.hand_source or DEFAULT_HAND_SOURCES.get(table)
    if not args.no_roundtrip:
        try:
            roundtrip_errors = _roundtrip_errors(table, records, hand_source)
        except GeneratedDataError as exc:
            roundtrip_errors = [exc]

    all_errors = list(diagnostics.errors) + roundtrip_errors
    if all_errors:
        for error in all_errors:
            print(str(error), file=sys.stderr)
        print("FAILED: {} diagnostic(s)".format(len(all_errors)), file=sys.stderr)
        return 1

    print("OK: {} record(s) in {} validated against schema '{}'".format(len(records), source_path, schema.name))
    return 0


def cmd_generate(args):
    table = args.table
    source_path = args.source or DEFAULT_SOURCES[table]
    out_dir = args.out_dir or DEFAULT_OUT_DIR
    inventory_path = args.inventory or DEFAULT_INVENTORY_PATHS.get(table)
    hand_source = args.hand_source or DEFAULT_HAND_SOURCES.get(table)

    try:
        schema, records, diagnostics = _load_and_validate(table, source_path)
    except GeneratedDataError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    roundtrip_errors = []
    if not args.no_roundtrip:
        try:
            roundtrip_errors = _roundtrip_errors(table, records, hand_source)
        except GeneratedDataError as exc:
            roundtrip_errors = [exc]

    all_errors = list(diagnostics.errors) + roundtrip_errors
    if all_errors:
        for error in all_errors:
            print(str(error), file=sys.stderr)
        print("FAILED: {} diagnostic(s); nothing written".format(len(all_errors)), file=sys.stderr)
        return 1

    output_path = os.path.join(out_dir, DEFAULT_OUTPUT_NAMES[table])
    content = _generate_output(table, records, source_path)
    changed = write_if_changed(output_path, content)
    print("{} {}".format("wrote" if changed else "up to date:", output_path))

    if inventory_path:
        inventory_content = _build_inventory(table, records)
        inventory_changed = write_if_changed(inventory_path, inventory_content)
        print("{} {}".format("wrote" if inventory_changed else "up to date:", inventory_path))

    return 0


def cmd_check(args):
    """Fail on drift in committed artifacts (inventory); self-heals the
    ephemeral build/ output but never touches anything outside build/.
    """
    table = args.table
    source_path = args.source or DEFAULT_SOURCES[table]
    out_dir = args.out_dir or DEFAULT_OUT_DIR
    inventory_path = args.inventory or DEFAULT_INVENTORY_PATHS.get(table)
    hand_source = args.hand_source or DEFAULT_HAND_SOURCES.get(table)

    try:
        schema, records, diagnostics = _load_and_validate(table, source_path)
    except GeneratedDataError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    roundtrip_errors = []
    if not args.no_roundtrip:
        try:
            roundtrip_errors = _roundtrip_errors(table, records, hand_source)
        except GeneratedDataError as exc:
            roundtrip_errors = [exc]

    all_errors = list(diagnostics.errors) + roundtrip_errors
    if all_errors:
        for error in all_errors:
            print(str(error), file=sys.stderr)
        print("FAILED: {} diagnostic(s)".format(len(all_errors)), file=sys.stderr)
        return 1

    drift_found = False

    # The bulky C output lives only under build/ and is never committed, so
    # there is nothing to "drift" against across git history -- regenerate
    # it (write-if-changed) exactly like `generate` would, purely so
    # incremental `make` builds stay current.
    output_path = os.path.join(out_dir, DEFAULT_OUTPUT_NAMES[table])
    generated_content = _generate_output(table, records, source_path)
    write_if_changed(output_path, generated_content)

    if inventory_path:
        generated_inventory = _build_inventory(table, records)
        existing_inventory = None
        if os.path.exists(inventory_path):
            with open(inventory_path, "r", encoding="utf-8") as handle:
                existing_inventory = handle.read()
        if existing_inventory != generated_inventory:
            print(
                "DRIFT: committed inventory {} is stale (regenerate with `generate`)".format(inventory_path),
                file=sys.stderr,
            )
            drift_found = True

    if drift_found:
        return 1

    print("OK: no drift for table '{}' ({} record(s))".format(table, len(records)))
    return 0


def build_arg_parser():
    parser = argparse.ArgumentParser(prog="python3 -m scripts.generated_data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common_kwargs = dict(add_help=False)

    def add_common_args(sub):
        sub.add_argument("--table", required=True, choices=REGISTRY.all_names() or ["supports"])
        sub.add_argument("--source", help="path to the table's JSON source (default: built-in per-table path)")
        sub.add_argument("--hand-source", help="path to the hand-written C file to round-trip against")
        sub.add_argument("--no-roundtrip", action="store_true", help="skip the hand-written round-trip comparison")

    validate_parser = subparsers.add_parser("validate", help="validate a table's JSON source")
    add_common_args(validate_parser)
    validate_parser.set_defaults(func=cmd_validate)

    generate_parser = subparsers.add_parser("generate", help="validate and generate C89 output + inventory")
    add_common_args(generate_parser)
    generate_parser.add_argument("--out-dir", help="output directory for generated C (default: build/generated/data)")
    generate_parser.add_argument("--inventory", help="path to the committed inventory/summary file to write")
    generate_parser.set_defaults(func=cmd_generate)

    check_parser = subparsers.add_parser("check", help="fail on validation or generation drift; writes nothing")
    add_common_args(check_parser)
    check_parser.add_argument("--out-dir", help="output directory to compare against (default: build/generated/data)")
    check_parser.add_argument("--inventory", help="path to the committed inventory/summary file to compare against")
    check_parser.set_defaults(func=cmd_check)

    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
