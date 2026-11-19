"""Execute the optional reference's real allocation validator at its C interface."""

from pathlib import Path
import json
import os
import shutil
import struct
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

    def _arm_data_symbols(self, path):
        compiler = self._arm_command()[0]
        binutils = os.environ.get("MODERN_BINUTILS_DIR")
        nm = discover_tool(compiler, binutils, "nm")
        objcopy = discover_tool(compiler, binutils, "objcopy")
        result = subprocess.run(
            [str(nm), "-S", "--defined-only", "--format=posix", str(path)],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            data_file = Path(temporary) / "data.bin"
            dump = subprocess.run(
                [str(objcopy), "--dump-section", f".data={data_file}", str(path)],
                capture_output=True, text=True,
            )
            self.assertEqual(dump.returncode, 0, dump.stdout + dump.stderr)
            data = data_file.read_bytes()
        symbols = {}
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) != 4 or fields[1] != "D":
                continue
            name, _, address, size = fields
            begin, size = int(address, 16), int(size, 16)
            self.assertLessEqual(begin + size, len(data), name)
            symbols[name] = data[begin:begin + size]
        return symbols

    def _arm_data_relocations(self, path):
        readelf = discover_tool(
            self._arm_command()[0], os.environ.get("MODERN_BINUTILS_DIR"), "readelf",
        )
        result = subprocess.run(
            [str(readelf), "-rW", str(path)], capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return [
            row for row in parse_readelf_relocations(result.stdout)
            if row.section_name == ".rel.data"
        ]

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
            storage_symbols = set(forwarders) | {
                "CheckChapterFlag", "CheckPermanentFlag", "CheckPermanentFlagFrom",
                "SetChapterFlag", "SetPermanentFlag", "ClearChapterFlag", "ClearPermanentFlag",
                "ResetChapterFlags", "ResetPermanentFlags",
                "GetChapterFlagBits", "GetPermanentFlagBits",
                "gChapterFlagBits", "gPermanentFlagBits",
            }
            policy_sections = set(forwarders.values())
            for row in records:
                if row.symbol_name in storage_symbols:
                    self.assertIn(row.section_name, policy_sections, row)
            self.assertTrue(all(
                any(row.symbol_name == "GradoGuide_" + name for row in records)
                for name in ("CheckFlag", "SetFlag", "ClearFlag")
            ))

    def test_inherited_event_scripts_preserve_only_non_guide_flag_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            guide_object = root / "guide-table.o"
            self._arm_compile(ROOT / "src/bmguide_config.c", guide_object)
            table = self._arm_data_symbols(guide_object)["gGuideTable"]
            self.assertEqual(len(table) % 12, 0)
            guide_flags = set()
            sentinel = False
            for entry in struct.iter_unpack("<BBHHHHH", table):
                if entry[0] == 12:
                    sentinel = True
                    break
                guide_flags.update((entry[4], entry[5]))
            self.assertTrue(sentinel)
            self.assertTrue(guide_flags)

            dynamic_seen = False
            for original, selected in (
                ("events_script.c", "static_guide_events.c"),
                ("events_wm.c", "static_guide_worldmap.c"),
            ):
                with self.subTest(provider=original):
                    native_object = root / f"native-{original}.o"
                    selected_object = root / f"selected-{original}.o"
                    self._arm_compile(ROOT / "src" / original, native_object)
                    self._arm_compile(EXAMPLE / "src" / selected, selected_object)
                    native = {
                        name: value for name, value in self._arm_data_symbols(native_object).items()
                        if name.startswith("EventScr")
                    }
                    replacement = {
                        name: value for name, value in self._arm_data_symbols(selected_object).items()
                        if name.startswith("EventScr")
                    }
                    self.assertTrue(native)
                    self.assertEqual(native.keys(), replacement.keys())
                    relocations = self._arm_data_relocations(native_object)
                    self.assertTrue(relocations)
                    self.assertEqual(relocations, self._arm_data_relocations(selected_object))
                    changed = 0
                    for name, script in native.items():
                        candidate = replacement[name]
                        self.assertEqual(len(script), len(candidate), name)
                        offset = 0
                        while offset < len(script):
                            self.assertLessEqual(offset + 4, len(script), name)
                            word = struct.unpack_from("<I", script, offset)[0]
                            command, flag = word & 0xFFFF, word >> 16
                            opcode, subcommand = command >> 8, command & 0xF
                            length = ((command >> 4) & 0xF) * 2
                            self.assertTrue(length and length % 4 == 0, (name, offset))
                            self.assertLessEqual(offset + length, len(script), (name, offset))
                            expected = script[offset:offset + length]
                            if flag in guide_flags and (
                                (opcode == 2 and subcommand == 1)
                                or (opcode == 3 and subcommand == 1)
                            ):
                                self.fail(f"unhandled inherited guide clear/check in {name}")
                            if opcode == 2 and subcommand == 9:
                                dynamic_seen |= flag == 0xFFFF
                                if flag in guide_flags:
                                    self.assertEqual(length, 4, (name, offset))
                                    expected = struct.pack("<I", 0x20)
                                    changed += 1
                            self.assertEqual(
                                candidate[offset:offset + length], expected,
                                f"{name} command at byte {offset}",
                            )
                            offset += length
                    self.assertGreater(changed, 0, original)
            self.assertTrue(dynamic_seen)

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
            for selected, initialized in ((False, False), (True, False), (True, True)):
                source = EXAMPLE / "src/static_guide.c" if selected else ROOT / "src/bmguide.c"
                guide = root / f"guide-{int(selected)}-{int(initialized)}.o"
                # The host has no ARM r4 register; native compilation retains the annotation.
                result = subprocess.run(
                    [*common, "-Dasm(x)=", "-c", str(source), "-o", str(guide)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                backend = objects[0]
                if initialized:
                    flags = EXAMPLE / "src/native_flags.c"
                    backend = root / "native-flags.o"
                    result = subprocess.run(
                        [*common, "-c", str(flags), "-o", str(backend)],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self._arm_compile(flags, root / "native-flags-arm.o")
                binary = root / f"guide-{int(selected)}-{int(initialized)}"
                result = subprocess.run(
                    [
                        *common, f"-DEXPECT_STATIC_GUIDE={int(selected)}",
                        f"-DEXPECT_GUIDE_INITIALIZER={int(initialized)}",
                        "-no-pie", "-Wl,--gc-sections",
                        str(Path(__file__).parent / "fixtures/grado_guide_probe.c"),
                        str(SOURCE), str(backend), str(objects[1]), str(guide),
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
            probe = Path(__file__).parent / "fixtures/grado_allocation_probe.c"
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

    def test_native_field_values_preserve_real_backend_and_refusals(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            values = EXAMPLE / "src/values.c"
            binary = root / "values"
            result = subprocess.run(
                [
                    "cc", "-std=gnu89", "-DMODERN=1", "-DNONMATCHING=1",
                    "-funsigned-char", "-ffunction-sections", "-fdata-sections",
                    "-Werror=implicit-function-declaration",
                    "-Werror=incompatible-pointer-types",
                    "-Wno-int-to-pointer-cast", "-Wno-pointer-to-int-cast",
                    "-I", str(ROOT / "include"), "-I", str(EXAMPLE / "include"),
                    "-no-pie", "-Wl,--gc-sections",
                    str(Path(__file__).parent / "fixtures/grado_values_probe.c"),
                    str(SOURCE), str(values), str(ROOT / "src/eventinfo.c"),
                    "-o", str(binary),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self._arm_compile(values, root / "values.o", ("-Werror",))

    def test_campaign_symbols_share_native_backing_and_rescue_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = EXAMPLE / "src/campaign_state.c"
            binary = root / "campaign"
            result = subprocess.run(
                [
                    "cc", "-std=gnu89", "-DMODERN=1", "-DNONMATCHING=1",
                    "-funsigned-char", "-ffunction-sections", "-fdata-sections",
                    "-Werror=implicit-function-declaration",
                    "-Werror=incompatible-pointer-types",
                    "-Wno-int-to-pointer-cast", "-Wno-pointer-to-int-cast",
                    "-I", str(ROOT / "include"), "-I", str(EXAMPLE / "include"),
                    "-no-pie", "-Wl,--gc-sections", "-Wl,--wrap=GradoState_WriteSymbol",
                    str(Path(__file__).parent / "fixtures/grado_campaign_probe.c"),
                    str(SOURCE), str(EXAMPLE / "src/values.c"), str(campaign),
                    str(EXAMPLE / "src/lifecycle.c"),
                    str(ROOT / "src/eventinfo.c"), "-o", str(binary),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self._arm_compile(campaign, root / "campaign.o", ("-Werror",))
            self._arm_compile(
                EXAMPLE / "src/lifecycle.c", root / "lifecycle-release.o",
                ("-Werror", "-DFE8_EXPANSION_DEBUG=0"),
            )
            self._arm_compile(
                EXAMPLE / "src/lifecycle.c", root / "lifecycle-debug.o",
                ("-Werror", "-DFE8_EXPANSION_DEBUG=1"),
            )


if __name__ == "__main__":
    unittest.main()
