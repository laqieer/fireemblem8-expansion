"""Bounded exact-object preparation and matched-interpreter closure admission."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest import mock

from scripts.workflow_pilot import sealed_capsule as capsule
from scripts.workflow_pilot.tests import test_sealed_capsule as fixtures


ROOT = Path(__file__).resolve().parents[3]


@unittest.skipUnless(
    sys.platform == "linux" and os.uname().machine == "x86_64",
    "Linux x86-64 capsule preparation")
class CapsulePreparationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = fixtures.SealedCapsuleTests
        cls.fixture.setUpClass()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.tearDownClass()

    def spec(self, source):
        self.fixture.write("checks/preparation_case.py", source.encode())
        revision = self.fixture.commit()
        return capsule.CapsuleSpec(
            trees={"base": revision}, programs={"probe": "checks/preparation_case.py"})

    def test_elapsed_time_is_shared_across_real_git_reads_and_cache_access(self):
        now = [0.0]
        with (mock.patch.object(time, "monotonic", new=lambda: now[0]),
              mock.patch.object(capsule, "_git", wraps=capsule._git) as read):
            source = capsule._GitSource(self.fixture.root)
            expected = source.get("commit", self.fixture.base)
            self.assertEqual(source.get("commit", self.fixture.base), expected)
            self.assertEqual(read.call_count, 2)
            now[0] = capsule.MAX_PREPARATION_SECONDS + 1
            for oid in (self.fixture.origin, self.fixture.base):
                with self.subTest(oid=oid), self.assertRaisesRegex(capsule.CapsuleError, "aggregate preparation"):
                    source.get("commit", oid)
            self.assertEqual(read.call_count, 2)

    def test_small_process_budget_stops_new_spawns_but_not_cached_objects(self):
        with (mock.patch.object(capsule, "MAX_PREPARATION_GIT_PROCESSES", 2),
              mock.patch.object(subprocess, "Popen", wraps=subprocess.Popen) as launch):
            source = capsule._GitSource(self.fixture.root)
            expected = source.get("commit", self.fixture.base)
            self.assertEqual(source.get("commit", self.fixture.base), expected)
            self.assertEqual(launch.call_count, 2)
            with self.assertRaisesRegex(capsule.CapsuleError, "Git process budget"):
                source.get("commit", self.fixture.origin)
            self.assertEqual(launch.call_count, 2)

    def test_remaining_aggregate_budget_bounds_a_real_stalled_child_and_reaps_it(self):
        children, waits = [], []
        real_popen, collect = subprocess.Popen, capsule._collect

        def stalled(command, **options):
            child = real_popen(
                [capsule.PYTHON, "-I", "-c", "import time; time.sleep(30)"], **options)
            children.append(child)
            return child

        def bounded(process, timeout, *args, **kwargs):
            waits.append(timeout)
            return collect(process, timeout, *args, **kwargs)

        with (
            mock.patch.object(capsule, "MAX_PREPARATION_SECONDS", 0.2),
            mock.patch.object(subprocess, "Popen", side_effect=stalled),
            mock.patch.object(capsule, "_collect", side_effect=bounded),
        ):
            with self.assertRaises(capsule.CapsuleError):
                capsule._GitSource(self.fixture.root)
        self.assertEqual(len(children), 1)
        self.assertEqual(len(waits), 1)
        self.assertGreater(waits[0], 0)
        self.assertLessEqual(waits[0], 0.2)
        self.assertIsNotNone(children[0].returncode)
        with self.assertRaises(ChildProcessError):
            os.waitid(os.P_PID, children[0].pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        self.assertTrue(children[0].stdout.closed and children[0].stderr.closed)

    def test_expired_preparation_is_not_reset_before_capsule_descriptor_creation(self):
        now, labels = [0.0], []
        make_bundle, sealed = capsule._make_bundle, capsule.SealedBytes

        def delayed(*args, **kwargs):
            raw = make_bundle(*args, **kwargs)
            now[0] = capsule.MAX_PREPARATION_SECONDS + 1
            return raw

        def seal(raw, label, limit):
            labels.append(label)
            return sealed(raw, label, limit)

        with (mock.patch.object(time, "monotonic", new=lambda: now[0]),
              mock.patch.object(capsule, "_make_bundle", side_effect=delayed),
              mock.patch.object(capsule, "SealedBytes", side_effect=seal)):
            with self.assertRaisesRegex(capsule.CapsuleError, "aggregate preparation"):
                capsule.prepare(self.fixture.root, self.fixture.spec)
        self.assertEqual(labels, ["python-image"])

    def test_missing_required_stdlib_and_compile_errors_precede_capsule_resources(self):
        self.fixture.write("checks/compile_helper.py", b"return 1\n")
        for source, reason in (
            ("import winreg\ndef capsule_main(request, context): return True\n", "unavailable"),
            ("import xml.etree.no_such_module\ndef capsule_main(request, context): return True\n", "unavailable"),
            ("return 1\ndef capsule_main(request, context): return True\n", "invalid trusted Python"),
            ("nonlocal absent\ndef capsule_main(request, context): return True\n", "invalid trusted Python"),
            ("from checks import compile_helper\n"
             "def capsule_main(request, context): return True\n", "invalid trusted Python"),
        ):
            spec = self.spec(source)
            labels, commands = [], []
            sealed, popen = capsule.SealedBytes, subprocess.Popen

            def seal(raw, label, limit):
                labels.append(label)
                return sealed(raw, label, limit)

            def launch(command, **options):
                commands.append(options.get("pass_fds", ()))
                return popen(command, **options)

            with (self.subTest(source=source),
                  mock.patch.object(capsule, "SealedBytes", side_effect=seal),
                  mock.patch.object(subprocess, "Popen", side_effect=launch)):
                with self.assertRaisesRegex(capsule.CapsuleError, reason):
                    capsule.prepare(self.fixture.root, spec)
            self.assertEqual(labels, ["python-image"])
            self.assertFalse(any(len(fds) > 1 for fds in commands))

    def test_invalid_runtime_compile_is_rejected_before_capsule_descriptors(self):
        self.fixture.write(capsule.RUNTIME_PATH, b"return None\n")
        labels, sealed = [], capsule.SealedBytes

        def seal(raw, label, limit):
            labels.append(label)
            return sealed(raw, label, limit)

        try:
            spec = self.spec("def capsule_main(request, context): return True\n")
            with mock.patch.object(capsule, "SealedBytes", side_effect=seal):
                with self.assertRaisesRegex(capsule.CapsuleError, "self-contained capsule runtime"):
                    capsule.prepare(self.fixture.root, spec)
            self.assertEqual(labels, ["python-image"])
        finally:
            self.fixture.write(capsule.RUNTIME_PATH, self.fixture.runtime)
            self.fixture.commit()

    def test_os_path_alias_and_platform_extension_execute_without_cache_expansion(self):
        spec = self.spec(
            "import os.path\nfrom os.path import basename\nfrom os import path\n"
            "import importlib, sys, _ctypes\n"
            "def capsule_main(request, context):\n"
            "    return {'static': os.path.basename('a/leaf'), 'from': basename('a/other'),\n"
            "            'attribute': path.splitext('a.py')[1],\n"
            "            'dynamic': importlib.import_module('os.path').join('a','b'),\n"
            "            'private_alias_cached': 'posixpath' in sys.modules,\n"
            "            'extension': _ctypes.__name__}\n")
        with capsule.prepare(self.fixture.root, spec) as prepared:
            self.assertEqual(prepared.execute("probe", {}).value, {
                "static": "leaf", "from": "other", "attribute": ".py", "dynamic": "a/b",
                "private_alias_cached": False, "extension": "_ctypes",
            })

    @unittest.skipUnless(sys.version_info >= (3, 12), "PEP 695 needs the admitted Python 3.12+ pair")
    def test_matching_current_interpreter_compiles_and_executes_its_newer_syntax(self):
        spec = self.spec("type Alias = int\n"
                         "def capsule_main(request, context):\n    return Alias.__value__.__name__\n")
        with capsule.prepare(self.fixture.root, spec) as prepared:
            self.assertEqual(prepared.execute("probe", {}).value, "int")

    def test_different_preparer_executable_rejects_before_git_or_sealed_resources(self):
        identity = capsule._interpreter_identity

        def different(path):
            value = identity(path)
            return ("different",) if path == "/proc/self/exe" else value

        with (
            mock.patch.object(capsule, "_interpreter_identity", side_effect=different),
            mock.patch.object(capsule, "_make_bundle", side_effect=AssertionError) as bundle,
            mock.patch.object(os, "memfd_create", side_effect=AssertionError) as create,
            mock.patch.object(subprocess, "Popen", side_effect=AssertionError) as launch,
        ):
            with self.assertRaises(capsule.CapsuleUnavailable):
                capsule.prepare(self.fixture.root, self.fixture.spec)
            for operation in (bundle, create, launch):
                operation.assert_not_called()

    def test_different_version_or_stdlib_report_cannot_admit_a_pairing(self):
        for field, value in (("version", [3, 10, 0]), ("stdlib", "/different/stdlib"),
                             ("implementation", "different")):
            report = capsule._python_report()
            report["runtime"][field] = value
            if field == "version":
                report["version"] = value[:2]
            with (
                self.subTest(field=field),
                mock.patch.object(subprocess, "Popen"),
                mock.patch.object(capsule, "_collect", return_value=(0, capsule.canonical(report), b"")),
                mock.patch.object(capsule, "_make_bundle", side_effect=AssertionError) as bundle,
            ):
                with self.assertRaises(capsule.CapsuleUnavailable):
                    capsule.prepare(self.fixture.root, self.fixture.spec)
                bundle.assert_not_called()

    def test_real_copied_interpreter_cannot_act_as_a_preparer(self):
        source = (
            "import pathlib,sys,json\n"
            f"sys.path.insert(0,{str(ROOT)!r})\n"
            "from scripts.workflow_pilot import sealed_capsule as capsule\n"
            "spec=capsule.CapsuleSpec(trees={'base':'a'*40},programs={'probe':'checks/probe.py'})\n"
            "try:\n"
            f"    capsule.prepare(pathlib.Path({str(self.fixture.root)!r}),spec)\n"
            "except capsule.CapsuleUnavailable as error:\n"
            "    print(json.dumps({'unavailable':error.disposition}))\n"
            "else:\n"
            "    raise AssertionError('different executable was admitted')\n")
        with capsule._ExecutionInterpreter() as interpreter, capsule._Child() as owner:
            process, _ = interpreter.launch(source, [], owner=owner)
            status, stdout, stderr = capsule._collect(process, 5, 4096)
        self.assertEqual((status, stderr), (0, b""))
        self.assertEqual(json.loads(stdout), {"unavailable": "sealed-capsule-unavailable"})


if __name__ == "__main__":
    unittest.main()
