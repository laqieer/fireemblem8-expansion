"""Issue #77 configuration, dispatch, resource, and ARM-object checks."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "src" / "custom_spell_effect.c"
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
ARM_CC = shutil.which("arm-none-eabi-gcc")
ARM_NM = shutil.which("arm-none-eabi-nm")
HOST_CC = shutil.which("gcc") or shutil.which("cc")
HOST_DRIVER = (
    ROOT / "tools" / "gba-playtest" / "tests" / "c" / "custom_spell_effect_host_driver.c"
)


def run(command):
    return subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)


class CustomSpellConfigTests(unittest.TestCase):
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
        )
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
            ec.CUSTOM_SPELL_EFFECT_REFERENCE_INVENTORY_DIGEST,
        )
        self.assertEqual(
            enabled.custom_spell_effect_resource_budget_digest,
            ec.CUSTOM_SPELL_EFFECT_RESOURCE_BUDGET_DIGEST,
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
                "inventory_digest": ec.CUSTOM_SPELL_EFFECT_REFERENCE_INVENTORY_DIGEST,
                "resource_budget_digest": ec.CUSTOM_SPELL_EFFECT_RESOURCE_BUDGET_DIGEST,
            },
        )

        with self.assertRaises(ec.ConfigError):
            ec.load_identity(
                ROOT / "config.mk", "debug", "aapcs", "16M",
                repo_root=ROOT, custom_spell_effects=2,
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


if __name__ == "__main__":
    unittest.main()
