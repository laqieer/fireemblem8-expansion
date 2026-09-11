"""Make-dependency discovery coverage for generated strategy data."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

from scripts.generated_data.autoplaystrategies import deps
from scripts.generated_data.tests._util import scratch_dir


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


class AutoplayStrategiesDependencyTests(unittest.TestCase):
    def test_discovery_tracks_strategy_objective_and_bundle_owners(self):
        paths = deps.collect_input_paths(
            os.path.join(ROOT, "src", "data", "autoplay_strategies.json"),
            os.path.join(ROOT, "src", "data", "chapter_objectives.json"),
            os.path.join(ROOT, "src", "data"),
        )
        self.assertIn(
            os.path.join(ROOT, "src", "data", "autoplay_strategies.json"),
            paths,
        )
        self.assertIn(
            os.path.join(ROOT, "src", "data", "chapter_objectives.json"),
            paths,
        )
        self.assertIn(os.path.join(ROOT, "src", "data", "ch2_bundle.json"), paths)
        self.assertIn(os.path.join(ROOT, "scripts", "assets", "tmx.py"), paths)

    def test_dependency_clis_keep_tmx_input_and_emit_complete_depfiles(self):
        with scratch_dir() as temporary:
            scratch = Path(temporary)
            malformed = scratch / "invalid.json"
            malformed.write_text("{", encoding="utf-8")
            for name, arguments in (
                ("chapterobjectives", [
                    "--source", "src/data/chapter_objectives.json",
                    "--bundle-source", "src/data/ch2_bundle.json",
                ]),
                ("autoplaystrategies", [
                    "--source", "src/data/autoplay_strategies.json",
                    "--objectives-source", "src/data/chapter_objectives.json",
                    "--bundle-source", "src/data/ch2_bundle.json",
                ]),
            ):
                with self.subTest(schema=name):
                    command = [
                        sys.executable, "-B", "-m", "scripts.generated_data." + name + ".deps",
                    ]
                    discovered = subprocess.run(
                        command + arguments, cwd=ROOT, text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(discovered.returncode, 0, discovered.stderr)
                    inputs = discovered.stdout.splitlines()
                    self.assertEqual(inputs, sorted(set(inputs)))
                    for relative in (
                        "scripts/assets/tmx.py",
                        "scripts/generated_data/chapterbundle/schema.py",
                        "src/data/chapter_objectives.json",
                        "src/data/ch2_bundle.json",
                        arguments[1],
                    ):
                        self.assertIn(os.path.realpath(os.path.join(ROOT, relative)), inputs)
                    depfile = scratch / (name + ".mk")
                    target = "build/generated/data/" + name + ".c"
                    output_args = ["--make-target", target, "--depfile", str(depfile)]
                    emitted = subprocess.run(
                        command + arguments + output_args,
                        cwd=ROOT, text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(emitted.returncode, 0, emitted.stderr)
                    expected_bytes = depfile.read_bytes()
                    actual_target, separator, prerequisites = expected_bytes.decode("utf-8").partition(": ")
                    self.assertEqual((actual_target, separator), (target, ": "))
                    self.assertEqual(prerequisites.split(), inputs)
                    invalid_args = list(arguments)
                    invalid_args[1] = str(malformed)
                    failed = subprocess.run(
                        command + invalid_args + output_args,
                        cwd=ROOT, text=True, capture_output=True, check=False,
                    )
                    self.assertNotEqual(failed.returncode, 0)
                    self.assertIn(str(malformed), failed.stderr)
                    self.assertEqual(depfile.read_bytes(), expected_bytes)


if __name__ == "__main__":
    unittest.main()
