"""Capability contract for the optional GCC analyzer portion of issue #84."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]

FAKE_COMPILER = """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

arguments = sys.argv[1:]
with open(os.environ["FAKE_CC_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps(arguments) + "\\n")

if "-fanalyzer" in arguments and os.environ["FAKE_ANALYZER_SUPPORTED"] != "1":
    sys.exit(2)

if "-o" in arguments:
    output = pathlib.Path(arguments[arguments.index("-o") + 1])
    output.parent.mkdir(parents=True, exist_ok=True)
    if "FAKE_HARNESS_LOG" not in os.environ:
        output.touch()
    else:
        sources = [pathlib.Path(arg).stem for arg in arguments
                   if pathlib.Path(arg).name in (
                       "sio_protocol_host_test.c", "runtime_bounds_host_test.c",
                       "png_bounds_host_test.c")]
        name = sources[0] if len(sources) == 1 else output.name
        output.write_text(
            "#!/usr/bin/env python3\\n"
            "import json, os\\n"
            f"name = {name!r}\\n"
            "with open(os.environ['FAKE_HARNESS_LOG'], 'a', encoding='utf-8') as stream:\\n"
            "    stream.write(json.dumps({'event': 'harness', 'name': name, "
            "'asan': os.environ.get('ASAN_OPTIONS'), "
            "'ubsan': os.environ.get('UBSAN_OPTIONS')}) + '\\\\n')\\n"
            "if os.environ.get('FAKE_HARNESS_FAILURE') == name:\\n"
            "    raise SystemExit(17)\\n"
        )
        output.chmod(0o755)
"""

FAKE_SUBMAKE = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

arguments = sys.argv[1:]
targets = []
directory = Path(".")
index = 0
while index < len(arguments):
    argument = arguments[index]
    if argument in ("-C", "--directory", "-f", "--file", "--makefile"):
        index += 1
        if index >= len(arguments):
            raise SystemExit("missing recursive make option value")
        if argument in ("-C", "--directory"):
            directory = Path(arguments[index])
    elif argument.startswith("-C") and len(argument) > 2:
        directory = Path(argument[2:])
    elif argument.startswith("--directory="):
        directory = Path(argument.partition("=")[2])
    elif (argument in ("-j", "--jobs") and index + 1 < len(arguments)
          and arguments[index + 1].isdigit()):
        index += 1
    elif not argument.startswith("-") and "=" not in argument:
        targets.append(argument)
    index += 1
repository = Path(os.environ["FAKE_REPOSITORY"]).resolve()
if targets == ["codeql-fanalyzer-test"] and directory.resolve() == repository:
    with open(os.environ["FAKE_HARNESS_LOG"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": "analyzer"}) + "\\n")
    real_make = os.environ["FAKE_REAL_MAKE"]
    os.execv(real_make, [real_make, *arguments])
allowed = {(repository / "tools" / name).resolve() for name in ("gbagfx", "mid2agb")}
if not targets and directory.resolve() in allowed:
    raise SystemExit(0)
raise SystemExit("unexpected recursive make")
"""


class CodeqlFanalyzerGateTests(unittest.TestCase):
    def run_gate(self, *, supported: bool, required: bool):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            output_dir = root / "output"
            log = root / "compiler.jsonl"
            bin_dir.mkdir()

            compiler = bin_dir / "fake-cc"
            compiler.write_text(FAKE_COMPILER, encoding="utf-8")
            compiler.chmod(0o755)

            pkg_config = bin_dir / "pkg-config"
            pkg_config.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            pkg_config.chmod(0o755)

            environment = dict(os.environ)
            environment.pop("FAKE_HARNESS_LOG", None)
            environment.pop("FAKE_HARNESS_FAILURE", None)
            environment.update(
                {
                    "FAKE_ANALYZER_SUPPORTED": "1" if supported else "0",
                    "FAKE_CC_LOG": str(log),
                    "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
                }
            )
            result = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "codeql-fanalyzer-test",
                    f"HOST_CC={compiler}",
                    f"CODEQL_TEST_DIR={output_dir}",
                    f"CODEQL_REQUIRE_FANALYZER={1 if required else 0}",
                ],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            invocations = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
            ]
            outputs = {
                path.name
                for path in output_dir.glob("*_analyzer.o")
            }
            return result, invocations, outputs

    def run_runtime_gate(self, *, failing_harness=None):
        make = shutil.which("make")
        if make is None:
            self.fail("make is not available on PATH")
        make = os.path.abspath(make)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            output_dir = root / "output"
            compiler_log = root / "compiler.jsonl"
            harness_log = root / "harness.jsonl"
            bin_dir.mkdir()

            compiler = bin_dir / "fake-cc"
            compiler.write_text(FAKE_COMPILER, encoding="utf-8")
            compiler.chmod(0o755)
            submake = bin_dir / "fake-submake"
            submake.write_text(FAKE_SUBMAKE, encoding="utf-8")
            submake.chmod(0o755)
            pkg_config = bin_dir / "pkg-config"
            pkg_config.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            pkg_config.chmod(0o755)
            python = bin_dir / "fake-python"
            python.write_text(
                "#!/usr/bin/env python3\n"
                "import os, sys\n"
                'if sys.argv[1:3] == ["-m", "unittest"]:\n'
                "    raise SystemExit(0)\n"
                f"os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])\n",
                encoding="utf-8",
            )
            python.chmod(0o755)

            environment = dict(os.environ)
            for name in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEFILES", "MAKELEVEL"):
                environment.pop(name, None)
            environment.update(
                {
                    "FAKE_ANALYZER_SUPPORTED": "1",
                    "FAKE_CC_LOG": str(compiler_log),
                    "FAKE_HARNESS_LOG": str(harness_log),
                    "FAKE_HARNESS_FAILURE": failing_harness or "",
                    "FAKE_REAL_MAKE": make,
                    "FAKE_REPOSITORY": str(ROOT),
                    "PATH": f"{bin_dir}{os.pathsep}{environment['PATH']}",
                }
            )
            result = subprocess.run(
                [
                    make,
                    "--no-print-directory",
                    "codeql-alerts-test",
                    f"HOST_CC={compiler}",
                    f"CODEQL_TEST_DIR={output_dir}",
                    "CODEQL_REQUIRE_FANALYZER=1",
                    f"MAKE={submake}",
                    f"PYTHON={python}",
                ],
                cwd=ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=60,
            )
            invocations = [
                json.loads(line)
                for line in compiler_log.read_text(encoding="utf-8").splitlines()
            ] if compiler_log.exists() else []
            events = [
                json.loads(line)
                for line in harness_log.read_text(encoding="utf-8").splitlines()
            ] if harness_log.exists() else []
            return result, invocations, events

    def test_supported_analyzer_runs_every_check(self):
        result, invocations, outputs = self.run_gate(supported=True, required=False)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("analyzer support detected; running checks", result.stdout)
        self.assertEqual(len(invocations), 4)
        self.assertIn("fanalyzer_probe.c", " ".join(invocations[0]))
        self.assertEqual(
            outputs,
            {
                "sio_core_analyzer.o",
                "event_analyzer.o",
                "convert_png_analyzer.o",
            },
        )

    def test_unsupported_analyzer_is_an_explicit_skip(self):
        result, invocations, outputs = self.run_gate(supported=False, required=False)

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("SKIP:", result.stdout)
        self.assertIn("does not support the required -fanalyzer flags", result.stdout)
        self.assertEqual(len(invocations), 1)
        self.assertEqual(outputs, set())

    def test_required_analyzer_rejects_probe_failure(self):
        result, invocations, outputs = self.run_gate(supported=False, required=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("analyzer support is required", result.stdout)
        self.assertEqual(len(invocations), 1)
        self.assertEqual(outputs, set())

    def test_recursive_analyzer_uses_path_selected_make(self):
        real_make = shutil.which("make")
        if real_make is None:
            self.fail("make is not available on PATH")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            wrapper = root / "make-wrapper"
            selected_make = bin_dir / "make"
            log = root / "make.jsonl"
            wrapper.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "if os.path.abspath(sys.argv[0]) != os.environ['FAKE_MAKE_SELECTED']:\n"
                "    raise SystemExit('selected make entrypoint was not preserved')\n"
                "with open(os.environ['FAKE_MAKE_LOG'], 'a', encoding='utf-8') as stream:\n"
                "    stream.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                f"os.execv({real_make!r}, [{real_make!r}, *sys.argv[1:]])\n",
                encoding="utf-8",
            )
            wrapper.chmod(0o755)
            selected_make.symlink_to(wrapper)
            with mock.patch.dict(os.environ, {
                "FAKE_MAKE_LOG": str(log),
                "FAKE_MAKE_SELECTED": str(selected_make),
                "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            }):
                result, _, _ = self.run_runtime_gate()

            self.assertEqual(result.returncode, 0, result.stdout)
            calls = [
                json.loads(line)
                for line in log.read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(any("codeql-alerts-test" in call for call in calls))
            self.assertTrue(any("codeql-fanalyzer-test" in call for call in calls))

    def test_runtime_harnesses_precede_the_optional_analyzer_gate(self):
        harnesses = {
            "sio_protocol_host_test",
            "runtime_bounds_host_test",
            "png_bounds_host_test",
        }
        result, invocations, events = self.run_runtime_gate()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(any("-fanalyzer" in args for args in invocations))
        sources = {
            harness: (ROOT / "tests/codeql" / f"{harness}.c").resolve()
            for harness in harnesses
        }
        runtime_compiles = [
            (harness, args)
            for args in invocations
            for harness, source in sources.items()
            if any((ROOT / arg).resolve() == source
                   for arg in args if Path(arg).suffix == ".c")
        ]
        self.assertEqual(len(runtime_compiles), len(harnesses))
        self.assertEqual({harness for harness, _ in runtime_compiles}, harnesses)
        for harness, args in runtime_compiles:
            with self.subTest(compile=harness):
                sanitizers = set()
                for arg in args:
                    if arg.startswith("-fsanitize="):
                        sanitizers.update(arg.removeprefix("-fsanitize=").split(","))
                    elif arg.startswith("-fno-sanitize="):
                        disabled = arg.removeprefix("-fno-sanitize=").split(",")
                        if "all" in disabled:
                            sanitizers.clear()
                        else:
                            sanitizers.difference_update(disabled)
                self.assertTrue({"address", "undefined"} <= sanitizers)
                self.assertNotIn("-fanalyzer", args)

        executions = [(index, event) for index, event in enumerate(events)
                      if event["event"] == "harness"]
        analyzers = [index for index, event in enumerate(events)
                     if event["event"] == "analyzer"]
        self.assertEqual(len(executions), len(harnesses))
        self.assertEqual({event["name"] for _, event in executions}, harnesses)
        self.assertTrue(analyzers)
        for index, event in executions:
            with self.subTest(execution=event["name"]):
                asan = dict(item.split("=", 1) for item in (event["asan"] or "").split(":")
                            if "=" in item)
                ubsan = dict(item.split("=", 1) for item in (event["ubsan"] or "").split(":")
                             if "=" in item)
                self.assertLess(index, analyzers[0])
                self.assertEqual(asan.get("detect_leaks"), "0")
                self.assertEqual(ubsan.get("halt_on_error"), "1")

        for harness in harnesses:
            with self.subTest(failing_harness=harness):
                result, _, events = self.run_runtime_gate(failing_harness=harness)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertTrue(any(event["event"] == "harness" and event["name"] == harness
                                    for event in events))
                self.assertFalse(any(event["event"] == "analyzer" for event in events))


if __name__ == "__main__":
    unittest.main()
