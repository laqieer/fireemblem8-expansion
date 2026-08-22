"""Static safety contract for issue #49's trusted patch publisher."""

from __future__ import annotations

import re
import shlex
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


def parse_patch_release_run_commands(workflow: str) -> list[list[list[str]]]:
    """Parse run scalars from the publisher job's YAML sequence structure."""
    job = re.search(
        r"(?ms)^  patch-release:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        workflow,
    )
    if job is None:
        raise AssertionError("workflow must define a jobs.patch-release job")

    steps = job.group("body").split("\n    steps:\n", 1)
    if len(steps) != 2:
        raise AssertionError("publisher job must define a steps sequence")

    lines = steps[1].splitlines()
    step_starts = [
        index for index, line in enumerate(lines) if re.match(r"^    - ", line)
    ]
    commands = []
    for start, end in zip(step_starts, step_starts[1:] + [len(lines)]):
        step = lines[start:end]
        run = None
        for index, line in enumerate(step):
            inline = re.match(r"^    - run: (?P<value>.+)$", line)
            field = re.match(r"^      run: (?P<value>.+)$", line)
            match = inline or field
            if match is None:
                continue
            value = match.group("value")
            if value == "|":
                run = [
                    following[8:]
                    for following in step[index + 1:]
                    if following.startswith("        ")
                ]
            else:
                run = [value]
            break
        if run is not None:
            commands.append([shlex.split(line) for line in run if line])
    return commands


class PatchReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        job = re.search(
            r"(?ms)^  patch-release:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
            cls.text,
        )
        if job is None:
            raise AssertionError("workflow must define a jobs.patch-release job")
        cls.patch_job = job.group("body")

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

    def test_publisher_uses_one_python_interpreter_for_install_and_execution(self):
        install_interpreters = set()
        publisher_interpreters = set()
        for step in parse_patch_release_run_commands(self.text):
            for command in step:
                for index in range(len(command) - 3):
                    if command[index + 1:index + 4] == ["-m", "pip", "install"]:
                        install_interpreters.add(command[index])
                if command[1:3] == ["-m", "scripts.modernize.patch_release"]:
                    publisher_interpreters.add(command[0])

        self.assertEqual(install_interpreters, {"python3"})
        self.assertEqual(publisher_interpreters, {"python3"})
        self.assertEqual(install_interpreters, publisher_interpreters)


if __name__ == "__main__":
    unittest.main()
