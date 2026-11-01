"""Execute the optional reference's real allocation validator at its C interface."""

from pathlib import Path
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
            result = subprocess.run(
                [
                    "arm-none-eabi-gcc", "-std=gnu89", "-mthumb", "-mcpu=arm7tdmi",
                    "-DMODERN", "-DNONMATCHING", "-I", str(ROOT / "include"),
                    "-I", str(ROOT), "-I", str(EXAMPLE / "include"),
                    "-Wall", "-Wextra", "-Werror", "-c", str(SOURCE),
                    "-o", str(Path(temporary) / "state.o"),
                ],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
