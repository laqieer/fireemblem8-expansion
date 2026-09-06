"""Mandatory modern-lane ARM positives, explicitly selected outside host discovery."""

from collections import Counter
import json
import shlex
import subprocess
import sys

from scripts.workflow_pilot.tests.test_review_subjects import SubjectTestCase
from scripts.workflow_pilot.tests.review_support import ENV, ROOT


class ArmSubjectTests(SubjectTestCase):
    def assert_complete_aoe(self, members, revision):
        observations = self.run_members(members, revision)
        self.assert_satisfied(observations)
        self.assertEqual({item.kind for item in observations}, {"native", "arm-object"})
        self.assertEqual(
            {item.obligation.member: item.checks for item in observations
             if item.kind == "arm-object"},
            {"enabled:objects": 2, "disabled:objects": 1})

    def test_complete_aoe_subject_has_native_and_arm_objects(self):
        self.assert_complete_aoe(self.tools.members(self.scope("aoe")), self.repo.base)

    def test_semantics_preserving_source_refactor_remains_green(self):
        path = "src/expansion_aoe.c"
        source = (self.repo.root / path).read_text()
        revision = self.repo.commit({path: "/* Formatting-only review control. */\n" + source})
        members = self.tools.members(self.scope("aoe", revision), (self.repo.base,))
        self.assert_complete_aoe(members, revision)

    def test_owned_non_system_tools_execute_actual_arm_probes(self):
        directory = self.repo.root / "build" / "owned arm tools"
        (directory / "bin").mkdir(parents=True)
        log = directory / "invocations"
        compiler_override = directory / "compiler override"
        for tool in ("gcc", "nm", "size", "gcc-override"):
            wrapper = compiler_override if tool == "gcc-override" else directory / "bin" / ("arm-none-eabi-" + tool)
            key = "MODERN_CC" if tool.startswith("gcc") else "MODERN_" + tool.upper()
            wrapper.write_text(
                "#!/bin/sh\nprintf '%s\\n' " + shlex.quote(tool) + " >> " + shlex.quote(str(log))
                + "\nexec " + shlex.quote(self.tools.arm_tools[key]) + ' "$@"\n')
            wrapper.chmod(0o700)
        probe = directory / "resolved-tools.mk"
        resolved_paths = directory / "resolved paths"
        probe.write_text(
            ".PHONY: __issue179_tools__\n__issue179_tools__:\n"
            '\t@printf "%s\\n" "$(MODERN_CC)" "$(MODERN_NM)" "$(MODERN_SIZE)" > '
            + shlex.quote(str(resolved_paths)) + "\n")
        code = """
import json, sys
sys.path.insert(0, sys.argv[1])
from scripts.workflow_pilot.trusted_review_gate import GitTree, ReviewTools
from scripts.workflow_pilot.tests.review_support import request
from pathlib import Path
root, revision = Path(sys.argv[2]), sys.argv[3]
tools = ReviewTools(GitTree(root, revision), root)
data = request("TC-GAMEPLAY-006", "aoe-item-dispatch", revision, revision)
observations = tools.run_obligations(tools.members(data), revision)
print(json.dumps([(item.obligation.member, item.verdict, item.checks) for item in observations]))
"""
        for override in (False, True):
            with self.subTest(compiler_override=override):
                log.unlink(missing_ok=True)
                resolved = subprocess.run(
                    ["make", "--no-print-directory", "-f", "Makefile", "-f", str(probe),
                     "MODERN_TOOLCHAIN_ROOT=" + str(directory),
                     *(["MODERN_CC=" + str(compiler_override)] if override else []),
                     "__issue179_tools__"],
                    cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=60)
                self.assertEqual(resolved.returncode, 0, resolved.stderr)
                paths = resolved_paths.read_text().splitlines()
                self.assertEqual(len(paths), 3)
                environment = {**ENV, **dict(zip(("MODERN_CC", "MODERN_NM", "MODERN_SIZE"), paths))}
                result = subprocess.run(
                    [sys.executable, "-I", "-c", code, str(ROOT), str(self.repo.root), self.repo.base],
                    cwd=ROOT, env=environment, capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(all(row[1] == "satisfied" and row[2] > 0
                                    for row in json.loads(result.stdout)))
                self.assertTrue(log.is_file(), "selected ARM tools were bypassed")
                compiler = "gcc-override" if override else "gcc"
                self.assertEqual(Counter(log.read_text().splitlines()), {compiler: 3, "nm": 3, "size": 1})
