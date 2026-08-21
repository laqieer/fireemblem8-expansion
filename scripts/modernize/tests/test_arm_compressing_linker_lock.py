from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
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

    def test_staged_publish_keeps_complete_output_during_producer_consumer_overlap(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shared.o"
            symbol_output = Path(str(output) + ".sym.o")
            output.write_text("old object", encoding="utf-8")
            symbol_output.write_text("old symbols", encoding="utf-8")
            staging_ready = threading.Event()
            release_producer = threading.Event()

            def build(staging):
                Path(staging).write_text("new object", encoding="utf-8")
                Path(staging + ".sym.o").write_text("new symbols", encoding="utf-8")
                staging_ready.set()
                self.assertTrue(
                    release_producer.wait(10),
                    "test did not release the staged producer",
                )

            def produce():
                with arm_linker.output_lock(output):
                    arm_linker.build_and_publish_output(output, build)

            with ThreadPoolExecutor(max_workers=1) as executor:
                producer = executor.submit(produce)
                self.assertTrue(
                    staging_ready.wait(3),
                    "producer did not finish staging the replacement",
                )

                # This models arm-none-eabi-ld reading the object and its symbols
                # while another Make root is still regenerating the shared output.
                self.assertEqual(output.read_text(encoding="utf-8"), "old object")
                self.assertEqual(
                    symbol_output.read_text(encoding="utf-8"),
                    "old symbols",
                )

                release_producer.set()
                producer.result(timeout=3)

            self.assertEqual(output.read_text(encoding="utf-8"), "new object")
            self.assertEqual(symbol_output.read_text(encoding="utf-8"), "new symbols")

    def test_consumer_lock_keeps_object_and_sidecar_from_one_generation(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shared.o"
            symbol_output = Path(str(output) + ".sym.o")
            output.write_text("old object", encoding="utf-8")
            symbol_output.write_text("old symbols", encoding="utf-8")
            first_replace = threading.Event()
            release_publish = threading.Event()
            consumer_started = threading.Event()
            consumer_acquired_lock = threading.Event()
            original_replace = arm_linker.os.replace

            def pause_between_publications(source, destination):
                original_replace(source, destination)
                if destination == str(output):
                    first_replace.set()
                    self.assertTrue(
                        release_publish.wait(3),
                        "test did not release the staged publication",
                    )

            def build(staging):
                Path(staging).write_text("new object", encoding="utf-8")
                Path(staging + ".sym.o").write_text("new symbols", encoding="utf-8")

            def produce():
                with arm_linker.output_lock(output):
                    arm_linker.build_and_publish_output(output, build)

            def consume():
                consumer_started.set()
                with arm_linker.output_lock(output):
                    consumer_acquired_lock.set()
                    return (
                        output.read_text(encoding="utf-8"),
                        symbol_output.read_text(encoding="utf-8"),
                    )

            arm_linker.os.replace = pause_between_publications
            try:
                with ThreadPoolExecutor(max_workers=2) as executor:
                    producer = executor.submit(produce)
                    self.assertTrue(
                        first_replace.wait(3),
                        "producer did not publish the staged object",
                    )

                    # Without the shared consumer lock, a reader can observe
                    # the publication gap while the prior pair is staged away.
                    self.assertEqual(output.read_text(encoding="utf-8"), "new object")
                    self.assertFalse(symbol_output.exists())

                    consumer = executor.submit(consume)
                    self.assertTrue(
                        consumer_started.wait(3),
                        "consumer task did not start",
                    )
                    self.assertFalse(
                        consumer_acquired_lock.is_set(),
                        "consumer bypassed the shared object publication lock",
                    )

                    release_publish.set()
                    producer.result(timeout=3)
                    self.assertEqual(consumer.result(timeout=3), ("new object", "new symbols"))
            finally:
                arm_linker.os.replace = original_replace

    def test_locked_consumer_process_waits_for_producer_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shared.o"
            output.write_text("complete object", encoding="utf-8")
            code = "import sys; print('CONSUMED', flush=True)"

            with arm_linker.output_lock(output):
                consumer = subprocess.Popen(
                    [
                        sys.executable,
                        str(MODULE_PATH),
                        "--debug",
                        "--lock-output",
                        str(output),
                        "--",
                        sys.executable,
                        "-c",
                        code,
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                self.addCleanup(consumer.kill)
                self.assertTrue(
                    consumer.stdout.readline().startswith("Waiting for output lock:"),
                    "consumer did not reach the output lock",
                )
                self.assertIsNone(
                    consumer.poll(),
                    "cross-process consumer bypassed the producer lock",
                )

            stdout, stderr = consumer.communicate(timeout=3)
            self.assertEqual(consumer.returncode, 0, stderr + stdout)
            self.assertIn("CONSUMED", stdout)

    def test_legacy_delete_before_build_negative_control_hides_consumer_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shared.o"
            output.write_text("old object", encoding="utf-8")
            deleted_output = threading.Event()
            release_producer = threading.Event()

            def legacy_producer():
                output.unlink()
                deleted_output.set()
                self.assertTrue(
                    release_producer.wait(10),
                    "test did not release the legacy producer",
                )
                output.write_text("new object", encoding="utf-8")

            with ThreadPoolExecutor(max_workers=1) as executor:
                producer = executor.submit(legacy_producer)
                self.assertTrue(
                    deleted_output.wait(3),
                    "legacy producer did not delete the published output",
                )

                # Pre-fix control: the original delete-then-rebuild sequence
                # deterministically leaves the linker consumer without its input.
                with self.assertRaises(FileNotFoundError):
                    output.read_text(encoding="utf-8")

                release_producer.set()
                producer.result(timeout=3)

    def test_failed_staging_preserves_last_complete_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shared.o"
            symbol_output = Path(str(output) + ".sym.o")
            output.write_text("old object", encoding="utf-8")
            symbol_output.write_text("old symbols", encoding="utf-8")

            def failed_build(staging):
                Path(staging).write_text("incomplete object", encoding="utf-8")
                Path(staging + ".sym.o").write_text(
                    "incomplete symbols",
                    encoding="utf-8",
                )
                raise RuntimeError("simulated compressor failure")

            with self.assertRaisesRegex(RuntimeError, "simulated compressor failure"):
                arm_linker.build_and_publish_output(output, failed_build)

            self.assertEqual(output.read_text(encoding="utf-8"), "old object")
            self.assertEqual(symbol_output.read_text(encoding="utf-8"), "old symbols")
            self.assertEqual(list(Path(temporary).glob(".shared.o.*")), [])

    def test_sidecar_publish_failure_restores_last_complete_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shared.o"
            symbol_output = Path(str(output) + ".sym.o")
            output.write_text("old object", encoding="utf-8")
            symbol_output.write_text("old symbols", encoding="utf-8")
            original_replace = arm_linker.os.replace

            def fail_sidecar_publish(source, destination):
                if source.endswith(".tmp.sym.o") and destination == str(symbol_output):
                    raise OSError("simulated sidecar publication failure")
                original_replace(source, destination)

            def build(staging):
                Path(staging).write_text("new object", encoding="utf-8")
                Path(staging + ".sym.o").write_text("new symbols", encoding="utf-8")

            arm_linker.os.replace = fail_sidecar_publish
            try:
                with self.assertRaisesRegex(OSError, "sidecar publication failure"):
                    arm_linker.build_and_publish_output(output, build)
            finally:
                arm_linker.os.replace = original_replace

            self.assertEqual(output.read_text(encoding="utf-8"), "old object")
            self.assertEqual(symbol_output.read_text(encoding="utf-8"), "old symbols")
            self.assertEqual(list(Path(temporary).glob(".shared.o.*")), [])

    def test_malformed_staging_and_first_build_failure_leave_no_publication_or_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shared.o"
            stale = Path(temporary) / ".shared.o.interrupted.tmp.sym.o"
            stale.write_text("interrupted", encoding="utf-8")

            def missing_symbol(staging):
                Path(staging).write_text("incomplete object", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "staged symbol output"):
                arm_linker.build_and_publish_output(output, missing_symbol)

            self.assertFalse(output.exists())
            self.assertFalse(Path(str(output) + ".sym.o").exists())
            self.assertEqual(list(Path(temporary).glob(".shared.o.*")), [])

    def test_first_build_publishes_both_outputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "shared.o"

            def build(staging):
                Path(staging).write_text("first object", encoding="utf-8")
                Path(staging + ".sym.o").write_text("first symbols", encoding="utf-8")

            arm_linker.build_and_publish_output(output, build)

            self.assertEqual(output.read_text(encoding="utf-8"), "first object")
            self.assertEqual(
                Path(str(output) + ".sym.o").read_text(encoding="utf-8"),
                "first symbols",
            )


if __name__ == "__main__":
    unittest.main()
