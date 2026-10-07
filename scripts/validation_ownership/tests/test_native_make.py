"""Single-invocation native evaluator admission; full qualification is separate."""

from pathlib import Path
from contextlib import contextmanager
import subprocess
import unittest
from unittest.mock import patch

from scripts.validation_ownership.authority import ENVIRONMENT
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.make_probe import (
    _executable_runtime, _make_interpreter, _make_runtime,
)


class NativeRuntimeTests(unittest.TestCase):
    @contextmanager
    def budget(self):
        budget = ProbeBudget()
        try:
            yield budget
        finally:
            budget.close()

    def test_actual_make_and_shell_images_have_complete_charged_runtime(self):
        for path in ("/usr/bin/make", "/usr/bin/sh"):
            with self.subTest(path=path), self.budget() as budget:
                runtime = dict(_executable_runtime(path, budget))
                self.assertEqual(runtime[path], Path(path).read_bytes())
                interpreter = _make_interpreter(runtime[path])
                self.assertIn(interpreter, runtime)
                self.assertGreaterEqual(len(runtime), 3)
                self.assertTrue(all(data.startswith(b"\x7fELF") for data in runtime.values()))
                self.assertGreaterEqual(budget.bytes["control"], sum(map(len, runtime.values())))
                self.assertEqual(budget.runs, 1)
        with self.budget() as budget:
            self.assertEqual(dict(_make_runtime(budget))["/usr/bin/make"], Path("/usr/bin/make").read_bytes())

    def test_loader_uses_captured_interpreter_clean_environment_and_no_candidate_cwd(self):
        with self.budget() as budget:
            original = budget.run
            with patch.object(budget, "run", wraps=original) as launch:
                runtime = dict(_executable_runtime("/usr/bin/sh", budget))
            interpreter = _make_interpreter(runtime["/usr/bin/sh"])
            launch.assert_called_once_with(
                [interpreter, "--list", "/usr/bin/sh"], env=ENVIRONMENT, cwd=Path("/"),
            )

    def test_untrusted_path_and_failed_or_unresolved_loader_refuse(self):
        with self.budget() as budget, patch.object(budget, "run") as launch:
            with self.assertRaisesRegex(MakeProbeError, "trusted system"):
                _executable_runtime(str(Path(__file__).resolve()), budget)
            launch.assert_not_called()
        for status, output in ((1, b""), (0, b""), (0, b"libc.so.6 => not found\n"),
                               (0, b"libc.so.6 => /work/libc.so.6 (0x1)\n")):
            with self.subTest(status=status, output=output), self.budget() as budget:
                with patch.object(budget, "run", return_value=subprocess.CompletedProcess(
                    [], status, output, b"loader failure",
                )):
                    with self.assertRaises(MakeProbeError):
                        _executable_runtime("/usr/bin/sh", budget)

    def test_non_elf_input_refuses_before_loader_execution(self):
        with self.budget() as budget, patch(
            "scripts.validation_ownership.make_probe._trusted_runtime_bytes", return_value=b"not ELF",
        ), patch.object(budget, "run") as launch:
            with self.assertRaisesRegex(MakeProbeError, "x86-64 ELF"):
                _executable_runtime("/usr/bin/sh", budget)
            launch.assert_not_called()
