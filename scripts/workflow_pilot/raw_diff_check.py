#!/usr/bin/env python3
"""Check added raw diff lines against the fixed handoff whitespace policy."""

from __future__ import annotations

import argparse
import os
import re
import stat
import subprocess
import sys
from pathlib import Path


GIT = "/usr/bin/git"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HUNK_RE = re.compile(
    rb"^@@ -[0-9]+(?:,[0-9]+)? \+([0-9]+)(?:,([0-9]+))? @@"
)
WHITESPACE_POLICY = "blank-at-eol,blank-at-eof,space-before-tab"


def git_environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }


def git_command(repository_root: Path, *arguments: str) -> tuple[str, ...]:
    return (
        GIT,
        "--no-replace-objects",
        "--no-pager",
        "-C",
        str(repository_root),
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "core.pager=cat",
        "-c",
        f"core.whitespace={WHITESPACE_POLICY}",
        "-c",
        "core.attributesFile=/dev/null",
        "-c",
        "color.ui=false",
        "-c",
        "color.diff=false",
        "-c",
        "core.quotePath=true",
        "-c",
        "diff.external=",
        "-c",
        "diff.wsErrorHighlight=all",
        "-c",
        "diff.algorithm=myers",
        "-c",
        "diff.indentHeuristic=false",
        "-c",
        "diff.renames=false",
        "-c",
        "core.abbrev=40",
        *arguments,
    )


def run_git(repository_root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        git_command(repository_root, *arguments),
        cwd=repository_root,
        env=git_environment(),
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(
            f"Git {' '.join(arguments)} failed"
            + (f": {detail}" if detail else "")
        )
    return completed.stdout
def _git_dir(repository_root: Path) -> Path:
        entry = repository_root / ".git"
        metadata = entry.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            return entry
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("repository .git entry is not permitted")
        raw = entry.read_text(encoding="utf-8")
        if not raw.startswith("gitdir:"):
            raise ValueError("repository .git file is malformed")
        git_dir = Path(raw[len("gitdir:"):].strip())
        if not git_dir.is_absolute():
            git_dir = repository_root / git_dir
        git_dir = git_dir.resolve(strict=True)
        if not git_dir.is_dir():
            raise ValueError("repository gitdir is not a directory")
        return git_dir
def _reject_local_attributes(repository_root: Path) -> None:
        attributes = _git_dir(repository_root) / "info" / "attributes"
        try:
            metadata = attributes.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size:
            raise ValueError("local Git attributes are not permitted")


def exact_repository_root(value: str) -> Path:
        root = Path(value).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("repository root is not a directory")
        _reject_local_attributes(root)
        top = Path(
            run_git(root, "rev-parse", "--show-toplevel")
            .decode("utf-8")
            .strip()
        ).resolve()
        if root != top:
            raise ValueError(f"repository root must be exact Git top level {top}")
        return root


def validate_sha(value: str, label: str) -> str:
    if SHA_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase full Git SHA")
    return value


def leading_space_before_tab(content: bytes) -> bool:
    leading = content[: len(content) - len(content.lstrip(b" \t"))]
    return b" \t" in leading


def raw_diff_errors(
    repository_root: Path,
    parent_sha: str,
    candidate_sha: str,
) -> list[str]:
    diff = run_git(
        repository_root,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--text",
        "--no-renames",
        "--unified=0",
        "--no-color",
        "--no-prefix",
        parent_sha,
        candidate_sha,
        "--",
    )
    errors = []
    path = None
    line_number = None
    for line in diff.split(b"\n"):
        if line.startswith(b"diff --git "):
            path = None
            line_number = None
            continue
        if line.startswith(b"+++ ") and line_number is None:
            raw_path = line[4:]
            path = raw_path.decode("utf-8", errors="surrogateescape")
            continue
        match = HUNK_RE.match(line)
        if match is not None:
            line_number = int(match.group(1))
            continue
        if line_number is None:
            continue
        if line.startswith(b"+"):
            content = line[1:]
            if content.endswith((b" ", b"\t", b"\r")):
                errors.append(f"{path}:{line_number}: blank-at-eol")
            if leading_space_before_tab(content):
                errors.append(f"{path}:{line_number}: space-before-tab")
            line_number += 1
        elif line.startswith(b" "):
            line_number += 1

    changed_paths = run_git(
        repository_root,
        "diff",
        "--name-only",
        "--no-renames",
        "-z",
        parent_sha,
        candidate_sha,
        "--",
    ).split(b"\0")
    for raw_path in changed_paths:
        if not raw_path:
            continue
        path_text = raw_path.decode("utf-8", errors="surrogateescape")
        try:
            candidate = run_git(
                repository_root,
                "cat-file",
                "blob",
                f"{candidate_sha}:{path_text}",
            )
        except ValueError:
            continue
        try:
            parent = run_git(
                repository_root,
                "cat-file",
                "blob",
                f"{parent_sha}:{path_text}",
            )
        except ValueError:
            parent = b""
        candidate_lf = candidate.replace(b"\r\n", b"\n")
        parent_lf = parent.replace(b"\r\n", b"\n")
        candidate_blank_lines = len(candidate_lf) - len(
            candidate_lf.rstrip(b"\n")
        )
        parent_blank_lines = len(parent_lf) - len(parent_lf.rstrip(b"\n"))
        if candidate_blank_lines > 1 and candidate_blank_lines > parent_blank_lines:
            errors.append(f"{path_text}:EOF: blank-at-eof")
    return sorted(set(errors))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", required=True)
    parser.add_argument("--parent", required=True)
    parser.add_argument("--candidate", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        repository_root = exact_repository_root(args.repository_root)
        parent_sha = validate_sha(args.parent, "parent")
        candidate_sha = validate_sha(args.candidate, "candidate")
        errors = raw_diff_errors(repository_root, parent_sha, candidate_sha)
    except (OSError, ValueError) as error:
        print(f"raw-diff-check: {error}", file=sys.stderr)
        return 2
    for error in errors:
        print(error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
