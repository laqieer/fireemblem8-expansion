import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from scripts.fonttools.cjk.inventory import (
    CjkFontError,
    FONT_SOURCES,
    build_generated_files,
    read_sfnt_identity,
)
from scripts.fonttools.cjk.package import (
    ASSET_ROOT,
    FEHBUILDER_BASELINE_MANIFEST,
    FEHRR_SOURCES,
    archive_package,
    check_compact_assets,
    compact_asset_filenames,
    refresh_compact_asset_inventory_provenance,
)


class CjkFontTests(unittest.TestCase):
    SCRATCH = Path(__file__).resolve().parent / ".scratch"

    def test_expansion_catalog_inventory_provenance_matches_current_62_keys(self):
        inventory = json.loads((ROOT / "fonts/cjk/inventory.json").read_text())
        registry_path = ROOT / "texts/expansion/registry.json"
        registry = json.loads(registry_path.read_text())
        active_keys = {
            record["key"]
            for record in registry["messages"]
            if record["status"] == "active"
        }
        self.assertEqual(len(active_keys), 62)

        catalog_paths = sorted((ROOT / "texts/expansion").glob("catalog.*.json"))
        source_paths = [registry_path, *catalog_paths]
        for path in source_paths:
            relative_path = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            self.assertEqual(
                inventory["inputs"][relative_path],
                {
                    "byte_count": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                },
            )

        for path in catalog_paths:
            catalog = json.loads(path.read_text())
            self.assertEqual(set(catalog["strings"]), active_keys)

        expected_catalogs = [
            path.relative_to(ROOT).as_posix() for path in catalog_paths
        ]
        for locale in ("ja", "zh-Hans"):
            expansion = inventory["locales"][locale]["expansion"]
            self.assertEqual(
                expansion["active_key_count"],
                len(active_keys),
            )
            self.assertEqual(expansion["catalogs"], expected_catalogs)

    def test_canonical_authored_catalogs_are_inventory_inputs_and_fully_covered(self):
        inventory = json.loads((ROOT / "fonts/cjk/inventory.json").read_text())
        manifest_path = ROOT / "texts/locales/authored/manifest.json"
        input_paths = [
            manifest_path,
            *(
                ROOT / f"texts/locales/authored/catalog.{locale}.json"
                for locale in ("ja", "zh-Hans")
            ),
        ]
        for path in input_paths:
            relative = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            self.assertEqual(
                inventory["inputs"][relative],
                {
                    "byte_count": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                },
            )

        for locale in ("ja", "zh-Hans"):
            catalog_path = (
                ROOT / f"texts/locales/authored/catalog.{locale}.json"
            )
            catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
            metadata = inventory["locales"][locale]["authored_game"]
            self.assertEqual(metadata["string_count"], 329)
            self.assertEqual(
                metadata["catalog"],
                catalog_path.relative_to(ROOT).as_posix(),
            )
            corpora = {
                style: set(
                    (ROOT / f"fonts/cjk/corpora/{locale}.{style}.txt").read_text(
                        encoding="utf-8"
                    )
                )
                for style in ("system", "talk")
            }
            authored_scalars = {
                character
                for text in catalog["strings"].values()
                for character in text
                if ord(character) > 0x7F and not character.isspace()
            }
            self.assertTrue(authored_scalars)
            self.assertTrue(authored_scalars <= set().union(*corpora.values()))
            compact_manifest = json.loads(
                (ROOT / "graphics/fonts/cjk/manifest.json").read_text()
            )
            for style in ("system", "talk"):
                asset = compact_manifest["assets"][f"{locale}.{style}"]
                codepoints = (
                    ROOT / asset["codepoints"]["path"]
                ).read_bytes()
                values = set(
                    struct.unpack(
                        f"<{asset['glyph_count']}I",
                        codepoints,
                    )
                )
                self.assertEqual(values, set(map(ord, corpora[style])))

    def test_inventory_counts_tokens_and_spacing_contract(self):
        inventory = json.loads((ROOT / "fonts/cjk/inventory.json").read_text())
        self.assertEqual(inventory["union"]["spacing_scalars"], ["U+3000"])
        locale_scalars = {}
        for locale in ("ja", "zh-Hans"):
            record = inventory["locales"][locale]
            expected_source_count = (
                record["glyph_scalar_count"]
                + len(record["spacing_scalars"])
                + len(record["nonrendering_scalars"])
            )
            self.assertEqual(
                record["source_non_ascii_scalar_count"],
                expected_source_count,
            )
            for style in ("system", "talk"):
                corpus = (
                    ROOT / f"fonts/cjk/corpora/{locale}.{style}.txt"
                ).read_text()
                self.assertTrue(corpus)
                self.assertFalse(any(character.isspace() for character in corpus))
                self.assertEqual(
                    tuple(map(ord, corpus)),
                    tuple(sorted(set(map(ord, corpus)))),
                )
                self.assertNotIn("[CTRL:", corpus)
                self.assertEqual(
                    len(corpus), record["styles"][style]["glyph_scalar_count"]
                )
            locale_scalars[locale] = set().union(
                *(
                    set(
                        map(
                            ord,
                            (ROOT / f"fonts/cjk/corpora/{locale}.{style}.txt").read_text(),
                        )
                    )
                    for style in ("system", "talk")
                )
            )
            self.assertNotEqual(
                (ROOT / f"fonts/cjk/corpora/{locale}.system.txt").read_bytes(),
                (ROOT / f"fonts/cjk/corpora/{locale}.talk.txt").read_bytes(),
            )
            usage = record["runtime_usage"]
            expected_record_count = (
                usage["game_target_count"]
                + record["expansion"]["active_key_count"]
                + 2
            )
            self.assertEqual(usage["record_count"], expected_record_count)
            self.assertEqual(
                usage["expansion_key_count"],
                record["expansion"]["active_key_count"],
            )
            self.assertEqual(usage["unclassified_count"], 0)
            self.assertGreater(usage["classifications"]["both"], 0)

        union = set(map(ord, (ROOT / "fonts/cjk/corpora/union.txt").read_text()))
        self.assertEqual(union, set().union(*locale_scalars.values()))
        self.assertEqual(len(union), inventory["union"]["glyph_scalar_count"])
        self.assertEqual(
            inventory["union"]["source_non_ascii_scalar_count"],
            inventory["union"]["glyph_scalar_count"]
            + len(inventory["union"]["spacing_scalars"]),
        )
        self.assertIn(0x5019, locale_scalars["ja"])
        self.assertIn(0x5019, locale_scalars["zh-Hans"])
        self.assertIn(0x8A3A, locale_scalars["ja"])
        self.assertIn(0x8BCA, locale_scalars["zh-Hans"])

    def test_runtime_usage_registry_covers_every_string_without_reunion(self):
        usage = json.loads((ROOT / "fonts/cjk/runtime_usage.json").read_text())
        inventory = json.loads((ROOT / "fonts/cjk/inventory.json").read_text())
        self.assertEqual(
            usage["policy"],
            {
                "description_strings_are_both": True,
                "every_supported_runtime_string_is_classified": True,
                "unclassified_strings_are_forbidden": True,
            },
        )
        for locale in ("ja", "zh-Hans"):
            data = usage["locales"][locale]
            expected = inventory["locales"][locale]["runtime_usage"]
            self.assertEqual(len(data["records"]), expected["record_count"])
            self.assertEqual(data["summary"], expected)
            self.assertEqual(data["summary"]["unclassified_count"], 0)
            self.assertTrue(
                all(record["styles"] in (["system"], ["talk"], ["system", "talk"])
                    for record in data["records"])
            )
            by_id = {record["id"]: record for record in data["records"]}
            for target in ("game:0x08BC", "game:0x08BD", "game:0x08D3"):
                self.assertIn("talk", by_id[target]["styles"], (locale, target))
            talk = set(
                (ROOT / f"fonts/cjk/corpora/{locale}.talk.txt").read_text()
            )
            if locale == "zh-Hans":
                self.assertTrue({"售", "周"} <= talk)

    def test_inventory_regeneration_is_byte_identical(self):
        generated = build_generated_files(ROOT)
        for relative_path, expected in generated.items():
            self.assertEqual(
                (ROOT / relative_path).read_bytes(),
                expected,
                relative_path,
            )

    def test_font_identity_license_and_hash_pins(self):
        sources = json.loads((ROOT / "fonts/cjk/font-sources.json").read_text())
        self.assertEqual(sources["license"]["license_id"], "OFL-1.1")
        for locale, expected in FONT_SOURCES.items():
            path = ROOT / expected["path"]
            data = path.read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), expected["sha256"])
            self.assertEqual(len(data), expected["byte_length"])
            identity = read_sfnt_identity(data)
            self.assertEqual(identity["family"], expected["family"])
            self.assertEqual(identity["version"], expected["version"])
            self.assertIn("Open Font License", identity["license"])
            self.assertEqual(
                sources["fonts"][locale]["source_url"],
                expected["source_url"],
            )

    def test_compact_asset_validation_is_deterministic(self):
        first = check_compact_assets(ROOT)
        second = check_compact_assets(ROOT)
        self.assertEqual(first, second)

    def test_inventory_provenance_refresh_requires_unchanged_font_oracle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "fonts/cjk/reports").mkdir(parents=True)
            shutil.copytree(
                ROOT / "fonts/cjk/corpora",
                root / "fonts/cjk/corpora",
            )
            shutil.copytree(
                ROOT / "fonts/cjk/febuilder-baseline",
                root / "fonts/cjk/febuilder-baseline",
            )
            for relative_path in (
                "fonts/cjk/inventory.json",
                "fonts/cjk/febuilder-manifest.json",
                FEHBUILDER_BASELINE_MANIFEST,
                FEHBUILDER_BASELINE_MANIFEST,
                "fonts/cjk/reports/febuilder-generation-report.json",
                "fonts/cjk/reports/febuilder-gates.json",
                FEHRR_SOURCES,
            ):
                shutil.copy2(ROOT / relative_path, root / relative_path)
            shutil.copytree(
                ROOT / "graphics/fonts/cjk",
                root / "graphics/fonts/cjk",
            )

            manifest_path = root / "graphics/fonts/cjk/manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["sources"]["inventory"]["sha256"] = "0" * 64
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )

            refresh_compact_asset_inventory_provenance(root)
            refreshed = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                refreshed["sources"]["inventory"]["sha256"],
                hashlib.sha256(
                    (root / "fonts/cjk/inventory.json").read_bytes()
                ).hexdigest(),
            )

            corpus_path = root / "fonts/cjk/corpora/ja.system.txt"
            corpus_path.write_text(
                corpus_path.read_text(encoding="utf-8") + "追",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                CjkFontError,
                "scalars must be sorted and unique|corpus SHA-256 mismatch",
            ):
                refresh_compact_asset_inventory_provenance(root)

    def test_compact_assets_use_typed_extensions_and_existing_manifest_paths(self):
        manifest = json.loads(
            (ROOT / ASSET_ROOT / "manifest.json").read_text()
        )
        for path in (ROOT / ASSET_ROOT).iterdir():
            self.assertNotEqual(path.suffix, ".bin", path)
        for prefix, asset in manifest["assets"].items():
            expected_suffixes = {
                "codepoints": ".codepoints.u32le",
                "widths": ".widths.u8",
                "bitmap": ".glyphs.2bpp",
            }
            for kind, filename in compact_asset_filenames(prefix).items():
                self.assertTrue(filename.endswith(expected_suffixes[kind]), filename)
                relative_path = f"{ASSET_ROOT}/{filename}"
                self.assertEqual(asset[kind]["path"], relative_path)
                self.assertTrue((ROOT / relative_path).is_file(), relative_path)

    def test_febuilder_gate_counts_follow_current_manifest(self):
        manifest = json.loads(
            (ROOT / FEHBUILDER_BASELINE_MANIFEST).read_text()
        )
        gates = json.loads(
            (ROOT / "fonts/cjk/reports/febuilder-gates.json").read_text()
        )
        report = json.loads(
            (ROOT / "fonts/cjk/reports/febuilder-generation-report.json").read_text()
        )
        expected_rows = sum(job["scalarCount"] for job in report["jobs"])
        for gate in gates["gates"].values():
            self.assertEqual(gate["job_count"], len(manifest["jobs"]))
            self.assertEqual(gate["row_count"], expected_rows)

    def test_aggregate_maps_widths_and_bitmaps_are_valid(self):
        manifest = json.loads(
            (ROOT / "graphics/fonts/cjk/manifest.json").read_text()
        )
        fehrr_lock = json.loads((ROOT / FEHRR_SOURCES).read_text())
        source_scalars = {
            prefix: {
                int(row["scalar"][2:], 16)
                for row in asset["glyphs"]
            }
            for prefix, asset in fehrr_lock["assets"].items()
        }
        self.assertEqual(
            manifest["spacing_scalars"],
            [
                {
                    "advance": 16,
                    "bitmap": None,
                    "locales": ["ja", "zh-Hans"],
                    "runtime_styles": ["system", "talk"],
                    "scalar": "U+3000",
                }
            ],
        )
        payload_total = 0
        aligned_total = 0
        for name, asset in manifest["assets"].items():
            count = asset["glyph_count"]
            codepoints = (ROOT / asset["codepoints"]["path"]).read_bytes()
            widths = (ROOT / asset["widths"]["path"]).read_bytes()
            glyphs = (ROOT / asset["bitmap"]["path"]).read_bytes()
            values = struct.unpack(f"<{count}I", codepoints)
            self.assertEqual(values, tuple(sorted(set(values))), name)
            self.assertEqual(len(widths), count)
            self.assertTrue(all(1 <= width <= 16 for width in widths), name)
            self.assertEqual(len(glyphs), count * 64)
            for index, scalar in enumerate(values):
                if not any(glyphs[index * 64 : (index + 1) * 64]):
                    self.assertIn(scalar, source_scalars[name], (name, scalar))
            for kind in ("codepoints", "widths", "bitmap"):
                data = (ROOT / asset[kind]["path"]).read_bytes()
                payload_total += len(data)
                aligned_total += (len(data) + 3) & ~3
                self.assertEqual(
                    hashlib.sha256(data).hexdigest(),
                    asset[kind]["sha256"],
                )
        self.assertEqual(manifest["rom_budget"]["payload_bytes"], payload_total)
        self.assertEqual(
            manifest["rom_budget"]["four_byte_aligned_blob_bytes"],
            aligned_total,
        )

    def test_fehrr_priority_lock_preserves_source_widths_and_fallbacks(self):
        manifest = json.loads((ROOT / "graphics/fonts/cjk/manifest.json").read_text())
        lock_data = (ROOT / FEHRR_SOURCES).read_bytes()
        lock = json.loads(lock_data)
        priority = manifest["sources"]["fehrr_priority"]
        self.assertEqual(priority["path"], FEHRR_SOURCES)
        self.assertEqual(priority["sha256"], hashlib.sha256(lock_data).hexdigest())
        self.assertEqual(
            priority["policy"],
            (
                "same-game same-style FEHRR glyph first; same-game cross-style "
                "FEHRR glyph second; pinned FEHRR supplemental style tier third; "
                "verified FEBuilder baseline fallback last"
            ),
        )
        self.assertEqual(lock["source"]["repository"], "https://github.com/laqieer/FEHRR.git")
        self.assertEqual(len(lock["source"]["commit"]), 40)
        self.assertEqual(
            lock["selection_policy"],
            priority["policy"],
        )
        for name, asset in lock["assets"].items():
            counts = asset["selection_counts"]
            self.assertEqual(
                asset["glyph_count"],
                counts["same_game_same_style"]
                + counts["same_game_cross_style"]
                + counts["fehrr_supplemental"],
            )
            self.assertEqual(
                asset["glyph_count"] + counts["febuilder_fallback"],
                manifest["assets"][name]["glyph_count"],
            )
            self.assertGreaterEqual(counts["same_game_cross_style"], 0)
            self.assertEqual(
                len(asset["febuilder_fallbacks"]),
                counts["febuilder_fallback"],
            )
        self.assertEqual(
            manifest["assets"]["zh-Hans.talk"]["source_priority"][
                "febuilder_fallback"
            ],
            1,
        )
        self.assertEqual(
            lock["assets"]["zh-Hans.talk"]["febuilder_fallbacks"],
            [
                {
                    "reason": "absent from configured FEHRR style tiers",
                    "scalar": "U+FF05",
                }
            ],
        )
        supplemental = {
            row["scalar"]
            for asset in lock["assets"].values()
            for row in asset["glyphs"]
            if row["selection_kind"] == "fehrr_supplemental"
        }
        self.assertIn("U+7FD4", supplemental)  # 翔
        self.assertIn("U+8BCA", supplemental)  # 诊
        ja_talk_middle_dot = next(
            row
            for row in lock["assets"]["ja.talk"]["glyphs"]
            if row["scalar"] == "U+30FB"
        )
        self.assertEqual(ja_talk_middle_dot["selection_kind"], "same_game_cross_style")
        self.assertEqual(
            ja_talk_middle_dot["filename"],
            "glyph/fe8j/FontItem_E383BB.png",
        )
        self.assertIn(
            {
                "filename": "FontText_E383BB.png",
                "ignored_line": 3627,
                "ignored_width": 1,
                "scalar": "U+30FB",
                "selected_line": 1758,
                "selected_width": 7,
                "source_locale": "fe8j",
                "source_style": "text",
            },
            lock["duplicate_width_resolution"]["conflicts"],
        )
        self.assertEqual(check_compact_assets(ROOT), check_compact_assets(ROOT))

    def test_font_domain_contains_no_committed_package_archive(self):
        tracked = subprocess.run(
            (
                "git",
                "ls-files",
                "-z",
                "--",
                "fonts/cjk",
                "graphics/fonts/cjk",
                "scripts/fonttools/cjk",
                "cjk_fonts.mk",
                "docs/cjk_fonts.md",
            ),
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.decode("utf-8")
        for relative_path in filter(None, tracked.split("\0")):
            path = ROOT / relative_path
            self.assertNotEqual(path.suffix.lower(), ".zip", path)
            with path.open("rb") as source:
                signature = source.read(4)
            self.assertNotIn(
                signature,
                (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
                path,
            )

    def test_package_zip_writer_is_byte_deterministic(self):
        self.SCRATCH.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".archive_",
            dir=self.SCRATCH,
        ) as temporary:
            base = Path(temporary)
            package = base / "package"
            package.mkdir()
            (package / "b.bin").write_bytes(b"b")
            (package / "a.bin").write_bytes(b"a")
            first = archive_package(package, base / "first.zip")
            second = archive_package(package, base / "second.zip")
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
