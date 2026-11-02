"""Exercise optional Grado derivatives through the existing asset producers."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "examples/restoration-of-grado/assets"


class GradoVisualAssetTests(unittest.TestCase):
    def test_map_derivatives_produce_native_configuration_indices(self):
        samples = {
            "CH00": ((0, 0, 1704), (2, 4, 1996), (4, 3, 2492), (14, 5, 2876), (12, 1, 1808)),
            "CH02": ((0, 0, 2604), (7, 10, 2904), (4, 9, 3328), (14, 1, 2492), (7, 4, 3280)),
        }
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="grado-native-maps-", dir=ROOT / "build") as directory:
            output = Path(directory) / "generated/assets"
            generated = subprocess.run(
                [
                    sys.executable, "-B", "-m", "scripts.assets",
                    "--manifest", str(ASSETS / "manifest.json"),
                    "--out-dir", str(output), "--item-id-cap", "0xCD", "generate",
                ],
                cwd=ROOT, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
            sources = {
                "CH00": ASSETS / "maps/CH00.mar",
                "CH02": output / "tmx/CH2_MAIN_MAP.mar",
            }
            for chapter, source in sources.items():
                with self.subTest(chapter=chapter):
                    binary = Path(directory) / f"{chapter}.bin"
                    converted = subprocess.run(
                        [sys.executable, "-B", str(ROOT / "scripts/mar_to_map.py"),
                         str(source), str(binary)],
                        cwd=ROOT, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(converted.returncode, 0, converted.stdout + converted.stderr)
                    native = binary.read_bytes()
                    self.assertEqual(native[:2], bytes((16, 12)))
                    self.assertEqual(len(native), 386)
                    self.assertTrue(all(
                        int.from_bytes(native[index:index + 2], "little") % 4 == 0
                        for index in range(2, len(native), 2)
                    ))
                    for x, y, expected in samples[chapter]:
                        offset = 2 + 2 * (16 * y + x)
                        self.assertEqual(int.from_bytes(native[offset:offset + 2], "little"), expected)
            for manifest, size, first_tile in (
                (ROOT / "assets/manifest.json", 15, 520),
                (ASSETS / "manifest.json", 16, 2604),
            ):
                with self.subTest(manifest=manifest):
                    regenerated = subprocess.run(
                        [
                            sys.executable, "-B", "-m", "scripts.assets",
                            "--manifest", str(manifest), "--out-dir", str(output),
                            "--item-id-cap", "0xCD", "generate",
                        ],
                        cwd=ROOT, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(regenerated.returncode, 0, regenerated.stdout + regenerated.stderr)
                    converted = subprocess.run(
                        [sys.executable, "-B", str(ROOT / "scripts/mar_to_map.py"),
                         str(output / "tmx/CH2_MAIN_MAP.mar"), str(Path(directory) / "selected.bin")],
                        cwd=ROOT, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(converted.returncode, 0, converted.stdout + converted.stderr)
                    native = (Path(directory) / "selected.bin").read_bytes()
                    self.assertEqual(native[:2], bytes((size, size if size == 15 else 12)))
                    self.assertEqual(int.from_bytes(native[2:4], "little"), first_tile)
