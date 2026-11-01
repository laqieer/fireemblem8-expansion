"""Execute the optional reference's real allocation validator at its C interface."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
EXAMPLE = ROOT / "examples/restoration-of-grado"
SOURCE = EXAMPLE / "src/state.c"


class GradoAllocationNativeTests(unittest.TestCase):
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
                "            113,114,115,116,117,118,119,125,130,132,135,136,138,179\n"
                "        };\n"
                "        struct GradoStateField single =\n"
                "            {120,1,GRADO_STATE_BOOLEAN,1,255,GRADO_STATE_PERMANENT};\n"
                "        unsigned int i;\n"
                "        for (i = 0; i < sizeof(owned) / sizeof(owned[0]); ++i)\n"
                "        {\n"
                "            single.firstFlag = owned[i];\n"
                "            assert(GradoState_ValidateAllocation(&single,1,GRADO_GUIDE_STATIC)"
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
            newlib = os.environ.get(
                "MODERN_NEWLIB_INCLUDE",
                "/usr/include/newlib" if Path("/usr/include/newlib/stdlib.h").is_file() else "",
            )
            header_flags = ["-isystem", newlib] if newlib else []
            result = subprocess.run(
                [
                    "arm-none-eabi-gcc", "-std=gnu89", "-mthumb", "-mcpu=arm7tdmi",
                    "-DMODERN", "-DNONMATCHING", "-I", str(ROOT / "include"),
                    "-I", str(ROOT), "-I", str(EXAMPLE / "include"),
                    *header_flags,
                    "-Wall", "-Wextra", "-Werror", "-c", str(SOURCE),
                    "-o", str(Path(temporary) / "state.o"),
                ],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
