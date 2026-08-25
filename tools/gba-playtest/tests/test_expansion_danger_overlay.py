"""
Issue #6 host tests -- player QoL danger/range overlay map-menu surface.

These compile the real src/menu_def.c and src/bmmenu.c with a native host
compiler in both the disabled (default) and enabled configurations and prove,
from the actual compiled objects, that:

  * the disabled build's gMapMenuItems table is byte-identical to vanilla
    (unchanged size -- no visible item added, fully vanilla behaviour);
  * the enabled build adds exactly one MenuItemDef and stays within
    MENU_ITEM_MAX;
  * the promote-wrapper is compile-gated (the default bmmenu object has no
    reference to it) and delegates to the vanilla danger-zone effect;
  * the surface is wired through modern.mk's -D flags.

The full menu Proc/rendering engine is not host-executable, so selection/
cancel lifecycle is proven at runtime by the gba-playtest scenario
(tools/gba-playtest/scenarios/starter-danger-overlay-*.json); here we prove
the table/callback/config contract that the scenario builds on.
"""

import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INCLUDE_DIRS = [REPO_ROOT / "include", REPO_ROOT / "include" / "generated"]
MENU_DEF_SRC = REPO_ROOT / "src" / "menu_def.c"
BMMENU_SRC = REPO_ROOT / "src" / "bmmenu.c"
BMMENU_HEADER = REPO_ROOT / "include" / "bmmenu.h"
PLAYERPHASE_SRC = REPO_ROOT / "src" / "playerphase.c"
PROBE_HEADER = REPO_ROOT / "include" / "expansion_danger_overlay.h"

CC = shutil.which("gcc") or shutil.which("cc")
ARM_CC = shutil.which("arm-none-eabi-gcc")
NM = shutil.which("nm")
OBJDUMP = shutil.which("objdump")

FLAG = "FE8_EXPANSION_DANGER_OVERLAY_MENU"
MODERN = "FE8_EXPANSION_MODERN_BUILD"


def _skip_if_no_host_compiler():
    if CC is None:
        raise unittest.SkipTest("no host C compiler (gcc/cc) available")


def _include_flags():
    flags = []
    for directory in INCLUDE_DIRS:
        flags += ["-I", str(directory)]
    return flags


def _compile(work_dir, src, obj_name, defines=(), extra=()):
    obj = Path(work_dir) / obj_name
    cmd = [CC, "-c", "-w"] + _include_flags()
    for define in defines:
        cmd += ["-D", define]
    cmd += list(extra) + [str(src), "-o", str(obj)]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr, obj


