"""Every-pass obligations from actual native source/image/mutation history."""

import copy
from dataclasses import replace
import shlex
import unittest
from unittest.mock import patch

from scripts.validation_ownership import graph_probe, make_probe, phase_census, read_epochs, source_directories
from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.tests import test_source_phases as phases
from scripts.validation_ownership.tests import test_foundation as foundation


class PhaseCensusTests(unittest.TestCase):
    def setUp(self):
        self.case = phases.SourcePhaseTests()
        self.case.setUp()
        self.fixture = self.case.fixture

    def tearDown(self):
        self.case.tearDown()

    def native(self, session):
        return session.make(
            "all", variables=("HIDDEN", "FILES"), commands=self.case.commands(session),
            observe_source_journal=True, source_journal_mode=source_directories.MODE,
        )

    def test_first_pass_default_survives_actual_final_namespace_and_definition_change(self):
        with self.case.session() as session:
            observed = self.native(session)
            self.assertEqual(observed.semantics["domains"]["HIDDEN"]["origin"], "undefined")
            self.assertEqual(observed.semantics["native_dispatches"][0]["environment"]["HIDDEN"], "secret")
            usage, sources, streams, individual = phase_census.analyze(
                session, observed, "all", (), self.case.commands(session),
            )
            self.assertEqual(len(individual), 2)
            self.assertIn("HIDDEN", individual[0]["defaults"])
            self.assertNotIn("HIDDEN", individual[1]["defaults"])
            self.assertIn("HIDDEN", usage["defaults"])
            self.assertEqual(streams[0].failed_sources, ("build/remade.mk",))
            self.assertEqual(streams[1].failed_sources, ())
            self.assertIn("build/remade.mk", sources)
        self.fixture.assert_clean(session)

    def test_complete_small_phase_planner_rejects_the_unsealed_first_pass_default(self):
        with self.case.session() as session:
            with patch.object(graph_probe, "MakeCommands", lambda owner, contracts: self.case.commands(owner)):
                with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults.*HIDDEN"):
                    graph_probe.run_probe(
                        session.loader, {"all"}, {}, {}, session=session, source_phases=True,
                    )
        self.fixture.assert_clean(session)

    def test_removing_only_every_pass_union_recovers_the_small_planner_false_admission(self):
        with self.case.session() as session:
            with patch.object(graph_probe, "MakeCommands", lambda owner, contracts: self.case.commands(owner)):
                with patch.object(phase_census, "union_usages", side_effect=lambda usages: usages[-1]):
                    result = graph_probe.run_probe(
                        session.loader, {"all"}, {}, {}, session=session, source_phases=True,
                    )
            self.assertEqual(result["all"]["variable_census"]["defaults"], [])
            native = result["all"]["record"]["variants"][0]["record"]["native_dispatches"]
            self.assertEqual(native[0]["environment"]["HIDDEN"], "secret")
            self.assertTrue(native[0]["rebuilding_makefiles"])
        self.fixture.assert_clean(session)

    def positive_source(self, extra="", recipe="all: ;\n"):
        self.fixture.add("Makefile", (
            ".DEFAULT_GOAL := all\nFILES := $(wildcard src/*.c)\n"
            "ifeq ($(FILES),)\nHIDDEN := secret\nelse\nHIDDEN := final\nendif\nexport HIDDEN\n"
            "build/remade.mk:\n\tpython3 writer.py\ninclude build/remade.mk\n"
            + extra + recipe
        ))

    def test_complete_small_phase_planner_accepts_closed_source_and_intersects_constants(self):
        self.positive_source()
        with self.case.session() as session:
            observed = self.native(session)
            usage, _, _, parts = phase_census.analyze(session, observed, "all", (), self.case.commands(session))
            self.assertEqual([part["read_constants"]["HIDDEN"] for part in parts], ["secret", "final"])
            self.assertNotIn("HIDDEN", usage["read_constants"])
        self.fixture.assert_clean(session)
        with self.case.session() as session:
            with patch.object(graph_probe, "MakeCommands", lambda owner, contracts: self.case.commands(owner)):
                result = graph_probe.run_probe(session.loader, {"all"}, {}, {}, session=session, source_phases=True)
            self.assertEqual(result["all"]["variable_census"]["defaults"], [])
            self.assertEqual(result["all"]["record"]["includes"], ["Makefile", "build/remade.mk"])
        self.fixture.assert_clean(session)

    def test_first_pass_graph_recipe_and_export_dependencies_survive_the_union(self):
        self.fixture.add("Makefile", (
            ".DEFAULT_GOAL := all\nFILES := $(wildcard src/*.c)\n"
            "ifeq ($(FILES),)\nHIDDEN ?= secret\n"
            "READ_NOW := $(EARLY_INPUT)\nEARLY = $(EARLY_RECIPE)\n"
            "export HIDDEN READ_NOW\nall: $(EARLY_EDGE)\n\t@printf '%s' '$(EARLY)'\n"
            "else\nall: ;\nendif\n"
            "build/remade.mk:\n\tpython3 writer.py\ninclude build/remade.mk\n"
        ))
        with self.case.session() as session:
            observed = self.native(session)
            usage, _, _, parts = phase_census.analyze(
                session, observed, "all", (), self.case.commands(session),
            )
            self.assertIn("EARLY_EDGE", parts[0]["graph"])
            self.assertIn("EARLY_RECIPE", parts[0]["recipe"])
            self.assertIn("EARLY_INPUT", parts[0]["dependencies"]["READ_NOW"])
            self.assertTrue({"EARLY_EDGE", "EARLY_RECIPE", "EARLY_INPUT"}.isdisjoint(parts[1]["all"]))
            self.assertIn("EARLY_EDGE", usage["graph"])
            self.assertIn("EARLY_RECIPE", usage["recipe"])
            self.assertIn("EARLY_INPUT", usage["dependencies"]["READ_NOW"])
            self.assertIn("READ_NOW", usage["recipe"])
            first = observed.semantics["native_dispatches"][0]
            self.assertEqual(first["environment"]["HIDDEN"], "secret")
            self.assertEqual(first["environment"]["READ_NOW"], "")
        self.fixture.assert_clean(session)

    def test_repeated_source_visits_and_file_reads_do_not_collapse_or_parse_data(self):
        self.fixture.add("Makefile", (
            ".DEFAULT_GOAL := all\nCOPY := $(file <Makefile)\nDATA := $(file <data.mk)\n"
            "include child.mk\ninclude child.mk\nall: ;\n"
        ))
        self.fixture.add("child.mk", "VISIBLE ?= child\n")
        self.fixture.add("data.mk", "HIDDEN ?= not-a-source\n")
        with self.case.session() as session:
            observed = self.native(session)
            usage, sources, streams, _ = phase_census.analyze(
                session, observed, "all", (), self.case.commands(session),
            )
            archive = session._original_source_archive(observed)
            part, = archive.passes
            self.assertEqual([visit.name for visit in part.visits], ["Makefile", "child.mk", "child.mk"])
            self.assertNotEqual(part.visits[1].number, part.visits[2].number)
            self.assertEqual(streams[0].read_sources, ("Makefile", "child.mk", "child.mk"))
            self.assertTrue({"Makefile", "data.mk"}.issubset({item.name for item in part.other_opens}))
            self.assertEqual(set(sources), {"Makefile", "child.mk"})
            self.assertEqual(usage["defaults"], {"VISIBLE"})
            self.assertEqual(observed.semantics["domains"]["HIDDEN"]["origin"], "undefined")
        self.fixture.assert_clean(session)

    def test_actual_original_command_input_precedes_override_and_unused_bodies_stay_lazy(self):
        self.fixture.add("Makefile", (
            ".DEFAULT_GOAL := all\nFIRST := $(VALUE)\noverride VALUE := final\n"
            "ifeq ($(FIRST),initial)\nEARLY ?= kept\nendif\n"
            "UNUSED = $(error unused body expanded)\nMETA := $(value UNUSED)\nall: ;\n"
        ))
        state = (("command-line", "VALUE", "initial"),)
        with self.case.session() as session:
            observed = session.make(
                "all", variables=("VALUE", "FIRST"), assignments=state,
                observe_source_journal=True, source_journal_mode=source_directories.MODE,
            )
            usage, _, streams, _ = phase_census.analyze(session, observed, "all", state, {})
            values = {row.name: row for row in session._original_source_archive(observed).passes[0].inputs[0].variables}
            self.assertEqual(values["VALUE"].value, "initial")
            self.assertEqual((values["VALUE"].flags >> 26) & 7, 4)
            self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "final")
            self.assertEqual(observed.semantics["domains"]["FIRST"]["value"], "initial")
            self.assertIn("EARLY", usage["defaults"])
            self.assertEqual(streams[0].mode_state.exact_reference("FIRST"), "initial")
            self.assertNotIn("FIRST", usage["read_constants"])
        self.fixture.assert_clean(session)

    def test_recursive_aliases_and_metadata_laziness_use_source_time_snapshots(self):
        for extra, expression, accepted in (
            ("SNAP := $(wildcard src/*.c)\nALIAS = $(SNAP)\n", "$(ALIAS)", True),
            ("LAZY = $(wildcard src/*.c)\nALIAS = $(LAZY)\n", "$(ALIAS)", False),
            ("LAZY = $(wildcard src/*.c)\n", "$(value LAZY)", True),
            ("SNAP := $(wildcard src/*.c)\nall: SNAP = $(wildcard src/*.c)\n", "$(SNAP)", False),
            ("SNAP := $(wildcard src/*.c)\n", "$(foreach SNAP,local,$(SNAP))", False),
        ):
            self.positive_source(extra, "all:\n\t@printf '%s' '" + expression + "'\n")
            with self.subTest(expression=expression, accepted=accepted), self.case.session() as session:
                observed = self.native(session)
                if accepted:
                    phase_census.analyze(session, observed, "all", (), self.case.commands(session))
                else:
                    with self.assertRaises(MakeProbeError):
                        phase_census.analyze(session, observed, "all", (), self.case.commands(session))
            self.fixture.assert_clean(session)

    def test_parse_time_and_unclosed_nonremake_mutations_do_not_get_entry_authority(self):
        self.fixture.add("Makefile", ".DEFAULT_GOAL := all\nEARLY := $(shell python3 writer.py)\nall: ;\n")
        with self.case.session() as session:
            observed = self.native(session)
            with self.assertRaisesRegex(MakeProbeError, "after-read/remake/reexec"):
                phase_census.analyze(session, observed, "all", (), self.case.commands(session))
        self.fixture.assert_clean(session)

    def test_genuine_message_header_component_interprets_the_original_raw_source_constructor(self):
        from scripts.validation_ownership.tests import test_header_pipeline as header
        from scripts.validation_ownership.graph_commands import MakeCommands

        case = header.ArmHeaderPipelineTests(
            "test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps",
        )
        case.setUp()
        original = make_probe.ProbeSession._make
        analyzed = []
        try:
            (case.fixture.root / "src/query.c").unlink()
            del case.fixture.entries["src/query.c"]
            case.fixture.add("src/.keep", "original empty C namespace\n")
            source = (case.fixture.root / "Makefile").read_text()
            source = source.replace(
                "MODERN_ALL_C_HEADER_DEPS := build/native/src/query.headers.d\n",
                "MODERN_ALL_C_SOURCES ?= $(wildcard src/*.c)\n"
                "ifeq (,$(findstring src/msg_data.c,$(MODERN_ALL_C_SOURCES)))\n"
                "MODERN_ALL_C_SOURCES += src/msg_data.c\nendif\n"
                "MODERN_ALL_C_HEADER_DEPS := $(addprefix $(MODERN_OUTPUT_DIR)/,$(MODERN_ALL_C_SOURCES:.c=.headers.d))\n",
            )
            case.fixture.add("Makefile", source)
            def interpret(session, *args, **kwargs):
                kwargs["observe_source_journal"] = True
                kwargs["source_journal_mode"] = source_directories.MODE
                observed = original(session, *args, **kwargs)
                commands = MakeCommands(session, case.contracts)
                analyzed.append(phase_census.analyze(
                    session, observed, "expansion-modern-all", (), commands,
                    external_names={"MODERN_ALL_C_SOURCES"},
                ))
                return observed
            with patch.object(make_probe.ProbeSession, "_make", interpret):
                case.test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps()
            self.assertEqual(len(analyzed), 1)
            usage, _, streams, parts = analyzed[0]
            self.assertEqual(len(parts), 2)
            self.assertIn("MODERN_ALL_C_SOURCES", usage["defaults"])
            self.assertEqual(streams[0].failed_sources, ("build/native/src/msg_data.headers.d",))
            self.assertEqual(streams[1].failed_sources, ())
        finally:
            case.tearDown()

    def test_final_nonremake_mutation_does_not_get_source_phase_authority(self):
        self.fixture.add("Makefile", ".DEFAULT_GOAL := all\nall:\n\t+python3 writer.py\n")
        with self.case.session() as session:
            observed = self.native(session)
            with self.assertRaisesRegex(MakeProbeError, "after-read/remake/reexec"):
                phase_census.analyze(session, observed, "all", (), self.case.commands(session))
        self.fixture.assert_clean(session)

    def test_replaced_include_versions_within_a_real_read_pass_remain_held(self):
        from scripts.validation_ownership.tests import test_read_epochs as epochs

        case = epochs.ReadEpochTests("test_archive_preserves_replaced_include_contents_between_real_visits")
        case.setUp()
        original = make_probe.ProbeSession._make
        analyzed = []
        def observe(session, *args, **kwargs):
            kwargs["observe_source_journal"] = True
            kwargs["source_journal_mode"] = source_directories.MODE
            observed = original(session, *args, **kwargs)
            with self.assertRaisesRegex(MakeProbeError, "after-read/remake/reexec"):
                phase_census.analyze(session, observed, "all", (), kwargs["commands"])
            analyzed.append(observed)
            return observed
        try:
            with patch.object(make_probe.ProbeSession, "_make", observe):
                case.test_archive_preserves_replaced_include_contents_between_real_visits()
            self.assertEqual(len(analyzed), 1)
        finally:
            case.tearDown()

    def test_original_input_flags_and_issued_observation_cannot_be_substituted(self):
        with self.case.session() as session:
            observed = self.native(session)
            proof = phase_census.OriginalSourceProof(session, observed)
            part = proof.archive.passes[0]
            for flags in (0x10, 0x80, 0x08, 0x40, 0x100, 3 << 26, 6 << 26):
                scope = part.inputs[0]._replace(variables=(
                    *part.inputs[0].variables,
                    read_epochs.OriginalVariable("UNSAFE_INPUT", "hidden", flags, None, 0, 0),
                ))
                changed = part._replace(inputs=(scope,))
                phase = phase_census.SourcePass(
                    proof, changed, proof.images[0], (), "all", (), self.case.commands(session), "Makefile",
                )
                with self.subTest(flags=flags), self.assertRaisesRegex(MakeProbeError, "original input"):
                    phase.input("UNSAFE_INPUT")
            with self.assertRaises(MakeProbeError):
                phase_census.OriginalSourceProof(session, replace(observed))
            for target, state in (("foreign", ()), ("all", (("command-line", "HIDDEN", "forged"),))):
                with self.subTest(target=target, state=state), self.assertRaises(MakeProbeError):
                    phase_census.analyze(session, observed, target, state, self.case.commands(session))
            with self.assertRaises(MakeProbeError):
                phase_census.analyze(
                    session, observed, "all", (), self.case.commands(session), primary_source="writer.py",
                )
            saved_trace = copy.deepcopy(observed.read_trace)
            for defect in ("status", "source", "input"):
                try:
                    if defect == "status":
                        next(row for row in observed.read_trace["events"]
                             if row["kind"] == "source-exit" and row["error"])["error"] = 0
                    elif defect == "source":
                        observed.read_trace["sources"][0]["data"] = ""
                    else:
                        next(row for row in observed.read_trace["events"]
                             if row["kind"] == "pass-entry")["inputs"][0]["variables"][0][1] += "-forged"
                    with self.subTest(defect=defect), self.assertRaises(MakeProbeError):
                        phase_census.analyze(session, observed, "all", (), self.case.commands(session))
                finally:
                    observed.read_trace.clear()
                    observed.read_trace.update(copy.deepcopy(saved_trace))
            saved = copy.deepcopy(observed.source_journal)
            observed.source_journal["transactions"][0]["origin"]["stage"] = "source-read"
            with self.assertRaises(MakeProbeError):
                phase_census.OriginalSourceProof(session, observed)
            observed.source_journal.clear()
            observed.source_journal.update(saved)
            phase_census.OriginalSourceProof(session, observed)
        self.fixture.assert_clean(session)
        with self.case.session() as other:
            with self.assertRaises(MakeProbeError):
                phase_census.OriginalSourceProof(other, observed)
        self.fixture.assert_clean(other)
        with self.assertRaises(MakeProbeError):
            phase_census.OriginalSourceProof(session, observed)

    def test_exact_filter_uses_native_words_and_does_not_normalize_unicode_patterns(self):
        for expression, expected in (
            ("$(filter all clean,all build clean)", "all clean"),
            ("$(filter src/%.c,src/a.c include/a.h src/b.c)", "src/a.c src/b.c"),
            ("$(filter-out all clean,all build clean)", "build"),
            ("$(filter all,  all\tbuild all  )", "all all"),
        ):
            self.fixture.add("Makefile", "MATCH := " + expression + "\nall: ;\n")
            with self.subTest(expression=expression), self.case.session() as session:
                native = session.make("all", variables=("MATCH",))
                self.assertEqual(native.semantics["domains"]["MATCH"]["value"], expected)
                self.assertEqual(graph_probe._MakeSourceMode().exact_initializer_value(expression), expected)
            self.fixture.assert_clean(session)
        expression = "$(filter a" + chr(160) + "b,a b)"
        self.fixture.add("Makefile", "MATCH := " + expression + "\nall: ;\n")
        with self.case.session() as session:
            native = session.make("all", variables=("MATCH",))
            self.assertEqual(native.semantics["domains"]["MATCH"]["value"], "")
            self.assertIsNone(graph_probe._MakeSourceMode().exact_initializer_value(expression))
        self.fixture.assert_clean(session)

    def test_existing_native_owner_and_case_cover_phase_interpretation(self):
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
            for path, owner in (
                ("scripts/validation_ownership/phase_census.py", "paths.ownership"),
                ("scripts/validation_ownership/tests/test_phase_census.py", "paths.ownership-native"),
            ):
                rule, = [row for row in graph["path_rules"] if reporter._path_rule_matches(row, path, set())]
                self.assertEqual(rule["id"], owner)
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
            (("python3", "-m", "unittest", __name__, "-v"), "scripts/validation_ownership/tests/test_phase_census.py"),
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
