"""
Issue #11 slice 1 host tests -- debug-tools registry, hotkey-collision
guards, one-entry-path enforcement, and scenario schema validation.

Where possible these tests compile and *execute* the real, unmodified
project sources (include/expansion_debugtools.h, src/debugtools_registry.c)
with a native host compiler rather than re-implementing or pattern-matching
their logic, so a regression in the actual registry/gate code is caught
here, not only on real hardware/emulator playtests. See
tools/gba-playtest/tests/c/ for the small stub/driver sources that make
this possible; those files are test-only and are never referenced by
modern.mk/Makefile.
"""

import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INCLUDE_DIRS = [REPO_ROOT / "include", REPO_ROOT / "include" / "generated"]
C_FIXTURES_DIR = Path(__file__).resolve().parent / "c"
REGISTRY_SRC = REPO_ROOT / "src" / "debugtools_registry.c"
HEADER = REPO_ROOT / "include" / "expansion_debugtools.h"
TITLESCREEN_SRC = REPO_ROOT / "src" / "titlescreen.c"

CC = shutil.which("gcc") or shutil.which("cc")


def _skip_if_no_host_compiler():
    if CC is None:
        raise unittest.SkipTest("no host C compiler (gcc/cc) available")


def _include_flags():
    flags = []
    for d in INCLUDE_DIRS:
        flags += ["-I", str(d)]
    return flags


def _compile(work_dir: Path, src: Path, obj_name: str, defines=()):
    """Compiles a single source file to an object file with the host
    compiler. Returns (returncode, combined_output)."""
    obj = work_dir / obj_name
    cmd = [CC, "-c", "-w"] + _include_flags()
    for d in defines:
        cmd += ["-D", d]
    cmd += [str(src), "-o", str(obj)]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr, obj


def _link(work_dir: Path, objects, exe_name: str):
    exe = work_dir / exe_name
    cmd = [CC] + [str(o) for o in objects] + ["-o", str(exe)]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr, exe


