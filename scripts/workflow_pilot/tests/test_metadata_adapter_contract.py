"""Closed contract tests for issue #177 metadata continuity adapters."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from scripts.workflow_pilot import metadata_adapter_contract


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


def _job_blocks(text: str) -> dict[str, str]:
    jobs_start = text.index("\njobs:\n") + len("\njobs:\n")
    jobs_text = text[jobs_start:]
    matches = list(
        re.finditer(r"^  (?P<name>[A-Za-z][A-Za-z0-9_-]*):\n", jobs_text, re.MULTILINE)
    )
    return {
        match.group("name"): jobs_text[
            match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(jobs_text)
        ]
        for index, match in enumerate(matches)
    }


def _step_blocks(job: str) -> list[str]:
    matches = list(re.finditer(r"^    -(?:[ \t]|\Z)", job, re.MULTILINE))
    return [
        job[
            match.start() : matches[index + 1].start() if index + 1 < len(matches) else len(job)
        ]
        for index, match in enumerate(matches)
    ]


def _literal_run_script(step: str) -> str:
    lines = step.splitlines()
    run_index = lines.index("      run: |")
    return "\n".join(
        line[8:] if line else ""
        for line in lines[run_index + 1 :]
        if not line or line.startswith("        ")
    ) + "\n"


class MetadataAdapterContractTests(unittest.TestCase):
    def test_real_workflow_adapters_share_the_reviewed_contract(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        jobs = _job_blocks(text)
        scripts = {
            job_name: _literal_run_script(_step_blocks(jobs[job_name])[0])
            for job_name in ("host-tests", "build")
        }
        self.assertEqual(len(set(scripts.values())), 1)
        for job_name, script in scripts.items():
            with self.subTest(job=job_name):
                metadata_adapter_contract.validate_metadata_adapter_script(script)

    def test_shell_parser_rejects_trailing_whitespace_after_continuation_backslash(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        script = _literal_run_script(_step_blocks(_job_blocks(text)["host-tests"])[0])
        mutations = (
            script.replace(
                'if [ "$CLASSIFIER_RESULT" != "success" ] || \\\n',
                'if [ "$CLASSIFIER_RESULT" != "success" ] || \\ \n',
                1,
            ),
            script.replace(
                '   [ "$FALLBACK_IDENTITY_RESULT" != "success" ] || \\\n',
                '   [ "$FALLBACK_IDENTITY_RESULT" != "success" ] || \\\t\n',
                1,
            ),
            script.replace(
                '   [ "$GITHUB_EVENT_NAME" != "pull_request" ] || \\\n',
                '   [ "$GITHUB_EVENT_NAME" != "pull_request" ] || \\  \n',
                1,
            ),
        )
        for mutated in mutations:
            with self.subTest(mutated=mutated.splitlines()[0]):
                with self.assertRaisesRegex(
                    ValueError,
                    "trailing whitespace after a continuation backslash",
                ):
                    metadata_adapter_contract.validate_metadata_adapter_script(mutated)

    def test_non_continuation_trailing_whitespace_is_not_semantic(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        script = _literal_run_script(_step_blocks(_job_blocks(text)["host-tests"])[0])
        mutated = script.replace("fi\n", "fi   \n", 1).replace(
            "        import sys\n",
            "        import sys   \n",
            1,
        )
        metadata_adapter_contract.validate_metadata_adapter_script(mutated)


if __name__ == "__main__":
    unittest.main()
