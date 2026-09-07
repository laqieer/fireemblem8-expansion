"""Real coordinator-owned checks, without a delegated owner or new result commit."""

import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator

from scripts.workflow_pilot import adaptive_gate as gate, agent_handoff as handoff
from scripts.workflow_pilot import coordinator_observations as observations, raw_diff_check as raw
from scripts.workflow_pilot.tests.test_agent_handoff import GitFixture, at_offset
from scripts.workflow_pilot.tests.test_adaptive_gate import decisions


ROOT = Path(__file__).resolve().parents[3]
CHECKS = {
    "raw": {"contract": "git-diff-check", "evidence_id": "raw-v1", "inputs": []},
    "config": {"contract": "coordinator-check", "evidence_id": "config-v1", "inputs": []},
}
CONFIG_CHECK = """
import json, sys
from pathlib import Path
value = json.loads((Path(sys.argv[1]) / 'docs/value.json').read_text())
if value != {'value': 7}:
    raise SystemExit(7)
print(json.dumps({'protocol_changes': 1}))
"""


class CoordinatorLocalTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GitFixture(assign=False)
        self.addCleanup(self.fixture.close)
        self.head = self.fixture.commit('{"value":7}\n', name="docs/value.json")
        self.state = handoff.new_state("owner/repository", "coordinator-one", {
            "mode": "plan", "observed_at": at_offset(-10), "valid_until": at_offset(300),
            "autostop_enabled": None, "stop_on_disconnect": None,
            "plan": "Bounded test-owned validation; no host settings asserted.",
        })
        self.pr = SimpleNamespace(
            repository=self.state["repository"], number=191, head_sha=self.head,
            head_ref="agent/test", base_sha=self.fixture.parent, base_ref="master")
        decision = gate.select_mode(decisions(), number=191, head_sha=self.head,
                                    decision_oid="a" * 40, changed_lines=1)
        self.record = gate.begin_candidate(self.state, self.pr, self.pr.base_sha, decision)

    def executor(self, context, head):
        self.assertEqual(head, self.head)
        result = raw.run_process(
            ["/usr/bin/python3", "-I", "-B", "-c", CONFIG_CHECK, context["allowed_worktree"]],
            cwd=ROOT, env=raw.git_environment())
        measurements = dict.fromkeys(handoff.METRICS)
        if result.returncode == 0:
            measurements.update(json.loads(result.stdout), rom_bytes=0, ram_bytes=0)
        return result, measurements

    def complete(self):
        local = gate.register_local_validation(
            self.state, self.record, self.pr, self.fixture.worktree, CHECKS)
        self.assertFalse(gate._local_ready(self.state, self.pr))
        gate.capture_local_check(self.state, self.record, self.pr, "raw")
        self.assertFalse(gate._local_ready(self.state, self.pr))
        check = gate.capture_local_check(self.state, self.record, self.pr, "config", self.executor)
        self.assertEqual(check["exit_code"], 0)
        self.assertGreater(check["pid"], 0)
        self.assertGreater(check["peak_rss_bytes"], 0)
        self.assertEqual(check["measurements"]["protocol_changes"], 1)
        self.assertTrue(gate._local_ready(self.state, self.pr))
        self.assertEqual(self.state["assignments"], [])
        return local

    def test_current_committed_head_can_complete_all_checks_without_owner_or_commit(self):
        self.assertFalse(gate._local_ready(self.state, self.pr))
        self.complete()
        self.assertEqual(handoff.observe_git({"allowed_worktree": str(self.fixture.worktree)})["head"],
                         self.head)
        self.assertIsNone(self.record["full_run_id"])
        self.assertIsNone(self.record["dispatch_requested_at"])

    def test_raw_only_and_candidate_success_or_commands_are_not_registration(self):
        for definitions in (
            {"raw": CHECKS["raw"]},
            {**CHECKS, "config": {**CHECKS["config"], "program": "passed"}},
            {**CHECKS, "config": {**CHECKS["config"], "inputs": ["candidate.py"]}},
        ):
            with self.subTest(definitions=definitions), self.assertRaises(ValueError):
                gate.register_local_validation(
                    self.state, self.record, self.pr, self.fixture.worktree, definitions)
        self.assertFalse(gate._local_ready(self.state, self.pr))

    def test_missing_failed_incomplete_and_changed_definitions_never_reuse_success(self):
        local = self.complete()
        good = copy.deepcopy(local)
        mutations = [
            lambda value: value["checks"].pop("config"),
            lambda value: value["checks"]["config"]["observation"].update(exit_code=7),
            lambda value: value["checks"]["config"]["observation"].update(completed_at=None, exit_code=None),
            lambda value: value["checks"]["config"]["observation"].update(pid=None),
            lambda value: value["checks"]["config"]["observation"].update(peak_rss_bytes=None),
            lambda value: value["checks"]["config"]["observation"].update(result_sha="b" * 40),
            lambda value: value["required_checks"]["config"].update(evidence_id="config-v2"),
            lambda value: value["required_checks"].update(extra={**CHECKS["config"], "evidence_id": "extra"}),
        ]
        for change in mutations:
            self.record["local_validation"] = copy.deepcopy(good)
            change(self.record["local_validation"])
            self.assertFalse(gate._local_ready(self.state, self.pr))
        self.record["local_validation"] = good
        missing = gate.capture_local_check(
            self.state, self.record, self.pr, "config", lambda *_: ({"passed": True}, {}))
        self.assertIsNone(missing["exit_code"])
        self.assertIn("unavailable", missing["detail"])
        self.assertFalse(gate._local_ready(self.state, self.pr))
        gate.register_local_validation(self.state, self.record, self.pr, self.fixture.worktree, CHECKS)
        self.assertEqual(self.record["local_validation"]["checks"], {})

    def test_worktree_base_identity_and_availability_are_reobserved(self):
        local = self.complete()
        good = copy.deepcopy(local)
        for field, value in (("head_sha", "b" * 40), ("base_sha", "b" * 40),
                             ("base_ref", "other"), ("coordinator_id", "other"),
                             ("worktree", str(self.fixture.repository))):
            self.record["local_validation"] = copy.deepcopy(good)
            self.record["local_validation"][field] = value
            self.assertFalse(gate._local_ready(self.state, self.pr))
        self.record["local_validation"] = copy.deepcopy(good)
        self.record["local_validation"]["git_identity"]["inode"] += 1
        self.assertFalse(gate._local_ready(self.state, self.pr))
        self.record["local_validation"] = copy.deepcopy(good)
        changed_base = SimpleNamespace(**{**vars(self.pr), "base_sha": self.head})
        self.assertFalse(gate._local_ready(self.state, changed_base))
        path = self.fixture.worktree / "docs/value.json"
        path.write_text('{"value":8}\n')
        self.assertFalse(gate._local_ready(self.state, self.pr))
        path.write_text('{"value":7}\n')
        self.record["local_validation"]["clock"]["boot_id"] = "changed-boot"
        self.assertFalse(gate._local_ready(self.state, self.pr))
        self.record["local_validation"] = copy.deepcopy(good)
        self.state["availability"]["valid_until"] = at_offset(-1)
        self.assertFalse(gate._local_ready(self.state, self.pr))
        with self.assertRaises(ValueError):
            gate.capture_local_check(self.state, self.record, self.pr, "config", self.executor)

    def test_real_failed_native_capture_replaces_prior_success(self):
        self.complete()

        def failed(context, head):
            result = raw.run_process(
                ["/usr/bin/python3", "-I", "-B", "-c", "raise SystemExit(7)"],
                cwd=ROOT, env=raw.git_environment())
            return result, dict.fromkeys(handoff.METRICS)

        check = gate.capture_local_check(self.state, self.record, self.pr, "config", failed)
        self.assertEqual(check["exit_code"], 7)
        self.assertGreater(check["pid"], 0)
        self.assertFalse(gate._local_ready(self.state, self.pr))

    def test_applicable_incomplete_delegation_cannot_be_hidden(self):
        self.complete()
        assignment = copy.deepcopy(self.fixture.assignment)
        assignment["assigned_parent_sha"] = self.head
        entry = handoff.assign(self.state, assignment)
        self.assertFalse(gate._local_ready(self.state, self.pr))
        with self.assertRaises(ValueError):
            gate.register_local_validation(self.state, self.record, self.pr, self.fixture.worktree, CHECKS)
        entry["closed_at"] = observations.utc_now()
        self.assertFalse(gate._local_ready(self.state, self.pr))
        with self.assertRaises(ValueError):
            gate.register_local_validation(self.state, self.record, self.pr, self.fixture.worktree, CHECKS)

    def test_capture_detects_real_worktree_mutation_and_loses_prior_success(self):
        self.complete()

        def changing(context, head):
            result, measurements = self.executor(context, head)
            (self.fixture.worktree / "docs/value.json").write_text('{"value":8}\n')
            return result, measurements

        check = gate.capture_local_check(self.state, self.record, self.pr, "config", changing)
        self.assertIsNone(check["exit_code"])
        self.assertFalse(gate._local_ready(self.state, self.pr))
        (self.fixture.worktree / "docs/value.json").write_text('{"value":7}\n')
        self.assertFalse(gate._local_ready(self.state, self.pr))

    def test_registry_change_during_real_capture_invalidates_the_observation(self):
        self.complete()

        def changed(context, head):
            result = self.executor(context, head)
            self.record["local_validation"]["required_checks"]["config"]["evidence_id"] = "config-v2"
            return result

        check = gate.capture_local_check(self.state, self.record, self.pr, "config", changed)
        self.assertIsNone(check["exit_code"])
        self.assertFalse(gate._local_ready(self.state, self.pr))

    def test_removing_a_criterion_and_its_result_cannot_shrink_registered_coverage(self):
        definitions = {**CHECKS, "second": {**CHECKS["config"], "evidence_id": "second-v1"}}
        local = gate.register_local_validation(
            self.state, self.record, self.pr, self.fixture.worktree, definitions)
        for check_id in definitions:
            gate.capture_local_check(self.state, self.record, self.pr, check_id,
                                     self.executor if check_id != "raw" else None)
        self.assertTrue(gate._local_ready(self.state, self.pr))
        del local["required_checks"]["second"]
        del local["checks"]["second"]
        self.assertFalse(gate._local_ready(self.state, self.pr))

    def test_schema_and_runtime_reuse_closed_check_types(self):
        self.complete()
        validator = Draft202012Validator(json.loads(
            (ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text()))
        self.assertTrue(validator.is_valid(self.state))
        handoff.validate_state(self.state)
        for field, value in (("local_ready", True), ("program", "success"), ("checks", []),
                             ("required_checks", {"raw": CHECKS["raw"]})):
            state = copy.deepcopy(self.state)
            state["candidates"][0]["local_validation"][field] = value
            self.assertFalse(validator.is_valid(state))
            with self.assertRaises(ValueError):
                handoff.validate_state(state)
