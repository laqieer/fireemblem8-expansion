"""Lifecycle contracts for the issue #62 test-only battle-animation probe."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
sys.path.insert(0, str(PLAYTEST_DIR))

import gba_playtest  # noqa: E402


def function_body(source: str, name: str) -> str:
    start = source.index("{}(".format(name))
    opening = source.index("{", start)
    depth = 1
    index = opening + 1
    while depth:
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
        index += 1
    return source[opening + 1:index - 1]


class BanimPackageRuntimeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.commands = (REPO_ROOT / "src" / "banim-ekrcmd.c").read_text(encoding="utf-8")
        self.intro = (REPO_ROOT / "src" / "banim-ekrbattleintro.c").read_text(encoding="utf-8")
        self.starting = (REPO_ROOT / "src" / "banim-ekrbattlestarting.c").read_text(
            encoding="utf-8"
        )
        self.probe = (REPO_ROOT / "src" / "banim_package_runtime_test.c").read_text(
            encoding="utf-8"
        )
        self.events = (REPO_ROOT / "src" / "eventscr.c").read_text(encoding="utf-8")
        self.runner = (PLAYTEST_DIR / "run_banim_package_runtime_check.py").read_text(
            encoding="utf-8"
        )

    def test_alias_mutation_is_exclusive_to_the_scripted_resolver_path(self):
        self.assertNotIn(
            "BanimPackageRuntimeTest_BeginScriptedBattle",
            function_body(self.commands, "SetBattleScripted"),
        )
        self.assertIn(
            """#if FE8_BANIM_PACKAGE_RUNTIME_TEST
    if (CheckBattleScripted() == true)
    {
        BanimPackageRuntimeTest_BeginScriptedBattle();
    }
#endif""",
            self.intro,
        )
        begin = function_body(self.probe, "BanimPackageRuntimeTest_BeginScriptedBattle")
        self.assertIn(
            """if (CheckBattleScripted() == false)
        return;""",
            begin,
        )
        self.assertIn(
            "return gBanimPackageRuntimeTestProbe.selectionCount == 0;",
            function_body(self.probe, "BanimPackageRuntimeTest_ForceFirstScriptedBattle"),
        )
        self.assertIn(
            """#if FE8_BANIM_PACKAGE_RUNTIME_TEST
        if (BanimPackageRuntimeTest_ForceFirstScriptedBattle())""",
            self.events,
        )

    def test_entry_and_completion_use_real_battle_lifecycle_boundaries(self):
        self.assertIn(
            "BanimPackageRuntimeTest_MarkBattleEntry();",
            function_body(self.starting, "BeginAnimsOnBattleAnimations"),
        )
        self.assertNotIn(
            "BanimPackageRuntimeTest_MarkBattleComplete",
            function_body(self.commands, "SetBattleUnscripted"),
        )
        self.assertIn(
            "BanimPackageRuntimeTest_MarkBattleComplete();",
            function_body(self.starting, "EkrMainEndExec"),
        )
        complete = function_body(self.probe, "BanimPackageRuntimeTest_MarkBattleComplete")
        self.assertIn("battleEntryCount == 1", complete)

    def test_runtime_runner_has_default_negative_and_scripted_positive_controls(self):
        self.assertIn("banim-package-runtime-default-control", self.runner)
        self.assertIn('expect(default_values, name, 0, "default control")', self.runner)
        self.assertIn('expect(values, "selectionCount", 1, "scripted battle")', self.runner)
        self.assertIn('expect(values, "battleEntryCount", 1, "scripted battle")', self.runner)
        self.assertIn('expect(values, "battleCompleteCount", 1, "scripted battle")', self.runner)
        scenario = gba_playtest.load_scenario(
            PLAYTEST_DIR / "scenarios" / "combat.json"
        )
        self.assertEqual(scenario.checkpoints[0].frame, 3285)


if __name__ == "__main__":
    unittest.main()
