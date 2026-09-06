"""Closed parsed contract for issue #177 metadata summary continuity."""

from __future__ import annotations

import ast
import hashlib

from . import metadata_adapter_contract


_EXPECTED_HEREDOC_INTRODUCER = "  /usr/bin/python3 -I -S - <<'PY' || exit 1"
_EXPECTED_MAIN_DECLARATION = "def main():"
_EXPECTED_MAIN_CALL = "main()"
_PYTHON_SEMANTIC_SHA256 = "4e8a789e3ea7e173df42d1e8c1ee53a78041f4d083d93bb88250adc507f5361f"
# Security-boundary static-contract exception: Bash lexical details such as
# quoting, continuation, and heredoc termination are still hashed exactly as
# defense in depth because reproducing shell tokenization safely without
# execution is incomplete. Parsed Python AST semantics remain the primary
# independent proof that the reviewed no-checkout continuity logic is unchanged.
_RAW_SCRIPT_SHA256 = "f538e716877d2a6699748afd60c55898aa3ee4fcc57ab4f1efdb4efd6409101b"
MAX_PYTHON_SOURCE_BYTES = 32768


def _raw_script_sha256(script: str) -> str:
    # YAML's literal "|" run scalar clips trailing line breaks to one.
    return hashlib.sha256(
        metadata_adapter_contract._require_ascii_boundary(
            script.rstrip("\n") + "\n",
            label="metadata summary shell",
        )
    ).hexdigest()


def summary_continuity_python_source(script: str) -> str:
    metadata_adapter_contract._require_ascii_boundary(
        script,
        label="metadata summary shell",
    )
    lines = script.split("\n")
    if lines.count(_EXPECTED_HEREDOC_INTRODUCER) != 1:
        raise ValueError(
            "metadata summary shell heredoc introducer differs from the reviewed contract"
        )
    opener_index = lines.index(_EXPECTED_HEREDOC_INTRODUCER)
    try:
        terminator_index = lines.index("PY", opener_index + 1)
    except ValueError as error:
        raise ValueError("metadata summary shell heredoc is unterminated") from error
    if any(line == "PY" for line in lines[opener_index + 1 : terminator_index]):
        raise ValueError("metadata summary shell heredoc is malformed")
    if terminator_index <= opener_index + 2:
        raise ValueError("metadata summary shell Python block is empty")
    if lines[opener_index + 1] != _EXPECTED_MAIN_DECLARATION:
        raise ValueError(
            "metadata summary shell Python wrapper differs from the reviewed contract"
        )
    if lines[terminator_index - 1] != _EXPECTED_MAIN_CALL:
        raise ValueError(
            "metadata summary shell Python wrapper differs from the reviewed contract"
        )
    return "\n".join(lines[opener_index + 1 : terminator_index]) + "\n"


def validate_summary_continuity_python(source: str) -> None:
    source_bytes = metadata_adapter_contract._require_ascii_boundary(
        source,
        label="metadata summary Python source",
    )
    if len(source_bytes) > MAX_PYTHON_SOURCE_BYTES:
        raise ValueError("metadata summary Python source exceeds size limit")
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise ValueError(f"metadata summary Python is invalid: {error}") from error
    except RecursionError as error:
        raise ValueError("metadata summary Python recursion exceeds limits") from error
    if any(isinstance(node, ast.ImportFrom) for node in ast.walk(tree)):
        raise ValueError("metadata summary Python must not use from-imports")
    try:
        digest = metadata_adapter_contract._semantic_ast_digest(tree)
    except RecursionError as error:
        raise ValueError("metadata summary Python AST recursion exceeds limits") from error
    if digest != _PYTHON_SEMANTIC_SHA256:
        raise ValueError("metadata summary Python AST differs from the reviewed contract")


def validate_summary_continuity_script(script: str) -> None:
    if _raw_script_sha256(script) != _RAW_SCRIPT_SHA256:
        raise ValueError(
            "metadata summary shell raw identity differs from the reviewed contract"
        )
    validate_summary_continuity_python(summary_continuity_python_source(script))
