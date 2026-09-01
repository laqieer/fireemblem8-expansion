"""Closed contract tests for issue #177 metadata continuity adapters."""

from __future__ import annotations

import ast
import copy
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


def _legacy_ast_clone(node: object) -> object:
    if isinstance(node, ast.AST):
        fields = list(getattr(node, "_fields", ()))
        if type(node).__name__ == "FunctionDef" and "type_params" in fields:
            fields.remove("type_params")
        clone_type = type(type(node).__name__, (ast.AST,), {"_fields": tuple(fields)})
        clone = clone_type()
        for field in fields:
            setattr(clone, field, _legacy_ast_clone(getattr(node, field)))
        return clone
    if isinstance(node, list):
        return [_legacy_ast_clone(item) for item in node]
    return copy.deepcopy(node)


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

    def test_python_validator_rejects_uniform_extra_heredoc_indentation(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        script = _literal_run_script(_step_blocks(_job_blocks(text)["host-tests"])[0])
        source = metadata_adapter_contract.metadata_adapter_python_source(script)
        mutated = script.replace(
            source,
            "".join(
                f" {line}\n" if line else "\n"
                for line in source.splitlines()
            ),
            1,
        )
        with self.assertRaisesRegex(ValueError, "metadata adapter Python is invalid"):
            metadata_adapter_contract.validate_metadata_adapter_script(mutated)
        with self.assertRaises(IndentationError):
            compile(
                metadata_adapter_contract.metadata_adapter_python_source(mutated),
                "<metadata-adapter-raw>",
                "exec",
            )

    def test_semantic_ast_digest_is_stable_across_empty_type_params_compatibility(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        script = _literal_run_script(_step_blocks(_job_blocks(text)["host-tests"])[0])
        source = metadata_adapter_contract.metadata_adapter_python_source(script)
        tree = ast.parse(source)
        legacy = _legacy_ast_clone(tree)
        self.assertEqual(
            metadata_adapter_contract._normalize_semantic_ast(tree),
            metadata_adapter_contract._normalize_semantic_ast(legacy),
        )
        self.assertEqual(
            metadata_adapter_contract._semantic_ast_digest(tree),
            metadata_adapter_contract._semantic_ast_digest(legacy),
        )

    def test_semantic_ast_rejects_nonempty_compatibility_fields(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        script = _literal_run_script(_step_blocks(_job_blocks(text)["host-tests"])[0])
        source = metadata_adapter_contract.metadata_adapter_python_source(script)
        tree = ast.parse(source)
        function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
        function.type_params = [ast.Name(id="T", ctx=ast.Load())]
        with self.assertRaisesRegex(
            ValueError,
            "unsupported nonempty compatibility field FunctionDef.type_params",
        ):
            metadata_adapter_contract._semantic_ast_digest(tree)


if __name__ == "__main__":
    unittest.main()
