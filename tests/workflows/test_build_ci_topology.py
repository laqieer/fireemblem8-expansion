"""Structural contract for consolidated candidate and master Build CI."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
RETIRED_WORKFLOW_FILENAME = "full" + "-matrix.yml"
RETIRED_WORKFLOW = ROOT / ".github" / "workflows" / RETIRED_WORKFLOW_FILENAME
MAKEFILE = ROOT / "Makefile"
MASTER_PUBLISHER_CONDITION = (
    "${{ github.event_name == 'push' && github.ref == 'refs/heads/master' }}"
)
COMBINED_WORKERS = ("host-tests", "build", "extended-host-tests", "legacy")
INDEPENDENT_JOBS = COMBINED_WORKERS + ("patch-release",)
SUMMARY_NEEDS = "needs: [host-tests, build, extended-host-tests, legacy]"
PULL_REQUEST_TRIGGER = 'pull_request:\n    branches: [ "master" ]'
PUSH_TRIGGER = 'push:\n    branches: [ "master" ]'
SUMMARY_RESULTS = (
    '"$HOST_TESTS_RESULT"',
    '"$BUILD_RESULT"',
    '"$EXTENDED_HOST_TESTS_RESULT"',
    '"$LEGACY_RESULT"',
)


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


def _run_block_commands(job: str) -> list[str]:
    lines = job.splitlines()
    commands = []
    index = 0
    while index < len(lines):
        inline = re.match(r"^    - run: (?P<value>.+)$", lines[index])
        field = re.match(r"^      run: (?P<value>.+)$", lines[index])
        match = inline or field
        if match is None:
            index += 1
            continue
        if match.group("value") == "|":
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


def _contains_command(job: str, command: str) -> bool:
    return any(
        _normalise(command) in _normalise(run)
        for run in _run_block_commands(job)
    )


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
    if PULL_REQUEST_TRIGGER not in header:
        errors.append("Build must retain the pull-request master trigger")
    if PUSH_TRIGGER not in header:
        errors.append("Build must retain the push master trigger")
    if "workflow_dispatch" in header:
        errors.append("Build must not expose a manual retired-workflow trigger")
    if retired_workflow_exists:
        errors.append("the retired standalone CI workflow must be deleted")

    jobs = _job_blocks(text)
    expected_jobs = {
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

    for job_name, job in jobs.items():
        for command in _run_block_commands(job):
            words = set(command.split())
            if "apt-get" in words and "libpng-dev" in words and "pkg-config" not in words:
                errors.append(f"{job_name} installs libpng-dev without pkg-config")

    for job_name in COMBINED_WORKERS:
        if "if:" in jobs[job_name]:
            errors.append(f"{job_name} must run for pull-request candidates and master pushes")

    if f"if: {MASTER_PUBLISHER_CONDITION}" not in jobs["patch-release"]:
        errors.append("patch-release must remain master-push-only")
    for job_name in INDEPENDENT_JOBS:
        if "needs:" in jobs[job_name]:
            errors.append(f"{job_name} must not create a serial Build critical path")

    summary = jobs["summary"]
    if "if: always()" not in summary:
        errors.append("summary must run after failed combined jobs on both triggers")
    if SUMMARY_NEEDS not in summary:
        errors.append("summary must depend on every required combined Build job")
    loop = summary[summary.index("for result") : summary.index("done", summary.index("for result"))]
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

    def test_real_workflow_consolidates_master_evidence(self):
        self.assertEqual(_errors(self.text, RETIRED_WORKFLOW.exists()), [])
        self.assertEqual(
            _remote_completion_errors(MAKEFILE.read_text(encoding="utf-8")),
            [],
        )

    def test_combined_worker_removed_from_pull_request_path_fails(self):
        changed = self.text.replace(
            "  extended-host-tests:\n",
            f"  extended-host-tests:\n    if: {MASTER_PUBLISHER_CONDITION}\n",
            1,
        )
        self.assertTrue(any("must run for pull-request" in error for error in _errors(changed, False)))

    def test_missing_pull_request_trigger_fails(self):
        changed = self.text.replace(PULL_REQUEST_TRIGGER, 'pull_request:\n    branches: [ "other" ]', 1)
        self.assertTrue(any("pull-request master trigger" in error for error in _errors(changed, False)))

    def test_missing_push_trigger_fails(self):
        changed = self.text.replace(PUSH_TRIGGER, 'push:\n    branches: [ "other" ]', 1)
        self.assertTrue(any("push master trigger" in error for error in _errors(changed, False)))

    def test_independent_jobs_reject_serial_dependencies(self):
        for job_name in INDEPENDENT_JOBS:
            with self.subTest(job_name=job_name):
                changed = self.text.replace(
                    f"  {job_name}:\n",
                    f"  {job_name}:\n    needs: [host-tests]\n",
                    1,
                )
                self.assertTrue(
                    any(
                        f"{job_name} must not create a serial Build critical path" in error
                        for error in _errors(changed, False)
                    )
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

    def test_missing_summary_dependency_fails(self):
        changed = self.text.replace(
            SUMMARY_NEEDS,
            "needs: [host-tests, build, extended-host-tests]",
            1,
        )
        self.assertTrue(any("summary must depend" in error for error in _errors(changed, False)))

    def test_summary_omitting_legacy_result_fails(self):
        changed = self.text.replace(
            '"$LEGACY_RESULT"\n        do',
            '"$HOST_TESTS_RESULT"\n        do',
            1,
        )
        self.assertTrue(any("summary loop omits" in error for error in _errors(changed, False)))

    def test_summary_comparison_outside_loop_fails(self):
        changed = self.text.replace(
            '[ "$result" != "success" ]',
            '[ "$HOST_TESTS_RESULT" != "success" ]',
            1,
        )
        self.assertTrue(any("summary loop must fail closed" in error for error in _errors(changed, False)))

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
