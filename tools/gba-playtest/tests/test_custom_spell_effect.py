"""Issue #77 configuration, dispatch, resource, and ARM-object checks."""

import ctypes
import errno
import json
import os
import re
import select
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

import host_mode

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.workflow_pilot import raw_diff_check as process_ownership

SOURCE = ROOT / "src" / "custom_spell_effect.c"
DATA_SOURCE = ROOT / "src" / "data" / "custom_spell_effect_data.c"
DISPATCH = ROOT / "src" / "banim-efxmagic.c"
HEADER = ROOT / "include" / "custom_spell_effect.h"
EFXMAGIC_HEADER = ROOT / "include" / "efxmagic.h"
TEST_HEADER = ROOT / "include" / "custom_spell_effect_test.h"
TEST_SOURCE = ROOT / "src" / "custom_spell_effect_test.c"
MAIN_SOURCE = ROOT / "src" / "main.c"
LAYOUT_DRIVER = (
    ROOT / "tools" / "gba-playtest" / "tests" / "c"
    / "custom_spell_effect_layout_driver.c"
)
RUNTIME_RUNNER = ROOT / "tools" / "gba-playtest" / "run_custom_spell_effect_checks.py"
REFERENCE_MANIFEST = ROOT / "assets" / "manifests" / "custom-spell-reference.json"
ARM_CC = shutil.which("arm-none-eabi-gcc")
ARM_NM = shutil.which("arm-none-eabi-nm")
HOST_CC = shutil.which("gcc") or shutil.which("cc")
HOST_DRIVER = (
    ROOT / "tools" / "gba-playtest" / "tests" / "c" / "custom_spell_effect_host_driver.c"
)


def run(command):
    return subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)


def generated_asset_dir(manifest, enabled, build_root="build/expansion-modern"):
    completed = run(
        [
            "make",
            "--no-print-directory",
            "print-ASSET_OUTPUT_DIR",
            "MODERN_BUILD_ROOT={}".format(build_root),
            "ASSET_MANIFEST={}".format(manifest),
            "EXPANSION_CUSTOM_SPELL_EFFECTS={}".format(enabled),
        ]
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stdout + completed.stderr)
    for line in reversed(completed.stdout.splitlines()):
        if line.startswith("build/"):
            return ROOT / line
    raise AssertionError(completed.stdout + completed.stderr)


def generate_reference_assets():
    completed = run(
        [
            "make",
            "--no-print-directory",
            "assets-generate",
            "ASSET_MANIFEST={}".format(REFERENCE_MANIFEST),
            "EXPANSION_CUSTOM_SPELL_EFFECTS=1",
        ]
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stdout + completed.stderr)
    return generated_asset_dir(REFERENCE_MANIFEST, 1)


