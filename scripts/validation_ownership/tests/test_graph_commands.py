from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import unittest
from unittest import mock

from scripts.validation_ownership.authority import (
    AuthorityLoader, ENVIRONMENT, GitTreeEntries, GitTreeEntry, git_tree_entries,
)
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.graph_commands import (
    CODE_PREFIXES, ROOT_RUNTIME_FILES, MakeCommands, asset_discovery_command,
)
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
            "mkdir -p .dep/src/ && cc -E -Iinclude/first -I include/second "
            "-iquote include -nostdinc -undef -DACTIVE=1 "
            "src/input.c -MM -MG -MT src/input.o > .dep/src/input.d"
        )
        tokens = shlex.split(command)
        arguments = tokens[4:-2]
        ordinary = subprocess.run(
            ["/usr/bin/cc", *arguments[1:]], cwd=self.root,
            env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        )
        self.add("Makefile", (
            "include .dep/src/input.d\n"
            ".dep/src/input.d: src/input.c\n\t" + command + "\n"
            "src/input.o: ;\n"
        ))
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
                "src/input.o", variables=("MAKE_RESTARTS",),
                commands={command: registration},
            )
            self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
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
                        f'-mcpu=arm7tdmi -mthumb -mthumb-interwork {query} '
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
