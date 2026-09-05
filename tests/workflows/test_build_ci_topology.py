"""Structural contract for consolidated candidate and master Build CI."""

from __future__ import annotations

import copy
import fnmatch
import http.server
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import types
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import (
    candidate_evidence,
    event_classifier,
    hydrate_authority,
    metadata_adapter_contract,
    reporter,
    summary_continuity_contract,
)


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
WORKFLOW_GOVERNANCE_CASE = ROOT / "docs" / "test-cases" / "workflow-governance.md"
WORKFLOW_PILOT_DOC = ROOT / "docs" / "workflow-pilot.md"
EVENT_FIXTURE = (
    ROOT
    / "scripts"
    / "workflow_pilot"
    / "tests"
    / "fixtures"
    / "event_classification.json"
)
PRE_FIX_WORKFLOW = EVENT_FIXTURE.with_name("pre_fix_build.yml")
PYTHON_REQUIREMENTS = ROOT / ".github" / "requirements" / "build.txt"
RETIRED_WORKFLOW_FILENAME = "full" + "-matrix.yml"
RETIRED_WORKFLOW = ROOT / ".github" / "workflows" / RETIRED_WORKFLOW_FILENAME
MAKEFILE = ROOT / "Makefile"
MASTER_PUBLISHER_CONDITION = (
    "${{ always() && needs.event-identity.result == 'success' && "
    "github.event_name == 'push' && "
    "needs.event-identity.outputs.fallback_kind == 'push' && "
    "needs.event-identity.outputs.fallback_sha == github.event.after && "
    "needs.event-identity.outputs.fallback_sha == github.sha }}"
)
COMBINED_WORKERS = ("host-tests", "build", "extended-host-tests", "legacy")
METADATA_ADAPTER_JOBS = candidate_evidence.METADATA_ADAPTER_JOB_IDS
METADATA_SKIPPED_JOBS = candidate_evidence.METADATA_SKIPPED_JOB_IDS
CLASSIFIER_JOB = "event-classifier"
METADATA_TRIGGERED_JOBS = set(METADATA_ADAPTER_JOBS) | {
    "event-identity",
    "event-router",
    CLASSIFIER_JOB,
    "summary",
}
METADATA_CHECK_CONTEXTS = set(COMBINED_WORKERS) | {
    "event-identity",
    "event-router",
    candidate_evidence.METADATA_CLASSIFIER,
    "patch-release",
    candidate_evidence.METADATA_ATTESTATION,
}
METADATA_CLASSIFIER_FAILURE_CHECKS = set(COMBINED_WORKERS) | {
    "event-identity",
    "event-router",
    candidate_evidence.METADATA_CLASSIFIER,
    "patch-release",
    "summary",
}
REQUIRED_BUILD_CONTEXTS = frozenset(candidate_evidence.REQUIRED_BUILD_CONTEXTS)
SUMMARY_NEEDS = (
    "needs: [event-identity, event-classifier, host-tests, build, "
    "extended-host-tests, legacy, patch-release]"
)
WORKER_NEEDS = "needs: [event-identity, event-classifier]"
FULL_WORKER_STEP_CONDITION = (
    "${{ needs.event-classifier.result == 'failure' || "
    "needs.event-classifier.outputs.classification == 'full' }}"
)
METADATA_ADAPTER_STEP_CONDITION = (
    "${{ needs.event-classifier.result == 'success' && "
    "needs.event-classifier.outputs.classification == 'metadata-only' }}"
)
WORKER_CONDITION = (
    "${{ always() && ((needs.event-classifier.result == 'success' && "
    "needs.event-identity.result == 'success' && "
    "needs.event-classifier.outputs.classification == 'full' && "
    "needs.event-classifier.outputs.head_valid == 'true' && "
    "needs.event-classifier.outputs.run_expensive == 'true' && "
    "((github.event_name == 'pull_request' && "
    "needs.event-identity.outputs.fallback_kind == 'pull_request' && "
    "needs.event-identity.outputs.fallback_sha == "
    "needs.event-classifier.outputs.expected_head && "
    "needs.event-classifier.outputs.expected_head == "
    "github.event.pull_request.head.sha && "
    "github.event.pull_request.head.sha != '' && "
    "(needs.event-classifier.outputs.identity_valid == 'true' || "
    "needs.event-classifier.outputs.full_fallback == 'true')) || "
    "(github.event_name == 'push' && "
    "needs.event-identity.outputs.fallback_kind == 'push' && "
    "needs.event-identity.outputs.fallback_sha == "
    "needs.event-classifier.outputs.expected_head && "
    "needs.event-identity.outputs.fallback_sha == github.sha && "
    "needs.event-classifier.outputs.identity_valid == 'true' && "
    "needs.event-classifier.outputs.expected_head == github.event.after && "
    "needs.event-classifier.outputs.expected_base == '' && "
    "github.event.after != ''))) || "
    "(needs.event-classifier.result == 'failure' && "
    "needs.event-identity.result == 'success' && "
    "((github.event_name == 'pull_request' && "
    "needs.event-identity.outputs.fallback_kind == 'pull_request' && "
    "needs.event-identity.outputs.fallback_sha == "
    "github.event.pull_request.head.sha) || "
    "(github.event_name == 'push' && "
    "needs.event-identity.outputs.fallback_kind == 'push' && "
    "needs.event-identity.outputs.fallback_sha == github.event.after && "
    "needs.event-identity.outputs.fallback_sha == github.sha)))) }}"
)
HOST_BUILD_CONDITION = (
    "${{ always() && ((needs.event-classifier.result == 'success' && "
    "needs.event-identity.result == 'success' && "
    "needs.event-classifier.outputs.classification == 'metadata-only' && "
    "needs.event-classifier.outputs.head_valid == 'true' && "
    "needs.event-classifier.outputs.identity_valid == 'true' && "
    "needs.event-classifier.outputs.full_fallback == 'false' && "
    "needs.event-classifier.outputs.run_expensive == 'false' && "
    "github.event_name == 'pull_request' && "
    "needs.event-identity.outputs.fallback_kind == 'pull_request' && "
    "needs.event-identity.outputs.fallback_sha == "
    "needs.event-classifier.outputs.expected_head && "
    "needs.event-classifier.outputs.expected_head == "
    "github.event.pull_request.head.sha && "
    "needs.event-classifier.outputs.expected_base == "
    "github.event.pull_request.base.sha && "
    "github.event.pull_request.head.sha != '' && "
    "github.event.pull_request.base.sha != '') || "
    "(needs.event-classifier.result == 'success' && "
    "needs.event-identity.result == 'success' && "
    "needs.event-classifier.outputs.classification == 'full' && "
    "needs.event-classifier.outputs.head_valid == 'true' && "
    "needs.event-classifier.outputs.run_expensive == 'true' && "
    "((github.event_name == 'pull_request' && "
    "needs.event-identity.outputs.fallback_kind == 'pull_request' && "
    "needs.event-identity.outputs.fallback_sha == "
    "needs.event-classifier.outputs.expected_head && "
    "needs.event-classifier.outputs.expected_head == "
    "github.event.pull_request.head.sha && "
    "github.event.pull_request.head.sha != '' && "
    "(needs.event-classifier.outputs.identity_valid == 'true' || "
    "needs.event-classifier.outputs.full_fallback == 'true')) || "
    "(github.event_name == 'push' && "
    "needs.event-identity.outputs.fallback_kind == 'push' && "
    "needs.event-identity.outputs.fallback_sha == "
    "needs.event-classifier.outputs.expected_head && "
    "needs.event-identity.outputs.fallback_sha == github.sha && "
    "needs.event-classifier.outputs.identity_valid == 'true' && "
    "needs.event-classifier.outputs.expected_head == github.event.after && "
    "needs.event-classifier.outputs.expected_base == '' && "
    "github.event.after != ''))) || "
    "(needs.event-classifier.result == 'failure' && "
    "needs.event-identity.result == 'success' && "
    "((github.event_name == 'pull_request' && "
    "needs.event-identity.outputs.fallback_kind == 'pull_request' && "
    "needs.event-identity.outputs.fallback_sha == "
    "github.event.pull_request.head.sha) || "
    "(github.event_name == 'push' && "
    "needs.event-identity.outputs.fallback_kind == 'push' && "
    "needs.event-identity.outputs.fallback_sha == github.event.after && "
    "needs.event-identity.outputs.fallback_sha == github.sha)))) }}"
)
CANDIDATE_FULL_JOBS = set(COMBINED_WORKERS) | {
    "event-identity",
    "event-router",
    CLASSIFIER_JOB,
    "summary",
}
EMITTED_FULL_CHECKS = CANDIDATE_FULL_JOBS | {"patch-release"}
EVENT_CLASSIFIER_DYNAMIC_NAME = (
    "${{ needs.event-router.result == 'success' && "
    "needs.event-router.outputs.classification == 'metadata-only' && "
    "'metadata-classifier' || 'event-classifier' }}"
)
HASHED_PIP_INSTALL = (
    "python3 -m pip install --require-hashes --only-binary=:all: --no-deps "
    "-r .github/requirements/build.txt"
)
EXPECTED_HASHED_REQUIREMENTS = {
    "numpy": (
        "2.5.2",
        "sha256:3cdec01fa790a186d430433fdd4d4ffb70eed6f0eeb4bf05c8dbe2dce0a9bcb8",
    ),
    "pillow": (
        "12.3.0",
        "sha256:78cb2c6865a35ab8ff8b75fd122f6033b92a62c82801110e48ddd6c936a45d91",
    ),
    "ttp": (
        "0.10.1",
        "sha256:2c8bc871f7740b690c6df6fb8c9633be58fcda123eea3e53be40a79e4af54b83",
    ),
}
PIP_INVOCATION_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?:(?:/usr/bin/)?python(?:3(?:\.[0-9]+)?)?[\"']?\s+"
    r"(?:-I\s+)?-m\s+pip|pip(?:3(?:\.[0-9]+)?)?)"
    r"(?=\s|$)"
)
PULL_REQUEST_TRIGGER = "  pull_request:\n"
PULL_REQUEST_ACTIONS = ("opened", "synchronize", "reopened", "edited")
PUSH_TRIGGER = 'push:\n    branches: [ "master" ]'
SUMMARY_RESULTS = (
    '"$HOST_TESTS_RESULT"',
    '"$BUILD_RESULT"',
    '"$EXTENDED_HOST_TESTS_RESULT"',
    '"$LEGACY_RESULT"',
)
MAP_MENU_PRESENTATION_GATE = (
    "make expansion-modern-map-menu-presentation-check -j1"
)
WORKFLOW_PILOT_GATE = (
    "/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py "
    "reporter-tests"
)
WORKFLOW_PILOT_BASELINE_GATE = (
    "/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py baseline "
    '--repository-root "$GITHUB_WORKSPACE" '
    "--fixture scripts/workflow_pilot/tests/fixtures/baseline.json "
    "--decisions .github/workflow-pilot-decisions.json "
    "--expected scripts/workflow_pilot/tests/fixtures/baseline_expected.json "
    "> /dev/null"
)
EXPECTED_BUILD_SHA_EXPRESSION = (
    "${{ (needs.event-classifier.result == 'success' && "
    "needs.event-classifier.outputs.expected_head) || "
    "(needs.event-classifier.result == 'failure' && "
    "needs.event-identity.outputs.fallback_sha) || '' }}"
)
HOST_ENV_LINE = f"      EXPECTED_BUILD_SHA: {EXPECTED_BUILD_SHA_EXPRESSION}"
METADATA_ADAPTER_ENV = (
    "        BASH_ENV: ''",
    "        CLASSIFICATION: ${{ needs.event-classifier.outputs.classification }}",
    "        CLASSIFIED_BASE_SHA: ${{ needs.event-classifier.outputs.expected_base }}",
    "        CLASSIFIED_BUILD_SHA: ${{ needs.event-classifier.outputs.expected_head }}",
    "        CLASSIFIER_RESULT: ${{ needs.event-classifier.result }}",
    "        ENV: ''",
    "        FALLBACK_IDENTITY_RESULT: ${{ needs.event-identity.result }}",
    "        FALLBACK_KIND: ${{ needs.event-identity.outputs.fallback_kind }}",
    "        FALLBACK_SHA: ${{ needs.event-identity.outputs.fallback_sha }}",
    "        FULL_FALLBACK: ${{ needs.event-classifier.outputs.full_fallback }}",
    "        HEAD_VALID: ${{ needs.event-classifier.outputs.head_valid }}",
    "        IDENTITY_VALID: ${{ needs.event-classifier.outputs.identity_valid }}",
    "        PATH: /usr/bin:/bin",
    "        RUN_EXPENSIVE: ${{ needs.event-classifier.outputs.run_expensive }}",
)
COMBINED_JOB_ENV = {
    "host-tests": (HOST_ENV_LINE,),
    "build": (HOST_ENV_LINE,),
    "extended-host-tests": (HOST_ENV_LINE,),
    "legacy": (
        HOST_ENV_LINE,
        "      AGBCC_COMMIT: da598c1d918402c42c0c0d7128ba14567f3175e9",
        "      MGFEMBP_AGBCC_COMMIT: 63b22f3eb8a8051af30bd80c4795b355e439e7ef",
    ),
}
WORKFLOW_PILOT_AUTHORITY_HYDRATION = (
    "/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py hydrate "
    '--repository-root "$GITHUB_WORKSPACE" '
    "--fixture scripts/workflow_pilot/tests/fixtures/baseline.json "
    "--decisions .github/workflow-pilot-decisions.json "
    '--expected-head "$EXPECTED_BUILD_SHA"'
)
SCRUBBED_STEP_ENV = (
    "        BASH_ENV: ''",
    "        ENV: ''",
    "        GIT_ALTERNATE_OBJECT_DIRECTORIES: ''",
    "        GIT_CEILING_DIRECTORIES: ''",
    "        GIT_COMMON_DIR: ''",
    "        GIT_CONFIG_COUNT: '0'",
    "        GIT_CONFIG_GLOBAL: /dev/null",
    "        GIT_CONFIG_KEY_0: ''",
    "        GIT_CONFIG_NOSYSTEM: '1'",
    "        GIT_CONFIG_PARAMETERS: ''",
    "        GIT_CONFIG_SYSTEM: /dev/null",
    "        GIT_CONFIG_VALUE_0: ''",
    "        GIT_DIR: ''",
    "        GIT_EXEC_PATH: ''",
    "        GIT_INDEX_FILE: ''",
    "        GIT_NAMESPACE: ''",
    "        GIT_NO_LAZY_FETCH: '1'",
    "        GIT_NO_REPLACE_OBJECTS: '1'",
    "        GIT_OBJECT_DIRECTORY: ''",
    "        GIT_REPLACE_REF_BASE: ''",
    "        GIT_WORK_TREE: ''",
    "        PATH: /usr/bin:/bin",
    "        PYTHONPATH: ''",
)


def _git_run(
    repository_root: Path,
    *arguments: str,
    check: bool = True,
    text: bool = False,
    offline: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        reporter.git_command(repository_root, *arguments),
        env=reporter.git_environment(offline=offline),
        check=check,
        capture_output=True,
        text=text,
    )


def _trigger_block(header: str, event_name: str) -> str:
    event = re.search(
        rf"^  {re.escape(event_name)}:[ \t]*(?P<inline>.*)$",
        header,
        re.MULTILINE,
    )
    if event is None:
        raise ValueError(f"missing {event_name} trigger")
    if event.group("inline"):
        raise ValueError(f"{event_name} trigger must use a block mapping")

    next_event = re.search(r"^  [A-Za-z_][A-Za-z0-9_-]*:", header[event.end():], re.MULTILINE)
    end = event.end() + next_event.start() if next_event is not None else len(header)
    return header[event.end():end]


def _flow_sequence(block: str, field: str) -> tuple[str, ...] | None:
    key = rf"(?:{re.escape(field)}|\"{re.escape(field)}\"|'{re.escape(field)}')"
    sequence = re.search(
        rf"^    {key}[ \t]*:[ \t]*\[(?P<values>[^\]]*)\][ \t]*$",
        block,
        re.MULTILINE,
    )
    if sequence is None:
        if re.search(rf"^    {key}[ \t]*:", block, re.MULTILINE):
            raise ValueError(f"{field} must use the reviewed flow sequence")
        return None

    values = tuple(
        value.strip().strip("\"'")
        for value in sequence.group("values").split(",")
        if value.strip()
    )
    if not values or any(not value for value in values):
        raise ValueError(f"{field} is empty")
    return values


def _pull_request_actions(header: str) -> tuple[str, ...]:
    block = _trigger_block(header, "pull_request")
    for field in ("branches", "branches-ignore"):
        key = rf"(?:{re.escape(field)}|\"{re.escape(field)}\"|'{re.escape(field)}')"
        if re.search(rf"^    {key}[ \t]*:", block, re.MULTILINE):
            raise ValueError(
                "pull_request must not define branches or branches-ignore filters"
            )

    actions = _flow_sequence(block, "types")
    if (
        actions is None
        or len(actions) != len(PULL_REQUEST_ACTIONS)
        or set(actions) != set(PULL_REQUEST_ACTIONS)
    ):
        raise ValueError(
            "pull_request types must be opened, synchronize, reopened, and edited"
        )
    return actions


def _push_branches(header: str) -> tuple[str, ...] | None:
    return _flow_sequence(_trigger_block(header, "push"), "branches")


def _event_branch(event: dict) -> str:
    payload = event.get("payload", event)
    if event["event_name"] == "pull_request":
        return payload["pull_request"]["base"]["ref"]
    prefix = "refs/heads/"
    ref = payload["ref"]
    return ref[len(prefix):] if ref.startswith(prefix) else ref


def _is_lower_sha(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None


def _triggered_jobs(text: str, event: dict) -> set[str]:
    header = text[: text.index("\njobs:\n")]
    payload = event.get("payload", event)
    try:
        if event["event_name"] == "pull_request":
            actions = _pull_request_actions(header)
            if payload["action"] not in actions:
                return set()
            branches = None
        elif event["event_name"] == "push":
            branches = _push_branches(header)
        else:
            return set()
    except ValueError:
        return set()
    if branches is not None and not any(
        fnmatch.fnmatchcase(_event_branch(event), pattern) for pattern in branches
    ):
        return set()

    jobs = set(_job_blocks(text))
    runner = event.get("runner", {})
    raw_github_sha = runner.get(
        "github_sha",
        event.get("github_sha", event.get("sha", "")),
    )
    push_fallback = (
        event["event_name"] == "push"
        and payload["ref"] == "refs/heads/master"
        and _is_lower_sha(payload.get("after"))
        and _is_lower_sha(raw_github_sha)
        and raw_github_sha == payload["after"]
    )
    if not push_fallback:
        jobs.discard("patch-release")
    pull_request = payload.get("pull_request", {})
    base = pull_request.get("base", {}) if isinstance(pull_request, dict) else {}
    head = pull_request.get("head", {}) if isinstance(pull_request, dict) else {}
    pr_base_sha = runner.get(
        "pr_base_sha",
        base.get("sha", "") if isinstance(base, dict) else "",
    )
    pr_head_sha = runner.get(
        "pr_head_sha",
        head.get("sha", "") if isinstance(head, dict) else "",
    )
    push_sha = runner.get(
        "push_sha",
        event.get("sha", payload.get("after", "")),
    )
    github_ref = runner.get(
        "github_ref",
        event.get(
            "github_ref",
            "refs/pull/177/merge"
            if event["event_name"] == "pull_request"
            else payload.get("ref", ""),
        ),
    )
    pr_number = runner.get(
        "pr_number",
        event.get("number", payload.get("number", 177)),
    )
    pr_event_identity = (
        event["event_name"] == "pull_request"
        and isinstance(pr_number, int)
        and not isinstance(pr_number, bool)
        and pr_number > 0
        and isinstance(github_ref, str)
        and github_ref == f"refs/pull/{pr_number}/merge"
        and _is_lower_sha(pr_head_sha)
    )
    classifier_result = event.get("classifier_result", "success")
    if classifier_result == "failure":
        push_fallback = push_fallback and push_sha == raw_github_sha
        if not (pr_event_identity or push_fallback):
            jobs.difference_update(COMBINED_WORKERS)
        return jobs
    if classifier_result != "success":
        jobs.difference_update(COMBINED_WORKERS)
        return jobs
    if not (pr_event_identity or push_fallback):
        jobs.difference_update(COMBINED_WORKERS)
        return jobs
    decision = event_classifier.classify_event(
        event["event_name"],
        payload,
        github_ref=runner.get(
            "github_ref",
            event.get("github_ref", event.get("ref", "")),
        ),
        github_sha=runner.get(
            "github_sha",
            event.get("github_sha", event.get("sha", "")),
        ),
        pr_base_sha=pr_base_sha,
        pr_head_sha=pr_head_sha,
        push_sha=push_sha,
    )
    missing_base_fallback = (
        event["event_name"] == "pull_request"
        and decision.classification == "full"
        and decision.head_valid
        and not decision.identity_valid
        and decision.full_fallback
    )
    if decision.classification == "metadata-only":
        jobs.difference_update(METADATA_SKIPPED_JOBS)
        return jobs
    if not decision.run_expensive or (
        not decision.identity_valid and not missing_base_fallback
    ):
        jobs.difference_update(COMBINED_WORKERS)
    return jobs


def _pre_fix_triggered_jobs(text: str, event: dict) -> set[str]:
    header = text[: text.index("\njobs:\n")]
    payload = event.get("payload", event)
    if event["event_name"] != "pull_request":
        return set()
    if payload["action"] not in _pull_request_actions(header):
        return set()
    return set(_job_blocks(text))


def _resolved_check_name(
    text: str,
    job_name: str,
    event: dict,
    decision: event_classifier.EventDecision | None,
    *,
    classifier_result: str,
) -> str:
    job = _job_blocks(text)[job_name]
    direct_name = _direct_job_name(job)
    if direct_name is None:
        return job_name
    if job_name in COMBINED_WORKERS:
        return direct_name
    if job_name == "event-classifier" and direct_name == EVENT_CLASSIFIER_DYNAMIC_NAME:
        return (
            candidate_evidence.METADATA_CLASSIFIER
            if decision is not None and decision.classification == "metadata-only"
            else job_name
        )
    return direct_name


def _emitted_check_names(text: str, event: dict) -> set[str]:
    scheduled = _triggered_jobs(text, event)
    if not scheduled:
        return set()

    payload = event.get("payload", event)
    runner = event.get("runner", {})
    classifier_result = event.get("classifier_result", "success")
    decision = None
    if event["event_name"] in {"pull_request", "push"}:
        pull_request = payload.get("pull_request", {})
        base = pull_request.get("base", {}) if isinstance(pull_request, dict) else {}
        head = pull_request.get("head", {}) if isinstance(pull_request, dict) else {}
        try:
            decision = event_classifier.classify_event(
                event["event_name"],
                payload,
                github_ref=runner.get(
                    "github_ref",
                    event.get("github_ref", event.get("ref", "")),
                ),
                github_sha=runner.get(
                    "github_sha",
                    event.get("github_sha", event.get("sha", "")),
                ),
                pr_base_sha=runner.get(
                    "pr_base_sha",
                    base.get("sha", "") if isinstance(base, dict) else "",
                ),
                pr_head_sha=runner.get(
                    "pr_head_sha",
                    head.get("sha", "") if isinstance(head, dict) else "",
                ),
                push_sha=runner.get(
                    "push_sha",
                    event.get("sha", payload.get("after", "")),
                ),
            )
        except event_classifier.EventClassificationError:
            decision = None

    return {
        _resolved_check_name(
            text,
            job_name,
            event,
            decision,
            classifier_result=classifier_result,
        )
        for job_name in _job_blocks(text)
    }


def _job_blocks(text: str) -> dict[str, str]:
    jobs_start = text.index("\njobs:\n") + len("\njobs:\n")
    jobs_text = text[jobs_start:]
    matches = list(
        re.finditer(r"^  (?P<name>[A-Za-z][A-Za-z0-9_-]*):\n", jobs_text, re.MULTILINE)
    )
    return {
        match.group("name"): jobs_text[
            match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(jobs_text)
        ]
        for index, match in enumerate(matches)
    }


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _github_expression_balance_errors(expression: str) -> list[str]:
    if not expression.startswith("${{ ") or not expression.endswith(" }}"):
        return ["expression must use the complete GitHub expression wrapper"]
    body = expression[4:-3]
    depth = 0
    in_string = False
    index = 0
    while index < len(body):
        character = body[index]
        if character == "'":
            if in_string and index + 1 < len(body) and body[index + 1] == "'":
                index += 2
                continue
            in_string = not in_string
        elif not in_string and character == "(":
            depth += 1
        elif not in_string and character == ")":
            if depth == 0:
                return ["expression has an unmatched closing parenthesis"]
            depth -= 1
        index += 1
    errors = []
    if in_string:
        errors.append("expression has an unterminated string")
    if depth:
        errors.append(f"expression has {depth} unmatched opening parenthesis")
    return errors


def _direct_job_if(job: str) -> str:
    matches = re.findall(r"^    if: (?P<expression>.+)$", job, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("job must contain exactly one direct if expression")
    return matches[0]


def _direct_job_name(job: str) -> str | None:
    matches = re.findall(r"^    name: (?P<expression>.+)$", job, re.MULTILINE)
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("job must contain exactly one direct name expression")
    return matches[0]


def _run_block_commands(job: str) -> list[str]:
    lines = job.splitlines()
    commands = []
    index = 0
    while index < len(lines):
        inline = re.match(r"^    - run[ \t]*:[ \t]+(?P<value>.+)$", lines[index])
        field = re.match(r"^      run[ \t]*:[ \t]+(?P<value>.+)$", lines[index])
        match = inline or field
        if match is None:
            index += 1
            continue
        if re.fullmatch(
            r"[|>](?:(?:[1-9][+-]?)|(?:[+-][1-9]?))?",
            match.group("value"),
        ):
            index += 1
            block = []
            while index < len(lines) and lines[index].startswith("        "):
                line = lines[index].strip()
                if line and not line.startswith("#"):
                    block.append(line)
                index += 1
            commands.extend(block)
            continue
        value = match.group("value").strip()
        if value and not value.startswith("#"):
            commands.append(value)
        index += 1
    return commands


def _literal_run_script(step: str) -> str:
    lines = step.split("\n")
    try:
        run_index = lines.index("      run: |")
    except ValueError as error:
        raise AssertionError("step lacks a literal run block") from error
    script = []
    for line in lines[run_index + 1 :]:
        if line and not line.startswith("        "):
            break
        script.append(line[8:] if line else "")
    return "\n".join(script) + "\n"


def _metadata_adapter_scripts(text: str) -> dict[str, str]:
    return {
        job_name: _literal_run_script(_step_blocks(_job_blocks(text)[job_name])[0])
        for job_name in METADATA_ADAPTER_JOBS
    }


def _metadata_adapter_python_source(script: str) -> str:
    return metadata_adapter_contract.metadata_adapter_python_source(script)


def _indent_metadata_adapter_heredoc(script: str) -> str:
    source = metadata_adapter_contract.metadata_adapter_python_source(script)
    indented = "".join(
        f" {line}\n" if line else "\n"
        for line in source.splitlines()
    )
    return script.replace(source, indented, 1)


def _indent_metadata_adapter_heredoc_in_step(step: str) -> str:
    lines = step.splitlines(keepends=True)
    start = next(
        index
        for index, line in enumerate(lines)
        if line.strip() == "/usr/bin/python3 -I - <<'PY'"
    )
    end = next(
        index for index in range(start + 1, len(lines)) if lines[index].strip() == "PY"
    )
    for index in range(start + 1, end):
        if lines[index].strip():
            lines[index] = " " + lines[index]
    return "".join(lines)


def _metadata_adapter_payload(
    *,
    action: str = "edited",
    number: int = 177,
    base_ref: str = "master",
    base_sha: str = "2" * 40,
    head_sha: str = "1" * 40,
    body: str | None = "New body",
    title: str = "New title",
    changes: dict | None = None,
) -> dict:
    if changes is None:
        changes = {"body": {"from": "Old body"}}
    return {
        "action": action,
        "changes": changes,
        "number": number,
        "pull_request": {
            "base": {"ref": base_ref, "sha": base_sha},
            "body": body,
            "head": {"sha": head_sha},
            "title": title,
        },
    }


def _metadata_adapter_payload_bytes(payload: dict) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        + b"\n"
    )


def _metadata_adapter_python_env(event_path: Path | str) -> dict[str, str]:
    return {
        "CLASSIFIED_BASE_SHA": "2" * 40,
        "CLASSIFIED_BUILD_SHA": "1" * 40,
        "EXPECTED_BUILD_SHA": "1" * 40,
        "FALLBACK_SHA": "1" * 40,
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_REF": "refs/pull/177/merge",
    }


def _metadata_adapter_env_for_case(case: dict, event_path: Path | str) -> dict[str, str]:
    runner = case["runner"]
    pr_head_sha = runner.get("pr_head_sha", "")
    pr_base_sha = runner.get("pr_base_sha", "")
    return {
        "CLASSIFICATION": "metadata-only",
        "CLASSIFIED_BASE_SHA": pr_base_sha,
        "CLASSIFIED_BUILD_SHA": pr_head_sha,
        "CLASSIFIER_RESULT": "success",
        "EXPECTED_BUILD_SHA": pr_head_sha,
        "FALLBACK_IDENTITY_RESULT": "success",
        "FALLBACK_KIND": "pull_request",
        "FALLBACK_SHA": pr_head_sha,
        "FULL_FALLBACK": "false",
        "GITHUB_EVENT_NAME": case["event_name"],
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_REF": runner["github_ref"],
        "HEAD_VALID": "true" if event_classifier._is_sha(pr_head_sha) else "false",
        "IDENTITY_VALID": (
            "true"
            if event_classifier._is_sha(pr_head_sha)
            and event_classifier._is_sha(pr_base_sha)
            else "false"
        ),
        "RUN_EXPENSIVE": "false",
    }


def _run_metadata_adapter_python_source(
    source: str,
    env: dict[str, str],
) -> tuple[int, str]:
    stderr = io.StringIO()
    code = 0
    with (
        mock.patch.dict(os.environ, env, clear=True),
        mock.patch("sys.stderr", stderr),
    ):
        try:
            exec(compile(source, "<metadata-adapter>", "exec"), {"__name__": "__main__"})
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, stderr.getvalue()


SUMMARY_TEST_REPOSITORY = "owner/repo"
SUMMARY_TEST_WORKFLOW_ID = 123456789
SUMMARY_TEST_HEAD_SHA = "1" * 40
SUMMARY_TEST_BASE_SHA = "2" * 40
SUMMARY_TEST_PR_NUMBER = 177
SUMMARY_TEST_RUN_ID = 9001
SUMMARY_TEST_RUN_NUMBER = 9001
SUMMARY_TEST_RUN_ATTEMPT = 1
SUMMARY_TEST_CREATED_AT = "2026-09-01T00:00:00Z"
SUMMARY_TEST_RUN_STARTED_AT = "2026-09-01T00:00:01Z"
SUMMARY_TEST_JOB_STARTED_AT = "2026-09-01T00:00:02Z"


def _summary_timestamp(second: int) -> str:
    return f"2026-09-01T00:00:{second:02d}Z"


def _replace_summary_job(
    jobs: list[dict],
    job_name: str,
    **changes,
) -> list[dict]:
    mutated = copy.deepcopy(jobs)
    target = next(job for job in mutated if job["name"] == job_name)
    target.update(changes)
    return mutated


def _summary_runs_path(*, repo: str = SUMMARY_TEST_REPOSITORY, head_sha: str = SUMMARY_TEST_HEAD_SHA, page: int = 1) -> str:
    owner, name = repo.split("/", 1)
    return (
        f"/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(name, safe='')}/actions/workflows/build.yml/runs?"
        + urllib.parse.urlencode(
            [
                ("event", "pull_request"),
                ("head_sha", head_sha),
                ("per_page", "100"),
                ("page", str(page)),
            ]
        )
    )


def _summary_total_pages(total_count: int) -> int:
    if total_count == 0:
        return 1
    page_count, remainder = divmod(total_count, 100)
    return page_count + (1 if remainder else 0)


def _summary_runs_link_header(
    *,
    current_page: int,
    total_count: int,
    repo: str = SUMMARY_TEST_REPOSITORY,
    head_sha: str = SUMMARY_TEST_HEAD_SHA,
    next_page: int | None = None,
    last_page: int | None = None,
    include_next: bool = True,
    include_last: bool = True,
    include_prev: bool = False,
    include_first: bool = False,
) -> str:
    total_pages = _summary_total_pages(total_count)
    if next_page is None:
        next_page = current_page + 1
    if last_page is None:
        last_page = total_pages
    links = []
    if include_prev:
        links.append(
            f'<{{api_base}}'
            f'{_summary_runs_path(repo=repo, head_sha=head_sha, page=current_page - 1)}>; '
            'rel="prev"'
        )
    if include_next:
        links.append(
            f'<{{api_base}}'
            f'{_summary_runs_path(repo=repo, head_sha=head_sha, page=next_page)}>; '
            'rel="next"'
        )
    if include_last:
        links.append(
            f'<{{api_base}}'
            f'{_summary_runs_path(repo=repo, head_sha=head_sha, page=last_page)}>; '
            'rel="last"'
        )
    if include_first:
        links.append(
            f'<{{api_base}}'
            f'{_summary_runs_path(repo=repo, head_sha=head_sha, page=1)}>; '
            'rel="first"'
        )
    return ", ".join(links)


def _summary_jobs_path(run_id: int, *, repo: str = SUMMARY_TEST_REPOSITORY, attempt: int = 1) -> str:
    owner, name = repo.split("/", 1)
    return (
        f"/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(name, safe='')}/actions/runs/{run_id}/attempts/{attempt}/jobs?"
        + urllib.parse.urlencode([("per_page", "100"), ("page", "1")])
    )


def _summary_workflow_run(
    run_id: int,
    *,
    repo: str = SUMMARY_TEST_REPOSITORY,
    workflow_id: int = SUMMARY_TEST_WORKFLOW_ID,
    pr_number: int = SUMMARY_TEST_PR_NUMBER,
    head_sha: str = SUMMARY_TEST_HEAD_SHA,
    base_sha: str = SUMMARY_TEST_BASE_SHA,
    event: str = "pull_request",
    status: str = "completed",
    conclusion: str | None = "success",
    run_number: int | None = None,
    run_attempt: int = 1,
    path: str | None = None,
    pull_requests: list[dict] | None = None,
    url: str | None = None,
    created_at: str = SUMMARY_TEST_CREATED_AT,
    run_started_at: str | None = SUMMARY_TEST_RUN_STARTED_AT,
) -> dict:
    owner, name = repo.split("/", 1)
    if path is None:
        path = f".github/workflows/build.yml@refs/pull/{pr_number}/merge"
    if pull_requests is None:
        pull_requests = [
            {
                "number": pr_number,
                "head": {"sha": head_sha},
                "base": {"sha": base_sha},
            }
        ]
    if url is None:
        url = (
            f"https://api.github.test/repos/"
            f"{urllib.parse.quote(owner, safe='')}/"
            f"{urllib.parse.quote(name, safe='')}/actions/runs/{run_id}"
        )
    if run_number is None:
        run_number = SUMMARY_TEST_RUN_NUMBER if run_id == SUMMARY_TEST_RUN_ID else run_id
    if run_id == SUMMARY_TEST_RUN_ID and status == "completed" and conclusion == "success":
        status = "in_progress"
        conclusion = None
    return {
        "id": run_id,
        "workflow_id": workflow_id,
        "run_number": run_number,
        "run_attempt": run_attempt,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "created_at": created_at,
        "head_sha": head_sha,
        "path": path,
        "pull_requests": pull_requests,
        "run_started_at": run_started_at,
        "url": url,
    }


def _summary_job(
    name: str,
    conclusion: str | None,
    *,
    status: str = "completed",
    runner_name: str | None = "GitHub Actions 1",
    started_at: str | None = SUMMARY_TEST_JOB_STARTED_AT,
) -> dict:
    return {
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "runner_name": runner_name,
        "started_at": started_at,
    }


def _summary_full_jobs() -> list[dict]:
    return [
        _summary_job("event-identity", "success"),
        _summary_job("event-router", "success"),
        _summary_job("event-classifier", "success"),
        _summary_job("host-tests", "success"),
        _summary_job("build", "success"),
        _summary_job("extended-host-tests", "success"),
        _summary_job("legacy", "success"),
        _summary_job("patch-release", "skipped", runner_name=None, started_at=None),
        _summary_job("summary", "success"),
    ]


def _summary_metadata_jobs() -> list[dict]:
    return [
        _summary_job("event-identity", "success"),
        _summary_job("event-router", "success"),
        _summary_job(candidate_evidence.METADATA_CLASSIFIER, "success"),
        _summary_job("host-tests", "success"),
        _summary_job("build", "success"),
        _summary_job("extended-host-tests", "skipped", runner_name=None, started_at=None),
        _summary_job("legacy", "skipped", runner_name=None, started_at=None),
        _summary_job("patch-release", "skipped", runner_name=None, started_at=None),
        _summary_job("summary", "success"),
    ]


def _summary_api_payload(
    key: str,
    items: list[dict],
    *,
    total_count: int | None = None,
) -> dict:
    return {
        "total_count": len(items) if total_count is None else total_count,
        key: items,
    }


def _summary_response(body, *, status: int = 200, headers: dict[str, str] | None = None):
    if isinstance(body, bytes):
        payload = body
    else:
        payload = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return status, headers or {}, payload


def _summary_metadata_env(**overrides: str) -> dict[str, str]:
    env = {
        "BUILD_RESULT": "success",
        "CLASSIFICATION": "metadata-only",
        "CLASSIFIED_BASE_SHA": SUMMARY_TEST_BASE_SHA,
        "CLASSIFIED_BUILD_SHA": SUMMARY_TEST_HEAD_SHA,
        "CLASSIFIER_RESULT": "success",
        "EXTENDED_HOST_TESTS_RESULT": "skipped",
        "FALLBACK_IDENTITY_RESULT": "success",
        "FALLBACK_KIND": "pull_request",
        "FALLBACK_SHA": SUMMARY_TEST_HEAD_SHA,
        "FULL_FALLBACK": "false",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REF": f"refs/pull/{SUMMARY_TEST_PR_NUMBER}/merge",
        "GITHUB_REPOSITORY": SUMMARY_TEST_REPOSITORY,
        "GITHUB_TOKEN": "token",
        "HEAD_VALID": "true",
        "HOST_TESTS_RESULT": "success",
        "IDENTITY_VALID": "true",
        "LEGACY_RESULT": "skipped",
        "PATCH_RELEASE_RESULT": "skipped",
        "PR_BASE_SHA": SUMMARY_TEST_BASE_SHA,
        "PR_HEAD_SHA": SUMMARY_TEST_HEAD_SHA,
        "PR_NUMBER": str(SUMMARY_TEST_PR_NUMBER),
        "PUSH_SHA": "",
        "RAW_PUSH_SHA": "a" * 40,
        "RUN_ATTEMPT": str(SUMMARY_TEST_RUN_ATTEMPT),
        "RUN_EXPENSIVE": "false",
        "RUN_ID": str(SUMMARY_TEST_RUN_ID),
        "RUN_NUMBER": str(SUMMARY_TEST_RUN_NUMBER),
    }
    env.update(overrides)
    return env


def _run_summary_with_api(
    script: str,
    *,
    environment: dict[str, str],
    routes: dict[str, tuple[int, dict[str, str], bytes] | list[tuple[int, dict[str, str], bytes]]],
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]]]:
    completed, requests = _run_summary_with_api_servers(
        script,
        environment=environment,
        primary_routes=routes,
    )
    return completed, requests["primary"]


