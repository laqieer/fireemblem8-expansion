import ast
import contextlib
import inspect
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from scripts.upstream_port import cli, verify as verify_mod
from scripts.workflow_pilot import publisher_command_signatures
from tests.workflows import test_build_ci_topology as topology_tests
from tests.workflows import test_patch_release_workflow as patch_workflow_tests

publisher_shell_contract = publisher_command_signatures.publisher_shell_contract

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BUILD_WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "build.yml")
UPSTREAM_PORTING_PATH = os.path.join(REPO_ROOT, "docs", "upstream-porting.md")
BROKEN_PUBLISHER_INDENTATION_SHA = "77aae9df769aa87f2fd9bed13b767ff8e7dd171a"

# Issues #7/#17 remediation: the documentation step is a genuine required
# workflow gate, but it is the sole correctness step deliberately excluded
# from verify.gates(). Its exact commands and position are asserted separately
# below; localization remains part of the current 28-gate candidate mirror.
_DOCS_GOVERNANCE_STEP_NAME = "Check documentation (issues #7/#17)"
_CODEQL_ALERTS_STEP_NAME = "Run CodeQL alert regression suite (issue #84)"
_LOCALIZATION_HOST_STEP_NAME = "Run localization host test suite (issue #18)"
_GAME_LOCALIZATION_WIDTH_STEP_NAME = "Run full-game localization width contract (issue #18)"
_WORKFLOW_CONTRACT_STEP_NAME = "Run workflow contract test suite"
_WORKFLOW_PILOT_TEST_STEP_NAME = verify_mod._WORKFLOW_PILOT_TEST_STEP_NAME
_WORKFLOW_PILOT_BASELINE_STEP_NAME = (
    verify_mod._WORKFLOW_PILOT_BASELINE_STEP_NAME
)


def _parse_workflow_gate_commands(path=BUILD_WORKFLOW_PATH):
    if path == BUILD_WORKFLOW_PATH:
        text = _build_workflow_text()
    else:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    return _parse_workflow_gate_commands_text(text)


def _build_workflow_text():
    return publisher_command_signatures.authority_file_bytes(
        ".github/workflows/build.yml"
    ).decode("utf-8")


