#!/usr/bin/env python3
"""Closed public self-check for the validation-ownership probe foundation."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not sys.flags.isolated or len(arguments) != 2:
        print(
            "validation-ownership-probe-check: isolated startup and one "
            "--repository-root are required",
            file=sys.stderr,
        )
        return 2
    if arguments[0] != "--repository-root":
        print(
            "validation-ownership-probe-check: unexpected arguments",
            file=sys.stderr,
        )
        return 2
    try:
        selected = Path(arguments[1]).resolve(strict=True)
    except OSError as error:
        print(
            f"validation-ownership-probe-check: {error}",
            file=sys.stderr,
        )
        return 2
    if selected != ROOT:
        print(
            "validation-ownership-probe-check: repository root differs from "
            "the controlled source root",
            file=sys.stderr,
        )
        return 2
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            del os.environ[name]
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))

    from scripts.validation_ownership.budget import ProbeBudget, ProbeLimits
    from scripts.validation_ownership.make_probe import (
        MakeVariant,
        run_make_probe,
    )
    from scripts.validation_ownership.sandbox import ExecutionSnapshot

    scratch = ROOT / "build/test-artifacts/validation-ownership-probe-check"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    fixture = scratch / "fixture"
    fixture.mkdir()
    (fixture / "Makefile").write_text(
        "check: input\n\t@echo probe-foundation-ok\n",
        encoding="ascii",
    )
    (fixture / "input").write_text("admitted\n", encoding="ascii")
    try:
        budget = ProbeBudget(ProbeLimits(seconds=60))
        observations = run_make_probe(
            ExecutionSnapshot.capture(fixture, budget),
            targets={"check"},
            variants=[MakeVariant()],
            owner_inputs={"check": {"Makefile", "input"}},
            registered_commands=[],
            scratch_root=scratch,
            budget=budget,
        )
        if (
            len(observations) != 1
            or observations[0].target != "check"
            or b"probe-foundation-ok" not in observations[0].raw_stdout
        ):
            raise RuntimeError("authentic Make self-check returned wrong semantics")
    except Exception as error:
        print(
            f"validation-ownership-probe-check: {error}",
            file=sys.stderr,
        )
        return 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