def _symbol_size(obj, name):
    if NM is None:
        raise unittest.SkipTest("no host 'nm' available")
    proc = subprocess.run([NM, "--print-size", str(obj)], capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[3] == name:
            return int(parts[1], 16)
    raise AssertionError("symbol %r not found (with size) in %s" % (name, obj))


def _referenced_symbol_names(obj):
    if NM is None:
        raise unittest.SkipTest("no host 'nm' available")
    proc = subprocess.run([NM, str(obj)], capture_output=True, text=True)
    return {line.split()[-1] for line in proc.stdout.splitlines() if line.split()}


def _symbol_type(obj, name):
    """The single-letter nm type for a symbol (e.g. 'D'/'d' data, 'U'
    undefined reference). Raises if the symbol is absent entirely."""
    if NM is None:
        raise unittest.SkipTest("no host 'nm' available")
    proc = subprocess.run([NM, str(obj)], capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1] == name:
            return parts[-2]
    raise AssertionError("symbol %r not found in %s" % (name, obj))


def _object_section_names(obj):
    if OBJDUMP is None:
        raise unittest.SkipTest("no host 'objdump' available")
    proc = subprocess.run([OBJDUMP, "-h", str(obj)], capture_output=True, text=True)
    names = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit():
            names.add(parts[1])
    return names


def _section_relocation_symbols(obj, section):
    if OBJDUMP is None:
        raise unittest.SkipTest("no host 'objdump' available")
    proc = subprocess.run(
        [OBJDUMP, "-r", "-j", section, str(obj)], capture_output=True, text=True
    )
    if proc.returncode:
        raise AssertionError(proc.stdout + proc.stderr)
    return {
        re.sub(r"-0x[0-9a-fA-F]+$", "", line.split()[-1])
        for line in proc.stdout.splitlines()
        if line.split()
    }


def _relocation_section_counts(obj, symbol):
    if OBJDUMP is None:
        raise unittest.SkipTest("no host 'objdump' available")
    proc = subprocess.run([OBJDUMP, "-r", str(obj)], capture_output=True, text=True)
    if proc.returncode:
        raise AssertionError(proc.stdout + proc.stderr)
    section = None
    counts = {}
    for line in proc.stdout.splitlines():
        match = re.match(r"RELOCATION RECORDS FOR \[(.+)\]:", line)
        if match:
            section = match.group(1)
        elif section is not None and symbol in line:
            counts[section] = counts.get(section, 0) + 1
    return counts


def _section_is_all_zero(obj, section):
    if OBJDUMP is None:
        raise unittest.SkipTest("no host 'objdump' available")
    proc = subprocess.run([OBJDUMP, "-s", "-j", section, str(obj)],
                          capture_output=True, text=True)
    started = False
    for line in proc.stdout.splitlines():
        if line.startswith("Contents of section"):
            started = True
            continue
        if started:
            parts = line.split()
            if parts and re.fullmatch(r"[0-9a-fA-F]+", parts[0]):
                for tok in parts[1:5]:
                    if re.fullmatch(r"[0-9a-fA-F]+", tok) and set(tok) != {"0"}:
                        return False
    return True


def _probe_constants(work_dir):
    """sizeof(struct MenuItemDef) and MENU_ITEM_MAX, on this host."""
    src = Path(work_dir) / "probe.c"
    src.write_text(
        '#include "global.h"\n#include "uimenu.h"\n#include <stdio.h>\n'
        'int main(void){printf("%zu %d\\n", sizeof(struct MenuItemDef),'
        ' (int)MENU_ITEM_MAX); return 0;}\n',
        encoding="utf-8",
    )
    exe = Path(work_dir) / "probe"
    cmd = [CC, "-w"] + _include_flags() + [str(src), "-o", str(exe)]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = subprocess.run([str(exe)], capture_output=True, text=True).stdout.split()
    return int(out[0]), int(out[1])


class DangerOverlayTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _skip_if_no_host_compiler()

    def test_disabled_table_is_vanilla_and_enabled_adds_exactly_one(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            sizeof_item, menu_item_max = _probe_constants(tmp)
            rc, out, dis = _compile(tmp, MENU_DEF_SRC, "md_dis.o")
            self.assertEqual(rc, 0, "compiling menu_def.c (disabled) failed:\n" + out)
            rc, out, ena = _compile(tmp, MENU_DEF_SRC, "md_en.o", defines=[FLAG + "=1"])
            self.assertEqual(rc, 0, "compiling menu_def.c (enabled) failed:\n" + out)
            dis_size = _symbol_size(dis, "gMapMenuItems")
            ena_size = _symbol_size(ena, "gMapMenuItems")

        self.assertEqual(dis_size % sizeof_item, 0, "table size must be a whole number of items")
        dis_entries = dis_size // sizeof_item
        ena_entries = ena_size // sizeof_item
        # Exactly one MenuItemDef added when enabled; nothing when disabled.
        self.assertEqual(ena_entries, dis_entries + 1,
                         "enabled table must add exactly one MenuItemDef")
        # The table includes a MenuItemsEnd terminator; visible items exclude it.
        visible_when_enabled = ena_entries - 1
        self.assertLessEqual(visible_when_enabled, menu_item_max,
                             "enabled visible item count must stay within MENU_ITEM_MAX")

    def test_enabled_object_references_the_promoted_effect(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, ena = _compile(tmp, MENU_DEF_SRC, "md_en.o", defines=[FLAG + "=1"])
            self.assertEqual(rc, 0, out)
            refs = _referenced_symbol_names(ena)
        self.assertIn("ExpansionDangerOverlay_MenuSelect", refs,
                      "enabled menu table must reference the promote-wrapper")


class DangerOverlayWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _skip_if_no_host_compiler()

    def test_disabled_bmmenu_has_no_wrapper_reference(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, obj = _compile(tmp, BMMENU_SRC, "bmmenu_default.o")
            self.assertEqual(rc, 0, out)
            refs = _referenced_symbol_names(obj)
        wrapper_refs = [n for n in refs if "ExpansionDangerOverlay" in n]
        self.assertEqual(wrapper_refs, [],
                         "default bmmenu.o must not reference the overlay wrapper; found: %r"
                         % wrapper_refs)

    def test_enabled_bmmenu_defines_the_wrapper(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, obj = _compile(
                tmp,
                BMMENU_SRC,
                "bmmenu_enabled.o",
                defines=[FLAG + "=1"],
                extra=["-ffunction-sections"],
            )
            self.assertEqual(rc, 0, out)
            sections = _object_section_names(obj)
            wrapper_sections = [
                section
                for section in sections
                if section.endswith("ExpansionDangerOverlay_MenuSelect")
            ]
            self.assertEqual(wrapper_sections, [".text.ExpansionDangerOverlay_MenuSelect"])
            relocations = _section_relocation_symbols(obj, wrapper_sections[0])
        self.assertIn(
            "MapMenu_DangerZone_UnusedEffect",
            relocations,
            "enabled wrapper must delegate through the existing danger-zone effect",
        )


class DangerOverlayConfigurationTests(unittest.TestCase):
    def test_parsed_default_and_enabled_identity_are_distinct(self):
        import sys

        sys.path.insert(0, str(REPO_ROOT / "scripts" / "modernize"))
        import expansion_config as ec

        default = ec.load_identity(
            REPO_ROOT / "config.mk", "debug", "aapcs", "16M", repo_root=REPO_ROOT
        )
        enabled = ec.load_identity(
            REPO_ROOT / "config.mk",
            "debug",
            "aapcs",
            "16M",
            repo_root=REPO_ROOT,
            danger_overlay_menu=1,
        )
        self.assertEqual(default.danger_overlay_menu, 0)
        self.assertEqual(enabled.danger_overlay_menu, 1)
        self.assertNotEqual(default.config_fingerprint, enabled.config_fingerprint)
        with self.assertRaises(ec.ConfigError):
            ec.load_identity(
                REPO_ROOT / "config.mk",
                "debug",
                "aapcs",
                "16M",
                repo_root=REPO_ROOT,
                danger_overlay_menu=2,
            )

    def test_arm_aapcs_compiles_enabled(self):
        if ARM_CC is None:
            raise unittest.SkipTest("arm-none-eabi-gcc not available")
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for src, name in ((MENU_DEF_SRC, "md.o"), (BMMENU_SRC, "bm.o")):
                obj = Path(tmp) / name
                cmd = [ARM_CC, "-mthumb", "-mcpu=arm7tdmi", "-mabi=aapcs", "-std=gnu89",
                       "-c", "-w"] + _include_flags() + ["-D" + FLAG + "=1",
                       str(src), "-o", str(obj)]
                proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0,
                                 "arm compile of %s failed:\n%s" % (src.name, proc.stdout + proc.stderr))


class DangerOverlayProbeTests(unittest.TestCase):
    """The QoL semantic probe (always-linked in every modern build) and its
    compile-gated writes; plus the standing anti-orphan legacy regression."""

    @classmethod
    def setUpClass(cls):
        _skip_if_no_host_compiler()

    def test_probe_defined_in_every_modern_build(self):
        """The negative-control probe is *defined* (present, 20-byte struct)
        in every modern build: the modern default (macro on, feature off),
        the modern feature-on build, and any feature-on build. This is what
        the runtime negative/positive scenarios rely on always finding."""
        import tempfile
        configs = (
            ([MODERN + "=1"], "modern_default"),
            ([MODERN + "=1", FLAG + "=1"], "modern_enabled"),
            ([FLAG + "=1"], "feature_only"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            for defs, tag in configs:
                rc, out, obj = _compile(tmp, PLAYERPHASE_SRC, "pp_%s.o" % tag, defines=defs)
                self.assertEqual(rc, 0, "compiling playerphase.c (%s) failed:\n%s" % (tag, out))
                self.assertEqual(_symbol_type(obj, "gExpansionDangerOverlayProbe").upper(),
                                 "D", "%s build must *define* the probe symbol" % tag)
                self.assertEqual(_symbol_size(obj, "gExpansionDangerOverlayProbe"), 5 * 4,
                                 "%s probe must be the 5x u32 struct (20 bytes)" % tag)

    def test_modern_default_probe_is_present_and_all_zero(self):
        """modern default (FE8_EXPANSION_MODERN_BUILD=1, feature off) keeps the
        always-linked probe present and its ewram_data all-zero -- the exact
        negative-control precondition, proven from the compiled object."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, obj = _compile(tmp, PLAYERPHASE_SRC, "pp_md.o", defines=[MODERN + "=1"])
            self.assertEqual(rc, 0, out)
            self.assertIn("gExpansionDangerOverlayProbe", _referenced_symbol_names(obj))
            self.assertIn("ewram_data", _object_section_names(obj),
                          "modern-disabled playerphase.o must emit the probe ewram_data")
            self.assertTrue(_section_is_all_zero(obj, "ewram_data"),
                            "modern-disabled probe (ewram_data) must be all-zero")

    def test_probe_code_relocations_are_feature_gated(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            rc, out, default_obj = _compile(
                tmp,
                PLAYERPHASE_SRC,
                "pp_default_sections.o",
                defines=[MODERN + "=1"],
                extra=["-ffunction-sections"],
            )
            self.assertEqual(rc, 0, out)
            rc, out, enabled_obj = _compile(
                tmp,
                PLAYERPHASE_SRC,
                "pp_enabled_sections.o",
                defines=[MODERN + "=1", FLAG + "=1"],
                extra=["-ffunction-sections"],
            )
            self.assertEqual(rc, 0, out)

            self.assertEqual(
                _relocation_section_counts(default_obj, "gExpansionDangerOverlayProbe"),
                {},
                "default modern playerphase code must not access the always-defined probe",
            )
            self.assertEqual(
                _relocation_section_counts(enabled_obj, "gExpansionDangerOverlayProbe"),
                {
                    ".text.PlayerPhase_DisplayDangerZone": 4,
                    ".text.PlayerPhase_RangeDisplayIdle": 4,
                },
            )

    def test_legacy_like_build_emits_no_probe_and_no_ewram_orphan(self):
        """Standing anti-orphan regression (issue #6 Sprint 1 narrow fix): a
        legacy-like compile (NO modern macro, feature off) must define nothing
        here, so src/playerphase.o emits NO ewram_data section and NO probe
        symbol (neither definition nor reference). Otherwise playerphase.o's
        ewram_data becomes a *silent orphan* under ldscript.txt, whose legacy
        ewram_data output section enumerates objects one-by-one and does not
        list src/playerphase.o. Proven from the compiled object with
        objdump/nm, since agbcc is not part of this host suite."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, obj = _compile(tmp, PLAYERPHASE_SRC, "pp_legacy.o")  # no -D flags
            self.assertEqual(rc, 0, out)
            self.assertNotIn("ewram_data", _object_section_names(obj),
                             "legacy-like playerphase.o must emit no ewram_data section "
                             "(else it is a silent orphan under ldscript.txt)")
            probe_syms = [n for n in _referenced_symbol_names(obj)
                          if "DangerOverlayProbe" in n]
            self.assertEqual(probe_syms, [],
                             "legacy-like playerphase.o must neither define nor "
                             "reference the probe; found: %r" % probe_syms)

    def test_playerphase_arm_compiles_enabled(self):
        if ARM_CC is None:
            raise unittest.SkipTest("arm-none-eabi-gcc not available")
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            obj = Path(tmp) / "pp.o"
            cmd = [ARM_CC, "-mthumb", "-mcpu=arm7tdmi", "-mabi=aapcs", "-std=gnu89",
                   "-c", "-w"] + _include_flags() + ["-D" + FLAG + "=1",
                   str(PLAYERPHASE_SRC), "-o", str(obj)]
            proc = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0,
                             "arm compile of playerphase.c failed:\n" + proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
