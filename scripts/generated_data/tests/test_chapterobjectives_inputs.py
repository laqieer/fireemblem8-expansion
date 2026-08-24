"""Live prerequisite discovery for typed chapter objective generation."""

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.generated_data.chapterobjectives import deps


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "scripts" / "generated_data" / "tests" / "fixtures" / "chapterobjectives"
SCRATCH_ROOT = ROOT / "build" / "test-chapterobjectives-inputs"


class ChapterObjectivesInputTests(unittest.TestCase):
    def setUp(self):
        self.work = SCRATCH_ROOT / self.id().rsplit(".", 1)[-1]
        shutil.rmtree(self.work, ignore_errors=True)
        (self.work / "bundles").mkdir(parents=True)

        self.objectives = self.work / "two_chapters.json"
        objective_data = json.loads((FIXTURES / "two_chapters.json").read_text(encoding="utf-8"))
        objective_data["chapters"][0]["objectives"][0]["area"]["xMax"] = 14
        self.objectives.write_text(json.dumps(objective_data), encoding="utf-8")

        self.l2_units = self.work / "units_l2.json"
        self.l3_units = self.work / "units_l3.json"
        shutil.copyfile(FIXTURES / "deps_units_l2.json", self.l2_units)
        shutil.copyfile(FIXTURES / "deps_units_l3.json", self.l3_units)
        for name, units in (("l2_bundle.json", self.l2_units), ("l3_bundle.json", self.l3_units)):
            bundle = json.loads(
                (FIXTURES / "two_chapter_bundles" / name).read_text(encoding="utf-8")
            )
            bundle["chapterObjectives"]["source"] = str(self.objectives)
            bundle["tables"]["units"]["source"] = str(units)
            (self.work / "bundles" / name).write_text(json.dumps(bundle), encoding="utf-8")

        self.out_dir = self.work / "generated"
        self.target = self.out_dir / "data_chapter_objectives.c"

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def _make(self):
        return subprocess.run(
            [
                "make",
                "--no-print-directory",
                str(self.target),
                "GENERATED_DATA_OUT_DIR={}".format(self.out_dir),
                "GENERATED_DATA_CHAPTEROBJECTIVES_SOURCE={}".format(self.objectives),
                "GENERATED_DATA_CHAPTEROBJECTIVES_CHAPTERBUNDLE_SOURCE={}".format(
                    self.work / "bundles"
                ),
                "GENERATED_DATA_CHAPTEROBJECTIVES_INVENTORY={}".format(
                    self.work / "inventory.md"
                ),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_discovery_and_make_track_each_owner_source_and_map_metadata(self):
        inputs = set(deps.collect_input_paths(self.objectives, self.work / "bundles"))
        for required in (
            self.objectives,
            self.work / "bundles" / "l2_bundle.json",
            self.work / "bundles" / "l3_bundle.json",
            self.l2_units,
            self.l3_units,
            ROOT / "src" / "data" / "chapter_settings.json",
            ROOT / "src" / "data" / "data_8B363C.c",
            ROOT / "assets" / "manifest.json",
            ROOT / "assets" / "tmx" / "Ch2Map.tmx",
            ROOT / "graphics" / "map" / "layout" / "Ch3Map.json",
            ROOT / "scripts" / "generated_data" / "chapterobjectives" / "schema.py",
            ROOT / "scripts" / "generated_data" / "chapterobjectives" / "generate.py",
            ROOT / "scripts" / "generated_data" / "chapterbundle" / "schema.py",
            ROOT / "scripts" / "generated_data" / "units" / "schema.py",
            ROOT / "scripts" / "generated_data" / "shops" / "schema.py",
            ROOT / "scripts" / "generated_data" / "traps" / "schema.py",
            ROOT / "scripts" / "generated_data" / "eventscripts" / "schema.py",
            ROOT / "scripts" / "generated_data" / "eventlists" / "schema.py",
            ROOT / "scripts" / "generated_data" / "supports" / "schema.py",
        ):
            self.assertIn(os.path.realpath(required), inputs)

        initial = self._make()
        self.assertEqual(initial.returncode, 0, initial.stdout)
        self.assertTrue(self.target.is_file())

        l3_data = json.loads(self.l3_units.read_text(encoding="utf-8"))
        l3_data["groups"][0]["units"][0]["charIndex"] = "CHARACTER_EIRIKA"
        self.l3_units.write_text(json.dumps(l3_data), encoding="utf-8")
        stale_membership = self._make()
        self.assertNotEqual(stale_membership.returncode, 0)
        self.assertIn("generate --table chapterobjectives", stale_membership.stdout)
        self.assertIn("character 'CHARACTER_SETH' is not a member", stale_membership.stdout)

        shutil.copyfile(FIXTURES / "deps_units_l3.json", self.l3_units)
        restored = self._make()
        self.assertEqual(restored.returncode, 0, restored.stdout)

        for module in (
            ROOT / "scripts" / "generated_data" / "chapterbundle" / "schema.py",
            ROOT / "scripts" / "generated_data" / "units" / "schema.py",
        ):
            original_stat = module.stat()
            try:
                os.utime(
                    module,
                    ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000_000),
                )
                refreshed = self._make()
                self.assertEqual(refreshed.returncode, 0, refreshed.stdout)
                self.assertIn("generate --table chapterobjectives", refreshed.stdout)
            finally:
                os.utime(module, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

        manifest = ROOT / "assets" / "manifest.json"
        original = manifest.read_bytes()
        original_stat = manifest.stat()
        try:
            manifest.write_bytes(original.replace(b'"mapWidth": 15', b'"mapWidth": 14', 1))
            stale_area = self._make()
            self.assertNotEqual(stale_area.returncode, 0)
            self.assertIn("generate --table chapterobjectives", stale_area.stdout)
            self.assertIn("xMax 14 out of range [0, 13]", stale_area.stdout)
        finally:
            manifest.write_bytes(original)
            os.utime(manifest, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))


if __name__ == "__main__":
    unittest.main()
