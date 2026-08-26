"""Discover selected autoplay-strategy inputs used to validate event lists."""

from __future__ import annotations

import argparse
import os
import sys

from ..autoplaystrategies import schema as strategies_schema
from ..diagnostics import GeneratedDataError


def _canonical(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def collect_input_paths(strategy_source):
    records = strategies_schema.load_records(strategy_source)
    paths = set(records["source_paths"])
    paths.add(_canonical(strategy_source))
    return tuple(sorted(paths))


def write_depfile(depfile, target, inputs):
    content = "{}: {}\n".format(target, " ".join(inputs))
    directory = os.path.dirname(depfile)
    os.makedirs(directory, exist_ok=True)
    try:
        with open(depfile, "r", encoding="utf-8") as handle:
            if handle.read() == content:
                return False
    except OSError:
        pass
    temporary = depfile + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.replace(temporary, depfile)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-source", required=True)
    parser.add_argument("--make-target")
    parser.add_argument("--depfile")
    args = parser.parse_args(argv)
    if bool(args.make_target) != bool(args.depfile):
        parser.error("--make-target and --depfile must be supplied together")
    try:
        inputs = collect_input_paths(args.strategy_source)
    except OSError as error:
        print(
            "error: unable to read event-list strategy dependency source: {}".format(error),
            file=sys.stderr,
        )
        return 1
    except GeneratedDataError as error:
        print("error: {}".format(error), file=sys.stderr)
        return 1
    if args.depfile:
        write_depfile(args.depfile, args.make_target, inputs)
    else:
        print("\n".join(inputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
