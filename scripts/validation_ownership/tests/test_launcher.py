from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts.upstream_port import verify


ROOT = Path(__file__).resolve().parents[3]
CONTROLS = ("MAKEFILES", "MAKEFLAGS", "GNUMAKEFLAGS", "MAKEOVERRIDES", "MFLAGS")


class StandaloneLauncherTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / "build/test-artifacts/validation-ownership"
        scratch.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        package = self.root / "scripts/validation_ownership"
        package.mkdir(parents=True)
        self.launcher = package / "isolated_launcher.py"
        shutil.copyfile(ROOT / "scripts/validation_ownership/isolated_launcher.py", self.launcher)
        # Substitute only the graph payload; its real Make invocation makes
        # inherited preloads and dry-run controls observable at the boundary.
        (package / "reporter.py").write_text(
            "import argparse, json, os, subprocess\n"
            "def main(arguments):\n"
            "    parser = argparse.ArgumentParser()\n"
            "    parser.add_argument('--repository-root', required=True)\n"
            "    parser.parse_args(arguments)\n"
            "    result = subprocess.run(['/usr/bin/make', '-f', 'payload.mk', 'all'],\n"
            "                            capture_output=True, env=os.environ)\n"
            "    print(json.dumps({key: os.environ[key] for key in "
            + repr(CONTROLS)
            + " if key in os.environ}))\n"
            "    return result.returncode\n",
            encoding="utf-8",
        )
        (self.root / "payload.mk").write_text(
            "all:\n\t@printf payload-ran > payload.marker\n", encoding="ascii",
        )
        self.poison = self.root / "preload.mk"
        self.poison.write_text("$(file >preloaded.marker,preloaded)\n", encoding="ascii")
        self.environment = {
            key: value for key, value in os.environ.items()
            if key not in CONTROLS and not key.startswith("GIT_")
        }
        self.environment.update({"PATH": "/usr/bin:/bin", "LC_ALL": "C"})

    def launch(self, environment, *, flags=("-I", "-S", "-B"), extra=()):
        return subprocess.run(
            [sys.executable, *flags, str(self.launcher), "check",
             "--repository-root", str(self.root), *extra],
            cwd=self.root, env={**self.environment, **environment},
            capture_output=True, text=True, timeout=10,
        )

    def test_make_preload_is_real_and_standalone_scrubs_it_before_payload(self):
        environment = {"MAKEFILES": str(self.poison)}
        control = subprocess.run(
            ["/usr/bin/make", "-f", "payload.mk", "all"], cwd=self.root,
            env={**self.environment, **environment},
            capture_output=True, timeout=10,
        )
        self.assertEqual(control.returncode, 0)
        marker = self.root / "preloaded.marker"
        self.assertTrue(marker.is_file())
        marker.unlink()
        (self.root / "payload.marker").unlink()
        result = self.launch(environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual((self.root / "payload.marker").read_text(), "payload-ran")
        self.assertEqual(json.loads(result.stdout), {})

    def test_standalone_removes_all_make_controls_without_skipping_payload(self):
        for name in CONTROLS:
            with self.subTest(control=name):
                (self.root / "payload.marker").unlink(missing_ok=True)
                (self.root / "preloaded.marker").unlink(missing_ok=True)
                value = str(self.poison) if name == "MAKEFILES" else "-n"
                result = self.launch({name: value})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {})
                self.assertEqual((self.root / "payload.marker").read_text(), "payload-ran")
                self.assertFalse((self.root / "preloaded.marker").exists())
                (self.root / "payload.marker").unlink()

    def test_eval_is_not_a_standalone_argument_and_make_cannot_guard_it(self):
        expression = "--eval=$(file >evaluated.marker,evaluated)"
        control = subprocess.run(
            ["/usr/bin/make", expression, "-f", "payload.mk", "all"],
            cwd=self.root, env=self.environment, capture_output=True, timeout=10,
        )
        self.assertEqual(control.returncode, 0)
        marker = self.root / "evaluated.marker"
        self.assertTrue(marker.exists())
        marker.unlink()
        (self.root / "payload.marker").unlink()
        result = self.launch({}, extra=(expression,))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / "payload.marker").exists())

    def test_standalone_requires_isolated_no_site_startup(self):
        for flags in ((), ("-I",), ("-S",)):
            with self.subTest(flags=flags):
                (self.root / "payload.marker").unlink(missing_ok=True)
                result = self.launch({}, flags=flags)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "payload.marker").exists())

    def test_owned_gate_starts_python_instead_of_make(self):
        gate = next(gate for gate in verify.gates(jobs=1) if gate.name == "validation-ownership-check")
        self.assertEqual(gate.command, [
            "/usr/bin/python3", "-I", "-S", "-B",
            "scripts/validation_ownership/isolated_launcher.py", "check",
            "--repository-root", "$GITHUB_WORKSPACE",
        ])
