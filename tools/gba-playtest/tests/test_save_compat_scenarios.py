"""Runtime coverage for issue #2 slice 2's global save-compatibility gate,
generalized across every supported build: the legacy agbcc ROM
(fireemblem8.gba) and the modern AAPCS debug/release ROMs
(build/expansion-modern/{debug,release}/aapcs/fireemblem8.gba).

Both persisted struct-layout divergences previously exposed under AAPCS
(bitfield-boundary packing in struct Dungeon, struct-size rounding in
struct BonusClaimSaveData -- see
scripts/modernize/tests/test_save_format_layout.py) have been root-caused
and fixed (include/bmdifficulty.h, include/bmsave.h). All eight
SaveCompatState values, Back-preservation, confirmed erase, and the
host-migrated v0->v1 load are therefore verified here against every
supported build identically -- no ABI scoping, no legacy-only carve-out.

Each ROM's coverage is skipped independently (never a false pass) if that
particular ROM has not been built yet:
  - legacy:         `make fireemblem8.gba`
  - modern debug:   `make expansion-modern-rom MODERN_CONFIG=debug MODERN_ABI=aapcs`
  - modern release: `make expansion-modern-rom MODERN_CONFIG=release MODERN_ABI=aapcs`
or if the libmGBA backend is unavailable in this environment.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
SCENARIOS_DIR = PLAYTEST_DIR / "scenarios"
FINGERPRINTS_DIR = PLAYTEST_DIR / "fingerprints"

sys.path.insert(0, str(PLAYTEST_DIR))
sys.path.insert(0, str(PLAYTEST_DIR / "tests"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "modernize"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "modernize" / "tests"))

import gba_playtest  # noqa: E402
import sram_fixture as sf  # noqa: E402

# Every non-CURRENT SaveCompatState covered by the savecompat-dialog-back
# scenario, mapped from its fixture/fingerprint name to the state constant.
_DIALOG_BACK_STATES = {
    "valid-legacy": sf.STATE_VALID_LEGACY,
    "header-corrupt": sf.STATE_HEADER_CORRUPT,
    "metadata-corrupt": sf.STATE_METADATA_CORRUPT,
    "older": sf.STATE_OLDER,
    "newer": sf.STATE_NEWER,
    "config-incompatible": sf.STATE_CONFIG_INCOMPATIBLE,
}

# (fingerprint-suffix, ROM path relative to REPO_ROOT, human-readable name)
_ROMS = {
    "legacy": REPO_ROOT / "fireemblem8.gba",
    "modern-debug": REPO_ROOT / "build" / "expansion-modern" / "debug" / "aapcs" / "fireemblem8.gba",
    "modern-release": REPO_ROOT / "build" / "expansion-modern" / "release" / "aapcs" / "fireemblem8.gba",
}

_UNAVAILABLE_MARKERS = (
    "C compiler ",
    "mgba/core/core.h: No such file",
    "'mgba/core/core.h' file not found",
    "cannot find -lmgba",
    "library not found for -lmgba",
)


def _capture_or_skip(rom: Path, scenario, sram_image: Path):
    try:
        return gba_playtest.capture(rom, scenario, sram_image)
    except gba_playtest.PlaytestError as exc:
        if any(marker in str(exc) for marker in _UNAVAILABLE_MARKERS):
            raise unittest.SkipTest(
                f"libmGBA integration skipped explicitly: {exc}"
            ) from exc
        raise


def _load_committed_fingerprint(name: str) -> dict:
    path = FINGERPRINTS_DIR / f"{name}.json"
    return gba_playtest.validate_fingerprint(
        json.loads(path.read_text(encoding="utf-8")), str(path)
    )


def _make_test_class(suffix: str, rom: Path):
    """Builds one TestCase subclass per ROM, so a missing ROM only skips
    that ROM's coverage (never the others), and every failure names the
    exact ROM/fixture/state/checkpoint it came from."""

    class _SaveCompatScenarioTests(unittest.TestCase):
        __qualname__ = f"SaveCompatScenarioTests_{suffix.replace('-', '_')}"

        @classmethod
        def setUpClass(cls):
            if not rom.exists():
                raise unittest.SkipTest(
                    f"ROM not built for '{suffix}': {rom}"
                )

        def setUp(self):
            self._tmpdir = tempfile.TemporaryDirectory(prefix=f"gba-playtest-{suffix}-")
            self.addCleanup(self._tmpdir.cleanup)
            self.fixture_dir = Path(self._tmpdir.name)

        def test_current_state_no_dialog(self):
            """SAVE_COMPAT_CURRENT (via the auto-repaired
            SAVE_COMPAT_EMPTY input): normal save menu is entered and no
            compatibility dialog appears."""
            fixture_path = self.fixture_dir / "empty.sav"
            sf.write_fixture(fixture_path, sf.STATE_EMPTY)
            scenario = gba_playtest.load_scenario(SCENARIOS_DIR / "savecompat-current.json")
            actual = _capture_or_skip(rom, scenario, fixture_path)
            expected = _load_committed_fingerprint(f"savecompat-current-{suffix}")
            differences = gba_playtest.compare_fingerprints(expected, actual, policy="behavior")
            self.assertEqual(
                differences, [],
                f"rom={suffix} fixture=empty (SAVE_COMPAT_EMPTY): {differences}",
            )

        def test_each_non_current_state_shows_distinct_dialog_and_preserves_sram(self):
            """Every non-CURRENT state: the global dialog appears, the
            correct state-specific diagnostic message is selected, and
            Back leaves the entire SRAM image byte-for-byte unchanged."""
            dialog_scenario = gba_playtest.load_scenario(
                SCENARIOS_DIR / "savecompat-dialog-back.json"
            )
            dialog_shown_hashes: dict[str, str] = {}

            for name, state in _DIALOG_BACK_STATES.items():
                with self.subTest(rom=suffix, state=name):
                    fixture_path = self.fixture_dir / f"{name}.sav"
                    sf.write_fixture(fixture_path, state)
                    actual = _capture_or_skip(rom, dialog_scenario, fixture_path)

                    expected = _load_committed_fingerprint(
                        f"savecompat-dialog-back-{name}-{suffix}"
                    )
                    differences = gba_playtest.compare_fingerprints(
                        expected, actual, policy="behavior"
                    )
                    self.assertEqual(
                        differences, [],
                        f"rom={suffix} state={name} fixture={fixture_path.name}: "
                        f"{differences}",
                    )

                    checkpoints = {cp["name"]: cp for cp in actual["checkpoints"]}
                    sram_hashes = {cp["sram_hash"] for cp in checkpoints.values()}
                    self.assertEqual(
                        len(sram_hashes), 1,
                        f"rom={suffix} state={name}: SRAM must be byte-identical "
                        f"across dialog-shown/after-dismiss/back-returned "
                        f"checkpoints (found {sram_hashes})",
                    )
                    dialog_shown_hashes[name] = checkpoints["dialog-shown"]["framebuffer_hash"]

            # "correct state/message is selected" (requirement 7): every
            # state must render a visibly distinct diagnostic screen.
            self.assertEqual(
                len(set(dialog_shown_hashes.values())), len(dialog_shown_hashes),
                f"rom={suffix}: expected a distinct dialog per state, "
                f"got {dialog_shown_hashes}",
            )

        def test_confirmed_erase_produces_fresh_current_and_reaches_save_menu(self):
            """Confirmed full erase: image becomes a valid fresh CURRENT
            format and the normal save menu becomes reachable."""
            fixture_path = self.fixture_dir / "header-corrupt.sav"
            sf.write_fixture(fixture_path, sf.STATE_HEADER_CORRUPT)
            scenario = gba_playtest.load_scenario(SCENARIOS_DIR / "savecompat-erase.json")
            actual = _capture_or_skip(rom, scenario, fixture_path)
            expected = _load_committed_fingerprint(f"savecompat-erase-{suffix}")
            differences = gba_playtest.compare_fingerprints(expected, actual, policy="behavior")
            self.assertEqual(
                differences, [],
                f"rom={suffix} fixture=header-corrupt confirmed-erase: {differences}",
            )

            checkpoints = {cp["name"]: cp for cp in actual["checkpoints"]}
            self.assertNotEqual(
                checkpoints["erase-selected"]["sram_hash"],
                checkpoints["erased"]["sram_hash"],
                f"rom={suffix}: confirmed erase must change the SRAM image "
                f"(whole-chip wipe + fresh CURRENT initialization)",
            )

        def test_migrated_v1_load_reaches_normal_save_menu_with_no_dialog(self):
            """Host-migrated v0->v1 image: generated by the existing CLI,
            classified CURRENT, and loaded through the normal save path
            with no compatibility dialog."""
            source = self.fixture_dir / "host-migration-source-vanilla.sav"
            migrated = self.fixture_dir / "host-migration-dest-current.sav"
            sf.write_fixture(source, sf.STATE_VALID_LEGACY)
            sf.migrate_fixture(source, migrated)

            current_scenario = gba_playtest.load_scenario(
                SCENARIOS_DIR / "savecompat-current.json"
            )
            actual = _capture_or_skip(rom, current_scenario, migrated)

            expected = _load_committed_fingerprint(f"savecompat-current-migrated-{suffix}")
            differences = gba_playtest.compare_fingerprints(expected, actual, policy="behavior")
            self.assertEqual(
                differences, [],
                f"rom={suffix}: migrated-v1 load did not reach the normal "
                f"save menu unchanged: {differences}",
            )

    _SaveCompatScenarioTests.__name__ = f"SaveCompatScenarioTests_{suffix.replace('-', '_')}"
    return _SaveCompatScenarioTests


# Register one TestCase subclass per ROM in this module's globals so
# unittest's test discovery picks all of them up independently.
for _suffix, _rom in _ROMS.items():
    _cls = _make_test_class(_suffix, _rom)
    globals()[_cls.__name__] = _cls
del _suffix, _rom, _cls


if __name__ == "__main__":
    unittest.main()
