"""Exercise optional Grado derivatives through the existing asset producers."""

from pathlib import Path
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image


ROOT = Path(__file__).resolve().parents[3]
ASSETS = ROOT / "examples/restoration-of-grado/assets"


class GradoVisualAssetTests(unittest.TestCase):
    def test_early_portrait_packages_alias_complete_live_entries(self):
        registry = json.loads((ROOT / "assets/portrait_registry.json").read_text())["entries"]
        expected = {
            "amelia": 25,
            "duessel": 37,
            "natasha": 17,
            "knoll": 41,
        }
        by_id = {entry["id"]: entry for entry in registry}
        for package, portrait_id in expected.items():
            with self.subTest(package=package):
                metadata = json.loads(
                    (ROOT / f"assets/portraits/{package}/metadata.json").read_text()
                )
                entry = by_id[portrait_id]
                components = metadata["alias"]["components"]
                self.assertEqual(metadata["portraitId"], portrait_id)
                self.assertEqual(metadata["anchors"]["mouth"], [entry["xMouth"], entry["yMouth"]])
                self.assertEqual(metadata["anchors"]["eyes"], [entry["xEyes"], entry["yEyes"]])
                self.assertEqual(metadata["blinkKind"], entry["blinkKind"])
                self.assertEqual(
                    components,
                    {
                        "img": entry["img"],
                        "imgChibi": entry["imgChibi"],
                        "pal": entry["pal"],
                        "imgMouth": entry["imgMouth"],
                    },
                )
                for suffix in ("img", "imgChibi", "imgMouth"):
                    self.assertTrue((ROOT / f"graphics/portrait/{components[suffix]}.png").is_file())
                self.assertTrue((ROOT / f"graphics/portrait/{components['pal']}.agbpal").is_file())

    def test_static_carriers_use_existing_tsa_and_palette_consumers(self):
        carriers = {
            "graphics/bg/grado_imperial_tomb.png": 6,
            "graphics/bg/grado_council_hall.png": 6,
            "graphics/cg/grado_funeral_omen.png": 6,
            "graphics/cg/grado_title.png": 6,
        }
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="grado-static-carriers-", dir=ROOT / "build") as directory:
            output = Path(directory)
            unsupported = subprocess.run(
                [
                    sys.executable, "-B", str(ASSETS / "static/derive_visuals.py"), "derive",
                    str(ROOT / "graphics/bg/grado_imperial_tomb.png"),
                    str(output / "unsupported.png"), "--banks", "9",
                ],
                cwd=ROOT, capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(unsupported.returncode, 0)
            for filename, bank_limit in carriers.items():
                with self.subTest(filename=filename):
                    source = ROOT / filename
                    image = Image.open(source)
                    self.assertEqual(image.mode, "P")
                    self.assertEqual(image.size, (240, 160))
                    self.assertLessEqual(image.getextrema()[1] // 16 + 1, bank_limit)
                    self.assertTrue(all(pixel & 15 for pixel in image.tobytes()))
                    raw = output / (source.stem + ".feimg2.bin")
                    tsa = output / (source.stem + ".fetsa2.bin")
                    converted = subprocess.run(
                        [
                            sys.executable, "-B", str(ROOT / "scripts/gfxtools/tsa_generator.py"),
                            str(source), str(raw), str(tsa),
                        ],
                        cwd=ROOT, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(converted.returncode, 0, converted.stdout + converted.stderr)
                    self.assertLessEqual(len(raw.read_bytes()), 10 * 0x800)
                    tilemap = tsa.read_bytes()
                    self.assertEqual(tilemap[:2], bytes((29, 19)))
                    self.assertEqual(len(tilemap), 2 + 30 * 20 * 2)
                    banks = {
                        (int.from_bytes(tilemap[offset:offset + 2], "little") >> 12) & 15
                        for offset in range(2, len(tilemap), 2)
                    }
                    self.assertLessEqual(max(banks) + 1, bank_limit)
                    tiles = raw.read_bytes()
                    for offset in range(2, len(tilemap), 2):
                        tile = int.from_bytes(tilemap[offset:offset + 2], "little") & 0x3FF
                        pixels = tiles[tile * 32:(tile + 1) * 32]
                        self.assertEqual(len(pixels), 32)
                        self.assertTrue(all(value & 15 and value >> 4 for value in pixels))
                    if source.parent.name == "cg":
                        split = subprocess.run(
                            [
                                sys.executable, "-B",
                                str(ASSETS / "static/derive_visuals.py"), "split-cg",
                                str(raw), str(output / (source.stem + "_part")),
                            ],
                            cwd=ROOT, capture_output=True, text=True, timeout=30,
                        )
                        self.assertEqual(split.returncode, 0, split.stdout + split.stderr)
                        parts = [
                            (output / f"{source.stem}_part_{part}.bin").read_bytes()
                            for part in range(10)
                        ]
                        self.assertTrue(all(len(part) == 0x800 for part in parts))
                        self.assertEqual(b"".join(parts)[:len(raw.read_bytes())], raw.read_bytes())

    def test_derivation_reserves_transparent_index_in_every_bank(self):
        with tempfile.TemporaryDirectory(prefix="grado-opaque-") as directory:
            output = Path(directory)
            source = output / "source.png"
            image = Image.new("RGB", (240, 160))
            image.putdata([
                (x % 256, y % 256, (x + y) % 256)
                for y in range(160) for x in range(240)
            ])
            image.save(source)
            for banks in (6, 8):
                with self.subTest(banks=banks):
                    target = output / f"banks-{banks}.png"
                    result = subprocess.run(
                        [sys.executable, "-B", str(ASSETS / "static/derive_visuals.py"),
                         "derive", str(source), str(target), "--banks", str(banks)],
                        cwd=ROOT, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    with Image.open(target) as derived:
                        pixels = derived.tobytes()
                    self.assertEqual({value // 16 for value in pixels}, set(range(banks)))
                    self.assertTrue(all(value & 15 for value in pixels))

    def test_generated_cg_partitions_are_ignored_and_owned_by_both_clean_targets(self):
        partitions = {
            f"graphics/cg/{carrier}_part_{index}.bin"
            for carrier in ("grado_funeral_omen", "grado_title") for index in range(10)
        }
        ignored = subprocess.run(
            ["git", "check-ignore", "--stdin"], input="\n".join(sorted(partitions)) + "\n",
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(set(ignored.stdout.splitlines()), partitions, ignored.stderr)
        for target in ("clean", "clean_fast"):
            with self.subTest(target=target):
                result = subprocess.run(
                    ["make", "--no-print-directory", "-n", target, "NODEP=1"],
                    cwd=ROOT, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                removals = set()
                for line in result.stdout.splitlines():
                    words = shlex.split(line)
                    if words and words[0] == "rm":
                        removals.update(words[1:])
                self.assertTrue(partitions <= removals, partitions - removals)

    def test_carrier_rules_retain_png_input_with_native_sibling_ordering(self):
        for prefix in (
            "graphics/bg/grado_imperial_tomb", "graphics/bg/grado_council_hall",
            "graphics/cg/grado_funeral_omen", "graphics/cg/grado_title",
        ):
            with self.subTest(prefix=prefix):
                result = subprocess.run(
                    ["make", "--no-print-directory", "-n", "-W", prefix + ".png",
                     "--eval", f"{prefix}.fetsa2.bin: {prefix}.feimg2.bin",
                     prefix + ".feimg2.bin", "FE8_PROJECT=restoration-of-grado",
                     "FE8_PROJECT_PROFILE=primary", "MODERN_CONFIG=release"],
                    cwd=ROOT, capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                commands = [
                    shlex.split(line) for line in result.stdout.splitlines()
                    if line.startswith("scripts/gfxtools/tsa_generator.py ")
                ]
                self.assertEqual(commands, [[
                    "scripts/gfxtools/tsa_generator.py", prefix + ".png",
                    prefix + ".feimg2.bin", prefix + ".fetsa2.bin",
                ]])

    def test_splitter_changes_invalidate_both_partition_families(self):
        with tempfile.TemporaryDirectory(prefix="grado-partition-make-") as directory:
            work = Path(directory)
            script = Path("examples/restoration-of-grado/assets/static/derive_visuals.py")
            (work / script).parent.mkdir(parents=True)
            shutil.copyfile(ROOT / script, work / script)
            (work / "graphics/cg").mkdir(parents=True)
            for carrier in ("grado_funeral_omen", "grado_title"):
                with self.subTest(carrier=carrier):
                    raw = f"graphics/cg/{carrier}.feimg2.bin"
                    prefix = f"graphics/cg/{carrier}_part"
                    data = bytes(range(256)) * 10
                    (work / raw).write_bytes(data)
                    command = [
                        "make", "--no-print-directory", "-f", str(ROOT / "graphics_file_rules.mk"),
                        "-o", raw, prefix + "_0.bin",
                    ]
                    built = subprocess.run(
                        command, cwd=work, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
                    self.assertEqual(
                        b"".join((work / f"{prefix}_{part}.bin").read_bytes() for part in range(10)),
                        data + bytes(10 * 0x800 - len(data)),
                    )
                    fresh = subprocess.run(
                        [*command, "-q"], cwd=work, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(fresh.returncode, 0, fresh.stdout + fresh.stderr)
                    stale = subprocess.run(
                        [*command, "-n", "-W", str(script)],
                        cwd=work, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(stale.returncode, 0, stale.stdout + stale.stderr)
                    commands = [
                        shlex.split(line) for line in stale.stdout.splitlines()
                        if line.startswith("python3 -B ")
                    ]
                    self.assertEqual(commands, [[
                        "python3", "-B", str(script), "split-cg", raw, prefix,
                    ]])

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
                "CH00": ROOT / "graphics/map/layout/grado/CH00.mar",
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
