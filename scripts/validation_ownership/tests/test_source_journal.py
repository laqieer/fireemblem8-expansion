"""Independent fixed-directory windows; the original new-parent path stays held."""

import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import shlex
import struct
import subprocess
import unittest
from unittest.mock import patch

from scripts.validation_ownership import make_probe, source_journal
from scripts.validation_ownership.authority import ENVIRONMENT
from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.producer_channel import ProducerChannel
from scripts.validation_ownership.tests import test_foundation as foundation
from scripts.validation_ownership.tests import test_source_phases as phases


class SourceJournalTests(unittest.TestCase):
    def setUp(self):
        self.case = phases.SourcePhaseTests()
        self.case.setUp()
        self.fixture = self.case.fixture

    def tearDown(self):
        self.case.tearDown()

    def test_actual_fixed_parent_publication_and_cleanup_have_independent_kernel_events(self):
        self.fixture.add("build/keep.h", "/* genuinely preexisting fixture parent */\n")
        with self.case.session() as session:
            result = session.make(
                "all", variables=("HIDDEN",), commands=self.case.commands(session), observe_source_journal=True,
            )
            journal = result.source_journal
            self.assertTrue(journal["closed"])
            self.assertEqual(journal["mode"], "fixed-directories")
            self.assertEqual([row["stage"] for row in journal["native_receipts"]], ["begin", "end"])
            transactions = journal["transactions"]
            self.assertEqual([row["operation"] for row in transactions], ["files", "cleanup", "cleanup"])
            self.assertEqual(set(transactions[0]["paths"]), {"src/new.c", "build/remade.mk"})
            self.assertEqual((transactions[0]["origin"]["pass"], transactions[0]["origin"]["stage"]),
                             (1, "after-read"))
            self.assertTrue(all(row["producer"] is None and row["origin"] is None for row in transactions[1:]))
            self.assertEqual(
                [(row["event_start"], row["event_end"]) for row in transactions],
                [(0, transactions[0]["event_end"]),
                 (transactions[0]["event_end"], transactions[1]["event_end"]),
                 (transactions[1]["event_end"], len(journal["events"]))],
            )
            self.assertEqual(result.semantics["native_dispatches"][0]["environment"]["HIDDEN"], "secret")
            self.assertEqual(result.semantics["domains"]["HIDDEN"]["origin"], "undefined")
            self.assertEqual(len(session._source_phase_images(result)), 2)
            token = session._original_namespace(result, target="all", makefile="Makefile")
            with self.assertRaises(MakeProbeError):
                session._original_wildcard(token, "src/*.c")
            instances = list(session._source_journal_instances)
            self.assertTrue(instances and all(item.fd == -1 and item.pins for item in instances))
        self.fixture.assert_clean(session)
        self.assertTrue(all(item.closed and not item.pins for item in instances))

    def test_new_directory_is_refused_instead_of_bootstrapping_an_incomplete_watch(self):
        with self.case.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "newly created directory"):
                session.make("all", commands=self.case.commands(session), observe_source_journal=True)
        self.fixture.assert_clean(session)

    def test_unknown_host_transients_and_restored_input_writes_are_not_absorbed(self):
        self.fixture.add("build/keep.h", "/* existing parent */\n")
        for defect in ("before-transient", "during-transient", "restored-input"):
            with self.subTest(defect=defect), self.case.session() as session:
                receive, changed = ProducerChannel.receive, []
                def inject(channel):
                    data = receive(channel)
                    if data is None:
                        return None
                    value = json.loads(data)
                    stage = "begin" if defect == "before-transient" else "end"
                    if value.get("kind") == "journal-barrier" and value["stage"] == stage and not changed:
                        changed.append(defect)
                        if defect == "restored-input":
                            path = session.tree / "Makefile"
                            original = path.read_bytes()
                            path.write_bytes(original + b"# unreceipted host write\n")
                            path.write_bytes(original)
                        else:
                            path = session.tree / "src/unreceipted"
                            path.write_bytes(b"transient")
                            path.unlink()
                    return data
                with patch.object(ProducerChannel, "receive", inject), self.assertRaises(MakeProbeError):
                    session.make("all", commands=self.case.commands(session), observe_source_journal=True)
                self.assertEqual(changed, [defect])
            self.fixture.assert_clean(session)

    def test_removed_observer_and_corrupted_real_kernel_stream_fail_closed(self):
        self.fixture.add("build/keep.h", "/* existing parent */\n")
        with self.case.session() as session:
            with patch.object(source_journal.FixedDirectoryJournal, "_read_events", return_value=[]):
                with self.assertRaisesRegex(MakeProbeError, "kernel mutations"):
                    session.make("all", commands=self.case.commands(session), observe_source_journal=True)
        self.fixture.assert_clean(session)
        for defect in ("overflow", "truncated", "foreign-watch"):
            changed = []
            class KernelCalls:
                def __getattr__(self, name):
                    return getattr(os, name)
                def read(self, descriptor, count):
                    data = os.read(descriptor, count)
                    if data and not changed:
                        changed.append(defect)
                        if defect == "overflow":
                            return struct.pack("iIII", -1, source_journal.OVERFLOW, 0, 0) + data
                        if defect == "truncated":
                            return data[:-1]
                        return struct.pack("i", 0x7FFFFFFF) + data[4:]
                    return data
            with self.subTest(defect=defect), self.case.session() as session:
                with patch.object(source_journal, "os", KernelCalls()), self.assertRaises(MakeProbeError):
                    session.make("all", commands=self.case.commands(session), observe_source_journal=True)
                self.assertEqual(changed, [defect])
            self.fixture.assert_clean(session)

    def test_native_windows_replies_and_terminal_receipts_cannot_be_forged(self):
        self.fixture.add("build/keep.h", "/* existing parent */\n")
        for defect in ("origin", "producer", "stage", "reply-digest", "report-missing", "report-receipt"):
            with self.subTest(defect=defect), self.case.session() as session:
                receive, send, read = ProducerChannel.receive, ProducerChannel.send, session.budget.read_bytes
                changed = []
                def receive_changed(channel):
                    data = receive(channel)
                    if data is None:
                        return None
                    value = json.loads(data)
                    if value.get("kind") == "journal-barrier" and defect in {"origin", "producer", "stage"} and not changed:
                        changed.append(defect)
                        if defect == "origin":
                            value["origin"]["pass"] += 1
                        elif defect == "producer":
                            value["producer"] += 1
                        else:
                            value["stage"] = {}
                        return json.dumps(value).encode()
                    return data
                def send_changed(channel, data):
                    value = json.loads(data)
                    if value.get("kind") == "journal-resume" and defect == "reply-digest" and not changed:
                        changed.append(defect)
                        value["journal_sha256"] = "0" * 64
                        data = json.dumps(value).encode()
                    return send(channel, data)
                def read_changed(path, category):
                    data = read(path, category)
                    if category == "control" and Path(path).name.startswith("report-") and defect.startswith("report"):
                        value = json.loads(data)
                        if "source_journal" in value and not changed:
                            changed.append(defect)
                            if defect == "report-missing":
                                del value["source_journal"]
                            else:
                                value["source_journal"]["receipts"].pop()
                            return json.dumps(value).encode()
                    return data
                with patch.object(ProducerChannel, "receive", receive_changed), patch.object(ProducerChannel, "send", send_changed):
                    with patch.object(session.budget, "read_bytes", read_changed), self.assertRaises(MakeProbeError):
                        session.make("all", commands=self.case.commands(session), observe_source_journal=True)
                self.assertEqual(changed, [defect])
            self.fixture.assert_clean(session)

    def test_copied_changed_remapped_and_expired_journals_do_not_reuse_original_pins(self):
        self.fixture.add("build/keep.h", "/* existing parent */\n")
        with self.case.session() as session:
            result = session.make("all", commands=self.case.commands(session), observe_source_journal=True)
            with self.assertRaises(MakeProbeError):
                session._source_phase_images(replace(result))
            saved = copy.deepcopy(result.source_journal)
            result.source_journal["events"].clear()
            with self.assertRaises(MakeProbeError):
                session._source_phase_images(result)
            result.source_journal.clear()
            result.source_journal.update(saved)
            self.assertEqual(len(session._source_phase_images(result)), 2)
            original, renamed = session.tree / "src", session.tree / "renamed-src"
            original.rename(renamed)
            original.mkdir()
            try:
                with self.assertRaisesRegex(MakeProbeError, "remapped"):
                    session._source_phase_images(result)
            finally:
                original.rmdir()
                renamed.rename(original)
        self.fixture.assert_clean(session)
        with self.assertRaises(MakeProbeError):
            session._source_phase_images(result)

    def test_nested_make_and_invalid_or_default_selections_keep_their_boundaries(self):
        for name in ("observe_source_journal", "observe_source_phases"):
            for value in (None, 0, 1, "true"):
                with self.subTest(name=name, value=value), self.case.session() as session:
                    runs = session.budget.runs
                    with self.assertRaises(MakeProbeError):
                        session.make("all", **{name: value})
                    self.assertEqual(session.budget.runs, runs)
                self.fixture.assert_clean(session)
        with self.case.session() as session:
            result = session.make("all", commands=self.case.commands(session), observe_source_phases=True)
            self.assertIsNone(result.source_journal)
        self.fixture.assert_clean(session)
        self.fixture.add("build/keep.h", "/* existing parent */\n")
        with self.case.session() as session:
            attempts = []
            def nested(command):
                runs = session.budget.runs
                try:
                    return session.make("all")
                finally:
                    attempts.append((runs, session.budget.runs))
            with patch.object(session, "command", nested), self.assertRaisesRegex(MakeProbeError, "non-nested"):
                session.make("all", commands=self.case.commands(session), observe_source_journal=True)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(*attempts[0])
        self.fixture.assert_clean(session)

    def test_genuine_existing_parent_message_arm_filter_transfer_and_cleanup(self):
        from scripts.validation_ownership.tests import test_header_pipeline as header_tests

        case = header_tests.ArmHeaderPipelineTests(
            "test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps",
        )
        case.setUp()
        case.fixture.add("build/native/src/existing-parent.txt", "admitted existing-parent profile\n")
        captured = []
        original = make_probe.ProbeSession._make
        def observe(session, *args, **kwargs):
            kwargs["observe_source_journal"] = True
            result = original(session, *args, **kwargs)
            captured.append(result)
            return result
        def ordinary_existing_parent():
            completed = subprocess.run(
                ["/usr/bin/make", "-rR", "--no-print-directory", "expansion-modern-all"],
                cwd=case.fixture.root, env=ENVIRONMENT, capture_output=True, timeout=20,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            path = case.fixture.root / case.target
            value = path.read_bytes()
            path.unlink()
            return value
        try:
            # This profile's admitted parent witness must survive ordinary-result cleanup.
            with patch.object(case, "ordinary", ordinary_existing_parent):
                with patch.object(make_probe.ProbeSession, "_make", observe):
                    case.test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps()
            self.assertEqual(len(captured), 1)
            journal = captured[0].source_journal
            self.assertTrue(journal["closed"])
            self.assertEqual([row["operation"] for row in journal["transactions"]],
                             ["files", "directory", "files", "files", "retire", "transfer", "cleanup", "cleanup"])
            self.assertEqual(len(journal["native_receipts"]), 12)
            transfer, = [row for row in journal["transactions"] if row["operation"] == "transfer"]
            events = journal["events"][transfer["event_start"]:transfer["event_end"]]
            self.assertEqual([row["mask"] for row in events], [source_journal.MOVED_FROM, source_journal.MOVED_TO])
            self.assertEqual(events[0]["cookie"], events[1]["cookie"])
            self.assertNotEqual(events[0]["cookie"], 0)
        finally:
            case.tearDown()

    def test_original_missing_parent_message_path_remains_an_explicit_hold(self):
        from scripts.validation_ownership.tests import test_header_pipeline as header_tests

        case = header_tests.ArmHeaderPipelineTests(
            "test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps",
        )
        case.setUp()
        sessions = []
        original = make_probe.ProbeSession._make
        def observe(session, *args, **kwargs):
            sessions.append(session)
            kwargs["observe_source_journal"] = True
            return original(session, *args, **kwargs)
        try:
            with patch.object(make_probe.ProbeSession, "_make", observe):
                with self.assertRaisesRegex(MakeProbeError, "newly created directory"):
                    case.test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps()
            self.assertEqual(len(sessions), 1)
            case.assert_clean(sessions[0])
        finally:
            case.tearDown()

    def test_exact_native_owner_runtime_and_human_case_are_wired(self):
        from scripts import check_docs
        from scripts.validation_ownership import ci_verifier, reporter
        from scripts.validation_ownership.authority import AuthorityLoader, git_tree_entries
        from scripts.validation_ownership.budget import ProbeBudget

        root = foundation.ROOT
        graph = reporter.load_json(root / reporter.GRAPH_PATH)
        budget = ProbeBudget()
        try:
            entries = git_tree_entries(root, budget=budget)
            sources = reporter._path_admission_sources(AuthorityLoader(root, entries, "HEAD", budget=budget), set())
            for path, expected in (
                ("scripts/validation_ownership/source_journal.py", "paths.ownership"),
                ("scripts/validation_ownership/tests/test_source_journal.py", "paths.ownership-native"),
            ):
                rule, = [row for row in graph["path_rules"] if reporter._path_rule_matches(row, path, set())]
                self.assertEqual(rule["id"], expected)
                self.assertEqual(reporter._path_admission(path, rule, sources), "exact-ownership-rule")
                if "/tests/" not in path:
                    self.assertIn(path, ci_verifier.TRUSTED_RUNTIME_PATHS)
                else:
                    removed = {**rule, "include": [item for item in rule["include"]
                                                   if item != {"kind": "exact", "path": path}]}
                    with self.assertRaises(reporter.OwnershipError):
                        reporter._path_admission(path, removed, sources)
        finally:
            budget.close()
        registry, errors = check_docs.parse_test_case_registry(str(root))
        self.assertEqual(errors, [])
        case, = [item for item in registry["cases"] if item["id"] == "TC-WORKFLOW-GATE-OWNERSHIP-001"]
        feature, = [item for item in registry["features"] if item["id"] == case["feature_id"]]
        self.assertIn(
            (("python3", "-m", "unittest", __name__, "-v"), "scripts/validation_ownership/tests/test_source_journal.py"),
            {(tuple(shlex.split(item["command"])), item["evidence"]) for item in case["automation"]},
        )
        selected = {
            **registry, "features": [{**feature, "required_cases": [case["id"]]}], "cases": [case],
            "coverage": {**registry["coverage"], "expected_feature_ids": [feature["id"]]},
        }
        with patch.object(check_docs, "parse_test_case_registry", return_value=(selected, [])):
            self.assertEqual(check_docs.check_test_case_registry(str(root)), [])


if __name__ == "__main__":
    unittest.main()
