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

    def test_linker_script_quotes_paths_with_spaces(self):
        """Object and glob paths with spaces must be quoted."""
        result = builder.generate_linker_script_text(
            (MGFEMBP / "mgfembp.lds").read_text(encoding="utf-8"),
            Path("build dir/out put"),
        )
        self.assertIn('"build dir/out put/main.o"(.text)', result)
        self.assertIn('"build dir/out put/*.o"(fake_glue)', result)

    def test_linker_script_no_quotes_without_spaces(self):
        result = builder.generate_linker_script_text(
            (MGFEMBP / "mgfembp.lds").read_text(encoding="utf-8"),
            Path("build/out"),
        )
        self.assertNotIn('"build/out/', result)

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

    def test_embed_asset_list_matches_final_assets(self):
        """EXPECTED_EMBED_SOURCE_ASSETS must cover every non-embed source
        referenced in FINAL_EMBED_ASSETS."""
        sources_from_pipeline = set()
        for source_name, _, _ in builder.FINAL_EMBED_ASSETS:
            if not source_name.startswith("embed/"):
                sources_from_pipeline.add(source_name)
        expected_set = set(builder.EXPECTED_EMBED_SOURCE_ASSETS)
        self.assertEqual(
            sources_from_pipeline, expected_set,
            f"drift: pipeline={sources_from_pipeline - expected_set}"
            f" expected={expected_set - sources_from_pipeline}",
        )

    def test_embed_assets_exist_in_submodule(self):
        """Every listed embed source asset must exist in the submodule."""
        if not MGFEMBP.is_dir():
            self.skipTest("mgfembp submodule not present")
        for asset in builder.EXPECTED_EMBED_SOURCE_ASSETS:
            path = MGFEMBP / asset
            self.assertTrue(
                path.is_file(),
                f"missing submodule asset: {asset}",
            )

    def test_modern_mk_embed_list_matches_script(self):
        """modern.mk MODERN_MGFEMBP_EMBED_ASSETS must list the same
        source assets as build_mgfembp.py."""
        mk = (ROOT / "modern.mk").read_text(encoding="utf-8")
        for asset in builder.EXPECTED_EMBED_SOURCE_ASSETS:
            self.assertIn(
                f"mgfembp/{asset}",
                mk,
                f"modern.mk missing embed asset: mgfembp/{asset}",
            )

    def test_fe6sio_dep_included_in_modern_mk(self):
        """modern.mk must -include the fe6sio .d file."""
        mk = (ROOT / "modern.mk").read_text(encoding="utf-8")
        self.assertIn("MODERN_FE6SIO_OBJ:.o=.d", mk)

    # -- Runtime embed manifest validation ----------------------------------

    def test_validate_embed_manifest_matching(self):
        """Correct manifest passes validation."""
        builder.validate_embed_manifest(list(builder.EXPECTED_EMBED_SOURCE_ASSETS))

    def test_validate_embed_manifest_with_prefix(self):
        """Manifest entries with mgfembp/ prefix are normalized."""
        prefixed = [f"mgfembp/{a}" for a in builder.EXPECTED_EMBED_SOURCE_ASSETS]
        builder.validate_embed_manifest(prefixed)

    def test_validate_embed_manifest_missing_raises(self):
        partial = list(builder.EXPECTED_EMBED_SOURCE_ASSETS)[:-1]
        with self.assertRaises(ValueError) as ctx:
            builder.validate_embed_manifest(partial)
        self.assertIn("missing from manifest", str(ctx.exception))

    def test_validate_embed_manifest_extra_raises(self):
        extended = list(builder.EXPECTED_EMBED_SOURCE_ASSETS) + ["data/extra.bin"]
        with self.assertRaises(ValueError) as ctx:
            builder.validate_embed_manifest(extended)
        self.assertIn("extra in manifest", str(ctx.exception))

    def test_validate_embed_manifest_duplicate_raises(self):
        doubled = list(builder.EXPECTED_EMBED_SOURCE_ASSETS) + [
            builder.EXPECTED_EMBED_SOURCE_ASSETS[0]
        ]
        with self.assertRaises(ValueError) as ctx:
            builder.validate_embed_manifest(doubled)
        self.assertIn("duplicate", str(ctx.exception))

    def test_modern_mk_passes_embed_assets_to_builder(self):
        """modern.mk must pass --embed-asset for each asset."""
        mk = (ROOT / "modern.mk").read_text(encoding="utf-8")
        self.assertIn("--embed-asset", mk)
        self.assertIn("MODERN_MGFEMBP_EMBED_ASSETS", mk)

    # -- Depfile filtering --------------------------------------------------

    def test_filter_depfile_removes_root_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "fe6sio.d"
            d.write_text(
                "out/fe6sio.o: fe6sio_payload.bin.lz \\\n"
                " src/data/fe6_rom_header.inc include/gba.inc asm/fe6sio.s\n",
                encoding="utf-8",
            )
            builder.filter_fe6sio_depfile(str(d))
            result = d.read_text(encoding="utf-8")
        self.assertNotIn("fe6sio_payload.bin.lz", result)
        self.assertIn("gba.inc", result)
        self.assertIn("fe6_rom_header.inc", result)
        self.assertIn("fe6sio.s", result)

    def test_filter_depfile_absent_token_is_noop(self):
        """Zero occurrences is acceptable (already clean)."""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "test.d"
            d.write_text(
                "out/fe6sio.o: include/gba.inc asm/fe6sio.s\n",
                encoding="utf-8",
            )
            builder.filter_fe6sio_depfile(str(d))
            result = d.read_text(encoding="utf-8")
        self.assertIn("gba.inc", result)

    def test_filter_depfile_duplicate_token_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "test.d"
            d.write_text(
                "out/fe6sio.o: fe6sio_payload.bin.lz \\\n"
                " fe6sio_payload.bin.lz include/gba.inc\n",
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                builder.filter_fe6sio_depfile(str(d))
            self.assertIn("2", str(ctx.exception))

    def test_filter_depfile_preserves_spaces_in_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "test.d"
            d.write_text(
                "out/fe6sio.o: fe6sio_payload.bin.lz \\\n"
                " path\\ with\\ spaces/gba.inc asm/fe6sio.s\n",
                encoding="utf-8",
            )
            builder.filter_fe6sio_depfile(str(d))
            result = d.read_text(encoding="utf-8")
        self.assertNotIn("fe6sio_payload.bin.lz", result)
        self.assertIn("path\\ with\\ spaces/gba.inc", result)


if __name__ == "__main__":
    unittest.main()
