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
LEGACY_APT_PACKAGES = {
    "build-essential",
    "binutils-arm-none-eabi",
    "gcc-arm-none-eabi",
    "libnewlib-arm-none-eabi",
    "libpng-dev",
    "pkg-config",
    "python3-pip",
    "python3-numpy",
    "python3-pil",
}
LEGACY_TOOLCHAIN_EXECUTABLES = {
    "arm-none-eabi-cpp": "gcc-arm-none-eabi",
    "arm-none-eabi-as": "binutils-arm-none-eabi",
    "arm-none-eabi-ld": "binutils-arm-none-eabi",
    "arm-none-eabi-objcopy": "binutils-arm-none-eabi",
}


def parsed_jobs(text: str):
    jobs, violations = wg._workflow_jobs(text)
    if violations:
        raise AssertionError(f"workflow structure is invalid: {violations}")
    return {job.key: job for job in jobs}


def parsed_job_entries(job):
    entries, problems = wg._mapping_entries_at_indent(job.text, 4)
    if problems:
        raise AssertionError(f"job {job.key!r} structure is invalid: {problems}")
    return entries


def parsed_job_steps(job):
    steps, violations = wg._job_steps(job)
    if violations:
        raise AssertionError(f"job {job.key!r} steps are invalid: {violations}")
    return steps


def entry_value(entries, key: str) -> str:
    matches = [entry for entry in entries if entry.key == key]
    if len(matches) != 1:
        raise AssertionError(f"expected exactly one {key!r} mapping, found {len(matches)}")
    return matches[0].value


def named_step(job, name: str):
    matches = [
        (item, entries)
        for item, entries in parsed_job_steps(job)
        if wg._step_name(entries) == name
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one step {name!r} in job {job.key!r}, found {len(matches)}"
        )
    return matches[0]


def step_commands(step_entries) -> tuple[str, ...]:
    run_entries = [entry for entry in step_entries if entry.key == "run"]
    if len(run_entries) != 1:
        raise AssertionError(f"expected exactly one run mapping, found {len(run_entries)}")
    script, problem = wg._run_script(run_entries[0])
    if problem is not None:
        raise AssertionError(problem)
    return tuple(wg._executable_run_lines(script))


def workflow_commands(jobs) -> list[str]:
    return [
        command
        for job in jobs.values()
        for _item, entries in parsed_job_steps(job)
        for command in (
            step_commands(entries)
            if len([entry for entry in entries if entry.key == "run"]) == 1
            else ()
        )
    ]


def legacy_toolchain_contract_violations(text: str) -> list[str]:
    violations = []
    legacy = parsed_jobs(text)["legacy"]
    steps = parsed_job_steps(legacy)
    step_names = [wg._step_name(entries) for _item, entries in steps]

    install_name = "Install archival build dependencies"
    preflight_name = "Preflight archival toolchain executables"
    build_name = "Build tools"
    for name in (install_name, preflight_name, build_name):
        if step_names.count(name) != 1:
            violations.append(
                f"legacy must contain exactly one {name!r} step, found {step_names.count(name)}"
            )
    if violations:
        return violations

    install_index = step_names.index(install_name)
    preflight_index = step_names.index(preflight_name)
    build_index = step_names.index(build_name)
    if not install_index < preflight_index < build_index:
        violations.append(
            "legacy toolchain preflight must run after dependency installation and before builds"
        )

    install_commands = step_commands(steps[install_index][1])
    if len(install_commands) != 1:
        violations.append("legacy dependency step must contain exactly one executable command")
    else:
        marker = "sudo apt-get install -y "
        command = install_commands[0]
        if marker not in command:
            violations.append("legacy dependency step must use sudo apt-get install -y")
        else:
            packages = set(shlex.split(command.split(marker, 1)[1]))
            if packages != LEGACY_APT_PACKAGES:
                violations.append(
                    "legacy dependency packages differ: "
                    f"missing={sorted(LEGACY_APT_PACKAGES - packages)}, "
                    f"unexpected={sorted(packages - LEGACY_APT_PACKAGES)}"
                )

    preflight_commands = step_commands(steps[preflight_index][1])
    observed = {}
    for command in preflight_commands:
        match = re.match(r"^command -v (arm-none-eabi-[a-z0-9-]+)\b", command)
        if match:
            observed[match.group(1)] = command
    if set(observed) != set(LEGACY_TOOLCHAIN_EXECUTABLES):
        violations.append(
            "legacy preflight executables differ: "
            f"missing={sorted(set(LEGACY_TOOLCHAIN_EXECUTABLES) - set(observed))}, "
            f"unexpected={sorted(set(observed) - set(LEGACY_TOOLCHAIN_EXECUTABLES))}"
        )
    for executable, package in LEGACY_TOOLCHAIN_EXECUTABLES.items():
        command = observed.get(executable, "")
        if command and ("::error::" not in command or package not in command):
            violations.append(
                f"legacy preflight for {executable} must name its providing package actionably"
            )

    return violations


class FullMatrixWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.jobs = parsed_jobs(cls.text)

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
        self.assertEqual(
            list(self.jobs),
            ["host", "modern", "legacy", "release-evidence", "summary"],
        )
        expected = {
            "host": "30",
            "modern": "60",
            "legacy": "60",
            "release-evidence": "20",
            "summary": "5",
        }
        for job_name, timeout in expected.items():
            self.assertEqual(
                entry_value(parsed_job_entries(self.jobs[job_name]), "timeout-minutes"),
                timeout,
            )

    def test_checkout_is_exact_recursive_and_credential_free(self):
        for job_name in wg._FULL_MATRIX_LANE_JOBS:
            checkout_steps = wg._full_matrix_checkout_steps(
                parsed_job_steps(self.jobs[job_name])
            )
            self.assertEqual(len(checkout_steps), 1, msg=job_name)
            _index, _item, entries = checkout_steps[0]
            self.assertEqual(entry_value(entries, "uses"), CHECKOUT_PIN)
            with_entries = [entry for entry in entries if entry.key == "with"]
            self.assertEqual(len(with_entries), 1, msg=job_name)
            values, problems = wg._mapping_entries_at_indent(with_entries[0].text, 10)
            self.assertEqual(problems, [], msg=job_name)
            self.assertEqual(entry_value(values, "ref"), "${{ github.sha }}")
            self.assertEqual(entry_value(values, "fetch-depth"), "0")
            self.assertEqual(entry_value(values, "submodules"), "recursive")
            self.assertEqual(entry_value(values, "persist-credentials"), "false")

    def test_each_lane_logs_and_verifies_the_exact_sha(self):
        for job_name in wg._FULL_MATRIX_LANE_JOBS:
            steps = parsed_job_steps(self.jobs[job_name])
            checkout_index, _item, _entries = wg._full_matrix_checkout_steps(steps)[0]
            _verify_item, verify_entries = steps[checkout_index + 1]
            self.assertEqual(
                wg._step_name(verify_entries),
                wg._FULL_MATRIX_VERIFY_STEP_NAME,
            )
            env_entries = [entry for entry in verify_entries if entry.key == "env"]
            self.assertEqual(len(env_entries), 1)
            env_values, problems = wg._mapping_entries_at_indent(env_entries[0].text, 10)
            self.assertEqual(problems, [])
            self.assertEqual(entry_value(env_values, "EXPECTED_SHA"), "${{ github.sha }}")
            self.assertEqual(entry_value(env_values, "EXPECTED_REF"), "${{ github.ref }}")
            self.assertEqual(step_commands(verify_entries), wg._FULL_MATRIX_VERIFY_COMMANDS)

    def test_host_uses_each_canonical_broad_gate_once(self):
        actual_commands = workflow_commands({"host": self.jobs["host"]})
        expected_commands = [
            command
            for (job_name, _step_name), commands in wg._FULL_MATRIX_STEP_COMMANDS.items()
            if job_name == "host"
            for command in commands
        ]
        for command in expected_commands:
            self.assertEqual(
                actual_commands.count(command),
                1,
                msg=f"canonical host command must appear exactly once: {command}",
            )

        for stale_or_duplicate in (
            "make localization-check",
            "make game-localization-check",
            "check-authored-catalogs",
            "check-final-mapping",
        ):
            self.assertFalse(
                any(stale_or_duplicate in command for command in actual_commands)
            )

        for duplicate_subordinate in (
            "test_text_renderer_native.py",
            "test_text_consumers_native.py",
            "test_text_consumer_audit.py",
        ):
            self.assertFalse(
                any(duplicate_subordinate in command for command in actual_commands)
            )

    def test_offline_fe8j_boundary_is_honest_and_live_proof_stays_local(self):
        _item, gate_entries = named_step(
            self.jobs["host"], "Run full-game localization artifact gates"
        )
        self.assertEqual(
            step_commands(gate_entries),
            wg._FULL_MATRIX_STEP_COMMANDS[
                ("host", "Run full-game localization artifact gates")
            ],
        )
        host_commands = workflow_commands({"host": self.jobs["host"]})
        for forbidden in (
            "--require-live-origin",
            "baserom.gba",
            "FE8J_BASEROM",
            "game-localization-final-check",
        ):
            self.assertFalse(any(forbidden in command for command in host_commands))

        _item, boundary_entries = named_step(
            self.jobs["host"], "Record FE8J live-provenance boundary"
        )
        boundary_commands = "\n".join(step_commands(boundary_entries))
        self.assertIn("mandatory local maintainer pre-push step", boundary_commands)
        self.assertIn("not a CI command", boundary_commands)
        self.assertIn("docs/game_locale_sources.md", boundary_commands)
        self.assertIn("CI receives no legally restricted FE8J input", boundary_commands)

    def test_live_provenance_documentation_is_branch_truthful_and_mirrored(self):
        self.assertTrue(GAME_LOCALE_SOURCES_DOC.is_file())
        for path in (LOCALIZATION_DOC, FRAMEWORK_SUPPORT_DOC):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("game-localization-final-check", text)
            self.assertNotIn("FE8J_BASEROM", text)
            self.assertIn("game_locale_sources.md", text)
            self.assertRegex(text, r"local maintainer\s+pre-push")

    def test_every_workflow_make_target_resolves_on_the_checked_out_tree(self):
        commands = [
            command
            for command in workflow_commands(self.jobs)
            if command.startswith("make ")
        ]
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
        commands = workflow_commands(self.jobs)
        for command in [command for command in commands if command.startswith("python3 ")]:
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

        for command in [command for command in commands if command.startswith("./")]:
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
        modern = self.jobs["modern"]
        entries = parsed_job_entries(modern)
        strategy_entries = [entry for entry in entries if entry.key == "strategy"]
        self.assertEqual(len(strategy_entries), 1)
        strategy, problems = wg._mapping_entries_at_indent(strategy_entries[0].text, 6)
        self.assertEqual(problems, [])
        self.assertEqual(entry_value(strategy, "fail-fast"), "false")
        matrix_entries = [entry for entry in strategy if entry.key == "matrix"]
        self.assertEqual(len(matrix_entries), 1)
        matrix, problems = wg._mapping_entries_at_indent(matrix_entries[0].text, 8)
        self.assertEqual(problems, [])
        self.assertEqual(entry_value(matrix, "config"), "[debug, release]")
        canonical = (
            "make expansion-modern-linker-check "
            "MODERN_CONFIG=${{ matrix.config }} MODERN_ABI=aapcs -j2"
        )
        _item, gate_entries = named_step(
            modern, "Run canonical modern linker/runtime gate"
        )
        self.assertEqual(step_commands(gate_entries), (canonical,))
        all_commands = workflow_commands(self.jobs)
        for subordinate in (
            "expansion-modern-budget-check",
            "expansion-modern-localization-runtime-",
            "expansion-modern-localization-profile-",
            "expansion-modern-shifted-check",
        ):
            self.assertFalse(any(subordinate in command for command in all_commands))

    def test_legacy_is_baserom_free_pinned_and_checks_identity_manifest(self):
        legacy = self.jobs["legacy"]
        entries = parsed_job_entries(legacy)
        env_entries = [entry for entry in entries if entry.key == "env"]
        self.assertEqual(len(env_entries), 1)
        env, problems = wg._mapping_entries_at_indent(env_entries[0].text, 6)
        self.assertEqual(problems, [])
        self.assertEqual(
            entry_value(env, "AGBCC_COMMIT"),
            "da598c1d918402c42c0c0d7128ba14567f3175e9",
        )
        self.assertEqual(
            entry_value(env, "MGFEMBP_AGBCC_COMMIT"),
            "63b22f3eb8a8051af30bd80c4795b355e439e7ef",
        )
        legacy_commands = workflow_commands({"legacy": legacy})
        self.assertEqual(legacy_commands.count("test ! -e baserom.gba"), 2)
        self.assertEqual(legacy_commands.count("make legacy -j2"), 1)
        self.assertEqual(legacy_commands.count("make -C mgfembp compare"), 1)

    def test_legacy_installs_and_preflights_required_arm_toolchain(self):
        self.assertEqual(legacy_toolchain_contract_violations(self.text), [])

    def test_legacy_toolchain_contract_rejects_cpp_dependency_and_preflight_mutations(self):
        mutations = {
            "missing gcc package": self.text.replace(
                " binutils-arm-none-eabi gcc-arm-none-eabi libnewlib-arm-none-eabi ",
                " binutils-arm-none-eabi libnewlib-arm-none-eabi ",
                1,
            ),
            "missing cpp preflight": re.sub(
                r"^          command -v arm-none-eabi-cpp .*\n",
                "",
                self.text,
                count=1,
                flags=re.MULTILINE,
            ),
        }
        for name, mutated in mutations.items():
            with self.subTest(name=name):
                self.assertNotEqual(mutated, self.text)
                self.assertTrue(legacy_toolchain_contract_violations(mutated))

    def test_release_evidence_runs_individual_guards_and_expected_blocker(self):
        release = self.jobs["release-evidence"]
        for (job_name, step_name), expected in wg._FULL_MATRIX_STEP_COMMANDS.items():
            if job_name != "release-evidence":
                continue
            _item, entries = named_step(release, step_name)
            self.assertEqual(step_commands(entries), expected)

        entries = parsed_job_entries(release)
        env_entries = [entry for entry in entries if entry.key == "env"]
        self.assertEqual(len(env_entries), 1)
        env, problems = wg._mapping_entries_at_indent(env_entries[0].text, 6)
        self.assertEqual(problems, [])
        self.assertEqual(
            entry_value(env, "RELEASE_TARGET_SHA"),
            "${{ github.sha }}",
        )
        self.assertNotIn(
            "make release-check-require-eligible",
            workflow_commands({"release-evidence": release}),
        )

    def test_summary_depends_on_every_lane_and_fails_closed(self):
        summary = self.jobs["summary"]
        entries = parsed_job_entries(summary)
        self.assertEqual(entry_value(entries, "if"), "always()")
        self.assertEqual(
            wg._decode_flow_identifier_sequence(entry_value(entries, "needs")),
            (wg._FULL_MATRIX_SUMMARY_NEEDS, None),
        )
        env_entries = [entry for entry in entries if entry.key == "env"]
        self.assertEqual(len(env_entries), 1)
        env, problems = wg._mapping_entries_at_indent(env_entries[0].text, 6)
        self.assertEqual(problems, [])
        self.assertEqual(
            {entry.key: entry.value for entry in env},
            wg._FULL_MATRIX_SUMMARY_ENV,
        )
        _item, summary_entries = named_step(
            summary, "Render fail-closed matrix summary"
        )
        self.assertEqual(
            step_commands(summary_entries),
            wg._FULL_MATRIX_SUMMARY_COMMANDS,
        )


if __name__ == "__main__":
    unittest.main()
