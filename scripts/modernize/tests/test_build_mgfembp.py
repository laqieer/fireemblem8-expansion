"""Tests for the modern FE6 SIO payload builder."""

import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "modernize" / "build_mgfembp.py"
MGFEMBP = ROOT / "mgfembp"
EXPECTED_BINARY_SIZE = 26451
spec = importlib.util.spec_from_file_location("build_mgfembp", SCRIPT)
builder = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(builder)


class BuildMgfembpTests(unittest.TestCase):

    def tool_paths(self):
        cc = os.environ.get("MODERN_CC") or shutil.which("arm-none-eabi-gcc")
        if not cc:
            return None
        cc_path = Path(cc)
        tool_dir = cc_path.parent
        ld = os.environ.get("MODERN_LD") or str(tool_dir / "arm-none-eabi-ld")
        objcopy = os.environ.get("MODERN_OBJCOPY") or str(tool_dir / "arm-none-eabi-objcopy")
        gbagfx = os.environ.get("GBAGFX") or str(ROOT / "tools" / "gbagfx" / "gbagfx")
        if not Path(ld).is_file() or not Path(objcopy).is_file() or not Path(gbagfx).is_file():
            return None
        return {
            "cc": cc,
            "ld": ld,
            "objcopy": objcopy,
            "gbagfx": gbagfx,
        }

    def test_source_list_matches_submodule(self):
        src_dir = MGFEMBP / "src"
        if not src_dir.is_dir():
            self.skipTest("mgfembp submodule not present")
        c_sources, asm_sources = builder.validate_source_list(MGFEMBP)
        self.assertEqual([path.name for path in c_sources], list(builder.EXPECTED_C_SOURCES))
        self.assertEqual([path.name for path in asm_sources], list(builder.EXPECTED_ASM_SOURCES))

    def test_no_agbcc_in_build_script(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for needle in ("agbcc", "old_agbcc"):
            self.assertNotIn(needle, source)

    def test_linker_script_generation(self):
        result = builder.generate_linker_script_text(
            (MGFEMBP / "mgfembp.lds").read_text(encoding="utf-8"),
            Path("build/out"),
        )
        self.assertIn("build/out/main.o(.text)", result)
        self.assertIn("build/out/*.o(fake_glue)", result)
        self.assertIn("*libc.a:libc_a-memcpy.o(.text .text.*)", result)
        self.assertIn("*libgcc.a:_divsi3.o(.text .text.*)", result)
        self.assertIn(".text.catchall", result)
        self.assertIn(".bss.catchall", result)

    def test_structural_invariants_on_real_build(self):
        tools = self.tool_paths()
        if tools is None:
            self.skipTest("modern toolchain or gbagfx not available")
        with tempfile.TemporaryDirectory() as temporary:
            out_dir = Path(temporary) / "out"
            subprocess.run(
                [
                    os.environ.get("PYTHON", shutil.which("python3") or "python3"),
                    str(SCRIPT),
                    "--submodule-root", str(MGFEMBP),
                    "--output-dir", str(out_dir),
                    "--cc", tools["cc"],
                    "--ld", tools["ld"],
                    "--objcopy", tools["objcopy"],
                    "--gbagfx", tools["gbagfx"],
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=True,
            )
            elf_path = out_dir / "fe6sio_payload.elf"
            bin_path = out_dir / "fe6sio_payload.bin"
            lz_path = out_dir / "fe6sio_payload.bin.lz"
            self.assertEqual(builder.read_elf_entry(elf_path), builder.EXPECTED_ENTRY)
            self.assertEqual(builder.read_first_word(bin_path), builder.EXPECTED_FIRST_WORD)
            self.assertEqual(bin_path.stat().st_size, EXPECTED_BINARY_SIZE)
            self.assertLess(lz_path.stat().st_size, bin_path.stat().st_size)
            self.assertEqual(lz_path.read_bytes()[0], 0x10)


if __name__ == "__main__":
    unittest.main()
