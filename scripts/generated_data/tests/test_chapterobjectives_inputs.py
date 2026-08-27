"""Live prerequisite discovery for typed chapter objective generation."""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRATCH_ROOT = ROOT / "build" / "test-chapterobjectives-inputs"


class ChapterObjectivesInputTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = SCRATCH_ROOT / self.id().rsplit(".", 1)[-1]
        shutil.rmtree(self.sandbox, ignore_errors=True)
        self.repo = self.sandbox / "repo"
        shutil.copytree(
            ROOT,
            self.repo,
            ignore=shutil.ignore_patterns(".git", "build", "__pycache__", "*.pyc"),
        )
        self.work = self.repo / "build" / "chapterobjectives-inputs"
        (self.work / "bundles").mkdir(parents=True)

        self.objectives = self.work / "two_chapters.json"
        fixtures = self.repo / "scripts" / "generated_data" / "tests" / "fixtures" / "chapterobjectives"
        objective_data = json.loads((fixtures / "two_chapters.json").read_text(encoding="utf-8"))
        objective_data["chapters"][0]["objectives"][0]["area"]["xMax"] = 14
        self.objectives.write_text(json.dumps(objective_data), encoding="utf-8")

        self.l2_units = self.work / "units_l2.json"
        self.l3_units = self.work / "units_l3.json"
        shutil.copyfile(fixtures / "deps_units_l2.json", self.l2_units)
        shutil.copyfile(fixtures / "deps_units_l3.json", self.l3_units)
        for name, units in (("l2_bundle.json", self.l2_units), ("l3_bundle.json", self.l3_units)):
            bundle = json.loads(
                (fixtures / "two_chapter_bundles" / name).read_text(encoding="utf-8")
            )
            bundle["chapterObjectives"]["source"] = str(self.objectives)
            bundle["tables"]["units"]["source"] = str(units)
            (self.work / "bundles" / name).write_text(json.dumps(bundle), encoding="utf-8")

        self.out_dir = self.work / "generated"
        self.target = self.out_dir / "data_chapter_objectives.c"

    def tearDown(self):
        shutil.rmtree(self.sandbox, ignore_errors=True)

    def _make(self, bundle_source=None):
        return subprocess.run(
            [
                "make",
                "--no-print-directory",
                str(self.target),
                "GENERATED_DATA_OUT_DIR={}".format(self.out_dir),
                "GENERATED_DATA_CHAPTEROBJECTIVES_SOURCE={}".format(self.objectives),
                "GENERATED_DATA_CHAPTEROBJECTIVES_CHAPTERBUNDLE_SOURCE={}".format(
                    bundle_source or self.work / "bundles"
                ),
                "GENERATED_DATA_CHAPTEROBJECTIVES_INVENTORY={}".format(
                    self.work / "inventory.md"
                ),
            ],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_discovery_and_make_track_each_owner_source_and_map_metadata(self):
        discovery = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.generated_data.chapterobjectives.deps",
                "--source",
                str(self.objectives),
                "--bundle-source",
                str(self.work / "bundles"),
            ],
            cwd=self.repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(discovery.returncode, 0, discovery.stdout + discovery.stderr)
        inputs = set(discovery.stdout.splitlines())
        for required in (
            self.objectives,
            self.work / "bundles" / "l2_bundle.json",
            self.work / "bundles" / "l3_bundle.json",
            self.work / "bundles",
            self.l2_units,
            self.l3_units,
            self.repo / "src" / "data" / "chapter_settings.json",
            self.repo / "src" / "data" / "data_8B363C.c",
            self.repo / "assets" / "manifest.json",
            self.repo / "assets",
            self.repo / "assets" / "tmx" / "Ch2Map.tmx",
            self.repo / "assets" / "tmx",
            self.repo / "graphics" / "map" / "layout" / "Ch3Map.json",
            self.repo / "graphics" / "map" / "layout",
            self.repo / "scripts" / "generated_data" / "chapterobjectives" / "schema.py",
            self.repo / "scripts" / "generated_data" / "chapterobjectives" / "generate.py",
            self.repo / "scripts" / "generated_data" / "chapterbundle" / "schema.py",
            self.repo / "scripts" / "generated_data" / "units" / "schema.py",
            self.repo / "scripts" / "generated_data" / "shops" / "schema.py",
            self.repo / "scripts" / "generated_data" / "traps" / "schema.py",
            self.repo / "scripts" / "generated_data" / "eventscripts" / "schema.py",
            self.repo / "scripts" / "generated_data" / "eventlists" / "schema.py",
            self.repo / "scripts" / "generated_data" / "supports" / "schema.py",
            self.repo / "scripts" / "assets" / "tmx.py",
        ):
            self.assertIn(os.path.realpath(required), inputs)

        initial = self._make()
        self.assertEqual(initial.returncode, 0, initial.stdout)
        self.assertTrue(self.target.is_file())
        target_before_failure = self.target.read_bytes()
        depfile = self.out_dir / "chapterobjectives.inputs.mk"
        depfile_target, separator, depfile_inputs = depfile.read_text(encoding="utf-8").partition(": ")
        self.assertEqual(depfile_target, str(self.target))
        self.assertEqual(separator, ": ")
        self.assertIn(os.path.realpath(self.l3_units), depfile_inputs.split())
        self.assertIn(os.path.realpath(self.work / "bundles"), depfile_inputs.split())

        warm = self._make()
        self.assertEqual(warm.returncode, 0, warm.stdout)

        missing_bundle = self.work / "missing_bundles"
        missing = self._make(missing_bundle)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("unable to read chapter objective dependency source", missing.stdout)
        self.assertEqual(self.target.read_bytes(), target_before_failure)

        malformed_bundle = self.work / "malformed_bundle.json"
        malformed_bundle.write_text("{", encoding="utf-8")
        malformed = self._make(malformed_bundle)
        self.assertNotEqual(malformed.returncode, 0)
        self.assertIn("error:", malformed.stdout)
        self.assertEqual(self.target.read_bytes(), target_before_failure)

        l3_data = json.loads(self.l3_units.read_text(encoding="utf-8"))
        l3_data["groups"][0]["units"][0]["charIndex"] = "CHARACTER_EIRIKA"
        self.l3_units.write_text(json.dumps(l3_data), encoding="utf-8")
        stale_membership = self._make()
        self.assertNotEqual(stale_membership.returncode, 0)
        self.assertIn("FAILED: 2 diagnostic(s); nothing written", stale_membership.stdout)
        self.assertIn("character 'CHARACTER_SETH' is not a member", stale_membership.stdout)

        shutil.copyfile(
            self.repo / "scripts" / "generated_data" / "tests" / "fixtures" / "chapterobjectives"
            / "deps_units_l3.json",
            self.l3_units,
        )
        restored = self._make()
        self.assertEqual(restored.returncode, 0, restored.stdout)

        layout_dir = self.repo / "graphics" / "map" / "layout"
        layout = layout_dir / "Ch3Map.json"
        membership = layout_dir / "chapterobjectives_dependency_membership.json"
        layout_original = layout.read_bytes()
        layout_stat = layout.stat()
        layout_dir_stat = layout_dir.stat()
        try:
            membership.write_text('{"id":"membership","width":1,"height":1}', encoding="utf-8")
            added_member = self._make()
            self.assertEqual(added_member.returncode, 0, added_member.stdout)
            self.assertIn(os.path.realpath(membership), depfile.read_text(encoding="utf-8"))

            layout.unlink()
            missing_layout = self._make()
            self.assertNotEqual(missing_layout.returncode, 0)
            self.assertIn("could not resolve the owning chapter map dimensions", missing_layout.stdout)
        finally:
            layout.write_bytes(layout_original)
            os.utime(layout, ns=(layout_stat.st_atime_ns, layout_stat.st_mtime_ns))
            if membership.exists():
                membership.unlink()
            os.utime(layout_dir, ns=(layout_dir_stat.st_atime_ns, layout_dir_stat.st_mtime_ns))

        for module in (
            self.repo / "scripts" / "generated_data" / "chapterbundle" / "schema.py",
            self.repo / "scripts" / "generated_data" / "units" / "schema.py",
            self.repo / "scripts" / "assets" / "tmx.py",
        ):
            original_stat = module.stat()
            try:
                os.utime(
                    module,
                    ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000_000),
                )
                refreshed = self._make()
                self.assertEqual(refreshed.returncode, 0, refreshed.stdout)
            finally:
                os.utime(module, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

        manifest = self.repo / "assets" / "manifest.json"
        tmx = self.repo / "assets" / "tmx" / "Ch2Map.tmx"
        manifest_original = manifest.read_bytes()
        tmx_original = tmx.read_bytes()
        objectives_original = self.objectives.read_bytes()
        manifest_stat = manifest.stat()
        tmx_stat = tmx.stat()
        objectives_stat = self.objectives.stat()
        try:
            text = tmx_original.decode("utf-8")
            text = text.replace('width="15"', 'width="14"', 2)
            resized_lines = []
            for line in text.splitlines():
                stripped = line.strip()
                if stripped and stripped[0].isdigit():
                    values = stripped.rstrip(",").split(",")
                    if len(values) == 15:
                        suffix = "," if stripped.endswith(",") else ""
                        line = line[:len(line) - len(stripped)] + ",".join(values[:-1]) + suffix
                resized_lines.append(line)
            tmx.write_text("\n".join(resized_lines) + "\n", encoding="utf-8")
            manifest_mismatch = self._make()
            self.assertNotEqual(manifest_mismatch.returncode, 0)
            self.assertIn("manifest dimensions 15x15 do not match TMX 14x15", manifest_mismatch.stdout)

            manifest.write_bytes(manifest_original.replace(b'"mapWidth": 15', b'"mapWidth": 14', 1))
            objective_data = json.loads(objectives_original.decode("utf-8"))
            objective_data["chapters"][0]["objectives"][0]["area"]["xMax"] = 13
            self.objectives.write_text(json.dumps(objective_data), encoding="utf-8")
            matching_resize = self._make()
            self.assertEqual(matching_resize.returncode, 0, matching_resize.stdout)

            objective_data["chapters"][0]["objectives"][0]["area"]["xMax"] = 14
            self.objectives.write_text(json.dumps(objective_data), encoding="utf-8")
            stale_area = self._make()
            self.assertNotEqual(stale_area.returncode, 0)
            self.assertIn("xMax 14 out of range [0, 13]", stale_area.stdout)
        finally:
            manifest.write_bytes(manifest_original)
            tmx.write_bytes(tmx_original)
            self.objectives.write_bytes(objectives_original)
            os.utime(manifest, ns=(manifest_stat.st_atime_ns, manifest_stat.st_mtime_ns))
            os.utime(tmx, ns=(tmx_stat.st_atime_ns, tmx_stat.st_mtime_ns))
            os.utime(
                self.objectives,
                ns=(objectives_stat.st_atime_ns, objectives_stat.st_mtime_ns),
            )


if __name__ == "__main__":
    unittest.main()
