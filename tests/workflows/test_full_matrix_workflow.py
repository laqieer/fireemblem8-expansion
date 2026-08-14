"""Static contract tests for the manually dispatched full-matrix workflow."""

from __future__ import annotations

import importlib.util
import re
import shlex
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.release_rehearsal import workflow_guard as wg

WORKFLOW = ROOT / ".github" / "workflows" / "full-matrix.yml"
README = ROOT / "README.md"
LOCALIZATION_DOC = ROOT / "docs" / "localization.md"
FRAMEWORK_SUPPORT_DOC = ROOT / "docs" / "framework-support.md"
GAME_LOCALE_SOURCES_DOC = ROOT / "docs" / "game_locale_sources.md"
CHECKOUT_PIN = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
FULL_MATRIX_BADGE = (
    "[![Full Matrix CI]"
    "(https://github.com/laqieer/fireemblem8-expansion/actions/workflows/"
    "full-matrix.yml/badge.svg)]"
    "(https://github.com/laqieer/fireemblem8-expansion/actions/workflows/"
    "full-matrix.yml)"
)


def job_block(text: str, job_id: str) -> str:
    match = re.search(
        rf"^  {re.escape(job_id)}:\n(?P<body>.*?)(?=^  [a-z][a-z0-9-]*:\n|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError(f"missing workflow job: {job_id}")
    return match.group(0)


def command_lines(text: str, prefix: str) -> list[str]:
    commands = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            commands.append(stripped)
        elif stripped.startswith("run: ") and stripped[5:].startswith(prefix):
            commands.append(stripped[5:])
    return commands


class FullMatrixWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_identity_dispatch_only_and_no_inputs(self):
        self.assertTrue(WORKFLOW.is_file())
        self.assertTrue(self.text.startswith("name: Full Matrix CI\n\n"))
        self.assertRegex(
            self.text,
            r"\Aname: Full Matrix CI\n\non:\n  workflow_dispatch: \{\}\n\npermissions:",
        )
        for trigger in ("pull_request:", "push:", "schedule:", "repository_dispatch:"):
            self.assertNotIn(trigger, self.text)
        self.assertNotIn("inputs:", self.text)
        self.assertNotIn("github.event.inputs", self.text)

    def test_shared_structural_workflow_guard_contract_is_clean(self):
        self.assertEqual(wg.validate_workflow_contract(self.text, "full-matrix"), [])

    def test_readme_badge_matches_workflow_name_and_path(self):
        readme = README.read_text(encoding="utf-8")
        self.assertEqual(readme.splitlines().count(FULL_MATRIX_BADGE), 1)
        self.assertEqual(
            len([line for line in readme.splitlines() if "[![Full Matrix CI]" in line]),
            1,
        )
        self.assertRegex(self.text, r"\Aname: Full Matrix CI\n")

    def test_permissions_and_concurrency_are_fail_safe(self):
        self.assertRegex(
            self.text,
            r"permissions:\n  contents: read\n\nconcurrency:\n"
            r"  group: \$\{\{ github\.workflow \}\}-\$\{\{ github\.ref \}\}\n"
            r"  cancel-in-progress: true",
        )
        self.assertNotRegex(self.text, r"^\s+[a-z-]+:\s*write\s*$")
        self.assertNotIn("secrets.", self.text)

    def test_required_jobs_and_realistic_timeouts_exist(self):
        jobs = re.findall(r"^  ([a-z][a-z0-9-]*):\n", self.text, re.MULTILINE)
        self.assertEqual(
            jobs,
            ["host", "modern", "legacy", "release-evidence", "summary"],
        )
        self.assertIn("timeout-minutes: 30", job_block(self.text, "host"))
        self.assertIn("timeout-minutes: 60", job_block(self.text, "modern"))
        self.assertIn("timeout-minutes: 60", job_block(self.text, "legacy"))
        self.assertIn("timeout-minutes: 20", job_block(self.text, "release-evidence"))
        self.assertIn("timeout-minutes: 5", job_block(self.text, "summary"))

    def test_checkout_is_exact_recursive_and_credential_free(self):
        self.assertEqual(self.text.count(f"uses: {CHECKOUT_PIN}"), 4)
        self.assertEqual(self.text.count("ref: ${{ github.sha }}"), 4)
        self.assertEqual(self.text.count("fetch-depth: 0"), 4)
        self.assertEqual(self.text.count("submodules: recursive"), 4)
        self.assertEqual(self.text.count("persist-credentials: false"), 4)
        self.assertNotRegex(self.text, r"uses:\s+actions/checkout@(?![0-9a-f]{40}\b)")

    def test_each_lane_logs_and_verifies_the_exact_sha(self):
        self.assertEqual(self.text.count("printf 'github.sha=%s\\n'"), 4)
        self.assertEqual(self.text.count("printf 'github.ref=%s\\n'"), 4)
        self.assertEqual(
            self.text.count('test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"'),
            4,
        )

    def test_host_uses_each_canonical_broad_gate_once(self):
        host = job_block(self.text, "host")
        lines = [line.strip() for line in host.splitlines()]
        commands = (
            "python3 -m unittest discover -s scripts/artifact_guard_tests "
            "-p 'test_*.py' -v",
            "python3 scripts/artifact_guard.py --revision HEAD",
            "python3 -m unittest discover -s scripts/docs_check_tests "
            "-p 'test_*.py' -v",
            "python3 scripts/check_docs.py --check --check-examples",
            "make generated-data-test",
            "make generated-data-check",
            "make localization-test",
            "make game-localization-test",
            "python3 -m scripts.localization.game_locales check",
            "python3 -m scripts.localization.game_locales check-crosswalk",
            "python3 -m scripts.localization.game_locales check-raw-closure",
            "make -f cjk_fonts.mk cjk-fonts-check cjk-fonts-test",
            "python3 -m unittest discover -s scripts/texttools/tests "
            "-p 'test_multilang_codec*.py' -v",
            "python3 -m unittest discover -s scripts/modernize/tests "
            "-p 'test_expansion_config.py' -v",
            "python3 -m unittest discover -s scripts/linker_report/tests "
            "-p 'test_*.py' -v",
        )
        for command in commands:
            self.assertEqual(
                lines.count(command),
                1,
                msg=f"canonical host command must appear exactly once: {command}",
            )

        for stale_or_duplicate in (
            "make localization-check",
            "make game-localization-check",
            "check-authored-catalogs",
            "check-final-mapping",
        ):
            self.assertNotIn(stale_or_duplicate, host)

        for duplicate_subordinate in (
            "test_text_renderer_native.py",
            "test_text_consumers_native.py",
            "test_text_consumer_audit.py",
        ):
            self.assertNotIn(duplicate_subordinate, host)

    def test_offline_fe8j_boundary_is_honest_and_live_proof_stays_local(self):
        host = job_block(self.text, "host")
        self.assertIn("scripts.localization.game_locales check", host)
        self.assertIn("check-crosswalk", host)
        self.assertIn("check-raw-closure", host)
        self.assertNotIn("--require-live-origin", host)
        self.assertNotIn("baserom.gba", host)
        self.assertNotIn("FE8J_BASEROM", host)
        self.assertNotIn("game-localization-final-check", host)
        self.assertIn("mandatory local maintainer pre-push step", host)
        self.assertIn("not a CI command", host)
        self.assertIn("docs/game_locale_sources.md", host)
        self.assertIn("CI receives no legally restricted FE8J input", host)

    def test_live_provenance_documentation_is_branch_truthful_and_mirrored(self):
        self.assertTrue(GAME_LOCALE_SOURCES_DOC.is_file())
        for path in (LOCALIZATION_DOC, FRAMEWORK_SUPPORT_DOC):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("game-localization-final-check", text)
            self.assertNotIn("FE8J_BASEROM", text)
            self.assertIn("game_locale_sources.md", text)
            self.assertRegex(text, r"local maintainer\s+pre-push")

    def test_every_workflow_make_target_resolves_on_the_checked_out_tree(self):
        commands = command_lines(self.text, "make ")
        self.assertTrue(commands)
        database_cache = {}
        for command in commands:
            normalized = command.replace("${{ matrix.config }}", "debug")
            argv = shlex.split(normalized)
            requested_targets = []
            context_args = []
            index = 1
            while index < len(argv):
                arg = argv[index]
                if arg in ("-f", "--file"):
                    self.assertTrue((ROOT / argv[index + 1]).is_file(), msg=command)
                    context_args.extend((arg, argv[index + 1]))
                    index += 2
                    continue
                if arg in ("-C", "--directory"):
                    self.assertTrue((ROOT / argv[index + 1]).is_dir(), msg=command)
                    context_args.extend((arg, argv[index + 1]))
                    index += 2
                    continue
                if not arg.startswith("-") and "=" not in arg:
                    requested_targets.append(arg)
                index += 1

            cache_key = tuple(context_args)
            if cache_key not in database_cache:
                result = subprocess.run(
                    [
                        argv[0],
                        "--no-print-directory",
                        "-prRn",
                        *context_args,
                        "__fullci_contract_database_only__",
                    ],
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                    check=False,
                )
                database_cache[cache_key] = result.stdout

            database = database_cache[cache_key]
            for target in requested_targets:
                self.assertRegex(
                    database,
                    rf"(?m)^{re.escape(target)}:(?:\s|$)",
                    msg=f"workflow Make target does not exist: {command}",
                )

    def test_every_workflow_python_entry_point_exists_and_cli_help_resolves(self):
        for command in command_lines(self.text, "python3 "):
            argv = shlex.split(command)
            if argv[1:3] == ["-m", "unittest"]:
                source_dir = ROOT / argv[argv.index("-s") + 1]
                pattern = argv[argv.index("-p") + 1]
                self.assertTrue(source_dir.is_dir(), msg=command)
                self.assertTrue(
                    any(source_dir.glob(pattern)),
                    msg=f"workflow unittest pattern matches no tests: {command}",
                )
                continue

            if argv[1] == "-m":
                module = argv[2]
                self.assertIsNotNone(
                    importlib.util.find_spec(module),
                    msg=f"workflow module does not exist: {module}",
                )
                probe = ["python3", "-m", module]
                if len(argv) > 3 and not argv[3].startswith("-"):
                    probe.append(argv[3])
                probe.append("--help")
                result = subprocess.run(
                    probe,
                    cwd=ROOT,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(
                    result.returncode,
                    0,
                    msg=(
                        f"workflow CLI help does not resolve: {command}\n"
                        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
                    ),
                )
                continue

            if argv[1].endswith(".py"):
                self.assertTrue((ROOT / argv[1]).is_file(), msg=command)

        for command in command_lines(self.text, "./"):
            executable = ROOT / shlex.split(command)[0]
            if executable.is_file():
                self.assertTrue(executable.stat().st_mode & 0o111, msg=command)
            else:
                self.assertIn(
                    command,
                    (
                        "./build.sh",
                        './install.sh "$GITHUB_WORKSPACE"',
                        './install.sh "$GITHUB_WORKSPACE/mgfembp"',
                    ),
                    msg=f"unknown non-repository executable: {command}",
                )

    def test_modern_matrix_owns_all_subordinate_linker_runtime_coverage(self):
        modern = job_block(self.text, "modern")
        self.assertIn("fail-fast: false", modern)
        self.assertIn("config: [debug, release]", modern)
        canonical = (
            "make expansion-modern-linker-check "
            "MODERN_CONFIG=${{ matrix.config }} MODERN_ABI=aapcs -j2"
        )
        self.assertEqual(modern.count(canonical), 1)
        for subordinate in (
            "expansion-modern-budget-check",
            "expansion-modern-localization-runtime-",
            "expansion-modern-localization-profile-",
            "expansion-modern-shifted-check",
        ):
            self.assertNotIn(subordinate, self.text)

    def test_legacy_is_baserom_free_pinned_and_checks_identity_manifest(self):
        legacy = job_block(self.text, "legacy")
        self.assertIn(
            "AGBCC_COMMIT: da598c1d918402c42c0c0d7128ba14567f3175e9",
            legacy,
        )
        self.assertIn(
            "MGFEMBP_AGBCC_COMMIT: 63b22f3eb8a8051af30bd80c4795b355e439e7ef",
            legacy,
        )
        self.assertEqual(legacy.count("test ! -e baserom.gba"), 2)
        self.assertEqual(legacy.count("make legacy -j2"), 1)
        self.assertEqual(legacy.count("make -C mgfembp compare"), 1)

    def test_release_evidence_runs_individual_guards_and_expected_blocker(self):
        release = job_block(self.text, "release-evidence")
        commands = (
            "make release-test",
            "make release-full-matrix-workflow-guard",
            "make release-changelog-check",
            "scripts.release_rehearsal.allowlist check "
            '--target-sha "$RELEASE_TARGET_SHA"',
            "python3 -m scripts.release_rehearsal.doc_links",
            "make release-tree-coverage-check",
            "make release-submodule-binding-check",
            "scripts.release_rehearsal.provenance check "
            '--target-sha "$RELEASE_TARGET_SHA"',
            "scan_source_release_candidate(",
            "make release-check-expect-blocked",
        )
        for command in commands:
            self.assertEqual(
                release.count(command),
                1,
                msg=f"release evidence command must appear exactly once: {command}",
            )
        self.assertIn("RELEASE_TARGET_SHA: ${{ github.sha }}", release)
        self.assertNotIn("release-check-require-eligible", release)

    def test_summary_depends_on_every_lane_and_fails_closed(self):
        summary = job_block(self.text, "summary")
        self.assertIn("if: always()", summary)
        self.assertIn(
            "needs: [host, modern, legacy, release-evidence]",
            summary,
        )
        for result in (
            "needs.host.result",
            "needs.modern.result",
            "needs.legacy.result",
            "needs.release-evidence.result",
        ):
            self.assertIn(result, summary)
        for row in (
            "| SHA |",
            "| Ref |",
            "| host |",
            "| modern (debug + release) |",
            "| legacy |",
            "| release-evidence |",
        ):
            self.assertIn(row, summary)
        self.assertIn('if [ "$result" != "success" ]', summary)
        self.assertIn("exit 1", summary)
        self.assertIn('>> "$GITHUB_STEP_SUMMARY"', summary)


if __name__ == "__main__":
    unittest.main()
