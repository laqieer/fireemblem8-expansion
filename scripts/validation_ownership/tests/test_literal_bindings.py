import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.assets.manifest import (
    discovery_sources, load_manifest, render_discovery_artifact, render_discovery_makefile,
)
from scripts.validation_ownership import graph_probe
from scripts.validation_ownership.authority import GitTreeEntry
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.graph_commands import CODE_PREFIXES, MakeCommands
from scripts.validation_ownership.tests.test_make_probe import AuthoritativeMakeProbeTests


ROOT = Path(__file__).resolve().parents[3]


class LiteralBindingModuleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = AuthoritativeMakeProbeTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def generic(self, content="INVENTORY_ITEMS := alpha beta\nENTRY_TOTAL := 2\n", *, prefix="", suffix=""):
        case = self.fixture
        commands = case.include_writer(content)
        case.original_input_witness()
        case.add("Makefile", prefix + "INC := build/include.mk\n-include $(INC)\n.PHONY: FORCE\nFORCE:\n"
                 "ifeq ($(MAKE_RESTARTS),)\n$(INC): FORCE\n\t@python3 writer.py\n"
                 "else\n$(INC): ;\nendif\n" + suffix + "all: ;\n")
        return commands

    def production(self, *, consumer=""):
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
            "else\n$(OUT): ;\nendif\nall: ;\n"
        )
        case.add("Makefile", source)
        registry = json.loads((ROOT / ".github/validation-ownership-make-dynamics.json").read_text())
        contracts = {item["expression"]: item for item in registry["contracts"]}
        return lambda session: MakeCommands(session, contracts), inputs, output, contracts

    def observe(self, factory, *, names=(), assignments=()):
        case = self.fixture
        with case.session() as session:
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
        self.assertIsNone(session.base)
        self.assertFalse(session.budget.children)
        return usage

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
        with self.fixture.session() as session:
            result = graph_probe.run_probe(
                session.loader, {"all"}, {}, contracts, session=session, scoped_variable_names={"@"},
            )["all"]
        self.assertEqual(result["record"]["includes"], ["Makefile", output])

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
        cases = (
            ("", "READ := $(INVENTORY_ITEMS)\n"),
            ("ALIAS = $(INVENTORY_ITEMS)\n", "READ := $(ALIAS)\n"),
            ("", "NAME = INVENTORY_ITEMS\nREAD := $($(NAME))\n"),
            ("", "READ := $(origin INVENTORY_ITEMS)\n"),
            ("", "READ := $(flavor INVENTORY_ITEMS)\n"),
            ("", "READ := $(value INVENTORY_ITEMS)\n"),
            ("", "READ := $(.VARIABLES)\n"),
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
