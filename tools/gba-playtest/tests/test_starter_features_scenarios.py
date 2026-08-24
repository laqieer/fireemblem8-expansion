"""
Issue #6 starter-feature runtime scenarios (reuses the issue #13 gba-playtest
harness; no new framework).

Schema/pointer-audit checks always run. The libmGBA runtime verifications run
only when the Make gates point at built modern ROMs (the starter runtime gate
does this after building the dedicated starter-foundation profile ROM and the
default ROM);
otherwise they skip, exactly like the other modern-ROM scenario tests that
skip when the ROM has not been built.
"""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
SCENARIOS_DIR = PLAYTEST_DIR / "scenarios"
FINGERPRINTS_DIR = PLAYTEST_DIR / "fingerprints"
TESTS_DIR = Path(__file__).resolve().parent
for path in (PLAYTEST_DIR, TESTS_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import gba_playtest  # noqa: E402
import check_starter_probe_addresses  # noqa: E402
import host_mode  # noqa: E402

POSITIVE = "starter-hook-modern-debug"
NEGATIVE = "starter-hook-negative-modern-debug"

# Pointer-like value bands (mirrors the pointer-oracle audit intent): a probe
# expectation must never assert a live ROM/RAM pointer value.
_POINTER_BANDS = (
    (0x02000000, 0x0203FFFF),  # EWRAM
    (0x03000000, 0x03007FFF),  # IWRAM
    (0x08000000, 0x09FFFFFF),  # ROM
)


def _is_pointer_like(value, size):
    if size != 4:
        return False
    return any(low <= value <= high for (low, high) in _POINTER_BANDS)


class StarterHookScenarioSchemaTests(unittest.TestCase):
    """Always-on: the committed scenario/fingerprint pairs are well-formed,
    enabled, and only ever assert semantic scalars (never a pointer)."""

    def _scenario(self, name):
        return gba_playtest.load_scenario(SCENARIOS_DIR / (name + ".json"))

    def _fingerprint(self, name):
        path = FINGERPRINTS_DIR / (name + ".json")
        import json
        return gba_playtest.validate_fingerprint(
            json.loads(path.read_text(encoding="utf-8")), str(path), policy="behavior"
        )

    def test_both_scenarios_parse_and_are_enabled(self):
        for name in (POSITIVE, NEGATIVE):
            scenario = self._scenario(name)
            self.assertFalse(getattr(scenario, "disabled", False),
                             "%s must not be a stub/disabled scenario" % name)
            self.assertTrue(scenario.checkpoints, "%s needs checkpoints" % name)

    def test_both_fingerprints_validate(self):
        for name in (POSITIVE, NEGATIVE):
            fp = self._fingerprint(name)
            self.assertEqual(fp["scenario"], name)

    def test_probe_addresses_bind_to_current_debug_and_release_elves(self):
        cases = (
            (
                "build/expansion-modern-starter/debug/aapcs/fireemblem8.elf",
                "starter-hook-modern-debug",
            ),
            (
                "build/expansion-modern/debug/aapcs/fireemblem8.elf",
                "starter-hook-negative-modern-debug",
            ),
            (
                "build/expansion-modern-starter/release/aapcs/fireemblem8.elf",
                "starter-hook-clean-modern-release",
            ),
            (
                "build/expansion-modern/release/aapcs/fireemblem8.elf",
                "starter-hook-clean-negative-modern-release",
            ),
        )
        missing = [
            elf
            for elf, _ in cases
            if not (REPO_ROOT / elf).is_file()
        ]
        if missing:
            raise unittest.SkipTest(
                "starter probe binding ELFs not built: %s" % ", ".join(missing)
            )
        for elf, name in cases:
            check_starter_probe_addresses.check_bindings(
                REPO_ROOT / elf,
                SCENARIOS_DIR / (name + ".json"),
                FINGERPRINTS_DIR / (name + ".json"),
            )

    def test_danger_overlay_probe_addresses_bind_to_current_elves(self):
        cases = (
            (
                "build/expansion-modern-starter/debug/aapcs/fireemblem8.elf",
                "starter-danger-overlay-modern-debug",
            ),
            (
                "build/expansion-modern/debug/aapcs/fireemblem8.elf",
                "starter-danger-overlay-negative-modern-debug",
            ),
            (
                "build/expansion-modern-starter/release/aapcs/fireemblem8.elf",
                "starter-danger-overlay-modern-release",
            ),
            (
                "build/expansion-modern/release/aapcs/fireemblem8.elf",
                "starter-danger-overlay-negative-modern-release",
            ),
        )
        missing = [
            elf
            for elf, _ in cases
            if not (REPO_ROOT / elf).is_file()
        ]
        if missing:
            raise unittest.SkipTest(
                "danger-overlay probe binding ELFs not built: %s" % ", ".join(missing)
            )
        for elf, name in cases:
            check_starter_probe_addresses.check_bindings(
                REPO_ROOT / elf,
                SCENARIOS_DIR / (name + ".json"),
                FINGERPRINTS_DIR / (name + ".json"),
                symbol="gExpansionDangerOverlayProbe",
                probe_size=5 * 4,
                checkpoint_markers=("overlay",),
            )

    def test_probes_are_semantic_scalars_not_pointers(self):
        import json
        for name in (POSITIVE, NEGATIVE):
            data = json.loads((FINGERPRINTS_DIR / (name + ".json")).read_text(encoding="utf-8"))
            for cp in data["checkpoints"]:
                for probe in cp.get("probes", []):
                    value = int(probe["value"], 16)
                    self.assertFalse(
                        _is_pointer_like(value, probe["size"]),
                        "%s checkpoint %r probe %s asserts a pointer-like value %s"
                        % (name, cp["name"], probe["address"], probe["value"]),
                    )

    def test_positive_asserts_hook_fired_and_negative_asserts_all_zero(self):
        import json
        pos = json.loads((FINGERPRINTS_DIR / (POSITIVE + ".json")).read_text(encoding="utf-8"))
        neg = json.loads((FINGERPRINTS_DIR / (NEGATIVE + ".json")).read_text(encoding="utf-8"))

        def probe_cp(data, needle):
            for cp in data["checkpoints"]:
                if needle in cp["name"]:
                    return cp
            raise AssertionError("no checkpoint matching %r" % needle)

        pos_mech = probe_cp(pos, "mechanics-probe")
        pos_values = [int(p["value"], 16) for p in pos_mech["probes"]]
        # registerOk=1, applyCount=2, sampleTrigger=2 among the semantic counters.
        self.assertIn(1, pos_values, "positive must record a registration")
        self.assertIn(2, pos_values, "positive must record apply/sample activity")

        neg_mech = probe_cp(neg, "mechanics-probe")
        self.assertTrue(all(int(p["value"], 16) == 0 for p in neg_mech["probes"]),
                        "negative control must keep every probe zero")

    def test_both_scenarios_resolve_the_same_real_combat(self):
        """Both scenarios must reach a genuine battle (enemy 15/15 -> 15/0),
        so the probe delta is attributable to real combat, not a faked write."""
        import json
        for name in (POSITIVE, NEGATIVE):
            data = json.loads((FINGERPRINTS_DIR / (name + ".json")).read_text(encoding="utf-8"))
            after = None
            for cp in data["checkpoints"]:
                if "dead" in cp["name"]:
                    after = cp
            self.assertIsNotNone(after, "%s must probe the post-hit enemy state" % name)
            curhp = [p for p in after["probes"] if p["address"] == "0x0202eba7"][0]
            self.assertEqual(int(curhp["value"], 16), 0,
                             "%s enemy must actually die in real combat" % name)


@host_mode.live_artifact_testcase("starter-hook runtime coverage")
class StarterHookRuntimeTests(unittest.TestCase):
    """Runtime libmGBA verification against built profile ROMs (skips unless a
    caller supplies them)."""

    def _verify(self, rom_env, scenario_name):
        rom = os.environ.get(rom_env)
        if not rom or not Path(rom).is_file():
            raise unittest.SkipTest("%s not set to a built ROM" % rom_env)
        import json
        scenario = gba_playtest.load_scenario(SCENARIOS_DIR / (scenario_name + ".json"))
        expected = gba_playtest.validate_fingerprint(
            json.loads((FINGERPRINTS_DIR / (scenario_name + ".json")).read_text(encoding="utf-8")),
            scenario_name,
            policy="behavior",
        )
        actual = host_mode.capture_live_or_skip(
            Path(rom),
            scenario,
            label="starter-hook runtime coverage",
        )
        diffs = gba_playtest.compare_fingerprints(expected, actual, policy="behavior")
        self.assertEqual(diffs, [], "runtime mismatch for %s:\n%s" % (scenario_name, "\n".join(diffs)))

    def test_positive_hook_scenario_on_starter_profile_rom(self):
        self._verify("STARTER_HOOK_ROM", POSITIVE)

    def test_negative_hook_scenario_on_default_rom(self):
        self._verify("STARTER_HOOK_NEGATIVE_ROM", NEGATIVE)


if __name__ == "__main__":
    unittest.main()
