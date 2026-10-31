"""Explicit ARM/libmGBA suite; the host-only discovery lane does not select it."""

from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[5]
FIXTURES = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools/gba-playtest"))

from probe_bindings import resolve_elf_symbol  # noqa: E402
from scripts.localization.game_catalog.build import build_game_catalog, write_build  # noqa: E402


@unittest.skipIf(
    os.environ.get("GBA_PLAYTEST_HOST_ONLY") == "1",
    "explicit host-only lane excludes ARM/libmGBA execution",
)
class SelectedTextNativeTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "build").mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(
            prefix="selected-text-native-", dir=ROOT / "build"
        )
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.cc = os.environ.get("MODERN_CC", "arm-none-eabi-gcc")
        self.nm = os.environ.get("MODERN_NM", "arm-none-eabi-nm")
        self.objcopy = os.environ.get("MODERN_OBJCOPY", "arm-none-eabi-objcopy")

    def run_command(self, command, *, cwd=None):
        result = subprocess.run(
            [str(value) for value in command],
            cwd=self.work if cwd is None else cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=90,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout)
        return result.stdout

    def generate_inputs(self):
        source = self.work / "input"
        source.mkdir()
        generated = self.work / "generated"
        generated.mkdir()
        (source / "textdefs.txt").write_text(
            "[X] = 0\n[NL] = 1\n[NL2] = 2\n[A] = 3\n[.] = 31\n"
        )
        (source / "texts.txt").write_text(
            "## MSG_NATIVE_ZERO\nBase zero[.][X]\n"
            "## MSG_NATIVE_PAGES\nNative[NL]One[A][NL2]Second[.]Tail[.][X]\n"
            "## MSG_NATIVE_ITEM\nPractice Blade[.][X]\n"
        )
        self.run_command([
            sys.executable, "-B", ROOT / "scripts/texttools/textprocess.py",
            source / "texts.txt", source / "textdefs.txt",
            generated / "msg_data.c", generated / "selected_msg.h", "utf8",
        ])
        (source / "ja_indexed.txt").write_text("#0x0000\n日\n", encoding="utf-8")
        (source / "authored.ja.json").write_text(json.dumps({
            "kind": "fe8u-game-authored-catalog",
            "locale": "ja",
            "strings": {
                "pages": "剣[NL]一[A][NL2]二[.]剣[.][X]",
                "item": "剣[.][X]",
            },
        }, ensure_ascii=False), encoding="utf-8")
        rows = []
        for index, key in enumerate((None, "pages", "item")):
            rows.append({
                "target_id": f"0x{index:04X}",
                "state": "verified",
                "source": (
                    {"kind": "english_fallback", "reason": "native fixture control"}
                    if key is None else {"kind": "authored", "translation_key": key}
                ),
                "verification": {
                    "method": "authored-fixture",
                    "evidence": "original native fixture inputs",
                    "evidence_kind": "fixture",
                    "source_table": "selected text native fixture",
                    "source_symbol": f"fixture-{index}",
                    "source_key": f"0x{index:04X}",
                    "subsystem": "module-native text",
                    "rationale": "original fixture text",
                    "confidence": "explicit" if key is None else "high",
                },
            })
        (source / "mapping.json").write_text(json.dumps({
            "schema_version": 2,
            "kind": "fe8u-locale-mapping",
            "authority": "verified",
            "authoritative": True,
            "locale_ids": ["ja", "zh-Hans"],
            "note": "Original native fixture; not a production translation claim.",
            "rows": rows,
        }), encoding="utf-8")
        catalog = build_game_catalog(
            english_texts_path=source / "texts.txt",
            english_definitions_path=source / "textdefs.txt",
            ja_indexed_path=source / "ja_indexed.txt",
            mapping_path=source / "mapping.json",
            target_header_path=generated / "selected_msg.h",
            authored_paths={"ja": source / "authored.ja.json"},
            enabled_locales=("ja",),
        )
        self.assertEqual(catalog.target_count, 3)
        write_build(catalog, output_dir=generated / "localized")
        return generated

    def compile_profile(self, generated, mask):
        folder = self.work / str(mask)
        folder.mkdir()
        flags = [
            "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork", "-mabi=aapcs",
            "-std=gnu89", "-ffreestanding", "-fno-builtin", "-fno-common",
            "-fno-pic", "-fno-pie", "-fno-unwind-tables",
            "-fno-asynchronous-unwind-tables", "-ffunction-sections", "-fdata-sections",
            "-O1", "-g3", "-DMODERN=1", "-DNONMATCHING=1", "-DBUGFIX=1",
            f"-DFE8_EXPANSION_ENABLED_LOCALE_MASK={mask}u",
            "-DFE8_EXPANSION_DEFAULT_LOCALE_ID=0", "-DFE8_EXPANSION_STARTER_CONTENT=0",
            "-I" + str(ROOT / "include"), "-I" + str(ROOT), "-I" + str(generated),
            "-include", generated / "selected_msg.h",
            "-Werror=implicit-function-declaration", "-Werror=incompatible-pointer-types",
        ]
        if mask != 1:
            flags.append("-I" + str(generated / "localized"))
        sources = [
            ROOT / "src" / name for name in (
                "msg.c", "ramfunc.c", "bmitem.c", "bmlib.c", "bmio.c", "bm.c",
                "hardware.c", "bmmap.c", "expansion_locale.c",
                "localized_game_text.c", "localized_text_codec.c", "text_utf8.c",
            )
        ]
        sources += [generated / "msg_data.c", FIXTURES / "selected_text_native.c"]
        if mask != 1:
            sources.append(generated / "localized/game_localization_catalog.c")
        objects = []
        for source in sources:
            target = folder / source.with_suffix(".o").name
            self.run_command([self.cc, *flags, "-c", source, "-o", target])
            objects.append(target)
        for source, name in (
            (ROOT / "src/rom_header.s", "header.o"),
            (FIXTURES / "selected_text_boot.S", "boot.o"),
            (ROOT / "asm/arm.s", "arm.o"),
            (ROOT / "src/libagbsyscall.s", "syscalls.o"),
        ):
            target = folder / name
            self.run_command([
                self.cc, "-mcpu=arm7tdmi", "-mthumb-interwork",
                "-I" + str(ROOT / "include"), "-I" + str(ROOT),
                "-c", source, "-o", target,
            ])
            objects.append(target)
        (self.work / "sound").mkdir(exist_ok=True)
        self.run_command([
            self.cc, "-mcpu=arm7tdmi", "-mthumb-interwork", "-I" + str(ROOT),
            "-c", ROOT / "sound/music_player_table.s",
            "-o", self.work / "sound/music_player_table.o",
        ])
        linker = folder / "native.ld"
        linker.write_text(f"""
OUTPUT_ARCH(arm)
ENTRY(Init)
MEMORY {{
 ROM(rx) : ORIGIN = 0x08000000, LENGTH = 1M
 EWRAM(rwx) : ORIGIN = 0x02000000, LENGTH = 256K
 IWRAM(rwx) : ORIGIN = 0x03000000, LENGTH = 32K
}}
__hq_mixer_enabled = 0;
SECTIONS {{
 .rom : {{
   KEEP({folder}/header.o(.text))
   KEEP({folder}/boot.o(.text.boot))
   *(.text*) *(.rodata*) *(.locale_data)
   . = ALIGN(4);
 }} > ROM
 .data : {{
   __data_start = .; *(.data*) . = ALIGN(4); __data_end = .;
 }} > EWRAM AT> ROM
 __data_load = LOADADDR(.data);
 .ewram (NOLOAD) : {{
   *(ewram_data*) *(.bss*) *(COMMON) . = ALIGN(4);
 }} > EWRAM
 .iwram (NOLOAD) : {{
   INCLUDE {ROOT}/linker/iwram.ld
   *(iwram_data*) . = ALIGN(4);
 }} > IWRAM
 /DISCARD/ : {{ *(.comment) *(.note*) }}
}}
ASSERT(SIZEOF(.iwram) < 0x7800, "native IWRAM overlaps stack")
ASSERT(SIZEOF(.data) + SIZEOF(.ewram) < 0x40000, "native EWRAM overflow")
""")
        elf = folder / "native.elf"
        self.run_command([
            self.cc, "-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork", "-mabi=aapcs",
            "-nostartfiles", "-Wl,--gc-sections", "-Wl,--no-undefined",
            "-Wl,-T," + str(linker), *objects, "-lc", "-lnosys", "-lgcc", "-o", elf,
        ])
        rom = folder / "native.gba"
        self.run_command([
            self.objcopy, "-O", "binary", "--gap-fill=0xff", elf, rom,
        ])
        return elf, rom

    def capture_probe(self, elf, rom):
        _, size = resolve_elf_symbol(elf, "gNativeTextProbe", self.nm)
        self.assertEqual(size, 48 + 16 * 148)
        scenario = rom.parent / "scenario.json"
        scenario.write_text(json.dumps({
            "schema_version": 1,
            "name": "selected-text-native",
            "frames": [],
            "checkpoints": [{
                "name": "native-consumer-records",
                "frame": 60,
                "framebuffer": False,
                "probes": [
                    {"address": f"gNativeTextProbe+0x{offset:x}", "size": 4}
                    for offset in range(0, size, 4)
                ],
            }],
        }))
        output = rom.parent / "fingerprint.json"
        self.run_command([
            sys.executable, "-B", ROOT / "tools/gba-playtest/gba_playtest.py",
            "capture", "--rom", rom, "--elf", elf, "--nm", self.nm,
            "--scenario", scenario, "--output", output,
        ], cwd=ROOT)
        result = json.loads(output.read_text())
        probes = {
            entry["address"]: int(entry["value"], 16)
            for entry in result["checkpoints"][0]["probes"]
        }
        return b"".join(
            probes[f"gNativeTextProbe+0x{offset:x}"].to_bytes(4, "little")
            for offset in range(0, size, 4)
        )

    def test_three_message_english_and_localized_real_consumers(self):
        generated = self.generate_inputs()
        english = [
            b"Native\x01One\x03\x02Second\x1fTail", b"Base zero",
            b"Practice Blade", b"Native\x01One\x03\x02Second\x1fTail",
        ]
        japanese = [
            "剣\x01一\x03\x02二\x1f剣".encode(), b"Base zero", "剣".encode(),
            "剣\x01一\x03\x02二\x1f剣".encode(),
        ]
        for mask in (1, 3):
            with self.subTest(locale_mask=mask):
                elf, rom = self.compile_profile(generated, mask)
                data = self.capture_probe(elf, rom)
                header = struct.unpack_from("<12I", data)
                phase, count, msg_count, actual_mask, item_size, name_offset, retained_id = header[:7]
                self.assertEqual((phase, msg_count, actual_mask), (0x314D0E, 3, mask))
                self.assertEqual((item_size, name_offset, retained_id), (36, 0, 2))
                self.assertEqual(header[10], 1)
                self.assertTrue(0x03000000 <= header[8] < 0x03007800)
                self.assertGreater(header[9], 0)
                expected = [(0, index, consumer, payload, 0) for index, consumer, payload in zip(
                    (1, 0, 2, 1), (0, 0, 1, 2), english
                )]
                if mask == 3:
                    self.assertEqual(header[11], 3)
                    expected += [
                        (1, index, consumer, payload, 2 if index == 0 else 1)
                        for index, consumer, payload in zip(
                            (1, 0, 2, 1), (0, 0, 1, 2), japanese
                        )
                    ]
                    expected += [
                        (0, 1, 0, english[0], 0),
                        (0, 3, 0, b"<!LOC_INV!>", 6),
                        (0, 0xFFFFFFFF, 0, b"<!LOC_INV!>", 6),
                        (1, 1, 3, b"<!L", 4),
                    ]
                self.assertEqual(count, len(expected))
                observed = []
                for index, (locale, message, consumer, payload, status) in enumerate(expected):
                    offset = 48 + 148 * index
                    fields = struct.unpack_from("<6I", data, offset)
                    self.assertEqual((fields[0], fields[1], fields[4]), (locale, message, consumer))
                    self.assertLessEqual(fields[3], 95)
                    self.assertEqual(fields[3], len(payload))
                    self.assertEqual(data[offset + 24:offset + 25 + len(payload)], payload + b"\0")
                    record = {
                        "locale": fields[0], "message": fields[1], "consumer": fields[4],
                        "payload_hex": data[offset + 24:offset + 24 + fields[3]].hex(),
                    }
                    if mask == 3:
                        self.assertEqual(fields[2], status)
                        token_count, control_count = struct.unpack_from("<2I", data, offset + 124)
                        self.assertNotEqual(token_count, 0xFFFFFFFF)
                        self.assertLessEqual(control_count, 16)
                        controls = list(data[offset + 132:offset + 132 + control_count])
                        record.update(status=fields[2], controls=controls)
                        if index in (0, 3, 4, 7, 8):
                            self.assertEqual(controls, [1, 3, 2, 31])
                    observed.append(record)
                if mask == 3:
                    offset = 48 + 148 * 11
                    guards = [struct.unpack_from("<I", data, offset + value)[0]
                              for value in (20, 120)]
                    self.assertEqual(guards, [0x13579BDF, 0x2468ACE0])
                    observed[11]["destination_guards"] = guards
                print(json.dumps({
                    "scope": "module-native, not a full-game profile",
                    "locale_mask": actual_mask, "message_count": msg_count,
                    "copied_arm_bytes": header[9], "message_buffer_bytes": header[7],
                    "records": observed,
                }), flush=True)


if __name__ == "__main__":
    unittest.main()
