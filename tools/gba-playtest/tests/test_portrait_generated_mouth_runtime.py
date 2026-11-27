"""Generated portrait mouth data through the ARM face consumer and VRAM."""

import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "gba-playtest"))
sys.path.insert(0, str(ROOT / "tools" / "gba-playtest" / "tests"))

import gba_playtest as gp
import host_mode
from scripts.assets import manifest
from scripts.assets.tests.test_manifest import (
    valid_portrait_record, write_indexed_png_stream,
)


@host_mode.live_artifact_testcase("generated portrait ARM runtime")
class GeneratedMouthRuntimeTests(unittest.TestCase):
    def test_generated_and_prefix_control_on_arm(self):
        with tempfile.TemporaryDirectory(prefix="portrait-mouth-runtime-") as directory:
            root = Path(directory)
            raw = valid_portrait_record(root)
            record = SimpleNamespace(
                id=raw["id"], kind=raw["kind"], sources=raw["sources"],
                ownership=raw["ownership"],
            )
            rows = []
            for y in range(112):
                row = bytearray(64)
                if 72 <= y < 88:
                    row[32:48] = b"\x11" * 16
                    row[48:64] = b"\x22" * 16
                rows.append(b"\0" + row)
            write_indexed_png_stream(
                ROOT / raw["sources"][0], idat=zlib.compress(b"".join(rows))
            )

            generated = manifest.portrait_component_outputs([record], root)
            for path, data in generated.items():
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                Path(path).write_bytes(data)
            (root / "portrait_data.inc").write_text(
                manifest.render_portrait_data([record]), encoding="utf-8"
            )
            (root / "portrait_components.h").write_text(
                manifest.render_portrait_symbols([record]), encoding="utf-8"
            )
            files = manifest._portrait_component_files(record)
            mouth = root / files["mouth"]
            fixed = mouth.read_bytes()
            for control in (False, True):
                with self.subTest(prefix_control=control):
                    mouth.write_bytes(b"\x11" * 256 + b"\x22" * 256 if control else fixed)
                    assembly = root / "components.s"
                    sections = []
                    for suffix, key in (
                        ("tileset", "tileset_lz"), ("chibi", "chibi_lz"),
                        ("mouth", "mouth"), ("palette", "palette"),
                    ):
                        symbol = "portrait_Proof_" + suffix
                        sections.extend([
                            '.section .rodata,"a",%progbits', ".balign 4",
                            f".global {symbol}", f"{symbol}:",
                            f'.incbin "{root / files[key]}"',
                            f".global {symbol}_end", f"{symbol}_end:",
                        ])
                    assembly.write_text("\n".join(sections) + "\n", encoding="utf-8")
                    elf = root / "fixture.elf"
                    rom = root / "fixture.gba"
                    subprocess.run([
                        "arm-none-eabi-gcc", "-mcpu=arm7tdmi", "-mthumb",
                        "-mthumb-interwork", "-mabi=aapcs", "-std=gnu89",
                        "-ffreestanding", "-fno-builtin", "-nostdlib", "-O2",
                        "-ffunction-sections", "-fdata-sections",
                        "-I", str(ROOT / "include"), "-I", str(root),
                        str(ROOT / "src" / "face.c"),
                        str(ROOT / "src" / "bmlib.c"),
                        str(ROOT / "src" / "portrait_data.c"),
                        str(ROOT / "tools/gba-playtest/tests/c/portrait_mouth_runtime.c"),
                        str(ROOT / "tools/gba-playtest/tests/c/expansion_autoplay_planner_runtime_start.s"),
                        str(assembly),
                        "-Wl,-T," + str(ROOT / "tools/gba-playtest/tests/c/expansion_autoplay_planner_runtime.ld"),
                        "-Wl,--gc-sections", "-lgcc", "-o", str(elf),
                    ], cwd=ROOT, check=True)
                    subprocess.run(
                        ["arm-none-eabi-objcopy", "-O", "binary", str(elf), str(rom)],
                        check=True,
                    )
                    image = bytearray(rom.read_bytes())
                    image[0xBD] = (-sum(image[0xA0:0xBD]) - 0x19) & 0xFF
                    rom.write_bytes(image)
                    nm = subprocess.run(
                        ["arm-none-eabi-nm", "-n", str(elf)], check=True,
                        capture_output=True, text=True,
                    )
                    address = next(
                        int(parts[0], 16) for line in nm.stdout.splitlines()
                        if len(parts := line.split()) == 3 and parts[2] == "gMouthRuntimeResults"
                    )
                    scenario = gp.parse_scenario_data({
                        "schema_version": 1,
                        "name": "generated-portrait-mouth",
                        "frames": [],
                        "checkpoints": [{
                            "name": "complete", "frame": 3, "framebuffer": False,
                            "probes": [
                                {"address": f"0x{address + offset:08x}", "size": 4}
                                for offset in range(0, 16, 4)
                            ],
                        }],
                    })
                    actual = gp.capture(rom, scenario)
                    values = [
                        int(probe["value"], 16)
                        for probe in actual["checkpoints"][0]["probes"]
                    ]
                    self.assertEqual(values[0], 24)
                    self.assertEqual(values[3], 1)
                    if control:
                        self.assertGreater(values[1], 0)
                    else:
                        self.assertEqual(values[1:3], [0, 0])


if __name__ == "__main__":
    unittest.main()
