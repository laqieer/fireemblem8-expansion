"""TC-WORKFLOW-HOST-PYTHON-DEPS-001: real isolated installs, entirely owned fixtures."""

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
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


class ReporterExecutionTests(unittest.TestCase):
    def setUp(self):
        artifacts = ROOT / "build/test-artifacts"
        artifacts.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="reporter-execution-", dir=artifacts)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tests = self.root / "scripts/workflow_pilot/tests"
        self.tests.mkdir(parents=True)
        for directory in (self.root / "scripts", self.tests.parent, self.tests):
            (directory / "__init__.py").write_text("", encoding="ascii")
        self.launcher = self.tests.parent / "isolated_launcher.py"
        shutil.copyfile(ROOT / "scripts/workflow_pilot/isolated_launcher.py", self.launcher)
        (self.root / "records").mkdir()

    def run_launcher(self, *arguments):
        environment = dict(host_python.tool_environment(self.root))
        environment["GIT_DIR"] = str(self.root / "not-git-authority")
        return subprocess.run(
            [sys.executable, "-I", str(self.launcher), "reporter-tests", *arguments],
            cwd=self.root, env=environment, capture_output=True, text=True, timeout=20,
        )

    def test_complete_discovery_uses_two_distinct_live_isolated_processes(self):
        (self.tests / "test_concurrent.py").write_text(
            'import json, os, sys, time, unittest\nfrom pathlib import Path\n'
            'ROOT = Path(__file__).resolve().parents[3]\n'
            'def phase(name):\n'
            '    with (ROOT / "records" / (name + "-" + str(os.getpid()))).open("x") as stream:\n'
            '        stream.write(str(os.getpid()))\n'
            'def setUpModule(): phase("module-setup")\n'
            'def tearDownModule(): phase("module-teardown")\n'
            'class Cases(unittest.TestCase):\n'
            '    @classmethod\n'
            '    def setUpClass(cls): phase("class-setup")\n'
            '    @classmethod\n'
            '    def tearDownClass(cls): phase("class-teardown")\n'
            '    def record(self, number):\n'
            '        pid = os.getpid()\n'
            '        records = ROOT / "records"\n'
            '        with (records / ("case-" + str(number))).open("x") as stream:\n'
            '            json.dump({"pid": pid, "ppid": os.getppid(), "isolated": sys.flags.isolated,\n'
            '                       "git": [name for name in os.environ if name.startswith("GIT_")]}, stream)\n'
            '        marker = records / ("pid-" + str(pid))\n'
            '        if marker.exists():\n'
            '            return\n'
            '        marker.touch()\n'
            '        deadline = time.monotonic() + 5\n'
            '        while time.monotonic() < deadline:\n'
            '            others = [int(path.name[4:]) for path in records.glob("pid-*")\n'
            '                      if path != marker]\n'
            '            if others:\n'
            '                os.kill(others[0], 0)\n'
            '                break\n'
            '            time.sleep(0.01)\n'
            '        else:\n'
            '            self.fail("no second live isolated reporter process")\n'
            '        (records / ("observed-" + str(pid))).touch()\n'
            '        while len(list(records.glob("observed-*"))) < 2:\n'
            '            self.assertLess(time.monotonic(), deadline)\n'
            '            time.sleep(0.01)\n'
            'def case(number):\n'
            '    return lambda self: self.record(number)\n'
            'for number in range(6):\n'
            '    setattr(Cases, "test_" + str(number), case(number))\n',
            encoding="ascii",
        )
        completed = self.run_launcher()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        records = {
            path.name: json.loads(path.read_text())
            for path in (self.root / "records").glob("case-*")
        }
        self.assertEqual(set(records), {f"case-{number}" for number in range(6)})
        pids = {record["pid"] for record in records.values()}
        self.assertEqual(len(pids), 2)
        self.assertEqual(len({record["ppid"] for record in records.values()}), 1)
        for phase in ("module-setup", "module-teardown", "class-setup", "class-teardown"):
            self.assertEqual(
                {int(path.read_text()) for path in (self.root / "records").glob(phase + "-*")},
                pids,
            )
        self.assertTrue(all(record["isolated"] == 1 and not record["git"]
                            for record in records.values()))
        progress = [
            line for line in completed.stderr.splitlines()
            if "test_" in line or line.endswith(" ok") or "Ran " in line
        ]
        self.assertTrue(progress, completed.stderr)
        self.assertTrue(
            all(re.match(r"\[reporter \d+\] ", line) for line in progress),
            completed.stderr,
        )
        self.assertEqual(
            {int(re.match(r"\[reporter (\d+)\]", line).group(1)) for line in progress}, pids,
        )

    def test_one_assertion_fails_the_parent_without_omitting_other_tests(self):
        (self.tests / "test_failure.py").write_text(
            'import os, unittest\nfrom pathlib import Path\n'
            'ROOT = Path(__file__).resolve().parents[3]\n'
            'class Cases(unittest.TestCase):\n'
            '    def record(self, number):\n'
            '        with (ROOT / "records" / str(number)).open("x") as stream:\n'
            '            stream.write(str(os.getpid()))\n'
            '        self.assertNotEqual(number, 1, "deliberate assertion failure")\n'
            'def case(number): return lambda self: self.record(number)\n'
            'for number in range(6):\n'
            '    setattr(Cases, "test_" + str(number), case(number))\n',
            encoding="ascii",
        )
        completed = self.run_launcher()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("deliberate assertion failure", completed.stderr)
        self.assertEqual(
            {path.name for path in (self.root / "records").iterdir()},
            {str(number) for number in range(6)},
        )

    def test_abnormal_reporter_process_exit_fails_the_parent(self):
        (self.tests / "test_exit.py").write_text(
            'import os, unittest\nfrom pathlib import Path\n'
            'ROOT = Path(__file__).resolve().parents[3]\n'
            'class Cases(unittest.TestCase):\n'
            '    def test_abnormal(self): os._exit(7)\n'
            '    def test_other(self): (ROOT / "records" / "other").write_text(str(os.getpid()))\n',
            encoding="ascii",
        )
        completed = self.run_launcher()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("exited 7", completed.stderr)
        self.assertTrue((self.root / "records/other").is_file())

    def test_zero_exit_before_assigned_cases_complete_is_not_success(self):
        (self.tests / "test_early_zero.py").write_text(
            'import os, unittest\nfrom pathlib import Path\n'
            'ROOT = Path(__file__).resolve().parents[3]\n'
            'class Cases(unittest.TestCase):\n'
            '    def record(self, number):\n'
            '        (ROOT / "records" / str(number)).write_text(str(os.getpid()))\n'
            '        os._exit(0)\n'
            'def case(number): return lambda self: self.record(number)\n'
            'for number in range(6):\n'
            '    setattr(Cases, "test_" + str(number), case(number))\n',
            encoding="ascii",
        )
        completed = self.run_launcher()
        self.assertNotEqual(completed.returncode, 0)
        self.assertLess(len(list((self.root / "records").iterdir())), 6)

    def test_load_tests_instance_configuration_is_preserved(self):
        (self.tests / "test_configured.py").write_text(
            'import unittest\n'
            'class Configured(unittest.TestCase):\n'
            '    value = 1\n'
            '    def test_input(self):\n'
            '        self.assertEqual(self.value, 1, "configured failing input")\n'
            'def load_tests(loader, tests, pattern):\n'
            '    case = Configured("test_input")\n'
            '    case.value = 0\n'
            '    return unittest.TestSuite([case])\n',
            encoding="ascii",
        )
        completed = self.run_launcher()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("configured failing input", completed.stderr)

    def test_child_discovery_must_match_the_complete_assigned_set(self):
        (self.tests / "test_drift.py").write_text(
            'import multiprocessing, unittest\nfrom pathlib import Path\n'
            'ROOT = Path(__file__).resolve().parents[3]\n'
            'class Cases(unittest.TestCase):\n'
            '    def test_a(self): (ROOT / "records/ran").touch()\n'
            '    def test_b(self): (ROOT / "records/also-ran").touch()\n'
            'def load_tests(loader, tests, pattern):\n'
            '    names = ["test_a", "test_b"]\n'
            '    if multiprocessing.current_process().name != "MainProcess":\n'
            '        names = names[:1]\n'
            '    return unittest.TestSuite(Cases(name) for name in names)\n',
            encoding="ascii",
        )
        completed = self.run_launcher()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("child discovery differs", completed.stderr)
        self.assertEqual(list((self.root / "records").iterdir()), [])

    def test_normal_fixture_skip_is_preserved(self):
        for fixture in ("class", "module"):
            with self.subTest(fixture=fixture):
                source = (
                    'import unittest\n'
                    'class Cases(unittest.TestCase):\n'
                    '    def test_a(self): self.fail("skipped fixture must not run")\n'
                    '    def test_b(self): self.fail("skipped fixture must not run")\n'
                )
                if fixture == "class":
                    source += (
                        '    @classmethod\n'
                        '    def setUpClass(cls): raise unittest.SkipTest("legitimate fixture skip")\n'
                    )
                else:
                    source += (
                        'def setUpModule(): raise unittest.SkipTest("legitimate fixture skip")\n'
                    )
                (self.tests / "test_fixture_skip.py").write_text(source, encoding="ascii")
                completed = self.run_launcher()
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertIn("legitimate fixture skip", completed.stderr)

    def test_stopped_suite_is_not_partial_success(self):
        for action in ("result.stop(); return result", "return result"):
            for fixture_skip in (False, True):
                with self.subTest(action=action, fixture_skip=fixture_skip):
                    source = (
                        'import unittest\n'
                        'class Stopped(unittest.TestCase):\n'
                        '    def test_only(self): self.fail("unrun case must not pass")\n'
                        f'    def run(self, result): {action}\n'
                    )
                    if fixture_skip:
                        source += (
                            'class A_Skipped(unittest.TestCase):\n'
                            '    @classmethod\n'
                            '    def setUpClass(cls): raise unittest.SkipTest("fixture control")\n'
                            '    def test_a(self): self.fail("must stay skipped")\n'
                            '    def test_b(self): self.fail("must stay skipped")\n'
                        )
                    (self.tests / "test_stopped.py").write_text(source, encoding="ascii")
                    completed = self.run_launcher()
                    self.assertNotEqual(completed.returncode, 0)

    def test_fixture_reentry_is_scoped_to_its_actual_invocation(self):
        (self.tests / "reentry_other.py").write_text(
            'import unittest\n'
            'class B(unittest.TestCase):\n'
            '    def test_ok(self): pass\n'
            'class Fillers(unittest.TestCase):\n'
            '    def test_a(self): pass\n'
            '    def test_b(self): pass\n'
            '    def test_c(self): pass\n',
            encoding="ascii",
        )
        for fixture in ("class", "module"):
            for noop in (False, True):
                with self.subTest(fixture=fixture, noop=noop):
                    source = (
                        'import unittest\nfrom pathlib import Path\n'
                        'from .reentry_other import Fillers\n'
                        f'NOOP = {noop!r}\n'
                        'class A(unittest.TestCase):\n'
                        '    visits = 0\n'
                        '    def test_first(self):\n'
                        '        (Path(__file__).resolve().parents[3] / "records/first").touch()\n'
                        '    def test_last(self): self.fail("later invocation must be skipped")\n'
                        '    def run(self, result):\n'
                        '        if NOOP and self._testMethodName == "test_first": return result\n'
                        '        return super().run(result)\n'
                    )
                    if fixture == "class":
                        source += (
                            '    @classmethod\n'
                            '    def setUpClass(cls):\n'
                            '        cls.visits += 1\n'
                            '        if cls.visits == 2: raise unittest.SkipTest("later class invocation")\n'
                            'class B(unittest.TestCase):\n'
                            '    def test_ok(self): pass\n'
                        )
                    else:
                        source += (
                            'from .reentry_other import B\n'
                            'visits = 0\n'
                            'def setUpModule():\n'
                            '    global visits\n'
                            '    visits += 1\n'
                            '    if visits == 2: raise unittest.SkipTest("later module invocation")\n'
                        )
                    source += (
                        'def load_tests(loader, tests, pattern):\n'
                        '    return unittest.TestSuite([\n'
                        '        A("test_first"), Fillers("test_a"), B("test_ok"),\n'
                        '        Fillers("test_b"), A("test_last"), Fillers("test_c")])\n'
                    )
                    (self.tests / "test_reentry.py").write_text(source, encoding="ascii")
                    marker = self.root / "records/first"
                    marker.unlink(missing_ok=True)
                    completed = self.run_launcher()
                    self.assertEqual(completed.returncode, int(noop), completed.stderr)
                    self.assertEqual(marker.exists(), not noop)

    def test_single_discovered_case_is_not_dropped(self):
        (self.tests / "test_single.py").write_text(
            'import os, sys, unittest\nfrom pathlib import Path\n'
            'class Single(unittest.TestCase):\n'
            '    def test_only(self):\n'
            '        self.assertEqual(sys.flags.isolated, 1)\n'
            '        (Path(__file__).resolve().parents[3] / "records/only").write_text(str(os.getpid()))\n',
            encoding="ascii",
        )
        completed = self.run_launcher()
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertTrue((self.root / "records/only").is_file())

    def test_duplicate_discovery_is_rejected_before_execution(self):
        (self.tests / "test_duplicate.py").write_text(
            'import unittest\nfrom pathlib import Path\n'
            'class Single(unittest.TestCase):\n'
            '    def test_only(self):\n'
            '        (Path(__file__).resolve().parents[3] / "records/ran").touch()\n'
            'def load_tests(loader, tests, pattern):\n'
            '    return unittest.TestSuite([Single("test_only"), Single("test_only")])\n',
            encoding="ascii",
        )
        completed = self.run_launcher()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("unique test IDs", completed.stderr)
        self.assertEqual(list((self.root / "records").iterdir()), [])

    def test_empty_or_broken_discovery_and_extra_arguments_are_not_success(self):
        empty = self.run_launcher()
        self.assertNotEqual(empty.returncode, 0)
        self.assertIn("nonempty", empty.stderr)
        (self.tests / "test_broken.py").write_text("def broken syntax\n", encoding="ascii")
        broken = self.run_launcher()
        self.assertNotEqual(broken.returncode, 0)
        self.assertIn("Failed to import test module", broken.stderr)
        closed = self.run_launcher("--test", "anything")
        self.assertNotEqual(closed.returncode, 0)
        self.assertIn("accepts no arguments", closed.stderr)
        self.assertEqual(list((self.root / "records").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
