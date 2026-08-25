"""Resolve chapter-objective enablement through the canonical table loader."""

from __future__ import annotations

import argparse
import sys

from ..diagnostics import GeneratedDataError
from . import schema


def is_enabled(source_path):
    return bool(schema.load_records(source_path))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    args = parser.parse_args(argv)
    try:
        print("1" if is_enabled(args.source) else "0")
    except (OSError, GeneratedDataError) as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
