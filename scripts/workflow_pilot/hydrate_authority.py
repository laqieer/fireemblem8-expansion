#!/usr/bin/env python3
"""Hydrate exact workflow-pilot commit authority for CI checkouts."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import reporter


GIT = reporter.GIT
BATCH_SIZE = 256
FETCH_TIMEOUT_SECONDS = 120
ANCHOR_PREFIX = "refs/tags/workflow-pilot-baseline/"
FETCH_OPTIONS = (
    "--quiet",
    "--no-tags",
    "--filter=blob:none",
    "--no-write-fetch-head",
)
BLOB_FETCH_OPTIONS = (
    "--quiet",
    "--no-tags",
    "--no-write-fetch-head",
)


def run_git(
    repository_root: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            reporter.git_command(repository_root, *arguments),
            env=reporter.git_environment(offline=False),
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


def available_objects(
    repository_root: Path,
    shas: list[str],
    object_type: str,
) -> set[str]:
    if not shas:
        return set()
    if object_type == "blob":
        available = set()
        for requested in shas:
            try:
                completed = subprocess.run(
                    reporter.git_command(
                        repository_root,
                        "cat-file",
                        "-t",
                        requested,
                    ),
                    env=reporter.git_environment(offline=True),
                    check=False,
                    capture_output=True,
                    timeout=FETCH_TIMEOUT_SECONDS,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise reporter.PilotDataError(
                    f"cannot inspect Git authority objects: {error}"
                ) from error
            if completed.returncode != 0:
                continue
            actual_type = completed.stdout.decode("ascii").strip()
            if actual_type != object_type:
                raise reporter.PilotDataError(
                    f"fixture identity {requested} is not a {object_type} object"
                )
            available.add(requested)
        return available
    try:
        completed = subprocess.run(
            reporter.git_command(
                repository_root,
                "cat-file",
                "--batch-check=%(objectname) %(objecttype)",
            ),
            input="".join(f"{sha}\n" for sha in shas).encode("ascii"),
            env=reporter.git_environment(offline=True),
            check=False,
            capture_output=True,
            timeout=FETCH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise reporter.PilotDataError(
            f"cannot inspect Git authority objects: {error}"
        ) from error
    lines = completed.stdout.decode("ascii").splitlines()
    if len(lines) != len(shas):
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise reporter.PilotDataError(
            "Git returned incomplete authority object results"
            + (f": {detail}" if detail else "")
        )
    available = set()
    for requested, line in zip(shas, lines):
        fields = line.split()
        if (
            len(fields) == 2
            and fields[0] == requested
            and fields[1] == object_type
        ):
            available.add(requested)
        elif fields != [requested, "missing"]:
            raise reporter.PilotDataError(
                f"fixture identity {requested} is not a commit object"
            )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if len(available) == len(shas) or "lazy fetching disabled" not in detail:
            raise reporter.PilotDataError(
                "Git failed while inspecting available authority objects"
                + (f": {detail}" if detail else "")
            )
    return available


def available_commits(
    repository_root: Path,
    shas: list[str],
) -> set[str]:
    return available_objects(repository_root, shas, "commit")


def required_commits_from_fixture(fixture_path: Path) -> tuple[str, list[str]]:
    data = reporter.validate_fixture(reporter.load_json(fixture_path))
    return data["fixture"]["repository"], sorted(data["commits"])


def maximal_commit_tips(graph: dict[str, list[str]]) -> list[str]:
    ancestors = set()
    for sha in graph:
        pending = list(graph[sha])
        while pending:
            candidate = pending.pop()
            if candidate in ancestors or candidate not in graph:
                continue
            ancestors.add(candidate)
            pending.extend(graph[candidate])
    return sorted(set(graph) - ancestors)


def required_anchor_refs(fixture_path: Path) -> dict[str, str]:
    data = reporter.validate_fixture(reporter.load_json(fixture_path))
    graph = {
        sha: commit["parents"]
        for sha, commit in data["commits"].items()
    }
    tips = maximal_commit_tips(graph)
    covered = set()
    for tip in tips:
        pending = [tip]
        while pending:
            candidate = pending.pop()
            if candidate in covered or candidate not in graph:
                continue
            covered.add(candidate)
            pending.extend(graph[candidate])
    if covered != set(graph):
        raise reporter.PilotDataError(
            "derived baseline anchors do not cover every fixture commit"
        )
    return {f"{ANCHOR_PREFIX}{sha}": sha for sha in tips}


def parse_remote_anchor_refs(raw: bytes) -> dict[str, str]:
    anchors = {}
    for line in raw.decode("ascii").splitlines():
        fields = line.split("\t")
        if len(fields) != 2:
            raise reporter.PilotDataError("origin anchor response is malformed")
        sha, name = fields
        reporter.expect_sha(sha, "origin anchor target")
        suffix = name.removeprefix(ANCHOR_PREFIX)
        if (
            suffix == name
            or reporter.SHA_RE.fullmatch(suffix) is None
            or name in anchors
        ):
            raise reporter.PilotDataError(
                "origin anchor name is malformed or duplicated"
            )
        anchors[name] = sha
    return anchors


def query_remote_anchor_refs(repository_root: Path) -> dict[str, str]:
    return parse_remote_anchor_refs(
        run_git(
            repository_root,
            "ls-remote",
            "--refs",
            "origin",
            f"{ANCHOR_PREFIX}*",
        ).stdout
    )


def required_override_decision_commits(
    fixture_path: Path,
    decisions_path: Path,
) -> tuple[str, list[str], list[str]]:
    data = reporter.validate_fixture(reporter.load_json(fixture_path))
    decisions = reporter.expect_object(
        reporter.load_json(decisions_path),
        "decisions",
    )
    reporter.expect_keys(
        decisions,
        "decisions",
        ("schema_version", "pull_requests", "artifacts"),
    )
    version = reporter.expect_int(
        decisions["schema_version"],
        "decisions.schema_version",
        1,
    )
    if version != reporter.SCHEMA_VERSION:
        raise reporter.PilotDataError(
            f"decisions schema_version must be {reporter.SCHEMA_VERSION}"
        )
    records = reporter.expect_list(
        decisions["pull_requests"],
        "decisions.pull_requests",
    )
    reporter.expect_list(decisions["artifacts"], "decisions.artifacts")
    records_by_pr = {}
    for index, raw_record in enumerate(records):
        label = f"decisions.pull_requests[{index}]"
        record = reporter.expect_object(raw_record, label)
        reporter.expect_keys(
            record,
            label,
            (
                "pull_request",
                "risk_boundaries",
                "threshold",
                "gate_mode",
                "stack",
                "pilot",
            ),
        )
        number = reporter.expect_int(
            record["pull_request"],
            f"{label}.pull_request",
            1,
        )
        if number in records_by_pr:
            raise reporter.PilotDataError(
                f"duplicate PR decision {number}"
            )
        threshold = reporter.expect_object(
            record["threshold"],
            f"{label}.threshold",
        )
        reporter.expect_keys(
            threshold,
            f"{label}.threshold",
            ("triggers", "override_history"),
        )
        records_by_pr[number] = threshold

    introduction_events = [
        event
        for event in data["events"].values()
        if event["type"] == "threshold_override_introduced"
    ]
    required_decision_commits = set()
    for number, threshold in records_by_pr.items():
        history = reporter.expect_list(
            threshold["override_history"],
            f"PR {number}.threshold.override_history",
        )
        introductions = {
            event["override_index"]: event
            for event in introduction_events
            if event["pr_number"] == number
        }
        if len(introductions) != len(
            [
                event
                for event in introduction_events
                if event["pr_number"] == number
            ]
        ):
            raise reporter.PilotDataError(
                f"PR {number} threshold override provenance repeats an index"
            )
        if set(introductions) != set(range(len(history))):
            raise reporter.PilotDataError(
                f"PR {number} threshold overrides lack exact authoritative "
                "introduction coverage"
            )
        for override_index in range(len(history)):
            entry = reporter.historical_override_entry(
                decisions,
                "hydration-input",
                number,
                override_index,
            )
            introduction = introductions[override_index]
            if introduction["decision_digest"] != reporter.threshold_override_digest(
                number,
                override_index,
                entry,
            ):
                raise reporter.PilotDataError(
                    f"PR {number} threshold override {override_index} digest "
                    "does not match the current decision entry"
                )
            required_decision_commits.add(introduction["sha"])
        if history:
            reviews = [
                review
                for review in data["reviews"].values()
                if review["pr_number"] == number
                and review["author"] == reporter.REVIEW_BOT
            ]
            if reviews:
                first_review = min(
                    reviews,
                    key=lambda review: reporter.parse_time(
                        review["submitted_at"],
                        f"review {review['id']}.submitted_at",
                    ),
                )
                required_decision_commits.add(first_review["commit_sha"])

    orphan_prs = sorted(
        {
            event["pr_number"]
            for event in introduction_events
        }
        - set(records_by_pr)
    )
    if orphan_prs:
        raise reporter.PilotDataError(
            "threshold override provenance has no decision record for PRs "
            + ", ".join(str(number) for number in orphan_prs)
        )
    return (
        data["fixture"]["repository"],
        sorted(data["commits"]),
        sorted(required_decision_commits),
    )


def authority_state(repository_root: Path) -> tuple[bytes, bytes, bytes | None]:
    head = reporter.run_git(repository_root, "rev-parse", "HEAD")
    refs = reporter.run_git(
        repository_root,
        "for-each-ref",
        "--format=%(refname)%00%(objectname)",
    )
    raw_path = (
        reporter.run_git(
            repository_root,
            "rev-parse",
            "--git-path",
            "FETCH_HEAD",
        )
        .decode("utf-8")
        .strip()
    )
    fetch_head_path = Path(raw_path)
    if not fetch_head_path.is_absolute():
        fetch_head_path = repository_root / fetch_head_path
    try:
        fetch_head = (
            fetch_head_path.read_bytes()
            if fetch_head_path.is_file()
            else None
        )
    except OSError as error:
        raise reporter.PilotDataError(
            f"cannot inspect FETCH_HEAD authority state: {error}"
        ) from error
    return head, refs, fetch_head


def require_unchanged_authority_state(
    repository_root: Path,
    expected_head: str,
    before: tuple[bytes, bytes, bytes | None],
) -> None:
    after = authority_state(repository_root)
    if after[0].decode("ascii").strip() != expected_head:
        raise reporter.PilotDataError(
            "exact fixture authority hydration moved checked-out HEAD"
        )
    if after[1] != before[1]:
        raise reporter.PilotDataError(
            "exact fixture authority hydration moved repository refs"
        )
    if after[2] != before[2]:
        raise reporter.PilotDataError(
            "exact fixture authority hydration changed FETCH_HEAD"
        )


def hydrate_anchor_commits(
    repository_root: Path,
    repository: str,
    required: list[str],
    anchors: dict[str, str],
    expected_head: str,
) -> dict[str, int]:
    repository_root = reporter.validate_repository_root(repository_root)
    reporter.expect_string(repository, "required repository")
    reporter.expect_sha(expected_head, "--expected-head")
    if not required:
        raise reporter.PilotDataError("required commit set must not be empty")
    for sha in required:
        reporter.expect_sha(sha, "required commit")
    if required != sorted(set(required)):
        raise reporter.PilotDataError(
            "required commits must be unique and sorted"
        )
    expected_anchors = {
        f"{ANCHOR_PREFIX}{sha}": sha
        for sha in sorted(anchors.values())
    }
    if anchors != expected_anchors:
        raise reporter.PilotDataError(
            "required anchor refs must be canonical, unique, and sorted"
        )

    remote = reporter.run_git(
        repository_root,
        "config",
        "--get",
        "remote.origin.url",
    ).decode("utf-8").strip()
    origin_repository = reporter._github_repository_from_remote(remote)
    if origin_repository != repository:
        raise reporter.PilotDataError(
            "origin does not match the required repository"
        )
    remote_anchors = query_remote_anchor_refs(repository_root)
    if remote_anchors != anchors:
        missing = sorted(set(anchors) - set(remote_anchors))
        extra = sorted(set(remote_anchors) - set(anchors))
        moved = sorted(
            name
            for name in set(anchors) & set(remote_anchors)
            if anchors[name] != remote_anchors[name]
        )
        raise reporter.PilotDataError(
            "origin baseline anchor refs differ from derived fixture authority "
            f"(missing={missing}, extra={extra}, moved={moved})"
        )

    before = authority_state(repository_root)
    head_before = before[0].decode("ascii").strip()
    if head_before != expected_head:
        raise reporter.PilotDataError(
            f"checked-out HEAD {head_before} does not match {expected_head}"
        )
    missing = sorted(set(required) - available_commits(repository_root, required))
    anchor_names = sorted(anchors)
    if missing:
        for offset in range(0, len(anchor_names), BATCH_SIZE):
            run_git(
                repository_root,
                "fetch",
                *FETCH_OPTIONS,
                "origin",
                *anchor_names[offset : offset + BATCH_SIZE],
            )

    unavailable = sorted(
        set(required) - available_commits(repository_root, required)
    )
    if unavailable:
        raise reporter.PilotDataError(
            "exact fixture authority remains unavailable: "
            + ", ".join(unavailable)
        )
    actual = reporter._load_git_commit_objects(repository_root, required)
    actual_tips = maximal_commit_tips(
        {sha: commit["parents"] for sha, commit in actual.items()}
    )
    if set(anchors.values()) != set(actual_tips):
        raise reporter.PilotDataError(
            "origin baseline anchors do not cover the validated raw parent graph"
        )
    require_unchanged_authority_state(repository_root, expected_head, before)
    return {"required": len(required), "fetched": len(missing)}


def required_decision_blob_ids(
    repository_root: Path,
    decision_commits: list[str],
) -> list[str]:
    blob_ids = set()
    path = reporter.DECISION_RECORD_PATH.as_posix()
    for commit in decision_commits:
        raw = reporter.run_git(
            repository_root,
            "ls-tree",
            commit,
            "--",
            path,
        ).decode("utf-8")
        fields = raw.rstrip("\n").split(maxsplit=3)
        if (
            len(fields) != 4
            or fields[0] != "100644"
            or fields[1] != "blob"
            or fields[3] != path
        ):
            raise reporter.PilotDataError(
                f"commit {commit} lacks exact regular-file decision record"
            )
        blob_ids.add(reporter.expect_sha(fields[2], f"commit {commit} decision blob"))
    return sorted(blob_ids)


def hydrate_override_decision_blobs(
    repository_root: Path,
    repository: str,
    decision_commits: list[str],
    expected_head: str,
) -> dict[str, int]:
    repository_root = reporter.validate_repository_root(repository_root)
    reporter.expect_string(repository, "required repository")
    reporter.expect_sha(expected_head, "--expected-head")
    if decision_commits != sorted(set(decision_commits)):
        raise reporter.PilotDataError(
            "required decision commits must be unique and sorted"
        )
    for commit in decision_commits:
        reporter.expect_sha(commit, "required decision commit")
    remote = reporter.run_git(
        repository_root,
        "config",
        "--get",
        "remote.origin.url",
    ).decode("utf-8").strip()
    if reporter._github_repository_from_remote(remote) != repository:
        raise reporter.PilotDataError(
            "origin does not match the required repository"
        )
    before = authority_state(repository_root)
    if before[0].decode("ascii").strip() != expected_head:
        raise reporter.PilotDataError(
            "checked-out HEAD does not match --expected-head"
        )
    required = required_decision_blob_ids(
        repository_root,
        decision_commits,
    )
    missing = sorted(
        set(required) - available_objects(repository_root, required, "blob")
    )
    for offset in range(0, len(missing), BATCH_SIZE):
        run_git(
            repository_root,
            "fetch",
            *BLOB_FETCH_OPTIONS,
            "origin",
            *missing[offset : offset + BATCH_SIZE],
        )
    unavailable = sorted(
        set(required) - available_objects(repository_root, required, "blob")
    )
    if unavailable:
        raise reporter.PilotDataError(
            "exact override decision blobs remain unavailable: "
            + ", ".join(unavailable)
        )
    require_unchanged_authority_state(repository_root, expected_head, before)
    return {"required_blobs": len(required), "fetched_blobs": len(missing)}


def hydrate_authority(
    repository_root: Path,
    fixture_path: Path,
    decisions_path: Path,
    expected_head: str,
) -> dict[str, int]:
    repository_root, fixture_path, decisions_path = validate_input_paths(
        repository_root,
        fixture_path,
        decisions_path,
    )
    repository, required, decision_commits = required_override_decision_commits(
        fixture_path,
        decisions_path,
    )
    commit_result = hydrate_anchor_commits(
        repository_root,
        repository,
        required,
        required_anchor_refs(fixture_path),
        expected_head,
    )
    blob_result = hydrate_override_decision_blobs(
        repository_root,
        repository,
        decision_commits,
        expected_head,
    )
    return {**commit_result, **blob_result}


def validate_input_paths(
    repository_root: Path,
    fixture_path: Path,
    decisions_path: Path,
) -> tuple[Path, Path, Path]:
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
    expected_decisions = (repository_root / reporter.DECISION_RECORD_PATH).resolve()
    try:
        decisions_path = decisions_path.resolve(strict=True)
    except OSError as error:
        raise reporter.PilotDataError(
            f"strict decision record is unavailable: {error}"
        ) from error
    if decisions_path != expected_decisions:
        raise reporter.PilotDataError(
            f"--decisions must identify {expected_decisions}"
        )
    return repository_root, fixture_path, decisions_path


def print_anchor_refs(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Print required workflow-pilot remote anchor refs."
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        _, fixture, _ = validate_input_paths(
            args.repository_root,
            args.fixture,
            args.decisions,
        )
        anchors = required_anchor_refs(fixture)
    except reporter.PilotDataError as error:
        print(f"workflow-pilot-anchors: {error}", file=sys.stderr)
        return 2
    for name, sha in anchors.items():
        print(f"{name} {sha}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "CI-only: hydrate exact commit objects required by the committed "
            "workflow-pilot fixture without moving HEAD or refs."
        )
    )
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--decisions", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = hydrate_authority(
            args.repository_root,
            args.fixture,
            args.decisions,
            args.expected_head,
        )
    except reporter.PilotDataError as error:
        print(f"workflow-pilot-hydration: {error}", file=sys.stderr)
        return 2
    print(
        "workflow-pilot-hydration: "
        f"required={result['required']} fetched={result['fetched']} "
        f"required_blobs={result['required_blobs']} "
        f"fetched_blobs={result['fetched_blobs']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
