"""Capability contract for the optional GCC analyzer portion of issue #84."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"

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
    output.touch()
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

    def test_runtime_harnesses_precede_the_optional_analyzer_gate(self):
        text = MAKEFILE.read_text(encoding="utf-8")
        recipe = text.split("codeql-alerts-test:\n", 1)[1].split(
            "\ncodeql-fanalyzer-test:",
            1,
        )[0]
        analyzer_gate = recipe.index("codeql-fanalyzer-test")

        for harness in (
            "$(CODEQL_TEST_DIR)/sio_protocol_host_test",
            "$(CODEQL_TEST_DIR)/runtime_bounds_host_test",
            "$(CODEQL_TEST_DIR)/png_bounds_host_test",
        ):
            with self.subTest(harness=harness):
                self.assertLess(recipe.index(harness), analyzer_gate)

        self.assertNotRegex(
            recipe,
            r"\$\(HOST_CC\)[^\n]*\s-fanalyzer(?:\s|\\)",
        )


if __name__ == "__main__":
    unittest.main()
