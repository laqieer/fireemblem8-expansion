"""Prewatched new parents; never general directory relocation or phase authority."""

import json
from dataclasses import replace
from pathlib import Path
import shlex
from unittest.mock import patch
import unittest

from scripts.validation_ownership import make_probe, source_directories
from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.make_probe import Command
from scripts.validation_ownership.producer_channel import ProducerChannel
from scripts.validation_ownership.tests import test_source_phases as phases
from scripts.validation_ownership.tests import test_foundation as foundation


class SourceDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.case = phases.SourcePhaseTests()
        self.case.setUp()
        self.fixture = self.case.fixture

    def tearDown(self):
        self.case.tearDown()

    def test_new_parent_is_watched_before_publication_and_retired_after_files(self):
        with self.case.session() as session:
            result = session.make(
                "all", variables=("HIDDEN",), commands=self.case.commands(session),
                observe_source_journal=True, source_journal_mode=source_directories.MODE,
            )
            observed = result.source_journal
            self.assertEqual((observed["version"], observed["mode"]), (2, source_directories.MODE))
            self.assertTrue(observed["closed"])
            directory, = observed["directories"]
            self.assertEqual(directory["path"], "build")
            self.assertTrue(directory["watch_events"] and directory["installed_events"])
            self.assertLess(max(directory["watch_events"]), min(directory["installed_events"]))
            self.assertEqual([row["operation"] for row in observed["transactions"]],
                             ["files", "cleanup", "cleanup", "cleanup-directory"])
            self.assertEqual(result.semantics["native_dispatches"][0]["environment"]["HIDDEN"], "secret")
            self.assertEqual(result.semantics["domains"]["HIDDEN"]["origin"], "undefined")
            self.assertNotIn("build", result.source_phases["entries"][0]["image"]["directories"])
            self.assertIn("build", result.source_phases["entries"][1]["image"]["directories"])
            self.assertFalse((session.tree / "build").exists())
            self.assertEqual(len(session._source_phase_images(result)), 2)
            token = session._original_namespace(result, target="all", makefile="Makefile")
            with self.assertRaises(MakeProbeError):
                session._original_wildcard(token, "src/*.c")
        self.fixture.assert_clean(session)

    def test_watched_stage_transients_nonempty_replacement_and_mode_changes_reject(self):
        for defect in ("before-transient", "after-transient", "nonempty", "source-replaced", "mode"):
            with self.subTest(defect=defect), self.case.session() as session:
                send, receive, changed = ProducerChannel.send, ProducerChannel.receive, []
                def before_install(channel, data):
                    value = json.loads(data)
                    if (
                        value.get("kind") == "directory-ready" and value["phase"] == "watch"
                        and defect != "after-transient" and not changed
                    ):
                        changed.append(defect)
                        journal = session._source_journal_active[-1]
                        stage = journal.staging_root / value["stage"]
                        if defect == "source-replaced":
                            stage.rename(journal.staging_root / "unissued-source")
                            stage.mkdir()
                        elif defect == "mode":
                            stage.chmod(0o700)
                        else:
                            child = stage / "unissued-child"
                            child.write_bytes(b"actual hidden activity")
                            if defect == "before-transient":
                                child.unlink()
                    return send(channel, data)
                def after_install(channel):
                    data = receive(channel)
                    if data is None:
                        return None
                    value = json.loads(data)
                    if (
                        value.get("kind") == "directory-handoff" and value["phase"] == "installed"
                        and defect == "after-transient" and not changed
                    ):
                        changed.append(defect)
                        child = session.tree / value["path"] / "unissued-child"
                        child.write_bytes(b"actual hidden activity")
                        child.unlink()
                    return data
                with patch.object(ProducerChannel, "send", before_install), patch.object(ProducerChannel, "receive", after_install):
                    with self.assertRaises(MakeProbeError):
                        session.make(
                            "all", commands=self.case.commands(session),
                            observe_source_journal=True, source_journal_mode=source_directories.MODE,
                        )
                self.assertEqual(changed, [defect])
            self.fixture.assert_clean(session)

    def test_actual_directory_packets_and_acknowledgements_retain_closed_bindings(self):
        for defect in ("source", "parent", "path", "sequence", "origin", "ack-digest", "terminal-receipt"):
            with self.subTest(defect=defect), self.case.session() as session:
                receive, send, read = ProducerChannel.receive, ProducerChannel.send, session.budget.read_bytes
                changed = []
                def request_changed(channel):
                    data = receive(channel)
                    if data is None:
                        return None
                    value = json.loads(data)
                    if value.get("kind") == "directory-handoff" and defect in {"source", "parent", "path", "sequence", "origin"} and not changed:
                        changed.append(defect)
                        if defect in {"source", "parent"}:
                            value[defect][1] += 1
                        elif defect == "path":
                            value["path"] = "src"
                        elif defect == "sequence":
                            value["sequence"] += 1
                        else:
                            value["origin"]["scope"] += "-foreign"
                        return json.dumps(value).encode()
                    return data
                def reply_changed(channel, data):
                    value = json.loads(data)
                    if value.get("kind") == "directory-ready" and defect == "ack-digest" and not changed:
                        changed.append(defect)
                        value["journal_sha256"] = "0" * 64
                        data = json.dumps(value).encode()
                    return send(channel, data)
                def report_changed(path, category):
                    data = read(path, category)
                    if category == "control" and Path(path).name.startswith("report-") and defect == "terminal-receipt":
                        value = json.loads(data)
                        journal = value.get("source_journal")
                        if journal is not None and journal.get("version") == 2 and not changed:
                            changed.append(defect)
                            journal["directories"].pop()
                            return json.dumps(value).encode()
                    return data
                with patch.object(ProducerChannel, "receive", request_changed), patch.object(ProducerChannel, "send", reply_changed):
                    with patch.object(session.budget, "read_bytes", report_changed), self.assertRaises(MakeProbeError):
                        session.make(
                            "all", commands=self.case.commands(session),
                            observe_source_journal=True, source_journal_mode=source_directories.MODE,
                        )
                self.assertEqual(changed, [defect])
            self.fixture.assert_clean(session)

    def test_missing_child_watch_evidence_and_remapped_public_parent_reject(self):
        original = source_directories.PrewatchedDirectoryJournal._read_events
        with self.case.session() as session:
            removed = []
            def without_move(journal):
                rows = original(journal)
                changed = [row for row in rows if row["mask"] == 0x800]
                if changed:
                    removed.extend(changed)
                    return [row for row in rows if row["mask"] != 0x800]
                return rows
            with patch.object(source_directories.PrewatchedDirectoryJournal, "_read_events", without_move):
                with self.assertRaisesRegex(MakeProbeError, "exact kernel move"):
                    session.make(
                        "all", commands=self.case.commands(session),
                        observe_source_journal=True, source_journal_mode=source_directories.MODE,
                    )
            self.assertTrue(removed)
        self.fixture.assert_clean(session)
        with self.case.session() as session:
            send, moved = ProducerChannel.send, []
            def remap(channel, data):
                value = json.loads(data)
                if value.get("kind") == "directory-ready" and value["phase"] == "watch" and not moved:
                    old = session.tree
                    saved = old.with_name("saved-original-source")
                    old.rename(saved)
                    old.mkdir()
                    moved.append((old, saved))
                return send(channel, data)
            try:
                with patch.object(ProducerChannel, "send", remap), self.assertRaises(MakeProbeError):
                    session.make(
                        "all", commands=self.case.commands(session),
                        observe_source_journal=True, source_journal_mode=source_directories.MODE,
                    )
                self.assertEqual(len(moved), 1)
            finally:
                for old, saved in moved:
                    old.rmdir()
                    saved.rename(old)
        self.fixture.assert_clean(session)

    def test_profile_selection_copy_expiry_and_ungranted_directory_rename_stay_closed(self):
        for mode in (None, True, "unknown"):
            with self.subTest(mode=mode), self.case.session() as session:
                runs = session.budget.runs
                with self.assertRaises(MakeProbeError):
                    session.make("all", observe_source_journal=True, source_journal_mode=mode)
                self.assertEqual(session.budget.runs, runs)
            self.fixture.assert_clean(session)
        with self.case.session() as session:
            with self.assertRaises(MakeProbeError):
                session.make("all", source_journal_mode=source_directories.MODE)
        self.fixture.assert_clean(session)
        with self.case.session() as session:
            observed = session.make(
                "all", commands=self.case.commands(session),
                observe_source_journal=True, source_journal_mode=source_directories.MODE,
            )
            self.assertEqual(len(session._source_phase_images(observed)), 2)
            with self.assertRaises(MakeProbeError):
                session._source_phase_images(replace(observed))
            instances = list(session._source_journal_instances)
            self.assertTrue(all(journal.stage_removed and journal.stage_fd == -1 for journal in instances))
        self.fixture.assert_clean(session)
        self.assertTrue(all(journal.closed and not journal.pins for journal in instances))
        with self.assertRaises(MakeProbeError):
            session._source_phase_images(observed)
        with self.case.session() as session:
            with self.assertRaises(MakeProbeError):
                session.command(Command(("/usr/bin/python3", "-c",
                    "import os;os.mkdir('/work/source');os.rename('/work/source','/work/destination')")))
        self.fixture.assert_clean(session)

    def test_exact_runtime_native_owner_and_existing_case_cover_directory_handoffs(self):
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
                ("scripts/validation_ownership/source_directories.py", "paths.ownership"),
                ("scripts/validation_ownership/tests/test_source_directories.py", "paths.ownership-native"),
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
            (("python3", "-m", "unittest", __name__, "-v"), "scripts/validation_ownership/tests/test_source_directories.py"),
            {(tuple(shlex.split(item["command"])), item["evidence"]) for item in case["automation"]},
        )
        selected = {
            **registry, "features": [{**feature, "required_cases": [case["id"]]}], "cases": [case],
            "coverage": {**registry["coverage"], "expected_feature_ids": [feature["id"]]},
        }
        with patch.object(check_docs, "parse_test_case_registry", return_value=(selected, [])):
            self.assertEqual(check_docs.check_test_case_registry(str(root)), [])

    def test_original_missing_parent_message_pipeline_uses_real_prewatched_handoffs(self):
        from scripts.validation_ownership.tests import test_header_pipeline as header_tests

        case = header_tests.ArmHeaderPipelineTests(
            "test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps",
        )
        case.setUp()
        captured = []
        original = make_probe.ProbeSession._make
        def observe(session, *args, **kwargs):
            kwargs["observe_source_journal"] = True
            kwargs["source_journal_mode"] = source_directories.MODE
            result = original(session, *args, **kwargs)
            captured.append(result)
            return result
        try:
            with patch.object(make_probe.ProbeSession, "_make", observe):
                case.test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps()
            self.assertEqual(len(captured), 1)
            result = captured[0]
            self.assertTrue(result.source_journal["closed"])
            self.assertNotIn("build", result.source_phases["entries"][0]["image"]["directories"])
            self.assertEqual([row["path"] for row in result.source_journal["directories"]],
                             ["build", "build/native", "build/native/src"])
            self.assertEqual([row["job"]["command_line"] for row in result.semantics["native_dispatches"]],
                             [1, 1, 2, 3, 4, 5])
            self.assertEqual([row["paths"][0] for row in result.source_journal["transactions"]
                              if row["operation"] == "cleanup-directory"],
                             ["build/native/src", "build/native", "build"])
        finally:
            case.tearDown()

    def test_atomic_existing_destination_refusal_does_not_retire_the_foreign_directory(self):
        for terminal in (False, True):
            with self.subTest(terminal_failure=terminal), self.case.session() as session:
                send, raced = ProducerChannel.send, []
                def collide(channel, data):
                    value = json.loads(data)
                    if value.get("kind") == "directory-ready" and value["phase"] == "watch" and not raced:
                        path = session.tree / value["path"]
                        path.mkdir()
                        raced.append((path, path.stat().st_ino))
                        if terminal:
                            session.budget.reject("injected terminal failure after directory race")
                    return send(channel, data)
                with patch.object(ProducerChannel, "send", collide):
                    with self.assertRaisesRegex(MakeProbeError, "atomic prewatched directory install refused|injected terminal failure"):
                        session.make(
                            "all", commands=self.case.commands(session),
                            observe_source_journal=True, source_journal_mode=source_directories.MODE,
                        )
                self.assertEqual(len(raced), 1)
                path, inode = raced[0]
                self.assertTrue(path.is_dir(), "cleanup removed a directory that the native handoff never owned")
                self.assertEqual(path.stat().st_ino, inode)
            self.fixture.assert_clean(session)


if __name__ == "__main__":
    unittest.main()