def _parse_workflow_gate_commands_text(text):
    return [
        (step_name, list(argv))
        for _, step_name, argv in verify_mod._parse_workflow_gate_contract_text(
            text
        )
    ]


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

    def test_safe_gate_command_construction_does_not_import_publisher_authority(self):
            tree = ast.parse(inspect.getsource(verify_mod.publisher_authority_command))
            names = {
                node.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Name)
            }
            self.assertNotIn("publisher_command_signatures", names)
            command = verify_mod.publisher_authority_command(
                ".",
                "HEAD",
                "upstream-port",
            )
            self.assertEqual(command[:4], ["/usr/bin/python3", "-I", "-S", "-c"])

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

    def test_patch_release_workflow_imports_without_yaml_dependency(self):
        self.assertNotIn("yaml", sys.modules)
        snapshot = publisher_command_signatures._authority_snapshot()
        for module in (sys.modules[__name__], patch_workflow_tests):
            with self.subTest(module=module.__name__):
                object_id = getattr(module, "__authority_object_id__", "")
                if object_id:
                    self.assertEqual(
                        getattr(module, "__authority_tree_id__", ""),
                        snapshot.tree_id,
                    )
                    continue
                relative = os.path.relpath(module.__file__, REPO_ROOT)
                with open(module.__file__, "rb") as source:
                    self.assertEqual(
                        source.read(),
                        snapshot.files[relative].data,
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
            [
                verify_mod.publisher_authority_command(
                    ".",
                    "HEAD",
                    "workflows",
                )
            ],
        )
        gate = {g.name: g for g in verify_mod.gates()}["workflow-contract-tests"]
        self.assertEqual(gate.command, commands[0])
        self.assertNotIn("make", gate.command)
        for gate_name in ("upstream-port-tests", "workflow-contract-tests"):
            authority_gate = next(
                gate
                for gate in verify_mod.gates()
                if gate.name == gate_name
            )
            self.assertEqual(
                authority_gate.command[:4],
                ["/usr/bin/python3", "-I", "-S", "-c"],
            )
            self.assertNotIn("-m", authority_gate.command[:5])

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
                        "/usr/bin/python3", "-I",
                        "scripts/workflow_pilot/isolated_launcher.py",
                        "reporter-tests",
                    ],
                ),
                (
                    _WORKFLOW_PILOT_BASELINE_STEP_NAME,
                    [
                        "/usr/bin/python3", "-I",
                        "scripts/workflow_pilot/isolated_launcher.py",
                        "baseline",
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

    def test_workflow_parser_is_linear_on_long_environment_adversary(self):
        workflow = _build_workflow_text()
        self.assertTrue(_parse_workflow_gate_commands_text(workflow))
        adversarial = workflow.replace(
            "    - name: Run workflow-pilot reporter regression suite (issue #176)\n"
            "      if: ${{ needs.event-classifier.result == 'failure' || "
            "needs.event-classifier.outputs.classification == 'full' }}\n"
            "      env:\n"
            "        BASH_ENV: ''\n",
            "    - name: Run workflow-pilot reporter regression suite (issue #176)\n"
            "      if: ${{ needs.event-classifier.result == 'failure' || "
            "needs.event-classifier.outputs.classification == 'full' }}\n"
            "      env:\n"
            + ("        a\n" * 50000)
            + "        BASH_ENV: ''\n",
            1,
        )
        with self.assertRaisesRegex(
            ValueError,
            "unsupported mapping-key syntax|reviewed scrubbed environment",
        ):
            _parse_workflow_gate_commands_text(adversarial)

    def test_every_mirrored_gate_rejects_unmodeled_execution_fields(self):
        workflow = _build_workflow_text()
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
                        ValueError,
                        "unsupported direct mapping|unsupported direct fields|"
                        "must contain exactly|duplicate direct fields",
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
                "upstream-port-tests",
                "workflow-contract-tests",
                "gba-playtest-host-suite",
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
            "event identity validator, event router, and mode-specific "
            "classifier check precede the four",
            "`summary` is their fail-closed join",
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
    def test_repository_discovery_ignores_ambient_git_redirection(self):
        hostile = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "alias.rev-parse",
            "GIT_CONFIG_VALUE_0": "!printf redirected",
            "GIT_DIR": os.path.join(REPO_ROOT, "build", "redirected.git"),
            "GIT_OBJECT_DIRECTORY": os.path.join(
                REPO_ROOT,
                "build",
                "redirected-objects",
            ),
            "GIT_WORK_TREE": os.path.join(
                REPO_ROOT,
                "build",
                "redirected-tree",
            ),
        }
        with mock.patch.dict(os.environ, hostile, clear=False):
            self.assertEqual(
                verify_mod._git_top_level(REPO_ROOT),
                REPO_ROOT,
            )
    """Issue #10/#13 harness fix: the host lane runs the tools/gba-playtest
    suite in explicit host-only mode (GBA_PLAYTEST_HOST_ONLY=1), so its
    result is decided by mode, never by whether a git-ignored ROM happens to
    exist in the worktree while the later ROM gates rebuild it. That env
    assignment must be a literal part of the mirrored gate argv, must be
    applied to that one child process only, and must never reach the ROM
    build gates (which own the live/runtime coverage)."""

    HOST_ONLY_ENV = "GBA_PLAYTEST_HOST_ONLY"

    def test_host_suite_gate_carries_the_literal_env_assignment(self):
        gate = next(
            gate
            for gate in verify_mod.gates()
            if gate.name == "gba-playtest-host-suite"
        )
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
        gate = next(
            gate
            for gate in verify_mod.gates()
            if gate.name == "gba-playtest-host-suite"
        )
        env_overrides, argv = verify_mod._split_env_prefix(gate.command)
        self.assertEqual(env_overrides, {self.HOST_ONLY_ENV: "1"})
        self.assertEqual(argv[0], "python3")
        self.assertNotIn(self.HOST_ONLY_ENV, " ".join(argv))

    def test_no_other_gate_requests_host_only_mode(self):
        for gate in verify_mod.gates():
            if gate.name == "gba-playtest-host-suite":
                continue
            with self.subTest(gate=gate.name):
                self.assertNotIn(
                    self.HOST_ONLY_ENV,
                    " ".join(gate.command),
                    f"{gate.name} must not inherit or repeat the host-only "
                    f"switch: the ROM/runtime gates own live coverage",
                )

    def test_workflow_sets_host_only_in_the_host_job_only(self):
        workflow = _build_workflow_text()
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

        host_index = next(
            index
            for index, gate in enumerate(verify_mod.gates(jobs=2))
            if gate.name == "gba-playtest-host-suite"
        )
        host_argv, host_env = seen[host_index]
        self.assertEqual(host_argv[0], "python3")
        self.assertIsNotNone(host_env)
        self.assertEqual(host_env[self.HOST_ONLY_ENV], "1")

        for index, (gate, (argv, env)) in enumerate(
            zip(verify_mod.gates(jobs=2), seen)
        ):
            if index == host_index:
                continue
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

    def test_ambient_workspace_does_not_override_explicit_target(self):
        with (
            mock.patch.dict(
                os.environ,
                {"GITHUB_WORKSPACE": "/different/repository"},
                clear=True,
            ),
            mock.patch.object(
                verify_mod,
                "_git_top_level",
                return_value=REPO_ROOT,
            ),
        ):
            self.assertEqual(
                verify_mod._resolve_repository_root(REPO_ROOT),
                REPO_ROOT,
            )

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
    def clone_target(self, parent, name="target"):
        target = os.path.join(parent, name)
        subprocess.run(
            [
                verify_mod._trusted_git_executable(),
                "--no-replace-objects",
                "-C",
                parent,
                "clone",
                "-q",
                "--no-hardlinks",
                REPO_ROOT,
                target,
            ],
            env=verify_mod._git_environment(),
            check=True,
            capture_output=True,
        )
        target_workflow = os.path.join(
            target,
            ".github",
            "workflows",
            "build.yml",
        )
        workflow_bytes = publisher_command_signatures.authority_file_bytes(
            ".github/workflows/build.yml"
        )
        with open(target_workflow, "wb") as destination:
            destination.write(workflow_bytes)
        return target

    def job_bounds(self, workflow, job_name):
        start = workflow.index(f"\n  {job_name}:\n") + 1
        body_start = workflow.index("\n", start) + 1
        next_job = re.search(
            r"^  [A-Za-z_][A-Za-z0-9_-]*:",
            workflow[body_start:],
            re.M,
        )
        end = (
            len(workflow)
            if next_job is None
            else body_start + next_job.start()
        )
        return start, end

    def job_body(self, workflow, job_name):
        start, end = self.job_bounds(workflow, job_name)
        return workflow[start:end]

    def append_job_steps(self, workflow, job_name, steps):
        _, end = self.job_bounds(workflow, job_name)
        return workflow[:end] + steps + "\n" + workflow[end:]

    def replace_in_job(self, workflow, job_name, old, new):
        start, end = self.job_bounds(workflow, job_name)
        body = workflow[start:end]
        self.assertIn(old, body)
        return workflow[:start] + body.replace(old, new, 1) + workflow[end:]

    def assert_only_job_changed(self, original, changed, job_name):
        self.assertNotEqual(
            self.job_body(original, job_name),
            self.job_body(changed, job_name),
        )
        for other in verify_mod._EXPECTED_JOBS:
            if other != job_name:
                self.assertEqual(
                    self.job_body(original, other),
                    self.job_body(changed, other),
                )

    def test_normal_cli_executes_all_gates_at_selected_target_root(self):
        artifact_root = os.path.join(REPO_ROOT, "build", "test-artifacts")
        os.makedirs(artifact_root, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="verify-target-checkout-",
            dir=artifact_root,
        ) as temporary:
            target_root = self.clone_target(temporary)
            for arguments, expected_root in (
                (["verify"], REPO_ROOT),
                (["--repo", target_root, "verify"], target_root),
            ):
                with self.subTest(arguments=arguments):
                    seen = []
                    real_run = subprocess.run

                    def fake_run(argv, **kwargs):
                        if argv[0] == verify_mod._trusted_git_executable():
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

    def test_cross_checkout_requires_exact_target_gate_equivalence(self):
        artifact_root = os.path.join(REPO_ROOT, "build", "test-artifacts")
        os.makedirs(artifact_root, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="verify-target-equivalence-",
            dir=artifact_root,
        ) as temporary:
            target_root = self.clone_target(temporary)
            workflow_path = os.path.join(
                target_root,
                ".github",
                "workflows",
                "build.yml",
            )
            with open(workflow_path, "r", encoding="utf-8") as handle:
                original = handle.read()
            self.assertEqual(
                len(verify_mod.run_gates(target_root, dry_run=True)),
                28,
            )

            mutations = {
                "newer-added": original.replace(
                    "    - name: Run upstream-port tooling test suite\n",
                    "    - name: Run target-only newer gate\n"
                    "      run: python3 -c pass\n\n"
                    "    - name: Run upstream-port tooling test suite\n",
                    1,
                ),
                "older-removed": original.replace(
                    "    - name: Run upstream-port tooling test suite\n",
                    "    - name: Removed upstream-port tooling test suite\n",
                    1,
                ),
                "mutated-argv": original.replace(
                    "        AUTHORITY_SUITE: upstream-port",
                    "        AUTHORITY_SUITE: check",
                    1,
                ),
                "reordered": original.replace(
                    "    - name: Run upstream-port tooling test suite\n",
                    "__UPSTREAM_STEP__",
                    1,
                ).replace(
                    "    - name: Run workflow contract test suite\n",
                    "    - name: Run upstream-port tooling test suite\n",
                    1,
                ).replace(
                    "__UPSTREAM_STEP__",
                    "    - name: Run workflow contract test suite\n",
                    1,
                ),
                "altered-action": original.replace(
                    verify_mod._CHECKOUT_USES,
                    "actions/checkout@" + "0" * 40,
                    1,
                ),
                "altered-with": original.replace(
                    "        fetch-depth: 0",
                    "        fetch-depth: 1",
                    1,
                ),
                "altered-env": original.replace(
                    "        BASH_ENV: ''",
                    "        BASH_ENV: build/mask",
                    1,
                ),
                "altered-setup": original.replace(
                    "      run: ./build_tools.sh",
                    "      run: exit 1",
                    1,
                ),
                "extra-job": original
                + "\n  target-only:\n"
                + "    runs-on: ubuntu-latest\n"
                + "    steps:\n"
                + "    - run: exit 1\n",
            }
            for name, changed in mutations.items():
                with self.subTest(name=name):
                    with open(workflow_path, "w", encoding="utf-8") as handle:
                        handle.write(changed)
                    with self.assertRaisesRegex(
                        ValueError,
                        "target Build workflow gate contract differs|"
                        "authority checkout differs|"
                        "classifier mapping differs|"
                        "unreviewed unnamed step|reviewed scrubbed environment|"
                        "workflow job order|step roles and order|"
                        "publisher authority bootstrap differs|"
                        "must contain exactly",
                    ):
                        verify_mod.run_gates(target_root, dry_run=True)

            os.unlink(workflow_path)
            with self.assertRaisesRegex(
                ValueError,
                "target Build workflow",
            ):
                verify_mod.run_gates(target_root, dry_run=True)

    def test_event_setup_router_and_mode_are_closed_but_not_local_gates(self):
        original = _build_workflow_text()
        structure = verify_mod._parse_workflow_structure_text(original)
        self.assertEqual(
            structure[1],
            (
                "event-identity",
                "event-router",
                "event-classifier",
                "host-tests",
                "build",
                "extended-host-tests",
                "legacy",
                "patch-release",
                "summary",
            ),
        )
        self.assertEqual(len(verify_mod.gates()), 28)
        gate_jobs = {
            job_name
            for job_name, _, _ in verify_mod._workflow_gate_contract(
                structure
            )
        }
        self.assertTrue(
            {"event-identity", "event-router", "event-classifier"}.isdisjoint(
                gate_jobs
            )
        )

        mutations = (
            original.replace(
                '[[ "$1" =~ ^[0-9a-f]{40}$ && "$2" = "\\"$1\\"" ]]',
                '[[ -n "$1" ]]',
                1,
            ),
            original.replace(
                '"$EVENT_REF" = "refs/pull/$PR_NUMBER/merge"',
                '-n "$EVENT_REF"',
                1,
            ),
            original.replace(
                '[[ "$1" =~ ^[1-9][0-9]*$ && "$2" = "$1" ]]',
                '[[ -n "$1" ]]',
                1,
            ),
            original.replace(
                "      expected_head: ${{ steps.classify.outputs.expected_head }}",
                "      expected_head: attacker",
                1,
            ),
            original.replace(
                "      CLASSIFIER_REF: ${{ "
                "needs.event-identity.outputs.classifier_ref }}",
                "      CLASSIFIER_REF: ${{ github.sha }}",
                1,
            ),
            original.replace(
                "      identity_valid: ${{ steps.classify.outputs.identity_valid }}",
                "      identity_valid: true",
                1,
            ),
            original.replace(
                "        fetch-depth: 1",
                "        fetch-depth: 0",
                1,
            ),
            original.replace(
                "    - name: Verify classifier authority revision",
                "    - name: Skip classifier authority verification",
                1,
            ),
            original.replace(
                "      id: classify",
                "      id: attacker",
                1,
            ),
            original.replace(
                "/usr/bin/python3 -I scripts/workflow_pilot/isolated_launcher.py "
                "classify-event",
                "python3 scripts/workflow_pilot/event_classifier.py",
                1,
            ),
            original.replace(
                '            echo "run_expensive=true"',
                '            echo "run_expensive=false"',
                1,
            ),
            original.replace(
                '/usr/bin/git check-ref-format "refs/heads/$PR_BASE_REF"',
                'test -n "$PR_BASE_REF"',
                1,
            ),
        )
        for changed in mutations:
            with self.subTest(mutation=changed[:180]):
                self.assertNotEqual(changed, original)
                try:
                    changed_structure = verify_mod._parse_workflow_structure_text(
                        changed
                    )
                except ValueError:
                    continue
                self.assertNotEqual(changed_structure, structure)

    def test_patch_release_supervisor_parent_remount_variants_reject_structure_parse(self):
        original = _build_workflow_text()
        verify_mod._parse_workflow_structure_text(original)
        for label, changed in patch_workflow_tests.generate_supervisor_parent_remount_mutations(
            original
        ):
            with self.subTest(variant=label):
                self.assertTrue(
                    patch_workflow_tests.workflow_has_supervisor_parent_readonly_remount(
                        changed
                    )
                )
                with self.assertRaises(ValueError):
                    verify_mod._parse_workflow_structure_text(changed)

    def test_patch_release_raw_identity_variants_reject_structure_parse(self):
        original = _build_workflow_text()
        verify_mod._parse_workflow_structure_text(original)
        for label, changed in patch_workflow_tests.generate_publisher_raw_identity_mutations(
            original
        ):
            with self.subTest(variant=label):
                self.assertTrue(
                    patch_workflow_tests.publisher_boundary_errors(changed)
                )
                with self.assertRaises(ValueError):
                    verify_mod._parse_workflow_structure_text(changed)

    def test_patch_release_command_inventory_decisions_are_mirrored(self):
        original = _build_workflow_text()
        verify_mod._parse_workflow_structure_text(original)
        mutations = (
            original.replace(
                publisher_shell_contract.PATCH_RELEASE_MEMBERSHIP_CHECKER_INTRODUCER,
                "/usr/bin/python3 -I -S -c "
                "'open(\"/mnt/supervisor/cgroup/\"+\"cgroup.\"+\"procs\").read()'",
                1,
            ),
            original.replace(
                "/usr/bin/mknod -m 0666 /dev/null c 1 3",
                "/usr/bin/touch /dev/null",
                1,
            ),
        )
        for changed in mutations:
            with self.subTest(mutation=changed[:180]):
                self.assertNotEqual(changed, original)
                self.assertTrue(
                    patch_workflow_tests.publisher_boundary_errors(changed)
                )
                with self.assertRaises(ValueError):
                    verify_mod._parse_workflow_structure_text(changed)

    def test_historical_patch_release_parser_indentation_is_rejected(self):
        original = _build_workflow_text()
        verify_mod._parse_workflow_structure_text(original)
        broken = subprocess.check_output(
            [
                "git",
                "--no-pager",
                "show",
                f"{BROKEN_PUBLISHER_INDENTATION_SHA}:.github/workflows/build.yml",
            ],
            cwd=REPO_ROOT,
            text=True,
        )
        with self.assertRaisesRegex(
            ValueError,
            "patch-release parser script differs|isolated candidate build differs|"
            "publisher authority bootstrap differs",
        ):
            verify_mod._parse_workflow_structure_text(broken)

    def test_every_combined_worker_requires_the_fail_closed_classifier_edge(self):
        original = _build_workflow_text()
        for job_name in verify_mod._COMBINED_JOBS:
            expected_if = (
                verify_mod._HOST_BUILD_CONDITION
                if job_name in verify_mod._METADATA_ADAPTER_JOBS
                else verify_mod._WORKER_CONDITION
            )
            for old, new in (
                (
                    "    needs: [event-identity, event-classifier]",
                    "    needs: [event-classifier]",
                ),
                (
                    f"    if: {expected_if}",
                    "    if: ${{ needs.event-classifier.outputs."
                    "run_expensive == 'true' }}",
                ),
            ):
                with self.subTest(job=job_name, field=old):
                    changed = self.replace_in_job(
                        original,
                        job_name,
                        old,
                        new,
                    )
                    self.assert_only_job_changed(
                        original,
                        changed,
                        job_name,
                    )
                    with self.assertRaises(ValueError):
                        verify_mod._parse_workflow_structure_text(changed)

    def test_unnamed_and_duplicate_setup_steps_reject_in_every_worker(self):
        artifact_root = os.path.join(REPO_ROOT, "build", "test-artifacts")
        os.makedirs(artifact_root, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="verify-invisible-step-",
            dir=artifact_root,
        ) as temporary:
            target_root = self.clone_target(temporary)
            workflow_path = os.path.join(
                target_root,
                ".github",
                "workflows",
                "build.yml",
            )
            with open(workflow_path, "r", encoding="utf-8") as handle:
                original = handle.read()
            for job_name in verify_mod._COMBINED_JOBS:
                with self.subTest(job=job_name, mutation="unnamed"):
                    changed = self.append_job_steps(
                        original,
                        job_name,
                        "    - run: exit 1\n",
                    )
                    self.assert_only_job_changed(original, changed, job_name)
                    with open(workflow_path, "w", encoding="utf-8") as handle:
                        handle.write(changed)
                    with self.assertRaisesRegex(
                        ValueError,
                        "unreviewed unnamed step",
                    ):
                        verify_mod.run_gates(target_root, dry_run=True)
                with self.subTest(job=job_name, mutation="duplicate-setup"):
                    changed = self.append_job_steps(
                        original,
                        job_name,
                        "    - name: Build tools\n"
                        "      run: exit 1\n\n"
                        "    - name: Build tools\n"
                        "      run: exit 1\n",
                    )
                    self.assert_only_job_changed(original, changed, job_name)
                    with open(workflow_path, "w", encoding="utf-8") as handle:
                        handle.write(changed)
                    with self.assertRaisesRegex(
                        ValueError,
                        "duplicate step names|must contain exactly",
                    ):
                        verify_mod.run_gates(target_root, dry_run=True)

    def test_metadata_adapter_parsed_contract_rejects_extra_shell_and_python_behavior(self):
        artifact_root = os.path.join(REPO_ROOT, "build", "test-artifacts")
        os.makedirs(artifact_root, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="verify-metadata-adapter-",
            dir=artifact_root,
        ) as temporary:
            target_root = self.clone_target(temporary)
            workflow_path = os.path.join(
                target_root,
                ".github",
                "workflows",
                "build.yml",
            )
            with open(workflow_path, "r", encoding="utf-8") as handle:
                original = handle.read()
            mutations = {
                "extra-python-command": self.replace_in_job(
                    original,
                    "host-tests",
                    '        PY\n',
                    '        PY\n        /usr/bin/python3 -c "pass"\n',
                ),
                "extra-touch-command": self.replace_in_job(
                    original,
                    "host-tests",
                    '        fi\n        /usr/bin/python3 -I - <<\'PY\'\n',
                    '        fi\n        /usr/bin/touch "$GITHUB_EVENT_PATH"\n'
                    "        /usr/bin/python3 -I - <<'PY'\n",
                ),
                "extra-python-import": self.replace_in_job(
                    original,
                    "host-tests",
                    "        import sys\n",
                    "        import sys\n        import socket\n",
                ),
                "extra-python-dead-branch": self.replace_in_job(
                    original,
                    "host-tests",
                    "        payload = load_event_payload()\n",
                    "        payload = load_event_payload()\n"
                    "        if False:\n"
                    "            json.dumps({})\n",
                ),
                "raw-trailing-space-drift": self.replace_in_job(
                    original,
                    "host-tests",
                    "        fi\n",
                    "        fi   \n",
                ),
                "raw-comment-drift": self.replace_in_job(
                    original,
                    "host-tests",
                    "        import sys\n",
                    "        import sys\n        # lexical drift\n",
                ),
                "unquoted-heredoc-introducer": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    "        /usr/bin/python3 -I - <<PY\n",
                ),
                "double-quoted-heredoc-introducer": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    '        /usr/bin/python3 -I - <<"PY"\n',
                ),
                "escaped-heredoc-introducer": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    "        /usr/bin/python3 -I - <<\\PY\n",
                ),
                "dash-heredoc-introducer": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    "        /usr/bin/python3 -I - <<-'PY'\n",
                ),
                "backslash-space": self.replace_in_job(
                    original,
                    "host-tests",
                    '        if [ "$CLASSIFIER_RESULT" != "success" ] || \\\n',
                    '        if [ "$CLASSIFIER_RESULT" != "success" ] || \\ \n',
                ),
                "backslash-tab": self.replace_in_job(
                    original,
                    "host-tests",
                    '           [ "$FALLBACK_IDENTITY_RESULT" != "success" ] || \\\n',
                    '           [ "$FALLBACK_IDENTITY_RESULT" != "success" ] || \\\t\n',
                ),
                "backslash-trailing-spaces": self.replace_in_job(
                    original,
                    "host-tests",
                    '           [ "$GITHUB_EVENT_NAME" != "pull_request" ] || \\\n',
                    '           [ "$GITHUB_EVENT_NAME" != "pull_request" ] || \\  \n',
                ),
                "uniform-python-heredoc-indent": self.replace_in_job(
                    original,
                    "host-tests",
                    topology_tests._step_blocks(
                        topology_tests._job_blocks(original)["host-tests"]
                    )[0],
                    topology_tests._indent_metadata_adapter_heredoc_in_step(
                        topology_tests._step_blocks(
                            topology_tests._job_blocks(original)["host-tests"]
                        )[0]
                    ),
                ),
                "nbsp": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    "        /usr/bin/python3 -I - <<'PY'\u00a0\n",
                ),
                "em-space": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    "        /usr/bin/python3 -I - <<'PY'\u2003\n",
                ),
                "en-space": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    "        /usr/bin/python3 -I - <<'PY'\u2002\n",
                ),
                "thin-space": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    "        /usr/bin/python3 -I - <<'PY'\u2009\n",
                ),
                "ideographic-space": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    "        /usr/bin/python3 -I - <<'PY'\u3000\n",
                ),
                "zero-width-space": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    "        /usr/bin/python3 -I - <<'PY'\u200b\n",
                ),
                "bom": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    "\ufeff        /usr/bin/python3 -I - <<'PY'\n",
                ),
                "line-separator": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    "        /usr/bin/python3 -I - <<'PY'\u2028\n",
                ),
                "paragraph-separator": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    "        /usr/bin/python3 -I - <<'PY'\u2029\n",
                ),
                "carriage-return": self.replace_in_job(
                    original,
                    "host-tests",
                    "        /usr/bin/python3 -I - <<'PY'\n",
                    "        /usr/bin/python3 -I - <<'PY'\r\n",
                ),
                "ascii-tab": self.replace_in_job(
                    original,
                    "host-tests",
                    "        import sys\n",
                    "\t        import sys\n",
                ),
                "ascii-escape": self.replace_in_job(
                    original,
                    "host-tests",
                    "        import sys\n",
                    "        import sys\x1b\n",
                ),
                "ascii-nul": self.replace_in_job(
                    original,
                    "host-tests",
                    "        import sys\n",
                    "        import sys\x00\n",
                ),
            }
            for name, changed in mutations.items():
                with self.subTest(mutation=name):
                    self.assert_only_job_changed(original, changed, "host-tests")
                    with open(workflow_path, "w", encoding="utf-8") as handle:
                        handle.write(changed)
                    with self.assertRaisesRegex(
                        ValueError,
                        "metadata adapter script differs|workflow has content after the jobs mapping",
                    ):
                        verify_mod.run_gates(target_root, dry_run=True)

    def test_target_execution_context_is_closed_before_dry_run(self):
        artifact_root = os.path.join(REPO_ROOT, "build", "test-artifacts")
        os.makedirs(artifact_root, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="verify-execution-context-",
            dir=artifact_root,
        ) as temporary:
            target_root = self.clone_target(temporary)
            workflow_path = os.path.join(
                target_root,
                ".github",
                "workflows",
                "build.yml",
            )
            with open(workflow_path, "r", encoding="utf-8") as handle:
                original = handle.read()
            self.assertEqual(
                len(verify_mod.run_gates(target_root, dry_run=True)),
                28,
            )

            for job_name in verify_mod._COMBINED_JOBS:
                timeout = "90" if job_name == "build" else "60"
                job_mutations = {
                    "self-hosted": self.replace_in_job(
                        original,
                        job_name,
                        "    runs-on: ubuntu-latest",
                        "    runs-on: self-hosted",
                    ),
                    "container": self.replace_in_job(
                        original,
                        job_name,
                        f"    timeout-minutes: {timeout}",
                        f"    timeout-minutes: {timeout}\n"
                        "    container: ubuntu:latest",
                    ),
                    "timeout": self.replace_in_job(
                        original,
                        job_name,
                        f"    timeout-minutes: {timeout}",
                        "    timeout-minutes: 1",
                    ),
                    "job-env": self.replace_in_job(
                        original,
                        job_name,
                        "    env:\n",
                        "    env:\n      BASH_ENV: build/mask\n",
                    ),
                    "defaults-shell": self.replace_in_job(
                        original,
                        job_name,
                        "    steps:\n",
                        "    defaults:\n"
                        "      run:\n"
                        "        shell: bash\n"
                        "    steps:\n",
                    ),
                    "services": self.replace_in_job(
                        original,
                        job_name,
                        "    steps:\n",
                        "    services:\n"
                        "      db:\n"
                        "        image: postgres\n"
                        "    steps:\n",
                    ),
                    "strategy": self.replace_in_job(
                        original,
                        job_name,
                        "    steps:\n",
                        "    strategy: {matrix: {runner: [self-hosted]}}\n"
                        "    steps:\n",
                    ),
                    "complex-key": self.replace_in_job(
                        original,
                        job_name,
                        "    runs-on: ubuntu-latest",
                        '    "runs-on": ubuntu-latest',
                    ),
                    "duplicate-key": self.replace_in_job(
                        original,
                        job_name,
                        f"    timeout-minutes: {timeout}",
                        f"    timeout-minutes: {timeout}\n"
                        f"    timeout-minutes: {timeout}",
                    ),
                    "reordered-keys": self.replace_in_job(
                        self.replace_in_job(
                            original,
                            job_name,
                            "    runs-on: ubuntu-latest",
                            "    __RUNS_ON__",
                        ),
                        job_name,
                        f"    timeout-minutes: {timeout}",
                        "    runs-on: ubuntu-latest",
                    ).replace(
                        "    __RUNS_ON__",
                        f"    timeout-minutes: {timeout}",
                        1,
                    ),
                }
                for mutation, changed in job_mutations.items():
                    with self.subTest(job=job_name, mutation=mutation):
                        with open(
                            workflow_path,
                            "w",
                            encoding="utf-8",
                        ) as handle:
                            handle.write(changed)
                        with self.assertRaises(ValueError):
                            verify_mod.run_gates(target_root, dry_run=True)

    def test_publisher_and_summary_contexts_are_closed_before_dry_run(self):
        artifact_root = os.path.join(REPO_ROOT, "build", "test-artifacts")
        os.makedirs(artifact_root, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="verify-terminal-jobs-",
            dir=artifact_root,
        ) as temporary:
            target_root = self.clone_target(temporary)
            workflow_path = os.path.join(
                target_root,
                ".github",
                "workflows",
                "build.yml",
            )
            with open(workflow_path, "r", encoding="utf-8") as handle:
                original = handle.read()
            mutations = []
            for job_name in ("patch-release", "summary"):
                for label, old, new in (
                        (
                            "runner",
                            "    runs-on: ubuntu-latest",
                            "    runs-on: self-hosted",
                        ),
                        (
                            "permissions",
                            "    runs-on: ubuntu-latest",
                            "    permissions: write-all\n"
                            "    runs-on: ubuntu-latest",
                        ),
                        (
                            "container",
                            "    runs-on: ubuntu-latest",
                            "    container: ubuntu:latest\n"
                            "    runs-on: ubuntu-latest",
                        ),
                        (
                            "defaults",
                            "    runs-on: ubuntu-latest",
                            "    defaults: {run: {shell: bash}}\n"
                            "    runs-on: ubuntu-latest",
                        ),
                        (
                            "unknown",
                            "    runs-on: ubuntu-latest",
                            "    mystery: true\n"
                            "    runs-on: ubuntu-latest",
                        ),
                ):
                        mutations.append(
                            (
                                job_name,
                                label,
                                self.replace_in_job(
                                    original,
                                    job_name,
                                    old,
                                    new,
                                ),
                            )
                        )
            for job_name, label, old, new in (
                (
                        "patch-release",
                        "needs",
                        "    needs: [event-identity]",
                        "    needs: [event-classifier]",
                ),
                (
                        "patch-release",
                        "if",
                        f"    if: {verify_mod._PUBLISHER_CONDITION}",
                        "    if: always()",
                ),
                (
                        "patch-release",
                        "env",
                        "      PATCH_COMMIT: ${{ "
                        "needs.event-identity.outputs.fallback_sha }}",
                        "      PATCH_COMMIT: attacker",
                ),
                (
                        "patch-release",
                        "revision",
                        '        test "$ACTUAL_SHA" = "$PATCH_COMMIT"',
                        "        true",
                ),
                (
                        "patch-release",
                        "secret",
                        "        BASEROM_URL: ${{ secrets.BASEROM_URL }}",
                        "        BASEROM_URL: ${{ secrets.OTHER }}",
                ),
                (
                        "patch-release",
                        "step",
                        "    - name: Build candidate in isolated namespace "
                        "and stage public inputs",
                        "    - name: Skip isolated candidate build",
                ),
                (
                        "patch-release",
                        "command",
                        "/usr/bin/python3 -I -S -c",
                        "python3 -c",
                ),
                (
                        "patch-release",
                        "isolation",
                        "        /usr/bin/mount --make-rprivate /",
                        "        true",
                ),
                (
                        "patch-release",
                        "late-upload-revalidation",
                        "    - name: Revalidate patch-only upload",
                        "    - name: Skip patch-only upload revalidation",
                ),
                (
                        "patch-release",
                        "candidate-output-isolation",
                        "          < /dev/null > /dev/null 2>&1 &",
                        "          &",
                ),
                (
                        "patch-release",
                        "supervisor-cgroup-view",
                        '        /usr/bin/mount --bind "$cgroup_path" '
                        "/mnt/supervisor/cgroup",
                        "        true",
                ),
                (
                        "patch-release",
                        "late-supervisor-parent-remount",
                        "        /usr/bin/mount -t tmpfs \\\n"
                        "          -o nosuid,mode=0755,size=4m builder-dev /dev",
                        "        /usr/bin/mount -o remount,ro,nosuid,nodev,noexec "
                        "/mnt/supervisor\n"
                        "        /usr/bin/mount -t tmpfs \\\n"
                        "          -o nosuid,mode=0755,size=4m builder-dev /dev",
                ),
                (
                        "patch-release",
                        "reordered-supervisor-parent-remount",
                        "        /usr/bin/mount -t tmpfs \\\n"
                        "          -o nosuid,mode=0755,size=4m builder-dev /dev",
                        "        /usr/bin/mount -o nodev,ro,noexec,nosuid,remount "
                        "/mnt/supervisor\n"
                        "        /usr/bin/mount -t tmpfs \\\n"
                        "          -o nosuid,mode=0755,size=4m builder-dev /dev",
                ),
                (
                        "patch-release",
                        "action",
                        verify_mod._UPLOAD_USES,
                        "actions/upload-artifact@" + "0" * 40,
                ),
                (
                        "summary",
                        "if",
                        "    if: always()",
                        "    if: false",
                ),
                (
                        "summary",
                        "needs",
                        "    needs: [event-identity, event-classifier, "
                        "host-tests, build, "
                        "extended-host-tests, legacy, patch-release]",
                        "    needs: [build, host-tests, legacy]",
                ),
                (
                        "summary",
                        "env",
                        "      HOST_TESTS_RESULT: ${{ needs.host-tests.result }}",
                        "      HOST_TESTS_RESULT: success",
                ),
                (
                        "summary",
                        "command",
                        '            exit 1',
                        '            exit 0',
                ),
                (
                        "summary",
                        "action",
                        "    - name: Render fail-closed combined Build summary",
                        "    - uses: actions/checkout@" + "0" * 40,
                ),
            ):
                mutations.append(
                        (
                            job_name,
                            label,
                            self.replace_in_job(original, job_name, old, new),
                        )
                )
            for job_name, label, changed in mutations:
                with self.subTest(job=job_name, mutation=label):
                        self.assert_only_job_changed(original, changed, job_name)
                        with open(workflow_path, "w", encoding="utf-8") as handle:
                            handle.write(changed)
                        with self.assertRaises(ValueError):
                            verify_mod.run_gates(target_root, dry_run=True)

            workflow_mutations = {
                "env": original.replace(
                    "permissions:\n",
                    "env:\n  BASH_ENV: build/mask\n\npermissions:\n",
                    1,
                ),
                "defaults": original.replace(
                    "permissions:\n",
                    "defaults:\n  run:\n    shell: bash\n\npermissions:\n",
                    1,
                ),
                "permissions": original.replace(
                    "  contents: read",
                    "  contents: write",
                    1,
                ),
                "concurrency": original.replace(
                    "permissions:\n",
                    "concurrency: target-controlled\n\npermissions:\n",
                    1,
                ),
            }
            for mutation, changed in workflow_mutations.items():
                with self.subTest(workflow=mutation):
                    with open(
                        workflow_path,
                        "w",
                        encoding="utf-8",
                    ) as handle:
                        handle.write(changed)
                    with self.assertRaises(ValueError):
                        verify_mod.run_gates(target_root, dry_run=True)

    def test_source_root_gate_equivalence_remains_supported(self):
        self.assertEqual(
            len(verify_mod.run_gates(REPO_ROOT, dry_run=True)),
            28,
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
        snapshot = publisher_command_signatures._authority_snapshot()
        completed = subprocess.run(
            verify_mod.publisher_authority_command(
                REPO_ROOT,
                snapshot.revision,
                "upstream-verify",
                "--dry-run",
            ),
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
