"""TC-WORKFLOW-HOST-PYTHON-DEPS-001: real isolated installs, entirely owned fixtures."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest
from unittest import mock
import uuid

from scripts import host_python


ROOT = Path(__file__).resolve().parents[3]
BOOTSTRAP = ROOT / "scripts" / "host_python.py"
LOCK = ROOT / ".github" / "requirements" / "host-tests.txt"


class HostPythonTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / "build" / ("host-python-test-" + uuid.uuid4().hex)
        (self.root / ".bootstrap" / "home").mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.root)
        self.tools = host_python.tool_environment(self.root)
        self.wheels = Path(sys.prefix) / "wheelhouse"

    def run_python(self, *arguments, python=sys.executable, environment=None):
        return subprocess.run(
            [str(python), *map(str, arguments)],
            cwd=ROOT,
            env=self.tools if environment is None else environment,
            text=True,
            capture_output=True,
            check=False,
        )

    def create(self, path, *, wheels=None, bootstrap=BOOTSTRAP, environment=None):
        self.assertTrue(
            self.wheels.is_dir(),
            "run this suite with build/host-python/bin/python3 after scripts/host_python.py create",
        )
        return self.run_python(
            "-I", bootstrap, "create", "--environment", path,
            "--wheelhouse", self.wheels if wheels is None else wheels,
            environment=environment,
        )

    def clean_python(self, name="clean"):
        path = self.root / name
        completed = self.run_python("-I", "-m", "venv", "--without-pip", path)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return path / "bin" / "python3"

    def assert_failed(self, completed, message):
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertIn(message, completed.stdout + completed.stderr)

    def test_current_isolated_interpreter_has_exact_closure_draft_and_formats(self):
        completed = self.run_python("-I", BOOTSTRAP, "check")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["environment"], str(Path(sys.prefix).resolve()))
        self.assertEqual(result["packages"], host_python.locked_versions(LOCK.read_text()))
        self.assertEqual(result["schema_draft"], "2020-12")
        self.assertEqual(result["formats"], ["date-time"])

    def test_draft_and_format_adversaries_are_executed_not_silently_skipped(self):
        from jsonschema import Draft202012Validator, FormatChecker

        host_python.check_schema_support()
        with mock.patch.dict(FormatChecker.checkers, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "missing required.*date-time"):
                host_python.check_schema_support()
        with mock.patch.dict(
            FormatChecker.checkers, {"date-time": (lambda value: True, ())}
        ):
            with self.assertRaisesRegex(ValueError, "accepted invalid input"):
                host_python.check_schema_support()
        with mock.patch.object(Draft202012Validator, "is_valid", return_value=True):
            with self.assertRaisesRegex(ValueError, "accepted invalid input"):
                host_python.check_schema_support()

    def test_lock_parser_rejects_unpinned_unhashed_duplicate_or_external_inputs(self):
        text = LOCK.read_text()
        valid = host_python.locked_versions(text)
        entries = re.findall(
            r"^[a-z][a-z0-9-]*==[^\n]+\\\n[^\n]+", text, re.MULTILINE
        )
        self.assertEqual(
            host_python.locked_versions("# reordered, same artifacts\n\n" + "\n".join(reversed(entries))),
            valid,
        )
        for invalid in (
            "",
            text + "\n" + entries[0],
            text.replace("==", ">=", 1),
            re.sub(r"--hash=sha256:[0-9a-f]{64}", "", text, count=1),
            text.replace("--hash=sha256:", "--hash=md5:", 1),
            text + "\n--index-url https://example.invalid\n",
            text + "\n-r other.txt\n",
            text + "\njsonschema @ https://example.invalid/package.whl\n",
            text + "\nunfinished==1 \\\n",
        ):
            with self.subTest(invalid=invalid[-100:]):
                with self.assertRaises(ValueError):
                    host_python.locked_versions(invalid)

    def test_unsupported_profiles_fail_before_creating_environment(self):
        variants = (
            mock.patch.object(host_python.sys, "version_info", (3, 13, 0)),
            mock.patch.object(host_python.sys, "platform", "darwin"),
            mock.patch.object(host_python.platform, "machine", return_value="aarch64"),
            mock.patch.object(host_python.platform, "libc_ver", return_value=("musl", "1.2")),
            mock.patch.object(host_python.platform, "libc_ver", return_value=("glibc", "2.16")),
        )
        for variant in variants:
            with variant, self.assertRaisesRegex(ValueError, "supported host profile"):
                host_python.create_environment(self.root / "unsupported")
            self.assertFalse((self.root / "unsupported").exists())

    def test_existing_external_and_symlink_environments_are_not_modified(self):
        existing = self.root / "existing"
        existing.mkdir()
        sentinel = existing / "keep"
        sentinel.write_bytes(b"owned pre-existing content")
        alias = self.root / "alias"
        alias.symlink_to(existing, target_is_directory=True)
        for path in (existing, alias, alias / "child", ROOT / "not-build"):
            with self.subTest(path=path):
                with self.assertRaises((FileExistsError, ValueError)):
                    host_python.create_environment(path)
                self.assertEqual(sentinel.read_bytes(), b"owned pre-existing content")
                self.assertFalse((existing / "child").exists())
        self.assertFalse((ROOT / "not-build").exists())

    def test_clean_environment_reproduces_missing_dependency(self):
        python = self.clean_python()
        completed = self.run_python("-I", "-c", "import jsonschema", python=python)
        self.assert_failed(completed, "ModuleNotFoundError: No module named 'jsonschema'")
        checked = self.run_python("-I", BOOTSTRAP, "check", python=python)
        self.assert_failed(checked, "host dependency versions differ from lock")
        nonisolated = self.run_python(BOOTSTRAP, "check", python=python)
        self.assert_failed(nonisolated, "isolated Python startup (-I) is required")

    def test_complete_offline_bootstrap_ignores_ambient_package_configuration(self):
        environment = dict(self.tools)
        config = self.root / "pip.conf"
        config.write_text("[global]\nindex-url = https://example.invalid\n")
        forbidden = self.root / "not-an-install-target"
        environment.update(
            PIP_CONFIG_FILE=str(config),
            PIP_INDEX_URL="https://example.invalid",
            PIP_EXTRA_INDEX_URL="https://example.invalid",
            PIP_TARGET=str(forbidden),
            PIP_USER="1",
            PYTHONUSERBASE=str(forbidden),
        )
        target = self.root / "installed"
        completed = self.create(target, environment=environment)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        checked = self.run_python("-I", BOOTSTRAP, "check", python=target / "bin/python3")
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(
            json.loads(checked.stdout)["packages"],
            host_python.locked_versions(LOCK.read_text()),
        )
        self.assertFalse(forbidden.exists())

    def test_wrong_wheel_hash_is_rejected_before_installation(self):
        wheels = self.root / "wheels"
        shutil.copytree(self.wheels, wheels)
        wheel = next(wheels.glob("jsonschema-*.whl"))
        wheel.write_bytes(wheel.read_bytes() + b"corrupt artifact")
        target = self.root / "rejected"
        completed = self.create(target, wheels=wheels)
        self.assert_failed(completed, "DO NOT MATCH THE HASHES")
        imported = self.run_python("-I", "-c", "import jsonschema", python=target / "bin/python3")
        self.assert_failed(imported, "ModuleNotFoundError")

    def test_missing_and_incompatible_wheels_fail_offline(self):
        for name in ("missing", "incompatible"):
            with self.subTest(name=name):
                wheels = self.root / name
                shutil.copytree(self.wheels, wheels)
                wheel = next(wheels.glob("rpds_py-*.whl"))
                if name == "missing":
                    wheel.unlink()
                else:
                    wheel.rename(wheel.with_name(wheel.name.replace("-cp312-cp312-", "-cp311-cp311-")))
                completed = self.create(self.root / (name + "-venv"), wheels=wheels)
                self.assert_failed(completed, "No matching distribution found for rpds-py")

    def test_incomplete_optional_or_transitive_closure_cannot_pass(self):
        for missing, diagnostic in (
            ("rfc3339-validator", "missing required JSON Schema format validators"),
            ("six", "requires six, which is not installed"),
        ):
            with self.subTest(missing=missing):
                fixture = self.root / missing
                (fixture / "scripts").mkdir(parents=True)
                (fixture / ".github/requirements").mkdir(parents=True)
                bootstrap = fixture / "scripts/host_python.py"
                shutil.copy2(BOOTSTRAP, bootstrap)
                text = re.sub(
                    rf"^{missing}==[^\n]+\\\n[^\n]+\n", "",
                    LOCK.read_text(), count=1, flags=re.MULTILINE,
                )
                (fixture / ".github/requirements/host-tests.txt").write_text(text)
                completed = self.create(
                    fixture / "build/venv", bootstrap=bootstrap,
                )
                self.assert_failed(completed, diagnostic)

    def test_user_site_only_install_does_not_satisfy_isolated_environment(self):
        python = self.clean_python()
        userbase = self.root / "userbase"
        usersite = userbase / "lib/python3.12/site-packages"
        completed = subprocess.run(
            host_python.pip_command(
                Path(sys.executable), "install", "--target", str(usersite),
                "--no-index", "--find-links", str(self.wheels), "--require-hashes",
                "--only-binary=:all:", "--no-deps", "-r", str(LOCK),
            ),
            env=self.tools, text=True, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        environment = {**self.tools, "PYTHONUSERBASE": str(userbase)}
        visible = self.run_python(
            "-c", "import jsonschema; print(jsonschema.__file__)",
            python="/usr/bin/python3", environment=environment,
        )
        self.assertEqual(visible.returncode, 0, visible.stderr)
        self.assertTrue(Path(visible.stdout.strip()).is_relative_to(usersite))
        isolated = self.run_python(
            "-I", "-c", "import jsonschema", python=python, environment=environment,
        )
        self.assert_failed(isolated, "ModuleNotFoundError")
        checked = self.run_python(
            "-I", BOOTSTRAP, "check", python=python, environment=environment,
        )
        self.assert_failed(checked, "host dependency versions differ from lock")

    def test_system_site_opt_in_is_rejected(self):
        python = self.clean_python()
        configuration = python.parent.parent / "pyvenv.cfg"
        configuration.write_text(
            configuration.read_text().replace(
                "include-system-site-packages = false",
                "include-system-site-packages = true",
            )
        )
        checked = self.run_python("-I", BOOTSTRAP, "check", python=python)
        self.assert_failed(checked, "system site packages must be disabled")


if __name__ == "__main__":
    unittest.main()
