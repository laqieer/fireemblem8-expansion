"""Keep the complete native probe suite in one required, parallel CI owner."""

import importlib
import shlex
import subprocess
import unittest
from pathlib import Path

from tests.workflows import test_build_ci_topology as topology


ROOT = Path(__file__).resolve().parents[2]
PROBE_COMMAND = (
    "make", "-f", "scripts/validation_ownership/foundation.mk", "ownership-probe-test",
)
PROBE_TEST_MODULES = (
    "scripts.validation_ownership.tests.test_foundation",
    "scripts.validation_ownership.tests.test_producer",
    "scripts.validation_ownership.tests.test_dependency",
)
NATIVE_PACKAGES = frozenset({
    "build-essential", "binutils-arm-none-eabi", "libpng-dev", "pkg-config", "python3-venv",
})


def case_ids(suite):
    for item in suite:
        if isinstance(item, unittest.TestCase):
            yield item.id()
        else:
            yield from case_ids(item)


class PlanCollector:
    """Inspect unittest selection without executing native cases in the host job."""

    def run(self, suite):
        self.selected = list(case_ids(suite))
        return unittest.TestResult()


class ProbeExecutionOwnershipTests(unittest.TestCase):
    def owner_step(self, text):
        owners = []
        for name, job in topology._job_blocks(text).items():
            for step in topology._step_blocks(job):
                for command in topology._run_block_commands(step):
                    if PROBE_COMMAND[-1] not in command:
                        continue
                    if tuple(shlex.split(command, comments=True)) == PROBE_COMMAND:
                        owners.append((name, step))
        self.assertEqual([name for name, _ in owners], ["extended-host-tests"])
        step = owners[0][1]
        fields = topology._direct_step_mapping_fields(step)
        self.assertIsNotNone(fields)
        self.assertEqual(len(fields), len(set(fields)))
        self.assertTrue({"name", "run"} <= set(fields) <= {"name", "run", "id"})
        return step

    def test_full_parallel_owner_selects_every_native_case(self):
        from scripts.workflow_pilot.tests.test_adaptive_gate import WorkflowTests, workflow_condition

        text = topology.WORKFLOW.read_text(encoding="utf-8")
        self.owner_step(text)
        command = subprocess.run(
            [*PROBE_COMMAND, "--dry-run"], cwd=ROOT, check=True,
            capture_output=True, text=True, timeout=10,
        )
        argv = shlex.split(command.stdout)
        self.assertEqual(argv[:3], ["python3", "-m", "unittest"])
        loader = unittest.TestLoader()
        collector = PlanCollector()
        unittest.TestProgram(
            module=None, argv=["unittest", *argv[3:]], exit=False,
            testLoader=loader, testRunner=collector,
        )
        self.assertEqual(loader.errors, [])
        expected = []
        for name in PROBE_TEST_MODULES:
            selected = list(case_ids(unittest.TestLoader().loadTestsFromModule(importlib.import_module(name))))
            self.assertTrue(selected, name)
            expected.extend(selected)
        self.assertEqual(len(expected), len(set(expected)))
        self.assertEqual(sorted(collector.selected), sorted(expected))
        self.assertEqual(len(collector.selected), len(set(collector.selected)))
        condition = topology._direct_job_if(topology._job_blocks(text)["extended-host-tests"])
        contexts = WorkflowTests()
        for event, mode in (
            ("pull_request", "full"), ("push", "full"), ("workflow_dispatch", "full"),
            ("pull_request", "metadata-only"), ("pull_request", "review-first"),
        ):
            with self.subTest(event=event, mode=mode):
                active = workflow_condition(condition, contexts.context(event, mode))
                selected = collector.selected if active else []
                self.assertEqual(sorted(selected), sorted(expected) if mode == "full" else [])

    def test_native_cases_are_not_duplicated_in_workflow_discovery(self):
        native = set()
        for name in PROBE_TEST_MODULES:
            selected = set(case_ids(unittest.TestLoader().loadTestsFromModule(importlib.import_module(name))))
            self.assertTrue(selected, name)
            native.update(selected)
        loader = unittest.TestLoader()
        workflow = set(case_ids(loader.discover(str(ROOT / "tests/workflows"), pattern="test_*.py")))
        self.assertEqual(loader.errors, [])
        self.assertTrue(native)
        self.assertTrue(workflow)
        self.assertEqual(native & workflow, set())

    def native_dependencies(self, text):
        steps = topology._step_blocks(topology._job_blocks(text)["extended-host-tests"])
        owner = self.owner_step(text)
        installers = []
        prefix = ["sudo", "apt-get", "update", "&&", "sudo", "apt-get", "install", "-y"]
        for index, step in enumerate(steps):
            for command in topology._run_block_commands(step):
                words = shlex.split(command, comments=True)
                if words[:len(prefix)] != prefix:
                    continue
                fields = topology._direct_step_mapping_fields(step)
                self.assertIsNotNone(fields)
                self.assertEqual(len(fields), len(set(fields)))
                self.assertTrue({"name", "run"} <= set(fields) <= {"name", "run", "id"})
                installers.append((index, words[len(prefix):]))
        self.assertEqual(len(installers), 1)
        index, packages = installers[0]
        self.assertEqual(set(packages), NATIVE_PACKAGES)
        self.assertLess(index, steps.index(owner))

    def test_native_owner_installs_real_consumer_dependencies_before_execution(self):
        text = topology.WORKFLOW.read_text(encoding="utf-8")
        self.native_dependencies(text)
        line = next(
            command for command in topology._run_block_commands(
                topology._job_blocks(text)["extended-host-tests"],
            ) if "apt-get install" in command
        )
        for package in sorted(NATIVE_PACKAGES):
            with self.subTest(missing_package=package):
                with self.assertRaises(AssertionError):
                    self.native_dependencies(text.replace(line, line.replace(" " + package, "", 1), 1))
        changed = line[:line.index("build-essential")] + " ".join(
            shlex.quote(package) for package in sorted(NATIVE_PACKAGES, reverse=True)
        )
        self.native_dependencies(text.replace(line, changed, 1))
        installer = next(
            step for step in topology._step_blocks(topology._job_blocks(text)["extended-host-tests"])
            if line in topology._run_block_commands(step)
        )
        for replacement in (
            installer.replace("      run:", "      if: false\n      run:", 1),
            installer.replace("      run:", "      continue-on-error: true\n      run:", 1),
            installer.replace(line, line + " || true"),
        ):
            with self.subTest(inactive_installer=replacement):
                with self.assertRaises(AssertionError):
                    self.native_dependencies(text.replace(installer, replacement, 1))
        owner = self.owner_step(text)
        with self.assertRaises(AssertionError):
            self.native_dependencies(text.replace(installer, "", 1).replace(owner, owner + installer, 1))

    def test_missing_duplicate_or_disabled_owner_rejects(self):
        text = topology.WORKFLOW.read_text(encoding="utf-8")
        step = self.owner_step(text)
        for replacement in (
            "", step + step,
            step.replace("      run:", "      if: false\n      run:", 1),
            step.replace("      run:", "      continue-on-error: true\n      run:", 1),
        ):
            with self.subTest(replacement=replacement):
                with self.assertRaises(AssertionError):
                    self.owner_step(text.replace(step, replacement, 1))
        quoted = step.replace(PROBE_COMMAND[2], f'"{PROBE_COMMAND[2]}"').replace(
            "    - name:", "    - name: Renamed", 1,
        )
        self.owner_step(text.replace(step, quoted, 1))
