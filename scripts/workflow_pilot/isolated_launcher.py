#!/usr/bin/env python3
"""Closed isolated-startup launcher for protected workflow-pilot modes."""

from __future__ import annotations

import os
import hashlib
import re
import subprocess
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MODES = frozenset(
    {
        "agent-handoff",
        "anchor-refs",
        "attest-metadata-event",
        "baseline",
        "classify-event",
        "hydrate",
        "lifecycle-check",
        "pr-metadata",
        "reporter-tests",
        "review-family",
    }
)
LIFECYCLE_CHECKS = frozenset({"workflow-pilot-reporter", "workflow-pilot-tests"})


def clear_ambient_git_environment() -> None:
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            del os.environ[name]


def controlled_repository_root(arguments: list[str], *, external: bool = False) -> Path:
    positions = [
        index
        for index, argument in enumerate(arguments)
        if argument == "--repository-root"
    ]
    if len(positions) != 1 or positions[0] + 1 >= len(arguments):
        raise ValueError("mode requires exactly one --repository-root")
    root = Path(arguments[positions[0] + 1]).resolve(strict=True)
    if external:
        if root == ROOT:
            raise ValueError("review-family requires a trusted launcher outside candidate storage")
    elif root != ROOT:
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


def run_review_family(arguments: list[str]) -> int:
    root = controlled_repository_root(arguments, external=True)
    positions = [index for index, value in enumerate(arguments) if value == "--tool-revision"]
    if len(positions) != 1 or positions[0] + 1 >= len(arguments):
        raise ValueError("review-family requires exactly one --tool-revision")
    revision = arguments[positions[0] + 1]
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("reviewed tool revision must be an exact lowercase SHA")
    environment = {
        "PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
    }

    def git(*args):
        try:
            completed = subprocess.run(
                ["/usr/bin/git", "--no-optional-locks", "-c", "core.fsmonitor=false",
                 "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
                env=environment, capture_output=True, timeout=60)
        except subprocess.TimeoutExpired as error:
            raise ValueError("reviewed Git source timed out: " + str(error)[-1000:]) from error
        if completed.returncode:
            raise ValueError("reviewed Git source unavailable: " +
                             completed.stderr.decode(errors="replace")[-1000:])
        return completed.stdout

    top = Path(git("rev-parse", "--show-toplevel").decode().strip()).resolve(strict=True)
    if top != root:
        raise ValueError("candidate storage must be the exact repository top level")
    if git("cat-file", "-t", revision).strip() != b"commit":
        raise ValueError("reviewed tool revision must identify a commit object")

    def source(path, *, namespace=False):
        records = [record for record in git("ls-tree", "-z", revision, "--", path).split(b"\0")
                   if record]
        if namespace and not records:
            return b""
        if len(records) != 1:
            raise ValueError("reviewed source is missing or ambiguous: " + path)
        metadata, actual_path = records[0].split(b"\t", 1)
        mode, kind, oid = metadata.decode("ascii").split()
        if kind != "blob" or mode not in {"100644", "100755"} or actual_path.decode() != path:
            raise ValueError("reviewed source must be a regular Git blob: " + path)
        raw = git("cat-file", "blob", oid)
        if hashlib.sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest() != oid:
            raise ValueError("reviewed source object mismatch: " + path)
        return raw

    # Fixed source-loading seam, not a transitive import/capability analyzer.
    # In particular, do not import a working-copy reporter/initializer to
    # establish the very Git authority used to select its reviewed bytes.
    for name, path, package in (
        ("scripts", "scripts/__init__.py", True),
        ("scripts.workflow_pilot", "scripts/workflow_pilot/__init__.py", True),
        ("scripts.workflow_pilot.reporter", "scripts/workflow_pilot/reporter.py", False),
        ("scripts.workflow_pilot.trusted_review_gate", "scripts/workflow_pilot/trusted_review_gate.py", False),
    ):
        raw = source(path, namespace=name == "scripts")
        module = types.ModuleType(name)
        module.__file__ = str(root / path)
        module.__package__ = name if package else name.rpartition(".")[0]
        if package:
            module.__path__ = []
        sys.modules[name] = module
        if "." in name:
            parent, _, child = name.rpartition(".")
            setattr(sys.modules[parent], child, module)
        exec(compile(raw, revision + ":" + path, "exec"), module.__dict__)
    return module.main(arguments)


def dispatch(mode: str, arguments: list[str]) -> int:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(sorted(MODES))}")
    os.chdir(ROOT)
    if mode == "agent-handoff":
        from scripts.workflow_pilot import agent_handoff

        return agent_handoff.main(arguments)
    if mode == "reporter-tests":
        return run_reporter_tests(arguments)
    if mode == "lifecycle-check":
        return run_lifecycle_check(arguments)
    if mode == "review-family":
        return run_review_family(arguments)
    if mode == "classify-event":
        from scripts.workflow_pilot import event_classifier

        return event_classifier.main(arguments)
    if mode == "attest-metadata-event":
        from scripts.workflow_pilot import metadata_event

        return metadata_event.main(arguments)
    if mode == "pr-metadata":
        from scripts.workflow_pilot import pr_metadata

        return pr_metadata.main(arguments)

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
