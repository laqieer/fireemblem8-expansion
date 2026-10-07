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
            "def build_arg_parser(*, parser_class=argparse.ArgumentParser):\n"
            "    parser = parser_class()\n"
            "    parser.add_argument('--repository-root', required=True)\n"
            "    parser.add_argument('--changed', action='append', default=[])\n"
            "    parser.add_argument('--revision', default='HEAD')\n"
            "    parser.add_argument('--base-revision')\n"
            "    return parser\n"
            "def parse_args(arguments):\n"
            "    return build_arg_parser().parse_args(arguments)\n"
            "def run_parsed(parsed):\n"
            "    result = subprocess.run(['/usr/bin/make', '-f', 'payload.mk', 'all'],\n"
            "                            capture_output=True, env=os.environ)\n"
            "    print(json.dumps({'controls': {key: os.environ[key] for key in "
            + repr(CONTROLS)
            + " if key in os.environ}, 'repository_root': str(parsed.repository_root), "
            "'changed': parsed.changed}))\n"
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

    def launch(self, environment, *, mode="check", flags=("-I", "-S", "-B"), extra=()):
        return subprocess.run(
            [sys.executable, *flags, str(self.launcher), mode,
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
        self.assertEqual(json.loads(result.stdout)["controls"], {})

    def test_standalone_removes_all_make_controls_without_skipping_payload(self):
        for name in CONTROLS:
            with self.subTest(control=name):
                (self.root / "payload.marker").unlink(missing_ok=True)
                (self.root / "preloaded.marker").unlink(missing_ok=True)
                value = str(self.poison) if name == "MAKEFILES" else "-n"
                result = self.launch({name: value})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)["controls"], {})
                self.assertEqual((self.root / "payload.marker").read_text(), "payload-ran")
                self.assertFalse((self.root / "preloaded.marker").exists())
                (self.root / "payload.marker").unlink()

    def test_reporter_import_and_its_subprocess_receive_scrubbed_controls(self):
        names = (*CONTROLS, "GIT_DIR", "GIT_TEST_CONTROL", "BASH_ENV", "ENV")
        reporter = self.launcher.with_name("reporter.py")
        reporter.write_text(
            "import json, os, subprocess\nfrom pathlib import Path\n"
            "Path(__file__).with_name('import.json').write_text(json.dumps({"
            "'controls': {key: os.environ[key] for key in " + repr(names)
            + " if key in os.environ}, 'kept': os.environ.get('IMPORT_KEEP')}))\n"
            "subprocess.run(['/bin/bash', '-c', 'printf import-ran > import-process.marker'], "
            "check=True)\n" + reporter.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        hook = self.root / "import-env.sh"
        hook.write_text("printf inherited > import-env.marker\n", encoding="ascii")
        environment = {name: "inherited" for name in names}
        environment.update({"MAKEFILES": str(self.poison), "BASH_ENV": str(hook),
                            "ENV": str(hook), "IMPORT_KEEP": "kept"})
        subprocess.run(
            ["/bin/bash", "-c", "true"], cwd=self.root,
            env={**self.environment, **environment}, check=True, timeout=10,
        )
        marker = self.root / "import-env.marker"
        self.assertEqual(marker.read_text(), "inherited")
        marker.unlink()
        for mode, extra in (("check", ()), ("resolve", ("--changed", "src/data/file.json"))):
            with self.subTest(mode=mode):
                for name in ("import-env.marker", "import-process.marker", "payload.marker"):
                    (self.root / name).unlink(missing_ok=True)
                result = self.launch(environment, mode=mode, extra=extra)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse(marker.exists())
                observed = json.loads(reporter.with_name("import.json").read_text())
                self.assertEqual(observed, {"controls": {}, "kept": "kept"})
                self.assertEqual((self.root / "import-process.marker").read_text(), "import-ran")
                self.assertEqual((self.root / "payload.marker").read_text(), "payload-ran")
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

    def test_check_mode_rejects_parser_accepted_changed_spellings_before_payload(self):
        for extra in (("--changed=src/data/file.json",), ("--cha", "src/data/file.json")):
            with self.subTest(extra=extra):
                (self.root / "payload.marker").unlink(missing_ok=True)
                result = self.launch({}, extra=extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("check mode does not accept --changed", result.stderr)
                self.assertFalse((self.root / "payload.marker").exists())

    def test_resolve_mode_accepts_equals_and_abbreviated_changed_options(self):
        result = self.launch(
            {}, mode="resolve",
            extra=("--changed=src/data/file.json", "--cha", "src/data/other.json"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / "payload.marker").read_text(), "payload-ran")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["controls"], {})
        self.assertEqual(payload["repository_root"], str(self.root))
        self.assertEqual(payload["changed"], ["src/data/file.json", "src/data/other.json"])

    def test_last_repository_root_value_controls_the_precheck(self):
        other = self.root / "other-root"
        other.mkdir()
        for extra in (
            ("--repository-root=" + str(other),),
            ("--repo=" + str(other),),
        ):
            with self.subTest(extra=extra):
                (self.root / "payload.marker").unlink(missing_ok=True)
                result = self.launch({}, extra=extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("controlled source root", result.stderr)
                self.assertFalse((self.root / "payload.marker").exists())

    def test_actual_reporter_keeps_relative_root_meaning_after_launcher_chdir(self):
        from scripts.validation_ownership.tests.report_fixture import ReportFixture

        fixture = ReportFixture()
        self.addCleanup(fixture.close)
        launcher = fixture.root / "scripts/validation_ownership/isolated_launcher.py"
        reports = []
        for root_argument in (str(fixture.root), fixture.root.name):
            result = subprocess.run(
                [sys.executable, "-I", "-S", "-B", str(launcher), "check",
                 "--repository-root", root_argument],
                cwd=fixture.root.parent, env=self.environment,
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertFalse(report["policy"]["narrowing_authorized"])
            self.assertEqual(report["coverage"]["tracked_paths"], report["coverage"]["owned_paths"])
            reports.append(report["coverage"])
        self.assertEqual(reports[0], reports[1])
        alias = fixture.root.parent / "alias"
        alias.symlink_to(fixture.root, target_is_directory=True)
        rejected = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(launcher), "check",
             "--repository-root", str(alias)],
            cwd=fixture.root.parent, env=self.environment,
            capture_output=True, text=True, timeout=60,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(rejected.stdout, "")
        self.assertIn("non-symlink directory", rejected.stderr)

    def test_owned_gate_starts_python_instead_of_make(self):
        gate = next(gate for gate in verify.gates(jobs=1) if gate.name == "validation-ownership-check")
        self.assertEqual(gate.command, [
            "/usr/bin/python3", "-I", "-S", "-B",
            "scripts/validation_ownership/isolated_launcher.py", "check",
            "--repository-root", "$GITHUB_WORKSPACE",
        ])
