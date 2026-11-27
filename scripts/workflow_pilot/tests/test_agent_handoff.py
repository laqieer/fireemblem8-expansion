import copy
import io
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


def gh_stub_environment(root, response):
    bin_dir = Path(root) / "fake-bin"
    bin_dir.mkdir(exist_ok=True)
    executable = bin_dir / "gh"
    executable.write_text(
        "#!/bin/sh\nprintf '%s' '" + json.dumps(response) + "'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    environment = raw.git_environment()
    environment["PATH"] = str(bin_dir) + os.pathsep + environment["PATH"]
    return environment


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
        self.open_prs_patch = mock.patch.object(
            handoff, "list_open_pull_requests",
            return_value=[{
                "number": 191, "title": "Fix issue #178", "body": "Closes #178",
                "headRefName": "agent/test", "closingIssuesReferences": [],
            }],
        )
        self.open_prs_patch.start()
        self.processes = []
        self.entry = handoff.assign(self.state, self.assignment) if assign else None
        self.parent_log, self.owner_log = self.home / "parent.jsonl", self.home / "owner.jsonl"

    def close(self):
        self.open_prs_patch.stop()
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

    def capture_resources(self, revision, check_id="resource"):
        parent_map, candidate_map = self.home / "parent.map", self.home / "candidate.map"
        original = (ROOT / "scripts/linker_report/tests/fixtures/basic.map").read_text()
        parent_map.write_text(original)
        candidate_map.write_text(original.replace("0x100000", "0x100008")
                                 .replace("0x2000", "0x2004").replace("0x03002000", "0x03002004"))
        def executor(assignment, result_sha):
            capture = raw.run_process(
                ["/usr/bin/python3", "-I", str(ROOT / "scripts/linker_report/budget.py"),
                 "--map", str(candidate_map), "--output", str(self.home / "resource-report.json")],
                cwd=ROOT, env=raw.git_environment(),
            )
            return capture, observations.linker_growth(parent_map, candidate_map)
        return handoff.capture_check(self.entry, check_id, revision, executor)


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

    def test_protocol_presence_and_typed_values_not_source_spelling_determine_changes(self):
        for before, after, changed in (
            (None, "null\n", 1), ("null\n", None, 1), (None, None, 0), ("null\n", "null\n", 0),
            (None, '{"type":"integer"}\n', 1), ('{"type":"integer"}\n', None, 1),
            ('{"a":1,"b":2}\n', '{\n  "b":2,\n  "a":1e0\n}\n', 0),
            ('{"value":"\\ud83d\\ude80"}\n', '{"value":"🚀"}\n', 0),
            ('{"flag":true}\n', '{"flag":1}\n', 1),
            ("false\n", "0.0\n", 1), ("[1,2]\n", "[2,1]\n", 1),
            ('{"type":"integer"}\n', '{"type":"string"}\n', 1),
        ):
            with self.subTest(before=before, after=after):
                f = GitFixture(assign=False)
                self.addCleanup(f.close)
                file = f.worktree / "docs/input.schema.json"
                if before is not None:
                    file.write_text(before)
                    f.parent = f.commit_pending(trailers=False)
                    f.assignment["assigned_parent_sha"] = f.parent
                f.assignment["required_checks"]["protocol"] = {
                    "contract": "protocol-json", "evidence_id": "protocol", "inputs": ["docs/input.schema.json"],
                }
                f.entry = handoff.assign(f.state, f.assignment)
                process = f.owner_process()
                f.receive()
                if after is None:
                    file.unlink(missing_ok=True)
                else:
                    file.write_text(after)
                result = f.deliver(f.commit())
                f.finish_owner(process)
                verdict = f.validate(result)
                check = next(item for item in f.entry["checks"] if item["id"] == "protocol")
                self.assertEqual(check["measurements"]["protocol_changes"], changed)
                self.assertEqual(verdict["handoff_ready"], changed == 0, verdict)
                if changed:
                    self.assertIn("protocol-changes-budget-exceeded", verdict["rejection_codes"])
                    f.assignment["budgets"]["protocol_changes"] = changed
                    self.assertTrue(f.validate(result)["handoff_ready"])
                self.assertEqual(handoff.summarize_handoffs(f.state)["accepted"], 1)

    def test_disjoint_protocol_checks_aggregate_independently_of_partition_and_order(self):
        names = ["docs/one.schema.json", "docs/two.schema.json", "docs/three.schema.json"]
        for groups in ((names,), (names[:1], names[1:]), (names[2:], names[1:2], names[:1])):
            with self.subTest(groups=groups):
                f = GitFixture()
                self.addCleanup(f.close)
                f.assignment["required_checks"].update({
                    f"protocol-{index}": {"contract": "protocol-json", "evidence_id": f"protocol-{index}",
                                         "inputs": group} for index, group in enumerate(groups)
                })
                for name in names:
                    (f.worktree / name).write_text('{"type":"integer"}\n')
                result = f.complete()
                f.assignment["budgets"]["protocol_changes"] = 2
                verdict = f.validate(result)
                self.assertIn("protocol-changes-budget-exceeded", verdict["rejection_codes"])
                self.assertEqual(sum(check["measurements"]["protocol_changes"] for check in f.entry["checks"]
                                     if check["contract"] == "protocol-json"), 3)
                f.assignment["budgets"]["protocol_changes"] = 3
                self.assertTrue(f.validate(result)["handoff_ready"])
                self.assertEqual(handoff.summarize_handoffs(f.state)["accepted"], 1)

    def test_valid_unicode_protocol_uses_input_bounds_not_reencoded_escape_size(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        payload = {"wide": ["🚀" * 10000] * 20, "number": 1}
        file = f.worktree / "docs/unicode.schema.json"
        before = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        self.assertLess(len(before.encode("utf-8")), observations.MAX_JSON_BYTES)
        self.assertGreater(len(json.dumps(payload)), observations.MAX_JSON_BYTES)
        file.write_text(before)
        f.parent = f.commit_pending(trailers=False)
        f.assignment["assigned_parent_sha"] = f.parent
        f.assignment["required_checks"]["protocol"] = {
            "contract": "protocol-json", "evidence_id": "protocol", "inputs": ["docs/unicode.schema.json"],
        }
        f.entry = handoff.assign(f.state, f.assignment)
        file.write_text(json.dumps({"number": 1.0, "wide": payload["wide"]}, ensure_ascii=False))
        result = f.complete()
        verdict = f.validate(result)
        self.assertTrue(verdict["handoff_ready"], verdict)
        check = next(item for item in f.entry["checks"] if item["id"] == "protocol")
        self.assertEqual(check["measurements"]["protocol_changes"], 0)

    def test_protocol_invalid_documents_and_nonregular_inputs_stay_unobserved(self):
        for content in ("", "{", '{"a":1,"a":2}\n', '"\\ud800"\n', None):
            with self.subTest(content=content):
                f = GitFixture()
                self.addCleanup(f.close)
                f.assignment["required_checks"]["protocol"] = {
                    "contract": "protocol-json", "evidence_id": "protocol", "inputs": ["docs/input.schema.json"],
                }
                file = f.worktree / "docs/input.schema.json"
                if content is None:
                    file.symlink_to("base.txt")
                else:
                    file.write_text(content)
                result = f.complete()
                verdict = f.validate(result)
                self.assertIn("git-or-check-observation-failed", verdict["rejection_codes"])
                self.assertFalse(verdict["handoff_ready"])
                self.assertFalse(any(check["id"] == "protocol" for check in f.entry["checks"]))

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

    def test_lifetime_crossing_during_real_checks_is_rechecked_at_retirement(self):
        f = self.fixture
        result = f.complete()
        f.assignment["max_lifetime_seconds"] = 30
        offset = 0
        def clock():
            return (observations.timestamp(observations.utc_now()) + timedelta(seconds=offset)).isoformat().replace(
                "+00:00", "Z")
        capture = handoff.capture_check
        def finish_check(*args, **kwargs):
            nonlocal offset
            check = capture(*args, **kwargs)
            self.assertEqual(check["exit_code"], 0)
            self.assertGreater(check["pid"], 0)
            offset = 31
            return check
        with mock.patch.object(handoff, "now", side_effect=clock), \
                mock.patch.object(handoff, "capture_check", side_effect=finish_check):
            verdict = f.validate(result)
            self.assertFalse(verdict["handoff_ready"], verdict)
            self.assertIn("owner-lifetime-exceeded", verdict["rejection_codes"])
            self.assertEqual(f.entry["closed_at"], verdict["observed_at"])
            self.assertEqual(handoff.summarize_handoffs(f.state)["rejected"], 1)

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
                with mock.patch.object(handoff, "preflight_issue_pr", return_value={}):
                    with self.assertRaisesRegex(handoff.HandoffDataError, "initial"):
                        handoff.assign(candidate, assignment)
                self.assertEqual(observations.json_bytes(candidate), before)
            with self.subTest(source="loaded", issue=issue, pr=pr):
                standalone = {**f.state, "assignments": []}
                with mock.patch.object(handoff, "preflight_issue_pr", return_value={}):
                    entry = handoff.assign(standalone, assignment)
                path = f.home / "duplicate-root.json"
                write_json(path, {**f.state, "assignments": [*f.state["assignments"], entry]})
                with self.assertRaisesRegex(handoff.HandoffDataError, "initial"):
                    handoff.validate_state(observations.load_json(path))
        with mock.patch.object(handoff, "preflight_issue_pr", return_value={}):
            independent = handoff.assign(f.state, {**fresh, "issue": 179, "pull_request": None})
        self.assertEqual(independent["assignment"]["kind"], "initial")
        other = f.home / "independent"
        git(f.repository, "worktree", "add", "-b", "agent/independent", str(other), f.parent)
        with mock.patch.object(handoff, "preflight_issue_pr", return_value={}):
            handoff.assign(f.state, {
                **fresh, "id": "independent", "owner_id": "independent-owner",
                "session_id": "independent-session", "dispatch_id": "independent-dispatch",
                "issue": 180, "pull_request": None, "allowed_worktree": str(other),
                "expected_branch": "agent/independent", "assigned_parent_sha": f.parent,
            })
        self.assertEqual(len(handoff.validate_state(f.state)["assignments"]), 3)

    def test_successor_binds_published_pr_to_new_root_lineage(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        f.assignment["pull_request"] = None
        with mock.patch.object(handoff, "list_open_pull_requests", return_value=[]):
            f.entry = handoff.assign(f.state, f.assignment)
        result = f.complete()
        self.assertTrue(f.validate(result)["handoff_ready"])

        successor = {
            **f.assignment,
            "id": "assignment-two",
            "owner_id": "owner-two",
            "session_id": "session-two",
            "dispatch_id": "dispatch-two",
            "pull_request": 192,
            "assigned_parent_sha": result["result_sha"],
            "predecessor_id": f.assignment["id"],
            "kind": "review",
        }
        published_pr = {
            "number": 192,
            "title": "Fix issue #178",
            "body": "",
            "headRefName": "agent/test",
            "closingIssuesReferences": [],
        }
        with mock.patch.object(handoff, "list_open_pull_requests", return_value=[published_pr]):
            entry = handoff.assign(f.state, successor)

        self.assertEqual(entry["assignment"]["pull_request"], 192)
        self.assertEqual(len(handoff.validate_state(f.state)["assignments"]), 2)

    def test_issue_pr_preflight_uses_exact_implementation_references(self):
        def pull_request(*, number=200, title="Unrelated", body="", branch="feature/other",
                         closing=()):
            return {
                "number": number, "title": title, "body": body, "headRefName": branch,
                "closingIssuesReferences": list(closing),
            }

        cases = (
            (pull_request(title="Fix #178"), "title"),
            (pull_request(title="Fix owner/repository#178"), "title"),
            (pull_request(branch="fix/issue-178"), "branch"),
            (pull_request(body="Refs #178"), "body"),
            (pull_request(body="Fix #178"), "body"),
            (pull_request(body="Fixes owner/repository#178"), "body"),
            (pull_request(body="Fix https://github.com/owner/repository/issues/178"), "body"),
            (pull_request(body="Refs #177, #178"), "body"),
            (pull_request(
                body="Implements [issue #178](https://github.com/owner/repository/issues/178)"),
                "body"),
            (pull_request(
                body="Implements [owner/repository#178](https://github.com/owner/repository/issues/178)"),
                "body"),
            (pull_request(
                body="Implements [Issue #178](https://github.com/OWNER/REPOSITORY/ISSUES/178)"),
                "body"),
            (pull_request(body="Address #178"), "body"),
            (pull_request(body="Addresses owner/repository#178"), "body"),
            (pull_request(closing=({
                "number": 178,
                "repository": {"owner": {"login": "owner"}, "name": "repository"},
            },)), "closing-issue"),
        )
        for candidate, source in cases:
            with self.subTest(source=source):
                result = handoff.implementation_pull_request_matches(
                    "owner/repository", 178, candidate)
                self.assertIn(source, result["sources"])

        unrelated = pull_request(
            title="Fix #1178", branch="fix/1178",
            body="Part of #178; depends on #178",
        )
        self.assertIsNone(handoff.implementation_pull_request_matches(
            "owner/repository", 178, unrelated))
        for candidate in (
            pull_request(title="Fix other/repository#178"),
            pull_request(title="Fix other/repository-#178"),
            pull_request(title="Fix other/repository.#178"),
            pull_request(title="Fix other/repository_#178"),
            pull_request(title="Fix another/owner/repository#178"),
            pull_request(branch="release/1.178.0"),
            pull_request(
                body="Implements [issue #178](https://github.com/other/project/issues/178)"),
            pull_request(
                body="Implements [issue #178](https://github.com/owner/repository/issues/179)"),
            pull_request(
                body="Part of [issue #178](https://github.com/owner/repository/issues/178)"),
            pull_request(
                body="Depends on [issue #178](https://github.com/owner/repository/issues/178)"),
            pull_request(body="Refs #177; Depends on #178"),
            pull_request(body="Refs #177, Depends on #178"),
        ):
            with self.subTest(candidate=candidate):
                self.assertIsNone(handoff.implementation_pull_request_matches(
                    "owner/repository", 178, candidate))
        for body in (
            "Refs #1780",
            "Refs owner/repository#1780",
            "Refs https://github.com/owner/repository/issues/1780",
        ):
            with self.subTest(body=body):
                self.assertIsNone(handoff.implementation_pull_request_matches(
                    "owner/repository", 178, pull_request(body=body)))
        cross_repository = pull_request(closing=({
            "number": 178,
            "repository": {"owner": {"login": "other"}, "name": "repository"},
        },))
        self.assertIsNone(handoff.implementation_pull_request_matches(
            "owner/repository", 178, cross_repository))

    def test_preflight_continues_one_selected_pr_and_blocks_duplicates(self):
        existing = {
            "number": 191, "title": "Fix issue #178", "body": "Closes #178",
            "headRefName": "agent/test", "closingIssuesReferences": [],
        }
        decision = handoff.preflight_issue_pr(
            "owner/repository", 178, 191, "agent/test", pull_requests=[existing])
        self.assertEqual(decision["decision"], "continue-existing")
        self.assertEqual(decision["pull_request"], 191)

        with self.assertRaisesRegex(handoff.HandoffDataError, "already exists"):
            handoff.preflight_issue_pr(
                "owner/repository", 178, pull_requests=[existing])
        list_reference = {
            **existing, "title": "Unrelated", "body": "Refs #177, #178",
            "headRefName": "feature/other",
        }
        with self.assertRaisesRegex(handoff.HandoffDataError, "already exists"):
            handoff.preflight_issue_pr(
                "owner/repository", 178, pull_requests=[list_reference])
        foreign_reference = {
            **list_reference, "title": "Fix other/repository-#178", "body": "",
        }
        decision = handoff.preflight_issue_pr(
            "owner/repository", 178, pull_requests=[foreign_reference])
        self.assertEqual(decision["decision"], "new-root")
        with self.assertRaisesRegex(handoff.HandoffDataError, "select existing"):
            handoff.preflight_issue_pr(
                "owner/repository", 178, 192, "agent/test", pull_requests=[existing])
        with self.assertRaisesRegex(handoff.HandoffDataError, "not agent/other"):
            handoff.preflight_issue_pr(
                "owner/repository", 178, 191, "agent/other", pull_requests=[existing])
        with self.assertRaisesRegex(handoff.HandoffDataError, "multiple open"):
            handoff.preflight_issue_pr(
                "owner/repository", 178, 191, "agent/test",
                pull_requests=[existing, {**existing, "number": 192, "headRefName": "agent/other"}])

        replacement = handoff.preflight_issue_pr(
            "owner/repository", 178, expected_branch="agent/replacement", pull_requests=[])
        self.assertEqual(replacement["decision"], "new-root")
        self.assertEqual(replacement["branch"], "agent/replacement")

    def test_assign_runs_issue_pr_gate_before_reserving_new_root(self):
        f = self.fixture
        new_assignment = {
            **f.assignment, "id": "new-issue-root", "issue": 179, "pull_request": None,
            "owner_id": "new-owner", "session_id": "new-session", "dispatch_id": "new-dispatch",
        }
        other_pr = {
            "number": 192, "title": "Issue 179 implementation", "body": "",
            "headRefName": "agent/test", "closingIssuesReferences": [],
        }
        state = handoff.new_state("owner/repository", "coordinator-one", f.state["availability"])
        with mock.patch.object(handoff, "list_open_pull_requests", return_value=[other_pr]):
            with self.assertRaisesRegex(handoff.HandoffDataError, "already exists"):
                handoff.assign(state, new_assignment)
        self.assertEqual(state["assignments"], [])

        with mock.patch.object(handoff, "list_open_pull_requests", return_value=[]):
            entry = handoff.assign(state, new_assignment)
        self.assertEqual(entry["assignment"]["issue"], 179)

    def test_preflight_cli_queries_live_inventory_and_fails_closed(self):
        self.fixture.open_prs_patch.stop()
        process = mock.Mock(returncode=0, stdout=b"[]", stderr=b"")
        with mock.patch.object(handoff.git, "run_process", return_value=process) as run_process:
            self.assertEqual(handoff.list_open_pull_requests("owner/repository"), [])
        argv = run_process.call_args.args[0]
        self.assertEqual(argv[:7], [
            "gh", "pr", "list", "--repo", "owner/repository", "--state", "open",
        ])
        self.assertIn("--json", argv)

        existing = [{
            "number": 191, "title": "Fix issue #178", "body": "Closes #178",
            "headRefName": "agent/test", "closingIssuesReferences": [],
        }]
        with mock.patch.object(
            handoff.git, "run_process",
            return_value=mock.Mock(
                returncode=0, stdout=observations.json_bytes(existing), stderr=b""),
        ):
            discovered = handoff.list_open_pull_requests("owner/repository")
        self.assertEqual(
            handoff.preflight_issue_pr(
                "owner/repository", 178, 191, "agent/test", pull_requests=discovered
            )["decision"],
            "continue-existing",
        )

        unrelated_empty_body_pr = [{
            "number": 193,
            "title": "Unrelated PR",
            "body": "",
            "headRefName": "feature/unrelated",
            "closingIssuesReferences": [],
        }]
        with mock.patch.object(
            handoff.git, "run_process",
            return_value=mock.Mock(
                returncode=0,
                stdout=observations.json_bytes(unrelated_empty_body_pr),
                stderr=b"",
            ),
        ):
            decision = handoff.preflight_issue_pr(
                "owner/repository", 179, expected_branch="fix/issue-179")
        self.assertEqual(decision["decision"], "new-root")

        with mock.patch.object(handoff.git, "run_process",
                               return_value=mock.Mock(returncode=1, stdout=b"", stderr=b"offline")):
            with self.assertRaisesRegex(handoff.HandoffDataError, "discovery failed"):
                handoff.list_open_pull_requests("owner/repository")

        for raw in (b"{}", observations.json_bytes([
            {"number": 191, "title": "a", "body": "", "headRefName": "b",
             "closingIssuesReferences": []},
            {"number": 191, "title": "c", "body": "", "headRefName": "d",
             "closingIssuesReferences": []},
        ])):
            with mock.patch.object(handoff.git, "run_process",
                                   return_value=mock.Mock(returncode=0, stdout=raw, stderr=b"")):
                with self.assertRaises(handoff.HandoffDataError):
                    handoff.list_open_pull_requests("owner/repository")

        bounded = observations.json_bytes([
            {"number": number, "title": "a", "body": "", "headRefName": "b",
             "closingIssuesReferences": []}
            for number in range(1, handoff.MAX_OPEN_PULL_REQUESTS + 1)
        ])
        with mock.patch.object(handoff.git, "run_process",
                               return_value=mock.Mock(returncode=0, stdout=bounded, stderr=b"")):
            with self.assertRaisesRegex(handoff.HandoffDataError, "1000-result bound"):
                handoff.list_open_pull_requests("owner/repository")

        output = io.BytesIO()
        arguments = [
            "preflight", "--repository", "owner/repository", "--issue", "179",
            "--expected-branch", "fix/issue-179",
        ]
        parsed = handoff.parse_args(arguments)
        with mock.patch.object(handoff, "list_open_pull_requests", return_value=[]):
            with mock.patch.object(handoff, "parse_args", return_value=parsed):
                with mock.patch.object(handoff.sys, "stdout", mock.Mock(buffer=output)):
                    status = handoff.main(arguments)
        self.assertEqual(status, 0)
        self.assertEqual(observations.parse_bytes(output.getvalue())["decision"], "new-root")

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

    def test_explicit_recovery_plan_can_retain_unknown_stop_settings(self):
        original = self.fixture.state
        for autostop, disconnect in ((None, None), (None, False), (True, None)):
            planned = copy.deepcopy(original)
            planned["availability"].update(
                mode="plan", autostop_enabled=autostop, stop_on_disconnect=disconnect,
                plan="Persist observations and renew coverage after interruption; do not rely on uptime.")
            for name, validator in (("schema", self.validator.validate), ("runtime", handoff.validate_state)):
                with self.subTest(settings=(autostop, disconnect), consumer=name):
                    validator(observations.parse_bytes(observations.json_bytes(planned)))
            self.assertEqual(handoff.availability_errors(planned, observations.utc_now()), [])
            always_on = copy.deepcopy(planned)
            always_on["availability"].update(mode="always-on", plan=None)
            self.reject_both(always_on, handoff.validate_state)

        for plan in (None, "", " "):
            changed = copy.deepcopy(planned)
            changed["availability"]["plan"] = plan
            self.reject_both(changed, handoff.validate_state)
        for invalid in (0, 1, "unknown", []):
            changed = copy.deepcopy(planned)
            changed["availability"]["autostop_enabled"] = invalid
            self.reject_both(changed, handoff.validate_state)

        expired = copy.deepcopy(planned)
        expired["availability"]["valid_until"] = at_offset(-1)
        self.assertEqual(handoff.availability_errors(expired, observations.utc_now()),
                         ["coordinator-unavailable"])
        clock = planned["clock"]
        for change in ({"boot_id": "different-boot"},
                       {"boottime_ns": clock["boottime_ns"] + 6_000_000_000}):
            with self.subTest(clock=change), mock.patch.object(
                    observations, "clock_observation", return_value={**clock, **change}):
                self.assertEqual(handoff.availability_errors(planned, observations.utc_now()),
                                 ["coordinator-unavailable"])

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

    def test_malformed_prohibited_action_members_are_rejected_without_raw_type_errors(self):
        for invalid in ({}, []):
            with self.subTest(invalid=invalid):
                assignment = copy.deepcopy(self.fixture.assignment)
                assignment["prohibited_remote_actions"][0] = invalid
                self.reject_both(assignment, handoff.validate_assignment)

    def test_malformed_terminal_state_is_rejected_before_lifetime_read(self):
        for invalid in ({}, [], "invalid", 0, False):
            with self.subTest(invalid=invalid):
                state = copy.deepcopy(self.fixture.state)
                state["assignments"][0]["terminal_task"] = invalid
                self.reject_both(state, handoff.validate_state)

    def test_protocol_input_overlap_is_an_explicit_runtime_cross_record_constraint(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        f.assignment["required_checks"].update({
            "one": {"contract": "protocol-json", "evidence_id": "one", "inputs": ["docs/a.json", "docs/b.json"]},
            "two": {"contract": "protocol-json", "evidence_id": "two", "inputs": ["docs/c.json"]},
        })
        self.validator.validate(f.assignment)
        f.entry = handoff.assign(f.state, f.assignment)
        for inputs in (["docs/a.json"], ["docs/b.json", "docs/a.json"], ["docs/c.json", "docs/b.json"]):
            with self.subTest(inputs=inputs):
                assignment = copy.deepcopy(f.assignment)
                assignment["required_checks"]["two"]["inputs"] = inputs
                self.validator.validate(assignment)
                with self.assertRaisesRegex(handoff.HandoffDataError, "overlapping protocol inputs"):
                    handoff.parse_assignment(observations.json_bytes(assignment))
                state = copy.deepcopy(f.state)
                state["assignments"][0]["assignment"] = assignment
                with self.assertRaisesRegex(handoff.HandoffDataError, "overlapping protocol inputs"):
                    handoff.validate_state(state)
                empty = {**f.state, "assignments": []}
                with self.assertRaisesRegex(handoff.HandoffDataError, "overlapping protocol inputs"):
                    handoff.assign(empty, assignment)
                self.assertEqual(empty["assignments"], [])

    def test_dispatch_identity_is_unique_in_reservations_and_loaded_history(self):
        f = self.fixture
        result = f.complete()
        self.assertTrue(f.validate(result)["handoff_ready"])
        for kind in ("review", "initial"):
            with self.subTest(kind=kind):
                assignment = {**copy.deepcopy(f.assignment), "id": "next", "owner_id": "next-owner",
                              "session_id": "next-session", "kind": kind,
                              "predecessor_id": f.assignment["id"] if kind == "review" else None,
                              "issue": 178 if kind == "review" else 179,
                              "pull_request": 191 if kind == "review" else 192,
                              "assigned_parent_sha": result["result_sha"]}
                state = copy.deepcopy(f.state)
                before = observations.json_bytes(state)
                with self.subTest(boundary="reservation"):
                    with self.assertRaisesRegex(handoff.HandoffDataError, "dispatch"):
                        handoff.assign(state, assignment)
                    self.assertEqual(observations.json_bytes(state), before)
                assignment["dispatch_id"] = "next-dispatch"
                state = copy.deepcopy(f.state)
                with mock.patch.object(handoff, "preflight_issue_pr", return_value={}):
                    handoff.assign(state, assignment)
                self.validator.validate(state)
                for key in ("id", "owner_id", "session_id", "dispatch_id"):
                    with self.subTest(boundary="loaded", identity=key):
                        duplicate = copy.deepcopy(state)
                        duplicate["assignments"][1]["assignment"][key] = f.assignment[key]
                        self.validator.validate(duplicate)
                        path = f.home / "duplicate-identity.json"
                        write_json(path, duplicate)
                        with self.assertRaises(handoff.HandoffDataError):
                            handoff.validate_state(observations.load_json(path))

    def test_interruption_requires_a_close_but_time_order_is_a_runtime_contract(self):
        f = self.fixture
        process = f.owner_process()
        f.finish_owner(process)
        handoff.preserve_interruption(f.entry, "process-exit")
        self.validator.validate(f.state)
        handoff.validate_state(f.state)
        changed = copy.deepcopy(f.state)
        changed["assignments"][0]["closed_at"] = None
        self.reject_both(changed, handoff.validate_state)
        for value in (at_offset(-3600), at_offset(3600)):
            with self.subTest(at=value):
                changed = copy.deepcopy(f.state)
                changed["assignments"][0]["interruption"]["at"] = value
                self.validator.validate(changed)
                with self.assertRaises(handoff.HandoffDataError):
                    handoff.validate_state(changed)

    def test_invalid_or_stale_owner_clock_cannot_become_zero_elapsed_time(self):
        f = self.fixture
        f.receive()
        for field in ("assigned_at", "closed_at", "clock", "event"):
            with self.subTest(field=field):
                changed = copy.deepcopy(f.state)
                if field == "clock":
                    changed["clock"]["at"] = at_offset(3600)
                elif field == "event":
                    changed["assignments"][0]["events"][-1]["at"] = at_offset(3600)
                else:
                    changed["assignments"][0][field] = at_offset(3600)
                if field == "closed_at":
                    with self.assertRaises(ValidationError):
                        self.validator.validate(changed)
                else:
                    self.validator.validate(changed)
                with self.assertRaises(handoff.HandoffDataError):
                    handoff.validate_state(changed)
                with self.assertRaises(handoff.HandoffDataError):
                    handoff.summarize_handoffs(changed)
        changed = copy.deepcopy(f.state)
        changed["clock"]["at"] = (observations.timestamp(f.entry["assigned_at"])
                                 - timedelta(microseconds=1)).isoformat().replace("+00:00", "Z")
        self.validator.validate(changed)
        with self.assertRaises(handoff.HandoffDataError):
            handoff.summarize_handoffs(changed)
        for value in (None, ""):
            changed = copy.deepcopy(f.state)
            changed["assignments"][0]["assigned_at"] = value
            self.reject_both(changed, handoff.validate_state)

    def test_unicode_scalar_constraints_cover_reusable_text_and_wire_fields(self):
        f = self.fixture
        self.assertTrue(f.validate(f.complete())["handoff_ready"])
        watcher_process = f.waiting_process()
        handoff.reserve_watcher(f.state, "watch", 91, 1, f.parent, watcher_process.pid)
        cases = [
            (f.assignment, handoff.validate_assignment, ("acceptance_criteria", "case-one", "text")),
            (f.assignment, handoff.validate_assignment, ("expected_branch",)),
            (f.assignment, handoff.validate_assignment, ("allowed_worktree",)),
            (f.assignment, handoff.validate_assignment, ("allowed_scope", 0)),
            (f.state, handoff.validate_state, ("availability", "plan")),
            (f.state, handoff.validate_state, ("assignments", 0, "events", 0, "source_id")),
            (f.state, handoff.validate_state, ("assignments", 0, "checks", 0, "detail")),
            (f.state, handoff.validate_state, ("assignments", 0, "git_identity", "common_dir")),
            (f.state, handoff.validate_state, ("assignments", 0, "cursors", 0, "session_id")),
            (f.state, handoff.validate_state, ("watchers", 0, "query_error")),
        ]
        f.assignment["required_checks"]["protocol"] = {
            "contract": "protocol-json", "evidence_id": "protocol", "inputs": ["docs/input.json"],
        }
        cases.append((f.assignment, handoff.validate_assignment, ("required_checks", "protocol", "inputs", 0)))
        recovery = GitFixture()
        self.addCleanup(recovery.close)
        process = recovery.owner_process()
        recovery.finish_owner(process)
        handoff.preserve_interruption(recovery.entry, "process-exit")
        for field in ("lock_reason", "oom_evidence"):
            cases.append((recovery.state, handoff.validate_state, ("assignments", 0, "interruption", field)))
        for base, parser, path in cases:
            for scalar in ("\ud800", "\udfff", "x\udbffy", "é通信🚀", "\ud7ff\ue000"):
                valid = not any(0xD800 <= ord(char) <= 0xDFFF for char in scalar)
                document = copy.deepcopy(base)
                target = document
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = (target[path[-1]] or "text") + scalar
                wire = json.dumps(document, ensure_ascii=True).encode("ascii")
                with self.subTest(path=path, scalar=ascii(scalar), boundary="schema"):
                    self.assertEqual(self.validator.is_valid(json.loads(wire)), valid)
                with self.subTest(path=path, scalar=ascii(scalar), boundary="api"):
                    if valid:
                        parser(observations.parse_bytes(wire))
                        parser(observations.parse_bytes(json.dumps(document, ensure_ascii=False).encode("utf-8")))
                    else:
                        with self.assertRaises(handoff.HandoffDataError):
                            parser(observations.parse_bytes(wire))
                        with self.assertRaises(handoff.HandoffDataError):
                            parser(document)

    def test_unicode_scalar_and_protocol_overlap_controls_through_real_cli(self):
        f = GitFixture(assign=False)
        self.addCleanup(f.close)
        environment = gh_stub_environment(f.home, [{
            "number": 191, "title": "Fix issue #178", "body": "Closes #178",
            "headRefName": "agent/test", "closingIssuesReferences": [],
        }])
        state_path, assignment_path = f.home / "state.json", f.home / "assignment.json"
        command = [str(ROOT / "build/host-python/bin/python3"), "-I",
                   str(ROOT / "scripts/workflow_pilot/isolated_launcher.py"), "agent-handoff",
                   "assign", "--state", str(state_path), "--assignment", str(assignment_path)]
        for text, expected in (("\ud800", 2), ("\udfff", 2), ('通信 café 🚀\n"quoted"\\escaped', 0)):
            for ascii_wire in (True, False) if expected == 0 else (True,):
                with self.subTest(text=ascii(text), ascii_wire=ascii_wire):
                    write_json(state_path, f.state)
                    before = state_path.read_bytes()
                    assignment = copy.deepcopy(f.assignment)
                    assignment["acceptance_criteria"]["case-one"]["text"] = text
                    wire = json.dumps(assignment, ensure_ascii=ascii_wire).encode("utf-8")
                    assignment_path.write_bytes(wire)
                    self.assertEqual(self.validator.is_valid(json.loads(wire)), expected == 0)
                    observed = raw.run_process(command, cwd=ROOT, env=environment)
                    self.assertEqual(observed.returncode, expected, observed.stderr.decode())
                    if expected:
                        self.assertEqual(state_path.read_bytes(), before)
                    else:
                        loaded = observations.load_json(state_path)["assignments"][0]["assignment"]
                        self.assertEqual(loaded["acceptance_criteria"]["case-one"]["text"], text)
        assignment = copy.deepcopy(f.assignment)
        assignment["required_checks"].update({
            name: {"contract": "protocol-json", "evidence_id": name, "inputs": ["docs/input.json"]}
            for name in ("one", "two")
        })
        write_json(state_path, f.state)
        before = state_path.read_bytes()
        write_json(assignment_path, assignment)
        self.validator.validate(assignment)
        rejected = raw.run_process(command, cwd=ROOT, env=environment)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(state_path.read_bytes(), before)

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
        environment = gh_stub_environment(f.home, [{
            "number": 191, "title": "Fix issue #178", "body": "Closes #178",
            "headRefName": "agent/test", "closingIssuesReferences": [],
        }])
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
            cwd=ROOT, env=environment,
        )
        self.assertEqual(assigned.returncode, 0, assigned.stderr.decode())
        loaded = observations.load_json(state_path)
        self.assertIs(type(loaded["assignments"][0]["assignment"]["issue"]), int)
        process = f.waiting_process()
        observe = [*command, "observe", "--state", str(state_path),
                   "--assignment-id", f.assignment["id"], "--pid", str(process.pid)]
        self.assertEqual(raw.run_process(observe, cwd=ROOT, env=environment).returncode, 0)
        wire = state_path.read_bytes().replace(
            f'"pid":{process.pid}'.encode(), f'"pid":{process.pid}e0'.encode())
        self.validator.validate(json.loads(wire))
        state_path.write_bytes(wire)
        sampled = raw.run_process(observe, cwd=ROOT, env=environment)
        self.assertEqual(sampled.returncode, 0, sampled.stderr.decode())
        self.assertIs(type(observations.load_json(state_path)["assignments"][0]["process"]["pid"]), int)
        for token in (b"true", b"178.5", b"NaN", b"1e309", b"9223372036854775808.0"):
            with self.subTest(token=token):
                assignment_path.write_bytes(raw_assignment.replace(b'"issue":178', b'"issue":' + token))
                before = state_path.read_bytes()
                rejected = raw.run_process(
                    [*command, "assign", "--state", str(state_path), "--assignment", str(assignment_path)],
                    cwd=ROOT, env=environment,
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
            if invalid == "2025-02-29T00:00:00Z":
                with self.assertRaises(handoff.HandoffDataError):
                    handoff.validate_state(observations.parse_bytes(
                        observations.json_bytes(changed)))
            else:
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

    def test_retained_watcher_capacity_exceeds_active_watcher_limit(self):
        state = copy.deepcopy(self.fixture.state)
        at = observations.utc_now()
        state["watchers"] = [
            {
                "id": f"retained-{index}",
                "coordinator_id": "previous-coordinator",
                "run_id": index + 1,
                "attempt": 1,
                "head_sha": self.fixture.parent,
                "process": {
                    "pid": 100000 + index,
                    "start_ticks": 1,
                    "boot_id": "test-boot",
                    "runtime_handle": f"retained-{index}",
                    "observed_at": at,
                    "state": "exited",
                    "peak_rss_bytes": 1,
                    "rss_complete": False,
                    "exit_code": 0,
                    "age_ms": 0,
                },
                "started_at": at,
                "ended_at": at,
                "exit_code": 0,
                "run": None,
                "query_error": None,
            }
            for index in range(129)
        ]
        state["watchers"].append({
            "id": "current-active",
            "coordinator_id": state["coordinator_id"],
            "run_id": 1000,
            "attempt": 1,
            "head_sha": self.fixture.parent,
            "process": {
                "pid": 200000,
                "start_ticks": 1,
                "boot_id": "test-boot",
                "runtime_handle": "current-active",
                "observed_at": at,
                "state": "running",
                "peak_rss_bytes": 1,
                "rss_complete": False,
                "exit_code": None,
                "age_ms": 0,
            },
            "started_at": at,
            "ended_at": None,
            "exit_code": None,
            "run": None,
            "query_error": None,
        })
        self.validator.validate(state)
        handoff.validate_state(state)

        def promote_to_active(watcher):
            watcher["coordinator_id"] = state["coordinator_id"]
            watcher["process"]["state"] = "running"
            watcher["process"]["exit_code"] = None
            watcher["ended_at"] = None
            watcher["exit_code"] = None

        at_active_limit = copy.deepcopy(state)
        for watcher in at_active_limit["watchers"][:127]:
            promote_to_active(watcher)
        self.validator.validate(at_active_limit)
        handoff.validate_state(at_active_limit)

        over_active_limit = copy.deepcopy(at_active_limit)
        promote_to_active(over_active_limit["watchers"][127])
        with self.assertRaises(ValidationError):
            self.validator.validate(over_active_limit)
        with self.assertRaisesRegex(handoff.HandoffDataError, "active watcher limit"):
            handoff.validate_state(over_active_limit)

        wrong_coordinator = copy.deepcopy(state)
        wrong_coordinator["watchers"][-1]["coordinator_id"] = "previous-coordinator"
        with self.assertRaisesRegex(handoff.HandoffDataError, "duplicate coordinator"):
            handoff.validate_state(wrong_coordinator)

        for invalid_id in (None, {}, "invalid coordinator"):
            with self.subTest(invalid_id=invalid_id):
                invalid_retained = copy.deepcopy(state)
                invalid_retained["watchers"][0]["coordinator_id"] = invalid_id
                with self.assertRaises(handoff.HandoffDataError):
                    handoff.validate_state(invalid_retained)

        overflow = copy.deepcopy(state)
        overflow["watchers"] = [None] * 32769
        state_schema = Draft202012Validator({"$defs": self.schema["$defs"], "$ref": "#/$defs/state"})
        self.assertTrue(any(error.validator == "maxItems" and list(error.path) == ["watchers"]
                            for error in state_schema.iter_errors(overflow)))
        with self.assertRaisesRegex(handoff.HandoffDataError, "bounded list"):
            handoff.validate_state(overflow)


class RawDiffBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = GitFixture()
        self.addCleanup(self.fixture.close)

    def test_metadata_paths_with_embedded_controls_match_git(self):
        root = self.fixture.home
        repository = root / "common\r\nrepo"
        repository.mkdir()
        (root / "common").mkdir()
        git(repository, "init", "-b", "master")
        git(repository, "config", "user.name", "Handoff Test")
        git(repository, "config", "user.email", "handoff@example.invalid")
        (repository / "tracked").write_text("fixture\n")
        git(repository, "add", "tracked")
        git(repository, "commit", "-m", "fixture")
        worktree = root / "metadata-worktree"
        git(repository, "worktree", "add", "--detach", str(worktree), "HEAD")
        private = Path(git(worktree, "rev-parse", "--absolute-git-dir"))
        common = repository / ".git"
        entry = worktree / ".git"
        commondir = private / "commondir"

        for gitdir_kind in ("absolute", "relative"):
            for common_kind in ("absolute", "relative"):
                with self.subTest(gitdir=gitdir_kind, commondir=common_kind):
                    gitdir_value = (str(private) if gitdir_kind == "absolute"
                                    else os.path.relpath(private, worktree))
                    common_value = (str(common) if common_kind == "absolute"
                                    else os.path.relpath(common, private))
                    entry.write_bytes(b"gitdir: " + os.fsencode(gitdir_value) + b"\r\n\n")
                    commondir.write_bytes(os.fsencode(common_value) + b"\r\n\n")
                    git_private = Path(git(worktree, "rev-parse", "--absolute-git-dir"))
                    git_common = Path(git(
                        worktree, "rev-parse", "--path-format=absolute", "--git-common-dir",
                    ))
                    parsed_private = raw._metadata_directory(entry.read_bytes(), b"gitdir: ", worktree)
                    parsed_common = raw._metadata_directory(
                        commondir.read_bytes(), b"", parsed_private,
                    )
                    self.assertEqual(parsed_private.resolve(), git_private.resolve())
                    self.assertEqual(parsed_common.resolve(), git_common.resolve())
                    self.assertEqual(raw.exact_repository_root(str(worktree)), worktree.resolve())

    def test_malformed_and_private_metadata_reject_before_git_dispatch(self):
        f = self.fixture
        entry = f.worktree / ".git"
        original = entry.read_bytes()
        try:
            for bad in (b"bad-prefix", b"gitdir: \r\n", b"gitdir: /tmp/a\0b\n",
                        b"gitdir: " + b"x" * raw.MAX_METADATA_BYTES):
                with self.subTest(metadata=bad[:20]):
                    entry.write_bytes(bad)
                    with mock.patch.object(raw, "run_git", side_effect=AssertionError("Git dispatched")):
                        with self.assertRaises(ValueError):
                            raw.exact_repository_root(str(f.worktree))
        finally:
            entry.write_bytes(original)

        private = Path(f.entry["git_identity"]["git_dir"])
        info = private / "info"
        info.mkdir(exist_ok=True)
        grafts = info / "grafts"
        grafts.write_text("forbidden\n")
        with mock.patch.object(raw, "run_git", side_effect=AssertionError("Git dispatched")):
            with self.assertRaisesRegex(ValueError, "graft file"):
                raw.exact_repository_root(str(f.worktree))

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

    def test_full_pr_raw_cli_bounds_each_pair_without_skipping_other_files(self):
        f = self.fixture
        line = "unchanged payload line\n"
        payload = line * (raw.MAX_BYTES // (3 * len(line)) + 1)
        paths = [f.worktree / "docs" / name for name in ("first.txt", "second.txt")]
        for path in paths:
            path.write_text("before\n" + payload)
        parent = f.commit_pending()
        for path in paths:
            path.write_text("after\n" + payload)
        revision = f.commit_pending()
        command = [
            sys.executable, "-I", str(ROOT / "scripts/workflow_pilot/raw_diff_check.py"),
            "--repository-root", str(f.worktree), "--parent", parent,
            "--candidate", revision,
        ]
        clean = raw.run_process(command, cwd=ROOT, env=raw.git_environment())
        self.assertEqual(clean.returncode, 0, (clean.stdout + clean.stderr).decode())
        assignment = {**f.assignment, "assigned_parent_sha": parent}
        with self.assertRaisesRegex(handoff.HandoffDataError, "4 MiB"):
            handoff.task_changes(assignment, revision)
        paths[1].write_text("after \n" + payload)
        invalid = f.commit_pending()
        command[-1] = invalid
        refused = raw.run_process(command, cwd=ROOT, env=raw.git_environment())
        self.assertEqual(refused.returncode, 1, (refused.stdout + refused.stderr).decode())
        self.assertIn(b"blank-at-eol", refused.stdout + refused.stderr)
        paths[1].write_text("after\n" + payload + "\n")
        command[-1] = f.commit_pending()
        refused = raw.run_process(command, cwd=ROOT, env=raw.git_environment())
        self.assertEqual(refused.returncode, 1, (refused.stdout + refused.stderr).decode())
        self.assertIn(b"blank-at-eof", refused.stdout + refused.stderr)

    def test_full_pr_raw_cli_still_bounds_combined_diff_output(self):
        f = self.fixture
        line = "unchanged payload line\n"
        payload = line * (raw.MAX_BYTES // (3 * len(line)) + 1)
        paths = [f.worktree / "docs" / name for name in ("first.txt", "second.txt")]
        for path in paths:
            path.write_text(payload)
        parent = f.commit_pending()
        for path in paths:
            path.write_text(payload.replace("unchanged", "rewritten"))
        revision = f.commit_pending()
        refused = raw.run_process(
            [
                sys.executable, "-I", str(ROOT / "scripts/workflow_pilot/raw_diff_check.py"),
                "--repository-root", str(f.worktree), "--parent", parent,
                "--candidate", revision,
            ],
            cwd=ROOT, env=raw.git_environment(),
        )
        self.assertEqual(refused.returncode, 2, (refused.stdout + refused.stderr).decode())
        self.assertIn(b"process output exceeds", refused.stdout + refused.stderr)

    def test_raw_cli_still_refuses_an_oversized_old_new_pair(self):
        f = self.fixture
        line = "unchanged payload line\n"
        payload = line * (raw.MAX_BYTES // (2 * len(line)) + 1)
        parent = f.commit("before\n" + payload)
        revision = f.commit("after\n" + payload)
        refused = raw.run_process(
            [
                sys.executable, "-I", str(ROOT / "scripts/workflow_pilot/raw_diff_check.py"),
                "--repository-root", str(f.worktree), "--parent", parent,
                "--candidate", revision,
            ],
            cwd=ROOT, env=raw.git_environment(),
        )
        self.assertEqual(refused.returncode, 2, (refused.stdout + refused.stderr).decode())
        self.assertIn(b"4 MiB", refused.stdout + refused.stderr)

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
    def measured_handoff(self, *, repeated_resources=False):
        f = GitFixture()
        self.addCleanup(f.close)
        f.assignment["allowed_scope"].append("src/")
        f.assignment["budgets"].update(rom_bytes=8, ram_bytes=4, protocol_changes=2)
        f.assignment["required_checks"]["resource"] = {
            "contract": "coordinator-check", "evidence_id": "resource", "inputs": [],
        }
        if repeated_resources:
            f.assignment["required_checks"]["resource-two"] = {
                "contract": "coordinator-check", "evidence_id": "resource-two", "inputs": [],
            }
        for name in ("one", "two"):
            file = f"docs/{name}.schema.json"
            f.assignment["required_checks"][name] = {
                "contract": "protocol-json", "evidence_id": name, "inputs": [file],
            }
            (f.worktree / file).write_text('{"type":"integer"}\n')
        result = f.complete(name="src/resource.c", content="int resource;\n")
        f.capture_resources(result["result_sha"])
        if repeated_resources:
            f.capture_resources(result["result_sha"], "resource-two")
        self.assertTrue(f.validate(result)["handoff_ready"])
        return f

    def reject_live_and_report(self, f, state, code):
        self.assertEqual(state["assignments"][0]["validation"]["local_outcome"], "accepted")
        baseline = {"schema_version": 1, "snapshot": {"repository": f.state["repository"]}}
        with self.subTest(boundary="report"):
            with self.assertRaises(reporter.PilotDataError):
                reporter.with_handoff_metrics(baseline, json.dumps(state).encode("ascii"))
        with self.subTest(boundary="live"):
            state = copy.deepcopy(state)
            result = state["assignments"][0]["result"]
            if code is None:
                with self.assertRaises(handoff.HandoffDataError):
                    handoff.validate_handoff(state, result, worktree=f.worktree, run_checks=False)
            else:
                verdict = handoff.validate_handoff(state, result, worktree=f.worktree, run_checks=False)
                self.assertFalse(verdict["handoff_ready"])
                self.assertIn(code, verdict["rejection_codes"])
                metrics = reporter.with_handoff_metrics(baseline, observations.json_bytes(state))[
                    "implementation_handoffs"]
                self.assertEqual((metrics["accepted"], metrics["rejected"]), (0, 1))

    def test_partial_and_repeated_resource_observations_measure_one_global_delta(self):
        f = self.measured_handoff(repeated_resources=True)
        self.assertEqual(handoff.summarize_handoffs(f.state)["accepted"], 1)
        state = copy.deepcopy(f.state)
        entry = state["assignments"][0]
        first = next(check for check in entry["checks"] if check["id"] == "resource")
        second = next(check for check in entry["checks"] if check["id"] == "resource-two")
        self.assertEqual(first["measurements"], second["measurements"])
        first["measurements"]["rom_bytes"] = None
        second["measurements"]["ram_bytes"] = None
        self.assertTrue(handoff.validate_handoff(
            state, entry["result"], worktree=f.worktree, run_checks=False)["handoff_ready"])
        self.assertEqual(handoff.summarize_handoffs(state)["accepted"], 1)
        second["measurements"]["rom_bytes"] = None
        self.reject_live_and_report(f, state, "missing-budget-measurement")

    def test_wall_lifetime_boundaries_match_live_and_historical_acceptance(self):
        f = GitFixture()
        self.addCleanup(f.close)
        result = f.complete()
        self.assertTrue(f.validate(result)["handoff_ready"])
        baseline = {"schema_version": 1, "snapshot": {"repository": f.state["repository"]}}
        histories = []
        for micros in (-1, 0, 1):
            with self.subTest(microseconds_past_limit=micros):
                state = copy.deepcopy(f.state)
                entry = state["assignments"][0]
                elapsed = timedelta(seconds=f.assignment["max_lifetime_seconds"], microseconds=micros)
                entry["assigned_at"] = (observations.timestamp(entry["closed_at"]) - elapsed).isoformat().replace(
                    "+00:00", "Z")
                handoff.validate_state(state)
                self.assertLess(entry["process"]["age_ms"], f.assignment["max_lifetime_seconds"] * 1000)
                wire = observations.json_bytes(state)
                histories.append((wire, micros <= 0))
                with self.subTest(boundary="report"):
                    if micros > 0:
                        with self.assertRaises(reporter.PilotDataError):
                            reporter.with_handoff_metrics(baseline, wire)
                    else:
                        metrics = reporter.with_handoff_metrics(baseline, wire)["implementation_handoffs"]
                        self.assertEqual(metrics["accepted"], 1)
                        self.assertEqual(metrics["max_lifetime_ms"], 300000)
                verdict = handoff.validate_handoff(state, entry["result"], worktree=f.worktree, run_checks=False)
                self.assertEqual(verdict["handoff_ready"], micros <= 0)
                if micros > 0:
                    self.assertIn("owner-lifetime-exceeded", verdict["rejection_codes"])
        f.close()
        self.assertFalse(f.worktree.exists())
        for wire, valid in histories:
            with self.subTest(worktree_absent=True, valid=valid):
                if valid:
                    self.assertEqual(reporter.with_handoff_metrics(baseline, wire)["implementation_handoffs"][
                        "accepted"], 1)
                else:
                    with self.assertRaises(reporter.PilotDataError):
                        reporter.with_handoff_metrics(baseline, wire)

    def test_accepted_resource_measurements_remain_complete_and_within_budgets(self):
        f = self.measured_handoff()
        for metric in ("rom_bytes", "ram_bytes", "protocol_changes"):
            for mutation in ("missing", "unknown", "over-budget", "string", "boolean", "fraction", "negative"):
                with self.subTest(metric=metric, mutation=mutation):
                    state = copy.deepcopy(f.state)
                    checks = state["assignments"][0]["checks"]
                    check = next(item for item in checks if item["id"] == (
                        "one" if metric == "protocol_changes" else "resource"))
                    code = None
                    if mutation == "missing":
                        del check["measurements"][metric]
                    elif mutation == "unknown":
                        check["measurements"][metric] = None
                        code = "missing-budget-measurement"
                    elif mutation == "over-budget":
                        if metric == "protocol_changes":
                            state["assignments"][0]["assignment"]["budgets"][metric] -= 1
                        else:
                            check["measurements"][metric] = f.assignment["budgets"][metric] + 1
                        code = metric.replace("_", "-") + "-budget-exceeded"
                    else:
                        check["measurements"][metric] = {
                            "string": "1", "boolean": True, "fraction": 0.5, "negative": -1,
                        }[mutation]
                    self.reject_live_and_report(f, state, code)
        for value in (None, 0):
            state = copy.deepcopy(f.state)
            check = next(item for item in state["assignments"][0]["checks"] if item["id"] == "resource")
            check["measurements"] = value
            self.reject_live_and_report(f, state, None)
        self.assertEqual(handoff.summarize_handoffs(f.state)["accepted"], 1)

    def test_accepted_protocol_partitions_cannot_be_masked_by_other_observations(self):
        f = self.measured_handoff()
        for mutation in ("unknown", "impossible", "missing-check", "overlap", "undeclared-rescue"):
            with self.subTest(mutation=mutation):
                state = copy.deepcopy(f.state)
                entry = state["assignments"][0]
                protocol = next(check for check in entry["checks"] if check["id"] == "one")
                resource = next(check for check in entry["checks"] if check["id"] == "resource")
                resource["measurements"]["protocol_changes"] = 0
                code = "missing-budget-measurement"
                if mutation == "unknown":
                    protocol["measurements"]["protocol_changes"] = None
                elif mutation == "impossible":
                    protocol["measurements"]["protocol_changes"] = 2
                    code = "invalid-protocol-measurement"
                elif mutation == "missing-check":
                    entry["checks"].remove(protocol)
                    code = "missing-check"
                elif mutation == "overlap":
                    entry["assignment"]["required_checks"]["two"]["inputs"] = ["docs/one.schema.json"]
                    code = None
                else:
                    extra = {**copy.deepcopy(resource), "id": "not-required"}
                    resource["measurements"]["rom_bytes"] = None
                    entry["checks"].append(extra)
                self.reject_live_and_report(f, state, code)

    def test_accepted_checks_retain_identity_completion_and_observation_times(self):
        f = self.measured_handoff()
        for field, value, code in (
            ("result_sha", f.parent, "check-identity-mismatch"),
            ("parent_sha", "f" * 40, "check-identity-mismatch"),
            ("worktree", str(f.repository), "check-identity-mismatch"),
            ("contract", "git-diff-check", "check-identity-mismatch"),
            ("evidence_id", "wrong-evidence", "check-identity-mismatch"),
            ("exit_code", 1, "required-check-failed"),
            ("exit_code", None, "incomplete-check"),
            ("started_at", at_offset(-3600), "check-time-mismatch"),
            ("completed_at", at_offset(3600), "check-time-mismatch"),
        ):
            with self.subTest(field=field, value=value):
                state = copy.deepcopy(f.state)
                check = next(item for item in state["assignments"][0]["checks"] if item["id"] == "resource")
                check[field] = value
                self.reject_live_and_report(f, state, code)
        for mutation in ("missing-check", "missing-evidence", "unfinished"):
            with self.subTest(mutation=mutation):
                state = copy.deepcopy(f.state)
                entry = state["assignments"][0]
                check = next(item for item in entry["checks"] if item["id"] == "resource")
                if mutation == "missing-check":
                    entry["checks"].remove(check)
                    code = "missing-check"
                elif mutation == "missing-evidence":
                    entry["result"]["evidence_refs"].remove("resource")
                    code = "missing-evidence"
                else:
                    check.update(completed_at=None, exit_code=None)
                    code = "incomplete-check"
                self.reject_live_and_report(f, state, code)

    def test_captured_host_and_import_zeros_support_historical_reporting_without_worktree(self):
        for kind in ("host", "import", "measured"):
            with self.subTest(kind=kind):
                if kind == "measured":
                    f = self.measured_handoff()
                else:
                    f = GitFixture(upstream=kind == "import")
                    self.addCleanup(f.close)
                    if kind == "import":
                        process = f.owner_process()
                        f.receive()
                        git(f.worktree, "merge", "--no-commit", "--no-ff", f.upstream)
                        result = f.deliver(f.commit_pending())
                        f.finish_owner(process)
                    else:
                        result = f.complete(name="docs/empty.txt", content="")
                    self.assertTrue(f.validate(result)["handoff_ready"])
                    raw_check = next(check for check in f.entry["checks"] if check["id"] == "raw")
                    self.assertEqual(raw_check["measurements"], dict.fromkeys(handoff.METRICS, 0))
                    for metric in handoff.METRICS:
                        state = copy.deepcopy(f.state)
                        state["assignments"][0]["checks"][0]["measurements"][metric] = None
                        self.reject_live_and_report(f, state, "missing-budget-measurement")
                baseline = {"schema_version": 1, "snapshot": {"repository": f.state["repository"]}}
                wire = observations.json_bytes(f.state)
                f.close()
                self.assertFalse(f.worktree.exists())
                envelope = reporter.with_handoff_metrics(baseline, wire)
                self.assertEqual(envelope["implementation_handoffs"]["accepted"], 1)
                self.assertEqual(envelope["baseline"], baseline)

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
