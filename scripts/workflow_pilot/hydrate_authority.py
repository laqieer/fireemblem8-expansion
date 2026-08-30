#!/usr/bin/env python3
"""Hydrate exact workflow-pilot commit authority for CI checkouts."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import reporter


GIT = "/usr/bin/git"
BATCH_SIZE = 256
FETCH_TIMEOUT_SECONDS = 120
FETCH_OPTIONS = (
    "--quiet",
    "--no-tags",
    "--filter=blob:none",
    "--no-write-fetch-head",
)


def run_git(
    repository_root: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    environment = reporter.offline_git_environment()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        completed = subprocess.run(
            (GIT, "-C", str(repository_root), *arguments),
            env=environment,
            check=False,
            capture_output=True,
            timeout=FETCH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise reporter.PilotDataError(
            f"cannot execute bounded Git authority hydration: {error}"
        ) from error
    if not check or completed.returncode == 0:
        return completed
    detail = completed.stderr.decode("utf-8", errors="replace").strip()
    raise reporter.PilotDataError(
        f"Git {' '.join(arguments)} failed"
        + (f": {detail}" if detail else "")
    )


def available_commits(
    repository_root: Path,
    shas: list[str],
) -> set[str]:
    if not shas:
        return set()
    environment = reporter.offline_git_environment()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    try:
        completed = subprocess.run(
            (
                GIT,
                "-C",
                str(repository_root),
                "cat-file",
                "--batch-check=%(objectname) %(objecttype)",
            ),
            input="".join(f"{sha}\n" for sha in shas).encode("ascii"),
            env=environment,
            check=False,
            capture_output=True,
            timeout=FETCH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise reporter.PilotDataError(
            f"cannot inspect Git authority objects: {error}"
        ) from error
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise reporter.PilotDataError(
            "cannot inspect Git authority objects"
            + (f": {detail}" if detail else "")
        )
    lines = completed.stdout.decode("ascii").splitlines()
    if len(lines) != len(shas):
        raise reporter.PilotDataError(
            "Git returned incomplete authority object results"
        )
    available = set()
    for requested, line in zip(shas, lines):
        fields = line.split()
        if len(fields) == 2 and fields[0] == requested and fields[1] == "commit":
            available.add(requested)
        elif fields != [requested, "missing"]:
            raise reporter.PilotDataError(
                f"fixture identity {requested} is not a commit object"
            )
    return available


def hydrate_authority(
    repository_root: Path,
    fixture_path: Path,
    expected_head: str,
) -> dict[str, int]:
    repository_root = reporter.validate_repository_root(repository_root)
    expected_fixture = (repository_root / reporter.BASELINE_FIXTURE_PATH).resolve()
    try:
        fixture_path = fixture_path.resolve(strict=True)
    except OSError as error:
        raise reporter.PilotDataError(
            f"strict baseline fixture is unavailable: {error}"
        ) from error
    if fixture_path != expected_fixture:
        raise reporter.PilotDataError(
            f"--fixture must identify {expected_fixture}"
        )
    reporter.expect_sha(expected_head, "--expected-head")
    data = reporter.validate_fixture(reporter.load_json(fixture_path))

    remote = reporter.run_git(
        repository_root,
        "config",
        "--get",
        "remote.origin.url",
    ).decode("utf-8").strip()
    repository = reporter._github_repository_from_remote(remote)
    if repository != data["fixture"]["repository"]:
        raise reporter.PilotDataError(
            "origin does not match the strict baseline repository"
        )

    head_before = reporter.run_git(
        repository_root,
        "rev-parse",
        "HEAD",
    ).decode("ascii").strip()
    if head_before != expected_head:
        raise reporter.PilotDataError(
            f"checked-out HEAD {head_before} does not match {expected_head}"
        )
    refs_before = reporter.run_git(
        repository_root,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)",
    )

    required = sorted(data["commits"])
    missing = sorted(set(required) - available_commits(repository_root, required))
    for offset in range(0, len(missing), BATCH_SIZE):
        batch = missing[offset : offset + BATCH_SIZE]
        run_git(
            repository_root,
            "fetch",
            *FETCH_OPTIONS,
            "origin",
            *batch,
        )

    unavailable = sorted(
        set(required) - available_commits(repository_root, required)
    )
    if unavailable:
        raise reporter.PilotDataError(
            "exact fixture authority remains unavailable: "
            + ", ".join(unavailable)
        )
    head_after = reporter.run_git(
        repository_root,
        "rev-parse",
        "HEAD",
    ).decode("ascii").strip()
    refs_after = reporter.run_git(
        repository_root,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)",
    )
    if head_after != expected_head:
        raise reporter.PilotDataError(
            "exact fixture authority hydration moved checked-out HEAD"
        )
    if refs_after != refs_before:
        raise reporter.PilotDataError(
            "exact fixture authority hydration moved repository refs"
        )
    return {"required": len(required), "fetched": len(missing)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "CI-only: hydrate exact commit objects required by the committed "
            "workflow-pilot fixture without moving HEAD or refs."
        )
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = hydrate_authority(
            args.repository_root,
            args.fixture,
            args.expected_head,
        )
    except reporter.PilotDataError as error:
        print(f"workflow-pilot-hydration: {error}", file=sys.stderr)
        return 2
    print(
        "workflow-pilot-hydration: "
        f"required={result['required']} fetched={result['fetched']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
