import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "scripts" / "arm_compressing_linker.py"
SPEC = importlib.util.spec_from_file_location("arm_compressing_linker", MODULE_PATH)
arm_linker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(arm_linker)


class ArmCompressingLinkerLockTests(unittest.TestCase):
    def test_same_output_has_same_lock_and_different_output_does_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first.o"
            second = Path(temporary) / "second.o"
            self.assertEqual(
                arm_linker.output_lock_path(first),
                arm_linker.output_lock_path(first),
            )
            self.assertNotEqual(
                arm_linker.output_lock_path(first),
                arm_linker.output_lock_path(second),
            )

    def test_second_process_waits_for_same_output_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shared.o"
            code = (
                "import importlib.util, sys, time\n"
                "spec = importlib.util.spec_from_file_location('linker', sys.argv[1])\n"
                "module = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(module)\n"
                "with module.output_lock(sys.argv[2]):\n"
                "    print('ACQUIRED', flush=True)\n"
                "    time.sleep(float(sys.argv[3]))\n"
            )
            first = subprocess.Popen(
                [sys.executable, "-c", code, str(MODULE_PATH), str(output), "0.5"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.addCleanup(first.kill)
            self.assertEqual(first.stdout.readline().strip(), "ACQUIRED")

            second = subprocess.Popen(
                [sys.executable, "-c", code, str(MODULE_PATH), str(output), "0"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.addCleanup(second.kill)
            time.sleep(0.1)
            self.assertIsNone(second.poll(), "second producer bypassed the lock")

            first_stdout, first_stderr = first.communicate(timeout=3)
            second_stdout, second_stderr = second.communicate(timeout=3)
            self.assertEqual(first.returncode, 0, first_stderr + first_stdout)
            self.assertEqual(second.returncode, 0, second_stderr + second_stdout)
            self.assertIn("ACQUIRED", second_stdout)


if __name__ == "__main__":
    unittest.main()
