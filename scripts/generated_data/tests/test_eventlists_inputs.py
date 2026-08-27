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
OTHER_CHAPTER_FLAG = "EVFLAG_5"


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
        self.canonical_bundle = self.repo / "src" / "data" / "ch2_bundle.json"
        self._write_bundle(
            self.canonical_bundle,
            self.canonical_strategies,
            ["AutoplayStrategies_EventListsInputs"],
        )
        self.custom_strategy_dir = (
            self.repo / "build" / "eventlists-inputs" / "custom-strategy-sources"
        )
        self.custom_strategy_dir.mkdir(parents=True)
        self.custom_strategies = self.custom_strategy_dir / "custom_strategies.json"
        self._set_pair(self.custom_strategies, VALID_FLAG)
        self.other_strategies = self.custom_strategy_dir / "other_strategies.json"
        self._set_other_chapter_pair()
        self.custom_bundle = (
            self.repo / "build" / "eventlists-inputs" / "custom_bundle.json"
        )
        self.custom_file_bundle = (
            self.repo / "build" / "eventlists-inputs" / "custom_file_bundle.json"
        )
        self.wrong_symbols_bundle = (
            self.repo / "build" / "eventlists-inputs" / "wrong_symbols_bundle.json"
        )
        self._write_bundle(
            self.custom_bundle,
            self.custom_strategy_dir,
            ["AutoplayStrategies_EventListsInputs"],
        )
        self._write_bundle(
            self.custom_file_bundle,
            self.custom_strategies,
            ["AutoplayStrategies_EventListsInputs"],
        )
        self._write_bundle(
            self.wrong_symbols_bundle,
            self.custom_strategy_dir,
            [],
        )

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

    def _set_other_chapter_pair(self):
        source = json.loads(self.custom_strategies.read_text(encoding="utf-8"))
        source["strategies"] = []
        source["chapters"] = [
            {
                "chapter": "CHAPTER_L_3",
                "symbol": "AutoplayStrategies_EventListsOtherChapter",
                "chapterAssignment": {
                    "strategy": "AUTOPLAY_STRATEGY_OBJECTIVE_FIRST",
                    "activationFlag": OTHER_CHAPTER_FLAG,
                },
                "groupAssignments": [],
                "unitAssignments": [],
            }
        ]
        self._write_json(self.other_strategies, source)

    def _set_event_pair(self, flag):
        source = json.loads(self.eventlists.read_text(encoding="utf-8"))
        source["helperScripts"][0]["entries"][0]["args"][1] = flag
        self._write_json(self.eventlists, source)

    def _write_bundle(self, path, strategy_source, symbols):
        bundle = json.loads(self.canonical_bundle.read_text(encoding="utf-8"))
        bundle["autoplayStrategies"] = {
            "source": str(strategy_source),
            "symbols": symbols,
        }
        self._write_json(path, bundle)

    def _make(
        self,
        strategy_source=None,
        reference_profiles="1",
        bundle_source=None,
    ):
        command = [
            "make",
            "--no-print-directory",
            str(self.target),
            "GENERATED_DATA_OUT_DIR={}".format(self.out_dir),
            "GENERATED_DATA_AUTOPLAYSTRATEGIES_REFERENCE_PROFILES={}".format(
                reference_profiles
            ),
        ]
        if strategy_source is not None:
            command.append(
                "GENERATED_DATA_AUTOPLAYSTRATEGIES_SOURCE={}".format(strategy_source)
            )
            if bundle_source is None:
                bundle_source = self.custom_bundle
        if bundle_source is not None:
            command.append(
                "GENERATED_DATA_AUTOPLAYSTRATEGIES_CHAPTERBUNDLE_SOURCE={}".format(
                    bundle_source
                )
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

        canonical_disabled = self._make(reference_profiles="0")
        self.assertNotEqual(canonical_disabled.returncode, 0)
        self.assertIn(
            "undefined strategy reference 'AUTOPLAY_STRATEGY_OBJECTIVE_FIRST'",
            canonical_disabled.stdout,
        )
        canonical_reenabled = self._make(reference_profiles="1")
        self.assertEqual(canonical_reenabled.returncode, 0, canonical_reenabled.stdout)
        self.assertIn("generate --table eventlists", canonical_reenabled.stdout)

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
        wrong_source = self._make(
            self.custom_strategy_dir,
            bundle_source=self.canonical_bundle,
        )
        self.assertNotEqual(wrong_source.returncode, 0)
        self.assertIn(
            "do not match event-list owner sources",
            wrong_source.stdout,
        )
        wrong_symbols = self._make(
            self.custom_strategy_dir,
            bundle_source=self.wrong_symbols_bundle,
        )
        self.assertNotEqual(wrong_symbols.returncode, 0)
        self.assertIn(
            "is not declared by the event-list owner's autoplayStrategies symbols",
            wrong_symbols.stdout,
        )

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
        self.assertIn(os.path.realpath(self.other_strategies), depfile_inputs)
        self.assertIn(
            os.path.realpath(self.custom_bundle),
            depfile_inputs,
        )

        custom_file_valid = self._make(
            self.custom_strategies,
            bundle_source=self.custom_file_bundle,
        )
        self.assertEqual(custom_file_valid.returncode, 0, custom_file_valid.stdout)

        disabled = self._make(self.custom_strategy_dir, reference_profiles="0")
        self.assertNotEqual(disabled.returncode, 0)
        self.assertIn("generate --table eventlists", disabled.stdout)
        self.assertIn(
            "undefined strategy reference 'AUTOPLAY_STRATEGY_OBJECTIVE_FIRST'",
            disabled.stdout,
        )
        stamp = self.out_dir / ".ch2-eventlists.config"
        self.assertIn(
            "reference_profiles=0",
            stamp.read_text(encoding="utf-8"),
        )
        reenabled = self._make(self.custom_strategy_dir, reference_profiles="1")
        self.assertEqual(reenabled.returncode, 0, reenabled.stdout)
        self.assertIn("generate --table eventlists", reenabled.stdout)

        self._set_event_pair(OTHER_CHAPTER_FLAG)
        cross_chapter = self._make(self.custom_strategy_dir)
        self.assertNotEqual(cross_chapter.returncode, 0)
        self.assertIn(
            "is not declared by autoplay strategy assignments",
            cross_chapter.stdout,
        )
        self._set_event_pair(VALID_FLAG)
        cross_chapter_restored = self._make(self.custom_strategy_dir)
        self.assertEqual(
            cross_chapter_restored.returncode,
            0,
            cross_chapter_restored.stdout,
        )

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
