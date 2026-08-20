"""Issue #42 host/native/config/seam tests for the typed AoE framework."""

import os
import re
import runpy
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INCLUDES = ["-I", str(ROOT / "include"), "-I", str(ROOT / "include" / "generated")]
CORE = ROOT / "src" / "expansion_aoe.c"
REFERENCE = ROOT / "src" / "expansion_aoe_reference.c"
DRIVER = Path(__file__).resolve().parent / "c" / "expansion_aoe_driver.c"
DISABLED_DRIVER = (
    Path(__file__).resolve().parent / "c" / "expansion_aoe_disabled_driver.c"
)
CC = shutil.which("gcc") or shutil.which("cc")
ARM_CC = shutil.which("arm-none-eabi-gcc")
ARM_NM = shutil.which("arm-none-eabi-nm")
ARM_SIZE = shutil.which("arm-none-eabi-size")


def run(command, cwd=ROOT):
    return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True)


class AoEHostTests(unittest.TestCase):
    def setUp(self):
        if CC is None:
            self.skipTest("no host C compiler")

    def build_and_run(self, work: Path, enabled: bool):
        defines = ["-DFE8_EXPANSION_MODERN_BUILD=1"]
        if enabled:
            defines.append("-DFE8_EXPANSION_AOE_REFERENCE=1")
        sources = [CORE, REFERENCE, DRIVER] if enabled else [REFERENCE, DISABLED_DRIVER]
        objects = []
        for source in sources:
            obj = work / (source.stem + ".o")
            completed = run(
                [
                    CC,
                    "-std=gnu89",
                    "-Werror=declaration-after-statement",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    *INCLUDES,
                    *defines,
                    "-c",
                    str(source),
                    "-o",
                    str(obj),
                ]
            )
            self.assertEqual(
                completed.returncode,
                0,
                f"compile failed for {source.name}:\n"
                + completed.stdout
                + completed.stderr,
            )
            objects.append(obj)
        exe = work / ("aoe-enabled" if enabled else "aoe-disabled")
        completed = run([CC, *(str(obj) for obj in objects), "-o", str(exe)])
        self.assertEqual(
            completed.returncode,
            0,
            "link failed:\n" + completed.stdout + completed.stderr,
        )
        return run([str(exe)])

    def test_enabled_target_effect_route_and_reference_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = self.build_and_run(Path(tmp), enabled=True)
        self.assertEqual(
            completed.returncode,
            0,
            "enabled AoE host driver failed:\n" + completed.stdout + completed.stderr,
        )
        self.assertIn("AOE_HOST_TEST: PASS", completed.stdout)

    def test_disabled_reference_is_inert(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = self.build_and_run(Path(tmp), enabled=False)
        self.assertEqual(
            completed.returncode,
            0,
            "disabled AoE host driver failed:\n" + completed.stdout + completed.stderr,
        )
        self.assertIn("AOE_DISABLED_HOST_TEST: PASS", completed.stdout)


class AoEArmAndBudgetTests(unittest.TestCase):
    def test_arm_aapcs_compile_and_fixed_ewram_budget(self):
        if ARM_CC is None or ARM_NM is None or ARM_SIZE is None:
            self.skipTest("arm-none-eabi compiler/binutils unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            core_obj = work / "core.o"
            ref_obj = work / "reference.o"
            disabled_ref_obj = work / "reference-disabled.o"
            common = [
                ARM_CC,
                "-mcpu=arm7tdmi",
                "-mthumb",
                "-mthumb-interwork",
                "-mabi=aapcs",
                "-std=gnu89",
                "-ffreestanding",
                "-fno-builtin",
                "-Werror=declaration-after-statement",
                "-Werror=implicit-function-declaration",
                "-Werror=implicit-int",
                *INCLUDES,
                "-DFE8_EXPANSION_MODERN_BUILD=1",
                "-DFE8_EXPANSION_AOE_REFERENCE=1",
            ]
            completed = run([*common, "-c", str(CORE), "-o", str(core_obj)])
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            completed = run([*common, "-c", str(REFERENCE), "-o", str(ref_obj)])
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            disabled_common = [
                flag
                for flag in common
                if flag != "-DFE8_EXPANSION_AOE_REFERENCE=1"
            ]
            completed = run(
                [*disabled_common, "-c", str(REFERENCE), "-o", str(disabled_ref_obj)]
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            symbols = run([ARM_NM, "-S", str(core_obj)]).stdout
            self.assertNotIn(
                "sItemRoutes",
                symbols,
                "core must not retain an always-live EWRAM item-route registry",
            )
            dispatch_state = re.search(
                r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
                r"sItemDispatchActive$",
                symbols,
                flags=re.MULTILINE,
            )
            self.assertIsNotNone(dispatch_state, "dispatch reentrancy state missing")
            self.assertLessEqual(int(dispatch_state.group(1), 16), 4)
            ref_symbols = run([ARM_NM, "-S", str(ref_obj)]).stdout
            probe = re.search(
                r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
                r"gExpansionAoEReferenceProbe$",
                ref_symbols,
                flags=re.MULTILINE,
            )
            self.assertIsNotNone(probe, "fixed runtime-probe symbol missing")
            disabled_symbols = run([ARM_NM, "-S", str(disabled_ref_obj)]).stdout
            self.assertNotIn(
                "gExpansionAoEReferenceProbe",
                disabled_symbols,
                "disabled reference must not allocate a zero-filled probe",
            )
            section_output = run(
                [ARM_SIZE, "-A", str(core_obj), str(ref_obj)]
            ).stdout
            ewram_bytes = sum(
                int(value)
                for value in re.findall(
                    r"^ewram_data\s+(\d+)\s+", section_output, re.MULTILINE
                )
            )
            self.assertLessEqual(ewram_bytes, 128)
            text_bytes = sum(
                int(value)
                for value in re.findall(r"^\.text\s+(\d+)\s+", section_output, re.MULTILINE)
            )
            self.assertLessEqual(text_bytes, 8 * 1024)
            self.assertNotIn(
                "ExpansionAoEReference_Heal",
                disabled_symbols,
                "disabled reference must compile out its effect callback",
            )

    def test_gnu89_diagnostics_are_enforced(self):
        if CC is None:
            self.skipTest("no host C compiler")
        fixtures = {
            "declaration-after-statement": """
                int main(void)
                {
                    int value = 0;
                    value++;
                    int late = value;
                    return late;
                }
            """,
            "implicit-function-declaration": """
                int main(void)
                {
                    return MissingFunction();
                }
            """,
            "implicit-int": """
                MissingReturnType(void)
                {
                    return 0;
                }
            """,
        }
        build_root = ROOT / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as tmp:
            work = Path(tmp)
            for name, source in fixtures.items():
                fixture = work / f"{name}.c"
                fixture.write_text(source, encoding="utf-8")
                completed = run(
                    [
                        CC,
                        "-std=gnu89",
                        "-Werror=declaration-after-statement",
                        "-Werror=implicit-function-declaration",
                        "-Werror=implicit-int",
                        "-c",
                        str(fixture),
                        "-o",
                        str(work / f"{name}.o"),
                    ]
                )
                self.assertNotEqual(
                    completed.returncode,
                    0,
                    f"{name} fixture unexpectedly compiled:\n"
                    + completed.stdout
                    + completed.stderr,
                )
                self.assertIn(name, completed.stderr)

    def test_shared_sources_use_c89_comments(self):
        for path in (
            CORE,
            REFERENCE,
            ROOT / "include" / "expansion_aoe.h",
            ROOT / "include" / "expansion_aoe_reference.h",
        ):
            text = path.read_text(encoding="utf-8")
            without_blocks = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
            without_strings = re.sub(r'"(\\.|[^"\\])*"', " ", without_blocks)
            self.assertNotIn("//", without_strings, f"{path.name} has // comments")


class AoEConfigAndSeamTests(unittest.TestCase):
    def test_config_identity_tracks_reference_flag_and_rejects_invalid_value(self):
        import sys

        sys.path.insert(0, str(ROOT / "scripts" / "modernize"))
        import expansion_config as ec

        off = ec.load_identity(
            ROOT / "config.mk",
            "debug",
            "aapcs",
            "16M",
            repo_root=ROOT,
            aoe_reference=0,
        )
        on = ec.load_identity(
            ROOT / "config.mk",
            "debug",
            "aapcs",
            "16M",
            repo_root=ROOT,
            aoe_reference=1,
        )
        self.assertEqual(off.aoe_reference, 0)
        self.assertEqual(on.aoe_reference, 1)
        self.assertNotEqual(off.config_fingerprint, on.config_fingerprint)
        self.assertEqual(off.save_compat_epoch, on.save_compat_epoch)
        with self.assertRaises(ec.ConfigError):
            ec.load_identity(
                ROOT / "config.mk",
                "debug",
                "aapcs",
                "16M",
                repo_root=ROOT,
                aoe_reference=2,
            )

    def test_item_action_and_ai_pipelines_use_one_dispatch_api(self):
        expected = {
            "src/bmitemuse.c": 2,
            "src/bmusemind.c": 1,
            "src/cp_staff.c": 1,
            "src/cpextra_80407F0.c": 1,
        }
        for relative, count in expected.items():
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertEqual(
                text.count("ExpansionAoE_DispatchItem"),
                count,
                f"{relative} must use only the shared AoE item dispatch seam",
            )
            self.assertIn("FE8_EXPANSION_MODERN_BUILD", text)

    def test_item_routes_are_rom_authored_through_one_weak_provider(self):
        core = CORE.read_text(encoding="utf-8")
        header = (ROOT / "include" / "expansion_aoe.h").read_text(encoding="utf-8")
        self.assertIn("ExpansionAoE_GetItemRouteTable(void)", core)
        self.assertIn("__attribute__((weak))", core)
        self.assertIn("ExpansionAoE_ValidateItemRouteTable", core)
        self.assertNotIn("ExpansionAoE_RegisterItemRoute", core)
        self.assertNotIn("ExpansionAoE_ResetItemRoutes", core)
        self.assertIn("const struct ExpansionAoEItemRoute* routes;", header)

    def test_reference_flag_is_default_off_across_make_c_and_autoconf(self):
        config_mk = (ROOT / "config.mk").read_text(encoding="utf-8")
        header = (ROOT / "include" / "expansion_config.h").read_text(encoding="utf-8")
        configure_ac = (ROOT / "configure.ac").read_text(encoding="utf-8")
        modern_mk = (ROOT / "modern.mk").read_text(encoding="utf-8")
        self.assertIn("EXPANSION_AOE_REFERENCE       ?= 0", config_mk)
        self.assertIn("#define FE8_EXPANSION_AOE_REFERENCE 0", header)
        self.assertIn("--enable-aoe-reference", configure_ac)
        self.assertIn(
            "-DFE8_EXPANSION_AOE_REFERENCE=$(EXPANSION_AOE_REFERENCE)", modern_mk
        )
        self.assertIn("expansion-modern-aoe-check:", modern_mk)
        self.assertIn(
            "MODERN_AOE_DISABLED_PROFILE_ROOT := build/expansion-modern-aoe-disabled",
            modern_mk,
        )
        self.assertIn(
            "MODERN_BUILD_ROOT=$(MODERN_AOE_DISABLED_PROFILE_ROOT)",
            modern_mk,
        )
        self.assertIn("EXPANSION_AOE_REFERENCE=0", modern_mk)
        self.assertIn(
            '--disabled-elf "$(MODERN_AOE_DISABLED_PROFILE_ELF)"',
            modern_mk,
        )
        runner = (
            ROOT / "tools" / "gba-playtest" / "run_aoe_checks.py"
        ).read_text(encoding="utf-8")
        probe_header = (
            ROOT / "include" / "expansion_aoe_reference.h"
        ).read_text(encoding="utf-8")
        fields = re.findall(r"/\* [0-9A-F]{2} \*/ u32 ([A-Za-z0-9_]+);", probe_header)
        module = runpy.run_path(
            str(ROOT / "tools" / "gba-playtest" / "run_aoe_checks.py"),
            run_name="aoe_runtime_schema",
        )
        self.assertEqual(tuple(fields), module["PROBE_FIELDS"])

    def test_aoe_linked_gate_rejects_archival_abi(self):
        completed = run(
            [
                "make",
                "-n",
                "expansion-modern-aoe-check",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=apcs-gnu",
            ]
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("requires MODERN_ABI=aapcs", completed.stderr + completed.stdout)


if __name__ == "__main__":
    unittest.main()
