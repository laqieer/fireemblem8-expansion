import copy
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.ui_presentation.schema import (
    UiPresentationTableSchema,
    load_records,
    generate_c,
    validate,
)


ROOT = Path(__file__).resolve().parents[3]


class UiPresentationSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = load_records("src/data/ui_presentation.json")

    def test_reference_manifest_validates(self):
        diagnostics = DiagnosticCollector()
        validate(self.records, diagnostics)
        self.assertTrue(diagnostics.ok, diagnostics.render())
        self.assertIn("gExpansionUiPresentationManifest", generate_c(self.records, "source"))

    def test_required_asset_without_id_is_rejected(self):
        records = copy.deepcopy(self.records)
        records[0]["resources"]["required"] = True
        diagnostics = DiagnosticCollector()
        validate(records, diagnostics)
        self.assertFalse(diagnostics.ok)
        self.assertIn("required resources need an asset_id", diagnostics.render())

    def test_resource_budget_overflow_is_rejected(self):
        records = copy.deepcopy(self.records)
        records[0]["resources"]["vram_bytes"] = 0x8001
        diagnostics = DiagnosticCollector()
        validate(records, diagnostics)
        self.assertFalse(diagnostics.ok)
        self.assertIn("VRAM requirement exceeds", diagnostics.render())

    def test_unknown_localization_key_is_rejected(self):
        records = copy.deepcopy(self.records)
        records[0]["title_key"] = "ui.presentation.missing"
        diagnostics = DiagnosticCollector()
        validate(records, diagnostics)
        self.assertFalse(diagnostics.ok)
        self.assertIn("not an active localization id", diagnostics.render())

    def test_fallback_text_escapes_controls_quotes_slashes_and_utf8(self):
        records = copy.deepcopy(self.records)
        records[0]["fallback_text"] = 'line\n\t\x01"\\ café'

        generated = generate_c(records, "fixture")

        self.assertIn(r'line\n\t\001\"\\ caf\303\251', generated)

    def test_embedded_nul_is_rejected_with_actionable_diagnostic(self):
        records = copy.deepcopy(self.records)
        records[0]["fallback_text"] = "Chapter\x00title"
        diagnostics = DiagnosticCollector()

        validate(records, diagnostics)

        self.assertFalse(diagnostics.ok)
        self.assertIn("must not contain NUL", diagnostics.render())

    def test_generated_fixture_compiles_and_round_trips_fallback_text(self):
        records = copy.deepcopy(self.records)
        records[0]["fallback_text"] = 'line\n\t\x01"\\ café'
        artifact_dir = ROOT / "build" / "test-artifacts" / "ui-presentation-schema"
        artifact_dir.mkdir(parents=True, exist_ok=True)

        generated_path = artifact_dir / "fixture.c"
        main_path = artifact_dir / "main.c"
        binary_path = artifact_dir / "fixture"
        generated_path.write_text(generate_c(records, "fixture"), encoding="utf-8")
        main_path.write_text(
            """
#include <string.h>
#include "expansion_ui_presentation.h"

int main(void)
{
    return strcmp(
        gExpansionUiPresentationManifest[0].fallbackText,
    "line\\n\\t\\001\\"\\\\ café") == 0 ? 0 : 1;
}
""",
            encoding="utf-8",
        )

        try:
            subprocess.run(
                [
                    "cc",
                    "-std=c99",
                    "-Iinclude",
                    "-DMODERN",
                    str(generated_path),
                    str(main_path),
                    "-o",
                    str(binary_path),
                ],
                cwd=ROOT,
                check=True,
            )
            subprocess.run([str(binary_path)], cwd=ROOT, check=True)
        finally:
            shutil.rmtree(artifact_dir, ignore_errors=True)

    def test_manifest_has_a_fixed_record_budget(self):
        self.assertEqual(UiPresentationTableSchema.record_budget, 32)

    def test_manifest_accepts_32_records_and_rejects_33(self):
        records = [copy.deepcopy(self.records[0]) for _ in range(32)]
        for index, record in enumerate(records):
            record["id"] = index

        diagnostics = DiagnosticCollector()
        validate(records, diagnostics)
        self.assertTrue(diagnostics.ok, diagnostics.render())
        generated = generate_c(records, "fixture")
        self.assertIn("u8 const gExpansionUiPresentationManifestCount", generated)
        self.assertIn("u8 const gExpansionUiPresentationManifestCount = 32;", generated)

        records.append(copy.deepcopy(records[-1]))
        records[-1]["id"] = 32
        diagnostics = DiagnosticCollector()
        validate(records, diagnostics)
        self.assertFalse(diagnostics.ok)
        self.assertIn("fixed manifest capacity is 32", diagnostics.render())


if __name__ == "__main__":
    unittest.main()
