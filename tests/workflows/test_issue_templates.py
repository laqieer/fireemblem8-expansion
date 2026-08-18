"""Static contract tests for repository issue forms."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_ROOT = ROOT / ".github" / "ISSUE_TEMPLATE"
FEATURE = TEMPLATE_ROOT / "feature_request.yml"
BUG = TEMPLATE_ROOT / "bug_report.yml"
CONFIG = TEMPLATE_ROOT / "config.yml"
ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
BLOCK_RE = re.compile(r"(?m)^  - type: ")


def body_blocks(text: str) -> list[str]:
    parts = BLOCK_RE.split(text)
    return [part for part in parts[1:] if part.strip()]


def block_id(block: str) -> str | None:
    match = re.search(r"(?m)^    id: ([a-z0-9_]+)$", block)
    return match.group(1) if match else None


def fields(text: str) -> dict[str, str]:
    result = {}
    for block in body_blocks(text):
        field_id = block_id(block)
        if field_id is not None:
            result[field_id] = block
    return result


class IssueTemplateTests(unittest.TestCase):
    def test_forms_have_required_top_level_shape_and_unique_ids(self):
        for path in (FEATURE, BUG):
            with self.subTest(path=path):
                text = path.read_text(encoding="utf-8")
                self.assertRegex(text, r"(?m)^name: .+$")
                self.assertRegex(text, r"(?m)^description: .+$")
                self.assertIn("\nbody:\n", text)

                ids = [field_id for block in body_blocks(text) if (field_id := block_id(block))]
                self.assertEqual(len(ids), len(set(ids)))
                self.assertTrue(all(ID_RE.fullmatch(field_id) for field_id in ids))

    def test_feature_form_requires_review_contract(self):
        text = FEATURE.read_text(encoding="utf-8")
        form_fields = fields(text)
        required = {
            "feature_description",
            "request_reason",
            "use_cases",
            "dependencies_conflicts",
            "configuration",
            "acceptance_criteria",
            "submission_checks",
        }
        self.assertTrue(required.issubset(form_fields))
        for field_id in required - {"submission_checks"}:
            with self.subTest(field_id=field_id):
                self.assertIn("required: true", form_fields[field_id])
        self.assertIn('labels: ["enhancement"]', text)

    def test_bug_form_requires_triage_evidence_and_screenshots(self):
        text = BUG.read_text(encoding="utf-8")
        form_fields = fields(text)
        required = {
            "commit",
            "build_config",
            "environment",
            "reproduction",
            "expected",
            "actual",
            "screenshots",
            "submission_checks",
        }
        self.assertTrue(required.issubset(form_fields))
        for field_id in required - {"submission_checks"}:
            with self.subTest(field_id=field_id):
                self.assertIn("required: true", form_fields[field_id])
        self.assertTrue(form_fields["screenshots"].startswith("upload\n"))
        self.assertIn('labels: ["bug"]', text)

    def test_template_chooser_routes_unstructured_requests_to_discussions(self):
        text = CONFIG.read_text(encoding="utf-8")
        self.assertIn("blank_issues_enabled: false", text)
        self.assertIn(
            "https://github.com/laqieer/fireemblem8-expansion/discussions",
            text,
        )


if __name__ == "__main__":
    unittest.main()
