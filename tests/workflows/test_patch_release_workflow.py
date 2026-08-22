"""Static safety contract for issue #49's trusted patch publisher."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


class PatchReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.patch_job = cls.text.split("\n  patch-release:\n", 1)[1]

    def test_trusted_push_only_and_no_pr_publication(self):
        self.assertIn("github.event_name == 'push'", self.patch_job)
        self.assertIn("github.ref == 'refs/heads/master'", self.patch_job)
        self.assertIn("needs: build", self.patch_job)
        self.assertNotIn("pull_request_target", self.text)
        self.assertEqual(self.text.count("uses: actions/upload-artifact@v7"), 1)

    def test_secret_is_scoped_to_the_trusted_job_only(self):
        self.assertEqual(self.text.count("secrets.BASEROM_URL"), 1)
        self.assertIn("BASEROM_URL: ${{ secrets.BASEROM_URL }}", self.patch_job)
        self.assertNotIn("BASEROM_URL:", self.text.split("\n  patch-release:\n", 1)[0])
        self.assertIn("--proto '=https'", self.patch_job)
        self.assertNotIn("set -x", self.patch_job)

    def test_runner_context_is_scoped_to_steps(self):
        job_header = self.patch_job.split("\n    steps:\n", 1)[0]
        self.assertNotIn("runner.temp", job_header)
        self.assertIn(
            "PATCH_ARTIFACT_DIR: ${{ runner.temp }}/patch-artifact",
            self.patch_job,
        )

    def test_artifact_is_exactly_named_allowlisted_and_retained_for_30_days(self):
        self.assertIn(
            "modern-release-all-locales-all-features-aapcs-bps-${{ github.sha }}",
            self.patch_job,
        )
        self.assertIn("retention-days: 30", self.patch_job)
        self.assertIn("fireemblem8-expansion-all-locales-all-features-aapcs.bps", self.patch_job)
        self.assertIn("manifest.json README.txt", self.patch_job)
        self.assertNotIn("modern-release-aapcs-rom-map", self.text)

    def test_profile_and_local_verifier_are_required_before_upload(self):
        self.assertIn("make expansion-modern-all-locales-all-features-check -j1", self.patch_job)
        self.assertIn("scripts.modernize.patch_release create", self.patch_job)
        self.assertIn("scripts.modernize.patch_release verify", self.patch_job)
        self.assertIn("--commit \"$PATCH_COMMIT\"", self.patch_job)


if __name__ == "__main__":
    unittest.main()
