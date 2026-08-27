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
from homebrew_fixture import build_production_planner_rom
from probe_bindings import ElfSymbolResolver


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
        self.assertEqual(
            scripted["campaign_checkpoint"]["semantic_state_digest"],
            searched["campaign_checkpoint"]["semantic_state_digest"],
        )
        changed = dict(scripted["campaign_checkpoint"])
        changed.pop("semantic_state_digest")
        changed["resources"] = {"gold": 999}
        self.assertNotEqual(
            scripted["campaign_checkpoint"]["semantic_state_digest"],
            planner.semantic_state_digest(changed),
        )
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
        pages = planner.PlannerBridge.action_pages(observation)
        self.assertEqual(len(pages), 2)
        self.assertEqual([len(page) for page in pages], [29, 12])
        bounded = planner.PlannerBridge(PROVENANCE)
        bounded.begin(PROVENANCE)
        maximum = bounded.observe(
            1,
            (),
            tuple(
                planner.Action("MOVE_WAIT", 1, (index, 0))
                for index in range(512)
            ),
        )
        maximum_pages = planner.PlannerBridge.action_pages(maximum)
        self.assertEqual(len(maximum_pages), 18)
        self.assertEqual(maximum_pages[-1][-1].ordinal, 511)
        with self.assertRaisesRegex(planner.PlannerError, "resource limit"):
            overflow = planner.PlannerBridge(PROVENANCE)
            overflow.begin(PROVENANCE)
            overflow.observe(
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

    def test_host_begin_and_commit_limits_are_atomic(self):
        boundary = planner.PlannerBridge(PROVENANCE)
        boundary.begin(PROVENANCE)
        boundary_observation = boundary.observe(
            1,
            (),
            (planner.Action("MOVE_WAIT", 1, (1, 1)),),
        )
        boundary._trace = [
            {"event": "committed", "ordinal": index}
            for index in range(planner.MAX_TRACE_ACTIONS - 1)
        ]
        boundary.commit(
            planner.Command(
                planner.CommandKind.COMMIT,
                boundary_observation.run_id,
                boundary_observation.observation_id,
                0,
                boundary_observation.actions[0].token,
            )
        )
        self.assertEqual(
            sum(entry.get("event") == "committed" for entry in boundary.trace),
            planner.MAX_TRACE_ACTIONS,
        )

        bridge = planner.PlannerBridge(PROVENANCE)
        bridge.begin(PROVENANCE)
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.PROTOCOL_ERROR.value,
        ):
            bridge.begin(PROVENANCE)

        observation = bridge.observe(
            1,
            (),
            (planner.Action("MOVE_WAIT", 1, (1, 1)),),
        )
        command = planner.Command(
            planner.CommandKind.COMMIT,
            observation.run_id,
            observation.observation_id,
            0,
            observation.actions[0].token,
        )
        bridge._trace = [
            {"event": "committed", "ordinal": index}
            for index in range(planner.MAX_TRACE_ACTIONS)
        ]
        trace_before = tuple(bridge._trace)
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.RESOURCE_LIMIT.value,
        ):
            bridge.commit(command)
        self.assertEqual(tuple(bridge._trace), trace_before)
        self.assertIs(bridge._observation, observation)
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.CANCELLED.value,
        ):
            bridge.commit(
                planner.Command(
                    planner.CommandKind.CANCEL,
                    observation.run_id,
                    observation.observation_id,
                )
            )
        bridge.begin(PROVENANCE)

        oversized = planner.PlannerBridge(PROVENANCE)
        oversized.begin(PROVENANCE)
        observation = oversized.observe(
            1,
            (),
            (planner.Action("MOVE_WAIT", 1, (1, 1)),),
        )
        command = planner.Command(
            planner.CommandKind.COMMIT,
            observation.run_id,
            observation.observation_id,
            0,
            observation.actions[0].token,
        )
        oversized._trace = [
            {"event": "padding", "payload": "x" * planner.MAX_TRACE_BYTES}
        ]
        trace_before = tuple(oversized._trace)
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.RESOURCE_LIMIT.value,
        ):
            oversized.commit(command)
        self.assertEqual(tuple(oversized._trace), trace_before)
        self.assertIs(oversized._observation, observation)

    def test_security_boundary_has_no_raw_memory_save_or_network_surface(self):
        root = TESTS_DIR.parents[2]
        target = (root / "src" / "expansion_autoplay_planner.c").read_text(encoding="utf-8")
        host = (PLAYTEST_DIR / "autoplay_planner.py").read_text(encoding="utf-8")
        self.assertNotIn("gActionData", target)
        self.assertNotIn("busWrite", target)
        self.assertNotIn("socket", host)
        self.assertNotIn("subprocess", host)
        self.assertNotIn("savestate", host)

    def test_cp_decide_wait_uses_dedicated_mailbox_poll_state(self):
        root = TESTS_DIR.parents[2]
        source = (root / "src" / "cp_decide.c").read_text(encoding="utf-8")
        wait_case = source.split(
            "case EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT:", 1
        )[1].split("case EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED:", 1)[0]
        self.assertIn("Proc_Goto(proc, 1)", wait_case)
        self.assertNotIn("Proc_Goto(proc, 0)", wait_case)
        poll_state = source.split("PROC_LABEL(1)", 1)[1].split(
            "PROC_LABEL(2)", 1
        )[0]
        self.assertIn("PROC_REPEAT(CpDecide_PollPlanner)", poll_state)
        poll_function = source.split(
            "static void CpDecide_PollPlanner(ProcPtr proc)", 2
        )[-1].split("static void CpDecide_CompleteDecision", 1)[0]
        self.assertIn("ExpansionAutoplayPlanner_PollDecision", poll_function)
        self.assertNotIn("AiDecideMainFunc", poll_function)
        self.assertNotIn("AiClearDecision", poll_function)

    def test_map_lifecycle_preserves_only_true_chapter_transition(self):
        source = (
            TESTS_DIR.parents[2] / "src" / "bmio.c"
        ).read_text(encoding="utf-8")
        start = source.split("void StartBattleMap", 1)[1].split(
            "void RestartBattleMap", 1
        )[0]
        restart = source.split("void RestartBattleMap", 1)[1].split(
            "void GameCtrl_StartResumedGame", 1
        )[0]
        resume = source.split("void GameCtrl_StartResumedGame", 1)[1].split(
            "void EndBMapMain", 1
        )[0]
        end = source.split("void EndBMapMain", 1)[1]
        self.assertIn("ExpansionAutoplay_ResetForChapterTransition()", start)
        for destructive in (restart, resume, end):
            self.assertIn("ExpansionAutoplay_Reset()", destructive)
            self.assertNotIn(
                "ExpansionAutoplay_ResetForChapterTransition()",
                destructive,
            )

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

    def test_configure_planner_selects_debug_for_bare_make(self):
        root = TESTS_DIR.parents[2]
        build_root = root / "build" / "test-artifacts" / "planner-configure"
        build_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            configured = subprocess.run(
                [str(root / "configure"), "--enable-autoplay-planner"],
                cwd=temporary,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                configured.returncode,
                0,
                configured.stdout + configured.stderr,
            )
            fragment = (Path(temporary) / "config.autotools.mk").read_text(
                encoding="utf-8"
            )
            self.assertIn("MODERN_CONFIG := debug", fragment)
            self.assertIn("EXPANSION_AUTOPLAY_PLANNER := 1", fragment)

            variables = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "print-MODERN_CONFIG",
                    "print-EXPANSION_AUTOPLAY_PLANNER",
                ],
                cwd=temporary,
                capture_output=True,
                text=True,
            )
            self.assertEqual(variables.returncode, 0, variables.stdout + variables.stderr)
            self.assertIn(
                "MODERN_CONFIG is a simple variable set to [debug]",
                variables.stdout,
            )
            self.assertIn(
                "EXPANSION_AUTOPLAY_PLANNER is a simple variable set to [1]",
                variables.stdout,
            )

            dry_run = subprocess.run(
                ["make", "--no-print-directory", "-n"],
                cwd=temporary,
                capture_output=True,
                text=True,
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stdout + dry_run.stderr)
            release = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "-n",
                    "expansion-modern-rom",
                    "MODERN_CONFIG=release",
                ],
                cwd=temporary,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(release.returncode, 0)
            self.assertIn("modern-debug-only", release.stdout + release.stderr)

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
                    "-DFE8_AUTOPLAY_PLANNER_RUNTIME_TEST=1",
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
            self.assertEqual(int(observation.group(1), 16), 1020)
            selected = re.search(
                r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
                r"sPlannerSelectedDecision(?:\.\d+)?$",
                symbols.stdout,
                re.MULTILINE,
            )
            self.assertIsNotNone(selected, "retained selected decision missing")
            self.assertEqual(int(selected.group(1), 16), 11)
            self.assertNotIn("sPlannerCandidates", symbols.stdout)

            sections = subprocess.run(
                [size, "-A", str(objects[0])],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(sections.returncode, 0, sections.stdout + sections.stderr)
            section_sizes = {
                match.group(1): int(match.group(2))
                for match in re.finditer(
                    r"^(\S+)\s+(\d+)\s+\d+$",
                    sections.stdout,
                    re.MULTILINE,
                )
            }
            self.assertEqual(section_sizes["ewram_data"], 1144)
            self.assertEqual(section_sizes[".bss"], 20)
            self.assertEqual(
                section_sizes[".text"]
                + section_sizes[".rodata"]
                + section_sizes[".rodata.str1.4"],
                3971,
            )

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
    def test_production_mailbox_replays_two_chapters_without_save_or_snapshot(self):
        root = TESTS_DIR.parents[2] / "build" / "test-artifacts" / "autoplay-planner"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            rom = Path(temporary) / "planner-two-chapter.gba"
            elf = Path(temporary) / "planner-two-chapter.elf"
            try:
                build_production_planner_rom(rom, elf)
            except RuntimeError as error:
                if "planner runtime toolchain unavailable" in str(error):
                    self.skipTest(str(error))
                raise
            resolver = ElfSymbolResolver(elf)
            scenario = gba_playtest.parse_scenario_data(
                {
                    "schema_version": 2,
                    "name": "autoplay-planner-two-chapter",
                    "frames": [],
                    "run_until": {
                        "max_frames": 1000,
                        "terminal_conditions": [
                            {
                                "reason": "success",
                                "all": [
                                    {
                                        "address": "gPlannerRuntimeProbe",
                                        "size": 4,
                                        "operator": "eq",
                                        "value": "0x00000001",
                                    }
                                ],
                            }
                        ],
                        "turn_limit": {
                            "maximum": 0xFFFFFFFF,
                            "address": "gPlannerRuntimeProbe+0x24",
                            "size": 4,
                        },
                        "action_limit": {
                            "maximum": 0xFFFFFFFF,
                            "address": "gPlannerRuntimeProbe+0x1c",
                            "size": 4,
                        },
                        "checkpoint": {
                            "name": "terminal",
                            "framebuffer": False,
                            "probes": [
                                {
                                    "address": f"gPlannerRuntimeProbe+0x{offset:02x}",
                                    "size": 4,
                                }
                                for offset in range(0, 0x2C, 4)
                            ],
                        },
                    },
                },
                "autoplay-planner-two-chapter",
                resolver,
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
            values = [
                int(probe["value"], 16)
                for probe in first["checkpoints"][0]["probes"]
            ]
            self.assertEqual(values[0], 1)
            self.assertEqual(values[1:5], [9, 9, 9, 4])
            self.assertEqual(values[5], 29)
            self.assertEqual(values[6:8], [3, 64])
            self.assertEqual(values[8:], [1, 2, 1])


if __name__ == "__main__":
    unittest.main()
