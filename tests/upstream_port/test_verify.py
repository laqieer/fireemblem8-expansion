import contextlib
import inspect
import io
import os
import re
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.upstream_port import cli, verify as verify_mod

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUILD_WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "build.yml")
UPSTREAM_PORTING_PATH = os.path.join(REPO_ROOT, "docs", "upstream-porting.md")

_STEP_NAME_RE = re.compile(r"^    - name: (.+)$", re.M)
_SINGLE_RUN_RE = re.compile(r"^      run: (?!\|\s*$)(.+)$", re.M)
_MULTI_RUN_RE = re.compile(r"^      run: \|\n((?:        .*\n?)+)", re.M)

# Steps in build.yml that are pure environment setup (checkout, apt/pip
# installs, building host tools) rather than a pass/fail correctness gate
# `verify` needs to reproduce. Everything else in the workflow is expected
# to have a literal, argv-identical counterpart in verify.gates().
_NON_GATE_STEP_NAMES = {
    # Exact checkout binding is a required workflow invariant, but not a
    # repository correctness gate mirrored by scripts/upstream_port/verify.py.
    # tests/workflows/test_build_ci_checkout.py owns its positive and negative
    # structural coverage.
    "Verify checked-out revision",
    # CI-only authority setup: checkout's exact-ref optimization may omit
    # historical branch commits required by the offline workflow-pilot fixture.
    # Local verify uses an ordinary clone and remains network-independent.
    "Hydrate workflow-pilot Git authority",
    # host-tests job setup: installs build-essential + libmgba-dev only (no
    # arm-none-eabi toolchain), so it is environment setup, not a gate.
    "Install host-only dependencies (no arm-none-eabi toolchain)",
    # build job setup
    "Install dependencies",
    "Build tools",
    # Combined-gate extended and archival job setup.
    "Install extended host dependencies",
    "Install archival build dependencies",
    "Preflight archival toolchain executables",
    "Install pinned archival agbcc compilers",
}

# Issues #7/#17 remediation: the documentation step is a genuine required
# workflow gate, but it is the sole correctness step deliberately excluded
# from verify.gates(). Its exact commands and position are asserted separately
# below; localization remains part of the current 28-gate candidate mirror.
_DOCS_GOVERNANCE_STEP_NAME = "Check documentation (issues #7/#17)"
_CODEQL_ALERTS_STEP_NAME = "Run CodeQL alert regression suite (issue #84)"
_LOCALIZATION_HOST_STEP_NAME = "Run localization host test suite (issue #18)"
_GAME_LOCALIZATION_WIDTH_STEP_NAME = "Run full-game localization width contract (issue #18)"
_WORKFLOW_CONTRACT_STEP_NAME = "Run workflow contract test suite"
_WORKFLOW_PILOT_TEST_STEP_NAME = (
    "Run workflow-pilot reporter regression suite (issue #176)"
)
_WORKFLOW_PILOT_BASELINE_STEP_NAME = (
    "Validate workflow-pilot baseline against checked-out Git history"
)
_SCRUBBED_PILOT_ENV = (
    "BASH_ENV: ''",
    "ENV: ''",
    "PATH: /usr/bin:/bin",
    "PYTHONPATH: ''",
)


