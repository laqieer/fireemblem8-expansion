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
        "--manifest", default=DEFAULT_MANIFEST,
        help="version-1 source-owned manifest (default: %(default)s)",
    )
    parser.add_argument(
        "--out-dir", default=DEFAULT_OUT_DIR,
        help="ignored generated-output directory (default: %(default)s)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "generate", "check", "sources", "tmx-incbin-consumers", "clean"):
        subparsers.add_parser(name)
    return parser


def _render_error(error):
    if isinstance(error, GeneratedDataValidationError):
        return "\n".join(str(item) for item in error.errors)
    return str(error)


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        args.out_dir = manifest.safe_output_dir(args.out_dir)
        if args.command == "validate":
            records = manifest.load_and_validate(args.manifest)
            print("OK: {} asset record(s) validated".format(len(records)))
        elif args.command == "generate":
            records = manifest.generate(args.manifest, args.out_dir)
            print("OK: generated {} asset record(s) under {}".format(len(records), args.out_dir))
        elif args.command == "check":
            records = manifest.check(args.manifest, args.out_dir)
            print("OK: {} generated asset record(s) are current".format(len(records)))
        elif args.command == "sources":
            records = manifest.load_and_validate(args.manifest)
            for record in records:
                for source in record.sources:
                    print(source)
        elif args.command == "tmx-incbin-consumers":
            records = manifest.load_and_validate(args.manifest)
            for record_id in manifest.tmx_incbin_consumer_ids(records):
                print(record_id)
        else:
            if os.path.exists(args.out_dir):
                shutil.rmtree(args.out_dir)
            print("OK: removed {}".format(args.out_dir))
    except (GeneratedDataError, GeneratedDataValidationError, OSError, ValueError) as exc:
        print(_render_error(exc), file=sys.stderr)
        return 1
    return 0
