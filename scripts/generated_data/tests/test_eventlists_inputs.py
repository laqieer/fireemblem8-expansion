"""Incremental Make coverage for event-list strategy validation inputs."""

import json
import os
import shutil
import subprocess
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRATCH_ROOT = ROOT / "build" / "test-eventlists-inputs"
VALID_FLAG = "EVFLAG_HIDE_BLINKING_ICON"
INVALID_FLAG = "EVFLAG_BATTLE_QUOTES"


class EventListsInputTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = SCRATCH_ROOT / self.id().rsplit(".", 1)[-1]
        shutil.rmtree(self.sandbox, ignore_errors=True)
        self.repo = self.sandbox / "repo"
        shutil.copytree(
            ROOT,
            self.repo,
            ignore=shutil.ignore_patterns(".git", "build", "__pycache__", "*.pyc"),
        )
        self.eventlists = self.repo / "src" / "data" / "ch2_eventlists.json"
        eventlists = json.loads(self.eventlists.read_text(encoding="utf-8"))
        eventlists["helperScripts"] = [
            {
                "symbol": "EventScr_Ch2_StrategyActivation",
                "entries": [
                    {
                        "helper": "strategy",
                        "operation": "activate",
                        "args": [
                            "AUTOPLAY_STRATEGY_OBJECTIVE_FIRST",
                            VALID_FLAG,
                        ],
                    }
                ],
            }
        ]
        self._write_json(self.eventlists, eventlists)

        self.canonical_strategies = (
            self.repo / "src" / "data" / "autoplay_strategies.json"
        )
        self._set_pair(self.canonical_strategies, VALID_FLAG)
        self.custom_strategy_dir = (
            self.repo / "build" / "eventlists-inputs" / "custom-strategy-sources"
        )
        self.custom_strategy_dir.mkdir(parents=True)
        self.custom_strategies = self.custom_strategy_dir / "custom_strategies.json"
        self._set_pair(self.custom_strategies, VALID_FLAG)

        self.out_dir = self.repo / "build" / "eventlists-inputs" / "generated"
        self.target = self.out_dir / "data_ch2_eventlists.c"

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def _write_json(self, path, data):
        previous = path.stat().st_mtime_ns if path.exists() else 0
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        updated = max(time.time_ns(), previous + 1)
        os.utime(path, ns=(updated, updated))

    def _set_pair(self, path, flag):
        source = json.loads(
            (self.repo / "src" / "data" / "autoplay_strategies.json").read_text(
                encoding="utf-8"
            )
        )
        source["chapters"] = [
            {
                "chapter": "CHAPTER_L_2",
                "symbol": "AutoplayStrategies_EventListsInputs",
                "chapterAssignment": {
                    "strategy": "AUTOPLAY_STRATEGY_OBJECTIVE_FIRST",
                    "activationFlag": flag,
                },
                "groupAssignments": [],
                "unitAssignments": [],
            }
        ]
        self._write_json(path, source)

    def _make(self, strategy_source=None):
        command = [
            "make",
            "--no-print-directory",
            str(self.target),
            "GENERATED_DATA_OUT_DIR={}".format(self.out_dir),
        ]
        if strategy_source is not None:
            command.append(
                "GENERATED_DATA_AUTOPLAYSTRATEGIES_SOURCE={}".format(strategy_source)
            )
        return subprocess.run(
            command,
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_canonical_and_custom_pair_changes_regenerate_without_cross_profile_noise(self):
        initial = self._make()
        self.assertEqual(initial.returncode, 0, initial.stdout)
        self.assertIn("generate --table eventlists", initial.stdout)

        self._set_pair(self.canonical_strategies, INVALID_FLAG)
        canonical_invalid = self._make()
        self.assertNotEqual(canonical_invalid.returncode, 0)
        self.assertIn("generate --table eventlists", canonical_invalid.stdout)
        self.assertIn(
            "is not declared by autoplay strategy assignments",
            canonical_invalid.stdout,
        )

        self._set_pair(self.canonical_strategies, VALID_FLAG)
        canonical_restored = self._make()
        self.assertEqual(canonical_restored.returncode, 0, canonical_restored.stdout)

        self._set_pair(self.canonical_strategies, INVALID_FLAG)
        custom_valid = self._make(self.custom_strategy_dir)
        self.assertEqual(custom_valid.returncode, 0, custom_valid.stdout)
        self.assertIn(
            "autoplaystrategies={}".format(self.custom_strategy_dir),
            custom_valid.stdout,
        )
        depfile = self.out_dir / "eventlists.inputs.mk"
        depfile_inputs = depfile.read_text(encoding="utf-8").partition(": ")[2].split()
        self.assertIn(os.path.realpath(self.custom_strategy_dir), depfile_inputs)
        self.assertIn(os.path.realpath(self.custom_strategies), depfile_inputs)

        self._set_pair(self.custom_strategies, INVALID_FLAG)
        custom_invalid = self._make(self.custom_strategy_dir)
        self.assertNotEqual(custom_invalid.returncode, 0)
        self.assertIn("generate --table eventlists", custom_invalid.stdout)
        self.assertIn(
            "is not declared by autoplay strategy assignments",
            custom_invalid.stdout,
        )

        self._set_pair(self.custom_strategies, VALID_FLAG)
        custom_restored = self._make(self.custom_strategy_dir)
        self.assertEqual(custom_restored.returncode, 0, custom_restored.stdout)
        target_mtime = self.target.stat().st_mtime_ns

        self._set_pair(self.canonical_strategies, VALID_FLAG)
        unrelated = self._make(self.custom_strategy_dir)
        self.assertEqual(unrelated.returncode, 0, unrelated.stdout)
        self.assertNotIn("generate --table eventlists", unrelated.stdout)
        self.assertEqual(self.target.stat().st_mtime_ns, target_mtime)


if __name__ == "__main__":
    unittest.main()
