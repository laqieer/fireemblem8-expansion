#!/usr/bin/env python3
"""Closed isolated-startup launcher for protected workflow-pilot modes."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODES = frozenset({"hydrate", "reporter-tests", "baseline"})


def clear_ambient_git_environment() -> None:
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            del os.environ[name]


def controlled_repository_root(arguments: list[str]) -> Path:
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
    return root


def run_reporter_tests(arguments: list[str]) -> int:
    if arguments:
        raise ValueError("reporter-tests mode accepts no arguments")
    suite = unittest.defaultTestLoader.discover(
        str(ROOT / "scripts" / "workflow_pilot" / "tests"),
        pattern="test_*.py",
        top_level_dir=str(ROOT),
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def dispatch(mode: str, arguments: list[str]) -> int:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(sorted(MODES))}")
    os.chdir(ROOT)
    if mode == "reporter-tests":
        return run_reporter_tests(arguments)

    controlled_repository_root(arguments)
    if mode == "hydrate":
        from scripts.workflow_pilot import hydrate_authority

        return hydrate_authority.main(arguments)

    from scripts.workflow_pilot import reporter

    return reporter.main(arguments)


def main(argv: list[str] | None = None) -> int:
    if not sys.flags.isolated:
        print(
            "workflow-pilot-launcher: isolated Python startup (-I) is required",
            file=sys.stderr,
        )
        return 2
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        print("workflow-pilot-launcher: mode is required", file=sys.stderr)
        return 2
    clear_ambient_git_environment()
    sys.path.insert(0, str(ROOT))
    try:
        return dispatch(arguments[0], arguments[1:])
    except (OSError, ValueError) as error:
        print(f"workflow-pilot-launcher: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
