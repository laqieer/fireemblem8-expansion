import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from scripts.assets.manifest import (
    discovery_sources, load_manifest, render_discovery_artifact, render_discovery_makefile,
)
from scripts.validation_ownership import graph_probe, reporter
from scripts.validation_ownership.authority import AuthorityLoader, GitTreeEntry, git_tree_entries
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.graph_commands import CODE_PREFIXES, MakeCommands
from scripts.validation_ownership.tests import test_make_probe


ROOT = Path(__file__).resolve().parents[3]
ASSET_BINDING = "ASSET_BANIM_INCBIN_CONSUMERS"
CONDITIONAL_CONSUMER = (
    "SELECTOR = ASSET_BANIM_INCBIN_CONSUMERS\n"
    "ifdef $(SELECTOR)\nPHASE_LABEL := final\nelse\nPHASE_LABEL := first\nendif\n"
    "export PHASE_LABEL\n"
)
UNIVERSE_CONSUMER = (
    "PHASE_LABEL := $(filter ASSET_BANIM_INCBIN_CONSUMERS,$(.VARIABLES:%=%))\n"
    "export PHASE_LABEL\n"
)


class LiteralBindingModuleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_make_probe.AuthoritativeMakeProbeTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_module_discovery_collects_only_owned_cases(self):
        pending = [unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])]
        modules, identifiers = set(), []
        while pending:
            case = pending.pop()
            if isinstance(case, unittest.TestSuite):
                pending.extend(case)
            else:
                modules.add(type(case).__module__)
                identifiers.append(case.id())
        self.assertEqual(modules, {__name__})
        self.assertEqual(len(identifiers), 8)
        self.assertEqual(len(identifiers), len(set(identifiers)))
        graph = reporter.load_json(ROOT / reporter.GRAPH_PATH)
        budget = ProbeBudget()
        try:
            entries = git_tree_entries(ROOT, budget=budget)
            loader = AuthorityLoader(ROOT, entries, "HEAD", budget=budget)
            sources = reporter._path_admission_sources(loader, set())

            def ownership_rule(path):
                matches = [rule for rule in graph["path_rules"]
                           if reporter._path_rule_matches(rule, path, set())]
                self.assertEqual([(rule["id"], rule["surface"]) for rule in matches],
                                 [("paths.ownership", "surface.ownership")])
                return matches[0]

            path = Path(__file__).resolve().relative_to(ROOT).as_posix()
            for owned in (path, "scripts/validation_ownership/tests/test_make_probe.py",
                          "scripts/validation_ownership/tests/test_graph_commands.py"):
                self.assertIn(owned, entries)
                self.assertEqual(reporter._path_admission(owned, ownership_rule(owned), sources),
                                 "exact-ownership-rule")
            rule = ownership_rule(path)
            without_exact = {**rule, "include": [
                selector for selector in rule["include"]
                if selector != {"kind": "exact", "path": path}
            ]}
            self.assertTrue(reporter._path_rule_matches(without_exact, path, set()))
            with self.assertRaisesRegex(reporter.OwnershipError, "lacks semantic admission"):
                reporter._path_admission(path, without_exact, sources)
            neighbor = "scripts/validation_ownership/tests/test_unregistered_literal_bindings.py"
            self.assertNotIn(neighbor, entries)
            with self.assertRaisesRegex(reporter.OwnershipError, "lacks semantic admission"):
                reporter._path_admission(neighbor, ownership_rule(neighbor), sources)
        finally:
            budget.close()
            self.assertFalse(budget.children)
            self.assertFalse(budget.producer_waiters)

    def generic(self, content="INVENTORY_ITEMS := alpha beta\nENTRY_TOTAL := 2\n", *, prefix="", suffix=""):
        case = self.fixture
        commands = case.include_writer(content)
        case.original_input_witness()
        case.add("Makefile", prefix + "INC := build/include.mk\n-include $(INC)\n.PHONY: FORCE\nFORCE:\n"
                 "ifeq ($(MAKE_RESTARTS),)\n$(INC): FORCE\n\t@python3 writer.py\n"
                 "else\n$(INC): ;\nendif\n" + suffix + "all: ;\n")
        return commands

    def production(self, *, consumer="", late_consumer=""):
        case = self.fixture
        manifest = "assets/manifest.json"
        records = load_manifest(str(ROOT / manifest))
        inputs = (manifest, *discovery_sources(records))
        for path in inputs:
            case.add(path, "")
            data = (ROOT / path).read_bytes()
            (case.root / path).write_bytes(data)
            case.entries[path] = GitTreeEntry(path, "100644", "blob", hashlib.sha1(data).hexdigest())
        for prefix in CODE_PREFIXES:
            for path in (ROOT / prefix).rglob("*.py"):
                case.add(path.relative_to(ROOT).as_posix(), path.read_text())
        output = "build/generated/asset-discovery/current.mk"
        command = (
            'python3 -m scripts.assets --custom-spell-effects "0" --item-id-cap "0xCD" '
            '--manifest "assets/manifest.json" --discovery-makefile "' + output + '" discovery-makefile'
        )
        source = (
            "OUT := " + output + "\nTOOL := python3 -m scripts.assets\n"
            "-include $(OUT)\n" + consumer + ".PHONY: FORCE\nFORCE:\n"
            "ifeq ($(MAKE_RESTARTS),)\n$(OUT): FORCE\n"
            "\t@mkdir -p \"$(dir $@)\"\n\t" + command + "\n"
            "else\n$(OUT): ;\nendif\nall: ;\n" + late_consumer
        )
        case.add("Makefile", source)
        registry = json.loads((ROOT / ".github/validation-ownership-make-dynamics.json").read_text())
        contracts = {item["expression"]: item for item in registry["contracts"]}
        return lambda session: MakeCommands(session, contracts), inputs, output, contracts

    def observe(self, factory, *, names=(), assignments=()):
        case = self.fixture
        session = case.session()
        try:
            with session:
                commands = factory(session)
                native = session.make(
                    "all", makefile="Makefile", variables=("MAKEFILE_LIST", "MAKE_RESTARTS"),
                    definitions=names, assignments=assignments, commands=commands,
                )
                self.native = native
                sources = graph_probe._loaded_sources(session, native, primary_source="Makefile")
                units, inputs, scoped = graph_probe._prepare_rule_templates(
                    session, "all", assignments, commands, native, sources, primary_source="Makefile",
                )
                self.units = units
                usage = graph_probe.source_census(
                    sources, reference_units=units, source_assignments=assignments, source_target="all",
                    template_graph_inputs=inputs, template_scoped=scoped, budget=session.budget,
                )
        finally:
            self.assertIsNone(session.base)
            self.assertFalse(session.budget.children)
            self.assertFalse(session.budget.producer_waiters)
        return usage

    def small_probe(self, contracts):
        session = self.fixture.session()
        try:
            with session:
                try:
                    return graph_probe.run_probe(
                        session.loader, {"all"}, {}, contracts, session=session, scoped_variable_names={"@"},
                    )["all"]
                finally:
                    self.probe_accounting = (session.budget.runs, session.budget.states)
        finally:
            self.assertIsNone(session.base)
            self.assertFalse(session.budget.children)
            self.assertFalse(session.budget.producer_waiters)

    def assert_phase_values(self, output, first, final):
        self.assertEqual(len(self.native.generated), 1)
        self.assertEqual(self.native.generated[0].path, output)
        self.assertEqual(len(self.native.generated[0].data), 347)
        self.assertEqual(self.native.semantics["domains"]["MAKE_RESTARTS"],
                         {"origin": "environment", "flavor": "recursive", "value": "1"})
        definition = {"origin": "file", "flavor": "simple", "value": final}
        self.assertEqual(self.native.semantics["definitions"]["global"]["PHASE_LABEL"], definition)
        self.assertEqual(self.native.semantics["definitions"]["files"],
                         [{"target": "all", "variables": {"PHASE_LABEL": definition}}])
        contexts = self.native.semantics["native_dispatches"]
        self.assertEqual(len(contexts), 2)
        self.assertEqual([context["arguments"] for context in contexts],
                         [event["arguments"] for event in self.native.events])
        for context in contexts:
            self.assertIn("PHASE_LABEL", context["environment"])
            self.assertEqual(context["environment"]["PHASE_LABEL"], first)
        self.assertFalse((self.fixture.root / output).exists())

    def assert_production_read_rejects(
        self, consumer, first, final, *, complete=False, after_phase=False,
        rejection="literal binding module.*consumer",
    ):
        factory, _, output, contracts = self.production(
            consumer="" if after_phase else consumer, late_consumer=consumer if after_phase else "",
        )
        with self.assertRaisesRegex(MakeProbeError, rejection):
            self.observe(factory, names=("PHASE_LABEL",))
        self.assert_phase_values(output, first, final)
        statements = [graph_probe.ASSIGNMENT.fullmatch(chunk.text)
                      for chunk in graph_probe._make_logical_chunks(consumer)]
        self.assertFalse(any(statement and statement["operator"] == "?=" for statement in statements))
        if complete:
            with self.assertRaisesRegex(MakeProbeError, rejection):
                self.small_probe(contracts)
        return self.native.generated[0].data

    def test_generic_unconsumed_literal_module_closes_all_obligations(self):
        for content in (
            "INVENTORY_ITEMS := alpha beta\nENTRY_TOTAL := 2\n",
            "# nonsemantic comment\n  ENTRY_TOTAL :=   2\nINVENTORY_ITEMS := alpha beta\n",
            "PROJECT_LABEL := catalog\nPROJECT_COUNT := 2\n",
        ):
            with self.subTest(content=content):
                factory = self.generic(content)
                usage = self.observe(factory)
                self.assertEqual(usage["literal_binding_modules"], ("build/include.mk",))
                self.assertEqual(len(self.units.phase_tests), 1)
                self.assertEqual(self.native.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
                self.assertEqual(self.native.semantics["files"][0]["prerequisites"], [])

    def test_actual_production_emitter_qualifies_without_candidate_observers(self):
        factory, inputs, output, contracts = self.production()
        self.assertEqual(len(inputs), 15)
        names = (
            "ASSET_MANIFEST_SOURCE_DIGEST", "ASSET_PORTRAIT_INCBIN_CONSUMERS", "ASSET_TMX_INCBIN_CONSUMERS",
            "ASSET_BANIM_INCBIN_CONSUMERS", "ASSET_CUSTOM_SPELL_INCBIN_CONSUMERS",
        )
        usage = self.observe(factory, names=names)
        self.assertEqual(usage["literal_binding_modules"], (output,))
        self.assertEqual([self.native.semantics["definitions"]["global"][name]["value"] for name in names[1:]],
                         ["EIRIKA_FORMATTED_PORTRAIT", "CH2_MAIN_MAP", "LORM_SP1_PROOF", ""])
        with self.fixture.session() as session:
            _, expected = render_discovery_artifact(
                str(ROOT / "assets/manifest.json"), output, tracked_sources=frozenset(inputs),
                source_identities=session.snapshot.owners(inputs),
            )
        self.assertEqual(self.native.generated[0].data, expected.encode())
        ordinary = render_discovery_makefile(load_manifest(str(ROOT / "assets/manifest.json")))
        self.assertNotEqual(ordinary.encode(), self.native.generated[0].data)
        self.assertEqual(ordinary.splitlines()[2:], expected.splitlines()[2:])
        self.assertEqual(len(self.native.events), 2)
        self.assertEqual(self.native.semantics["files"][0]["prerequisites"], [])
        result = self.small_probe(contracts)
        self.assertEqual(result["record"]["includes"], ["Makefile", output])
        body = "$(ASSET_BANIM_INCBIN_CONSUMERS)$(.VARIABLES:%=%)$(shell touch marker)"
        consumer = (
            "UNREAD = " + body + "\n"
            "KIND := $(origin UNREAD)\nFLAVOR := ${flavor UNREAD}\nRAW := $(value UNREAD)\n"
            "ALIAS = $(origin UNREAD)\n"
            "ifeq ($(ALIAS),file)\nPHASE_LABEL := stable\nendif\n"
            "export KIND FLAVOR RAW PHASE_LABEL\n"
        )
        factory, _, output, contracts = self.production(consumer=consumer)
        usage = self.observe(factory, names=("KIND", "FLAVOR", "RAW", "PHASE_LABEL"))
        self.assertEqual(usage["literal_binding_modules"], (output,))
        self.assertEqual(self.native.generated[0].data, expected.encode())
        for name, value in (("KIND", "file"), ("FLAVOR", "recursive"), ("RAW", body), ("PHASE_LABEL", "stable")):
            self.assertEqual(self.native.semantics["definitions"]["global"][name],
                             {"origin": "file", "flavor": "simple", "value": value})
            for context in self.native.semantics["native_dispatches"]:
                self.assertIn(name, context["environment"])
                self.assertEqual(context["environment"][name], value)
        self.assertEqual(self.small_probe(contracts)["record"]["includes"], ["Makefile", output])
        self.assertFalse((self.fixture.root / "marker").exists())

    def test_first_pass_default_and_export_remain_visible_obligations(self):
        consumer = (
            "ifeq ($(origin ASSET_BANIM_INCBIN_CONSUMERS),undefined)\n"
            "FIRST_PASS_ONLY ?= first\nexport FIRST_PASS_ONLY\nendif\n"
        )
        factory, _, _output, _contracts = self.production(consumer=consumer)
        with self.assertRaisesRegex(MakeProbeError, "literal binding module.*consumer"):
            self.observe(factory, names=("FIRST_PASS_ONLY",))
        self.assertEqual(self.native.semantics["definitions"]["global"]["FIRST_PASS_ONLY"]["origin"], "undefined")
        contexts = self.native.semantics["native_dispatches"]
        self.assertTrue(any(context["environment"].get("FIRST_PASS_ONLY") == "first" for context in contexts))

    def test_consumers_aliases_metadata_exports_and_universe_cannot_be_pruned(self):
        generated = {
            self.assert_production_read_rejects(CONDITIONAL_CONSUMER, "first", "final", complete=True),
            self.assert_production_read_rejects(UNIVERSE_CONSUMER, "", ASSET_BINDING, complete=True),
        }
        for declarations, condition, first_branch, second_branch in (
            ("", "ifdef " + ASSET_BINDING, "final", "first"),
            ("SELECTOR = " + ASSET_BINDING + "\n", "ifndef ${SELECTOR}", "first", "final"),
            ("SELECTOR = " + ASSET_BINDING + "\nALIAS = ${SELECTOR}\nNEXT = $(ALIAS)\n",
             "ifdef $(NEXT)", "final", "first"),
        ):
            consumer = (
                declarations + condition + "\nPHASE_LABEL := " + first_branch
                + "\nelse\nPHASE_LABEL := " + second_branch + "\nendif\nexport PHASE_LABEL\n"
            )
            with self.subTest(consumer=consumer):
                generated.add(self.assert_production_read_rejects(consumer, "first", "final"))
        for declarations, expression, computed in (
            ("", "$(.VARIABLES)", False),
            ("", "${.VARIABLES}", False),
            ("", "${.VARIABLES:%=%}", False),
            ("UNIVERSE = $(.VARIABLES:%=%)\nALIAS = ${UNIVERSE}\nSELECTOR = ALIAS\n", "$($(SELECTOR))", True),
            ("PATTERN = %\nREPLACEMENT = %\n", "$(.VARIABLES:${PATTERN}=$(REPLACEMENT))", False),
        ):
            consumer = (declarations + "PHASE_LABEL := $(filter " + ASSET_BINDING + "," + expression
                        + ")\nexport PHASE_LABEL\n")
            with self.subTest(expression=expression):
                if computed:
                    generated.add(self.assert_production_read_rejects(consumer, "", ASSET_BINDING))
                    generated.add(self.assert_production_read_rejects(
                        consumer.replace("SELECTOR = ALIAS\n", "SELECTOR = $(subst X,ALIAS,X)\n"),
                        "", ASSET_BINDING,
                        rejection="literal binding phase must contain one producer rule",
                    ))
                generated.add(self.assert_production_read_rejects(
                    consumer, "", ASSET_BINDING, after_phase=computed,
                ))
        for expression in ("$(call .VARIABLES)", "${call .VARIABLES,unused}"):
            consumer = "PHASE_LABEL := $(filter " + ASSET_BINDING + "," + expression + ")\nexport PHASE_LABEL\n"
            with self.subTest(late_call=expression):
                generated.add(self.assert_production_read_rejects(
                    consumer, "", ASSET_BINDING, complete=True, after_phase=True,
                ))
        for expression, first, final, computed in (
            ("$(" + ASSET_BINDING + ":%=%)", "", "LORM_SP1_PROOF", False),
            ("${${SELECTOR}:%=%}", "", "LORM_SP1_PROOF", True),
            ("$(origin " + ASSET_BINDING + ")", "undefined", "file", False),
            ("${flavor " + ASSET_BINDING + "}", "undefined", "simple", False),
            ("$(value " + ASSET_BINDING + ")", "", "LORM_SP1_PROOF", False),
        ):
            consumer = (
                "SELECTOR = " + ASSET_BINDING + "\nALIAS = " + expression
                + "\nNEXT = ${ALIAS}\nPHASE_LABEL := $(NEXT)\nexport PHASE_LABEL\n"
            )
            with self.subTest(expression=expression):
                if computed:
                    generated.add(self.assert_production_read_rejects(
                        consumer, first, final,
                        rejection="literal binding phase must contain one producer rule",
                    ))
                generated.add(self.assert_production_read_rejects(consumer, first, final, after_phase=computed))
        self.assertEqual(len(generated), 1)
        cases = (
            ("", "READ := $(INVENTORY_ITEMS)\n"),
            ("ALIAS = $(INVENTORY_ITEMS)\n", "READ := $(ALIAS)\n"),
            ("", "NAME = INVENTORY_ITEMS\nREAD := $($(NAME))\n"),
            ("", "READ := $(origin INVENTORY_ITEMS)\n"),
            ("", "READ := $(flavor INVENTORY_ITEMS)\n"),
            ("", "READ := $(value INVENTORY_ITEMS)\n"),
            ("", "READ := $(.VARIABLES)\n"),
            ("NAME = .VARIABLES\n", "READ := ${${NAME}:%=%}\n"),
            ("NAME = .VARIABLES\n", "READ := $(call $(NAME))\n"),
            ("NAME = MAKE_RESTARTS\n", "READ := $($(NAME):%=%)\n"),
            ("NAME = $(subst X,INVENTORY_ITEMS,X)\n", "READ := $($(NAME):%=%)\n"),
            ("", "export INVENTORY_ITEMS\n"),
            ("", "export\n"),
            ("", "READ := $(file <build/include.mk)\n"),
            ("", "ifeq ($(wildcard build/include.mk),)\nFIRST_ONLY ?= value\nendif\n"),
        )
        for prefix, suffix in cases:
            with self.subTest(prefix=prefix, suffix=suffix):
                factory = self.generic(prefix=prefix, suffix=suffix)
                with self.assertRaises(MakeProbeError):
                    self.observe(factory)
        factory = self.generic(prefix="UNUSED = $(INVENTORY_ITEMS)$(shell touch marker)\n")
        self.observe(factory)
        self.assertFalse((self.fixture.root / "marker").exists())

    def test_binding_syntax_original_inputs_and_phase_shape_fail_closed(self):
        for content in (
            "SHELL := sh\n", "CFLAGS := flag\n", "TARGET_ARCH := flag\n", "VPATH := source\n",
            "META = literal\n", "export META := literal\n", "META := $(value OTHER)\n",
            "META := first\nMETA := second\n", "include mode:\nMETA := literal\n",
        ):
            with self.subTest(content=content):
                factory = self.generic(content)
                if content.startswith("include"):
                    self.fixture.add("mode:", "")
                with self.assertRaises(MakeProbeError):
                    self.observe(factory)
        for origin in ("command-line", "environment"):
            factory = self.generic()
            with self.assertRaisesRegex(MakeProbeError, "overridden or exported"):
                self.observe(factory, assignments=((origin, "INVENTORY_ITEMS", "input"),))
        factory = self.generic()
        source = (self.fixture.root / "Makefile").read_text()
        self.fixture.add("Makefile", source.replace("$(INC): ;", "other: ;"))
        with self.assertRaises(MakeProbeError):
            self.observe(factory)
        factory = self.generic()
        self.fixture.add("Makefile", (self.fixture.root / "Makefile").read_text().replace(
            "else\n$(INC): ;\nendif\n", "else\n$(INC): ;\nendif\n$(INC): extra\nextra:\n",
        ))
        with self.assertRaisesRegex(MakeProbeError, "another possible rule owner"):
            self.observe(factory)
        factory = self.generic()
        witness = "scripts/generated_data/chapterbundle/__init__.py"
        (self.fixture.root / witness).unlink()
        del self.fixture.entries[witness]
        with self.assertRaisesRegex(MakeProbeError, "empty source witness"):
            self.observe(factory)

    def test_certificate_and_phase_obligations_have_independent_removal_controls(self):
        certify = graph_probe._certify_literal_bindings
        reference_base = graph_probe._make_reference_base

        def without_resolved_reads(*args, **kwargs):
            return certify(*args, **{**kwargs, "read_names": ()})

        def whole_body_universe(body, **kwargs):
            base = reference_base(body, **kwargs)
            return body if base == ".VARIABLES" else base

        generated = set()
        for attribute, restored, consumer, first, final in (
            ("_certify_literal_bindings", without_resolved_reads, CONDITIONAL_CONSUMER, "first", "final"),
            ("_make_reference_base", whole_body_universe, UNIVERSE_CONSUMER, "", ASSET_BINDING),
        ):
            with self.subTest(restored=attribute):
                factory, _, output, contracts = self.production(consumer=consumer)
                with patch.object(graph_probe, attribute, restored):
                    usage = self.observe(factory, names=("PHASE_LABEL",))
                    self.assert_phase_values(output, first, final)
                    self.assertEqual(usage["literal_binding_modules"], (output,))
                    self.assertEqual(usage["defaults"], set())
                    result = self.small_probe(contracts)
                    self.assertEqual(result["record"]["includes"], ["Makefile", output])
                generated.add(self.native.generated[0].data)
                self.assert_production_read_rejects(consumer, first, final, complete=True)
        self.assertEqual(len(generated), 1)
        with patch.object(graph_probe, "_certify_literal_bindings", without_resolved_reads):
            self.assert_production_read_rejects(UNIVERSE_CONSUMER, "", ASSET_BINDING)
        with patch.object(graph_probe, "_make_reference_base", whole_body_universe):
            self.assert_production_read_rejects(CONDITIONAL_CONSUMER, "first", "final")
        factory = self.generic()
        with patch.object(graph_probe, "_literal_binding_include", return_value=None):
            with self.assertRaisesRegex(MakeProbeError, "unproven generated include source history"):
                self.observe(factory)
        consumer = (
            "ifeq ($(origin ASSET_BANIM_INCBIN_CONSUMERS),undefined)\n"
            "FIRST_PASS_ONLY ?= first\nexport FIRST_PASS_ONLY\nendif\n"
        )
        factory, _, output, _ = self.production(consumer=consumer)
        with patch.object(graph_probe, "_certify_literal_bindings", return_value=None):
            usage = self.observe(factory, names=("FIRST_PASS_ONLY",))
        self.assertEqual(usage["literal_binding_modules"], (output,))
        self.assertEqual(self.native.semantics["definitions"]["global"]["FIRST_PASS_ONLY"]["origin"], "undefined")
        self.assertIn("FIRST_PASS_ONLY", usage["defaults"])
        self.assertTrue(any(context["environment"].get("FIRST_PASS_ONLY") == "first"
                            for context in self.native.semantics["native_dispatches"]))
        factory = self.generic()
        source = (self.fixture.root / "Makefile").read_text()
        self.fixture.add("Makefile", source.replace(
            "ifeq ($(MAKE_RESTARTS),)\n", "ifeq ($(MAKE_RESTARTS),)\nFIRST_PHASE_ONLY := value\n",
        ))
        with self.assertRaisesRegex(MakeProbeError, "phase must contain"):
            self.observe(factory)

        def unproved_phases(units, observation, budget):
            return frozenset(unit.phase for _, _, unit in units.ordered if unit.phase is not None)

        with patch.object(graph_probe, "_literal_binding_phases", unproved_phases):
            usage = self.observe(factory)
        self.assertEqual(usage["literal_binding_modules"], ("build/include.mk",))

    def test_literal_module_name_and_deadline_admissions_are_unchanged(self):
        budget = ProbeBudget()
        body = "".join("METADATA_" + str(index) + " := literal\n" for index in range(512)).encode()
        try:
            self.assertEqual(len(graph_probe._literal_binding_include("build/metadata.mk", body, budget)), 512)
            with self.assertRaisesRegex(MakeProbeError, "existing name bound"):
                graph_probe._literal_binding_include("build/metadata.mk", body + b"ONE_MORE := literal\n", budget)
        finally:
            budget.close()
        with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline"):
            graph_probe._literal_binding_include("build/metadata.mk", body, budget)
