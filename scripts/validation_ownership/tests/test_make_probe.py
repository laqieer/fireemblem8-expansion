import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path
import secrets
import shutil
import unittest
from unittest.mock import patch

from scripts.validation_ownership import graph_probe, make_probe, reporter
from scripts.validation_ownership.authority import AuthorityLoader, ENVIRONMENT, GitTreeEntries, GitTreeEntry, encoded
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.budget import Limits, MAX_PLANNED_STATE_BYTES
from scripts.validation_ownership.graph_probe import _MakeSourceMode, make_source_units, run_probe, source_census
from scripts.validation_ownership.make_probe import Command, ProbeSession
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

    def ordinary(self, *assignments, environment=None, target="all", makefile=None):
        budget = ProbeBudget()
        try:
            actual = budget.run(
                ["/usr/bin/make", "--no-print-directory", *(("-f", makefile) if makefile is not None else ()),
                 *assignments, target],
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

    def original_target_slices(self, path, declarations, first, last):
        chunks = list(graph_probe._make_logical_chunks((ROOT / path).read_text()))
        start = next(chunk.start for chunk in chunks if chunk.text.startswith(first))
        end = next(chunk.end for chunk in chunks if chunk.start >= start and chunk.text.startswith(last))
        result = []
        for chunk in chunks:
            if chunk.start > end:
                break
            if chunk.start >= start or chunk.text.startswith(tuple(declarations)):
                result.append(chunk.text + "\n")
            else:
                result.append("# nondependent fixture source omitted" + " \\\n#" * (chunk.end - chunk.start) + "\n")
        return "".join(result)

    def observed_source_census(
        self, names=("FIRST", "SECOND"), *, assignments=(), witness=True,
        target="all", commands_factory=None, makefile="Makefile",
    ):
        from scripts.validation_ownership.graph_commands import MakeCommands
        if witness:
            self.original_input_witness()
        ordinary = self.ordinary(
            *(name + "=" + value for origin, name, value in assignments if origin == "command-line"),
            environment={name: value for origin, name, value in assignments if origin == "environment"},
            target=target,
            makefile=makefile,
        )
        modes = []
        bind = _MakeSourceMode.bind_invocation

        def bind_original(mode, target):
            modes.append(mode)
            return bind(mode, target)

        with self.session() as session:
            commands = MakeCommands(session, {}) if commands_factory is None else commands_factory(session)
            native = session.make(
                target, makefile=makefile, variables=("MAKEFILE_LIST", "MAKE_RESTARTS"), definitions=names,
                assignments=assignments, commands=commands,
            )
            self.last_include_observation = native
            records = native.semantics["definitions"]["global"]
            self.last_target_values = [records[name]["value"] for name in names]
            self.assertEqual(ordinary, ("\n".join(self.last_target_values) + "\n").encode())
            sources = graph_probe._loaded_sources(session, native, primary_source=makefile)
            with patch.object(_MakeSourceMode, "bind_invocation", bind_original):
                units, inputs, scoped = graph_probe._prepare_rule_templates(
                    session, target, assignments, commands, native, sources, primary_source=makefile,
                )
            self.assertEqual(len(modes), 1)
            self.assertIs(modes[0].budget, session.budget)
            usage = source_census(
                sources, reference_units=units, source_assignments=assignments, source_target=target,
                template_graph_inputs=inputs, template_scoped=scoped, budget=session.budget,
            )
            original_values = {
                name: {binding.value for binding in modes[0].definitions[name]} for name in names
            }
        self.assertFalse(session.budget.children)
        self.assertIsNone(session.base)
        return usage, original_values, records

    def include_writer(self, data, *, changing=False):
        (self.root / "build/include.mk").unlink(missing_ok=True)
        self.add("writer.py", (
            "from pathlib import Path\nroot=Path(__file__).resolve().parent\n"
            "out=(Path('/work') if root==Path('/repo') else root)/'build/include.mk'\n"
            "out.parent.mkdir(parents=True,exist_ok=True)\n"
            + f"data={data!r}\n"
            + "if not out.exists() or out.read_text()!=data:out.write_text(data)\n"
        ))
        seed = ""
        if changing:
            self.add("first.py", (
                "from pathlib import Path\nroot=Path(__file__).resolve().parent\n"
                "out=(Path('/work') if root==Path('/repo') else root)/'build/include.mk'\n"
                "out.parent.mkdir(parents=True,exist_ok=True)\nout.write_text('first: input\\n')\n"
            ))
            seed = "ifeq ($(MAKE_RESTARTS),)\nSEED := $(shell python3 first.py)\nendif\n"
        self.add("Makefile", seed + ".PHONY: FORCE\nFORCE:\nINC := build/include.mk\n$(INC): FORCE\n"
                 "\t@python3 writer.py\n-include $(INC)\n"
                 "FIRST = alpha  \\\n beta\nSECOND = alpha  \\\n beta\n"
                 "all:\n\t@printf '%s\\n' '$(value FIRST)' '$(value SECOND)'\n")
        programs = ("writer.py", "first.py") if changing else ("writer.py",)
        return lambda session: {"python3 " + program: Command(
            ("/usr/bin/python3", "/repo/" + program), code=(program,),
            outputs=("build/include.mk",), publication_policy="if-content-changed",
        ) for program in programs}

    def original_forced_include_fixture(self, *, renamed=False, target="assets-check"):
        from scripts.validation_ownership.graph_commands import MakeCommands
        self.add("fixture/source.json", '["fixture/input.h"]\n')
        self.add("fixture/bundle.json", '["fixture/bundle.h"]\n')
        self.add("fixture/input.h", "fixture input\n")
        self.add("fixture/bundle.h", "fixture bundle\n")
        self.add("scripts/generated_data/chapterbundle/schema.py", "# fixture prerequisite\n")
        self.add("writer.py", (
            "import argparse,json\nfrom pathlib import Path\n"
            "p=argparse.ArgumentParser()\n"
            "for name in ('source','bundle-source','make-target','depfile'):p.add_argument('--'+name,required=True)\n"
            "a=p.parse_args();root=Path(__file__).resolve().parent\n"
            "names=[a.source,a.bundle_source]+json.loads((root/a.source).read_text())+json.loads((root/a.bundle_source).read_text())\n"
            "data=a.make_target+': '+' '.join(str(root/name) for name in names)+'\\n'\n"
            "out=(Path('/work') if root==Path('/repo') else root)/a.depfile\n"
            "out.parent.mkdir(parents=True,exist_ok=True)\n"
            "if not out.exists() or out.read_text()!=data:out.write_text(data)\n"
        ))
        source = self.original_target_slices(
            "generated_data.mk",
            ("GENERATED_DATA_OUT_DIR ", "GENERATED_DATA_SHARED_PY_SOURCES :=",
             "GENERATED_DATA_CHAPTEROBJECTIVES_SOURCE ?=", "GENERATED_DATA_CHAPTEROBJECTIVES_CHAPTERBUNDLE_SOURCE ?="),
            "GENERATED_DATA_CHAPTEROBJECTIVES_C :=", "$(GENERATED_DATA_CHAPTEROBJECTIVES_C):",
        )
        prefix = "PROJECT_" if renamed else "GENERATED_DATA_"
        if renamed:
            source = source.replace("GENERATED_DATA_", prefix)
        path = "project-rules.mk" if renamed else "generated_data.mk"
        names = (prefix + "CHAPTEROBJECTIVES_DEPFILE", prefix + "CONFIG_INPUTS_chapterobjectives")
        self.add(path, source)
        self.add("Makefile", "PYTHON := python3\ninclude " + path + "\n" + target + ":\n\t@printf '%s\\n' "
                 + " ".join("'$(value " + name + ")'" for name in names) + "\n")
        assignments = (
            ("command-line", prefix + "CHAPTEROBJECTIVES_SOURCE", "fixture/source.json"),
            ("command-line", prefix + "CHAPTEROBJECTIVES_CHAPTERBUNDLE_SOURCE", "fixture/bundle.json"),
            ("command-line", prefix + "CHAPTEROBJECTIVES_DEP_DISCOVERY", "python3 writer.py"),
        )
        output = "build/generated/data/chapterobjectives.inputs.mk"
        generated_target = "build/generated/data/data_chapter_objectives.c"
        args = ("--source", "fixture/source.json", "--bundle-source", "fixture/bundle.json",
                "--make-target", generated_target, "--depfile", output)
        flat = 'python3 writer.py --source "fixture/source.json" --bundle-source "fixture/bundle.json" --make-target "' + generated_target + '" --depfile "' + output + '"'
        registry = json.loads((ROOT / ".github/validation-ownership-make-dynamics.json").read_text())

        def commands(session):
            adapters = MakeCommands(session, {item["expression"]: item for item in registry["contracts"]})
            writer = Command(
                ("/usr/bin/python3", "/repo/writer.py", *args), code=("writer.py",),
                sources=("fixture/source.json", "fixture/bundle.json"),
                outputs=(output,), publication_policy="if-content-changed",
            )
            return {
                flat: writer, flat.replace(" --", " \\\n\t--"): writer,
                "mkdir -p build/generated/data": adapters["mkdir -p build/generated/data"],
            }
        return names, assignments, commands

    def target_mode_fixture(self, source, *, assignments=(), witness=True):
        source += "FIRST = alpha  \\\n beta\nSECOND = alpha  \\\n beta\n"
        source += "all:\n\t@printf '%s\\n' '$(value FIRST)' '$(value SECOND)'\n"
        self.add("Makefile", source)
        usage, values, _ = self.observed_source_census(assignments=assignments, witness=witness)
        for name, native in zip(("FIRST", "SECOND"), self.last_target_values):
            self.assertEqual(values[name], {native})
            self.assertEqual(usage["definitions"][name], [native])
        return self.last_target_values

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
        options = {
            "declared_external_names": {
                "TOOLCHAIN", "PREFIX", "CPP", "PYTHON", "HOST_CC",
                "AUTOTOOLS_CONFIG_MK", "AUTOTOOLS_BUILD_DIR", "EXPANSION_HQ_MIXER",
            },
            "ambient_undefined_names": {"OS", "DEVKITARM", "MAKEOVERRIDES", "FE8_ITEM_ID_CAP"},
            "trusted_builtin_names": {"PATH", "MAKECMDGOALS", "MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS"},
        }
        with self.session() as session:
            startup = session.original_make_inputs("assets-check", ("OS", "PATH"))
            self.assertEqual(startup["OS"], {"origin": "undefined", "flavor": "undefined", "value": ""})
            self.assertEqual(startup["PATH"]["origin"], "environment")
            self.assertEqual(startup["PATH"]["value"], ENVIRONMENT["PATH"])
            native = session.make("assets-check", definitions=("CPPFLAGS", "EXE"), commands=MakeCommands(session, contracts))
            expected = native.semantics["definitions"]["global"]["CPPFLAGS"]["value"]
            self.assertEqual(native.semantics["definitions"]["global"]["EXE"],
                             {"origin": "file", "flavor": "simple", "value": ""})
            self.assertEqual(ordinary.stdout, (expected + "\n").encode())
            with patch.object(session, "original_make_inputs", wraps=session.original_make_inputs) as original_inputs:
                result = run_probe(
                    session.loader, {"assets-check"}, {}, contracts, session=session, **options,
                )["assets-check"]
            queried = {name for call in original_inputs.call_args_list for name in call.args[1]}
            self.assertTrue({"OS", "PATH"} <= queried)
        record = result["record"]["variants"][0]["record"]
        self.assertEqual(record["definitions"]["global"]["CPPFLAGS"]["value"], expected)
        self.assertIn("-undef -DFE8_ARCHIVAL_BUILD=1", expected)
        self.assertNotIn("-undef  -DFE8_ARCHIVAL_BUILD=1", expected)
        self.assertEqual(len(result["record"]["variants"]), 1)
        self.assertFalse(session.budget.children)
        witness = "scripts/generated_data/chapterbundle/__init__.py"
        (self.root / witness).unlink()
        del self.entries[witness]
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
                run_probe(session.loader, {"assets-check"}, {}, contracts, session=session, **options)

    def test_original_effect_free_inputs_and_branch_alternatives_do_not_invent_modes(self):
        continuation = "CPPFLAGS := -DFIRST=1 \\\n -DSECOND=2\nall:\n\t@/usr/bin/printf '%s\\n' '$(value CPPFLAGS)'\n"
        for prefix, expected_input, expected_value in (
            ("ifeq ($(OS),Windows_NT)\nEXE := .exe\nelse\nEXE :=\nendif\nAS := as$(EXE)\n", "OS", None),
            ("CHOICE = yes\nifeq ($(CHOICE),yes)\nVALUE := one\nelse\nVALUE := two\nendif\nCOPY := $(VALUE)\n", None, "one"),
            ("CHOICE := $(subst X,yes,X)\nifeq ($(CHOICE),yes)\nVALUE := one\nelse\nVALUE := two\nendif\nCOPY := $(VALUE)\n", "VALUE", "one"),
            ("CHOICE := $(subst X,no,X)\nifeq ($(CHOICE),yes)\nVALUE := one\nelse\nVALUE := two\nendif\nCOPY := $(VALUE)\n", "VALUE", "two"),
            ("export PATH := /usr/bin:$(PATH)\n", "PATH", None),
            ("CONFIG = absent.mk\n-include $(wildcard $(CONFIG))\n", None, None),
        ):
            with self.subTest(prefix=prefix):
                result, queried = self.original_effects_probe(prefix + continuation)
                self.assertEqual(self.ordinary(), b"-DFIRST=1 -DSECOND=2\n")
                if expected_input is not None:
                    self.assertIn(expected_input, queried)
                value = result["record"]["variants"][0]["record"]["definitions"]["global"]["CPPFLAGS"]["value"]
                self.assertEqual(value, "-DFIRST=1 -DSECOND=2")
                if expected_value is not None:
                    with self.session() as session:
                        native = session.make("all", definitions=("VALUE", "COPY"))
                    for name in ("VALUE", "COPY"):
                        self.assertEqual(native.semantics["definitions"]["global"][name],
                                         {"origin": "file", "flavor": "simple", "value": expected_value})

    def test_original_input_effect_evidence_does_not_hide_effectful_alternatives(self):
        continuation = "CPPFLAGS := -DFIRST=1 \\\n -DSECOND=2\nall:\n\t@/usr/bin/printf '%s\\n' '$(value CPPFLAGS)'\n"
        known = "CHOICE = yes\nVALUE = $(eval .POSIX:)\nifeq ($(CHOICE),yes)\nVALUE = literal\nendif\nCOPY := $(VALUE)\n"
        result, _ = self.original_effects_probe(known + continuation)
        self.assertEqual(self.ordinary(), b"-DFIRST=1 -DSECOND=2\n")
        self.assertEqual(result["record"]["variants"][0]["record"]["definitions"]["global"]["CPPFLAGS"]["value"],
                         "-DFIRST=1 -DSECOND=2")
        with self.session() as session:
            native = session.make("all", definitions=("VALUE", "COPY"))
        self.assertEqual(native.semantics["definitions"]["global"]["VALUE"]["value"], "literal")
        self.assertEqual(native.semantics["definitions"]["global"]["COPY"]["value"], "literal")
        for choice, spacing in (("yes", " "), ("no", "  ")):
            with self.subTest(opaque_choice=choice):
                source = known.replace("CHOICE = yes", "CHOICE := $(subst X," + choice + ",X)") + continuation
                self.add("Makefile", source)
                expected = "-DFIRST=1" + spacing + "-DSECOND=2"
                self.assertEqual(self.ordinary(), (expected + "\n").encode())
                with self.session() as session:
                    native = session.make("all", definitions=("CPPFLAGS", "COPY"))
                self.assertEqual(native.semantics["definitions"]["global"]["CPPFLAGS"]["value"], expected)
                self.assertEqual(native.semantics["definitions"]["global"]["COPY"]["value"],
                                 "literal" if choice == "yes" else "")
                with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
                    self.original_effects_probe(source)
        cases = (
            "OS = $(eval .POSIX:)Linux\nifeq ($(OS),Windows_NT)\nVALUE = one\nelse\nVALUE = two\nendif\n",
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

    def test_simple_append_rhs_effects_seal_defaults_in_all_declaration_forms(self):
        for append in (
            "UNUSED += $(eval MODE ?= first)\n",
            "override UNUSED += $(eval MODE ?= first)\n",
            "define UNUSED +=\n$(eval MODE ?= first)\nendef\n",
        ):
            with self.subTest(append=append):
                self.add("Makefile", "UNUSED :=\n" + append
                         + "all: $(MODE)\n\t@echo $(MODE)\nfirst second: ;\n")
                self.assertEqual(self.ordinary("MODE=first"), b"first\n")
                self.assertEqual(self.ordinary("MODE=second"), b"second\n")
                with self.session() as session:
                    baseline = session.make("all", definitions=("UNUSED", "MODE"))
                    actual = [
                        session.make("all", assignments=(("command-line", "MODE", value),))
                        .semantics["files"][0]["prerequisites"][0]["name"]
                        for value in ("first", "second")
                    ]
                self.assertEqual(actual, ["first", "second"])
                self.assertEqual(baseline.semantics["definitions"]["global"]["UNUSED"]["flavor"], "simple")
                self.assertEqual(baseline.semantics["definitions"]["global"]["MODE"], {
                    "origin": "file", "flavor": "recursive", "value": "first",
                })
                with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
                    self.observe()
                result = self.observe(
                    {"MODE": {"kind": "explicit", "values": ["first", "second"]}},
                    environment_names={"MODE"},
                )["all"]
                self.assertEqual(result["variable_census"]["defaults"], ["MODE"])
                self.assertEqual(result["prerequisite_domain_census"]["enumerated"], ["MODE"])
                self.assertEqual({
                    variant["record"]["files"][0]["prerequisites"][0]["name"]
                    for variant in result["record"]["variants"]
                }, {"first", "second"})
                self.assertEqual({
                    tuple(variant["state"][0]) for variant in result["record"]["variants"] if variant["state"]
                }, {(origin, "MODE", value) for origin in ("command-line", "environment") for value in ("first", "second")})

    def test_append_rhs_keeps_unused_recursive_and_escaped_bodies_lazy(self):
        for initial, append, flavor, raw in (
            ("UNUSED =\n", "UNUSED += $(eval MODE ?= first)$(shell touch marker)\n",
             "recursive", "$(eval MODE ?= first)$(shell touch marker)"),
            ("UNUSED =\n", "define UNUSED +=\n$(eval MODE ?= first)$(shell touch marker)\nendef\n",
             "recursive", "$(eval MODE ?= first)$(shell touch marker)"),
            ("UNUSED :=\n", "UNUSED += $$(eval MODE ?= first)\n", "simple", "$(eval MODE ?= first)"),
        ):
            with self.subTest(initial=initial, append=append):
                self.add("Makefile", initial + append + "all:\n\t@printf '%s\\n' '$(flavor UNUSED)' '$(value UNUSED)'\n")
                self.assertEqual(self.ordinary(), (flavor + "\n" + raw + "\n").encode())
                with self.session() as session:
                    native = session.make("all", definitions=("UNUSED", "MODE"))
                    self.assertEqual(native.semantics["definitions"]["global"]["UNUSED"]["flavor"], flavor)
                    self.assertEqual(native.semantics["definitions"]["global"]["UNUSED"]["value"], raw)
                    self.assertEqual(native.semantics["definitions"]["global"]["MODE"]["origin"], "undefined")
                    self.assertFalse((session.tree / "marker").exists())
                result = self.observe()["all"]
                self.assertEqual(result["variable_census"]["defaults"], [])
                self.assertEqual(result["prerequisite_domain_census"]["enumerated"], [])
                self.assertFalse((self.root / "marker").exists())

    def test_append_timing_separates_precedence_from_rhs_execution(self):
        cli = (("command-line", "UNUSED", "cli"),)
        environment = (("environment", "UNUSED", "environment"),)
        for source, assignments, expected, flavor, origin in (
            ("UNUSED :=\nUNUSED += $(eval MODE ?= first)\n", cli, False, "recursive", "command line"),
            ("UNUSED :=\noverride UNUSED += $(eval MODE ?= first)\n", cli, False, "recursive", "override"),
            ("UNUSED := $(eval MODE ?= first)\n", cli, True, "recursive", "command line"),
            ("override UNUSED := old\nUNUSED += $(eval MODE ?= first)\n", (), True, "simple", "override"),
            ("UNUSED :=\nUNUSED += $(eval MODE ?= first)\n", environment, True, "simple", "file"),
            ("UNUSED += $(eval MODE ?= first)\n", environment, False, "recursive", "file"),
            ("UNUSED :=\noverride UNUSED +=\nUNUSED =\nUNUSED += $(eval MODE ?= first)\n",
             (), False, "recursive", "file"),
            ("UNUSED :=\nprivate UNUSED += $(eval MODE ?= first)\n", (), True, "simple", "file"),
            ("UNUSED :=\nexport UNUSED += $(eval MODE ?= first)\n", (), True, "simple", "file"),
            ("UNUSED :=\noverride define UNUSED +=\n$(eval MODE ?= first)\nendef\n", cli, False, "recursive", "override"),
            ("override UNUSED :=\nall: UNUSED += $(eval MODE ?= first)\n", cli, False, "simple", "override"),
            ("UNUSED :=\noverride UNUSED += $(eval IGNORED =) \nUNUSED =\nUNUSED += $(eval MODE ?= first)\n",
             (), True, "simple", "override"),
            ("UNUSED :=\noverride define UNUSED +=\n $(eval IGNORED =)\nendef\nUNUSED =\n"
             "UNUSED += $(eval MODE ?= first)\n", (), True, "simple", "override"),
        ):
            with self.subTest(source=source, assignments=assignments):
                source += "$(info $(origin MODE))\nall: ;\n"
                self.add("Makefile", source)
                ordinary = self.ordinary(
                    *(name + "=" + value for kind, name, value in assignments if kind == "command-line"),
                    environment={name: value for kind, name, value in assignments if kind == "environment"},
                )
                self.assertEqual(ordinary.splitlines()[0], b"file" if expected else b"undefined")
                with self.session() as session:
                    native = session.make("all", definitions=("MODE", "UNUSED"), assignments=assignments)
                values = native.semantics["definitions"]["global"]
                self.assertEqual(values["MODE"]["origin"] == "file", expected)
                self.assertEqual((values["UNUSED"]["flavor"], values["UNUSED"]["origin"]), (flavor, origin))
                census = source_census({"Makefile": source.encode()}, source_assignments=assignments, source_target="all")
                self.assertEqual(census["defaults"], {"MODE"} if expected else set())

    def test_append_timing_uses_original_scope_and_flavor_history(self):
        for source, expected, global_flavor, local_flavor in (
            ("UNUSED :=\nUNUSED += $(eval MODE ?= first)\nUNUSED = literal\n", True, "recursive", "recursive"),
            ("UNUSED =\nUNUSED += $(eval MODE ?= first)\nUNUSED := literal\n", False, "simple", "simple"),
            ("all: UNUSED :=\nall: UNUSED += $(eval MODE ?= first)\n", True, "undefined", "simple"),
            ("UNUSED :=\nall: UNUSED += $(eval MODE ?= first)\n", False, "simple", "recursive"),
            ("all: UNUSED =\nall: UNUSED += $(eval MODE ?= first)\n", False, "undefined", "recursive"),
            ("other: UNUSED :=\nall: UNUSED += $(eval MODE ?= first)\n", False, "undefined", "recursive"),
        ):
            with self.subTest(source=source):
                source += "$(info $(origin MODE))\nall: ;\n"
                self.add("Makefile", source)
                self.assertEqual(self.ordinary().splitlines()[0], b"file" if expected else b"undefined")
                with self.session() as session:
                    native = session.make("all", definitions=("MODE", "UNUSED"))
                metadata = native.semantics["definitions"]
                self.assertEqual(metadata["global"]["MODE"]["origin"] == "file", expected)
                self.assertEqual(metadata["global"]["UNUSED"]["flavor"], global_flavor)
                self.assertEqual(metadata["files"][0]["variables"]["UNUSED"]["flavor"], local_flavor)
                census = source_census({"Makefile": source.encode()}, source_target="all")
                self.assertEqual(census["defaults"], {"MODE"} if expected else set())
        self.add("Makefile", "UNUSED =\n$(eval UNUSED :=)\nUNUSED += $(eval MODE ?= first)\nall: $(MODE)\nfirst second: ;\n")
        with self.session() as session:
            native = session.make("all", definitions=("MODE", "UNUSED"))
        self.assertEqual(native.semantics["definitions"]["global"]["UNUSED"]["flavor"], "simple")
        self.assertEqual(native.semantics["definitions"]["global"]["MODE"]["value"], "first")
        with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
            self.observe()
        result = self.observe({"MODE": {"kind": "explicit", "values": ["first", "second"]}})["all"]
        self.assertEqual(result["variable_census"]["defaults"], ["MODE"])
        self.add("Makefile", "UNUSED =\nCHANGE = UNUSED :=\n$(eval $(CHANGE))\n"
                 "UNUSED += $(eval MODE ?= first)\nall: $(MODE)\nfirst: ;\n")
        with self.session() as session:
            native = session.make("all", definitions=("MODE", "UNUSED"))
        self.assertEqual(native.semantics["definitions"]["global"]["UNUSED"]["flavor"], "simple")
        self.assertEqual(native.semantics["definitions"]["global"]["MODE"]["value"], "first")
        with self.assertRaisesRegex(MakeProbeError, "unproven original Make append RHS timing"):
            self.observe()

    def test_append_uses_original_inputs_and_retains_unknown_branch_timing(self):
        self.original_input_witness()
        self.add("Makefile", "UNUSED += $(eval MODE ?= first)$(shell touch marker)\nall:\n\t@echo '$(flavor UNUSED)'\n")
        self.assertEqual(self.ordinary(), b"recursive\n")
        with self.session() as session:
            with patch.object(session, "original_make_inputs", wraps=session.original_make_inputs) as original:
                result = run_probe(session.loader, {"all"}, {}, {}, session=session)["all"]
            self.assertIn("UNUSED", {name for call in original.call_args_list for name in call.args[1]})
            native = session.make("all", definitions=("MODE", "UNUSED"))
            self.assertEqual(native.semantics["definitions"]["global"]["MODE"]["origin"], "undefined")
            self.assertEqual(native.semantics["definitions"]["global"]["UNUSED"]["flavor"], "recursive")
        self.assertEqual(result["variable_census"]["defaults"], [])
        self.assertFalse((self.root / "marker").exists())
        source = ("UNUSED =\nCHOICE = yes\nifeq ($(CHOICE),yes)\nUNUSED :=\nendif\n"
                  "UNUSED += $(eval MODE ?= first)\nall: $(MODE)\nfirst: ;\n")
        self.add("Makefile", source)
        with self.session() as session:
            native = session.make("all", definitions=("MODE", "UNUSED"))
        self.assertEqual(native.semantics["definitions"]["global"]["MODE"]["value"], "first")
        self.assertEqual(native.semantics["definitions"]["global"]["UNUSED"]["flavor"], "simple")
        with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
            self.observe()
        domains = {"MODE": {"kind": "explicit", "values": ["first"]}}
        known = self.observe(domains)["all"]
        self.assertEqual(known["variable_census"]["defaults"], ["MODE"])
        self.assertEqual(len(known["record"]["variants"]), 2)
        for variant in known["record"]["variants"]:
            self.assertEqual(variant["record"]["files"][0]["prerequisites"],
                             [{"name": "first", "order_only": False}])
        for choice in ("yes", "no"):
            with self.subTest(opaque_choice=choice):
                self.add("Makefile", source.replace("CHOICE = yes", "CHOICE := $(subst X," + choice + ",X)"))
                self.ordinary()
                with self.session() as session:
                    native = session.make("all", definitions=("MODE", "UNUSED"))
                metadata = native.semantics["definitions"]["global"]
                self.assertEqual(metadata["UNUSED"]["flavor"], "simple" if choice == "yes" else "recursive")
                self.assertEqual(metadata["MODE"]["origin"], "file" if choice == "yes" else "undefined")
                self.assertEqual(native.semantics["files"][0]["prerequisites"],
                                 [{"name": "first", "order_only": False}] if choice == "yes" else [])
                with self.assertRaisesRegex(MakeProbeError, "unproven original Make append RHS timing"):
                    self.observe(domains)

    def test_emitted_append_requires_original_timing_and_preserves_expansion_stages(self):
        for operator in ("=", ":="):
            with self.subTest(operator=operator):
                source = "UNUSED " + operator + "\n$(eval UNUSED += $$(eval MODE ?= first))\nall: ;\n"
                self.add("Makefile", source)
                with self.session() as session:
                    native = session.make("all", definitions=("MODE", "UNUSED"))
                self.assertEqual(native.semantics["definitions"]["global"]["MODE"]["origin"],
                                 "file" if operator == ":=" else "undefined")
                if operator == ":=":
                    with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
                        self.observe()
                    result = self.observe({"MODE": {"kind": "explicit", "values": ["first", "second"]}})["all"]
                    self.assertEqual(result["variable_census"]["defaults"], ["MODE"])
                else:
                    self.assertEqual(self.observe()["all"]["variable_census"]["defaults"], [])
        self.add("Makefile", "UNUSED :=\ndefine RULE\nUNUSED += $$(eval MODE ?= first)\nendef\n"
                 "$(eval $(RULE))\nall: ;\n")
        with self.session() as session:
            native = session.make("all", definitions=("MODE",))
        self.assertEqual(native.semantics["definitions"]["global"]["MODE"]["value"], "first")
        with self.assertRaisesRegex(MakeProbeError, "unproven emitted Make append RHS timing"):
            self.observe()
        self.add("Makefile", "UNUSED =\n$(eval UNUSED += $(eval MODE ?= first))\nall: $(MODE)\nfirst second: ;\n")
        with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
            self.observe()
        result = self.observe({"MODE": {"kind": "explicit", "values": ["first", "second"]}})["all"]
        self.assertEqual(result["variable_census"]["defaults"], ["MODE"])
        self.assertEqual(result["prerequisite_domain_census"]["enumerated"], ["MODE"])
        self.add("Makefile", ".SECONDEXPANSION:\nall: $$(eval MODE ?= first)\n")
        with self.session() as session:
            native = session.make("all", definitions=("MODE",))
        self.assertEqual(native.semantics["definitions"]["global"]["MODE"]["value"], "first")
        with self.assertRaisesRegex(MakeProbeError, "external-default declaration has a dynamic name"):
            self.observe()

    def test_actual_generated_data_target_prefix_has_original_parse_stage_proof(self):
        for grouped, renamed in ((False, False), (True, False), (True, True)):
            with self.subTest(grouped=grouped, renamed=renamed):
                source = self.original_target_slices(
                    "generated_data.mk", ("GENERATED_DATA_OUT_DIR ", "GENERATED_DATA_CONFIG_INPUTS_items :="),
                    "GENERATED_DATA_ITEM_CAP_STAMP :=",
                    "GENERATED_DATA_CONFIG_INPUTS_items += $(GENERATED_DATA_ACTIVE_HEADER)" if grouped
                    else "GENERATED_DATA_CONFIG_INPUTS_items +=",
                )
                names = ("GENERATED_DATA_ITEM_CAP_STAMP", "GENERATED_DATA_CONFIG_INPUTS_items")
                path = "generated_data.mk"
                if renamed:
                    source = source.replace("GENERATED_DATA_", "PROJECT_DATA_")
                    names = tuple(name.replace("GENERATED_DATA_", "PROJECT_DATA_") for name in names)
                    path = "project-rules.mk"
                self.add(path, source)
                self.add("Makefile", "include " + path + "\nall:\n\t@printf '%s\\n' "
                         + " ".join("'$(value " + name + ")'" for name in names) + "\n")
                _, values, native = self.observed_source_census(names)
                self.assertEqual(native[names[0]]["value"], "build/generated/data/.item_id_cap.stamp")
                for name in names:
                    self.assertEqual(values[name], {native[name]["value"]})
                expected = "include/constants/items.h include/bmitem.h include/variables.h include/constants/msg.h "
                expected += "src/data/data_item_icon.c include/constants/items_expansion.h src/data/items_expansion.json "
                expected += "build/generated/data/.item_id_cap.stamp"
                if grouped:
                    expected += " build/generated/data/id_space_active.h"
                self.assertEqual(native[names[1]]["value"], expected)

    def test_actual_modern_pattern_target_slice_keeps_parent_continuations(self):
        source = self.original_target_slices(
            "modern.mk", ("MODERN_CONFIG ?=", "MODERN_ABI ?=", "MODERN_BUILD_ROOT :=", "MODERN_OUTPUT_DIR :="),
            "$(MODERN_OUTPUT_DIR)/%.o: %.c", '\t"$(MODERN_CC)" $(MODERN_CFLAGS) -MMD',
        )
        self.add("modern.mk", source)
        self.assertEqual(self.target_mode_fixture("include modern.mk\n"), ["alpha beta", "alpha beta"])
        renamed = source.replace("MODERN_", "PROJECT_")
        self.add("other-rules.mk", renamed)
        self.assertEqual(self.target_mode_fixture("include other-rules.mk\n"), ["alpha beta", "alpha beta"])

    def test_generated_targets_keep_delayed_posix_and_original_lexical_roles(self):
        ordinary = ["alpha beta", "alpha beta"]
        delayed = ["alpha beta", "alpha   beta"]
        for source, expected in (
            ("TARGET = ordinary\n$(TARGET):\n", ordinary),
            ("T = ordinary\n${T}:\n", ordinary),
            ("T = ordinary\n$T:\n", ordinary),
            ("TARGET = .POSIX\n$(TARGET):\n", delayed),
            ("TARGET = one .POSIX two\n$(TARGET):\n", delayed),
            ("EMPTY =\n$(EMPTY):\n", ordinary),
            ("EMPTY =\n.POSIX:\n$(EMPTY)\n", ["alpha   beta", "alpha   beta"]),
            ("obj/%.o:\n", ordinary),
            (".POSIX%:\n", ordinary),
            ("./.POSIX:\n", delayed),
            ("././.POSIX:\n", delayed),
            (".//.POSIX:\n", delayed),
            (".POSIX/:\n", ordinary),
            ("directory/.POSIX:\n", ordinary),
            (".POSIX other%:\n", delayed),
            ("\\.POSIX:\n", ordinary),
            (".POSIX\\ :\n", ordinary),
            ("\\ .POSIX:\n", ordinary),
            ("one\\ two:\n", ordinary),
            ("one\\:two:\n", ordinary),
            (".POSIX\\::\n", ordinary),
            (".POSIX &: ;\n", delayed),
            (".POSIX&: ;\n", delayed),
            (".POSIX& : ;\n", ordinary),
            ("TARGET = .POSIX&\n$(TARGET): ;\n", ordinary),
            (".POSIX::\n", delayed),
            ("ordinary: .POSIX\n", ordinary),
            ("$$literal:\n", ordinary),
        ):
            with self.subTest(source=source):
                self.assertEqual(self.target_mode_fixture(source), expected)

    def test_target_proof_keeps_original_flavor_origin_and_history(self):
        ordinary = ["alpha beta", "alpha beta"]
        delayed = ["alpha beta", "alpha   beta"]
        for source, assignments, expected in (
            ("INPUT = ordinary\nTARGET := $(INPUT)\nINPUT = .POSIX\n$(TARGET):\n", (), ordinary),
            ("INPUT = ordinary\nTARGET = $(INPUT)\nINPUT = .POSIX\n$(TARGET):\n", (), delayed),
            ("TARGET = ordinary\n$(TARGET):\nTARGET = .POSIX\n", (), ordinary),
            ("TARGET = .POSIX\n$(TARGET):\nTARGET = ordinary\n", (), ["alpha   beta", "alpha   beta"]),
            ("TARGET = ordinary\n$(TARGET):\n", (("command-line", "TARGET", ".POSIX"),), delayed),
            ("TARGET = ordinary\n$(TARGET):\n", (("environment", "TARGET", ".POSIX"),), ordinary),
            ("TARGET ?= ordinary\n$(TARGET):\n", (("environment", "TARGET", ".POSIX"),), delayed),
            ("TARGET ?= ordinary\n$(TARGET):\n", (("environment", "TARGET", ""),), ordinary),
            ("override TARGET = .POSIX\n$(TARGET):\n", (("command-line", "TARGET", "ordinary"),), delayed),
            ("CHOICE = yes\nTARGET = first\nifeq ($(CHOICE),yes)\nTARGET = second\nendif\n$(TARGET):\n", (), ordinary),
            ("CHOICE = yes\nPART = first\nifeq ($(CHOICE),yes)\nPART = second\nendif\nTARGET := out/$(PART)\n$(TARGET):\n",
             (), ordinary),
        ):
            with self.subTest(source=source, assignments=assignments):
                self.assertEqual(self.target_mode_fixture(source, assignments=assignments), expected)
        self.add("mode.mk", "TARGET = .POSIX\n$(TARGET):\n")
        self.assertEqual(self.target_mode_fixture("include mode.mk\n"), ["alpha   beta", "alpha   beta"])

    def test_unproven_target_results_do_not_gain_final_value_or_normal_mode_authority(self):
        known = "CHOICE = yes\nTARGET = ordinary\nifeq ($(CHOICE),yes)\nTARGET = .POSIX\nendif\n$(TARGET):\n"
        self.assertEqual(self.target_mode_fixture(known), ["alpha beta", "alpha   beta"])
        literal_values = _MakeSourceMode.literal_values
        alternatives = []

        def record_target(mode, expression, *args, **kwargs):
            values = literal_values(mode, expression, *args, **kwargs)
            if expression == "$(TARGET)" and values is not None:
                alternatives.append(frozenset(values))
            return values

        for choice in ("yes", "no"):
            with self.subTest(opaque_choice=choice):
                alternatives.clear()
                source = known.replace("CHOICE = yes", "CHOICE := $(subst X," + choice + ",X)")
                with patch.object(_MakeSourceMode, "literal_values", record_target):
                    with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
                        self.target_mode_fixture(source)
                self.assertIn(frozenset({"ordinary", ".POSIX"}), alternatives)
                self.assertEqual(self.last_target_values,
                                 ["alpha beta", "alpha   beta"] if choice == "yes" else ["alpha beta"] * 2)
        for source, witness in (
            ("$(UNPROVEN):\n", False),
            ("TARGET := $(subst MARK,.POSIX,MARK)\n$(TARGET):\nTARGET = ordinary\n", True),
            ("TARGET := $(if yes,ordinary)\n$(TARGET):\n", True),
            ("TARGET = $(eval .POSIX:)ordinary\n$(TARGET):\n", True),
            ("wild*:\n", True),
            ("TARGET = .POSIX:\n$(TARGET) ;\n", True),
            ("TARGET = ordinary: .POSIX\n$(TARGET)\n", True),
            ("TARGET = ordinary\n$(eval TARGET = .POSIX)\n$(TARGET):\n", True),
            ("$$(.POSIX):\n", True),
            (".POSIX\\&: ;\n", True),
        ):
            with self.subTest(source=source):
                if not witness:
                    path = "scripts/generated_data/chapterbundle/__init__.py"
                    (self.root / path).unlink()
                    del self.entries[path]
                with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
                    self.target_mode_fixture(source, witness=witness)
                self.assertEqual(len(self.last_target_values), 2)
        self.add(".POSIX", "fixture filename for a real wildcard match\n")
        with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
            self.target_mode_fixture(".POSI*:\n")
        self.assertEqual(self.last_target_values, ["alpha beta", "alpha   beta"])

    def test_target_proof_keeps_literal_metadata_bodies_unexpanded(self):
        for lookup in ("$(origin RULE)", "$(flavor RULE)"):
            with self.subTest(lookup=lookup):
                source = "RULE = $(error unused body)$(shell touch marker)\n" + lookup + ":\n"
                self.assertEqual(self.target_mode_fixture(source), ["alpha beta", "alpha beta"])
                self.assertFalse((self.root / "marker").exists())
        self.assertEqual(self.target_mode_fixture("RULE = ordinary\n$(value RULE):\n"), ["alpha beta", "alpha beta"])
        self.assertEqual(
            self.target_mode_fixture("RULE = $(error unused body)\nTARGET := $(origin RULE)\n$(TARGET):\n"),
            ["alpha beta", "alpha beta"],
        )
        with self.assertRaises(MakeProbeError):
            self.target_mode_fixture("RULE = ordinary\nNAME = RULE\n$(origin $(NAME)):\n")

    def test_original_target_value_alternatives_keep_the_existing_context_bound(self):
        literal_values = _MakeSourceMode.literal_values
        for width in (9, 10):
            for opaque in (False, True):
                with self.subTest(width=width, opaque=opaque):
                    source = "" if opaque else "CHOICE = yes\n"
                    for index in range(width):
                        name = "PART_" + str(index)
                        choice = "CHOICE_" + str(index) if opaque else "CHOICE"
                        if opaque:
                            source += choice + " := $(subst X,yes,X)\n"
                        source += name + " = a\nifeq ($(" + choice + "),yes)\n" + name + " = b\nendif\n"
                    expression = "out/" + "".join("$(PART_" + str(index) + ")" for index in range(width))
                    source += "TARGET := " + expression + "\n$(TARGET):\n"
                    sizes = []

                    def record_plan(mode, text, *args, **kwargs):
                        values = literal_values(mode, text, *args, **kwargs)
                        if text == expression and values is not None:
                            sizes.append(len(values))
                        return values

                    with patch.object(_MakeSourceMode, "literal_values", record_plan):
                        if opaque and width == 10:
                            with self.assertRaisesRegex(MakeProbeError, "existing bounded context plan"):
                                self.target_mode_fixture(source)
                            self.assertEqual(self.last_target_values, ["alpha beta"] * 2)
                        else:
                            self.assertEqual(self.target_mode_fixture(source), ["alpha beta"] * 2)
                            self.assertIn(512 if opaque else 1, sizes)
                    target = "out/" + "b" * width
                    with self.session() as session:
                        native = session.make(target, definitions=("TARGET",))
                    self.assertEqual(native.semantics["definitions"]["global"]["TARGET"]["value"], target)
                    self.assertEqual(native.semantics["files"][0]["target"], target)

    def test_variable_include_names_join_actual_sources_and_empty_lists(self):
        self.add("child.mk", "CHILD = ordinary\n")
        self.add("other.mk", "OTHER = ordinary\n")
        self.add("sub/child.mk", "CHILD = ordinary\n")
        self.add("odd:equal=semi;.mk", "ODD = ordinary\n")
        self.add("$literal.mk", "LITERAL = ordinary\n")
        for directive in ("include", "-include", "sinclude"):
            for prefix, expression, expected in (
                ("INC := child.mk\n", "$(INC)", ["Makefile", "child.mk"]),
                ("DIR = sub\nINC = $(DIR)/child.mk\n", "${INC}", ["Makefile", "sub/child.mk"]),
                ("INC = child.mk other.mk\n", "$(INC)", ["Makefile", "child.mk", "other.mk"]),
                ("INC =\n", "$(INC)", ["Makefile"]),
                ("", "", ["Makefile"]),
                ("INC = odd:equal=semi;.mk\n", "$(INC)", ["Makefile", "odd:equal=semi;.mk"]),
                ("", "$$literal.mk", ["Makefile", "$literal.mk"]),
            ):
                with self.subTest(directive=directive, expression=expression):
                    self.assertEqual(self.target_mode_fixture(prefix + directive + " " + expression + "\n"),
                                     ["alpha beta", "alpha beta"])
                    self.assertEqual(
                        self.last_include_observation.semantics["domains"]["MAKEFILE_LIST"]["value"].split(), expected,
                    )

    def test_original_include_context_preserves_history_origins_and_proven_conditions(self):
        self.add("ordinary.mk", "VALUE = ordinary\n")
        self.add("posix.mk", ".POSIX:\n")
        normal, posix = ["alpha beta"] * 2, ["alpha   beta"] * 2
        for source, assignments, expected in (
            ("NAME = ordinary.mk\nINC := $(NAME)\nNAME = posix.mk\ninclude $(INC)\n", (), normal),
            ("NAME = ordinary.mk\nINC = $(NAME)\nNAME = posix.mk\ninclude $(INC)\n", (), posix),
            ("INC = ordinary.mk\ninclude $(INC)\nINC = posix.mk\n", (), normal),
            ("INC = posix.mk\ninclude $(INC)\nINC = ordinary.mk\n", (), posix),
            ("INC = ordinary.mk\ninclude $(INC)\n", (("command-line", "INC", "posix.mk"),), posix),
            ("INC = ordinary.mk\ninclude $(INC)\n", (("environment", "INC", "posix.mk"),), normal),
            ("INC ?= ordinary.mk\ninclude $(INC)\n", (("environment", "INC", "posix.mk"),), posix),
            ("INC = posix.mk\nifeq ($(MAKECMDGOALS),all)\ninclude $(INC)\nendif\n", (), posix),
            ("INC = missing.mk\nifeq ($(MAKECMDGOALS),other)\n-include $(INC)\nendif\n", (), normal),
        ):
            with self.subTest(source=source, assignments=assignments):
                self.assertEqual(self.target_mode_fixture(source, assignments=assignments), expected)

    def test_variable_include_occurrences_keep_order_and_reject_changed_source_or_cycles(self):
        self.add("one.mk", "VALUE = one\n")
        self.add("two.mk", "VALUE = two\n")
        self.assertEqual(
            self.target_mode_fixture("INC = one.mk\ninclude $(INC)\nINC = two.mk\ninclude $(INC)\nINC = one.mk\ninclude $(INC)\n"),
            ["alpha beta"] * 2,
        )
        self.assertEqual(self.last_include_observation.semantics["domains"]["MAKEFILE_LIST"]["value"].split(),
                         ["Makefile", "one.mk", "two.mk", "one.mk"])
        self.add("changed.mk", "VALUE = alpha  \\\n beta\n")
        with self.assertRaisesRegex(MakeProbeError, "source changed"):
            self.target_mode_fixture("INC = changed.mk\ninclude $(INC)\n.POSIX:\nRECORDED = yes\ninclude $(INC)\n")
        with self.assertRaisesRegex(MakeProbeError, "recursive"):
            source_census({
                "Makefile": b"INC = child.mk\ninclude $(INC)\n",
                "child.mk": b"include Makefile\n",
            })

    def test_unproven_and_missing_include_outcomes_do_not_become_empty_success(self):
        self.add("ordinary.mk", "VALUE = ordinary\n")
        self.add("posix.mk", ".POSIX:\n")
        literal_values = _MakeSourceMode.literal_values
        alternatives = []

        def record_include(mode, expression, *args, **kwargs):
            values = literal_values(mode, expression, *args, **kwargs)
            if expression == "$(INC)" and values is not None:
                alternatives.append(frozenset(values))
            return values

        for kind, known, expected_values, expected_visits in (
            ("selection", "CHOICE = yes\nINC = ordinary.mk\nifeq ($(CHOICE),yes)\nINC = posix.mk\nendif\ninclude $(INC)\n",
             ["alpha   beta"] * 2, ["Makefile", "posix.mk"]),
            ("presence", "CHOICE = yes\nINC = ordinary.mk\nifeq ($(CHOICE),yes)\ninclude $(INC)\nendif\n",
             ["alpha beta"] * 2, ["Makefile", "ordinary.mk"]),
        ):
            self.assertEqual(self.target_mode_fixture(known), expected_values)
            self.assertEqual(self.last_include_observation.semantics["domains"]["MAKEFILE_LIST"]["value"].split(),
                             expected_visits)
            for choice in ("yes", "no"):
                with self.subTest(kind=kind, opaque_choice=choice):
                    alternatives.clear()
                    source = known.replace("CHOICE = yes", "CHOICE := $(subst X," + choice + ",X)")
                    with patch.object(_MakeSourceMode, "literal_values", record_include):
                        with self.assertRaises(MakeProbeError):
                            self.target_mode_fixture(source)
                    visits = expected_visits if choice == "yes" else (
                        ["Makefile", "ordinary.mk"] if kind == "selection" else ["Makefile"]
                    )
                    self.assertEqual(self.last_include_observation.semantics["domains"]["MAKEFILE_LIST"]["value"].split(),
                                     visits)
                    self.assertEqual(self.last_target_values, expected_values if choice == "yes" else ["alpha beta"] * 2)
                    if kind == "selection":
                        self.assertIn(frozenset({"ordinary.mk", "posix.mk"}), alternatives)
        for source in (
            "INC := $(subst X,ordinary.mk,X)\ninclude $(INC)\n",
            "INC = $(eval .POSIX:)ordinary.mk\ninclude $(INC)\n",
            "INC = ./*.mk\n-include $(INC)\n",
        ):
            with self.subTest(source=source):
                with self.assertRaises(MakeProbeError):
                    self.target_mode_fixture(source)
        self.add("Makefile", "INC = missing.mk\n-include $(INC)\nall: ;\n")
        with self.session() as session:
            native = session.make("all", variables=("MAKEFILE_LIST", "MAKE_RESTARTS"))
            self.assertEqual(native.semantics["domains"]["MAKEFILE_LIST"]["value"], "Makefile")
            with self.assertRaisesRegex(MakeProbeError, "unadmitted Make file-open"):
                graph_probe._loaded_sources(session, native, primary_source="Makefile")
        with self.assertRaisesRegex(MakeProbeError, "include outcome"):
            source_census({"Makefile": b"INC = missing.mk\n-include $(INC)\nall: ;\n"})
        self.add("Makefile", "INC = missing.mk\ninclude $(INC)\nall: ;\n")
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "GNU Make failed"):
                session.make("all", variables=("MAKEFILE_LIST",))
        with self.assertRaisesRegex(MakeProbeError, "include outcome"):
            source_census({"Makefile": b"include missing.mk\nall: ;\n"})
        self.assertEqual(self.target_mode_fixture("INC = absent.mk\n-include $(wildcard $(INC))\n"),
                         ["alpha beta"] * 2)

    def test_original_force_backed_include_uses_real_stable_remake_evidence(self):
        for renamed, target in ((False, "assets-check"), (True, "assets-check"),
                                (False, "validation-ownership-check")):
            with self.subTest(renamed=renamed, target=target):
                names, assignments, commands = self.original_forced_include_fixture(renamed=renamed, target=target)
                _, values, records = self.observed_source_census(
                    names, assignments=assignments, target=target, commands_factory=commands,
                )
                for name in names:
                    self.assertEqual(values[name], {records[name]["value"]})
                native = self.last_include_observation
                if target == "validation-ownership-check":
                    self.assertEqual(native.generated, ())
                    self.assertEqual(native.semantics["domains"]["MAKE_RESTARTS"]["origin"], "undefined")
                else:
                    self.assertEqual(native.semantics["domains"]["MAKE_RESTARTS"],
                                     {"value": "1", "origin": "environment", "flavor": "recursive"})
                    self.assertEqual(len(native.generated), 1)
                    self.assertEqual(native.generated[0].path, "build/generated/data/chapterobjectives.inputs.mk")
                    self.assertEqual(native.generated[0].data,
                                     b"build/generated/data/data_chapter_objectives.c: /repo/fixture/source.json /repo/fixture/bundle.json /repo/fixture/input.h /repo/fixture/bundle.h\n")
                    self.assertEqual(len(native.events), 4)
                    self.assertEqual(len(native.semantics["dynamic_commands"]), 2)

    def test_remade_include_requires_stable_literal_dependency_history(self):
        commands = self.include_writer("plain: input\n")
        _, values, _ = self.observed_source_census(commands_factory=commands)
        self.assertEqual(values["FIRST"], {"alpha beta"})
        self.assertEqual(self.last_include_observation.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
        for data in (".POSIX:\n", "VARIABLE = changed\n", "plain: $(eval MODE ?= changed)\n",
                     ".SECONDEXPANSION:\n", "include other.mk\n"):
            with self.subTest(data=data):
                commands = self.include_writer(data)
                if data == "include other.mk\n":
                    self.add("other.mk", "OTHER = ordinary\n")
                with self.assertRaisesRegex(MakeProbeError, "generated include source history|literal binding statement"):
                    self.observed_source_census(commands_factory=commands)
                self.assertEqual(self.last_include_observation.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
        commands = self.include_writer("second: input\n", changing=True)
        with self.assertRaisesRegex(MakeProbeError, "conflicting generated output producers"):
            self.observed_source_census(commands_factory=commands)

    def test_native_include_sequence_and_restart_controls_remain_authoritative(self):
        commands = self.include_writer("plain: input\n")
        makefile = (self.root / "Makefile").read_text()
        self.add("Makefile", makefile.replace(
            "FIRST = alpha", "READ := $(origin MAKE_RESTARTS)\nFIRST = alpha",
        ))
        with self.assertRaisesRegex(MakeProbeError, "restart-sensitive"):
            self.observed_source_census(commands_factory=commands)
        self.add("Makefile", "NAME = MAKE_RESTARTS\n" + makefile.replace(
            "FIRST = alpha", "READ := $($(NAME))\nFIRST = alpha",
        ))
        with self.assertRaisesRegex(MakeProbeError, "unproven.*mode"):
            self.observed_source_census(commands_factory=commands)
        self.add("Makefile", makefile)
        with self.session() as session:
            native = session.make("all", variables=("MAKEFILE_LIST", "MAKE_RESTARTS"), commands=commands(session))
            sources = graph_probe._loaded_sources(session, native, primary_source="Makefile")
            with self.assertRaisesRegex(MakeProbeError, "source-read evidence"):
                graph_probe._native_include_context(
                    session, native, {"Makefile": sources["Makefile"]}, (), primary_source="Makefile",
                )
            with self.assertRaisesRegex(MakeProbeError, "restart history"):
                graph_probe._native_include_context(
                    session, native, sources, (("command-line", "MAKE_RESTARTS", "1"),), primary_source="Makefile",
                )
            conflicting = replace(native, semantics={
                **native.semantics,
                "dynamic_commands": [
                    *native.semantics["dynamic_commands"],
                    {"generated_outputs": [["build/include.mk", "100644", "0" * 64]]},
                ],
            })
            with self.assertRaisesRegex(MakeProbeError, "changed or unreceipted"):
                graph_probe._native_include_context(session, conflicting, sources, (), primary_source="Makefile")
            with self.assertRaisesRegex(MakeProbeError, "changed from its original capture"):
                graph_probe._native_include_context(
                    session, native, {**sources, "build/include.mk": b".POSIX:\n"}, (), primary_source="Makefile",
                )
        with self.assertRaisesRegex(MakeProbeError, "traversal differs"):
            graph_probe._source_units(
                {"Makefile": b"INC = child.mk\ninclude $(INC)\n", "child.mk": b"VALUE = literal\n"},
                read_order=("Makefile", "child.mk", "child.mk"),
            )

    def test_selected_primary_and_included_sources_cannot_be_omitted_or_reordered(self):
        self.add("child.mk", "CHILD = ordinary\n")
        self.add("Makefile", "$(error wrong primary selected)\n")
        body = "include child.mk\nFIRST = alpha  \\\n beta\nSECOND = alpha  \\\n beta\n"
        body += "all:\n\t@printf '%s\\n' '$(value FIRST)' '$(value SECOND)'\n"
        self.add("project.mk", body)
        usage, values, _ = self.observed_source_census(makefile="project.mk")
        self.assertEqual(values["FIRST"], {"alpha beta"})
        self.assertNotIn("MAKEFILE_LIST", usage["defined"])
        for line in (
            "MAKEFILE_LIST := child.mk\n",
            "MAKEFILE_LIST := project.mk\n",
            "MAKEFILE_LIST := child.mk project.mk\n",
            "define REWRITE\nMAKEFILE_LIST := project.mk\nendef\n$(eval $(REWRITE))\n",
        ):
            with self.subTest(line=line):
                direct = body.replace("alpha  \\\n beta", "alpha beta")
                self.add("project.mk", direct.replace("FIRST =", line + "FIRST =", 1))
                with self.assertRaisesRegex(MakeProbeError, "source-read evidence|traversal differs"):
                    self.observed_source_census(makefile="project.mk")
        self.add("project.mk", "define UNUSED\nMAKEFILE_LIST := erased\nendef\n" + body)
        self.observed_source_census(makefile="project.mk")
        self.add("data.txt", "ordinary data, not a Make program\n")
        self.add("project.mk", "DATA := $(file <data.txt)\n" + body)
        self.observed_source_census(makefile="project.mk")
        self.assertEqual(self.last_include_observation.semantics["domains"]["MAKEFILE_LIST"]["value"],
                         "project.mk child.mk")
        binary = b"\xff\xfe\n"
        (self.root / "data.bin").write_bytes(binary)
        self.entries["data.bin"] = GitTreeEntry("data.bin", "100644", "blob", hashlib.sha1(binary).hexdigest())
        self.add("project.mk", "DATA := $(file <data.bin)\n" + body)
        self.observed_source_census(makefile="project.mk")
        self.assertIn(("/repo/data.bin", "data.bin"), self.last_include_observation.file_open_attempts)

    def test_template_metadata_queries_keep_the_selected_primary(self):
        self.add("input", "ordinary dependency data\n")
        source = (
            "ITEMS := first second\nINPUT := input\nFIRST := alpha\nSECOND := beta\n"
            "define RULE\n$(1): $(INPUT)\nendef\n"
            "$(foreach item,$(ITEMS),$(eval $(call RULE,$(item))))\n"
            "all: first second\n\t@printf '%s\\n' '$(value FIRST)' '$(value SECOND)'\n"
        )
        for makefile in ("Makefile", "project.mk"):
            with self.subTest(makefile=makefile):
                self.add("Makefile", source if makefile == "Makefile"
                         else "$(error unselected Makefile executed)\n")
                self.add(makefile, source)
                with patch.object(ProbeSession, "make", autospec=True, side_effect=ProbeSession.make) as calls:
                    _, values, _ = self.observed_source_census(makefile=makefile)
                self.assertEqual(values, {"FIRST": {"alpha"}, "SECOND": {"beta"}})
                metadata = [
                    call.kwargs for call in calls.call_args_list
                    if "RULE" in call.kwargs.get("definitions", ())
                ]
                self.assertTrue(metadata)
                self.assertEqual({call.get("makefile", "Makefile") for call in metadata}, {makefile})
                files = {item["target"]: item for item in self.last_include_observation.semantics["files"]}
                for target in ("first", "second"):
                    self.assertEqual(files[target]["prerequisites"], [{"name": "input", "order_only": False}])

    def test_generated_colon_directives_are_not_literal_dependency_rules(self):
        for directive in ("include", "-include", "sinclude"):
            with self.subTest(directive=directive):
                commands = self.include_writer(directive + " mode:\n")
                self.add("mode:", ".POSIX:\n")
                with self.assertRaisesRegex(MakeProbeError, "generated include source history"):
                    self.observed_source_census(commands_factory=commands)
                native = self.last_include_observation
                self.assertEqual(native.semantics["domains"]["MAKEFILE_LIST"]["value"],
                                 "Makefile build/include.mk mode:")
                self.assertEqual(self.last_target_values, ["alpha   beta", "alpha   beta"])
        for data in ("include: input\n", "export: input\n", "ordinary: /repo/input\n", "ordinary: input\n"):
            with self.subTest(data=data):
                commands = self.include_writer(data)
                _, values, _ = self.observed_source_census(commands_factory=commands)
                self.assertEqual(values["FIRST"], {"alpha beta"})

    def test_actual_export_forms_cannot_hide_restart_sensitive_process_inputs(self):
        for declaration in (
            "export MAKE_RESTARTS\n",
            "export MAKEFILE_LIST\n",
            "NAMES = MAKE_RESTARTS\nexport $(NAMES)\n",
            "NAMES = MAKEFILE_LIST\nexport ${NAMES}\n",
            "export\n",
            ".EXPORT_ALL_VARIABLES:\n",
            "$(eval export MAKE_RESTARTS)\n",
        ):
            with self.subTest(declaration=declaration):
                commands = self.include_writer("ordinary: input\n")
                self.add("Makefile", declaration + (self.root / "Makefile").read_text())
                with self.assertRaisesRegex(MakeProbeError, "restart-sensitive|unproven"):
                    self.observed_source_census(commands_factory=commands)
                contexts = [context for context in self.last_include_observation.semantics["native_dispatches"]
                            if any("writer.py" in argument for argument in context["arguments"])]
                self.assertEqual(len(contexts), 2)
                self.assertTrue(any(
                    name in context["environment"] for context in contexts for name in ("MAKE_RESTARTS", "MAKEFILE_LIST")
                ))
                self.assertTrue(any(
                    contexts[0]["environment"].get(name) != contexts[1]["environment"].get(name)
                    for name in ("MAKE_RESTARTS", "MAKEFILE_LIST")
                ))
        for declaration in (
            "SAFE = literal\nexport SAFE\n",
            "SAFE = literal\nNAMES = SAFE\nexport $(NAMES)\n",
            "export SAFE := literal\n",
            "ifeq (yes,no)\nexport MAKE_RESTARTS\nendif\n",
        ):
            with self.subTest(declaration=declaration):
                commands = self.include_writer("ordinary: input\n")
                self.add("Makefile", declaration + (self.root / "Makefile").read_text())
                self.observed_source_census(commands_factory=commands)
                self.assertTrue(all(
                    "MAKEFILE_LIST" not in context["environment"] and "MAKE_RESTARTS" not in context["environment"]
                    for context in self.last_include_observation.semantics["native_dispatches"]
                ))

    def test_native_exports_join_consumption_without_expanding_unexported_bodies(self):
        for prefix, defaults in (
            ("BODY = $(eval MODE ?= first)\nexport BODY\n", {"MODE"}),
            ("$(eval BODY = $$(eval MODE ?= first))\nexport BODY\n", {"MODE"}),
            ("BODY = $(eval MODE ?= first)\n", set()),
        ):
            with self.subTest(prefix=prefix):
                self.add("Makefile", prefix + "FIRST = first\nSECOND = second\n"
                         "all:\n\t@printf '%s\\n' '$(value FIRST)' '$(value SECOND)'\n")
                usage, _, _ = self.observed_source_census()
                self.assertEqual(usage["defaults"], defaults)
                contexts = self.last_include_observation.semantics["native_dispatches"]
                self.assertEqual(any("BODY" in context["environment"] for context in contexts), bool(defaults))

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
        source += continued.replace("FIRST", "SECOND")
        sources, native = self.mode_values(source, expected=["alpha beta", "alpha    beta"])
        usage = source_census(sources, source_target="all")
        self.assertEqual([usage["definitions"][name][0] for name in ("FIRST", "SECOND")], native)
        source = "ifeq ($(filter $(UNPROVEN_PATTERN),$(MAKECMDGOALS)),)\n.POSIX:\nendif\n" + continued
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
        for source, includes, expected, proven in (
            ("SWITCH = yes\nifeq ($(SWITCH),yes)\n.POSIX:\nendif\n" + values, {}, ["alpha beta", "alpha    beta"], True),
            ("$(eval .POSIX:)\n" + values, {}, ["alpha    beta"] * 2, False),
            ("MODE_PREFIX = .PO\nMODE_SUFFIX = SIX\n$(MODE_PREFIX)$(MODE_SUFFIX):\n" + values,
             {}, ["alpha beta", "alpha    beta"], True),
            ("SELECTED = mode.mk\ninclude $(SELECTED)\n" + values,
             {"mode.mk": ".POSIX:\n"}, ["alpha    beta"] * 2, True),
        ):
            with self.subTest(source=source):
                sources, native = self.mode_values(source, includes=includes, expected=expected)
                if proven:
                    actual = source_census(sources)
                    self.assertEqual([actual["definitions"][name][0] for name in ("FIRST", "SECOND")], native)
                else:
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
        self.framework_templates()
        projection = lambda files: [
            {key: item[key] for key in ("target", "recipe", "prerequisites")} for item in files
        ]
        baseline = self.observe_framework()["record"]["variants"][0]["record"]
        self.ordinary()
        generated = self.root / "build/generated/data/data_alpha.c"
        object_file = self.root / "build/modern/src/data_alpha.o"
        expected_c = generated.read_bytes()
        expected_object = object_file.read_bytes()
        self.assertEqual(expected_object[:4], b"\x7fELF")
        self.assertEqual(int.from_bytes(expected_object[18:20], "little"), 40)
        self.add("Makefile", (self.root / "Makefile").read_text().replace(
            "include generated_data.mk", "ifeq (yes,yes)\ninclude generated_data.mk\nendif",
        ))
        active = self.observe_framework()["record"]["variants"][0]["record"]
        with self.session() as session:
            native = session.make("all", variables=("MAKEFILE_LIST",))
        self.assertEqual(projection(active["files"]), projection(baseline["files"]))
        self.assertEqual(projection(active["files"]), projection(native.semantics["files"]))
        self.assertEqual(native.semantics["domains"]["MAKEFILE_LIST"]["value"].split(),
                         ["Makefile", "generated_data.mk", "modern.mk"])
        self.ordinary("-B")
        self.assertEqual(generated.read_bytes(), expected_c)
        self.assertEqual(object_file.read_bytes(), expected_object)
        self.add("Makefile", (self.root / "Makefile").read_text().replace("ifeq (yes,yes)", "ifeq (yes,no)"))
        with self.assertRaisesRegex(MakeProbeError, "No rule to make target"):
            self.observe_framework()
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
            ("effectful-include", "Makefile", "include generated_data.mk",
             "ifeq ($(eval TEMPLATE_CONTEXT := touched),)\ninclude generated_data.mk\nendif"),
        )
        for case, path, old, new in cases:
            with self.subTest(case=case):
                self.framework_templates()
                self.add(path, (self.root / path).read_text().replace(old, new))
                if case == "effectful-include":
                    with self.session() as session:
                        native = session.make("all", variables=("MAKEFILE_LIST",), definitions=("TEMPLATE_CONTEXT",))
                    self.assertEqual(native.semantics["definitions"]["global"]["TEMPLATE_CONTEXT"],
                                     {"origin": "file", "flavor": "simple", "value": "touched"})
                    self.assertEqual(projection(native.semantics["files"]), projection(baseline["files"]))
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
        self.original_input_witness()
        self.add("first.mk", "SELECTED = first\n")
        self.add("second.mk", "SELECTED = second\n")
        for declarations, reference, later in (
            ("NAME = FLAGS\n", "$($(NAME))", ""),
            ("NAME = FLAGS\n", "${${NAME}}", ""),
            ("POINTER = FLAGS\nNAME = $(POINTER)\n", "$($(NAME))", ""),
            ("NAME = FLAGS\nPREFIX_FLAGS = $(FLAGS)\n", "$(PREFIX_$(NAME))", ""),
            ("POINTER = FLAGS\nNAME := $(POINTER)\nPOINTER = OTHER\nOTHER = first\n", "$($(NAME))", ""),
            ("NAME = FLAGS\nOTHER = first\n", "$($(NAME))", "NAME = OTHER\n"),
        ):
            with self.subTest(declarations=declarations, reference=reference, later=later):
                self.add("Makefile", "FLAGS ?= first\n" + declarations + "include " + reference + ".mk\n"
                         "all: $(SELECTED)\n\t@echo $(FLAGS)\nfirst second: ;\n" + later)
                self.assertEqual(self.ordinary("FLAGS=first"), b"first\n")
                self.assertEqual(self.ordinary("FLAGS=second"), b"second\n")
                with self.session() as session:
                    original = session.original_make_inputs("all", ("FLAGS",))
                    native = session.make(
                        "all", assignments=(("command-line", "FLAGS", "second"),),
                        variables=("MAKEFILE_LIST",), definitions=("NAME", "SELECTED"),
                    )
                self.assertEqual(original["FLAGS"], {"origin": "undefined", "flavor": "undefined", "value": ""})
                self.assertEqual(native.semantics["domains"]["MAKEFILE_LIST"]["value"].split(),
                                 ["Makefile", "second.mk"])
                self.assertEqual(native.semantics["files"][0]["prerequisites"][0]["name"], "second")
                if later:
                    self.assertEqual(native.semantics["definitions"]["global"]["NAME"]["value"], "OTHER")
                self.assertFalse(session.budget.children)
                with self.assertRaisesRegex(MakeProbeError, "symbolic inputs influence the Make graph"):
                    self.observe(external={"FLAGS"}, symbolic_recipe_names={"FLAGS"})
                streams = []
                source_units = graph_probe._source_units

                def retained_reads(*args, **kwargs):
                    result = source_units(*args, **kwargs)
                    streams.append(result)
                    return result

                with patch.object(graph_probe, "_source_units", retained_reads):
                    records = self.observe({"FLAGS": {"kind": "explicit", "values": ["first", "second"]}})["all"]
                self.assertTrue({"NAME", "FLAGS"} <= {
                    name for stream in streams for _, _, unit in stream.ordered for name in unit.reads
                })
                self.assertEqual(records["record"]["includes"], ["Makefile", "first.mk", "second.mk"])
                self.assertEqual(records["prerequisite_domain_census"]["enumerated"], ["FLAGS"])
                self.assertEqual({
                    variant["record"]["files"][0]["prerequisites"][0]["name"]
                    for variant in records["record"]["variants"]
                }, {"first", "second"})

    def test_opaque_computed_selector_requires_its_declared_native_fallback(self):
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
        self.assertEqual({
            variant["record"]["files"][0]["prerequisites"][0]["name"]
            for variant in observed["record"]["variants"]
        }, {"first", "second"})

    def test_original_computed_include_proofs_have_independent_restoration_controls(self):
        self.original_input_witness()
        self.add("first.mk", "SELECTED = first\n")
        self.add("Makefile", "FLAGS ?= first\nNAME = FLAGS\ninclude $($(NAME)).mk\nall: ;\n")
        with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
            self.observe()
        domains = {"FLAGS": {"kind": "explicit", "values": ["first"]}}
        effectful = _MakeSourceMode.effectful
        literal_values = _MakeSourceMode.literal_values

        def old_effect_limit(mode, expression):
            if next(graph_probe.computed_selectors(graph_probe._without_literal_metadata(expression)), None) is not None:
                mode.last_effect_input = "computed-selector"
                return True
            return effectful(mode, expression)

        def old_literal_limit(mode, expression, active=()):
            if next(graph_probe.computed_selectors(expression), None) is not None:
                return None
            return literal_values(mode, expression, active)

        for method, old in (("effectful", old_effect_limit), ("literal_values", old_literal_limit)):
            with self.subTest(method=method):
                self.assertEqual(self.observe(domains)["all"]["record"]["includes"], ["Makefile", "first.mk"])
                with patch.object(_MakeSourceMode, method, old):
                    with self.assertRaisesRegex(MakeProbeError, "unproven original include outcome"):
                        self.observe(domains)
                self.assertEqual(self.observe(domains)["all"]["record"]["includes"], ["Makefile", "first.mk"])
        witness = "scripts/generated_data/chapterbundle/__init__.py"
        (self.root / witness).unlink()
        del self.entries[witness]
        with self.assertRaisesRegex(MakeProbeError, "unproven original include outcome"):
            self.observe(domains)

    def test_original_computed_references_keep_unknown_and_metadata_boundaries(self):
        self.original_input_witness()
        self.add("first.mk", "SELECTED = first\n")
        for declaration in (
            "NAME = $(subst X,FLAGS,X)\n",
            "NAME = $(UNRESOLVED)\n",
            "NAME = $(NAME)\n",
            "NAME = FLAGS\nFLAGS = $(eval SIDE_EFFECT := first)first\n",
        ):
            with self.subTest(declaration=declaration):
                self.add("Makefile", "FLAGS = first\n" + declaration + "include $($(NAME)).mk\nall: ;\n")
                with self.assertRaises(MakeProbeError):
                    self.observe({"NAME": {"kind": "tracked-fallback"}})
        self.add("Makefile", "FLAGS = first\nNAME = .VARIABLES\nall: ;\n"
                 "READ := $(filter FLAGS,$($(NAME)))\n")
        with self.assertRaisesRegex(MakeProbeError, "computed selector"):
            self.observe()
        self.add("file.mk", "SELECTED = first\n")
        self.add("Makefile", "UNREAD = $(error unused)$(shell touch marker)\n"
                 "ALIAS = $(origin UNREAD)\nNAME = ALIAS\nREAD := $($(NAME))\n"
                 "include $(READ).mk\nall:\n\t@echo $(READ)\n")
        self.assertEqual(self.ordinary(), b"file\n")
        with self.session() as session:
            native = session.make("all", definitions=("UNREAD", "READ"))
        self.assertEqual(native.semantics["definitions"]["global"]["UNREAD"]["value"],
                         "$(error unused)$(shell touch marker)")
        self.assertEqual(native.semantics["definitions"]["global"]["READ"]["value"], "file")
        self.assertEqual(self.observe()["all"]["record"]["includes"], ["Makefile", "file.mk"])
        self.assertFalse((self.root / "marker").exists())

    def test_original_computed_reference_context_and_deadline_bounds_are_unchanged(self):
        budget = ProbeBudget()
        mode = _MakeSourceMode(budget=budget)
        try:
            for index in range(513):
                mode.assign("VALUE_" + str(index), "=", "same.mk")
            mode.assign("NAME", "=", "VALUE_0")
            for index in range(1, 512):
                mode.assign("NAME", "=", "VALUE_" + str(index), active=None)
            self.assertEqual(mode.literal_values("$($(NAME))"), frozenset(("same.mk",)))
            self.assertFalse(mode.effectful("$($(NAME))"))
            self.assertTrue({"NAME", "VALUE_0", "VALUE_511"} <= mode.reads)
            mode.assign("NAME", "=", "VALUE_512", active=None)
            with self.assertRaisesRegex(MakeProbeError, "existing bounded context plan"):
                mode.literal_values("$($(NAME))")
            mode.assign("NAME", "=", "$(NAME)")
            self.assertIsNone(mode.reference_names("$(NAME)"))
            self.assertTrue(mode.effectful("$($(NAME))"))
            mode.assign("CHAIN_0", "=", "VALUE_0")
            for index in range(1, 1100):
                mode.assign("CHAIN_" + str(index), "=", "$(CHAIN_" + str(index - 1) + ")")
            self.assertIsNone(mode.reference_names("$(CHAIN_1099)"))
        finally:
            budget.close()
        with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline"):
            mode.literal_values("$($(NAME))")

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
        self.original_input_witness()
        declarations = self.original_target_slices(
            "modern.mk", ("MODERN_TOOLCHAIN_ROOT ?=",),
            "# Resolve modern OBJCOPY consistently", "export MODERN_TOOLCHAIN_ROOT",
        )
        consumers = [
            chunk.text for chunk in graph_probe._make_logical_chunks((ROOT / "modern.mk").read_text())
            if chunk.text.startswith("\t") and name in graph_probe.references(chunk.text)
        ]
        self.assertEqual(len(consumers), 1)
        self.add("modern-size.mk", declarations)
        for package in ("scripts", "scripts/workflow_pilot", "scripts/workflow_pilot/tests"):
            self.add(package + "/__init__.py", "# bounded environment-consumer fixture\n")
        self.add("scripts/workflow_pilot/tests/arm_review_subjects.py", (
            "import json,os,unittest\n"
            "class EnvironmentConsumer(unittest.TestCase):\n"
            " def test_size(self):\n"
            "  print(json.dumps({'MODERN_SIZE':os.environ['MODERN_SIZE']}))\n"
        ))
        contract = json.loads((ROOT / ".github/validation-ownership-make-dynamics.json").read_text())
        self.assertEqual(contract["seal"], reporter._sha256(
            reporter.MAKE_DYNAMIC_SEAL_DOMAIN, reporter.canonical_make_dynamic_payload(contract),
        ))
        options = {
            "external": contract["ambient_inputs"]["allowed_names"],
            "symbolic_recipe_names": contract["prerequisite_domains"]["symbolic_recipe_names"],
        }
        records = []
        for root, value in (("", "arm-none-eabi-size"), ("/selected", "/selected/bin/arm-none-eabi-size")):
            self.add("Makefile", "PREFIX := arm-none-eabi-\nEXE :=\n"
                     "MODERN_TOOLCHAIN_ROOT := " + root + "\nMODERN_CC := arm-none-eabi-gcc\n"
                     "PYTHON := /usr/bin/python3\ninclude modern-size.mk\nall:\n" + consumers[0] + "\n")
            ordinary = self.ordinary()
            self.assertEqual(json.loads(ordinary.decode().splitlines()[-1]), {name: value})
            with self.session() as session:
                commands = graph_probe.MakeCommands(session, {})
                original = session.original_make_inputs("all", (name,))
                native = session.make(
                    "all", variables=("MAKEFILE_LIST", "MAKE_RESTARTS", name), commands=commands,
                )
                sources = graph_probe._loaded_sources(session, native, primary_source="Makefile")
                units, inputs, scoped = graph_probe._prepare_rule_templates(
                    session, "all", (), commands, native, sources, primary_source="Makefile",
                )
                usage = source_census(
                    sources, reference_units=units, template_graph_inputs=inputs, template_scoped=scoped,
                    budget=session.budget, source_target="all",
                )
            self.assertEqual(original[name], {"origin": "undefined", "flavor": "undefined", "value": ""})
            self.assertEqual(native.semantics["domains"][name],
                             {"origin": "file", "flavor": "recursive", "value": value})
            self.assertIn(name, usage["defaults"])
            self.assertIn(name, usage["recipe_only"])
            record = self.observe(**options)["all"]["record"]
            self.assertEqual(record["symbolic_recipe_names"], ["MODERN_CC", "MODERN_NM", name])
            self.assertEqual(record["variants"][0]["record"]["domains"][name]["value"], value)
            records.append(record)
        self.assertNotEqual(records[0], records[1])
        override = "/external/bin/size"
        self.assertEqual(json.loads(self.ordinary(environment={name: override}).decode().splitlines()[-1]),
                         {name: override})
        unconditional = []
        for chunk in graph_probe._make_logical_chunks(declarations):
            assignment = graph_probe.ASSIGNMENT.fullmatch(chunk.text)
            if assignment and assignment["name"] == name:
                self.assertEqual(assignment["operator"], "?=")
                unconditional.append(chunk.text[:assignment.start("operator")] + "="
                                     + chunk.text[assignment.end("operator"):] + "\n")
            else:
                unconditional.append(chunk.text + "\n")
        self.add("modern-size.mk", "".join(unconditional))
        self.assertEqual(json.loads(self.ordinary(environment={name: override}).decode().splitlines()[-1]),
                         {name: value})
        self.add("modern-size.mk", declarations)
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
        self.original_input_witness()
        self.add("Makefile", "MODE ?= one\nifeq ($(MODE),two)\ninclude branch.mk\nendif\nall: ;\n")
        self.add("branch.mk", "BRANCH ?= selected\nall: $(BRANCH)\nselected: ;\n")
        with self.session() as session:
            original = session.original_make_inputs("all", ("MODE", "BRANCH"))
            for value in ("one", "two"):
                self.ordinary("MODE=" + value)
                native = session.make(
                    "all", assignments=(("command-line", "MODE", value),),
                    variables=("MAKEFILE_LIST",), definitions=("BRANCH",),
                )
                self.assertEqual(native.semantics["domains"]["MAKEFILE_LIST"]["value"].split(),
                                 ["Makefile"] + (["branch.mk"] if value == "two" else []))
                self.assertEqual(native.semantics["files"][0]["prerequisites"],
                                 [{"name": "selected", "order_only": False}] if value == "two" else [])
                self.assertEqual(native.semantics["definitions"]["global"]["BRANCH"], {
                    "origin": "file" if value == "two" else "undefined",
                    "flavor": "recursive" if value == "two" else "undefined",
                    "value": "selected" if value == "two" else "",
                })
        self.assertEqual(original, {
            name: {"origin": "undefined", "flavor": "undefined", "value": ""} for name in ("MODE", "BRANCH")
        })
        domains = {
            "MODE": {"kind": "explicit", "values": ["one", "two"]},
            "BRANCH": {"kind": "tracked-fallback"},
        }
        result = self.observe(domains)
        self.assertEqual(result["all"]["prerequisite_domain_census"]["used"], ["BRANCH", "MODE"])
        self.assertEqual(result["all"]["prerequisite_domain_census"]["enumerated"], ["BRANCH", "MODE"])
        self.assertEqual(result["all"]["record"]["includes"], ["Makefile", "branch.mk"])
        branch_states = []
        for variant in result["all"]["record"]["variants"]:
            state = {name: value for _, name, value in variant["state"]}
            self.assertEqual(variant["record"]["files"][0]["prerequisites"],
                             [{"name": "selected", "order_only": False}] if state.get("MODE") == "two" else [])
            if "BRANCH" in state:
                branch_states.append(state)
        self.assertEqual(branch_states, [{"MODE": "two", "BRANCH": "selected"}])
        with patch.object(_MakeSourceMode, "literal_comparison", return_value=None):
            with self.assertRaisesRegex(MakeProbeError, "unproven original include outcome"):
                self.observe(domains)
            mode = _MakeSourceMode()
            mode.bind_invocation("all")
            self.assertTrue(mode.condition("ifeq", "($(MAKECMDGOALS),all)"))
        self.assertEqual(self.observe(domains), result)
        witness = "scripts/generated_data/chapterbundle/__init__.py"
        (self.root / witness).unlink()
        del self.entries[witness]
        with self.assertRaisesRegex(MakeProbeError, "unproven original include outcome"):
            self.observe(domains)

    def test_original_file_conditions_preserve_native_operands_and_history(self):
        self.original_input_witness()
        self.add("yes.mk", "SELECTED = first\n")
        self.add("no.mk", "SELECTED = second\n")
        alternatives = (
            "MODE = one\nCHOICE = present\nifdef CHOICE\nMODE = one\nelse\nMODE = two\nendif\n"
        )
        for prefix, condition, later, expected in (
            ("MODE = one\n", "ifeq ($(MODE),one)", "", "first"),
            ("MODE = one\n", "ifneq (${MODE},one)", "", "second"),
            ("MODE = a b\n", "ifeq '$(MODE)' \"a b\"", "", "first"),
            ("MODE = a b\n", "ifneq \"${MODE}\" 'other value'", "", "first"),
            ("VALUE = one\nMODE = $(VALUE)\n", "ifeq ($(MODE),one)", "", "first"),
            ("NAME = MODE\nMODE = one\n", "ifeq ($($(NAME)),one)", "", "first"),
            ("VALUE = one\nMODE := $(VALUE)\nVALUE = two\n", "ifeq ($(MODE),one)", "", "first"),
            ("VALUE = one\nMODE = $(VALUE)\nVALUE = two\n", "ifeq ($(MODE),one)", "", "second"),
            ("MODE = one\n", "ifeq ($(MODE),one)", "MODE = two\n", "first"),
            ("MODE = all\n", "ifeq ($(strip $(MAKECMDGOALS)),$(MODE))", "", "first"),
            ("UNREAD = $(error unused)$(shell touch marker)\nMODE = $(origin UNREAD)\n",
             "ifeq ($(MODE),file)", "", "first"),
            (alternatives, "ifneq ($(MODE),missing)", "", "first"),
            (alternatives, "ifeq ($(MODE),missing)", "", "second"),
        ):
            with self.subTest(prefix=prefix, condition=condition, later=later):
                self.add("Makefile", prefix + condition + "\ninclude yes.mk\nelse\ninclude no.mk\nendif\n"
                         + later + "all: $(SELECTED)\n\t@echo $(SELECTED)\nfirst second: ;\n")
                self.assertEqual(self.ordinary(), (expected + "\n").encode())
                with self.session() as session:
                    native = session.make("all", variables=("MAKEFILE_LIST",), definitions=("MODE",))
                result = self.observe(trusted_builtin_names={"MAKECMDGOALS"})["all"]["record"]
                selected = "yes.mk" if expected == "first" else "no.mk"
                self.assertEqual(native.semantics["domains"]["MAKEFILE_LIST"]["value"].split(),
                                 ["Makefile", selected])
                self.assertEqual(result["includes"], ["Makefile", selected])
                self.assertEqual(result["variants"][0]["record"]["files"][0]["prerequisites"],
                                 native.semantics["files"][0]["prerequisites"])
                self.assertEqual(native.semantics["files"][0]["prerequisites"][0]["name"], expected)
                if later:
                    self.assertEqual(native.semantics["definitions"]["global"]["MODE"]["value"], "two")
                self.assertFalse((self.root / "marker").exists())

    def test_original_file_conditions_keep_ambiguous_and_opaque_reads_unproven(self):
        self.original_input_witness()
        self.add("yes.mk", "SELECTED = first\n")
        self.add("no.mk", "SELECTED = second\n")
        for prefix, condition, expected in (
            ("MODE = one\nCHOICE = present\nifdef CHOICE\nMODE = one\nelse\nMODE = two\nendif\n",
             "ifeq ($(MODE),one)", "first"),
            ("MODE = $(subst X,one,X)\n", "ifeq ($(MODE),one)", "first"),
            ("MODE = $(eval EFFECT := changed)one\n", "ifeq ($(MODE),one)", "first"),
        ):
            with self.subTest(prefix=prefix):
                self.add("Makefile", prefix + condition + "\ninclude yes.mk\nelse\ninclude no.mk\nendif\n"
                         "all: $(SELECTED)\n\t@echo $(SELECTED)\nfirst second: ;\n")
                self.assertEqual(self.ordinary(), (expected + "\n").encode())
                with self.session() as session:
                    native = session.make("all", variables=("MAKEFILE_LIST",), definitions=("MODE", "EFFECT"))
                self.assertEqual(native.semantics["domains"]["MAKEFILE_LIST"]["value"].split(), ["Makefile", "yes.mk"])
                with self.assertRaisesRegex(MakeProbeError, "unproven original include outcome"):
                    self.observe()
        self.add("Makefile", "MODE ?= one\nifeq ($(MODE),one)\ninclude yes.mk\nendif\nall: ;\n")
        with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
            self.observe()
        with self.assertRaisesRegex(MakeProbeError, "symbolic inputs influence"):
            self.observe(external={"MODE"}, symbolic_recipe_names={"MODE"})
        self.add("Makefile", "ifeq ($(UNSEALED),)\ninclude yes.mk\nendif\nall: ;\n")
        with self.assertRaisesRegex(MakeProbeError, "unsealed undefined"):
            self.observe()

    def test_ineligible_original_conditions_do_not_read_operands(self):
        self.original_input_witness()
        name = "UNREACHED_" + "X" * 120
        for keyword in ("ifeq", "ifneq"):
            for kind, outer, nested, ending, active_outer in (
                ("nested", "ifeq (yes,no)", "", "\nendif", "ifeq (yes,yes)"),
                ("else-if", "ifeq (yes,yes)", "else ", "", "ifeq (yes,no)"),
            ):
                with self.subTest(keyword=keyword, kind=kind):
                    source = outer + "\n" + nested + keyword + " ($(" + name + "),yes)\nendif" + ending + "\nall: ;\n"
                    self.add("Makefile", source)
                    self.ordinary()
                    with self.session() as session:
                        native = session.make("all", variables=("MAKEFILE_LIST",))
                    result = self.observe()["all"]
                    self.assertEqual(native.semantics["domains"]["MAKEFILE_LIST"]["value"].split(), ["Makefile"])
                    self.assertEqual(result["record"]["includes"], ["Makefile"])
                    self.assertEqual(result["prerequisite_domain_census"]["used"], [])
                    self.assertEqual(result["record"]["variants"][0]["record"]["files"][0]["prerequisites"],
                                     native.semantics["files"][0]["prerequisites"])
                    self.add("Makefile", source.replace(outer, active_outer, 1))
                    self.ordinary()
                    with self.assertRaisesRegex(MakeProbeError, "invalid original Make input name"):
                        self.observe()

    def test_original_file_condition_comparisons_keep_control_and_resource_bounds(self):
        budget = ProbeBudget()
        mode = _MakeSourceMode(budget=budget)
        try:
            mode.assign("LEFT", "=", "left_0")
            for index in range(1, 512):
                mode.assign("LEFT", "=", "left_" + str(index), active=None)
            self.assertTrue(mode.condition("ifneq", "($(LEFT),right)"))
            self.assertFalse(mode.condition("ifeq", "($(LEFT),right)"))
            self.assertIsNone(mode.condition("ifeq", "($(LEFT),left_0)"))
            self.assertIn("LEFT", mode.reads)
            mode.assign("LEFT", "=", "left_512", active=None)
            with self.assertRaisesRegex(MakeProbeError, "existing bounded context plan"):
                mode.condition("ifeq", "($(LEFT),right)")
            mode.assign("LEFT", "=", "$(LEFT)")
            self.assertIsNone(mode.condition("ifeq", "($(LEFT),right)"))
            mode.assign("NAME", "=", ".VARIABLES")
            self.assertIsNone(mode.condition("ifeq", "($($(NAME)),right)"))
            for name in ("MAKEFILE_LIST", "MAKE_RESTARTS", "MAKECMDGOALS"):
                mode.assign(name, "=", "forged")
                mode.reads.clear()
                self.assertIsNone(mode.condition("ifeq", "($(" + name + "),forged)"))
        finally:
            budget.close()
        with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline"):
            mode.condition("ifeq", "($(LEFT),right)")

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
            job_frames = [value for value in native["accessed"] if value.startswith("make-job-context:")]
            self.assertEqual(len(job_frames), 1)
            job = json.loads(job_frames[0].removeprefix("make-job-context:"))
            self.assertEqual(job, {
                "sequence": dispatch["sequence"], "kind": "recipe", "target": "all", "command_line": 1,
            })
            dispatch["job"] = job
            dispatch["ignore_errors"] = dispatch.pop("global_ignore_errors")
            self.assertEqual(dispatch, result.semantics["native_dispatches"][0])
            self.assertEqual(session.observations_used - before[0], native["observations"])
            self.assertGreaterEqual(
                native["observation_bytes"], len(frames[0].encode()) + len(job_frames[0].encode()) + 128,
            )
            self.assertGreaterEqual(
                session.budget.bytes["control"] - before[1],
                native["observation_bytes"] + len(encoded(result.semantics)),
            )
            self.last_export_evidence = {
                "runs": session.budget.runs, "states": session.budget.states,
                "observations": native["observations"], "observation_bytes": native["observation_bytes"],
                "control_delta": session.budget.bytes["control"] - before[1],
                "native_dispatch_frame_bytes": len(frames[0].encode()),
                "native_job_frame_bytes": len(job_frames[0].encode()),
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

    def framework_template_continuation(self, *, renamed=False, posix=False, flattened=False):
        self.framework_templates(renamed=renamed)
        self.original_input_witness()
        macro = "GENERATED_DATA_LINK_TABLE_RULES"
        name = "GENERATED_DATA_CONFIG_INPUTS_units"
        chunks = list(graph_probe._make_logical_chunks((ROOT / "generated_data.mk").read_text()))
        caller = next(chunk for chunk in chunks if chunk.text.startswith("$(foreach ") and macro in chunk.text)
        continuation = next(chunk for chunk in chunks if chunk.text.startswith(name + " :="))
        source = self.original_target_slices("generated_data.mk", (), "define " + macro, caller.text)
        for chunk in chunks:
            if caller.end < chunk.start < continuation.start:
                source += "\n" * (chunk.end - chunk.start + 1)
        tail = continuation.text
        if flattened:
            tail = graph_probe._collapse_make_continuations(tail, posix=False)
        source += tail + "\n"
        if renamed:
            source = source.replace(macro, "PROJECT_" + macro)
        self.add("generated_data.mk", source)
        parent = (self.root / "Makefile").read_text()
        if posix:
            parent = ".POSIX:\n" + parent
        self.add("Makefile", parent + "measure:\n\t@printf '%s\\n' '$(value " + name + ")'\n")
        expected = graph_probe.ASSIGNMENT.fullmatch(
            graph_probe._collapse_make_continuations(continuation.text, posix=posix),
        )["value"].lstrip(graph_probe.MAKE_SPACE)
        return name, expected

    def test_template_mode_proof_precedes_actual_dependent_continuation(self):
        for renamed, posix, flattened in ((False, False, False), (True, False, False),
                                          (False, True, False), (False, False, True)):
            with self.subTest(renamed=renamed, posix=posix, flattened=flattened):
                name, expected = self.framework_template_continuation(
                    renamed=renamed, posix=posix, flattened=flattened,
                )
                self.assertEqual(self.ordinary(target="measure"), (expected + "\n").encode())
                with self.session() as session:
                    native = session.make("all", variables=("MAKEFILE_LIST",), definitions=(name,))
                self.assertEqual(native.semantics["definitions"]["global"][name],
                                 {"origin": "file", "flavor": "simple", "value": expected})
                self.assertEqual(native.semantics["domains"]["MAKEFILE_LIST"]["value"].split(),
                                 ["Makefile", "generated_data.mk", "modern.mk"])
                actual = self.observe_framework()["record"]["variants"][0]["record"]
                projection = lambda files: [
                    {key: item[key] for key in ("target", "recipe", "prerequisites")} for item in files
                ]
                self.assertEqual(projection(actual["files"]), projection(native.semantics["files"]))
                if not posix and not flattened:
                    with patch.object(graph_probe._TemplateModeProof, "__call__", return_value=False):
                        with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
                            self.observe_framework()
                    self.assertEqual(self.observe_framework()["record"]["variants"][0]["record"], actual)

    def test_template_mode_proof_rejects_original_effects_and_concealed_writes(self):
        name, _ = self.framework_template_continuation()
        parent = (self.root / "Makefile").read_text()
        key = "GENERATED_DATA_CONFIG_INPUTS_alpha"
        staged = key + " := $$(eval .POSIX:)$$(eval " + key + " := include/alpha.h)include/alpha.h"
        self.add("Makefile", parent.replace(key + " := include/alpha.h", staged))
        with self.session() as session:
            native = session.make("all", definitions=(key, name))
        self.assertEqual(native.semantics["definitions"]["global"][key]["value"], "include/alpha.h")
        self.assertIn("  ", native.semantics["definitions"]["global"][name]["value"])
        with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
            self.observe_framework()

        self.framework_templates()
        self.add("include/later.h", "/* captured late dependency */\n")
        parent = (self.root / "Makefile").read_text().replace(key + " := include/alpha.h", staged)
        parent += "LATE = first \\\n second\nifeq ($(LATE),first  second)\n" + key + " := include/later.h\nendif\n"
        self.add("Makefile", parent)
        with self.session() as session:
            native = session.make("all", definitions=(key, "LATE"))
        self.assertEqual(native.semantics["definitions"]["global"][key]["value"], "include/later.h")
        self.assertEqual(native.semantics["definitions"]["global"]["LATE"]["value"], "first  second")
        with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
            self.observe_framework()

        for suffix in ("GENERATED_DATA_LINKED_TABLES := alpha\n",
                       "GENERATED_DATA_LINK_TABLE_RULES = ignored\n"):
            with self.subTest(suffix=suffix):
                self.framework_template_continuation()
                self.add("Makefile", (self.root / "Makefile").read_text() + suffix)
                with self.assertRaises(MakeProbeError):
                    self.observe_framework()
        self.framework_template_continuation()
        self.add("Makefile", "$(eval EARLIER_EFFECT := one)\n" + (self.root / "Makefile").read_text())
        with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
            self.observe_framework()
        self.framework_template_continuation()
        with patch.object(graph_probe._TemplateModeProof, "native_value", return_value={
            "origin": "file", "flavor": "simple", "value": "beta",
        }):
            with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
                self.observe_framework()
        name, _ = self.framework_template_continuation()
        self.add("Makefile", (self.root / "Makefile").read_text().replace(
            "src/data_alpha.c src/data_beta.c", "src/data_POSIX.c",
        ).replace("all: $(MODERN_OUTPUT_DIR)/src/data_$(SELECTED_TABLE).o", "all: ;"))
        self.add("generated_data.mk", (self.root / "generated_data.mk").read_text().replace(
            "$(GENERATED_DATA_OUT_DIR)/data_$(1).c:", ".$(1):",
        ))
        with self.session() as session:
            native = session.make("all", definitions=(name,))
        self.assertIn("  ", native.semantics["definitions"]["global"][name]["value"])
        with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
            self.observe_framework()

    def test_template_mode_inputs_keep_snapshot_and_occurrence_versions(self):
        self.framework_template_continuation()
        parent = (self.root / "Makefile").read_text()
        self.add("Makefile", "UNREAD = $(error unused)$(shell touch marker)\nMETA := $(origin UNREAD)\n" + parent.replace(
            "include generated_data.mk",
            "GENERATED_DATA_LINKED_HAND_SOURCES := src/data_beta.c\ninclude generated_data.mk",
        ))
        actual = self.observe_framework()
        self.assertIn("build/generated/data/data_alpha.c", {
            dependency["name"] for item in actual["record"]["variants"][0]["record"]["files"]
            for dependency in item["prerequisites"]
        })
        self.assertFalse((self.root / "marker").exists())
        self.framework_template_continuation()
        self.add("Makefile", (self.root / "Makefile").read_text().replace(
            "include generated_data.mk", "TABLE_SNAPSHOT := $(GENERATED_DATA_LINKED_TABLES)\ninclude generated_data.mk",
        ))
        self.add("generated_data.mk", (self.root / "generated_data.mk").read_text().replace(
            "$(foreach t,$(GENERATED_DATA_LINKED_TABLES),", "$(foreach t,$(TABLE_SNAPSHOT),",
        ))
        self.assertEqual(self.observe_framework()["record"]["includes"],
                         ["Makefile", "generated_data.mk", "modern.mk"])
        self.framework_template_continuation()
        self.add("Makefile", (self.root / "Makefile").read_text().replace(
            "include generated_data.mk", "include generated_data.mk\ninclude generated_data.mk",
        ))
        with self.assertRaises(MakeProbeError):
            self.observe_framework()
        self.framework_template_continuation()
        source = (self.root / "generated_data.mk").read_text()
        caller = next(chunk.text for chunk in graph_probe._make_logical_chunks(source)
                      if chunk.text.startswith("$(foreach "))
        self.add("generated_data.mk", source.replace(caller, caller + "\nGENERATED_DATA_LINKED_TABLES := alpha\n" + caller))
        with self.assertRaises(MakeProbeError):
            self.observe_framework()

    def test_template_call_payload_padding_cannot_hide_parser_effects(self):
        self.original_input_witness()
        for argument, outer, posix, accepted in (
            ("$(t)", "", False, True),
            ("$(t)", "  ", False, True),
            ("$(t) ", "", True, False),
            ("$(t)\t", "", True, False),
            (" $(t)", "", False, False),
        ):
            with self.subTest(argument=argument, outer=outer):
                self.add("Makefile", "define RULE\nout/$(1).POSIX:\nendef\n"
                         + outer + "$(foreach t,alpha,$(eval $(call RULE," + argument + ")))" + outer + "\n"
                         "LATE = first \\\n second\nifeq ($(LATE),first  second)\nHIDDEN ?= secret\nendif\nall: ;\n")
                self.ordinary()
                with self.session() as session:
                    native = session.make("all", definitions=("LATE", "HIDDEN"))
                records = native.semantics["definitions"]["global"]
                self.assertEqual(records["LATE"]["value"], "first  second" if posix else "first second")
                self.assertEqual(records["HIDDEN"]["origin"], "file" if posix else "undefined")
                if posix:
                    self.assertEqual(records["HIDDEN"]["value"], "secret")
                if accepted:
                    self.assertEqual(self.observe(scoped_variable_names={"1", "t"})["all"]["variable_census"]["defaults"], [])
                else:
                    with self.assertRaisesRegex(MakeProbeError, "rule-template invocation"):
                        self.observe(scoped_variable_names={"1", "t"})

    def test_template_empty_pattern_claim_cannot_hide_mode_and_defaults(self):
        self.original_input_witness()
        for initializer in ("$(patsubst ,POSIX,)", "POSIX"):
            with self.subTest(initializer=initializer):
                self.add("Makefile", "TABLES := " + initializer + "\ndefine RULE\n.$(1):\nendef\n"
                         "$(foreach t,$(TABLES),$(eval $(call RULE,$(t))))\n"
                         "LATE = first \\\n second\nifeq ($(LATE),first  second)\n"
                         "TABLES :=\nHIDDEN ?= secret\nendif\nall: ;\n")
                self.ordinary()
                with self.session() as session:
                    native = session.make("all", definitions=("LATE", "TABLES", "HIDDEN"))
                records = native.semantics["definitions"]["global"]
                self.assertEqual(records["LATE"]["value"], "first  second")
                self.assertEqual(records["TABLES"]["value"], "")
                self.assertEqual(records["HIDDEN"], {"origin": "file", "flavor": "recursive", "value": "secret"})
                with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
                    self.observe(scoped_variable_names={"1", "t"})
        budget = ProbeBudget()
        try:
            mode = _MakeSourceMode(budget=budget, template_mode=lambda *args: False)
            self.assertIsNone(mode.template_initializer("$(patsubst ,POSIX,)"))
            for words in ("", " ", "word", "word "):
                with self.subTest(words=words):
                    self.add("Makefile", "VALUE := $(patsubst ,POSIX," + words + ")\n"
                             "all:\n\t@printf '%s\\n' '$(VALUE)'\n")
                    ordinary = self.ordinary().decode().removesuffix("\n")
                    with self.session() as session:
                        native = session.make("all", variables=("VALUE",))
                    self.assertEqual(native.semantics["domains"]["VALUE"]["value"], ordinary)
                    for claim in (ordinary, words):
                        self.assertFalse(graph_probe._matches_original_patsubst(("", "POSIX", words), claim, budget))
        finally:
            budget.close()

    def test_template_initializer_claims_match_gnu_and_retain_bounds(self):
        budget = ProbeBudget()
        try:
            for pattern, replacement, words in (
                ("src/data_%.c", "%", "src/data_alpha.c src/data_beta.c"),
                ("%", "prefix_%_suffix", "alpha beta"),
                ("%.c", "%.o", "one.c other.s two.c"),
                ("same", "changed", "same other same"),
                ("a%b", "%", "ab axb other"),
                ("same", "", "same  other same"),
                ("%", "", "one two"),
            ):
                with self.subTest(pattern=pattern, replacement=replacement, words=words):
                    self.add("Makefile", "VALUE := $(patsubst " + pattern + "," + replacement + "," + words
                             + ")\nall:\n\t@printf '%s\\n' '$(VALUE)'\n")
                    ordinary = self.ordinary().decode().removesuffix("\n")
                    with self.session() as session:
                        native = session.make("all", variables=("VALUE",))
                    value = native.semantics["domains"]["VALUE"]["value"]
                    self.assertEqual(value, ordinary)
                    self.assertTrue(graph_probe._matches_original_patsubst((pattern, replacement, words), value, budget))
                    self.assertFalse(graph_probe._matches_original_patsubst(
                        (pattern, replacement, words), value + " wrong", budget,
                    ))
            mode = _MakeSourceMode(budget=budget, template_mode=lambda *args: False)
            mode.assign("NAMES", "=", "src/data_alpha.c")
            mode.assign("TABLES", ":=", "$(patsubst src/data_%.c,%,$(NAMES))")
            self.assertIsNone(mode.literal_text("$(TABLES)"))
            proof = mode.template_values["TABLES"][1]
            mode.assign("PADDED", ":=", "$(patsubst src/data_%.c,%,$(NAMES)) ")
            mode.assign("PADDED_ALIAS", ":=", "$(TABLES) ")
            self.assertNotIn("PADDED", mode.template_values)
            self.assertNotIn("PADDED_ALIAS", mode.template_values)
            mode.assign("NAMES", "=", "src/data_beta.c")
            self.assertTrue(graph_probe._matches_original_patsubst(proof[1], "alpha", budget))
            self.assertFalse(graph_probe._matches_original_patsubst(proof[1], "beta", budget))
            mode.assign("WORDS", "=", " ".join("word" + str(index) for index in range(512)))
            self.assertIsNotNone(mode.template_initializer("$(patsubst %,prefix_%,$(WORDS))"))
            mode.assign("WORDS", "+=", "one_more")
            self.assertIsNone(mode.template_initializer("$(patsubst %,prefix_%,$(WORDS))"))
        finally:
            budget.close()
        with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline"):
            graph_probe._matches_original_patsubst(("%", "%", ""), "", budget)


    def production_header_template_fixture(self, *, late=True, renamed=False):
        self.framework_templates()
        self.original_input_witness()
        config = "GENERATED_DATA_CONFIG_INPUTS_characters"
        tail_name = "GENERATED_DATA_CONFIG_INPUTS_units"
        macro = "GENERATED_DATA_LINK_TABLE_RULES"
        chunks = list(graph_probe._make_logical_chunks((ROOT / "generated_data.mk").read_text()))
        first = next(chunk.start for chunk in chunks if chunk.text == "define " + macro)
        call = next(chunk for chunk in chunks if chunk.text.startswith("$(foreach ") and macro in chunk.text)
        tail = next(chunk for chunk in chunks if chunk.text.startswith(tail_name + " :="))
        wanted = {
            "GENERATED_DATA_PY", "GENERATED_DATA_OUT_DIR", "GENERATED_DATA_LINKED_HAND_SOURCES",
            "GENERATED_DATA_LINKED_TABLES", "GENERATED_DATA_CONFIG_INPUTS_classes",
            "GENERATED_DATA_CONFIG_INPUTS_items", "GENERATED_DATA_ITEM_CAP_STAMP",
            "GENERATED_DATA_ACTIVE_HEADER", "GENERATED_DATA_CONFIG_INPUTS_supports", config,
            "GENERATED_DATA_SHARED_PY_SOURCES",
        }
        source = []
        character_rhs = None
        for chunk in chunks:
            if chunk.start > (tail.end if late else call.end):
                break
            assignment = graph_probe.ASSIGNMENT.fullmatch(
                graph_probe._collapse_make_continuations(chunk.text, posix=False),
            )
            if assignment and assignment["name"] == config:
                character_rhs = assignment["value"].lstrip(graph_probe.MAKE_SPACE)
            if (assignment and assignment["name"] in wanted
                    or first <= chunk.start <= call.end or late and chunk.start == tail.start):
                source.append(chunk.text + "\n")
            else:
                source.append("\n" * (chunk.end - chunk.start + 1))
        selected = "".join(source)
        if renamed:
            selected = selected.replace(macro, "PROJECT_" + macro)
        self.add("generated_data.mk", selected)
        tail_measurement = "$(value " + tail_name + ")" if late else ""
        self.add("Makefile", "PYTHON := /usr/bin/python3\ninclude generated_data.mk\n"
                 "all: $(GENERATED_DATA_OUT_DIR)/data_characters.c\n"
                 "measure:\n\t@printf '%s\\n' '$(value " + config + ")' '" + tail_measurement + "'\n")
        for table in ("classes", "items", "supports", "characters"):
            self.add("src/data/" + table + ".json", '{"value":1}\n')
            for path in (ROOT / "scripts/generated_data" / table).glob("*.py"):
                self.add(path.relative_to(ROOT).as_posix(), "# namespace-only table script fixture\n")
        for path in (ROOT / "scripts/generated_data").glob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            if relative not in self.entries:
                self.add(relative, "# namespace-only shared script fixture\n")
        for path in (ROOT / "scripts/assets").glob("*.py"):
            self.add(path.relative_to(ROOT).as_posix(), "# namespace-only asset script fixture\n")
        self.assertIsNotNone(character_rhs)
        wildcard = next(graph_probe._make_expression_spans(character_rhs))
        literal_paths = character_rhs[:wildcard[0]].split()
        self.assertEqual(len(literal_paths), 9)
        for path in literal_paths:
            self.add(path, (ROOT / path).read_text())
        return config, tail_name, literal_paths

    def test_composed_header_bound_accepts_actual_four_table_source(self):
        for late, renamed in ((False, False), (True, False), (True, True)):
            with self.subTest(late=late, renamed=renamed):
                config, tail, literals = self.production_header_template_fixture(late=late, renamed=renamed)
                ordinary = self.ordinary(target="measure").decode().splitlines()
                asset_paths = sorted(name for name in self.entries
                                     if Path(name).parent.as_posix() == "scripts/assets" and name.endswith(".py"))
                self.assertEqual(ordinary[0].split(), [*literals, *asset_paths])
                with self.session() as session:
                    native = session.make(
                        "all", variables=("MAKEFILE_LIST", "GENERATED_DATA_LINKED_TABLES"),
                        definitions=(config, tail),
                    )
                self.assertEqual(native.semantics["domains"]["GENERATED_DATA_LINKED_TABLES"]["value"].split(),
                                 ["classes", "items", "supports", "characters"])
                self.assertEqual(native.semantics["definitions"]["global"][config]["value"], ordinary[0])
                self.assertEqual(native.semantics["definitions"]["global"][tail]["value"], ordinary[1])
                result = self.observe(scoped_variable_names={"1", "t", "@", "@D"},
                                      symbolic_recipe_names={"GENERATED_DATA_PY"})["all"]["record"]
                projection = lambda files: [
                    {key: item[key] for key in ("target", "recipe", "prerequisites")} for item in files
                ]
                self.assertEqual(projection(result["variants"][0]["record"]["files"]),
                                 projection(native.semantics["files"]))
                self.assertEqual(result["includes"], ["Makefile", "generated_data.mk"])
                if late and not renamed:
                    self.ordinary()
                    self.assertEqual((self.root / "build/generated/data/data_characters.c").read_text(),
                                     "const int data_characters=1;\n")

    def test_composed_header_bound_removal_recovers_original_refusal(self):
        config, _, _ = self.production_header_template_fixture()
        options = {"scoped_variable_names": {"1", "t", "@", "@D"},
                   "symbolic_recipe_names": {"GENERATED_DATA_PY"}}
        positive = self.observe(**options)["all"]
        ordinary = self.ordinary(target="measure")
        checked = []
        header_data = graph_probe._TemplateModeProof.header_data

        def actual_header(proof, mode, name, active=()):
            result = header_data(proof, mode, name, active)
            if name == config:
                checked.append((result, mode.template_values.get(name)))
            return result

        with patch.object(_MakeSourceMode, "template_header_composition", return_value=None):
            self.assertEqual(self.observe(**options)["all"], positive)
        with patch.object(ProbeSession, "_original_wildcard", side_effect=make_probe._NamespaceUnavailable(
            "independent exact namespace proof removed",
        )):
            self.assertEqual(self.observe(**options)["all"], positive)
            with patch.object(_MakeSourceMode, "template_header_composition", return_value=None), \
                 patch.object(graph_probe._TemplateModeProof, "header_data", actual_header):
                with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
                    self.observe(**options)
        self.assertEqual(checked, [(False, None)])
        self.assertEqual(self.ordinary(target="measure"), ordinary)
        self.assertEqual(self.observe(**options)["all"], positive)
        self.add("include/later.h", "/* late source input */\n")
        self.add("Makefile", (self.root / "Makefile").read_text() + config + " := include/later.h\n")
        with self.session() as session:
            native = session.make("all", definitions=(config,))
        self.assertEqual(native.semantics["definitions"]["global"][config]["value"], "include/later.h")
        with self.assertRaisesRegex(MakeProbeError, "original global assignment context"):
            self.observe(**options)

    def test_composed_header_bounds_snapshot_references_without_becoming_values(self):
        self.add("Makefile", "all: ;\n")
        budget = ProbeBudget()
        mode = _MakeSourceMode(budget=budget, namespace=frozenset(("include/a.h", "include/b.h")),
                               template_mode=lambda *args: False)
        try:
            mode.assign("PREFIX", "=", "literal.h")
            mode.assign("PATTERN", "=", "include/*.h")
            mode.assign("FILES", ":=", "$(wildcard $(PATTERN))")
            mode.assign("ALIAS", "=", "$(FILES)")
            expression = "before.h $(PREFIX)\t${ALIAS} after.h $(wildcard include/*.h)"
            mode.assign("COMPOSED", ":=", expression)
            self.assertEqual(mode.template_values["COMPOSED"][1][0], "header-bound")
            self.assertTrue({"PREFIX", "PATTERN", "FILES", "ALIAS"} <= mode.reads)
            mode.assign("COPIED", ":=", "$(COMPOSED)")
            self.assertEqual(mode.template_values["COPIED"][1], mode.template_values["COMPOSED"][1])
            with self.session() as session:
                proof = graph_probe._TemplateModeProof(session, "all", (), None, None, "Makefile", False)
                with patch.object(proof, "native_value", side_effect=AssertionError("bound used as native claim")):
                    self.assertTrue(proof.header_data(mode, "COMPOSED"))
                    self.assertIsNone(proof.value(mode, "COMPOSED"))
                    self.assertIsNone(proof.text(mode, "$(COMPOSED)"))
                    self.assertIsNone(mode.literal_values("$(COMPOSED)"))
                    self.assertIsNone(mode.condition("ifeq", "($(COMPOSED),safe)"))
                    self.assertIsNone(mode.target_posix("$(COMPOSED):"))
                    mode.assign("RULE", "=", "out/$(1): $(COMPOSED)")
                    call = "$(foreach t,alpha,$(eval $(call RULE,$(t))))"
                    self.assertTrue(proof(mode, call))
                    self.assertFalse(proof(mode, call.replace("t,alpha,", "t,$(COMPOSED),")))
                    mode.assign("RULE", "=", "$(COMPOSED)/$(1):")
                    self.assertFalse(proof(mode, call))
                    mode.assign("RULE", "=", "out/$(1):\n\t@printf '%s' '$(COMPOSED)'")
                    self.assertFalse(proof(mode, call))
                mode.assign("FILES", ":=", "unsafe:header")
                self.assertTrue(proof.header_data(mode, "COMPOSED"))
                self.assertIsNone(mode.template_initializer("before $(FILES) after"))
                mode.assign("RECURSIVE", "=", "before $(COPIED) after")
                self.assertNotIn("RECURSIVE", mode.template_values)
                mode.assign("CONDITIONAL", ":=", "before $(COPIED) after", active=None)
                self.assertNotIn("CONDITIONAL", mode.template_values)
                mode.assign("DEFAULT", "?=", "before $(COPIED) after")
                self.assertNotIn("DEFAULT", mode.template_values)
                mode.assign("COMPOSED", "+=", "more.h")
                self.assertNotIn("COMPOSED", mode.template_values)
                mode.uncertain("unproved namespace change")
                self.assertIsNone(mode.template_initializer("before $(COPIED) after"))
        finally:
            budget.close()
        with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline"):
            mode.template_header_composition("before $(wildcard include/*.h)")

    def test_composed_header_bounds_require_every_original_fragment(self):
        budget = ProbeBudget()
        try:
            mode = _MakeSourceMode(budget=budget, namespace=frozenset(("include/a.h",)),
                                   template_mode=lambda *args: False)
            mode.assign("SAFE", "=", "safe.h")
            mode.assign("FILES", ":=", "$(wildcard include/*.h)")
            mode.assign("BAD", ":=", "$$(eval .POSIX:)")
            mode.assign("CYCLE", "=", "$(CYCLE)")
            for expression in (
                "$(SAFE) ${wildcard include/*.h}",
                "$(wildcard include/*.h)middle${SAFE}",
                "${SAFE}${FILES}$(wildcard include/*.h)tail",
            ):
                with self.subTest(expression=expression):
                    self.assertEqual(mode.template_initializer(expression)[0], "header-bound")
            for expression in (
                "bad: $(wildcard include/*.h)",
                "bad= $(wildcard include/*.h)",
                "bad; $(wildcard include/*.h)",
                "bad# $(wildcard include/*.h)",
                "bad| $(wildcard include/*.h)",
                "bad\\ $(wildcard include/*.h)",
                "bad\n$(wildcard include/*.h)",
                "bad\0$(wildcard include/*.h)",
                "safe $$(wildcard include/*.h)",
                "safe $(eval X := changed) $(wildcard include/*.h)",
                "safe $(shell echo safe.h)",
                "safe $(call .VARIABLES)",
                "safe $(.VARIABLES:%=%)",
                "safe $(MISSING) $(wildcard include/*.h)",
                "safe $(BAD) $(wildcard include/*.h)",
                "safe $(CYCLE) $(wildcard include/*.h)",
                "safe $(wildcard $(MISSING))",
                "safe $(wildcard include/*.h",
            ):
                with self.subTest(expression=expression):
                    self.assertIsNone(mode.template_initializer(expression))
            mode.namespace = frozenset(("include/a.h", "include/unsafe:header.h"))
            self.assertIsNone(mode.template_initializer("safe $(wildcard include/*.h)"))
            mode.namespace = None
            self.assertIsNone(mode.template_initializer("safe $(wildcard include/*.h)"))
        finally:
            budget.close()

    def test_composed_header_bounds_keep_native_defaults_exports_and_reads(self):
        self.original_input_witness()
        for path in ("first.h", "second.h", "last.h", "include/a.h", "include/b.h"):
            self.add(path, "/* fixture dependency */\n")
        self.add("Makefile", (
            "PREFIX ?= first.h\nFILES := $(wildcard include/*.h)\nALIAS = $(FILES)\n"
            "HEADER := $(PREFIX) ${ALIAS} last.h\nexport HEADER\n"
            "UNREAD = $(error unused body expanded)$(shell touch marker)\nMETA := $(origin UNREAD)\n"
            "define RULE\nout/$(1): $(HEADER)\nendef\n"
            "$(foreach t,alpha,$(eval $(call RULE,$(t))))\n"
            "LATE = first \\\n second\nall: out/alpha\n"
            "\t@printf '%s\\n' \"$$HEADER\" '$(value LATE)' '$(META)'\n"
        ))
        options = {"scoped_variable_names": {"1", "t"}}
        domains = {"PREFIX": {"kind": "explicit", "values": ["first.h", "second.h"]}}
        result = self.observe(domains, **options)["all"]
        self.assertIn("PREFIX", result["variable_census"]["defaults"])
        self.assertIn("PREFIX", result["prerequisite_domain_census"]["enumerated"])
        observed_prefixes = set()
        with self.session() as session:
            for variant in result["record"]["variants"]:
                state = variant["state"]
                ordinary = self.ordinary(
                    *(name + "=" + value for origin, name, value in state if origin == "command-line"),
                    environment={name: value for origin, name, value in state if origin == "environment"},
                ).decode().splitlines()
                native = session.make(
                    "all", assignments=state, definitions=("HEADER", "PREFIX", "UNREAD"),
                )
                records = native.semantics["definitions"]["global"]
                expected = records["PREFIX"]["value"] + " include/a.h include/b.h last.h"
                observed_prefixes.add(records["PREFIX"]["value"])
                self.assertEqual(ordinary, [expected, "first second", "file"])
                self.assertEqual(records["HEADER"], {"origin": "file", "flavor": "simple", "value": expected})
                self.assertIn("$(error unused body expanded)", records["UNREAD"]["value"])
                actual = variant["record"]
                files = {item["target"]: item for item in actual["files"]}
                self.assertEqual([item["name"] for item in files["out/alpha"]["prerequisites"]],
                                 expected.split())
                self.assertEqual(actual["native_dispatches"][0]["environment"]["HEADER"], expected)
                self.assertEqual(actual["native_dispatches"], native.semantics["native_dispatches"])
        self.assertEqual(observed_prefixes, {"first.h", "second.h"})
        self.assertFalse((self.root / "marker").exists())
        with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults.*PREFIX"):
            self.observe(**options)
        with self.assertRaisesRegex(MakeProbeError, "symbolic inputs influence the Make graph.*PREFIX"):
            self.observe(external={"PREFIX"}, symbolic_recipe_names={"PREFIX"}, **options)
        self.assertFalse((self.root / "marker").exists())

    def production_static_target_fixture(self, *, late=True, renamed=False, initializer=None):
        config, tail, _ = self.production_header_template_fixture()
        source_name = "GENERATED_DATA_LINKED_C"
        objects = "GENERATED_DATA_LINKED_OBJECTS"
        parts = list(graph_probe._make_logical_chunks((ROOT / "generated_data.mk").read_text()))
        target = next(part for part in parts if part.text.startswith("$(" + objects + "):"))
        following = next(part for part in parts if part.start > target.start
                         and part.text == ".PHONY: generated-data-link-check")
        lines = (self.root / "generated_data.mk").read_text().splitlines(keepends=True)
        for part in parts:
            assignment = graph_probe.ASSIGNMENT.fullmatch(graph_probe._collapse_make_continuations(part.text))
            if (assignment and assignment["name"] in {source_name, objects}
                    or target.start <= part.start <= following.end):
                value = part.text
                if assignment and assignment["name"] == objects and initializer is not None:
                    value = objects + " := " + initializer
                lines[part.start - 1:part.end] = (value + "\n").splitlines(keepends=True)
        source = "".join(lines if late else lines[:following.end])
        if renamed:
            for old, new in (
                (objects, "PROJECT_OBJECTS"), (source_name, "PROJECT_SOURCES"),
                ("GENERATED_DATA_LINKED_HAND_SOURCES", "PROJECT_INPUTS"),
                ("GENERATED_DATA_LINK_TABLE_RULES", "PROJECT_RULES"),
            ):
                source = source.replace(old, new)
            source_name, objects = "PROJECT_SOURCES", "PROJECT_OBJECTS"
        self.add("generated_data.mk", source)
        self.add("Makefile", (
            "PYTHON := /usr/bin/python3\nUNAME := Linux\n"
            "CPP := cpp\nCPPFLAGS := -Iinclude\nCC1 := tools/agbcc/bin/agbcc\nCC1FLAGS := -O2\n"
            "AS := arm-none-eabi-as\nASFLAGS := -mcpu=arm7tdmi\nSED := sed\n"
            "include generated_data.mk\nall: $(GENERATED_DATA_OUT_DIR)/data_characters.c\n"
            "measure:\n\t@printf '%s\\n' '$(value " + config + ")' '"
            + ("$(value " + tail + ")" if late else "") + "' '$(value " + source_name
            + ")' '$(value " + objects + ")'\n"
        ))
        return (config, tail, source_name, objects), {
            "scoped_variable_names": {"1", "t", "@", "@D", "<"},
            "symbolic_recipe_names": {"GENERATED_DATA_PY", "CPP", "CPPFLAGS", "CC1", "CC1FLAGS",
                                     "AS", "ASFLAGS", "SED"},
        }

    def test_original_exact_targets_accept_actual_static_rule_and_equivalent_facts(self):
        literal = " ".join("build/generated/data/data_" + table + ".o"
                           for table in ("classes", "items", "supports", "characters"))
        patsubst = "$(patsubst src/%.c,build/generated/data/%.o,$(GENERATED_DATA_LINKED_HAND_SOURCES))"
        for initializer, late, renamed in ((literal, True, False), (None, False, False),
                                           (None, True, False), (None, True, True),
                                           (patsubst, True, False)):
            with self.subTest(initializer=initializer, late=late, renamed=renamed):
                names, options = self.production_static_target_fixture(
                    initializer=initializer, late=late, renamed=renamed,
                )
                ordinary = self.ordinary(target="measure").decode().splitlines()
                with self.session() as session:
                    native = session.make("all", definitions=names, variables=("GENERATED_DATA_LINKED_TABLES",))
                self.assertEqual([native.semantics["definitions"]["global"][name]["value"] for name in names],
                                 ordinary)
                self.assertEqual(ordinary[-1], literal)
                self.assertEqual(native.semantics["domains"]["GENERATED_DATA_LINKED_TABLES"]["value"],
                                 "classes items supports characters")
                actual = self.observe(**options)["all"]["record"]["variants"][0]["record"]
                projection = lambda record: [
                    {key: item[key] for key in ("target", "recipe", "prerequisites")} for item in record["files"]
                ]
                self.assertEqual(projection(actual), projection(native.semantics))
                if initializer is None and late and not renamed:
                    self.ordinary()
                    self.assertEqual((self.root / "build/generated/data/data_characters.c").read_text(),
                                     "const int data_characters=1;\n")

    def test_original_exact_targets_require_constructor_and_consumer_independently(self):
        names, options = self.production_static_target_fixture()
        positive = self.observe(**options)["all"]
        ordinary = self.ordinary(target="measure")
        captured = []
        target = _MakeSourceMode.target_posix

        def record_target(mode, header):
            result = target(mode, header)
            if header.startswith("$(" + names[-1] + "):"):
                captured.append((result, mode.template_values.get(names[-1])))
            return result

        for method, expected_kind in (("exact_initializer_value", None), ("exact_target_text", "exact")):
            with self.subTest(removal=method):
                captured.clear()
                with patch.object(_MakeSourceMode, method, return_value=None), \
                     patch.object(_MakeSourceMode, "target_posix", record_target):
                    with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
                        self.observe(**options)
                self.assertEqual(len(captured), 1)
                self.assertIsNone(captured[0][0])
                self.assertEqual(None if captured[0][1] is None else captured[0][1][1][0], expected_kind)
                self.assertEqual(self.ordinary(target="measure"), ordinary)
                self.assertEqual(self.observe(**options)["all"], positive)
        _, options = self.production_static_target_fixture(initializer=(
            "$(patsubst src/%.c,build/generated/data/%.o,$(GENERATED_DATA_LINKED_HAND_SOURCES))"
        ))
        with patch.object(_MakeSourceMode, "exact_target_text", return_value=None):
            with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
                self.observe(**options)
        self.observe(**options)

    def test_original_exact_word_operations_match_gnu_text_and_native_metadata(self):
        cases = (
            ("first/x.c second/y.c", "$(notdir $(WORDS))", "x.c y.c"),
            ("a/ b/ file c/", "$(notdir $(WORDS))", "  file "),
            ("a//b/../c.c ./ . ../ / plain", "$(notdir $(WORDS))", "c.c  .   plain"),
            ("  first/x.c\tsecond/y.c  ", "$(notdir $(WORDS))", "x.c y.c"),
            ("dir\\name.c a\\ b.c dir/file.c", "$(notdir $(WORDS))", "dir\\name.c a\\ b.c file.c"),
            ("  first\tsecond  ", "$(addprefix out/,$(WORDS))", "out/first out/second"),
            (" \t ", "$(addprefix out/,$(WORDS))", ""),
            ("first second", "$(addprefix a  b/,$(WORDS))", "a  b/first a  b/second"),
            ("a/ b/ file c/", "$(addprefix out/,$(notdir $(WORDS)))", "out/file"),
            ("first second", "$(addprefix $(EMPTY) lead ,$(WORDS))", " lead first  lead second"),
            ("  first.c\tother.s second.c  ", "$(WORDS:.c=.o)", "first.o other.s second.o"),
            (".c first.c .c", "$(WORDS:.c=)", " first "),
            ("  first\tsecond  ", "$(WORDS:=.o)", "first.o second.o"),
            ("  first.c\tother.s second.c  ", "${WORDS:%.c=pre_%.o}", "pre_first.o other.s pre_second.o"),
            ("first.c second.c", "head $(addprefix out/,$(WORDS)) tail", "head out/first.c out/second.c tail"),
        )
        source = "EMPTY :=\n"
        for index, (words, expression, _) in enumerate(cases):
            source += "define WORDS_" + str(index) + "\n" + words + "\nendef\n"
            source += "VALUE_" + str(index) + " := " + expression.replace("WORDS", "WORDS_" + str(index)) + "\n"
        names = tuple("VALUE_" + str(index) for index in range(len(cases)))
        source += "all:\n\t@printf '%s\\n' " + " ".join("'$(value " + name + ")'" for name in names) + "\n"
        self.add("Makefile", source)
        self.assertEqual(self.ordinary().decode().splitlines(), [case[2] for case in cases])
        with self.session() as session:
            native = session.make("all", definitions=names)
            mode = _MakeSourceMode(budget=session.budget, template_mode=lambda *args: False)
            proof = graph_probe._TemplateModeProof(session, "all", (), None, native, "Makefile", False)
            mode.assign("EMPTY", ":=", "")
            for name, (words, expression, expected) in zip(names, cases):
                with self.subTest(expression=expression, words=words):
                    mode.assign("WORDS", "=", words, literal_body=True)
                    mode.assign(name, ":=", expression)
                    self.assertEqual(mode.template_values[name][1], ("exact", expected))
                    self.assertIsNone(mode.literal_values("$(" + name + ")"))
                    with patch.object(proof, "native_value", side_effect=AssertionError("original data used a native seed")):
                        self.assertEqual(proof.value(mode, name), expected)
                    self.assertEqual(native.semantics["definitions"]["global"][name],
                                     {"origin": "file", "flavor": "simple", "value": expected})
            mode.assign("WORDS", "=", "first.c other.s")
            self.assertIsNone(mode.exact_initializer_value("$(WORDS:.c=%.o)"))

    def test_original_exact_targets_keep_delayed_posix_and_later_source_obligations(self):
        for initializer in (".POSIX", "$(notdir directory/.POSIX)", "$(addprefix .,POSIX)",
                            "$(patsubst X,.POSIX,X)"):
            with self.subTest(initializer=initializer):
                source = "TARGETS := " + initializer + "\n$(TARGETS): %.o: %.c\n"
                self.assertEqual(self.target_mode_fixture(source), ["alpha beta", "alpha   beta"])
        for initializer, concealed in (("$(notdir directory/.POSIX)", False),
                                       ("$(notdir directory/.POSIX)", True),
                                       ("$(patsubst X,.POSIX,X)", True)):
            with self.subTest(initializer=initializer, concealed=concealed):
                source = (
                    "TARGETS := " + initializer + "\n$(TARGETS): %.o: %.c\n"
                    "FIRST = alpha  \\\n beta\nSECOND = alpha  \\\n beta\n"
                )
                source += (
                    "ifeq ($(SECOND),alpha   beta)\nTARGETS := ordinary\nHIDDEN ?= secret\nendif\n"
                    if concealed else "TARGETS := ordinary\nHIDDEN ?= secret\n"
                )
                self.add("Makefile", source + "all:\n\t@printf '%s\\n' '$(value FIRST)' '$(value SECOND)'\n")
                self.original_input_witness()
                with self.session() as session:
                    native = session.make("all", definitions=("TARGETS", "FIRST", "SECOND", "HIDDEN"))
                records = native.semantics["definitions"]["global"]
                self.assertEqual(records["TARGETS"]["value"], "ordinary")
                self.assertEqual(records["HIDDEN"]["value"], "secret")
                self.assertEqual([records[name]["value"] for name in ("FIRST", "SECOND")],
                                 ["alpha beta", "alpha   beta"])
                if initializer.startswith("$(patsubst"):
                    with self.assertRaisesRegex(MakeProbeError, "unproven.*continuation"):
                        self.observe()
                else:
                    usage, values, _ = self.observed_source_census()
                    self.assertIn("HIDDEN", usage["defaults"])
                    self.assertEqual(values["SECOND"], {"alpha   beta"})
                    with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults.*HIDDEN"):
                        self.observe()

    def test_original_exact_snapshots_and_unknown_contexts_never_borrow_final_values(self):
        self.add("Makefile", "all: ;\n")
        budget = ProbeBudget()
        try:
            mode = _MakeSourceMode(budget=budget, template_mode=lambda *args: False,
                                   namespace=frozenset(("include/a.h",)))
            mode.assign("INPUTS", "=", "first/alpha.c second/beta.c")
            mode.assign("FILES", ":=", "$(notdir $(INPUTS))")
            mode.assign("ALIAS", "=", "${FILES}")
            mode.assign("OBJECTS", ":=", "$(addprefix out/,${ALIAS:.c=.o})")
            self.assertTrue({"INPUTS", "FILES", "ALIAS"} <= mode.reads)
            mode.assign("INPUTS", "=", "changed/late.c")
            mode.assign("FILES", ":=", "late.c")
            self.assertEqual(mode.template_values["OBJECTS"][1], ("exact", "out/alpha.o out/beta.o"))
            self.assertEqual(mode.exact_reference("OBJECTS"), "out/alpha.o out/beta.o")
            mode.assign("UNREAD", "=", "$(error unused)$(shell touch marker)")
            mode.assign("META", ":=", "$(origin UNREAD)")
            mode.assign("META_PATH", ":=", "$(addprefix $(META)/,safe)")
            self.assertEqual(mode.exact_reference("META_PATH"), "file/safe")
            self.assertIn("UNREAD", mode.reads)
            mode.assign("BOUND", ":=", "$(wildcard include/*.h)")
            literal_filter = "$(filter %.c,alpha.c)"
            self.add("Makefile", "FILTERED := " + literal_filter + "\nall: ;\n")
            with self.session() as session:
                observed = session.make("all", variables=("FILTERED",))
            self.assertEqual(observed.semantics["domains"]["FILTERED"],
                             {"origin": "file", "flavor": "simple", "value": "alpha.c"})
            self.assertEqual(mode.exact_initializer_value(literal_filter),
                             observed.semantics["domains"]["FILTERED"]["value"])
            for expression in (
                "$(addprefix out/,$(BOUND))", "$(notdir $(MISSING))",
                "$(addprefix out/,$(UNREAD))", "$(notdir $$(eval .POSIX:))",
                "$(call .VARIABLES)", "$(notdir $(.VARIABLES:%=%))",
                "$(filter $(UNPROVEN_PATTERN),alpha.c)", "$(subst X,alpha,X)", "$(shell echo alpha.c)",
                "$(addprefix out/,$(notdir incomplete)", "$(FILES:.c=one=two)",
            ):
                with self.subTest(expression=expression):
                    self.assertIsNone(mode.exact_initializer_value(expression))
            mode.assign("CYCLE", "=", "$(CYCLE)")
            self.assertIsNone(mode.exact_initializer_value("$(notdir $(CYCLE))"))
            mode.assign("RECURSIVE", "=", "$(notdir first/alpha.c)")
            self.assertNotIn("RECURSIVE", mode.template_values)
            self.assertEqual(mode.exact_reference("RECURSIVE"), "alpha.c")
            mode.assign("CONDITIONAL", ":=", "$(notdir first/alpha.c)", active=None)
            mode.assign("DEFAULT", "?=", "$(notdir first/alpha.c)")
            mode.assign("SCOPED", ":=", "$(notdir first/alpha.c)", scope="all")
            self.assertFalse({"CONDITIONAL", "DEFAULT", "SCOPED"} & set(mode.template_values))
            mode.assign("OBJECTS", "+=", "extra.o")
            self.assertEqual(mode.exact_reference("OBJECTS"), "out/alpha.o out/beta.o extra.o")
            for posix, namespace in ((None, True), (False, False)):
                mode.posix, mode.original_namespace_valid = posix, namespace
                with patch.object(mode, "original_target_value", side_effect=AssertionError("unproved target lookup")):
                    self.assertIsNone(mode.exact_target_text("$(META_PATH)"))
            mode.uncertain()
            self.assertIsNone(mode.exact_reference("META_PATH"))
            self.assertFalse((self.root / "marker").exists())
            forced = _MakeSourceMode(definitions={"VALUE": "command"}, forced=frozenset(("VALUE",)),
                                     budget=budget, template_mode=lambda *args: False)
            forced.assign("VALUE", ":=", "$(notdir file/ignored)")
            self.assertNotIn("VALUE", forced.template_values)
            self.assertEqual(forced.literal_text("$(VALUE)"), "command")
        finally:
            budget.close()
        with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline"):
            mode.exact_initializer_value("$(notdir value)")
        with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline"):
            mode.exact_target_text("$(META_PATH)")

    def test_original_exact_text_retention_is_funded_before_join(self):
        budget = ProbeBudget(Limits(cache_bytes=1000))
        produced = []

        def parts():
            for index in range(8):
                produced.append(index)
                yield "x" * 512

        try:
            with self.assertRaisesRegex(MakeProbeError, "aggregate cache byte budget"):
                graph_probe._join_make_text(parts(), budget)
            self.assertTrue(budget.failed)
            self.assertLess(len(produced), 8)
        finally:
            budget.close()
        budget = ProbeBudget(Limits(cache_bytes=1000))
        produced.clear()
        try:
            with patch.object(budget, "charge"):
                self.assertEqual(len(graph_probe._join_make_text(parts(), budget)), 4096)
            self.assertEqual(len(produced), 8)
        finally:
            budget.close()

    def test_original_exact_targets_keep_finite_defaults_exports_and_read_closure(self):
        self.original_input_witness()
        for name in ("first", "second"):
            self.add("src/" + name + ".c", "/* bounded source input */\n")
        self.add("Makefile", (
            "STEMS ?= first.c\nROOT := out\nSOURCES := $(addprefix src/,$(STEMS))\n"
            "FILES := $(notdir $(SOURCES))\nOBJECTS := $(addprefix $(ROOT)/,$(FILES:.c=.o))\n"
            "ALIAS = $(OBJECTS)\nexport SNAPSHOT := $(ALIAS)\n"
            "UNREAD = $(error unused)$(shell touch marker)\nMETA := $(origin UNREAD)\n"
            "$(OBJECTS): $(ROOT)/%.o: src/%.c\n"
            "\t@printf '%s\\n' \"$$SNAPSHOT\" '$(META)'\n"
            "LATE = alpha  \\\n beta\nall: $(OBJECTS)\n"
        ))
        options = {"scoped_variable_names": set(), "environment_names": {"STEMS"}}
        domains = {"STEMS": {"kind": "explicit", "values": ["first.c", "second.c"]}}
        result = self.observe(domains, **options)["all"]
        self.assertIn("STEMS", result["variable_census"]["defaults"])
        self.assertIn("STEMS", result["prerequisite_domain_census"]["enumerated"])
        with self.session() as session:
            for variant in result["record"]["variants"]:
                state = variant["state"]
                ordinary = self.ordinary(
                    *(name + "=" + value for origin, name, value in state if origin == "command-line"),
                    environment={name: value for origin, name, value in state if origin == "environment"},
                ).decode().splitlines()
                native = session.make("all", assignments=state, definitions=("SNAPSHOT", "OBJECTS"))
                value = native.semantics["definitions"]["global"]["OBJECTS"]["value"]
                self.assertEqual(ordinary, [value, "file"])
                record = variant["record"]
                self.assertEqual(record["native_dispatches"][0]["environment"]["SNAPSHOT"], value)
                self.assertEqual(record["native_dispatches"], native.semantics["native_dispatches"])
                files = {item["target"]: item for item in record["files"]}
                self.assertEqual(files["all"]["prerequisites"], [{"name": value, "order_only": False}])
                self.assertEqual(files[value]["prerequisites"],
                                 [{"name": "src/" + Path(value).stem + ".c", "order_only": False}])
        self.assertFalse((self.root / "marker").exists())
        with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults.*STEMS"):
            self.observe(**options)
        with self.assertRaisesRegex(MakeProbeError, "symbolic inputs influence the Make graph.*STEMS"):
            self.observe(external={"STEMS"}, symbolic_recipe_names={"STEMS"}, **options)

    def related_static_target_fixture(self, path, name, inputs, *, prelude="", scoped_assignment="",
                                      late_consumer=False):
        self.original_input_witness()
        parts = list(graph_probe._make_logical_chunks((ROOT / path).read_text()))
        constructor = next(part.text for part in parts
                           if (match := graph_probe.ASSIGNMENT.fullmatch(
                               graph_probe._collapse_make_continuations(part.text)))
                           and match["name"] == name)
        header = next(part.text for part in parts if part.text.startswith("$(" + name + "):")
                      and len(graph_probe._rule_separators(part.text)) == 2)
        for dependency in (
            "src/alpha.c", "src/beta.c", "src/msg_data.c", "src/expansion_bgm_data.c",
            "src/data/alpha.c", "src/data/beta.c", "asm/alpha.s", "asm/beta.s",
            ".dep/src/alpha.d", ".dep/src/beta.d", ".dep/src/msg_data.d",
            "out/src/data/alpha.pre.c", "out/src/data/beta.pre.c",
            "tools/preproc/preproc", "tools/scaninc/scaninc", "include/extra.h",
        ):
            self.add(dependency, "/* bounded prerequisite fixture */\n")
        source = "".join(key + " := " + value + "\n" for key, value in inputs.items())
        source += prelude + constructor + "\n" + scoped_assignment
        if not late_consumer:
            source += "all: $(" + name + ")\n"
        if "$$(" in header:
            source += ".SECONDEXPANSION:\n"
        source += header + "\n\t@printf '%s\\n' '$@' '$^'\n"
        source += "NEXT := recorded\nLATE = alpha  \\\n beta\n"
        if late_consumer:
            source += "all: $(" + name + ")\n"
        source += "measure:\n\t@printf '%s\\n' '$(value " + name + ")' '$(value LATE)'\n"
        self.add("Makefile", source)
        self.last_related_target_fixture = {
            "path": path, "name": name, "constructor": constructor, "header": header,
            "literal_fixture_inputs": inputs, "original_or_fixture_prelude": prelude,
            "original_scoped_assignment": scoped_assignment,
            "late_aggregate_consumer": late_consumer,
            "recipe_boundary": "Benign fixture print recipe; original static header and secondary prerequisites retained.",
        }
        return {"scoped_variable_names": {"@", "^"}}

    def test_original_exact_related_static_headers_keep_native_secondary_dependencies(self):
        shared = {"MODERN_OUTPUT_DIR": "out", "MODERN_PREPROC": "tools/preproc/preproc",
                  "MODERN_SCANINC": "tools/scaninc/scaninc"}
        cases = (
            ("Makefile", "ASM_OBJECTS", {"SFILES": "asm/alpha.s asm/beta.s", "data_dep": "include/extra.h"}),
            ("Makefile", "DATA_SRC_C_OBJECTS", {"DATA_SRC_C_FILES": "src/data/alpha.c src/data/beta.c",
                                               "PREPROC": "tools/preproc/preproc", "data_dep": "include/extra.h"}),
            *(("modern.mk", name, {**shared, "MODERN_ALL_DATA_C_SOURCES": "src/data/alpha.c src/data/beta.c"})
              for name in ("MODERN_ALL_DATA_PRE", "MODERN_ALL_DATA_OBJECTS", "MODERN_ALL_DATA_ASSET_DEPS")),
            ("modern.mk", "MODERN_ALL_C_HEADER_DEPS", {**shared, "MODERN_ALL_C_SOURCES": "src/alpha.c src/beta.c"}),
        )
        self.last_related_target_cases = []
        for path, name, inputs in cases:
            with self.subTest(path=path, name=name):
                options = self.related_static_target_fixture(path, name, inputs)
                ordinary = self.ordinary(target="measure").decode().splitlines()
                with self.session() as session:
                    native = session.make("all", definitions=(name, "LATE"))
                self.assertEqual([native.semantics["definitions"]["global"][key]["value"] for key in (name, "LATE")],
                                 ordinary)
                self.assertEqual(ordinary[1], "alpha beta")
                result = self.observe(**options)["all"]
                record = result["record"]["variants"][0]["record"]
                projection = lambda data: [
                    {key: item[key] for key in ("target", "recipe", "prerequisites")} for item in data["files"]
                ]
                self.assertEqual(projection(record), projection(native.semantics))
                files = {item["target"]: item for item in record["files"]}
                self.assertEqual([item["name"] for item in files["all"]["prerequisites"]], ordinary[0].split())
                if "data_dep" in inputs:
                    for target in ordinary[0].split():
                        self.assertIn({"name": "include/extra.h", "order_only": False}, files[target]["prerequisites"])
                self.last_related_target_cases.append({
                    **self.last_related_target_fixture, "status": "bounded literal leaves supported",
                    "native_target_value": ordinary[0], "runs_states": self.last_accounting,
                })

    def test_original_exact_target_proof_does_not_waive_secondary_value_guards(self):
        inputs = {"SFILES": "asm/alpha.s asm/beta.s", "data_dep": "include/extra.h"}
        options = self.related_static_target_fixture("Makefile", "ASM_OBJECTS", inputs)
        positive = self.observe(**options)["all"]["record"]["variants"][0]["record"]
        options = self.related_static_target_fixture("Makefile", "ASM_OBJECTS", inputs, late_consumer=True)
        with self.session() as session:
            native = session.make("all", definitions=("ASM_OBJECTS",))
        projection = lambda data: [
            {key: item[key] for key in ("target", "recipe", "prerequisites")} for item in data["files"]
        ]
        self.assertEqual(projection(positive), projection(native.semantics))
        usages = []
        graph_definitions = graph_probe._graph_definitions

        def record_usage(session, target, state, commands, observation, usage, **kwargs):
            usages.append(usage)
            return graph_definitions(session, target, state, commands, observation, usage, **kwargs)

        with patch.object(graph_probe, "_graph_definitions", record_usage):
            with self.assertRaisesRegex(MakeProbeError, "unproven emitted-reference substitution"):
                self.observe(**options)
        self.assertIn("ASM_OBJECTS", usages[0]["stage_graph"])
        self.assertTrue(any("$(ASM_OBJECTS)" in expression for expression in usages[0]["stage_sinks"]))
        self.assertIn("$(SFILES:.s=.o)", {value.strip() for value in usages[0]["source_expressions"]["ASM_OBJECTS"]})

    def test_original_exact_required_leaf_and_scope_gaps_remain_explicit_failures(self):
        """Former refusals now require fixed and component-removal evidence."""
        make = (ROOT / "Makefile").read_text().splitlines()
        modern = (ROOT / "modern.mk").read_text().splitlines()
        actual = lambda lines, prefix: next(line for line in lines if line.startswith(prefix)) + "\n"
        def between(lines, first, last):
            start = next(index for index, line in enumerate(lines) if line.startswith(first))
            stop = next(index for index, line in enumerate(lines) if index > start and line.startswith(last))
            return "\n".join(lines[start:stop]) + "\n"

        hand_sources = actual((ROOT / "generated_data.mk").read_text().splitlines(),
                              "GENERATED_DATA_LINKED_HAND_SOURCES :=")
        c_sources = hand_sources + between(make, "CFILES_GENERATED :=", "ASM_S_FILES  :=")
        all_sources = hand_sources + between(make, "CFILES_GENERATED :=", "SFILES_COMPILED :=")
        modern_sources = hand_sources + between(modern, "MODERN_ALL_C_SOURCES ?=", "MODERN_ALL_DATA_C_SOURCES ?=")
        bgm = between(modern, "MODERN_BGM_REGISTRY_JSON :=", "MODERN_ALL_C_OBJECTS :=")
        findstring = modern_sources + bgm
        scoped = actual(modern, "$(MODERN_ALL_DATA_OBJECTS): MODERN_CFLAGS +=")
        cases = (
            ("filter-out", "Makefile", "LEGACY_C_OBJECTS",
             {"C_SUBDIR": "src", "DEPS_DIR": ".dep"},
             c_sources + actual(make, "C_OBJECTS    :=") + actual(make, "LEGACY_MSG_OBJECT :="), ""),
            ("wildcard-composed-leaf", "Makefile", "ASM_OBJECTS",
             {"C_SUBDIR": "src", "ASM_SUBDIR": "asm", "DATA_SUBDIR": "data", "DATA_SRC_SUBDIR": "src/data",
              "data_dep": "include/extra.h"}, all_sources, ""),
            ("multi-pattern-wildcard-leaf", "Makefile", "DATA_SRC_C_OBJECTS",
             {"DATA_SRC_SUBDIR": "src/data", "PREPROC": "tools/preproc/preproc", "data_dep": "include/extra.h"},
             actual(make, "DATA_SRC_C_FILES :="), ""),
            ("recursive-wildcard-default", "modern.mk", "MODERN_ALL_DATA_PRE",
             {"MODERN_OUTPUT_DIR": "out", "MODERN_PREPROC": "tools/preproc/preproc"},
             actual(modern, "MODERN_ALL_DATA_C_SOURCES ?="), ""),
            ("unknown-findstring-conditional-append", "modern.mk", "MODERN_ALL_C_HEADER_DEPS",
             {"MODERN_OUTPUT_DIR": "out"}, findstring, ""),
            ("append-to-exact-snapshot", "modern.mk", "MODERN_ALL_C_HEADER_DEPS",
             {"MODERN_OUTPUT_DIR": "out"}, findstring, ""),
            ("derived-target-specific-context", "modern.mk", "MODERN_ALL_DATA_OBJECTS",
             {"MODERN_OUTPUT_DIR": "out", "MODERN_CFLAGS": "-O2"},
             actual(modern, "MODERN_ALL_DATA_C_SOURCES ?=") + actual(modern, "MODERN_DATA_LAYOUT_FLAGS :="), scoped),
        )
        registry = json.loads((ROOT / ".github/validation-ownership-make-dynamics.json").read_text())
        self.last_related_target_cases = []
        for gap, path, name, inputs, prelude, target_assignment in cases:
            with self.subTest(gap=gap):
                options = self.related_static_target_fixture(
                    path, name, inputs, prelude=prelude, scoped_assignment=target_assignment,
                )
                for dependency in ("src/rom_header.s", "src/crt0.s", "src/m4a_1.s", "src/libagbsyscall.s"):
                    self.add(dependency, "/* actual named assembly input; fixture never assembles */\n")
                self.add(".dep/src/expansion_bgm_data.d", "/* bounded depfile prerequisite */\n")
                for dependency in ("src/data/bgm_registry.json", "scripts/modernize/bgm_registry.py"):
                    self.add(dependency, (ROOT / dependency).read_text())
                if gap in {"unknown-findstring-conditional-append", "append-to-exact-snapshot"}:
                    for dependency in ("src/msg_data.c", "src/expansion_bgm_data.c"):
                        (self.root / dependency).unlink()
                        del self.entries[dependency]
                    self.add("Makefile", (self.root / "Makefile").read_text()
                             + "src/msg_data.c src/expansion_bgm_data.c: ; @true\n")
                    options["ambient_undefined_names"] = {"MODERN_INTERNAL_AUTOPLAY_STRATEGY_ROUTER_ABSENT"}
                defaults = {
                    variable for variable in ("MODERN_ALL_C_SOURCES", "MODERN_ALL_DATA_C_SOURCES")
                    if variable + " ?=" in prelude
                }
                self.assertTrue(defaults <= set(registry["ambient_inputs"]["allowed_names"]))
                self.assertTrue(defaults <= set(registry["prerequisite_domains"]["tracked_fallback_names"]))
                domains = {variable: {"kind": "tracked-fallback"} for variable in defaults}
                ordinary = self.ordinary(target="measure").decode().splitlines()
                with self.session() as session:
                    native = session.make("all", definitions=(name, "LATE"))
                records = native.semantics["definitions"]["global"]
                self.assertEqual([records[key]["value"] for key in (name, "LATE")], ordinary)
                self.assertTrue(ordinary[0])
                self.assertEqual(ordinary[1], "alpha beta")
                fixed = self.observe(domains, **options)["all"]
                record = fixed["record"]["variants"][0]["record"]
                projection = lambda data: [
                    {key: item[key] for key in ("target", "recipe", "prerequisites")} for item in data["files"]
                ]
                self.assertEqual(projection(record), projection(native.semantics))
                exact = _MakeSourceMode.exact_initializer_value
                assign = _MakeSourceMode.assign
                targets = _MakeSourceMode.assign_targets

                def without_operator(mode, expression, *args, **kwargs):
                    function = graph_probe._make_function(expression)
                    operation = "filter-out" if gap == "filter-out" else "findstring"
                    if function is not None and function[0] == operation:
                        return None
                    return exact(mode, expression, *args, **kwargs)

                def without_append(mode, variable, operator, *args, **kwargs):
                    effect = assign(mode, variable, operator, *args, **kwargs)
                    if operator == "+=" and kwargs.get("scope") is None:
                        mode.template_values.pop(variable, None)
                    return effect

                def without_scopes(mode, declaration, **kwargs):
                    if "$" in declaration["target"]:
                        mode.uncertain("removed exact derived scope resolution")
                        return graph_probe._AssignmentEffect(None, None)
                    return targets(mode, declaration, **kwargs)

                if "wildcard" in gap:
                    removal = patch.object(ProbeSession, "_original_wildcard", side_effect=make_probe._NamespaceUnavailable(
                        "removed independent original wildcard authority",
                    ))
                elif gap == "append-to-exact-snapshot":
                    removal = patch.object(_MakeSourceMode, "assign", without_append)
                elif gap == "derived-target-specific-context":
                    removal = patch.object(_MakeSourceMode, "assign_targets", without_scopes)
                else:
                    removal = patch.object(_MakeSourceMode, "exact_initializer_value", without_operator)
                with removal:
                    with self.assertRaises(MakeProbeError) as rejected:
                        self.observe(domains, **options)
                self.assertEqual(self.observe(domains, **options)["all"], fixed)
                self.last_related_target_cases.append({
                    **self.last_related_target_fixture, "status": "original ancestors fixed; independent removal rejects",
                    "gap": gap, "native_target_value": ordinary[0], "native_metadata": records[name],
                    "component_removal_rejection": str(rejected.exception),
                })

    def test_original_namespace_capability_is_issued_typed_and_context_bound(self):
        self.add("src/a.c", "a\n")
        self.add("src/b.c", "b\n")
        self.add("src/.hidden.c", "hidden\n")
        self.add("src/directory.c/member.txt", "directory member\n")
        (self.root / "src/untracked.c").write_text("not an admitted input\n")
        patterns = "src/b.c src/*.c src/a.c"
        self.add("Makefile", "VALUE := $(wildcard " + patterns + ")\nall: ;\n")
        with self.session() as session:
            native = session.make("all", variables=("VALUE", "MAKEFILE_LIST", "MAKE_RESTARTS"))
            token = session._original_namespace(native, target="all", makefile="Makefile")
            self.assertEqual(session._original_wildcard(token, patterns), native.semantics["domains"]["VALUE"]["value"])
            self.assertEqual(session._original_wildcard(token, patterns),
                             "src/b.c src/a.c src/b.c src/directory.c src/a.c")
            self.assertEqual(session._original_wildcard(token, "absent/*.c src/missing.c"), "")
            self.assertEqual(session._original_wildcard(token, "src/.*.c"), "src/.hidden.c")
            with self.assertRaisesRegex(MakeProbeError, "forged|expired"):
                session._original_wildcard(make_probe._OriginalNamespace(), patterns)
            with self.assertRaisesRegex(MakeProbeError, "issued native observation"):
                session._original_namespace(replace(native), target="all", makefile="Makefile")
            for target, makefile, assignments in (
                ("other", "Makefile", ()), ("all", "other.mk", ()),
                ("all", "Makefile", (("command-line", "VALUE", "forged"),)),
            ):
                with self.subTest(target=target, makefile=makefile, assignments=assignments):
                    with self.assertRaisesRegex(MakeProbeError, "invocation/state"):
                        session._original_namespace(native, target=target, makefile=makefile, assignments=assignments)
            original = native.semantics["domains"]["MAKE_RESTARTS"]
            native.semantics["domains"]["MAKE_RESTARTS"] = {"origin": "environment", "flavor": "recursive", "value": "1"}
            with self.assertRaisesRegex(MakeProbeError, "observation changed"):
                session._original_wildcard(token, patterns)
            native.semantics["domains"]["MAKE_RESTARTS"] = original
            for pattern in ("src/?.c", "src/[ab].c", "*/a.c", "~/a.c", "src/../a.c", "src\\a.c"):
                with self.subTest(pattern=pattern):
                    with self.assertRaises(make_probe._NamespaceUnavailable):
                        session._original_wildcard(token, pattern)
        self.assertIsNone(session.base)
        self.assertFalse(session.budget.children)
        self.assertFalse(session.budget.producer_waiters)
        with self.assertRaisesRegex(MakeProbeError, "deadline|budget"):
            session._original_wildcard(token, patterns)

    def test_original_namespace_rejects_unadmitted_types_and_changed_backing(self):
        self.add("src/a.c", "source\n")
        link = "src/link.c"
        (self.root / link).symlink_to("a.c")
        self.entries[link] = GitTreeEntry(link, "120000", "blob", hashlib.sha1(b"a.c").hexdigest())
        self.add("src/space name.c", "unsupported raw word spelling\n")
        self.add("Makefile", "all: ;\n")
        with self.session() as session:
            native = session.make("all")
            token = session._original_namespace(native, target="all", makefile="Makefile")
            with self.assertRaisesRegex(make_probe._NamespaceUnavailable, "unadmitted object"):
                session._original_wildcard(token, "src/link.c")
            with self.assertRaisesRegex(make_probe._NamespaceUnavailable, "spelling/type"):
                session._original_wildcard(token, "src/space*")
            self.assertEqual(session._original_wildcard(token, "src/a.c"), "src/a.c")
            changed = session.tree / "src/new.c"
            changed.write_text("unreceipted private-tree mutation\n")
            changed.unlink()
            with self.assertRaisesRegex(make_probe._NamespaceUnavailable, "identity changed"):
                session._original_wildcard(token, "src/a.c")

    def test_original_namespace_publication_history_keeps_gnu_cache_counterexample(self):
        self.last_namespace_cases = []
        for location in ("build", "src"):
            with self.subTest(location=location):
                outputs = (location + "/new.c/child.txt", location + "/z.c")
                self.add("src/a.c", "initial\n")
                self.add("writer.py", (
                    "from pathlib import Path\n"
                    "base=Path('/work') if Path(__file__).resolve().parent==Path('/repo') else Path(__file__).resolve().parent\n"
                    "for name in " + repr(outputs) + ":\n"
                    " path=base/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('generated\\n')\n"
                ))
                self.add("Makefile", (
                    "EARLY := $(wildcard src/*.c)\nLAZY = $(wildcard src/*.c)\n"
                    "TRIGGER := $(shell python3 writer.py)\nLATE := $(wildcard src/*.c)\n"
                    "all:\n\t@printf '%s\\n' '$(EARLY)' '$(LATE)' '$(LAZY)'\n"
                ))
                with self.session() as session:
                    native = session.make(
                        "all", variables=("LAZY",), definitions=("EARLY", "LATE"),
                        commands={"python3 writer.py": Command(
                            ("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",), outputs=outputs,
                        )},
                    )
                    token = session._original_namespace(native, target="all", makefile="Makefile")
                    self.assertEqual(native.semantics["definitions"]["global"]["EARLY"]["value"], "src/a.c")
                    self.assertEqual(native.semantics["definitions"]["global"]["LATE"]["value"], "src/a.c")
                    self.assertEqual(native.semantics["domains"]["LAZY"]["value"], "src/a.c")
                    if location == "src":
                        with self.assertRaisesRegex(make_probe._NamespaceUnavailable, "changed during invocation"):
                            session._original_wildcard(token, "src/*.c")
                    else:
                        self.assertEqual(session._original_wildcard(token, "src/*.c"), "src/a.c")
                    self.last_namespace_cases.append({
                        "location": location, "generated": [item.path for item in native.generated],
                        "early": native.semantics["definitions"]["global"]["EARLY"],
                        "late": native.semantics["definitions"]["global"]["LATE"],
                        "mutations": list(session._require_namespace(token).mutations),
                    })
                self.assertEqual(self.ordinary().decode().splitlines(), ["src/a.c"] * 3)
                expected = "src/a.c src/new.c src/z.c" if location == "src" else "src/a.c"
                self.assertEqual(self.ordinary().decode().splitlines(), [expected] * 3)

    def test_original_namespace_requires_private_completed_and_funded_capture(self):
        self.add("src/a.c", "source\n")
        self.add("Makefile", "all: ;\n")
        with self.session() as session:
            native = session.make("all")
            token = session._original_namespace(native, target="all", makefile="Makefile")
            issued = session._require_namespace(token)
            fake = make_probe._NamespaceCapture(
                issued.image, issued.epoch, issued.request, dict(issued.stamps), [],
                native_complete=True, closed=True,
            )
            with self.assertRaisesRegex(MakeProbeError, "incomplete original namespace"):
                session._seal_namespace(fake, native)
            with self.assertRaises(TypeError):
                issued.stamps["src"] = ()
            with self.assertRaises(TypeError):
                issued.image.members["src"] = ()
            incomplete = session._begin_namespace("all", "Makefile", ())
            session._end_namespace(incomplete)
            with self.assertRaisesRegex(MakeProbeError, "incomplete original namespace"):
                session._seal_namespace(incomplete, native)
            self.assertEqual(session._original_wildcard(token, "src/*.c"), "src/a.c")
            session.budget.charge("cache", session.budget.limits.cache_bytes - session.budget.bytes["cache"] - 1)
            with self.assertRaisesRegex(MakeProbeError, "aggregate cache byte budget"):
                session._original_wildcard(token, "src/*.c")
        self.assertFalse(session.budget.children)
        self.assertFalse(session.budget.producer_waiters)

    def test_original_namespace_records_nested_inherited_and_restored_publications(self):
        self.add("src/a.c", "source\n")
        self.add("writer.py", (
            "from pathlib import Path\nimport sys\n"
            "p=Path('/work/src/generated.c');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(sys.argv[1])\n"
        ))
        self.add("child.mk", "AGAIN := $(shell python3 writer.py first)\npeek: ;\n")
        self.add("Makefile", (
            "FIRST := $(shell python3 writer.py first)\nNESTED := $(shell printf inspect)\n"
            "SECOND := $(shell python3 writer.py first)\nRESTORED := $(shell python3 writer.py first)\nall: ;\n"
        ))
        registrations = {
            "python3 writer.py " + value: Command(
                ("/usr/bin/python3", "/repo/writer.py", value), code=("writer.py",),
                outputs=("src/generated.c",), publication_policy="if-content-changed",
            ) for value in ("first", "second")
        }
        registrations["printf inspect"] = Command(("/usr/bin/printf", "inspect"))
        nested = []
        with self.session() as session:
            command = session.command

            def enter_child(registration):
                if registration.argv == ("/usr/bin/printf", "inspect"):
                    child = session.make("peek", makefile="child.mk", commands=registrations)
                    token = session._original_namespace(child, target="peek", makefile="child.mk")
                    self.assertEqual(session._original_wildcard(token, "src/*.c"), "src/a.c src/generated.c")
                    self.assertTrue(all(row[1] == "retained" for row in session._require_namespace(token).mutations))
                    nested.append((child, token))
                return command(registration)

            with patch.object(session, "command", enter_child):
                native = session.make("all", commands=registrations)
            token = session._original_namespace(native, target="all", makefile="Makefile")
            mutations = session._require_namespace(token).mutations
            actions = [row[1] for row in mutations if row[2] == "src/generated.c"]
            self.assertEqual(actions, ["created", "retained", "retained", "retained", "removed"])
            self.assertEqual(native.generated[0].data, b"first")
            self.assertEqual(len({row[0] for row in mutations}), len(mutations))
            with self.assertRaisesRegex(make_probe._NamespaceUnavailable, "changed during invocation"):
                session._original_wildcard(token, "src/*.c")
            with self.assertRaises(make_probe._NamespaceUnavailable):
                session._original_wildcard(nested[0][1], "src/*.c")
        self.assertIsNone(session.base)
        self.assertFalse(session.budget.children)
        self.assertFalse(session.budget.producer_waiters)

    def test_original_namespace_failed_conflicting_publication_cannot_issue(self):
        self.add("src/a.c", "source\n")
        self.add("writer.py", (
            "from pathlib import Path\nimport sys\n"
            "p=Path('/work/src/new.c');p.parent.mkdir(parents=True,exist_ok=True);p.write_text(sys.argv[1])\n"
        ))
        self.add("Makefile", "A := $(shell python3 writer.py first)\nB := $(shell python3 writer.py second)\nall: ;\n")
        commands = {
            "python3 writer.py " + value: Command(
                ("/usr/bin/python3", "/repo/writer.py", value), code=("writer.py",), outputs=("src/new.c",),
            ) for value in ("first", "second")
        }
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "conflicting generated output producers"):
                session.make("all", commands=commands)
            self.assertFalse(session._namespace_issued)
            self.assertFalse(session._namespace_tokens)
        self.assertFalse(session.budget.children)
        self.assertFalse(session.budget.producer_waiters)

    def test_original_namespace_rejects_caller_supplied_initial_publications(self):
        self.add("Makefile", "all: ;\n")
        with self.session() as session:
            path = session.tree / "forged.c"
            path.write_text("unreceipted\n")
            value = make_probe.GeneratedFile("forged.c", b"unreceipted\n", 0o644)
            session.published_sources[value.path] = value
            session.published_versions[value.path] = ("forged", 1, make_probe.publication_identity(path.stat()))
            with self.assertRaisesRegex(MakeProbeError, "unreceipted inherited publications"):
                session.make("all")
            self.assertFalse(session._namespace_issued)
        self.assertIsNone(session.base)

    def test_original_boolean_and_word_algebra_keeps_all_read_consumers_lazy(self):
        for assignment in (
            "VALUE := $(and ,$(UNUSED))\n",
            "VALUE = $(and ,$(UNUSED))\n",
            "EMPTY :=\nVALUE := $(and $(EMPTY),$(UNUSED))\n",
            "EMPTY :=\nVALUE = $(and $(EMPTY),$(UNUSED))\n",
        ):
            with self.subTest(assignment=assignment):
                self.add("Makefile", (
                    "UNUSED = $(eval HIDDEN ?= secret)$(error unused body)$(shell touch marker)\n"
                    + assignment + "export VISIBLE = $(VALUE)\n"
                    "all:\n\t@printf '%s\\n' '$(VALUE)' '$(and ,$(UNUSED))' \"$$VISIBLE\"\n"
                ))
                self.assertEqual(self.ordinary(), b"\n\n\n")
                result = self.observe()["all"]
                self.assertEqual(result["variable_census"]["defaults"], [])
                record = result["record"]["variants"][0]["record"]
                self.assertEqual(record["native_dispatches"][0]["environment"]["VISIBLE"], "")
                self.assertNotIn("UNUSED", record["domains"])
                self.assertFalse((self.root / "marker").exists())
        self.add("Makefile", (
            "WORDS := b.o a.c b.o a.c\nFILTER := $(filter-out %.o,$(WORDS))\n"
            "EMPTY_FILTER := $(filter-out ,$(WORDS))\nSUBSTRING := $(findstring src/msg_data.c,src/msg_data.cpp)\n"
            "SPACE := $(subst X, ,X)\n"
            "all:\n\t@printf '%s\\n' '$(FILTER)' '$(EMPTY_FILTER)' '$(SUBSTRING)'\n"
        ))
        expected = ["a.c a.c", "b.o a.c b.o a.c", "src/msg_data.c"]
        self.assertEqual(self.ordinary().decode().splitlines(), expected)
        with self.session() as session:
            native = session.make("all", definitions=("FILTER", "EMPTY_FILTER", "SUBSTRING"))
        self.assertEqual([native.semantics["definitions"]["global"][name]["value"]
                          for name in ("FILTER", "EMPTY_FILTER", "SUBSTRING")], expected)
        self.observe()
        self.add("Makefile", (
            "EMPTY :=\nSPACE := $(EMPTY) $(EMPTY)\n"
            "A := $(and $(SPACE),yes)\nB := $(and yes,$(SPACE))\nC := $(and yes,  final  )\n"
            "all:\n\t@printf '%s\\n' '$(value A)' '$(value B)' '$(value C)'\n"
        ))
        self.assertEqual(self.ordinary(), b"yes\n \nfinal\n")
        with self.session() as session:
            native = session.make("all", definitions=("A", "B", "C"))
        self.assertEqual([native.semantics["definitions"]["global"][name]["value"] for name in ("A", "B", "C")],
                         ["yes", " ", "final"])
        self.observe()

    def test_original_recursive_wildcards_capture_at_use_not_default_definition(self):
        self.original_input_witness()
        self.add("first/a.c", "first\n")
        self.add("second/b.c", "second\n")
        source = (
            "DIRECTORY := first\nFILES ?= $(wildcard $(DIRECTORY)/*.c)\n"
            "FIRST := $(FILES)\nDIRECTORY := second\nSECOND := $(FILES)\n"
            "all:\n\t@printf '%s\\n' '$(value FIRST)' '$(value SECOND)'\n"
        )
        self.add("Makefile", source)
        usage, _, records = self.observed_source_census()
        self.assertEqual(records["FIRST"]["value"], "first/a.c")
        self.assertEqual(records["SECOND"]["value"], "second/b.c")
        self.assertIn("FILES", usage["defaults"])
        for origin, value in (("command-line", ""), ("environment", ""), ("command-line", "first/a.c")):
            with self.subTest(origin=origin, value=value):
                usage, _, records = self.observed_source_census(assignments=((origin, "FILES", value),))
                self.assertEqual(records["FIRST"]["value"], value)
                self.assertEqual(records["SECOND"]["value"], value)
                self.assertIn("FILES", usage["defaults"])

    def test_original_lazy_read_constants_cannot_prune_other_scopes_or_earlier_reads(self):
        self.original_input_witness()
        self.add("Makefile", (
            "EMPTY :=\nVALUE = $(and $(EMPTY),$(UNUSED))\nUNUSED = visible\n"
            "all: EMPTY = nonempty\nall:\n\t@printf '%s\\n' '$(VALUE)'\n"
        ))
        self.assertEqual(self.ordinary(), b"visible\n")
        record = self.observe()["all"]["record"]["variants"][0]["record"]
        self.assertEqual(record["files"][0]["variables"]["VALUE"]["value"], "visible")
        self.add("Makefile", (
            "EMPTY :=\nVALUE = $(and $(EMPTY),$(UNUSED))\nUNUSED = visible\n"
            "all:\n\t$(eval EMPTY := nonempty)@printf '%s\\n' '$(VALUE)'\n"
        ))
        self.assertEqual(self.ordinary(), b"visible\n")
        with self.assertRaises(MakeProbeError):
            self.observe()
        source = (
            "VALUE = $(and $(EMPTY),$(UNUSED))\nUNUSED = present\n"
            "all: $(VALUE)\nEMPTY :=\nall: ;\npresent: ;\n"
        )
        self.add("Makefile", source)
        state = (("environment", "EMPTY", "nonempty"),)
        self.ordinary(environment={"EMPTY": "nonempty"})
        with self.session() as session:
            native = session.make("all", assignments=state, variables=("MAKEFILE_LIST", "MAKE_RESTARTS"))
            sources = graph_probe._loaded_sources(session, native, primary_source="Makefile")
            units, inputs, scoped = graph_probe._prepare_rule_templates(
                session, "all", state, None, native, sources, primary_source="Makefile",
            )
            usage = source_census(
                sources, reference_units=units, source_assignments=state, source_target="all",
                template_graph_inputs=inputs, template_scoped=scoped, budget=session.budget,
            )
        self.assertEqual(native.semantics["files"][0]["prerequisites"], [{"name": "present", "order_only": False}])
        self.assertIn("UNUSED", usage["execution_dependencies"]["VALUE"])

    def test_original_derived_scopes_keep_target_local_append_and_late_lookup(self):
        for lhs in ("one two", "$(TARGETS)"):
            self.add("Makefile", (
                "TARGETS := $(addprefix ,one two)\nCFLAGS := global\nFLAGS := first\n"
                + lhs + ": CFLAGS += $(FLAGS)\nFLAGS := later\n"
                "all: one two\none two:\n"
                "\t@printf '%s\\n' '$(CFLAGS)' '$(flavor CFLAGS)' '$(value CFLAGS)'\n"
            ))
            expected = ["global later", "recursive", "$(FLAGS)"] * 2
            self.assertEqual(self.ordinary().decode().splitlines(), expected)
            with self.session() as session:
                native = session.make("all", variables=("CFLAGS",), definitions=("FLAGS",))
                raw = session.make("all", definitions=("CFLAGS",))
            files = {item["target"]: item for item in native.semantics["files"]}
            for name in ("one", "two"):
                self.assertEqual(files[name]["variables"]["CFLAGS"]["value"], "global later")
                self.assertEqual(files[name]["variables"]["CFLAGS"]["flavor"], "recursive")
                local = next(item for item in raw.semantics["definitions"]["files"] if item["target"] == name)
                self.assertEqual(local["variables"]["CFLAGS"],
                                 {"origin": "file", "flavor": "recursive", "value": "$(FLAGS)"})
            self.observe()

    def read_closure_census(self, names):
        from scripts.validation_ownership.graph_commands import MakeCommands
        with self.session() as session:
            commands = MakeCommands(session, {})
            native = session.make(
                "all", variables=("MAKEFILE_LIST", "MAKE_RESTARTS"), definitions=names, commands=commands,
            )
            sources = graph_probe._loaded_sources(session, native, primary_source="Makefile")
            units, inputs, scoped = graph_probe._prepare_rule_templates(
                session, "all", (), commands, native, sources, primary_source="Makefile",
            )
            usage = source_census(
                sources, reference_units=units, source_target="all",
                template_graph_inputs=inputs, template_scoped=scoped, budget=session.budget,
            )
        self.assertIsNone(session.base)
        self.assertFalse(session.budget.children)
        self.assertFalse(session.budget.producer_waiters)
        return usage, native

    def reject_unsealed_read_default(self):
        with self.session() as session:
            with self.assertRaises(MakeProbeError) as rejected:
                run_probe(session.loader, {"all"}, {}, {}, session=session)
            accounting = (session.budget.runs, session.budget.states)
        self.assertIsNone(session.base)
        self.assertFalse(session.budget.children)
        self.assertFalse(session.budget.producer_waiters)
        return {"error": str(rejected.exception), "runs_states": accounting}

    def test_original_wildcard_logical_entries_keep_hidden_default_obligations(self):
        self.original_input_witness()
        self.last_soundness_cases = []
        matcher = make_probe._star_name

        def without_logical_entries(pattern, name):
            return False if name in {b".", b".."} else matcher(pattern, name)

        for name, directory, prelude, expression in (
            ("MATCH", "src", "", "$(wildcard src/.*)"),
            ("SELECTED", "assets", "DIRECTORY := assets\nPATTERN = ${DIRECTORY}/.*\n",
             "${wildcard ${PATTERN}}"),
            ("MATCH", "src", "PATTERN := src/.*\nALIAS := $(PATTERN)\n", "$(wildcard $(ALIAS))"),
        ):
            with self.subTest(name=name, directory=directory, expression=expression):
                self.add(directory + "/ordinary.c", "admitted source\n")
                self.add("Makefile", (
                    prelude + name + " := " + expression + "\n"
                    "ifneq ($(" + name + "),)\nHIDDEN ?= secret\nendif\n"
                    "all:\n\t@printf '%s\\n' '$(" + name + ")'\n"
                ))
                expected = directory + "/. " + directory + "/.."
                self.assertEqual(self.ordinary(), (expected + "\n").encode())
                usage, native = self.read_closure_census(("HIDDEN", name))
                records = native.semantics["definitions"]["global"]
                self.assertEqual(records[name], {"origin": "file", "flavor": "simple", "value": expected})
                self.assertEqual(records["HIDDEN"], {"origin": "file", "flavor": "recursive", "value": "secret"})
                self.assertIn("HIDDEN", usage["defaults"])
                fixed = self.reject_unsealed_read_default()
                with patch.object(make_probe, "_star_name", without_logical_entries):
                    preimage = self.observe()["all"]
                    self.assertEqual(preimage["variable_census"]["defaults"], [])
                    removed_accounting = self.last_accounting
                self.reject_unsealed_read_default()
                self.last_soundness_cases.append({
                    "expression": expression, "native": records, "fixed": fixed,
                    "removal_defaults": preimage["variable_census"]["defaults"],
                    "removal_runs_states": removed_accounting,
                })

    def test_original_wildcard_logical_entry_boundary_matches_gnu(self):
        self.original_input_witness()
        self.add("src/a.c", "ordinary\n")
        patterns = {
            "DOT": "src/.*", "DOTS": "src/..*", "LITERAL_DOT": "src/.",
            "NORMAL": "src/*.c", "HIDDEN_NAMES": "src/.hidden*", "DOT_SUFFIX": "src/.*c",
        }
        for hidden in (False, True):
            with self.subTest(hidden=hidden):
                if hidden:
                    self.add("src/.hidden.c", "hidden\n")
                expected_hidden = "src/.hidden.c" if hidden else ""
                expected = {
                    "DOT": "src/. src/.." + (" src/.hidden.c" if hidden else ""),
                    "DOTS": "src/..", "LITERAL_DOT": "src/.", "NORMAL": "src/a.c",
                    "HIDDEN_NAMES": expected_hidden, "DOT_SUFFIX": expected_hidden,
                }
                self.add("Makefile", (
                    "".join(name + " := $(wildcard " + pattern + ")\n" for name, pattern in patterns.items())
                    + "ifneq ($(DOT),)\nHIDDEN ?= secret\nendif\nall:\n\t@printf '%s\\n' "
                    + " ".join("'$(" + name + ")'" for name in patterns) + "\n"
                ))
                self.assertEqual(self.ordinary(), ("\n".join(expected.values()) + "\n").encode())
                with self.session() as session:
                    native = session.make(
                        "all", variables=("MAKEFILE_LIST", "MAKE_RESTARTS"), definitions=(*patterns, "HIDDEN"),
                    )
                    token = session._original_namespace(native, target="all", makefile="Makefile")
                    records = native.semantics["definitions"]["global"]
                    for name, value in expected.items():
                        self.assertEqual(records[name]["value"], value)
                    self.assertEqual(records["HIDDEN"],
                                     {"origin": "file", "flavor": "recursive", "value": "secret"})
                    for pattern in ("src/.*", "src/.**", "src/..*", "src/.", "src/..", "src/*.c src/.*"):
                        with self.subTest(pattern=pattern):
                            with self.assertRaises(make_probe._NamespaceUnavailable):
                                session._original_wildcard(token, pattern)
                    for name in ("NORMAL", "HIDDEN_NAMES", "DOT_SUFFIX"):
                        self.assertEqual(session._original_wildcard(token, patterns[name]), expected[name])
                self.assertIsNone(session.base)
                self.assertFalse(session.budget.children)
                self.assertFalse(session.budget.producer_waiters)
                self.reject_unsealed_read_default()
                self.add("Makefile", (
                    "VALUE := $(wildcard src/*.c src/.hidden* src/.*c)\n"
                    "all:\n\t@printf '%s\\n' '$(VALUE)'\n"
                ))
                safe = "src/a.c" + (" src/.hidden.c src/.hidden.c" if hidden else "")
                self.assertEqual(self.ordinary(), (safe + "\n").encode())
                self.assertEqual(self.observe()["all"]["variable_census"]["defaults"], [])

    def test_foreach_local_bindings_cannot_prune_deferred_hidden_defaults(self):
        self.original_input_witness()
        self.last_soundness_cases = []
        for label, empty, value, unused, prelude, expression, body, closed in (
            ("literal", "EMPTY", "VALUE", "UNUSED", "", "$(foreach EMPTY,nonempty,$(VALUE))",
             "$(and $(EMPTY),$(UNUSED))", True),
            ("renamed-braced", "VOID", "SELECTED", "DEFERRED", "", "${foreach VOID,nonempty,${SELECTED}}",
             "${and ${VOID},${DEFERRED}}", True),
            ("nested", "EMPTY", "VALUE", "UNUSED", "OUTER := global\n",
             "$(foreach OUTER,once,$(foreach EMPTY,nonempty,$(VALUE)))", "$(and $(EMPTY),$(UNUSED))", True),
            ("referenced", "EMPTY", "VALUE", "UNUSED", "BINDER := EMPTY\n",
             "$(foreach ${BINDER},nonempty,$(VALUE))", "$(and $(EMPTY),$(UNUSED))", False),
            ("computed", "EMPTY", "VALUE", "UNUSED", "PREFIX := EMP\nTAIL := TY\n",
             "$(foreach $(PREFIX)$(TAIL),nonempty,$(VALUE))", "$(and $(EMPTY),$(UNUSED))", False),
            ("short", "EMPTY", "VALUE", "UNUSED", "N := EMPTY\n",
             "$(foreach $N,nonempty,$(VALUE))", "$(and $(EMPTY),$(UNUSED))", False),
            ("called", "EMPTY", "VALUE", "UNUSED",
             "define BODY\n$(foreach EMPTY,nonempty,$(VALUE))\nendef\n",
             "$(call BODY)", "$(and $(EMPTY),$(UNUSED))", False),
            ("target-specific-preservation", "EMPTY", "VALUE", "UNUSED", "all: EMPTY = nonempty\n",
             "$(VALUE)", "$(and $(EMPTY),$(UNUSED))", True),
        ):
            with self.subTest(label=label):
                self.add("Makefile", (
                    "SAFE := unchanged\n" + empty + " :=\n" + value + " = " + body + "\n"
                    + unused + " = $(eval HIDDEN ?= secret)visible\n" + prelude
                    + "all:\n\t@printf '%s\\n' '" + expression + "'\n"
                ))
                self.assertEqual(self.ordinary(), b"visible\n")
                usage, native = self.read_closure_census(("HIDDEN", empty, value))
                records = native.semantics["definitions"]["global"]
                self.assertEqual(records["HIDDEN"], {"origin": "file", "flavor": "recursive", "value": "secret"})
                self.assertEqual(records[empty], {"origin": "file", "flavor": "simple", "value": ""})
                self.assertEqual(records[value], {"origin": "file", "flavor": "recursive", "value": body})
                self.assertIn("HIDDEN", usage["defaults"])
                self.assertIn(unused, usage["execution_dependencies"][value])
                self.assertNotIn(empty, usage["read_constants"])
                if closed:
                    self.assertEqual(usage["read_constants"]["SAFE"], "unchanged")
                else:
                    self.assertEqual(usage["read_constants"], {})
                fixed = self.reject_unsealed_read_default()
                row = {"case": label, "native": records, "fixed": fixed}
                if label in {"literal", "renamed-braced"}:
                    with patch.object(graph_probe, "_foreach_read_bindings", return_value=set()):
                        preimage = self.observe()["all"]
                        self.assertEqual(preimage["variable_census"]["defaults"], [])
                        row.update(removal_defaults=[], removal_runs_states=self.last_accounting)
                    self.reject_unsealed_read_default()
                self.last_soundness_cases.append(row)

    def test_foreach_read_scope_keeps_unshadowed_and_metadata_bodies_lazy(self):
        self.original_input_witness()
        for operator, expression in (
            ("=", "$(VALUE)"), ("=", "$(foreach ITEM,once,$(VALUE))"),
            (":=", "$(foreach EMPTY,once,$(VALUE))"),
        ):
            with self.subTest(operator=operator, expression=expression):
                self.add("Makefile", (
                    "EMPTY :=\nVALUE " + operator + " $(and $(EMPTY),$(UNUSED))\n"
                    "UNUSED = $(eval HIDDEN ?= secret)$(error unused body)$(shell touch marker)\n"
                    "export VISIBLE = $(VALUE)\nall:\n\t@printf '%s\\n' '" + expression + "' \"$$VISIBLE\"\n"
                ))
                self.assertEqual(self.ordinary(), b"\n\n")
                usage, native = self.read_closure_census(("HIDDEN", "UNUSED"))
                self.assertEqual(native.semantics["definitions"]["global"]["HIDDEN"],
                                 {"origin": "undefined", "flavor": "undefined", "value": ""})
                if operator == ":=":
                    self.assertNotIn("EMPTY", usage["read_constants"])
                else:
                    self.assertEqual(usage["read_constants"]["EMPTY"], "")
                self.assertNotIn("UNUSED", usage["execution_dependencies"]["VALUE"])
                self.assertEqual(usage["defaults"], set())
                result = self.observe()["all"]
                self.assertEqual(result["variable_census"]["defaults"], [])
                record = result["record"]["variants"][0]["record"]
                self.assertEqual(record["native_dispatches"][0]["environment"]["VISIBLE"], "")
                self.assertNotIn("UNUSED", record["domains"])
                self.assertFalse((self.root / "marker").exists())
        body = "$(foreach $(error unconsumed binder),once,$(shell touch marker))"
        self.add("Makefile", (
            "BODY = " + body + "\nall:\n\t@printf '%s\\n' '$(origin BODY)' '$(flavor BODY)'\n"
        ))
        self.assertEqual(self.ordinary(), b"file\nrecursive\n")
        usage, native = self.read_closure_census(("BODY",))
        self.assertEqual(native.semantics["definitions"]["global"]["BODY"]["value"], body)
        self.assertEqual(usage["read_constants"], {})
        self.observe()
        self.assertFalse((self.root / "marker").exists())
        for expression, expected in (
            ("$(foreach EMPTY,word,$(foreach OTHER,word,$(VALUE)))", {"EMPTY", "OTHER"}),
            ("$$(foreach EMPTY,word,$$(VALUE))", {"EMPTY"}),
            ("${foreach ${BINDER},word,${VALUE}}", None),
            ("$(foreach $(error unconsumed),word,$(VALUE))", None),
            ("$(foreach EMPTY,word", None),
        ):
            self.assertEqual(graph_probe._foreach_read_bindings(expression), expected)
        for expression, expected in (
            ("printf '$$)'", set()),
            ("$$(foreach EMPTY,word,$$(VALUE))", set()),
            ("$$$(foreach EMPTY,word,$(VALUE))", {"EMPTY"}),
            ("'# ${foreach EMPTY,word,${VALUE}}'", {"EMPTY"}),
            ("$(eval VALUE := $$(foreach EMPTY,word,$$(VALUE)))", {"EMPTY"}),
            ("$(call BODY,$$(foreach ${BINDER},word,$$(VALUE)))", None),
            ("printf '$)'", None),
        ):
            with self.subTest(ordinary_recipe=expression):
                self.assertEqual(
                    graph_probe._foreach_read_bindings(expression, context="ordinary-recipe"), expected,
                )

    def test_ignored_make_comments_do_not_disable_lazy_read_constants(self):
        self.original_input_witness()
        for comment, empty, target in (
            ("# literal dollar $\n", "EMPTY :=\n", "all:\n"),
            ("", "EMPTY := # literal dollar $\n", "all:\n"),
            ("", "EMPTY :=\n", "all: # literal dollar $\n"),
            ("# $(foreach $(error unused),once,$(VALUE))\n", "EMPTY :=\n", "all:\n"),
            ("# $(call unused) $(eval EMPTY := changed)\n", "EMPTY :=\n", "all:\n"),
        ):
            with self.subTest(comment=comment, empty=empty, target=target):
                self.add("Makefile", (
                    comment + empty + "VALUE = $(and $(EMPTY),$(UNUSED))\n"
                    "UNUSED = $(error unreachable)$(shell touch marker)\n"
                    + target + "\t@printf '%s\\n' '$(VALUE)'\n"
                ))
                self.assertEqual(self.ordinary(), b"\n")
                usage, native = self.read_closure_census(("VALUE", "UNUSED", "HIDDEN"))
                result = self.observe()["all"]
                self.assertEqual(usage["read_constants"]["EMPTY"], "")
                self.assertNotIn("UNUSED", usage["execution_dependencies"]["VALUE"])
                self.assertEqual(result["variable_census"]["defaults"], [])
                self.assertEqual(native.semantics["definitions"]["global"]["UNUSED"]["value"],
                                 "$(error unreachable)$(shell touch marker)")
                self.assertFalse((self.root / "marker").exists())

    def test_make_hash_data_retains_local_binders_and_hidden_defaults(self):
        self.original_input_witness()
        local = "$(foreach EMPTY,nonempty,$(VALUE))"
        for label, extra, recipe in (
            ("recipe", "", "all:\n\t@printf '%s\\n' '# " + local + "'\n"),
            ("inline", "", "all: ; @printf '%s\\n' '# " + local + "'\n"),
            ("define", "define BODY\n# " + local + "\nendef\n",
             "all:\n\t@printf '%s\\n' '$(BODY)'\n"),
            ("escaped", "BODY = \\# " + local + "\n",
             "all:\n\t@printf '%s\\n' '$(BODY)'\n"),
        ):
            with self.subTest(role=label):
                self.add("Makefile", (
                    "EMPTY :=\nVALUE = $(and $(EMPTY),$(UNUSED))\n"
                    "UNUSED = $(eval HIDDEN ?= secret)visible\n" + extra + recipe
                ))
                self.assertEqual(self.ordinary(), b"# visible\n")
                usage, native = self.read_closure_census(("HIDDEN",))
                self.assertEqual(native.semantics["definitions"]["global"]["HIDDEN"],
                                 {"origin": "file", "flavor": "recursive", "value": "secret"})
                self.assertIn("HIDDEN", usage["defaults"])
                self.assertNotIn("EMPTY", usage["read_constants"])
                self.reject_unsealed_read_default()

    def test_foreach_parse_time_scope_cannot_hide_posix_and_later_defaults(self):
        self.original_input_witness()
        local = "$(foreach EMPTY,nonempty,$(VALUE))"
        for label, prelude, trigger, operand in (
            ("literal", "", local, "$(EMPTY)"),
            ("nested-braced", "", "${foreach OUTER,once,${foreach EMPTY,nonempty,${VALUE}}}", "$(EMPTY)"),
            ("referenced-binder", "BINDER := EMPTY\n", "$(foreach $(BINDER),nonempty,$(VALUE))", "$(EMPTY)"),
            ("escaped-hash", "", "\\# " + local, "$(EMPTY)"),
            ("define-body", "define BODY\n# " + local + "\nendef\n", "$(BODY)", "$(EMPTY)"),
            ("local-metadata", "", local, "$(value EMPTY)"),
            ("shared-binding", "", "$(VALUE)" + local + "$(VALUE)", "$(EMPTY)"),
            ("substitution-reference", "BASE := unchanged\n", "$(BASE:" + local + "=x)", "$(EMPTY)"),
        ):
            with self.subTest(role=label):
                self.add("Makefile", (
                    "EMPTY :=\nVALUE = $(and " + operand + ",$(UNUSED))\n"
                    "UNUSED = $(eval .POSIX:)\n" + prelude + "TRIGGER := " + trigger + "\n"
                    "LATE = first \\\n second\nifeq ($(LATE),first  second)\nHIDDEN ?= secret\nendif\nall: ;\n"
                ))
                self.ordinary()
                with self.session() as session:
                    native = session.make("all", definitions=("EMPTY", "LATE", "HIDDEN"))
                records = native.semantics["definitions"]["global"]
                self.assertEqual(records["EMPTY"], {"origin": "file", "flavor": "simple", "value": ""})
                self.assertEqual(records["LATE"],
                                 {"origin": "file", "flavor": "recursive", "value": "first  second"})
                self.assertEqual(records["HIDDEN"],
                                 {"origin": "file", "flavor": "recursive", "value": "secret"})
                self.reject_unsealed_read_default()
                if label in {"literal", "local-metadata"}:
                    with patch.object(
                        _MakeSourceMode, "effect_initializer_value",
                        lambda mode, value, local: mode.exact_initializer_value(value),
                    ):
                        self.assertEqual(self.observe()["all"]["variable_census"]["defaults"], [])
                    self.reject_unsealed_read_default()

    def test_foreach_parse_time_scope_preserves_global_and_unshadowed_laziness(self):
        self.original_input_witness()
        for operator, trigger in (
            ("=", "$(VALUE)"),
            ("=", "$(foreach ITEM,once,$(VALUE))"),
            (":=", "$(foreach EMPTY,nonempty,$(VALUE))"),
            ("=", "$(foreach EMPTY,,$(VALUE))"),
        ):
            with self.subTest(operator=operator, trigger=trigger):
                self.add("Makefile", (
                    "EMPTY :=\nUNUSED = $(eval .POSIX:)\n"
                    "VALUE " + operator + " $(and $(EMPTY),$(UNUSED))\nTRIGGER := " + trigger + "\n"
                    "LATE = first \\\n second\nifeq ($(LATE),first  second)\nHIDDEN ?= secret\nendif\nall: ;\n"
                ))
                self.ordinary()
                with self.session() as session:
                    native = session.make("all", definitions=("LATE", "HIDDEN"))
                records = native.semantics["definitions"]["global"]
                self.assertEqual(records["LATE"],
                                 {"origin": "file", "flavor": "recursive", "value": "first second"})
                self.assertEqual(records["HIDDEN"],
                                 {"origin": "undefined", "flavor": "undefined", "value": ""})
                self.assertEqual(self.observe()["all"]["variable_census"]["defaults"], [])

    def test_foreach_effect_scope_keeps_operand_effects_and_local_metadata_separate(self):
        for definitions, expression, expected in (
            ({"EMPTY": "", "VALUE": "$(and $(EMPTY),$(UNUSED))", "UNUSED": "$(eval .POSIX:)"},
             "$(foreach EMPTY,nonempty,$(VALUE))", True),
            ({"EMPTY": "", "VALUE": "$(and $(value EMPTY),$(UNUSED))", "UNUSED": "$(eval .POSIX:)"},
             "$(foreach EMPTY,nonempty,$(VALUE))", True),
            ({"ITEM": "$(eval .POSIX:)"}, "$(foreach ITEM,once,$(ITEM))", False),
            ({"ITEM": "$(eval .POSIX:)"}, "$(foreach ITEM,$(ITEM),constant)", True),
            ({"ITEM": "$(eval .POSIX:)LOCAL"}, "$(foreach $(ITEM),once,constant)", True),
            ({"UNUSED": "$(eval .POSIX:)"}, "$(foreach ITEM,,$(UNUSED))", False),
            ({"UNUSED": "$(eval .POSIX:)"}, "$(value UNUSED)", False),
            ({"EMPTY": "", "SELECTOR": "EMPTY", "UNUSED": "$(eval .POSIX:)"},
             "$(foreach SELECTOR,UNUSED,$($(SELECTOR)))", True),
        ):
            with self.subTest(expression=expression):
                self.assertEqual(_MakeSourceMode(definitions=definitions).effectful(expression), expected)

    def test_original_filter_patterns_keep_c_whitespace_default_obligations(self):
        self.original_input_witness()
        self.last_soundness_cases = []
        parser = graph_probe._original_filter_patterns

        def unicode_split_preimage(value, budget):
            return parser(" ".join(value.split()), budget)

        for label, separator, name, referenced, recipe_read in (
            ("no-recipe-primary", chr(0xA0), "VALUE", False, False),
            ("nbsp", chr(0xA0), "MATCH", False, True),
            ("renamed-reference", chr(0xA0), "SELECTED", True, True),
            ("em-space", chr(0x2003), "MATCH", False, True),
        ):
            with self.subTest(label=label):
                pattern = "a" + separator + "b"
                prelude = "PATTERN := " + pattern + "\n" if referenced else ""
                expression = "${filter-out ${PATTERN},a b}" if referenced else "$(filter-out " + pattern + ",a b)"
                source = (
                    prelude + name + " := " + expression + "\n"
                    "ifneq ($(" + name + "),)\nHIDDEN ?= secret\nendif\n"
                    + ("all:\n\t@printf '%s\\n' '$(" + name + ")'\n" if recipe_read else "all: ;\n")
                )
                self.add("Makefile", source)
                ordinary = self.ordinary()
                if recipe_read:
                    self.assertEqual(ordinary, b"a b\n")
                usage, native = self.read_closure_census(("HIDDEN", name))
                if not recipe_read:
                    target, = [item for item in native.semantics["files"] if item["target"] == "all"]
                    self.assertEqual(target["recipe"].strip(graph_probe.MAKE_SPACE), "")
                records = native.semantics["definitions"]["global"]
                self.assertEqual(records[name], {"origin": "file", "flavor": "simple", "value": "a b"})
                self.assertEqual(records["HIDDEN"], {"origin": "file", "flavor": "recursive", "value": "secret"})
                self.assertIsNone(parser(pattern, None))
                self.assertIn("HIDDEN", usage["defaults"])
                fixed = self.reject_unsealed_read_default()
                with patch.object(graph_probe, "_original_filter_patterns", unicode_split_preimage):
                    preimage = self.observe()["all"]
                    self.assertEqual(preimage["variable_census"]["defaults"], [])
                    removed_accounting = self.last_accounting
                self.reject_unsealed_read_default()
                self.last_soundness_cases.append({
                    "case": label, "source": source, "ordinary_stdout": ordinary.decode(),
                    "native": records, "fixed": fixed,
                    "removal_defaults": [], "removal_runs_states": removed_accounting,
                })
        self.add("Makefile", (
            "VALUE := $(filter-out a b,a b)\n"
            "ifneq ($(VALUE),)\nHIDDEN ?= secret\nendif\nall: ;\n"
        ))
        ordinary = self.ordinary()
        usage, native = self.read_closure_census(("HIDDEN", "VALUE"))
        target, = [item for item in native.semantics["files"] if item["target"] == "all"]
        self.assertEqual(target["recipe"].strip(graph_probe.MAKE_SPACE), "")
        records = native.semantics["definitions"]["global"]
        self.assertEqual(records["VALUE"], {"origin": "file", "flavor": "simple", "value": ""})
        self.assertEqual(records["HIDDEN"], {"origin": "undefined", "flavor": "undefined", "value": ""})
        self.assertEqual(usage["defaults"], set())
        positive = self.observe()["all"]
        self.assertEqual(positive["variable_census"]["defaults"], [])
        self.last_soundness_cases.append({
            "case": "no-recipe-ascii-control", "ordinary_stdout": ordinary.decode(), "native": records,
            "plan_defaults": [], "runs_states": self.last_accounting,
        })

    def test_original_c_word_boundaries_keep_exact_values_and_lazy_reads(self):
        self.original_input_witness()
        nbsp = chr(0xA0)
        cases = {
            "ASCII": ("$(filter-out a\tb,a b a b)", ""),
            "EMPTY_FILTER": ("$(filter-out , a\tb a )", "a b a"),
            "WORDS": ("$(filter-out a,a" + nbsp + "b a b)", "a" + nbsp + "b b"),
            "STRIPPED": ("$(strip \t a" + nbsp + "b \t c \t)", "a" + nbsp + "b c"),
            "SUBSTRING": ("$(findstring " + nbsp + ",a" + nbsp + "b)", nbsp),
            "BOOLEAN": ("$(and " + nbsp + ",visible)", "visible"),
            "FINAL": ("$(and yes," + nbsp + ")", nbsp),
        }
        source = "".join(name + " := " + expression + "\n" for name, (expression, _) in cases.items())
        source += "all:\n\t@printf '%s\\n' " + " ".join("'$(" + name + ")'" for name in cases) + "\n"
        self.add("Makefile", source)
        self.assertEqual(self.ordinary(), ("\n".join(value for _, value in cases.values()) + "\n").encode())
        usage, native = self.read_closure_census(tuple(cases))
        mode = _MakeSourceMode(template_mode=lambda *args: False)
        list(make_source_units(source, mode=mode))
        for name, (expression, value) in cases.items():
            self.assertEqual(native.semantics["definitions"]["global"][name]["value"], value)
            self.assertEqual(mode.exact_initializer_value(expression), value)
            self.assertEqual(mode.exact_reference(name), value)
        self.assertEqual(usage["defaults"], set())
        self.assertEqual(self.observe()["all"]["variable_census"]["defaults"], [])
        for expression in ("$(and " + nbsp + ",$(UNUSED))", "${and ${NONBREAK},${UNUSED}}"):
            with self.subTest(expression=expression):
                self.add("Makefile", (
                    "NONBREAK := " + nbsp + "\nVALUE = " + expression + "\n"
                    "UNUSED = $(eval HIDDEN ?= secret)visible\nall:\n\t@printf '%s\\n' '$(VALUE)'\n"
                ))
                self.assertEqual(self.ordinary(), b"visible\n")
                usage, native = self.read_closure_census(("HIDDEN", "NONBREAK", "VALUE"))
                records = native.semantics["definitions"]["global"]
                self.assertEqual(records["NONBREAK"]["value"], nbsp)
                self.assertEqual(records["HIDDEN"], {"origin": "file", "flavor": "recursive", "value": "secret"})
                self.assertEqual(usage["read_constants"]["NONBREAK"], nbsp)
                self.assertIn("UNUSED", usage["execution_dependencies"]["VALUE"])
                self.assertIn("HIDDEN", usage["defaults"])
                self.reject_unsealed_read_default()


if __name__ == "__main__":
    unittest.main()