def _run(exe: Path):
    proc = subprocess.run([str(exe)], capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def _defined_symbol_names(obj: Path) -> set:
    nm = shutil.which("nm")
    if nm is None:
        raise unittest.SkipTest("no host 'nm' available")
    proc = subprocess.run([nm, str(obj)], capture_output=True, text=True)
    names = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        # "<addr> <type> <name>" for defined symbols; undefined entries use
        # "                 U <name>" (no address column).
        if len(parts) == 3 and parts[1] != "U":
            names.add(parts[2])
        elif len(parts) == 2 and parts[0] != "U":
            names.add(parts[1])
    return names


class DebugToolsRegistryHostTests(unittest.TestCase):
    """Compiles and executes the real src/debugtools_registry.c (enabled
    path) against tools/gba-playtest/tests/c/debugtools_registry_driver.c,
    proving capacity (max 9), deterministic append order, duplicate
    id/label rejection, invalid-action rejection, capacity-full rejection,
    and out-of-range NULL reads -- all through the exact public API
    (include/expansion_debugtools.h) contributor code uses.
    """

    @classmethod
    def setUpClass(cls):
        _skip_if_no_host_compiler()

    def _build_and_run(self, tmp_path, driver_src, extra_objects_ok=True):
        work = Path(tmp_path)
        rc, out, registry_obj = _compile(
            work, REGISTRY_SRC, "registry_enabled.o",
            defines=["FE8_EXPANSION_DEBUGTOOLS_ENABLED=1"],
        )
        self.assertEqual(rc, 0, f"compiling src/debugtools_registry.c (enabled) failed:\n{out}")

        rc, out, stubs_obj = _compile(work, C_FIXTURES_DIR / "debugtools_host_stubs.c", "stubs.o")
        self.assertEqual(rc, 0, f"compiling debugtools_host_stubs.c failed:\n{out}")

        rc, out, driver_obj = _compile(work, driver_src, "driver.o")
        self.assertEqual(rc, 0, f"compiling {driver_src.name} failed:\n{out}")

        rc, out, exe = _link(work, [registry_obj, stubs_obj, driver_obj], "test_exe")
        self.assertEqual(rc, 0, f"linking host registry test failed:\n{out}")

        rc, out = _run(exe)
        return rc, out

    def test_registry_capacity_order_and_errors(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self._build_and_run(
                tmp, C_FIXTURES_DIR / "debugtools_registry_driver.c"
            )
            self.assertEqual(rc, 0, f"host registry test failed:\n{out}")
            self.assertIn("DEBUGTOOLS_HOST_TEST: PASS", out)
            self.assertIn("DEBUGTOOLS_ACTION_MAX=9", out)
            self.assertIn("DEBUGTOOLS_HUB_MENU_SLOTS=11", out)

    def test_registry_disabled_path_behavior_and_symbol_omission(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            rc, out, registry_obj = _compile(
                work, REGISTRY_SRC, "registry_disabled.o",
                defines=["FE8_EXPANSION_DEBUGTOOLS_ENABLED=0"],
            )
            self.assertEqual(rc, 0, f"compiling src/debugtools_registry.c (disabled) failed:\n{out}")

            # Physical omission: the hub menu table and every hub-internal
            # static function must not exist as symbols at all in the
            # disabled translation unit -- this is the same evidence a
            # release ELF's `nm` output gives, but checkable on the host
            # without the arm-none-eabi toolchain.
            defined = _defined_symbol_names(registry_obj)
            forbidden = {
                "gDebugToolsHubMenuDef",
                "DebugToolsHub_BuildMenuItems",
                "DebugToolsHub_ShowDiagnostics",
                "DebugToolsHub_BackSelected",
                "DebugToolsHub_OnEnd",
            }
            leaked = forbidden & defined
            self.assertFalse(
                leaked,
                f"disabled build must physically omit hub internals; found: {leaked}",
            )
            # gDebugToolsProbe must still be linked in every build (the
            # release playtest scenario reads it directly by address).
            self.assertIn("gDebugToolsProbe", defined)

            rc, out, driver_obj = _compile(
                work, C_FIXTURES_DIR / "debugtools_disabled_driver.c", "disabled_driver.o"
            )
            self.assertEqual(rc, 0, f"compiling debugtools_disabled_driver.c failed:\n{out}")

            rc, out, exe = _link(work, [registry_obj, driver_obj], "disabled_test_exe")
            self.assertEqual(rc, 0, f"linking disabled host test failed:\n{out}")

            rc, out = _run(exe)
            self.assertEqual(rc, 0, f"disabled-path host test failed:\n{out}")
            self.assertIn("DEBUGTOOLS_DISABLED_HOST_TEST: PASS", out)


class DebugToolsHotkeyCollisionHostTests(unittest.TestCase):
    """Compiles small snippets against the real include/expansion_debugtools.h
    to prove its compile-time #error guards actually fire for both reserved
    soft-reset combos and for a zero mask, and that a legitimate override
    (and the untouched default) still compile cleanly."""

    @classmethod
    def setUpClass(cls):
        _skip_if_no_host_compiler()

    def _compile_snippet(self, tmp_path, body: str):
        work = Path(tmp_path)
        src = work / "snippet.c"
        src.write_text(body)
        cmd = [CC, "-c", "-w"] + _include_flags() + [str(src), "-o", str(work / "snippet.o")]
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
        return proc.returncode, proc.stdout + proc.stderr

    def test_default_mask_compiles(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self._compile_snippet(
                tmp, '#include "expansion_debugtools.h"\nint main(void) { return 0; }\n'
            )
            self.assertEqual(rc, 0, f"the untouched default hotkey mask must compile cleanly:\n{out}")

    def test_zero_mask_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self._compile_snippet(
                tmp,
                "#define FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK (0)\n"
                '#include "expansion_debugtools.h"\n'
                "int main(void) { return 0; }\n",
            )
            self.assertNotEqual(rc, 0, "a zero hotkey mask must fail to compile")
            self.assertIn("must not be 0", out)

    def test_l_r_a_b_soft_reset_collision_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self._compile_snippet(
                tmp,
                "#define FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK "
                "(L_BUTTON | R_BUTTON | A_BUTTON | B_BUTTON)\n"
                '#include "expansion_debugtools.h"\n'
                "int main(void) { return 0; }\n",
            )
            self.assertNotEqual(rc, 0, "the L+R+A+B soft-reset combo must be rejected at compile time")
            self.assertIn("L+R+A+B soft-reset combo", out)

    def test_a_b_select_start_soft_reset_collision_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self._compile_snippet(
                tmp,
                "#define FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK "
                "(A_BUTTON | B_BUTTON | SELECT_BUTTON | START_BUTTON)\n"
                '#include "expansion_debugtools.h"\n'
                "int main(void) { return 0; }\n",
            )
            self.assertNotEqual(rc, 0, "the A+B+SELECT+START soft-reset combo must be rejected at compile time")
            self.assertIn("A+B+SELECT+START soft-reset combo", out)

    def test_legitimate_custom_mask_compiles(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc, out = self._compile_snippet(
                tmp,
                "#define FE8_EXPANSION_DEBUGTOOLS_HOTKEY_MASK (L_BUTTON | SELECT_BUTTON)\n"
                '#include "expansion_debugtools.h"\n'
                "int main(void) { return 0; }\n",
            )
            self.assertEqual(rc, 0, f"a non-colliding custom mask must compile cleanly:\n{out}")


class DebugToolsOneEntryPathTests(unittest.TestCase):
    """Structural (source-grep) proof that the title-screen hotkey has
    exactly one call site and one definition, matching the WHERE
    constraint that this slice adds exactly one global hub entry path."""

    def test_hotkey_check_has_exactly_one_call_site(self):
        call_sites = []
        for src in (REPO_ROOT / "src").glob("*.c"):
            text = src.read_text(encoding="utf-8", errors="replace")
            call_sites += [
                f"{src.name}:{i + 1}"
                for i, line in enumerate(text.splitlines())
                if re.search(r"\bDebugTools_TitleHotkeyCheck\s*\(", line)
                and "void DebugTools_TitleHotkeyCheck" not in line
            ]
        self.assertEqual(len(call_sites), 1, f"expected exactly one call site, found: {call_sites}")
        self.assertTrue(call_sites[0].startswith("titlescreen.c:"), call_sites)

    def test_hotkey_check_defined_exactly_once_per_config(self):
        # Two definitions live in the same file under #if/#else -- so the
        # *pattern* count is 2 (enabled + disabled bodies), never more;
        # that's the single supported source of truth for the entry path.
        text = REGISTRY_SRC.read_text(encoding="utf-8", errors="replace")
        defs = re.findall(r"^void DebugTools_TitleHotkeyCheck\(void\)$", text, flags=re.MULTILINE)
        self.assertEqual(len(defs), 2, "expected exactly one enabled + one disabled definition")

    def test_dormant_tools_untouched(self):
        for name in ("src/bmdebug.c", "src/uidebug.c", "src/menu_def.c"):
            text = (REPO_ROOT / name).read_text(encoding="utf-8", errors="replace")
            self.assertNotIn("DebugTools_", text, f"{name} must stay untouched/unreachable per issue #11 slice 1 WHERE")


class DebugToolsScenarioSchemaTests(unittest.TestCase):
    """Validates the two new playtest scenarios against gba_playtest's own
    schema parser (the same validation `capture`/`verify` perform), and
    checks the debug/release scenarios assert complementary, non-trivial
    expectations (not just a placeholder pass-through)."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO_ROOT / "tools" / "gba-playtest"))
        global gba_playtest
        import gba_playtest  # noqa: F401 (imported for its side effect / module ref)

    def _load(self, name):
        import json
        path = REPO_ROOT / "tools" / "gba-playtest" / "scenarios" / name
        data = json.loads(path.read_text(encoding="utf-8"))
        return gba_playtest.parse_scenario_data(data), data

    def test_debug_scenario_is_schema_valid(self):
        scenario, data = self._load("debugtools-hub-modern-debug.json")
        self.assertEqual(scenario.name, "debugtools-hub-modern-debug")
        self.assertEqual(len(data["checkpoints"]), 5)
        last = data["checkpoints"][-1]
        self.assertEqual(last["probes"][0]["expected"], "0x00000001")  # hubOpenCount
        self.assertEqual(last["probes"][3]["expected"], "0x44424c31")  # launcherArmed magic

    def test_release_scenario_is_schema_valid(self):
        scenario, data = self._load("debugtools-hub-modern-release.json")
        self.assertEqual(scenario.name, "debugtools-hub-modern-release")
        self.assertEqual(len(data["checkpoints"]), 5)
        for checkpoint in data["checkpoints"]:
            for probe in checkpoint["probes"]:
                self.assertEqual(probe["expected"], "0x00000000")

    def test_debug_and_release_scenarios_use_identical_input_script(self):
        _, debug_data = self._load("debugtools-hub-modern-debug.json")
        _, release_data = self._load("debugtools-hub-modern-release.json")
        self.assertEqual(
            debug_data["frames"], release_data["frames"],
            "release must replay the exact same frame-for-frame input as debug "
            "to prove the hotkey has no effect, not merely a different script",
        )

    def test_debug_and_release_scenarios_probe_their_own_config_addresses(self):
        _, debug_data = self._load("debugtools-hub-modern-debug.json")
        _, release_data = self._load("debugtools-hub-modern-release.json")
        debug_addrs = [p["address"] for p in debug_data["checkpoints"][0]["probes"]]
        release_addrs = [p["address"] for p in release_data["checkpoints"][0]["probes"]]
        self.assertNotEqual(debug_addrs, release_addrs)
        self.assertEqual(len(debug_addrs), 4)
        self.assertEqual(len(release_addrs), 4)

    def test_debug_scenario_has_two_hotkey_pulses_before_the_select(self):
        """Review-fix regression: the reviewer reproduced hubOpenCount=2 by
        releasing and re-pressing SELECT+R while the hub remained open
        (pulses at 600-606 and 650-656). The scenario's frame script must
        replay that exact repeated-pulse shape, with the A-select occurring
        only after both pulses complete."""
        _, data = self._load("debugtools-hub-modern-debug.json")
        hotkey_frames = [
            f for f in data["frames"]
            if set(f["keys"]) == {"SELECT", "R"}
        ]
        self.assertEqual(
            len(hotkey_frames), 2,
            "expected exactly two separate SELECT+R hotkey pulses (the reviewer's "
            "release-and-repress reproduction), found: {}".format(hotkey_frames),
        )
        # The two pulses must be fully separated (keys released in between)
        # so each one independently edge-triggers newKeys, matching the
        # reviewer's exact repro rather than one continuous hold.
        self.assertLess(hotkey_frames[0]["end"], hotkey_frames[1]["start"])
        a_frames = [f for f in data["frames"] if f["keys"] == ["A"] and f["start"] > hotkey_frames[1]["end"]]
        self.assertEqual(len(a_frames), 1, "the launcher-selecting A press must occur strictly after both hotkey pulses")

    def test_debug_scenario_proves_hub_open_count_stays_one_after_repeated_pulse(self):
        """The critical regression-proof checkpoint: hubOpenCount must be
        exactly 1 both right after the first pulse and again after the
        second (repeated) pulse -- never 2."""
        _, data = self._load("debugtools-hub-modern-debug.json")
        by_name = {c["name"]: c for c in data["checkpoints"]}
        self.assertIn("hub-opened-after-first-pulse", by_name)
        self.assertIn("hub-still-single-after-repeated-pulse", by_name)
        first = by_name["hub-opened-after-first-pulse"]
        second = by_name["hub-still-single-after-repeated-pulse"]
        self.assertLess(first["frame"], second["frame"])
        self.assertEqual(first["probes"][0]["expected"], "0x00000001")  # hubOpenCount
        self.assertEqual(
            second["probes"][0]["expected"], "0x00000001",
            "hubOpenCount must still read exactly 1 after the repeated hotkey pulse "
            "-- a value of 2 here would reproduce the reviewer's defect",
        )


class DebugToolsTimerFreezeTests(unittest.TestCase):
    """Structural and scenario-schema proof for the review-fix MEDIUM
    defect: Title_IDLE's proc->timer_idle (and its gDebugToolsProbe mirror)
    must freeze while the debug hub is active, rather than silently sailing
    past the vanilla `== 815` attract-mode-transition threshold."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO_ROOT / "tools" / "gba-playtest"))
        global gba_playtest
        import gba_playtest  # noqa: F401

    def test_hub_active_check_precedes_timer_idle_increment(self):
        """Structural ordering proof: within Title_IDLE, the
        DebugTools_IsHubActive() check guarding the increment must appear
        (textually, and therefore control-flow-wise for this straight-line
        function body) before the proc->timer_idle++ statement it guards."""
        text = TITLESCREEN_SRC.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"void Title_IDLE\([^)]*\)\s*\{(.*?)\n\}", text, flags=re.DOTALL)
        self.assertIsNotNone(match, "could not locate Title_IDLE's function body")
        body = match.group(1)

        guard_pos = body.find("DebugTools_IsHubActive")
        increment_pos = body.find("timer_idle++")
        self.assertNotEqual(guard_pos, -1, "Title_IDLE must reference DebugTools_IsHubActive()")
        self.assertNotEqual(increment_pos, -1, "Title_IDLE must still increment proc->timer_idle somewhere")
        self.assertLess(
            guard_pos, increment_pos,
            "the DebugTools_IsHubActive() guard must appear before the "
            "proc->timer_idle++ statement it protects, so the increment "
            "is actually skipped while the hub is active rather than the "
            "hub check merely gating unrelated logic afterward",
        )

    def test_timer_idle_increment_is_conditional_on_hub_inactive(self):
        """The increment must be reachable only when the hub is NOT
        active (a guard of `if (!DebugTools_IsHubActive())`), not merely
        textually preceded by an unrelated hub check."""
        text = TITLESCREEN_SRC.read_text(encoding="utf-8", errors="replace")
        match = re.search(
            r"if\s*\(\s*!\s*DebugTools_IsHubActive\(\)\s*\)\s*\{([^}]*timer_idle\+\+[^}]*)\}",
            text,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(
            match,
            "expected an `if (!DebugTools_IsHubActive()) { ... proc->timer_idle++ ... }` "
            "guard wrapping the idle-timer increment in Title_IDLE",
        )

    def _load(self, name):
        import json
        path = REPO_ROOT / "tools" / "gba-playtest" / "scenarios" / name
        data = json.loads(path.read_text(encoding="utf-8"))
        return gba_playtest.parse_scenario_data(data), data

    def test_timer_freeze_scenario_is_schema_valid(self):
        scenario, data = self._load("debugtools-timer-freeze-modern-debug.json")
        self.assertEqual(scenario.name, "debugtools-timer-freeze-modern-debug")
        self.assertEqual(len(data["checkpoints"]), 3)

    def test_timer_freeze_scenario_proves_frozen_then_resumed(self):
        """The scenario JSON schema only supports exact-match expectations
        (no relational operators -- see gba_playtest.py's Probe.expected),
        so the freeze/resume proof's inequality reasoning must live here:
        parse the two 'frozen' checkpoints' titleIdleTimerSample values as
        integers and assert they are exactly equal (frozen while the hub
        is open, spanning the 815-frame threshold), then assert the final
        post-close checkpoint's value is strictly greater (resumed
        ticking after the hub closes)."""
        _, data = self._load("debugtools-timer-freeze-modern-debug.json")
        by_name = {c["name"]: c for c in data["checkpoints"]}
        for name in ("hub-open-early-sample", "hub-open-late-frozen", "hub-closed-resumed"):
            self.assertIn(name, by_name, f"missing expected checkpoint: {name}")

        def timer_value(checkpoint):
            for probe in checkpoint["probes"]:
                if probe["address"] == "0x0203159c":
                    return int(probe["expected"], 16)
            self.fail(f"checkpoint {checkpoint['name']} does not probe titleIdleTimerSample")

        def hub_open_count(checkpoint):
            for probe in checkpoint["probes"]:
                if probe["address"] == "0x0203158c":
                    return int(probe["expected"], 16)
            self.fail(f"checkpoint {checkpoint['name']} does not probe hubOpenCount")

        early = timer_value(by_name["hub-open-early-sample"])
        late = timer_value(by_name["hub-open-late-frozen"])
        resumed = timer_value(by_name["hub-closed-resumed"])

        self.assertEqual(
            early, late,
            "titleIdleTimerSample must be byte-for-byte identical across the "
            "two checkpoints spanning the 815-frame threshold while the hub "
            "remains open -- any difference means the timer advanced while blocked",
        )
        self.assertGreater(
            resumed, late,
            "titleIdleTimerSample must have increased after the hub is closed, "
            "proving idle-timer progress resumes rather than staying stuck forever",
        )

        # hubOpenCount must stay exactly 1 across opening, holding, and
        # closing the hub in this scenario -- opening once and closing it
        # must never require or trigger a second hub.
        for name in ("hub-open-early-sample", "hub-open-late-frozen", "hub-closed-resumed"):
            self.assertEqual(
                hub_open_count(by_name[name]), 1,
                f"hubOpenCount must stay 1 at checkpoint {name}",
            )

    def test_timer_freeze_scenario_holds_hub_open_across_815_threshold(self):
        """Sanity-check the scenario's own timing claim: the hub must open
        (via a single hotkey pulse) well before frame 1292 (empirically the
        frame at which a hub-free run's proc->timer_idle reaches 815, given
        the observed timer_idle == frame - 477 relationship while running
        freely) and must not be closed (no B press) until after it."""
        _, data = self._load("debugtools-timer-freeze-modern-debug.json")
        hotkey_frames = [f for f in data["frames"] if set(f["keys"]) == {"SELECT", "R"}]
        close_frames = [f for f in data["frames"] if f["keys"] == ["B"]]
        self.assertEqual(len(hotkey_frames), 1, "expected exactly one hotkey pulse opening the hub")
        self.assertEqual(len(close_frames), 1, "expected exactly one B press closing the hub")
        vanilla_815_frame = 815 + 477
        self.assertLess(hotkey_frames[0]["end"], vanilla_815_frame)
        self.assertGreater(close_frames[0]["start"], vanilla_815_frame)


if __name__ == "__main__":
    unittest.main()
