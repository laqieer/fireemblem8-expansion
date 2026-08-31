#!/usr/bin/env python3
"""Closed isolated-startup launcher for validation ownership reporting."""

from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODES = frozenset({"check", "resolve"})


def _clear_ambient_git_environment() -> None:
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
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
    if not sys.flags.isolated:
        print(
            "validation-ownership-launcher: isolated Python startup (-I) is required",
            file=sys.stderr,
        )
        return 2
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] not in MODES:
        print(
            "validation-ownership-launcher: mode must be check or resolve",
            file=sys.stderr,
        )
        return 2
    mode = arguments.pop(0)
    try:
        _controlled_root(arguments)
        if mode == "check" and "--changed" in arguments:
            raise ValueError("check mode does not accept --changed")
        if mode == "resolve" and "--changed" not in arguments:
            raise ValueError("resolve mode requires at least one --changed")
        _clear_ambient_git_environment()
        os.chdir(ROOT)
        sys.path.insert(0, str(ROOT))
        from scripts.validation_ownership import reporter

        return reporter.main(arguments)
    except (OSError, ValueError) as error:
        print(f"validation-ownership-launcher: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