class CustomSpellConfigTests(unittest.TestCase):
    def test_tester_facing_runtime_operations_match_one_selected_tsa(self):
        documentation = (ROOT / "docs" / "custom_spell_effects.md").read_text(
            encoding="utf-8"
        )
        registry = json.loads(
            (ROOT / "docs" / "test-cases" / "registry.json").read_text(
                encoding="utf-8"
            )
        )
        case = next(
            entry
            for entry in registry["cases"]
            if entry["id"] == "TC-CUSTOM-SPELL-061-001"
        )
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("five per-frame runtime\n  operations", documentation)
        self.assertIn(
            "OBJ graphics/palette, BG graphics/palette, and one\n"
            "  distance-selected TSA",
            documentation,
        )
        self.assertIn("five per-frame runtime operations exactly once", case["expected_result"])
        self.assertIn("one distance-selected TSA", case["expected_result"])
        for operation in (
            "SpellFx_RegisterBgPal",
            "SpellFx_RegisterBgGfx",
            "SpellFx_RegisterObjPal",
            "SpellFx_RegisterObjGfx",
            "SpellFx_WriteBgMap",
        ):
            self.assertEqual(source.count(operation), 1)

    def test_identity_tracks_enabled_state_and_preserves_save_epoch(self):
        import sys

        sys.path.insert(0, str(ROOT / "scripts" / "modernize"))
        import expansion_config as ec

        disabled = ec.load_identity(
            ROOT / "config.mk", "debug", "aapcs", "16M",
            repo_root=ROOT, custom_spell_effects=0,
        )
        enabled = ec.load_identity(
            ROOT / "config.mk", "debug", "aapcs", "16M",
            repo_root=ROOT, custom_spell_effects=1,
            asset_manifest=REFERENCE_MANIFEST,
        )
        contract = ec.resolve_custom_spell_contract(ROOT, REFERENCE_MANIFEST, 1)
        self.assertEqual(disabled.custom_spell_effects, 0)
        self.assertEqual(enabled.custom_spell_effects, 1)
        self.assertNotEqual(disabled.config_fingerprint, enabled.config_fingerprint)
        self.assertEqual(disabled.save_compat_epoch, enabled.save_compat_epoch)
        self.assertEqual(disabled.custom_spell_effect_runtime_abi, 0)
        self.assertEqual(enabled.custom_spell_effect_runtime_abi, 1)
        self.assertEqual(
            disabled.custom_spell_effect_inventory_digest,
            ec.CUSTOM_SPELL_EFFECT_EMPTY_DIGEST,
        )
        self.assertEqual(
            disabled.custom_spell_effect_resource_budget_digest,
            ec.CUSTOM_SPELL_EFFECT_EMPTY_DIGEST,
        )
        self.assertEqual(
            enabled.custom_spell_effect_inventory_digest,
            contract["inventory_digest"],
        )
        self.assertEqual(
            enabled.custom_spell_effect_resource_budget_digest,
            contract["resource_digest"],
        )
        self.assertNotIn(
            "custom_spell_effect_contract", disabled.fingerprint_fields()
        )
        self.assertNotIn(
            "custom_spell_effects", disabled.fingerprint_fields()["features"]
        )
        disabled_fields = disabled.fingerprint_fields()
        enabled_fields = enabled.fingerprint_fields()
        disabled_features = disabled_fields["features"]
        enabled_features = enabled_fields["features"]
        self.assertEqual(
            {key: value for key, value in enabled_fields.items()
             if key not in ("features", "custom_spell_effect_contract")},
            {key: value for key, value in disabled_fields.items() if key != "features"},
        )
        self.assertEqual(
            {key: value for key, value in enabled_features.items()
             if key != "custom_spell_effects"},
            disabled_features,
        )
        self.assertEqual(set(enabled_fields) - set(disabled_fields), {"custom_spell_effect_contract"})
        self.assertEqual(set(disabled_fields) - set(enabled_fields), set())
        self.assertEqual(
            enabled_features["custom_spell_effects"],
            1,
        )
        self.assertEqual(
            enabled_fields["custom_spell_effect_contract"],
            {
                "runtime_abi": 1,
                "inventory_digest": contract["inventory_digest"],
                "resource_budget_digest": contract["resource_digest"],
            },
        )

        with self.assertRaises(ec.ConfigError):
            ec.load_identity(
                ROOT / "config.mk", "debug", "aapcs", "16M",
                repo_root=ROOT, custom_spell_effects=2,
            )
        with self.assertRaises(ec.ConfigError):
            ec.load_identity(
                ROOT / "config.mk", "debug", "aapcs", "16M",
                repo_root=ROOT, custom_spell_effects=1,
                asset_manifest=ROOT / "assets" / "manifest.json",
            )
        with self.assertRaises(ec.ConfigError):
            ec.load_identity(
                ROOT / "config.mk", "debug", "aapcs", "16M",
                repo_root=ROOT, custom_spell_effects=0,
                asset_manifest=REFERENCE_MANIFEST,
            )

    def test_default_off_surface_is_consistent(self):
        self.assertIn("EXPANSION_CUSTOM_SPELL_EFFECTS ?= 0", (ROOT / "config.mk").read_text())
        self.assertIn(
            "#define FE8_EXPANSION_CUSTOM_SPELL_EFFECTS 0",
            (ROOT / "include" / "expansion_config.h").read_text(),
        )
        self.assertIn(
            "--enable-custom-spell-effects", (ROOT / "configure.ac").read_text()
        )
        self.assertIn(
            "-DFE8_EXPANSION_CUSTOM_SPELL_EFFECTS=$(EXPANSION_CUSTOM_SPELL_EFFECTS)",
            (ROOT / "modern.mk").read_text(),
        )
        self.assertIn(
            "'custom_spell_effects=$(EXPANSION_CUSTOM_SPELL_EFFECTS)'",
            (ROOT / "modern.mk").read_text(),
        )
        self.assertIn(
            '--custom-spell-effects "$(EXPANSION_CUSTOM_SPELL_EFFECTS)"',
            (ROOT / "modern.mk").read_text(),
        )
        self.assertIn(
            '--asset-manifest "$(ASSET_MANIFEST)"',
            (ROOT / "modern.mk").read_text(),
        )


