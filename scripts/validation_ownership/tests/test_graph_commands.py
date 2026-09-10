from __future__ import annotations

import hashlib
import json
from dataclasses import replace
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import unittest
from unittest import mock

from scripts.bash_parser import parse_bash_script_commands
from scripts.validation_ownership.authority import (
    AuthorityLoader, ENVIRONMENT, GitTreeEntries, GitTreeEntry, encoded, git_tree_entries,
)
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.graph_commands import (
    CODE_PREFIXES, FIND_DIRECTORY_BODY, ROOT_RUNTIME_FILES, MakeCommands,
    asset_discovery_command, python_command,
)
from scripts.validation_ownership import graph_report
from scripts.validation_ownership import make_probe
from scripts.validation_ownership.make_probe import Command, ProbeSession
from scripts.validation_ownership.graph_probe import run_probe


ROOT = Path(__file__).resolve().parents[3]


class GraphCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "build/test-artifacts/ownership-domain-tests" / secrets.token_hex(12)
        self.root = self.directory / "repo"
        self.root.mkdir(parents=True)
        self.entries = {}
        self.contracts = {
            entry["expression"]: entry
            for entry in json.loads((ROOT / ".github/validation-ownership-make-dynamics.json").read_bytes())["contracts"]
        }
        for path in (ROOT / "tools/scaninc").iterdir():
            if path.suffix in {".h", ".cpp"}:
                self.add(path.relative_to(ROOT).as_posix(), path.read_bytes())
        self.add("scripts/validation_ownership/scaninc_sources.cpp",
                 (ROOT / "scripts/validation_ownership/scaninc_sources.cpp").read_bytes())

    def tearDown(self):
        shutil.rmtree(self.directory)

    def add(self, name, data, mode="100644"):
        if isinstance(data, str):
            data = data.encode()
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o755 if mode == "100755" else 0o644)
        self.entries[name] = GitTreeEntry(name, mode, "blob", hashlib.sha1(data).hexdigest())

    def session(self, *, runtime_files=()):
        budget = ProbeBudget()
        loader = AuthorityLoader(self.root, GitTreeEntries(self.entries, budget=budget), budget=budget)
        return ProbeSession(
            loader, scratch_root=self.root / "build/scratch", budget=budget,
            runtime_files=runtime_files,
        )

    def capture_loader(self, budget):
        def git(*arguments):
            return subprocess.run(
                ["/usr/bin/git", "-C", str(self.root), *arguments],
                env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                capture_output=True, check=True, timeout=15,
            ).stdout
        git("init", "--quiet")
        git("add", "-A", "--", ".")
        revision = git("write-tree").decode().strip()
        entries = git_tree_entries(self.root, revision, budget=budget)
        self.assertEqual(set(entries), set(self.entries))
        return AuthorityLoader(self.root, entries, revision, budget=budget)

    def capture_complete_loader(self, budget):
        def git(*arguments):
            return subprocess.run(
                ["/usr/bin/git", "-C", str(self.root), *arguments],
                env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                capture_output=True, check=True, timeout=15,
            ).stdout
        git("init", "--quiet")
        git("add", "-A", "--", ".")
        revision = git("write-tree").decode().strip()
        return graph_report.capture(self.root, revision, budget)

    def ordinary_scaninc(self, source):
        output = self.directory / "scaninc"
        environment = {**ENVIRONMENT, "TMPDIR": str(self.directory)}
        built = subprocess.run(
            ["/usr/bin/g++", "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror",
             *[str(self.root / path) for path in sorted(self.entries)
               if path.startswith("tools/scaninc/") and path.endswith(".cpp")],
             "-o", str(output)],
            cwd=self.root, env=environment, capture_output=True, timeout=30,
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        return subprocess.run(
            [str(output), "-I", "include", "-I", "", source],
            cwd=self.root, env=environment, capture_output=True, check=True, timeout=10,
        ).stdout

    def add_repo_file(self, path):
        source = ROOT / path
        mode = "100755" if source.stat().st_mode & 0o111 else "100644"
        self.add(path, source.read_bytes(), mode)

    def add_generated_dependency_fixture(self):
        if (ROOT / "scripts/__init__.py").is_file():
            self.add_repo_file("scripts/__init__.py")
        for path in (ROOT / "scripts" / "generated_data").rglob("*.py"):
            if "tests" not in path.relative_to(ROOT).parts:
                self.add_repo_file(path.relative_to(ROOT).as_posix())
        for name in ("scripts/assets/__init__.py", "scripts/assets/tmx.py"):
            if (ROOT / name).is_file():
                self.add_repo_file(name)

        fixture_root = ROOT / "scripts" / "generated_data" / "tests" / "fixtures"
        chapterbundle = fixture_root / "chapterbundle"
        self.add(
            "include/constants/characters.h",
            "#define CHARACTER_EIRIKA 1\n",
        )
        self.add(
            "include/constants/event-flags.h",
            "#define EVFLAG_TMP(n) (n)\n",
        )
        self.add("include/bmunit.h", "#define FACTION_ID_BLUE 0\n")
        self.add_repo_file("scripts/generated_data/tests/fixtures/chapterbundle/chapters.h")
        self.add(
            "include/constants/chapters.h",
            (chapterbundle / "chapters.h").read_bytes(),
        )
        self.add(
            "src/data/chapter_settings.json",
            (chapterbundle / "chapter_settings.json").read_bytes(),
        )
        self.add(
            "src/data/data_8B363C.c",
            (chapterbundle / "data_8B363C.c").read_bytes(),
        )
        self.add("assets/manifest.json", "{}\n")
        self.add("assets/tmx/Example.tmx", "<map width='1' height='1'/>\n")
        self.add("assets/tmx/placeholder.txt", "keep\n")
        self.add("graphics/map/layout/Example.json", "{}\n")

        for name in (
            "deps_units.json",
            "deps_shops.json",
            "deps_traps.json",
            "deps_eventscripts.json",
            "deps_eventlists.json",
            "deps_supports.json",
        ):
            self.add(
                "testdata/deps/" + name,
                (chapterbundle / name).read_bytes(),
            )
        self.add(
            "testdata/objectives/el_objectives.json",
            (chapterbundle / "deps_chapterobjectives.json").read_bytes(),
        )
        self.add(
            "testdata/strategies/el_strategies.json",
            (chapterbundle / "deps_autoplaystrategies.json").read_bytes(),
        )
        bundle = json.loads((chapterbundle / "valid.json").read_text(encoding="utf-8"))
        for table in bundle["tables"].values():
            table["source"] = "testdata/deps/" + Path(table["source"]).name
        bundle["supportOwners"]["source"] = "testdata/deps/deps_supports.json"
        self.add(
            "testdata/bundles/el_bundle.json",
            json.dumps(bundle, indent=2) + "\n",
        )

        cases = [
            {
                "name": "chapterobjectives",
                "arguments": ("testdata/objectives", "testdata/bundles"),
                "command": (
                    'python3 -m scripts.generated_data.chapterobjectives.deps \\\n'
                    '\t--source "testdata/objectives" \\\n'
                    '\t--bundle-source "testdata/bundles" \\\n'
                    '\t--make-target "build/generated/data/data_chapter_objectives.c" \\\n'
                    '\t--depfile "build/generated/data/chapterobjectives.inputs.mk"'
                ),
                "make_command": (
                    'python3 -m scripts.generated_data.chapterobjectives.deps --source '
                    '"testdata/objectives" --bundle-source "testdata/bundles" '
                    '--make-target "build/generated/data/data_chapter_objectives.c" '
                    '--depfile "build/generated/data/chapterobjectives.inputs.mk"'
                ),
                "output": "build/generated/data/chapterobjectives.inputs.mk",
                "target": "build/generated/data/data_chapter_objectives.c",
            },
            {
                "name": "autoplaystrategies",
                "arguments": (
                    "testdata/strategies",
                    "testdata/objectives",
                    "testdata/bundles",
                ),
                "command": (
                    'python3 -m scripts.generated_data.autoplaystrategies.deps \\\n'
                    '\t--source "testdata/strategies" \\\n'
                    '\t--objectives-source "testdata/objectives" \\\n'
                    '\t--bundle-source "testdata/bundles" \\\n'
                    '\t--make-target "build/generated/data/data_autoplay_strategies.c" \\\n'
                    '\t--depfile "build/generated/data/autoplaystrategies.inputs.mk"'
                ),
                "make_command": (
                    'python3 -m scripts.generated_data.autoplaystrategies.deps --source '
                    '"testdata/strategies" --objectives-source '
                    '"testdata/objectives" --bundle-source "testdata/bundles" '
                    '--make-target "build/generated/data/data_autoplay_strategies.c" '
                    '--depfile "build/generated/data/autoplaystrategies.inputs.mk"'
                ),
                "output": "build/generated/data/autoplaystrategies.inputs.mk",
                "target": "build/generated/data/data_autoplay_strategies.c",
            },
            {
                "name": "eventlists",
                "arguments": ("testdata/strategies", "testdata/bundles"),
                "command": (
                    'python3 -m scripts.generated_data.eventlists.deps \\\n'
                    '\t--strategy-source "testdata/strategies" \\\n'
                    '\t--bundle-source "testdata/bundles" \\\n'
                    '\t--make-target "build/generated/data/.ch2-eventlists.validated" \\\n'
                    '\t--depfile "build/generated/data/eventlists.inputs.mk"'
                ),
                "make_command": (
                    'python3 -m scripts.generated_data.eventlists.deps --strategy-source '
                    '"testdata/strategies" --bundle-source "testdata/bundles" '
                    '--make-target "build/generated/data/.ch2-eventlists.validated" '
                    '--depfile "build/generated/data/eventlists.inputs.mk"'
                ),
                "output": "build/generated/data/eventlists.inputs.mk",
                "target": "build/generated/data/.ch2-eventlists.validated",
            },
        ]
        return cases

    def rewrite_generated_dependency_bundle(self, *, units_source):
        bundle_path = self.root / "testdata/bundles/el_bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundle["tables"]["units"]["source"] = units_source
        self.add(
            "testdata/bundles/el_bundle.json",
            json.dumps(bundle, indent=2) + "\n",
        )

    def normalize_repo_bytes(self, data):
        return data.replace(os.fsencode(str(self.root)), b"/repo")

    def test_real_voice_source_and_native_make_agree_with_ordinary_scanner(self):
        source = "sound/voicegroups/voicegroup038.s"
        included = "asm/macros/music_voice.inc"
        for path in (source, included):
            self.add(path, (ROOT / path).read_bytes())
        command = f'tools/scaninc/scaninc -I include -I "" {source}'
        self.add("Makefile", f"INPUTS := $(shell {command})\nall: $(INPUTS)\n\t@:\n")
        ordinary = self.ordinary_scaninc(source)
        self.assertEqual(ordinary, (included + "\n").encode())
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            registered = commands[command]
            result = probe.command(registered)
            self.assertEqual(result.stdout, ordinary)
            self.assertEqual(result.consumed, tuple(sorted((source, included))))
            observed = probe.make("all", variables=("INPUTS",), commands=commands)
            self.assertEqual(observed.semantics["domains"]["INPUTS"]["value"], included)
            self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                {"name": included, "order_only": False},
            ])
            self.assertEqual(len(observed.semantics["dynamic_commands"]), 1)
            self.assertTrue(observed.semantics["dynamic_commands"][0]["command"]["native_tool"])
            self.assertTrue(all(event["match"] == 0 for event in observed.events))
        self.assertIsNone(probe.base)
        self.assertFalse(probe.runtime_tools)
        self.assertFalse(probe.runtime_query_profiles)
        self.assertFalse(probe.budget.children)

    def test_bash_parser_matches_shell_continuations_and_rejects_multi_command_registration(self):
        script = (
            'python3 -c "import json,sys;print(json.dumps(sys.argv[1:]))" \\\n'
            '\talph\\\n'
            'a \\\n'
            '\t"double\\\n'
            'quoted" \'single\\\n'
            'quoted\' "slash\\\\keep"'
        )
        ordinary = subprocess.run(
            ["/bin/sh", "-c", script],
            cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        )
        parsed, = parse_bash_script_commands(script, "fixture")
        self.assertEqual(json.loads(ordinary.stdout), list(parsed[3:]))
        self.assertEqual(parsed[3:], (
            "alpha", "doublequoted", "single\\\nquoted", "slash\\keep",
        ))
        contract = {
            "multi-python": {
                "id": "multi-python",
                "command_regex": r"(?s)^python3.*$",
                "input_files": [],
                "input_variables": [],
                "automatic_inputs": [],
                "resolved_value": None,
                "owning_evidence_ids": ["owner"],
            },
        }
        with self.session() as probe:
            with self.assertRaisesRegex(
                MakeProbeError, "unsupported multi-command shell shape",
            ):
                MakeCommands(probe, contract)['python3 -c "print(1)"\npython3 -c "print(2)"']

    def test_standard_python_command_preserves_namespace_import_behavior(self):
        self.add(
            "scripts/generated_data/demo/helper.py",
            "VALUE = 'helper'\n",
        )
        self.add(
            "scripts/generated_data/demo/tool.py",
            "from .helper import VALUE\nRESULT = VALUE + ':ok'\n",
        )
        ordinary = subprocess.run(
            [
                "/usr/bin/python3", "-I", "-S", "-B", "-c",
                "import json,sys;sys.path.insert(0,'.');import scripts;"
                "from scripts.generated_data.demo import tool;"
                "print(json.dumps({'loader':type(scripts.__spec__.loader).__name__,"
                "'origin':scripts.__spec__.origin,'paths':list(scripts.__path__),"
                "'result':tool.RESULT},sort_keys=True))",
            ],
            cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        )
        expected = json.loads(ordinary.stdout)
        expected["paths"] = [path.replace(str(self.root), "/repo") for path in expected["paths"]]
        budget = ProbeBudget()
        loader = self.capture_complete_loader(budget)
        with ProbeSession(loader, scratch_root=self.root / "build/scratch", budget=budget) as probe:
            actual = probe.command(python_command(
                probe,
                "import json, scripts;"
                "from scripts.generated_data.demo import tool;"
                "print(json.dumps({'loader':type(scripts.__spec__.loader).__name__,"
                "'origin':scripts.__spec__.origin,'paths':list(scripts.__path__),"
                "'result':tool.RESULT},sort_keys=True))",
                code=(
                    "scripts/generated_data/demo/helper.py",
                    "scripts/generated_data/demo/tool.py",
                ),
            ))
            self.assertEqual(json.loads(actual.stdout), expected)
            self.assertEqual(
                set(actual.code_consumed),
                {
                    "scripts/generated_data/demo/helper.py",
                    "scripts/generated_data/demo/tool.py",
                },
            )
        self.assertFalse(budget.children)

    def test_scaninc_uses_real_parser_search_order_and_recursive_include_closure(self):
        self.add("data/root.s", '.include "leaf.inc"\n.include "missing.inc"\n.incbin "asset.bin"\n')
        self.add("include/leaf.inc", '.include "nested.inc"\n')
        self.add("include/nested.inc", '.incbin "nested.bin"\n')
        self.add("leaf.inc", '.incbin "wrong-root.bin"\n')
        self.add("data/leaf.inc", '.incbin "wrong-local.bin"\n')
        ordinary = self.ordinary_scaninc("data/root.s")
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            result = probe.command(commands.scaninc("data/root.s"))
            self.assertEqual(result.stdout, ordinary)
            self.assertEqual(result.consumed, (
                "data/root.s", "include/leaf.inc", "include/nested.inc",
            ))
            self.assertEqual(result.stdout.splitlines(), [
                b"asset.bin", b"include/leaf.inc", b"include/nested.inc", b"nested.bin",
            ])

    def test_real_banim_parent_includes_preserve_ordinary_search_spelling(self):
        source = "banim/banim_lorm_sp1_motion.s"
        includes = ("include/banim_sheet.inc", "include/banim_code.inc", "include/banim_code_frame.inc")
        for path in (source, *includes):
            self.add(path, (ROOT / path).read_bytes())
        ordinary = self.ordinary_scaninc(source)
        command = f'tools/scaninc/scaninc -I include -I "" {source}'
        self.add("Makefile", f"INPUTS := $(shell {command})\nall: ;\n")
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            output = probe.command(commands[command])
            self.assertEqual(output.stdout, ordinary)
            self.assertEqual(set(output.consumed), {source, *includes})
            observed = probe.make("all", variables=("INPUTS",), commands=commands)
            self.assertEqual(
                observed.semantics["domains"]["INPUTS"]["value"],
                " ".join(ordinary.decode().splitlines()),
            )
        self.assertFalse(probe.budget.children)

    def test_scaninc_does_not_collapse_an_absent_intermediate_directory(self):
        self.add("src/root.s", '.include "../include/leaf.inc"\n.include "missing/../leaf.inc"\n')
        self.add("include/leaf.inc", '.incbin "first.bin"\n')
        self.add("src/leaf.inc", '.incbin "second.bin"\n')
        self.add("src/missing/anchor", "directory member\n")
        ordinary = self.ordinary_scaninc("src/root.s")
        with self.session() as probe:
            output = probe.command(MakeCommands(probe, self.contracts).scaninc("src/root.s"))
            self.assertEqual(output.stdout, ordinary)
            self.assertIn(b"src/missing/../leaf.inc", output.stdout.splitlines())
            self.assertEqual(set(output.consumed), {"src/root.s", "include/leaf.inc", "src/leaf.inc"})
        self.assertFalse(probe.budget.children)

    def test_scaninc_rejects_escaping_and_unadmitted_sources(self):
        for content, expected in (
            ('.include "../outside.inc"\n', "canonical and repository-relative"),
            ('.include "link.inc"\n', "unadmitted source"),
        ):
            with self.subTest(content=content):
                self.add("root.s", content)
                self.add("link.inc", "untracked.inc", mode="120000")
                (self.root / "link.inc").unlink()
                (self.root / "link.inc").symlink_to("untracked.inc")
                with self.session() as probe:
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        MakeCommands(probe, self.contracts).scaninc("root.s")
        with self.session() as probe:
            with self.assertRaisesRegex(MakeProbeError, "resolves no regular inputs"):
                MakeCommands(probe, self.contracts).scaninc("untracked.s")

    def test_command_contract_mismatch_never_uses_resolved_value_as_output(self):
        self.add("Makefile", "all: ;\n")
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            with self.assertRaisesRegex(MakeProbeError, "exactly one sealed domain"):
                commands["printf fabricated"]
            for entry in commands.contracts:
                entry["resolved_value"] = "counterfeit"
            self.assertEqual(probe.command(commands["uname"]).stdout, b"Linux\n")

    def test_dependency_adapter_preserves_cc_order_output_closure_and_make_restart(self):
        self.add("src/input.c", '#include "config.h"\n#if ACTIVE\n#include <selected.h>\n#endif\n')
        self.add("include/config.h", "#define CONFIGURED 1\n")
        self.add("include/first/selected.h", '#include "deep.h"\n')
        self.add("include/first/deep.h", "#define SELECTED 1\n")
        self.add("include/second/selected.h", "#error wrong search order\n")
        command = (
            "mkdir -p .dep/src/ && cc -E -Iinclude/first \\\n"
            "\t-I include/second -iquote include -nostdinc -undef \\\n"
            "\t-DACTIVE=1 src/input.c -MM -MG -MT src/input.o > .dep/src/input.d"
        )
        make_command = (
            "mkdir -p .dep/src/ && cc -E -Iinclude/first -I include/second "
            "-iquote include -nostdinc -undef -DACTIVE=1 "
            "src/input.c -MM -MG -MT src/input.o > .dep/src/input.d"
        )
        tokens, = parse_bash_script_commands(command, "dependency fixture")
        arguments = tokens[4:-2]
        ordinary = subprocess.run(
            ["/usr/bin/cc", *arguments[1:]], cwd=self.root,
            env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        )
        self.add("archival_dependencies.mk", (
            "include .dep/src/input.d\n"
            ".dep/src/input.d: src/input.c\n\t" + make_command + "\n"
        ))
        self.add("Makefile", "include archival_dependencies.mk\nsrc/input.o: ;\n")
        with self.session() as probe:
            registration = MakeCommands(probe, self.contracts)[command]
            self.assertTrue(registration.dependency_only)
            self.assertEqual(registration.argv, ("/usr/bin/cc", *arguments[1:]))
            produced = probe.command(registration)
            self.assertIsNone(produced.artifact)
            self.assertEqual(produced.stdout, b"")
            self.assertEqual(produced.generated[0].data, ordinary.stdout)
            self.assertEqual(produced.consumed, ("src/input.c",))
            self.assertEqual(set(produced.code_consumed), {
                "include/config.h", "include/first/selected.h", "include/first/deep.h",
            })
            observed = probe.make(
                "src/input.o", variables=("MAKEFILE_LIST", "MAKE_RESTARTS"),
                commands={make_command: registration},
            )
            self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
            self.assertIn(
                "archival_dependencies.mk",
                observed.semantics["domains"]["MAKEFILE_LIST"]["value"].split(),
            )
            self.assertEqual(
                [entry["name"] for entry in observed.semantics["files"][0]["prerequisites"]],
                ["src/input.c", "include/config.h", "include/first/selected.h", "include/first/deep.h"],
            )
            dynamic, = observed.semantics["dynamic_commands"]
            self.assertTrue(dynamic["command"]["dependency_only"])
            self.assertEqual({item[0] for item in dynamic["command"]["inputs"]}, {
                "src/input.c", "include/config.h", "include/first/selected.h", "include/first/deep.h",
            })
            self.assertEqual(dynamic["generated_outputs"][0][0], ".dep/src/input.d")

    def test_dependency_adapter_rejects_directory_driver_and_forwards_invalid_flags(self):
        self.add("src/input.c", "int value;\n")
        command = (
            "mkdir -p .dep/src/ && cc -E -nostdinc -undef "
            "src/input.c -MM -MG -MT src/input.o > .dep/src/input.d"
        )
        for bad, expected in (
            (command.replace("mkdir -p .dep/src/", "mkdir -p .dep/other/"), "directory differs"),
            (command.replace("&& cc ", "&& clang "), "supported host C driver"),
        ):
            with self.subTest(command=bad), self.session() as probe:
                before = probe.budget.runs
                capsule_processes = probe.processes_used
                with self.assertRaisesRegex(MakeProbeError, expected):
                    MakeCommands(probe, self.contracts)[bad]
                self.assertEqual(probe.budget.runs, before + 1)
                self.assertEqual(probe.processes_used, capsule_processes)
        with self.session() as probe:
            bad = command.replace(" -undef ", " -undef -fplugin=evil.so ")
            registration = MakeCommands(probe, self.contracts).dependency(bad)
            self.assertIn("-fplugin=evil.so", registration.argv)
            before = probe.budget.runs
            with self.assertRaises(MakeProbeError):
                probe.command(registration)
            self.assertEqual(probe.budget.runs, before)

    def test_graph_uses_actual_published_bytes_without_preexecuting_the_producer(self):
        self.add("src/input.c", "int input;\n")
        self.add("static.mk", "all: ;\n")
        command = (
            "mkdir -p .dep/src/ && cc -E -nostdinc -undef "
            "src/input.c -MM -MG -MT src/input.o > .dep/src/input.d"
        )
        self.add("Makefile", (
            "include .dep/src/input.d\n"
            ".dep/src/input.d: src/input.c\n\t" + command + "\n"
            "src/input.o: ;\n"
        ))
        with self.session() as probe:
            outputs = []
            observations = []
            execute = probe.command
            make = probe.make

            def observe_command(registration):
                result = execute(registration)
                if registration.outputs:
                    outputs.append(result)
                return result

            def observe_make(*arguments, **options):
                result = make(*arguments, **options)
                observations.append(result)
                return result

            with mock.patch.object(probe, "command", new=observe_command):
                with mock.patch.object(probe, "make", new=observe_make):
                    result = run_probe(
                        probe.loader, {"src/input.o"}, {}, self.contracts, session=probe,
                    )
            self.assertEqual(len(outputs), 1)
            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0].generated, outputs[0].generated)
            self.assertEqual(outputs[0].generated[0].data, b"src/input.o: src/input.c\n")
            self.assertEqual(
                result["src/input.o"]["record"]["includes"], [".dep/src/input.d", "Makefile"],
            )
            self.assertFalse((probe.tree / ".dep/src/input.d").exists())
            self.assertEqual(probe.make("all", makefile="static.mk").generated, ())
        self.assertIsNone(probe.base)
        self.assertFalse(probe.runtime_tools)
        self.assertFalse(probe.runtime_query_profiles)
        self.assertFalse(probe.budget.children)

    def test_real_linker_discovery_uses_explicit_python_and_reaches_make(self):
        path = "scripts/arm_compressing_linker.py"
        self.add(path, (ROOT / path).read_bytes(), "100755")
        self.add("linker_script_banim.txt", (ROOT / "linker_script_banim.txt").read_bytes())
        contract = next(item for item in self.contracts.values()
                        if item["id"] == "banim-compressing-linker-inputs")
        expression = contract["expression"]
        self.add("Makefile", "PYTHON := python3\nINPUTS := " + expression
                 + "\nall:\n\t@printf '%s\\n' '$(INPUTS)'\n")
        ordinary = subprocess.run(
            ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
            cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        ).stdout
        self.assertGreater(len(ordinary), 0)
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            actual = probe.make("all", variables=("INPUTS",), commands=commands)
            self.assertEqual(actual.semantics["domains"]["INPUTS"]["value"],
                             ordinary.decode().strip())
            self.assertEqual(actual.stderr, b"")
            self.assertEqual(len(actual.semantics["dynamic_commands"]), 1)
            self.assertTrue(actual.events)
            self.assertTrue(all(event["match"] == 0 for event in actual.events))

    def test_registered_find_matches_real_find_with_nested_unicode_and_multiple_batches(self):
        descriptors = set(os.listdir("/proc/self/fd"))
        paths = [
            "texts/a.txt",
            "texts/empty/nonmatching.bin",
            "texts/nested/deep.txt",
            "texts/nested/ignored.md",
            "texts/nested/" + "x" * 240 + ".txt",
            "texts/日本語.txt",
            *(f"texts/many/{index:03d}.txt" for index in range(256)),
        ]
        for path in sorted(paths):
            self.add(path, path + "\n")
        command = 'find texts -type f -name "*.txt"'
        contract = next(
            item for item in self.contracts.values()
            if item["id"] == "legacy-text-source-discovery"
        )
        self.add("Makefile", (
            "TEXT_DIR := texts\n"
            f"TEXTS := {contract['expression']}\n"
            "all: ;\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/find", "texts", "-type", "f", "-name", "*.txt"],
            cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        ).stdout
        with self.session() as probe:
            commands = MakeCommands(probe, {contract["expression"]: contract})
            registration = commands[command]
            result = probe.command(registration)
            self.assertEqual(result.stdout, ordinary)
            self.assertEqual(result.consumed, registration.sources)
            unknown_types = Command(
                (
                    *registration.argv[:5],
                    registration.argv[5].replace(
                        "kind = record[18]", "kind = DT_UNKNOWN",
                    ),
                    *registration.argv[6:],
                ),
                sources=registration.sources, directories=registration.directories,
            )
            fallback = probe.command(unknown_types)
            self.assertEqual(fallback.stdout, ordinary)
            self.assertEqual(fallback.consumed, registration.sources)
            fallback_status_paths = {
                record[1] for record in fallback.metadata
                if record[0] == 262 and record[2] & 0x100 and record[6] == 0
            }
            self.assertTrue(
                {"/repo/" + path for path in paths} <= fallback_status_paths,
                "unknown entry types must execute no-follow metadata lookup for every file",
            )
            records = [record for record in result.metadata if record[0] == 217]
            self.assertTrue(records)
            self.assertTrue(all(record[4] == 4096 for record in records))
            self.assertTrue(all(
                len(record[7]) == len(record[8]) == 2 * record[4]
                for record in records
            ))
            root_batches = [
                record for record in records
                if record[1] == "/repo/texts/many" and record[6] > 0
            ]
            self.assertGreater(len(root_batches), 1)
            self.assertIn("texts/日本語.txt\n".encode(), result.stdout)
            self.assertNotIn(b"nonmatching.bin", result.stdout)
            self.assertNotIn(b"ignored.md", result.stdout)
            observed = probe.make("all", variables=("TEXTS",), commands=commands)
            self.assertEqual(
                observed.semantics["domains"]["TEXTS"]["value"],
                " ".join(ordinary.decode().splitlines()),
            )
            self.assertEqual(len(observed.semantics["dynamic_commands"]), 1)
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)
        self.assertEqual(set(os.listdir("/proc/self/fd")), descriptors)

    def test_generated_dependency_remakes_publish_real_depfiles_and_restart_make(self):
        cases = self.add_generated_dependency_fixture()
        lines = [f"-include {case['output']}" for case in cases]
        lines.extend((".PHONY: all force", "ifeq ($(MAKE_RESTARTS),)"))
        for case in cases:
            lines.append(f"{case['output']}: force\n\t{case['make_command']}")
        lines.append("else")
        for case in cases:
            lines.append(f"{case['output']}: ;")
        lines.append("endif")
        for case in cases:
            lines.append(f"{case['target']}: ;")
        lines.append("all: " + " ".join(case["target"] for case in cases))
        lines.append("force: ;")
        self.add("Makefile", "\n".join(lines) + "\n")
        ordinary = {}
        for case in cases:
            completed = subprocess.run(
                ["/bin/sh", "-c", case["command"]],
                cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                capture_output=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            ordinary[case["output"]] = self.normalize_repo_bytes(
                (self.root / case["output"]).read_bytes()
            )
        commands = {}
        for case in cases:
            with self.session() as probe:
                commands[case["make_command"]] = MakeCommands(probe, self.contracts)[case["command"]]
            self.assertIsNone(probe.base)
            self.assertFalse(probe.budget.children)
        with self.session() as probe:
            observed = probe.make("all", variables=("MAKE_RESTARTS",), commands=commands)
            self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
            self.assertEqual(
                {item.path: item.data for item in observed.generated},
                ordinary,
            )
            self.assertEqual(
                {
                    output[0]
                    for record in observed.semantics["dynamic_commands"]
                    for output in record["generated_outputs"]
                },
                {case["output"] for case in cases},
            )
            self.assertEqual(len(observed.semantics["dynamic_commands"]), 3)
            current = {item.path: item for item in observed.generated}
            self.assertIn(
                b"/repo/assets/manifest.json",
                current["build/generated/data/chapterobjectives.inputs.mk"].data,
            )
            self.assertIn(
                b"/repo/testdata/objectives/el_objectives.json",
                current["build/generated/data/autoplaystrategies.inputs.mk"].data,
            )
            self.assertIn(
                b"/repo/testdata/bundles/el_bundle.json",
                current["build/generated/data/eventlists.inputs.mk"].data,
            )
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)

    def test_generated_dependency_uses_named_option_semantics_and_rejects_key_drift(self):
        cases = {case["name"]: case for case in self.add_generated_dependency_fixture()}
        canonical = cases["autoplaystrategies"]["command"]
        reordered = (
            'python3 -m scripts.generated_data.autoplaystrategies.deps \\\n'
            '\t--bundle-source "testdata/bundles" \\\n'
            '\t--make-target "build/generated/data/data_autoplay_strategies.c" \\\n'
            '\t--source "testdata/strategies" \\\n'
            '\t--objectives-source "testdata/objectives" \\\n'
            '\t--depfile "build/generated/data/autoplaystrategies.inputs.mk"'
        )
        ordinary = {}
        for label, command in (("canonical", canonical), ("reordered", reordered)):
            completed = subprocess.run(
                ["/bin/sh", "-c", command],
                cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                capture_output=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            ordinary[label] = (self.root / "build/generated/data/autoplaystrategies.inputs.mk").read_bytes()
        self.assertEqual(ordinary["reordered"], ordinary["canonical"])
        with self.session() as probe:
            canonical_command = MakeCommands(probe, self.contracts)[canonical]
            canonical_inputs = json.loads(canonical_command.argv[-3])
            canonical_sources = canonical_command.sources
            canonical_dirs = canonical_command.directories
        with self.session() as probe:
            reordered_command = MakeCommands(probe, self.contracts)[reordered]
            reordered_inputs = json.loads(reordered_command.argv[-3])
            self.assertEqual(reordered_inputs, canonical_inputs)
            self.assertEqual(reordered_command.sources, canonical_sources)
            self.assertEqual(reordered_command.directories, canonical_dirs)
            for bad, expected in (
                (
                    canonical.replace(
                        '\t--objectives-source "testdata/objectives" \\\n', "",
                    ),
                    "missing --objectives-source",
                ),
                (
                    canonical.replace(
                        '\t--depfile "build/generated/data/autoplaystrategies.inputs.mk"',
                        '\t--extra "ignored" \\\n\t--depfile "build/generated/data/autoplaystrategies.inputs.mk"',
                    ),
                    "extra --extra",
                ),
            ):
                with self.subTest(command=bad):
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        MakeCommands(probe, self.contracts)[bad]

    def test_generated_dependency_adapter_respects_selected_views_and_missing_companions(self):
        cases = {case["name"]: case for case in self.add_generated_dependency_fixture()}
        removed = "assets/tmx/Example.tmx"
        base_budget = ProbeBudget()
        base_loader = self.capture_loader(base_budget)
        select_budget = ProbeBudget()
        base_again = self.capture_loader(select_budget)
        (self.root / removed).unlink()
        del self.entries[removed]
        current_budget = ProbeBudget()
        current_loader = self.capture_loader(current_budget)
        command = cases["chapterobjectives"]["command"]
        marker = f"/repo/{removed}".encode()
        with ProbeSession(
            current_loader, scratch_root=self.root / "build/scratch", budget=current_budget,
        ) as probe:
            current = MakeCommands(probe, self.contracts)[command]
            reported = json.loads(current.argv[-3])
            self.assertNotIn(marker.decode(), reported)
        current_for_base = self.capture_loader(select_budget)
        with ProbeSession(
            current_for_base, scratch_root=self.root / "build/scratch", budget=select_budget,
        ) as probe:
            with probe.select_view(base_again) as selected:
                self.assertIs(selected, probe)
                base = MakeCommands(probe, self.contracts)[command]
                reported = json.loads(base.argv[-3])
                self.assertIn(marker.decode(), reported)
        missing = "assets/manifest.json"
        (self.root / missing).unlink()
        del self.entries[missing]
        with self.session() as probe:
            with self.assertRaisesRegex(
                MakeProbeError, r"(missing declared owner input 'assets/manifest\.json'|source declaration resolves no regular inputs: assets/manifest\.json)",
            ):
                MakeCommands(probe, self.contracts)[command]

    def test_generated_dependency_chapterbundle_support_cache_is_view_bound(self):
        self.add_generated_dependency_fixture()
        self.add(
            "testdata/deps/deps_units_second.json",
            (ROOT / "scripts/generated_data/tests/fixtures/chapterbundle/deps_units_second.json").read_bytes(),
        )
        budget = ProbeBudget()
        base_loader = self.capture_loader(budget)
        self.rewrite_generated_dependency_bundle(units_source="testdata/deps/deps_units_second.json")
        current_loader = self.capture_loader(budget)
        bundle_source = "testdata/bundles"
        bundle_sources = ("testdata/bundles/el_bundle.json",)
        with ProbeSession(
            current_loader, scratch_root=self.root / "build/scratch", budget=budget,
        ) as probe:
            commands = MakeCommands(probe, self.contracts)
            _code, current_refs, _dirs, _members, _sources = commands._chapterbundle_dependency_support(
                bundle_source, bundle_sources,
            )
            self.assertIn("testdata/deps/deps_units_second.json", current_refs)
            self.assertNotIn("testdata/deps/deps_units.json", current_refs)
            with probe.select_view(base_loader) as selected:
                self.assertIs(selected, probe)
                _code, base_refs, _dirs, _members, _sources = commands._chapterbundle_dependency_support(
                    bundle_source, bundle_sources,
                )
                self.assertIn("testdata/deps/deps_units.json", base_refs)
                self.assertNotIn("testdata/deps/deps_units_second.json", base_refs)
            _code, current_again, _dirs, _members, _sources = commands._chapterbundle_dependency_support(
                bundle_source, bundle_sources,
            )
            self.assertEqual(current_again, current_refs)

    def test_registered_find_reduces_actual_directory_observation_traffic(self):
        self.add("Makefile", "all: ;\n")
        for index in range(21):
            path = f"texts/d{index:02d}/entry.txt"
            self.add(path, path + "\n")
        command = 'find texts -type f -name "*.txt"'
        contract = next(
            item for item in self.contracts.values()
            if item["id"] == "legacy-text-source-discovery"
        )
        scandir_body = (
            "import fnmatch,os,sys\n"
            "def visit(path):\n"
            "    with os.scandir(path) as entries:\n"
            "        for entry in entries:\n"
            "            if entry.is_dir(follow_symlinks=False): visit(entry.path)\n"
            "            elif entry.is_file(follow_symlinks=False) and "
            "fnmatch.fnmatchcase(entry.name,sys.argv[2]): print(entry.path)\n"
            "visit(sys.argv[1])"
        )
        observations = {}
        for name in ("scandir", "getdents4096"):
            with self.session() as probe:
                registration = MakeCommands(
                    probe, {contract["expression"]: contract},
                )[command]
                selected = registration if name == "getdents4096" else Command(
                    ("/usr/bin/python3", "-I", "-S", "-B", "-c",
                     scandir_body, "texts", "*.txt"),
                    sources=registration.sources, directories=registration.directories,
                )
                before = probe.budget.bytes.get("control", 0)
                result = probe.command(selected)
                observations[name] = {
                    "output": result.stdout,
                    "control": probe.budget.bytes["control"] - before,
                    "metadata": result.metadata,
                }
            self.assertIsNone(probe.base)
            self.assertFalse(probe.budget.children)
        self.assertEqual(observations["getdents4096"]["output"],
                         observations["scandir"]["output"])
        old_records = [
            record for record in observations["scandir"]["metadata"] if record[0] == 217
        ]
        new_records = [
            record for record in observations["getdents4096"]["metadata"]
            if record[0] == 217
        ]
        self.assertEqual(len(old_records), 44)
        self.assertEqual(len(new_records), 44)
        self.assertTrue(all(record[4] == 32768 for record in old_records))
        self.assertTrue(all(record[4] == 4096 for record in new_records))
        for records in (old_records, new_records):
            self.assertTrue(all(
                len(record[7]) == len(record[8]) == 2 * record[4]
                for record in records
            ))
        old_control = observations["scandir"]["control"]
        new_control = observations["getdents4096"]["control"]
        self.assertLessEqual(2 * new_control, old_control)
        self.assertLess(
            len(encoded(observations["getdents4096"]["metadata"])),
            len(encoded(observations["scandir"]["metadata"])) // 2,
        )

    def test_registered_find_rejects_escaping_missing_and_nonregular_roots(self):
        self.add("Makefile", "all: ;\n")
        contract = next(
            item for item in self.contracts.values()
            if item["id"] == "legacy-text-source-discovery"
        )
        with self.session() as probe:
            with self.assertRaisesRegex(MakeProbeError, "exactly one sealed domain"):
                MakeCommands(probe, {contract["expression"]: contract})[
                    'find ../texts -type f -name "*.txt"'
                ]
        self.assertIsNone(probe.base)
        for shape, expected in (
            ("missing", "directory declaration is absent"),
            ("nonregular", "nonregular source namespace"),
        ):
            if shape == "nonregular":
                self.add("texts", "target", mode="120000")
                (self.root / "texts").unlink()
                (self.root / "texts").symlink_to("target")
            command = 'find texts -type f -name "*.txt"'
            with self.session() as probe:
                registration = MakeCommands(
                    probe, {contract["expression"]: contract},
                )[command]
                with self.assertRaisesRegex(MakeProbeError, expected):
                    probe.command(registration)
            self.assertIsNone(probe.base)
            self.assertFalse(probe.budget.children)

    def test_registered_find_does_not_follow_nonregular_descendants(self):
        self.add("Makefile", "all: ;\n")
        self.add("texts/regular.txt", "regular\n")
        self.add("outside.txt", "outside\n")
        self.add("texts/link.txt", "../outside.txt", mode="120000")
        link = self.root / "texts/link.txt"
        link.unlink()
        link.symlink_to("../outside.txt")
        ordinary = subprocess.run(
            ["/usr/bin/find", "texts", "-type", "f", "-name", "*.txt"],
            cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        )
        self.assertEqual(ordinary.stdout, b"texts/regular.txt\n")
        contract = next(
            item for item in self.contracts.values()
            if item["id"] == "legacy-text-source-discovery"
        )
        command = 'find texts -type f -name "*.txt"'
        with self.session() as probe:
            registration = MakeCommands(
                probe, {contract["expression"]: contract},
            )[command]
            with self.assertRaisesRegex(
                MakeProbeError, "nonregular namespace in source enumeration",
            ):
                probe.command(registration)
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)

    @unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "requires arm-none-eabi-gcc")
    def test_modern_toolchain_directory_queries_match_ordinary_shell_and_make(self):
        compiler = str(Path(shutil.which("arm-none-eabi-gcc")).resolve())
        self.add("scripts/shiftcheck/modern_toolchain.sh",
                 (ROOT / "scripts/shiftcheck/modern_toolchain.sh").read_bytes(), "100755")
        contracts = {
            item["id"]: item for item in self.contracts.values()
            if item["id"] in {"modern-libgcc-directory", "modern-libc-directory"}
        }
        for label, binutils in (("no-B", ""), ("valid-B", '"-B/usr/bin/"')):
            with self.subTest(profile=label):
                queries = {
                    "LIBGCC": (
                        "modern-libgcc-directory", "-print-libgcc-file-name",
                    ),
                    "LIBC": (
                        "modern-libc-directory", "-print-file-name=libc.a",
                    ),
                }
                makefile = [
                    f"MODERN_CC := {compiler}",
                    f"MODERN_BINUTILS_FLAG := {binutils}",
                    "MODERN_ARCH_FLAGS := -mcpu=arm7tdmi -mthumb -mthumb-interwork",
                ]
                expected = {}
                for variable, (contract_id, query) in queries.items():
                    makefile.append(f"{variable} := {contracts[contract_id]['expression']}")
                    makefile.append(f"{variable}_STATUS := $(.SHELLSTATUS)")
                    command = (
                        f'p=$("{compiler}" {binutils + " " if binutils else ""}'
                        '-mcpu=arm7tdmi \\\n\t'
                        f'-mthumb -mthumb-interwork {query} '
                        '2>/dev/null) && dirname "$p"'
                    )
                    ordinary = subprocess.run(
                        ["/bin/sh", "-c", command], cwd=self.root,
                        env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                        capture_output=True, timeout=15,
                    )
                    self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
                    expected[variable] = ordinary.stdout.decode().strip()
                makefile.append("all: ;")
                self.add("Makefile", "\n".join(makefile) + "\n")
                ordinary_make = subprocess.run(
                    ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
                    cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                    capture_output=True, timeout=15,
                )
                self.assertEqual(ordinary_make.returncode, 0, ordinary_make.stderr)
                with self.session(runtime_files=("/bin/arm-none-eabi-gcc",)) as probe:
                    commands = MakeCommands(
                        probe, {item["expression"]: item for item in contracts.values()},
                    )
                    for variable, expected_value in expected.items():
                        _, query = queries[variable]
                        command = (
                            f'p=$("{compiler}" {binutils + " " if binutils else ""}'
                            '-mcpu=arm7tdmi \\\n\t'
                            f'-mthumb -mthumb-interwork {query} 2>/dev/null) && dirname "$p"'
                        )
                        registration = commands[command]
                        self.assertEqual(
                            probe.command(registration).stdout.decode().strip(),
                            expected_value,
                        )
                    observed = probe.make(
                        "all", variables=("LIBGCC", "LIBGCC_STATUS", "LIBC", "LIBC_STATUS"),
                        commands=commands,
                    )
                    self.assertEqual(
                        observed.semantics["domains"]["LIBGCC"]["value"], expected["LIBGCC"],
                    )
                    self.assertEqual(
                        observed.semantics["domains"]["LIBC"]["value"], expected["LIBC"],
                    )
                    self.assertEqual(observed.semantics["domains"]["LIBGCC_STATUS"]["value"], "0")
                    self.assertEqual(observed.semantics["domains"]["LIBC_STATUS"]["value"], "0")
                    self.assertEqual(len(observed.semantics["dynamic_commands"]), 2)
                    for record in observed.semantics["dynamic_commands"]:
                        command = record["command"]
                        self.assertEqual(command["argv"][0], compiler)
                        self.assertEqual(command["argv"][1:-1], (
                            ([] if not binutils else ["-B/usr/bin/"])
                            + ["-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork"]
                        ))
                        self.assertEqual(command["stdout_transform"], "dirname")
                        self.assertEqual(command["runtime_tool"]["path"], compiler)
                        self.assertEqual(command["runtime_tool"]["canonical"], compiler)
                        self.assertEqual(
                            command["runtime_tool"]["mode"],
                            Path(compiler).stat().st_mode & 0o777,
                        )
                        self.assertRegex(command["runtime_tool"]["sha256"], r"^[0-9a-f]{64}$")
                    self.assertEqual(observed.stderr, b"")
                self.assertIsNone(probe.base)
                self.assertFalse(probe.runtime_tools)
                self.assertFalse(probe.runtime_query_profiles)
                self.assertFalse(probe.budget.children)

    def test_modern_toolchain_directory_queries_reject_unsupported_driver_and_binutils_paths(self):
        self.add("scripts/shiftcheck/modern_toolchain.sh",
                 (ROOT / "scripts/shiftcheck/modern_toolchain.sh").read_bytes(), "100755")
        cases = (
            ('p=$("/usr/bin/cc" -mcpu=arm7tdmi -mthumb -mthumb-interwork '
             '-print-libgcc-file-name 2>/dev/null) && dirname "$p"',
             "supported arm-none-eabi-gcc compiler"),
            ('p=$("arm-none-eabi-gcc" -v -mcpu=arm7tdmi -mthumb -mthumb-interwork '
             '-print-libgcc-file-name 2>/dev/null) && dirname "$p"',
             "declared flags"),
            ('p=$("arm-none-eabi-gcc" -mthumb -mcpu=arm7tdmi -mthumb-interwork '
             '-print-libgcc-file-name 2>/dev/null) && dirname "$p"',
             "declared flags"),
        )
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            for command, expected in cases:
                with self.subTest(command=command):
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        commands[command]
            bad_binutils = (
                'p=$("arm-none-eabi-gcc" "-B/opt/toolchain/bin/" '
                '-mcpu=arm7tdmi -mthumb -mthumb-interwork '
                '-print-file-name=libc.a 2>/dev/null) && dirname "$p"'
            )
            with mock.patch.object(shutil, "which", return_value=None):
                with self.assertRaisesRegex(MakeProbeError, "supported binutils roots"):
                    commands[bad_binutils]

    def test_root_runtime_capture_preserves_actual_absent_compiler_alias(self):
        self.add("Makefile", "all: ;\n")
        capture = make_probe._capture_runtime_input

        def absent_compiler(path, budget):
            item = capture(path, budget)
            if path == "/bin/arm-none-eabi-gcc":
                return replace(item, data=None, mode=None)
            return item

        with mock.patch.object(make_probe, "_capture_runtime_input", absent_compiler):
            with self.session(runtime_files=ROOT_RUNTIME_FILES) as probe:
                compiler = next(
                    item for item in probe.runtime_inputs
                    if item.path == "/bin/arm-none-eabi-gcc"
                )
                self.assertIsNone(compiler.data)
                self.assertIsNone(compiler.mode)
                self.assertEqual(compiler.canonical, "/usr/bin/arm-none-eabi-gcc")
                self.assertEqual(compiler.aliases, (("/bin", "usr/bin"),))
                self.assertNotIn(compiler.path, probe.runtime_dispatch)
                self.assertFalse(
                    (probe.runtime_root / compiler.canonical.lstrip("/")).exists()
                )
                observed = probe.make("all")
                self.assertEqual(observed.stderr, b"")
        self.assertIsNone(probe.base)
        self.assertFalse(probe.runtime_inputs)
        self.assertFalse(probe.budget.children)

    def test_modern_toolchain_directory_query_never_executes_checkout_local_name_match(self):
        tool = self.root / "build/toolchain-root/usr/bin/arm-none-eabi-gcc"
        marker = self.root / "candidate-executed"
        self.add(
            tool.relative_to(self.root).as_posix(),
            f"#!/bin/sh\nprintf executed > {marker}\nexit 7\n",
            "100755",
        )
        self.add("scripts/shiftcheck/modern_toolchain.sh",
                 (ROOT / "scripts/shiftcheck/modern_toolchain.sh").read_bytes(), "100755")
        command = (
            f'p=$("{tool}" -mcpu=arm7tdmi -mthumb -mthumb-interwork '
            '-print-libgcc-file-name 2>/dev/null) && dirname "$p"'
        )
        contract = next(
            item for item in self.contracts.values() if item["id"] == "modern-libgcc-directory"
        )
        self.add("Makefile", (
            f"MODERN_CC := {tool}\n"
            "MODERN_BINUTILS_FLAG :=\n"
            "MODERN_ARCH_FLAGS := -mcpu=arm7tdmi -mthumb -mthumb-interwork\n"
            f"VALUE := {contract['expression']}\n"
            "STATUS := $(.SHELLSTATUS)\n"
            "all:\n\t@printf 'VALUE=%s\\nSTATUS=%s\\n' '$(VALUE)' '$(STATUS)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
            cwd=self.root,
            env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, timeout=15,
        )
        self.assertEqual(ordinary.returncode, 0)
        self.assertEqual(ordinary.stdout, b"VALUE=\nSTATUS=7\n")
        self.assertTrue(marker.is_file())
        marker.unlink()
        with self.session() as probe:
            commands = MakeCommands(probe, {contract["expression"]: contract})
            with self.assertRaisesRegex(
                MakeProbeError, "checkout-local modern compiler lacks a trusted installed-tool identity",
            ):
                commands[command]
            self.assertFalse(marker.exists())
        self.assertIsNone(probe.base)
        self.assertFalse(probe.runtime_tools)
        self.assertFalse(probe.runtime_query_profiles)
        self.assertFalse(probe.budget.children)

    @unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "requires arm-none-eabi-gcc")
    def test_modern_toolchain_runtime_receipt_cannot_authorize_other_compiler_modes(self):
        compiler = str(Path(shutil.which("arm-none-eabi-gcc")).resolve())
        self.add("scripts/shiftcheck/modern_toolchain.sh",
                 (ROOT / "scripts/shiftcheck/modern_toolchain.sh").read_bytes(), "100755")
        with self.session(runtime_files=("/bin/arm-none-eabi-gcc",)) as probe:
            tool = probe.runtime_tool(compiler)
            with self.assertRaisesRegex(
                MakeProbeError, "metadata query escaped its exact profile",
            ):
                probe.command(Command(
                    (compiler, "--version"), runtime_tool=tool, stdout_transform="dirname",
                ))
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)

    def test_modern_toolchain_directory_query_rejects_shell_injection_before_execution(self):
        self.add("scripts/shiftcheck/modern_toolchain.sh",
                 (ROOT / "scripts/shiftcheck/modern_toolchain.sh").read_bytes(), "100755")
        marker = self.root / "injected"
        command = (
            'p=$("arm-none-eabi-gcc" -mcpu=arm7tdmi -mthumb -mthumb-interwork '
            '-print-libgcc-file-name 2>/dev/null) && dirname "$p"; '
            f'printf injected > "{marker}"'
        )
        with self.session() as probe:
            with self.assertRaisesRegex(MakeProbeError, "exactly one sealed domain"):
                MakeCommands(probe, self.contracts)[command]
            self.assertFalse(marker.exists())
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)

    def test_python_adapters_track_only_their_actual_import_closure(self):
        for name, data in (
            ("scripts/__init__.py", ""),
            ("scripts/generated_data/__init__.py", ""),
            ("scripts/generated_data/helper.py", "VALUE = 7\n"),
            ("scripts/generated_data/unrelated.py", "VALUE = 99\n"),
            ("scripts/generated_data/tool.py",
             "from scripts.generated_data import helper\nprint(helper.VALUE)\n"),
        ):
            self.add(name, data)
        inline = {
            "inline-python": {
                "id": "inline-python",
                "command_regex": r'^python3 -c "import scripts\.generated_data\.helper as h; print\(h\.VALUE\)"$',
                "input_files": [],
                "input_variables": [],
                "automatic_inputs": [],
                "resolved_value": None,
                "owning_evidence_ids": ["owner"],
            },
        }
        script = {
            "script-python": {
                "id": "script-python",
                "command_regex": r"^python3 scripts/generated_data/tool\.py$",
                "input_files": [],
                "input_variables": [],
                "automatic_inputs": [],
                "resolved_value": None,
                "owning_evidence_ids": ["owner"],
            },
        }
        with self.session() as probe:
            inline_registration = MakeCommands(probe, inline)[
                'python3 -c "import scripts.generated_data.helper as h; print(h.VALUE)"'
            ]
            self.assertIn("scripts/generated_data/helper.py", inline_registration.code)
            self.assertNotIn("scripts/generated_data/unrelated.py", inline_registration.code)
            self.assertEqual(probe.command(inline_registration).stdout, b"7\n")

            script_registration = MakeCommands(probe, script)["python3 scripts/generated_data/tool.py"]
            self.assertIn("scripts/generated_data/tool.py", script_registration.code)
            self.assertIn("scripts/generated_data/helper.py", script_registration.code)
            self.assertNotIn("scripts/generated_data/unrelated.py", script_registration.code)
            inputs = {item[0] for item in probe.command(script_registration).input_identities}
            self.assertIn("scripts/generated_data/tool.py", inputs)
            self.assertIn("scripts/generated_data/helper.py", inputs)
            self.assertNotIn("scripts/generated_data/unrelated.py", inputs)

    def test_three_record_asset_discovery_reaches_make_and_restarts_once(self):
        from scripts.assets.manifest import discovery_sources, load_manifest

        source = "assets/manifest.json"
        records = load_manifest(str(ROOT / source))
        self.assertEqual(len(records), 3)
        inputs = (source, *discovery_sources(records))
        self.assertEqual(len(inputs), 15)
        for path in inputs:
            self.add(path, (ROOT / path).read_bytes())
        for prefix in CODE_PREFIXES:
            for path in (ROOT / prefix).rglob("*.py"):
                self.add(path.relative_to(ROOT).as_posix(), path.read_bytes())
        output = "build/generated/asset-discovery/current.mk"
        command = (
            'python3 -m scripts.assets --custom-spell-effects "0" --item-id-cap "0xCD" '
            f'--manifest "{source}" --discovery-makefile "{output}" discovery-makefile'
        )
        self.add("Makefile", (
            f"-include {output}\n"
            ".PHONY: all force\n"
            "ifeq ($(MAKE_RESTARTS),)\n"
            f"{output}: force\n\t{command}\n"
            "else\n"
            f"{output}: ;\n"
            "endif\n"
            "all: ;\n"
        ))
        names = (
            "ASSET_PORTRAIT_INCBIN_CONSUMERS", "ASSET_TMX_INCBIN_CONSUMERS",
            "ASSET_BANIM_INCBIN_CONSUMERS", "ASSET_CUSTOM_SPELL_INCBIN_CONSUMERS",
        )
        budget = ProbeBudget()
        base_loader = self.capture_loader(budget)
        manifest = json.loads((self.root / source).read_bytes())
        manifest["assets"] = [
            record for record in manifest["assets"] if record["id"] != "LORM_SP1_PROOF"
        ]
        self.add(source, json.dumps(manifest))
        deleted = "assets/banim/lorm_sp1/script.txt"
        (self.root / deleted).unlink()
        del self.entries[deleted]
        current_loader = self.capture_loader(budget)
        current_inputs = (source, *discovery_sources(load_manifest(str(self.root / source))))
        self.asset_view_evidence = {"scope": "asset producer views, not graph ownership", "views": {}}
        with ProbeSession(
            current_loader, scratch_root=self.root / "build/scratch", budget=budget,
        ) as probe:
            def observe(view, expected_inputs, banim):
                registration = asset_discovery_command(probe, source, output)
                produced = probe.command(registration)
                self.assertEqual(set(produced.consumed), set(expected_inputs))
                self.assertEqual([item.path for item in produced.generated], [output])
                actual = probe.make(
                    "all", variables=(*names, "MAKE_RESTARTS"),
                    commands={command: registration},
                )
                values = {name: actual.semantics["domains"][name]["value"] for name in names}
                self.assertEqual(values, {
                    "ASSET_PORTRAIT_INCBIN_CONSUMERS": "EIRIKA_FORMATTED_PORTRAIT",
                    "ASSET_TMX_INCBIN_CONSUMERS": "CH2_MAIN_MAP",
                    "ASSET_BANIM_INCBIN_CONSUMERS": banim,
                    "ASSET_CUSTOM_SPELL_INCBIN_CONSUMERS": "",
                })
                self.assertEqual(actual.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
                self.assertEqual(len(actual.semantics["dynamic_commands"]), 1)
                self.assertEqual(
                    actual.semantics["dynamic_commands"][0]["generated_outputs"][0][0], output,
                )
                self.asset_view_evidence["views"][view] = {
                    "consumed": list(produced.consumed),
                    "values": values,
                    "make_restarts": actual.semantics["domains"]["MAKE_RESTARTS"]["value"],
                    "generated_outputs": actual.semantics["dynamic_commands"][0]["generated_outputs"],
                }
            observe("current", current_inputs, "")
            self.assertNotIn(deleted, probe.snapshot.files)
            previous_runs = budget.runs
            previous_states = budget.states
            deadline = budget.deadline
            with probe.select_view(base_loader) as selected:
                self.assertIs(selected, probe)
                self.assertIn(deleted, probe.snapshot.files)
                observe("base", inputs, "LORM_SP1_PROOF")
                self.assertEqual(budget.deadline, deadline)
            self.assertNotIn(deleted, probe.snapshot.files)
            self.assertGreater(budget.runs, previous_runs)
            self.assertGreater(budget.states, previous_states)
            self.asset_view_evidence["accounting"] = {
                "runs": budget.runs, "states": budget.states, "bytes": dict(budget.bytes),
                "processes": probe.processes_used, "live_process_peak": probe.live_process_peak,
                "syscalls": probe.syscalls_used, "deadline": budget.deadline,
            }
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)
        self.asset_view_evidence["cleanup"] = probe.base is None and not probe.budget.children

    def test_asset_publication_is_stable_across_independent_materializations(self):
        from scripts.assets.manifest import discovery_sources, load_manifest

        source = "assets/manifest.json"
        inputs = (source, *discovery_sources(load_manifest(str(ROOT / source))))
        for path in inputs:
            self.add(path, (ROOT / path).read_bytes())
        for prefix in CODE_PREFIXES:
            for path in (ROOT / prefix).rglob("*.py"):
                self.add(path.relative_to(ROOT).as_posix(), path.read_bytes())
        output = "build/generated/asset-discovery/current.mk"
        command = (
            'python3 -m scripts.assets --custom-spell-effects "0" --item-id-cap "0xCD" '
            f'--manifest "{source}" --discovery-makefile "{output}" discovery-makefile'
        )
        self.add("Makefile", f"include {output}\n{output}:\n\t{command}\nall: ;\n")
        results = []
        identities = []
        timestamps = []
        for change in (None, None, "content", "mode"):
            if change is not None:
                path = "assets/portrait_registry.json"
                data = (self.root / path).read_bytes()
                self.add(
                    path, data + b"\n" if change == "content" else data,
                    "100755" if change == "mode" else "100644",
                )
            budget = ProbeBudget()
            loader = self.capture_loader(budget)
            with ProbeSession(loader, scratch_root=self.root / "build/scratch", budget=budget) as probe:
                identities.append(probe.source_owners(inputs))
                before = {path: (probe.tree / path).stat().st_mtime_ns for path in inputs}
                observed = probe.make(
                    "all", variables=("MAKE_RESTARTS",),
                    commands={command: asset_discovery_command(probe, source, output)},
                )
                self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
                self.assertEqual(
                    {path: (probe.tree / path).stat().st_mtime_ns for path in inputs}, before,
                )
                timestamps.append(before)
                results.append(observed)
            self.assertIsNone(probe.base)
            self.assertFalse(budget.children)
        self.assertEqual(identities[0], identities[1])
        self.assertNotEqual(timestamps[0], timestamps[1])
        self.assertEqual(results[0].generated, results[1].generated)
        self.assertEqual(results[0].semantics["dynamic_commands"], results[1].semantics["dynamic_commands"])
        self.assertEqual(results[0].semantic_digest, results[1].semantic_digest)
        for before, after in ((1, 2), (2, 3)):
            self.assertNotEqual(identities[before], identities[after])
            self.assertNotEqual(results[before].generated, results[after].generated)
            self.assertNotEqual(results[before].semantic_digest, results[after].semantic_digest)


if __name__ == "__main__":
    unittest.main()
