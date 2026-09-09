#!/usr/bin/env python3
"""Closed isolated-startup launcher for the foundation and ownership graph."""

from __future__ import annotations

import argparse
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


class _LauncherArgumentParserError(ValueError):
    pass


class _LauncherArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise _LauncherArgumentParserError(message)


def _controlled_root(argument: str) -> Path:
    candidate = Path(argument)
    if candidate.is_symlink():
        raise ValueError("repository root must be a non-symlink directory")
    root = candidate.resolve(strict=True)
    if root != ROOT:
        raise ValueError(
            f"--repository-root must identify controlled source root {ROOT}"
        )
    return root


def _parse_reporter_arguments(arguments: list[str]):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.validation_ownership import reporter

    parser = reporter.build_arg_parser(parser_class=_LauncherArgumentParser)
    try:
        return reporter, parser.parse_args(arguments)
    except _LauncherArgumentParserError as error:
        raise ValueError(str(error)) from error


def main(argv: list[str] | None = None) -> int:
    if not sys.flags.isolated or not sys.flags.no_site:
        print(
            "validation-ownership-launcher: isolated no-site startup (-I -S) is required",
            file=sys.stderr,
        )
        return 2
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0].startswith("--"):
        _clear_ambient_execution_environment()
        sys.path.insert(0, str(ROOT))
        from scripts.validation_ownership.consumer import main as foundation_main

        return foundation_main(arguments)
    if not arguments or arguments[0] not in MODES:
        print(
            "validation-ownership-launcher: mode is not allowlisted",
            file=sys.stderr,
        )
        return 2
    mode = arguments.pop(0)
    try:
        if mode in {"check", "resolve"}:
            reporter, parsed = _parse_reporter_arguments(arguments)
            parsed.repository_root = _controlled_root(parsed.repository_root)
            if mode == "check" and parsed.changed:
                raise ValueError("check mode does not accept --changed")
            if mode == "resolve" and not parsed.changed:
                raise ValueError("resolve mode requires at least one --changed")
        else:
            reporter = None
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
        if reporter is None:
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
        return reporter.run_parsed(parsed)
    except (OSError, ValueError) as error:
        print(f"validation-ownership-launcher: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
