import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "audit.py"
FIXTURE = HERE / "fixtures" / "repo"
SPEC = importlib.util.spec_from_file_location("modernize_audit", SCRIPT)
audit = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(audit)


class AuditTests(unittest.TestCase):
    def copy_fixture(self, destination: Path) -> Path:
        root = destination / "repo"
        shutil.copytree(FIXTURE, root)
        (root / "scripts" / "modernize").mkdir(parents=True)
        (root / "scripts" / "modernize" / "decisions.json").write_text(
            '{"schema_version": 1, "decisions": []}\n', encoding="utf-8"
        )
        return root

    def test_all_scanner_classes_and_narrow_suppressions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_fixture(Path(temporary))
            inventory = audit.scan_repository(
                root, root / "scripts" / "modernize" / "decisions.json"
            )

        categories = set(inventory["summary"]["by_category"])
        self.assertEqual(categories, set(audit.CATEGORY_META))
        evidence = [finding["evidence"] for finding in inventory["findings"]]
        self.assertNotIn("Documentation only: 0x08ABCDEF", evidence)
        naked = [
            finding
            for finding in inventory["findings"]
            if finding["category"] == "naked-function"
        ]
        self.assertEqual(len(naked), 1)
        mismatched = [
            finding
            for finding in inventory["findings"]
            if finding["category"] == "mismatched-signature"
        ]
        self.assertEqual(len(mismatched), 1)
        inline_asm = [
            finding
            for finding in inventory["findings"]
            if finding["category"] == "inline-asm"
        ]
        self.assertEqual(len(inline_asm), 1)
        fixed_bases = [
            finding
            for finding in inventory["findings"]
            if finding["category"] == "fixed-rom-base"
        ]
        self.assertTrue(
            any("arm_compressing_linker" in item["evidence"] for item in fixed_bases)
        )
        self.assertFalse(any(item["file"] == "scripts/tool.py" for item in fixed_bases))
        self.assertFalse(
            any(
                item["file"] == "asm/helper.s"
                for item in inventory["findings"]
                if item["category"] == "raw-rom-address"
            )
        )

    def test_output_is_deterministic_relative_and_sorted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_fixture(Path(temporary))
            decisions = root / "scripts" / "modernize" / "decisions.json"
            first = audit.scan_repository(root, decisions)
            second = audit.scan_repository(root, decisions)

        self.assertEqual(audit.json_bytes(first), audit.json_bytes(second))
        self.assertEqual(audit.markdown_text(first), audit.markdown_text(second))
        rendered = audit.json_bytes(first).decode("utf-8")
        self.assertNotIn(str(root), rendered)
        self.assertNotIn("generated_at", rendered)
        keys = [
            (item["detected_category"], item["file"], item["line"], item["evidence"])
            for item in first["findings"]
        ]
        self.assertEqual(keys, sorted(keys))

    def test_check_passes_and_detects_report_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_fixture(Path(temporary))
            common = ["--root", str(root)]
            self.assertEqual(audit.main(common), 0)
            self.assertEqual(audit.main([*common, "--check"]), 0)
            report = root / "reports" / "modernize" / "inventory.json"
            report.write_bytes(report.read_bytes() + b" ")
            self.assertEqual(audit.main([*common, "--check"]), 1)

    def test_decisions_remain_visible_and_can_reclassify(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.copy_fixture(Path(temporary))
            decision_path = root / "scripts" / "modernize" / "decisions.json"
            initial = audit.scan_repository(root, decision_path)
            target = next(
                item
                for item in initial["findings"]
                if item["category"] == "inline-asm"
            )
            decision_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "decisions": [
                            {
                                "id": target["id"],
                                "status": "reclassified",
                                "category": "platform-call",
                                "disposition": "typed-asm-boundary",
                                "reason": "Reviewed BIOS boundary",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            inventory = audit.scan_repository(root, decision_path)

        updated = next(item for item in inventory["findings"] if item["id"] == target["id"])
        self.assertEqual(updated["detected_category"], "inline-asm")
        self.assertEqual(updated["category"], "platform-call")
        self.assertEqual(updated["status"], "reclassified")
        self.assertTrue(inventory["decision_ledger"][0]["active"])


if __name__ == "__main__":
    unittest.main()