class CustomSpellContractTests(unittest.TestCase):
    def test_closed_index_dispatch_preserves_vanilla_lut_path(self):
        dispatch = DISPATCH.read_text(encoding="utf-8")
        custom_source = SOURCE.read_text(encoding="utf-8")
        custom_header = HEADER.read_text(encoding="utf-8")
        efxmagic_header = EFXMAGIC_HEADER.read_text(encoding="utf-8")
        self.assertIn("index >= CUSTOM_SPELL_EFFECT_BASE", dispatch)
        self.assertIn("index <= CUSTOM_SPELL_EFFECT_LAST", dispatch)
        self.assertIn("CustomSpellEffect_Lookup((u8)index)", dispatch)
        self.assertIn(
            "gEkrSpellAnimLutCount = ARRAY_COUNT(gEkrSpellAnimLut);",
            dispatch,
        )
        self.assertIn(
            "#if BUGFIX\n"
            "extern const u32 gEkrSpellAnimLutCount;\n"
            "#endif",
            efxmagic_header,
        )
        self.assertNotIn("CUSTOM_SPELL_EFFECT_VANILLA_ANIM_COUNT", custom_header)
        self.assertEqual(custom_source.count("fallback >= gEkrSpellAnimLutCount"), 1)
        self.assertEqual(
            custom_source.count(
                "effect->fallbackAnimationId >= gEkrSpellAnimLutCount"
            ),
            1,
        )
        self.assertIn(
            "index < 0 || (u32)index >= gEkrSpellAnimLutCount",
            dispatch,
        )
        self.assertIn("gEkrSpellAnimLut[index](anim);", dispatch)
        self.assertLess(
            dispatch.index("index < 0 || (u32)index >= gEkrSpellAnimLutCount"),
            dispatch.index("gEkrSpellAnimLut[index](anim);"),
        )
        self.assertNotIn("SpellAssoc", dispatch)

    def test_runtime_probe_is_isolated_and_test_only(self):
        header = TEST_HEADER.read_text(encoding="utf-8")
        source = TEST_SOURCE.read_text(encoding="utf-8")
        main = (ROOT / "src" / "main.c").read_text(encoding="utf-8")
        runner = RUNTIME_RUNNER.read_text(encoding="utf-8")
        self.assertIn("#define FE8_EXPANSION_CUSTOM_SPELL_TEST 0", header)
        self.assertIn("#if FE8_EXPANSION_CUSTOM_SPELL_TEST", source)
        self.assertIn("CustomSpellEffectTest_PrepareAnims", source)
        self.assertIn("CustomSpellEffectTest_RecordSetupFailure", source)
        self.assertIn("if (!CustomSpellEffectTest_PrepareAnims())", source)
        self.assertIn("if (proc == NULL)", source)
        self.assertIn("allocationFailureCleanups", runner)
        self.assertIn("StartSpellAnimation(gAnims[0]);", source)
        self.assertIn("SetMainUpdateRoutine(OnMain);", main)
        self.assertIn("CustomSpellEffectTest_Start();", main)
        self.assertLess(
            main.index('#include "custom_spell_effect_test.h"'),
            main.index("#ifdef MODERN"),
        )
        self.assertNotIn("CHAPTER_", source)
        self.assertNotIn("CallEvent", source)
        self.assertIn('"StartGame and all chapter scripts, invokes the public "', runner)
        self.assertIn("resolve_elf_symbol", runner)


