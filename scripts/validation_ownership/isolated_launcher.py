#!/usr/bin/env python3
"""Closed isolated entry point for the ownership-probe foundation."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    if not sys.flags.isolated:
        print(
            "validation-ownership-launcher: isolated Python startup (-I) is required",
            file=sys.stderr,
        )
        return 2
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in {"check", "tests"}:
        print(
            "validation-ownership-launcher: mode is not allowlisted",
            file=sys.stderr,
        )
        return 2
    mode = arguments.pop(0)
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            del os.environ[name]
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    if mode == "tests":
        if arguments:
            print(
                "validation-ownership-launcher: tests accepts no arguments",
                file=sys.stderr,
            )
            return 2
        suite = unittest.defaultTestLoader.discover(
            str(ROOT / "scripts/validation_ownership/tests"),
            pattern="test_*.py",
            top_level_dir=str(ROOT),
        )
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    from scripts.validation_ownership import probe_check

    return probe_check.main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
