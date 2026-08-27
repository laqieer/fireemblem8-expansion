"""Focused contract checks for issue #92's local planner bridge."""

from __future__ import annotations

import json
import os
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
from homebrew_fixture import (
    build_planner_transport_backend,
    build_production_planner_rom,
)


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
        complete = planner.collect_observation_pages(bridge, observation)
        self.assertEqual(len(complete.actions), 41)
        self.assertEqual(complete.actions[-1].ordinal, 40)
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
        maximum_complete = planner.collect_observation_pages(bounded, maximum)
        self.assertEqual(maximum.page_count, 20)
        self.assertEqual(maximum_complete.actions[-1].ordinal, 511)
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
        boundary_complete = planner.collect_observation_pages(
            boundary, boundary_observation
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
                boundary_complete.actions[0].token,
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
        complete = planner.collect_observation_pages(bridge, observation)
        command = planner.Command(
            planner.CommandKind.COMMIT,
            observation.run_id,
            observation.observation_id,
            0,
            complete.actions[0].token,
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
        complete = planner.collect_observation_pages(oversized, observation)
        command = planner.Command(
            planner.CommandKind.COMMIT,
            observation.run_id,
            observation.observation_id,
            0,
            complete.actions[0].token,
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

        atomic = planner.PlannerBridge(PROVENANCE)
        atomic.begin(PROVENANCE)
        atomic._trace = [
            {"event": "padding", "payload": "x" * planner.MAX_TRACE_BYTES}
        ]
        trace_before = tuple(atomic._trace)
        next_id_before = atomic._next_observation_id
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.RESOURCE_LIMIT.value,
        ):
            atomic.observe(
                1,
                (),
                (planner.Action("MOVE_WAIT", 1, (1, 1)),),
            )
        self.assertEqual(tuple(atomic._trace), trace_before)
        self.assertEqual(atomic._next_observation_id, next_id_before)
        self.assertIsNone(atomic._observation)

    def test_security_boundary_has_no_raw_memory_save_or_network_surface(self):
        root = TESTS_DIR.parents[2]
        target = (root / "src" / "expansion_autoplay_planner.c").read_text(encoding="utf-8")
        host = (PLAYTEST_DIR / "autoplay_planner.py").read_text(encoding="utf-8")
        transport = (PLAYTEST_DIR / "planner_transport_backend.c").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("gActionData", target)
        self.assertNotIn("busWrite", target)
        self.assertNotIn("socket", host)
        self.assertNotIn("subprocess", host)
        self.assertNotIn("savestate", host)
        self.assertNotIn("ADDRESS", transport)
        self.assertNotIn("POKE", transport)
        self.assertIn("PLANNER_COMMAND_ADDR", transport)

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
        planner_branch = source.split(
            "if (ExpansionAutoplayPlanner_IsActive())", 1
        )[1].split("else", 1)[0]
        self.assertIn("AiGenerateUnitMovementMapRespectStay", planner_branch)
        self.assertIn("ExpansionAutoplayPlanner_OfferDecision(NULL)", planner_branch)
        self.assertNotIn("AiDecideMainFunc", planner_branch)

    def test_map_lifecycle_preserves_only_true_chapter_transition(self):
        root = TESTS_DIR.parents[2]
        source = (root / "src" / "bmio.c").read_text(encoding="utf-8")
        event = (root / "src" / "event.c").read_text(encoding="utf-8")
        event_commands = (root / "src" / "eventscr.c").read_text(encoding="utf-8")
        autoplay = (root / "src" / "expansion_autoplay.c").read_text(
            encoding="utf-8"
        )
        start = source.split("void StartBattleMap", 1)[1].split(
            "void RestartBattleMap", 1
        )[0]
        restart = source.split("void RestartBattleMap", 1)[1].split(
            "void GameCtrl_StartResumedGame", 1
        )[0]
        resume = source.split("void GameCtrl_StartResumedGame", 1)[1].split(
            "void EndBMapMain", 1
        )[0]
        teardown = source.split("static void EndBMapMainInternal", 1)[1].split(
            "void ChapterChangeUnitCleanup", 1
        )[0]
        self.assertIn("ExpansionAutoplay_ResetForChapterTransition()", start)
        self.assertIn("ExpansionAutoplayPlanner_OnMapReady()", start)
        for destructive in (restart, resume):
            self.assertIn("ExpansionAutoplay_Reset()", destructive)
            self.assertNotIn(
                "ExpansionAutoplay_ResetForChapterTransition()",
                destructive,
            )
            self.assertIn("ExpansionAutoplayPlanner_OnMapReady()", destructive)
        player_phase = autoplay.split(
            "void ExpansionAutoplay_OnPlayerPhaseStart", 1
        )[1].split("void ExpansionAutoplay_OnBlueComputerPhaseStart", 1)[0]
        self.assertIn("ExpansionAutoplayPlanner_OnMapReady()", player_phase)
        self.assertIn("EndBMapMainInternal(false)", teardown)
        self.assertIn("EndBMapMainInternal(true)", teardown)
        self.assertIn("ExpansionAutoplay_ResetForChapterTransition()", teardown)
        self.assertIn("ExpansionAutoplay_Reset()", teardown)
        self.assertIn(
            "EV_STATE_PLANNER_CHAPTER_TRANSITION",
            event,
        )
        mnch = event_commands.split("case EVSUBCMD_MNCH:", 1)[1].split(
            "case EVSUBCMD_MNC2:", 1
        )[0]
        mnc2 = event_commands.split("case EVSUBCMD_MNC2:", 1)[1].split(
            "case EVSUBCMD_MNC3:", 1
        )[0]
        mnts = event_commands.split("case EVSUBCMD_MNTS:", 1)[1].split(
            "case EVSUBCMD_MNCH:", 1
        )[0]
        mnc4 = event_commands.split("case EVSUBCMD_MNC4:", 1)[1].split(
            "} // switch", 1
        )[0]
        for preserving in (mnch, mnc2):
            self.assertIn("EV_STATE_PLANNER_CHAPTER_TRANSITION", preserving)
        for destructive in (mnts, mnc4):
            self.assertNotIn("EV_STATE_PLANNER_CHAPTER_TRANSITION", destructive)

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
            self.assertIn(
                "expansion-modern-boot-check",
                dry_run.stdout,
            )
            self.assertNotIn(
                "MODERN_CONFIG=release",
                dry_run.stdout,
            )
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
            self.assertEqual(int(observation.group(1), 16), 996)
            self.assertLessEqual(int(observation.group(1), 16), planner.PAGE_MAX_BYTES)
            self.assertNotIn("sPlannerCandidates", symbols.stdout)
            self.assertNotIn("sPlannerSelectedDecision", symbols.stdout)

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
            self.assertLessEqual(
                section_sizes.get("ewram_data", 0)
                + section_sizes.get(".bss", 0),
                4096,
            )
            self.assertEqual(section_sizes.get("iwram_data", 0), 0)
            self.assertLessEqual(
                section_sizes[".text"]
                + section_sizes[".rodata"]
                + section_sizes.get(".rodata.str1.4", 0),
                12 * 1024,
            )

            profile_sections: dict[bool, dict[str, int]] = {}
            for enabled in (False, True):
                profile_objects = []
                for source in (
                    root / "src" / "expansion_autoplay.c",
                    root / "src" / "rng.c",
                ):
                    output = temporary_path / (
                        f"{source.stem}-planner-{int(enabled)}.o"
                    )
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
                            "-I",
                            str(root / "include"),
                            "-I",
                            str(root / "include" / "generated"),
                            "-DFE8_EXPANSION_MODERN_BUILD=1",
                            "-DFE8_EXPANSION_DEBUG=1",
                            f"-DFE8_EXPANSION_AUTOPLAY_PLANNER={int(enabled)}",
                            "-c",
                            str(source),
                            "-o",
                            str(output),
                        ],
                        cwd=root,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stdout + completed.stderr,
                    )
                    profile_objects.append(output)
                sizes = subprocess.run(
                    [size, "-A", *map(str, profile_objects)],
                    cwd=root,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(sizes.returncode, 0, sizes.stdout + sizes.stderr)
                totals: dict[str, int] = {}
                for section, value in re.findall(
                    r"^(\S+)\s+(\d+)\s+\d+$",
                    sizes.stdout,
                    re.MULTILINE,
                ):
                    totals[section] = totals.get(section, 0) + int(value)
                profile_sections[enabled] = totals
            self.assertEqual(
                profile_sections[True].get("iwram_data", 0),
                profile_sections[False].get("iwram_data", 0),
            )
            self.assertLessEqual(
                profile_sections[True].get("ewram_data", 0)
                - profile_sections[False].get("ewram_data", 0),
                5,
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


class PlannerProcessTransport:
    def __init__(self, backend: Path, rom: Path) -> None:
        self.process = subprocess.Popen(
            [str(backend), str(rom)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.checkpoint: tuple[int, ...] = ()
        self.command: tuple[int, ...] = ()
        self.frame_count = 4
        self.observation = self._read_state()

    def _read_state(self) -> planner.Observation:
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr is not None else ""
            raise AssertionError(f"planner transport terminated: {stderr}")
        fields = line.split()
        if (
            not fields
            or fields[0] != "OBS"
            or "CHECKPOINT" not in fields
            or "COMMAND" not in fields
        ):
            raise AssertionError(f"unexpected planner transport response: {line}")
        checkpoint_index = fields.index("CHECKPOINT")
        command_index = fields.index("COMMAND")
        observation = planner.parse_transport_observation(
            int(value, 16) for value in fields[1:checkpoint_index]
        )
        self.checkpoint = tuple(
            int(value, 16)
            for value in fields[checkpoint_index + 1 : command_index]
        )
        self.command = tuple(
            int(value, 16) for value in fields[command_index + 1 :]
        )
        self.observation = observation
        return observation

    def _send(self, line: str) -> planner.Observation:
        assert self.process.stdin is not None
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        return self._read_state()

    def start(
        self,
        *,
        scenario_identity: int | None = None,
    ) -> planner.Observation:
        ready = self.observation
        return self._send(
            "START {:08x} {:08x} {:08x} {:08x}".format(
                ready.actual_rom_identity,
                ready.actual_config_identity,
                ready.actual_scenario_identity
                if scenario_identity is None
                else scenario_identity,
                ready.actual_seed_identity,
            )
        )

    def exchange(
        self, command: planner.Command
    ) -> planner.Observation:
        if command.kind is planner.CommandKind.PAGE:
            if command.page_index is None:
                raise AssertionError("PAGE requires page_index")
            return self._send(
                "PAGE {:08x} {:08x} {:08x}".format(
                    command.run_id,
                    command.observation_id,
                    command.page_index,
                )
            )
        if command.kind is planner.CommandKind.COMMIT:
            if command.action_ordinal is None or command.token is None:
                raise AssertionError("COMMIT requires ordinal and opaque token")
            return self._send(
                "COMMIT {:08x} {:08x} {:08x} {:08x} {:08x}".format(
                    command.run_id,
                    command.observation_id,
                    command.action_ordinal,
                    command.token.lo,
                    command.token.hi,
                )
            )
        if command.kind is planner.CommandKind.CANCEL:
            return self._send(
                "CANCEL {:08x} {:08x}".format(
                    command.run_id,
                    command.observation_id,
                )
            )
        raise AssertionError(f"unsupported transport command {command.kind}")

    def malformed(self, kind: int = 0xFFFFFFFF) -> planner.Observation:
        return self._send(
            "MALFORMED {:08x} {:08x} {:08x}".format(
                kind,
                self.observation.run_id,
                self.observation.observation_id,
            )
        )

    def step(self) -> planner.Observation:
        observation = self._send("STEP")
        self.frame_count += 1
        return observation

    def run_frames(self, count: int, keys: int = 0) -> planner.Observation:
        observation = self._send(f"RUN {count:x} {keys:x}")
        self.frame_count += count
        return observation

    def close(self) -> None:
        if self.process.poll() is None and self.process.stdin is not None:
            self.process.stdin.write("QUIT\n")
            self.process.stdin.flush()
        self.process.communicate(timeout=10)


class PlannerLibmGBAIntegrationTests(unittest.TestCase):
    def _build_transport(self, temporary: str) -> tuple[Path, Path]:
        rom = Path(temporary) / "planner-two-chapter.gba"
        elf = Path(temporary) / "planner-two-chapter.elf"
        backend = Path(temporary) / "planner-transport"
        build_production_planner_rom(rom, elf)
        build_planner_transport_backend(backend, elf)
        return rom, backend

    def _run_planner(
        self,
        backend: Path,
        rom: Path,
        implementation: planner.ScriptedPlanner | planner.BoundedSearchPlanner,
    ) -> tuple[planner.Observation, tuple[int, ...]]:
        transport = PlannerProcessTransport(backend, rom)
        try:
            waiting = transport.start()
            self.assertEqual(waiting.state, 2)
            first = planner.collect_observation_pages(transport, waiting)
            self.assertEqual(len(first.fields), planner.SEMANTIC_FIELD_COUNT)
            self.assertEqual(len(first.map_cells), 32 * 17)
            self.assertEqual(len(first.units), 1)
            self.assertEqual(len(first.actions), 512)
            fields = {field.name: field for field in first.fields}
            self.assertEqual(
                fields["map_dimensions"].value,
                32 | (17 << 16),
            )
            self.assertEqual(
                fields["active_unit"].availability,
                planner.Availability.AVAILABLE,
            )
            self.assertEqual(
                fields["objective_id"].availability,
                planner.Availability.UNSUPPORTED_RULE,
            )
            hidden = next(
                cell for cell in first.map_cells
                if (cell.x, cell.y) == (1, 0)
            )
            self.assertEqual(hidden.availability, planner.Availability.NOT_VISIBLE)
            self.assertEqual(hidden.unit, 0)
            choice = implementation.choose(first)
            waiting = transport.exchange(
                planner.Command(
                    planner.CommandKind.COMMIT,
                    first.run_id,
                    first.observation_id,
                    choice.ordinal,
                    choice.token,
                )
            )
            self.assertEqual(waiting.state, 2)
            self.assertEqual(waiting.chapter, 2)
            self.assertEqual(transport.checkpoint[4], 1)

            second = planner.collect_observation_pages(transport, waiting)
            choice = implementation.choose(second)
            committed = transport.exchange(
                planner.Command(
                    planner.CommandKind.COMMIT,
                    second.run_id,
                    second.observation_id,
                    choice.ordinal,
                    choice.token,
                )
            )
            self.assertEqual(committed.state, 3)
            self.assertEqual(transport.checkpoint[4], 2)
            return committed, transport.checkpoint
        finally:
            transport.close()

    def test_host_driven_production_mailbox_replays_two_chapters(self):
        root = TESTS_DIR.parents[2] / "build" / "test-artifacts" / "autoplay-planner"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            try:
                rom, backend = self._build_transport(temporary)
            except RuntimeError as error:
                if (
                    "planner runtime toolchain unavailable" in str(error)
                    or "planner transport host compiler unavailable" in str(error)
                ):
                    self.skipTest(str(error))
                raise
            scripted = self._run_planner(
                backend, rom, planner.ScriptedPlanner()
            )
            searched = self._run_planner(
                backend, rom, planner.BoundedSearchPlanner(max_nodes=512)
            )
            self.assertEqual(scripted, searched)

    def test_host_driven_transport_rejects_and_times_out(self):
        root = TESTS_DIR.parents[2] / "build" / "test-artifacts" / "autoplay-planner"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            try:
                rom, backend = self._build_transport(temporary)
            except RuntimeError as error:
                if (
                    "planner runtime toolchain unavailable" in str(error)
                    or "planner transport host compiler unavailable" in str(error)
                ):
                    self.skipTest(str(error))
                raise

            transport = PlannerProcessTransport(backend, rom)
            try:
                rejected = transport.start(
                    scenario_identity=transport.observation.actual_scenario_identity ^ 1
                )
                self.assertEqual(rejected.rejection, 9)
                malformed = transport.malformed()
                self.assertEqual(malformed.rejection, 9)
                waiting = transport.start()
                complete = planner.collect_observation_pages(transport, waiting)
                choice = planner.ScriptedPlanner().choose(complete)
                forged = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        complete.run_id,
                        complete.observation_id,
                        choice.ordinal,
                        planner.OpaqueToken(choice.token.lo, choice.token.hi ^ 1),
                    )
                )
                self.assertEqual(forged.rejection, 4)
                cancelled = transport.exchange(
                    planner.Command(
                        planner.CommandKind.CANCEL,
                        complete.run_id,
                        complete.observation_id,
                    )
                )
                self.assertEqual(cancelled.state, 4)
                self.assertEqual(cancelled.rejection, 8)
            finally:
                transport.close()

            transport = PlannerProcessTransport(backend, rom)
            try:
                waiting = transport.start()
                for _ in range(300):
                    waiting = transport.malformed()
                    if waiting.state == 4:
                        break
                self.assertEqual(waiting.state, 4)
                self.assertEqual(waiting.rejection, 10)
            finally:
                transport.close()

    @unittest.skipUnless(
        os.environ.get("PLANNER_PRODUCTION_ROM")
        and os.environ.get("PLANNER_PRODUCTION_ELF"),
        "enabled production ROM/ELF not supplied",
    )
    def test_enabled_production_rom_executes_host_selected_action(self):
        rom = Path(os.environ["PLANNER_PRODUCTION_ROM"])
        elf = Path(os.environ["PLANNER_PRODUCTION_ELF"])
        root = TESTS_DIR.parents[2] / "build" / "test-artifacts" / "autoplay-planner"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            backend = Path(temporary) / "planner-transport"
            build_planner_transport_backend(backend, elf)
            route = json.loads(
                (
                    TESTS_DIR.parent
                    / "scenarios"
                    / "starter-danger-overlay-negative-modern-debug.json"
                ).read_text(encoding="utf-8")
            )
            transport = PlannerProcessTransport(backend, rom)
            try:
                target_frame = 3950
                for frame_range in route["frames"]:
                    start = frame_range["start"]
                    end = min(frame_range["end"], target_frame)
                    if start > target_frame:
                        break
                    if transport.frame_count < start:
                        transport.run_frames(start - transport.frame_count)
                    keys = 0
                    for name in frame_range["keys"]:
                        keys |= gba_playtest.KEY_BITS[name]
                    transport.run_frames(end - start + 1, keys)
                if transport.frame_count <= target_frame:
                    transport.run_frames(target_frame + 1 - transport.frame_count)

                for index in range(8):
                    if transport.observation.state == 1:
                        break
                    key = (
                        gba_playtest.KEY_BITS["START"]
                        if index % 2 == 0
                        else gba_playtest.KEY_BITS["A"]
                    )
                    transport.run_frames(6, key)
                    transport.run_frames(59)
                self.assertEqual(transport.observation.state, 1)
                waiting = transport.start()
                self.assertEqual(waiting.state, 2)
                complete = planner.collect_observation_pages(transport, waiting)
                self.assertGreater(len(complete.map_cells), 0)
                self.assertGreater(len(complete.units), 0)
                self.assertGreater(len(complete.actions), 0)
                choice = planner.ScriptedPlanner().choose(complete)
                actor_before = next(
                    unit for unit in complete.units if unit.slot == choice.action.actor
                )
                self.assertNotEqual(actor_before.position, choice.action.destination)

                forged = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        complete.run_id,
                        complete.observation_id,
                        choice.ordinal,
                        planner.OpaqueToken(choice.token.lo ^ 1, choice.token.hi),
                    )
                )
                self.assertEqual(forged.rejection, 4)
                accepted = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        complete.run_id,
                        complete.observation_id,
                        choice.ordinal,
                        choice.token,
                    )
                )
                self.assertIn(accepted.state, (2, 3))

                for _ in range(20):
                    if (
                        transport.observation.state == 2
                        and transport.observation.observation_id
                        != complete.observation_id
                    ):
                        break
                    transport.run_frames(60)
                self.assertEqual(transport.observation.state, 2)
                self.assertNotEqual(
                    transport.observation.observation_id,
                    complete.observation_id,
                )
                followup = planner.collect_observation_pages(
                    transport, transport.observation
                )
                actor_after = next(
                    unit for unit in followup.units if unit.slot == choice.action.actor
                )
                self.assertEqual(actor_after.position, choice.action.destination)
                cancelled = transport.exchange(
                    planner.Command(
                        planner.CommandKind.CANCEL,
                        followup.run_id,
                        followup.observation_id,
                    )
                )
                self.assertEqual(cancelled.state, 4)
            finally:
                transport.close()


if __name__ == "__main__":
    unittest.main()
