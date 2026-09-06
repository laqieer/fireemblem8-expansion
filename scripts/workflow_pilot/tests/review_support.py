"""Owned source-backed review fixtures; no live PRs or outside worktrees."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
ENV = {
    "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_AUTHOR_NAME": "Review fixture", "GIT_AUTHOR_EMAIL": "review@example.invalid",
    "GIT_COMMITTER_NAME": "Review fixture", "GIT_COMMITTER_EMAIL": "review@example.invalid",
}


def git(root, *args):
    completed = subprocess.run(
        ["/usr/bin/git", "--no-optional-locks", "-c", "core.fsmonitor=false",
         "-c", "core.hooksPath=/dev/null", "-C", str(root), *args],
        env=ENV, capture_output=True, check=True)
    return completed.stdout.decode().strip()


def request(case="TC-WORKFLOW-REVIEW-FAMILY-001", subject="review-session",
            base="a" * 40, head="b" * 40):
    return {
        "schema_version": 1, "repository": "owner/repo", "pull_request": 1,
        "base_sha": base, "candidate_sha": head,
        "subjects": [{"case_id": case, "subject": subject}], "findings": [],
    }


class Runtime:
    def __init__(self, head, subjects):
        self.calls = []
        self.result = SimpleNamespace(
            task="task-1", owner="reviewer", role="code-review", head=head,
            subjects=subjects, completed=True, actions=("read-candidate", "emit-report"),
            files=3, findings=(), started_at="2026-01-01T00:00:00Z",
            completed_at="2026-01-01T00:00:01Z")

    def start(self, **arguments):
        self.calls.append(("start", arguments))
        return self.result.task

    def read(self, task):
        self.calls.append(("read", task))
        return self.result


class Snapshot:
    def __init__(self, root):
        self.root = root
        git(root, "init", "-q")
        names = subprocess.run(
            ["/usr/bin/git", "-C", str(ROOT), "ls-files", "-z"],
            env=ENV, capture_output=True, check=True).stdout.decode().split("\0")
        exact = {
            "scripts/__init__.py", "scripts/host_python.py",
            "scripts/workflow_pilot/__init__.py", "scripts/workflow_pilot/reporter.py",
            "scripts/workflow_pilot/isolated_launcher.py",
            "scripts/workflow_pilot/review_family.py",
            "scripts/workflow_pilot/review_subjects.py",
            "scripts/workflow_pilot/trusted_review_gate.py",
            "scripts/assets/__init__.py", "scripts/assets/tmx.py",
            "docs/test-cases/registry.json",
            "src/expansion_aoe.c", "src/expansion_aoe_reference.c",
            "src/events/ch2-eventinfo.h", "src/events_udefs.c",
            "assets/manifest.json",
            "src/events_shoplist.c", "src/events_trapdata.c",
            "tools/gba-playtest/tests/c/expansion_aoe_driver.c",
            "tools/gba-playtest/tests/c/expansion_aoe_disabled_driver.c",
        }
        prefixes = ("include/", "src/data/", "src/events/", "assets/tmx/",
                    "scripts/generated_data/", "reports/generated_data_")
        for name in sorted(exact | {name for name in names if name.startswith(prefixes)}):
            source = ROOT / name
            if not source.is_file():
                continue
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        git(root, "add", ".")
        git(root, "commit", "-qm", "source-backed fixture")
        self.base = git(root, "rev-parse", "HEAD")

    def commit(self, changes, *, parent=None):
        git(self.root, "reset", "--hard", parent or self.base)
        for name, value in changes.items():
            path = self.root / name
            if value is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(value.encode() if isinstance(value, str) else value)
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "controlled production mutation")
        revision = git(self.root, "rev-parse", "HEAD")
        git(self.root, "reset", "--hard", self.base)
        return revision


@contextmanager
def snapshot():
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="review-family-tests-", dir=build) as directory:
        yield Snapshot(Path(directory))
