"""Focused contract checks for issue #92's local planner bridge."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
PLAYTEST_DIR = TESTS_DIR.parent
for path in (str(PLAYTEST_DIR), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import autoplay_planner as planner
import gba_playtest
from homebrew_fixture import build_two_chapter_planner_rom


PROVENANCE = {
    "config": "modern-debug",
    "rom": {"sha1": "fixture", "size": 1024},
    "scenario": {"name": "two-chapter", "schema_version": 1},
}


class PlannerBridgeTests(unittest.TestCase):
    def test_scripted_and_search_planners_replay_two_chapters_without_save_state(self):
        scripted = planner.run_two_chapter_replay(planner.ScriptedPlanner(), PROVENANCE)
        searched = planner.run_two_chapter_replay(planner.BoundedSearchPlanner(), PROVENANCE)
        self.assertEqual(scripted["terminal"], "success")
        self.assertEqual(searched["terminal"], "success")
        self.assertEqual(scripted["campaign_checkpoint"]["chapter"], 2)
        self.assertEqual(searched["campaign_checkpoint"]["inventory"], ("fixture-key",))
        self.assertEqual(scripted["trace_digest"], planner.run_two_chapter_replay(
            planner.ScriptedPlanner(), PROVENANCE
        )["trace_digest"])
        self.assertEqual(len(json.dumps(scripted, sort_keys=True)), len(json.dumps(searched, sort_keys=True)))

    def test_mailbox_rejects_stale_unknown_forged_and_cancelled_requests(self):
        bridge = planner.PlannerBridge(PROVENANCE)
        run_id = bridge.begin(PROVENANCE)
        observation = bridge.observe(
            1,
            (planner.Field("map", "gBmMapTerrain", 4096, planner.Availability.AVAILABLE, 1),),
            (planner.Action("MOVE_WAIT", 1, (1, 1)),),
        )
        with self.assertRaisesRegex(planner.PlannerError, planner.Rejection.STALE_OBSERVATION.value):
            bridge.commit(planner.Command(planner.CommandKind.COMMIT, run_id, 99, 0, "forged"))
        with self.assertRaisesRegex(planner.PlannerError, planner.Rejection.UNKNOWN_ACTION.value):
            bridge.commit(planner.Command(planner.CommandKind.COMMIT, run_id, observation.observation_id, 1, "forged"))
        with self.assertRaisesRegex(planner.PlannerError, planner.Rejection.TOKEN_MISMATCH.value):
            bridge.commit(planner.Command(planner.CommandKind.COMMIT, run_id, observation.observation_id, 0, "forged"))
        self.assertEqual(len(bridge.trace), 2)
        with self.assertRaisesRegex(planner.PlannerError, planner.Rejection.CANCELLED.value):
            bridge.commit(planner.Command(planner.CommandKind.CANCEL, run_id, observation.observation_id))
        self.assertTrue(bridge.cancelled)

    def test_bounds_availability_pages_and_provenance_fail_closed(self):
        bridge = planner.PlannerBridge(PROVENANCE)
        with self.assertRaisesRegex(planner.PlannerError, "provenance mismatch"):
            bridge.begin({**PROVENANCE, "config": "modern-release"})
        bridge.begin(PROVENANCE)
        unavailable = planner.Field(
            "objective", "chapter objectives", 32, planner.Availability.UNSUPPORTED_RULE, None
        )
        actions = tuple(planner.Action("MOVE_WAIT", 1, (index, 0)) for index in range(41))
        observation = bridge.observe(1, (unavailable,), actions)
        self.assertEqual(len(planner.PlannerBridge.action_pages(observation)), 3)
        bounded = planner.PlannerBridge(PROVENANCE)
        bounded.begin(PROVENANCE)
        with self.assertRaisesRegex(planner.PlannerError, "resource limit"):
            bounded.observe(
                1,
                (),
                tuple(planner.Action("MOVE_WAIT", 1, (0, 0)) for _ in range(513)),
            )

    def test_mailbox_has_no_arbitrary_memory_write_api(self):
        mailbox = planner.Mailbox()
        self.assertFalse(hasattr(mailbox, "write"))
        self.assertFalse(hasattr(mailbox, "address"))
        mailbox.submit(planner.Command(planner.CommandKind.START, 1, 0))
        with self.assertRaisesRegex(planner.PlannerError, "unconsumed"):
            mailbox.submit(planner.Command(planner.CommandKind.START, 1, 0))

    def test_security_boundary_has_no_raw_memory_save_or_network_surface(self):
        root = TESTS_DIR.parents[2]
        target = (root / "src" / "expansion_autoplay_planner.c").read_text(encoding="utf-8")
        host = (PLAYTEST_DIR / "autoplay_planner.py").read_text(encoding="utf-8")
        self.assertNotIn("gActionData", target)
        self.assertNotIn("busWrite", target)
        self.assertNotIn("socket", host)
        self.assertNotIn("subprocess", host)
        self.assertNotIn("savestate", host)

    def test_debug_only_configuration_rejects_release_mailbox(self):
        root = TESTS_DIR.parents[2]
        command = [
            "python3",
            "scripts/modernize/expansion_config.py",
            "validate",
            "--config-mk",
            "config.mk",
            "--abi",
            "aapcs",
            "--rom-size",
            "16M",
            "--text-shift",
            "0",
            "--repo-root",
            ".",
            "--autoplay-planner",
            "1",
        ]
        debug = subprocess.run(
            [*command, "--config", "debug"], cwd=root, capture_output=True, text=True
        )
        self.assertEqual(debug.returncode, 0, debug.stdout + debug.stderr)
        release = subprocess.run(
            [*command, "--config", "release"], cwd=root, capture_output=True, text=True
        )
        self.assertNotEqual(release.returncode, 0)
        self.assertIn("modern-debug-only", release.stderr)

    def test_c_mailbox_adapter_accepts_only_typed_token_commit(self):
        compiler = shutil.which("gcc") or shutil.which("cc")
        if compiler is None:
            self.skipTest("no host C compiler")
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            executable = Path(temporary) / "planner-driver"
            completed = subprocess.run(
                [
                    compiler,
                    "-std=gnu89",
                    "-Werror=declaration-after-statement",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    "-O2",
                    "-I",
                    str(root / "include"),
                    "-I",
                    str(root / "include" / "generated"),
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-DFE8_EXPANSION_DEBUG=1",
                    "-DFE8_EXPANSION_AUTOPLAY_PLANNER=1",
                    str(root / "src" / "expansion_autoplay_planner.c"),
                    str(TESTS_DIR / "c" / "expansion_autoplay_planner_driver.c"),
                    "-o",
                    str(executable),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            completed = subprocess.run(
                [str(executable)], cwd=root, capture_output=True, text=True
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("AUTOPLAY_PLANNER_HOST_TEST: PASS", completed.stdout)

    def test_arm_adapter_compiles_at_the_existing_computer_decision_boundary(self):
        compiler = shutil.which("arm-none-eabi-gcc")
        nm = shutil.which("arm-none-eabi-nm")
        size = shutil.which("arm-none-eabi-size")
        if compiler is None or nm is None or size is None:
            self.skipTest("ARM compiler/binutils unavailable")
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            temporary_path = Path(temporary)
            objects = []
            for source in (
                root / "src" / "expansion_autoplay_planner.c",
                root / "src" / "cp_decide.c",
            ):
                output = temporary_path / f"{source.stem}.o"
                completed = subprocess.run(
                    [
                        compiler,
                        "-mcpu=arm7tdmi",
                        "-mthumb",
                        "-mthumb-interwork",
                        "-mabi=aapcs",
                        "-std=gnu89",
                        "-ffreestanding",
                        "-fno-builtin",
                        "-O2",
                        "-Werror=declaration-after-statement",
                        "-Werror=implicit-function-declaration",
                        "-Werror=implicit-int",
                        "-I",
                        str(root / "include"),
                        "-I",
                        str(root / "include" / "generated"),
                        "-DFE8_EXPANSION_MODERN_BUILD=1",
                        "-DFE8_EXPANSION_DEBUG=1",
                        "-DFE8_EXPANSION_AUTOPLAY_PLANNER=1",
                        "-c",
                        str(source),
                        "-o",
                        str(output),
                    ],
                    cwd=root,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                objects.append(output)
            symbols = subprocess.run(
                [nm, "-S", *map(str, objects)], cwd=root, capture_output=True, text=True
            )
            self.assertEqual(symbols.returncode, 0, symbols.stdout + symbols.stderr)
            self.assertRegex(
                symbols.stdout,
                r"\bU ExpansionAutoplayPlanner_OfferDecision\b",
            )
            observation = re.search(
                r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
                r"gExpansionAutoplayPlannerObservation$",
                symbols.stdout,
                re.MULTILINE,
            )
            self.assertIsNotNone(observation, "planner observation symbol missing")
            self.assertLessEqual(int(observation.group(1), 16), 256)

            disabled = temporary_path / "planner-release-disabled.o"
            completed = subprocess.run(
                [
                    compiler,
                    "-mcpu=arm7tdmi",
                    "-mthumb",
                    "-mthumb-interwork",
                    "-mabi=aapcs",
                    "-std=gnu89",
                    "-ffreestanding",
                    "-fno-builtin",
                    "-O2",
                    "-DNDEBUG",
                    "-I",
                    str(root / "include"),
                    "-I",
                    str(root / "include" / "generated"),
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-DFE8_EXPANSION_AUTOPLAY_PLANNER=0",
                    "-c",
                    str(root / "src" / "expansion_autoplay_planner.c"),
                    "-o",
                    str(disabled),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            symbols = subprocess.run(
                [nm, str(disabled)], cwd=root, capture_output=True, text=True
            )
            self.assertEqual(symbols.returncode, 0, symbols.stdout + symbols.stderr)
            self.assertNotIn("gExpansionAutoplayPlanner", symbols.stdout)


class PlannerLibmGBAIntegrationTests(unittest.TestCase):
    def test_two_chapter_fixture_replays_from_clean_boot_without_save_or_snapshot(self):
        root = TESTS_DIR.parents[2] / "build" / "test-artifacts" / "autoplay-planner"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            rom = Path(temporary) / "planner-two-chapter.gba"
            build_two_chapter_planner_rom(rom)
            scenario = gba_playtest.parse_scenario_data(
                {
                    "schema_version": 2,
                    "name": "autoplay-planner-two-chapter",
                    "frames": [
                        {"start": 1, "end": 1, "keys": ["A"]},
                        {"start": 3, "end": 3, "keys": ["A"]},
                    ],
                    "run_until": {
                        "max_frames": 8,
                        "terminal_conditions": [
                            {
                                "reason": "success",
                                "all": [
                                    {
                                        "address": "0x02000008",
                                        "size": 4,
                                        "operator": "eq",
                                        "value": "0x00000001",
                                    }
                                ],
                            }
                        ],
                        "turn_limit": {
                            "maximum": 3,
                            "address": "0x02000004",
                            "size": 4,
                        },
                        "action_limit": {
                            "maximum": 3,
                            "address": "0x02000000",
                            "size": 4,
                        },
                        "checkpoint": {
                            "name": "terminal",
                            "framebuffer": False,
                            "probes": [
                                {"address": "0x02000000", "size": 4},
                                {"address": "0x02000004", "size": 4},
                                {"address": "0x02000008", "size": 4},
                            ],
                        },
                    },
                },
                "autoplay-planner-two-chapter",
            )
            try:
                first = gba_playtest.capture(rom, scenario, work_dir=Path(temporary))
                second = gba_playtest.capture(rom, scenario, work_dir=Path(temporary))
            except gba_playtest.PlaytestError as error:
                if "libmGBA backend unavailable" in str(error):
                    self.skipTest(str(error))
                raise
            self.assertEqual(first, second)
            self.assertEqual(first["terminal"]["reason"], "success")
            self.assertEqual(first["checkpoints"][0]["probes"][0]["value"], "0x00000002")
            self.assertEqual(first["checkpoints"][0]["probes"][1]["value"], "0x00000002")


if __name__ == "__main__":
    unittest.main()
