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
        self.owner_step(topology.WORKFLOW.read_text(encoding="utf-8"))
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
        foundation = importlib.import_module("scripts.validation_ownership.tests.test_foundation")
        expected = list(case_ids(unittest.TestLoader().loadTestsFromModule(foundation)))
        self.assertTrue(expected)
        self.assertEqual(sorted(collector.selected), sorted(expected))
        self.assertEqual(len(collector.selected), len(set(collector.selected)))

    def test_native_cases_are_not_duplicated_in_workflow_discovery(self):
        foundation = importlib.import_module("scripts.validation_ownership.tests.test_foundation")
        native = set(case_ids(unittest.TestLoader().loadTestsFromModule(foundation)))
        loader = unittest.TestLoader()
        workflow = set(case_ids(loader.discover(str(ROOT / "tests/workflows"), pattern="test_*.py")))
        self.assertEqual(loader.errors, [])
        self.assertTrue(native)
        self.assertTrue(workflow)
        self.assertEqual(native & workflow, set())

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
