"""Static contract for the master-only Full Matrix workflow."""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "full-matrix.yml"
MASTER_ONLY = "if: ${{ github.ref == 'refs/heads/master' }}"
MASTER_SUMMARY = "if: ${{ github.ref == 'refs/heads/master' && always() }}"


def job_block(text, name):
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [A-Za-z][A-Za-z0-9_-]*:\n|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return "" if match is None else match.group("body")


class FullMatrixWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_matrix_is_manual_and_master_only(self):
        self.assertIn("workflow_dispatch: {}", self.text)
        self.assertNotIn("pull_request:", self.text)
        self.assertNotIn("push:", self.text)

        for job in ("host", "modern", "legacy"):
            with self.subTest(job=job):
                self.assertIn(MASTER_ONLY, job_block(self.text, job))

        self.assertIn(MASTER_SUMMARY, job_block(self.text, "summary"))

    def test_non_master_dispatch_cannot_execute_matrix_lanes(self):
        for job in ("host", "modern", "legacy", "summary"):
            with self.subTest(job=job):
                self.assertIn("github.ref == 'refs/heads/master'", job_block(self.text, job))
