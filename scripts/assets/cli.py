"""Command-line surface for the versioned asset-manifest framework."""

from __future__ import annotations

import argparse
import os
import shutil
import sys

from scripts.generated_data.diagnostics import (
    GeneratedDataError,
    GeneratedDataValidationError,
)
from scripts.generated_data import idspace as generated_idspace

from . import manifest


DEFAULT_MANIFEST = os.path.join("assets", "manifest.json")
DEFAULT_OUT_DIR = os.path.join("build", "generated", "assets")


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--custom-spell-effects",
        choices=("0", "1"),
        default="0",
        help="resolved EXPANSION_CUSTOM_SPELL_EFFECTS value (default: %(default)s)",
    )
    parser.add_argument(
        "--item-id-cap",
        default=None,
        help="resolved FE8_ITEM_ID_CAP (required for validation and generation)",
    )
    parser.add_argument(
        "--manifest", default=DEFAULT_MANIFEST,
        help="version-1 source-owned manifest (default: %(default)s)",
    )
    parser.add_argument(
        "--out-dir", default=DEFAULT_OUT_DIR,
        help="ignored generated-output directory (default: %(default)s)",
    )
    parser.add_argument(
        "--discovery-makefile",
        default=None,
        help="build-local discovery Make artifact output",
    )
    parser.add_argument(
        "--selection-stamp",
        default=None,
        help="build-local selected-profile stamp updated with generation",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in (
        "validate",
        "generate",
        "check",
        "sources",
        "discovery-makefile",
        "portrait-incbin-consumers",
        "tmx-incbin-consumers",
        "banim-incbin-consumers",
        "custom-spell-incbin-consumers",
        "clean",
    ):
        subparsers.add_parser(name)
    return parser


def _render_error(error):
    if isinstance(error, GeneratedDataValidationError):
        return "\n".join(str(item) for item in error.errors)
    return str(error)


def _item_id_cap(value):
    if value is None:
        raise GeneratedDataError(
            "--item-id-cap is required for validation, generation, and discovery"
        )
    try:
        cap = int(value, 0)
    except ValueError as exc:
        raise GeneratedDataError(
            "--item-id-cap {!r} is not an integer".format(value)
        ) from exc
    return generated_idspace.validate_domain_cap(
        generated_idspace.domain_by_key("item"), cap
    )


def main(argv=None):
    args = _parser().parse_args(argv)
    custom_spell_effects = int(args.custom_spell_effects)
    try:
        args.out_dir = manifest.safe_output_dir(args.out_dir)
        item_id_cap = (
            None if args.command == "clean" else _item_id_cap(args.item_id_cap)
        )
        if args.command == "validate":
            records = manifest.load_and_validate(
                args.manifest,
                custom_spell_effects,
                item_id_cap=item_id_cap,
            )
            print("OK: {} asset record(s) validated".format(len(records)))
        elif args.command == "generate":
            records = manifest.generate(
                args.manifest,
                args.out_dir,
                custom_spell_effects,
                item_id_cap=item_id_cap,
                selection_stamp=args.selection_stamp,
            )
            print("OK: generated {} asset record(s) under {}".format(len(records), args.out_dir))
        elif args.command == "check":
            records = manifest.check(
                args.manifest,
                args.out_dir,
                custom_spell_effects,
                item_id_cap=item_id_cap,
            )
            print("OK: {} generated asset record(s) are current".format(len(records)))
        elif args.command == "sources":
            records = manifest.load_discovery(args.manifest)
            for source in manifest.discovery_sources(records):
                print(source)
        elif args.command == "discovery-makefile":
            if args.discovery_makefile is None:
                raise GeneratedDataError(
                    "discovery-makefile requires --discovery-makefile"
                )
            records = manifest.write_discovery_makefile(
                args.manifest, args.discovery_makefile
            )
            print(
                "OK: wrote discovery Make artifact for {} asset record(s)".format(
                    len(records)
                )
            )
        elif args.command == "portrait-incbin-consumers":
            records = manifest.load_discovery(args.manifest)
            for record_id in manifest.portrait_incbin_consumer_ids(records):
                print(record_id)
        elif args.command == "tmx-incbin-consumers":
            records = manifest.load_discovery(args.manifest)
            for record_id in manifest.tmx_incbin_consumer_ids(records):
                print(record_id)
        elif args.command == "banim-incbin-consumers":
            records = manifest.load_discovery(args.manifest)
            for record_id in manifest.banim_incbin_consumer_ids(records):
                print(record_id)
        elif args.command == "custom-spell-incbin-consumers":
            records = manifest.load_discovery(args.manifest)
            for record_id in manifest.custom_spell_incbin_consumer_ids(records):
                print(record_id)
        else:
            if os.path.exists(args.out_dir):
                shutil.rmtree(args.out_dir)
            print("OK: removed {}".format(args.out_dir))
    except (GeneratedDataError, GeneratedDataValidationError, OSError, ValueError) as exc:
        print(_render_error(exc), file=sys.stderr)
        return 1
    return 0
