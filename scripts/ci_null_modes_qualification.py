"""One separately allocated restricted-host fixture qualification; never merge."""

import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

SOURCE = "0818374feed58b1495e9ff3c9e415791e2a8cb12"
REPOSITORY = "laqieer/fireemblem8-expansion"
BRANCH = "diagnostic/issue-180-null-modes-1"
WORKFLOW = ".github/workflows/issue180-null-modes-1.yml"
PROGRAM = "scripts/ci_null_modes_qualification.py"
FILES = {WORKFLOW, PROGRAM}
ARTIFACTS = {"scope.json", "result.json"}
METHODS = (
    "test_toolchain_null_mount_preserves_readonly_and_writable_parents",
    "test_toolchain_null_mount_rejects_wrong_and_substituted_devices",
    "test_toolchain_null_mount_failures_have_no_remount_fallback",
    "test_toolchain_null_old_remount_restores_readonly_rejection",
)
MODES = ("readonly", "writable", "wrong-device", "substituted", "unsupported", "locked", "old")
ENV = {
    "HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_COUNT": "0",
    "GIT_NO_REPLACE_OBJECTS": "1", "GIT_OPTIONAL_LOCKS": "0",
}


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def identity(context, event):
    sha, run = context.get("GITHUB_SHA", ""), context.get("GITHUB_RUN_ID", "")
    require(re.fullmatch("[0-9a-f]{40}", sha) is not None
            and re.fullmatch("[1-9][0-9]{0,19}", run) is not None, "run identity")
    expected = {
        "GITHUB_REPOSITORY": REPOSITORY, "GITHUB_EVENT_NAME": "push",
        "GITHUB_REF": "refs/heads/" + BRANCH, "GITHUB_ACTOR": "laqieer",
        "GITHUB_TRIGGERING_ACTOR": "laqieer", "GITHUB_RUN_NUMBER": "1", "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_WORKFLOW_SHA": sha,
        "GITHUB_WORKFLOW_REF": REPOSITORY + "/" + WORKFLOW + "@refs/heads/" + BRANCH,
        "RUNNER_ENVIRONMENT": "github-hosted", "RUNNER_OS": "Linux",
    }
    require(all(context.get(key) == value for key, value in expected.items()), "fixed hosted owner run")
    require(type(event) is dict and type(event.get("repository")) is dict
            and type(event.get("sender")) is dict
            and event["repository"].get("full_name") == REPOSITORY
            and event["repository"].get("private") is False
            and event["sender"].get("login") == "laqieer"
            and event.get("ref") == expected["GITHUB_REF"] and event.get("after") == sha
            and event.get("before") == "0" * 40 and event.get("created") is True
            and event.get("deleted") is False, "first public branch creation")
    return {"head": sha, "source": SOURCE, "run_id": run, "run_number": 1, "run_attempt": 1,
            "required_backend": "restricted", "allocation_consumed": True, "never_merge": True}


def git(root, *arguments):
    result = subprocess.run(
        ["/usr/bin/git", "--no-pager", "--no-optional-locks", "-c", "core.hooksPath=/dev/null",
         "-c", "core.fsmonitor=false", "-C", str(root), *arguments],
        env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=5, check=False,
    )
    require(result.returncode == 0 and len(result.stdout) <= 16384, "read-only source check")
    return result.stdout


def verify(root, revision, *, harness=False):
    require(root.is_absolute() and root.resolve(strict=True) == root, "canonical checkout")
    require(git(root, "rev-parse", "HEAD").strip() == revision.encode(), "exact source revision")
    git(root, "diff", "--quiet", "--no-ext-diff", "--ignore-submodules=none", revision, "--")
    if harness:
        require(git(root, "rev-list", "--parents", "-n", "1", revision).split() ==
                [revision.encode(), SOURCE.encode()], "one normal preparation parent")
        rows = git(root, "diff", "--name-status", "-z", SOURCE, revision).split(b"\0")
        require(len(rows) == 5 and rows[-1] == b"", "two additive paths")
        require({(rows[index], rows[index + 1].decode("ascii")) for index in (0, 2)} ==
                {(b"A", name) for name in FILES}, "closed preparation inventory")
        for name in FILES:
            mode = os.lstat(root / name).st_mode
            require(stat.S_ISREG(mode) and stat.S_IMODE(mode) == 0o644, "regular preparation mode")


def context():
    workspace = Path(os.environ["GITHUB_WORKSPACE"])
    require(workspace.resolve(strict=True) == workspace, "canonical workspace")
    with Path(os.environ["GITHUB_EVENT_PATH"]).open("rb") as stream:
        raw = stream.read(65537)
    require(len(raw) <= 65536, "bounded event")
    scope = identity(os.environ, json.loads(raw))
    harness, subject = workspace / "harness", workspace / "candidate"
    require(Path(__file__).resolve() == harness / PROGRAM, "exact harness program")
    output = workspace / ("issue180-null-modes-1-" + scope["run_id"] + "-records")
    return scope, harness, subject, output


