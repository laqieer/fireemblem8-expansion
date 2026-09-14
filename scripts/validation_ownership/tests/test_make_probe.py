import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import shutil
import unittest
from unittest.mock import patch

from scripts.validation_ownership import reporter
from scripts.validation_ownership.authority import AuthorityLoader, ENVIRONMENT, GitTreeEntries, GitTreeEntry, encoded
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.budget import Limits, MAX_PLANNED_STATE_BYTES
from scripts.validation_ownership.graph_probe import _MakeSourceMode, make_source_units, run_probe, source_census
from scripts.validation_ownership.make_probe import ProbeSession
from scripts.validation_ownership.tests.test_foundation import _PendingTrafficLimits


ROOT = Path(__file__).resolve().parents[3]


class AuthoritativeMakeProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "build/test-artifacts/graph-probe" / secrets.token_hex(12)
        self.root = self.directory / "repo"
        self.root.mkdir(parents=True)
        self.entries = {}

    def tearDown(self):
        shutil.rmtree(self.directory)

    def add(self, name, value):
        data = value.encode()
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.entries[name] = GitTreeEntry(name, "100644", "blob", hashlib.sha1(data).hexdigest())

    def session(self, budget=None, *, runtime_files=()):
        budget = ProbeBudget() if budget is None else budget
        loader = AuthorityLoader(self.root, GitTreeEntries(self.entries, budget=budget), budget=budget)
        return ProbeSession(loader, scratch_root=self.root / "build/probe", budget=budget, runtime_files=runtime_files)

    def framework_templates(self, *, renamed=False):
        for path, name in (
            ("generated_data.mk", "GENERATED_DATA_LINK_TABLE_RULES"),
            ("modern.mk", "GENERATED_DATA_MODERN_OVERRIDE_RULES"),
        ):
            source = (ROOT / path).read_text()
            start = source.index("define " + name + "\n")
            stop = source.index("\nendef", start) + len("\nendef")
            caller = next(line for line in source.split("\n") if line.startswith("$(foreach ") and name in line)
            selected = source[start:stop] + "\n\n" + caller + "\n"
            self.add(path, selected.replace(name, "PROJECT_" + name) if renamed else selected)
        self.add("Makefile", (
            ".DEFAULT_GOAL := all\n"
            "GENERATED_DATA_OUT_DIR := build/generated/data\n"
            "GENERATED_DATA_LINKED_HAND_SOURCES := src/data_alpha.c src/data_beta.c\n"
            "GENERATED_DATA_LINKED_TABLES := $(patsubst src/data_%.c,%,$(GENERATED_DATA_LINKED_HAND_SOURCES))\n"
            "GENERATED_DATA_SHARED_PY_SOURCES := $(wildcard scripts/generated_data/*.py)\n"
            "GENERATED_DATA_CONFIG_INPUTS_alpha := include/alpha.h\n"
            "GENERATED_DATA_CONFIG_INPUTS_beta := include/beta.h\n"
            "GENERATED_DATA_PY := /usr/bin/python3 -m scripts.generated_data\n"
            "MODERN_OUTPUT_DIR := build/modern\n"
            "MODERN_CC := /usr/bin/arm-none-eabi-gcc\n"
            "MODERN_CFLAGS := -mthumb -mcpu=arm7tdmi -O2\n"
            "SELECTED_TABLE := alpha\n"
            "include generated_data.mk\ninclude modern.mk\n"
            "all: $(MODERN_OUTPUT_DIR)/src/data_$(SELECTED_TABLE).o\n"
            ".SECONDEXPANSION:\n"
        ))
        for name in ("alpha", "beta"):
            self.add("src/data/" + name + ".json", '{"value":1}\n')
            self.add("include/" + name + ".h", "/* fixture config */\n")
            self.add("scripts/generated_data/" + name + "/schema.py", "# fixture module input\n")
        self.add("scripts/generated_data/__init__.py", "")
        self.add("scripts/generated_data/__main__.py", (
            "import argparse,json\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser();p.add_argument('mode');p.add_argument('--table');p.add_argument('--out-dir')\n"
            "a=p.parse_args();data=json.loads(Path('src/data/'+a.table+'.json').read_text())\n"
            "out=Path(a.out_dir);out.mkdir(parents=True,exist_ok=True)\n"
            "(out/('data_'+a.table+'.c')).write_text('const int data_'+a.table+'='+str(data['value'])+';\\n')\n"
        ))

    def observe_framework(self, domains=None, *, symbolic=()):
        with self.session() as session:
            result = run_probe(
                session.loader, {"all"}, domains or {}, {}, session=session,
                scoped_variable_names={"1", "t", "@", "@D", "<"},
                symbolic_recipe_names={"MODERN_CC", "MODERN_CFLAGS", "GENERATED_DATA_PY", *symbolic},
                declared_external_names=set(symbolic),
            )
        self.assertIsNone(session.base)
        self.assertFalse(session.budget.children)
        return result["all"]

    def observe(self, domains=None, *, targets=("all",), **options):
        domains = domains or {}
        with self.session() as session:
            result = run_probe(
                session.loader, set(targets), domains, {}, session=session,
                declared_external_names=set(domains) | set(options.pop("external", ())),
                **options,
            )
            self.last_accounting = (session.budget.runs, session.budget.states)
        self.assertIsNone(session.base)
        self.assertFalse(session.budget.children)
        self.assertFalse(session.budget.producer_waiters)
        return result

    def recipe_pages(self, **options):
        with self.session() as session:
            with patch.object(session, "make", wraps=session.make) as made:
                result = run_probe(session.loader, {"all"}, {}, {}, session=session, **options)
            pages = [
                (tuple(call.kwargs.get("variables", ())), tuple(call.kwargs.get("definitions", ())))
                for call in made.call_args_list
            ]
        self.assertIsNone(session.base)
        self.assertFalse(session.budget.children)
        return result["all"]["record"]["variants"][0]["record"], pages

    def ordinary(self, *assignments, environment=None):
        budget = ProbeBudget()
        try:
            actual = budget.run(
                ["/usr/bin/make", "--no-print-directory", *assignments, "all"],
                cwd=self.root, env={**ENVIRONMENT, **(environment or {})},
            )
            self.assertEqual(actual.returncode, 0, actual.stderr)
            return actual.stdout
        finally:
            budget.close()
            self.assertFalse(budget.children)

    def mode_values(self, source, *, includes=None, expected=None, assignments=()):
        source += "all:\n\t@printf '%s\\n' '$(value FIRST)' '$(value SECOND)'; :\n"
        self.add("Makefile", source)
        for name, body in (includes or {}).items():
            self.add(name, body)
        ordinary = self.ordinary(
            *(name + "=" + value for origin, name, value in assignments if origin == "command-line"),
            environment={name: value for origin, name, value in assignments if origin == "environment"},
        ).decode().splitlines()
        with self.session() as session:
            native = session.make(
                "all", variables=("MAKEFILE_LIST",), definitions=("FIRST", "SECOND"), assignments=assignments,
            )
            values = [native.semantics["definitions"]["global"][name]["value"] for name in ("FIRST", "SECOND")]
            self.last_mode_dispatches = native.semantics["native_dispatches"]
            sources = {
                name: (self.root / name).read_bytes()
                for name in native.semantics["domains"]["MAKEFILE_LIST"]["value"].split()
            }
        self.assertEqual(ordinary, values)
        if expected is not None:
            self.assertEqual(values, expected)
        return sources, values

    def mode_first(self, source, *, includes=None, assignments=()):
        source += "$(info $(value FIRST))\nall: ;\n"
        self.add("Makefile", source)
        for name, value in (includes or {}).items():
            self.add(name, value)
        ordinary = self.ordinary(
            *(name + "=" + value for origin, name, value in assignments if origin == "command-line"),
            environment={name: value for origin, name, value in assignments if origin == "environment"},
        ).decode().splitlines()[0]
        with self.session() as session:
            native = session.make("all", variables=("MAKEFILE_LIST",), definitions=("FIRST",), assignments=assignments)
        value = native.semantics["definitions"]["global"]["FIRST"]["value"]
        self.assertEqual(ordinary, value)
        visits = native.semantics["domains"]["MAKEFILE_LIST"]["value"].split()
        return {name: (self.root / name).read_bytes() for name in visits}, value, visits

    def original_input_witness(self):
        path = "scripts/generated_data/chapterbundle/__init__.py"
        value = (ROOT / path).read_text()
        self.assertEqual(value, "")
        self.add(path, value)

    def original_effects_probe(self, source, *, includes=None, external=()):
        self.original_input_witness()
        self.add("Makefile", source)
        for name, value in (includes or {}).items():
            self.add(name, value)
        with self.session() as session:
            with patch.object(session, "original_make_inputs", wraps=session.original_make_inputs) as original:
                result = run_probe(
                    session.loader, {"all"}, {}, {}, session=session,
                    declared_external_names=set(external),
                    ambient_undefined_names={"OS", "DEVKITARM", "UNDEFINED", "MISSING"},
                    trusted_builtin_names={"PATH", "MAKECMDGOALS", "MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS"},
                )
            queried = {name for call in original.call_args_list for name in call.args[1]}
        self.assertFalse(session.budget.children)
        self.assertIsNone(session.base)
        return result["all"], queried

    def shell_assignment_contract(self, *, effectful=True):
        producer = (
            """python3 -c 'print(chr(36)+"(eval .POSIX:)")'"""
            if effectful else """python3 -c 'print("first")'"""
        )
        return producer, {"fixture": {
            "id": "shell-assignment-fixture", "command_regex": re.escape(producer), "input_files": [],
        }}

    def test_conditionals_and_finite_origins_are_native_make_observations(self):
        self.add("Makefile", "MODE ?= one\nall: $(MODE)\none two: ;\n")
        result = self.observe({
            "MODE": {"kind": "explicit", "values": ["one", "two"]},
        }, environment_names={"MODE"})
        records = result["all"]["record"]["variants"]
        self.assertEqual({tuple(state["state"][0]) for state in records if state["state"]}, {
            ("command-line", "MODE", "one"), ("command-line", "MODE", "two"),
            ("environment", "MODE", "one"), ("environment", "MODE", "two"),
        })
        self.assertEqual({state["record"]["files"][0]["prerequisites"][0]["name"] for state in records},
                         {"one", "two"})
        self.assertEqual(result["all"]["prerequisite_domain_census"]["enumerated"], ["MODE"])

    def test_graph_plan_admission_accumulates_before_queued_execution(self):
        self.add("Makefile", "MODE ?= one\nall: $(MODE)\none two: ;\n")
        budget = ProbeBudget(_PendingTrafficLimits(seconds=30, runs=64))
        domains = {"MODE": {"kind": "explicit", "values": ["one", "two"]}}
        with self.session(budget) as session:
            original = budget.admit_planned_state
            admitted = []

            def admit(size):
                before = budget.states, budget.bytes.get("pending", 0)
                original(size)
                self.assertEqual(budget.states, before[0])
                self.assertEqual(budget.bytes["pending"] - before[1], size)
                admitted.append(size)

            expected = 0
            with patch.object(budget, "admit_planned_state", side_effect=admit), \
                 patch.object(session, "make", wraps=session.make) as made:
                for _ in range(2):
                    result = run_probe(session.loader, {"all"}, domains, {}, session=session)
                    variants = result["all"]["record"]["variants"]
                    self.assertEqual(
                        {item["record"]["files"][0]["prerequisites"][0]["name"] for item in variants},
                        {"one", "two"},
                    )
                    expected += sum(len(encoded(item["state"])) for item in variants if item["state"])
                    self.assertEqual(budget.planned_state_bytes, expected)
                    self.assertEqual(sum(admitted), expected)
                    self.assertEqual(budget.states, made.call_count)
                budget.admit_planned_state(MAX_PLANNED_STATE_BYTES - expected)
                before = made.call_count
                with self.assertRaisesRegex(MakeProbeError, "aggregate planned-state"):
                    run_probe(session.loader, {"all"}, domains, {}, session=session)
                self.assertEqual(made.call_count, before + 1)
                self.assertEqual(budget.states, made.call_count)
        self.assertEqual(budget.planned_state_bytes, MAX_PLANNED_STATE_BYTES)
        self.assertTrue(budget.closed)
        self.assertFalse(budget.children)
        self.assertFalse(budget.producer_waiters)

    def test_all_targets_share_one_budget_and_keep_standalone_makecmdgoals(self):
        self.add("Makefile", "LOCAL = $(MAKECMDGOALS)\none two:\n\t@echo $(LOCAL)\n")
        result = self.observe(targets=("one", "two"), trusted_builtin_names={"MAKECMDGOALS"})
        self.assertEqual(set(result), {"one", "two"})
        self.assertEqual(self.last_accounting[1], 4)
        for name in result:
            self.assertEqual(result[name]["record"]["variants"][0]["record"]["files"][0]["target"], name)
            self.assertEqual(result[name]["record"]["variants"][0]["record"]["files"][0]["variables"]["LOCAL"]["value"], name)

    def test_unsealed_default_undefined_and_dynamic_default_reject(self):
        for source, expected in (
            ("UNSEALED ?= x\nall: ;\n", "unsealed external defaults"),
            ("all: $(UNSEALED)\n", "unsealed undefined"),
            ("NAME = SELECT\n$(NAME) ?= x\nall: ;\n", "dynamic name"),
        ):
            with self.subTest(source=source):
                self.add("Makefile", source)
                with self.assertRaisesRegex(MakeProbeError, expected):
                    self.observe()

    def test_actual_error_and_missing_prerequisite_propagate(self):
        for source in ("$(error reject real input)\nall: ;\n", "all: missing.bin\n"):
            with self.subTest(source=source):
                self.add("Makefile", source)
                with self.assertRaises(MakeProbeError):
                    self.observe()

    def test_unknown_optional_include_attempts_reject_even_when_make_ignores_absence(self):
        for path in ("missing-untracked.mk", "./missing-untracked.mk", "/repo/missing-untracked.mk"):
            with self.subTest(path=path):
                self.add("Makefile", "-include " + path + "\nall: ;\n")
                with self.assertRaisesRegex(MakeProbeError, "unadmitted Make file-open"):
                    self.observe()

    def test_optional_tracked_include_keeps_actual_source_ownership(self):
        self.add("Makefile", "-include known.mk\nall: ;\n")
        self.add("known.mk", "# Existing admitted source.\n")
        result = self.observe()["all"]["record"]
        self.assertEqual(result["includes"], ["Makefile", "known.mk"])

    def test_native_open_attempts_keep_ignored_absence_and_raw_spelling(self):
        self.add("Makefile", "-include /repo/missing-untracked.mk\nall: ;\n")
        with self.session() as session:
            observed = session.make("all", variables=("MAKEFILE_LIST",))
            self.assertEqual(observed.semantics["domains"]["MAKEFILE_LIST"]["value"], "Makefile")
            self.assertIn(
                ("/repo/missing-untracked.mk", "/repo/missing-untracked.mk"),
                observed.file_open_attempts,
            )
            self.assertIn(("/repo/Makefile", "Makefile"), observed.file_open_attempts)
        self.assertFalse(session.budget.children)

    def test_unknown_eager_command_never_creates_its_marker(self):
        self.add("Makefile", "VALUE != touch marker\nall: ;\n")
        with self.assertRaises(MakeProbeError):
            self.observe()
        self.assertFalse((self.root / "marker").exists())

    def test_real_eval_target_and_automatic_reference_census(self):
        self.add("Makefile", (
            "define RULE\nselected: input\n\t@echo $$@ $$<\nendef\n"
            "$(eval $(RULE))\nall: selected\n"
        ))
        self.add("input", "data\n")
        result = self.observe(scoped_variable_names={"@", "<"})
        variant = result["all"]["record"]["variants"][0]["record"]
        selected = next(item for item in variant["files"] if item["target"] == "selected")
        self.assertEqual(selected["prerequisites"], [{"name": "input", "order_only": False}])
        self.assertIn("$@", selected["recipe"])

    def test_eval_emitted_defaults_use_ordinary_external_sealing(self):
        for caller in ("$(eval $(RULE))", "$(eval $(call RULE))", "$(eval $(FORWARD))"):
            with self.subTest(caller=caller):
                self.add("Makefile", "define RULE\nMODE ?= first\nall: $$(MODE)\nendef\n"
                         "FORWARD = $(call RULE)\n" + caller + "\nfirst second: ;\n")
                with self.session() as session:
                    actual = [
                        session.make("all", assignments=(("command-line", "MODE", value),))
                        .semantics["files"][0]["prerequisites"][0]["name"]
                        for value in ("first", "second")
                    ]
                self.assertEqual(actual, ["first", "second"])
                with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
                    self.observe()
                with self.assertRaisesRegex(MakeProbeError, "cyclic"):
                    source_census({"Makefile": b"NAME = $(NAME)\nall: $($(NAME))\n"})
                with self.assertRaisesRegex(MakeProbeError, "symbolic inputs influence"):
                    self.observe(external={"MODE"}, symbolic_recipe_names={"MODE"})
                result = self.observe({
                    "MODE": {"kind": "explicit", "values": ["first", "second"]},
                }, environment_names={"MODE"})["all"]
                self.assertEqual(result["variable_census"]["defaults"], ["MODE"])
                self.assertEqual(result["prerequisite_domain_census"], {
                    "used": ["MODE"], "enumerated": ["MODE"], "generated_paths": [],
                })
                variants = result["record"]["variants"]
                self.assertEqual({tuple(variant["state"][0]) for variant in variants if variant["state"]}, {
                    (origin, "MODE", value) for origin in ("command-line", "environment") for value in ("first", "second")
                })
                self.assertEqual({
                    variant["record"]["files"][0]["prerequisites"][0]["name"] for variant in variants
                }, {"first", "second"})

    def test_computed_consumption_seals_reached_eval_defaults(self):
        for declarations, expression in (
            ("NAME = RULE\n", "$(RULE)"),
            ("NAME = RULE\n", "$($(NAME))"),
            ("NAME = RULE\n", "${${NAME}}"),
            ("NAME = RULE\n", "$(call $(NAME))"),
            ("NAME = RULE\nALIAS = $($(NAME))\n", "$(ALIAS)"),
            ("NAME = RULE\nRESULT := $($(NAME))\n", ""),
        ):
            with self.subTest(expression=expression, declarations=declarations):
                self.add("Makefile", "define RULE\n$(eval MODE ?= first)\nendef\n" + declarations
                         + "all: " + expression + " $(MODE)\n\t@echo $(MODE)\nfirst second: ;\n")
                with self.session() as session:
                    actual = [
                        session.make("all", assignments=(("command-line", "MODE", value),))
                        .semantics["files"][0]["prerequisites"][0]["name"] for value in ("first", "second")
                    ]
                self.assertEqual(actual, ["first", "second"])
                with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
                    self.observe()
                result = self.observe({"MODE": {"kind": "explicit", "values": ["first", "second"]}},
                                      environment_names={"MODE"})["all"]
                self.assertEqual(result["variable_census"]["defaults"], ["MODE"])
                self.assertEqual(result["prerequisite_domain_census"]["enumerated"], ["MODE"])
                self.assertEqual({
                    variant["record"]["files"][0]["prerequisites"][0]["name"]
                    for variant in result["record"]["variants"]
                }, {"first", "second"})
                self.assertEqual({
                    variant["state"][0][0] for variant in result["record"]["variants"] if variant["state"]
                }, {"command-line", "environment"})

    def test_consumption_fixed_point_retains_new_selector_histories(self):
        self.add("Makefile", "SEED = $(eval NEXT = MIDDLE)\nMIDDLE = $(eval LAST = RULE)\n"
                 "RULE = $(eval MODE ?= first)\nENTRY = SEED\n"
                 "all: $($(ENTRY)) $($(NEXT)) $($(LAST)) $(MODE)\n\t@echo $(MODE)\nfirst second: ;\n")
        self.assertEqual(self.ordinary("MODE=second"), b"second\n")
        with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
            self.observe()
        result = self.observe({"MODE": {"kind": "explicit", "values": ["first", "second"]}})["all"]
        self.assertEqual(result["variable_census"]["defaults"], ["MODE"])
        self.assertEqual(result["prerequisite_domain_census"]["enumerated"], ["MODE"])
        self.assertEqual({
            variant["record"]["files"][0]["prerequisites"][0]["name"]
            for variant in result["record"]["variants"]
        }, {"first", "second"})

    def test_fixed_point_keeps_unused_metadata_bodies_lazy_and_rejects_ambiguity(self):
        for sink in ("all: ;\n", "all: $(origin RULE)\nfile: ;\n"):
            self.add("Makefile", "RULE = $(eval MODE ?= first)$(shell touch marker)\n"
                     "NAME = RULE\nUNUSED = $($(NAME))\n" + sink)
            result = self.observe()["all"]
            self.assertEqual(result["variable_census"]["defaults"], [])
            self.assertFalse((self.root / "marker").exists())
        self.add("Makefile", "RULE = $(eval MODE ?= first)\nNAME = $(subst X,RULE,X)\n"
                 "all: $($(NAME)) $(MODE)\nfirst second: ;\n")
        with self.session() as session:
            actual = session.make("all", assignments=(("command-line", "MODE", "second"),))
        self.assertEqual(actual.semantics["files"][0]["prerequisites"][0]["name"], "second")
        with self.assertRaisesRegex(MakeProbeError, "computed selector"):
            self.observe()
        self.add("Makefile", "RULE = $(eval MODE ?= first)\n"
                 "all: $(origin RULE) $(RULE) $(MODE)\nfile first second: ;\n")
        with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
            self.observe()

    def test_emitted_default_modifiers_and_scope_match_source_declarations(self):
        for modifiers in ("", "export ", "private ", "override ", "export override private "):
            with self.subTest(modifiers=modifiers):
                ordinary = modifiers + "MODE ?= first\nall: $(MODE)\nfirst second: ;\n"
                expected = source_census({"Makefile": ordinary.encode()})["defaults"]
                self.add("Makefile", ordinary)
                with self.session() as session:
                    direct = session.make("all", definitions=("MODE",))
                self.add("Makefile", "define RULE\n" + modifiers + "MODE ?= first\nall: $$(MODE)\nendef\n"
                         "$(eval $(call RULE))\nfirst second: ;\n")
                with self.session() as session:
                    emitted = session.make("all", definitions=("MODE",))
                self.assertEqual(emitted.semantics["definitions"]["global"]["MODE"],
                                 direct.semantics["definitions"]["global"]["MODE"])
                self.assertEqual(emitted.semantics["files"][0]["prerequisites"],
                                 direct.semantics["files"][0]["prerequisites"])
                if expected:
                    with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
                        self.observe()
                else:
                    self.assertEqual(self.observe()["all"]["variable_census"]["defaults"], [])
                result = self.observe({"MODE": {"kind": "explicit", "values": ["first", "second"]}})["all"]
                self.assertEqual(result["variable_census"]["defaults"], sorted(expected))
        self.add("Makefile", "define RULE\nall: private MODE ?= first\nall:\n\t@echo $$(MODE)\nendef\n"
                 "$(eval $(RULE))\n")
        with self.session() as session:
            baseline = session.make("all", definitions=("MODE",))
            variant = session.make("all", definitions=("MODE",), assignments=(("command-line", "MODE", "second"),))
        self.assertEqual(baseline.semantics["definitions"]["global"]["MODE"]["origin"], "undefined")
        self.assertEqual(baseline.semantics["definitions"]["files"][0]["variables"]["MODE"]["value"], "first")
        self.assertEqual(variant.semantics["definitions"]["global"]["MODE"]["value"], "second")
        self.assertEqual(variant.semantics["definitions"]["files"][0]["variables"]["MODE"]["origin"], "undefined")
        self.assertEqual(self.ordinary(), b"first\n")
        self.assertEqual(self.ordinary("MODE=second"), b"\n")
        with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
            self.observe()
        result = self.observe({"MODE": {"kind": "explicit", "values": ["first", "second"]}})["all"]
        self.assertEqual(result["variable_census"]["defaults"], ["MODE"])

    def test_unused_default_bodies_and_literal_default_text_do_not_execute(self):
        self.add("Makefile", "define UNUSED\nMODE ?= first\n$(shell touch marker)\nendef\n"
                 "DEFERRED = $(eval OTHER ?= first)$(shell touch marker)\n"
                 "TEXT = this is MODE ?= first\nall:\n\t@printf '%s\\n' 'OTHER ?= first'\n")
        with self.session() as session:
            native = session.make("all", definitions=("UNUSED", "DEFERRED", "MODE", "OTHER"))
            self.assertEqual(native.semantics["definitions"]["global"]["MODE"]["origin"], "undefined")
            self.assertEqual(native.semantics["definitions"]["global"]["OTHER"]["origin"], "undefined")
            self.assertIn("$(shell touch marker)", native.semantics["definitions"]["global"]["UNUSED"]["value"])
            self.assertFalse((session.tree / "marker").exists())
        result = self.observe()["all"]
        self.assertEqual(result["variable_census"]["defaults"], [])
        self.assertEqual(result["prerequisite_domain_census"]["enumerated"], [])
        self.assertFalse((self.root / "marker").exists())
        self.assertEqual(self.ordinary(), b"OTHER ?= first\n")

    def test_conditional_define_headers_retain_their_own_default_contract(self):
        for prefix in ("", "override "):
            self.add("Makefile", prefix + "define CHOICE ?=\nfirst\nendef\nall: ;\n")
            with self.session() as session:
                native = session.make("all", definitions=("CHOICE",))
            self.assertEqual(native.semantics["definitions"]["global"]["CHOICE"]["value"], "first")
            if not prefix:
                with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
                    self.observe()
            result = self.observe(external={"CHOICE"})["all"]
            self.assertEqual(result["variable_census"]["defaults"], [] if prefix else ["CHOICE"])

    def test_recipe_symbolic_inputs_cannot_be_graph_selectors(self):
        self.add("Makefile", "FLAGS ?= default\nall:\n\t@echo $(FLAGS)\n")
        result = self.observe(external={"FLAGS"}, symbolic_recipe_names={"FLAGS"})
        self.assertEqual(result["all"]["record"]["symbolic_recipe_names"], ["FLAGS"])
        self.add("Makefile", "FLAGS ?= selected\nALIAS = $(FLAGS)\nall: $(ALIAS)\nselected: ;\n")
        with self.assertRaisesRegex(MakeProbeError, "symbolic inputs influence"):
            self.observe(external={"FLAGS"}, symbolic_recipe_names={"FLAGS"})

    def test_computed_graph_positions_close_real_selected_domains(self):
        expressions = (
            ("prerequisite", "all: $($(NAME))\nfirst second: ;\n"),
            ("order-only", "all: | $($(NAME))\nfirst second: ;\n"),
            ("target", "$($(NAME)): ;\nall: $($(NAME))\n"),
            ("secondary", ".SECONDEXPANSION:\nall: $$($$(NAME))\nfirst second: ;\n"),
            ("definition", "ALIAS = $($(NAME))\nall: $(ALIAS)\nfirst second: ;\n"),
            ("eval", "$(eval all: $($(NAME)))\nfirst second: ;\n"),
            ("call", "define RULE\nall: $($(NAME))\nendef\n$(eval $(call RULE))\nfirst second: ;\n"),
            ("computed-call", "SELECTOR = RULE\ndefine RULE\nall: $(FLAGS)\nendef\n"
                             "$(eval $(call $(SELECTOR)))\nfirst second: ;\n"),
            ("braced", "all: ${${NAME}}\nfirst second: ;\n"),
            ("partial", "all: $(F$(NAME))\nfirst second: ;\n"),
            ("target-local-secondary", ".SECONDEXPANSION:\nall: NAME = FLAGS\nall: $$($$(NAME))\nfirst second: ;\n"),
        )
        domain = {"FLAGS": {"kind": "explicit", "values": ["first", "second"]}}
        for position, body in expressions:
            with self.subTest(position=position):
                name = "LAGS" if position == "partial" else "FLAGS"
                self.add("Makefile", "FLAGS ?= first\nNAME = " + name + "\n" + body + "all:\n\t@echo $(FLAGS)\n")
                with self.session() as session:
                    actual = {
                        session.make("all", assignments=(("command-line", "FLAGS", value),))
                        .semantics["files"][0]["prerequisites"][0]["name"]
                        for value in ("first", "second")
                    }
                self.assertEqual(actual, {"first", "second"})
                with self.assertRaises(MakeProbeError):
                    self.observe(external={"FLAGS"}, symbolic_recipe_names={"FLAGS"})
                observed = self.observe(domain)["all"]
                self.assertEqual(observed["prerequisite_domain_census"]["enumerated"], ["FLAGS"])
                self.assertEqual({
                    item["record"]["files"][0]["prerequisites"][0]["name"]
                    for item in observed["record"]["variants"]
                }, actual)

    def test_deferred_and_constructed_eval_selectors_keep_actual_graph_domains(self):
        cases = (
            ("simple", "DEPS := $$($(NAME))\n.SECONDEXPANSION:\nall: $(DEPS)\n"),
            ("recursive", "DEPS = $$($$(NAME))\n.SECONDEXPANSION:\nall: $(DEPS)\n"),
            ("eval", "DOLLAR := $$\nDEP =\nall: $(eval DEP = $(DOLLAR)($(DOLLAR)(NAME))) $(DEP)\n"),
        )
        self.last_staged_evidence = []
        for name, body in cases:
            with self.subTest(stage=name):
                self.add("Makefile", "FLAGS ?= first\nNAME = FLAGS\n" + body
                         + "\t@echo $(FLAGS)\nfirst second: ;\n")
                self.assertEqual(self.ordinary("FLAGS=first"), b"first\n")
                self.assertEqual(self.ordinary("FLAGS=second"), b"second\n")
                with self.assertRaises(MakeProbeError):
                    self.observe(external={"FLAGS"}, symbolic_recipe_names={"FLAGS"})
                result = self.observe({"FLAGS": {"kind": "explicit", "values": ["first", "second"]}})["all"]
                self.assertEqual(result["prerequisite_domain_census"]["enumerated"], ["FLAGS"])
                observed = {
                    item["record"]["files"][0]["prerequisites"][0]["name"]
                    for item in result["record"]["variants"]
                }
                self.assertEqual(observed, {"first", "second"})
                self.last_staged_evidence.append({
                    "stage": name, "prerequisites": sorted(observed),
                    "enumerated": result["prerequisite_domain_census"]["enumerated"],
                    "runs_states": self.last_accounting,
                })

    def test_native_raw_definitions_do_not_expand_unused_variable_bodies(self):
        self.add("Makefile", "ifneq ($(origin VO_OBSERVE_RAW_NAMES),undefined)\n$(error observer input leaked)\nendif\n"
                 "UNUSED = $(shell touch marker)\nNAME = global\nall: NAME = local\nall: ;\n")
        with self.session() as session:
            result = session.make("all", definitions=("UNUSED", "NAME"))
            definitions = result.semantics["definitions"]
            self.assertEqual(definitions["global"]["UNUSED"], {
                "value": "$(shell touch marker)", "origin": "file", "flavor": "recursive",
            })
            self.assertEqual(definitions["global"]["NAME"]["value"], "global")
            self.assertEqual(definitions["files"][0]["variables"]["NAME"]["value"], "local")
            self.assertEqual(result.semantics["domains"], {})
            self.assertEqual(result.semantics["files"][0]["variables"], {})
            self.assertEqual(result.semantics["native_dispatches"], [])
            self.assertEqual(result.events, ())
            self.assertFalse((session.tree / "marker").exists())
        self.assertFalse((self.root / "marker").exists())
        self.assertIsNone(session.base)
        self.assertFalse(session.budget.children)
        for variables, definitions in ((("NAME",), ("NAME",)), ((), tuple("VALUE_" + str(index) for index in range(513)))):
            with self.subTest(names=len(definitions)), self.session() as session:
                before = session.budget.runs
                with self.assertRaises(MakeProbeError):
                    session.make("all", variables=variables, definitions=definitions)
                self.assertEqual(session.budget.runs, before)

    def test_unresolved_generated_stages_reject_instead_of_omitting_domains(self):
        cases = (
            ".SECONDEXPANSION:\nall: $(DOLLAR)($(DOLLAR)(NAME))\n",
            "DEP =\nall: $(eval DEP := $(DOLLAR)($(DOLLAR)(NAME))) $(DEP)\n",
            "DEP =\nall: $(eval DEP = $(DOLLAR)($(DOLLAR)(NAME))) $(DEP) $(eval DEP =)\n",
        )
        for body in cases:
            with self.subTest(body=body):
                self.add("Makefile", "FLAGS ?= first\nNAME = FLAGS\nDOLLAR := $$\n" + body
                         + "\t@echo $(FLAGS)\nfirst second: ;\n")
                self.assertEqual(self.ordinary("FLAGS=first"), b"first\n")
                self.assertEqual(self.ordinary("FLAGS=second"), b"second\n")
                with self.assertRaises(MakeProbeError):
                    self.observe({"FLAGS": {"kind": "explicit", "values": ["first", "second"]}})

    def test_partial_dollar_templates_reject_in_all_graph_stages(self):
        templates = (
            ("paren", "PREFIX := $$(F\nEND := )\n", "$(PREFIX)LAGS$(END)", ""),
            ("brace", "PREFIX := $${F\nEND := }\n", "$(PREFIX)LAGS$(END)", ""),
            ("multiple", "PREFIX := $$(F\nMIDDLE := LA\nEND := GS)\n", "$(PREFIX)$(MIDDLE)$(END)", ""),
            ("nested", "NAME = FLAGS\nPREFIX := $$($$(N\nMIDDLE := A\nEND := ME))\n",
             "$(PREFIX)$(MIDDLE)$(END)", ""),
            ("nested-brace", "NAME = FLAGS\nPREFIX := $${$${N\nMIDDLE := A\nEND := ME}}\n",
             "$(PREFIX)$(MIDDLE)$(END)", ""),
            ("embedded", "PREFIX := prefix$$(F\nEND := )\n", "$(PREFIX)LAGS$(END)", "prefix"),
        )
        self.last_fragment_evidence = []
        for template, declarations, expression, prefix in templates:
            for stage in ("secondary", "immediate-eval", "rule-eval", "secondary-with-unrelated-eval"):
                with self.subTest(template=template, stage=stage):
                    if stage.startswith("secondary"):
                        body = ".SECONDEXPANSION:\nall: " + expression + "\n"
                        if stage == "secondary-with-unrelated-eval":
                            body = "$(eval UNUSED = harmless)\n" + body
                    elif stage == "immediate-eval":
                        body = "DEP =\nall: $(eval DEP := " + expression + ") $(DEP)\n"
                    else:
                        body = "$(eval all: " + expression + ")\nall:\n"
                    self.add("Makefile", "FLAGS ?= first\n" + declarations + body
                             + "\t@echo $(FLAGS)\nfirst second prefixfirst prefixsecond: ;\n")
                    actual = []
                    with self.session() as session:
                        for value in ("first", "second"):
                            native = session.make("all", assignments=(("command-line", "FLAGS", value),))
                            actual.append(native.semantics["files"][0]["prerequisites"][0]["name"])
                    self.assertEqual(actual, [prefix + "first", prefix + "second"])
                    for symbolic in (True, False):
                        with self.assertRaises(MakeProbeError):
                            if symbolic:
                                self.observe(external={"FLAGS"}, symbolic_recipe_names={"FLAGS"})
                            else:
                                self.observe({"FLAGS": {"kind": "explicit", "values": ["first", "second"]}})
                    self.last_fragment_evidence.append({
                        "template": template, "stage": stage, "native_prerequisites": actual,
                        "symbolic_rejected": True, "finite_rejected": True,
                    })

    def test_recursive_eval_resolves_partial_templates_and_unused_fragments_stay_unused(self):
        for opening, closing, stage in (
            ("$$(F", ")", ""), ("$${F", "}", ""),
            ("$$(F", ")", ".SECONDEXPANSION:\n"), ("$${F", "}", ".SECONDEXPANSION:\n"),
        ):
            with self.subTest(opening=opening, stage=stage):
                self.add("Makefile", "FLAGS ?= first\nPREFIX := " + opening + "\nEND := " + closing
                         + "\nDEP =\n" + stage + "all: $(eval DEP = $(PREFIX)LAGS$(END)) $(DEP)\n"
                         "\t@echo $(FLAGS)\nfirst second: ;\n")
                self.assertEqual(self.ordinary("FLAGS=first"), b"first\n")
                self.assertEqual(self.ordinary("FLAGS=second"), b"second\n")
                with self.assertRaises(MakeProbeError):
                    self.observe(external={"FLAGS"}, symbolic_recipe_names={"FLAGS"})
                result = self.observe({"FLAGS": {"kind": "explicit", "values": ["first", "second"]}})["all"]
                self.assertEqual(result["prerequisite_domain_census"]["enumerated"], ["FLAGS"])
                self.assertEqual({
                    item["record"]["files"][0]["prerequisites"][0]["name"]
                    for item in result["record"]["variants"]
                }, {"first", "second"})
        self.add("Makefile", "UNUSED_PREFIX := $$(F\nUNUSED_BODY = $(shell touch marker)\n"
                 ".SECONDEXPANSION:\nall: ;\n")
        self.observe()
        self.assertFalse((self.root / "marker").exists())

    def assert_staged_rejection_before_late_observation(self):
        for symbolic in (True, False):
            with self.subTest(symbolic=symbolic), self.session() as session:
                domains = {} if symbolic else {"FLAGS": {"kind": "explicit", "values": ["first", "second"]}}
                with patch.object(session, "make", wraps=session.make) as calls:
                    with self.assertRaisesRegex(MakeProbeError, "unproven emitted-reference"):
                        run_probe(
                            session.loader, {"all"}, domains, {}, session=session,
                            declared_external_names={"FLAGS"},
                            symbolic_recipe_names={"FLAGS"} if symbolic else (),
                        )
                    self.assertEqual(calls.call_count, 1)
                    self.assertEqual(calls.call_args.kwargs.get("definitions", ()), ())
            self.assertIsNone(session.base)
            self.assertFalse(session.budget.children)

    def test_opaque_staged_transformations_do_not_invent_emitted_reference_authority(self):
        transformations = (
            ("subst", "", "$(subst OTHER,FLAGS,$(TEXT))"),
            ("patsubst", "END := )\n", "$(patsubst %OTHER$(END),%FLAGS$(END),$(TEXT))"),
            ("substitution-reference", "END := )\n", "$(TEXT:OTHER$(END)=FLAGS$(END))"),
            ("call-builtin", "subst = ignored variable\n", "$(call subst,OTHER,FLAGS,$(TEXT))"),
            ("call-macro", "CHANGE = $(subst OTHER,FLAGS,$(TEXT))\n", "$(call CHANGE)"),
        )
        for name, declarations, expression in transformations:
            for stage in ("secondary", "immediate-eval", "rule-eval"):
                with self.subTest(transformation=name, stage=stage):
                    if stage == "secondary":
                        body = ".SECONDEXPANSION:\nall: $(PAYLOAD)\n"
                    elif stage == "immediate-eval":
                        body = "DEP =\nall: $(eval DEP := $(PAYLOAD)) $(DEP)\n"
                    else:
                        body = "$(eval all: $(PAYLOAD))\nall:\n"
                    self.add("Makefile", "FLAGS ?= first\nOTHER = first\nTEXT := $$(OTHER)\n"
                             + declarations + "PAYLOAD = " + expression + "\n" + body
                             + "\t@echo $(FLAGS)\nfirst second: ;\n")
                    self.assertEqual(self.ordinary("FLAGS=first"), b"first\n")
                    self.assertEqual(self.ordinary("FLAGS=second"), b"second\n")
                    with self.session() as session:
                        actual = [
                            session.make("all", assignments=(("command-line", "FLAGS", value),))
                            .semantics["files"][0]["prerequisites"][0]["name"]
                            for value in ("first", "second")
                        ]
                    self.assertEqual(actual, ["first", "second"])
                    self.assert_staged_rejection_before_late_observation()

    def test_late_or_stateful_payload_values_cannot_supply_original_stage_evidence(self):
        self.last_late_evidence = []
        for mode in ("stateful", "rewritten"):
            with self.subTest(mode=mode):
                payload = "$(subst OTHER,FLAGS,$(TEXT))"
                if mode == "stateful":
                    payload = "$(eval SEEN += x)$(if $(word 2,$(SEEN)),first," + payload + ")"
                source = (
                    "FLAGS ?= first\nOTHER = first\nTEXT := $$(OTHER)\nSEEN =\nPAYLOAD = " + payload
                    + "\n.SECONDEXPANSION:\nall: $(PAYLOAD)\n\t@echo $(FLAGS)\n"
                    + ("\t$(eval PAYLOAD = first)\n" if mode == "rewritten" else "")
                    + "first second: ;\n"
                )
                self.add("Makefile", source)
                self.assertEqual(self.ordinary("FLAGS=second"), b"second\n")
                with self.session() as session:
                    later = session.make(
                        "all", assignments=(("command-line", "FLAGS", "second"),),
                        variables=("PAYLOAD",) if mode == "stateful" else (),
                        definitions=("PAYLOAD",) if mode == "rewritten" else (),
                    )
                    self.assertEqual(later.semantics["files"][0]["prerequisites"][0]["name"], "second")
                    observed = (
                        later.semantics["domains"] if mode == "stateful"
                        else later.semantics["definitions"]["global"]
                    )
                    self.assertEqual(observed["PAYLOAD"]["value"], "first")
                    self.last_late_evidence.append({
                        "mode": mode, "actual_prerequisite": "second", "later_payload": observed["PAYLOAD"]["value"],
                    })
                self.assert_staged_rejection_before_late_observation()

    def test_complete_logical_and_define_bodies_reject_original_rewritten_transformations(self):
        declarations = (
            "PAYLOAD = $(subst OTHER,FLAGS,\\\n  $(TEXT))\n",
            "define PAYLOAD\n$(subst OTHER,FLAGS,\n$(TEXT))\nendef\n",
            "define PAYLOAD\n$(subst OTHER,FLAGS,\\\n  $(TEXT))\nendef\n",
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                self.add("Makefile", "FLAGS ?= first\nOTHER = first\nTEXT := $$(OTHER)\n"
                         + declaration + ".SECONDEXPANSION:\nall: $(PAYLOAD)\n"
                         "\t@echo $(FLAGS)\n\t$(eval PAYLOAD = first)\nfirst second: ;\n")
                self.assertEqual(self.ordinary("FLAGS=second"), b"second\n")
                with self.session() as session:
                    actual = session.make(
                        "all", assignments=(("command-line", "FLAGS", "second"),), definitions=("PAYLOAD",),
                    )
                    self.assertEqual(actual.semantics["files"][0]["prerequisites"][0]["name"], "second")
                    self.assertEqual(actual.semantics["definitions"]["global"]["PAYLOAD"]["value"], "first")
                self.assert_staged_rejection_before_late_observation()

    def test_selector_history_cannot_be_replaced_by_a_later_native_value(self):
        bodies = (
            "NAME ?= FLAGS\nall: $($(NAME))\n\t@echo $(FLAGS)\nNAME = OTHER\n",
            "NAME ?= FLAGS\n.SECONDEXPANSION:\nall: $$($$(NAME))\n\t@echo $(FLAGS)\n\t$(eval NAME = OTHER)\n",
            "$(eval NAME = FLAGS)\nall: $($(NAME))\n\t@echo $(FLAGS)\nNAME = OTHER\n",
            "define SET_NAME\nNAME = FLAGS\nendef\n$(eval $(SET_NAME))\n"
            "all: $($(NAME))\n\t@echo $(FLAGS)\nNAME = OTHER\n",
        )
        for body in bodies:
            with self.subTest(body=body):
                self.add("Makefile", "FLAGS ?= first\nOTHER = first\n" + body + "first second: ;\n")
                with self.session() as session:
                    actual = session.make(
                        "all", assignments=(("command-line", "FLAGS", "second"),), variables=("NAME",),
                    )
                    self.assertEqual(actual.semantics["files"][0]["prerequisites"][0]["name"], "second")
                    self.assertEqual(actual.semantics["domains"]["NAME"]["value"], "OTHER")
                domains = {"NAME": {"kind": "tracked-fallback"}}
                with self.assertRaises(MakeProbeError):
                    self.observe(domains, external={"FLAGS"}, symbolic_recipe_names={"FLAGS"})
                result = self.observe({
                    **domains, "FLAGS": {"kind": "explicit", "values": ["first", "second"]},
                })["all"]
                self.assertEqual(result["prerequisite_domain_census"]["enumerated"], ["FLAGS", "NAME"])
                self.assertEqual({
                    variant["record"]["files"][0]["prerequisites"][0]["name"]
                    for variant in result["record"]["variants"]
                }, {"first", "second"})
        self.add("Makefile", "FLAGS ?= first\nNAME ?= $(subst X,FLAGS,X)\nOTHER = first\n"
                 "all: $($(NAME))\n\t@echo $(FLAGS)\nNAME = OTHER\nfirst second: ;\n")
        with self.assertRaises(MakeProbeError):
            self.observe({"NAME": {"kind": "tracked-fallback"}, "FLAGS": {"kind": "explicit", "values": ["first", "second"]}})
        for assignment in ("$(eval $(DEST) = FLAGS)", "$(DEST) = FLAGS"):
            self.add("Makefile", "FLAGS ?= first\nDEST = NAME\nOTHER = first\n" + assignment
                     + "\nall: $($(NAME))\n\t@echo $(FLAGS)\nNAME = OTHER\nfirst second: ;\n")
            with self.session() as session:
                native = session.make("all", assignments=(("command-line", "FLAGS", "second"),), variables=("NAME",))
                self.assertEqual(native.semantics["files"][0]["prerequisites"][0]["name"], "second")
                self.assertEqual(native.semantics["domains"]["NAME"]["value"], "OTHER")
            with self.assertRaisesRegex(MakeProbeError, "original assignment history"):
                self.observe({"NAME": {"kind": "tracked-fallback"}, "FLAGS": {"kind": "explicit", "values": ["first", "second"]}})

    def test_make_logical_source_matches_native_definitions_without_shell_folding(self):
        for posix in (False, True):
            with self.subTest(posix=posix):
                source = (".POSIX:\n" if posix else "") + (
                    "# ignored continuation \\\nHIDDEN = $(shell touch marker)\n"
                    "VALUE = alpha  \\\n \t\\\n beta\n"
                    "HASH = a\\#b\nHASH_EXPR = $(subst x,#,x)\n"
                    "define BLOCK\none\\\n two\n\tendef # retained body data\nthree\nendef\n"
                    "all: ;\n"
                )
                self.add("Makefile", source)
                census = source_census({"Makefile": source.encode()})
                self.assertNotIn("HIDDEN", census["defined"])
                with self.session() as session:
                    native = session.make("all", definitions=("VALUE", "HASH", "HASH_EXPR", "BLOCK"))
                    for name, value in native.semantics["definitions"]["global"].items():
                        self.assertEqual(census["definitions"][name], [value["value"]])
                    self.assertFalse((session.tree / "marker").exists())
                self.assertFalse((self.root / "marker").exists())
        source = "all:\n\t@printf '%s\\n' 'one\\\n\t#two'\n"
        self.add("Makefile", source)
        recipe = next(unit.text for unit in make_source_units(source) if unit.text.startswith("\t"))
        with self.session() as session:
            native = session.make("all")
            self.assertEqual(recipe[1:] + "\n", native.semantics["files"][0]["recipe"])

    def test_posix_mode_keeps_gnu_delayed_statement_and_define_activation(self):
        continued = "alpha  \\\n  \\\n beta"
        values = "FIRST = " + continued + "\nSECOND = " + continued + "\n"
        for source, expected in (
            (".POSIX:\n" + values, ["alpha beta", "alpha    beta"]),
            (".PHONY .POSIX:\n" + values, ["alpha beta", "alpha    beta"]),
            (".POSIX:\nifeq (yes,no)\nIGNORED = yes\nendif\n" + values, ["alpha beta", "alpha    beta"]),
            (".POSIX:\ndefine FIRST\n" + continued + "\nendef\nSECOND = " + continued + "\n",
             ["alpha    beta", "alpha    beta"]),
            (".POSIX := variable-not-target\n" + values, ["alpha beta", "alpha beta"]),
            (".POSIX: LOCAL = variable-not-rule\n" + values, ["alpha beta", "alpha beta"]),
        ):
            with self.subTest(source=source):
                sources, native = self.mode_values(source, expected=expected)
                actual = source_census(sources)
                self.assertEqual([actual["definitions"][name][0] for name in ("FIRST", "SECOND")], native)

    def test_original_invocation_supports_the_actual_make_guard_prefix(self):
        prefix = (ROOT / "Makefile").read_text().split("\nvalidation-ownership-check:\n", 1)[0]
        source = prefix + (
            "\nvalidation-ownership-check: measurement\nelse\nall other: measurement\nendif\n"
            "PROBE = alpha  \\\n  \\\n beta\nmeasurement:\n\t@printf '%s\\n' '$(value PROBE)'\n"
        )
        self.add("Makefile", source)
        controls = {"MAKECMDGOALS", "MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEOVERRIDES"}
        for target in ("all", "other", "validation-ownership-check"):
            with self.subTest(target=target):
                budget = ProbeBudget()
                try:
                    ordinary = budget.run(["/usr/bin/make", "-f", "Makefile", target], cwd=self.root, env=ENVIRONMENT)
                    self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
                    self.assertEqual(ordinary.stdout, b"alpha beta\n")
                finally:
                    budget.close()
                    self.assertFalse(budget.children)
                with self.session() as session:
                    native = session.make(target, variables=("MAKECMDGOALS",),
                                          definitions=("PROBE", "_VALIDATION_OWNERSHIP_FLAGS"))
                self.assertEqual(native.semantics["domains"]["MAKECMDGOALS"], {
                    "value": target, "origin": "default", "flavor": "simple",
                })
                self.assertEqual(native.semantics["definitions"]["global"]["PROBE"]["value"], "alpha beta")
                guard = native.semantics["definitions"]["global"]["_VALIDATION_OWNERSHIP_FLAGS"]
                self.assertEqual(guard["origin"], "override" if target == "validation-ownership-check" else "undefined")
                observed = self.observe(targets=(target,), trusted_builtin_names=controls)[target]
                self.assertEqual(observed["record"]["variants"][0]["record"]["files"][0]["target"], target)
                self.assertEqual(source_census({"Makefile": source.encode()}, source_target=target)["definitions"]["PROBE"],
                                 ["alpha beta"])

    def test_original_native_inputs_precede_candidate_source_and_keep_raw_origins(self):
        self.original_input_witness()
        self.add("Makefile", "$(error candidate source must not run)\nall: ;\n")
        with self.session() as session:
            result = session.original_make_inputs(
                "all", ("OS", "PATH", "CC", "MAYBE"),
                assignments=(("environment", "MAYBE", "$(error unused body)"),),
            )
            self.assertEqual(result["OS"], {"origin": "undefined", "flavor": "undefined", "value": ""})
            self.assertEqual(result["PATH"], {"origin": "environment", "flavor": "recursive", "value": ENVIRONMENT["PATH"]})
            self.assertEqual(result["CC"]["origin"], "default")
            self.assertEqual(result["MAYBE"]["value"], "$(error unused body)")
            self.assertEqual(session.budget.states, 1)
        with self.session() as session:
            before = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "invocation controls"):
                session.original_make_inputs("all", ("MAKEFLAGS",))
            self.assertEqual(session.budget.runs, before)
        with self.session() as session:
            before = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "bounded name contract"):
                session.original_make_inputs("all", tuple("INPUT_" + str(index) for index in range(513)))
            self.assertEqual(session.budget.runs, before)

    def test_complete_actual_tools_prefix_has_original_input_effect_evidence(self):
        original = (ROOT / "Makefile").read_text()
        source = original[:original.index("\nASFLAGS  :=")] + (
            "\nendif\nassets-check:\n\t@/usr/bin/printf '%s\\n' '$(value CPPFLAGS)'\n"
        )
        self.original_input_witness()
        self.add("Makefile", source)
        registry = json.loads((ROOT / ".github/validation-ownership-make-dynamics.json").read_text())
        uname = next(row for row in registry["contracts"] if row["id"] == "host-uname")
        from scripts.validation_ownership.graph_commands import MakeCommands
        budget = ProbeBudget()
        try:
            ordinary = budget.run(["/usr/bin/make", "-f", "Makefile", "assets-check"], cwd=self.root, env=ENVIRONMENT)
            self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
        finally:
            budget.close()
        contracts = {uname["expression"]: uname}
        with self.session() as session:
            native = session.make("assets-check", definitions=("CPPFLAGS",), commands=MakeCommands(session, contracts))
            expected = native.semantics["definitions"]["global"]["CPPFLAGS"]["value"]
            self.assertEqual(ordinary.stdout, (expected + "\n").encode())
            with patch.object(session, "original_make_inputs", wraps=session.original_make_inputs) as original_inputs:
                result = run_probe(
                    session.loader, {"assets-check"}, {}, contracts, session=session,
                    declared_external_names={
                        "TOOLCHAIN", "PREFIX", "CPP", "PYTHON", "HOST_CC",
                        "AUTOTOOLS_CONFIG_MK", "AUTOTOOLS_BUILD_DIR", "EXPANSION_HQ_MIXER",
                    },
                    ambient_undefined_names={"OS", "DEVKITARM", "MAKEOVERRIDES", "FE8_ITEM_ID_CAP"},
                    trusted_builtin_names={"PATH", "MAKECMDGOALS", "MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS"},
                )["assets-check"]
            queried = {name for call in original_inputs.call_args_list for name in call.args[1]}
            self.assertTrue({"OS", "PATH", "EXE"} <= queried)
        record = result["record"]["variants"][0]["record"]
        self.assertEqual(record["definitions"]["global"]["CPPFLAGS"]["value"], expected)
        self.assertIn("-undef -DFE8_ARCHIVAL_BUILD=1", expected)
        self.assertNotIn("-undef  -DFE8_ARCHIVAL_BUILD=1", expected)
        self.assertEqual(len(result["record"]["variants"]), 1)
        self.assertFalse(session.budget.children)

    def test_original_effect_free_inputs_and_branch_alternatives_do_not_invent_modes(self):
        continuation = "CPPFLAGS := -DFIRST=1 \\\n -DSECOND=2\nall:\n\t@/usr/bin/printf '%s\\n' '$(value CPPFLAGS)'\n"
        for prefix, expected_input in (
            ("ifeq ($(OS),Windows_NT)\nEXE := .exe\nelse\nEXE :=\nendif\nAS := as$(EXE)\n", "OS"),
            ("CHOICE = yes\nifeq ($(CHOICE),yes)\nVALUE := one\nelse\nVALUE := two\nendif\nCOPY := $(VALUE)\n", "VALUE"),
            ("export PATH := /usr/bin:$(PATH)\n", "PATH"),
            ("CONFIG = absent.mk\n-include $(wildcard $(CONFIG))\n", None),
        ):
            with self.subTest(prefix=prefix):
                result, queried = self.original_effects_probe(prefix + continuation)
                self.assertEqual(self.ordinary(), b"-DFIRST=1 -DSECOND=2\n")
                if expected_input is not None:
                    self.assertIn(expected_input, queried)
                value = result["record"]["variants"][0]["record"]["definitions"]["global"]["CPPFLAGS"]["value"]
                self.assertEqual(value, "-DFIRST=1 -DSECOND=2")

    def test_original_input_effect_evidence_does_not_hide_effectful_alternatives(self):
        continuation = "CPPFLAGS := -DFIRST=1 \\\n -DSECOND=2\nall: ;\n"
        cases = (
            "OS = $(eval .POSIX:)Linux\nifeq ($(OS),Windows_NT)\nVALUE = one\nelse\nVALUE = two\nendif\n",
            "CHOICE = yes\nVALUE = $(eval .POSIX:)\nifeq ($(CHOICE),yes)\nVALUE = literal\nendif\nCOPY := $(VALUE)\n",
            "$(eval OS = $(eval .POSIX:)Linux)\nCOPY := $(OS)\n",
        )
        for prefix in cases:
            with self.subTest(prefix=prefix):
                self.original_input_witness()
                self.add("Makefile", prefix + continuation)
                with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
                    self.original_effects_probe(prefix + continuation)
        with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
            self.original_effects_probe(
                "SELECT = mode.mk\ninclude $(SELECT)\n" + continuation,
                includes={"mode.mk": "$(eval .POSIX:)\n"},
            )

    def test_mode_failure_reports_real_source_span_and_first_input_without_values(self):
        source = "# diagnostic prefix\nifeq ($(UNDEFINED),yes)\nVALUE = one\nendif\nCPPFLAGS := first \\\n second\nall: ;\n"
        budget = ProbeBudget()
        try:
            with self.assertRaises(MakeProbeError) as rejected:
                source_census({"diagnostic.mk": source.encode()}, budget=budget)
        finally:
            budget.close()
        message = str(rejected.exception)
        self.assertIn("diagnostic.mk:5-6", message)
        self.assertIn("logical 5", message)
        self.assertIn("first uncertainty diagnostic.mk:2", message)
        self.assertIn("[UNDEFINED]", message)
        self.assertNotIn("CPPFLAGS :=", message)
        self.assertIsInstance(rejected.exception.__cause__, MakeProbeError)

    def test_recursive_shell_assignment_cannot_hide_conditional_defaults(self):
        from scripts.validation_ownership.graph_commands import MakeCommands
        producer, contracts = self.shell_assignment_contract()
        self.add("Makefile", "PAYLOAD != " + producer + "\nRESULT := $(PAYLOAD)\n"
                 "ifeq (alpha  \\\n beta,alpha beta)\nIGNORED = yes\nelse\nMODE ?= first\nendif\n"
                 "all: $(MODE)\n\t@echo $(MODE)\nfirst second: ;\n")
        self.assertEqual(self.ordinary("MODE=first"), b"first\n")
        self.assertEqual(self.ordinary("MODE=second"), b"second\n")
        with self.session() as session:
            commands = MakeCommands(session, contracts)
            native = [
                session.make("all", definitions=("PAYLOAD",), assignments=(("command-line", "MODE", value),),
                             commands=commands)
                for value in ("first", "second")
            ]
        self.assertEqual([value.semantics["files"][0]["prerequisites"][0]["name"] for value in native], ["first", "second"])
        self.assertEqual(native[0].semantics["definitions"]["global"]["PAYLOAD"], {
            "value": "$(eval .POSIX:)", "origin": "file", "flavor": "recursive",
        })
        self.assertEqual(len(native[0].semantics["dynamic_commands"]), 1)
        for domains in ({}, {"MODE": {"kind": "explicit", "values": ["first", "second"]}}):
            with self.session() as session:
                with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
                    run_probe(session.loader, {"all"}, domains, contracts, session=session)
            self.assertFalse(session.budget.children)
            self.assertIsNone(session.base)

    def test_shell_assignment_flavor_is_preserved_through_defines_eval_and_modifiers(self):
        from scripts.validation_ownership.graph_commands import MakeCommands
        producer, contracts = self.shell_assignment_contract()
        declarations = (
            "PAYLOAD != " + producer + "\n",
            "private PAYLOAD != " + producer + "\n",
            "export PAYLOAD != " + producer + "\n",
            "override PAYLOAD != " + producer + "\n",
            "define PAYLOAD !=\n" + producer + "\nendef\n",
            "$(eval PAYLOAD != " + producer + ")\n",
            "PAYLOAD != " + producer + "\nPAYLOAD += literal\n",
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration):
                source = declaration + "RESULT := $(PAYLOAD)\nCPPFLAGS := first \\\n second\n"
                source += "$(info $(value CPPFLAGS))\nall: ;\n"
                self.add("Makefile", source)
                self.assertEqual(self.ordinary().splitlines()[0], b"first  second")
                with self.session() as session:
                    native = session.make("all", definitions=("PAYLOAD", "CPPFLAGS"), commands=MakeCommands(session, contracts))
                self.assertEqual(native.semantics["definitions"]["global"]["PAYLOAD"]["flavor"], "recursive")
                self.assertIn("$(eval .POSIX:)", native.semantics["definitions"]["global"]["PAYLOAD"]["value"])
                self.assertEqual(native.semantics["definitions"]["global"]["CPPFLAGS"]["value"], "first  second")
                with self.session() as session:
                    with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
                        run_probe(session.loader, {"all"}, {}, contracts, session=session)

    def test_safe_unused_shell_results_and_simple_shell_snapshots_remain_supported(self):
        from scripts.validation_ownership.graph_commands import MakeCommands
        producer, contracts = self.shell_assignment_contract()
        source = "PAYLOAD != " + producer + "\nCPPFLAGS := first \\\n second\nall:\n"
        source += "\t@printf '%s\\n' '$(flavor PAYLOAD)' '$(value PAYLOAD)' '$(value CPPFLAGS)'\n"
        self.add("Makefile", source)
        self.assertEqual(self.ordinary(), b"recursive\n$(eval .POSIX:)\nfirst second\n")
        with self.session() as session:
            result = run_probe(session.loader, {"all"}, {}, contracts, session=session)["all"]
        self.assertEqual(result["record"]["variants"][0]["record"]["definitions"]["global"]["PAYLOAD"]["flavor"], "recursive")
        for operator in (":=", "::="):
            with self.subTest(operator=operator):
                source = "PAYLOAD " + operator + " $(shell " + producer + ")\nRESULT := $(PAYLOAD)\n"
                source += "CPPFLAGS := first \\\n second\nall:\n\t@printf '%s\\n' '$(value CPPFLAGS)'\n"
                self.add("Makefile", source)
                self.assertEqual(self.ordinary(), b"first second\n")
                with self.session() as session:
                    native = session.make("all", definitions=("PAYLOAD", "CPPFLAGS"), commands=MakeCommands(session, contracts))
                    self.assertEqual(native.semantics["definitions"]["global"]["PAYLOAD"]["flavor"], "simple")
                    self.assertEqual(native.semantics["definitions"]["global"]["PAYLOAD"]["value"], "$(eval .POSIX:)")
                    run_probe(session.loader, {"all"}, {}, contracts, session=session)
        producer, contracts = self.shell_assignment_contract(effectful=False)
        self.add("Makefile", "PAYLOAD != " + producer + "\nall: $(PAYLOAD)\nfirst: ;\n")
        with self.session() as session:
            result = run_probe(session.loader, {"all"}, {}, contracts, session=session)["all"]
        self.assertEqual(result["record"]["variants"][0]["record"]["files"][0]["prerequisites"], [
            {"name": "first", "order_only": False},
        ])

    def test_immediate_define_rhs_effects_and_target_shell_flavor_remain_native(self):
        from scripts.validation_ownership.graph_commands import MakeCommands
        producer, contracts = self.shell_assignment_contract(effectful=False)
        for operator, body in ((":=", "$(eval MODE ?= first)"), ("::=", "$(eval MODE ?= first)"),
                               ("!=", "$(eval MODE ?= first)" + producer)):
            with self.subTest(operator=operator):
                self.add("Makefile", "define UNUSED " + operator + "\n" + body
                         + "\nendef\nall: $(MODE)\nfirst: ;\n")
                with self.session() as session:
                    native = session.make("all", definitions=("MODE", "UNUSED"), commands=MakeCommands(session, contracts))
                self.assertEqual(native.semantics["definitions"]["global"]["MODE"]["value"], "first")
                self.assertEqual(native.semantics["definitions"]["global"]["UNUSED"]["flavor"],
                                 "recursive" if operator == "!=" else "simple")
                with self.session() as session:
                    with self.assertRaises(MakeProbeError):
                        run_probe(session.loader, {"all"}, {}, contracts, session=session)
        self.add("Makefile", "all: PAYLOAD != " + producer + "\nall:\n\t@echo $(PAYLOAD)\n")
        self.assertEqual(self.ordinary(), b"first\n")
        with self.session() as session:
            native = session.make("all", definitions=("PAYLOAD",), commands=MakeCommands(session, contracts))
            self.assertEqual(native.semantics["definitions"]["global"]["PAYLOAD"]["origin"], "undefined")
            self.assertEqual(native.semantics["definitions"]["files"][0]["variables"]["PAYLOAD"]["flavor"], "recursive")

    def test_unknown_mode_accepts_only_equivalent_assignment_constructs(self):
        assignment = "  override _VALIDATION_OWNERSHIP_FLAGS := \\\n\t$(strip $(MAKEFLAGS) $(MFLAGS) $(GNUMAKEFLAGS))"
        self.assertIn(assignment, (ROOT / "Makefile").read_text())
        for activation in ("$(eval UNUSED = literal)\n", "$(eval .POSIX:)\n"):
            with self.subTest(activation=activation):
                self.add("Makefile", activation + assignment + "\nall: ;\n")
                with self.session() as session:
                    native = session.make("all", definitions=("_VALIDATION_OWNERSHIP_FLAGS",))
                self.assertEqual(native.semantics["definitions"]["global"]["_VALIDATION_OWNERSHIP_FLAGS"], {
                    "value": "", "origin": "override", "flavor": "simple",
                })
                actual = source_census({"Makefile": (activation + assignment + "\nall: ;\n").encode()}, source_target="all")
                self.assertEqual(actual["definitions"]["_VALIDATION_OWNERSHIP_FLAGS"], [
                    "$(strip $(MAKEFLAGS) $(MFLAGS) $(GNUMAKEFLAGS))",
                ])
        for statement in (
            "override VALUE := \\\n literal",
            "export VALUE = \\\n literal",
            "all: private VALUE := \\\n literal",
        ):
            with self.subTest(statement=statement):
                mode = _MakeSourceMode(posix=None)
                folded = mode.collapse(statement, construct=True)
                self.assertIsNone(mode.posix)
                self.assertIn("literal", folded)
                with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
                    mode.collapse("NEXT = alpha  \\\n beta", construct=True)

    def test_unknown_mode_does_not_normalize_meaningful_values_or_define_data(self):
        for statement in (
            "VALUE = alpha  \\\n beta",
            "VALUE := $(subst x,alpha  \\\n beta,x)",
            "all: VALUE = alpha  \\\n beta",
        ):
            with self.subTest(statement=statement):
                with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
                    _MakeSourceMode(posix=None).collapse(statement, construct=True)
        body = "define BODY\nVALUE := \\\n literal\nendef\n"
        with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
            list(make_source_units(body, mode=_MakeSourceMode(posix=None)))
        for activation, expected in (("$(eval UNUSED = literal)\n", "alpha beta"),
                                     ("$(eval .POSIX:)\n", "alpha    beta")):
            source = activation + "VALUE := \\\n literal\nFIRST = alpha  \\\n  \\\n beta\n"
            sources, actual, _ = self.mode_first(source)
            self.assertEqual(actual, expected)
            with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
                source_census(sources, source_target="all")

    def test_original_control_facts_do_not_survive_source_changes_or_unknown_eval(self):
        continued = "FIRST = alpha  \\\n  \\\n beta\n"
        source = "MAKECMDGOALS = $(eval .POSIX:)\nRESULT := $(MAKECMDGOALS)\n" + continued
        sources, native, _ = self.mode_first(source)
        self.assertEqual(native, "alpha    beta")
        with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
            source_census(sources, source_target="all")
        source = "$(eval MAKECMDGOALS = other)\nifeq ($(MAKECMDGOALS),all)\nIGNORED = yes\nelse\n.POSIX:\nendif\n" + continued
        sources, native, _ = self.mode_first(source)
        self.assertEqual(native, "alpha beta")
        with self.assertRaisesRegex(MakeProbeError, "unproven.*POSIX"):
            source_census(sources, source_target="all")
        source = "DEST = MAKECMDGOALS\n$(DEST) = other\nifeq ($(MAKECMDGOALS),all)\nIGNORED = yes\nelse\n.POSIX:\nendif\n" + continued
        sources, native, _ = self.mode_first(source)
        self.assertEqual(native, "alpha beta")
        with self.assertRaisesRegex(MakeProbeError, "unproven.*POSIX"):
            source_census(sources, source_target="all")
        source = "undefine MAKECMDGOALS\nifeq ($(origin MAKECMDGOALS),undefined)\n.POSIX:\nendif\n" + continued
        sources, native, _ = self.mode_first(source)
        self.assertEqual(native, "alpha beta")
        with self.assertRaisesRegex(MakeProbeError, "unproven.*POSIX"):
            source_census(sources, source_target="all")
        source = "ifeq ($(filter %, $(MAKECMDGOALS)),all)\n.POSIX:\nendif\n" + continued
        sources, native, _ = self.mode_first(source)
        self.assertEqual(native, "alpha beta")
        with self.assertRaisesRegex(MakeProbeError, "unproven.*POSIX"):
            source_census(sources, source_target="all")
        mode = _MakeSourceMode(definitions={"MAKEOVERRIDES": "$(eval .POSIX:)"})
        mode.bind_invocation("all")
        self.assertIsNone(mode.posix)
        self.assertEqual(mode.control_values, {})
        self.assertEqual(mode.control_reads, set())

    def test_posix_mode_uses_proven_literal_conditional_branches(self):
        values = "FIRST = alpha  \\\n  \\\n beta\nSECOND = alpha  \\\n  \\\n beta\n"
        for prefix, expected in (
            ("ifeq (yes,no)\n.POSIX:\nendif\n", ["alpha beta", "alpha beta"]),
            ("ifeq (yes,yes)\n.POSIX:\nendif\n", ["alpha beta", "alpha    beta"]),
            ("ifeq (yes,no)\n.POSIX:\nelse\n.POSIX:\nendif\n", ["alpha beta", "alpha    beta"]),
            ("ifeq (yes,yes)\nIGNORED = no\nelse\n.POSIX:\nendif\n", ["alpha beta", "alpha beta"]),
            ("ifeq (yes,no)\nifeq (yes,yes)\n.POSIX:\nendif\nendif\n", ["alpha beta", "alpha beta"]),
            ("ifeq (yes,yes)\nifneq (yes,yes)\n.POSIX:\nelse ifeq (yes,yes)\n.POSIX:\nendif\nendif\n",
             ["alpha beta", "alpha    beta"]),
            ("ifeq 'a b' \"a b\"\n.POSIX:\nendif\n", ["alpha beta", "alpha    beta"]),
            ("ifeq (yes,no)\nifeq ($(shell touch marker),yes)\n.POSIX:\nendif\nendif\n",
             ["alpha beta", "alpha beta"]),
        ):
            with self.subTest(prefix=prefix):
                sources, native = self.mode_values(prefix + values, expected=expected)
                actual = source_census(sources)
                self.assertEqual([actual["definitions"][name][0] for name in ("FIRST", "SECOND")], native)
                self.assertFalse((self.root / "marker").exists())

    def test_posix_mode_carries_original_nested_include_and_eof_context(self):
        values = "FIRST = alpha  \\\n  \\\n beta\nSECOND = alpha  \\\n  \\\n beta\n"
        for source, includes, expected in (
            (".POSIX:\nACTIVATE = yes\ninclude values.mk\n", {"values.mk": values}, ["alpha    beta"] * 2),
            (".POSIX:\ninclude \\\n values.mk\n", {"values.mk": values}, ["alpha    beta"] * 2),
            ("include mode.mk\n" + values, {"mode.mk": ".POSIX:\n"}, ["alpha    beta"] * 2),
            (".POSIX:\nACTIVATE = yes\ninclude outer.mk\n",
             {"outer.mk": "include values.mk\n", "values.mk": values}, ["alpha    beta"] * 2),
            ("ifeq (yes,no)\n.POSIX:\nendif\ninclude values.mk\n",
             {"values.mk": values}, ["alpha beta"] * 2),
            (".POSIX:\ninclude outer.mk\n",
             {"outer.mk": "ifeq (yes,no)\ninclude ignored.mk\nendif\ninclude values.mk\n",
              "ignored.mk": "$(error unused include executed)\n", "values.mk": values}, ["alpha    beta"] * 2),
        ):
            with self.subTest(source=source):
                sources, native = self.mode_values(source, includes=includes, expected=expected)
                actual = source_census(sources)
                self.assertEqual([actual["definitions"][name][0] for name in ("FIRST", "SECOND")], native)

    def test_unproven_generated_conditional_and_include_modes_reject(self):
        values = "FIRST = alpha  \\\n  \\\n beta\nSECOND = alpha  \\\n  \\\n beta\n"
        for source, includes, expected in (
            ("SWITCH = yes\nifeq ($(SWITCH),yes)\n.POSIX:\nendif\n" + values, {}, ["alpha beta", "alpha    beta"]),
            ("$(eval .POSIX:)\n" + values, {}, ["alpha    beta"] * 2),
            ("MODE_PREFIX = .PO\nMODE_SUFFIX = SIX\n$(MODE_PREFIX)$(MODE_SUFFIX):\n" + values,
             {}, ["alpha beta", "alpha    beta"]),
            ("SELECTED = mode.mk\ninclude $(SELECTED)\n" + values,
             {"mode.mk": ".POSIX:\n"}, ["alpha    beta"] * 2),
        ):
            with self.subTest(source=source):
                sources, _ = self.mode_values(source, includes=includes, expected=expected)
                with self.assertRaisesRegex(MakeProbeError, "unproven.*(mode|POSIX)"):
                    source_census(sources)
        source = "MAYBE =\nunexport MAYBE\nRESULT := $(MAYBE)\n" + values
        sources, native = self.mode_values(source, expected=["alpha beta"] * 2)
        actual = source_census(sources)
        self.assertEqual([actual["definitions"][name][0] for name in ("FIRST", "SECOND")], native)
        assignments = (("command-line", "MAYBE", "$(eval .POSIX:)"),)
        sources, _ = self.mode_values(source, assignments=assignments, expected=["alpha    beta"] * 2)
        with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
            source_census(sources, source_assignments=assignments)

    def test_mode_assignment_origins_keep_gnu_file_and_default_precedence(self):
        continued = "FIRST = alpha  \\\n  \\\n beta\n"
        for origin, value, operator, prefix, expected, rejected in (
            ("environment", "literal", "=", "", "alpha    beta", True),
            ("command-line", "literal", "=", "", "alpha beta", False),
            ("environment", "", "=", "", "alpha    beta", True),
            ("environment", "literal", "?=", "", "alpha beta", False),
            ("environment", "", "?=", "", "alpha beta", False),
            ("command-line", "", "?=", "", "alpha beta", False),
            ("command-line", "literal", "=", "override ", "alpha    beta", True),
        ):
            with self.subTest(origin=origin, value=value, operator=operator, prefix=prefix):
                source = prefix + "MAYBE " + operator + " $(eval .POSIX:)\nRESULT := $(MAYBE)\n" + continued
                assignments = ((origin, "MAYBE", value),)
                sources, native, _ = self.mode_first(source, assignments=assignments)
                self.assertEqual(native, expected)
                if rejected:
                    with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
                        source_census(sources, source_assignments=assignments)
                else:
                    self.assertEqual(source_census(sources, source_assignments=assignments)["definitions"]["FIRST"], [native])
        source = "MAYBE = literal\nRESULT := $(MAYBE)\n" + continued
        assignments = (("environment", "MAYBE", "$(eval .POSIX:)"),)
        sources, native, _ = self.mode_first(source, assignments=assignments)
        self.assertEqual(native, "alpha beta")
        self.assertEqual(source_census(sources, source_assignments=assignments)["definitions"]["FIRST"], [native])

    def test_repeated_include_replays_changed_expression_inputs(self):
        continued = "FIRST = alpha  \\\n  \\\n beta\n"
        for nested in (False, True):
            for replacement, expected in (("literal", "alpha beta"), ("$(eval .POSIX:)", "alpha    beta")):
                with self.subTest(nested=nested, replacement=replacement):
                    name = "outer.mk" if nested else "mode.mk"
                    includes = {"mode.mk": "RESULT := $(SWITCH)\n"}
                    if nested:
                        includes["outer.mk"] = "include mode.mk\n"
                    source = "SWITCH =\ninclude " + name + "\nSWITCH = " + replacement + "\ninclude " + name + "\n"
                    sources, native, visits = self.mode_first(source + continued, includes=includes)
                    self.assertEqual(native, expected)
                    self.assertEqual(visits, ["Makefile", *([name, "mode.mk"] if nested else [name]) * 2])
                    if replacement == "literal":
                        self.assertEqual(source_census(sources)["definitions"]["FIRST"], [native])
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
                            source_census(sources)

    def test_completed_include_revisits_preserve_order_and_reject_changed_source_or_cycles(self):
        continued = "FIRST = alpha  \\\n  \\\n beta\n"
        source = "include repeated.mk\n.POSIX:\nACTIVATE = yes\ninclude repeated.mk\n"
        sources, native, visits = self.mode_first(source + continued, includes={"repeated.mk": "VALUE = literal\n"})
        self.assertEqual(native, "alpha    beta")
        self.assertEqual(visits, ["Makefile", "repeated.mk", "repeated.mk"])
        self.assertEqual(source_census(sources)["definitions"]["FIRST"], [native])
        sources, native, _ = self.mode_first(source, includes={"repeated.mk": continued})
        self.assertEqual(native, "alpha    beta")
        with self.assertRaisesRegex(MakeProbeError, "include source changed"):
            source_census(sources)
        budget = ProbeBudget()
        try:
            with self.assertRaisesRegex(MakeProbeError, "context is recursive"):
                source_census({"Makefile": b"include child.mk\n", "child.mk": b"include Makefile\n"}, budget=budget)
            self.assertEqual(budget.runs, 0)
            self.assertFalse(budget.children)
        finally:
            budget.close()

    def test_semantic_include_occurrences_preserve_later_secondary_expansion(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                name = "outer.mk" if nested else "rules.mk"
                source = "FLAGS ?= first\nOTHER = first\nTEXT := $$(OTHER)\nPAYLOAD = $(subst OTHER,FLAGS,$(TEXT))\n"
                source += "MODE = no\ninclude " + name + "\n.SECONDEXPANSION:\nMODE = yes\ninclude " + name + "\nfirst second: ;\n"
                self.add("Makefile", source)
                self.add("rules.mk", "ifeq ($(MODE),yes)\nall: $(PAYLOAD)\n\t@echo $(FLAGS)\nendif\n")
                if nested:
                    self.add("outer.mk", "include rules.mk\n")
                with self.session() as session:
                    observed = [
                        session.make("all", variables=("MAKEFILE_LIST",), assignments=(("command-line", "FLAGS", value),))
                        for value in ("first", "second")
                    ]
                self.assertEqual([item.semantics["files"][0]["prerequisites"][0]["name"] for item in observed],
                                 ["first", "second"])
                visits = ["Makefile", *(([name, "rules.mk"] if nested else [name]) * 2)]
                self.assertTrue(all(item.semantics["domains"]["MAKEFILE_LIST"]["value"].split() == visits for item in observed))
                self.assert_staged_rejection_before_late_observation()

    def test_repeated_semantic_visits_keep_supported_native_domains(self):
        for nested in (False, True):
            with self.subTest(nested=nested):
                name = "outer.mk" if nested else "rules.mk"
                self.add("Makefile", "FLAGS ?= first\nPAYLOAD := $$(FLAGS)\nMODE = no\ninclude " + name
                         + "\n.SECONDEXPANSION:\nMODE = yes\ninclude " + name + "\nfirst second: ;\n")
                self.add("rules.mk", "ifeq ($(MODE),yes)\nall: $(PAYLOAD)\n\t@echo $(FLAGS)\nendif\n")
                if nested:
                    self.add("outer.mk", "include rules.mk\n")
                self.assertEqual(self.ordinary("FLAGS=first"), b"first\n")
                self.assertEqual(self.ordinary("FLAGS=second"), b"second\n")
                with self.assertRaisesRegex(MakeProbeError, "symbolic inputs influence"):
                    self.observe(external={"FLAGS"}, symbolic_recipe_names={"FLAGS"})
                result = self.observe({"FLAGS": {"kind": "explicit", "values": ["first", "second"]}})["all"]
                self.assertEqual(result["prerequisite_domain_census"]["enumerated"], ["FLAGS"])
                self.assertEqual({
                    item["record"]["files"][0]["prerequisites"][0]["name"] for item in result["record"]["variants"]
                }, {"first", "second"})

    def test_template_inputs_share_the_same_repeated_visit_history(self):
        self.framework_templates()
        original = (self.root / "Makefile").read_text()
        self.add("shared.mk", "UNRELATED = literal\n")
        self.add("Makefile", original.replace("include generated_data.mk",
                 "include shared.mk\ninclude shared.mk\ninclude generated_data.mk"))
        result = self.observe_framework()
        files = {item["target"]: item for item in result["record"]["variants"][0]["record"]["files"]}
        self.assertEqual(files["build/modern/src/data_alpha.o"]["prerequisites"], [
            {"name": "build/generated/data/data_alpha.c", "order_only": False},
        ])
        self.add("shared.mk", "GENERATED_DATA_LINKED_TABLES := $(TABLES)\n")
        self.add("Makefile", original.replace("include generated_data.mk",
                 "TABLES = alpha beta\ninclude shared.mk\ninclude generated_data.mk")
                 + "TABLES = beta\ninclude shared.mk\n")
        with self.session() as session:
            native = session.make("all", variables=("MAKEFILE_LIST",))
        self.assertEqual(native.semantics["domains"]["MAKEFILE_LIST"]["value"].split().count("shared.mk"), 2)
        self.assertIn("build/generated/data/data_alpha.c", {item["target"] for item in native.semantics["files"]})
        with self.assertRaisesRegex(MakeProbeError, "original global assignment context"):
            self.observe_framework()

    def test_recipe_literal_metadata_keeps_unused_error_and_shell_bodies_raw(self):
        for body in ("$(error unused body expanded)", "$(shell touch marker)"):
            for operation, expected in (("origin", "file"), ("flavor", "recursive"), ("value", body)):
                with self.subTest(body=body, operation=operation):
                    self.add("Makefile", "RULE = " + body + "\nall:\n\t@printf '%s\\n' '$(" + operation + " RULE)'\n")
                    self.assertEqual(self.ordinary(), (expected + "\n").encode())
                    with self.session() as session:
                        native = session.make("all", definitions=("RULE",))
                    record, pages = self.recipe_pages()
                    self.assertEqual(record["definitions"]["global"]["RULE"],
                                     native.semantics["definitions"]["global"]["RULE"])
                    self.assertEqual(record["native_dispatches"], native.semantics["native_dispatches"])
                    self.assertTrue(any("RULE" in raw for _, raw in pages))
                    self.assertFalse(any("RULE" in expanded for expanded, _ in pages))
                    self.assertFalse((self.root / "marker").exists())

    def test_recipe_metadata_kinds_follow_aliases_and_preserve_mixed_execution(self):
        for operation, expected in (("origin", "file"), ("flavor", "recursive"),
                                    ("value", "$(error unused body expanded)")):
            with self.subTest(operation=operation):
                self.add("Makefile", "RULE = $(error unused body expanded)\nALIAS = $(" + operation
                         + " RULE)\nNEXT = $(ALIAS)\nNAME = NEXT\nall:\n\t@printf '%s\\n' '$($(NAME))'\n")
                self.assertEqual(self.ordinary(), (expected + "\n").encode())
                record, pages = self.recipe_pages()
                self.assertEqual(record["definitions"]["global"]["RULE"]["value"], "$(error unused body expanded)")
                self.assertTrue(any("NEXT" in expanded and "RULE" in raw for expanded, raw in pages))
                self.assertFalse(any("RULE" in expanded for expanded, _ in pages))
        self.add("Makefile", "RULE = literal\nALIAS = $(origin RULE)\nall:\n"
                 "\t@printf '%s|%s|%s|%s\\n' '$(RULE)' '$(ALIAS)' '$(flavor RULE)' '$(value RULE)'\n")
        self.assertEqual(self.ordinary(), b"literal|file|recursive|literal\n")
        record, pages = self.recipe_pages()
        self.assertEqual(record["domains"]["RULE"]["value"], "literal")
        self.assertEqual(record["definitions"]["global"]["RULE"]["value"], "literal")
        self.assertTrue(any("RULE" in expanded for expanded, _ in pages))
        self.assertTrue(any("RULE" in raw for _, raw in pages))
        self.assertTrue(all(not set(expanded) & set(raw) and len(expanded) + len(raw) <= 512 for expanded, raw in pages))
        self.add("Makefile", "RULE = $(error genuinely executed)\nall:\n\t@echo $(RULE) $(origin RULE)\n")
        with self.assertRaisesRegex(MakeProbeError, "genuinely executed"):
            self.observe()

    def test_recipe_metadata_retains_export_and_duplicate_file_contexts(self):
        self.add("Makefile", "RULE = $(error unused body expanded)\nexport VISIBLE = $(origin RULE)\n"
                 "all:\n\t@printf '%s\\n' \"$$VISIBLE\"\n")
        self.assertEqual(self.ordinary(), b"file\n")
        record, pages = self.recipe_pages()
        self.assertEqual(record["native_dispatches"][0]["environment"]["VISIBLE"], "file")
        self.assertIn("RULE", record["definitions"]["global"])
        self.assertFalse(any("RULE" in expanded for expanded, _ in pages))
        self.add("Makefile", "RULE = $(error unused body expanded)\n"
                 "all::\n\t@printf '%s\\n' '$(origin RULE)'\nall::\n\t@printf '%s\\n' '$(flavor RULE)'\n")
        self.assertEqual(self.ordinary(), b"file\nrecursive\n")
        record, _ = self.recipe_pages()
        self.assertEqual([item["target"] for item in record["definitions"]["files"]], ["all", "all"])
        self.assertTrue(all(item["variables"]["RULE"]["value"] == "$(error unused body expanded)"
                            for item in record["definitions"]["files"]))

    def test_recipe_metadata_pages_keep_combined_bounds_and_literal_selectors(self):
        names = ["VALUE_" + str(index) for index in range(513)]
        self.add("Makefile", "".join(name + " = $(error unused body expanded)\n" for name in names)
                 + "all:\n\t@printf '%s\\n' '" + " ".join("$(origin " + name + ")" for name in names) + "'\n")
        self.assertEqual(self.ordinary(), (" ".join(["file"] * 513) + "\n").encode())
        record, pages = self.recipe_pages()
        self.assertEqual(set(record["definitions"]["global"]), set(names))
        self.assertEqual([len(raw) for _, raw in pages if raw], [512, 1])
        self.assertTrue(all(len(expanded) + len(raw) <= 512 and not set(expanded) & set(raw) for expanded, raw in pages))
        self.add("Makefile", "RULE = $(error unused body expanded)\nNAME = RULE\nall:\n\t@echo $(origin $(NAME))\n")
        with self.assertRaisesRegex(MakeProbeError, "computed Make introspection"):
            self.observe()
        self.add("Makefile", "all:\n\t@printf '%s\\n' '$$(origin UNREAD)'\n")
        self.assertEqual(self.ordinary(), b"$(origin UNREAD)\n")
        record, pages = self.recipe_pages()
        self.assertFalse(any("UNREAD" in (*expanded, *raw) for expanded, raw in pages))
        self.add("Makefile", "all:\n\t@printf '%s\\n' '$(origin UNDEFINED)'\n")
        record, _ = self.recipe_pages(ambient_undefined_names={"UNDEFINED"})
        self.assertEqual(record["definitions"]["global"]["UNDEFINED"]["origin"], "undefined")

    def test_unused_and_late_mode_bodies_do_not_rewrite_original_source(self):
        values = "FIRST = alpha  \\\n  \\\n beta\nSECOND = alpha  \\\n  \\\n beta\n"
        for prefix, suffix in (
            ("define UNUSED\n.POSIX:\nendef\n", ""),
            ("UNUSED = $(eval .POSIX:)$(shell touch marker)\n", ""),
            ("", ".POSIX:\n"),
        ):
            with self.subTest(prefix=prefix, suffix=suffix):
                sources, native = self.mode_values(
                    prefix + values + suffix, expected=["alpha beta"] * 2,
                )
                actual = source_census(sources)
                self.assertEqual([actual["definitions"][name][0] for name in ("FIRST", "SECOND")], native)
                if suffix:
                    self.assertEqual(self.last_mode_dispatches[0]["arguments"][1], "-ec")
                self.assertFalse((self.root / "marker").exists())
        self.add("Makefile", values + "all:\n\t$(eval .POSIX:)\n")
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "prerequisites cannot be defined in recipes"):
                session.make("all")

    def test_mode_analysis_keeps_metadata_lazy_and_the_existing_deadline(self):
        source = "UNUSED = $(shell touch marker)\nMETA := $(origin UNUSED)\n"
        source += "V0 = literal\nV1 = literal\n"
        source += "".join("V" + str(index) + " = $(V" + str(index - 1) + ")$(V" + str(index - 2) + ")\n"
                          for index in range(2, 1100))
        source += "RESULT := $(if yes,ok,$(V1099))\n"
        source += "FIRST = alpha  \\\n  \\\n beta\nSECOND = alpha  \\\n  \\\n beta\n"
        sources, native = self.mode_values(source, expected=["alpha beta"] * 2)
        budget = ProbeBudget()
        try:
            actual = source_census(sources, budget=budget)
            self.assertEqual([actual["definitions"][name][0] for name in ("FIRST", "SECOND")], native)
            self.assertFalse((self.root / "marker").exists())
            self.assertEqual(budget.runs, 0)
        finally:
            budget.close()
        with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline"):
            source_census(sources, budget=budget)

    def test_source_faithful_framework_templates_keep_native_graph_and_real_outputs(self):
        self.framework_templates()
        actual = self.observe_framework()
        record = actual["record"]["variants"][0]["record"]
        files = {item["target"]: item for item in record["files"]}
        generated = "build/generated/data/data_alpha.c"
        object_file = "build/modern/src/data_alpha.o"
        self.assertEqual(files[object_file]["prerequisites"], [{"name": generated, "order_only": False}])
        self.assertEqual({item["name"] for item in files[generated]["prerequisites"]}, {
            "src/data/alpha.json", "scripts/generated_data/__init__.py", "scripts/generated_data/__main__.py",
            "scripts/generated_data/alpha/schema.py", "include/alpha.h",
        })
        self.assertIn("MODERN_CFLAGS", actual["record"]["symbolic_recipe_names"])
        self.assertEqual(self.ordinary().count(b"error:"), 0)
        self.assertEqual((self.root / generated).read_text(), "const int data_alpha=1;\n")
        self.assertEqual((self.root / object_file).read_bytes()[:4], b"\x7fELF")

    def test_make_bom_crlf_backslashes_and_nested_defines_keep_native_values(self):
        for separator, count in (("\n", 1), ("\r\n", 2), ("\r\n", 3)):
            with self.subTest(separator=separator, backslashes=count):
                source = "\ufeff" + (
                    "VALUE = before" + "\\" * count + "\n  AFTER = tail\n"
                    "define BLOCK\ndefine INNER\nliteral\nendef\nendef\nall: ;\n"
                ).replace("\n", separator)
                self.add("Makefile", source)
                census = source_census({"Makefile": source.encode()})
                with self.session() as session:
                    native = session.make("all", definitions=("VALUE", "BLOCK", "AFTER"))
                for name in ("VALUE", "BLOCK"):
                    self.assertEqual(census["definitions"][name], [native.semantics["definitions"]["global"][name]["value"]])
                self.assertEqual("AFTER" in census["defined"], count == 2)
        for source in (
            "PART = LOAD\ndefine PAY$(PART)\nfirst\nendef\nall: ;\n",
            ".RECIPEPREFIX := >\nall:\n>@echo real\n",
        ):
            self.add("Makefile", source)
            with self.session() as session:
                session.make("all")
            with self.assertRaises(MakeProbeError):
                source_census({"Makefile": source.encode()})

    def test_secondary_stages_follow_original_rule_and_include_order(self):
        self.add("rules.mk", "all: $(PAYLOAD)\n\t@echo $(FLAGS)\nfirst second: ;\n")
        declarations = "FLAGS ?= first\nPAYLOAD = $(subst OTHER,$(FLAGS),OTHER)\n"
        self.add("Makefile", declarations + "include rules.mk\n.SECONDEXPANSION:\n")
        domain = {"FLAGS": {"kind": "explicit", "values": ["first", "second"]}}
        before = self.observe(domain)["all"]
        self.assertEqual(before["prerequisite_domain_census"]["enumerated"], ["FLAGS"])
        self.assertEqual({
            variant["record"]["files"][0]["prerequisites"][0]["name"] for variant in before["record"]["variants"]
        }, {"first", "second"})
        self.add("Makefile", declarations + ".SECONDEXPANSION:\ninclude rules.mk\n")
        with self.assertRaisesRegex(MakeProbeError, "unproven emitted-reference"):
            self.observe(domain)

    def test_framework_template_selectors_keep_complete_native_dependencies(self):
        cases = (
            ("GENERATED_DATA_OUT_DIR", ["build/generated/data", "build/alternate/data"]),
            ("MODERN_OUTPUT_DIR", ["build/modern", "build/alternate/modern"]),
            ("GENERATED_DATA_CONFIG_INPUTS_alpha", ["include/alpha.h", "include/alternate.h"]),
            ("GENERATED_DATA_SHARED_PY_SOURCES", [
                "scripts/generated_data/__init__.py scripts/generated_data/__main__.py",
                "scripts/generated_data/__init__.py scripts/generated_data/__main__.py scripts/shared.py",
            ]),
            ("GENERATED_DATA_LINKED_HAND_SOURCES", [
                "src/data_alpha.c", "src/data_alpha.c src/data_beta.c",
            ]),
            ("SELECTED_TABLE", ["alpha", "beta"]),
        )
        for name, values in cases:
            with self.subTest(selector=name):
                self.framework_templates()
                self.add("include/alternate.h", "/* alternate config */\n")
                self.add("scripts/shared.py", "# additional shared dependency\n")
                observed = self.observe_framework({name: {"kind": "explicit", "values": values}})
                self.assertEqual(observed["prerequisite_domain_census"]["enumerated"], [name])
                variants = observed["record"]["variants"]
                for value in values:
                    state = [("command-line", name, value)]
                    record = next(variant["record"] for variant in variants if variant["state"] == state)
                    with self.session() as session:
                        native = session.make("all", assignments=(("command-line", name, value),))
                    projection = lambda files: [
                        {key: item[key] for key in ("target", "recipe", "prerequisites")} for item in files
                    ]
                    self.assertEqual(projection(record["files"]), projection(native.semantics["files"]))
                    files = {item["target"]: item for item in record["files"]}
                    selected = value if name == "SELECTED_TABLE" else "alpha"
                    generated_dir = value if name == "GENERATED_DATA_OUT_DIR" else "build/generated/data"
                    modern_dir = value if name == "MODERN_OUTPUT_DIR" else "build/modern"
                    generated = generated_dir + "/data_" + selected + ".c"
                    self.assertEqual(files[modern_dir + "/src/data_" + selected + ".o"]["prerequisites"], [
                        {"name": generated, "order_only": False},
                    ])
                    prerequisites = {item["name"] for item in files[generated]["prerequisites"]}
                    self.assertIn("src/data/" + selected + ".json", prerequisites)
                    self.assertIn("scripts/generated_data/" + selected + "/schema.py", prerequisites)
                    if name == "GENERATED_DATA_CONFIG_INPUTS_alpha":
                        self.assertIn(value, prerequisites)
                    if name == "GENERATED_DATA_SHARED_PY_SOURCES":
                        self.assertTrue(set(value.split()) <= prerequisites)

    def test_template_macro_names_and_real_wildcard_inputs_are_not_special_cased(self):
        self.framework_templates()
        before = self.observe_framework()["record"]["variants"][0]["record"]["files"]
        self.framework_templates(renamed=True)
        renamed = self.observe_framework()["record"]["variants"][0]["record"]["files"]
        self.assertEqual(before, renamed)
        self.add("scripts/generated_data/alpha/extra.py", "# new table-specific source\n")
        after = self.observe_framework()["record"]["variants"][0]["record"]["files"]
        generated = next(item for item in after if item["target"] == "build/generated/data/data_alpha.c")
        self.assertIn({"name": "scripts/generated_data/alpha/extra.py", "order_only": False}, generated["prerequisites"])
        self.assertNotEqual(before, after)

    def test_rule_template_opaque_parameter_and_context_variants_reject(self):
        cases = (
            ("header-function", "generated_data.mk", "$(wildcard scripts/generated_data/$(1)/*.py)",
             "$(subst Q,Q,$(wildcard scripts/generated_data/$(1)/*.py))"),
            ("immediate-effect", "generated_data.mk", "\t$(GENERATED_DATA_PY)",
             "\t$(eval SEEN = one)$(GENERATED_DATA_PY)"),
            ("deferred-effect", "generated_data.mk", "\t$(GENERATED_DATA_PY)",
             "\t$$(eval SEEN = one)$(GENERATED_DATA_PY)"),
            ("extra-dollar-stage", "generated_data.mk", "$$(@D)", "$$$(@D)"),
            ("extra-parameter", "generated_data.mk",
             "$(call GENERATED_DATA_LINK_TABLE_RULES,$(t))", "$(call GENERATED_DATA_LINK_TABLE_RULES,$(t),ignored)"),
            ("parameter-data", "Makefile", "src/data_beta.c", "src/data_bad.name.c"),
            ("initializer-effect", "Makefile", "GENERATED_DATA_OUT_DIR := build/generated/data",
             "GENERATED_DATA_OUT_DIR := $(eval SEEN = one)build/generated/data"),
            ("initializer-unproven", "Makefile", "GENERATED_DATA_OUT_DIR := build/generated/data",
             "GENERATED_DATA_OUT_DIR := $(UNKNOWN)build/generated/data"),
            ("late-output", "Makefile", ".SECONDEXPANSION:\n",
             ".SECONDEXPANSION:\nGENERATED_DATA_OUT_DIR := build/later\n"),
            ("late-macro", "Makefile", ".SECONDEXPANSION:\n",
             ".SECONDEXPANSION:\nGENERATED_DATA_LINK_TABLE_RULES = ignored\n"),
            ("late-undefine", "Makefile", ".SECONDEXPANSION:\n",
             ".SECONDEXPANSION:\nundefine GENERATED_DATA_LINKED_TABLES\n"),
            ("dynamic-write", "Makefile", ".SECONDEXPANSION:\n",
             ".SECONDEXPANSION:\nNAME = GENERATED_DATA_OUT_DIR\n$(NAME) := build/later\n"),
            ("dynamic-undefine", "Makefile", ".SECONDEXPANSION:\n",
             ".SECONDEXPANSION:\nNAME = GENERATED_DATA_LINKED_TABLES\nundefine $(NAME)\n"),
            ("wildcard-patterns", "generated_data.mk", "$(wildcard scripts/generated_data/$(1)/*.py)",
             "$(wildcard scripts/generated_data/$(1)/*.py scripts/shared/*.py)"),
            ("uncertain-include", "Makefile", "include generated_data.mk",
             "ifeq (yes,yes)\ninclude generated_data.mk\nendif"),
        )
        for case, path, old, new in cases:
            with self.subTest(case=case):
                self.framework_templates()
                self.add(path, (self.root / path).read_text().replace(old, new))
                with self.assertRaises(MakeProbeError):
                    self.observe_framework()
        self.framework_templates()
        with self.assertRaisesRegex(MakeProbeError, "macro identity"):
            self.observe_framework({"GENERATED_DATA_LINK_TABLE_RULES": {"kind": "tracked-fallback"}})
        with self.assertRaisesRegex(MakeProbeError, "macro identity"):
            self.observe_framework(symbolic={"GENERATED_DATA_LINK_TABLE_RULES"})
        self.add("Makefile", "define wildcard\nall: input\nendef\n"
                 "$(foreach t,absent,$(eval $(call wildcard,$(t))))\nall: ;\n")
        self.ordinary()
        with self.assertRaisesRegex(MakeProbeError, "parameterized rule-template invocation"):
            self.observe()

    def test_rule_templates_reject_late_context_and_reference_mismatch(self):
        self.framework_templates()
        source = (self.root / "Makefile").read_text()
        source = "FLAGS ?= include/first.h\n" + source.replace(
            "GENERATED_DATA_CONFIG_INPUTS_alpha := include/alpha.h",
            "GENERATED_DATA_CONFIG_INPUTS_alpha := $(FLAGS)",
        )
        self.add("Makefile", source + "GENERATED_DATA_LINKED_TABLES := beta\nall:\n\t@echo $(FLAGS)\n")
        for name in ("first", "second"):
            self.add("include/" + name + ".h", "/* selected original input */\n")
        with self.session() as session:
            for value in ("include/first.h", "include/second.h"):
                actual = session.make(
                    "all", assignments=(("command-line", "FLAGS", value),), definitions=("GENERATED_DATA_LINKED_TABLES",),
                )
                generated = next(item for item in actual.semantics["files"] if item["target"] == "build/generated/data/data_alpha.c")
                self.assertIn({"name": value, "order_only": False}, generated["prerequisites"])
                self.assertEqual(actual.semantics["definitions"]["global"]["GENERATED_DATA_LINKED_TABLES"]["value"], "beta")
        with self.assertRaisesRegex(MakeProbeError, "original global assignment context"):
            self.observe_framework(symbolic={"FLAGS"})
        with self.assertRaisesRegex(MakeProbeError, "original global assignment context"):
            self.observe_framework({"FLAGS": {"kind": "explicit", "values": ["include/first.h", "include/second.h"]}})

    def test_template_wildcard_namespace_cannot_emit_make_syntax(self):
        self.framework_templates()
        self.add("scripts/generated_data/alpha/unrelated;rule.txt", "# unproven wildcard namespace\n")
        with self.assertRaisesRegex(MakeProbeError, "reference-preserving namespace"):
            self.observe_framework()

    def test_transparent_staged_forwarding_and_unused_transformations_remain_supported(self):
        self.add("Makefile", "FLAGS ?= first\nTEXT := $$(FLAGS)\nCOPY = $(TEXT)\n"
                 "PAYLOAD = $(call COPY)\nUNUSED = $(subst X,Y,$(shell touch marker))\n"
                 ".SECONDEXPANSION:\nall: $(PAYLOAD)\n\t@echo $(FLAGS)\nfirst second: ;\n")
        self.assertEqual(self.ordinary("FLAGS=first"), b"first\n")
        self.assertEqual(self.ordinary("FLAGS=second"), b"second\n")
        result = self.observe({"FLAGS": {"kind": "explicit", "values": ["first", "second"]}})["all"]
        self.assertEqual(result["prerequisite_domain_census"]["enumerated"], ["FLAGS"])
        self.assertEqual({
            item["record"]["files"][0]["prerequisites"][0]["name"] for item in result["record"]["variants"]
        }, {"first", "second"})
        self.assertFalse((self.root / "marker").exists())
        self.add("Makefile", "FLAGS ?= first\nINPUT = OTHER\nPAYLOAD = $(subst OTHER,$(FLAGS),$(INPUT))\n"
                 "all: $(PAYLOAD)\nfirst second: ;\n")
        result = self.observe({"FLAGS": {"kind": "explicit", "values": ["first", "second"]}})["all"]
        self.assertEqual(result["prerequisite_domain_census"]["enumerated"], ["FLAGS"])
        self.assertEqual({
            item["record"]["files"][0]["prerequisites"][0]["name"] for item in result["record"]["variants"]
        }, {"first", "second"})

    def test_combined_raw_and_expanded_names_keep_exact_512_admission(self):
        self.add("Makefile", "".join("VALUE_" + str(index) + " := " + str(index) + "\n" for index in range(513))
                 + "all: ;\n")
        variables = tuple("VALUE_" + str(index) for index in range(256))
        definitions = tuple("VALUE_" + str(index) for index in range(256, 512))
        with self.session() as session:
            actual = session.make("all", variables=variables, definitions=definitions)
            self.assertEqual(set(actual.semantics["domains"]), set(variables))
            self.assertEqual(set(actual.semantics["definitions"]["global"]), set(definitions))
            for index in range(256, 512):
                self.assertEqual(actual.semantics["definitions"]["global"]["VALUE_" + str(index)]["value"], str(index))
            before = session.budget.runs, session.budget.states
            with self.assertRaises(MakeProbeError):
                session.make("all", variables=variables, definitions=(*definitions, "VALUE_512"))
            self.assertEqual((session.budget.runs, session.budget.states), before)
        self.assertIsNone(session.base)
        self.assertFalse(session.budget.children)

    def test_computed_include_and_unresolved_name_contracts_are_native(self):
        self.add("first.mk", "SELECTED = first\n")
        self.add("second.mk", "SELECTED = second\n")
        self.add("Makefile", (
            "FLAGS ?= first\nNAME = FLAGS\ninclude $($(NAME)).mk\n"
            "all: $(SELECTED)\n\t@echo $(FLAGS)\nfirst second: ;\n"
        ))
        self.assertEqual(self.ordinary("FLAGS=first"), b"first\n")
        self.assertEqual(self.ordinary("FLAGS=second"), b"second\n")
        with self.assertRaises(MakeProbeError):
            self.observe(external={"FLAGS"}, symbolic_recipe_names={"FLAGS"})
        records = self.observe({"FLAGS": {"kind": "explicit", "values": ["first", "second"]}})["all"]
        self.assertEqual(records["record"]["includes"], ["Makefile", "first.mk", "second.mk"])
        self.add("Makefile", (
            "FLAGS ?= first\nNAME = $(subst X,FLAGS,X)\n"
            "all: $($(NAME))\nfirst second: ;\n"
        ))
        self.assertEqual(self.ordinary("FLAGS=second"), b"make: Nothing to be done for 'all'.\n")
        with self.assertRaisesRegex(MakeProbeError, "computed selector"):
            self.observe({"FLAGS": {"kind": "explicit", "values": ["first", "second"]}})
        observed = self.observe({
            "FLAGS": {"kind": "explicit", "values": ["first", "second"]},
            "NAME": {"kind": "tracked-fallback"},
        })["all"]
        self.assertEqual(observed["prerequisite_domain_census"]["enumerated"], ["FLAGS", "NAME"])

    def test_computed_conditional_names_cannot_be_symbolic_recipe_inputs(self):
        self.add("Makefile", (
            "FLAGS ?=\nNAME = FLAGS\nifdef $(NAME)\nSELECTED = second\nelse\nSELECTED = first\nendif\n"
            "all: $(SELECTED)\n\t@printf '%s\\n' '$(SELECTED)'\nfirst second: ;\n"
        ))
        self.assertEqual(self.ordinary("FLAGS="), b"first\n")
        self.assertEqual(self.ordinary("FLAGS=yes"), b"second\n")
        with self.assertRaises(MakeProbeError):
            self.observe(external={"FLAGS"}, symbolic_recipe_names={"FLAGS"})
        result = self.observe({"FLAGS": {"kind": "explicit", "values": ["", "yes"]}})["all"]
        self.assertEqual({
            item["record"]["files"][0]["prerequisites"][0]["name"] for item in result["record"]["variants"]
        }, {"first", "second"})

    def test_computed_selector_values_include_actual_target_local_context(self):
        self.add("Makefile", (
            "FLAGS ?= first\nNAME ?= OTHER\nOTHER = first\n.SECONDEXPANSION:\n"
            "all: NAME = FLAGS\nall: $$($$(NAME))\n\t@echo $(FLAGS)\nfirst second: ;\n"
        ))
        with self.session() as session:
            actual = session.make("all", variables=("NAME",))
            self.assertEqual(actual.semantics["domains"]["NAME"]["value"], "OTHER")
            self.assertEqual(actual.semantics["files"][0]["variables"]["NAME"]["value"], "FLAGS")
        self.assertEqual(self.ordinary("FLAGS=first"), b"first\n")
        self.assertEqual(self.ordinary("FLAGS=second"), b"second\n")
        domain = {"NAME": {"kind": "tracked-fallback"}}
        with self.assertRaises(MakeProbeError):
            self.observe(domain, external={"FLAGS"}, symbolic_recipe_names={"FLAGS"})
        result = self.observe({
            **domain, "FLAGS": {"kind": "explicit", "values": ["first", "second"]},
        })["all"]
        self.assertEqual(result["prerequisite_domain_census"]["enumerated"], ["FLAGS", "NAME"])
        self.assertEqual({
            item["record"]["files"][0]["prerequisites"][0]["name"] for item in result["record"]["variants"]
        }, {"first", "second"})
        self.add("Makefile", "NAME = @\nall:\n\t@printf '%s\\n' '$($(NAME))'\n")
        self.assertEqual(self.ordinary(), b"all\n")
        record = self.observe(scoped_variable_names={"@"})["all"]
        self.assertEqual(record["variable_census"]["scoped_variables"], ["@"])
        self.assertEqual(record["record"]["variants"][0]["record"]["native_dispatches"][0]["arguments"][-1], "all")
        with self.assertRaises(MakeProbeError):
            self.observe()

    def test_modern_size_recipe_default_uses_sealed_contract(self):
        name = "MODERN_SIZE"
        usage = source_census({"modern.mk": (ROOT / "modern.mk").read_bytes()})
        self.assertIn(name, usage["defaults"])
        self.assertIn(name, usage["recipe_only"])
        contract = json.loads((ROOT / ".github/validation-ownership-make-dynamics.json").read_text())
        self.assertEqual(contract["seal"], reporter._sha256(
            reporter.MAKE_DYNAMIC_SEAL_DOMAIN, reporter.canonical_make_dynamic_payload(contract),
        ))
        options = {
            "external": contract["ambient_inputs"]["allowed_names"],
            "symbolic_recipe_names": contract["prerequisite_domains"]["symbolic_recipe_names"],
        }
        records = []
        for value in ("arm-none-eabi-size", "/selected/bin/arm-none-eabi-size"):
            self.add("Makefile", f"{name} ?= {value}\nall:\n\t@echo $({name})\n")
            record = self.observe(**options)["all"]["record"]
            self.assertEqual(record["symbolic_recipe_names"], [name])
            self.assertEqual(record["variants"][0]["record"]["domains"][name]["value"], value)
            records.append(record)
        self.assertNotEqual(records[0], records[1])
        with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
            self.observe(**{**options, "external": set(options["external"]) - {name}})
        for source, error in (
            (f"{name} ?= selected\nall: $({name})\nselected: ;\n", "symbolic inputs influence"),
            ("MODERN_SIZE_UNSEALED ?= x\nall: ;\n", "unsealed external defaults"),
        ):
            with self.subTest(source=source):
                self.add("Makefile", source)
                with self.assertRaisesRegex(MakeProbeError, error):
                    self.observe(**options)

    def test_branch_loaded_domains_reach_a_bounded_fixed_point(self):
        self.add("Makefile", "MODE ?= one\nifeq ($(MODE),two)\ninclude branch.mk\nendif\nall: ;\n")
        self.add("branch.mk", "BRANCH ?= selected\nall: $(BRANCH)\nselected: ;\n")
        result = self.observe({
            "MODE": {"kind": "explicit", "values": ["one", "two"]},
            "BRANCH": {"kind": "tracked-fallback"},
        })
        self.assertEqual(result["all"]["prerequisite_domain_census"]["used"], ["BRANCH", "MODE"])
        self.assertTrue(any(
            any(item[1] == "BRANCH" for item in variant["state"])
            for variant in result["all"]["record"]["variants"]
        ))

    def test_unloaded_source_does_not_backfill_census(self):
        self.add("Makefile", "all: ;\n")
        self.add("unused.mk", "UNUSED ?= x\nall: $(UNUSED)\n")
        result = self.observe({"UNUSED": {"kind": "tracked-fallback"}})
        self.assertEqual(result["all"]["prerequisite_domain_census"]["used"], [])

    def test_recipe_only_finite_names_are_not_enumerated(self):
        self.add("Makefile", "VALUE ?= x\nall:\n\t@echo $(VALUE)\n")
        result = self.observe({"VALUE": {"kind": "tracked-fallback"}})
        self.assertEqual(result["all"]["prerequisite_domain_census"]["used"], ["VALUE"])
        self.assertEqual(result["all"]["prerequisite_domain_census"]["enumerated"], [])

    def test_unconsumed_symbolic_variable_is_not_eagerly_expanded_by_observer(self):
        self.add("Makefile", "UNUSED ?= $(shell touch marker)\nall: ;\n")
        result = self.observe(external={"UNUSED"}, symbolic_recipe_names={"UNUSED"})["all"]
        self.assertEqual(result["record"]["symbolic_recipe_names"], [])
        self.assertFalse((self.root / "marker").exists())

    def test_closed_census_tracks_nested_definitions_and_second_expansion(self):
        census = source_census({"Makefile": (
            b"MODE ?= x\nALIAS = $(MODE)\n"
            b"define RULE\nall: $$(ALIAS)\nendef\n$(eval $(RULE))\n"
        )})
        self.assertIn("MODE", census["graph"])
        self.assertIn("ALIAS", census["graph"])

    def test_computed_introspection_cannot_hide_a_real_prerequisite_domain(self):
        self.add("Makefile", (
            "NAME ?= CHOICE\nCHOICE ?= one\n"
            "all: $(value $(NAME))\none two: ;\n"
        ))
        with self.session() as session:
            actual = {
                session.make(
                    "all", assignments=(("command-line", "CHOICE", value),),
                ).semantics["files"][0]["prerequisites"][0]["name"]
                for value in ("one", "two")
            }
            self.assertEqual(actual, {"one", "two"})
        with self.assertRaisesRegex(MakeProbeError, "computed Make introspection"):
            self.observe({
                "NAME": {"kind": "tracked-fallback"},
                "CHOICE": {"kind": "explicit", "values": ["one", "two"]},
            })

    def test_quoted_recipe_hash_preserves_its_real_variable_census(self):
        self.add("Makefile", "GUARD ?= FIRST\nall:\n\t@printf '#define $(GUARD) 1\\n'\n")
        first = self.observe(external={"GUARD"}, symbolic_recipe_names={"GUARD"})["all"]["record"]
        self.assertEqual(first["symbolic_recipe_names"], ["GUARD"])
        self.add("Makefile", "GUARD ?= SECOND\nall:\n\t@printf '#define $(GUARD) 1\\n'\n")
        second = self.observe(external={"GUARD"}, symbolic_recipe_names={"GUARD"})["all"]["record"]
        self.assertNotEqual(first, second)

    def test_actual_export_membership_values_and_target_context_are_authority(self):
        for declaration in ("export OPTION = {value}", "all: export OPTION = {value}"):
            records = []
            for value in ("first", "second"):
                self.add("Makefile", declaration.format(value=value) + "\nall:\n\t@printf '%s\\n' \"$$OPTION\"\n")
                self.assertEqual(self.ordinary(), (value + "\n").encode())
                with patch.dict(os.environ, {"ISSUE180_HOST_SECRET": "not-an-input"}):
                    record = self.observe()["all"]["record"]
                contexts = record["variants"][0]["record"]["native_dispatches"]
                self.assertEqual(len(contexts), 1)
                self.assertEqual(contexts[0]["environment"]["OPTION"], value)
                self.assertNotIn("ISSUE180_HOST_SECRET", contexts[0]["environment"])
                self.assertNotIn("LD_PRELOAD", contexts[0]["environment"])
                self.assertFalse(any(name.startswith("VO_") for name in contexts[0]["environment"]))
                records.append(record)
            self.assertNotEqual(*records)
        for assignment in ("", "all: OPTION = local\n"):
            self.add("Makefile", "export OPTION = first\nunexport OPTION\n" + assignment
                     + "all:\n\t@printf '%s\\n' \"$$OPTION\"\n")
            self.assertEqual(self.ordinary(), b"\n")
            context = self.observe()["all"]["record"]["variants"][0]["record"]["native_dispatches"][0]
            self.assertNotIn("OPTION", context["environment"])
        self.add("Makefile", "export OPTION = first\nall: unexport OPTION = local\nall:\n\t@true\n")
        with self.assertRaises(MakeProbeError):
            self.observe()

    def test_target_exports_keep_actual_scheduled_order_and_equivalent_declarations(self):
        records = []
        for swapped in (False, True):
            first, second = ("second", "first") if swapped else ("first", "second")
            source = (
                "all: one two\none: export OPTION = " + first + "\ntwo: export OPTION = " + second
                + "\none two:\n\t@printf '%s\\n' \"$$OPTION\"\n"
            )
            self.add("Makefile", source)
            self.assertEqual(self.ordinary(), (first + "\n" + second + "\n").encode())
            record = self.observe()["all"]["record"]
            contexts = record["variants"][0]["record"]["native_dispatches"]
            self.assertEqual([item["sequence"] for item in contexts], [1, 2])
            self.assertEqual([item["environment"]["OPTION"] for item in contexts], [first, second])
            records.append(record)
        self.assertNotEqual(*records)
        self.add("Makefile", (
            "two: export OPTION = first\none: export OPTION = second\nall: one two\n"
            "one two:\n\t@printf '%s\\n' \"$$OPTION\"\n"
        ))
        self.assertEqual(self.observe()["all"]["record"], records[-1])

    def test_export_observation_is_lazy_and_keeps_native_frame_admission(self):
        self.add("Makefile", "UNUSED = $(shell touch marker)\nall:\n\t@true\n")
        self.observe()
        self.assertFalse((self.root / "marker").exists())
        self.add("Makefile", "export OPTION = " + "x" * 4096 + "\nall:\n\t@true\n")
        with self.assertRaisesRegex(MakeProbeError, "pathname exceeds bound"):
            self.observe()
        self.assertFalse((self.root / "marker").exists())

    def test_export_contexts_spend_actual_native_and_retained_accounting(self):
        self.add("Makefile", "export OPTION = first\nall:\n\t@printf '%s\\n' \"$$OPTION\"\n")
        with self.session() as session:
            original = session._sandbox_run
            captured = []

            def observe(*arguments, **keywords):
                actual = original(*arguments, **keywords)
                captured.append(actual[1])
                return actual

            before = session.observations_used, session.budget.bytes.get("control", 0)
            with patch.object(session, "_sandbox_run", observe):
                result = session.make("all")
            native, = captured
            frames = [value for value in native["accessed"] if value.startswith("make-dispatch:")]
            self.assertEqual(len(frames), 1)
            dispatch = json.loads(frames[0].removeprefix("make-dispatch:"))
            dispatch["ignore_errors"] = dispatch.pop("global_ignore_errors")
            self.assertEqual(dispatch, result.semantics["native_dispatches"][0])
            self.assertEqual(session.observations_used - before[0], native["observations"])
            self.assertGreaterEqual(native["observation_bytes"], len(frames[0].encode()) + 128)
            self.assertGreaterEqual(
                session.budget.bytes["control"] - before[1],
                native["observation_bytes"] + len(encoded(result.semantics)),
            )
            self.last_export_evidence = {
                "runs": session.budget.runs, "states": session.budget.states,
                "observations": native["observations"], "observation_bytes": native["observation_bytes"],
                "control_delta": session.budget.bytes["control"] - before[1],
                "native_dispatch_frame_bytes": len(frames[0].encode()),
                "semantic_bytes": len(encoded(result.semantics)),
            }
        self.assertIsNone(session.base)
        self.assertFalse(session.budget.children)
        self.assertFalse(session.budget.producer_waiters)

    def test_native_ignore_error_policy_matches_actual_gnu_jobs(self):
        scenarios = (
            ("none", "", "all:\n\t@exit 7\n", 2, [False]),
            ("unrelated", ".IGNORE: unrelated-target\n", "all:\n\t@exit 7\n", 2, [False]),
            ("selected", ".IGNORE: all\n", "all:\n\t@exit 7\n", 0, [True]),
            ("global", ".IGNORE:\n", "all:\n\t@exit 7\n", 0, [True]),
            ("flag", "MAKEFLAGS += -i\n", "all:\n\t@exit 7\n", 0, [True]),
            ("local", "", "all:\n\t-@exit 7\n", 0, [True]),
            ("expanded-local", "PREFIX = -\n", "all:\n\t@$(PREFIX)exit 7\n", 0, [True]),
            ("mixed-targets", ".IGNORE: one\n", "all: one two\none two:\n\t@exit 7\n", 2, [True, False]),
            ("mixed-commands", "", "all:\n\t-@exit 7\n\t@exit 7\n", 2, [True, False]),
        )
        self.last_ignore_evidence = []
        for name, prelude, body, code, flags in scenarios:
            with self.subTest(scenario=name):
                self.add("Makefile", ".PHONY: all one two unrelated-target\n" + prelude + body)
                budget = ProbeBudget()
                try:
                    actual = budget.run(
                        ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root, env=ENVIRONMENT,
                    )
                    self.assertEqual(actual.returncode, code, actual.stderr)
                    if name == "mixed-targets":
                        for target, expected in (("one", 0), ("two", 2)):
                            reference = budget.run(
                                ["/usr/bin/make", "-f", "Makefile", target], cwd=self.root, env=ENVIRONMENT,
                            )
                            self.assertEqual(reference.returncode, expected, reference.stderr)
                finally:
                    budget.close()
                    self.assertFalse(budget.children)
                with self.session() as session:
                    native = session.make("all", observe_recipe_dispatch=True)
                    observed = [item["ignore_errors"] for item in native.semantics["recipe_dispatches"]]
                    self.last_ignore_evidence.append({
                        "scenario": name, "ordinary_exit": actual.returncode, "native_ignore": observed,
                    })
                    self.assertEqual(observed, flags)
                self.assertIsNone(session.base)
                self.assertFalse(session.budget.children)
                self.assertFalse(session.budget.producer_waiters)

    def test_native_job_policy_requires_complete_pid_bound_receipts(self):
        self.add("Makefile", "all:\n\t@true\n")
        for defect in ("missing-policy", "missing-helper", "wrong-pid", "invalid-flags"):
            with self.subTest(defect=defect), self.session() as session:
                original = session._sandbox_run

                def mutate(*arguments, **options):
                    completed, observed = original(*arguments, **options)
                    observed = {**observed, "accessed": list(observed["accessed"])}
                    prefix = "make-helper:" if defect == "missing-helper" else "make-job-policy:"
                    row = next(value for value in observed["accessed"] if value.startswith(prefix))
                    observed["accessed"].remove(row)
                    if not defect.startswith("missing"):
                        record = json.loads(row.removeprefix(prefix))
                        record[0 if defect == "wrong-pid" else 1] = 999999 if defect == "wrong-pid" else 4
                        observed["accessed"].append(prefix + json.dumps(record))
                    return completed, observed

                with patch.object(session, "_sandbox_run", mutate):
                    with self.assertRaises(MakeProbeError):
                        session.make("all")
                self.assertTrue(session.budget.failed)
            self.assertIsNone(session.base)
            self.assertFalse(session.budget.children)

    def test_parallel_jobs_keep_each_actual_command_policy(self):
        self.add("Makefile", "MAKEFLAGS += -j2\n.IGNORE: one\n.PHONY: all one two\n"
                 "all: one two\none:\n\t@printf one; exit 7\ntwo:\n\t@printf two; exit 7\n")
        with self.session() as session:
            result = session.make("all", observe_recipe_dispatch=True)
            policies = {
                item["arguments"][-1]: item["ignore_errors"]
                for item in result.semantics["recipe_dispatches"]
            }
            self.assertEqual(policies, {"printf one; exit 7": True, "printf two; exit 7": False})
        self.assertIsNone(session.base)
        self.assertFalse(session.budget.children)

    def test_multiline_recipe_literals_preserve_native_bytes_and_real_output(self):
        for literal in ("#", " ", ""):
            with self.subTest(literal=literal):
                records, outputs = [], []
                for value in ("first", "second"):
                    self.add("Makefile", "all:\n\t@printf '%s\\n' 'header\\\n\t" + literal + value + " '\n")
                    outputs.append(self.ordinary())
                    with self.session() as session:
                        recipe = session.make("all").semantics["files"][0]["recipe"]
                    record = self.observe()["all"]["record"]
                    self.assertEqual(record["variants"][0]["record"]["files"][0]["recipe"], recipe)
                    records.append(record)
                self.assertNotEqual(*outputs)
                self.assertNotEqual(*records)

    def test_make_comments_stay_stable_and_heredoc_data_is_native(self):
        self.add("Makefile", "all:\n\t@printf '%s\\n' hello\n")
        first = self.observe()["all"]["record"]
        self.add("Makefile", "# safe Make comment\nall:\n\t@printf '%s\\n' hello\n")
        self.assertEqual(self.ordinary(), b"hello\n")
        self.assertEqual(self.observe()["all"]["record"], first)
        records = []
        for value in ("first", "second"):
            self.add("Makefile", ".ONESHELL:\nall:\n\t@cat <<'EOF'\n\t#" + value + "\n\tEOF\n")
            self.assertEqual(self.ordinary(), ("#" + value + "\n").encode())
            with self.session() as session:
                recipe = session.make("all").semantics["files"][0]["recipe"]
            record = self.observe()["all"]["record"]
            self.assertEqual(record["variants"][0]["record"]["files"][0]["recipe"], recipe)
            records.append(record)
        self.assertNotEqual(*records)

    def test_inline_recipe_and_unused_debug_introspection_remain_recipe_context(self):
        self.add("Makefile", (
            "GUARD ?= FIRST\n"
            "all: ; @printf '#define $(GUARD) 1\\n'\n"
            "print-%: ; $(info $* is a $(flavor $*) variable) @true\n"
        ))
        record = self.observe(
            external={"GUARD"}, symbolic_recipe_names={"GUARD"},
            scoped_variable_names={"*"},
        )["all"]["record"]
        self.assertEqual(record["symbolic_recipe_names"], ["GUARD"])
        self.assertEqual(record["variants"][0]["record"]["domains"]["GUARD"]["value"], "FIRST")

    def test_function_semicolon_follows_native_recipe_interpretation(self):
        self.add("Makefile", "all: $(if yes,selected;@true,other)\n")
        self.add("selected", "data\n")
        record = self.observe()["all"]["record"]["variants"][0]["record"]
        self.assertEqual(record["files"][0]["prerequisites"], [
            {"name": "selected", "order_only": False},
        ])
        self.assertEqual(record["files"][0]["recipe"].strip(), "@true")

    def test_recipe_variable_change_is_measured_and_comment_only_is_stable(self):
        self.add("Makefile", "INNER = one\nOUTER = $(INNER)\nall:\n\t@echo $(OUTER)\n")
        first = self.observe()["all"]["record"]
        self.add("Makefile", "# unrelated comment\nINNER = one\nOUTER = $(INNER)\nall:\n\t@echo $(OUTER)\n")
        self.assertEqual(self.observe()["all"]["record"], first)
        self.add("Makefile", "INNER = two\nOUTER = $(INNER)\nall:\n\t@echo $(OUTER)\n")
        self.assertNotEqual(self.observe()["all"]["record"], first)

    def test_live_sized_112_name_fixture_runs_real_cli_states_not_registry_backfill(self):
        domains = {"VALUE_" + str(index): {"kind": "tracked-fallback"} for index in range(112)}
        self.add("Makefile", "".join(name + " ?= input\n" for name in domains)
                 + "all: " + " ".join("$(" + name + ")" for name in domains) + "\ninput: ;\n")
        result = self.observe(domains)["all"]
        states = result["record"]["variants"]
        self.assertEqual(len(states), 113)
        self.assertEqual({state["state"][0][1] for state in states if state["state"]}, set(domains))
        self.assertEqual(set(result["prerequisite_domain_census"]["enumerated"]), set(domains))

    def test_explicit_cross_branch_combinations_are_observed(self):
        self.add("Makefile", (
            "FIRST ?= no\nSECOND ?= no\n"
            "ifeq ($(FIRST),yes)\nifeq ($(SECOND),yes)\nSELECTED = combined\n"
            "else\nSELECTED = first\nendif\nelse\nSELECTED = baseline\nendif\n"
            "all: $(SELECTED)\ncombined first baseline: ;\n"
        ))
        domains = {name: {"kind": "explicit", "values": ["no", "yes"]} for name in ("FIRST", "SECOND")}
        result = self.observe(domains)["all"]
        self.assertIn("combined", {
            state["record"]["files"][0]["prerequisites"][0]["name"]
            for state in result["record"]["variants"]
        })

    def test_pattern_recipe_and_braced_short_variable_changes_are_observed(self):
        self.add("Makefile", "C = first\nLONG = ${C}\nall: item.out\n%.out: %.in\n\t@echo $@ $< $C ${LONG}\n")
        self.add("item.in", "data\n")
        first = self.observe(scoped_variable_names={"@", "<"})["all"]["record"]
        self.add("Makefile", "C = second\nLONG = ${C}\nall: item.out\n%.out: %.in\n\t@echo $@ $< $C ${LONG}\n")
        second = self.observe(scoped_variable_names={"@", "<"})["all"]["record"]
        self.assertNotEqual(first, second)
        self.assertEqual(first["variants"][0]["record"]["files"][1]["target"], "item.out")

    def test_real_target_error_after_budget_exhaustion_cannot_restart_a_report(self):
        self.add("Makefile", "all: ;\nother: ;\n")
        budget = ProbeBudget(Limits(states=1))
        loader = AuthorityLoader(self.root, GitTreeEntries(self.entries, budget=budget), budget=budget)
        with ProbeSession(loader, scratch_root=self.root / "build/probe", budget=budget) as session:
            with self.assertRaisesRegex(MakeProbeError, "aggregate variant"):
                run_probe(loader, {"all", "other"}, {}, {}, session=session)
            self.assertTrue(budget.failed)
            with self.assertRaises(MakeProbeError):
                session.make("all")
        self.assertFalse(budget.children)


if __name__ == "__main__":
    unittest.main()