def _run_summary_with_api_servers(
    script: str,
    *,
    environment: dict[str, str],
    primary_routes: dict[str, tuple[int, dict[str, str], bytes] | list[tuple[int, dict[str, str], bytes]]],
    secondary_routes: dict[str, tuple[int, dict[str, str], bytes] | list[tuple[int, dict[str, str], bytes]]] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, list[dict[str, object]]]]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.requests.append(
                {"path": self.path, "headers": dict(self.headers)}
            )
            responses = self.server.routes.get(self.path)
            if not responses:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            response = responses.pop(0)
            status, headers, body = response
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value.format(**self.server.format_context))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    primary_server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    secondary_server = (
        http.server.HTTPServer(("127.0.0.1", 0), Handler)
        if secondary_routes is not None
        else None
    )
    primary_server.routes = {
        path: list(value) if isinstance(value, list) else [value]
        for path, value in primary_routes.items()
    }
    primary_server.requests = []
    primary_server.api_base = f"http://127.0.0.1:{primary_server.server_port}"
    redirect_api_base = (
        f"http://127.0.0.1:{secondary_server.server_port}"
        if secondary_server is not None
        else primary_server.api_base
    )
    format_context = {
        "api_base": primary_server.api_base,
        "redirect_api_base": redirect_api_base,
    }
    primary_server.format_context = format_context
    threads = [
        threading.Thread(target=primary_server.serve_forever, daemon=True)
    ]
    if secondary_server is not None:
        secondary_server.routes = {
            path: list(value) if isinstance(value, list) else [value]
            for path, value in secondary_routes.items()
        }
        secondary_server.requests = []
        secondary_server.api_base = redirect_api_base
        secondary_server.format_context = format_context
        threads.append(
            threading.Thread(target=secondary_server.serve_forever, daemon=True)
        )
    for thread in threads:
        thread.start()
    try:
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="workflow-summary-runtime-",
            dir=artifact_root,
        ) as temporary:
            completed = subprocess.run(
                ["/bin/bash", "-c", script],
                cwd=ROOT,
                env={
                    **os.environ,
                    **environment,
                    "GITHUB_API_URL": primary_server.api_base,
                    "GITHUB_STEP_SUMMARY": str(Path(temporary) / "summary.md"),
                },
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        requests = {"primary": list(primary_server.requests)}
        requests["secondary"] = (
            list(secondary_server.requests) if secondary_server is not None else []
        )
        return completed, requests
    finally:
        primary_server.shutdown()
        if secondary_server is not None:
            secondary_server.shutdown()
        for thread in threads:
            thread.join()
        primary_server.server_close()
        if secondary_server is not None:
            secondary_server.server_close()


def _contains_command(job: str, command: str) -> bool:
    return any(
        _normalise(command) in _normalise(run)
        for run in _run_block_commands(job)
    )


def _step_blocks(job: str) -> list[str]:
    matches = list(re.finditer(r"^    -(?:[ \t]|\Z)", job, re.MULTILINE))
    return [
        job[
            match.start():
            matches[index + 1].start() if index + 1 < len(matches) else len(job)
        ]
        for index, match in enumerate(matches)
    ]


def _direct_step_mapping_fields(step: str) -> list[str] | None:
    sequence_key = re.compile(
        r"^    -[ \t]+(?P<field>[A-Za-z_][A-Za-z0-9_-]*)[ \t]*:"
    )
    continuation_key = re.compile(
        r"^      (?P<field>[A-Za-z_][A-Za-z0-9_-]*)[ \t]*:"
    )
    fields = []
    sequence_entries = 0
    for line in step.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 4:
            match = sequence_key.match(line)
            if match is None:
                return None
            sequence_entries += 1
            fields.append(match.group("field"))
        elif indent == 6:
            match = continuation_key.match(line)
            if match is None:
                return None
            fields.append(match.group("field"))
        elif indent < 8:
            return None
    if sequence_entries != 1:
        return None
    return fields


def _contains_exact_command(
    job: str,
    command: str,
    *,
    if_expression: str | None = None,
    env_lines: tuple[str, ...] | None = None,
) -> bool:
    expected = _normalise(command)
    for step in _step_blocks(job):
        commands = _run_block_commands(step)
        if len(commands) != 1 or _normalise(commands[0]) != expected:
            continue
        fields = _direct_step_mapping_fields(step)
        expected_fields = {"name", "run"}
        if if_expression is not None:
            expected_fields.add("if")
        expected_env = env_lines
        if expected_env is not None:
            expected_fields.add("env")
        elif _step_env_entries(step) == SCRUBBED_STEP_ENV:
            expected_fields.add("env")
            expected_env = SCRUBBED_STEP_ENV
        if fields is not None and len(fields) == len(expected_fields) and set(fields) == expected_fields:
            if if_expression is not None and f"      if: {if_expression}" not in step:
                continue
            if expected_env is not None and _step_env_entries(step) != expected_env:
                continue
            return True
    return False


def _step_env_entries(step: str) -> tuple[str, ...] | None:
    lines = step.splitlines()
    try:
        env_index = lines.index("      env:")
    except ValueError:
        return None
    entries = []
    for line in lines[env_index + 1 :]:
        if line.strip() and len(line) - len(line.lstrip(" ")) <= 6:
            break
        if line.strip() and not line.lstrip().startswith("#"):
            entries.append(line)
    return tuple(entries)


def _step_has_scrubbed_environment(step: str) -> bool:
    return _step_env_entries(step) == SCRUBBED_STEP_ENV


def _step_name(step: str) -> str | None:
    match = re.search(r"^    - name: (?P<name>.+)$", step, re.MULTILINE)
    return match.group("name") if match is not None else None


def _checkout_step_is_exact(step: str, *, if_expression: str | None = None) -> bool:
    action = (
        "    - uses: actions/checkout@"
        "3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    action_lines = [
        line.split(" #", 1)[0]
        for line in step.splitlines()
        if line.startswith("    - uses:")
    ]
    if action_lines != [action]:
        return False
    expected_fields = ["uses", "if", "with"] if if_expression is not None else ["uses", "with"]
    if _direct_step_mapping_fields(step) != expected_fields:
        return False
    if if_expression is not None and f"      if: {if_expression}" not in step:
        return False
    expected = (
        f"        ref: {EXPECTED_BUILD_SHA_EXPRESSION}",
        "        fetch-depth: 0",
        "        submodules: recursive",
        "        persist-credentials: false",
    )
    lines = step.splitlines()
    try:
        with_index = lines.index("      with:")
    except ValueError:
        return False
    entries = []
    for line in lines[with_index + 1 :]:
        if line.strip() and len(line) - len(line.lstrip(" ")) <= 6:
            break
        if line.strip() and not line.lstrip().startswith("#"):
            entries.append(line)
    return tuple(entries) == expected


def _run_step_is_exact(
    step: str,
    name: str,
    commands: tuple[str, ...],
    scrubbed: bool = False,
    if_expression: str | None = None,
    env_lines: tuple[str, ...] | None = None,
) -> bool:
    if _step_name(step) != name:
        return False
    if tuple(_run_block_commands(step)) != commands:
        return False
    if scrubbed and env_lines is None:
        env_lines = SCRUBBED_STEP_ENV
    fields = _direct_step_mapping_fields(step)
    expected_fields = {"name", "run"}
    if env_lines is not None:
        expected_fields.add("env")
    if if_expression is not None:
        expected_fields.add("if")
    if fields is None or len(fields) != len(expected_fields):
        return False
    if set(fields) != expected_fields:
        return False
    if if_expression is not None and f"      if: {if_expression}" not in step:
        return False
    if env_lines is None:
        return True
    return _step_env_entries(step) == env_lines


def _metadata_adapter_step_is_reviewed(step: str) -> bool:
    if _step_name(step) != "Attest metadata-only branch-protection continuity":
        return False
    if _direct_step_mapping_fields(step) != ["name", "if", "env", "run"]:
        return False
    if f"      if: {METADATA_ADAPTER_STEP_CONDITION}" not in step:
        return False
    if _step_env_entries(step) != METADATA_ADAPTER_ENV:
        return False
    try:
        metadata_adapter_contract.validate_metadata_adapter_script(
            _literal_run_script(step)
        )
    except ValueError:
        return False
    return True


def _summary_step_is_reviewed(step: str) -> bool:
    if _step_name(step) != "Render fail-closed combined Build summary":
        return False
    if _direct_step_mapping_fields(step) != ["name", "run"]:
        return False
    try:
        summary_continuity_contract.validate_summary_continuity_script(
            _literal_run_script(step)
        )
    except ValueError:
        return False
    return True


def _protected_host_prefix_errors(host: str) -> list[str]:
    steps = _step_blocks(host)
    if len(steps) < 10:
        return ["host-tests lacks the complete protected pre-pilot sequence"]
    expected = (
        _metadata_adapter_step_is_reviewed(steps[0]),
        _checkout_step_is_exact(
            steps[1],
            if_expression=FULL_WORKER_STEP_CONDITION,
        ),
        _run_step_is_exact(
            steps[2],
            "Verify checked-out revision",
            (
                'ACTUAL_SHA="$(git rev-parse HEAD)"',
                "printf 'checkout.sha=%s\\n' \"$ACTUAL_SHA\"",
                'test "$ACTUAL_SHA" = "$EXPECTED_BUILD_SHA"',
                "/usr/bin/python3 -I "
                "scripts/workflow_pilot/publisher_command_signatures.py --check",
            ),
            if_expression=FULL_WORKER_STEP_CONDITION,
        ),
        _run_step_is_exact(
            steps[3],
            "Hydrate workflow-pilot Git authority",
            (WORKFLOW_PILOT_AUTHORITY_HYDRATION,),
            if_expression=FULL_WORKER_STEP_CONDITION,
            env_lines=SCRUBBED_STEP_ENV,
        ),
        _run_step_is_exact(
            steps[4],
            "Install host-only dependencies (no arm-none-eabi toolchain)",
            (
                "sudo apt-get update && sudo apt-get install -y "
                "build-essential libmgba-dev",
            ),
            if_expression=FULL_WORKER_STEP_CONDITION,
        ),
        _run_step_is_exact(
            steps[5],
            "Run gba-playtest host test suite",
            (
                "GBA_PLAYTEST_HOST_ONLY=1 python3 -m unittest discover "
                "-s tools/gba-playtest/tests -v",
            ),
            if_expression=FULL_WORKER_STEP_CONDITION,
        ),
        _run_step_is_exact(
            steps[6],
            "Run upstream-port tooling test suite",
            ("python3 -m unittest discover -s tests/upstream_port -v",),
            if_expression=FULL_WORKER_STEP_CONDITION,
        ),
        _run_step_is_exact(
            steps[7],
            "Run workflow contract test suite",
            (
                "python3 -m unittest discover -s tests/workflows "
                '-p "test_*.py" -v',
            ),
            if_expression=FULL_WORKER_STEP_CONDITION,
        ),
        _run_step_is_exact(
            steps[8],
            "Run workflow-pilot reporter regression suite (issue #176)",
            (WORKFLOW_PILOT_GATE,),
            if_expression=FULL_WORKER_STEP_CONDITION,
            env_lines=SCRUBBED_STEP_ENV,
        ),
        _run_step_is_exact(
            steps[9],
            "Validate workflow-pilot baseline against checked-out Git history",
            (WORKFLOW_PILOT_BASELINE_GATE,),
            if_expression=FULL_WORKER_STEP_CONDITION,
            env_lines=SCRUBBED_STEP_ENV,
        ),
    )
    if all(expected):
        return []
    return [
        "host-tests protected pre-pilot step sequence differs from reviewed "
        "actions, commands, fields, order, or scrubbed environments"
    ]


def _has_execution_defaults(text: str, workflow_scope: bool) -> bool:
    indent = "" if workflow_scope else r" {4,}"
    key = r"(?:defaults|\"defaults\"|'defaults')"
    return re.search(
        rf"^{indent}{key}[ \t]*:",
        text,
        re.MULTILINE,
    ) is not None


def _has_unsupported_direct_key(text: str, indent: int, allow_sequence: bool) -> bool:
    simple_key = re.compile(
        rf"^{' ' * indent}[A-Za-z_][A-Za-z0-9_-]*[ \t]*:"
    )
    sequence = re.compile(
        rf"^{' ' * indent}-[ \t]+[A-Za-z_][A-Za-z0-9_-]*[ \t]*:"
    )
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        line_indent = len(line) - len(line.lstrip(" "))
        if line_indent != indent:
            continue
        if simple_key.match(line):
            continue
        if allow_sequence and sequence.match(line):
            continue
        return True
    return False


def _has_direct_key(text: str, indent: int, key: str) -> bool:
    return re.search(
        rf"^{' ' * indent}{re.escape(key)}[ \t]*:",
        text,
        re.MULTILINE,
    ) is not None


def _host_environment_errors(job: str) -> list[str]:
    lines = job.splitlines()
    env_indices = [
        index for index, line in enumerate(lines) if line == "    env:"
    ]
    if len(env_indices) != 1:
        return ["host-tests must define exactly one reviewed env mapping"]
    index = env_indices[0] + 1
    entries = []
    while index < len(lines):
        line = lines[index]
        if line.strip() and len(line) - len(line.lstrip(" ")) <= 4:
            break
        if line.strip() and not line.lstrip().startswith("#"):
            entries.append(line)
        index += 1
    if entries != [HOST_ENV_LINE]:
        return [
            "host-tests env must contain only the reviewed EXPECTED_BUILD_SHA"
        ]
    return []


def _combined_job_contract_errors(job_name: str, job: str) -> list[str]:
    direct_lines = []
    for line in job.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent != 4 or line.startswith("    -"):
            continue
        direct_lines.append(line)
    expected_timeout = 90 if job_name == "build" else 60
    expected_condition = HOST_BUILD_CONDITION if job_name in METADATA_ADAPTER_JOBS else WORKER_CONDITION
    expected_direct = [
        f"    {WORKER_NEEDS}",
        f"    if: {expected_condition}",
        "    runs-on: ubuntu-latest",
        f"    timeout-minutes: {expected_timeout}",
        "    env:",
        "    steps:",
    ]
    errors = []
    if direct_lines != expected_direct:
        errors.append(
            f"{job_name} direct job mapping differs from the reviewed "
            "runs-on, timeout, env, and steps contract"
        )

    lines = job.splitlines()
    try:
        env_index = lines.index("    env:")
    except ValueError:
        return errors + [f"{job_name} lacks its reviewed env mapping"]
    entries = []
    for line in lines[env_index + 1 :]:
        if line.strip() and len(line) - len(line.lstrip(" ")) <= 4:
            break
        if line.strip() and not line.lstrip().startswith("#"):
            entries.append(line)
    if tuple(entries) != COMBINED_JOB_ENV[job_name]:
        errors.append(f"{job_name} env differs from its reviewed exact mapping")

    if job_name == "build":
        steps = _step_blocks(job)
        if len(steps) < 2:
            errors.append("build lacks the trusted metadata continuity adapter")
        else:
            if not _metadata_adapter_step_is_reviewed(steps[0]):
                errors.append("build metadata continuity adapter differs")
            if not _checkout_step_is_exact(
                steps[1],
                if_expression=FULL_WORKER_STEP_CONDITION,
            ):
                errors.append("build checkout must stay full-mode only")
    return errors


def _identity_contract_errors(job: str) -> list[str]:
    required = (
        "    name: event-identity",
        "    runs-on: ubuntu-latest",
        "    timeout-minutes: 5",
        "      classifier_available: ${{ "
        "steps.identity.outputs.classifier_available }}",
        "      classifier_expected_sha: ${{ "
        "steps.identity.outputs.classifier_expected_sha }}",
        "      classifier_ref: ${{ steps.identity.outputs.classifier_ref }}",
        "      fallback_kind: ${{ steps.identity.outputs.fallback_kind }}",
        "      fallback_sha: ${{ steps.identity.outputs.fallback_sha }}",
        "      BASH_ENV: ''",
        "      DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}",
        "      EVENT_NAME: ${{ github.event_name }}",
        "      EVENT_REF: ${{ github.ref }}",
        "      PR_BASE_SHA_JSON: ${{ "
        "toJSON(github.event.pull_request.base.sha) }}",
        "      PR_HEAD_SHA_JSON: ${{ "
        "toJSON(github.event.pull_request.head.sha) }}",
        "      PR_NUMBER: ${{ github.event.number }}",
        "      PR_NUMBER_JSON: ${{ toJSON(github.event.number) }}",
        "      PUSH_SHA_JSON: ${{ toJSON(github.event.after) }}",
        "      RAW_SHA_JSON: ${{ toJSON(github.sha) }}",
        "    - name: Validate trusted event identities",
        "      id: identity",
        '          [[ "$1" =~ ^[0-9a-f]{40}$ && "$2" = "\\"$1\\"" ]]',
        '          [[ "$1" =~ ^[1-9][0-9]*$ && "$2" = "$1" ]]',
        "        classifier_available=false",
        '        classifier_ref=""',
        '          if is_pr_number "$PR_NUMBER" "$PR_NUMBER_JSON" && \\',
        '             [[ "$EVENT_REF" = "refs/pull/$PR_NUMBER/merge" ]] && \\',
        '             is_lower_sha "$PR_HEAD_SHA" "$PR_HEAD_SHA_JSON"; then',
        '        elif [[ "$EVENT_NAME" = "push" && '
        '"$EVENT_REF" = "refs/heads/master" ]] && \\',
        '             is_lower_sha "$PUSH_SHA" "$PUSH_SHA_JSON" && \\',
        '             is_lower_sha "$RAW_SHA" "$RAW_SHA_JSON" && \\',
        '             [[ "$RAW_SHA" = "$PUSH_SHA" ]]; then',
        '          elif [[ -n "$DEFAULT_BRANCH" ]]; then',
        '            bootstrap_ref="refs/heads/$DEFAULT_BRANCH"',
        '            if /usr/bin/git check-ref-format "$bootstrap_ref" \\',
        '              classifier_ref="$bootstrap_ref"',
        '        if [[ -n "$classifier_ref" ]]; then',
        "          classifier_available=true",
        '          echo "classifier_available=$classifier_available"',
        '          echo "fallback_kind=$fallback_kind"',
        '          echo "fallback_sha=$fallback_sha"',
    )
    errors = [
        f"event-identity lacks required closed contract: {item}"
        for item in required
        if item not in job
    ]
    direct = [
        line
        for line in job.splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and len(line) - len(line.lstrip(" ")) == 4
        and not line.startswith("    -")
    ]
    if direct != [
        "    name: event-identity",
        "    runs-on: ubuntu-latest",
        "    timeout-minutes: 5",
        "    outputs:",
        "    env:",
        "    steps:",
    ]:
        errors.append("event-identity direct job mapping differs")
    steps = _step_blocks(job)
    if (
        len(steps) != 1
        or _direct_step_mapping_fields(steps[0]) != ["name", "id", "run"]
        or _step_name(steps[0]) != "Validate trusted event identities"
    ):
        errors.append("event-identity must contain only its trusted validation step")
    if "uses:" in job or "actions/checkout" in job:
        errors.append("event-identity must not read candidate-controlled repository content")
    if (
        'classifier_ref="refs/heads/$DEFAULT_BRANCH"' in job
        or '/usr/bin/git check-ref-format "$classifier_ref"' in job
    ):
        errors.append(
            "event-identity must defer optional default-branch validation"
        )
    return errors


def _classifier_contract_errors(job: str) -> list[str]:
    required = (
        "    name: event-router",
        "    if: ${{ always() && needs.event-identity.result == 'success' }}",
        "    needs: [event-identity]",
        "    runs-on: ubuntu-latest",
        "    timeout-minutes: 5",
        "    outputs:",
        "      classification: ${{ steps.classify.outputs.classification }}",
        "      expected_base: ${{ steps.classify.outputs.expected_base }}",
        "      expected_head: ${{ steps.classify.outputs.expected_head }}",
        "      full_fallback: ${{ steps.classify.outputs.full_fallback }}",
        "      head_valid: ${{ steps.classify.outputs.head_valid }}",
        "      identity_valid: ${{ steps.classify.outputs.identity_valid }}",
        "      reason: ${{ steps.classify.outputs.reason }}",
        "      run_expensive: ${{ steps.classify.outputs.run_expensive }}",
        "      CLASSIFIER_AVAILABLE: ${{ "
        "needs.event-identity.outputs.classifier_available }}",
        "      CLASSIFIER_EXPECTED_SHA: ${{ "
        "needs.event-identity.outputs.classifier_expected_sha }}",
        "      CLASSIFIER_REF: ${{ needs.event-identity.outputs.classifier_ref }}",
        "      PR_BASE_SHA: ${{ github.event.pull_request.base.sha }}",
        "      PR_BASE_REF: ${{ github.event.pull_request.base.ref }}",
        "      PR_BASE_REF_JSON: ${{ toJSON(github.event.pull_request.base.ref) }}",
        "      PR_BASE_SHA_JSON: ${{ toJSON(github.event.pull_request.base.sha) }}",
        "      PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}",
        "      PUSH_SHA: ${{ github.event.after }}",
        "      VALIDATED_FALLBACK_KIND: ${{ "
        "needs.event-identity.outputs.fallback_kind }}",
        "      VALIDATED_FALLBACK_SHA: ${{ "
        "needs.event-identity.outputs.fallback_sha }}",
        "    - name: Require classifier authority",
        "      if: ${{ "
        "needs.event-identity.outputs.classifier_available != 'true' }}",
        '        echo "Build classifier authority is unavailable" >&2',
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "      if: ${{ "
        "needs.event-identity.outputs.classifier_available == 'true' }}",
        "        ref: ${{ needs.event-identity.outputs.classifier_ref }}",
        "        fetch-depth: 1",
        "        persist-credentials: false",
        "    - name: Verify classifier authority revision",
        '          test "$ACTUAL_SHA" = "$CLASSIFIER_EXPECTED_SHA"',
        "    - name: Classify Build event",
        "      id: classify",
        "/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py "
        "classify-event",
        '--pr-base-sha "$PR_BASE_SHA" --pr-head-sha "$PR_HEAD_SHA"',
        '--push-sha "$PUSH_SHA" --output "$GITHUB_OUTPUT"',
        '            echo "classification=full"',
        '            echo "reason=classifier-bootstrap"',
        '            echo "expected_base=$expected_base"',
        '            echo "expected_head=$expected_head"',
        '            echo "full_fallback=$full_fallback"',
        '            echo "head_valid=$head_valid"',
        '            echo "identity_valid=$identity_valid"',
        '            echo "run_expensive=true"',
        '/usr/bin/git check-ref-format "refs/heads/$PR_BASE_REF"',
    )
    errors = [
        f"event-router lacks required closed contract: {item}"
        for item in required
        if item not in job
    ]
    direct_lines = []
    for line in job.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 4 and not line.startswith("    -"):
            direct_lines.append(line)
    if direct_lines != [
        "    name: event-router",
        "    if: ${{ always() && needs.event-identity.result == 'success' }}",
        "    needs: [event-identity]",
        "    runs-on: ubuntu-latest",
        "    timeout-minutes: 5",
        "    outputs:",
        "    env:",
        "    steps:",
    ]:
        errors.append("event-router direct job mapping differs")
    if "|| github.sha" in job:
        errors.append("event-router must never fall back to the merge SHA")
    if "        submodules:" in job:
        errors.append("event-router authority checkout must not load submodules")
    steps = _step_blocks(job)
    if len(steps) != 4:
        errors.append("event-router must have exactly four reviewed steps")
    else:
        expected_fields = (
            ["name", "if", "run"],
            ["uses", "if", "with"],
            ["name", "if", "run"],
            ["name", "id", "if", "env", "run"],
        )
        if any(
            _direct_step_mapping_fields(step) != fields
            for step, fields in zip(steps, expected_fields)
        ):
            errors.append("event-router step mappings differ")
        if not _step_has_scrubbed_environment(steps[3]):
            errors.append(
                "event-router must retain its scrubbed isolated environment"
            )
        expected_verify = (
            'ACTUAL_SHA="$(git rev-parse HEAD)"',
            "printf 'classifier.sha=%s\\n' \"$ACTUAL_SHA\"",
            'if [ -n "$CLASSIFIER_EXPECTED_SHA" ]; then',
            'test "$ACTUAL_SHA" = "$CLASSIFIER_EXPECTED_SHA"',
            "else",
            'test "$CLASSIFIER_REF" = "refs/heads/$DEFAULT_BRANCH"',
            "fi",
        )
        expected_classify = (
            "if test -f scripts/workflow_pilot/event_classifier.py; then",
            "/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py "
            "classify-event \\",
            '--event-name "$GITHUB_EVENT_NAME" '
            '--event-path "$GITHUB_EVENT_PATH" \\',
            '--github-ref "$GITHUB_REF" --github-sha "$GITHUB_SHA" \\',
            '--pr-base-sha "$PR_BASE_SHA" --pr-head-sha "$PR_HEAD_SHA" \\',
            '--push-sha "$PUSH_SHA" --output "$GITHUB_OUTPUT"',
            "else",
            "base_ref_valid=false",
            'expected_base=""',
            'expected_head=""',
            "full_fallback=false",
            "head_valid=false",
            "identity_valid=false",
            'if [[ "$GITHUB_EVENT_NAME" = "pull_request" ]]; then',
            "LC_ALL=C",
            "export LC_ALL",
            'if [[ "$PR_BASE_REF" != "@" && "$PR_BASE_REF_JSON" = \\"*\\" && \\',
            "${#PR_BASE_REF} -le 1024 ]] && \\",
            '/usr/bin/git check-ref-format "refs/heads/$PR_BASE_REF" \\',
            "> /dev/null 2>&1; then",
            "base_ref_valid=true",
            "fi",
            'if [[ "$PR_BASE_SHA" =~ ^[0-9a-f]{40}$ && \\',
            '"$PR_BASE_SHA_JSON" = "\\"$PR_BASE_SHA\\"" ]]; then',
            'expected_base="$PR_BASE_SHA"',
            "fi",
            'if [[ "$VALIDATED_FALLBACK_KIND" = "pull_request" && \\',
            '"$VALIDATED_FALLBACK_SHA" = "$PR_HEAD_SHA" ]]; then',
            'expected_head="$VALIDATED_FALLBACK_SHA"',
            "head_valid=true",
            "fi",
            'if [[ -n "$expected_base" && -n "$expected_head" && \\',
            '"$base_ref_valid" = true ]]; then',
            "identity_valid=true",
            'elif [[ "$head_valid" = true ]]; then',
            "full_fallback=true",
            "fi",
            'elif [[ "$VALIDATED_FALLBACK_KIND" = "push" && \\',
            '"$VALIDATED_FALLBACK_SHA" = "$PUSH_SHA" ]]; then',
            'expected_head="$VALIDATED_FALLBACK_SHA"',
            "head_valid=true",
            "identity_valid=true",
            "fi",
            "{",
            'echo "classification=full"',
            'echo "reason=classifier-bootstrap"',
            'echo "expected_base=$expected_base"',
            'echo "expected_head=$expected_head"',
            'echo "full_fallback=$full_fallback"',
            'echo "head_valid=$head_valid"',
            'echo "identity_valid=$identity_valid"',
            'echo "run_expensive=true"',
            '} >> "$GITHUB_OUTPUT"',
            "fi",
        )
        if tuple(_run_block_commands(steps[0])) != (
            'echo "Build classifier authority is unavailable" >&2',
            "exit 1",
        ):
            errors.append("event-router unavailable-authority guard differs")
        if tuple(_run_block_commands(steps[2])) != expected_verify:
            errors.append("event-router authority verification command differs")
        if tuple(_run_block_commands(steps[3])) != expected_classify:
            errors.append("event-router command or bootstrap differs")
    return errors


def _mode_contract_errors(job: str) -> list[str]:
    required = (
        f"    name: {EVENT_CLASSIFIER_DYNAMIC_NAME}",
        "    if: always()",
        "    needs: [event-identity, event-router]",
        "      CLASSIFIED_HEAD: ${{ needs.event-router.outputs.expected_head }}",
        "      EVENT_IDENTITY_RESULT: ${{ needs.event-identity.result }}",
        "      EVENT_NAME: ${{ github.event_name }}",
        "      EVENT_SHA: ${{ github.sha }}",
        "      PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}",
        "      TRUSTED_EVENT_KIND: ${{ needs.event-identity.outputs.fallback_kind }}",
        "      TRUSTED_EVENT_SHA: ${{ needs.event-identity.outputs.fallback_sha }}",
        "      PUSH_SHA: ${{ github.event.after }}",
        "      full_fallback: ${{ needs.event-router.outputs.full_fallback }}",
        "      head_valid: ${{ needs.event-router.outputs.head_valid }}",
        "      FULL_FALLBACK: ${{ needs.event-router.outputs.full_fallback }}",
        "      ROUTER_RESULT: ${{ needs.event-router.result }}",
        '        case "$FULL_FALLBACK" in',
        '            echo "classified PR head lacks coherent trusted event identity" >&2',
        '            echo "classified push head lacks coherent trusted event identity" >&2',
        '          if [ "$EVENT_NAME" != "pull_request" ] || \\',
        '             [ "$TRUSTED_EVENT_KIND" != "pull_request" ] || \\',
        '          echo "full fallback mode is not authoritative" >&2',
        "    - name: Verify authoritative Build event mode",
    )
    errors = [
        f"event-classifier mode contract lacks: {item}"
        for item in required
        if item not in job
    ]
    direct = [
        line
        for line in job.splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and len(line) - len(line.lstrip(" ")) == 4
        and not line.startswith("    -")
    ]
    if direct != [
        f"    name: {EVENT_CLASSIFIER_DYNAMIC_NAME}",
        "    if: always()",
        "    needs: [event-identity, event-router]",
        "    runs-on: ubuntu-latest",
        "    timeout-minutes: 5",
        "    outputs:",
        "    env:",
        "    steps:",
    ]:
        errors.append("event-classifier mode direct mapping differs")
    steps = _step_blocks(job)
    if (
        len(steps) != 1
        or _direct_step_mapping_fields(steps[0]) != ["name", "run"]
        or _step_name(steps[0]) != "Verify authoritative Build event mode"
    ):
        errors.append("event-classifier mode step differs")
    return errors


def _hashed_requirements_errors(text: str) -> list[str]:
    logical_lines = []
    current = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        current = f"{current} {line}".strip()
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        logical_lines.append(current)
        current = ""

    errors = []
    if current:
        errors.append("unterminated requirement continuation")

    records = {}
    for line in logical_lines:
        fields = line.split()
        if not fields or "==" not in fields[0]:
            errors.append(f"invalid requirement record: {line}")
            continue
        name, version = fields[0].split("==", 1)
        hashes = [field.removeprefix("--hash=") for field in fields[1:]]
        if any(not field.startswith("--hash=sha256:") for field in fields[1:]):
            errors.append(f"{name} has a non-SHA256 requirement option")
            continue
        if len(hashes) != 1:
            errors.append(f"{name} must have exactly one reviewed wheel hash")
            continue
        if name in records:
            errors.append(f"duplicate requirement: {name}")
            continue
        records[name] = (version, hashes[0])

    if records != EXPECTED_HASHED_REQUIREMENTS:
        errors.append("Build Python requirements differ from reviewed versions/hashes")
    return errors


def _make_recipe(text: str, target: str) -> str:
    match = re.search(
        rf"^{re.escape(target)}:\n(?P<recipe>(?:\t.*\n?)*)",
        text,
        re.MULTILINE,
    )
    if match is None:
        raise AssertionError(f"missing Make target: {target}")
    return match.group("recipe")


def _errors(text: str, retired_workflow_exists: bool) -> list[str]:
    errors = []
    header = text[: text.index("\njobs:\n")]
    if _has_unsupported_direct_key(header, indent=0, allow_sequence=False):
        errors.append("workflow uses unsupported direct mapping-key syntax")
    if _has_execution_defaults(header, workflow_scope=True):
        errors.append("workflow execution defaults must not alter candidate gates")
    if _has_direct_key(header, indent=0, key="env"):
        errors.append("workflow-level env is forbidden")
    try:
        _pull_request_actions(header)
    except ValueError as exc:
        errors.append(f"Build pull-request trigger is invalid: {exc}")
    try:
        push_branches = _push_branches(header)
    except ValueError as exc:
        errors.append(f"Build push trigger is invalid: {exc}")
    else:
        if push_branches != ("master",):
            errors.append("Build pushes must remain restricted to master")
    if "workflow_dispatch" in header:
        errors.append("Build must not expose a manual retired-workflow trigger")
    if retired_workflow_exists:
        errors.append("the retired standalone CI workflow must be deleted")

    jobs = _job_blocks(text)
    expected_timeouts = {
        "event-identity": 5,
        "event-router": 5,
        "event-classifier": 5,
        "host-tests": 60,
        "build": 90,
        "extended-host-tests": 60,
        "legacy": 60,
        "patch-release": 60,
        "summary": 5,
    }
    for job_name, timeout in expected_timeouts.items():
        if job_name not in jobs:
            continue
        matches = re.findall(
            r"^    timeout-minutes: ([0-9]+)$",
            jobs[job_name],
            re.MULTILINE,
        )
        if matches != [str(timeout)]:
            errors.append(
                f"{job_name} timeout-minutes must be exactly {timeout}"
            )
    expected_jobs = {
        "event-identity",
        "event-router",
        "event-classifier",
        "host-tests",
        "build",
        "extended-host-tests",
        "legacy",
        "patch-release",
        "summary",
    }
    if set(jobs) != expected_jobs:
        errors.append(f"Build job set differs from consolidated contract: {sorted(jobs)}")
        return errors

    errors.extend(_identity_contract_errors(jobs["event-identity"]))
    errors.extend(_classifier_contract_errors(jobs["event-router"]))
    errors.extend(_mode_contract_errors(jobs["event-classifier"]))

    for job_name, job in jobs.items():
        for command in _run_block_commands(job):
            words = set(command.split())
            has_apt_get = any(
                word == "apt-get" or word.endswith("/apt-get")
                for word in words
            )
            if has_apt_get and "libpng-dev" in words and "pkg-config" not in words:
                errors.append(f"{job_name} installs libpng-dev without pkg-config")
        pip_invocations = [
            command
            for command in _run_block_commands(job)
            for _match in PIP_INVOCATION_RE.finditer(command)
        ]
        if job_name == "build":
            if len(pip_invocations) != 1 or _normalise(pip_invocations[0]) != _normalise(
                HASHED_PIP_INSTALL
            ):
                errors.append(f"{job_name} must use the reviewed hash-locked Python requirements")
        elif job_name == "patch-release":
            run_commands = [_normalise(command) for command in _run_block_commands(job)]
            downloads = [
                invocation
                for invocation in run_commands
                if "-m pip download" in invocation
            ]
            installs = [
                _normalise(line.strip())
                for line in job.splitlines()
                if "-m pip install" in line
            ]
            if len(downloads) != 1 or len(installs) != 1:
                errors.append(
                    f"{job_name} must use exactly one trusted wheel download and isolated install"
                )
            else:
                download_required = (
                    "-m pip download",
                    "--require-hashes",
                    "--only-binary=:all:",
                    "--no-deps",
                )
                install_required = (
                    "-m pip install",
                    "--no-index",
                    "--find-links=",
                    "--require-hashes",
                    "--only-binary=:all:",
                    "--no-deps",
                    ".github/requirements/build.txt",
                )
                if not all(fragment in downloads[0] for fragment in download_required):
                    errors.append(
                        f"{job_name} must download the reviewed hash-locked Python requirements"
                    )
                if ".github/requirements/build.txt" not in job:
                    errors.append(
                        f"{job_name} must use the reviewed hash-locked requirements file"
                    )
                if not all(fragment in installs[0] for fragment in install_required):
                    errors.append(
                        f"{job_name} must install only the staged hash-locked wheel set"
                    )
        elif pip_invocations:
            errors.append(f"{job_name} adds an unreviewed Python package install")

    for job_name in COMBINED_WORKERS:
        if _has_unsupported_direct_key(
            jobs[job_name],
            indent=4,
            allow_sequence=True,
        ):
            errors.append(
                f"{job_name} uses unsupported direct mapping-key syntax"
            )
        if _has_direct_key(
            jobs[job_name],
            indent=4,
            key="continue-on-error",
        ):
            errors.append(f"{job_name} must not be advisory")
        try:
            condition = _direct_job_if(jobs[job_name])
        except ValueError as error:
            errors.append(f"{job_name} condition is invalid: {error}")
        else:
            for error in _github_expression_balance_errors(condition):
                errors.append(f"{job_name} condition is invalid: {error}")
        errors.extend(_combined_job_contract_errors(job_name, jobs[job_name]))

    errors.extend(_host_environment_errors(jobs["host-tests"]))
    errors.extend(_protected_host_prefix_errors(jobs["host-tests"]))

    if f"if: {MASTER_PUBLISHER_CONDITION}" not in jobs["patch-release"]:
        errors.append("patch-release must remain master-push-only")
    if "    needs: [event-identity]" not in jobs["patch-release"]:
        errors.append("patch-release must depend only on trusted event identity")
    if (
        "github.event_name == 'pull_request' && "
        "github.event.pull_request.head.sha || github.sha"
    ) in text:
        errors.append("Build must never substitute the merge SHA for a PR head")
    summary = jobs["summary"]
    summary_steps = _step_blocks(summary)
    if len(summary_steps) != 1 or not _summary_step_is_reviewed(summary_steps[0]):
        errors.append("summary metadata continuity script differs from the reviewed contract")
    if "    name: summary" not in summary:
        errors.append("summary must stay canonical")
    if "if: always()" not in summary:
        errors.append("summary must run after failed combined jobs on both triggers")
    if SUMMARY_NEEDS not in summary:
        errors.append("summary must depend on every required combined Build job")
    if '"$CLASSIFIER_RESULT" != "success"' not in summary:
        errors.append("summary must fail when event classification fails")
    if (
        "successful PR classification lacks coherent trusted event identity"
        not in summary
        or "successful push classification lacks coherent trusted event identity"
        not in summary
        or '[ "$FALLBACK_SHA" != "$CLASSIFIED_BUILD_SHA" ]' not in summary
    ):
        errors.append(
            "summary must bind every successful classification to trusted event identity"
        )
    if (
        '"$CLASSIFIER_RESULT" = "failure"' not in summary
        or '[ "$FALLBACK_IDENTITY_RESULT" = "success" ]' not in summary
        or '[ "$FALLBACK_KIND" = "pull_request" ]' not in summary
        or '[ "$FALLBACK_SHA" = "$PR_HEAD_SHA" ]' not in summary
        or '[ "$FALLBACK_KIND" = "push" ]' not in summary
        or '[ "$FALLBACK_SHA" = "$PUSH_SHA" ]' not in summary
        or '[ "$FALLBACK_SHA" = "$RAW_PUSH_SHA" ]' not in summary
        or "classifier-fallback Build worker did not succeed" not in summary
        or "classifier-fallback publisher did not succeed" not in summary
        or "classifier failed after exact-head fallback workers completed"
        not in summary
        or "classifier failed after exact-push fallback jobs completed"
        not in summary
        or "classifier failure without an exact fallback SHA started a worker"
        not in summary
        or "classifier failure without a validated fallback SHA ran publisher"
        not in summary
    ):
        errors.append("summary must audit classifier-failure worker topology")
    if (
        '[ "$HEAD_VALID" = "true" ]' not in summary
        or '[ "$IDENTITY_VALID" = "false" ]' not in summary
        or '[ "$FULL_FALLBACK" = "true" ]' not in summary
        or "incomplete-base Build worker did not succeed" not in summary
        or "incomplete-base PR unexpectedly ran publisher" not in summary
        or "lacks authoritative PR base identity" not in summary
    ):
        errors.append("summary must audit incomplete-base exact-head workers")
    if (
        'if [ "$IDENTITY_VALID" != "true" ] || [ -z "$PR_HEAD_SHA" ]' not in summary
        or 'if [ "$IDENTITY_VALID" != "true" ] || [ -z "$PUSH_SHA" ]' not in summary
        or '"$CLASSIFIED_BUILD_SHA" != "$PR_HEAD_SHA"' not in summary
        or '"$CLASSIFIED_BASE_SHA" != "$PR_BASE_SHA"' not in summary
        or '"$CLASSIFIED_BUILD_SHA" != "$PUSH_SHA"' not in summary
        or '[ -n "$CLASSIFIED_BASE_SHA" ]' not in summary
        or '[ -z "$PR_HEAD_SHA" ]' not in summary
        or '[ -z "$PR_BASE_SHA" ]' not in summary
        or '[ -z "$PUSH_SHA" ]' not in summary
    ):
        errors.append("summary must fail closed on missing or stale event identity")
    metadata_start = summary.find('if [ "$CLASSIFICATION" = "metadata-only" ]')
    full_start = summary.find('if [ "$CLASSIFICATION" != "full" ]')
    metadata_section = (
        summary[metadata_start:full_start]
        if 0 <= metadata_start < full_start
        else ""
    )
    if (
        not metadata_section
        or '"$RUN_EXPENSIVE" != "false"' not in metadata_section
        or metadata_section.count('[ "$result" != "success" ]') != 1
        or metadata_section.count('[ "$result" != "skipped" ]') != 1
        or "metadata-only continuity adapter did not succeed" not in metadata_section
        or "metadata-only expensive Build worker was not skipped" not in metadata_section
        or '"$PATCH_RELEASE_RESULT" != "skipped"' not in metadata_section
    ):
        errors.append("summary must accept only exact metadata-only continuity")
    if (
        '"$CLASSIFICATION" != "full"' not in summary
        or '"$RUN_EXPENSIVE" != "true"' not in summary
        or '"$PATCH_RELEASE_RESULT" != "success"' not in summary
        or "pull-request Build unexpectedly ran publisher" not in summary
    ):
        errors.append("summary must reject unknown full-build classifier output")
    loop_start = summary.rindex("for result")
    loop = summary[loop_start : summary.index("done", loop_start)]
    if '[ "$result" != "success" ]' not in loop:
        errors.append("summary loop must fail closed")
    for result in SUMMARY_RESULTS:
        if result not in loop:
            errors.append(f"summary loop omits required result: {result}")

    extended_host = jobs["extended-host-tests"]
    for command in (
        "make -f cjk_fonts.mk cjk-fonts-check cjk-fonts-test",
        "python3 -m unittest discover -s scripts/texttools/tests -p 'test_multilang_codec*.py' -v",
        "python3 -m unittest discover -s scripts/modernize/tests -p 'test_expansion_config.py' -v",
        "python3 -m unittest discover -s scripts/linker_report/tests -p 'test_*.py' -v",
    ):
        if not _contains_command(extended_host, command):
            errors.append(f"extended host lost unique evidence: {command}")

    for duplicate in (
        "scripts/artifact_guard",
        "scripts/docs_check_tests",
        "make generated-data",
        "scripts.localization.game_locales",
        "make game-localization-test",
        "expansion-modern-linker-check",
    ):
        if _contains_command(extended_host, duplicate):
            errors.append(f"extended host repeats Build-owned evidence: {duplicate}")

    for command in (
        "scripts.localization.game_locales check-crosswalk",
        "scripts.localization.game_locales check-raw-closure",
    ):
        if not _contains_command(jobs["host-tests"], command):
            errors.append(f"candidate host lost Build-owned evidence: {command}")
    for command in (WORKFLOW_PILOT_GATE, WORKFLOW_PILOT_BASELINE_GATE):
        if not _contains_exact_command(
            jobs["host-tests"],
            command,
            if_expression=FULL_WORKER_STEP_CONDITION,
            env_lines=SCRUBBED_STEP_ENV,
        ):
            errors.append(
                f"candidate host lost exact fail-closed Build evidence: {command}"
            )
    if not _contains_exact_command(
        jobs["host-tests"],
        WORKFLOW_PILOT_AUTHORITY_HYDRATION,
        if_expression=FULL_WORKER_STEP_CONDITION,
        env_lines=SCRUBBED_STEP_ENV,
    ):
        errors.append(
            "candidate host lost exact workflow-pilot Git authority hydration"
        )
    hydration_index = jobs["host-tests"].find(
        "Hydrate workflow-pilot Git authority"
    )
    reporter_index = jobs["host-tests"].find(
        "Run workflow-pilot reporter regression suite (issue #176)"
    )
    if (
        hydration_index < 0
        or reporter_index < 0
        or hydration_index >= reporter_index
    ):
        errors.append(
            "workflow-pilot Git authority hydration must precede reporter tests"
        )
    if _has_execution_defaults(jobs["host-tests"], workflow_scope=False):
        errors.append("candidate host execution defaults must not alter pilot gates")

    legacy = jobs["legacy"]
    for command in ("make legacy -j2", "make -C mgfembp compare"):
        if not _contains_command(legacy, command):
            errors.append(f"legacy job lost unique evidence: {command}")

    build = jobs["build"]
    if not _contains_command(
        build,
        "make codeql-alerts-test CODEQL_REQUIRE_FANALYZER=1",
    ):
        errors.append("build must require analyzer support for codeql-alerts-test")
    for command in (
        "expansion-modern-linker-check MODERN_CONFIG=debug",
        "expansion-modern-linker-check MODERN_CONFIG=release",
    ):
        if not _contains_command(build, command):
            errors.append(f"build lost canonical modern evidence: {command}")
    if not _contains_command(build, MAP_MENU_PRESENTATION_GATE):
        errors.append(
            "build must gate the all-locales profile through map-menu presentation"
        )
    return errors


def _remote_completion_errors(makefile_text: str) -> list[str]:
    recipe = _make_recipe(makefile_text, "remote-completion-check")
    required = (
        "--event push --branch master --commit",
        "--workflow build.yml",
        "requires master, not",
    )
    errors = [f"remote completion lacks {item}" for item in required if item not in recipe]
    if RETIRED_WORKFLOW_FILENAME in recipe:
        errors.append("remote completion still depends on the retired workflow")
    return errors


class ConsolidatedBuildTopologyTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_real_workflow_consolidates_candidate_and_master_evidence(self):
        self.assertEqual(_errors(self.text, RETIRED_WORKFLOW.exists()), [])
        self.assertEqual(
            _remote_completion_errors(MAKEFILE.read_text(encoding="utf-8")),
            [],
        )

    def test_protected_environment_is_exact_and_cannot_mask_python(self):
        workflow_env_variants = (
            "env:\n  BASH_ENV: build/python-mask.sh\n",
            '"env":\n  PATH: /untrusted\n',
            '"\\u0065nv":\n  PYTHONPATH: build/mask\n',
            "? env\n:\n  SHELLOPTS: sourcepath\n",
            "!!str env:\n  ENV: build/mask\n",
            "env: &shared {BASH_ENV: build/python-mask.sh}\n",
            "env: {BASH_ENV: build/python-mask.sh}\n",
        )
        for variant in workflow_env_variants:
            with self.subTest(workflow_env=variant):
                changed = self.text.replace(
                    "\npermissions:\n",
                    f"\n{variant}\npermissions:\n",
                    1,
                )
                self.assertTrue(
                    any(
                        "workflow-level env is forbidden" in error
                        or "unsupported direct mapping-key syntax" in error
                        for error in _errors(changed, False)
                    )
                )

        env_block = (
            "    env:\n"
            f"{HOST_ENV_LINE}\n"
        )
        value = EXPECTED_BUILD_SHA_EXPRESSION
        host_env_variants = (
            f"    env:\n{HOST_ENV_LINE}\n"
            "      BASH_ENV: build/python-mask.sh\n",
            f"    env:\n{HOST_ENV_LINE}\n      ENV: build/mask\n",
            f"    env:\n{HOST_ENV_LINE}\n      PATH: /untrusted\n",
            f"    env:\n{HOST_ENV_LINE}\n      PYTHONPATH: build/mask\n",
            f"    env:\n{HOST_ENV_LINE}\n      SHELLOPTS: sourcepath\n",
            f'    env:\n      "EXPECTED_BUILD_SHA": {value}\n',
            f'    env:\n      "EXPECTED_\\u0042UILD_SHA": {value}\n',
            f"    env:\n      ? EXPECTED_BUILD_SHA\n      : {value}\n",
            f"    env:\n      !!str EXPECTED_BUILD_SHA: {value}\n",
            f"    env:\n      <<: *shared\n{HOST_ENV_LINE}\n",
            f"    env: &shared\n{HOST_ENV_LINE}\n",
            f"    env: {{EXPECTED_BUILD_SHA: \"{value}\"}}\n",
            f"    env:\n      EXPECTED_BUILD_SHA: \"{value}\"\n",
            f"    env:\n      EXPECTED_BUILD_SHA: !!str {value}\n",
            f"    env:\n      EXPECTED_BUILD_SHA: &sha {value}\n",
            "    env:\n      EXPECTED_BUILD_SHA: *sha\n",
        )
        for variant in host_env_variants:
            with self.subTest(host_env=variant):
                changed = self.text.replace(env_block, variant, 1)
                self.assertTrue(
                    any(
                        "host-tests env must contain only" in error
                        or "exactly one reviewed env mapping" in error
                        or "unsupported direct mapping-key syntax" in error
                        for error in _errors(changed, False)
                    )
                )

        masked = self.text.replace(
            env_block,
            f"    env:\n{HOST_ENV_LINE}\n"
            "      BASH_ENV: build/python-mask.sh\n",
            1,
        ).replace(
            "    - name: Run workflow-pilot reporter regression suite",
            "    - name: Prepare Python function mask\n"
            "      run: printf 'python3() { return 0; }\\n' "
            "> build/python-mask.sh\n\n"
            "    - name: Run workflow-pilot reporter regression suite",
            1,
        )
        self.assertTrue(
            any(
                "host-tests env must contain only" in error
                for error in _errors(masked, False)
            )
        )

    def test_every_pre_pilot_step_is_exact_and_cannot_persist_masks(self):
        mutations = (
            self.text.replace(
                "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                "actions/checkout@main",
                1,
            ),
            self.text.replace(
                "        persist-credentials: false\n",
                "        persist-credentials: true\n",
                1,
            ),
            self.text.replace(
                '        test "$ACTUAL_SHA" = "$EXPECTED_BUILD_SHA"\n',
                '        test "$ACTUAL_SHA" = "$EXPECTED_BUILD_SHA"\n'
                '        echo "BASH_ENV=build/mask" >> "$GITHUB_ENV"\n',
                1,
            ),
            self.text.replace(
                "sudo apt-get update && sudo apt-get install -y "
                "build-essential libmgba-dev",
                "sudo apt-get update && sudo apt-get install -y "
                "build-essential libmgba-dev && "
                'echo build/bin >> "$GITHUB_PATH"',
                1,
            ),
            self.text.replace(
                "GBA_PLAYTEST_HOST_ONLY=1 python3 -m unittest discover "
                "-s tools/gba-playtest/tests -v",
                "true",
                1,
            ),
            self.text.replace(
                "python3 -m unittest discover -s tests/upstream_port -v",
                "python3 -m unittest discover -s tests/upstream_port -v || true",
                1,
            ),
            self.text.replace(
                'python3 -m unittest discover -s tests/workflows -p "test_*.py" -v',
                'python3 -m unittest discover -s tests/workflows -p "test_*.py" '
                '-v && echo "PYTHONPATH=build/mask" >> "$GITHUB_ENV"',
                1,
            ),
            self.text.replace(
                "    - name: Run workflow-pilot reporter regression suite",
                "    - name: Unreviewed setup\n"
                "      run: echo build/bin >> \"$GITHUB_PATH\"\n\n"
                "    - name: Run workflow-pilot reporter regression suite",
                1,
            ),
            self.text.replace(
                "    - name: Run upstream-port tooling test suite",
                "    - uses: actions/setup-python@main\n\n"
                "    - name: Run upstream-port tooling test suite",
                1,
            ),
            self.text.replace(
                "    - name: Run upstream-port tooling test suite",
                "    - name: Run workflow contract test suite",
                1,
            ),
        )
        protected_names = (
            "Verify checked-out revision",
            "Install host-only dependencies (no arm-none-eabi toolchain)",
            "Run gba-playtest host test suite",
            "Run upstream-port tooling test suite",
            "Run workflow contract test suite",
        )
        for name in protected_names:
            mutations += (
                self.text.replace(
                    f"    - name: {name}\n",
                    f"    - name: {name}\n      shell: bash {{0}}\n",
                    1,
                ),
                self.text.replace(
                    f"    - name: {name}\n",
                    f"    - name: {name}\n      working-directory: /\n",
                    1,
                ),
                self.text.replace(
                    f"    - name: {name}\n",
                    f"    - name: {name}\n      continue-on-error: true\n",
                    1,
                ),
                self.text.replace(
                    f"    - name: {name}\n",
                    f"    - name: {name}\n      if: ${{{{ false }}}}\n",
                    1,
                ),
                self.text.replace(
                    f"    - name: {name}\n",
                    f'    - "name": {name}\n',
                    1,
                ),
            )
        for changed in mutations:
            with self.subTest(mutation=changed[:180]):
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "protected pre-pilot step sequence differs" in error
                        or "event-router lacks required closed contract" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_protected_pilot_steps_require_exact_scrubbed_environment(self):
        names = (
            "Hydrate workflow-pilot Git authority",
            "Run workflow-pilot reporter regression suite (issue #176)",
            "Validate workflow-pilot baseline against checked-out Git history",
        )
        env_block = "      env:\n" + "\n".join(SCRUBBED_STEP_ENV) + "\n"
        variants = (
            "",
            *(
                env_block.replace(f"{entry}\n", "")
                for entry in SCRUBBED_STEP_ENV
            ),
            env_block.replace("        PATH: /usr/bin:/bin", "        PATH: /untrusted"),
            env_block + "        GITHUB_ENV: build/mask\n",
            env_block.replace("      env:", '      "env":'),
            env_block.replace("        BASH_ENV:", "        BASH_ENV :"),
            env_block.replace("        ENV: ''", "        ENV: &mask ''"),
            env_block.replace("        PYTHONPATH: ''", "        <<: *mask"),
        )
        for name in names:
            for variant in variants:
                with self.subTest(name=name, variant=variant):
                    step_start = self.text.index(f"    - name: {name}\n")
                    env_start = self.text.index("      env:\n", step_start)
                    run_start = self.text.index("      run:", env_start)
                    changed = (
                        self.text[:env_start]
                        + variant
                        + self.text[run_start:]
                    )
                    self.assertTrue(
                        any(
                            "protected pre-pilot step sequence differs" in error
                            or "lost exact workflow-pilot" in error
                            for error in _errors(changed, False)
                        )
                    )

    def test_workflow_pilot_authority_hydration_is_exact_and_ordered(self):
        self.assertEqual(hydrate_authority.GIT, "/usr/bin/git")
        self.assertNotIn(
            "GIT_NO_LAZY_FETCH",
            reporter.git_environment(offline=False),
        )
        self.assertEqual(
            reporter.git_environment(offline=False)["GIT_CONFIG_COUNT"],
            "0",
        )
        self.assertEqual(
            reporter.git_environment(offline=False)["GIT_NO_REPLACE_OBJECTS"],
            "1",
        )
        self.assertEqual(hydrate_authority.BATCH_SIZE, 256)
        self.assertEqual(
            hydrate_authority.FETCH_OPTIONS,
            (
                "--quiet",
                "--no-tags",
                "--filter=blob:none",
                "--no-write-fetch-head",
            ),
        )
        self.assertEqual(
            hydrate_authority.BLOB_FETCH_OPTIONS,
            (
                "--quiet",
                "--no-tags",
                "--no-write-fetch-head",
            ),
        )
        host = _job_blocks(self.text)["host-tests"]
        self.assertTrue(
            _contains_exact_command(
                host,
                WORKFLOW_PILOT_AUTHORITY_HYDRATION,
                if_expression=FULL_WORKER_STEP_CONDITION,
                env_lines=SCRUBBED_STEP_ENV,
            )
        )
        self.assertLess(
            host.index("Hydrate workflow-pilot Git authority"),
            host.index("Run workflow-pilot reporter regression suite"),
        )
        replacements = (
            "true",
            WORKFLOW_PILOT_AUTHORITY_HYDRATION.replace(" -I ", " "),
            WORKFLOW_PILOT_AUTHORITY_HYDRATION.replace(
                "/usr/bin/python3",
                "python3",
            ),
            WORKFLOW_PILOT_AUTHORITY_HYDRATION.replace(
                "scripts/workflow_pilot/isolated_launcher.py",
                "scripts/workflow_pilot/reporter.py",
            ),
            WORKFLOW_PILOT_AUTHORITY_HYDRATION.replace(
                "--fixture scripts/workflow_pilot/tests/fixtures/baseline.json ",
                "",
            ),
            WORKFLOW_PILOT_AUTHORITY_HYDRATION.replace(
                "--decisions .github/workflow-pilot-decisions.json ",
                "",
            ),
            WORKFLOW_PILOT_AUTHORITY_HYDRATION.replace(
                '--expected-head "$EXPECTED_BUILD_SHA"',
                "--remote untrusted",
            ),
        )
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                changed = self.text.replace(
                    f"      run: {WORKFLOW_PILOT_AUTHORITY_HYDRATION}\n",
                    f"      run: {replacement}\n",
                    1,
                )
                self.assertTrue(
                    any(
                        "lost exact workflow-pilot Git authority hydration"
                        in error
                        for error in _errors(changed, False)
                    )
                )

    def test_isolated_launcher_and_closed_modes_are_pinned(self):
        commands = (
            WORKFLOW_PILOT_AUTHORITY_HYDRATION,
            WORKFLOW_PILOT_GATE,
            WORKFLOW_PILOT_BASELINE_GATE,
        )
        replacements = (
            lambda command: command.replace(" -I ", " "),
            lambda command: command.replace(
                "scripts/workflow_pilot/isolated_launcher.py",
                "scripts/workflow_pilot/reporter.py",
            ),
            lambda command: command.replace(
                " isolated_launcher.py hydrate",
                " isolated_launcher.py arbitrary",
            ),
            lambda command: command.replace(" hydrate ", " arbitrary "),
            lambda command: command.replace(" reporter-tests", " arbitrary"),
            lambda command: command.replace(" baseline ", " arbitrary "),
        )
        for command in commands:
            for replace in replacements:
                replacement = replace(command)
                if replacement == command:
                    continue
                with self.subTest(command=command, replacement=replacement):
                    changed = self.text.replace(
                        f"      run: {command}\n",
                        f"      run: {replacement}\n",
                        1,
                    )
                    self.assertTrue(
                        any(
                            "protected pre-pilot step sequence differs" in error
                            or "lost exact workflow-pilot" in error
                            or "lost exact fail-closed" in error
                            for error in _errors(changed, False)
                        )
                    )

    def test_isolated_launcher_ignores_repository_sitecustomize(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="workflow-isolated-launcher-",
            dir=artifact_root,
        ) as temporary:
            root = Path(temporary)
            package = root / "scripts" / "workflow_pilot"
            tests = package / "tests"
            tests.mkdir(parents=True)
            (root / "scripts" / "__init__.py").write_text("", encoding="ascii")
            (package / "__init__.py").write_text("", encoding="ascii")
            (tests / "__init__.py").write_text("", encoding="ascii")
            shutil.copy2(
                ROOT / "scripts/workflow_pilot/isolated_launcher.py",
                package / "isolated_launcher.py",
            )
            (root / "sitecustomize.py").write_text(
                "import os\nos._exit(0)\n",
                encoding="ascii",
            )
            (package / "hydrate_authority.py").write_text(
                "import os\n"
                "from pathlib import Path\n"
                "def main(argv):\n"
                "    assert not any(key.startswith('GIT_') for key in os.environ)\n"
                "    Path(__file__).resolve().parents[2]"
                ".joinpath('hydrate.marker').write_text('ran')\n"
                "    return 0\n",
                encoding="ascii",
            )
            (package / "reporter.py").write_text(
                "import os\n"
                "from pathlib import Path\n"
                "def main(argv):\n"
                "    assert not any(key.startswith('GIT_') for key in os.environ)\n"
                "    Path(__file__).resolve().parents[2]"
                ".joinpath('baseline.marker').write_text('ran')\n"
                "    return 0\n"
                "if __name__ == '__main__':\n"
                "    raise SystemExit(main(None))\n",
                encoding="ascii",
            )
            (tests / "test_probe.py").write_text(
                "import os\n"
                "import unittest\n"
                "from pathlib import Path\n"
                "class Probe(unittest.TestCase):\n"
                "    def test_probe(self):\n"
                "        self.assertFalse(any(key.startswith('GIT_') "
                "for key in os.environ))\n"
                "        Path(__file__).resolve().parents[3]"
                ".joinpath('tests.marker').write_text('ran')\n",
                encoding="ascii",
            )
            hostile_environment = dict(os.environ)
            hostile_environment["PYTHONPATH"] = str(root)
            hostile_environment["GIT_DIR"] = str(root / "redirected.git")
            hostile_environment["GIT_CONFIG_COUNT"] = "1"
            hostile_environment["GIT_CONFIG_KEY_0"] = "alias.status"
            hostile_environment["GIT_CONFIG_VALUE_0"] = "!exit 0"
            normal = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-m",
                    "scripts.workflow_pilot.reporter",
                ],
                cwd=root,
                env=hostile_environment,
                check=False,
                capture_output=True,
            )
            self.assertEqual(normal.returncode, 0)
            self.assertFalse((root / "baseline.marker").exists())

            launcher = "scripts/workflow_pilot/isolated_launcher.py"
            commands = (
                ["/usr/bin/python3", "-I", launcher, "reporter-tests"],
                [
                    "/usr/bin/python3",
                    "-I",
                    launcher,
                    "baseline",
                    "--repository-root",
                    str(root),
                ],
                [
                    "/usr/bin/python3",
                    "-I",
                    launcher,
                    "hydrate",
                    "--repository-root",
                    str(root),
                ],
            )
            for command in commands:
                completed = subprocess.run(
                    command,
                    cwd=root,
                    env=hostile_environment,
                    check=False,
                    capture_output=True,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stderr.decode("utf-8", errors="replace"),
                )
            for marker in (
                "tests.marker",
                "baseline.marker",
                "hydrate.marker",
            ):
                self.assertEqual(
                    (root / marker).read_text(encoding="ascii"),
                    "ran",
                )

            rejected = subprocess.run(
                ["/usr/bin/python3", "-I", launcher, "arbitrary"],
                cwd=root,
                env=hostile_environment,
                check=False,
                capture_output=True,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertFalse((root / "arbitrary.marker").exists())
            for command in (
                [
                    "/usr/bin/python3",
                    "-I",
                    launcher,
                    "reporter-tests",
                    "arbitrary",
                ],
                [
                    "/usr/bin/python3",
                    "-I",
                    launcher,
                    "baseline",
                    "--repository-root",
                    str(package),
                ],
            ):
                completed = subprocess.run(
                    command,
                    cwd=root,
                    env=hostile_environment,
                    check=False,
                    capture_output=True,
                )
                self.assertEqual(completed.returncode, 2)

    def test_exact_fixture_hydration_restores_force_pushed_commit(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="workflow-authority-hydration-",
            dir=artifact_root,
        ) as temporary:
            root = Path(temporary)
            seed = root / "seed"
            remote = root / "remote.git"
            checkout = root / "checkout"

            seed.mkdir()
            _git_run(seed, "init", "-q", "-b", "master")
            for key, value in (
                ("user.name", "Hydration Test"),
                ("user.email", "hydration@example.invalid"),
            ):
                _git_run(seed, "config", key, value)
            (seed / "expected.txt").write_text("expected\n", encoding="ascii")
            _git_run(seed, "add", "expected.txt")
            _git_run(seed, "commit", "-q", "-m", "expected head")
            expected_head = _git_run(
                seed,
                "rev-parse",
                "HEAD",
                text=True,
            ).stdout.strip()
            _git_run(seed, "checkout", "-q", "--orphan", "force-pushed")
            _git_run(seed, "rm", "-q", "-rf", ".")
            (seed / "historical.txt").write_text(
                "force-pushed\n",
                encoding="ascii",
            )
            decision_path = (
                seed / ".github" / "workflow-pilot-decisions.json"
            )
            decision_path.parent.mkdir(parents=True)
            decision_content = (
                b'{"artifacts":[],"pull_requests":['
                b'{"pull_request":1,"threshold":{"override_history":['
                b'{"enabled":true,"reason":"test override"}]}}],'
                b'"schema_version":1}\n'
            )
            decision_path.write_bytes(decision_content)
            _git_run(seed, "add", "historical.txt")
            _git_run(seed, "add", str(reporter.DECISION_RECORD_PATH))
            _git_run(
                seed,
                "commit",
                "-q",
                "-m",
                "force-pushed historical candidate",
            )
            historical = _git_run(
                seed,
                "rev-parse",
                "HEAD",
                text=True,
            ).stdout.strip()
            _git_run(seed, "checkout", "-q", "master")
            remote.mkdir()
            _git_run(remote, "init", "-q", "--bare")
            _git_run(seed, "remote", "add", "origin", str(remote))
            _git_run(
                seed,
                "push",
                "-q",
                "origin",
                "master",
                f"{historical}:refs/heads/force-pushed",
                offline=False,
            )
            anchors = {
                f"{hydrate_authority.ANCHOR_PREFIX}{sha}": sha
                for sha in sorted((expected_head, historical))
            }
            for name, sha in anchors.items():
                _git_run(remote, "update-ref", name, sha)
            _git_run(
                seed,
                "push",
                "-q",
                "origin",
                ":refs/heads/force-pushed",
                offline=False,
            )
            _git_run(remote, "reflog", "expire", "--expire=now", "--all")
            _git_run(remote, "gc", "--prune=now")
            _git_run(
                remote,
                "config",
                "uploadpack.allowAnySHA1InWant",
                "true",
            )
            _git_run(remote, "config", "uploadpack.allowFilter", "true")
            checkout.mkdir()
            _git_run(checkout, "init", "-q", "-b", "master")
            _git_run(
                checkout,
                "remote",
                "add",
                "origin",
                "https://github.com/laqieer/fireemblem8-expansion.git",
            )
            _git_run(
                checkout,
                "config",
                f"url.file://{remote}.insteadOf",
                "https://github.com/laqieer/fireemblem8-expansion.git",
            )
            _git_run(
                checkout,
                "fetch",
                "-q",
                "--depth=1",
                "origin",
                expected_head,
                offline=False,
            )
            _git_run(checkout, "checkout", "-q", "--detach", "FETCH_HEAD")
            self.assertNotEqual(
                _git_run(
                    checkout,
                    "cat-file",
                    "-e",
                    f"{historical}^{{commit}}",
                    check=False,
                ).returncode,
                0,
            )

            _git_run(
                checkout,
                "fetch",
                "--quiet",
                "--no-tags",
                "--filter=blob:none",
                "origin",
                "+refs/heads/*:refs/remotes/origin/*",
                offline=False,
            )
            self.assertNotEqual(
                _git_run(
                    checkout,
                    "cat-file",
                    "-e",
                    f"{historical}^{{commit}}",
                    check=False,
                ).returncode,
                0,
            )
            refs_before = _git_run(checkout, "show-ref").stdout
            fetch_head_before = (checkout / ".git" / "FETCH_HEAD").read_bytes()

            required = sorted([expected_head, historical])
            hostile = {
                "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(
                    seed / ".git" / "objects"
                ),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "remote.origin.url",
                "GIT_CONFIG_VALUE_0": "file:///does-not-exist",
                "GIT_DIR": str(seed / ".git"),
                "GIT_OBJECT_DIRECTORY": str(seed / ".git" / "objects"),
                "GIT_WORK_TREE": str(seed),
            }
            with mock.patch.dict(os.environ, hostile, clear=False):
                result = hydrate_authority.hydrate_anchor_commits(
                    checkout,
                    "laqieer/fireemblem8-expansion",
                    required,
                    anchors,
                    expected_head,
                )
            self.assertEqual(result["required"], 2)
            self.assertGreater(result["fetched"], 0)
            _git_run(
                checkout,
                "cat-file",
                "-e",
                f"{historical}^{{commit}}",
            )
            _git_run(
                checkout,
                "cat-file",
                "-e",
                f"{historical}^{{tree}}",
            )
            decision_blobs = hydrate_authority.required_decision_blob_ids(
                checkout,
                [historical],
            )
            self.assertEqual(len(decision_blobs), 1)
            self.assertEqual(
                hydrate_authority.available_objects(
                    checkout,
                    decision_blobs,
                    "blob",
                ),
                set(),
            )
            self.assertNotEqual(
                _git_run(
                    checkout,
                    "show",
                    f"{historical}:{reporter.DECISION_RECORD_PATH}",
                    check=False,
                ).returncode,
                0,
            )
            unrelated_blob = (
                _git_run(
                    checkout,
                    "ls-tree",
                    historical,
                    "--",
                    "historical.txt",
                    text=True,
                )
                .stdout.split()[2]
            )
            blob_result = hydrate_authority.hydrate_override_decision_blobs(
                checkout,
                "laqieer/fireemblem8-expansion",
                [historical],
                expected_head,
            )
            self.assertEqual(
                blob_result,
                {"required_blobs": 1, "fetched_blobs": 1},
            )
            self.assertEqual(
                _git_run(
                    checkout,
                    "show",
                    f"{historical}:{reporter.DECISION_RECORD_PATH}",
                ).stdout,
                decision_content,
            )
            self.assertEqual(
                hydrate_authority.available_objects(
                    checkout,
                    [unrelated_blob],
                    "blob",
                ),
                set(),
            )
            self.assertEqual(
                _git_run(
                    checkout,
                    "rev-parse",
                    "HEAD",
                    text=True,
                ).stdout.strip(),
                expected_head,
            )
            self.assertEqual(
                _git_run(checkout, "show-ref").stdout,
                refs_before,
            )
            self.assertEqual(
                (checkout / ".git" / "FETCH_HEAD").read_bytes(),
                fetch_head_before,
            )

            missing_name = next(iter(anchors))
            _git_run(remote, "update-ref", "-d", missing_name)
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "missing=",
            ):
                hydrate_authority.hydrate_anchor_commits(
                    checkout,
                    "laqieer/fireemblem8-expansion",
                    required,
                    anchors,
                    expected_head,
                )
            wrong_target = (
                historical
                if anchors[missing_name] == expected_head
                else expected_head
            )
            _git_run(remote, "update-ref", missing_name, wrong_target)
            with self.assertRaisesRegex(reporter.PilotDataError, "moved="):
                hydrate_authority.hydrate_anchor_commits(
                    checkout,
                    "laqieer/fireemblem8-expansion",
                    required,
                    anchors,
                    expected_head,
                )
            _git_run(remote, "update-ref", missing_name, anchors[missing_name])
            extra_name = f"{hydrate_authority.ANCHOR_PREFIX}{'f' * 40}"
            _git_run(remote, "update-ref", extra_name, expected_head)
            with self.assertRaisesRegex(reporter.PilotDataError, "extra="):
                hydrate_authority.hydrate_anchor_commits(
                    checkout,
                    "laqieer/fireemblem8-expansion",
                    required,
                    anchors,
                    expected_head,
                )
            _git_run(remote, "update-ref", "-d", extra_name)
            omitted = next(name for name in anchors if name != missing_name)
            _git_run(remote, "update-ref", "-d", omitted)
            subset = {missing_name: anchors[missing_name]}
            with self.assertRaisesRegex(reporter.PilotDataError, "do not cover"):
                hydrate_authority.hydrate_anchor_commits(
                    checkout,
                    "laqieer/fireemblem8-expansion",
                    required,
                    subset,
                    expected_head,
                )

    def test_production_hydration_extracts_only_strict_fixture_commits(self):
        fixture_path = ROOT / "scripts/workflow_pilot/tests/fixtures/baseline.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        repository, required = hydrate_authority.required_commits_from_fixture(
            fixture_path
        )
        self.assertEqual(repository, "laqieer/fireemblem8-expansion")
        self.assertEqual(required, sorted(set(required)))
        self.assertEqual(
            required,
            sorted(commit["sha"] for commit in fixture["commits"]),
        )
        decisions_path = ROOT / reporter.DECISION_RECORD_PATH
        (
            derived_repository,
            derived_commits,
            decision_commits,
        ) = hydrate_authority.required_override_decision_commits(
            fixture_path,
            decisions_path,
        )
        self.assertEqual(derived_repository, repository)
        self.assertEqual(derived_commits, required)
        fixture_data = reporter.validate_fixture(fixture)
        introduction_commits = {
            event["sha"]
            for event in fixture_data["events"].values()
            if event["type"] == "threshold_override_introduced"
        }
        self.assertEqual(set(decision_commits), introduction_commits)
        anchors = hydrate_authority.required_anchor_refs(fixture_path)
        self.assertEqual(len(anchors), 12)
        self.assertEqual(
            anchors,
            {
                f"{hydrate_authority.ANCHOR_PREFIX}{sha}": sha
                for sha in anchors.values()
            },
        )
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "malformed or duplicated",
        ):
            hydrate_authority.parse_remote_anchor_refs(
                b"0" * 40
                + b"\trefs/tags/workflow-pilot-baseline/not-a-sha\n"
            )
        duplicate = next(iter(anchors.items()))
        with self.assertRaisesRegex(
            reporter.PilotDataError,
            "malformed or duplicated",
        ):
            hydrate_authority.parse_remote_anchor_refs(
                (
                    f"{duplicate[1]}\t{duplicate[0]}\n"
                    f"{duplicate[1]}\t{duplicate[0]}\n"
                ).encode("ascii")
            )

    def test_anchor_ref_print_mode_is_deterministic_and_read_only(self):
        command = [
            "/usr/bin/python3",
            "-I",
            "scripts/workflow_pilot/isolated_launcher.py",
            "anchor-refs",
            "--repository-root",
            str(ROOT),
            "--fixture",
            str(ROOT / reporter.BASELINE_FIXTURE_PATH),
            "--decisions",
            str(ROOT / reporter.DECISION_RECORD_PATH),
        ]
        before = hydrate_authority.authority_state(ROOT)
        first = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        second = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        self.assertEqual(first, second)
        self.assertEqual(len(first.splitlines()), 12)
        self.assertEqual(hydrate_authority.authority_state(ROOT), before)

    def test_synthetic_stacked_pull_request_runs_candidate_jobs_on_its_real_base(self):
        event = {
            "event_name": "pull_request",
            "action": "opened",
            "pull_request": {
                "base": {"ref": "agent/issue-170", "sha": "2" * 40},
                "head": {"sha": "1" * 40},
            },
        }
        self.assertEqual(
            _triggered_jobs(self.text, event),
            CANDIDATE_FULL_JOBS,
        )
        self.assertNotIn("patch-release", _triggered_jobs(self.text, event))

    def test_pull_request_branch_filters_fail_closed_in_inline_and_block_forms(self):
        event = {
            "event_name": "pull_request",
            "action": "opened",
            "pull_request": {
                "base": {"ref": "agent/issue-170", "sha": "2" * 40},
                "head": {"sha": "1" * 40},
            },
        }
        mutations = (
            '    branches: [ "master" ]\n',
            '    branches:\n      - "master"\n',
            '    branches-ignore: [ "agent/**" ]\n',
            '    branches-ignore:\n      - "agent/**"\n',
            '    "branches": [ "master" ]\n',
            "    'branches':\n      - \"master\"\n",
            '    "branches-ignore": [ "agent/**" ]\n',
            "    'branches-ignore':\n      - \"agent/**\"\n",
            '    branches : [ "master" ]\n',
            '    "branches-ignore" : [ "agent/**" ]\n',
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                changed = self.text.replace(
                    PULL_REQUEST_TRIGGER,
                    PULL_REQUEST_TRIGGER + mutation,
                    1,
                )
                self.assertTrue(
                    any(
                        "must not define branches or branches-ignore filters" in error
                        for error in _errors(changed, False)
                    )
                )
                self.assertEqual(_triggered_jobs(changed, event), set())

    def test_candidate_pull_request_activity_types_are_explicit_and_fail_closed(self):
        for action in ("opened", "synchronize", "reopened"):
            with self.subTest(action=action):
                event = {
                    "event_name": "pull_request",
                    "action": action,
                    "pull_request": {
                        "base": {"ref": "agent/issue-170", "sha": "2" * 40},
                        "head": {"sha": "1" * 40},
                    },
                }
                self.assertEqual(
                    _triggered_jobs(self.text, event),
                    CANDIDATE_FULL_JOBS,
                )

        for action in ("closed", "labeled", "unlabeled", "assigned"):
            with self.subTest(action=action):
                event = {
                    "event_name": "pull_request",
                    "action": action,
                    "pull_request": {
                        "base": {"ref": "agent/issue-170", "sha": "2" * 40},
                        "head": {"sha": "1" * 40},
                    },
                }
                self.assertEqual(_triggered_jobs(self.text, event), set())

    def test_parsed_event_fixtures_select_exact_jobs_and_heads(self):
        fixture = json.loads(EVENT_FIXTURE.read_text(encoding="utf-8"))
        infrastructure = set(fixture["infrastructure_jobs"])
        for case in fixture["cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    _triggered_jobs(self.text, case),
                    set(case["expected"]["jobs"]) | infrastructure,
                )
                decision = event_classifier.classify_event(
                    case["event_name"],
                    case["payload"],
                    github_ref=case["runner"]["github_ref"],
                    github_sha=case["runner"]["github_sha"],
                    pr_base_sha=case["runner"]["pr_base_sha"],
                    pr_head_sha=case["runner"]["pr_head_sha"],
                    push_sha=case["runner"]["push_sha"],
                )
                self.assertEqual(
                    decision.expected_head,
                    case["expected"]["expected_head"],
                )
                self.assertEqual(
                    decision.expected_base,
                    case["expected"]["expected_base"],
                )
                self.assertEqual(
                    decision.identity_valid,
                    case["expected"]["identity_valid"],
                )
                if not decision.identity_valid:
                    expected_invalid_jobs = {"event-classifier", "summary"}
                    if decision.head_valid and not decision.expected_base:
                        expected_invalid_jobs.update(COMBINED_WORKERS)
                    self.assertEqual(
                        set(case["expected"]["jobs"]),
                        expected_invalid_jobs,
                    )
                    self.assertFalse(case["expected"]["summary_success"])
        header = self.text[: self.text.index("\njobs:\n")]
        self.assertEqual(
            "workflow_dispatch" in header,
            fixture["workflow_dispatch_supported"],
        )

    def test_pre_fix_body_edit_negative_control_executes_preserved_graph(self):
        fixture = json.loads(EVENT_FIXTURE.read_text(encoding="utf-8"))
        body_only = next(
            case
            for case in fixture["cases"]
            if case["id"] == "body-only-merge-sha-ignored"
        )
        header = self.text[: self.text.index("\njobs:\n")]
        self.assertIn(body_only["payload"]["action"], _pull_request_actions(header))
        pre_fix_text = PRE_FIX_WORKFLOW.read_text(encoding="utf-8")
        pre_fix_jobs = _pre_fix_triggered_jobs(pre_fix_text, body_only)
        self.assertEqual(pre_fix_jobs - {"summary"}, set(COMBINED_WORKERS))
        self.assertIn("summary", pre_fix_jobs)
        self.assertNotIn("event-classifier", pre_fix_jobs)
        self.assertEqual(
            _triggered_jobs(self.text, body_only),
            METADATA_TRIGGERED_JOBS,
        )

    def test_incomplete_base_fixtures_run_exact_head_full_fallback(self):
        fixture = json.loads(EVENT_FIXTURE.read_text(encoding="utf-8"))
        template = next(
            case
            for case in fixture["cases"]
            if case["id"] == "body-only-merge-sha-ignored"
        )
        for incomplete in fixture["incomplete_base_cases"]:
            with self.subTest(case=incomplete["id"]):
                case = json.loads(json.dumps(template))
                case["payload"]["pull_request"]["base"] = incomplete["base"]
                case["runner"]["pr_base_sha"] = incomplete["runner_base_sha"]
                self.assertEqual(
                    _triggered_jobs(self.text, case),
                    CANDIDATE_FULL_JOBS,
                )
                decision = event_classifier.classify_event(
                    case["event_name"],
                    case["payload"],
                    github_ref=case["runner"]["github_ref"],
                    github_sha=case["runner"]["github_sha"],
                    pr_base_sha=case["runner"]["pr_base_sha"],
                    pr_head_sha=case["runner"]["pr_head_sha"],
                    push_sha=case["runner"]["push_sha"],
                )
                self.assertTrue(decision.head_valid)
                self.assertTrue(decision.full_fallback)
                self.assertFalse(decision.identity_valid)
                self.assertEqual(
                    decision.expected_head,
                    case["runner"]["pr_head_sha"],
                )

    def test_metadata_base_ref_fixtures_use_git_branch_validation(self):
        fixture = json.loads(EVENT_FIXTURE.read_text(encoding="utf-8"))
        templates = {
            field: next(
                case
                for case in fixture["cases"]
                if case["id"] == case_id
            )
            for field, case_id in (
                ("body", "body-only-merge-sha-ignored"),
                ("title", "title-only"),
            )
        }
        metadata_jobs = METADATA_TRIGGERED_JOBS
        for ref_case in fixture["base_ref_validation_cases"]:
            for field, template in templates.items():
                with self.subTest(case=ref_case["id"], field=field):
                    case = json.loads(json.dumps(template))
                    case["payload"]["pull_request"]["base"]["ref"] = ref_case["ref"]
                    self.assertEqual(
                        _triggered_jobs(self.text, case),
                        metadata_jobs
                        if ref_case["accepted"]
                        else CANDIDATE_FULL_JOBS,
                    )
                    decision = event_classifier.classify_event(
                        case["event_name"],
                        case["payload"],
                        github_ref=case["runner"]["github_ref"],
                        github_sha=case["runner"]["github_sha"],
                        pr_base_sha=case["runner"]["pr_base_sha"],
                        pr_head_sha=case["runner"]["pr_head_sha"],
                        push_sha=case["runner"]["push_sha"],
                    )
                    self.assertEqual(
                        decision.classification,
                        "metadata-only" if ref_case["accepted"] else "full",
                    )
                    if not ref_case["accepted"]:
                        self.assertTrue(decision.full_fallback)
                        self.assertFalse(decision.identity_valid)

    def test_metadata_worker_names_require_complete_raw_pr_identity(self):
        fixture = json.loads(EVENT_FIXTURE.read_text(encoding="utf-8"))
        body_only = next(
            case
            for case in fixture["cases"]
            if case["id"] == "body-only-merge-sha-ignored"
        )
        cases = (
            (
                "metadata-body-edit",
                body_only,
                METADATA_CHECK_CONTEXTS,
            ),
            (
                "missing-base-ref",
                {
                    **body_only,
                    "payload": {
                        **body_only["payload"],
                        "pull_request": {
                            **body_only["payload"]["pull_request"],
                            "base": {
                                "sha": body_only["payload"]["pull_request"]["base"]["sha"],
                            },
                        },
                    },
                },
                EMITTED_FULL_CHECKS,
            ),
            (
                "empty-base-ref",
                {
                    **body_only,
                    "payload": {
                        **body_only["payload"],
                        "pull_request": {
                            **body_only["payload"]["pull_request"],
                            "base": {
                                **body_only["payload"]["pull_request"]["base"],
                                "ref": "",
                            },
                        },
                    },
                },
                EMITTED_FULL_CHECKS,
            ),
            (
                "missing-base-sha",
                {
                    **body_only,
                    "payload": {
                        **body_only["payload"],
                        "pull_request": {
                            **body_only["payload"]["pull_request"],
                            "base": {
                                "ref": body_only["payload"]["pull_request"]["base"]["ref"],
                            },
                        },
                    },
                },
                EMITTED_FULL_CHECKS,
            ),
            (
                "missing-head-sha",
                {
                    **body_only,
                    "payload": {
                        **body_only["payload"],
                        "pull_request": {
                            **body_only["payload"]["pull_request"],
                            "head": {},
                        },
                    },
                },
                EMITTED_FULL_CHECKS,
            ),
            (
                "wrong-pr-ref",
                {
                    **body_only,
                    "runner": {
                        **body_only["runner"],
                        "github_ref": "refs/pull/999/merge",
                    },
                },
                METADATA_CHECK_CONTEXTS,
            ),
            (
                "no-body-change",
                {
                    **body_only,
                    "payload": {
                        **body_only["payload"],
                        "changes": {
                            "body": {
                                "from": body_only["payload"]["pull_request"]["body"],
                            },
                        },
                    },
                },
                EMITTED_FULL_CHECKS,
            ),
            (
                "mixed-base-and-body",
                next(
                    case
                    for case in fixture["cases"]
                    if case["id"] == "mixed-base-and-body"
                ),
                EMITTED_FULL_CHECKS,
            ),
        )
        for name, case, expected in cases:
            with self.subTest(case=name):
                self.assertEqual(_emitted_check_names(self.text, case), expected)

    def test_metadata_check_contexts_cannot_replace_candidate_contexts(self):
        fixture = json.loads(EVENT_FIXTURE.read_text(encoding="utf-8"))
        body_only = next(
            case
            for case in fixture["cases"]
            if case["id"] == "body-only-merge-sha-ignored"
        )
        base_edit = next(
            case
            for case in fixture["cases"]
            if case["id"] == "base-only-stack-retarget"
        )
        opened = next(
            case
            for case in fixture["cases"]
            if case["id"] == "stacked-opened"
        )
        jobs = _job_blocks(self.text)
        for job_name in COMBINED_WORKERS:
            with self.subTest(job=job_name):
                self.assertIsNone(_direct_job_name(jobs[job_name]))
        self.assertIn(EVENT_CLASSIFIER_DYNAMIC_NAME, jobs["event-classifier"])
        self.assertIn("    name: summary\n", jobs["summary"])
        self.assertEqual(_emitted_check_names(self.text, body_only), METADATA_CHECK_CONTEXTS)
        self.assertEqual(_emitted_check_names(self.text, base_edit), EMITTED_FULL_CHECKS)
        self.assertEqual(_emitted_check_names(self.text, opened), EMITTED_FULL_CHECKS)
        self.assertTrue(REQUIRED_BUILD_CONTEXTS <= EMITTED_FULL_CHECKS)
        self.assertEqual(
            REQUIRED_BUILD_CONTEXTS & METADATA_CHECK_CONTEXTS,
            {"build", "host-tests", "summary"},
        )
        self.assertIn(candidate_evidence.METADATA_ATTESTATION, EMITTED_FULL_CHECKS)
        self.assertIn(candidate_evidence.FULL_ATTESTATION, METADATA_CHECK_CONTEXTS)
        self.assertEqual(
            candidate_evidence.FULL_ATTESTATION,
            candidate_evidence.METADATA_ATTESTATION,
        )
        self.assertNotEqual(
            candidate_evidence.FULL_CLASSIFIER,
            candidate_evidence.METADATA_CLASSIFIER,
        )

    def test_classifier_failure_on_metadata_shaped_edit_keeps_canonical_worker_names(self):
        fixture = json.loads(EVENT_FIXTURE.read_text(encoding="utf-8"))
        body_only = next(
            case
            for case in fixture["cases"]
            if case["id"] == "body-only-merge-sha-ignored"
        )
        case = json.loads(json.dumps(body_only))
        case["classifier_result"] = "failure"
        self.assertEqual(_triggered_jobs(self.text, case), CANDIDATE_FULL_JOBS)
        self.assertEqual(
            _emitted_check_names(self.text, case),
            METADATA_CLASSIFIER_FAILURE_CHECKS,
        )

    def test_worker_name_overrides_fail_closed(self):
        stale_dynamic_name = (
            "${{ needs.event-classifier.outputs.classification == 'metadata-only' "
            "&& 'attacker-host-tests' || 'host-tests' }}"
        )
        mutations = [
            ("canonical-name", "    name: host-tests\n"),
            ("stale-adapter-label", "    name: attacker-host-tests\n"),
            ("dynamic-expression", f"    name: {stale_dynamic_name}\n"),
            (
                "duplicate-name-keys",
                "    name: host-tests\n"
                f"    name: {stale_dynamic_name}\n",
            ),
        ]
        for name, injected in mutations:
            with self.subTest(mutation=name):
                changed = self.text.replace(
                    "  host-tests:\n",
                    "  host-tests:\n" + injected,
                    1,
                )
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "host-tests direct job mapping differs" in error
                        or "host-tests uses unsupported" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_metadata_adapter_raw_event_checks_are_required(self):
        mutations = (
            (
                "remove-event-path-read",
                'event_path = env("GITHUB_EVENT_PATH", max_bytes=MAX_EVENT_PATH_BYTES)',
                "event_path = '/dev/null'",
            ),
            (
                "remove-lstat-check",
                "metadata = os.lstat(event_path)",
                "metadata = os.stat(event_path)",
            ),
            (
                "remove-regular-file-check",
                'if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):',
                "if False:",
            ),
            (
                "remove-owner-check",
                'if metadata.st_uid != os.getuid():',
                "if False:",
            ),
            (
                "remove-size-check",
                'if metadata.st_size > MAX_EVENT_BYTES:',
                "if False:",
            ),
            (
                "remove-base-ref-grammar-check",
                "if not is_git_branch_ref(base_ref):",
                "if False:",
            ),
            (
                "remove-current-field-presence-check",
                "if name not in pull_request:",
                "if False:",
            ),
            (
                "weaken-title-whitespace-check",
                "if not text.strip():",
                "if False:",
            ),
            (
                "remove-nofollow-open",
                'getattr(os, "O_NOFOLLOW", 0)',
                "0",
            ),
            (
                "remove-pre-read-race-check",
                'if file_signature(opened) != file_signature(metadata):',
                "if False:",
            ),
            (
                "remove-post-read-race-check",
                'if file_signature(final) != file_signature(opened) or len(raw) != opened.st_size:',
                "if False:",
            ),
            (
                "weaken-duplicate-json-check",
                "object_pairs_hook=reject_duplicates",
                "object_pairs_hook=None",
            ),
            (
                "weaken-change-key-check",
                "if change_keys not in ALLOWED_CHANGE_KEYS:",
                "if False:",
            ),
            (
                "weaken-change-difference-check",
                "if previous == current:",
                "if False:",
            ),
        )
        for name, old, new in mutations:
            with self.subTest(mutation=name):
                changed = self.text.replace(old, new, 1)
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "host-tests protected pre-pilot step sequence differs" in error
                        or "build metadata continuity adapter differs" in error
                        for error in _errors(changed, False)
                    )
                )
        with self.subTest(mutation="uniform-python-heredoc-indent"):
            host_job = _job_blocks(self.text)["host-tests"]
            host_step = _step_blocks(host_job)[0]
            changed = self.text.replace(
                host_step,
                _indent_metadata_adapter_heredoc_in_step(host_step),
                1,
            )
            self.assertNotEqual(changed, self.text)
            self.assertTrue(
                any(
                    "host-tests protected pre-pilot step sequence differs" in error
                    or "build metadata continuity adapter differs" in error
                    for error in _errors(changed, False)
                )
            )
        for name, mutator in (
            (
                "raw-trailing-space-drift",
                lambda step: step.replace("        fi\n", "        fi   \n", 1),
            ),
            (
                "raw-comment-drift",
                lambda step: step.replace(
                    "        import sys\n",
                    "        import sys\n        # lexical drift\n",
                    1,
                ),
            ),
        ):
            with self.subTest(mutation=name):
                host_job = _job_blocks(self.text)["host-tests"]
                host_step = _step_blocks(host_job)[0]
                changed = self.text.replace(host_step, mutator(host_step), 1)
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "host-tests protected pre-pilot step sequence differs" in error
                        or "build metadata continuity adapter differs" in error
                        for error in _errors(changed, False)
                    )
                )
        unicode_control_mutations = (
            ("nbsp", "        /usr/bin/python3 -I - <<'PY'\n", "        /usr/bin/python3 -I - <<'PY'\u00a0\n"),
            ("em-space", "        /usr/bin/python3 -I - <<'PY'\n", "        /usr/bin/python3 -I - <<'PY'\u2003\n"),
            ("en-space", "        /usr/bin/python3 -I - <<'PY'\n", "        /usr/bin/python3 -I - <<'PY'\u2002\n"),
            ("thin-space", "        /usr/bin/python3 -I - <<'PY'\n", "        /usr/bin/python3 -I - <<'PY'\u2009\n"),
            ("ideographic-space", "        /usr/bin/python3 -I - <<'PY'\n", "        /usr/bin/python3 -I - <<'PY'\u3000\n"),
            ("zero-width-space", "        /usr/bin/python3 -I - <<'PY'\n", "        /usr/bin/python3 -I - <<'PY'\u200b\n"),
            ("bom", "        /usr/bin/python3 -I - <<'PY'\n", "\ufeff        /usr/bin/python3 -I - <<'PY'\n"),
            ("line-separator", "        /usr/bin/python3 -I - <<'PY'\n", "        /usr/bin/python3 -I - <<'PY'\u2028\n"),
            ("paragraph-separator", "        /usr/bin/python3 -I - <<'PY'\n", "        /usr/bin/python3 -I - <<'PY'\u2029\n"),
            ("carriage-return", "        /usr/bin/python3 -I - <<'PY'\n", "        /usr/bin/python3 -I - <<'PY'\r\n"),
            ("ascii-tab", "        import sys\n", "\t        import sys\n"),
            ("ascii-escape", "        import sys\n", "        import sys\x1b\n"),
            ("ascii-nul", "        import sys\n", "        import sys\x00\n"),
        )
        for name, old, new in unicode_control_mutations:
            with self.subTest(mutation=name):
                changed = self.text.replace(old, new, 1)
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "host-tests protected pre-pilot step sequence differs" in error
                        or "build metadata continuity adapter differs" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_metadata_adapter_parsed_contract_rejects_extra_shell_and_python_behavior(self):
        mutations = (
            (
                "extra-python-command",
                '        PY\n',
                '        PY\n        /usr/bin/python3 -c "pass"\n',
            ),
            (
                "extra-curl-command",
                '        fi\n        /usr/bin/python3 -I - <<\'PY\'\n',
                '        fi\n        /usr/bin/curl https://example.invalid\n'
                "        /usr/bin/python3 -I - <<'PY'\n",
            ),
            (
                "extra-touch-command",
                '        fi\n        /usr/bin/python3 -I - <<\'PY\'\n',
                '        fi\n        /usr/bin/touch "$GITHUB_EVENT_PATH"\n'
                "        /usr/bin/python3 -I - <<'PY'\n",
            ),
            (
                "extra-shell-dead-branch",
                '        fi\n        /usr/bin/python3 -I - <<\'PY\'\n',
                "        fi\n"
                "        if false; then\n"
                "          /usr/bin/curl https://example.invalid\n"
                "        fi\n"
                "        /usr/bin/python3 -I - <<'PY'\n",
            ),
            (
                "extra-python-import",
                "        import sys\n",
                "        import sys\n        import socket\n",
            ),
            (
                "extra-python-call",
                "        payload = load_event_payload()\n",
                "        payload = load_event_payload()\n        json.dumps({})\n",
            ),
            (
                "extra-python-dead-branch",
                "        payload = load_event_payload()\n",
                "        payload = load_event_payload()\n"
                "        if False:\n"
                "            json.dumps({})\n",
            ),
            (
                "extra-python-unreachable-expression",
                '        if "title" in changes:\n            validate_change("title")\n',
                '        if "title" in changes:\n            validate_change("title")\n'
                "        0\n",
            ),
            (
                "unquoted-heredoc-introducer",
                "        /usr/bin/python3 -I - <<'PY'\n",
                "        /usr/bin/python3 -I - <<PY\n",
            ),
            (
                "double-quoted-heredoc-introducer",
                "        /usr/bin/python3 -I - <<'PY'\n",
                '        /usr/bin/python3 -I - <<"PY"\n',
            ),
            (
                "escaped-heredoc-introducer",
                "        /usr/bin/python3 -I - <<'PY'\n",
                "        /usr/bin/python3 -I - <<\\PY\n",
            ),
            (
                "dash-heredoc-introducer",
                "        /usr/bin/python3 -I - <<'PY'\n",
                "        /usr/bin/python3 -I - <<-'PY'\n",
            ),
            (
                "backslash-space",
                '        if [ "$CLASSIFIER_RESULT" != "success" ] || \\\n',
                '        if [ "$CLASSIFIER_RESULT" != "success" ] || \\ \n',
            ),
            (
                "backslash-tab",
                '           [ "$FALLBACK_IDENTITY_RESULT" != "success" ] || \\\n',
                '           [ "$FALLBACK_IDENTITY_RESULT" != "success" ] || \\\t\n',
            ),
            (
                "backslash-trailing-spaces",
                '           [ "$GITHUB_EVENT_NAME" != "pull_request" ] || \\\n',
                '           [ "$GITHUB_EVENT_NAME" != "pull_request" ] || \\  \n',
            ),
        )
        for name, old, new in mutations:
            with self.subTest(mutation=name):
                changed = self.text.replace(old, new, 1)
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "host-tests protected pre-pilot step sequence differs" in error
                        or "build metadata continuity adapter differs" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_metadata_adapter_runtime_requires_exact_raw_edited_event(self):
        scripts = _metadata_adapter_scripts(self.text)
        self.assertEqual(len(set(scripts.values())), 1)
        self.assertEqual(
            len({_metadata_adapter_python_source(script) for script in scripts.values()}),
            1,
        )
        base_env = {
            "CLASSIFICATION": "metadata-only",
            "CLASSIFIED_BASE_SHA": "2" * 40,
            "CLASSIFIED_BUILD_SHA": "1" * 40,
            "CLASSIFIER_RESULT": "success",
            "EXPECTED_BUILD_SHA": "1" * 40,
            "FALLBACK_IDENTITY_RESULT": "success",
            "FALLBACK_KIND": "pull_request",
            "FALLBACK_SHA": "1" * 40,
            "FULL_FALLBACK": "false",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REF": "refs/pull/177/merge",
            "HEAD_VALID": "true",
            "IDENTITY_VALID": "true",
            "RUN_EXPENSIVE": "false",
        }
        large_body = '\\"' * 120000
        self.assertGreater(len(json.dumps(large_body).encode("utf-8")), 131072)
        large_payload = _metadata_adapter_payload(body=large_body)
        large_payload_raw = _metadata_adapter_payload_bytes(large_payload)
        self.assertLessEqual(len(large_payload_raw), event_classifier.MAX_EVENT_BYTES)
        missing_current_body = _metadata_adapter_payload()
        del missing_current_body["pull_request"]["body"]
        missing_current_title = _metadata_adapter_payload(
            body="Stable body",
            changes={"title": {"from": "Old title"}},
        )
        del missing_current_title["pull_request"]["title"]
        cases = (
            {
                "name": "valid-body",
                "payload": _metadata_adapter_payload(),
                "expected": 0,
            },
            {
                "name": "valid-title",
                "payload": _metadata_adapter_payload(
                    body="Stable body",
                    changes={"title": {"from": "Old title"}},
                ),
                "expected": 0,
            },
            {
                "name": "valid-both",
                "payload": _metadata_adapter_payload(
                    changes={
                        "body": {"from": "Old body"},
                        "title": {"from": "Old title"},
                    }
                ),
                "expected": 0,
            },
            {
                "name": "valid-null-body",
                "payload": _metadata_adapter_payload(body=None),
                "expected": 0,
            },
            {
                "name": "valid-large-body-file",
                "payload": large_payload,
                "raw": large_payload_raw,
                "expected": 0,
            },
            {
                "name": "missing-event-path",
                "payload": _metadata_adapter_payload(),
                "env_overrides": {"GITHUB_EVENT_PATH": None},
                "expected": 1,
                "error": "missing GITHUB_EVENT_PATH",
            },
            {
                "name": "wrong-event-name",
                "payload": _metadata_adapter_payload(),
                "env_overrides": {"GITHUB_EVENT_NAME": "push"},
                "expected": 1,
                "error": "not authoritative",
            },
            {
                "name": "wrong-action",
                "payload": _metadata_adapter_payload(action="opened"),
                "expected": 1,
                "error": "not an edited pull_request",
            },
            {
                "name": "wrong-ref",
                "payload": _metadata_adapter_payload(),
                "env_overrides": {"GITHUB_REF": "refs/pull/178/merge"},
                "expected": 1,
                "error": "ref is invalid",
            },
            {
                "name": "wrong-number",
                "payload": _metadata_adapter_payload(number=0),
                "env_overrides": {"GITHUB_REF": "refs/pull/0/merge"},
                "expected": 1,
                "error": "PR number is invalid",
            },
            {
                "name": "missing-pull-request",
                "payload": {
                    "action": "edited",
                    "changes": {"body": {"from": "Old body"}},
                    "number": 177,
                },
                "expected": 1,
                "error": "pull_request is invalid",
            },
            {
                "name": "missing-current-body",
                "payload": missing_current_body,
                "expected": 1,
                "error": "pull_request.body is missing",
            },
            {
                "name": "missing-current-title",
                "payload": missing_current_title,
                "expected": 1,
                "error": "pull_request.title is missing",
            },
            {
                "name": "empty-changes",
                "payload": _metadata_adapter_payload(changes={}),
                "expected": 1,
                "error": "changes must be exactly body/title only",
            },
            {
                "name": "invalid-base-ref",
                "payload": _metadata_adapter_payload(base_ref="bad ref"),
                "expected": 1,
                "error": "base ref is invalid",
            },
            {
                "name": "lone-at-base-ref",
                "payload": _metadata_adapter_payload(base_ref="@"),
                "expected": 1,
                "error": "base ref is invalid",
            },
            {
                "name": "base-change",
                "payload": _metadata_adapter_payload(
                    changes={"base": {"from": {"ref": "topic", "sha": "3" * 40}}}
                ),
                "expected": 1,
                "error": "changes must be exactly body/title only",
            },
            {
                "name": "extra-change",
                "payload": _metadata_adapter_payload(
                    changes={
                        "body": {"from": "Old body"},
                        "draft": {"from": False},
                    }
                ),
                "expected": 1,
                "error": "changes must be exactly body/title only",
            },
            {
                "name": "same-old-current",
                "payload": _metadata_adapter_payload(
                    body="Same body",
                    changes={"body": {"from": "Same body"}},
                ),
                "expected": 1,
                "error": "body did not change",
            },
            {
                "name": "duplicate-change-keys",
                "raw": (
                    b'{"action":"edited","changes":{"body":{"from":"Old body"},'
                    b'"body":{"from":"Older body"}},"number":177,'
                    b'"pull_request":{"base":{"ref":"master","sha":"'
                    + b"2" * 40
                    + b'"},"body":"New body","head":{"sha":"'
                    + b"1" * 40
                    + b'"},"title":"New title"}}\n'
                ),
                "expected": 1,
                "error": "JSON repeats a key",
            },
            {
                "name": "malformed-json",
                "raw": b'{"action":"edited"\n',
                "expected": 1,
                "error": "payload is not valid JSON",
            },
            {
                "name": "trailing-garbage",
                "raw": _metadata_adapter_payload_bytes(_metadata_adapter_payload()) + b"x",
                "expected": 1,
                "error": "payload is not valid JSON",
            },
            {
                "name": "nan-json",
                "raw": (
                    b'{"action":"edited","changes":{"body":{"from":"Old body"}},'
                    b'"number":177,"pull_request":{"base":{"ref":"master","sha":"'
                    + b"2" * 40
                    + b'"},"body":"New body","head":{"sha":"'
                    + b"1" * 40
                    + b'"},"title":"New title"},"unused":NaN}\n'
                ),
                "expected": 1,
                "error": "contains non-finite number",
            },
            {
                "name": "infinity-json",
                "raw": (
                    b'{"action":"edited","changes":{"body":{"from":"Old body"}},'
                    b'"number":177,"pull_request":{"base":{"ref":"master","sha":"'
                    + b"2" * 40
                    + b'"},"body":"New body","head":{"sha":"'
                    + b"1" * 40
                    + b'"},"title":"New title"},"unused":Infinity}\n'
                ),
                "expected": 1,
                "error": "contains non-finite number",
            },
            {
                "name": "negative-infinity-json",
                "raw": (
                    b'{"action":"edited","changes":{"body":{"from":"Old body"}},'
                    b'"number":177,"pull_request":{"base":{"ref":"master","sha":"'
                    + b"2" * 40
                    + b'"},"body":"New body","head":{"sha":"'
                    + b"1" * 40
                    + b'"},"title":"New title"},"unused":-Infinity}\n'
                ),
                "expected": 1,
                "error": "contains non-finite number",
            },
            {
                "name": "overflow-float-json",
                "raw": (
                    b'{"action":"edited","changes":{"body":{"from":"Old body"}},'
                    b'"number":177,"pull_request":{"base":{"ref":"master","sha":"'
                    + b"2" * 40
                    + b'"},"body":"New body","head":{"sha":"'
                    + b"1" * 40
                    + b'"},"title":"New title"},"unused":1e999}\n'
                ),
                "expected": 1,
                "error": "float overflows",
            },
            {
                "name": "nested-nonfinite-unknown-field",
                "raw": (
                    b'{"action":"edited","changes":{"body":{"from":"Old body"}},'
                    b'"number":177,"pull_request":{"base":{"ref":"master","sha":"'
                    + b"2" * 40
                    + b'"},"body":"New body","head":{"sha":"'
                    + b"1" * 40
                    + b'"},"title":"New title"},'
                    b'"unused":{"nested":[0,{"bad":NaN}]}}\n'
                ),
                "expected": 1,
                "error": "contains non-finite number",
            },
            {
                "name": "oversized-payload",
                "raw": b" " * (event_classifier.MAX_EVENT_BYTES + 1),
                "expected": 1,
                "error": "payload exceeds 1 MiB",
            },
            {
                "name": "symlink-payload",
                "payload": _metadata_adapter_payload(),
                "path_kind": "symlink",
                "expected": 1,
                "error": "payload must be a regular file",
            },
            {
                "name": "fifo-payload",
                "path_kind": "fifo",
                "expected": 1,
                "error": "payload must be a regular file",
            },
            {
                "name": "device-payload",
                "path_kind": "device",
                "expected": 1,
                "error": "payload must be a regular file",
            },
        )
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="workflow-metadata-adapter-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            for job_name, script in scripts.items():
                for case in cases:
                    with self.subTest(job=job_name, case=case["name"]):
                        event_path = sandbox / f"{job_name}-{case['name']}.json"
                        path_kind = case.get("path_kind", "file")
                        raw = case.get("raw")
                        if raw is None and "payload" in case:
                            raw = _metadata_adapter_payload_bytes(case["payload"])
                        if path_kind == "symlink":
                            target = sandbox / f"{job_name}-{case['name']}-target.json"
                            target.write_bytes(raw)
                            event_path.unlink(missing_ok=True)
                            event_path.symlink_to(target)
                        elif path_kind == "fifo":
                            event_path.unlink(missing_ok=True)
                            os.mkfifo(event_path)
                        elif path_kind == "device":
                            event_path = Path("/dev/null")
                        else:
                            event_path.write_bytes(raw)
                        env = {**os.environ, **base_env}
                        env["GITHUB_EVENT_PATH"] = str(event_path)
                        for key, value in case.get("env_overrides", {}).items():
                            if value is None:
                                env.pop(key, None)
                            else:
                                env[key] = value
                        completed = subprocess.run(
                            ["/bin/bash", "-c", script],
                            cwd=ROOT,
                            env=env,
                            check=False,
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        self.assertEqual(
                            completed.returncode,
                            case["expected"],
                            completed.stderr,
                        )
                        if case.get("error") is not None:
                            self.assertIn(case["error"], completed.stderr)

    def test_metadata_adapters_match_classifier_on_fixture_pull_request_cases(self):
        scripts = _metadata_adapter_scripts(self.text)
        self.assertEqual(len(set(scripts.values())), 1)
        fixture = json.loads(EVENT_FIXTURE.read_text(encoding="utf-8"))
        templates = {
            case["id"]: case
            for case in fixture["cases"]
            if case["event_name"] == "pull_request"
        }
        cases = [copy.deepcopy(case) for case in templates.values()]

        missing_current_body = copy.deepcopy(templates["body-only-merge-sha-ignored"])
        missing_current_body["id"] = "body-only-missing-current-body"
        del missing_current_body["payload"]["pull_request"]["body"]
        cases.append(missing_current_body)

        missing_current_title = copy.deepcopy(templates["title-only"])
        missing_current_title["id"] = "title-only-missing-current-title"
        del missing_current_title["payload"]["pull_request"]["title"]
        cases.append(missing_current_title)

        overlength_ref = {
            "accepted": False,
            "id": "overlength",
            "ref": "a" * (event_classifier.MAX_BRANCH_REF_BYTES + 1),
        }
        for template_id in ("body-only-merge-sha-ignored", "title-only"):
            for ref_case in (*fixture["base_ref_validation_cases"], overlength_ref):
                mutated = copy.deepcopy(templates[template_id])
                mutated["id"] = f"{template_id}-base-ref-{ref_case['id']}"
                mutated["payload"]["pull_request"]["base"]["ref"] = ref_case["ref"]
                cases.append(mutated)

        numeric_template = templates["body-only-merge-sha-ignored"]
        numeric_runner = copy.deepcopy(numeric_template["runner"])
        numeric_cases = (
            (
                "nan",
                b'{"action":"edited","changes":{"body":{"from":"old evidence"}},'
                b'"number":177,"pull_request":{"base":{"ref":"master","sha":"'
                + b"2" * 40
                + b'"},"body":"new evidence","head":{"sha":"'
                + b"1" * 40
                + b'"},"title":"Implement issue 177"},"unused":NaN}\n',
            ),
            (
                "infinity",
                b'{"action":"edited","changes":{"body":{"from":"old evidence"}},'
                b'"number":177,"pull_request":{"base":{"ref":"master","sha":"'
                + b"2" * 40
                + b'"},"body":"new evidence","head":{"sha":"'
                + b"1" * 40
                + b'"},"title":"Implement issue 177"},"unused":Infinity}\n',
            ),
            (
                "negative-infinity",
                b'{"action":"edited","changes":{"body":{"from":"old evidence"}},'
                b'"number":177,"pull_request":{"base":{"ref":"master","sha":"'
                + b"2" * 40
                + b'"},"body":"new evidence","head":{"sha":"'
                + b"1" * 40
                + b'"},"title":"Implement issue 177"},"unused":-Infinity}\n',
            ),
            (
                "overflow-float",
                b'{"action":"edited","changes":{"body":{"from":"old evidence"}},'
                b'"number":177,"pull_request":{"base":{"ref":"master","sha":"'
                + b"2" * 40
                + b'"},"body":"new evidence","head":{"sha":"'
                + b"1" * 40
                + b'"},"title":"Implement issue 177"},"unused":1e999}\n',
            ),
            (
                "nested-unknown-nonfinite",
                b'{"action":"edited","changes":{"body":{"from":"old evidence"}},'
                b'"number":177,"pull_request":{"base":{"ref":"master","sha":"'
                + b"2" * 40
                + b'"},"body":"new evidence","head":{"sha":"'
                + b"1" * 40
                + b'"},"title":"Implement issue 177"},'
                b'"unused":{"nested":[0,{"bad":NaN}]}}\n',
            ),
        )
        for case_id, raw in numeric_cases:
            cases.append(
                {
                    "event_name": "pull_request",
                    "id": f"body-only-{case_id}",
                    "payload": copy.deepcopy(numeric_template["payload"]),
                    "raw": raw,
                    "runner": numeric_runner,
                }
            )

        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="workflow-metadata-adapter-fixture-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            for case in cases:
                payload = copy.deepcopy(case["payload"])
                match = re.fullmatch(
                    r"refs/pull/([1-9][0-9]*)/merge",
                    case["runner"]["github_ref"],
                )
                payload.setdefault(
                    "number",
                    int(match.group(1)) if match is not None else 177,
                )
                raw = case.get("raw", _metadata_adapter_payload_bytes(payload))
                for job_name, script in scripts.items():
                    with self.subTest(
                        case=case["id"],
                        job=job_name,
                    ):
                        event_path = sandbox / f"{job_name}-{case['id']}.json"
                        event_path.write_bytes(raw)
                        try:
                            loaded = event_classifier.load_event(event_path)
                        except event_classifier.EventClassificationError as error:
                            expected = 1
                            decision_label = f"loader-error:{error}"
                        else:
                            decision = event_classifier.classify_event(
                                case["event_name"],
                                loaded,
                                github_ref=case["runner"]["github_ref"],
                                github_sha=case["runner"]["github_sha"],
                                pr_base_sha=case["runner"]["pr_base_sha"],
                                pr_head_sha=case["runner"]["pr_head_sha"],
                                push_sha=case["runner"]["push_sha"],
                            )
                            expected = (
                                0 if decision.classification == "metadata-only" else 1
                            )
                            decision_label = (
                                f"{decision.classification}/{decision.reason}"
                            )
                        completed = subprocess.run(
                            ["/bin/bash", "-c", script],
                            cwd=ROOT,
                            env={
                                **os.environ,
                                **_metadata_adapter_env_for_case(case, event_path),
                            },
                            check=False,
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        self.assertEqual(
                            completed.returncode,
                            expected,
                            (
                                f"{case['id']} => {decision_label}\n"
                                f"{completed.stderr}"
                            ),
                        )

    def test_metadata_adapter_event_file_loader_rejects_races(self):
        scripts = _metadata_adapter_scripts(self.text)
        self.assertEqual(len(set(scripts.values())), 1)
        source = _metadata_adapter_python_source(next(iter(scripts.values())))
        payload = _metadata_adapter_payload_bytes(_metadata_adapter_payload())
        current_uid = os.getuid()

        def regular_file_stat(*, ino: int, size: int, mtime_ns: int, ctime_ns: int):
            return types.SimpleNamespace(
                st_ctime_ns=ctime_ns,
                st_dev=11,
                st_ino=ino,
                st_mode=stat.S_IFREG | 0o600,
                st_mtime_ns=mtime_ns,
                st_size=size,
                st_uid=current_uid,
            )

        base_env = _metadata_adapter_python_env("/virtual/event.json")

        with self.subTest(case="changed-before-read"):
            read = mock.Mock(side_effect=AssertionError("os.read must not run"))
            with (
                mock.patch("os.getuid", return_value=current_uid),
                mock.patch(
                    "os.lstat",
                    return_value=regular_file_stat(
                        ino=1,
                        size=len(payload),
                        mtime_ns=10,
                        ctime_ns=20,
                    ),
                ),
                mock.patch("os.open", return_value=9),
                mock.patch(
                    "os.fstat",
                    return_value=regular_file_stat(
                        ino=2,
                        size=len(payload),
                        mtime_ns=10,
                        ctime_ns=20,
                    ),
                ),
                mock.patch("os.read", read),
                mock.patch("os.close"),
            ):
                code, stderr = _run_metadata_adapter_python_source(source, base_env)
            self.assertEqual(code, 1)
            self.assertIn("payload changed before read", stderr)
            read.assert_not_called()

        with self.subTest(case="changed-while-read"):
            with (
                mock.patch("os.getuid", return_value=current_uid),
                mock.patch(
                    "os.lstat",
                    return_value=regular_file_stat(
                        ino=3,
                        size=len(payload),
                        mtime_ns=30,
                        ctime_ns=40,
                    ),
                ),
                mock.patch("os.open", return_value=10),
                mock.patch(
                    "os.fstat",
                    side_effect=[
                        regular_file_stat(
                            ino=3,
                            size=len(payload),
                            mtime_ns=30,
                            ctime_ns=40,
                        ),
                        regular_file_stat(
                            ino=3,
                            size=len(payload),
                            mtime_ns=31,
                            ctime_ns=41,
                        ),
                    ],
                ),
                mock.patch("os.read", side_effect=[payload, b""]),
                mock.patch("os.close"),
            ):
                code, stderr = _run_metadata_adapter_python_source(source, base_env)
            self.assertEqual(code, 1)
            self.assertIn("payload changed while being read", stderr)

    def test_classifier_failure_fixtures_select_only_exact_event_head_fallbacks(self):
        fixture = json.loads(EVENT_FIXTURE.read_text(encoding="utf-8"))
        infrastructure = set(fixture["infrastructure_jobs"])
        for case in fixture["classifier_failure_cases"]:
            with self.subTest(case=case["id"]):
                expected_jobs = set(case["expected_jobs"])
                if expected_jobs:
                    expected_jobs |= infrastructure
                self.assertEqual(
                    _triggered_jobs(self.text, case),
                    expected_jobs,
                )
                self.assertFalse(case["expected_summary_success"])
                if set(COMBINED_WORKERS) <= set(case["expected_jobs"]):
                    raw_head = (
                        case["runner"]["pr_head_sha"]
                        if case["event_name"] == "pull_request"
                        else case["runner"]["github_sha"]
                    )
                    self.assertEqual(
                        case["expected_worker_head"],
                        raw_head,
                    )
                    self.assertTrue(case["expected_worker_head"])
                    if case["event_name"] == "pull_request":
                        self.assertNotEqual(
                            case["expected_worker_head"],
                            case["runner"]["github_sha"],
                        )
                    else:
                        self.assertEqual(case["runner"]["pr_head_sha"], "")
                else:
                    self.assertEqual(case["expected_worker_head"], "")

        self.assertIn(
            "needs.event-classifier.result == 'failure'",
            EXPECTED_BUILD_SHA_EXPRESSION,
        )
        self.assertIn(
            "needs.event-identity.outputs.fallback_sha",
            EXPECTED_BUILD_SHA_EXPRESSION,
        )
        self.assertNotIn("github.event.pull_request.head.sha", EXPECTED_BUILD_SHA_EXPRESSION)
        self.assertNotIn("github.sha", EXPECTED_BUILD_SHA_EXPRESSION)

    def test_fallback_identity_fixtures_gate_workers_and_publisher(self):
        fixture = json.loads(EVENT_FIXTURE.read_text(encoding="utf-8"))
        identity_step = _step_blocks(_job_blocks(self.text)["event-identity"])[0]
        script = _literal_run_script(identity_step)
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="workflow-fallback-identity-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            for case in fixture["fallback_identity_cases"]:
                with self.subTest(case=case["id"]):
                    event_name = case["event_name"]
                    pr_head_sha = case.get("pr_head_sha", "")
                    pr_base_sha = case.get(
                        "pr_base_sha",
                        "2" * 40 if event_name == "pull_request" else "",
                    )
                    push_sha = case.get("push_sha", "")
                    raw_sha = case.get("raw_sha", "a" * 40)
                    payload = (
                        {
                            "action": "edited",
                            "pull_request": {
                                "base": {"ref": "master", "sha": pr_base_sha},
                                "head": {"sha": pr_head_sha},
                            },
                        }
                        if event_name == "pull_request"
                        else {
                            "after": push_sha,
                            "before": "3" * 40,
                            "ref": case["github_ref"],
                        }
                    )
                    event = {
                        "classifier_result": "failure",
                        "event_name": event_name,
                        "payload": payload,
                        "runner": {
                            "github_ref": case["github_ref"],
                            "github_sha": raw_sha,
                            "pr_base_sha": pr_base_sha,
                            "pr_head_sha": pr_head_sha,
                            "pr_number": case.get("pr_number", 177),
                            "push_sha": push_sha,
                        },
                    }
                    selected = _triggered_jobs(self.text, event)
                    expected_jobs = {
                        "event-identity",
                        "event-router",
                        "event-classifier",
                        "summary",
                    }
                    if case["run_workers"]:
                        expected_jobs.update(COMBINED_WORKERS)
                    if case["run_publisher"]:
                        expected_jobs.add("patch-release")
                    self.assertEqual(selected, expected_jobs)
                    self.assertEqual(
                        set(COMBINED_WORKERS) <= selected,
                        case["run_workers"],
                    )
                    self.assertEqual(
                        "patch-release" in selected,
                        case["run_publisher"],
                    )

                    output = sandbox / f"{case['id']}.out"
                    completed = subprocess.run(
                        ["/bin/bash", "-c", script],
                        cwd=sandbox,
                        env={
                            **os.environ,
                            "DEFAULT_BRANCH": case.get("default_branch", "master"),
                            "EVENT_NAME": event_name,
                            "EVENT_REF": case["github_ref"],
                            "GITHUB_OUTPUT": str(output),
                            "PR_BASE_SHA": pr_base_sha,
                            "PR_BASE_SHA_JSON": json.dumps(
                                pr_base_sha
                                if event_name == "pull_request"
                                else None
                            ),
                            "PR_HEAD_SHA": pr_head_sha,
                            "PR_HEAD_SHA_JSON": json.dumps(
                                pr_head_sha if event_name == "pull_request" else None
                            ),
                            "PR_NUMBER": (
                                str(case.get("pr_number", 177))
                                if event_name == "pull_request"
                                else ""
                            ),
                            "PR_NUMBER_JSON": (
                                json.dumps(case.get("pr_number", 177))
                                if event_name == "pull_request"
                                else "null"
                            ),
                            "PUSH_SHA": push_sha,
                            "PUSH_SHA_JSON": json.dumps(
                                push_sha if event_name == "push" else None
                            ),
                            "RAW_SHA": raw_sha,
                            "RAW_SHA_JSON": json.dumps(raw_sha),
                        },
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    outputs = dict(
                        line.split("=", 1)
                        for line in output.read_text(encoding="ascii").splitlines()
                    )
                    self.assertEqual(outputs["fallback_kind"], case["expected_kind"])
                    self.assertEqual(outputs["fallback_sha"], case["expected_sha"])
                    expected_classifier_ref = case.get(
                        "expected_classifier_ref",
                        pr_base_sha
                        if event_name == "pull_request"
                        else case["expected_sha"]
                        if case["run_publisher"]
                        else "",
                    )
                    self.assertEqual(
                        outputs["classifier_ref"],
                        expected_classifier_ref,
                    )
                    self.assertEqual(
                        outputs["classifier_available"],
                        str(
                            case.get(
                                "expected_classifier_available",
                                bool(expected_classifier_ref),
                            )
                        ).lower(),
                    )
                    self.assertNotEqual(outputs["fallback_sha"], "refs/heads/attacker")
                    if "attacker" in push_sha:
                        self.assertNotEqual(outputs["classifier_ref"], push_sha)

    def test_successful_classification_requires_coherent_event_identity(self):
        fixture = json.loads(EVENT_FIXTURE.read_text(encoding="utf-8"))
        pr_template = next(
            case
            for case in fixture["cases"]
            if case["id"] == "body-only-merge-sha-ignored"
        )
        push_template = next(
            case for case in fixture["cases"] if case["id"] == "master-push"
        )
        for identity in fixture["successful_identity_cases"]:
            with self.subTest(case=identity["id"]):
                case = json.loads(
                    json.dumps(
                        push_template
                        if identity["event_name"] == "push"
                        else pr_template
                    )
                )
                if identity["event_name"] == "pull_request":
                    case["payload"]["action"] = identity["action"]
                    if identity["action"] != "edited":
                        case["payload"].pop("changes", None)
                    case["runner"]["github_ref"] = identity["github_ref"]
                    case["runner"]["pr_number"] = identity["pr_number"]
                selected = _triggered_jobs(self.text, case)
                expected = {
                    "event-identity",
                    "event-router",
                    "event-classifier",
                    "summary",
                }
                if identity["run_workers"]:
                    expected.update(COMBINED_WORKERS)
                if identity["run_publisher"]:
                    expected.add("patch-release")
                if (
                    identity["expected_classification"] == "metadata-only"
                    and identity["expected_summary_success"]
                ):
                    expected.update(METADATA_ADAPTER_JOBS)
                self.assertEqual(selected, expected)

                decision = event_classifier.classify_event(
                    case["event_name"],
                    case["payload"],
                    github_ref=case["runner"]["github_ref"],
                    github_sha=case["runner"]["github_sha"],
                    pr_base_sha=case["runner"]["pr_base_sha"],
                    pr_head_sha=case["runner"]["pr_head_sha"],
                    push_sha=case["runner"]["push_sha"],
                )
                self.assertEqual(
                    decision.classification,
                    identity["expected_classification"],
                )
                self.assertEqual(
                    identity["expected_summary_success"],
                    identity["id"]
                    in {
                        "valid-full-pr-identity",
                        "valid-metadata-pr-identity",
                        "valid-successful-push-identity",
                    },
                )

    def test_edited_base_change_reruns_exact_head_candidate_without_publisher(self):
        unchanged_head = "4" * 40
        event = {
            "event_name": "pull_request",
            "action": "edited",
            "changes": {
                "base": {
                    "ref": {
                        "from": "agent/issue-170",
                    },
                    "sha": {
                        "from": "3" * 40,
                    },
                },
            },
            "pull_request": {
                "base": {"ref": "master", "sha": "2" * 40},
                "head": {"sha": unchanged_head},
            },
        }
        self.assertEqual(
            _triggered_jobs(self.text, event),
            CANDIDATE_FULL_JOBS,
        )
        self.assertNotIn("patch-release", _triggered_jobs(self.text, event))

    def test_parent_update_requires_a_child_head_synchronize_event(self):
        parent_push = {
            "event_name": "push",
            "ref": "refs/heads/agent/issue-170",
            "sha": "4" * 40,
        }
        self.assertEqual(_triggered_jobs(self.text, parent_push), set())

        child_synchronize = {
            "event_name": "pull_request",
            "action": "synchronize",
            "pull_request": {
                "base": {"ref": "agent/issue-170", "sha": "2" * 40},
                "head": {"sha": "5" * 40},
            },
        }
        self.assertEqual(
            _triggered_jobs(self.text, child_synchronize),
            CANDIDATE_FULL_JOBS,
        )
        self.assertNotIn("patch-release", _triggered_jobs(self.text, child_synchronize))

    def test_push_remains_master_only_and_prs_exclude_patch_release(self):
        master_push = {
            "event_name": "push",
            "ref": "refs/heads/master",
            "sha": "2" * 40,
            "after": "2" * 40,
            "before": "1" * 40,
        }
        other_push = {
            "event_name": "push",
            "ref": "refs/heads/agent/issue-170",
            "sha": "3" * 40,
        }
        self.assertEqual(
            _triggered_jobs(self.text, master_push),
            CANDIDATE_FULL_JOBS | {"patch-release"},
        )
        self.assertEqual(_triggered_jobs(self.text, other_push), set())

    def test_combined_worker_classifier_condition_is_exact(self):
        changed = self.text.replace(
            "  extended-host-tests:\n",
            f"  extended-host-tests:\n    if: {MASTER_PUBLISHER_CONDITION}\n",
            1,
        )
        self.assertTrue(
            any(
                "extended-host-tests direct job mapping differs" in error
                for error in _errors(changed, False)
            )
        )

    def test_combined_worker_expressions_are_balanced_and_fully_consumed(self):
        for job_name in COMBINED_WORKERS:
            job = _job_blocks(self.text)[job_name]
            condition = _direct_job_if(job)
            self.assertIsNone(_direct_job_name(job))
            with self.subTest(job=job_name, control="real-if"):
                self.assertEqual(
                    _github_expression_balance_errors(condition),
                    [],
                )
            for suffix, expected in (
                (")", "unmatched closing"),
                ("(", "unmatched opening"),
            ):
                changed_condition = condition[:-3] + suffix + " }}"
                with self.subTest(job=job_name, suffix=suffix, field="if"):
                    self.assertTrue(
                        any(
                            expected in error
                            for error in _github_expression_balance_errors(
                                changed_condition
                            )
                        )
                    )
                    changed_job = job.replace(
                        f"    if: {condition}",
                        f"    if: {changed_condition}",
                        1,
                    )
                    changed = self.text.replace(job, changed_job, 1)
                    self.assertTrue(
                        any(
                            f"{job_name} condition is invalid" in error
                            for error in _errors(changed, False)
                        )
                    )

    def test_classifier_authority_and_outputs_fail_closed(self):
        mutations = (
            self.text.replace(
                "      expected_head: ${{ steps.classify.outputs.expected_head }}",
                "      expected_head: attacker",
                1,
            ),
            self.text.replace(
                "      CLASSIFIER_REF: ${{ "
                "needs.event-identity.outputs.classifier_ref }}",
                "      CLASSIFIER_REF: ${{ github.sha }}",
                1,
            ),
            self.text.replace(
                "        ref: ${{ needs.event-identity.outputs.classifier_ref }}",
                "        ref: ${{ github.sha }}",
                1,
            ),
            self.text.replace("        fetch-depth: 1", "        fetch-depth: 0", 1),
            self.text.replace("      id: classify", "      id: attacker", 1),
            self.text.replace(
                "/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py "
                "classify-event",
                "python3 scripts/workflow_pilot/event_classifier.py",
                1,
            ),
            self.text.replace(
                '            echo "run_expensive=true"',
                '            echo "run_expensive=false"',
                1,
            ),
            self.text.replace(
                '/usr/bin/git check-ref-format "refs/heads/$PR_BASE_REF"',
                'test -n "$PR_BASE_REF"',
                1,
            ),
            self.text.replace(
                "${#PR_BASE_REF} -le 1024",
                "${#PR_BASE_REF} -ge 0",
                1,
            ),
            self.text.replace(
                "    name: event-router\n",
                "    name: event-router\n    permissions: write-all\n",
                1,
            ),
        )
        for changed in mutations:
            with self.subTest(mutation=changed[:180]):
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    _classifier_contract_errors(
                        _job_blocks(changed)["event-router"]
                    )
                )

    def test_trusted_fallback_identity_contract_rejects_weakened_validation(self):
        identity = _job_blocks(self.text)["event-identity"]
        for name, old, new in (
            (
                "nonempty-instead-of-sha",
                '[[ "$1" =~ ^[0-9a-f]{40}$ && "$2" = "\\"$1\\"" ]]',
                '[[ -n "$1" ]]',
            ),
            (
                "arbitrary-pr-ref",
                '"$EVENT_REF" = "refs/pull/$PR_NUMBER/merge"',
                '-n "$EVENT_REF"',
            ),
            (
                "unvalidated-pr-number",
                '[[ "$1" =~ ^[1-9][0-9]*$ && "$2" = "$1" ]]',
                '[[ -n "$1" ]]',
            ),
            (
                "mismatched-push",
                '[[ "$RAW_SHA" = "$PUSH_SHA" ]]',
                '[[ -n "$RAW_SHA" ]]',
            ),
            (
                "candidate-checkout",
                "    steps:\n",
                "    steps:\n"
                "    - uses: actions/checkout@"
                "3d3c42e5aac5ba805825da76410c181273ba90b1\n",
            ),
        ):
            with self.subTest(mutation=name):
                changed_identity = identity.replace(old, new, 1)
                self.assertNotEqual(changed_identity, identity)
                self.assertTrue(_identity_contract_errors(changed_identity))

    def test_combined_workers_require_valid_fresh_classifier_identity(self):
        for job_name in COMBINED_WORKERS:
            with self.subTest(job=job_name):
                job = _job_blocks(self.text)[job_name]
                expected_condition = (
                    HOST_BUILD_CONDITION
                    if job_name in METADATA_ADAPTER_JOBS
                    else WORKER_CONDITION
                )
                changed_job = job.replace(
                    expected_condition,
                    "${{ needs.event-classifier.outputs.run_expensive == 'true' }}",
                    1,
                )
                self.assertNotEqual(changed_job, job)
                changed = self.text.replace(job, changed_job, 1)
                self.assertTrue(
                    any(
                        f"{job_name} direct job mapping differs" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_parsed_job_timeout_map_is_exact_and_closed(self):
        expected = {
            "event-identity": 5,
            "event-router": 5,
            "event-classifier": 5,
            "host-tests": 60,
            "build": 90,
            "extended-host-tests": 60,
            "legacy": 60,
            "patch-release": 60,
            "summary": 5,
        }
        jobs = _job_blocks(self.text)
        actual = {}
        for job_name in expected:
            matches = re.findall(
                r"^    timeout-minutes: ([0-9]+)$",
                jobs[job_name],
                re.MULTILINE,
            )
            self.assertEqual(matches, [str(expected[job_name])], job_name)
            actual[job_name] = int(matches[0])
        self.assertEqual(actual, expected)

        for job_name, timeout in expected.items():
            replacement = 60 if timeout == 90 else 90 if timeout == 60 else 60
            with self.subTest(job=job_name, replacement=replacement):
                job = jobs[job_name]
                changed_job = job.replace(
                    f"    timeout-minutes: {timeout}",
                    f"    timeout-minutes: {replacement}",
                    1,
                )
                self.assertNotEqual(changed_job, job)
                changed = self.text.replace(job, changed_job, 1)
                self.assertTrue(_errors(changed, False))

    def test_combined_workers_reject_spaced_reviewed_job_keys(self):
        for job_name in COMBINED_WORKERS:
            with self.subTest(job=job_name):
                job = _job_blocks(self.text)[job_name]
                changed_job = job.replace("    runs-on:", "    runs-on :", 1)
                self.assertNotEqual(changed_job, job)
                changed = self.text.replace(job, changed_job, 1)
                self.assertTrue(
                    any(
                        f"{job_name} direct job mapping differs" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_reviewed_job_key_aliases_fail_closed(self):
        for job_name in COMBINED_WORKERS:
            allowed = {
                "needs": "[event-identity, event-classifier]",
                "if": (
                    HOST_BUILD_CONDITION
                    if job_name in METADATA_ADAPTER_JOBS
                    else WORKER_CONDITION
                ),
                "runs-on": "ubuntu-latest",
                "timeout-minutes": "90" if job_name == "build" else "60",
                "env": "",
                "steps": "",
            }
            for field, value in allowed.items():
                escaped = f'"\\u{ord(field[0]):04x}{field[1:]}"'
                suffix = f" {value}" if value else ""
                original = f"    {field}:{suffix}"
                variants = (
                    f'    "{field}":{suffix}',
                    f"    {escaped}:{suffix}",
                    f"    !!str {field}:{suffix}",
                    f"    ? {field}\n    :{suffix}",
                    f"    {{{field}:{suffix}}}",
                )
                for variant in variants:
                    with self.subTest(
                        job=job_name,
                        field=field,
                        variant=variant,
                    ):
                        job = _job_blocks(self.text)[job_name]
                        changed_job = job.replace(original, variant, 1)
                        self.assertNotEqual(changed_job, job)
                        changed = self.text.replace(job, changed_job, 1)
                        self.assertTrue(
                            any(
                                f"{job_name} direct job mapping differs" in error
                                or f"{job_name} uses unsupported" in error
                                for error in _errors(changed, False)
                            )
                        )

    def test_every_combined_worker_rejects_spaced_advisory_or_skip_keys(self):
        for job_name in COMBINED_WORKERS:
            for field in (
                "if : ${{ false }}",
                "continue-on-error : true",
            ):
                with self.subTest(job=job_name, field=field):
                    changed = self.text.replace(
                        f"  {job_name}:\n",
                        f"  {job_name}:\n    {field}\n",
                        1,
                    )
                    self.assertTrue(
                        any(
                            f"{job_name} direct job mapping differs" in error
                            or f"{job_name} uses unsupported" in error
                            or f"{job_name} must not be advisory" in error
                            for error in _errors(changed, False)
                        )
                    )

    def test_every_combined_worker_rejects_complex_job_keys(self):
        variants = (
            '"if": ${{ false }}',
            '"continue-\\u006fn-error": true',
            "? if\n    : ${{ false }}",
            "!!str continue-on-error: true",
            "{if: false, continue-on-error: true}",
        )
        for job_name, variant in zip(
            COMBINED_WORKERS,
            variants[: len(COMBINED_WORKERS)],
        ):
            with self.subTest(job=job_name, variant=variant):
                changed = self.text.replace(
                    f"  {job_name}:\n",
                    f"  {job_name}:\n    {variant}\n",
                    1,
                )
                self.assertTrue(
                    any(
                        f"{job_name} uses unsupported direct mapping-key syntax"
                        in error
                        for error in _errors(changed, False)
                    )
                )
        changed = self.text.replace(
            "  host-tests:\n",
            "  host-tests:\n    {if: false, continue-on-error: true}\n",
            1,
        )
        self.assertTrue(
            any(
                "host-tests uses unsupported direct mapping-key syntax" in error
                for error in _errors(changed, False)
            )
        )

    def test_every_combined_worker_has_a_closed_execution_context(self):
        execution_fields = {
            "container": "ubuntu:latest",
            "services": "{}",
            "strategy": "{matrix: {python: [3.12]}}",
            "permissions": "{contents: write}",
            "defaults": "{run: {shell: bash}}",
            "needs": "summary",
            "if": "${{ false }}",
            "continue-on-error": "true",
            "environment": "production",
            "concurrency": "attacker-controlled",
            "uses": "./untrusted-job.yml",
            "secrets": "inherit",
            "shell": "untrusted-shell {0}",
        }
        for job_name in COMBINED_WORKERS:
            timeout = "90" if job_name == "build" else "60"
            for field, value in execution_fields.items():
                with self.subTest(job=job_name, field=field):
                    changed = self.text.replace(
                        f"  {job_name}:\n",
                        f"  {job_name}:\n    {field}: {value}\n",
                        1,
                    )
                    self.assertTrue(
                        any(
                            f"{job_name} direct job mapping differs" in error
                            or f"{job_name} must " in error
                            for error in _errors(changed, False)
                        )
                    )
            for allowed_line in (
                "    runs-on: ubuntu-latest",
                f"    timeout-minutes: {timeout}",
                "    env:",
                "    steps:",
            ):
                with self.subTest(job=job_name, duplicate=allowed_line):
                    job = _job_blocks(self.text)[job_name]
                    changed_job = job.replace(
                        allowed_line,
                        f"{allowed_line}\n{allowed_line}",
                        1,
                    )
                    changed = self.text.replace(job, changed_job, 1)
                    self.assertTrue(
                        any(
                            f"{job_name} direct job mapping differs" in error
                            for error in _errors(changed, False)
                        )
                    )

            job = _job_blocks(self.text)[job_name]
            reordered = (
                job.replace(
                    "    runs-on: ubuntu-latest",
                    "    __RUNS_ON__",
                    1,
                )
                .replace(
                    f"    timeout-minutes: {timeout}",
                    "    runs-on: ubuntu-latest",
                    1,
                )
                .replace(
                    "    __RUNS_ON__",
                    f"    timeout-minutes: {timeout}",
                    1,
                )
            )
            with self.subTest(job=job_name, reordered=True):
                changed = self.text.replace(job, reordered, 1)
                self.assertTrue(
                    any(
                        f"{job_name} direct job mapping differs" in error
                        for error in _errors(changed, False)
                    )
                )

            for original, replacement in (
                ("    runs-on: ubuntu-latest", "    runs-on: self-hosted"),
                (
                    f"    timeout-minutes: {timeout}",
                    f"    timeout-minutes: {int(timeout) - 1}",
                ),
            ):
                with self.subTest(job=job_name, replacement=replacement):
                    job = _job_blocks(self.text)[job_name]
                    changed = self.text.replace(
                        job,
                        job.replace(original, replacement, 1),
                        1,
                    )
                    self.assertTrue(
                        any(
                            f"{job_name} direct job mapping differs" in error
                            for error in _errors(changed, False)
                        )
                    )

    def test_execution_context_key_syntax_bypasses_fail_closed(self):
        execution_fields = {
            "container": "ubuntu:latest",
            "services": "{}",
            "strategy": "{matrix: {python: [3.12]}}",
            "permissions": "{contents: write}",
            "defaults": "{run: {shell: bash}}",
            "needs": "summary",
            "if": "${{ false }}",
            "continue-on-error": "true",
            "environment": "production",
            "concurrency": "attacker-controlled",
            "uses": "./untrusted-job.yml",
            "secrets": "inherit",
            "shell": "untrusted-shell {0}",
        }
        for job_name in COMBINED_WORKERS:
            for field, value in execution_fields.items():
                escaped = f'"\\u{ord(field[0]):04x}{field[1:]}"'
                variants = (
                    f"{field} : {value}",
                    f'"{field}": {value}',
                    f"{escaped}: {value}",
                    f"!!str {field}: {value}",
                    f"{{{field}: {value}}}",
                    f"? {field}\n    : {value}",
                )
                for variant in variants:
                    with self.subTest(job=job_name, variant=variant):
                        changed = self.text.replace(
                            f"  {job_name}:\n",
                            f"  {job_name}:\n    {variant}\n",
                            1,
                        )
                        self.assertTrue(
                            any(
                                f"{job_name} direct job mapping differs" in error
                                or f"{job_name} uses unsupported" in error
                                or f"{job_name} must " in error
                                for error in _errors(changed, False)
                            )
                        )

    def test_missing_pull_request_trigger_fails(self):
        changed = self.text.replace(PULL_REQUEST_TRIGGER, "", 1)
        self.assertTrue(any("missing pull_request" in error for error in _errors(changed, False)))

    def test_pull_request_activity_type_mutations_fail(self):
        for actions in (
            "opened, synchronize, reopened",
            "opened, synchronize, reopened, edited, closed",
            "opened, synchronize, reopened, edited, labeled",
        ):
            with self.subTest(actions=actions):
                changed = self.text.replace(
                    "types: [opened, synchronize, reopened, edited]",
                    f"types: [{actions}]",
                    1,
                )
                self.assertTrue(
                    any("types must be opened" in error for error in _errors(changed, False))
                )

    def test_pull_request_activity_type_order_is_not_semantic(self):
        changed = self.text.replace(
            "types: [opened, synchronize, reopened, edited]",
            "types: [edited, reopened, opened, synchronize]",
            1,
        )
        self.assertEqual(_errors(changed, False), [])

    def test_missing_push_trigger_fails(self):
        changed = self.text.replace(PUSH_TRIGGER, 'push:\n    branches: [ "other" ]', 1)
        self.assertTrue(any("restricted to master" in error for error in _errors(changed, False)))

    def test_publisher_depends_only_on_trusted_event_identity(self):
        patch_release = _job_blocks(self.text)["patch-release"]
        self.assertEqual(
            re.findall(r"^    needs: (?P<value>.+)$", patch_release, re.MULTILINE),
            ["[event-identity]"],
        )

    def test_every_libpng_install_lane_declares_pkg_config(self):
        for job_name, job in _job_blocks(self.text).items():
            if "libpng-dev" not in job:
                continue
            with self.subTest(job_name=job_name):
                changed_job = job.replace(" pkg-config", "", 1)
                self.assertNotEqual(changed_job, job)
                changed = self.text.replace(job, changed_job, 1)
                self.assertTrue(
                    any(
                        f"{job_name} installs libpng-dev without pkg-config" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_build_python_dependencies_are_exactly_hash_locked(self):
        self.assertEqual(
            _hashed_requirements_errors(PYTHON_REQUIREMENTS.read_text(encoding="utf-8")),
            [],
        )

    def test_unhashed_privileged_pip_install_fails(self):
        changed = self.text.replace(
            HASHED_PIP_INSTALL,
            "python3 -m pip install ttp numpy pillow",
            1,
        )
        self.assertTrue(
            any(
                "must use the reviewed hash-locked Python requirements" in error
                for error in _errors(changed, False)
            )
        )

    def test_appended_second_pip_install_fails(self):
        changed = self.text.replace(
            HASHED_PIP_INSTALL,
            HASHED_PIP_INSTALL + " && python3 -m pip install evil",
            1,
        )
        self.assertTrue(
            any(
                "must use the reviewed hash-locked Python requirements" in error
                for error in _errors(changed, False)
            )
        )

    def test_separate_bare_or_versioned_pip_install_fails(self):
        for command in ("pip install evil", "pip3.12 install evil"):
            with self.subTest(command=command):
                changed = self.text.replace(
                    "    - name: Build tools\n",
                    f"    - run: {command}\n\n    - name: Build tools\n",
                    1,
                )
                self.assertTrue(
                    any(
                        "must use the reviewed hash-locked Python requirements" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_pip_global_options_before_install_fail(self):
        for command in (
            "python3 -m pip --isolated install evil",
            "pip --proxy https://example.invalid install evil",
        ):
            with self.subTest(command=command):
                changed = self.text.replace(
                    "    - name: Build tools\n",
                    f"    - run: {command}\n\n    - name: Build tools\n",
                    1,
                )
                self.assertTrue(
                    any(
                        "must use the reviewed hash-locked Python requirements" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_folded_block_scalar_pip_install_fails(self):
        for scalar in (">", ">-", ">+2"):
            with self.subTest(scalar=scalar):
                changed = self.text.replace(
                    "    - name: Build tools\n",
                    f"    - run: {scalar}\n"
                    "        echo preparing &&\n"
                    "        python3 -m pip --isolated install evil\n\n"
                    "    - name: Build tools\n",
                    1,
                )
                self.assertTrue(
                    any(
                        "must use the reviewed hash-locked Python requirements" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_changed_requirement_hash_fails(self):
        changed = PYTHON_REQUIREMENTS.read_text(encoding="utf-8").replace(
            EXPECTED_HASHED_REQUIREMENTS["numpy"][1],
            "sha256:" + ("0" * 64),
            1,
        )
        self.assertTrue(
            any(
                "differ from reviewed versions/hashes" in error
                for error in _hashed_requirements_errors(changed)
            )
        )

    def test_missing_summary_dependency_fails(self):
        changed = self.text.replace(
            SUMMARY_NEEDS,
            "needs: [host-tests, build, extended-host-tests]",
            1,
        )
        self.assertTrue(any("summary must depend" in error for error in _errors(changed, False)))

    def test_summary_requires_each_worker_dependency_and_result_check(self):
        for worker, changed_needs in (
            (
                "host-tests",
                "needs: [event-identity, event-classifier, build, "
                "extended-host-tests, legacy, patch-release]",
            ),
            (
                "build",
                "needs: [event-identity, event-classifier, host-tests, "
                "extended-host-tests, legacy, patch-release]",
            ),
            (
                "extended-host-tests",
                "needs: [event-identity, event-classifier, host-tests, "
                "build, legacy, patch-release]",
            ),
            (
                "legacy",
                "needs: [event-identity, event-classifier, host-tests, "
                "build, extended-host-tests, patch-release]",
            ),
            (
                "patch-release",
                "needs: [event-identity, event-classifier, host-tests, "
                "build, extended-host-tests, legacy]",
            ),
        ):
            with self.subTest(need=worker):
                changed = self.text.replace(SUMMARY_NEEDS, changed_needs, 1)
                self.assertTrue(
                    any("summary must depend" in error for error in _errors(changed, False))
                )
        final_loop = (
            'for result in "$HOST_TESTS_RESULT" "$BUILD_RESULT" '
            '"$EXTENDED_HOST_TESTS_RESULT" \\\n'
            '          "$LEGACY_RESULT"'
        )
        for missing, replacement in (
            (
                '"$HOST_TESTS_RESULT"',
                'for result in "$BUILD_RESULT" "$EXTENDED_HOST_TESTS_RESULT" \\\n'
                '          "$LEGACY_RESULT"',
            ),
            (
                '"$BUILD_RESULT"',
                'for result in "$HOST_TESTS_RESULT" "$EXTENDED_HOST_TESTS_RESULT" \\\n'
                '          "$LEGACY_RESULT"',
            ),
            (
                '"$EXTENDED_HOST_TESTS_RESULT"',
                'for result in "$HOST_TESTS_RESULT" "$BUILD_RESULT" \\\n'
                '          "$LEGACY_RESULT"',
            ),
            (
                '"$LEGACY_RESULT"',
                'for result in "$HOST_TESTS_RESULT" "$BUILD_RESULT" '
                '"$EXTENDED_HOST_TESTS_RESULT"',
            ),
        ):
            with self.subTest(result_check=missing):
                changed = self.text.replace(final_loop, replacement, 1)
                self.assertTrue(
                    any("summary loop omits" in error for error in _errors(changed, False))
                )

    def test_workflow_pilot_suite_remains_owned_by_required_host_job(self):
        host_tests = _job_blocks(self.text)["host-tests"]
        for command in (WORKFLOW_PILOT_GATE, WORKFLOW_PILOT_BASELINE_GATE):
            with self.subTest(command=command):
                self.assertTrue(
                    _contains_exact_command(
                        host_tests,
                        command,
                        if_expression=FULL_WORKER_STEP_CONDITION,
                        env_lines=SCRUBBED_STEP_ENV,
                    )
                )
        self.assertIn(
            '--repository-root "$GITHUB_WORKSPACE"',
            WORKFLOW_PILOT_BASELINE_GATE,
        )
        changed = self.text.replace(
            f"      run: {WORKFLOW_PILOT_GATE}\n",
            "      run: true\n",
            1,
        )
        self.assertNotEqual(changed, self.text)
        self.assertTrue(
            any(
                "candidate host lost exact fail-closed Build evidence: "
                f"{WORKFLOW_PILOT_GATE}"
                in error
                for error in _errors(changed, False)
            )
        )

        changed = self.text.replace(
            f"      run: {WORKFLOW_PILOT_BASELINE_GATE}\n",
            "      run: true\n",
            1,
        )
        self.assertNotEqual(changed, self.text)
        self.assertTrue(
            any(
                "candidate host lost exact fail-closed Build evidence: "
                f"{WORKFLOW_PILOT_BASELINE_GATE}"
                in error
                for error in _errors(changed, False)
            )
        )

    def test_workflow_pilot_steps_reject_spaced_protected_keys(self):
        changed = self.text
        steps = (
            (
                "Run workflow-pilot reporter regression suite (issue #176)",
                WORKFLOW_PILOT_GATE,
            ),
            (
                "Validate workflow-pilot baseline against checked-out Git history",
                WORKFLOW_PILOT_BASELINE_GATE,
            ),
        )
        for step_name, command in steps:
            changed = changed.replace(
                f"    - name: {step_name}\n",
                f"    - name : {step_name}\n",
                1,
            ).replace(
                f"      run: {command}\n",
                f"      run : {command}\n",
                1,
            )
        self.assertNotEqual(changed, self.text)
        self.assertTrue(
            any(
                "protected pre-pilot step sequence differs" in error
                or "lost exact fail-closed Build evidence" in error
                for error in _errors(changed, False)
            )
        )

    def test_both_workflow_pilot_steps_reject_complex_or_advisory_keys(self):
        variants = (
            "continue-on-error: true",
            "if: ${{ false }}",
            "shell: bash {0} || true",
            "working-directory: /",
            "working-directory : /",
            '"continue-on-error": true',
            '"continue-\\u006fn-error": true',
            "? continue-on-error\n      : true",
            "!!str continue-on-error: true",
            "{continue-on-error: true}",
            '"if": ${{ false }}',
            "@unsupported",
        )
        for command in (WORKFLOW_PILOT_GATE, WORKFLOW_PILOT_BASELINE_GATE):
            for variant in variants:
                with self.subTest(command=command, variant=variant):
                    changed = self.text.replace(
                        f"      run: {command}\n",
                        f"      {variant}\n      run: {command}\n",
                        1,
                    )
                    self.assertNotEqual(changed, self.text)
                    self.assertTrue(
                        any(
                            "candidate host lost exact fail-closed Build evidence"
                            in error
                            or "unsupported direct mapping-key syntax" in error
                            for error in _errors(changed, False)
                        )
                    )

    def test_both_workflow_pilot_steps_reject_advisory_or_complex_first_keys(self):
        steps = (
            (
                "Run workflow-pilot reporter regression suite (issue #176)",
                WORKFLOW_PILOT_GATE,
            ),
            (
                "Validate workflow-pilot baseline against checked-out Git history",
                WORKFLOW_PILOT_BASELINE_GATE,
            ),
        )
        variants = (
            "continue-on-error: true",
            "if: ${{ false }}",
            "shell: bash {0} || true",
            "working-directory: /",
            "working-directory : /",
            '"continue-on-error": true',
            '"continue-\\u006fn-error": true',
            "? continue-on-error\n      : true",
            "!!str continue-on-error: true",
            "{continue-on-error: true}",
            "@unsupported",
        )
        for step_name, command in steps:
            original = f"    - name: {step_name}\n"
            reviewed_key_variants = (
                f'"name": {step_name}',
                f'"n\\u0061me": {step_name}',
                f"? name\n      : {step_name}",
                f"!!str name: {step_name}",
                f"{{name: {step_name}}}",
            )
            for variant in variants + reviewed_key_variants:
                with self.subTest(command=command, variant=variant):
                    changed = self.text.replace(
                        original,
                        f"    - {variant}\n",
                        1,
                    )
                    self.assertNotEqual(changed, self.text)
                    self.assertTrue(
                        any(
                            "candidate host lost exact fail-closed Build evidence"
                            in error
                            or "unsupported direct mapping-key syntax" in error
                            for error in _errors(changed, False)
                        )
                    )

    def test_both_workflow_pilot_steps_reject_complex_run_keys(self):
        variants = (
            '"run"',
            '"r\\u0075n"',
            "!!str run",
        )
        for command in (WORKFLOW_PILOT_GATE, WORKFLOW_PILOT_BASELINE_GATE):
            for variant in variants:
                with self.subTest(command=command, variant=variant):
                    changed = self.text.replace(
                        f"      run: {command}\n",
                        f"      {variant}: {command}\n",
                        1,
                    )
                    self.assertNotEqual(changed, self.text)
                    self.assertTrue(
                        any(
                            "candidate host lost exact fail-closed Build evidence"
                            in error
                            or "unsupported direct mapping-key syntax" in error
                            for error in _errors(changed, False)
                        )
                    )

    def test_workflow_pilot_gates_reject_shell_success_masks_and_wrappers(self):
        mutations = (
            f"{WORKFLOW_PILOT_GATE} || true",
            f"{WORKFLOW_PILOT_GATE}; true",
            f"{WORKFLOW_PILOT_GATE} && true",
            f"sh -c \"{WORKFLOW_PILOT_GATE}\"",
            f"echo {WORKFLOW_PILOT_GATE}",
            f"$({WORKFLOW_PILOT_GATE})",
            f"{WORKFLOW_PILOT_GATE} 2>/dev/null",
        )
        for replacement in mutations:
            with self.subTest(replacement=replacement):
                changed = self.text.replace(
                    f"      run: {WORKFLOW_PILOT_GATE}\n",
                    f"      run: {replacement}\n",
                    1,
                )
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "candidate host lost exact fail-closed Build evidence"
                        in error
                        for error in _errors(changed, False)
                    )
                )

        inherited_defaults = (
            self.text.replace(
                "\njobs:\n",
                "\ndefaults:\n"
                "  run:\n"
                "    shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\ndefaults: # inherited mask\n"
                "  run:\n"
                "    shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\ndefaults:\n"
                "  run: # inherited mask\n"
                "    shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\ndefaults:\n"
                "    run:\n"
                "        shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    defaults:\n"
                "      run:\n"
                "        shell: bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    defaults: # inherited mask\n"
                "      run:\n"
                "        shell: bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    defaults:\n"
                "      run: # inherited mask\n"
                "        shell: bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    defaults:\n"
                "        run:\n"
                "            shell: bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\n\"defaults\" :\n"
                "  \"run\" :\n"
                "    \"shell\" : bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\ndefaults: {run: {shell: \"bash {0} || true\"}}\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    \"defaults\" :\n"
                "      \"run\" :\n"
                "        \"shell\" : bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    defaults: {run: {shell: \"bash {0} || true\"}}\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\n\"def\\u0061ults\":\n"
                "  run:\n"
                "    shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\n? defaults\n"
                ":\n"
                "  run:\n"
                "    shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\n!!str defaults:\n"
                "  run:\n"
                "    shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    \"def\\u0061ults\":\n"
                "      run:\n"
                "        shell: bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    ? defaults\n"
                "    :\n"
                "      run:\n"
                "        shell: bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    !!str defaults:\n"
                "      run:\n"
                "        shell: bash {0} || true\n",
                1,
            ),
        )
        for changed in inherited_defaults:
            with self.subTest(inherited_shell_default=changed[:200]):
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "execution defaults must not alter" in error
                        or "unsupported direct mapping-key syntax" in error
                        for error in _errors(changed, False)
                    )
                )

        baseline_mutations = (
            f"{WORKFLOW_PILOT_BASELINE_GATE} || true",
            f"{WORKFLOW_PILOT_BASELINE_GATE}; true",
            f"{WORKFLOW_PILOT_BASELINE_GATE} && true",
            f"sh -c '{WORKFLOW_PILOT_BASELINE_GATE}'",
            f"echo {WORKFLOW_PILOT_BASELINE_GATE}",
            f"$({WORKFLOW_PILOT_BASELINE_GATE})",
            WORKFLOW_PILOT_BASELINE_GATE.replace(
                "> /dev/null", "> /dev/null 2>&1"
            ),
        )
        for replacement in baseline_mutations:
            with self.subTest(replacement=replacement):
                changed = self.text.replace(
                    f"      run: {WORKFLOW_PILOT_BASELINE_GATE}\n",
                    f"      run: {replacement}\n",
                    1,
                )
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "candidate host lost exact fail-closed Build evidence"
                        in error
                        for error in _errors(changed, False)
                    )
                )

        for field in (
            "continue-on-error: true",
            "if: ${{ false }}",
            "shell: bash {0} || true",
        ):
            with self.subTest(advisory_field=field):
                changed = self.text.replace(
                    f"      run: {WORKFLOW_PILOT_BASELINE_GATE}\n",
                    f"      {field}\n      run: {WORKFLOW_PILOT_BASELINE_GATE}\n",
                    1,
                )
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "candidate host lost exact fail-closed Build evidence"
                        in error
                        for error in _errors(changed, False)
                    )
                )

        changed = self.text.replace(
            "  host-tests:\n",
            "  host-tests:\n    continue-on-error: true\n",
            1,
        )
        self.assertNotEqual(changed, self.text)
        self.assertTrue(
            any(
                "host-tests must not be advisory" in error
                for error in _errors(changed, False)
            )
        )

    def test_summary_omitting_legacy_result_fails(self):
        changed = self.text.replace(
            '"$LEGACY_RESULT"\n        do',
            '"$HOST_TESTS_RESULT"\n        do',
            1,
        )
        self.assertTrue(any("summary loop omits" in error for error in _errors(changed, False)))

    def test_summary_comparison_outside_loop_fails(self):
        before, marker, after = self.text.rpartition(
            '[ "$result" != "success" ]'
        )
        self.assertTrue(marker)
        changed = (
            before
            + '[ "$HOST_TESTS_RESULT" != "success" ]'
            + after
        )
        self.assertTrue(any("summary loop must fail closed" in error for error in _errors(changed, False)))

    def test_summary_metadata_and_classifier_results_fail_closed(self):
        mutations = (
            (
                '"$CLASSIFIER_RESULT" != "success"',
                '"$CLASSIFIER_RESULT" = "failure"',
                "classification fails",
            ),
            (
                '"$CLASSIFIED_BUILD_SHA" != "$PR_HEAD_SHA"',
                '"$CLASSIFIED_BUILD_SHA" != "$CLASSIFIED_BUILD_SHA"',
                "missing or stale event identity",
            ),
            (
                '"$CLASSIFIED_BASE_SHA" != "$PR_BASE_SHA"',
                '"$CLASSIFIED_BASE_SHA" != "$CLASSIFIED_BASE_SHA"',
                "missing or stale event identity",
            ),
            (
                'if [ "$IDENTITY_VALID" != "true" ] || [ -z "$PR_HEAD_SHA" ]',
                'if [ "$IDENTITY_VALID" = "false" ] || [ -z "$PR_HEAD_SHA" ]',
                "missing or stale event identity",
            ),
            (
                '[ "$result" != "skipped" ]',
                '[ "$result" != "success" ]',
                "metadata-only continuity",
            ),
            (
                '"$CLASSIFICATION" != "full"',
                '"$CLASSIFICATION" = "full"',
                "unknown full-build",
            ),
        )
        for old, new, expected_error in mutations:
            with self.subTest(mutation=old):
                summary = _job_blocks(self.text)["summary"]
                if old == '[ "$result" != "skipped" ]':
                    before, marker, after = summary.rpartition(old)
                    self.assertTrue(marker)
                    changed_summary = before + new + after
                else:
                    changed_summary = summary.replace(old, new, 1)
                self.assertNotEqual(changed_summary, summary)
                changed = self.text.replace(summary, changed_summary, 1)
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        expected_error in error
                        for error in _errors(changed, False)
                    )
                )

    def test_summary_runtime_rejects_missing_stale_and_failed_identity(self):
        script = _literal_run_script(_step_blocks(_job_blocks(self.text)["summary"])[0])
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        full = {
            "BUILD_RESULT": "success",
            "CLASSIFICATION": "full",
            "CLASSIFIED_BASE_SHA": "2" * 40,
            "CLASSIFIED_BUILD_SHA": "1" * 40,
            "CLASSIFIER_RESULT": "success",
            "EXTENDED_HOST_TESTS_RESULT": "success",
            "FALLBACK_IDENTITY_RESULT": "success",
            "FALLBACK_KIND": "pull_request",
            "FALLBACK_SHA": "1" * 40,
            "FULL_FALLBACK": "false",
            "GITHUB_EVENT_NAME": "pull_request",
            "GITHUB_REF": "refs/pull/177/merge",
            "HEAD_VALID": "true",
            "HOST_TESTS_RESULT": "success",
            "IDENTITY_VALID": "true",
            "LEGACY_RESULT": "success",
            "PR_BASE_SHA": "2" * 40,
            "PR_HEAD_SHA": "1" * 40,
            "PUSH_SHA": "",
            "RAW_PUSH_SHA": "a" * 40,
            "RUN_EXPENSIVE": "true",
            "PATCH_RELEASE_RESULT": "skipped",
        }
        push = {
            **full,
            "CLASSIFIED_BASE_SHA": "",
            "CLASSIFIED_BUILD_SHA": "3" * 40,
            "GITHUB_EVENT_NAME": "push",
            "GITHUB_REF": "refs/heads/master",
            "FALLBACK_KIND": "push",
            "FALLBACK_SHA": "3" * 40,
            "PR_BASE_SHA": "",
            "PR_HEAD_SHA": "",
            "PUSH_SHA": "3" * 40,
            "RAW_PUSH_SHA": "3" * 40,
            "PATCH_RELEASE_RESULT": "success",
        }
        skipped = {
            **full,
            "BUILD_RESULT": "skipped",
            "EXTENDED_HOST_TESTS_RESULT": "skipped",
            "HOST_TESTS_RESULT": "skipped",
            "LEGACY_RESULT": "skipped",
        }
        missing_base = {
            **full,
            "CLASSIFIED_BASE_SHA": "",
            "FULL_FALLBACK": "true",
            "IDENTITY_VALID": "false",
            "PR_BASE_SHA": "",
        }
        cases = (
            ("full-pr", full, 0, None),
            ("full-push", push, 0, None),
            (
                "successful-full-incoherent-event-ref",
                {
                    **skipped,
                    "FALLBACK_KIND": "none",
                    "FALLBACK_SHA": "",
                },
                1,
                "successful PR classification lacks coherent trusted event identity",
            ),
            (
                "successful-push-incoherent-event-kind",
                {
                    **push,
                    "BUILD_RESULT": "skipped",
                    "EXTENDED_HOST_TESTS_RESULT": "skipped",
                    "FALLBACK_KIND": "none",
                    "FALLBACK_SHA": "",
                    "HOST_TESTS_RESULT": "skipped",
                    "LEGACY_RESULT": "skipped",
                    "PATCH_RELEASE_RESULT": "skipped",
                },
                1,
                "successful push classification lacks coherent trusted event identity",
            ),
            ("missing-head", {**full, "PR_HEAD_SHA": ""}, 1, None),
            (
                "missing-base",
                missing_base,
                1,
                "lacks authoritative PR base identity",
            ),
            (
                "missing-base-worker-skipped",
                {
                    **missing_base,
                    "BUILD_RESULT": "skipped",
                },
                1,
                "incomplete-base Build worker did not succeed",
            ),
            (
                "malformed-base-with-diagnostic-sha",
                {
                    **full,
                    "FULL_FALLBACK": "true",
                    "IDENTITY_VALID": "false",
                },
                1,
                "lacks authoritative PR base identity",
            ),
            ("stale-head", {**full, "CLASSIFIED_BUILD_SHA": "9" * 40}, 1, None),
            ("stale-base", {**full, "CLASSIFIED_BASE_SHA": "9" * 40}, 1, None),
            (
                "classifier-failed",
                {**full, "CLASSIFIER_RESULT": "failure"},
                1,
                "exact-head fallback workers completed",
            ),
            (
                "classifier-failed-worker-skipped",
                {**skipped, "CLASSIFIER_RESULT": "failure"},
                1,
                "fallback Build worker did not succeed",
            ),
            (
                "classifier-failed-missing-head",
                {
                    **skipped,
                    "CLASSIFIER_RESULT": "failure",
                    "PR_HEAD_SHA": "",
                    "FALLBACK_KIND": "none",
                    "FALLBACK_SHA": "",
                },
                1,
                "failed without an exact fallback SHA",
            ),
            (
                "classifier-failed-ref-name-head",
                {
                    **skipped,
                    "CLASSIFIER_RESULT": "failure",
                    "FALLBACK_KIND": "none",
                    "FALLBACK_SHA": "",
                    "PR_HEAD_SHA": "refs/heads/attacker",
                },
                1,
                "failed without an exact fallback SHA",
            ),
            (
                "classifier-failed-push",
                {
                    **push,
                    "CLASSIFIER_RESULT": "failure",
                },
                1,
                "exact-push fallback jobs completed",
            ),
            (
                "classifier-failed-missing-push-sha",
                {
                    **skipped,
                    "CLASSIFIER_RESULT": "failure",
                    "GITHUB_EVENT_NAME": "push",
                    "GITHUB_REF": "refs/heads/master",
                    "PATCH_RELEASE_RESULT": "skipped",
                    "FALLBACK_KIND": "none",
                    "FALLBACK_SHA": "",
                    "PR_BASE_SHA": "",
                    "PR_HEAD_SHA": "",
                    "PUSH_SHA": "",
                    "RAW_PUSH_SHA": "",
                },
                1,
                "failed without an exact fallback SHA",
            ),
            (
                "classifier-failed-push-mismatch",
                {
                    **skipped,
                    "CLASSIFIER_RESULT": "failure",
                    "GITHUB_EVENT_NAME": "push",
                    "GITHUB_REF": "refs/heads/master",
                    "PATCH_RELEASE_RESULT": "skipped",
                    "FALLBACK_KIND": "none",
                    "FALLBACK_SHA": "",
                    "PR_BASE_SHA": "",
                    "PR_HEAD_SHA": "",
                    "PUSH_SHA": "3" * 40,
                    "RAW_PUSH_SHA": "4" * 40,
                },
                1,
                "failed without an exact fallback SHA",
            ),
            (
                "classifier-failed-push-ref-name",
                {
                    **skipped,
                    "CLASSIFIER_RESULT": "failure",
                    "FALLBACK_KIND": "none",
                    "FALLBACK_SHA": "",
                    "GITHUB_EVENT_NAME": "push",
                    "GITHUB_REF": "refs/heads/master",
                    "PATCH_RELEASE_RESULT": "skipped",
                    "PR_BASE_SHA": "",
                    "PR_HEAD_SHA": "",
                    "PUSH_SHA": "refs/heads/attacker",
                    "RAW_PUSH_SHA": "refs/heads/attacker",
                },
                1,
                "failed without an exact fallback SHA",
            ),
            (
                "classifier-failed-nonmaster-push",
                {
                    **skipped,
                    "CLASSIFIER_RESULT": "failure",
                    "GITHUB_EVENT_NAME": "push",
                    "GITHUB_REF": "refs/heads/other",
                    "PATCH_RELEASE_RESULT": "skipped",
                    "FALLBACK_KIND": "none",
                    "FALLBACK_SHA": "",
                    "PR_BASE_SHA": "",
                    "PR_HEAD_SHA": "",
                    "PUSH_SHA": "3" * 40,
                    "RAW_PUSH_SHA": "3" * 40,
                },
                1,
                "failed without an exact fallback SHA",
            ),
            ("identity-invalid", {**full, "IDENTITY_VALID": "false"}, 1, None),
            ("full-skipped", {**full, "BUILD_RESULT": "skipped"}, 1, None),
            (
                "unsupported-event",
                {**full, "GITHUB_EVENT_NAME": "schedule"},
                1,
                None,
            ),
        )
        with tempfile.TemporaryDirectory(
            prefix="workflow-summary-runtime-",
            dir=artifact_root,
        ) as temporary:
            summary_path = Path(temporary) / "summary.md"
            for name, environment, expected, error_fragment in cases:
                with self.subTest(name=name):
                    completed = subprocess.run(
                        ["/bin/bash", "-n"],
                        input=script,
                        text=True,
                        check=False,
                        capture_output=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    completed = subprocess.run(
                        ["/bin/bash", "-c", script],
                        cwd=ROOT,
                        env={
                            **os.environ,
                            **environment,
                            "GITHUB_STEP_SUMMARY": str(summary_path),
                        },
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        completed.returncode,
                        expected,
                        completed.stderr,
                    )
                    if error_fragment is not None:
                        self.assertIn(error_fragment, completed.stderr)

    def test_summary_runtime_metadata_only_requires_prior_full_build(self):
        script = _literal_run_script(_step_blocks(_job_blocks(self.text)["summary"])[0])
        completed = subprocess.run(
            ["/bin/bash", "-n"],
            input=script,
            text=True,
            check=False,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        page_one_runs = [
            _summary_workflow_run(
                9300 + index,
                event="push",
                head_sha=SUMMARY_TEST_HEAD_SHA,
                base_sha=SUMMARY_TEST_BASE_SHA,
                run_number=9200 - index,
            )
            for index in range(100)
        ]
        link_header = _summary_runs_link_header(current_page=1, total_count=103)
        routes = {
            _summary_runs_path(page=1): _summary_response(
                _summary_api_payload("workflow_runs", page_one_runs, total_count=103),
                headers={"Link": link_header},
            ),
            _summary_runs_path(page=2): _summary_response(
                _summary_api_payload(
                    "workflow_runs",
                    [
                        _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                        _summary_workflow_run(
                            8101,
                            created_at=_summary_timestamp(5),
                            run_started_at=_summary_timestamp(6),
                            run_number=8101,
                        ),
                        _summary_workflow_run(
                            8100,
                            created_at=_summary_timestamp(4),
                            run_started_at=_summary_timestamp(5),
                        )
                    ],
                    total_count=103,
                ),
                headers={
                    "Link": _summary_runs_link_header(
                        current_page=2,
                        total_count=103,
                        include_next=False,
                        include_prev=True,
                        include_first=True,
                    )
                },
            ),
            _summary_jobs_path(8101): _summary_response(
                _summary_api_payload("jobs", _summary_metadata_jobs())
            ),
            _summary_jobs_path(8100): _summary_response(
                _summary_api_payload("jobs", _summary_full_jobs())
            ),
        }
        completed, requests = _run_summary_with_api(
            script,
            environment=_summary_metadata_env(),
            routes=routes,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            [request["path"] for request in requests],
            [
                _summary_runs_path(page=1),
                _summary_runs_path(page=2),
                _summary_jobs_path(8101),
                _summary_jobs_path(8100),
            ],
        )
        self.assertTrue(requests)
        for request in requests:
            headers = request["headers"]
            self.assertEqual(headers.get("Authorization"), "Bearer token")
            self.assertEqual(
                headers.get("Accept"), "application/vnd.github+json"
            )

    def test_summary_runtime_metadata_only_requires_current_run_observation(self):
        script = _literal_run_script(_step_blocks(_job_blocks(self.text)["summary"])[0])
        completed = subprocess.run(
            ["/bin/bash", "-n"],
            input=script,
            text=True,
            check=False,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        later_page_push_runs = [
            _summary_workflow_run(
                9500 + index,
                event="push",
                head_sha=SUMMARY_TEST_HEAD_SHA,
                base_sha=SUMMARY_TEST_BASE_SHA,
                run_number=9400 - index,
            )
            for index in range(100)
        ]
        duplicate_current = _summary_workflow_run(SUMMARY_TEST_RUN_ID)
        mismatch_number_current = _summary_workflow_run(
            SUMMARY_TEST_RUN_ID,
            run_number=SUMMARY_TEST_RUN_NUMBER - 1,
        )
        mismatch_attempt_current = _summary_workflow_run(
            SUMMARY_TEST_RUN_ID,
            run_attempt=SUMMARY_TEST_RUN_ATTEMPT + 1,
        )
        mismatch_status_current = _summary_workflow_run(
            SUMMARY_TEST_RUN_ID,
            status="completed",
            conclusion=None,
        )
        mismatch_head_current = _summary_workflow_run(
            SUMMARY_TEST_RUN_ID,
            head_sha="4" * 40,
        )
        mismatch_base_current = _summary_workflow_run(
            SUMMARY_TEST_RUN_ID,
            pull_requests=[
                {
                    "number": SUMMARY_TEST_PR_NUMBER,
                    "head": {"sha": SUMMARY_TEST_HEAD_SHA},
                    "base": {"sha": "4" * 40},
                }
            ],
        )
        mismatch_pr_current = _summary_workflow_run(
            SUMMARY_TEST_RUN_ID,
            pr_number=SUMMARY_TEST_PR_NUMBER + 1,
            pull_requests=[
                {
                    "number": SUMMARY_TEST_PR_NUMBER + 1,
                    "head": {"sha": SUMMARY_TEST_HEAD_SHA},
                    "base": {"sha": SUMMARY_TEST_BASE_SHA},
                }
            ],
        )
        mismatch_workflow_current = _summary_workflow_run(
            SUMMARY_TEST_RUN_ID,
            workflow_id=SUMMARY_TEST_WORKFLOW_ID + 1,
        )
        mismatch_path_current = _summary_workflow_run(
            SUMMARY_TEST_RUN_ID,
            path=".github/workflows/other.yml@refs/pull/177/merge",
        )
        mismatch_started_current = _summary_workflow_run(
            SUMMARY_TEST_RUN_ID,
            run_started_at=None,
        )
        inconsistent_timestamps_current = _summary_workflow_run(
            SUMMARY_TEST_RUN_ID,
            created_at=_summary_timestamp(9),
            run_started_at=_summary_timestamp(8),
        )
        newer_exact_run = _summary_workflow_run(
            9002,
            pr_number=SUMMARY_TEST_PR_NUMBER,
            base_sha=SUMMARY_TEST_BASE_SHA,
            head_sha=SUMMARY_TEST_HEAD_SHA,
            run_number=SUMMARY_TEST_RUN_NUMBER + 1,
            created_at=_summary_timestamp(9),
            run_started_at=_summary_timestamp(10),
            pull_requests=[
                {
                    "number": SUMMARY_TEST_PR_NUMBER,
                    "head": {"sha": SUMMARY_TEST_HEAD_SHA},
                    "base": {"sha": SUMMARY_TEST_BASE_SHA},
                }
            ],
        )
        cases = (
            (
                "missing-current-older-success-only",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload("workflow_runs", [_summary_workflow_run(8100)])
                    )
                },
                "metadata-only summary current workflow run was not observed",
                [_summary_runs_path(page=1)],
            ),
            (
                "duplicate-current",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [duplicate_current, duplicate_current],
                            total_count=2,
                        )
                    )
                },
                "metadata-only summary current workflow run was duplicated",
                [_summary_runs_path(page=1)],
            ),
            (
                "current-number-mismatch",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [mismatch_number_current, _summary_workflow_run(8100)],
                            total_count=2,
                        )
                    )
                },
                "metadata-only summary current workflow run sequence drifted",
                [_summary_runs_path(page=1)],
            ),
            (
                "current-attempt-mismatch",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [mismatch_attempt_current, _summary_workflow_run(8100)],
                            total_count=2,
                        )
                    )
                },
                "metadata-only summary current workflow run sequence drifted",
                [_summary_runs_path(page=1)],
            ),
            (
                "current-workflow-mismatch",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [mismatch_workflow_current, _summary_workflow_run(8100)],
                            total_count=2,
                        )
                    )
                },
                "metadata-only summary workflow runs workflow_id drifted",
                [_summary_runs_path(page=1)],
            ),
            (
                "current-status-mismatch",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [mismatch_status_current, _summary_workflow_run(8100)],
                            total_count=2,
                        )
                    )
                },
                "metadata-only summary current workflow run status drifted",
                [_summary_runs_path(page=1)],
            ),
            (
                "current-head-mismatch",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [mismatch_head_current, _summary_workflow_run(8100)],
                            total_count=2,
                        )
                    )
                },
                "metadata-only summary current workflow run identity drifted",
                [_summary_runs_path(page=1)],
            ),
            (
                "current-base-mismatch",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [mismatch_base_current, _summary_workflow_run(8100)],
                            total_count=2,
                        )
                    )
                },
                "metadata-only summary current workflow run identity drifted",
                [_summary_runs_path(page=1)],
            ),
            (
                "current-pr-mismatch",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [mismatch_pr_current, _summary_workflow_run(8100)],
                            total_count=2,
                        )
                    )
                },
                "metadata-only summary current workflow run identity drifted",
                [_summary_runs_path(page=1)],
            ),
            (
                "current-path-mismatch",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [mismatch_path_current, _summary_workflow_run(8100)],
                            total_count=2,
                        )
                    )
                },
                "metadata-only summary current workflow run identity drifted",
                [_summary_runs_path(page=1)],
            ),
            (
                "current-started-at-missing",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [mismatch_started_current, _summary_workflow_run(8100)],
                            total_count=2,
                        )
                    )
                },
                "metadata-only summary current workflow run run_started_at is invalid",
                [_summary_runs_path(page=1)],
            ),
            (
                "current-timestamps-inconsistent",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [inconsistent_timestamps_current, _summary_workflow_run(8100)],
                            total_count=2,
                        )
                    )
                },
                "metadata-only summary current workflow run timestamps are inconsistent",
                [_summary_runs_path(page=1)],
            ),
            (
                "current-not-first-exact-page",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            later_page_push_runs[:99] + [_summary_workflow_run(8100)],
                            total_count=101,
                        ),
                        headers={
                            "Link": _summary_runs_link_header(
                                current_page=1,
                                total_count=101,
                            )
                        },
                    ),
                    _summary_runs_path(page=2): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [_summary_workflow_run(SUMMARY_TEST_RUN_ID)],
                            total_count=101,
                        )
                    ),
                },
                "metadata-only summary workflow runs are not ordered by run_number",
                [
                    _summary_runs_path(page=1),
                    _summary_runs_path(page=2),
                ],
            ),
            (
                "current-eventual-omission",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            later_page_push_runs,
                            total_count=101,
                        ),
                        headers={
                            "Link": _summary_runs_link_header(
                                current_page=1,
                                total_count=101,
                            )
                        },
                    ),
                    _summary_runs_path(page=2): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [_summary_workflow_run(8100)],
                            total_count=101,
                        )
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", _summary_full_jobs())
                    ),
                },
                "metadata-only summary current workflow run was not observed",
                [
                    _summary_runs_path(page=1),
                    _summary_runs_path(page=2),
                ],
            ),
            (
                "newer-exact-run-supersedes-current",
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [newer_exact_run, _summary_workflow_run(SUMMARY_TEST_RUN_ID), _summary_workflow_run(8100)],
                            total_count=3,
                        )
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", _summary_full_jobs())
                    ),
                },
                "metadata-only summary current workflow run is superseded",
                [_summary_runs_path(page=1)],
            ),
        )
        for name, routes, error_fragment, expected_requests in cases:
            with self.subTest(name=name):
                completed, requests = _run_summary_with_api(
                    script,
                    environment=_summary_metadata_env(),
                    routes=routes,
                )
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertIn(error_fragment, completed.stderr)
                self.assertEqual(
                    [request["path"] for request in requests],
                    expected_requests,
                )

    def test_summary_runtime_metadata_only_skips_failed_metadata_retry(self):
        script = _literal_run_script(_step_blocks(_job_blocks(self.text)["summary"])[0])
        completed = subprocess.run(
            ["/bin/bash", "-n"],
            input=script,
            text=True,
            check=False,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        failed_metadata_jobs = _replace_summary_job(
            _summary_metadata_jobs(),
            "summary",
            conclusion="failure",
        )
        routes = {
            _summary_runs_path(page=1): _summary_response(
                _summary_api_payload(
                    "workflow_runs",
                    [
                        _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                        _summary_workflow_run(
                            8102,
                            conclusion="failure",
                            created_at=_summary_timestamp(7),
                            run_started_at=_summary_timestamp(8),
                        ),
                        _summary_workflow_run(
                            8101,
                            created_at=_summary_timestamp(6),
                            run_started_at=_summary_timestamp(7),
                        ),
                    ],
                )
            ),
            _summary_jobs_path(8102): _summary_response(
                _summary_api_payload("jobs", failed_metadata_jobs)
            ),
            _summary_jobs_path(8101): _summary_response(
                _summary_api_payload("jobs", _summary_full_jobs())
            ),
        }
        completed, requests = _run_summary_with_api(
            script,
            environment=_summary_metadata_env(),
            routes=routes,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            [request["path"] for request in requests],
            [
                _summary_runs_path(page=1),
                _summary_jobs_path(8102),
                _summary_jobs_path(8101),
            ],
        )

    def test_summary_runtime_metadata_only_uses_newest_prior_full_authoritatively(self):
        script = _literal_run_script(_step_blocks(_job_blocks(self.text)["summary"])[0])
        completed = subprocess.run(
            ["/bin/bash", "-n"],
            input=script,
            text=True,
            check=False,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        full_success = _summary_full_jobs()
        full_missing_started_at = _replace_summary_job(
            full_success,
            "build",
            started_at=None,
        )
        unknown_shape = full_success + [_summary_job("attacker-job", "success")]
        mixed_shape = _replace_summary_job(
            full_success,
            "event-classifier",
            name="metadata-classifier",
        )
        cases = (
            (
                "newer-failed-full-blocks-older-success",
                [
                    _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                    _summary_workflow_run(
                        8101,
                        conclusion="failure",
                        created_at=_summary_timestamp(7),
                        run_started_at=_summary_timestamp(8),
                    ),
                    _summary_workflow_run(
                        8100,
                        created_at=_summary_timestamp(6),
                        run_started_at=_summary_timestamp(7),
                    ),
                ],
                {
                    _summary_jobs_path(8101): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                },
                "metadata-only summary newest prior full Build CI run did not complete successfully",
                [
                    _summary_runs_path(page=1),
                    _summary_jobs_path(8101),
                ],
            ),
            (
                "null-start-newer-failed-full-blocks-older-success",
                [
                    _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                    _summary_workflow_run(
                        8101,
                        conclusion="failure",
                        created_at=_summary_timestamp(7),
                        run_started_at=None,
                    ),
                    _summary_workflow_run(
                        8100,
                        created_at=_summary_timestamp(7),
                        run_started_at=_summary_timestamp(8),
                    ),
                ],
                {
                    _summary_jobs_path(8101): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                },
                "metadata-only summary newest prior full Build CI run did not complete successfully",
                [
                    _summary_runs_path(page=1),
                    _summary_jobs_path(8101),
                ],
            ),
            (
                "newer-cancelled-full-blocks-older-success",
                [
                    _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                    _summary_workflow_run(
                        8101,
                        conclusion="cancelled",
                        created_at=_summary_timestamp(7),
                        run_started_at=_summary_timestamp(8),
                    ),
                    _summary_workflow_run(
                        8100,
                        created_at=_summary_timestamp(6),
                        run_started_at=_summary_timestamp(7),
                    ),
                ],
                {
                    _summary_jobs_path(8101): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                },
                "metadata-only summary newest prior full Build CI run did not complete successfully",
                [
                    _summary_runs_path(page=1),
                    _summary_jobs_path(8101),
                ],
            ),
            (
                "newer-in-progress-full-blocks-older-success",
                [
                    _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                    _summary_workflow_run(
                        8101,
                        status="in_progress",
                        conclusion=None,
                        created_at=_summary_timestamp(7),
                        run_started_at=_summary_timestamp(8),
                    ),
                    _summary_workflow_run(
                        8100,
                        created_at=_summary_timestamp(6),
                        run_started_at=_summary_timestamp(7),
                    ),
                ],
                {
                    _summary_jobs_path(8101): _summary_response(
                        _summary_api_payload(
                            "jobs",
                            _replace_summary_job(
                                _replace_summary_job(
                                    full_success,
                                    "summary",
                                    status="in_progress",
                                    conclusion=None,
                                ),
                                "host-tests",
                                status="in_progress",
                                conclusion=None,
                            ),
                        )
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                },
                "metadata-only summary newest prior full Build CI run did not complete successfully",
                [
                    _summary_runs_path(page=1),
                    _summary_jobs_path(8101),
                ],
            ),
            (
                "null-start-newer-in-progress-full-blocks-older-success",
                [
                    _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                    _summary_workflow_run(
                        8101,
                        status="in_progress",
                        conclusion=None,
                        created_at=_summary_timestamp(7),
                        run_started_at=None,
                    ),
                    _summary_workflow_run(
                        8100,
                        created_at=_summary_timestamp(7),
                        run_started_at=_summary_timestamp(8),
                    ),
                ],
                {
                    _summary_jobs_path(8101): _summary_response(
                        _summary_api_payload(
                            "jobs",
                            _replace_summary_job(
                                _replace_summary_job(
                                    full_success,
                                    "summary",
                                    status="in_progress",
                                    conclusion=None,
                                ),
                                "host-tests",
                                status="in_progress",
                                conclusion=None,
                            ),
                        )
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                },
                "metadata-only summary newest prior full Build CI run did not complete successfully",
                [
                    _summary_runs_path(page=1),
                    _summary_jobs_path(8101),
                ],
            ),
            (
                "newer-unknown-shape-blocks-older-success",
                [
                    _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                    _summary_workflow_run(
                        8101,
                        created_at=_summary_timestamp(7),
                        run_started_at=_summary_timestamp(8),
                    ),
                    _summary_workflow_run(
                        8100,
                        created_at=_summary_timestamp(6),
                        run_started_at=_summary_timestamp(7),
                    ),
                ],
                {
                    _summary_jobs_path(8101): _summary_response(
                        _summary_api_payload("jobs", unknown_shape)
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                },
                "metadata-only summary prior run 8101 jobs have unexpected mode shape",
                [
                    _summary_runs_path(page=1),
                    _summary_jobs_path(8101),
                ],
            ),
            (
                "newer-mixed-shape-blocks-older-success",
                [
                    _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                    _summary_workflow_run(
                        8101,
                        created_at=_summary_timestamp(7),
                        run_started_at=_summary_timestamp(8),
                    ),
                    _summary_workflow_run(
                        8100,
                        created_at=_summary_timestamp(6),
                        run_started_at=_summary_timestamp(7),
                    ),
                ],
                {
                    _summary_jobs_path(8101): _summary_response(
                        _summary_api_payload("jobs", mixed_shape)
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                },
                "metadata-only summary prior run 8101 metadata job extended-host-tests is malformed",
                [
                    _summary_runs_path(page=1),
                    _summary_jobs_path(8101),
                ],
            ),
            (
                "newer-malformed-full-blocks-older-success",
                [
                    _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                    _summary_workflow_run(
                        8101,
                        created_at=_summary_timestamp(7),
                        run_started_at=_summary_timestamp(8),
                    ),
                    _summary_workflow_run(
                        8100,
                        created_at=_summary_timestamp(6),
                        run_started_at=_summary_timestamp(7),
                    ),
                ],
                {
                    _summary_jobs_path(8101): _summary_response(
                        _summary_api_payload("jobs", full_missing_started_at)
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                },
                "metadata-only summary newest prior full Build CI job build is not a successful runner-backed completion",
                [
                    _summary_runs_path(page=1),
                    _summary_jobs_path(8101),
                ],
            ),
            (
                "same-timestamp-full-order-uses-higher-run-id",
                [
                    _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                    _summary_workflow_run(
                        8101,
                        conclusion="failure",
                        created_at=_summary_timestamp(7),
                        run_started_at=_summary_timestamp(8),
                    ),
                    _summary_workflow_run(
                        8100,
                        created_at=_summary_timestamp(7),
                        run_started_at=_summary_timestamp(8),
                    ),
                ],
                {
                    _summary_jobs_path(8101): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                },
                "metadata-only summary newest prior full Build CI run did not complete successfully",
                [
                    _summary_runs_path(page=1),
                    _summary_jobs_path(8101),
                ],
            ),
            (
                "rerun-latest-attempt-failure-blocks-older-attempt-success",
                [
                    _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                    _summary_workflow_run(
                        8101,
                        run_attempt=2,
                        conclusion="failure",
                        created_at=_summary_timestamp(7),
                        run_started_at=_summary_timestamp(8),
                    ),
                ],
                {
                    _summary_jobs_path(8101, attempt=2): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                    _summary_jobs_path(8101, attempt=1): _summary_response(
                        _summary_api_payload("jobs", full_success)
                    ),
                },
                "metadata-only summary newest prior full Build CI run did not complete successfully",
                [
                    _summary_runs_path(page=1),
                    _summary_jobs_path(8101, attempt=2),
                ],
            ),
        )
        for name, workflow_runs, job_routes, error_fragment, expected_requests in cases:
            with self.subTest(name=name):
                routes = {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload("workflow_runs", workflow_runs)
                    ),
                    **job_routes,
                }
                completed, requests = _run_summary_with_api(
                    script,
                    environment=_summary_metadata_env(),
                    routes=routes,
                )
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertIn(error_fragment, completed.stderr)
                self.assertEqual(
                    [request["path"] for request in requests],
                    expected_requests,
                )

    def test_summary_runtime_metadata_only_chooses_newest_full_success(self):
        script = _literal_run_script(_step_blocks(_job_blocks(self.text)["summary"])[0])
        completed = subprocess.run(
            ["/bin/bash", "-n"],
            input=script,
            text=True,
            check=False,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        routes = {
            _summary_runs_path(page=1): _summary_response(
                _summary_api_payload(
                    "workflow_runs",
                    [
                        _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                        _summary_workflow_run(
                            8101,
                            created_at=_summary_timestamp(7),
                            run_started_at=_summary_timestamp(8),
                        ),
                        _summary_workflow_run(
                            8100,
                            created_at=_summary_timestamp(7),
                            run_started_at=_summary_timestamp(8),
                        ),
                    ],
                )
            ),
            _summary_jobs_path(8101): _summary_response(
                _summary_api_payload("jobs", _summary_full_jobs())
            ),
            _summary_jobs_path(8100): _summary_response(
                _summary_api_payload("jobs", _summary_full_jobs())
            ),
        }
        completed, requests = _run_summary_with_api(
            script,
            environment=_summary_metadata_env(),
            routes=routes,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            [request["path"] for request in requests],
            [
                _summary_runs_path(page=1),
                _summary_jobs_path(8101),
            ],
        )

    def test_summary_runtime_metadata_only_rejects_invalid_prior_full_evidence(self):
        script = _literal_run_script(_step_blocks(_job_blocks(self.text)["summary"])[0])
        completed = subprocess.run(
            ["/bin/bash", "-n"],
            input=script,
            text=True,
            check=False,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        self_only = {
            _summary_runs_path(page=1): _summary_response(
                _summary_api_payload(
                    "workflow_runs",
                    [
                        _summary_workflow_run(
                            SUMMARY_TEST_RUN_ID,
                            run_attempt=SUMMARY_TEST_RUN_ATTEMPT,
                        )
                    ],
                )
            )
        }
        mixed_jobs_routes = {
            _summary_runs_path(page=1): _summary_response(
                _summary_api_payload(
                    "workflow_runs",
                    [
                        _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                        _summary_workflow_run(8101),
                        _summary_workflow_run(8100),
                    ],
                )
            ),
            _summary_jobs_path(8101): _summary_response(
                _summary_api_payload(
                    "jobs",
                    [
                        _summary_job("event-identity", "success"),
                        _summary_job("event-router", "success"),
                        _summary_job("event-classifier", "success"),
                        _summary_job("host-tests", "success"),
                        _summary_job("build", "success"),
                    ],
                )
            ),
            _summary_jobs_path(8100): _summary_response(
                _summary_api_payload(
                    "jobs",
                    [
                        _summary_job("extended-host-tests", "success"),
                        _summary_job("legacy", "success"),
                        _summary_job("patch-release", "skipped", runner_name=None),
                        _summary_job("summary", "success"),
                    ],
                )
            ),
        }
        repeated_run_routes = {
            _summary_runs_path(page=1): _summary_response(
                _summary_api_payload(
                    "workflow_runs",
                    [_summary_workflow_run(SUMMARY_TEST_RUN_ID)]
                    + [
                        _summary_workflow_run(
                            8200 + index,
                            event="push",
                            run_number=8298 - index,
                        )
                        for index in range(99)
                    ],
                    total_count=101,
                ),
                headers={
                    "Link": _summary_runs_link_header(current_page=1, total_count=101)
                },
            ),
            _summary_runs_path(page=2): _summary_response(
                _summary_api_payload(
                    "workflow_runs",
                    [_summary_workflow_run(8200)],
                    total_count=101,
                )
            ),
        }
        page_cap_routes = {
            **{
                _summary_runs_path(page=page): _summary_response(
                    _summary_api_payload(
                        "workflow_runs",
                        [
                            _summary_workflow_run(
                                10000 + (page - 1) * 100 + index,
                                event="push",
                                run_number=9000 - ((page - 1) * 100 + index),
                            )
                            for index in range(100)
                        ],
                        total_count=1000,
                    ),
                    headers={"Link": _summary_runs_link_header(current_page=page, total_count=1000)},
                )
                for page in range(1, 11)
            }
        }
        missing_run_number = _summary_workflow_run(8100)
        del missing_run_number["run_number"]
        missing_workflow_id = _summary_workflow_run(8100)
        del missing_workflow_id["workflow_id"]
        cases = (
            (
                "metadata-expensive-ran",
                _summary_metadata_env(EXTENDED_HOST_TESTS_RESULT="success"),
                {},
                1,
                "metadata-only expensive Build worker was not skipped",
            ),
            (
                "missing-token",
                _summary_metadata_env(GITHUB_TOKEN=""),
                {},
                1,
                "metadata-only summary missing GITHUB_TOKEN",
            ),
            (
                "no-prior-full",
                _summary_metadata_env(),
                self_only,
                1,
                "metadata-only summary requires a prior successful complete full Build CI run",
            ),
            (
                "missing-last-nonfinal",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [_summary_workflow_run(SUMMARY_TEST_RUN_ID)]
                            + [
                                _summary_workflow_run(
                                    9100 - index,
                                    event="push",
                                    run_number=9000 - index,
                                )
                                for index in range(99)
                            ],
                            total_count=101,
                        ),
                        headers={
                            "Link": f'<{{api_base}}{_summary_runs_path(page=2)}>; rel="next"'
                        },
                    )
                },
                1,
                "metadata-only summary workflow runs last page is required",
            ),
            (
                "short-missing-next",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8101),
                            ],
                            total_count=3,
                        ),
                        headers={
                            "Link": f'<{{api_base}}{_summary_runs_path(page=2)}>; rel="next"'
                        },
                    ),
                    _summary_runs_path(page=2): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [_summary_workflow_run(8100)],
                            total_count=3,
                        )
                    ),
                },
                1,
                "metadata-only summary workflow runs page cardinality is invalid",
            ),
            (
                "short-nonfinal-page",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [_summary_workflow_run(9100 - index) for index in range(99)],
                            total_count=101,
                        ),
                        headers={
                            "Link": _summary_runs_link_header(
                                current_page=1,
                                total_count=101,
                            )
                        },
                    )
                },
                1,
                "metadata-only summary workflow runs page cardinality is invalid",
            ),
            (
                "short-final-page",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(
                                    9200 - index,
                                    run_number=9000 - index,
                                )
                                for index in range(100)
                            ],
                            total_count=101,
                        ),
                        headers={
                            "Link": _summary_runs_link_header(
                                current_page=1,
                                total_count=101,
                            )
                        },
                    ),
                    _summary_runs_path(page=2): _summary_response(
                        _summary_api_payload("workflow_runs", [], total_count=101)
                    ),
                },
                1,
                "metadata-only summary workflow runs page cardinality is invalid",
            ),
            (
                "overfull-final-page",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(
                                    9200 - index,
                                    run_number=9000 - index,
                                )
                                for index in range(100)
                            ],
                            total_count=101,
                        ),
                        headers={
                            "Link": _summary_runs_link_header(
                                current_page=1,
                                total_count=101,
                            )
                        },
                    ),
                    _summary_runs_path(page=2): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [_summary_workflow_run(8101), _summary_workflow_run(8100)],
                            total_count=101,
                        )
                    ),
                },
                1,
                "metadata-only summary workflow runs page cardinality is invalid",
            ),
            (
                "unexpected-next-after-complete",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(
                                    9900 + index,
                                    event="push",
                                    run_number=9800 - index,
                                )
                                for index in range(100)
                            ],
                            total_count=101,
                        ),
                        headers={
                            "Link": _summary_runs_link_header(
                                current_page=1,
                                total_count=101,
                            )
                        },
                    ),
                    _summary_runs_path(page=2): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [_summary_workflow_run(SUMMARY_TEST_RUN_ID)],
                            total_count=101,
                        ),
                        headers={
                            "Link": _summary_runs_link_header(
                                current_page=2,
                                total_count=101,
                                next_page=3,
                                include_prev=True,
                                include_first=True,
                            )
                        },
                    ),
                },
                1,
                "metadata-only summary workflow runs reported an unexpected next page",
            ),
            (
                "last-link-on-single-page",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100),
                            ],
                            total_count=2,
                        ),
                        headers={
                            "Link": _summary_runs_link_header(
                                current_page=1,
                                total_count=2,
                                include_next=False,
                            )
                        },
                    )
                },
                1,
                "metadata-only summary workflow runs reported pagination for a single page",
            ),
            (
                "changing-total-count",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(
                                    9300 - index,
                                    event="push",
                                    run_number=9000 - index,
                                )
                                for index in range(100)
                            ],
                            total_count=101,
                        ),
                        headers={
                            "Link": _summary_runs_link_header(
                                current_page=1,
                                total_count=101,
                            )
                        },
                    ),
                    _summary_runs_path(page=2): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [_summary_workflow_run(8100, event="push", run_number=8900)],
                            total_count=102,
                        )
                    ),
                },
                1,
                "metadata-only summary workflow runs total_count changed across pages",
            ),
            (
                "overfull-nonfinal-page",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(
                                    9200 - index,
                                    event="push",
                                    run_number=9000 - index,
                                )
                                for index in range(101)
                            ],
                            total_count=150,
                        )
                    )
                },
                1,
                "metadata-only summary workflow runs pagination is invalid",
            ),
            (
                "duplicate-last-relation",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [_summary_workflow_run(SUMMARY_TEST_RUN_ID)]
                            + [
                                _summary_workflow_run(
                                    9300 - index,
                                    event="push",
                                    run_number=9000 - index,
                                )
                                for index in range(99)
                            ],
                            total_count=101,
                        ),
                        headers={
                            "Link": (
                                f'<{{api_base}}{_summary_runs_path(page=2)}>; rel="next", '
                                f'<{{api_base}}{_summary_runs_path(page=2)}>; rel="last", '
                                f'<{{api_base}}{_summary_runs_path(page=3)}>; rel="last"'
                            )
                        },
                    )
                },
                1,
                "metadata-only summary workflow runs Link header repeats a relation",
            ),
            (
                "looping-next-page",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(
                                    9300 - index,
                                    event="push",
                                    run_number=9000 - index,
                                )
                                for index in range(100)
                            ],
                            total_count=101,
                        ),
                        headers={
                            "Link": _summary_runs_link_header(
                                current_page=1,
                                total_count=101,
                                next_page=1,
                            )
                        },
                    )
                },
                1,
                "metadata-only summary workflow runs next page is not sequential",
            ),
            (
                "wrong-last-page",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [_summary_workflow_run(SUMMARY_TEST_RUN_ID)]
                            + [
                                _summary_workflow_run(
                                    9300 - index,
                                    event="push",
                                    run_number=9000 - index,
                                )
                                for index in range(99)
                            ],
                            total_count=101,
                        ),
                        headers={
                            "Link": _summary_runs_link_header(
                                current_page=1,
                                total_count=101,
                                last_page=3,
                            )
                        },
                    )
                },
                1,
                "metadata-only summary workflow runs last page drifted",
            ),
            (
                "skipped-next-page",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(
                                    9300 - index,
                                    event="push",
                                    run_number=9000 - index,
                                )
                                for index in range(100)
                            ],
                            total_count=101,
                        ),
                        headers={
                            "Link": _summary_runs_link_header(
                                current_page=1,
                                total_count=101,
                                next_page=3,
                            )
                        },
                    )
                },
                1,
                "metadata-only summary workflow runs next page is not sequential",
            ),
            (
                "same-run-number-conflicting-ids",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8101, run_number=8100),
                                _summary_workflow_run(8100, run_number=8100),
                            ],
                        )
                    )
                },
                1,
                "metadata-only summary workflow runs repeat a run_number",
            ),
            (
                "nonmonotonic-run-number-order",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100, run_number=8000),
                                _summary_workflow_run(8101, run_number=8001),
                            ],
                        )
                    )
                },
                1,
                "metadata-only summary workflow runs are not ordered by run_number",
            ),
            (
                "run-number-not-older-than-current",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100, run_number=SUMMARY_TEST_RUN_NUMBER + 1),
                            ],
                        )
                    )
                },
                1,
                "metadata-only summary workflow runs are not ordered by run_number",
            ),
            (
                "missing-run-number",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                missing_run_number,
                            ],
                        )
                    )
                },
                1,
                "metadata-only summary workflow run run_number is invalid",
            ),
            (
                "missing-workflow-id",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                missing_workflow_id,
                            ],
                        )
                    )
                },
                1,
                "metadata-only summary workflow run workflow_id is invalid",
            ),
            (
                "workflow-id-drift",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100, workflow_id=SUMMARY_TEST_WORKFLOW_ID + 1),
                            ],
                        )
                    )
                },
                1,
                "metadata-only summary workflow runs workflow_id drifted",
            ),
            (
                "failed-prior-run",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100, conclusion="failure"),
                            ],
                        )
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", _summary_full_jobs())
                    ),
                },
                1,
                "metadata-only summary newest prior full Build CI run did not complete successfully",
            ),
            (
                "in-progress-prior-run",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(
                                    8100,
                                    status="in_progress",
                                    conclusion=None,
                                ),
                            ],
                        )
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", _summary_full_jobs())
                    ),
                },
                1,
                "metadata-only summary newest prior full Build CI run did not complete successfully",
            ),
            (
                "cancelled-prior-run",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100, conclusion="cancelled"),
                            ],
                        )
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", _summary_full_jobs())
                    ),
                },
                1,
                "metadata-only summary newest prior full Build CI run did not complete successfully",
            ),
            (
                "wrong-pr-binding",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100, pr_number=SUMMARY_TEST_PR_NUMBER + 1),
                            ],
                        )
                    )
                },
                1,
                "metadata-only summary requires a prior successful complete full Build CI run",
            ),
            (
                "wrong-head-binding",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100, head_sha="4" * 40),
                            ],
                        )
                    )
                },
                1,
                "metadata-only summary requires a prior successful complete full Build CI run",
            ),
            (
                "wrong-base-binding",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100, base_sha="3" * 40),
                            ],
                        )
                    )
                },
                1,
                "metadata-only summary requires a prior successful complete full Build CI run",
            ),
            (
                "wrong-repository-url",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(
                                    8100,
                                    url=(
                                        "https://api.github.test/repos/other/repo/"
                                        "actions/runs/8100"
                                    ),
                                ),
                            ],
                        )
                    )
                },
                1,
                "metadata-only summary requires a prior successful complete full Build CI run",
            ),
            (
                "wrong-event",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100, event="push"),
                            ],
                        )
                    )
                },
                1,
                "metadata-only summary requires a prior successful complete full Build CI run",
            ),
            (
                "wrong-workflow-path",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(
                                    8100,
                                    path=".github/workflows/other.yml@refs/pull/177/merge",
                                ),
                            ],
                        )
                    )
                },
                1,
                "metadata-only summary requires a prior successful complete full Build CI run",
            ),
            (
                "metadata-masquerade",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100),
                            ],
                        )
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", _summary_metadata_jobs())
                    ),
                },
                1,
                "metadata-only summary requires a prior successful complete full Build CI run",
            ),
            (
                "mixed-jobs-across-runs",
                _summary_metadata_env(),
                mixed_jobs_routes,
                1,
                "metadata-only summary prior run 8101 jobs have unexpected mode shape",
            ),
            (
                "missing-runner",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100),
                            ],
                        )
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload(
                            "jobs",
                            [
                                _summary_job("event-identity", "success"),
                                _summary_job("event-router", "success"),
                                _summary_job("event-classifier", "success"),
                                _summary_job("host-tests", "success"),
                                _summary_job("build", "success", runner_name=None),
                                _summary_job("extended-host-tests", "success"),
                                _summary_job("legacy", "success"),
                                _summary_job("patch-release", "skipped", runner_name=None),
                                _summary_job("summary", "success"),
                            ],
                        )
                    ),
                },
                1,
                "metadata-only summary newest prior full Build CI job build is not a successful runner-backed completion",
            ),
            (
                "bad-run-attempt",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100, run_attempt=0),
                            ],
                        )
                    )
                },
                1,
                "metadata-only summary workflow run attempt is invalid",
            ),
            (
                "malformed-workflow-runs",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(b"{")
                },
                1,
                "metadata-only summary workflow runs page 1 response is not valid JSON",
            ),
            (
                "total-count-cap",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [_summary_workflow_run(SUMMARY_TEST_RUN_ID)],
                            total_count=1001,
                        )
                    )
                },
                1,
                "metadata-only summary workflow runs total_count is invalid",
            ),
            (
                "malformed-link",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(
                                    8300 + index,
                                    event="push",
                                    run_number=8300 - index,
                                )
                                for index in range(100)
                            ],
                        ),
                        headers={"Link": "not a valid link"},
                    )
                },
                1,
                "metadata-only summary workflow runs Link header is malformed",
            ),
            (
                "repeated-run-across-pages",
                _summary_metadata_env(),
                repeated_run_routes,
                1,
                "metadata-only summary workflow runs repeated a run",
            ),
            (
                "prior-run-jobs-repeat-name",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100),
                            ],
                        )
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload(
                            "jobs",
                            [
                                _summary_job("event-identity", "success"),
                                _summary_job("event-identity", "success"),
                            ],
                        )
                    ),
                },
                1,
                "metadata-only summary prior run jobs repeat a name",
            ),
            (
                "prior-run-jobs-pagination",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response(
                        _summary_api_payload(
                            "workflow_runs",
                            [
                                _summary_workflow_run(SUMMARY_TEST_RUN_ID),
                                _summary_workflow_run(8100),
                            ],
                        )
                    ),
                    _summary_jobs_path(8100): _summary_response(
                        _summary_api_payload("jobs", _summary_full_jobs()),
                        headers={"Link": '<https://api.github.test/jobs?page=2>; rel="next"'},
                    ),
                },
                1,
                "metadata-only summary prior run jobs pagination is unsupported",
            ),
            (
                "http-error",
                _summary_metadata_env(),
                {
                    _summary_runs_path(page=1): _summary_response({}, status=403)
                },
                1,
                "metadata-only summary workflow runs page 1 request failed: HTTP 403",
            ),
            (
                "overflowing-pagination-bound",
                _summary_metadata_env(),
                page_cap_routes,
                1,
                "metadata-only summary workflow runs exceed the reviewed pagination bound",
            ),
        )
        for name, environment, routes, expected, error_fragment in cases:
            with self.subTest(name=name):
                completed, _requests = _run_summary_with_api(
                    script,
                    environment=environment,
                    routes=routes,
                )
                self.assertEqual(completed.returncode, expected, completed.stderr)
                self.assertIn(error_fragment, completed.stderr)

    def test_workflow_governance_docs_bind_metadata_summary_to_prior_full_evidence(self):
        governance = WORKFLOW_GOVERNANCE_CASE.read_text(encoding="utf-8")
        governance_compact = " ".join(governance.split())
        self.assertIn(
            'latest_full["summary"] == (title["run_id"], "success")',
            governance,
        )
        self.assertIn(
            "A later metadata continuity run advances the required canonical "
            "`summary` context only after proving that newest prior full run",
            governance_compact,
        )
        self.assertIn(
            "candidate eligibility remains bound to the newest prior complete full run",
            governance_compact,
        )
        self.assertNotIn(
            "the required canonical `summary` context remains on the latest "
            "successful full run even though later metadata runs reuse the worker names",
            governance_compact,
        )
        workflow_pilot = WORKFLOW_PILOT_DOC.read_text(encoding="utf-8")
        workflow_pilot_compact = " ".join(workflow_pilot.split())
        self.assertIn(
            "a prior successful full run remains eligible because the later metadata continuity "
            "run advances only the required canonical `summary` context after proving that "
            "prior full run",
            workflow_pilot_compact,
        )
        self.assertNotIn(
            "contexts are distinct rather than replacements",
            workflow_pilot_compact,
        )

    def test_summary_runtime_metadata_only_rejects_redirects_without_leaking_token(self):
        script = _literal_run_script(_step_blocks(_job_blocks(self.text)["summary"])[0])
        completed = subprocess.run(
            ["/bin/bash", "-n"],
            input=script,
            text=True,
            check=False,
            capture_output=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        redirect_statuses = (301, 302, 303, 307, 308)
        secret_token = "secret-redirect-token"
        for status in redirect_statuses:
            with self.subTest(kind="same-origin", status=status):
                completed, requests = _run_summary_with_api(
                    script,
                    environment=_summary_metadata_env(GITHUB_TOKEN=secret_token),
                    routes={
                        _summary_runs_path(page=1): _summary_response(
                            {},
                            status=status,
                            headers={
                                "Location": "{api_base}/redirect-target",
                            },
                        ),
                        "/redirect-target": _summary_response(
                            _summary_api_payload("workflow_runs", [])
                        ),
                    },
                )
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertIn(
                    f"metadata-only summary workflow runs page 1 request rejected redirect: HTTP {status}",
                    completed.stderr,
                )
                self.assertNotIn(secret_token, completed.stderr)
                self.assertEqual(
                    [request["path"] for request in requests],
                    [_summary_runs_path(page=1)],
                )

            with self.subTest(kind="cross-origin", status=status):
                completed, requests = _run_summary_with_api_servers(
                    script,
                    environment=_summary_metadata_env(GITHUB_TOKEN=secret_token),
                    primary_routes={
                        _summary_runs_path(page=1): _summary_response(
                            {},
                            status=status,
                            headers={
                                "Location": "{redirect_api_base}/redirect-target",
                            },
                        )
                    },
                    secondary_routes={
                        "/redirect-target": _summary_response(
                            _summary_api_payload("workflow_runs", [])
                        )
                    },
                )
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertIn(
                    f"metadata-only summary workflow runs page 1 request rejected redirect: HTTP {status}",
                    completed.stderr,
                )
                self.assertNotIn(secret_token, completed.stderr)
                self.assertEqual(
                    [request["path"] for request in requests["primary"]],
                    [_summary_runs_path(page=1)],
                )
                self.assertEqual(requests["secondary"], [])

    def test_event_mode_runtime_separates_metadata_from_full_checks(self):
        mode_step = _step_blocks(_job_blocks(self.text)["event-classifier"])[0]
        script = _literal_run_script(mode_step)
        pr_identity = {
            "CLASSIFIED_HEAD": "1" * 40,
            "EVENT_IDENTITY_RESULT": "success",
            "EVENT_NAME": "pull_request",
            "EVENT_SHA": "a" * 40,
            "PR_HEAD_SHA": "1" * 40,
            "PUSH_SHA": "",
            "TRUSTED_EVENT_KIND": "pull_request",
            "TRUSTED_EVENT_SHA": "1" * 40,
        }
        cases = (
            (
                "metadata",
                {
                    "CLASSIFICATION": "metadata-only",
                    "FULL_FALLBACK": "false",
                    "HEAD_VALID": "true",
                    "IDENTITY_VALID": "true",
                    "ROUTER_RESULT": "success",
                    "RUN_EXPENSIVE": "false",
                },
                0,
            ),
            (
                "push",
                {
                    "CLASSIFICATION": "full",
                    "CLASSIFIED_HEAD": "3" * 40,
                    "EVENT_NAME": "push",
                    "EVENT_SHA": "3" * 40,
                    "FULL_FALLBACK": "false",
                    "HEAD_VALID": "true",
                    "IDENTITY_VALID": "true",
                    "PR_HEAD_SHA": "",
                    "PUSH_SHA": "3" * 40,
                    "ROUTER_RESULT": "success",
                    "RUN_EXPENSIVE": "true",
                    "TRUSTED_EVENT_KIND": "push",
                    "TRUSTED_EVENT_SHA": "3" * 40,
                },
                0,
            ),
            (
                "full",
                {
                    "CLASSIFICATION": "full",
                    "FULL_FALLBACK": "false",
                    "HEAD_VALID": "true",
                    "IDENTITY_VALID": "true",
                    "ROUTER_RESULT": "success",
                    "RUN_EXPENSIVE": "true",
                },
                0,
            ),
            (
                "missing-base-full",
                {
                    "CLASSIFICATION": "full",
                    "FULL_FALLBACK": "true",
                    "HEAD_VALID": "true",
                    "IDENTITY_VALID": "false",
                    "ROUTER_RESULT": "success",
                    "RUN_EXPENSIVE": "true",
                },
                0,
            ),
            (
                "failed-router",
                {
                    "CLASSIFICATION": "",
                    "FULL_FALLBACK": "",
                    "HEAD_VALID": "",
                    "IDENTITY_VALID": "",
                    "ROUTER_RESULT": "failure",
                    "RUN_EXPENSIVE": "",
                },
                1,
            ),
            (
                "successful-metadata-incoherent-ref",
                {
                    "CLASSIFICATION": "metadata-only",
                    "FULL_FALLBACK": "false",
                    "HEAD_VALID": "true",
                    "IDENTITY_VALID": "true",
                    "ROUTER_RESULT": "success",
                    "RUN_EXPENSIVE": "false",
                    "TRUSTED_EVENT_KIND": "none",
                    "TRUSTED_EVENT_SHA": "",
                },
                1,
            ),
            (
                "push-metadata-router-output",
                {
                    "CLASSIFICATION": "metadata-only",
                    "CLASSIFIED_HEAD": "3" * 40,
                    "EVENT_NAME": "push",
                    "EVENT_SHA": "3" * 40,
                    "FULL_FALLBACK": "false",
                    "HEAD_VALID": "true",
                    "IDENTITY_VALID": "true",
                    "PR_HEAD_SHA": "",
                    "PUSH_SHA": "3" * 40,
                    "ROUTER_RESULT": "success",
                    "RUN_EXPENSIVE": "false",
                    "TRUSTED_EVENT_KIND": "push",
                    "TRUSTED_EVENT_SHA": "3" * 40,
                },
                1,
            ),
            (
                "pr-metadata-cross-event-kind",
                {
                    "CLASSIFICATION": "metadata-only",
                    "FULL_FALLBACK": "false",
                    "HEAD_VALID": "true",
                    "IDENTITY_VALID": "true",
                    "ROUTER_RESULT": "success",
                    "RUN_EXPENSIVE": "false",
                    "TRUSTED_EVENT_KIND": "push",
                },
                1,
            ),
            (
                "unsupported-event-metadata-router-output",
                {
                    "CLASSIFICATION": "metadata-only",
                    "EVENT_NAME": "schedule",
                    "FULL_FALLBACK": "false",
                    "HEAD_VALID": "true",
                    "IDENTITY_VALID": "true",
                    "ROUTER_RESULT": "success",
                    "RUN_EXPENSIVE": "false",
                    "TRUSTED_EVENT_KIND": "none",
                    "TRUSTED_EVENT_SHA": "",
                },
                1,
            ),
            (
                "successful-full-stale-trusted-head",
                {
                    "CLASSIFICATION": "full",
                    "FULL_FALLBACK": "false",
                    "HEAD_VALID": "true",
                    "IDENTITY_VALID": "true",
                    "ROUTER_RESULT": "success",
                    "RUN_EXPENSIVE": "true",
                    "TRUSTED_EVENT_SHA": "9" * 40,
                },
                1,
            ),
            (
                "invalid-metadata",
                {
                    "CLASSIFICATION": "metadata-only",
                    "FULL_FALLBACK": "false",
                    "HEAD_VALID": "true",
                    "IDENTITY_VALID": "false",
                    "ROUTER_RESULT": "success",
                    "RUN_EXPENSIVE": "false",
                },
                1,
            ),
            (
                "metadata-full-fallback",
                {
                    "CLASSIFICATION": "metadata-only",
                    "FULL_FALLBACK": "true",
                    "HEAD_VALID": "true",
                    "IDENTITY_VALID": "false",
                    "ROUTER_RESULT": "success",
                    "RUN_EXPENSIVE": "false",
                },
                1,
            ),
            (
                "fallback-with-valid-identity",
                {
                    "CLASSIFICATION": "full",
                    "FULL_FALLBACK": "true",
                    "HEAD_VALID": "true",
                    "IDENTITY_VALID": "true",
                    "ROUTER_RESULT": "success",
                    "RUN_EXPENSIVE": "true",
                },
                1,
            ),
            (
                "fallback-with-invalid-head",
                {
                    "CLASSIFICATION": "full",
                    "FULL_FALLBACK": "true",
                    "HEAD_VALID": "false",
                    "IDENTITY_VALID": "false",
                    "ROUTER_RESULT": "success",
                    "RUN_EXPENSIVE": "true",
                },
                1,
            ),
        )
        for name, environment, expected in cases:
            with self.subTest(name=name):
                completed = subprocess.run(
                    ["/bin/bash", "-c", script],
                    cwd=ROOT,
                    env={**os.environ, **pr_identity, **environment},
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, expected, completed.stderr)

    def test_push_metadata_router_output_falls_back_full_and_fails_summary(self):
        jobs = _job_blocks(self.text)
        mode_script = _literal_run_script(_step_blocks(jobs["event-classifier"])[0])
        push_sha = "3" * 40
        mode = subprocess.run(
            ["/bin/bash", "-c", mode_script],
            cwd=ROOT,
            env={
                **os.environ,
                "CLASSIFICATION": "metadata-only",
                "CLASSIFIED_HEAD": push_sha,
                "EVENT_IDENTITY_RESULT": "success",
                "EVENT_NAME": "push",
                "EVENT_SHA": push_sha,
                "FULL_FALLBACK": "false",
                "HEAD_VALID": "true",
                "IDENTITY_VALID": "true",
                "PR_HEAD_SHA": "",
                "PUSH_SHA": push_sha,
                "ROUTER_RESULT": "success",
                "RUN_EXPENSIVE": "false",
                "TRUSTED_EVENT_KIND": "push",
                "TRUSTED_EVENT_SHA": push_sha,
            },
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(mode.returncode, 1)
        self.assertIn("metadata event mode is not authoritative", mode.stderr)

        event = {
            "classifier_result": "failure",
            "event_name": "push",
            "payload": {
                "after": push_sha,
                "before": "2" * 40,
                "ref": "refs/heads/master",
            },
            "runner": {
                "github_ref": "refs/heads/master",
                "github_sha": push_sha,
                "pr_base_sha": "",
                "pr_head_sha": "",
                "push_sha": push_sha,
            },
        }
        self.assertEqual(
            _triggered_jobs(self.text, event),
            CANDIDATE_FULL_JOBS | {"patch-release"},
        )

        summary_script = _literal_run_script(_step_blocks(jobs["summary"])[0])
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="push-metadata-summary-",
            dir=artifact_root,
        ) as temporary:
            summary = subprocess.run(
                ["/bin/bash", "-c", summary_script],
                cwd=ROOT,
                env={
                    **os.environ,
                    "BUILD_RESULT": "success",
                    "CLASSIFICATION": "metadata-only",
                    "CLASSIFIED_BASE_SHA": "",
                    "CLASSIFIED_BUILD_SHA": push_sha,
                    "CLASSIFIER_RESULT": "failure",
                    "EXTENDED_HOST_TESTS_RESULT": "success",
                    "FALLBACK_IDENTITY_RESULT": "success",
                    "FALLBACK_KIND": "push",
                    "FALLBACK_SHA": push_sha,
                    "FULL_FALLBACK": "false",
                    "GITHUB_EVENT_NAME": "push",
                    "GITHUB_REF": "refs/heads/master",
                    "GITHUB_STEP_SUMMARY": str(Path(temporary) / "summary.md"),
                    "HEAD_VALID": "true",
                    "HOST_TESTS_RESULT": "success",
                    "IDENTITY_VALID": "true",
                    "LEGACY_RESULT": "success",
                    "PATCH_RELEASE_RESULT": "success",
                    "PR_BASE_SHA": "",
                    "PR_HEAD_SHA": "",
                    "PUSH_SHA": push_sha,
                    "RAW_PUSH_SHA": push_sha,
                    "RUN_EXPENSIVE": "false",
                },
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(summary.returncode, 1)
        self.assertIn("exact-push fallback jobs completed", summary.stderr)

    def test_classifier_bootstrap_preserves_incomplete_pr_identity(self):
        classifier_steps = _step_blocks(_job_blocks(self.text)["event-router"])
        script = _literal_run_script(classifier_steps[3])
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="workflow-classifier-bootstrap-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            cases = (
                (
                    "full-pr", "pull_request", "refs/pull/177/merge",
                    "2" * 40, "master", '"master"', "1" * 40, "", "a" * 40,
                    "2" * 40, "1" * 40, "true", "true", "false",
                ),
                (
                    "missing-base-sha", "pull_request", "refs/pull/177/merge",
                    "", "master", '"master"', "1" * 40, "", "a" * 40,
                    "", "1" * 40, "true", "false", "true",
                ),
                (
                    "missing-base-ref", "pull_request", "refs/pull/177/merge",
                    "2" * 40, "", "null", "1" * 40, "", "a" * 40,
                    "2" * 40, "1" * 40, "true", "false", "true",
                ),
                (
                    "empty-base-ref", "pull_request", "refs/pull/177/merge",
                    "2" * 40, "", '""', "1" * 40, "", "a" * 40,
                    "2" * 40, "1" * 40, "true", "false", "true",
                ),
                (
                    "malformed-base-ref", "pull_request", "refs/pull/177/merge",
                    "2" * 40, "7", "7", "1" * 40, "", "a" * 40,
                    "2" * 40, "1" * 40, "true", "false", "true",
                ),
                (
                    "space-base-ref", "pull_request", "refs/pull/177/merge",
                    "2" * 40, " ", '" "', "1" * 40, "", "a" * 40,
                    "2" * 40, "1" * 40, "true", "false", "true",
                ),
                (
                    "embedded-space-base-ref", "pull_request", "refs/pull/177/merge",
                    "2" * 40, "bad ref", '"bad ref"', "1" * 40, "", "a" * 40,
                    "2" * 40, "1" * 40, "true", "false", "true",
                ),
                (
                    "double-dot-base-ref", "pull_request", "refs/pull/177/merge",
                    "2" * 40, "topic..name", '"topic..name"', "1" * 40, "", "a" * 40,
                    "2" * 40, "1" * 40, "true", "false", "true",
                ),
                (
                    "dot-component-base-ref", "pull_request", "refs/pull/177/merge",
                    "2" * 40, "topic/.name", '"topic/.name"', "1" * 40, "", "a" * 40,
                    "2" * 40, "1" * 40, "true", "false", "true",
                ),
                (
                    "valid-slash-base-ref", "pull_request", "refs/pull/177/merge",
                    "2" * 40, "topic/feature-name", '"topic/feature-name"',
                    "1" * 40, "", "a" * 40,
                    "2" * 40, "1" * 40, "true", "true", "false",
                ),
                (
                    "malformed-base-sha", "pull_request", "refs/pull/177/merge",
                    "7", "master", '"master"', "1" * 40, "", "a" * 40,
                    "", "1" * 40, "true", "false", "true",
                ),
                (
                    "missing-head", "pull_request", "refs/pull/177/merge",
                    "2" * 40, "master", '"master"', "", "", "a" * 40,
                    "2" * 40, "", "false", "false", "false",
                ),
                (
                    "push", "push", "refs/heads/master",
                    "", "", "null", "", "3" * 40, "3" * 40,
                    "", "3" * 40, "true", "true", "false",
                ),
                (
                    "push-mismatch", "push", "refs/heads/master",
                    "", "", "null", "", "3" * 40, "4" * 40,
                    "", "", "false", "false", "false",
                ),
                (
                    "push-missing", "push", "refs/heads/master",
                    "", "", "null", "", "", "3" * 40,
                    "", "", "false", "false", "false",
                ),
                (
                    "push-nonmaster", "push", "refs/heads/other",
                    "", "", "null", "", "3" * 40, "3" * 40,
                    "", "", "false", "false", "false",
                ),
            )
            for (
                name, event_name, ref, base, base_ref, base_ref_json, head,
                push_sha, raw_push_sha, expected_base, expected_head, head_valid,
                identity_valid, full_fallback,
            ) in cases:
                with self.subTest(name=name):
                    output = sandbox / f"{name}.out"
                    completed = subprocess.run(
                        ["/bin/bash", "-c", script],
                        cwd=sandbox,
                        env={
                            **os.environ,
                            "GITHUB_EVENT_NAME": event_name,
                            "GITHUB_EVENT_PATH": str(sandbox / "unused.json"),
                            "GITHUB_OUTPUT": str(output),
                            "GITHUB_REF": ref,
                            "GITHUB_SHA": raw_push_sha,
                            "PR_BASE_SHA": base,
                            "PR_BASE_REF": base_ref,
                            "PR_BASE_REF_JSON": base_ref_json,
                            "PR_BASE_SHA_JSON": json.dumps(base),
                            "PR_HEAD_SHA": head,
                            "PUSH_SHA": push_sha,
                            "VALIDATED_FALLBACK_KIND": (
                                "pull_request"
                                if event_name == "pull_request" and expected_head
                                else "push"
                                if event_name == "push" and expected_head
                                else "none"
                            ),
                            "VALIDATED_FALLBACK_SHA": expected_head,
                        },
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    values = dict(
                        line.split("=", 1)
                        for line in output.read_text(encoding="ascii").splitlines()
                    )
                    self.assertEqual(values["expected_base"], expected_base)
                    self.assertEqual(values["expected_head"], expected_head)
                    self.assertEqual(values["head_valid"], head_valid)
                    self.assertEqual(values["identity_valid"], identity_valid)
                    self.assertEqual(values["full_fallback"], full_fallback)
                    self.assertEqual(values["run_expensive"], "true")
                    if event_name == "pull_request":
                        self.assertNotEqual(values["expected_head"], raw_push_sha)

    def test_classifier_bootstrap_base_refs_match_parsed_fixture(self):
        classifier_steps = _step_blocks(_job_blocks(self.text)["event-router"])
        script = _literal_run_script(classifier_steps[3])
        fixture = json.loads(EVENT_FIXTURE.read_text(encoding="utf-8"))
        ref_cases = list(fixture["base_ref_validation_cases"]) + [
            {
                "accepted": False,
                "id": "over-safety-bound",
                "ref": "a" * (event_classifier.MAX_BRANCH_REF_BYTES + 1),
            }
        ]
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="workflow-bootstrap-base-ref-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            for ref_case in ref_cases:
                with self.subTest(case=ref_case["id"]):
                    output = sandbox / f"{ref_case['id']}.out"
                    completed = subprocess.run(
                        ["/bin/bash", "-c", script],
                        cwd=sandbox,
                        env={
                            **os.environ,
                            "GITHUB_EVENT_NAME": "pull_request",
                            "GITHUB_EVENT_PATH": str(sandbox / "unused.json"),
                            "GITHUB_OUTPUT": str(output),
                            "GITHUB_REF": "refs/pull/177/merge",
                            "GITHUB_SHA": "a" * 40,
                            "PR_BASE_REF": ref_case["ref"],
                            "PR_BASE_REF_JSON": json.dumps(ref_case["ref"]),
                            "PR_BASE_SHA": "2" * 40,
                            "PR_BASE_SHA_JSON": json.dumps("2" * 40),
                            "PR_HEAD_SHA": "1" * 40,
                            "PUSH_SHA": "",
                            "VALIDATED_FALLBACK_KIND": "pull_request",
                            "VALIDATED_FALLBACK_SHA": "1" * 40,
                        },
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    values = dict(
                        line.split("=", 1)
                        for line in output.read_text(encoding="ascii").splitlines()
                    )
                    self.assertEqual(
                        values["identity_valid"],
                        "true" if ref_case["accepted"] else "false",
                    )
                    self.assertEqual(
                        values["full_fallback"],
                        "false" if ref_case["accepted"] else "true",
                    )
                    self.assertEqual(values["expected_head"], "1" * 40)

    def test_comment_text_is_not_treated_as_run_block_evidence(self):
        changed = self.text.replace(
            "        make legacy -j2\n",
            "        true\n        # make legacy -j2\n",
            1,
        )
        self.assertTrue(any("legacy job lost" in error for error in _errors(changed, False)))

    def test_duplicate_modern_gate_in_master_host_fails(self):
        changed = self.text.replace(
            "    - name: Run CJK font gates\n",
            "    - name: Duplicate modern gate\n"
            "      run: make expansion-modern-linker-check MODERN_CONFIG=debug MODERN_ABI=aapcs\n\n"
            "    - name: Run CJK font gates\n",
            1,
        )
        self.assertTrue(any("repeats Build-owned" in error for error in _errors(changed, False)))

    def test_build_cannot_silently_skip_required_analyzer_checks(self):
        changed = self.text.replace(" CODEQL_REQUIRE_FANALYZER=1", "", 1)
        self.assertTrue(
            any(
                "must require analyzer support" in error
                for error in _errors(changed, False)
            )
        )

    def test_all_locales_gate_cannot_regress_to_profile_prerequisite_only(self):
        changed = self.text.replace(
            MAP_MENU_PRESENTATION_GATE,
            "make expansion-modern-all-locales-all-features-check -j1",
            1,
        )
        self.assertNotEqual(changed, self.text)
        self.assertTrue(
            any(
                "must gate the all-locales profile through map-menu presentation"
                in error
                for error in _errors(changed, False)
            )
        )

    def test_retired_workflow_remote_completion_dependency_fails(self):
        changed = MAKEFILE.read_text(encoding="utf-8").replace(
            "--workflow build.yml",
            f"--workflow {RETIRED_WORKFLOW_FILENAME}",
            1,
        )
        self.assertTrue(any("retired workflow" in error for error in _remote_completion_errors(changed)))

    def test_pull_request_remote_completion_dependency_fails(self):
        changed = MAKEFILE.read_text(encoding="utf-8").replace("--event push ", "", 1)
        self.assertTrue(any("--event push" in error for error in _remote_completion_errors(changed)))

    def test_comment_only_change_preserves_contract(self):
        self.assertEqual(_errors(self.text + "\n# no graph change\n", False), [])


if __name__ == "__main__":
    unittest.main()
