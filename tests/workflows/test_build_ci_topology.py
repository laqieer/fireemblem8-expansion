"""Structural contract for consolidated candidate and master Build CI."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
FULL_MATRIX = ROOT / ".github" / "workflows" / "full-matrix.yml"
MAKEFILE = ROOT / "Makefile"
MASTER_WORKER_CONDITION = (
    "${{ github.event_name == 'push' && github.ref == 'refs/heads/master' }}"
)
SUMMARY_CONDITION = (
    "${{ always() && github.event_name == 'push' && github.ref == 'refs/heads/master' }}"
)
SUMMARY_NEEDS = "needs: [host-tests, build, master-host-tests, legacy, patch-release]"


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


def _contains_command(job: str, command: str) -> bool:
    return _normalise(command) in _normalise(job)


def _make_recipe(text: str, target: str) -> str:
    match = re.search(
        rf"^{re.escape(target)}:\n(?P<recipe>(?:\t.*\n?)*)",
        text,
        re.MULTILINE,
    )
    if match is None:
        raise AssertionError(f"missing Make target: {target}")
    return match.group("recipe")


def _errors(text: str, full_matrix_exists: bool) -> list[str]:
    errors = []
    header = text[: text.index("\njobs:\n")]
    if "pull_request:" not in header or "push:" not in header:
        errors.append("Build must retain pull-request and push triggers")
    if "workflow_dispatch" in header:
        errors.append("Build must not expose a manual Matrix trigger")
    if full_matrix_exists:
        errors.append("the standalone Full Matrix workflow must be deleted")

    jobs = _job_blocks(text)
    expected_jobs = {
        "host-tests",
        "build",
        "master-host-tests",
        "legacy",
        "patch-release",
        "summary",
    }
    if set(jobs) != expected_jobs:
        errors.append(f"Build job set differs from consolidated contract: {sorted(jobs)}")
        return errors

    for job_name in ("host-tests", "build"):
        if MASTER_WORKER_CONDITION in jobs[job_name]:
            errors.append(f"{job_name} must remain available to pull-request candidates")

    for job_name in ("master-host-tests", "legacy", "patch-release"):
        if f"if: {MASTER_WORKER_CONDITION}" not in jobs[job_name]:
            errors.append(f"{job_name} must be master-push-only")
        if "needs:" in jobs[job_name]:
            errors.append(f"{job_name} must not create a serial master critical path")

    summary = jobs["summary"]
    if f"if: {SUMMARY_CONDITION}" not in summary:
        errors.append("summary must be master-push-only and run after failed master jobs")
    if SUMMARY_NEEDS not in summary:
        errors.append("summary must depend on every required master Build job")
    if '[ "$result" != "success" ]' not in summary:
        errors.append("summary must fail closed")

    master_host = jobs["master-host-tests"]
    for command in (
        "make -f cjk_fonts.mk cjk-fonts-check cjk-fonts-test",
        "python3 -m unittest discover -s scripts/texttools/tests -p 'test_multilang_codec*.py' -v",
        "python3 -m unittest discover -s scripts/modernize/tests -p 'test_expansion_config.py' -v",
        "python3 -m unittest discover -s scripts/linker_report/tests -p 'test_*.py' -v",
    ):
        if not _contains_command(master_host, command):
            errors.append(f"master host lost unique evidence: {command}")

    for duplicate in (
        "scripts/artifact_guard",
        "scripts/docs_check_tests",
        "make generated-data",
        "scripts.localization.game_locales",
        "make game-localization-test",
        "expansion-modern-linker-check",
    ):
        if _contains_command(master_host, duplicate):
            errors.append(f"master host repeats Build-owned evidence: {duplicate}")

    for command in (
        "scripts.localization.game_locales check-crosswalk",
        "scripts.localization.game_locales check-raw-closure",
    ):
        if not _contains_command(jobs["host-tests"], command):
            errors.append(f"candidate host lost Build-owned evidence: {command}")

    legacy = jobs["legacy"]
    for command in ("make legacy -j2", "make -C mgfembp compare"):
        if not _contains_command(legacy, command):
            errors.append(f"legacy job lost unique evidence: {command}")

    build = jobs["build"]
    for command in (
        "expansion-modern-linker-check MODERN_CONFIG=debug",
        "expansion-modern-linker-check MODERN_CONFIG=release",
    ):
        if not _contains_command(build, command):
            errors.append(f"build lost canonical modern evidence: {command}")
    return errors


def _remote_completion_errors(makefile_text: str) -> list[str]:
    recipe = _make_recipe(makefile_text, "remote-completion-check")
    required = (
        "--branch master --commit",
        "--workflow build.yml",
        "requires merged master",
    )
    errors = [f"remote completion lacks {item}" for item in required if item not in recipe]
    if "full-matrix" in recipe:
        errors.append("remote completion still depends on deleted Matrix workflow")
    return errors


class ConsolidatedBuildTopologyTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_real_workflow_consolidates_master_evidence(self):
        self.assertEqual(_errors(self.text, FULL_MATRIX.exists()), [])
        self.assertEqual(
            _remote_completion_errors(MAKEFILE.read_text(encoding="utf-8")),
            [],
        )

    def test_master_worker_removed_from_pull_request_path_fails(self):
        changed = self.text.replace(
            f"if: {MASTER_WORKER_CONDITION}",
            "if: ${{ github.event_name == 'push' }}",
            1,
        )
        self.assertTrue(any("master-push-only" in error for error in _errors(changed, False)))

    def test_serial_master_worker_dependency_fails(self):
        changed = self.text.replace(
            "  patch-release:\n",
            "  patch-release:\n    needs: [build]\n",
            1,
        )
        self.assertTrue(any("serial master critical path" in error for error in _errors(changed, False)))

    def test_missing_summary_dependency_fails(self):
        changed = self.text.replace(
            SUMMARY_NEEDS,
            "needs: [host-tests, build, master-host-tests, legacy]",
            1,
        )
        self.assertTrue(any("summary must depend" in error for error in _errors(changed, False)))

    def test_duplicate_modern_gate_in_master_host_fails(self):
        changed = self.text.replace(
            "    - name: Run CJK font gates\n",
            "    - name: Duplicate modern gate\n"
            "      run: make expansion-modern-linker-check MODERN_CONFIG=debug MODERN_ABI=aapcs\n\n"
            "    - name: Run CJK font gates\n",
            1,
        )
        self.assertTrue(any("repeats Build-owned" in error for error in _errors(changed, False)))

    def test_matrix_remote_completion_dependency_fails(self):
        changed = MAKEFILE.read_text(encoding="utf-8").replace(
            "--workflow build.yml",
            "--workflow full-matrix.yml",
            1,
        )
        self.assertTrue(any("deleted Matrix" in error for error in _remote_completion_errors(changed)))

    def test_comment_only_change_preserves_contract(self):
        self.assertEqual(_errors(self.text + "\n# no graph change\n", False), [])


if __name__ == "__main__":
    unittest.main()
