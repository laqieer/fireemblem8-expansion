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
        "--manifest", default=DEFAULT_MANIFEST,
        help="version-1 source-owned manifest (default: %(default)s)",
    )
    parser.add_argument(
        "--out-dir", default=DEFAULT_OUT_DIR,
        help="ignored generated-output directory (default: %(default)s)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in (
        "validate",
        "generate",
        "check",
        "sources",
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


def main(argv=None):
    args = _parser().parse_args(argv)
    custom_spell_effects = int(args.custom_spell_effects)
    try:
        args.out_dir = manifest.safe_output_dir(args.out_dir)
        if args.command == "validate":
            records = manifest.load_and_validate(args.manifest, custom_spell_effects)
            print("OK: {} asset record(s) validated".format(len(records)))
        elif args.command == "generate":
            records = manifest.generate(
                args.manifest, args.out_dir, custom_spell_effects
            )
            print("OK: generated {} asset record(s) under {}".format(len(records), args.out_dir))
        elif args.command == "check":
            records = manifest.check(
                args.manifest, args.out_dir, custom_spell_effects
            )
            print("OK: {} generated asset record(s) are current".format(len(records)))
        elif args.command == "sources":
            records = manifest.load_discovery(args.manifest)
            sources = {"assets/portrait_registry.json"}
            for record in records:
                sources.update(record.sources)
                kind = manifest.KIND_REGISTRY.resolve(record.kind)
                if kind is not None:
                    sources.update(kind.source_dependencies(record))
            for source in sorted(sources):
                print(source)
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