def _parse_workflow_gate_commands(path=BUILD_WORKFLOW_PATH):
    """Read candidate-safe Build CI jobs with stdlib only (no PyYAML).

    ``verify`` is a local, no-secret mirror of all four combined candidate
    workers. Only the master-only publisher and the serial summary are
    excluded at the job level; the separately asserted documentation step is
    the one deliberate command-level exception. This deliberately re-derives
    expected commands from live run blocks instead of hardcoding a copy.
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()
    return _parse_workflow_gate_commands_text(text)


def _parse_workflow_gate_commands_text(text):
    commands = []
    for job_name in ("host-tests", "build", "extended-host-tests", "legacy"):
        job = re.search(
            rf"(?ms)^  {re.escape(job_name)}:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
            text,
        )
        assert job is not None, f"missing candidate Build job {job_name!r}"
        step_matches = list(_STEP_NAME_RE.finditer(job.group("body")))
        assert step_matches, f"no steps found parsing {job_name!r}; workflow format changed?"

        for i, m in enumerate(step_matches):
            step_name = m.group(1).strip()
            start = m.end()
            end = step_matches[i + 1].start() if i + 1 < len(step_matches) else len(job.group("body"))
            block = job.group("body")[start:end]

            if step_name in _NON_GATE_STEP_NAMES:
                continue

            if step_name != _DOCS_GOVERNANCE_STEP_NAME:
                fields = []
                for line in block.splitlines():
                    if not line.strip() or line.lstrip().startswith("#"):
                        continue
                    indent = len(line) - len(line.lstrip(" "))
                    if indent == 6:
                        field = re.match(
                            r"^      (?P<field>[A-Za-z_][A-Za-z0-9_-]*)[ \t]*:",
                            line,
                        )
                        assert field is not None, (
                            f"mirrored gate step {step_name!r} uses unsupported "
                            "direct mapping-key syntax"
                        )
                        fields.append(field.group("field"))
                    elif indent < 8:
                        raise AssertionError(
                            f"mirrored gate step {step_name!r} uses unsupported "
                            "direct mapping indentation"
                        )
                if step_name in {
                    _WORKFLOW_PILOT_TEST_STEP_NAME,
                    _WORKFLOW_PILOT_BASELINE_STEP_NAME,
                }:
                    assert fields == ["env", "run"], (
                        f"protected pilot step {step_name!r} must contain only "
                        f"the reviewed name, env, and run fields, got {fields!r}"
                    )
                    env_match = re.search(
                        r"(?ms)^      env:\n(?P<env>(?:        .+\n)+)"
                        r"^      run:",
                        block,
                    )
                    assert env_match is not None, (
                        f"protected pilot step {step_name!r} lacks its "
                        "reviewed scrubbed environment"
                    )
                    env_entries = tuple(
                        line.strip()
                        for line in env_match.group("env").splitlines()
                        if line.strip()
                    )
                    assert env_entries == _SCRUBBED_PILOT_ENV, (
                        f"protected pilot step {step_name!r} changes its "
                        "reviewed scrubbed environment"
                    )
                else:
                    assert fields == ["run"], (
                        f"mirrored gate step {step_name!r} must contain only "
                        f"the reviewed name and run fields, got {fields!r}"
                    )

            single_m = _SINGLE_RUN_RE.search(block)
            if single_m:
                lines = [single_m.group(1).strip()]
            else:
                multi_m = _MULTI_RUN_RE.search(block)
                assert multi_m, f"step {step_name!r} has no parseable 'run:' block"
                lines = [line.strip() for line in multi_m.group(1).splitlines() if line.strip()]

            if step_name == "Build archival lane without a copyrighted baserom":
                # The workflow adds shell-local `set`/`test` assertions around
                # its one portable verifier command. `verify` owns the
                # executable archival build itself; the workflow topology test
                # owns the surrounding no-baserom shell boundary.
                lines = [line for line in lines if line.startswith("make ")]

            for line in lines:
                commands.append((step_name, shlex.split(line)))

    return commands


class VerifyGatesMirrorWorkflowTests(unittest.TestCase):
    """Assert verify.gates() is a literal, argv-identical, order-preserving
    mirror of the gate steps in .github/workflows/build.yml -- parsed from
    the live workflow file, not a hardcoded copy -- excluding only the
    separately tested standalone documentation-governance step."""

    def test_gate_argv_matches_workflow_commands_in_order(self):
        workflow_commands = [
            (step_name, argv)
            for step_name, argv in _parse_workflow_gate_commands()
            if step_name != _DOCS_GOVERNANCE_STEP_NAME
        ]
        gate_commands = [g.command for g in verify_mod.gates(jobs=2)]

        self.assertEqual(
            len(gate_commands),
            len(workflow_commands),
            f"verify.gates() has {len(gate_commands)} gate(s) but build.yml "
            f"has {len(workflow_commands)} mirrored gate command(s): "
            f"{[c for _, c in workflow_commands]!r}",
        )
        for gate_command, (step_name, workflow_argv) in zip(gate_commands, workflow_commands):
            self.assertEqual(
                gate_command,
                workflow_argv,
                f"gate command {gate_command!r} does not literally match "
                f"build.yml step {step_name!r} command {workflow_argv!r}",
            )

    def test_all_locales_gate_mirrors_required_presentation_target(self):
        gate = next(
            gate
            for gate in verify_mod.gates(jobs=2)
            if gate.name == "modern-all-locales-all-features-profile"
        )
        self.assertEqual(
            gate.command,
            ["make", "expansion-modern-map-menu-presentation-check", "-j1"],
        )
        workflow_commands = dict(_parse_workflow_gate_commands())
        self.assertEqual(
            workflow_commands[
                "Build and verify all-locales/all-features map menu (issues #49/#168)"
            ],
            gate.command,
        )
        self.assertNotIn(
            ["make", "expansion-modern-all-locales-all-features-check", "-j1"],
            [command for _, command in _parse_workflow_gate_commands()],
        )

    def test_checkout_verification_is_not_counted_as_a_mirrored_gate(self):
        parsed = _parse_workflow_gate_commands()
        self.assertNotIn(
            "Verify checked-out revision",
            {step_name for step_name, _ in parsed},
        )
        self.assertFalse(
            any(argv and argv[0].startswith("ACTUAL_SHA=") for _, argv in parsed),
        )

    def test_ci_authority_hydration_is_setup_not_a_local_network_gate(self):
        parsed_names = {
            step_name for step_name, _ in _parse_workflow_gate_commands()
        }
        self.assertNotIn("Hydrate workflow-pilot Git authority", parsed_names)
        self.assertFalse(
            any(
                gate.command[:2] == ["git", "fetch"]
                for gate in verify_mod.gates()
            )
        )

    def test_issue_7_17_docs_governance_is_a_standalone_workflow_step_not_a_verify_gate(self):
        """Docs governance stays outside the current 28-gate candidate mirror
        while remaining required, argv-identical, and immediately after the
        artifact guard in build.yml."""
        names = [g.name for g in verify_mod.gates()]
        self.assertNotIn("docs-check-tests", names)
        self.assertNotIn("docs-check", names)

        all_workflow_commands = _parse_workflow_gate_commands()
        docs_commands = [
            argv
            for step_name, argv in all_workflow_commands
            if step_name == _DOCS_GOVERNANCE_STEP_NAME
        ]
        self.assertEqual(
            docs_commands,
            [
                [
                    "python3",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "scripts/docs_check_tests",
                    "-v",
                ],
                ["python3", "scripts/check_docs.py", "--check", "--check-examples"],
            ],
            "build.yml standalone docs-governance step must still run both "
            "commands, argv-identical, even though verify.gates() no longer "
            "mirrors them",
        )

        ordered_unique_steps = []
        for step_name, _ in all_workflow_commands:
            if not ordered_unique_steps or ordered_unique_steps[-1] != step_name:
                ordered_unique_steps.append(step_name)
        docs_index = ordered_unique_steps.index(_DOCS_GOVERNANCE_STEP_NAME)
        self.assertEqual(
            ordered_unique_steps[docs_index - 1],
            "Check tracked artifacts",
            "docs-governance step must immediately follow the artifact-guard step",
        )
        self.assertEqual(
            ordered_unique_steps[docs_index + 1],
            _CODEQL_ALERTS_STEP_NAME,
            "docs-governance step must immediately precede the #84 alert gate",
        )
        codeql_index = ordered_unique_steps.index(_CODEQL_ALERTS_STEP_NAME)
        self.assertEqual(
            ordered_unique_steps[codeql_index + 1],
            "Check default build lane and quickstart legacy glue (issue #15)",
            "the #84 alert gate must immediately precede the #15 default-lane step",
        )

    def test_issue_18_localization_host_suite_is_in_mirrored_gate_set(self):
        """The current-master localization host suite is mirrored exactly."""
        names = [g.name for g in verify_mod.gates()]
        self.assertIn("localization-host-suite", names)

        all_workflow_commands = _parse_workflow_gate_commands()
        localization_commands = [
            argv
            for step_name, argv in all_workflow_commands
            if step_name == _LOCALIZATION_HOST_STEP_NAME
        ]
        self.assertEqual(
            localization_commands,
            [[
                "python3", "-m", "unittest", "discover", "-s",
                "scripts/localization/tests", "-p", "test_*.py",
            ]],
        )

        ordered_unique_steps = []
        for step_name, _ in all_workflow_commands:
            if not ordered_unique_steps or ordered_unique_steps[-1] != step_name:
                ordered_unique_steps.append(step_name)
        localization_index = ordered_unique_steps.index(_LOCALIZATION_HOST_STEP_NAME)
        self.assertEqual(
            ordered_unique_steps[localization_index - 1],
            _WORKFLOW_PILOT_BASELINE_STEP_NAME,
        )

    def test_issue_18_full_game_width_contract_is_in_mirrored_gate_set(self):
        names = [g.name for g in verify_mod.gates()]
        self.assertIn("game-localization-width-contract", names)
        commands = [
            argv
            for step_name, argv in _parse_workflow_gate_commands()
            if step_name == _GAME_LOCALIZATION_WIDTH_STEP_NAME
        ]
        expected_commands = [
            ["make", "game-localization-test"],
            ["python3", "-m", "scripts.localization.game_locales", "check"],
            ["python3", "-m", "scripts.localization.game_locales", "check-crosswalk"],
            ["python3", "-m", "scripts.localization.game_locales", "check-raw-closure"],
        ]
        self.assertEqual(commands, expected_commands)
        by_name = {gate.name: gate for gate in verify_mod.gates()}
        self.assertEqual(
            [
                by_name["game-localization-width-contract"].command,
                by_name["game-localization-catalog-check"].command,
                by_name["game-localization-crosswalk-check"].command,
                by_name["game-localization-raw-closure-check"].command,
            ],
            expected_commands,
        )

    def test_workflow_contract_suite_is_fast_and_mirrored_exactly(self):
        names = [g.name for g in verify_mod.gates()]
        self.assertIn("workflow-contract-tests", names)

        commands = [
            argv
            for step_name, argv in _parse_workflow_gate_commands()
            if step_name == _WORKFLOW_CONTRACT_STEP_NAME
        ]
        self.assertEqual(
            commands,
            [[
                "python3", "-m", "unittest", "discover", "-s",
                "tests/workflows", "-p", "test_*.py", "-v",
            ]],
        )
        gate = {g.name: g for g in verify_mod.gates()}["workflow-contract-tests"]
        self.assertEqual(gate.command, commands[0])
        self.assertNotIn("make", gate.command)

    def test_workflow_pilot_commands_are_mirrored_exactly_in_order(self):
        workflow_commands = _parse_workflow_gate_commands()
        pilot_commands = [
            (step_name, argv)
            for step_name, argv in workflow_commands
            if step_name
            in {
                _WORKFLOW_PILOT_TEST_STEP_NAME,
                _WORKFLOW_PILOT_BASELINE_STEP_NAME,
            }
        ]
        self.assertEqual(
            pilot_commands,
            [
                (
                    _WORKFLOW_PILOT_TEST_STEP_NAME,
                    [
                        "/usr/bin/python3", "-m", "unittest", "discover", "-s",
                        "scripts/workflow_pilot/tests", "-p", "test_*.py", "-v",
                    ],
                ),
                (
                    _WORKFLOW_PILOT_BASELINE_STEP_NAME,
                    [
                        "/usr/bin/python3", "-m", "scripts.workflow_pilot.reporter",
                        "--repository-root", "$GITHUB_WORKSPACE",
                        "--fixture",
                        "scripts/workflow_pilot/tests/fixtures/baseline.json",
                        "--decisions", ".github/workflow-pilot-decisions.json",
                        "--expected",
                        "scripts/workflow_pilot/tests/fixtures/baseline_expected.json",
                        ">", "/dev/null",
                    ],
                ),
            ],
        )
        by_name = {gate.name: gate.command for gate in verify_mod.gates()}
        self.assertEqual(
            [
                by_name["workflow-pilot-reporter-tests"],
                by_name["workflow-pilot-baseline"],
            ],
            [argv for _, argv in pilot_commands],
        )
        ordered_steps = [step_name for step_name, _ in workflow_commands]
        self.assertLess(
            ordered_steps.index(_WORKFLOW_CONTRACT_STEP_NAME),
            ordered_steps.index(_WORKFLOW_PILOT_TEST_STEP_NAME),
        )
        self.assertLess(
            ordered_steps.index(_WORKFLOW_PILOT_BASELINE_STEP_NAME),
            ordered_steps.index(_LOCALIZATION_HOST_STEP_NAME),
        )

    def test_every_mirrored_gate_rejects_unmodeled_execution_fields(self):
        with open(BUILD_WORKFLOW_PATH, "r", encoding="utf-8") as handle:
            workflow = handle.read()
        mirrored_steps = []
        for step_name, _ in _parse_workflow_gate_commands():
            if (
                step_name != _DOCS_GOVERNANCE_STEP_NAME
                and step_name not in mirrored_steps
            ):
                mirrored_steps.append(step_name)

        variants = (
            "continue-on-error: true",
            "if: ${{ false }}",
            "shell: bash {0} || true",
            "working-directory: scripts",
            "working-directory : scripts",
            '"working-directory": scripts',
            "'working-directory' : scripts",
            '"working-\\u0064irectory": scripts',
            "? working-directory\n      : scripts",
            "!!str working-directory: scripts",
            "{working-directory: scripts}",
        )
        for step_name in mirrored_steps:
            marker = f"    - name: {step_name}\n"
            for variant in variants:
                with self.subTest(step=step_name, variant=variant):
                    changed = workflow.replace(
                        marker,
                        marker + f"      {variant}\n",
                        1,
                    )
                    self.assertNotEqual(changed, workflow)
                    with self.assertRaisesRegex(
                        AssertionError,
                        "unsupported direct mapping|only the reviewed "
                        "(?:name and run|name, env, and run)",
                    ):
                        _parse_workflow_gate_commands_text(changed)

    def test_issue_15_default_lane_and_quickstart_gates_present(self):
        names = [g.name for g in verify_mod.gates()]
        self.assertIn("default-lane-check", names)
        self.assertIn("quickstart-legacy-check", names)

        by_name = {g.name: g for g in verify_mod.gates()}
        self.assertEqual(
            by_name["default-lane-check"].command,
            [
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/modernize/tests",
                "-p",
                "test_build_default_lane.py",
                "-v",
            ],
        )
        self.assertEqual(
            by_name["quickstart-legacy-check"].command,
            [
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/modernize/tests",
                "-p",
                "test_quickstart.py",
                "-v",
            ],
        )

    def test_gate_list_full_ordered_names(self):
        # All 28 current candidate Build gates remain; docs governance is
        # deliberately absent and asserted as a standalone workflow step.
        names = [g.name for g in verify_mod.gates()]
        self.assertEqual(
            names,
            [
                "gba-playtest-host-suite",
                "upstream-port-tests",
                "workflow-contract-tests",
                "workflow-pilot-reporter-tests",
                "workflow-pilot-baseline",
                "localization-host-suite",
                "game-localization-width-contract",
                "game-localization-catalog-check",
                "game-localization-crosswalk-check",
                "game-localization-raw-closure-check",
                "artifact-guard-tests",
                "artifact-guard",
                "codeql-alerts-test",
                "default-lane-check",
                "quickstart-legacy-check",
                "generated-data-test",
                "generated-data-check",
                "modern-linker-check-debug",
                "modern-linker-check-release",
                "modern-itemexpansion-check-debug",
                "modern-itemexpansion-check-release",
                "modern-all-locales-all-features-profile",
                "cjk-font-gates",
                "multilang-codec-gates",
                "expansion-config-gates",
                "linker-budget-gates",
                "legacy-build",
                "legacy-payload-identity",
            ],
        )
        # The merged CI runs the fast `host-tests` lane textually before the
        # ROM `build` job, so the host-only gates are first. The first six
        # remain pure Python/native checks; the seventh runs the full-game
        # localization Make target but still never builds a ROM.
        # stay host-only -- never a ROM/linker `make` build (that belongs
        # solely to the modern-linker gates) -- so the fast host job and the
        # ROM build job never duplicate work.
        for g in verify_mod.gates()[:6]:
            self.assertNotIn("make", g.command)
            self.assertNotIn("expansion-modern-linker-check", g.command)

    def test_upstream_porting_documents_parallel_ci_and_local_legacy_prerequisite(self):
        with open(UPSTREAM_PORTING_PATH, "r", encoding="utf-8") as f:
            text = " ".join(f.read().split())

        for clause in (
            "four combined workers run in parallel",
            "only serial, fail-closed join",
            "install both the supported modern toolchain",
            "explicit archival `make legacy` prerequisites",
            "`verify` has no safe subset switch",
        ):
            with self.subTest(clause=clause):
                self.assertIn(clause, text)

    def test_artifact_guard_command(self):
        # Full-game closure and artifact-guard unit checks precede the
        # immutable-tree check in the mirrored Build gate order.
        g = verify_mod.gates()[11]
        self.assertEqual(g.name, "artifact-guard")
        self.assertEqual(g.command, ["python3", "scripts/artifact_guard.py", "--revision", "HEAD"])

    def test_debug_and_release_configs_differ(self):
        by_name = {g.name: g for g in verify_mod.gates()}
        debug_gate = by_name["modern-linker-check-debug"]
        release_gate = by_name["modern-linker-check-release"]
        self.assertIn("MODERN_CONFIG=debug", debug_gate.command)
        self.assertIn("MODERN_CONFIG=release", release_gate.command)

    def test_dry_run_never_executes_subprocess(self):
        results = verify_mod.run_gates(REPO_ROOT, dry_run=True)
        self.assertEqual(len(results), 28)
        self.assertTrue(all(r.ran is False for r in results))
        self.assertTrue(all(r.passed is False for r in results))  # not-ran != passed

    def test_dry_run_lists_full_ordered_gate_set_never_a_subset(self):
        """`--dry-run` (verify_mod.run_gates(dry_run=True)) must always list
        every gate the (non-dry-run) real run would perform, in the exact
        same order -- never a partial/filtered preview."""
        dry = [r.gate.name for r in verify_mod.run_gates(REPO_ROOT, dry_run=True)]
        real_names = [g.name for g in verify_mod.gates()]
        self.assertEqual(dry, real_names)
        self.assertEqual(len(dry), 28)


class VerifyGateSelectionRemovedTests(unittest.TestCase):
    """Adversarial coverage for the closure-integrity fix: `verify` (both the
    internal `run_gates` API and the public CLI) must have NO gate
    subset/selection capability at all -- an unknown gate name, a real gate
    name used to select a subset, an empty selection, or a duplicated one
    must all be impossible to express, not merely rejected at runtime. A
    partial/unknown/zero-gate "success" must never be produced."""

    def test_run_gates_has_no_selected_or_gates_parameter(self):
        sig = inspect.signature(verify_mod.run_gates)
        self.assertNotIn("selected", sig.parameters)
        self.assertNotIn("gates", sig.parameters)
        self.assertEqual(
            set(sig.parameters),
            {"repository_root", "jobs", "dry_run"},
        )

    def test_run_gates_rejects_unexpected_selection_kwarg(self):
        with self.assertRaises(TypeError):
            verify_mod.run_gates(  # type: ignore[call-arg]
                REPO_ROOT,
                dry_run=True,
                selected=["artifact-guard"],
            )

    def test_cli_verify_has_no_gate_flag_at_all(self):
        parser = cli.build_parser()
        # argparse doesn't expose a clean "does this option exist" query,
        # so introspect the verify subparser's registered actions directly.
        verify_subparser = parser._subparsers._group_actions[0].choices["verify"]
        option_strings = set()
        for action in verify_subparser._actions:
            option_strings.update(action.option_strings)
        self.assertNotIn("--gate", option_strings)
        self.assertNotIn("--gates", option_strings)

    def test_cli_verify_gate_flag_is_a_parser_error_not_silently_ignored(self):
        for bad_argv in (
            ["verify", "--gate", "artifact-guard"],
            ["verify", "--gate", "unknown-gate-name"],
            ["verify", "--gate", "artifact-guard", "--gate", "artifact-guard"],
            ["verify", "--gate", ""],
        ):
            with self.subTest(argv=bad_argv):
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as ctx:
                        cli.main(bad_argv)
                # argparse convention: exit code 2 for a CLI usage error.
                self.assertEqual(ctx.exception.code, 2)
                self.assertIn("unrecognized arguments", err.getvalue())

    def test_cli_verify_dry_run_lists_full_ordered_gate_set(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(["verify", "--dry-run"])
        self.assertEqual(code, 0)
        printed = out.getvalue()
        for name in [g.name for g in verify_mod.gates()]:
            self.assertIn(name, printed)
        # Every line for a dry-run gate is explicitly marked SKIPPED(dry-run)
        # -- never silently omitted, never marked PASS/FAIL without running.
        self.assertEqual(printed.count("[SKIPPED(dry-run)]"), 28)


class HostOnlyEnvGateMirrorTests(unittest.TestCase):
    """Issue #10/#13 harness fix: the host lane runs the tools/gba-playtest
    suite in explicit host-only mode (GBA_PLAYTEST_HOST_ONLY=1), so its
    result is decided by mode, never by whether a git-ignored ROM happens to
    exist in the worktree while the later ROM gates rebuild it. That env
    assignment must be a literal part of the mirrored gate argv, must be
    applied to that one child process only, and must never reach the ROM
    build gates (which own the live/runtime coverage)."""

    HOST_ONLY_ENV = "GBA_PLAYTEST_HOST_ONLY"

    def test_host_suite_gate_carries_the_literal_env_assignment(self):
        gate = verify_mod.gates()[0]
        self.assertEqual(gate.name, "gba-playtest-host-suite")
        self.assertEqual(
            gate.command,
            [
                "GBA_PLAYTEST_HOST_ONLY=1",
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tools/gba-playtest/tests",
                "-v",
            ],
        )

    def test_env_prefix_splits_into_child_env_and_unchanged_argv(self):
        env_overrides, argv = verify_mod._split_env_prefix(verify_mod.gates()[0].command)
        self.assertEqual(env_overrides, {self.HOST_ONLY_ENV: "1"})
        self.assertEqual(argv[0], "python3")
        self.assertNotIn(self.HOST_ONLY_ENV, " ".join(argv))

    def test_no_other_gate_requests_host_only_mode(self):
        for gate in verify_mod.gates()[1:]:
            with self.subTest(gate=gate.name):
                self.assertNotIn(
                    self.HOST_ONLY_ENV,
                    " ".join(gate.command),
                    f"{gate.name} must not inherit or repeat the host-only "
                    f"switch: the ROM/runtime gates own live coverage",
                )

    def test_workflow_sets_host_only_in_the_host_job_only(self):
        with open(BUILD_WORKFLOW_PATH, "r", encoding="utf-8") as handle:
            workflow = handle.read()
        host_job_start = workflow.index("\n  host-tests:\n")
        build_job_start = workflow.index("\n  build:\n")
        self.assertLess(host_job_start, build_job_start)
        host_job = workflow[host_job_start:build_job_start]
        build_job = workflow[build_job_start:]
        self.assertIn(
            "run: GBA_PLAYTEST_HOST_ONLY=1 python3 -m unittest discover "
            "-s tools/gba-playtest/tests -v",
            host_job,
        )
        self.assertNotIn(
            self.HOST_ONLY_ENV,
            build_job,
            "the ROM build job must never inherit host-only mode, or its "
            "runtime scenarios would skip",
        )

    def test_run_gates_injects_host_only_into_that_child_only(self):
        """Concurrency/isolation: with --jobs 2 the host gate and the later
        `make -j2` ROM gates run against the same worktree, so the host-only
        switch must live in exactly one child environment and must not leak
        into any later gate or into this process."""
        seen = []

        def fake_run(argv, cwd=None, env=None, **kwargs):
            seen.append((tuple(argv), None if env is None else dict(env)))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        parent_env = {key: value for key, value in os.environ.items()}
        parent_env.pop(self.HOST_ONLY_ENV, None)
        with mock.patch.dict(os.environ, parent_env, clear=True):
            with (
                mock.patch.object(
                    verify_mod.subprocess,
                    "run",
                    side_effect=fake_run,
                ),
                mock.patch.object(
                    verify_mod,
                    "_resolve_repository_root",
                    return_value=REPO_ROOT,
                ),
            ):
                results = verify_mod.run_gates(REPO_ROOT, jobs=2)
            self.assertNotIn(
                self.HOST_ONLY_ENV,
                os.environ,
                "run_gates must not mutate the parent environment",
            )

        self.assertEqual(len(results), 28)
        self.assertTrue(all(result.passed for result in results))
        self.assertEqual(len(seen), 28)

        host_argv, host_env = seen[0]
        self.assertEqual(host_argv[0], "python3")
        self.assertIsNotNone(host_env)
        self.assertEqual(host_env[self.HOST_ONLY_ENV], "1")

        for gate, (argv, env) in zip(verify_mod.gates(jobs=2)[1:], seen[1:]):
            with self.subTest(gate=gate.name):
                self.assertNotIn(
                    self.HOST_ONLY_ENV,
                    {} if env is None else env,
                    f"host-only mode leaked into {gate.name}",
                )

    def test_run_gates_preserves_argv_order_at_target_repository_root(self):
        seen = []

        def fake_run(argv, **kwargs):
            seen.append((list(argv), kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(verify_mod.subprocess, "run", side_effect=fake_run),
            mock.patch.object(
                verify_mod,
                "_resolve_repository_root",
                return_value=REPO_ROOT,
            ),
        ):
            results = verify_mod.run_gates(REPO_ROOT, jobs=2)

        expected_argv = []
        expected_stdout = []
        for gate in verify_mod.gates(jobs=2):
            _, argv = verify_mod._split_env_prefix(gate.command)
            argv, stdout = verify_mod._split_stdout_redirect(argv)
            expected_argv.append(verify_mod._expand_workspace(argv, REPO_ROOT))
            expected_stdout.append(stdout)

        self.assertEqual([argv for argv, _ in seen], expected_argv)
        self.assertEqual(
            [kwargs["cwd"] for _, kwargs in seen],
            [REPO_ROOT] * 28,
        )
        baseline_argv = seen[
            [gate.name for gate in verify_mod.gates()].index(
                "workflow-pilot-baseline"
            )
        ][0]
        self.assertEqual(
            baseline_argv[baseline_argv.index("--repository-root") + 1],
            REPO_ROOT,
        )
        self.assertEqual([kwargs["stdout"] for _, kwargs in seen], expected_stdout)
        self.assertEqual(
            [result.gate.name for result in results],
            [gate.name for gate in verify_mod.gates(jobs=2)],
        )

    def test_target_repository_root_is_both_authority_and_execution_root(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                verify_mod._resolve_repository_root(REPO_ROOT),
                REPO_ROOT,
            )
        with mock.patch.dict(
            os.environ,
            {"GITHUB_WORKSPACE": REPO_ROOT},
            clear=True,
        ):
            self.assertEqual(
                verify_mod._resolve_repository_root(REPO_ROOT),
                REPO_ROOT,
            )
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "exact Git top level"):
                verify_mod._resolve_repository_root(
                    os.path.join(REPO_ROOT, "tests")
                )

    def test_workspace_and_target_must_identify_the_same_checkout(self):
        with (
            mock.patch.dict(
                os.environ,
                {"GITHUB_WORKSPACE": "/different/repository"},
                clear=True,
            ),
            mock.patch.object(
                verify_mod,
                "_git_top_level",
                side_effect=[REPO_ROOT, "/different/repository"],
            ),
            self.assertRaisesRegex(ValueError, "different Git repositories"),
        ):
            verify_mod._resolve_repository_root(REPO_ROOT)

    def test_baseline_gate_expands_workspace_and_redirects_without_a_shell(self):
        seen = []

        def fake_run(argv, **kwargs):
            seen.append((tuple(argv), kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout=None, stderr="")

        with mock.patch.dict(os.environ, {}, clear=True):
            with (
                mock.patch.object(
                    verify_mod.subprocess,
                    "run",
                    side_effect=fake_run,
                ),
                mock.patch.object(
                    verify_mod,
                    "_resolve_repository_root",
                    return_value=REPO_ROOT,
                ),
            ):
                results = verify_mod.run_gates(REPO_ROOT, jobs=2)

        index = [gate.name for gate in verify_mod.gates()].index(
            "workflow-pilot-baseline"
        )
        argv, kwargs = seen[index]
        self.assertNotIn(">", argv)
        self.assertNotIn("/dev/null", argv)
        self.assertNotIn("$GITHUB_WORKSPACE", argv)
        self.assertEqual(argv[argv.index("--repository-root") + 1], REPO_ROOT)
        self.assertEqual(kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(results[index].stdout, "")


class VerifyCliCwdTests(unittest.TestCase):
    def test_normal_cli_executes_all_gates_at_selected_target_root(self):
        artifact_root = os.path.join(REPO_ROOT, "build", "test-artifacts")
        os.makedirs(artifact_root, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="verify-target-checkout-",
            dir=artifact_root,
        ) as temporary:
            target_root = os.path.join(temporary, "target")
            subprocess.run(
                ["git", "clone", "-q", "--shared", REPO_ROOT, target_root],
                check=True,
                capture_output=True,
            )
            for arguments, expected_root in (
                (["verify"], REPO_ROOT),
                (["--repo", target_root, "verify"], target_root),
            ):
                with self.subTest(arguments=arguments):
                    seen = []
                    real_run = subprocess.run

                    def fake_run(argv, **kwargs):
                        if argv[0] == "git":
                            return real_run(argv, **kwargs)
                        seen.append((list(argv), kwargs))
                        return subprocess.CompletedProcess(
                            argv,
                            0,
                            stdout="",
                            stderr="",
                        )

                    with (
                        contextlib.chdir(REPO_ROOT),
                        mock.patch.dict(os.environ, {}, clear=True),
                        mock.patch.object(
                            verify_mod.subprocess,
                            "run",
                            side_effect=fake_run,
                        ),
                        contextlib.redirect_stdout(io.StringIO()),
                    ):
                        self.assertEqual(cli.main(arguments), 0)

                    self.assertEqual(len(seen), 28)
                    self.assertEqual(
                        [kwargs["cwd"] for _, kwargs in seen],
                        [expected_root] * 28,
                    )
                    baseline = seen[
                        [gate.name for gate in verify_mod.gates()].index(
                            "workflow-pilot-baseline"
                        )
                    ][0]
                    self.assertEqual(
                        baseline[baseline.index("--repository-root") + 1],
                        expected_root,
                    )

    def test_dry_run_and_normal_cli_select_the_same_target_root(self):
        for common, expected_root in (
            ([], REPO_ROOT),
            (["--repo", REPO_ROOT], REPO_ROOT),
        ):
            with self.subTest(arguments=common):
                with (
                    contextlib.chdir(REPO_ROOT),
                    mock.patch.object(
                        verify_mod,
                        "run_gates",
                        return_value=[],
                    ) as run_gates,
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    self.assertEqual(cli.main([*common, "verify"]), 0)
                    self.assertEqual(
                        cli.main([*common, "verify", "--dry-run"]),
                        0,
                    )
                self.assertEqual(
                    run_gates.call_args_list,
                    [
                        mock.call(expected_root, jobs=2, dry_run=False),
                        mock.call(expected_root, jobs=2, dry_run=True),
                    ],
                )

    def test_documented_source_root_module_dry_run_is_real(self):
        completed = subprocess.run(
            [
                "python3",
                "-m",
                "scripts.upstream_port",
                "verify",
                "--dry-run",
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.count("[SKIPPED(dry-run)]"), 28)

    def test_invalid_explicit_repo_is_a_normal_cli_error(self):
        cases = (
            os.path.join(REPO_ROOT, "build", "does-not-exist"),
            os.path.join(REPO_ROOT, "tests"),
            os.path.join(REPO_ROOT, "scripts", "upstream_port", "cli.py"),
        )
        for target in cases:
            with self.subTest(target=target):
                stderr = io.StringIO()
                with (
                    contextlib.chdir(REPO_ROOT),
                    mock.patch.dict(os.environ, {}, clear=True),
                    contextlib.redirect_stderr(stderr),
                ):
                    self.assertEqual(
                        cli.main(["--repo", target, "verify", "--dry-run"]),
                        1,
                    )
                self.assertTrue(stderr.getvalue().startswith("error: "))
                self.assertNotIn("Traceback", stderr.getvalue())

    def test_implicit_repo_is_the_exact_source_root(self):
        self.assertEqual(cli._repo_root(None), REPO_ROOT)
        stderr = io.StringIO()
        with (
            mock.patch.object(cli, "SOURCE_ROOT", os.path.join(REPO_ROOT, "tests")),
            mock.patch.dict(os.environ, {}, clear=True),
            contextlib.redirect_stderr(stderr),
        ):
            self.assertEqual(cli.main(["verify", "--dry-run"]), 1)
        self.assertIn("exact Git top level", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
