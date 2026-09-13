import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import unittest
from unittest.mock import patch

from scripts.validation_ownership import reporter
from scripts.validation_ownership.authority import AuthorityLoader, ENVIRONMENT, GitTreeEntries, GitTreeEntry, encoded
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.budget import Limits, MAX_PLANNED_STATE_BYTES
from scripts.validation_ownership.graph_probe import run_probe, source_census
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

    def session(self, budget=None):
        budget = ProbeBudget() if budget is None else budget
        loader = AuthorityLoader(self.root, GitTreeEntries(self.entries, budget=budget), budget=budget)
        return ProbeSession(loader, scratch_root=self.root / "build/probe", budget=budget)

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

    def ordinary(self, *assignments):
        budget = ProbeBudget()
        try:
            actual = budget.run(
                ["/usr/bin/make", "--no-print-directory", *assignments, "all"],
                cwd=self.root, env=ENVIRONMENT,
            )
            self.assertEqual(actual.returncode, 0, actual.stderr)
            return actual.stdout
        finally:
            budget.close()
            self.assertFalse(budget.children)

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
