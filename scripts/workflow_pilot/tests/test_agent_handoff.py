import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from scripts.workflow_pilot import agent_handoff as handoff
from scripts.workflow_pilot import coordinator_observations as observations
from scripts.workflow_pilot import raw_diff_check as raw
from scripts.workflow_pilot import reporter


ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = ROOT / "build/test-artifacts"
SESSION = "test-session"


def at_offset(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def write_json(path, value):
    Path(path).write_bytes(observations.json_bytes(value))


def git(root, *args, input=None, check=True):
    result = subprocess.run(
        raw.git_command(root, *args), env=raw.git_environment(), input=input,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if check and result.returncode:
        raise AssertionError(result.stderr.decode())
    return result.stdout.decode().strip()


def native_event(kind, event_id, **data):
    return {"id": event_id, "parentId": None, "timestamp": observations.utc_now(),
            "type": kind, "data": data}


class GitFixture:
    def __init__(self, *, upstream=False, assign=True):
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=ARTIFACTS, prefix="agent-handoff-")
        self.home = Path(self.directory.name)
        self.repository = self.home / "source"
        self.repository.mkdir()
        git(self.repository, "init", "-b", "master")
        git(self.repository, "config", "user.name", "Handoff Test")
        git(self.repository, "config", "user.email", "handoff@example.invalid")
        (self.repository / "docs").mkdir()
        (self.repository / "docs/base.txt").write_text("base\n")
        git(self.repository, "add", ".")
        git(self.repository, "commit", "-m", "upstream base without task trailers")
        self.parent = git(self.repository, "rev-parse", "HEAD")
        self.upstream = None
        if upstream:
            (self.repository / "imported.txt").write_text("imported upstream\n")
            (self.repository / "docs/from-upstream.txt").write_text("upstream\n")
            git(self.repository, "add", ".")
            git(self.repository, "commit", "-m", "upstream author, no Copilot trailers")
            self.upstream = git(self.repository, "rev-parse", "HEAD")
        self.worktree = self.home / "work"
        git(self.repository, "worktree", "add", "-b", "agent/test", str(self.worktree), self.parent)
        self.assignment = {
            "schema_version": 3, "repository": "owner/repository", "id": "assignment-one", "issue": 178,
            "pull_request": 191, "owner_id": "owner-one", "session_id": SESSION, "dispatch_id": "dispatch-one",
            "assigned_parent_sha": self.parent, "expected_branch": "agent/test",
            "allowed_worktree": str(self.worktree), "allowed_scope": ["docs/"],
            "upstream_inputs": [self.upstream] if self.upstream else [], "finding_ids": ["review-one"],
            "acceptance_criteria": {"case-one": {"text": "Clean, measured exact descendant",
                                                "evidence_ids": ["raw-evidence"]}},
            "required_checks": {"raw": {"contract": "git-diff-check", "evidence_id": "raw-evidence", "inputs": []}},
            "budgets": {"changed_lines": 100, "rom_bytes": 0, "ram_bytes": 0, "protocol_changes": 0},
            "max_lifetime_seconds": 300, "max_peak_rss_bytes": 256 * 1024 * 1024,
            "prohibited_remote_actions": list(handoff.PROHIBITED_REMOTE_ACTIONS),
            "predecessor_id": None, "kind": "initial",
        }
        availability = {"mode": "always-on", "observed_at": at_offset(-60), "valid_until": at_offset(3600),
                        "autostop_enabled": False, "stop_on_disconnect": False, "plan": None}
        self.state = handoff.new_state("owner/repository", "coordinator-one", availability)
        self.processes = []
        self.entry = handoff.assign(self.state, self.assignment) if assign else None
        self.parent_log, self.owner_log = self.home / "parent.jsonl", self.home / "owner.jsonl"

    def close(self):
        for process in self.processes:
            if process.returncode is None:
                process.kill()
                process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()
        self.directory.cleanup()

    def waiting_process(self, *, exit_code=0):
        process = subprocess.Popen(
            ["/usr/bin/python3", "-I", "-c",
             f"import sys; print('ready', flush=True); sys.stdin.readline(); sys.exit({exit_code})"],
            cwd=self.worktree, env=raw.git_environment(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        self.processes.append(process)
        self.assert_ready(process)
        return process

    def owner_process(self, entry=None, *, exit_code=0):
        entry = entry or self.entry
        process = self.waiting_process(exit_code=exit_code)
        handoff.bind_process(entry, process.pid)
        return process

    @staticmethod
    def assert_ready(process):
        import select
        if not select.select([process.stdout], [], [], 5)[0]:
            raise AssertionError("owned process did not start")
        if process.stdout.readline() != b"ready\n":
            raise AssertionError("unexpected child startup")

    def finish_owner(self, process, entry=None, *, kill=False):
        entry = entry or self.entry
        if kill:
            process.kill()
        else:
            process.stdin.write(b"done\n")
            process.stdin.flush()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            observed = observations.observe_owned_exit(process, entry["process"])
            if observed["state"] == "exited" and observed["rss_complete"]:
                handoff.record_process(entry, observed)
                return observed
            time.sleep(0.01)
        raise AssertionError("owned process did not exit")

    def append(self, path, *events):
        with path.open("ab") as output:
            for event in events:
                output.write((json.dumps(event) + "\n").encode())

    def receive(self, *, progressing=True):
        self.append(self.parent_log,
                    native_event("session.start", "parent-start", sessionId="coordinator-session"),
                    native_event("tool.execution_start", "dispatch-event",
                                 toolCallId=self.assignment["dispatch_id"], toolName="task"))
        handoff.observe_cli(self.entry, self.parent_log)
        self.append(self.owner_log,
                    native_event("session.start", "owner-start", sessionId=SESSION),
                    native_event("user.message", "receipt-event",
                                 content=f"[handoff:{self.assignment['id']}] Implement assigned scope.",
                                 parentAgentTaskId=self.assignment["dispatch_id"]),
                    native_event("assistant.turn_start", "turn-one", turnId="turn-one"))
        if progressing:
            self.append(self.owner_log, native_event("tool.execution_start", "progress-event",
                                                    toolCallId="edit-one", toolName="apply_patch"))
        handoff.observe_cli(self.entry, self.owner_log)

    def commit(self, content="change\n", *, name="docs/change.txt", trailers=True):
        file = self.worktree / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
        return self.commit_pending(trailers=trailers)

    def commit_pending(self, *, trailers=True):
        git(self.worktree, "add", "-A")
        message = "Implement assigned change"
        if trailers:
            message += f"\n\n{handoff.COPILOT_TRAILER}\nCopilot-Session: {SESSION}"
        git(self.worktree, "commit", "-m", message)
        return git(self.worktree, "rev-parse", "HEAD")

    def deliver(self, sha):
        result = {"schema_version": 3, "assignment_id": self.assignment["id"],
                  "assigned_parent_sha": self.parent, "result_sha": sha,
                  "evidence_refs": [check["evidence_id"] for check in self.assignment["required_checks"].values()]}
        self.append(self.owner_log, native_event("assistant.message", "delivery-event",
                                                content=json.dumps({"handoff_result": result})))
        handoff.observe_cli(self.entry, self.owner_log)
        return result

    def complete(self, **commit_args):
        process = self.owner_process()
        self.receive()
        result = self.deliver(self.commit(**commit_args))
        self.finish_owner(process)
        return result

    def validate(self, result, **kwargs):
        return handoff.validate_handoff(self.state, result, worktree=self.worktree, **kwargs)


class ExactHandoffTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GitFixture()
        self.addCleanup(self.fixture.close)

    def test_clean_descendant_actual_checks_and_os_exit(self):
        f = self.fixture
        result = f.complete()
        verdict = f.validate(result)
        self.assertTrue(verdict["handoff_ready"], verdict)
        self.assertEqual(verdict["task_commits"], [result["result_sha"]])
        self.assertEqual(verdict["changed_lines"], 1)
        self.assertEqual([event["state"] for event in f.entry["events"]], list(handoff.STATES))
        self.assertIsNotNone(f.entry["closed_at"])
        self.assertEqual(f.entry["checks"][0]["exit_code"], 0)
        self.assertGreater(f.entry["checks"][0]["pid"], 0)
        self.assertGreater(f.entry["process"]["peak_rss_bytes"], 0)
        self.assertEqual(f.entry["coordination_turns"], 1)

    def test_abnormal_owner_exit_preserves_delivered_wip_before_retirement(self):
        for exit_code, kill in ((7, False), (0, True)):
            with self.subTest(exit_code=exit_code, kill=kill):
                f = GitFixture()
                self.addCleanup(f.close)
                process = f.owner_process(exit_code=exit_code)
                f.receive()
                result = f.deliver(f.commit())
                handoff.capture_check(f.entry, "raw", result["result_sha"])
                checks = copy.deepcopy(f.entry["checks"])
                f.finish_owner(process, kill=kill)
                self.assertEqual(f.entry["process"]["exit_code"], -9 if kill else exit_code)
                index = Path(f.entry["git_identity"]["git_dir"]) / "index"
                original_index = index.read_bytes()
                with mock.patch.object(observations, "kernel_oom_evidence", return_value=None):
                    verdict = f.validate(result)
                self.assertFalse(verdict["handoff_ready"], verdict)
                self.assertEqual(verdict["local_outcome"], "interrupted")
                self.assertIn("owner-exit-failed", verdict["rejection_codes"])
                self.assertEqual(f.entry["closed_at"], f.entry["interruption"]["at"])
                self.assertEqual(f.entry["interruption"]["head"], result["result_sha"])
                self.assertEqual(git(f.worktree, "rev-parse", "HEAD"), result["result_sha"])
                self.assertEqual(index.read_bytes(), original_index)
                self.assertEqual(f.entry["checks"], checks)
                handoff.validate_state(f.state)
                metrics = handoff.summarize_handoffs(f.state)
                self.assertEqual((metrics["accepted"], metrics["interrupted"]), (0, 1))
                labelled = copy.deepcopy(f.state)
                entry = labelled["assignments"][0]
                entry["interruption"] = None
                entry["validation"].update(local_outcome="accepted", handoff_ready=True, rejection_codes=[])
                handoff.validate_state(labelled)
                with self.assertRaisesRegex(handoff.HandoffDataError, "owner completion"):
                    handoff.summarize_handoffs(labelled)

    def test_unknown_owner_exit_is_not_zero_or_terminal_completion(self):
        f = self.fixture
        process = f.owner_process()
        f.receive()
        result = f.deliver(f.commit())
        handoff.capture_check(f.entry, "raw", result["result_sha"])
        process.stdin.write(b"done\n")
        process.stdin.flush()
        process.wait(timeout=5)
        observed = observations.sample_process(f.entry["process"])
        self.assertEqual(observed["state"], "exited")
        self.assertIsNone(observed["exit_code"])
        handoff.record_process(f.entry, observed)
        verdict = f.validate(result)
        self.assertFalse(verdict["handoff_ready"])
        self.assertIn("owner-exit-unknown", verdict["rejection_codes"])
        self.assertIsNone(f.entry["closed_at"])
        self.assertEqual(git(f.worktree, "rev-parse", "HEAD"), result["result_sha"])
        handoff.validate_state(f.state)

    def test_multiple_task_commits_are_strict_descendants(self):
        f = self.fixture
        process = f.owner_process()
        f.receive()
        first = f.commit()
        second = f.commit("second\n", name="docs/second.txt")
        result = f.deliver(second)
        f.finish_owner(process)
        verdict = f.validate(result)
        self.assertTrue(verdict["handoff_ready"], verdict)
        self.assertEqual(verdict["task_commits"], [first, second])

    def test_normal_upstream_merge_does_not_demand_imported_trailers_or_scope(self):
        f = GitFixture(upstream=True)
        self.addCleanup(f.close)
        process = f.owner_process()
        f.receive()
        git(f.worktree, "merge", "--no-commit", "--no-ff", f.upstream)
        result = f.deliver(f.commit())
        f.finish_owner(process)
        verdict = f.validate(result)
        self.assertTrue(verdict["handoff_ready"], verdict)
        self.assertEqual(verdict["imported_paths"], ["docs/from-upstream.txt", "imported.txt"])
        self.assertEqual(verdict["changed_lines"], 1)
        self.assertNotIn(f.upstream, verdict["task_commits"])

    def test_unrelated_merge_input_rejects_even_when_result_paths_are_allowed(self):
        f = GitFixture(upstream=True)
        self.addCleanup(f.close)
        f.assignment["upstream_inputs"] = []
        process = f.owner_process()
        f.receive()
        git(f.worktree, "merge", "--no-commit", "--no-ff", f.upstream)
        result = f.deliver(f.commit())
        f.finish_owner(process)
        self.assertIn("unauthorized-upstream", f.validate(result)["rejection_codes"])

    def test_stale_echo_wrong_worktree_and_unrelated_sha_reject(self):
        f = self.fixture
        result = f.complete()
        for changed, code in (
            ({**result, "result_sha": f.parent}, "stale-result"),
            ({**result, "assigned_parent_sha": "f" * 40}, "wrong-parent"),
        ):
            with self.subTest(code=code):
                self.assertIn(code, f.validate(changed)["rejection_codes"])
        verdict = handoff.validate_handoff(f.state, result, worktree=f.repository)
        self.assertIn("wrong-worktree", verdict["rejection_codes"])
        tree = git(f.repository, "rev-parse", f.parent + "^{tree}")
        other = git(f.repository, "commit-tree", tree, input=b"unrelated root\n")
        self.assertIn("unrelated-branch",
                      f.validate({**result, "result_sha": other})["rejection_codes"])

    def test_dirty_conflicting_missing_trailers_scope_and_line_limits(self):
        f = self.fixture
        result = f.complete()
        (f.worktree / "docs/dirty.txt").write_text("uncommitted\n")
        self.assertIn("dirty-worktree", f.validate(result)["rejection_codes"])
        (f.worktree / "docs/dirty.txt").unlink()
        private = Path(f.entry["git_identity"]["git_dir"])
        (private / "MERGE_HEAD").write_text(f.parent + "\n")
        self.assertIn("conflicting-worktree", f.validate(result)["rejection_codes"])
        (private / "MERGE_HEAD").unlink()
        f.assignment["budgets"]["changed_lines"] = 0
        self.assertIn("changed-lines-budget-exceeded", f.validate(result)["rejection_codes"])
        f.assignment["budgets"]["changed_lines"] = 100
        f.assignment["allowed_scope"] = ["docs/other.txt"]
        self.assertIn("scope-violation", f.validate(result)["rejection_codes"])
        missing = GitFixture()
        self.addCleanup(missing.close)
        self.assertIn("missing-copilot-trailer", missing.validate(missing.complete(trailers=False))["rejection_codes"])

    def test_hidden_index_flags_and_binary_changes_are_not_clean_quantified_work(self):
        f = self.fixture
        result = f.complete()
        for flag in ("--assume-unchanged", "--skip-worktree"):
            with self.subTest(flag=flag):
                git(f.worktree, "update-index", flag, "docs/base.txt")
                (f.worktree / "docs/base.txt").write_text("hidden mutation\n")
                self.assertFalse(f.validate(result)["handoff_ready"])
                git(f.worktree, "update-index", flag.replace("--", "--no-", 1), "docs/base.txt")
                (f.worktree / "docs/base.txt").write_text("base\n")
        other = GitFixture()
        self.addCleanup(other.close)
        other.assignment["allowed_scope"].append(".gitattributes")
        (other.worktree / ".gitattributes").write_text("* diff\n")
        result = other.complete(content="binary\0bytes\n")
        self.assertIn("unquantified-diff", other.validate(result)["rejection_codes"])

    def test_candidate_cannot_supply_observed_passes_or_execute_checker_replacement(self):
        f = self.fixture
        f.assignment["allowed_scope"].append("scripts/workflow_pilot/")
        marker = f.home / "executed"
        checker = f.worktree / "scripts/workflow_pilot/raw_diff_check.py"
        checker.parent.mkdir(parents=True)
        checker.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('wrong')\n")
        result = f.complete(content="trailing space \n")
        verdict = f.validate(result)
        self.assertIn("required-check-failed", verdict["rejection_codes"])
        self.assertFalse(marker.exists())
        with self.assertRaises(handoff.HandoffDataError):
            handoff.parse_result(observations.json_bytes({**result, "passed": True}))

    def test_named_evidence_and_check_identity_fail_closed(self):
        f = self.fixture
        result = f.complete()
        self.assertTrue(f.validate(result)["handoff_ready"])
        for field, value, code in (
            ("result_sha", f.parent, "check-identity-mismatch"),
            ("exit_code", 2, "required-check-failed"),
            ("exit_code", None, "incomplete-check"),
        ):
            with self.subTest(field=field, value=value):
                saved = copy.deepcopy(f.entry["checks"])
                f.entry["checks"][0][field] = value
                self.assertIn(code, f.validate(result, run_checks=False)["rejection_codes"])
                f.entry["checks"] = saved
        self.assertIn("missing-evidence",
                      f.validate({**result, "evidence_refs": []}, run_checks=False)["rejection_codes"])

    def test_resources_are_unknown_unless_measured_and_overages_reject(self):
        f = self.fixture
        f.assignment["allowed_scope"].append("src/")
        result = f.complete(name="src/resource.c", content="int resource;\n")
        self.assertIn("missing-budget-measurement", f.validate(result)["rejection_codes"])
        f.assignment["required_checks"]["resource"] = {
            "contract": "coordinator-check", "evidence_id": "resource-evidence", "inputs": [],
        }
        result["evidence_refs"].append("resource-evidence")
        f.entry["result"] = result
        parent_map = f.home / "parent.map"
        candidate_map = f.home / "candidate.map"
        parent_text = (ROOT / "scripts/linker_report/tests/fixtures/basic.map").read_text()
        parent_map.write_text(parent_text)
        candidate_map.write_text(parent_text.replace("0x100000", "0x100008")
                                 .replace("0x2000", "0x2004").replace("0x03002000", "0x03002004"))
        def executor(assignment, revision):
            self.assertEqual(revision, result["result_sha"])
            captured = raw.run_process(
                ["/usr/bin/python3", "-I", str(ROOT / "scripts/linker_report/budget.py"),
                 "--map", str(candidate_map), "--output", str(f.home / "resource-report.json")],
                cwd=ROOT, env=raw.git_environment(),
            )
            return captured, observations.linker_growth(parent_map, candidate_map)
        handoff.capture_check(f.entry, "resource", result["result_sha"], executor)
        rejected = f.validate(result)
        self.assertIn("rom-bytes-budget-exceeded", rejected["rejection_codes"])
        self.assertIn("ram-bytes-budget-exceeded", rejected["rejection_codes"])
        f.assignment["budgets"].update(rom_bytes=8, ram_bytes=4)
        self.assertTrue(f.validate(result)["handoff_ready"])

    def test_zero_line_non_host_changes_still_need_actual_resource_measurements(self):
        for kind in ("mode", "symlink", "empty-add", "empty-delete"):
            with self.subTest(kind=kind):
                f = GitFixture(assign=False)
                self.addCleanup(f.close)
                source = f.worktree / "src/resource"
                source.parent.mkdir()
                source.write_text("target")
                empty = f.worktree / "src/empty"
                empty.write_bytes(b"")
                f.parent = f.commit_pending(trailers=False)
                f.assignment["assigned_parent_sha"] = f.parent
                f.assignment["allowed_scope"].append("src/")
                f.assignment["required_checks"]["resource"] = {
                    "contract": "coordinator-check", "evidence_id": "resource-evidence", "inputs": [],
                }
                f.entry = handoff.assign(f.state, f.assignment)
                process = f.owner_process()
                f.receive()
                if kind == "mode":
                    source.chmod(0o755)
                elif kind == "symlink":
                    source.unlink()
                    source.symlink_to("target")
                elif kind == "empty-add":
                    (f.worktree / "src/empty-new").write_bytes(b"")
                else:
                    empty.unlink()
                result = f.deliver(f.commit_pending())
                f.finish_owner(process)
                owned, total, _, imported = handoff.task_changes(f.assignment, result["result_sha"])
                self.assertEqual((len(owned), total, imported), (1, 0, []))
                parent_map, candidate_map = f.home / "parent.map", f.home / "candidate.map"
                original = (ROOT / "scripts/linker_report/tests/fixtures/basic.map").read_text()
                parent_map.write_text(original)
                candidate_map.write_text(original)
                measured = False
                def executor(assignment, revision):
                    capture = raw.run_process(
                        ["/usr/bin/python3", "-I", str(ROOT / "scripts/linker_report/budget.py"),
                         "--map", str(candidate_map), "--output", str(f.home / "resource-report.json")],
                        cwd=ROOT, env=raw.git_environment(),
                    )
                    return capture, (observations.linker_growth(parent_map, candidate_map)
                                     if measured else dict.fromkeys(handoff.METRICS))
                handoff.capture_check(f.entry, "resource", result["result_sha"], executor)
                with self.subTest(observation="missing", kind=kind):
                    missing = f.validate(result)
                    self.assertFalse(missing["handoff_ready"])
                    self.assertIn("missing-budget-measurement", missing["rejection_codes"])
                measured = True
                zero = handoff.capture_check(f.entry, "resource", result["result_sha"], executor)
                self.assertEqual((zero["measurements"]["rom_bytes"], zero["measurements"]["ram_bytes"]), (0, 0))
                self.assertGreater(zero["pid"], 0)
                self.assertTrue(f.validate(result)["handoff_ready"])
                candidate_map.write_text(original.replace("0x100000", "0x100008")
                                         .replace("0x2000", "0x2004").replace("0x03002000", "0x03002004"))
                handoff.capture_check(f.entry, "resource", result["result_sha"], executor)
                over = f.validate(result)
                self.assertIn("rom-bytes-budget-exceeded", over["rejection_codes"])
                self.assertIn("ram-bytes-budget-exceeded", over["rejection_codes"])
                f.assignment["budgets"].update(rom_bytes=8, ram_bytes=4)
                self.assertTrue(f.validate(result)["handoff_ready"])

    def test_zero_line_host_change_and_pure_authorized_import_need_no_resource_check(self):
        f = self.fixture
        host = f.validate(f.complete(content="", name="docs/empty.txt"))
        self.assertEqual(host["changed_lines"], 0)
        self.assertTrue(host["handoff_ready"])
        imported = GitFixture(upstream=True)
        self.addCleanup(imported.close)
        process = imported.owner_process()
        imported.receive()
        git(imported.worktree, "merge", "--no-commit", "--no-ff", imported.upstream)
        result = imported.deliver(imported.commit_pending())
        imported.finish_owner(process)
        paths, total, _, upstream_paths = handoff.task_changes(imported.assignment, result["result_sha"])
        self.assertEqual((paths, total), ([], 0))
        self.assertIn("imported.txt", upstream_paths)
        self.assertTrue(imported.validate(result)["handoff_ready"])
        self.assertEqual(set(imported.assignment["required_checks"]), {"raw"})

    def test_parsed_protocol_budget_ignores_spelling_order_but_not_behavior(self):
        f = self.fixture
        f.assignment["required_checks"]["protocol"] = {
            "contract": "protocol-json", "evidence_id": "protocol-evidence",
            "inputs": ["docs/interface.schema.json"],
        }
        result = f.complete(name="docs/interface.schema.json", content='{"type":"integer"}\n')
        self.assertIn("protocol-changes-budget-exceeded", f.validate(result)["rejection_codes"])
        f.assignment["budgets"]["protocol_changes"] = 1
        self.assertTrue(f.validate(result)["handoff_ready"])
        parent_json = {"one": 1, "two": 2}
        self.assertEqual(observations.parse_bytes(b'{"two":2, "one":1}'), parent_json)

    def test_retirement_lifetime_rss_and_fresh_successor(self):
        f = self.fixture
        result = f.complete()
        saved = copy.deepcopy(f.entry["process"])
        f.entry["process"]["rss_complete"] = False
        self.assertIn("owner-rss-unknown", f.validate(result)["rejection_codes"])
        f.entry["process"] = copy.deepcopy(saved)
        f.entry["process"]["peak_rss_bytes"] = f.assignment["max_peak_rss_bytes"] + 1
        self.assertIn("owner-rss-exceeded", f.validate(result)["rejection_codes"])
        f.entry["process"] = copy.deepcopy(saved)
        f.entry["process"]["age_ms"] = f.assignment["max_lifetime_seconds"] * 1000 + 1
        self.assertIn("owner-lifetime-exceeded", f.validate(result)["rejection_codes"])
        f.entry["process"] = saved
        self.assertTrue(f.validate(result)["handoff_ready"])
        successor = {**copy.deepcopy(f.assignment), "id": "review-two", "owner_id": "fresh-owner",
                     "session_id": "fresh-session", "dispatch_id": "fresh-dispatch", "kind": "review",
                     "predecessor_id": f.assignment["id"], "assigned_parent_sha": result["result_sha"]}
        with self.assertRaisesRegex(handoff.HandoffDataError, "session"):
            handoff.assign(f.state, {**successor, "session_id": SESSION})
        handoff.assign(f.state, successor)
        with self.assertRaises(handoff.HandoffDataError):
            handoff.assign(f.state, {**successor, "id": "review-three"})

    def test_duplicate_owners_and_recorded_remote_actions_reject(self):
        f = self.fixture
        with self.assertRaises(handoff.HandoffDataError):
            handoff.assign(f.state, {**f.assignment, "id": "duplicate", "owner_id": "other"})
        result = f.complete()
        for action in handoff.PROHIBITED_REMOTE_ACTIONS:
            with self.subTest(action=action):
                f.entry["remote_actions"] = [{"id": "remote-event", "action": action, "at": observations.utc_now()}]
                self.assertIn("implementation-owner-remote-action", f.validate(result)["rejection_codes"])

    def test_initial_root_cannot_restart_a_completed_issue_or_pr(self):
        f = self.fixture
        result = f.complete()
        self.assertTrue(f.validate(result)["handoff_ready"])
        fresh = {**copy.deepcopy(f.assignment), "id": "fresh", "owner_id": "fresh-owner",
                 "session_id": "fresh-session", "dispatch_id": "fresh-dispatch",
                 "assigned_parent_sha": result["result_sha"]}
        for issue, pr in ((178, 191), (178, None), (178, 192), (179, 191)):
            assignment = {**fresh, "issue": issue, "pull_request": pr}
            with self.subTest(source="reservation", issue=issue, pr=pr):
                candidate = copy.deepcopy(f.state)
                before = observations.json_bytes(candidate)
                with self.assertRaisesRegex(handoff.HandoffDataError, "initial"):
                    handoff.assign(candidate, assignment)
                self.assertEqual(observations.json_bytes(candidate), before)
            with self.subTest(source="loaded", issue=issue, pr=pr):
                standalone = {**f.state, "assignments": []}
                entry = handoff.assign(standalone, assignment)
                path = f.home / "duplicate-root.json"
                write_json(path, {**f.state, "assignments": [*f.state["assignments"], entry]})
                with self.assertRaisesRegex(handoff.HandoffDataError, "initial"):
                    handoff.validate_state(observations.load_json(path))
        independent = handoff.assign(f.state, {**fresh, "issue": 179, "pull_request": None})
        self.assertEqual(independent["assignment"]["kind"], "initial")
        other = f.home / "independent"
        git(f.repository, "worktree", "add", "-b", "agent/independent", str(other), f.parent)
        handoff.assign(f.state, {
            **fresh, "id": "independent", "owner_id": "independent-owner",
            "session_id": "independent-session", "dispatch_id": "independent-dispatch",
            "issue": 180, "pull_request": None, "allowed_worktree": str(other),
            "expected_branch": "agent/independent", "assigned_parent_sha": f.parent,
        })
        self.assertEqual(len(handoff.validate_state(f.state)["assignments"]), 3)

    def test_available_plan_and_unavailable_suspend_or_disconnect(self):
        f = self.fixture
        f.state["availability"]["autostop_enabled"] = True
        self.assertEqual(handoff.availability_errors(f.state, observations.utc_now()), ["coordinator-unavailable"])
        f.state["availability"].update(mode="plan", plan="An always-on coordinator takes over before disconnect.")
        self.assertEqual(handoff.availability_errors(f.state, observations.utc_now()), [])
        f.state["availability"]["valid_until"] = at_offset(-1)
        self.assertTrue(handoff.availability_errors(f.state, observations.utc_now()))
        f.state["availability"]["valid_until"] = at_offset(3600)
        old = f.state["clock"]
        with mock.patch.object(observations, "clock_observation",
                               return_value={**old, "boottime_ns": old["boottime_ns"] + 6_000_000_000}):
            self.assertTrue(handoff.availability_errors(f.state, observations.utc_now()))


class HandoffSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((ROOT / "scripts/workflow_pilot/agent_handoff.schema.json").read_text())
        Draft202012Validator.check_schema(cls.schema)
        cls.validator = Draft202012Validator(cls.schema, format_checker=FormatChecker())

    def setUp(self):
        self.fixture = GitFixture()
        self.addCleanup(self.fixture.close)

    def reject_both(self, document, validator):
        with self.assertRaises(ValidationError):
            self.validator.validate(document)
        with self.assertRaises(handoff.HandoffDataError):
            validator(observations.parse_bytes(observations.json_bytes(document)))

    def test_real_generated_assignment_result_state_and_verdict(self):
        f = self.fixture
        result = f.complete()
        verdict = f.validate(result)
        for value, parser in ((f.assignment, handoff.validate_assignment), (result, handoff.validate_result),
                              (f.state, handoff.validate_state), (verdict, handoff.validate_verdict)):
            with self.subTest(kind=parser.__name__):
                self.validator.validate(value)
                parser(copy.deepcopy(value))
                for key in value:
                    malformed = copy.deepcopy(value)
                    del malformed[key]
                    self.reject_both(malformed, parser)
                self.reject_both({**value, "extra": 1}, parser)

    def test_assignment_uniqueness_bounds_enums_and_closed_nested_objects(self):
        base = self.fixture.assignment
        changes = [
            ("schema_version", 2), ("issue", True), ("owner_id", ""),
            ("owner_id", "owner\n"), ("allowed_worktree", "/"),
            ("assigned_parent_sha", "a" * 39), ("allowed_scope", ["docs/", "docs/"]),
            ("allowed_scope", ["../"]), ("allowed_scope", ["docs/"] * 257),
            ("upstream_inputs", ["a" * 40, "a" * 40]),
            ("finding_ids", ["one", "one"]), ("max_lifetime_seconds", 86401),
            ("max_peak_rss_bytes", 0), ("prohibited_remote_actions", ["push"] * 8),
        ]
        for name, value in changes:
            with self.subTest(name=name, value=value):
                changed = copy.deepcopy(base)
                changed[name] = value
                self.reject_both(changed, handoff.validate_assignment)
        for location in ("budgets", "acceptance_criteria", "required_checks"):
            changed = copy.deepcopy(base)
            if location == "budgets":
                changed[location]["rom_bytes"] = -1
            else:
                key = next(iter(changed[location]))
                changed[location][key]["unknown"] = 0
            self.reject_both(changed, handoff.validate_assignment)

    def test_bounded_bytes_precede_decode_or_copy_and_nofollow_files(self):
        with mock.patch.object(observations.json, "loads", side_effect=AssertionError("must not parse")):
            with self.assertRaisesRegex(handoff.HandoffDataError, "1 MiB"):
                handoff.parse_assignment(b" " * (observations.MAX_JSON_BYTES + 1))
        for raw_input in ({"cycle": None}, b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}',
                          b"[" * 1000 + b"]" * 1000, b'{"x":"' + b"x" * 16385 + b'"}'):
            with self.subTest(kind=type(raw_input)):
                with self.assertRaises(handoff.HandoffDataError):
                    observations.parse_bytes(raw_input)
        file = self.fixture.home / "input.json"
        file.symlink_to(self.fixture.worktree / "docs/base.txt")
        with self.assertRaises(handoff.HandoffDataError):
            observations.load_json(file)
        file.unlink()
        os.mkfifo(file)
        with self.assertRaises(handoff.HandoffDataError):
            observations.load_json(file)

    def test_integral_wire_numbers_have_schema_api_and_os_type_parity(self):
        f = self.fixture
        raw_assignment = observations.json_bytes(f.assignment)
        for token in (b"178.0", b"17.8e1", b"178e0"):
            with self.subTest(token=token):
                wire = raw_assignment.replace(b'"issue":178', b'"issue":' + token)
                self.validator.validate(json.loads(wire))
                value = handoff.parse_assignment(wire)
                self.assertEqual(value["issue"], 178)
                self.assertIs(type(value["issue"]), int)
                self.validator.validate(value)
        for token in (b"true", b"178.5", b"1.785e2", b"NaN", b"Infinity",
                      b"1e309", b"9223372036854775808.0"):
            with self.subTest(token=token):
                wire = raw_assignment.replace(b'"issue":178', b'"issue":' + token)
                self.assertFalse(self.validator.is_valid(json.loads(wire)))
                with self.assertRaises(handoff.HandoffDataError):
                    handoff.parse_assignment(wire)
        with self.subTest(maximum_integral=True):
            maximum = handoff.parse_assignment(raw_assignment.replace(
                b'"issue":178', b'"issue":9223372036854775807.0'))
            self.assertEqual(maximum["issue"], 2**63 - 1)
            self.validator.validate(maximum)
        for token in (b"178.00000000000000001", b"1e-99999"):
            with self.subTest(exact_fraction=token):
                wire = raw_assignment.replace(b'"rom_bytes":0', b'"rom_bytes":' + token)
                with self.assertRaises(handoff.HandoffDataError):
                    handoff.parse_assignment(wire)
        for token in (b"0.0", b"-0e9", b"0e999999999999999999999999999"):
            with self.subTest(zero=token):
                wire = raw_assignment.replace(b'"rom_bytes":0', b'"rom_bytes":' + token)
                self.validator.validate(json.loads(wire))
                value = handoff.parse_assignment(wire)["budgets"]["rom_bytes"]
                self.assertEqual(value, 0)
                self.assertIs(type(value), int)
        process = f.owner_process()
        wire = observations.json_bytes(f.state).replace(
            f'"pid":{process.pid}'.encode(), f'"pid":{process.pid}.0'.encode())
        self.validator.validate(json.loads(wire))
        loaded = handoff.validate_state(observations.parse_bytes(wire))
        identity = loaded["assignments"][0]["process"]
        self.assertIs(type(identity["pid"]), int)
        sampled = observations.sample_process(identity)
        self.assertEqual((sampled["pid"], sampled["state"]), (process.pid, "running"))
        self.assertIsNone(process.poll())

    def test_retained_integrity_record_has_independent_schema_runtime_checks(self):
        f = self.fixture
        process = f.owner_process()
        (f.worktree / "docs/recovery").write_text("preserved\n")
        f.finish_owner(process)
        handoff.preserve_interruption(f.entry, "process-exit")
        self.validator.validate(f.state)
        handoff.validate_state(f.state)
        for value in (None, "f" * 63, "F" * 64, "f" * 65):
            with self.subTest(digest=value):
                changed = copy.deepcopy(f.state)
                changed["assignments"][0]["interruption"]["retained_data_sha256"] = value
                self.reject_both(changed, handoff.validate_state)
        changed = copy.deepcopy(f.state)
        del changed["assignments"][0]["interruption"]["retained_data_sha256"]
        self.reject_both(changed, handoff.validate_state)

    def test_integral_wire_numbers_work_through_the_real_cli(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        state_path, assignment_path = f.home / "state.json", f.home / "assignment.json"
        write_json(state_path, f.state)
        raw_assignment = observations.json_bytes(f.assignment)
        wire = raw_assignment.replace(b'"issue":178', b'"issue":17.8e1').replace(
            b'"schema_version":3', b'"schema_version":3.0')
        self.validator.validate(json.loads(wire))
        assignment_path.write_bytes(wire)
        command = [str(ROOT / "build/host-python/bin/python3"), "-I",
                   str(ROOT / "scripts/workflow_pilot/isolated_launcher.py"), "agent-handoff"]
        assigned = raw.run_process(
            [*command, "assign", "--state", str(state_path), "--assignment", str(assignment_path)],
            cwd=ROOT, env=raw.git_environment(),
        )
        self.assertEqual(assigned.returncode, 0, assigned.stderr.decode())
        loaded = observations.load_json(state_path)
        self.assertIs(type(loaded["assignments"][0]["assignment"]["issue"]), int)
        process = f.waiting_process()
        observe = [*command, "observe", "--state", str(state_path),
                   "--assignment-id", f.assignment["id"], "--pid", str(process.pid)]
        self.assertEqual(raw.run_process(observe, cwd=ROOT, env=raw.git_environment()).returncode, 0)
        wire = state_path.read_bytes().replace(
            f'"pid":{process.pid}'.encode(), f'"pid":{process.pid}e0'.encode())
        self.validator.validate(json.loads(wire))
        state_path.write_bytes(wire)
        sampled = raw.run_process(observe, cwd=ROOT, env=raw.git_environment())
        self.assertEqual(sampled.returncode, 0, sampled.stderr.decode())
        self.assertIs(type(observations.load_json(state_path)["assignments"][0]["process"]["pid"]), int)
        for token in (b"true", b"178.5", b"NaN", b"1e309", b"9223372036854775808.0"):
            with self.subTest(token=token):
                assignment_path.write_bytes(raw_assignment.replace(b'"issue":178', b'"issue":' + token))
                before = state_path.read_bytes()
                rejected = raw.run_process(
                    [*command, "assign", "--state", str(state_path), "--assignment", str(assignment_path)],
                    cwd=ROOT, env=raw.git_environment(),
                )
                self.assertEqual(rejected.returncode, 2)
                self.assertEqual(state_path.read_bytes(), before)
        self.assertIsNone(process.poll())

    def test_timestamps_actual_calendar_and_fractional_lifetime(self):
        state = self.fixture.state
        for invalid in ("2025-02-29T00:00:00Z", "2026-09-06T00:00:00+00:00",
                        "2026-09-06T00:00:00.1234567Z"):
            changed = copy.deepcopy(state)
            changed["availability"]["observed_at"] = invalid
            self.reject_both(changed, handoff.validate_state)
        for valid in ("2024-02-29T00:00:00Z", "2026-09-06T00:00:00.123456Z"):
            changed = copy.deepcopy(state)
            changed["availability"]["observed_at"] = valid
            self.validator.validate(changed)
            handoff.validate_state(changed)
        for name in ("docs/file\nname", "docs/file\n", "docs/./name", "docs/../name", "docs//name"):
            document = copy.deepcopy(self.fixture.assignment)
            document["allowed_scope"] = [name]
            expected = name in {"docs/file\nname", "docs/file\n"}
            self.assertEqual(self.validator.is_valid(document), expected)
            if expected:
                handoff.validate_assignment(document)
            else:
                with self.assertRaises(handoff.HandoffDataError):
                    handoff.validate_assignment(document)

    def test_active_and_completed_watcher_process_schema_runtime_parity(self):
        f = self.fixture
        process = f.waiting_process()
        watcher = handoff.reserve_watcher(f.state, "watch", 91, 1, f.parent, process.pid)
        self.validator.validate(f.state)
        handoff.validate_state(f.state)
        for mutation in ("process-exited", "process-exit-code", "ended-running"):
            with self.subTest(mutation=mutation):
                candidate = copy.deepcopy(f.state)
                changed = candidate["watchers"][0]
                if mutation == "process-exited":
                    changed["process"]["state"] = "exited"
                elif mutation == "process-exit-code":
                    changed["process"]["exit_code"] = 0
                else:
                    changed["ended_at"] = observations.utc_now()
                self.reject_both(candidate, handoff.validate_state)
        process.stdin.write(b"done\n")
        process.stdin.flush()
        os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT)
        zombie = observations.sample_process(watcher["process"])
        self.assertEqual(zombie["state"], "exited")
        candidate = copy.deepcopy(f.state)
        candidate["watchers"][0]["process"] = zombie
        self.reject_both(candidate, handoff.validate_state)
        handoff.finish_watcher(f.state, watcher["id"])
        self.assertIsNone(watcher["exit_code"])
        self.assertFalse(watcher["process"]["rss_complete"])
        self.validator.validate(f.state)
        handoff.validate_state(f.state)
        owned = observations.observe_owned_exit(process, watcher["process"])
        self.assertTrue(owned["rss_complete"])
        self.assertEqual(owned["exit_code"], 0)


class RawDiffBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GitFixture()
        self.addCleanup(self.fixture.close)

    def test_whitespace_and_local_attributes_cannot_forge_success(self):
        f = self.fixture
        revision = f.commit("bad \n")
        git(f.worktree, "config", "core.whitespace", "-trailing-space")
        self.assertTrue(raw.raw_diff_errors(f.worktree, f.parent, revision))
        info = Path(f.entry["git_identity"]["common_dir"]) / "info/attributes"
        info.write_text("* -whitespace\n")
        with self.assertRaisesRegex(ValueError, "attributes"):
            raw.exact_repository_root(str(f.worktree))
        info.unlink()
        os.mkfifo(info)
        with self.assertRaisesRegex(ValueError, "attributes"):
            raw.exact_repository_root(str(f.worktree))

    def test_oversized_blob_rejects_before_materialization(self):
        f = self.fixture
        for content in ("x" * (raw.MAX_BYTES + 1), "\0" + "x" * raw.MAX_BYTES):
            with self.subTest(binary=content.startswith("\0")):
                revision = f.commit(content)
                with self.assertRaisesRegex(ValueError, "4 MiB"):
                    raw.raw_diff_errors(f.worktree, f.parent, revision)

    def test_raw_lf_crlf_empty_and_unchanged_eof_controls(self):
        f = self.fixture
        for content, fails in (("clean\n", False), ("\n", True), ("CRLF\r\n", True),
                               (" \tindent\n", True), ("new EOF blank\n\n", True), ("", False)):
            with self.subTest(content=content):
                revision = f.commit(content)
                self.assertEqual(bool(raw.raw_diff_errors(f.worktree, f.parent, revision)), fails)
        before = f.commit("old text\n\n")
        after = f.commit("changed text\n\n")
        self.assertEqual(raw.raw_diff_errors(f.worktree, before, after), [])

    def test_tracked_attributes_and_execution_config_do_not_override_checker(self):
        f = self.fixture
        marker = f.home / "executed"
        (f.worktree / ".gitattributes").write_text("* -whitespace diff=opaque\n")
        revision = f.commit("bad \n")
        git(f.worktree, "config", "diff.opaque.textconv", f"/usr/bin/touch {marker}")
        git(f.worktree, "config", "diff.external", f"/usr/bin/touch {marker}")
        git(f.worktree, "config", "core.fsmonitor", f"/usr/bin/touch {marker}")
        self.assertTrue(raw.raw_diff_errors(f.worktree, f.parent, revision))
        handoff.observe_git(f.assignment)
        self.assertFalse(marker.exists())

    def test_oversized_gitdir_and_symlinked_metadata_parent_reject_before_dispatch(self):
        f = self.fixture
        entry = f.worktree / ".git"
        original = entry.read_bytes()
        entry.write_bytes(b"gitdir: " + b"x" * raw.MAX_METADATA_BYTES)
        with mock.patch.object(raw, "run_git", side_effect=AssertionError("must not dispatch")):
            with self.assertRaisesRegex(ValueError, "4096"):
                raw.exact_repository_root(str(f.worktree))
        entry.write_bytes(original)
        private = Path(f.entry["git_identity"]["git_dir"])
        (private / "info").symlink_to(Path(f.entry["git_identity"]["common_dir"]) / "info",
                                      target_is_directory=True)
        with mock.patch.object(raw, "run_git", side_effect=AssertionError("must not dispatch")):
            with self.assertRaises(OSError):
                raw.exact_repository_root(str(f.worktree))

    def test_git_private_and_shared_metadata_are_nofollow(self):
        f = self.fixture
        common = Path(f.entry["git_identity"]["common_dir"])
        file = common / "info/grafts"
        file.symlink_to(f.worktree / "docs/base.txt")
        with mock.patch.object(raw, "run_git", side_effect=AssertionError("must not dispatch Git")):
            with self.assertRaises(ValueError):
                raw.exact_repository_root(str(f.worktree))

    def test_symlinked_index_is_rejected_before_git_index_consumers(self):
        f = self.fixture
        index = Path(f.entry["git_identity"]["git_dir"]) / "index"
        saved = f.home / "saved-index"
        saved.write_bytes(index.read_bytes())
        index.unlink()
        index.symlink_to(saved)
        with mock.patch.object(handoff, "_git", wraps=handoff._git) as observe:
            with self.assertRaisesRegex(handoff.HandoffDataError, "index.*nofollow"):
                handoff.observe_git(f.assignment)
        self.assertFalse(any(call.args[1] in {"ls-files", "status"} for call in observe.call_args_list))
        self.assertTrue(index.is_symlink())


class OptionalReporterTests(unittest.TestCase):
    def test_optional_metrics_preserve_baseline_and_unknowns(self):
        f = GitFixture()
        self.addCleanup(f.close)
        result = f.complete()
        self.assertTrue(f.validate(result)["handoff_ready"])
        f.state["clock"]["at"] = observations.utc_now()
        baseline = {"schema_version": 1, "snapshot": {"repository": f.state["repository"]},
                    "unchanged": ["baseline"]}
        before = copy.deepcopy(baseline)
        envelope = reporter.with_handoff_metrics(baseline, observations.json_bytes(f.state))
        self.assertEqual(baseline, before)
        self.assertEqual(envelope["baseline"], before)
        with self.assertRaisesRegex(reporter.PilotDataError, "baseline repository"):
            reporter.with_handoff_metrics({**baseline, "snapshot": {"repository": "different/repository"}},
                                          observations.json_bytes(f.state))
        metrics = envelope["implementation_handoffs"]
        self.assertEqual((metrics["accepted"], metrics["coordination_turns"], metrics["unknown_rss_records"]),
                         (1, 1, 0))
        f.entry["process"] = None
        f.entry["validation"] = None
        f.entry["closed_at"] = None
        unknown = handoff.summarize_handoffs(f.state)
        self.assertIsNone(unknown["max_peak_rss_bytes"])
        self.assertEqual(unknown["unknown_rss_records"], 1)

    def test_stale_and_incomplete_outcomes_are_not_relabelled_accepted(self):
        f = GitFixture()
        self.addCleanup(f.close)
        result = f.complete()
        stale = {**result, "result_sha": f.parent}
        f.validate(stale)
        metrics = handoff.summarize_handoffs(f.state)
        self.assertEqual((metrics["rejected"], metrics["stale_responses"]), (1, 1))
        forged = copy.deepcopy(f.state)
        forged["assignments"][0]["validation"]["handoff_ready"] = True
        baseline = {"schema_version": 1, "snapshot": {"repository": f.state["repository"]}}
        with self.assertRaises(reporter.PilotDataError):
            reporter.with_handoff_metrics(baseline, observations.json_bytes(forged))
        forged["assignments"][0]["validation"].update(local_outcome="accepted", rejection_codes=[])
        with self.assertRaises(reporter.PilotDataError):
            reporter.with_handoff_metrics(baseline, observations.json_bytes(forged))


if __name__ == "__main__":
    unittest.main()
