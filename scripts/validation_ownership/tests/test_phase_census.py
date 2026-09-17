"""Every-pass obligations from actual native source/image/mutation history."""

import copy
from dataclasses import replace
import shlex
import subprocess
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

    def universe_source(self, consumer):
        self.fixture.add("Makefile", (
            ".DEFAULT_GOAL := all\ninclude build/remade.mk\n" + consumer
            + "build/remade.mk:\n\tpython3 writer.py\nall: ;\n"
        ))
        self.fixture.add("writer.py", (
            "import os\nfrom pathlib import Path\n"
            "source=Path('/work/src/new.c');source.parent.mkdir(parents=True,exist_ok=True)\n"
            "source.write_text('/* actual native source output */\\n')\n"
            "include=Path('/work/build/remade.mk');include.parent.mkdir(parents=True,exist_ok=True)\n"
            "include.write_text('GENERATED_BINDING := inventory\\n')\n"
            "print(os.environ.get('PHASE_LABEL','<undefined>'))\n"
        ))

    def universe_native(self, session):
        return session.make(
            "all", variables=("GENERATED_BINDING",), definitions=("PHASE_LABEL",),
            commands=self.case.commands(session), observe_source_journal=True,
            source_journal_mode=source_directories.MODE,
        )

    def original_input_observation(self, session, state):
        return session.make(
            "all", variables=("PHASE_LABEL", "HIDDEN", "MAKECMDGOALS"), assignments=state,
            commands=self.case.commands(session), observe_source_journal=True,
            source_journal_mode=source_directories.MODE,
        )

    def original_input_plan(self, session, domains):
        with patch.object(graph_probe, "MakeCommands", lambda owner, contracts: self.case.commands(owner)):
            return graph_probe.run_probe(
                session.loader, {"all"}, domains, {}, session=session, source_phases=True,
                declared_external_names=set(domains),
            )["all"]

    def effective_export_source(self, declarations, *, required=False):
        self.fixture.add("Makefile", (
            ".DEFAULT_GOAL := all\n" + declarations
            + "all:\n\t" + ("+@" if required else "@") + "python3 capture.py\n"
        ))
        self.fixture.add("capture.py", "import os\nprint(os.environ['UNIVERSE'])\n")

    def effective_export_commands(self, session):
        return {"python3 capture.py": session._native_context_command(make_probe.Command(
            ("/usr/bin/python3", "/repo/capture.py"), code=("capture.py",),
        ))}

    def observe_effective_export(self, session, state=()):
        commands = self.effective_export_commands(session)
        results = []
        execute = session.command
        def capture(command):
            result = execute(command)
            results.append(result)
            return result
        with patch.object(session, "command", capture):
            observed = session.make(
                "all", definitions=("UNIVERSE",), assignments=state, commands=commands,
                observe_source_journal=True, source_journal_mode=source_directories.MODE,
            )
        return observed, commands, results

    def ordinary_effective_export(self, state=()):
        result = subprocess.run(
            ["/usr/bin/make", "--no-print-directory", *(name + "=" + value for origin, name, value in state
                                                        if origin == "command-line"), "all"],
            cwd=self.fixture.root,
            env={**graph_probe.ENVIRONMENT, **{name: value for origin, name, value in state if origin == "environment"}},
            capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        return result.stdout

    def effective_export_plan(self, session, domains):
        with patch.object(graph_probe, "MakeCommands", lambda owner, contracts: self.effective_export_commands(owner)):
            return graph_probe.run_probe(
                session.loader, {"all"}, domains, {}, session=session, source_phases=True,
            )["all"]

    def test_projected_recipe_exports_execute_source_and_command_bodies_before_suppression(self):
        body = "$(filter UNIVERSE,$(.VARIABLES:%=%))"
        for origin in ("file", "command-line"):
            declarations = "UNIVERSE = " + body + "\n" if origin == "file" else "KIND := $(origin UNIVERSE)\n"
            self.effective_export_source(declarations + "export UNIVERSE\n")
            state = () if origin == "file" else (("command-line", "UNIVERSE", body),)
            domains = {} if origin == "file" else {"UNIVERSE": {"kind": "explicit", "values": [body]}}
            self.assertEqual(self.ordinary_effective_export(state), b"UNIVERSE\n")
            with self.subTest(origin=origin), self.case.session() as session:
                observed, commands, executed = self.observe_effective_export(session, state)
                self.assertEqual(executed, [])
                dispatch, = observed.semantics["native_dispatches"]
                self.assertEqual(dispatch["kind"], "recipe")
                self.assertEqual(dispatch["job"], {"sequence": 1, "kind": "recipe", "target": "all", "command_line": 1})
                self.assertEqual(dispatch["environment"]["UNIVERSE"], "UNIVERSE")
                self.assertEqual(observed.semantics["definitions"]["global"]["UNIVERSE"]["value"], body)
                with self.assertRaisesRegex(MakeProbeError, "variable-universe"):
                    phase_census.analyze(session, observed, "all", state, commands)
            self.fixture.assert_clean(session)
            with self.case.session() as session:
                with self.assertRaisesRegex(MakeProbeError, "variable-universe"):
                    self.effective_export_plan(session, domains)
            self.fixture.assert_clean(session)

    def test_original_environment_exports_are_raw_but_explicit_make_reads_still_execute(self):
        body = "$(filter UNIVERSE,$(.VARIABLES:%=%))"
        for explicit in (False, True):
            declarations = "KIND := $(origin UNIVERSE)\n" + ("export UNIVERSE\n" if explicit else "")
            self.effective_export_source(declarations, required=True)
            state = (("environment", "UNIVERSE", body),)
            self.assertEqual(self.ordinary_effective_export(state), (body + "\n").encode())
            with self.subTest(explicit=explicit), self.case.session() as session:
                observed, commands, executed = self.observe_effective_export(session, state)
                command, = executed
                self.assertEqual((command.returncode, command.stdout, command.stderr), (0, (body + "\n").encode(), b""))
                dispatch, = observed.semantics["native_dispatches"]
                self.assertEqual((dispatch["kind"], dispatch["job"]["kind"]), ("value", "recipe"))
                self.assertEqual(dispatch["environment"]["UNIVERSE"], body)
                self.assertEqual(observed.semantics["definitions"]["global"]["UNIVERSE"], {
                    "origin": "environment", "flavor": "recursive", "value": body,
                })
                usage, _, _, _ = phase_census.analyze(session, observed, "all", state, commands)
                self.assertIn("UNIVERSE", usage["recipe"])
                self.assertNotIn("UNIVERSE", usage["source_expressions"])
                self.assertNotIn("UNIVERSE", usage["execution_dependencies"])
            self.fixture.assert_clean(session)
            for input_origin, extra in (("command-line", ""), ("environment", "ACTUAL_READ := $(UNIVERSE)\n")):
                self.effective_export_source(extra + declarations, required=True)
                state = ((input_origin, "UNIVERSE", body),)
                expected = b"UNIVERSE\n" if input_origin == "command-line" else (body + "\n").encode()
                self.assertEqual(self.ordinary_effective_export(state), expected)
                with self.subTest(explicit=explicit, input_origin=input_origin), self.case.session() as session:
                    observed, commands, executed = self.observe_effective_export(session, state)
                    self.assertEqual(executed[0].stdout, expected)
                    with self.assertRaisesRegex(MakeProbeError, "variable-universe"):
                        phase_census.analyze(session, observed, "all", state, commands)
                self.fixture.assert_clean(session)

    def test_effective_export_origin_tracks_conditional_append_override_and_simple_data(self):
        body = "$(filter UNIVERSE,$(.VARIABLES:%=%))"
        cases = (
            ("UNIVERSE ?= fallback\n", "environment", "recursive", body, True),
            ("UNIVERSE +=\n", "environment", "recursive", body, True),
            ("override UNIVERSE +=\n", "environment", "recursive", body, True),
            ("UNIVERSE += tail\n", "file", "recursive", "UNIVERSE tail", False),
            ("export UNIVERSE += tail\n", "file", "recursive", "UNIVERSE tail", False),
            ("UNIVERSE = " + body + "\n", "file", "recursive", "UNIVERSE", False),
            ("override UNIVERSE = " + body + "\n", "override", "recursive", "UNIVERSE", False),
            ("UNIVERSE := $(value UNIVERSE)\n", "file", "simple", body, True),
            ("UNIVERSE = literal\n", "file", "recursive", "literal", True),
            ("ifeq (yes,no)\nUNIVERSE = literal\nendif\n", "environment", "recursive", body, True),
            ("ifeq (yes,yes)\nUNIVERSE = literal\nendif\n", "file", "recursive", "literal", True),
            ("override UNIVERSE := literal\n", "override", "simple", "literal", True),
        )
        state = (("environment", "UNIVERSE", body),)
        for declarations, origin, flavor, value, accepted in cases:
            self.effective_export_source(declarations, required=True)
            self.assertEqual(self.ordinary_effective_export(state), (value + "\n").encode())
            with self.subTest(declarations=declarations), self.case.session() as session:
                observed, commands, executed = self.observe_effective_export(session, state)
                self.assertEqual(executed[0].stdout, (value + "\n").encode())
                metadata = observed.semantics["definitions"]["global"]["UNIVERSE"]
                self.assertEqual((metadata["origin"], metadata["flavor"]), (origin, flavor))
                if accepted:
                    phase_census.analyze(session, observed, "all", state, commands)
                else:
                    with self.assertRaisesRegex(MakeProbeError, "variable-universe"):
                        phase_census.analyze(session, observed, "all", state, commands)
            self.fixture.assert_clean(session)

    def test_raw_environment_code_bytes_remain_data_with_an_independent_namespace_snapshot(self):
        body = "$(wildcard src/*.c)$(error raw export must not execute)"
        self.effective_export_source("SNAPSHOT := $(wildcard src/*.c)\nKIND := $(origin UNIVERSE)\n",
                                     required=True)
        state = (("environment", "UNIVERSE", body),)
        self.assertEqual(self.ordinary_effective_export(state), (body + "\n").encode())
        with self.case.session() as session:
            observed, commands, executed = self.observe_effective_export(session, state)
            self.assertEqual(executed[0].stdout, (body + "\n").encode())
            usage, _, _, _ = phase_census.analyze(session, observed, "all", state, commands)
            self.assertIn("UNIVERSE", usage["recipe"])
            self.assertNotIn("UNIVERSE", usage["execution_dependencies"])
        self.fixture.assert_clean(session)

    def test_export_declarations_create_only_native_proven_empty_simple_bindings(self):
        for directive in ("export UNSET", "unexport UNSET", "export UNSET UNSET", "export ${NAME}"):
            self.fixture.add("Makefile", (
                ".DEFAULT_GOAL := all\nNAME := UNSET\n" + directive + "\n"
                "ifneq ($(origin UNSET),file)\nHIDDEN ?= incorrect\nendif\nall: ;\n"
            ))
            with self.subTest(directive=directive), self.case.session() as session:
                observed = session.make(
                    "all", definitions=("UNSET", "HIDDEN"), observe_source_journal=True,
                    source_journal_mode=source_directories.MODE,
                )
                self.assertEqual(observed.semantics["definitions"]["global"]["UNSET"],
                                 {"origin": "file", "flavor": "simple", "value": ""})
                self.assertEqual(observed.semantics["definitions"]["global"]["HIDDEN"]["origin"], "undefined")
                usage, _, streams, _ = phase_census.analyze(session, observed, "all", (), {})
                self.assertEqual(usage["defaults"], set())
                self.assertIn("UNSET", usage["defined"])
                self.assertEqual(streams[0].mode_state.literal_text("$(origin UNSET)"), "file")
                self.assertEqual(streams[0].mode_state.literal_text("$(flavor UNSET)"), "simple")
            self.fixture.assert_clean(session)

    def test_literal_projected_exports_are_data_and_keep_complete_small_planner_positive(self):
        for value in ("literal", "$$(filter UNIVERSE,$$(.VARIABLES:%=%))"):
            self.effective_export_source("UNIVERSE := " + value + "\nexport UNIVERSE\n")
            expected = value.replace("$$", "$")
            self.assertEqual(self.ordinary_effective_export(), (expected + "\n").encode())
            with self.subTest(value=value), self.case.session() as session:
                result = self.effective_export_plan(session, {})
                self.assertEqual(result["variable_census"]["defaults"], [])
                dispatch, = result["record"]["variants"][0]["record"]["native_dispatches"]
                self.assertEqual(dispatch["environment"]["UNIVERSE"], expected)
            self.fixture.assert_clean(session)

    def test_export_projection_and_raw_origin_removals_recover_the_actual_regressions(self):
        body = "$(filter UNIVERSE,$(.VARIABLES:%=%))"
        context = phase_census._recipe_export_context
        for origin in ("file", "command-line"):
            prefix = "UNIVERSE = " + body + "\n" if origin == "file" else "KIND := $(origin UNIVERSE)\n"
            self.effective_export_source(prefix + "export UNIVERSE\n")
            domains = {} if origin == "file" else {"UNIVERSE": {"kind": "explicit", "values": [body]}}
            with self.subTest(origin=origin), self.case.session() as session:
                with patch.object(phase_census, "_recipe_export_context",
                                  side_effect=lambda actual, event: context(actual, event) and event["kind"] == "value"):
                    result = self.effective_export_plan(session, domains)
                self.assertEqual(result["variable_census"]["defaults"], [])
                supplied = [row for row in result["record"]["variants"] if row["state"]]
                variant = supplied[0] if supplied else result["record"]["variants"][0]
                dispatch, = variant["record"]["native_dispatches"]
                self.assertEqual((dispatch["kind"], dispatch["environment"]["UNIVERSE"]), ("recipe", "UNIVERSE"))
            self.fixture.assert_clean(session)
        for explicit in (False, True):
            self.effective_export_source("KIND := $(origin UNIVERSE)\n" + ("export UNIVERSE\n" if explicit else ""),
                                         required=True)
            state = (("environment", "UNIVERSE", body),)
            with self.subTest(explicit=explicit), self.case.session() as session:
                observed, commands, executed = self.observe_effective_export(session, state)
                self.assertEqual(executed[0].stdout, (body + "\n").encode())
                with patch.object(phase_census, "_export_expands", side_effect=lambda binding: binding.flavor == "recursive"):
                    with self.assertRaisesRegex(MakeProbeError, "variable-universe"):
                        phase_census.analyze(session, observed, "all", state, commands)
                phase_census.analyze(session, observed, "all", state, commands)
            self.fixture.assert_clean(session)

    def test_original_recursive_execution_and_initial_only_exports_close_before_admission(self):
        body = "$(.VARIABLES:%=%)"
        domains = {"UNIVERSE": {"kind": "explicit", "values": [body]}}
        for executed in (True, False):
            consumer = "KIND := $(origin UNIVERSE)\n"
            consumer += ("PHASE_LABEL := $(filter GENERATED_BINDING,$(UNIVERSE))\n"
                         if executed else "PHASE_LABEL := stable\n")
            self.universe_source(consumer + "export PHASE_LABEL\n")
            state = (("command-line", "UNIVERSE", body),)
            with self.subTest(executed=executed), self.case.session() as session:
                observed = self.original_input_observation(session, state)
                original = {row.name: row for row in session._original_source_archive(observed).passes[0].inputs[0].variables}
                self.assertEqual(original["UNIVERSE"].value, body)
                self.assertTrue(original["UNIVERSE"].flags & 1)
                self.assertEqual((original["UNIVERSE"].flags >> 26) & 7, 4)
                first = observed.semantics["native_dispatches"][0]
                self.assertEqual(first["job"]["kind"], "recipe")
                self.assertNotIn("GENERATED_BINDING", first["environment"]["UNIVERSE"].split())
                self.assertIn(".VARIABLES", first["environment"]["UNIVERSE"].split())
                self.assertEqual(first["environment"]["PHASE_LABEL"], "" if executed else "stable")
                self.assertEqual(observed.semantics["domains"]["PHASE_LABEL"]["value"],
                                 "GENERATED_BINDING" if executed else "stable")
                with self.assertRaisesRegex(MakeProbeError, "variable-universe"):
                    phase_census.analyze(session, observed, "all", state, self.case.commands(session))
            self.fixture.assert_clean(session)
            with self.case.session() as session:
                with self.assertRaisesRegex(MakeProbeError, "variable-universe"):
                    self.original_input_plan(session, domains)
            self.fixture.assert_clean(session)
            with self.case.session() as session:
                with patch.object(phase_census.SourcePass, "record_execution", return_value=None):
                    result = self.original_input_plan(session, domains)
                self.assertEqual(result["variable_census"]["defaults"], [])
                supplied, = [row for row in result["record"]["variants"] if row["state"]]
                first = supplied["record"]["native_dispatches"][0]
                self.assertEqual(first["environment"]["PHASE_LABEL"], "" if executed else "stable")
                self.assertIn(".VARIABLES", first["environment"]["UNIVERSE"].split())
            self.fixture.assert_clean(session)
        self.universe_source(
            "UNIVERSE = " + body + "\nPHASE_LABEL := $(filter GENERATED_BINDING,$(UNIVERSE))\nexport PHASE_LABEL\n",
        )
        with self.case.session() as session:
            with patch.object(phase_census.SourcePass, "record_execution", return_value=None):
                with self.assertRaisesRegex(MakeProbeError, "variable-universe"):
                    self.original_input_plan(session, {})
        self.fixture.assert_clean(session)

    def test_original_input_precedence_metadata_and_unexport_use_effective_bindings(self):
        body = "$(.VARIABLES:%=%)"
        read = "PHASE_LABEL := $(filter GENERATED_BINDING,$(UNIVERSE))\n"
        cases = (
            ("override UNIVERSE := literal\n" + read, True, "", ""),
            (read + "override UNIVERSE := literal\n", False, "", "GENERATED_BINDING"),
            ("PHASE_LABEL := $(value UNIVERSE)\n", True, body, body),
            ("override UNIVERSE := $(value UNIVERSE)\nPHASE_LABEL := $(UNIVERSE)\n", True, body, body),
            ("UNIVERSE := literal\n" + read, None, None, None),
        )
        for origin in ("command-line", "environment"):
            for source, accepted, first_value, final_value in cases:
                self.universe_source(source + "unexport UNIVERSE\nexport PHASE_LABEL\n")
                if accepted is None:
                    accepted = origin == "environment"
                    first_value, final_value = ("", "") if accepted else ("", "GENERATED_BINDING")
                state = ((origin, "UNIVERSE", body),)
                with self.subTest(origin=origin, source=source), self.case.session() as session:
                    observed = self.original_input_observation(session, state)
                    first = observed.semantics["native_dispatches"][0]["environment"]
                    self.assertNotIn("UNIVERSE", first)
                    self.assertEqual(first["PHASE_LABEL"], first_value)
                    self.assertEqual(observed.semantics["domains"]["PHASE_LABEL"]["value"], final_value)
                    if accepted:
                        phase_census.analyze(session, observed, "all", state, self.case.commands(session))
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "variable-universe"):
                            phase_census.analyze(session, observed, "all", state, self.case.commands(session))
                self.fixture.assert_clean(session)

    def test_original_input_aliases_and_dependencies_are_not_source_declarations(self):
        for source, body, rejected in (
            ("ALIAS = $(UNIVERSE)\nPHASE_LABEL := $(filter GENERATED_BINDING,$(ALIAS))\n", "$(.VARIABLES:%=%)", True),
            ("SELECTOR := UNIVERSE\nPHASE_LABEL := $(filter GENERATED_BINDING,$($(SELECTOR)))\n", "$(.VARIABLES:%=%)", True),
            ("ALIAS = $(.VARIABLES:%=%)\nPHASE_LABEL := $(filter GENERATED_BINDING,$(UNIVERSE))\n", "$(ALIAS)", True),
            ("SELECTOR := UNIVERSE\nPHASE_LABEL := $($(SELECTOR))\n", "literal", False),
            ("LEAF := literal\nPHASE_LABEL := $(UNIVERSE)\n", "$(LEAF)", False),
        ):
            self.universe_source(source + "unexport UNIVERSE\nexport PHASE_LABEL\n")
            state = (("command-line", "UNIVERSE", body),)
            with self.subTest(source=source, body=body), self.case.session() as session:
                observed = self.original_input_observation(session, state)
                if rejected:
                    with self.assertRaisesRegex(MakeProbeError, "variable-universe"):
                        phase_census.analyze(session, observed, "all", state, self.case.commands(session))
                else:
                    usage, _, _, _ = phase_census.analyze(session, observed, "all", state, self.case.commands(session))
                    self.assertNotIn("UNIVERSE", usage["defined"])
                    self.assertIn("UNIVERSE", usage["dependencies"])
                    self.assertEqual(observed.semantics["native_dispatches"][0]["environment"]["PHASE_LABEL"], "literal")
                    if body == "$(LEAF)":
                        self.assertIn("LEAF", usage["dependencies"]["UNIVERSE"])
                        self.assertIn("LEAF", usage["all"])
            self.fixture.assert_clean(session)

    def test_original_input_lazy_branches_keep_unexecuted_bodies_out_of_closure(self):
        for body, expected in (
            ("$(and $(EMPTY),$(.VARIABLES:%=%))", ""),
            ("$(or literal,$(.VARIABLES:%=%))", "literal"),
            ("$(if yes,literal,$(.VARIABLES:%=%))", "literal"),
            ("$(if ,$(.VARIABLES:%=%),literal)", "literal"),
            ("$$(.VARIABLES:%=%)", "$(.VARIABLES:%=%)"),
            ("$(value UNIVERSE)", "$(value UNIVERSE)"),
        ):
            self.universe_source("EMPTY :=\nPHASE_LABEL := $(UNIVERSE)\nunexport UNIVERSE\nexport PHASE_LABEL\n")
            state = (("command-line", "UNIVERSE", body),)
            with self.subTest(body=body), self.case.session() as session:
                observed = self.original_input_observation(session, state)
                self.assertEqual(observed.semantics["native_dispatches"][0]["environment"]["PHASE_LABEL"], expected)
                usage, _, _, _ = phase_census.analyze(session, observed, "all", state, self.case.commands(session))
                self.assertEqual(usage["defaults"], set())
            self.fixture.assert_clean(session)

    def test_forced_control_facts_cannot_replace_actual_original_precedence(self):
        self.universe_source(
            "ifeq ($(MAKECMDGOALS),other)\nHIDDEN ?= secret\nexport HIDDEN\nendif\n",
        )
        state = (("command-line", "MAKECMDGOALS", "other"),)
        with self.case.session() as session:
            observed = self.original_input_observation(session, state)
            self.assertEqual(observed.semantics["native_dispatches"][0]["environment"]["HIDDEN"], "secret")
            self.assertEqual(observed.semantics["domains"]["MAKECMDGOALS"], {
                "origin": "command line", "flavor": "recursive", "value": "other",
            })
            proof = phase_census.OriginalSourceProof(session, observed)
            part = proof.archive.passes[0]
            phase = phase_census.SourcePass(
                proof, part, proof.images[0], proof.exports.get(part.number, ()),
                "all", state, self.case.commands(session), "Makefile",
            )
            stream, inputs, scoped = graph_probe._prepare_rule_templates(
                session, "all", state, self.case.commands(session), observed, phase.sources,
                primary_source="Makefile", phase=phase,
            )
            usage = graph_probe.source_census(
                phase.sources, reference_units=stream, template_graph_inputs=inputs, template_scoped=scoped,
                source_assignments=state, budget=session.budget, source_target="all",
            )
            self.assertIn("HIDDEN", usage["defaults"])
            self.assertFalse(stream.mode_state.original_namespace_valid)
            self.assertEqual(stream.mode_state.control_values, {})
            with self.assertRaises(MakeProbeError):
                phase_census.analyze(session, observed, "all", state, self.case.commands(session))
        self.fixture.assert_clean(session)
        domains = {"MAKECMDGOALS": {"kind": "explicit", "values": ["other"]}}
        with self.case.session() as session:
            with self.assertRaises(MakeProbeError):
                self.original_input_plan(session, domains)
        self.fixture.assert_clean(session)
        bind = graph_probe._MakeSourceMode.bind_invocation
        def omit_forced(mode, target):
            forced = mode.forced
            supplied = mode.invocation_inputs
            mode.forced = forced - graph_probe.INVOCATION_CONTROL_READS
            mode.invocation_inputs = supplied - graph_probe.INVOCATION_CONTROL_READS
            try:
                return bind(mode, target)
            finally:
                mode.forced = forced
                mode.invocation_inputs = supplied
        with self.case.session() as session:
            observed = self.original_input_observation(session, state)
            proof = phase_census.OriginalSourceProof(session, observed)
            part = proof.archive.passes[0]
            phase = phase_census.SourcePass(
                proof, part, proof.images[0], proof.exports.get(part.number, ()),
                "all", state, self.case.commands(session), "Makefile",
            )
            with patch.object(graph_probe._MakeSourceMode, "bind_invocation", omit_forced):
                stream, inputs, scoped = graph_probe._prepare_rule_templates(
                    session, "all", state, self.case.commands(session), observed, phase.sources,
                    primary_source="Makefile", phase=phase,
                )
                usage = graph_probe.source_census(
                    phase.sources, reference_units=stream, template_graph_inputs=inputs, template_scoped=scoped,
                    source_assignments=state, budget=session.budget, source_target="all",
                )
                self.assertEqual(stream.mode_state.control_values["MAKECMDGOALS"], "all")
                self.assertEqual(usage["defaults"], set())
                self.assertEqual(observed.semantics["native_dispatches"][0]["environment"]["HIDDEN"], "secret")
                with self.assertRaisesRegex(MakeProbeError, "effective original binding"):
                    self.original_input_plan(session, domains)
        self.fixture.assert_clean(session)

    def test_all_forced_invocation_facts_withhold_the_same_unproven_context(self):
        for name in graph_probe.INVOCATION_CONTROL_READS:
            for field in ("forced", "invocation_inputs"):
                with self.subTest(name=name, field=field):
                    mode = graph_probe._MakeSourceMode(**{field: frozenset((name,))})
                    mode.bind_invocation("all")
                    self.assertFalse(mode.original_namespace_valid)
                    self.assertEqual((mode.control_values, mode.control_metadata, mode.control_reads), ({}, {}, set()))
        mode = graph_probe._MakeSourceMode(forced=frozenset(("ORDINARY_INPUT",)))
        mode.bind_invocation("all")
        self.assertTrue(mode.original_namespace_valid)
        self.assertEqual(mode.control_text("$(MAKECMDGOALS)"), "all")
        self.assertEqual(mode.control_text("$(origin MAKECMDGOALS)"), "default")
        self.assertEqual(mode.control_reads, graph_probe.INVOCATION_CONTROL_READS)

    def test_environment_control_presence_is_not_mistaken_for_an_unforced_default(self):
        self.universe_source("ifeq ($(MAKECMDGOALS),other)\nHIDDEN ?= secret\nexport HIDDEN\nendif\n")
        state = (("environment", "MAKECMDGOALS", "other"),)
        with self.case.session() as session:
            observed = self.original_input_observation(session, state)
            original = {row.name: row for row in session._original_source_archive(observed).passes[0].inputs[0].variables}
            self.assertEqual((original["MAKECMDGOALS"].flags >> 26) & 7, 1)
            self.assertEqual(original["MAKECMDGOALS"].value, "other")
            self.assertEqual(observed.semantics["domains"]["MAKECMDGOALS"],
                             {"origin": "environment", "flavor": "recursive", "value": "other"})
            self.assertEqual(observed.semantics["native_dispatches"][0]["environment"]["HIDDEN"], "secret")
            with self.assertRaises(MakeProbeError):
                phase_census.analyze(session, observed, "all", state, self.case.commands(session))
        self.fixture.assert_clean(session)
    def test_initial_literal_exports_and_raw_shell_environments_are_distinct(self):
        for origin in ("command-line", "environment"):
            self.universe_source("KIND := $(origin UNIVERSE)\nPHASE_LABEL := stable\nexport PHASE_LABEL\n")
            state = ((origin, "UNIVERSE", "literal"),)
            with self.subTest(origin=origin), self.case.session() as session:
                observed = self.original_input_observation(session, state)
                self.assertEqual(observed.semantics["native_dispatches"][0]["environment"]["UNIVERSE"], "literal")
                usage, _, _, _ = phase_census.analyze(session, observed, "all", state, self.case.commands(session))
                self.assertIn("UNIVERSE", usage["recipe"])
                self.assertNotIn("UNIVERSE", usage["defined"])
            self.fixture.assert_clean(session)
        body = "$(.VARIABLES:%=%)"
        for during_read in (True, False):
            consumer = ".DEFAULT_GOAL := all\nKIND := $(origin UNIVERSE)\nPHASE_LABEL := $(value UNIVERSE)\n"
            consumer += ("TRIGGER := $(shell python3 capture.py)\nall: ;\n" if during_read
                         else "all: ; @printf '%s' '$(shell python3 capture.py)'\n")
            self.fixture.add("Makefile", consumer)
            self.fixture.add("capture.py", "import os\nprint(os.environ['UNIVERSE'])\n")
            state = (("environment", "UNIVERSE", body),)
            with self.subTest(during_read=during_read), self.case.session() as session:
                commands = {"python3 capture.py": session._native_context_command(make_probe.Command(
                    ("/usr/bin/python3", "/repo/capture.py"), code=("capture.py",),
                ))}
                observed = session.make(
                    "all", variables=("PHASE_LABEL",), assignments=state, commands=commands,
                    observe_source_journal=True, source_journal_mode=source_directories.MODE,
                )
                dispatches = observed.semantics["native_dispatches"]
                self.assertTrue(dispatches)
                expansions = [dispatch for dispatch in dispatches if dispatch["kind"] == "value"]
                self.assertTrue(expansions)
                for dispatch in dispatches:
                    self.assertEqual(dispatch["environment"]["UNIVERSE"], body)
                for dispatch in expansions:
                    self.assertEqual(dispatch["job"]["kind"], "expansion")
                self.assertTrue(all(dispatch["job"]["kind"] == "recipe"
                                    for dispatch in dispatches if dispatch["kind"] == "recipe"))
                origins = [event for event in observed.source_effects["events"] if event["kind"] == "origin"]
                self.assertEqual(len(origins), len(dispatches))
                self.assertTrue(all(row["stage"] == ("source-read" if during_read else "after-read") for row in origins))
                phase_census.analyze(session, observed, "all", state, commands)
            self.fixture.assert_clean(session)

    def test_unexported_original_recipe_reads_are_executed_and_scoped_unknowns_hold(self):
        self.universe_source("PHASE_LABEL = $(filter GENERATED_BINDING,$(UNIVERSE))\nunexport UNIVERSE\n")
        source = (self.fixture.root / "Makefile").read_text().replace(
            "all: ;\n", "all: ; @printf '%s' '$(PHASE_LABEL)'\n",
        )
        for scoped in (False, True):
            self.fixture.add("Makefile", source + ("all: override UNIVERSE := literal\n" if scoped else ""))
            state = (("command-line", "UNIVERSE", "$(.VARIABLES:%=%)"),)
            with self.subTest(scoped=scoped), self.case.session() as session:
                observed = self.original_input_observation(session, state)
                self.assertNotIn("UNIVERSE", observed.semantics["native_dispatches"][0]["environment"])
                with self.assertRaisesRegex(MakeProbeError, "target/private binding" if scoped else "variable-universe"):
                    phase_census.analyze(session, observed, "all", state, self.case.commands(session))
            self.fixture.assert_clean(session)

    def test_later_override_replaces_deferred_input_before_inline_or_block_recipe(self):
        for inline in (True, False):
            recipe = "all: ; +python3 capture.py $(UNIVERSE)\n" if inline else "all:\n\t+python3 capture.py $(UNIVERSE)\n"
            self.fixture.add("Makefile", (
                ".DEFAULT_GOAL := all\nunexport UNIVERSE\n" + recipe + "override UNIVERSE := literal\n"
            ))
            self.fixture.add("capture.py", "import sys\nprint(sys.argv[1])\n")
            state = (("command-line", "UNIVERSE", "$(.VARIABLES:%=%)"),)
            with self.subTest(inline=inline), self.case.session() as session:
                commands = {"python3 capture.py literal": session._native_context_command(make_probe.Command(
                    ("/usr/bin/python3", "/repo/capture.py", "literal"), code=("capture.py",),
                ))}
                observed = session.make(
                    "all", variables=("UNIVERSE",), assignments=state, commands=commands,
                    observe_source_journal=True, source_journal_mode=source_directories.MODE,
                )
                self.assertEqual(observed.semantics["native_dispatches"][0]["arguments"],
                                 ["python3", "capture.py", "literal"])
                self.assertEqual(observed.semantics["domains"]["UNIVERSE"]["value"], "literal")
                phase_census.analyze(session, observed, "all", state, commands)
            self.fixture.assert_clean(session)

    def test_executed_universe_reference_families_refuse_on_actual_original_passes(self):
        for declarations, expression in (
            ("", "$(.VARIABLES)"),
            ("", "${.VARIABLES}"),
            ("", "$(.VARIABLES:%=%)"),
            ("", "${.VARIABLES:%=%}"),
            ("PATTERN := %\nREPLACEMENT := %\n", "${.VARIABLES:$(PATTERN)=${REPLACEMENT}}"),
            ("UNIVERSE = $(.VARIABLES:%=%)\nALIAS = ${UNIVERSE}\nSELECTOR = ALIAS\n", "$($(SELECTOR))"),
            ("SELECTOR := .VARIABLES\n", "${${SELECTOR}:%=%}"),
            ("", "$(call .VARIABLES)"),
        ):
            self.universe_source(
                declarations + "PHASE_LABEL := $(filter GENERATED_BINDING," + expression + ")\nexport PHASE_LABEL\n",
            )
            with self.subTest(expression=expression), self.case.session() as session:
                observed = self.universe_native(session)
                self.assertEqual(observed.semantics["native_dispatches"][0]["environment"]["PHASE_LABEL"], "")
                self.assertTrue(observed.semantics["native_dispatches"][0]["rebuilding_makefiles"])
                self.assertEqual(observed.semantics["definitions"]["global"]["PHASE_LABEL"], {
                    "origin": "file", "flavor": "simple", "value": "GENERATED_BINDING",
                })
                with self.assertRaises(MakeProbeError):
                    phase_census.analyze(session, observed, "all", (), self.case.commands(session))
            self.fixture.assert_clean(session)

    def test_universe_conditional_and_export_membership_are_original_reads(self):
        for kind, consumer in (
            ("conditional", "ifdef .VARIABLES\nPHASE_LABEL := set\nelse\nPHASE_LABEL := unset\nendif\nexport PHASE_LABEL\n"),
            ("export", "PHASE_LABEL := stable\nexport .VARIABLES PHASE_LABEL\n"),
        ):
            self.universe_source(consumer)
            with self.subTest(kind=kind), self.case.session() as session:
                observed = self.universe_native(session)
                first = observed.semantics["native_dispatches"][0]
                self.assertTrue(first["rebuilding_makefiles"])
                self.assertIn("PHASE_LABEL", first["environment"])
                if kind == "export":
                    self.assertIn(".VARIABLES", first["environment"])
                    self.assertNotIn("GENERATED_BINDING", first["environment"][".VARIABLES"].split())
                with self.assertRaisesRegex(MakeProbeError, "variable-universe"):
                    phase_census.analyze(session, observed, "all", (), self.case.commands(session))
            self.fixture.assert_clean(session)

    def test_unused_universe_bodies_and_literal_metadata_remain_lazy_in_small_planner(self):
        body = "$(.VARIABLES:%=%)$(error unused body expanded)"
        for consumer, expected, metadata in (
            ("PHASE_LABEL := stable\nexport PHASE_LABEL\n", "stable", False),
            ("UNUSED = " + body + "\nKIND := $(origin UNUSED)\nFLAVOR := ${flavor UNUSED}\n"
             "RAW := $(value UNUSED)\nPHASE_LABEL := stable\nexport KIND FLAVOR RAW PHASE_LABEL\n", "stable", True),
            ("EMPTY :=\nUNUSED = " + body + "\n"
             "PHASE_LABEL := $(and $(EMPTY),$(UNUSED))\nexport PHASE_LABEL\n", "", False),
        ):
            self.universe_source(consumer)
            with self.subTest(consumer=consumer), self.case.session() as session:
                with patch.object(graph_probe, "MakeCommands", lambda owner, contracts: self.case.commands(owner)):
                    result = graph_probe.run_probe(
                        session.loader, {"all"}, {}, {}, session=session, source_phases=True,
                    )["all"]
                self.assertEqual(result["variable_census"]["defaults"], [])
                first = result["record"]["variants"][0]["record"]["native_dispatches"][0]
                self.assertEqual(first["environment"]["PHASE_LABEL"], expected)
                if metadata:
                    self.assertEqual(first["environment"]["KIND"], "file")
                    self.assertEqual(first["environment"]["FLAVOR"], "recursive")
                    self.assertEqual(first["environment"]["RAW"], body)
            self.fixture.assert_clean(session)

    def test_full_small_universe_admission_returns_only_when_read_check_is_removed(self):
        for declarations, expression in (
            ("", "$(.VARIABLES:%=%)"),
            ("UNIVERSE = $(.VARIABLES:%=%)\nALIAS = ${UNIVERSE}\nSELECTOR = ALIAS\n", "$($(SELECTOR))"),
        ):
            self.universe_source(
                declarations + "PHASE_LABEL := $(filter GENERATED_BINDING," + expression + ")\nexport PHASE_LABEL\n",
            )
            for removed in (False, True):
                with self.subTest(expression=expression, removed=removed), self.case.session() as session:
                    with patch.object(graph_probe, "MakeCommands", lambda owner, contracts: self.case.commands(owner)):
                        if not removed:
                            with self.assertRaisesRegex(MakeProbeError, "variable-universe"):
                                graph_probe.run_probe(
                                    session.loader, {"all"}, {}, {}, session=session, source_phases=True,
                                )
                        else:
                            with patch.object(phase_census.SourcePass, "check_reads", return_value=None):
                                result = graph_probe.run_probe(
                                    session.loader, {"all"}, {}, {}, session=session, source_phases=True,
                                )["all"]
                            self.assertEqual(result["variable_census"]["defaults"], [])
                            first = result["record"]["variants"][0]["record"]["native_dispatches"][0]
                            self.assertEqual(first["environment"]["PHASE_LABEL"], "")
                            self.assertTrue(first["rebuilding_makefiles"])
                self.fixture.assert_clean(session)

    def test_original_read_occurrence_not_a_later_literal_controls_universe_execution(self):
        state = (("environment", "GUARD", "enabled"),)
        for executes in (False, True):
            operand = " $(findstring GUARD,$(LABEL))" if executes else ""
            self.fixture.add("Makefile", (
                ".DEFAULT_GOAL := all\nUNIVERSE = $(.VARIABLES:%=%)\n"
                "LABEL = $(and $(GUARD),$(UNIVERSE))\nexport LABEL\n"
                "TRIGGER := $(shell python3 capture.py" + operand + ")\nGUARD :=\nall: ;\n"
            ))
            self.fixture.add("capture.py", "import sys\nprint(sys.argv[1:])\n")
            arguments = ("GUARD",) if executes else ()
            with self.subTest(executes=executes), self.case.session() as session:
                commands = {"python3 capture.py" + (" GUARD" if executes else ""):
                            session._native_context_command(make_probe.Command(
                                ("/usr/bin/python3", "/repo/capture.py", *arguments), code=("capture.py",),
                            ))}
                observed = session.make(
                    "all", variables=("LABEL",), assignments=state, commands=commands,
                    observe_source_journal=True, source_journal_mode=source_directories.MODE,
                )
                first = observed.semantics["native_dispatches"][0]
                self.assertNotIn("LABEL", first["environment"])
                self.assertEqual(first["environment"]["GUARD"], "enabled")
                self.assertEqual(first["arguments"], ["python3", "capture.py", *arguments])
                self.assertEqual(observed.semantics["domains"]["LABEL"]["value"], "")
                origins = [event for event in observed.source_effects["events"] if event["kind"] == "origin"]
                self.assertEqual([event["stage"] for event in origins], ["source-read"])
                if executes:
                    with self.assertRaisesRegex(MakeProbeError, "variable-universe"):
                        phase_census.analyze(session, observed, "all", state, commands)
                else:
                    phase_census.analyze(session, observed, "all", state, commands)
            self.fixture.assert_clean(session)

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