def write(output, name, value):
    require(name in ARTIFACTS, "artifact allowlist")
    data = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    require(len(data) <= 16384, "artifact bound")
    with (output / name).open("xb") as stream:
        stream.write(data)


def observation(mode, value):
    require(value["mode"] == mode and type(value["availability_status"]) is int
            and (value["backend"], value["availability_status"]) in (("ordinary", 0), ("restricted", 1)),
            "actual selected backend record")
    return {
        "mode": mode, "backend": value["backend"], "availability_status": value["availability_status"],
        "same_object": value["before"]["identity"] == value["after"]["identity"],
        "same_mount": value["before"]["mount_id"] == value["after"]["mount_id"],
        "flags": [value["before"]["flags"], value["after"]["flags"]], "failure": value["failure"],
        "calls": value["calls"], "post_drop": value["post_drop"], "denied": value["denied"],
        "null_io": value["null_io"], "fd_closed": value["fd_closed"],
        "nonzero_topology": value["local_nonzero_topology"],
    }


def failure_facts(value):
    require(type(value) is dict, "finite failure facts")
    kept = {}
    for name in (
        "backend", "stage", "first_error_stage", "availability_status", "outer_status",
        "status", "reap_status", "reaped", "capture_complete", "lifecycle_closed", "fd_restored",
    ):
        if name not in value:
            continue
        item = value[name]
        require(item is None or type(item) is bool or type(item) is int and -(1 << 63) <= item < 1 << 64
                or type(item) is str and re.fullmatch("[a-z][a-z0-9-]{0,63}", item), "finite failure field")
        kept[name] = item
    if "first_error" in value:
        error = value["first_error"]
        require(error is None or type(error) in (list, tuple) and len(error) == 2
                and all(item is None or type(item) is int and 0 <= item <= 4095 for item in error),
                "finite error pair")
        kept["first_error"] = error
    nested = value.get("coordinator_failure")
    if nested is not None:
        require(type(nested) is dict and "coordinator_failure" not in nested, "failure nesting")
        kept["coordinator_failure"] = failure_facts(nested)
    return kept


def qualify(subject):
    require(Path.cwd() == subject, "selected working directory")
    sys.path.insert(0, str(subject))
    from scripts.validation_ownership.tests.test_foundation import FoundationTests
    from scripts.validation_ownership.tests.null_mount_fixture import FixtureFailure

    original = FoundationTests.null_mount_kernel_control
    values = []

    def observe(case, mode):
        result = original(case, mode)
        values.append(observation(mode, result))
        require(values[-1]["backend"] == "restricted", "actual selected backend is not restricted")
        return result

    failures = []

    class Result(unittest.TestResult):
        def _exc_info_to_string(self, error, test):
            value = error[1]
            record = {"kind": type(value).__name__[:64], "method": test.id().split(".")[-1][:128]}
            if isinstance(value, FixtureFailure):
                record["facts"] = failure_facts(value.facts)
            failures.append(record)
            return record["kind"]

    result = Result()
    result.failfast = True
    started = time.monotonic()
    with patch.object(FoundationTests, "null_mount_kernel_control", observe):
        unittest.TestSuite(FoundationTests(name) for name in METHODS).run(result)
    return {
        "tests": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "skips": len(result.skipped), "elapsed_seconds": time.monotonic() - started,
        "selectors": list(METHODS), "observations": values, "first_failures": failures,
        "complete": result.wasSuccessful() and tuple(row["mode"] for row in values) == MODES,
    }


def main():
    require(sys.flags.isolated and sys.flags.no_site and not sys.flags.optimize, "isolated interpreter")
    scope, harness, subject, output = context()
    require(sys.argv[1:] in (["plan"], ["run"]), "closed operation")
    if sys.argv[1:] == ["plan"]:
        verify(harness, scope["head"], harness=True)
        output.mkdir(mode=0o700)
        write(output, "scope.json", scope)
        return 0
    with (output / "scope.json").open("rb") as stream:
        raw = stream.read(16385)
    require(len(raw) <= 16384 and json.loads(raw) == scope, "original scope")
    checks = {"harness_before": None, "subject_before": None, "harness_after": None, "subject_after": None}
    result = {"complete": False, "tests": 0, "observations": []}
    error = None
    try:
        verify(harness, scope["head"], harness=True)
        checks["harness_before"] = True
        verify(subject, SOURCE)
        checks["subject_before"] = True
        result = qualify(subject)
    except (OSError, ValueError, RuntimeError) as failure:
        error = type(failure).__name__
    finally:
        for key, root, revision, is_harness in (
            ("harness_after", harness, scope["head"], True), ("subject_after", subject, SOURCE, False),
        ):
            try:
                verify(root, revision, harness=is_harness)
                checks[key] = True
            except (OSError, ValueError, RuntimeError):
                checks[key] = False
    passed = result["complete"] and error is None and all(value is True for value in checks.values())
    write(output, "result.json", {"scope": scope, "source_checks": checks, "runner_error": error,
                                 "passed": passed, "result": result})
    print("restricted seven-mode qualification: " + ("passed" if passed else "failed"))
    return 0 if passed else 125


if __name__ == "__main__":
    raise SystemExit(main())
