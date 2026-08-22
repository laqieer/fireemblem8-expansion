"""Semantic contract tests for the post-merge Full Matrix workflow.

The YAML keys tested here are the public execution and security boundary of a
GitHub Actions workflow. The small parser deliberately reads job/step
structure rather than relying on comments, step order, or prose spelling.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "full-matrix.yml"
BUILD_WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
CHECKOUT_PIN = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
MASTER_CONDITION = "${{ github.ref == 'refs/heads/master' }}"


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


def _step_blocks(job_text: str) -> dict[str, str]:
    matches = list(re.finditer(r"^      - name: (?P<name>.+)\n", job_text, re.MULTILINE))
    return {
        match.group("name"): job_text[
            match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(job_text)
        ]
        for index, match in enumerate(matches)
    }


def _normalise_command(text: str) -> str:
    return " ".join(text.split())


def _contains_command(job_text: str, command: str) -> bool:
    return _normalise_command(command) in _normalise_command(job_text)


def _workflow_errors(workflow_text: str, build_text: str) -> list[str]:
    errors = []
    header = workflow_text[: workflow_text.index("\njobs:\n")]
    if "workflow_dispatch: {}" not in header:
        errors.append("Full Matrix must retain a no-input manual dispatch")
    if re.search(r"^\s{2}(?:pull_request|push|schedule):", header, re.MULTILINE):
        errors.append("Full Matrix must not run automatically or for pull requests")
    if "contents: read" not in header:
        errors.append("Full Matrix must retain read-only permissions")
    if "group: ${{ github.workflow }}-master" not in header:
        errors.append("Full Matrix concurrency must be master-scoped")

    jobs = _job_blocks(workflow_text)
    if set(jobs) != {"host", "modern", "legacy", "summary"}:
        errors.append("Full Matrix must contain only host, modern, legacy, and summary jobs")
        return errors

    for lane in ("host", "modern", "legacy"):
        if f"if: {MASTER_CONDITION}" not in jobs[lane]:
            errors.append(f"{lane} must reject non-master dispatches before execution")
        steps = _step_blocks(jobs[lane])
        checkout = steps.get("Checkout exact master revision", "")
        if CHECKOUT_PIN not in checkout or "ref: ${{ github.sha }}" not in checkout:
            errors.append(f"{lane} must check out the exact dispatched master revision")
        verification = steps.get("Log and verify tested revision", "")
        if 'test "$(git rev-parse HEAD)" = "$GITHUB_SHA"' not in verification:
            errors.append(f"{lane} must verify its event revision without a persisted ledger")

    host = jobs["host"]
    for command in (
        "make -f cjk_fonts.mk cjk-fonts-check cjk-fonts-test",
        "python3 -m unittest discover -s scripts/texttools/tests -p 'test_multilang_codec*.py' -v",
        "python3 -m unittest discover -s scripts/modernize/tests -p 'test_expansion_config.py' -v",
        "python3 -m unittest discover -s scripts/linker_report/tests -p 'test_*.py' -v",
    ):
        if not _contains_command(host, command):
            errors.append(f"Matrix host lost its unique command: {command}")

    build_owned = (
        "scripts/artifact_guard_tests",
        "scripts/artifact_guard.py --revision HEAD",
        "scripts/docs_check_tests",
        "make generated-data-test",
        "make generated-data-check",
        "make game-localization-test",
        "scripts.localization.game_locales",
    )
    for command in build_owned:
        if _contains_command(host, command):
            errors.append(f"Matrix host repeats Build-owned evidence: {command}")
        if not _contains_command(build_text, command):
            errors.append(f"Build no longer owns removed Matrix evidence: {command}")

    if "needs: [host, modern, legacy]" not in jobs["summary"]:
        errors.append("summary must consume every master Matrix lane")
    if "if: always()" not in jobs["summary"]:
        errors.append("summary must run for skipped or failed lanes")
    if '[ "$result" != "success" ]' not in jobs["summary"]:
        errors.append("summary must fail closed on skipped or failed lanes")
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


def _remote_completion_errors(plan: str) -> list[str]:
    required = (
        "git branch --show-current",
        'if [ "$$branch" != master ]',
        "--workflow build.yml",
        "--workflow full-matrix.yml",
        "Full Matrix CI for",
    )
    return [f"remote completion plan lacks {item}" for item in required if item not in plan]


class FullMatrixPolicyTests(unittest.TestCase):
    def setUp(self):
        self.workflow_text = WORKFLOW.read_text(encoding="utf-8")
        self.build_text = BUILD_WORKFLOW.read_text(encoding="utf-8")

    def test_real_workflow_has_master_only_deduplicated_contract(self):
        self.assertEqual(_workflow_errors(self.workflow_text, self.build_text), [])

    def test_non_master_lane_mutation_is_rejected(self):
        changed = self.workflow_text.replace(MASTER_CONDITION, "${{ github.ref != '' }}", 1)
        self.assertTrue(
            any("reject non-master" in error for error in _workflow_errors(changed, self.build_text))
        )

    def test_reintroduced_duplicate_host_evidence_is_rejected(self):
        changed = self.workflow_text.replace(
            "      - name: Run CJK font gates\n",
            "      - name: Reintroduced duplicate\n"
            "        run: make game-localization-test\n\n"
            "      - name: Run CJK font gates\n",
            1,
        )
        self.assertTrue(
            any("repeats Build-owned" in error for error in _workflow_errors(changed, self.build_text))
        )

    def test_comment_only_change_preserves_structural_contract(self):
        changed = self.workflow_text + "\n# Operator notes do not alter the execution graph.\n"
        self.assertEqual(_workflow_errors(changed, self.build_text), [])

    def test_remote_completion_requires_post_merge_build_and_matrix(self):
        recipe = _make_recipe(
            (ROOT / "Makefile").read_text(encoding="utf-8"), "remote-completion-check"
        )
        self.assertEqual(_remote_completion_errors(recipe), [])

    def test_missing_matrix_requirement_is_rejected(self):
        plan = _make_recipe(
            (ROOT / "Makefile").read_text(encoding="utf-8"), "remote-completion-check"
        ).replace("--workflow full-matrix.yml", "--workflow omitted.yml")
        self.assertTrue(
            any("full-matrix" in error for error in _remote_completion_errors(plan))
        )


if __name__ == "__main__":
    unittest.main()