class CustomSpellLifecycleTests(unittest.TestCase):
    def test_enabled_lifecycle_fallback_and_forced_cleanup(self):
        if HOST_CC is None:
            self.skipTest("no host C compiler")

        generated_assets = generate_reference_assets()
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as tmp:
            executable = Path(tmp) / "custom-spell-lifecycle"
            completed = run(
                [
                    HOST_CC,
                    "-std=gnu89",
                    "-Werror=declaration-after-statement",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    "-Iinclude",
                    "-I.",
                    "-I{}".format(generated_assets),
                    "-I{}".format(generated_assets / "custom_spell"),
                    "-DMODERN=1",
                    "-DBUGFIX=1",
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-DFE8_EXPANSION_CUSTOM_SPELL_EFFECTS=1",
                    str(HOST_DRIVER),
                    "-o",
                    str(executable),
                ]
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            completed = run([str(executable)])
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("CUSTOM_SPELL_HOST_TEST: PASS", completed.stdout)

    def test_typed_descriptor_and_resource_limits_are_public(self):
        header = HEADER.read_text(encoding="utf-8")
        source = SOURCE.read_text(encoding="utf-8")
        for text in (
            "#define CUSTOM_SPELL_EFFECT_BASE 0x80",
            "#define CUSTOM_SPELL_EFFECT_COUNT 16",
            "#define CUSTOM_SPELL_EFFECT_RUNTIME_ABI 1",
            "#define CUSTOM_SPELL_EFFECT_MAX_OBJ_BYTES 0x1000",
            "#define CUSTOM_SPELL_EFFECT_MAX_BG_BYTES 0x2000",
            "#define CUSTOM_SPELL_EFFECT_BG_TSA_BYTES 1200",
            "#define CUSTOM_SPELL_EFFECT_OBJ_PALETTE_LINE 2",
            "#define CUSTOM_SPELL_EFFECT_BG_PALETTE_LINE 1",
            "#define CUSTOM_SPELL_EFFECT_MAX_OAM_ENTRIES 16",
            "#define CUSTOM_SPELL_EFFECT_MAX_SOUND_EVENTS 8",
            "struct CustomSpellEffectFrame",
            "struct CustomSpellEffectResources",
            "struct CustomSpellEffectFrameAssets",
            "struct CustomSpellEffectOamScripts",
            "struct CustomSpellEffect",
        ):
            self.assertIn(text, header)
        self.assertIn("CustomSpellEffect_Validate", source)
        self.assertIn("BanimPresentationPolicy_UsesBackgrounds", source)
        self.assertIn("gEfxBgSemaphore != 0", source)
        self.assertIn("PROC_SET_END_CB(CustomSpellEffect_OnEnd)", source)
        self.assertIn("RegisterEfxSpellCastEnd();", source)
        self.assertIn("StartBattleAnimHitEffectsDefault", source)
        self.assertIn("SpellFx_RegisterBgGfx", source)
        self.assertIn("SpellFx_RegisterObjGfx", source)
        self.assertIn("SpellFx_WriteBgMap", source)
        self.assertIn("EfxCreateFrontAnim", source)
        self.assertIn("CUSTOM_SPELL_EFFECT_OBJ_PALETTE_LINE", source)
        self.assertIn("CUSTOM_SPELL_EFFECT_BG_PALETTE_LINE", source)
        self.assertIn("STRUCT_PAD(0x0A, 0x0C)", header)

    def test_descriptor_layout_and_fallback_are_c89_safe(self):
        header = HEADER.read_text(encoding="utf-8")
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            "/* 04 */ const struct CustomSpellEffectFrameAssets *assets;",
            header,
        )
        self.assertIn("/* 02 */ u8 soundStart;", header)
        self.assertIn("/* 03 */ u8 soundCount;", header)
        self.assertIn(
            "/* 18 */ struct CustomSpellEffectOamScripts oamScripts;",
            header,
        )
        self.assertIn("/* 28 */ const u16 *soundIds;", header)
        self.assertIn("/* 2C */ u8 animationId;", header)
        self.assertIn("if (anim == NULL)", source)
        self.assertIn("if (target != NULL)", source)
        self.assertIn("if (gEfxBgSemaphore != 0)", source)
        self.assertIn("Proc_Find(sProcScrCustomSpellEffect)", source)
        self.assertIn(
            "gEkrSpellAnimLut[effect->fallbackAnimationId] == NULL",
            source,
        )
        self.assertIn(
            "if (fallback >= gEkrSpellAnimLutCount\n"
            "        || gEkrSpellAnimLut[fallback] == NULL)\n"
            "        return;",
            source,
        )
        self.assertIn("frameData->flags != 0", source)
        self.assertIn("frameData->soundStart != validatedSoundEvents", source)
        self.assertIn("effect->soundIds[soundIndex] == 0", source)
        self.assertIn("proc->finalDisplayLatch = 1;", source)
        stripped = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
        self.assertNotIn("//", stripped)


