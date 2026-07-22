#!/usr/bin/env python3
"""Runtime + host-migration check orchestrator for the global save-
compatibility gate (issue #2 slice 2, requirement 9).

Invoked by `modern.mk`'s `expansion-modern-savefmt-check` target (one call
per MODERN_CONFIG, always MODERN_ABI=aapcs -- the only ABI shipped). This
script, in order:

1. Generates the synthetic SRAM fixtures this check needs (never committed
   binaries -- see tools/gba-playtest/tests/sram_fixture.py) into a
   caller-supplied, ignored build/temp directory.
2. Runs the host-side save-format migration/classification checks
   (scripts/modernize/save_format_tool.py's own CLI, exercised exactly as a
   human operator would run it) against a freshly generated v0 (vanilla)
   fixture, proving the "host migration checks" part of requirement 9.
3. Runs the committed runtime scenarios (tools/gba-playtest/scenarios/
   savecompat-current.json, savecompat-dialog-back.json,
   savecompat-erase.json) against the given ROM via gba_playtest.py's
   `verify --policy behavior`, matched against the committed
   config-specific fingerprints, covering ALL EIGHT SaveCompatState values:
     - SAVE_COMPAT_CURRENT (via the auto-repaired SAVE_COMPAT_EMPTY input)
     - SAVE_COMPAT_VALID_LEGACY_OR_VANILLA
     - SAVE_COMPAT_HEADER_CORRUPT
     - SAVE_COMPAT_METADATA_CORRUPT
     - SAVE_COMPAT_MIGRATABLE_OLDER
     - SAVE_COMPAT_NEWER_UNSUPPORTED
     - SAVE_COMPAT_SAVE_CONFIG_INCOMPATIBLE
   plus confirmed full erase (HEADER_CORRUPT -> erase -> fresh CURRENT) and
   the host-migrated v0->v1 load (migrated image classifies CURRENT and
   reaches the normal save menu with no dialog).
4. For MODERN_CONFIG=debug only, also runs savesuspend-resume-modern-debug
   (tools/gba-playtest/scenarios/savesuspend-resume-modern-debug.json):
   issue #2's final slice, a deterministic ordinary-UI manual Suspend (Map
   Menu) -> soft-reset combo -> Resume (Save Menu) proof, built on #11
   Slice 1's debug-only "Fast Boot: Chapter 2" launcher. Omitted for
   MODERN_CONFIG=release, since that launcher does not exist in release
   builds (see docs/debugtools.md) -- release's checks above are
   completely unchanged.

Every failure identifies exactly which fixture/state/checkpoint it came
from (never a bare non-zero exit code) per requirement 9's "Failures must
identify fixture/state/checkpoint."

No ABI scoping: this script runs identically against any given ROM
(legacy agbcc or modern AAPCS debug/release). Both persisted struct-layout
divergences previously exposed under AAPCS (bitfield-boundary packing in
struct Dungeon, and struct-size rounding in struct BonusClaimSaveData --
see scripts/modernize/tests/test_save_format_layout.py's module docstring
and include/bmdifficulty.h / include/bmsave.h for the fixes) have been
root-caused and fixed, so a host-generated fixture that depends on
ExpansionSaveMeta's/SaveBlocks' exact byte offsets is reliable against the
compiled modern AAPCS ROM exactly as it is against legacy agbcc.

Deliberately dependency-free (Python stdlib only + subprocess calls to the
repository's own existing tools), matching this repository's conventions.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
SCENARIOS_DIR = PLAYTEST_DIR / "scenarios"
FINGERPRINTS_DIR = PLAYTEST_DIR / "fingerprints"
GBA_PLAYTEST = PLAYTEST_DIR / "gba_playtest.py"
SAVE_FORMAT_TOOL = REPO_ROOT / "scripts" / "modernize" / "save_format_tool.py"

sys.path.insert(0, str(PLAYTEST_DIR / "tests"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "modernize"))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "modernize" / "tests"))

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


class CheckFailure(Exception):
    """Raised with a message that always names the failing
    fixture/state/checkpoint, per requirement 9."""


def _run(cmd: list[str], what: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise CheckFailure(
            f"{what} failed (exit {result.returncode}):\n"
            f"  command: {' '.join(cmd)}\n"
            f"  stdout: {result.stdout.strip()}\n"
            f"  stderr: {result.stderr.strip()}"
        )


def run_host_migration_checks(fixture_dir: Path) -> None:
    """Requirement 9's "host migration checks": generate a fresh v0
    (vanilla/no-metadata) fixture and migrate it via the real,
    unmodified save_format_tool.py CLI, proving the migrated result is
    SAVE_COMPAT_CURRENT -- exactly the v0->v1 workflow the migrated-load
    scenario below depends on."""
    source = fixture_dir / "host-migration-source-vanilla.sav"
    dest = fixture_dir / "host-migration-dest-current.sav"
    sf.write_fixture(source, sf.STATE_VALID_LEGACY)
    try:
        sf.migrate_fixture(source, dest)
    except (RuntimeError, AssertionError) as exc:
        raise CheckFailure(
            f"host migration check failed for fixture={source} -> {dest}: {exc}"
        ) from exc
    print(f"host migration check passed: {source.name} -> {dest.name} (SAVE_COMPAT_CURRENT)")


def run_scenario(
    rom: Path,
    scenario_name: str,
    fixture_state: str,
    fixture_name: str,
    fingerprint_name: str,
    fixture_dir: Path,
) -> None:
    scenario_path = SCENARIOS_DIR / f"{scenario_name}.json"
    fingerprint_path = FINGERPRINTS_DIR / f"{fingerprint_name}.json"
    if not scenario_path.exists():
        raise CheckFailure(f"missing committed scenario: {scenario_path}")
    if not fingerprint_path.exists():
        raise CheckFailure(f"missing committed fingerprint: {fingerprint_path}")

    fixture_path_ = fixture_dir / f"{fixture_name}.sav"
    if not fixture_path_.exists():
        sf.write_fixture(fixture_path_, fixture_state)

    cmd = [
        sys.executable, str(GBA_PLAYTEST), "verify",
        "--rom", str(rom),
        "--scenario", str(scenario_path),
        "--sram-image", str(fixture_path_),
        "--expected", str(fingerprint_path),
        "--policy", "behavior",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise CheckFailure(
            f"runtime scenario '{scenario_name}' failed "
            f"(fixture={fixture_name}, state={fixture_state}, "
            f"fingerprint={fingerprint_path.name}):\n"
            f"  stdout: {result.stdout.strip()}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    print(f"runtime scenario passed: {scenario_name} (fixture={fixture_name}, "
          f"state={fixture_state}) vs {fingerprint_path.name}")


def run_migrated_load_check(rom: Path, suffix: str, fixture_dir: Path) -> None:
    """Requirement 7's "host-migrated v0->v1 image": generated by the
    existing CLI, injected, classified CURRENT, and loaded through the
    normal save path (no dialog) -- reuses the savecompat-current
    scenario/fingerprint pair, since a CURRENT classification must reach
    the exact same normal-save-menu framebuffer regardless of how the
    image became CURRENT (fresh-erase vs host-migrated)."""
    source = fixture_dir / "host-migration-source-vanilla.sav"
    dest = fixture_dir / "host-migration-dest-current.sav"
    if not dest.exists():
        sf.write_fixture(source, sf.STATE_VALID_LEGACY)
        sf.migrate_fixture(source, dest)

    fingerprint_path = FINGERPRINTS_DIR / f"savecompat-current-migrated-{suffix}.json"
    if not fingerprint_path.exists():
        raise CheckFailure(f"missing committed fingerprint: {fingerprint_path}")

    cmd = [
        sys.executable, str(GBA_PLAYTEST), "verify",
        "--rom", str(rom),
        "--scenario", str(SCENARIOS_DIR / "savecompat-current.json"),
        "--sram-image", str(dest),
        "--expected", str(fingerprint_path),
        "--policy", "behavior",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise CheckFailure(
            f"migrated-v1 load scenario failed (fixture={dest.name}, "
            f"fingerprint={fingerprint_path.name}):\n"
            f"  stdout: {result.stdout.strip()}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    print(f"migrated-v1 load scenario passed vs {fingerprint_path.name}")


def run_suspend_resume_check(rom: Path, fixture_dir: Path) -> None:
    """Issue #2's final slice: deterministic ordinary-UI Suspend ->
    soft-reset -> Resume proof (tools/gba-playtest/scenarios/
    savesuspend-resume-modern-debug.json). Debug-only: this scenario
    replays #11 Slice 1's debug-only "Fast Boot: Chapter 2" title
    hotkey/launcher (see docs/debugtools.md), which does not exist in
    release builds, so this check is only ever called for
    MODERN_CONFIG=debug (see main()) and release's existing
    all-state/migrated coverage above is completely unaffected."""
    scenario_name = "savesuspend-resume-modern-debug"
    fingerprint_name = "savesuspend-resume-modern-debug"
    scenario_path = SCENARIOS_DIR / f"{scenario_name}.json"
    fingerprint_path = FINGERPRINTS_DIR / f"{fingerprint_name}.json"
    if not scenario_path.exists():
        raise CheckFailure(f"missing committed scenario: {scenario_path}")
    if not fingerprint_path.exists():
        raise CheckFailure(f"missing committed fingerprint: {fingerprint_path}")

    fixture_path_ = fixture_dir / "suspend-resume-current.sav"
    sf.write_fixture(fixture_path_, sf.STATE_CURRENT)

    cmd = [
        sys.executable, str(GBA_PLAYTEST), "verify",
        "--rom", str(rom),
        "--scenario", str(scenario_path),
        "--sram-image", str(fixture_path_),
        "--expected", str(fingerprint_path),
        "--policy", "behavior",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise CheckFailure(
            f"runtime scenario 'savesuspend-resume' failed "
            f"(fixture=suspend-resume-current, state=CURRENT, "
            f"checkpoints=suspend-confirmed/post-soft-reset-boot/"
            f"resumed-chapter2, fingerprint={fingerprint_path.name}):\n"
            f"  stdout: {result.stdout.strip()}\n"
            f"  stderr: {result.stderr.strip()}"
        )
    print(f"runtime scenario passed: {scenario_name} "
          f"(fixture=suspend-resume-current, state=CURRENT) vs {fingerprint_path.name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", required=True, type=Path,
                         help="path to the built modern AAPCS ROM")
    parser.add_argument("--config", required=True, choices=("debug", "release"),
                         help="MODERN_CONFIG this ROM was built with -- selects "
                              "the matching committed fingerprint suffix")
    parser.add_argument("--fixture-dir", required=True, type=Path,
                         help="ignored build/temp directory to generate fixtures into")
    args = parser.parse_args(argv)

    if not args.rom.exists():
        print(f"error: ROM not found: {args.rom}", file=sys.stderr)
        return 1

    args.fixture_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"modern-{args.config}"

    try:
        run_host_migration_checks(args.fixture_dir)

        # SAVE_COMPAT_CURRENT: blank/EMPTY SRAM auto-repairs to CURRENT
        # before the gate is ever reached; no dialog appears.
        run_scenario(args.rom, "savecompat-current", sf.STATE_EMPTY, "empty",
                     f"savecompat-current-{suffix}", args.fixture_dir)

        # Every non-CURRENT dialog state: dialog appears, correct
        # state-specific diagnostic is shown, Back leaves SRAM unchanged.
        for name, state in _DIALOG_BACK_STATES.items():
            run_scenario(
                args.rom, "savecompat-dialog-back", state, name,
                f"savecompat-dialog-back-{name}-{suffix}", args.fixture_dir,
            )

        # Confirmed full erase: HEADER_CORRUPT input -> Erase All Save
        # Data -> Yes -> whole-chip wipe + fresh CURRENT initialization.
        run_scenario(args.rom, "savecompat-erase", sf.STATE_HEADER_CORRUPT,
                     "header-corrupt", f"savecompat-erase-{suffix}", args.fixture_dir)

        # Host-migrated v0->v1 image classifies CURRENT and loads through
        # the normal save path (requirement 7's last bullet).
        run_migrated_load_check(args.rom, suffix, args.fixture_dir)

        # Issue #2's final slice: deterministic ordinary-UI Suspend ->
        # soft-reset -> Resume proof. Debug-only, since the launcher it
        # depends on does not exist in release builds.
        if args.config == "debug":
            run_suspend_resume_check(args.rom, args.fixture_dir)
    except CheckFailure as exc:
        print(f"expansion-modern-savefmt-check FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"expansion-modern-savefmt-check passed (config={args.config}): "
          f"all 8 SaveCompatState values, Back-preservation, confirmed erase, "
          f"host-migrated v1 load, "
          + ("and Suspend/soft-reset/Resume, " if args.config == "debug" else "")
          + f"verified against {args.rom}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
