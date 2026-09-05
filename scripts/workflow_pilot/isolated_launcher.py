#!/usr/bin/env python3
"""Closed isolated-startup launcher for protected workflow-pilot modes."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODES = frozenset(
    {
        "anchor-refs",
        "baseline",
        "classify-event",
        "git-broker-publish",
        "git-broker-preflight",
        "git-broker-serve",
        "hydrate",
        "lifecycle-check",
        "reporter-tests",
    }
)
LIFECYCLE_CHECKS = frozenset({"workflow-pilot-reporter", "workflow-pilot-tests"})


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


def run_lifecycle_check(arguments: list[str]) -> int:
    if len(arguments) != 6 or arguments[::2] != [
        "--artifact-root",
        "--authority-root",
        "--check",
    ]:
        raise ValueError("lifecycle-check requires its exact closed arguments")
    artifact_root = Path(arguments[1]).resolve(strict=True)
    authority_root = Path(arguments[3]).resolve(strict=True)
    check_id = arguments[5]
    if artifact_root != ROOT:
        raise ValueError(f"--artifact-root must identify launcher root {ROOT}")
    if check_id not in LIFECYCLE_CHECKS:
        raise ValueError("lifecycle check is not allowlisted")

    from scripts.workflow_pilot import reporter

    try:
        authority_root = reporter.validate_repository_root(authority_root)
    except reporter.PilotDataError as error:
        raise ValueError(str(error)) from error
    if check_id == "workflow-pilot-reporter":
        try:
            fixture = reporter.load_json(ROOT / reporter.BASELINE_FIXTURE_PATH)
            decisions = reporter.load_json(ROOT / reporter.DECISION_RECORD_PATH)
            report = reporter.build_report(fixture, decisions, authority_root)
            reporter.check_expected(
                report,
                reporter.load_json(ROOT / reporter.BASELINE_EXPECTED_PATH),
            )
        except reporter.PilotDataError as error:
            raise ValueError(str(error)) from error
        return 0

    os.environ["WORKFLOW_PILOT_TEST_AUTHORITY_ROOT"] = str(authority_root)
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "scripts.workflow_pilot.tests.test_reporter."
        "BaselineFixtureTests.test_frozen_baseline_and_expected_values"
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def dispatch(mode: str, arguments: list[str]) -> int:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(sorted(MODES))}")
    os.chdir(ROOT)
    if mode == "reporter-tests":
        return run_reporter_tests(arguments)
    if mode == "lifecycle-check":
        return run_lifecycle_check(arguments)
    if mode == "classify-event":
        from scripts.workflow_pilot import event_classifier

        return event_classifier.main(arguments)
    if mode in {
        "git-broker-preflight",
        "git-broker-publish",
        "git-broker-serve",
    }:
        from scripts.workflow_pilot import git_publication_broker

        broker_mode = {
            "git-broker-preflight": "preflight",
            "git-broker-publish": "publish",
            "git-broker-serve": "serve",
        }[mode]
        return git_publication_broker.main([broker_mode, *arguments])

    controlled_repository_root(arguments)
    if mode == "anchor-refs":
        from scripts.workflow_pilot import hydrate_authority

        return hydrate_authority.print_anchor_refs(arguments)
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