class CustomSpellArmTests(unittest.TestCase):
    def test_nonmodern_test_enable_fails_fast_and_default_compiles_away(self):
        if ARM_CC is None or ARM_NM is None:
            self.skipTest("arm-none-eabi compiler/binutils unavailable")

        common = [
            ARM_CC,
            "-mcpu=arm7tdmi",
            "-mthumb",
            "-mthumb-interwork",
            "-std=gnu89",
            "-ffreestanding",
            "-fno-builtin",
            "-fno-common",
            "-Iinclude",
            "-I.",
            "-DFE8_ARCHIVAL_BUILD=1",
            "-Werror=implicit-function-declaration",
            "-Werror=implicit-int",
        ]
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as tmp:
            work = Path(tmp)
            rejected = work / "main-nonmodern-test-enabled.o"
            completed = run(
                [
                    *common,
                    "-DFE8_EXPANSION_CUSTOM_SPELL_TEST=1",
                    "-c",
                    str(MAIN_SOURCE),
                    "-o",
                    str(rejected),
                ]
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn(
                "FE8_EXPANSION_CUSTOM_SPELL_TEST is available only in the modern test lane",
                completed.stdout + completed.stderr,
            )

            default = work / "main-nonmodern-default.o"
            completed = run(
                [*common, "-c", str(MAIN_SOURCE), "-o", str(default)]
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            default_symbols = run([ARM_NM, "-S", str(default)]).stdout
            self.assertNotIn("CustomSpellEffectTest", default_symbols)

    def test_enabled_and_disabled_arm_objects_obey_linkage_boundary(self):
        if ARM_CC is None or ARM_NM is None:
            self.skipTest("arm-none-eabi compiler/binutils unavailable")

        generated_assets = generate_reference_assets()
        common = [
            ARM_CC,
            "-mcpu=arm7tdmi",
            "-mthumb",
            "-mthumb-interwork",
            "-mabi=aapcs",
            "-std=gnu89",
            "-ffreestanding",
            "-fno-builtin",
            "-fno-common",
            "-Iinclude",
            "-I.",
            "-I{}".format(generated_assets),
            "-I{}".format(generated_assets / "custom_spell"),
            "-DMODERN=1",
            "-DBUGFIX=1",
            "-DFE8_EXPANSION_MODERN_BUILD=1",
            "-Werror=declaration-after-statement",
            "-Werror=implicit-function-declaration",
            "-Werror=implicit-int",
        ]
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as tmp:
            work = Path(tmp)
            enabled = work / "custom-enabled.o"
            disabled = work / "custom-disabled.o"
            data_enabled = work / "custom-data-enabled.o"
            data_disabled = work / "custom-data-disabled.o"
            legacy_dispatch = work / "banim-efxmagic-legacy.o"
            layout = work / "custom-spell-layout.o"
            for value, output in ((1, enabled), (0, disabled)):
                completed = run(
                    [
                        *common,
                        f"-DFE8_EXPANSION_CUSTOM_SPELL_EFFECTS={value}",
                        "-c",
                        str(SOURCE),
                        "-o",
                        str(output),
                    ]
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

            enabled_symbols = run([ARM_NM, "-S", str(enabled)]).stdout
            disabled_symbols = run([ARM_NM, "-S", str(disabled)]).stdout
            self.assertIn("CustomSpellEffect_Start", enabled_symbols)
            self.assertIn("CustomSpellEffect_Lookup", enabled_symbols)
            self.assertIn("gCustomSpellEffectDebugProbe", enabled_symbols)
            self.assertNotIn("CustomSpellEffect_", disabled_symbols)
            self.assertNotIn("sCustomSpellEffectActive", disabled_symbols)

            for value, output in ((1, data_enabled), (0, data_disabled)):
                completed = run(
                    [
                        *common,
                        f"-DFE8_EXPANSION_CUSTOM_SPELL_EFFECTS={value}",
                        "-c",
                        str(DATA_SOURCE),
                        "-o",
                        str(output),
                    ]
                )
                self.assertEqual(
                    completed.returncode, 0, completed.stdout + completed.stderr
                )
            self.assertIn(
                "gGeneratedCustomSpellEffects",
                run([ARM_NM, "-S", str(data_enabled)]).stdout,
            )
            public_symbol = work / "public-custom-spell-symbol.c"
            public_symbol.write_text(
                '#include "custom_spell_effect.h"\n'
                'u8 gPublicCustomSpellSymbol = CUSTOM_SPELL_REFERENCE;\n',
                encoding="ascii",
            )
            public_object = work / "public-custom-spell-symbol.o"
            completed = run(
                [
                    *common,
                    "-DFE8_EXPANSION_CUSTOM_SPELL_EFFECTS=1",
                    "-c",
                    str(public_symbol),
                    "-o",
                    str(public_object),
                ]
            )
            self.assertEqual(
                completed.returncode, 0, completed.stdout + completed.stderr
            )
            self.assertNotIn(
                "CustomSpell",
                run([ARM_NM, "-S", str(data_disabled)]).stdout,
            )

            legacy_common = [flag for flag in common if flag != "-DBUGFIX=1"]
            completed = run(
                [
                    *legacy_common,
                    "-DFE8_EXPANSION_CUSTOM_SPELL_EFFECTS=0",
                    "-c",
                    str(DISPATCH),
                    "-o",
                    str(legacy_dispatch),
                ]
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            legacy_symbols = run([ARM_NM, "-S", str(legacy_dispatch)]).stdout
            self.assertNotIn("gEkrSpellAnimLutCount", legacy_symbols)

            completed = run(
                [*common, "-c", str(LAYOUT_DRIVER), "-o", str(layout)]
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


@dataclass
class _ProfileBuild:
    output: TextIO | None = None
    process: subprocess.Popen | None = None
    leader_fd: int = -1
    settled: bool = False


# An unverified session must keep its waitable leader/pidfd, not just its number.
_RETAINED_PROFILE_BUILDS = {}


@dataclass
class _ProfileRoot:
    pins: dict[object, tuple[int, os.stat_result | None]] = field(default_factory=dict)
    holder_name: str | None = None
    claimed: bool = False
    published: bool = False


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_profile_pin(owner, key, path, *, dir_fd=None):
    descriptor = os.open(path, _DIRECTORY_FLAGS, dir_fd=dir_fd)
    owner.pins[key] = (descriptor, None)
    owner.pins[key] = (descriptor, os.fstat(descriptor))
    return descriptor


def _close_profile_pin(owner, key):
    descriptor, expected = owner.pins[key]
    try:
        actual = os.fstat(descriptor)
    except OSError as error:
        if error.errno == errno.EBADF:
            del owner.pins[key]
        raise
    if expected is not None and not os.path.samestat(expected, actual):
        del owner.pins[key]
        raise process_ownership.ProcessCleanupError("profile pin identity changed; refusing to close replacement")
    try:
        os.close(descriptor)
    except BaseException:
        # close can fail either before or after releasing the descriptor.
        # Retain only a still-live pin to the identity we actually acquired.
        try:
            following = os.fstat(descriptor)
        except OSError as error:
            if error.errno == errno.EBADF:
                del owner.pins[key]
        else:
            if not os.path.samestat(actual, following):
                del owner.pins[key]
        raise
    del owner.pins[key]


def _rename_profile_directory(source_fd, source, target_fd, target):
    rename = ctypes.CDLL(None, use_errno=True).renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source_fd, os.fsencode(source), target_fd, os.fsencode(target), 1):
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number), source)


def _profile_namespace_matches(owner, test_root, *, claimed):
    root_fd = owner.pins["root"][0]
    parent_fd = owner.pins["parent"][0]
    if not os.path.samestat(os.fstat(parent_fd), test_root.parent.stat()):
        raise process_ownership.ProcessCleanupError("profile parent namespace changed")
    if not claimed:
        actual = os.stat(test_root.name, dir_fd=parent_fd, follow_symlinks=False)
    else:
        try:
            os.stat(test_root.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise process_ownership.ProcessCleanupError("profile root namespace was replaced")
        holder_fd = owner.pins["holder"][0]
        if not os.path.samestat(os.fstat(holder_fd), os.stat(
                owner.holder_name, dir_fd=parent_fd, follow_symlinks=False)):
            raise process_ownership.ProcessCleanupError("profile cleanup namespace changed")
        actual = os.stat("root", dir_fd=holder_fd, follow_symlinks=False)
    if not stat.S_ISDIR(actual.st_mode) or not os.path.samestat(os.fstat(root_fd), actual):
        raise process_ownership.ProcessCleanupError("profile root identity changed")


def _clear_profile_directory(owner, descriptor, check_namespace):
    check_namespace()
    with os.scandir(descriptor) as entries:
        entries = list(entries)
    check_namespace()
    for entry in entries:
        check_namespace()
        expected = entry.stat(follow_symlinks=False)
        if stat.S_ISDIR(expected.st_mode):
            key = object()
            child = _open_profile_pin(owner, key, entry.name, dir_fd=descriptor)
            if not os.path.samestat(expected, os.fstat(child)):
                raise process_ownership.ProcessCleanupError("profile child directory identity changed")
            _clear_profile_directory(owner, child, check_namespace)
            check_namespace()
            if not os.path.samestat(os.fstat(child), os.stat(
                    entry.name, dir_fd=descriptor, follow_symlinks=False)):
                raise process_ownership.ProcessCleanupError("profile child namespace changed")
            os.rmdir(entry.name, dir_fd=descriptor)
            if os.fstat(child).st_nlink:
                raise process_ownership.ProcessCleanupError("profile child removal did not unlink its pin")
            _close_profile_pin(owner, key)
        else:
            os.unlink(entry.name, dir_fd=descriptor)
    check_namespace()


def _initialize_profile_root(test_root, owner, checkpoint):
    parent_fd = _open_profile_pin(owner, "parent", test_root.parent)
    checkpoint()
    holder_name = f".{test_root.name}.cleanup-{secrets.token_hex(12)}"
    os.mkdir(holder_name, 0o700, dir_fd=parent_fd)
    owner.holder_name = holder_name
    holder_fd = _open_profile_pin(owner, "holder", holder_name, dir_fd=parent_fd)
    checkpoint()
    # The root is still private when its identity is acquired. Publication
    # never opens or adopts whichever object occupies the public name.
    os.mkdir("root", 0o700, dir_fd=holder_fd)
    _open_profile_pin(owner, "root", "root", dir_fd=holder_fd)
    checkpoint()
    _rename_profile_directory(holder_fd, "root", parent_fd, test_root.name)
    owner.published = True
    _profile_namespace_matches(owner, test_root, claimed=False)
    checkpoint()


def _remove_profile_root(test_root, owner):
    parent_fd = owner.pins["parent"][0]
    holder_fd = owner.pins["holder"][0]
    holder_name = owner.holder_name
    _profile_namespace_matches(owner, test_root, claimed=False)
    # Claim into an exclusive private namespace, then verify the moved entry
    # against the existing pin. A replaced public name is never traversed.
    _rename_profile_directory(parent_fd, test_root.name, holder_fd, "root")
    actual = os.stat("root", dir_fd=holder_fd, follow_symlinks=False)
    if not os.path.samestat(os.fstat(owner.pins["root"][0]), actual):
        try:
            _rename_profile_directory(holder_fd, "root", parent_fd, test_root.name)
        except OSError as error:
            raise process_ownership.ProcessCleanupError(
                f"profile claim changed; replacement retained in {holder_name}/root") from error
        raise process_ownership.ProcessCleanupError("profile claim changed; replacement restored without deletion")
    owner.claimed = True
    check_namespace = lambda: _profile_namespace_matches(owner, test_root, claimed=True)
    _clear_profile_directory(owner, owner.pins["root"][0], check_namespace)
    check_namespace()
    os.rmdir("root", dir_fd=holder_fd)
    if os.fstat(owner.pins["root"][0]).st_nlink:
        raise process_ownership.ProcessCleanupError("profile removal did not unlink the pinned root")
    os.rmdir(holder_name, dir_fd=parent_fd)
    if os.fstat(holder_fd).st_nlink:
        raise process_ownership.ProcessCleanupError("profile cleanup holder was replaced")
    try:
        os.stat(test_root.name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise process_ownership.ProcessCleanupError("profile root namespace changed during removal")


def _finish_profile_build(build, deadline):
    if build.process is None or build.settled:
        return
    if build.process.returncode is not None:
        raise process_ownership.ProcessCleanupError("profile leader was reaped before session quiescence")
    if build.leader_fd < 0:
        os.waitid(os.P_PID, build.process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        try:
            os.killpg(build.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        raise process_ownership.ProcessCleanupError(
            "profile pidfd unavailable; waitable leader identity retained")
    process_ownership._terminate_owned(build.process, build.leader_fd, True, deadline)
    process_ownership._reap_leader(build.process)
    build.settled = True


def _close_profile_builds(builds, deadline):
    errors = []
    for build in builds:
        try:
            _finish_profile_build(build, deadline)
        except BaseException as error:
            errors.append(error)
    for build in builds:
        if build.output is not None:
            try:
                build.output.close()
            except BaseException as error:
                errors.append(error)
        if build.settled and build.leader_fd >= 0:
            descriptor, build.leader_fd = build.leader_fd, -1
            try:
                os.close(descriptor)
            except BaseException as error:
                errors.append(error)
    return errors


@contextmanager
def profile_builds(commands, test_root, *, timeout=600):
    with process_ownership._interruptible() as checkpoint:
        if test_root in _RETAINED_PROFILE_BUILDS:
            raise FileExistsError(errno.EEXIST, "profile ownership is still retained", test_root)
        test_root.parent.mkdir(parents=True, exist_ok=True)
        owner = _ProfileRoot()
        builds = []
        cleanup_errors = []
        failure = None
        try:
            _initialize_profile_root(test_root, owner, checkpoint)
            with process_ownership._child_reaper():
                try:
                    deadline = time.monotonic() + timeout

                    def remaining():
                        checkpoint()
                        value = deadline - time.monotonic()
                        if value <= 0:
                            raise subprocess.TimeoutExpired(commands, timeout)
                        return value

                    poller = select.poll()
                    pending = {}
                    for index, command in enumerate(commands):
                        remaining()
                        _profile_namespace_matches(owner, test_root, claimed=False)
                        build = _ProfileBuild()
                        builds.append(build)
                        # Regular files cannot impose a pipe-capacity dependency on
                        # the other build. They retain unlimited, attributed logs.
                        build.output = open(
                            f"build-{index}.log", "x+",
                            opener=lambda path, flags: os.open(
                                path, flags, dir_fd=owner.pins["root"][0]),
                        )
                        build.process = subprocess.Popen(
                            command, cwd=str(ROOT), stdin=subprocess.DEVNULL,
                            stdout=build.output, stderr=subprocess.STDOUT, start_new_session=True,
                        )
                        build.leader_fd = os.pidfd_open(build.process.pid)
                        checkpoint()
                        pending[build.leader_fd] = build
                        poller.register(build.leader_fd, select.POLLIN)
                    while pending:
                        for descriptor, events in poller.poll(max(1, int(min(remaining(), 0.05) * 1000))):
                            if not events & select.POLLIN:
                                raise process_ownership.ProcessCleanupError("profile leader identity is unavailable")
                            build = pending[descriptor]
                            _finish_profile_build(build, min(deadline, time.monotonic() + 5))
                            poller.unregister(descriptor)
                            del pending[descriptor]
                    results = []
                    for build in builds:
                        build.output.seek(0)
                        chunks = []
                        while True:
                            remaining()
                            chunk = build.output.read(65536)
                            if not chunk:
                                break
                            chunks.append(chunk)
                        results.append(subprocess.CompletedProcess(
                            build.process.args, build.process.returncode, "".join(chunks),
                        ))
                    yield results
                finally:
                    cleanup_errors.extend(_close_profile_builds(builds, time.monotonic() + 5))
        except BaseException as error:
            failure = error
        if not owner.published:
            if owner.pins or owner.holder_name is not None:
                _RETAINED_PROFILE_BUILDS[test_root] = (owner, builds)
                raise process_ownership.ProcessCleanupError(
                    f"profile private initialization/publication failed; retained {owner.holder_name} for {test_root}"
                ) from failure
            if failure is not None:
                raise failure
            raise process_ownership.ProcessCleanupError("profile publication did not complete")
        if "root" not in owner.pins:
            cleanup_errors.append(process_ownership.ProcessCleanupError("profile root identity is unavailable"))
        if cleanup_errors:
            _RETAINED_PROFILE_BUILDS[test_root] = (owner, builds)
            error = process_ownership.ProcessCleanupError(
                f"profile cleanup unconfirmed; retained {test_root}: "
                + "; ".join(str(item) or type(item).__name__ for item in cleanup_errors)
            )
            raise error from (failure if failure is not None else cleanup_errors[0])
        try:
            _remove_profile_root(test_root, owner)
        except BaseException as error:
            _RETAINED_PROFILE_BUILDS[test_root] = (owner, builds)
            raise process_ownership.ProcessCleanupError(
                f"profile root cleanup failed; retained {test_root}: {error}"
            ) from (failure if failure is not None else error)
        for key in reversed(tuple(owner.pins)):
            try:
                _close_profile_pin(owner, key)
            except BaseException as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            _RETAINED_PROFILE_BUILDS[test_root] = (owner, builds)
            raise process_ownership.ProcessCleanupError(
                f"profile pin closure failed; retained live identities for {test_root}: "
                + "; ".join(str(item) for item in cleanup_errors)
            ) from (failure if failure is not None else cleanup_errors[0])
        if failure is not None:
            raise failure


@host_mode.live_artifact_testcase("concurrent custom-spell full-modern object builds")
class CustomSpellProfileAssetIsolationTests(unittest.TestCase):
    def test_concurrent_enabled_disabled_full_modern_compiles_keep_assets_isolated(self):
        if ARM_CC is None:
            self.skipTest("arm-none-eabi compiler unavailable")

        test_root = ROOT / "build" / "test-artifacts" / "custom-spell-profile-assets"
        enabled_root = test_root / "enabled"
        disabled_root = test_root / "disabled"
        commands = (
            [
                "make",
                "--no-print-directory",
                "-j2",
                "expansion-modern-all",
                "MODERN_BUILD_ROOT={}".format(enabled_root.relative_to(ROOT)),
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "EXPANSION_CUSTOM_SPELL_EFFECTS=1",
                "ASSET_MANIFEST={}".format(REFERENCE_MANIFEST.relative_to(ROOT)),
            ],
            [
                "make",
                "--no-print-directory",
                "-j2",
                "expansion-modern-all",
                "MODERN_BUILD_ROOT={}".format(disabled_root.relative_to(ROOT)),
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "EXPANSION_CUSTOM_SPELL_EFFECTS=0",
            ],
        )
        with profile_builds(commands, test_root) as results:
            for result in results:
                self.assertEqual(result.returncode, 0, result.stdout)

            enabled_assets = list((enabled_root / "generated" / "assets").glob(
                "*/asset_manifest.mk"
            ))
            disabled_assets = list((disabled_root / "generated" / "assets").glob(
                "*/asset_manifest.mk"
            ))
            self.assertEqual(len(enabled_assets), 1)
            self.assertEqual(len(disabled_assets), 1)
            self.assertNotEqual(enabled_assets[0].parent, disabled_assets[0].parent)
            self.assertTrue(
                (enabled_assets[0].parent / "custom_spell" / "custom_spell_effect_data.inc").is_file()
            )
            self.assertFalse((disabled_assets[0].parent / "custom_spell").exists())
            for root in (enabled_root, disabled_root):
                self.assertTrue(
                    (root / "debug" / "aapcs" / "src" / "custom_spell_effect.o").is_file()
                )
                self.assertTrue(
                    (root / "debug" / "aapcs" / "src" / "data" / "custom_spell_effect_data.o").is_file()
                )


def run_required_profile_isolation():
    suite = unittest.defaultTestLoader.loadTestsFromName(
        "CustomSpellProfileAssetIsolationTests."
        "test_concurrent_enabled_disabled_full_modern_compiles_keep_assets_isolated",
        module=sys.modules[__name__],
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.testsRun != 1 or result.skipped or result.expectedFailures:
        print("Required profile isolation must execute one test without skips or expected failures.", file=sys.stderr)
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--require-profile-isolation"]:
        sys.exit(run_required_profile_isolation())
    unittest.main()
