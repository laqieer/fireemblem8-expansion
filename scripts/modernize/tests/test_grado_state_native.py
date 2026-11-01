"""Execute the optional reference's real allocation validator at its C interface."""

from pathlib import Path
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.modernize.build_mgfembp import discover_tool, gcc_command
from scripts.linker_report.overlay_audit import parse_readelf_relocations


ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples/restoration-of-grado"
SOURCE = EXAMPLE / "src/state.c"


class GradoAllocationNativeTests(unittest.TestCase):
    def _arm_command(self, *arguments):
        prefix = os.environ.get("PREFIX", "arm-none-eabi-")
        root = os.environ.get("MODERN_TOOLCHAIN_ROOT", "")
        compiler = os.environ.get(
            "MODERN_CC",
            str(Path(root) / "bin" / f"{prefix}gcc") if root else f"{prefix}gcc",
        )
        return gcc_command(compiler, os.environ.get("MODERN_BINUTILS_DIR"), *arguments)

    def _arm_compile(self, source, output, extra=()):
        newlib = os.environ.get(
            "MODERN_NEWLIB_INCLUDE",
            "/usr/include/newlib" if Path("/usr/include/newlib/stdlib.h").is_file() else "",
        )
        header_flags = ["-isystem", newlib] if newlib else []
        result = subprocess.run(
            [
                *self._arm_command(), "-std=gnu89", "-mthumb", "-mcpu=arm7tdmi",
                "-DMODERN", "-DNONMATCHING", "-I", str(ROOT / "include"),
                "-I", str(ROOT), *header_flags, "-Wall", "-Wextra",
                "-Werror=strict-prototypes", "-Werror=implicit-function-declaration",
                "-Werror=incompatible-pointer-types", *extra,
                "-c", str(source), "-o", str(output),
            ],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_compiler_honors_owned_toolchain_paths(self):
        compiler = shutil.which(self._arm_command()[0])
        self.assertIsNotNone(compiler, "the selected ARM compiler is unavailable")
        assembler = discover_tool(compiler, os.environ.get("MODERN_BINUTILS_DIR"), "as")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            toolchain = root / "tool chain"
            selected = toolchain / "bin/grado-probe-gcc"
            selected.parent.mkdir(parents=True)
            capture = root / "compiler.jsonl"
            selected.write_text(
                "#!/usr/bin/env python3\nimport json, os, sys\n"
                f"with open({str(capture)!r}, 'a') as log:\n"
                "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                f"os.execv({compiler!r}, [{compiler!r}, *sys.argv[1:]])\n"
            )
            selected.chmod(0o755)
            binutils = root / "bin utils"
            binutils.mkdir()
            (binutils / "as").symlink_to(assembler)
            for explicit in (False, True):
                with self.subTest(explicit_compiler=explicit):
                    overrides = {
                        "PREFIX": "grado-probe-",
                        "MODERN_TOOLCHAIN_ROOT": str(root / "missing" if explicit else toolchain),
                        "MODERN_BINUTILS_DIR": str(binutils),
                    }
                    with mock.patch.dict(os.environ, overrides):
                        if explicit:
                            os.environ["MODERN_CC"] = str(selected)
                        else:
                            os.environ.pop("MODERN_CC", None)
                        self._arm_compile(SOURCE, root / f"state-{explicit}.o")
            self.assertTrue(capture.is_file(), "native compilation bypassed the selected compiler")
            invocations = [json.loads(line) for line in capture.read_text().splitlines()]
            self.assertEqual(len(invocations), 2)
            for arguments in invocations:
                self.assertIn(f"-B{binutils}/", arguments)
                self.assertIn(str(SOURCE), arguments)

    def test_native_guide_slot_routes_every_flag_call_through_its_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "guide.o"
            self._arm_compile(
                EXAMPLE / "src/static_guide.c", output,
                ("-ffunction-sections", "-fdata-sections"),
            )
            result = subprocess.run(
                [
                    str(discover_tool(
                        self._arm_command()[0],
                        os.environ.get("MODERN_BINUTILS_DIR"),
                        "readelf",
                    )),
                    "-rW", str(output),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            records = parse_readelf_relocations(result.stdout)
            forwarders = {
                "CheckFlag": ".rel.text.GradoGuide_CheckFlag",
                "SetFlag": ".rel.text.GradoGuide_SetFlag",
                "ClearFlag": ".rel.text.GradoGuide_ClearFlag",
            }
            raw_calls = [row for row in records if row.symbol_name in forwarders]
            self.assertEqual({row.symbol_name for row in raw_calls}, set(forwarders))
            for row in raw_calls:
                self.assertEqual(row.section_name, forwarders[row.symbol_name])
            self.assertTrue(all(
                any(row.symbol_name == "GradoGuide_" + name for row in records)
                for name in ("CheckFlag", "SetFlag", "ClearFlag")
            ))

    def test_static_guide_preserves_real_content_and_flag_backing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            common = [
                "cc", "-std=gnu89", "-DMODERN=1", "-DNONMATCHING=1",
                "-funsigned-char", "-ffunction-sections", "-fdata-sections",
                "-Werror=implicit-function-declaration",
                "-Wno-int-to-pointer-cast", "-Wno-pointer-to-int-cast",
                "-I", str(ROOT / "include"), "-I", str(EXAMPLE / "include"),
            ]
            objects = []
            for name in ("eventinfo", "bmguide_config"):
                output = root / f"{name}.o"
                result = subprocess.run(
                    [*common, "-c", str(ROOT / "src" / f"{name}.c"), "-o", str(output)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                objects.append(output)
            for selected in (False, True):
                source = EXAMPLE / "src/static_guide.c" if selected else ROOT / "src/bmguide.c"
                guide = root / f"guide-{int(selected)}.o"
                # The host has no ARM r4 register; native compilation retains the annotation.
                result = subprocess.run(
                    [*common, "-Dasm(x)=", "-c", str(source), "-o", str(guide)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                binary = root / f"guide-{int(selected)}"
                result = subprocess.run(
                    [
                        *common, f"-DEXPECT_STATIC_GUIDE={int(selected)}",
                        "-no-pie", "-Wl,--gc-sections",
                        str(Path(__file__).parent / "fixtures/grado_guide_probe.c"),
                        str(SOURCE), *(str(path) for path in objects), str(guide),
                        "-o", str(binary),
                    ],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                result = subprocess.run([str(binary)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_allocation_domains_reservations_and_lifetimes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "global.h").write_text(
                "#ifndef TEST_GLOBAL_H\n#define TEST_GLOBAL_H\n"
                "typedef unsigned char u8;\ntypedef unsigned short u16;\n"
                "#endif\n"
            )
            probe = root / "probe.c"
            probe.write_text(
                '#include <assert.h>\n#include "grado_state.h"\n'
                "int main(void)\n{\n"
                "    struct GradoStateField fields[2] = {\n"
                "        {7, 3, GRADO_STATE_ONE_HOT, 3, 0, GRADO_STATE_CHAPTER},\n"
                "        {7, 2, GRADO_STATE_COUNTER, 3, 1, GRADO_STATE_CHAPTER}\n"
                "    };\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_PROGRESSIVE)"
                " == GRADO_ALLOCATION_VALID);\n"
                "    fields[1].chapter = 0;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_OVERLAP);\n"
                "    fields[1].firstFlag = 10;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALID);\n"
                "    fields[0].maxValue = 4;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALUE);\n"
                "    fields[0].maxValue = 3;\n"
                "    fields[1].maxValue = 4;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALUE);\n"
                "    fields[1].maxValue = 3;\n"
                "    fields[1].scope = 2;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_SCOPE);\n"
                "    fields[1].scope = GRADO_STATE_CHAPTER;\n"
                "    fields[1].firstFlag = 40;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_CAPACITY);\n"
                "    fields[1].bitCount = 1;\n"
                "    fields[1].maxValue = 1;\n"
                "    fields[1].kind = GRADO_STATE_BOOLEAN;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALID);\n"
                "    fields[1].firstFlag = 39;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_RESERVED);\n"
                "    fields[1].firstFlag = 1;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_RESERVED);\n"
                "    fields[1].scope = GRADO_STATE_PERMANENT;\n"
                "    fields[1].chapter = GRADO_STATE_NO_CHAPTER;\n"
                "    fields[1].firstFlag = 180;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_PROGRESSIVE)"
                " == GRADO_ALLOCATION_GUIDE_OWNED);\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALID);\n"
                "    fields[1].firstFlag = 179;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_RESERVED);\n"
                "    fields[1].firstFlag = 132;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_RESERVED);\n"
                "    fields[1].firstFlag = 136;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_RESERVED);\n"
                "    fields[1].firstFlag = 100;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_CAPACITY);\n"
                "    fields[1].firstFlag = 300;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALID);\n"
                "    fields[1].bitCount = 2;\n"
                "    fields[1].kind = GRADO_STATE_COUNTER;\n"
                "    fields[1].maxValue = 3;\n"
                "    assert(GradoState_ValidateAllocation(fields, 2, GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_CAPACITY);\n"
                "    {\n"
                "        static const unsigned short owned[] = {\n"
                "            101,102,103,104,105,106,107,108,109,110,111,112,\n"
                "            113,114,115,116,117,118,119,125,130,132,135,136,137,138,179\n"
                "        };\n"
                "        struct GradoStateField single =\n"
                "            {120,1,GRADO_STATE_BOOLEAN,1,255,GRADO_STATE_PERMANENT};\n"
                "        static const struct GradoStateField spans[] = {\n"
                "            {38,2,GRADO_STATE_ONE_HOT,2,0,GRADO_STATE_CHAPTER},\n"
                "            {126,5,GRADO_STATE_COUNTER,31,255,GRADO_STATE_PERMANENT},\n"
                "            {178,2,GRADO_STATE_COUNTER,3,255,GRADO_STATE_PERMANENT}\n"
                "        };\n"
                "        struct GradoStateField guideSpan =\n"
                "            {238,3,GRADO_STATE_COUNTER,7,255,GRADO_STATE_PERMANENT};\n"
                "        unsigned int i;\n"
                "        for (i = 0; i < sizeof(owned) / sizeof(owned[0]); ++i)\n"
                "        {\n"
                "            single.firstFlag = owned[i];\n"
                "            assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_RESERVED);\n"
                "            assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_PROGRESSIVE)"
                " == GRADO_ALLOCATION_RESERVED);\n"
                "        }\n"
                "        for (i = 180; i <= 295; ++i)\n"
                "        {\n"
                "            single.firstFlag = i;\n"
                "            assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_PROGRESSIVE)"
                " == (i <= 235 || i >= 240 ? GRADO_ALLOCATION_GUIDE_OWNED"
                " : GRADO_ALLOCATION_VALID));\n"
                "            assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALID);\n"
                "        }\n"
                "        for (i = 0; i < sizeof(spans) / sizeof(spans[0]); ++i)\n"
                "        {\n"
                "            assert(GradoState_ValidateAllocation(spans+i,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_RESERVED);\n"
                "            assert(GradoState_ValidateAllocation(spans+i,1,GRADO_GUIDE_PROGRESSIVE)"
                " == GRADO_ALLOCATION_RESERVED);\n"
                "        }\n"
                "        assert(GradoState_ValidateAllocation(&guideSpan,1,GRADO_GUIDE_PROGRESSIVE)"
                " == GRADO_ALLOCATION_GUIDE_OWNED);\n"
                "        assert(GradoState_ValidateAllocation(&guideSpan,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALID);\n"
                "        single.firstFlag = 120;\n"
                "        single.chapter = 0;\n"
                "        assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_SCOPE);\n"
                "        single.scope = GRADO_STATE_CHAPTER;\n"
                "        single.firstFlag = 7;\n"
                "        single.chapter = 21;\n"
                "        assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALID);\n"
                "        single.chapter = 22;\n"
                "        assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_SCOPE);\n"
                "        single.chapter = 0;\n"
                "        for (i = 1; i <= 6; ++i)\n"
                "        {\n"
                "            single.firstFlag = i;\n"
                "            assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_RESERVED);\n"
                "        }\n"
                "        single.firstFlag = 0;\n"
                "        assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_CAPACITY);\n"
                "        single.firstFlag = 7;\n"
                "        single.bitCount = 2;\n"
                "        assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALUE);\n"
                "        single.bitCount = 1;\n"
                "        single.maxValue = 2;\n"
                "        assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALUE);\n"
                "        single.kind = GRADO_STATE_ONE_HOT;\n"
                "        single.maxValue = 1;\n"
                "        assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALUE);\n"
                "        single.kind = GRADO_STATE_COUNTER;\n"
                "        single.maxValue = 0;\n"
                "        assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALUE);\n"
                "        single.kind = GRADO_STATE_BOOLEAN;\n"
                "        single.maxValue = 1;\n"
                "        single.bitCount = 0;\n"
                "        assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALUE);\n"
                "        single.bitCount = 9;\n"
                "        assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALUE);\n"
                "        single.bitCount = 1;\n"
                "        single.kind = 255;\n"
                "        assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALUE);\n"
                "        single.kind = GRADO_STATE_COUNTER;\n"
                "        single.bitCount = 8;\n"
                "        single.maxValue = 255;\n"
                "        assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALID);\n"
                "        single.maxValue = 256;\n"
                "        assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALUE);\n"
                "        assert(GradoState_ValidateAllocation(0,1,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_INPUT);\n"
                "        assert(GradoState_ValidateAllocation(&single,0,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_INPUT);\n"
                "        assert(GradoState_ValidateAllocation(&single,1,"
                "(enum GradoGuideStoragePolicy)2) == GRADO_ALLOCATION_INPUT);\n"
                "        fields[0].firstFlag = 140;\n"
                "        fields[0].bitCount = 3;\n"
                "        fields[0].kind = GRADO_STATE_ONE_HOT;\n"
                "        fields[0].maxValue = 3;\n"
                "        fields[0].chapter = 255;\n"
                "        fields[0].scope = GRADO_STATE_PERMANENT;\n"
                "        fields[1] = fields[0];\n"
                "        fields[1].firstFlag = 142;\n"
                "        assert(GradoState_ValidateAllocation(fields,2,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_OVERLAP);\n"
                "        fields[1].firstFlag = 143;\n"
                "        assert(GradoState_ValidateAllocation(fields,2,GRADO_GUIDE_STATIC)"
                " == GRADO_ALLOCATION_VALID);\n"
                "    }\n"
                "    return 0;\n}\n"
            )
            binary = root / "probe"
            result = subprocess.run(
                [
                    "cc", "-std=gnu89", "-Wall", "-Wextra", "-Werror",
                    "-I", str(root), "-I", str(EXAMPLE / "include"),
                    "-I", str(ROOT / "include"),
                    str(probe), str(SOURCE), "-o", str(binary),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_validator_compiles_for_the_supported_arm_abi(self):
        with tempfile.TemporaryDirectory() as temporary:
            self._arm_compile(
                SOURCE, Path(temporary) / "state.o", ("-Werror",),
            )


if __name__ == "__main__":
    unittest.main()
