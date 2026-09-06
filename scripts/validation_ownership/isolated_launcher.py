#!/usr/bin/env python3
"""Closed isolated-startup launcher for validation ownership reporting."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODES = frozenset({"check", "resolve", "tests", "lifecycle-check"})


def _clear_ambient_execution_environment() -> None:
    for name in tuple(os.environ):
        if name.startswith("GIT_") or name in {
            "MAKEFILES", "MAKEFLAGS", "GNUMAKEFLAGS", "MAKEOVERRIDES", "MFLAGS",
            "BASH_ENV", "ENV",
        }:
            del os.environ[name]


def _controlled_root(arguments: list[str]) -> None:
    positions = [
        index
        for index, argument in enumerate(arguments)
        if argument == "--repository-root"
    ]
    if len(positions) != 1 or positions[0] + 1 >= len(arguments):
        raise ValueError("mode requires exactly one --repository-root")
    root = Path(arguments[positions[0] + 1]).resolve(strict=True)
    if root != ROOT:
        raise ValueError(
            f"--repository-root must identify controlled source root {ROOT}"
        )


def main(argv: list[str] | None = None) -> int:
    if not sys.flags.isolated or not sys.flags.no_site:
        print(
            "validation-ownership-launcher: isolated no-site startup (-I -S) is required",
            file=sys.stderr,
        )
        return 2
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in MODES:
        print(
            "validation-ownership-launcher: mode is not allowlisted",
            file=sys.stderr,
        )
        return 2
    mode = arguments.pop(0)
    try:
        if mode in {"check", "resolve"}:
            _controlled_root(arguments)
        if mode == "check" and "--changed" in arguments:
            raise ValueError("check mode does not accept --changed")
        if mode == "resolve" and "--changed" not in arguments:
            raise ValueError("resolve mode requires at least one --changed")
        _clear_ambient_execution_environment()
        os.chdir(ROOT)
        sys.path.insert(0, str(ROOT))
        if mode == "tests":
            if arguments:
                raise ValueError("tests mode accepts no arguments")
            suite = unittest.defaultTestLoader.discover(
                str(ROOT / "scripts/validation_ownership/tests"),
                pattern="test_*.py",
                top_level_dir=str(ROOT),
            )
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            return 0 if result.wasSuccessful() else 1
        from scripts.validation_ownership import reporter

        if mode == "lifecycle-check":
            if len(arguments) != 6 or arguments[::2] != [
                "--artifact-root",
                "--authority-root",
                "--check",
            ]:
                raise ValueError("lifecycle-check requires exact closed arguments")
            try:
                return reporter.run_lifecycle_check(
                    Path(arguments[1]),
                    Path(arguments[3]),
                    arguments[5],
                )
            except reporter.OwnershipError as error:
                raise ValueError(str(error)) from error
        return reporter.main(arguments)
    except (OSError, ValueError) as error:
        print(f"validation-ownership-launcher: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
