"""Original source-phase controls; a capture alone grants no wildcard exception."""

import copy
from dataclasses import replace
import json
import shlex
import unittest
from unittest.mock import patch

from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.make_probe import Command
from scripts.validation_ownership.producer_channel import ProducerChannel
from scripts.validation_ownership.tests import test_foundation as foundation


class SourcePhaseTests(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.FoundationTests()
        self.fixture.setUp()
        self.fixture.add("src/keep.h", "/* original non-C namespace witness */\n")
        self.fixture.add("Makefile", (
            ".DEFAULT_GOAL := all\n"
            "FILES := $(wildcard src/*.c)\n"
            "ifeq ($(FILES),)\nHIDDEN ?= secret\nexport HIDDEN\nendif\n"
            "build/remade.mk:\n\tpython3 writer.py\n"
            "include build/remade.mk\nall: ;\n"
        ))
        self.fixture.add("writer.py", (
            "import os\nfrom pathlib import Path\n"
            "source=Path('/work/src/new.c');source.parent.mkdir(parents=True,exist_ok=True)\n"
            "source.write_text('/* genuine source creation */\\n')\n"
            "include=Path('/work/build/remade.mk');include.parent.mkdir(parents=True,exist_ok=True)\n"
            "include.write_text('# genuine included source\\n')\n"
            "print(os.environ.get('HIDDEN','<undefined>'))\n"
        ))

    def tearDown(self):
        self.fixture.tearDown()

    def session(self):
        return self.fixture.session(runtime_files=("/usr/include/build",), seconds=20)

    def commands(self, session):
        return {"python3 writer.py": session._native_context_command(Command(
            ("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",),
            outputs=("src/new.c", "build/remade.mk"),
        ))}

    def test_native_first_pass_hidden_cannot_be_erased_by_final_namespace(self):
        with self.session() as session:
            execute = session.command
            outputs = []
            def record(command):
                result = execute(command)
                outputs.append(result)
                return result
            with patch.object(session, "command", record):
                result = session.make(
                    "all", variables=("FILES", "HIDDEN"), commands=self.commands(session),
                    observe_read_epochs=True,
                )
            self.assertEqual([output.stdout for output in outputs], [b"secret\n"])
            self.assertEqual(result.semantics["domains"]["FILES"]["value"], "src/new.c")
            self.assertEqual(result.semantics["domains"]["HIDDEN"]["origin"], "undefined")
            self.assertEqual(result.semantics["native_dispatches"][0]["environment"]["HIDDEN"], "secret")
            self.assertTrue(result.semantics["native_dispatches"][0]["rebuilding_makefiles"])
            self.assertEqual(len([event for event in result.read_trace["events"]
                                  if event["kind"] == "pass-entry"]), 2)
            token = session._original_namespace(result, target="all", makefile="Makefile")
            with self.assertRaises(MakeProbeError):
                session._original_wildcard(token, "src/*.c")
        self.fixture.assert_clean(session)

    def test_actual_entry_images_precede_each_original_read_pass(self):
        with self.session() as session:
            result = session.make(
                "all", variables=("FILES", "HIDDEN"), commands=self.commands(session),
                observe_source_phases=True,
            )
            self.assertEqual(result.read_trace["version"], 2)
            phases = result.source_phases
            self.assertTrue(phases["closed"])
            self.assertEqual([entry["pass"] for entry in phases["entries"]], [1, 2])
            first, second = phases["entries"]
            self.assertEqual({name for name, identity in first["image"]["members"]["src"]}, {"keep.h"})
            self.assertEqual({name for name, identity in second["image"]["members"]["src"]}, {"keep.h", "new.c"})
            self.assertEqual(len(session._source_phase_images(result)), 2)
            self.assertEqual(result.semantics["domains"]["HIDDEN"]["origin"], "undefined")
            self.assertEqual(result.semantics["native_dispatches"][0]["environment"]["HIDDEN"], "secret")
            events = result.read_trace["events"]
            for index, event in enumerate(events):
                if event["kind"] == "pass-entry":
                    self.assertEqual(events[index + 1]["kind"], "entry-image")
                    self.assertEqual(events[index + 1]["pass"], event["pass"])
            token = session._original_namespace(result, target="all", makefile="Makefile")
            with self.assertRaises(MakeProbeError):
                session._original_wildcard(token, "src/*.c")
        self.fixture.assert_clean(session)
        self.assertFalse(session._source_phase_records)

    def test_entry_barrier_request_and_acknowledgement_cannot_be_forged(self):
        for defect in ("request-scope", "request-pass", "request-input", "reply-barrier", "reply-input", "reply-image"):
            with self.subTest(defect=defect), self.session() as session:
                receive, send, changed = ProducerChannel.receive, ProducerChannel.send, []
                def alter_receive(channel):
                    payload = receive(channel)
                    if payload is None:
                        return None
                    value = json.loads(payload)
                    if value.get("kind") == "read-barrier" and defect.startswith("request") and not changed:
                        changed.append(defect)
                        if defect == "request-scope":
                            value["scope"] += "-foreign"
                        elif defect == "request-pass":
                            value["pass"] += 1
                        else:
                            value["input_sha256"] = "0" * 64
                    return json.dumps(value).encode()
                def alter_send(channel, payload):
                    value = json.loads(payload)
                    if value.get("kind") == "read-resume" and defect.startswith("reply") and not changed:
                        changed.append(defect)
                        if defect == "reply-barrier":
                            value["barrier"] += 1
                        elif defect == "reply-input":
                            value["input_sha256"] = "0" * 64
                        else:
                            value["image_sha256"] = "0" * 64
                    return send(channel, json.dumps(value).encode())
                with patch.object(ProducerChannel, "receive", alter_receive), patch.object(ProducerChannel, "send", alter_send):
                    with self.assertRaises(MakeProbeError):
                        session.make("all", commands=self.commands(session), observe_source_phases=True)
                self.assertEqual(changed, [defect])
            self.fixture.assert_clean(session)

    def test_copied_changed_foreign_and_expired_observations_cannot_reuse_entry_images(self):
        with self.session() as session:
            result = session.make("all", commands=self.commands(session), observe_source_phases=True)
            self.assertEqual(len(session._source_phase_images(result)), 2)
            with self.assertRaises(MakeProbeError):
                session._source_phase_images(replace(result))
            saved = copy.deepcopy(result.source_phases)
            result.source_phases["entries"][0]["image"]["members"]["src"] = ()
            with self.assertRaises(MakeProbeError):
                session._source_phase_images(result)
            result.source_phases.clear()
            result.source_phases.update(saved)
            self.assertEqual(len(session._source_phase_images(result)), 2)
        self.fixture.assert_clean(session)
        with self.session() as other:
            with self.assertRaises(MakeProbeError):
                other._source_phase_images(result)
        self.fixture.assert_clean(other)
        with self.assertRaises(MakeProbeError):
            session._source_phase_images(result)

    def test_real_default_text_header_pipeline_captures_original_source_membership(self):
        from scripts.validation_ownership.tests import test_header_pipeline as header_tests
        from scripts.validation_ownership.make_probe import ProbeSession

        case = header_tests.ArmHeaderPipelineTests(
            "test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps",
        )
        case.setUp()
        captured = []
        original = ProbeSession._make
        def observe(session, *args, **kwargs):
            kwargs["observe_source_phases"] = True
            result = original(session, *args, **kwargs)
            captured.append(result)
            return result
        try:
            with patch.object(ProbeSession, "_make", observe):
                case.test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps()
            self.assertEqual(len(captured), 1)
            first, second = captured[0].source_phases["entries"]
            self.assertNotIn("msg_data.c", {name for name, identity in first["image"]["members"]["src"]})
            self.assertIn("msg_data.c", {name for name, identity in second["image"]["members"]["src"]})
            self.assertEqual([first["exec"], second["exec"]], [1, 2])
        finally:
            case.tearDown()

    def test_entry_image_runtime_and_case_keep_the_existing_exact_owner(self):
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
                ("scripts/validation_ownership/source_phases.py", "paths.ownership"),
                ("scripts/validation_ownership/tests/test_source_phases.py", "paths.ownership-native"),
            ):
                rule, = [rule for rule in graph["path_rules"] if reporter._path_rule_matches(rule, path, set())]
                self.assertEqual(rule["id"], expected)
                self.assertEqual(reporter._path_admission(path, rule, sources), "exact-ownership-rule")
                if "/tests/" not in path:
                    self.assertIn(path, ci_verifier.TRUSTED_RUNTIME_PATHS)
                else:
                    without = {**rule, "include": [item for item in rule["include"]
                                                  if item != {"kind": "exact", "path": path}]}
                    with self.assertRaises(reporter.OwnershipError):
                        reporter._path_admission(path, without, sources)
        finally:
            budget.close()
        registry, errors = check_docs.parse_test_case_registry(str(root))
        self.assertEqual(errors, [])
        case, = [item for item in registry["cases"] if item["id"] == "TC-WORKFLOW-GATE-OWNERSHIP-001"]
        feature, = [item for item in registry["features"] if item["id"] == case["feature_id"]]
        selected = {
            **registry, "features": [{**feature, "required_cases": [case["id"]]}], "cases": [case],
            "coverage": {**registry["coverage"], "expected_feature_ids": [feature["id"]]},
        }
        self.assertIn(
            (("python3", "-m", "unittest", __name__, "-v"), "scripts/validation_ownership/tests/test_source_phases.py"),
            {(tuple(shlex.split(item["command"])), item["evidence"]) for item in case["automation"]},
        )
        with patch.object(check_docs, "parse_test_case_registry", return_value=(selected, [])):
            self.assertEqual(check_docs.check_test_case_registry(str(root)), [])


if __name__ == "__main__":
    unittest.main()
