"""Closed contract tests for issue #177 metadata summary continuity."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from scripts.workflow_pilot import (
    summary_continuity_contract,
)


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
    lines = step.split("\n")
    run_index = lines.index("      run: |")
    return "\n".join(
        line[8:] if line else ""
        for line in lines[run_index + 1 :]
        if not line or line.startswith("        ")
    ) + "\n"


class SummaryContinuityContractTests(unittest.TestCase):
    def _summary_script(self) -> str:
        text = WORKFLOW.read_text(encoding="utf-8")
        return _literal_run_script(_step_blocks(_job_blocks(text)["summary"])[0])

    def test_real_workflow_summary_matches_reviewed_contract(self):
        summary_continuity_contract.validate_summary_continuity_script(
            self._summary_script()
        )

    def test_raw_identity_rejects_nonsemantic_whitespace_and_comment_drift(self):
        script = self._summary_script()
        source = summary_continuity_contract.summary_continuity_python_source(script)
        whitespace_mutated = script.replace("  exit 0\n", "  exit 0   \n", 1)
        comment_mutated = script.replace(
            source,
            source.replace(
                "  import sys\n",
                "  import sys\n  # lexical drift\n",
                1,
            ),
            1,
        )
        summary_continuity_contract.validate_summary_continuity_python(
            summary_continuity_contract.summary_continuity_python_source(comment_mutated)
        )
        for mutated in (whitespace_mutated, comment_mutated):
            with self.subTest(mutated=repr(mutated[:48])):
                with self.assertRaisesRegex(
                    ValueError,
                    "raw identity differs from the reviewed contract",
                ):
                    summary_continuity_contract.validate_summary_continuity_script(
                        mutated
                    )

    def test_python_source_extraction_requires_reviewed_wrapper(self):
        script = self._summary_script()
        wrapper_mutations = (
            script.replace("def main():\n", "def reviewed_main():\n", 1),
            script.replace("main()\n", "run()\n", 1),
            script.replace(
                "  /usr/bin/python3 -I -S - <<'PY' || exit 1\n",
                "  /usr/bin/python3 -I -S - <<'PY'\n",
                1,
            ),
        )
        for mutated in wrapper_mutations:
            with self.subTest(mutated=mutated.splitlines()[0]):
                with self.assertRaisesRegex(
                    ValueError,
                    "wrapper differs|introducer differs",
                ):
                    summary_continuity_contract.summary_continuity_python_source(
                        mutated
                    )

    def test_python_validator_rejects_semantic_drift_and_from_imports(self):
        source = summary_continuity_contract.summary_continuity_python_source(
            self._summary_script()
        )
        semantic_drift = source.replace("MAX_RUN_PAGES = 5", "MAX_RUN_PAGES = 6", 1)
        from_import = source.replace(
            "  import urllib.request\n",
            "  from urllib import request\n",
            1,
        )
        with self.assertRaisesRegex(ValueError, "AST differs"):
            summary_continuity_contract.validate_summary_continuity_python(
                semantic_drift
            )
        with self.assertRaisesRegex(ValueError, "must not use from-imports"):
            summary_continuity_contract.validate_summary_continuity_python(
                from_import
            )

    def test_python_source_ascii_boundary_rejects_unicode_and_controls(self):
        cases = (
            "def main():\n  pass\u00a0\nmain()\n",
            "\ufeffdef main():\n  pass\nmain()\n",
            "def main():\n  pass\u2028\nmain()\n",
            "def main():\n  pass\r\nmain()\n",
            "def main():\n  pass\x00\nmain()\n",
        )
        for source in cases:
            with self.subTest(source=repr(source)):
                with self.assertRaisesRegex(
                    ValueError,
                    "must be ASCII|unsupported control byte",
                ):
                    summary_continuity_contract.validate_summary_continuity_python(
                        source
                    )


if __name__ == "__main__":
    unittest.main()
