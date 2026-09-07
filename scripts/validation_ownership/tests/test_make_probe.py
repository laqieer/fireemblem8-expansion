import hashlib
from pathlib import Path
import secrets
import shutil
import unittest

from scripts.validation_ownership.authority import AuthorityLoader, GitTreeEntries, GitTreeEntry
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.budget import Limits
from scripts.validation_ownership.graph_probe import run_probe, source_census
from scripts.validation_ownership.make_probe import ProbeSession


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

    def session(self):
        budget = ProbeBudget()
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
        return result

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

    def test_closed_census_tracks_nested_definitions_and_second_expansion(self):
        census = source_census({"Makefile": (
            b"MODE ?= x\nALIAS = $(MODE)\n"
            b"define RULE\nall: $$(ALIAS)\nendef\n$(eval $(RULE))\n"
        )})
        self.assertIn("MODE", census["graph"])
        self.assertIn("ALIAS", census["graph"])

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
