"""Actual native effect origins, not complete source-phase authority."""

import copy
import hashlib
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from scripts.validation_ownership import make_probe
from scripts.validation_ownership import source_effects
from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.make_probe import Command
from scripts.validation_ownership.producer_channel import ProducerChannel
from scripts.validation_ownership.producer_channel import ChannelError
from scripts.validation_ownership.producer_channel import publication_identity
from scripts.validation_ownership.producer_channel import publication_file_versions
from scripts.validation_ownership.tests.test_read_epochs import CompletionTraceDataApiTests
from scripts.validation_ownership.tests import test_foundation as foundation
from scripts.validation_ownership.tests import test_source_phases as phases


class CompletionEffectDataApiTests(unittest.TestCase):
    """Original effect/source clocks stay independent of completed DATA."""

    def test_new_trace_keeps_effect_context_and_live_observer_version_checks(self):
        trace = CompletionTraceDataApiTests.trace_data()
        contexts = source_effects._read_contexts(trace)
        self.assertEqual(contexts[6], {"exec": 1, "pass": 1, "stage": "source-read", "visit": 1})
        self.assertEqual(contexts[9], contexts[6])
        self.assertEqual(contexts[11], {"exec": 1, "pass": 1, "stage": "after-read", "visit": None})
        journal = {"version": 1, "scope": trace["scope"], "events": [], "closed": True}
        self.assertIs(source_effects.validate_journal(
            journal, trace, dispatches=[], requests=[], publications=[], count_limit=32, file_limit=1024,
        ), journal)
        for version in (2, 3, 4):
            policy = SimpleNamespace(
                config={"producer_scope": trace["scope"]},
                read_trace=SimpleNamespace(version=version),
            )
            observer = source_effects.NativeSourceEffects(policy, {"version": 1, "scope": trace["scope"]})
            self.assertIs(observer.trace, policy.read_trace)
        for version in (1, True, 5):
            policy.read_trace.version = version
            with self.subTest(version=version), self.assertRaises(ChannelError):
                source_effects.NativeSourceEffects(policy, {"version": 1, "scope": trace["scope"]})

    def test_receipt_does_not_create_source_effect_or_publish_a_producer(self):
        trace = CompletionTraceDataApiTests.trace_data()
        journal = {"version": 1, "scope": trace["scope"], "events": [], "closed": True}
        for defect in ("dispatch", "publication", "origin", "version"):
            changed = copy.deepcopy(trace)
            bad = copy.deepcopy(journal)
            dispatches, publications = [], []
            if defect == "dispatch":
                dispatches = [{"sequence": 1, "executable": "/usr/bin/forbidden"}]
            elif defect == "publication":
                publications = [{}]
            elif defect == "origin":
                bad["events"] = [{"seq": 1, "kind": "origin", "trace_seq": 6}]
            else:
                changed["version"] = 5
            with self.subTest(defect=defect), self.assertRaises(ChannelError):
                source_effects.validate_journal(
                    bad, changed, dispatches=dispatches, requests=[], publications=publications,
                    count_limit=32, file_limit=1024,
                )


