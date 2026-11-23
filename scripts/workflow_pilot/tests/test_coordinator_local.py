"""Real coordinator-owned checks, without a delegated owner or new result commit."""

import copy
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from jsonschema import Draft202012Validator

from scripts.workflow_pilot import adaptive_gate as gate, agent_handoff as handoff
from scripts.workflow_pilot import coordinator_observations as observations, raw_diff_check as raw
from scripts.workflow_pilot.tests.test_agent_handoff import GitFixture, at_offset
from scripts.workflow_pilot.tests.test_adaptive_gate import decisions, model_control


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


def legacy_local(local):
    local = copy.deepcopy(local)
    definitions = local.pop("definition_sets", None)
    if definitions is not None:
        for captured in local["checks"].values():
            captured["definitions"] = copy.deepcopy(definitions[captured.pop("definition_index")])
    return local


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
            repository=self.state["repository"], repository_id=1, number=191, head_sha=self.head,
            head_ref="agent/test", base_sha=self.fixture.parent, base_ref="master")
        decision = gate.select_mode(decisions(), number=191, head_sha=self.head,
                                    decision_oid="a" * 40, changed_lines=1)
        self.record = gate.begin_candidate(
            self.state, self.pr, self.pr.base_sha, model_control(decision, self.pr), runs=())

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
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        gate.capture_local_check(self.state, self.record, self.pr, "raw")
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        check = gate.capture_local_check(self.state, self.record, self.pr, "config", self.executor)
        self.assertEqual(check["exit_code"], 0)
        self.assertGreater(check["pid"], 0)
        self.assertGreater(check["peak_rss_bytes"], 0)
        self.assertEqual(check["measurements"]["protocol_changes"], 1)
        self.assertTrue(gate._local_ready(self.state, self.pr, self.record))
        self.assertEqual(self.state["assignments"], [])
        return local

    def test_current_committed_head_can_complete_all_checks_without_owner_or_commit(self):
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
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
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))

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
            self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        self.record["local_validation"] = good
        missing = gate.capture_local_check(
            self.state, self.record, self.pr, "config", lambda *_: ({"passed": True}, {}))
        self.assertIsNone(missing["exit_code"])
        self.assertIn("unavailable", missing["detail"])
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
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
            self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        self.record["local_validation"] = copy.deepcopy(good)
        self.record["local_validation"]["git_identity"]["inode"] += 1
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        self.record["local_validation"] = copy.deepcopy(good)
        changed_base = SimpleNamespace(**{**vars(self.pr), "base_sha": self.head})
        self.assertFalse(gate._local_ready(self.state, changed_base, self.record))
        path = self.fixture.worktree / "docs/value.json"
        path.write_text('{"value":8}\n')
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        path.write_text('{"value":7}\n')
        self.record["local_validation"]["clock"]["boot_id"] = "changed-boot"
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        self.record["local_validation"] = copy.deepcopy(good)
        self.state["availability"]["valid_until"] = at_offset(-1)
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
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
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))

    def test_applicable_incomplete_delegation_cannot_be_hidden(self):
        self.complete()
        assignment = copy.deepcopy(self.fixture.assignment)
        assignment["assigned_parent_sha"] = self.head
        entry = handoff.assign(self.state, assignment)
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        with self.assertRaises(ValueError):
            gate.register_local_validation(self.state, self.record, self.pr, self.fixture.worktree, CHECKS)
        entry["closed_at"] = observations.utc_now()
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
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
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))
        (self.fixture.worktree / "docs/value.json").write_text('{"value":7}\n')
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))

    def test_registry_change_during_real_capture_invalidates_the_observation(self):
        self.complete()

        def changed(context, head):
            result = self.executor(context, head)
            self.record["local_validation"]["required_checks"]["config"]["evidence_id"] = "config-v2"
            return result

        check = gate.capture_local_check(self.state, self.record, self.pr, "config", changed)
        self.assertIsNone(check["exit_code"])
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))

    def test_removing_a_criterion_and_its_result_cannot_shrink_registered_coverage(self):
        definitions = {**CHECKS, "second": {**CHECKS["config"], "evidence_id": "second-v1"}}
        local = gate.register_local_validation(
            self.state, self.record, self.pr, self.fixture.worktree, definitions)
        for check_id in definitions:
            gate.capture_local_check(self.state, self.record, self.pr, check_id,
                                     self.executor if check_id != "raw" else None)
        self.assertTrue(gate._local_ready(self.state, self.pr, self.record))
        del local["required_checks"]["second"]
        del local["checks"]["second"]
        self.assertFalse(gate._local_ready(self.state, self.pr, self.record))

    def test_locked_migration_preserves_complete_stale_and_incomplete_captures(self):
        definitions = {
            **CHECKS,
            "second": {**CHECKS["config"], "evidence_id": "second"},
            "third": {**CHECKS["config"], "evidence_id": "third"},
        }
        gate.register_local_validation(self.state, self.record, self.pr, self.fixture.worktree, definitions)
        for check_id in definitions:
            gate.capture_local_check(self.state, self.record, self.pr, check_id,
                                     self.executor if check_id != "raw" else None)
        self.assertTrue(gate._local_ready(self.state, self.pr, self.record))
        original = copy.deepcopy(self.state)
        original["candidates"][0]["local_validation"] = legacy_local(
            original["candidates"][0]["local_validation"])
        path = self.fixture.home / "coordinator.json"

        for condition in ("current", "stale", "failed", "unfinished"):
            with self.subTest(condition=condition):
                old = copy.deepcopy(original)
                local = old["candidates"][0]["local_validation"]
                if condition == "stale":
                    local["checks"]["raw"]["definitions"]["config"]["evidence_id"] = "old-config"
                elif condition == "failed":
                    local["checks"]["config"]["observation"]["exit_code"] = 7
                elif condition == "unfinished":
                    local["checks"]["config"]["observation"].update(completed_at=None, exit_code=None)
                self.assertEqual(gate._local_ready(old, self.pr, old["candidates"][0]),
                                 condition == "current")
                self.assertTrue(Draft202012Validator(json.loads(
                    (ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text()
                )).is_valid(old))
                path.write_bytes(observations.json_bytes(old))
                result = subprocess.run(
                    [sys.executable, "-I", str(ROOT / "scripts/workflow_pilot/isolated_launcher.py"),
                     "agent-handoff", "compact", "--state", str(path)],
                    cwd=ROOT, env=raw.git_environment(), capture_output=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr.decode())
                loaded = observations.load_json(path)
                handoff.validate_state(loaded)
                self.assertTrue(Draft202012Validator(json.loads(
                    (ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text()
                )).is_valid(loaded))
                compact = loaded["candidates"][0]["local_validation"]
                self.assertEqual(len(compact["definition_sets"]), 2 if condition == "stale" else 1)
                self.assertEqual(legacy_local(compact), local)
                self.assertEqual(gate._local_ready(loaded, self.pr, loaded["candidates"][0]),
                                 condition == "current")
                expanded = copy.deepcopy(loaded)
                expanded["candidates"][0]["local_validation"] = legacy_local(compact)
                expanded["clock"]["at"] = old["clock"]["at"]
                self.assertEqual(expanded, old)
                if condition == "stale":
                    gate.capture_local_check(loaded, loaded["candidates"][0], self.pr, "raw")
                    self.assertTrue(gate._local_ready(loaded, self.pr, loaded["candidates"][0]))
                    self.assertEqual(len(compact["definition_sets"]), 1)

    def test_legacy_capture_compacts_only_when_repeated_sets_save_space(self):
        self.complete()
        local = legacy_local(self.record["local_validation"])
        del local["checks"]["config"]
        self.record["local_validation"] = local
        handoff.compact_state(self.state)
        self.assertNotIn("definition_sets", local)
        gate.capture_local_check(self.state, self.record, self.pr, "config", self.executor)
        self.assertIn("definition_sets", local)
        self.assertTrue(gate._local_ready(self.state, self.pr, self.record))

    def test_in_memory_compaction_keeps_current_candidate_and_registration_references(self):
        local = self.complete()
        handoff.compact_state(self.state)
        self.assertIs(self.state["candidates"][0], self.record)
        self.assertIs(self.record["local_validation"], local)
        gate.capture_local_check(self.state, self.record, self.pr, "config", self.executor)
        self.assertTrue(gate._local_ready(self.state, self.pr, self.state["candidates"][0]))

    def test_snapshot_compaction_recovers_a_real_locked_node_budget_failure(self):
        self.complete()
        seed = copy.deepcopy(self.record)
        local = legacy_local(seed["local_validation"])
        definitions = {
            "raw": CHECKS["raw"],
            **{f"case-{index}": {**CHECKS["config"], "evidence_id": f"case-{index}"}
               for index in range(12)},
        }
        captures = {}
        for check_id, definition in definitions.items():
            observation = copy.deepcopy(local["checks"]["raw" if check_id == "raw" else "config"]["observation"])
            observation.update(id=check_id, evidence_id=definition["evidence_id"])
            captures[check_id] = {"definitions": copy.deepcopy(definitions), "observation": observation}
        local.update(required_checks=copy.deepcopy(definitions), checks=captures)
        seed["local_validation"] = local
        state = copy.deepcopy(self.state)
        state["candidates"] = []

        for number in range(200, 300):
            candidate = copy.deepcopy(seed)
            candidate["pr_number"] = candidate["local_validation"]["pr_number"] = number
            state["candidates"].append(candidate)
            try:
                observations.json_bytes(state)
            except ValueError as error:
                self.assertIn("node limit", str(error))
                overflow = state["candidates"].pop()
                break
        else:
            self.fail("fixture did not reach the existing node bound")

        handoff.validate_state(state)
        path = self.fixture.home / "budget.json"
        before = observations.json_bytes(state)
        path.write_bytes(before)
        with self.assertRaisesRegex(ValueError, "node limit"):
            with observations.locked_state(path) as current:
                current["candidates"].append(overflow)
        self.assertEqual(path.read_bytes(), before)

        with observations.locked_state(path) as current:
            handoff.compact_state(current)
        compacted = observations.load_json(path)
        handoff.validate_state(compacted)
        self.assertLess(len(path.read_bytes()), len(before))
        with observations.locked_state(path) as current:
            current["candidates"].append(overflow)
            handoff.validate_state(current)
        self.assertEqual(len(observations.load_json(path)["candidates"]), len(state["candidates"]) + 1)

    def test_compaction_skips_growing_records_at_the_node_boundary(self):
        self.complete()
        legacy = legacy_local(self.record["local_validation"])
        state = copy.deepcopy(self.state)
        state["candidates"] = []
        for number in range(200, 217):
            candidate = copy.deepcopy(self.record)
            candidate["pr_number"] = number
            local = copy.deepcopy(legacy)
            local["pr_number"] = number
            if number != 216:
                del local["checks"]["config"]
            candidate["local_validation"] = local
            state["candidates"].append(candidate)

        def count_nodes(value):
            if isinstance(value, dict):
                return 1 + sum(count_nodes(key) + count_nodes(child) for key, child in value.items())
            if isinstance(value, list):
                return 1 + sum(count_nodes(child) for child in value)
            return 1

        handoff.validate_state(state)
        original = copy.deepcopy(state)
        path = self.fixture.home / "selective-compaction.json"
        with mock.patch.object(observations, "MAX_NODES", count_nodes(state)):
            path.write_bytes(observations.json_bytes(state))
            with observations.locked_state(path) as current:
                result = handoff.compact_state(current)
            reloaded = observations.load_json(path)
            handoff.validate_state(reloaded)
        self.assertEqual(result["compacted_candidates"], 1)
        for before, after in zip(original["candidates"][:-1], reloaded["candidates"][:-1]):
            self.assertEqual(after, before)
            self.assertNotIn("definition_sets", after["local_validation"])
        final = reloaded["candidates"][-1]["local_validation"]
        self.assertIn("definition_sets", final)
        self.assertEqual(legacy_local(final), original["candidates"][-1]["local_validation"])
        self.assertLess(count_nodes(reloaded), count_nodes(original))

    def test_compacting_reloaded_snapshots_never_grows_a_byte_limited_state(self):
        local = self.complete()
        raw_observation = local["checks"]["raw"]["observation"]
        semantic_observation = local["checks"]["config"]["observation"]
        definitions = {
            "raw": CHECKS["raw"],
            **{f"z-{index:02}": {**CHECKS["config"], "evidence_id": f"z-{index:02}"}
               for index in range(20)},
            **{f"a-{index:02}": {**CHECKS["config"], "evidence_id": f"a-{index:02}"}
               for index in range(10)},
        }
        snapshots = [copy.deepcopy(definitions)]
        for index in range(10):
            snapshot = copy.deepcopy(definitions)
            snapshot["raw"]["evidence_id"] = f"raw-version-{index}"
            snapshots.append(snapshot)

        checks = {}
        for check_id in definitions:
            observation = copy.deepcopy(raw_observation if check_id == "raw" else semantic_observation)
            observation.update(id=check_id, evidence_id=definitions[check_id]["evidence_id"])
            index = int(check_id[2:]) + 1 if check_id.startswith("a-") else 0
            checks[check_id] = {"definition_index": index, "observation": observation}
        local.update(required_checks=copy.deepcopy(definitions), definition_sets=snapshots, checks=checks)
        handoff.validate_state(self.state)
        path = self.fixture.home / "byte-limited-compaction.json"
        before = observations.json_bytes(self.state)
        path.write_bytes(before)
        with (mock.patch.object(observations, "MAX_JSON_BYTES", len(before)),
              mock.patch.object(observations, "utc_now", return_value=self.state["clock"]["at"])):
            with observations.locked_state(path) as current:
                result = handoff.compact_state(current)
            self.assertEqual(path.read_bytes(), before)
            reloaded = observations.load_json(path)
        self.assertEqual(result["compacted_candidates"], 0)
        self.assertEqual(legacy_local(reloaded["candidates"][0]["local_validation"]), legacy_local(local))

    def test_failed_migration_never_replaces_the_original_state(self):
        self.complete()
        old = copy.deepcopy(self.state)
        old["candidates"][0]["local_validation"] = legacy_local(
            old["candidates"][0]["local_validation"])
        path = self.fixture.home / "failed-migration.json"
        before = observations.json_bytes(old)
        path.write_bytes(before)
        output, error = io.StringIO(), io.StringIO()
        with (mock.patch.object(observations.os, "replace", side_effect=OSError("replace unavailable")),
              mock.patch.object(sys, "stdout", output), mock.patch.object(sys, "stderr", error)):
            self.assertEqual(handoff.main(["compact", "--state", str(path)]), 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("replace unavailable", error.getvalue())
        self.assertEqual(path.read_bytes(), before)

    def test_directory_sync_failure_reports_uncertain_replacement(self):
        self.complete()
        old = copy.deepcopy(self.state)
        old["candidates"][0]["local_validation"] = legacy_local(
            old["candidates"][0]["local_validation"])
        path = self.fixture.home / "uncertain-migration.json"
        before = observations.json_bytes(old)
        path.write_bytes(before)
        real_fsync = os.fsync

        def sync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("directory sync unavailable")
            return real_fsync(fd)

        output, error = io.StringIO(), io.StringIO()
        with (mock.patch.object(observations.os, "fsync", side_effect=sync),
              mock.patch.object(sys, "stdout", output), mock.patch.object(sys, "stderr", error)):
            self.assertEqual(handoff.main(["compact", "--state", str(path)]), 2)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("durability is uncertain", error.getvalue())
        self.assertIn("directory sync unavailable", error.getvalue())
        self.assertNotEqual(path.read_bytes(), before)
        reloaded = observations.load_json(path)
        handoff.validate_state(reloaded)
        self.assertEqual(legacy_local(reloaded["candidates"][0]["local_validation"]),
                         old["candidates"][0]["local_validation"])
        self.assertTrue(gate._local_ready(reloaded, self.pr, reloaded["candidates"][0]))

    def test_schema_and_runtime_reuse_closed_check_types(self):
        self.complete()
        validator = Draft202012Validator(json.loads(
            (ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text()))
        self.assertTrue(validator.is_valid(self.state))
        handoff.validate_state(self.state)
        for field, value in (("local_ready", True), ("program", "success"), ("checks", []),
                             ("required_checks", {"raw": CHECKS["raw"]}), ("definition_sets", None)):
            state = copy.deepcopy(self.state)
            state["candidates"][0]["local_validation"][field] = value
            self.assertFalse(validator.is_valid(state))
            with self.assertRaises(ValueError):
                handoff.validate_state(state)
        for index in (-1, True, 32):
            state = copy.deepcopy(self.state)
            state["candidates"][0]["local_validation"]["checks"]["raw"]["definition_index"] = index
            self.assertFalse(validator.is_valid(state))
            with self.assertRaises(ValueError):
                handoff.validate_state(state)
        state = copy.deepcopy(self.state)
        state["candidates"][0]["local_validation"]["checks"]["raw"]["definition_index"] = 31
        self.assertTrue(validator.is_valid(state))
        with self.assertRaisesRegex(ValueError, "definition index"):
            handoff.validate_state(state)
        state = copy.deepcopy(self.state)
        definitions = state["candidates"][0]["local_validation"]["definition_sets"]
        definitions.append(copy.deepcopy(definitions[0]))
        self.assertFalse(validator.is_valid(state))
        with self.assertRaisesRegex(ValueError, "duplicate captured definition set"):
            handoff.validate_state(state)
        state = copy.deepcopy(self.state)
        del state["candidates"][0]["local_validation"]["definition_sets"]
        self.assertFalse(validator.is_valid(state))
        with self.assertRaises(ValueError):
            handoff.validate_state(state)
