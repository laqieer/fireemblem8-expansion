"""Closed contract tests for issue #177 metadata continuity adapters."""

from __future__ import annotations

import ast
import copy
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import metadata_adapter_contract
from tests.workflows.test_build_ci_topology import _metadata_adapter_scripts


ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


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
        scripts = _metadata_adapter_scripts(text)
        self.assertEqual(len(set(scripts.values())), 1)
        for job_name, script in scripts.items():
            with self.subTest(job=job_name):
                metadata_adapter_contract.validate_metadata_adapter_script(script)

    def test_shell_parser_rejects_trailing_whitespace_after_continuation_backslash(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        script = _metadata_adapter_scripts(text)["host-tests"]
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
                    "trailing whitespace after a continuation backslash|unsupported control byte 0x09",
                ):
                    metadata_adapter_contract.parse_metadata_adapter_shell(mutated)

    def test_raw_identity_rejects_nonsemantic_whitespace_and_comment_drift(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        script = _metadata_adapter_scripts(text)["host-tests"]
        whitespace_mutated = script.replace("fi\n", "fi   \n", 1)
        comment_mutated = script.replace(
            "import sys\n",
            "import sys\n# lexical drift\n",
            1,
        )
        parsed_original = metadata_adapter_contract.parse_metadata_adapter_shell(script)
        parsed_whitespace = metadata_adapter_contract.parse_metadata_adapter_shell(
            whitespace_mutated
        )
        self.assertEqual(
            [command.tokens for command in parsed_whitespace],
            [command.tokens for command in parsed_original],
        )
        metadata_adapter_contract.validate_metadata_adapter_python(
            metadata_adapter_contract.metadata_adapter_python_source(comment_mutated)
        )
        for mutated in (whitespace_mutated, comment_mutated):
            with self.subTest(mutated=repr(mutated[:40])):
                with self.assertRaisesRegex(
                    ValueError,
                    "raw identity differs from the reviewed contract",
                ):
                    metadata_adapter_contract.validate_metadata_adapter_script(mutated)

    def test_shell_parser_rejects_unreviewed_heredoc_introducers(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        script = _metadata_adapter_scripts(text)["host-tests"]
        mutations = (
            "/usr/bin/python3 -I - <<PY\n",
            '/usr/bin/python3 -I - <<"PY"\n',
            "/usr/bin/python3 -I - <<\\PY\n",
            "/usr/bin/python3 -I - <<-'PY'\n",
            "/usr/bin/python3 -I - <<'PY' # trailing\n",
            "/usr/bin/python3 -I - <<'PY' && :\n",
        )
        for introducer in mutations:
            mutated = script.replace("/usr/bin/python3 -I - <<'PY'\n", introducer, 1)
            with self.subTest(introducer=introducer.rstrip()):
                with self.assertRaisesRegex(
                    ValueError,
                    "heredoc introducer differs from the reviewed contract",
                ):
                    metadata_adapter_contract.parse_metadata_adapter_shell(mutated)

    def test_ascii_shell_boundary_rejects_unicode_whitespace_and_controls(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        script = _metadata_adapter_scripts(text)["host-tests"]
        mutations = {
            "nbsp": ('/usr/bin/python3 -I - <<\'PY\'\n', "/usr/bin/python3 -I - <<'PY'\u00a0\n"),
            "em-space": ('/usr/bin/python3 -I - <<\'PY\'\n', "/usr/bin/python3 -I - <<'PY'\u2003\n"),
            "en-space": ('/usr/bin/python3 -I - <<\'PY\'\n', "/usr/bin/python3 -I - <<'PY'\u2002\n"),
            "thin-space": ('/usr/bin/python3 -I - <<\'PY\'\n', "/usr/bin/python3 -I - <<'PY'\u2009\n"),
            "ideographic-space": ('/usr/bin/python3 -I - <<\'PY\'\n', "/usr/bin/python3 -I - <<'PY'\u3000\n"),
            "zero-width-space": ('/usr/bin/python3 -I - <<\'PY\'\n', "/usr/bin/python3 -I - <<'PY'\u200b\n"),
            "bom": ('/usr/bin/python3 -I - <<\'PY\'\n', "\ufeff/usr/bin/python3 -I - <<'PY'\n"),
            "line-separator": ('/usr/bin/python3 -I - <<\'PY\'\n', "/usr/bin/python3 -I - <<'PY'\u2028\n"),
            "paragraph-separator": ('/usr/bin/python3 -I - <<\'PY\'\n', "/usr/bin/python3 -I - <<'PY'\u2029\n"),
            "carriage-return": ('/usr/bin/python3 -I - <<\'PY\'\n', "/usr/bin/python3 -I - <<'PY'\r\n"),
            "tab": ('import sys\n', "\timport sys\n"),
            "escape": ('import sys\n', "import sys\x1b\n"),
            "nul": ('import sys\n', "import sys\x00\n"),
        }
        for name, (old, new) in mutations.items():
            mutated = script.replace(old, new, 1)
            with self.subTest(mutation=name):
                with self.assertRaisesRegex(
                    ValueError,
                    "must be ASCII|unsupported control byte",
                ):
                    metadata_adapter_contract.parse_metadata_adapter_shell(mutated)

    def test_python_validator_rejects_uniform_extra_heredoc_indentation(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        script = _metadata_adapter_scripts(text)["host-tests"]
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
            metadata_adapter_contract.validate_metadata_adapter_python(
                metadata_adapter_contract.metadata_adapter_python_source(mutated)
            )
        with self.assertRaises(IndentationError):
            compile(
                metadata_adapter_contract.metadata_adapter_python_source(mutated),
                "<metadata-adapter-raw>",
                "exec",
            )
        with self.assertRaisesRegex(
            ValueError,
            "raw identity differs from the reviewed contract",
        ):
            metadata_adapter_contract.validate_metadata_adapter_script(mutated)

    def test_unquoted_heredoc_runtime_is_not_equivalent(self):
        quoted = "/usr/bin/python3 -I - <<'PY'\nprint('$MARKER')\nPY\n"
        unquoted = '/usr/bin/python3 -I - <<PY\nprint("$MARKER")\nPY\n'
        environment = {"MARKER": "expanded-value"}
        quoted_result = subprocess.run(
            ["/bin/bash", "-c", quoted],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        unquoted_result = subprocess.run(
            ["/bin/bash", "-c", unquoted],
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(quoted_result.returncode, 0, quoted_result.stderr)
        self.assertEqual(unquoted_result.returncode, 0, unquoted_result.stderr)
        self.assertEqual(quoted_result.stdout.strip(), "$MARKER")
        self.assertEqual(unquoted_result.stdout.strip(), "expanded-value")

    def test_semantic_ast_digest_is_stable_across_empty_type_params_compatibility(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        script = _metadata_adapter_scripts(text)["host-tests"]
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
        script = _metadata_adapter_scripts(text)["host-tests"]
        source = metadata_adapter_contract.metadata_adapter_python_source(script)
        tree = ast.parse(source)
        function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
        class_fields = tuple(ast.FunctionDef._fields)
        original_fields = tuple(getattr(function, "_fields", ()))
        had_type_params = hasattr(function, "type_params")
        original_type_params = getattr(function, "type_params", None)
        try:
            if "type_params" not in original_fields:
                function._fields = original_fields + ("type_params",)
            function.type_params = [ast.Name(id="T", ctx=ast.Load())]
            with self.assertRaisesRegex(
                ValueError,
                "unsupported nonempty compatibility field FunctionDef.type_params",
            ):
                metadata_adapter_contract._semantic_ast_digest(tree)
        finally:
            function._fields = original_fields
            if had_type_params:
                function.type_params = original_type_params
            elif hasattr(function, "type_params"):
                delattr(function, "type_params")
        self.assertEqual(tuple(ast.FunctionDef._fields), class_fields)

    def test_python_source_ascii_boundary_rejects_unicode_and_controls(self):
        cases = (
            "print('x')\u00a0\n",
            "\ufeffprint('x')\n",
            "print('x')\u2028\n",
            "print('x')\r\n",
            "print('x')\x00\n",
        )
        for source in cases:
            with self.subTest(source=repr(source)):
                with self.assertRaisesRegex(
                    ValueError,
                    "must be ASCII|unsupported control byte",
                ):
                    metadata_adapter_contract.validate_metadata_adapter_python(source)

    def test_semantic_ast_depth_limit_accepts_boundary_minus_one_and_rejects_plus_one(self):
        def nested_tree(depth: int) -> ast.Module:
            value: ast.expr = ast.Name(id="value", ctx=ast.Load())
            for _ in range(depth):
                value = ast.Subscript(
                    value=value,
                    slice=ast.Constant(value=0),
                    ctx=ast.Load(),
                )
            return ast.Module(
                body=[
                    ast.Assign(
                        targets=[ast.Name(id="result", ctx=ast.Store())],
                        value=value,
                        type_comment=None,
                    )
                ],
                type_ignores=[],
            )

        last_ok = None
        first_bad = None
        for depth in range(1, metadata_adapter_contract.MAX_AST_DEPTH * 2):
            try:
                metadata_adapter_contract._semantic_ast_digest(nested_tree(depth))
            except ValueError as error:
                if "depth limit" in str(error):
                    first_bad = depth
                    break
                raise
            else:
                last_ok = depth
        self.assertIsNotNone(last_ok)
        self.assertIsNotNone(first_bad)
        self.assertEqual(first_bad, last_ok + 1)

    def test_semantic_ast_node_limit_accepts_boundary_minus_one_and_rejects_plus_one(self):
        def list_tree(count: int) -> ast.Module:
            return ast.Module(
                body=[
                    ast.Assign(
                        targets=[ast.Name(id="result", ctx=ast.Store())],
                        value=ast.Tuple(
                            elts=[ast.Constant(value=0) for _ in range(count)],
                            ctx=ast.Load(),
                        ),
                        type_comment=None,
                    )
                ],
                type_ignores=[],
            )

        low = 1
        high = 1
        while True:
            try:
                metadata_adapter_contract._semantic_ast_digest(list_tree(high))
            except ValueError as error:
                if "node limit" in str(error):
                    break
                raise
            low = high
            high *= 2

        while low + 1 < high:
            mid = (low + high) // 2
            try:
                metadata_adapter_contract._semantic_ast_digest(list_tree(mid))
            except ValueError as error:
                if "node limit" in str(error):
                    high = mid
                else:
                    raise
            else:
                low = mid

        metadata_adapter_contract._semantic_ast_digest(list_tree(low))
        with self.assertRaisesRegex(ValueError, "node limit"):
            metadata_adapter_contract._semantic_ast_digest(list_tree(high))
        self.assertEqual(high, low + 1)

    def test_deep_malformed_python_rejects_normally(self):
        malformed = "result = value" + "[0" * (metadata_adapter_contract.MAX_AST_DEPTH + 32)
        with self.assertRaisesRegex(ValueError, "metadata adapter Python is invalid"):
            metadata_adapter_contract.validate_metadata_adapter_python(malformed)

    def test_validate_python_converts_recursion_errors_to_valueerror(self):
        with mock.patch.object(
            metadata_adapter_contract.ast,
            "parse",
            side_effect=RecursionError("boom"),
        ):
            with self.assertRaisesRegex(ValueError, "recursion exceeds limits"):
                metadata_adapter_contract.validate_metadata_adapter_python("result = 0\n")

        text = WORKFLOW.read_text(encoding="utf-8")
        script = _metadata_adapter_scripts(text)["host-tests"]
        source = metadata_adapter_contract.metadata_adapter_python_source(script)
        with mock.patch.object(
            metadata_adapter_contract,
            "_semantic_ast_digest",
            side_effect=RecursionError("boom"),
        ):
            with self.assertRaisesRegex(ValueError, "AST recursion exceeds limits"):
                metadata_adapter_contract.validate_metadata_adapter_python(source)


if __name__ == "__main__":
    unittest.main()