class PublicationVersionTests(unittest.TestCase):
    def test_actual_transferred_file_and_retired_versions_have_exact_custody(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data = b"HEADERS := include/global.h\n"
            temporary = root / "target.headers.d.tmp2"
            temporary.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            owner = "a" * 64
            target = "build/target.headers.d"
            before = [
                target + ".tmp2", owner, temporary.stat().st_mode & 0o777, len(data), digest,
                list(publication_identity(temporary.stat())),
            ]
            final = temporary.rename(root / "target.headers.d")
            after = [target, *before[1:5], list(publication_identity(final.stat()))]
            transfer = {
                "kind": "filesystem", "slot": 0, "owner": owner, "operation": "transfer",
                "path": target, "source": target + ".tmp2", "before": before, "after": after,
                "directories": [],
            }
            generic = {
                "slot": 0, "owner": owner, "policy": "replace",
                "outputs": [{
                    "path": before[0], "mode": before[2], "size": before[3], "sha256": before[4],
                    "effect": "created", "identity": before[5],
                }],
            }
            directory_effect = {
                "kind": "filesystem", "slot": 0, "owner": owner, "operation": "directory",
                "path": "build", "source": None, "before": None, "after": None, "directories": [],
            }
            self.assertEqual(
                publication_file_versions(generic, count_limit=32, file_limit=1024), generic["outputs"],
            )
            self.assertEqual(publication_file_versions(transfer, count_limit=32, file_limit=1024), [{
                "path": target, "mode": after[2], "size": after[3], "sha256": after[4],
                "identity": after[5],
            }])
            self.assertEqual(publication_file_versions(directory_effect, count_limit=32, file_limit=1024), [])
            for kind, original in (("generic", generic), ("transfer", transfer)):
                for defect in ("unknown", "owner", "identity", "size", "count", "path"):
                    with self.subTest(kind=kind, defect=defect):
                        bad = copy.deepcopy(original)
                        count_limit, file_limit = 32, 1024
                        if defect == "unknown":
                            bad["unknown"] = True
                        elif defect == "owner":
                            bad["owner"] = "b" * 64 if kind == "transfer" else "invalid"
                        elif defect == "identity":
                            identity = bad["after"][5] if kind == "transfer" else bad["outputs"][0]["identity"]
                            identity[3] += 1
                        elif defect == "size":
                            file_limit = len(data) - 1
                        elif defect == "count":
                            count_limit = 0
                        elif kind == "transfer":
                            bad["after"][0] = "build/foreign.headers.d"
                        else:
                            bad["outputs"][0]["path"] = ""
                        with self.assertRaises(ChannelError):
                            publication_file_versions(bad, count_limit=count_limit, file_limit=file_limit)
            observer = object.__new__(source_effects.NativeSourceEffects)
            observer.policy = SimpleNamespace(config={"observation_count": 32, "file_limit": 1024})
            observer.trace = SimpleNamespace(events=[{}] * 10)
            observer.events = [
                {"kind": "publication", "seq": seq, "trace_seq": seq, "producer": seq,
                 "confirmation": confirmation}
                for seq, confirmation in enumerate((generic, directory_effect, transfer), 1)
            ]
            self.assertEqual(
                observer.publication_entry(target, after[5]),
                {"kind": "publication", "event": 3, "producer": 3, "slot": 0, "owner": owner},
            )
            self.assertIsNone(observer.publication_entry(before[0], before[5]))
            self.assertIsNone(observer.publication_entry("build/foreign.mk", after[5]))
            changed = list(after[5])
            changed[1] += 1
            self.assertIsNone(observer.publication_entry(target, changed))
            retired = root / "target.headers.d.tmp"
            retired.write_bytes(data)
            retired_record = [
                target + ".tmp", owner, retired.stat().st_mode & 0o777, len(data), digest,
                list(publication_identity(retired.stat())),
            ]
            retired.unlink()
            retire = {
                "kind": "filesystem", "slot": 0, "owner": owner, "operation": "retire",
                "path": retired_record[0], "source": None, "before": retired_record, "after": None,
                "directories": [],
            }
            self.assertEqual(publication_file_versions(retire, count_limit=32, file_limit=1024), [])
            old = copy.deepcopy(generic)
            old["outputs"][0].update(path=retired_record[0], identity=retired_record[5])
            for confirmation in (old, retire):
                seq = len(observer.events) + 1
                observer.events.append({
                    "kind": "publication", "seq": seq, "trace_seq": seq, "producer": seq,
                    "confirmation": confirmation,
                })
            self.assertIsNone(observer.publication_entry(retired_record[0], retired_record[5]))
            self.assertIsNotNone(observer.publication_entry(target, after[5]))
            replacement = root / "replacement"
            replacement.write_bytes(b"HEADERS := include/bmunit.h\n")
            replacement.rename(final)
            newer = copy.deepcopy(generic)
            newer["owner"] = "b" * 64
            newer["outputs"] = [{
                "path": target, "mode": final.stat().st_mode & 0o777, "size": final.stat().st_size,
                "sha256": hashlib.sha256(final.read_bytes()).hexdigest(), "effect": "replaced",
                "identity": list(publication_identity(final.stat())),
            }]
            seq = len(observer.events) + 1
            observer.events.append({
                "kind": "publication", "seq": seq, "trace_seq": seq, "producer": seq,
                "confirmation": newer,
            })
            self.assertIsNone(observer.publication_entry(target, after[5]))
            self.assertEqual(observer.publication_entry(target, newer["outputs"][0]["identity"])["owner"], newer["owner"])
            earlier = source_effects._publication_version_at(
                observer.events, target, before_seq=seq, count_limit=32, file_limit=1024,
            )
            self.assertEqual(earlier[1]["identity"], after[5])


class SourceEffectTests(unittest.TestCase):
    def setUp(self):
        self.case = phases.SourcePhaseTests()
        self.case.setUp()
        self.fixture = self.case.fixture

    def tearDown(self):
        self.case.tearDown()

    def test_remake_origin_precedes_reexec_and_cannot_borrow_acknowledgement_phase(self):
        with self.case.session() as session:
            result = session.make(
                "all", variables=("FILES", "HIDDEN"), commands=self.case.commands(session),
                observe_source_phases=True,
            )
            self.assertEqual(result.semantics["native_dispatches"][0]["environment"]["HIDDEN"], "secret")
            self.assertEqual(result.semantics["domains"]["HIDDEN"]["origin"], "undefined")
            self.assertEqual([row["pass"] for row in result.source_phases["entries"]], [1, 2])
            origins = [row for row in result.source_effects["events"] if row["kind"] == "origin"]
            self.assertEqual(len(origins), 1)
            self.assertEqual(
                (origins[0]["exec"], origins[0]["pass"], origins[0]["stage"], origins[0]["visit"]),
                (1, 1, "after-read", None),
            )
            self.assertTrue(origins[0]["rebuilding_makefiles"])
            publications = [row for row in result.source_effects["events"] if row["kind"] == "publication"]
            self.assertEqual(len(publications), 1)
            self.assertEqual(publications[0]["origin"], origins[0]["origin"])
            self.assertEqual(
                {row["path"]: row["effect"] for row in publications[0]["confirmation"]["outputs"]},
                {"src/new.c": "created", "build/remade.mk": "created"},
            )
            second_exec = next(row["seq"] for row in result.read_trace["events"]
                               if row["kind"] == "exec" and row["exec"] == 2)
            self.assertLess(publications[0]["trace_seq"], second_exec)
            token = session._original_namespace(result, target="all", makefile="Makefile")
            with self.assertRaises(MakeProbeError):
                session._original_wildcard(token, "src/*.c")
        self.fixture.assert_clean(session)

    def test_literal_and_computed_include_file_builtin_and_eval_keep_actual_read_visits(self):
        for name, include in (("child.mk", "include child.mk"), ("nested/renamed.mk", "include ${SELECTED}")):
            self.fixture.add("Makefile", (
                ".DEFAULT_GOAL := all\n"
                "COPY := $(file <Makefile)\n"
                "SELECTED := " + name + "\n" + include + "\nall: ;\n"
            ))
            self.fixture.add(name, (
                "CHECK := $(shell printf visible)\n"
                "$(eval EVALUATED := $(shell printf evaluated))\n"
                "CALLED = $(shell printf called)\n"
                "CALLED_VALUE := $(call CALLED)\n"
            ))
            commands = {f"printf {value}": Command(("/usr/bin/printf", value))
                        for value in ("visible", "evaluated", "called")}
            with self.subTest(source=name), self.case.session() as session:
                result = session.make(
                    "all", variables=("CHECK", "EVALUATED", "CALLED_VALUE"), commands=commands,
                    observe_source_phases=True,
                )
                self.assertEqual([result.semantics["domains"][key]["value"]
                                  for key in ("CHECK", "EVALUATED", "CALLED_VALUE")],
                                 ["visible", "evaluated", "called"])
                visits = [row for row in result.read_trace["events"] if row["kind"] == "source-entry"]
                self.assertEqual([row["name"] for row in visits], ["Makefile", name])
                self.assertTrue(any(row["kind"] == "other-open" and row["name"] == "Makefile"
                                    for row in result.read_trace["events"]))
                origins = [row for row in result.source_effects["events"] if row["kind"] == "origin"]
                self.assertEqual(len(origins), 3)
                self.assertEqual({(row["exec"], row["pass"], row["stage"], row["visit"]) for row in origins},
                                 {(1, 1, "source-read", visits[-1]["visit"])})
                self.assertEqual([row["origin"] for row in origins], [1, 2, 3])
                self.assertTrue(all(not row["rebuilding_makefiles"] for row in origins))
            self.fixture.assert_clean(session)

    def test_deferred_expansion_and_identical_recipe_dispatches_do_not_invent_read_epochs_or_effects(self):
        self.fixture.add("Makefile", (
            ".DEFAULT_GOAL := all\n.PHONY: all first second\n"
            "DELAYED = $(shell printf delayed)\n"
            "all: first second\n\tprintf '%s' '$(DELAYED)'\n"
            "first:\n\tprintf identical\nsecond:\n\tprintf identical\n"
        ))
        with self.case.session() as session:
            result = session.make(
                "all", commands={"printf delayed": Command(("/usr/bin/printf", "delayed"))},
                observe_source_phases=True,
            )
            dispatches = result.semantics["native_dispatches"]
            identical = [row for row in dispatches if row["job"]["target"] in {"first", "second"}]
            self.assertEqual(len(identical), 2)
            self.assertEqual(identical[0]["arguments"], identical[1]["arguments"])
            origins = [row for row in result.source_effects["events"] if row["kind"] == "origin"]
            self.assertEqual(len(origins), len(dispatches))
            self.assertEqual({(row["exec"], row["pass"], row["stage"], row["visit"]) for row in origins},
                             {(1, 1, "after-read", None)})
            producers = [row for row in result.source_effects["events"] if row["kind"] == "producer"]
            publications = [row for row in result.source_effects["events"] if row["kind"] == "publication"]
            self.assertEqual(len(producers), 1)
            self.assertEqual(len(publications), 1)
            self.assertEqual(publications[0]["confirmation"]["outputs"], [])
            self.assertEqual(result.generated, ())
        self.fixture.assert_clean(session)

    def test_real_message_arm_filter_and_each_owned_effect_keep_native_origins(self):
        from scripts.validation_ownership.tests import test_header_pipeline as header_tests

        case = header_tests.ArmHeaderPipelineTests(
            "test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps",
        )
        case.setUp()
        captured = []
        original = make_probe.ProbeSession._make
        def observe(session, *args, **kwargs):
            kwargs["observe_source_phases"] = True
            result = original(session, *args, **kwargs)
            captured.append(result)
            return result
        try:
            with patch.object(make_probe.ProbeSession, "_make", observe):
                case.test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps()
            self.assertEqual(len(captured), 1)
            result = captured[0]
            origins = [row for row in result.source_effects["events"] if row["kind"] == "origin"]
            self.assertEqual(len(origins), 6)
            self.assertEqual({(row["exec"], row["pass"], row["stage"], row["visit"]) for row in origins},
                             {(1, 1, "after-read", None)})
            self.assertTrue(all(row["rebuilding_makefiles"] for row in origins))
            publications = [row["confirmation"] for row in result.source_effects["events"]
                            if row["kind"] == "publication"]
            self.assertEqual(len(publications), 6)
            self.assertEqual([row.get("operation", "files") for row in publications],
                             ["files", "directory", "files", "files", "retire", "transfer"])
            self.assertEqual(publications[0]["outputs"][0]["path"], "src/msg_data.c")
            self.assertGreater(publications[0]["outputs"][0]["size"], 3_000_000)
            self.assertEqual(publications[-1]["path"], case.target)
            self.assertEqual([row["pass"] for row in result.source_phases["entries"]], [1, 2])
            self.assertEqual(result.read_trace["version"], 4)
            opened = [
                row for row in result.read_trace["events"]
                if row["kind"] == "source-open" and row["path"] == case.target and row["result"] >= 0
            ]
            self.assertEqual(len(opened), 1)
            self.assertEqual(opened[0]["identity"], publications[-1]["after"][5])
            self.assertEqual(
                (opened[0]["custody"]["kind"], opened[0]["custody"]["slot"], opened[0]["custody"]["owner"]),
                ("publication", publications[-1]["slot"], publications[-1]["owner"]),
            )
        finally:
            case.tearDown()

    def test_nested_adoption_does_not_refresh_the_parent_origin(self):
        from scripts.validation_ownership.tests import test_producer as producer_tests

        case = producer_tests.ProducerTests(
            "test_nested_generated_query_keeps_one_live_view_until_outer_completion",
        )
        case.setUp()
        captured = []
        original = make_probe.ProbeSession._make
        def observe(session, *args, **kwargs):
            kwargs["observe_source_phases"] = True
            result = original(session, *args, **kwargs)
            captured.append(result)
            return result
        try:
            with patch.object(make_probe.ProbeSession, "_make", observe):
                case.test_nested_generated_query_keeps_one_live_view_until_outer_completion()
            self.assertEqual(len(captured), 2)
            child, parent = captured
            self.assertNotEqual(child.source_effects["scope"], parent.source_effects["scope"])
            adoption, = [row for row in parent.source_effects["events"] if row["kind"] == "adoption"]
            origin, = [row for row in parent.source_effects["events"]
                       if row["kind"] == "origin" and row["origin"] == adoption["origin"]]
            self.assertEqual((origin["exec"], origin["pass"], origin["stage"]), (1, 1, "source-read"))
            self.assertEqual({row[0] for row in adoption["records"]},
                             {"data/nested/value", "inner.generated.mk"})
            self.assertEqual([row["pass"] for row in child.source_phases["entries"]], [1, 2])
            self.assertEqual([row["pass"] for row in parent.source_phases["entries"]], [1])
        finally:
            case.tearDown()

    def test_live_origin_packet_cannot_change_scope_pass_or_actual_binding(self):
        for defect in ("scope", "origin", "phase", "sequence", "missing"):
            with self.subTest(defect=defect), self.case.session() as session:
                receive, changed = ProducerChannel.receive, []
                def corrupt(channel):
                    payload = receive(channel)
                    if payload is None:
                        return None
                    value = json.loads(payload)
                    if value.get("kind") == "request" and "source_origin" in value and not changed:
                        changed.append(defect)
                        context = value["source_origin"]
                        if defect == "scope":
                            context["scope"] += "-foreign"
                        elif defect == "origin":
                            context["origin"] += 1
                        elif defect == "phase":
                            context["pass"] = context["exec"] = 2
                        elif defect == "sequence":
                            context["producer"] += 1
                        else:
                            del value["source_origin"]
                    return json.dumps(value).encode()
                with patch.object(ProducerChannel, "receive", corrupt), self.assertRaises(MakeProbeError):
                    session.make("all", commands=self.case.commands(session), observe_source_phases=True)
                self.assertEqual(changed, [defect])
            self.fixture.assert_clean(session)

    def test_actual_native_report_removal_order_phase_and_publication_corruption_reject(self):
        for defect in ("missing", "scope", "phase", "order", "publication", "omission", "closed"):
            with self.subTest(defect=defect), self.case.session() as session:
                read, changed = session.budget.read_bytes, []
                def corrupt(path, category):
                    data = read(path, category)
                    if category != "control" or not Path(path).name.startswith("report-"):
                        return data
                    report = json.loads(data)
                    effects = report.get("source_effects")
                    if effects is None:
                        return data
                    changed.append(defect)
                    if defect == "missing":
                        del report["source_effects"]
                    elif defect == "scope":
                        effects["scope"] += "-foreign"
                    elif defect == "phase":
                        effects["events"][0]["stage"] = "source-read"
                    elif defect == "order":
                        effects["events"][:2] = reversed(effects["events"][:2])
                        for index, event in enumerate(effects["events"], 1):
                            event["seq"] = index
                    elif defect == "publication":
                        row = next(row for row in effects["events"] if row["kind"] == "publication")
                        row["confirmation"]["outputs"][0]["effect"] = "retained"
                    elif defect == "omission":
                        effects["events"] = [row for row in effects["events"] if row["kind"] != "publication"]
                    else:
                        effects["closed"] = False
                    return json.dumps(report).encode()
                with patch.object(session.budget, "read_bytes", corrupt), self.assertRaises(MakeProbeError):
                    session.make("all", commands=self.case.commands(session), observe_source_phases=True)
                self.assertEqual(changed, [defect])
            self.fixture.assert_clean(session)

    def test_default_read_only_and_changed_retained_sidecar_keep_existing_lifetime_boundary(self):
        for options in ({}, {"observe_read_epochs": True}):
            with self.subTest(options=options), self.case.session() as session:
                result = session.make("all", commands=self.case.commands(session), **options)
                self.assertIsNone(result.source_effects)
            self.fixture.assert_clean(session)
        with self.case.session() as session:
            result = session.make("all", commands=self.case.commands(session), observe_source_phases=True)
            saved = copy.deepcopy(result.source_effects)
            result.source_effects["events"].clear()
            with self.assertRaises(MakeProbeError):
                session._source_phase_images(result)
            result.source_effects.clear()
            result.source_effects.update(saved)
            self.assertEqual(len(session._source_phase_images(result)), 2)
        self.fixture.assert_clean(session)
        with self.assertRaises(MakeProbeError):
            session._source_phase_images(result)

    def test_exact_runtime_native_owner_and_case_admit_no_unregistered_neighbor(self):
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
                ("scripts/validation_ownership/source_effects.py", "paths.ownership"),
                ("scripts/validation_ownership/tests/test_source_effects.py", "paths.ownership-native"),
            ):
                rule, = [row for row in graph["path_rules"] if reporter._path_rule_matches(row, path, set())]
                self.assertEqual(rule["id"], expected)
                self.assertEqual(reporter._path_admission(path, rule, sources), "exact-ownership-rule")
                if "/tests/" not in path:
                    self.assertIn(path, ci_verifier.TRUSTED_RUNTIME_PATHS)
                else:
                    neighbor = path.replace(".py", "_unregistered.py")
                    self.assertFalse(reporter._path_rule_matches(rule, neighbor, set()))
                    with self.assertRaises(reporter.OwnershipError):
                        reporter._path_admission(neighbor, rule, sources)
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
            (("python3", "-m", "unittest", __name__, "-v"), "scripts/validation_ownership/tests/test_source_effects.py"),
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
