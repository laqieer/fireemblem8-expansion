"""Closed parsed contract for issue #177 metadata continuity adapters."""

from __future__ import annotations

import ast
import hashlib
import shlex
import textwrap
from dataclasses import dataclass


_SHELL_PUNCTUATION = "|;&<>()"
_PYTHON_AST_SHA256 = "43dcc50ebe0f13bce0b1935004dadb13821e2f39b975754e5e1b51f106710e76"
_ALLOWED_IMPORTS = ("decimal", "json", "math", "os", "re", "stat", "sys")


@dataclass(frozen=True)
class ParsedShellCommand:
    tokens: tuple[str, ...]
    heredoc: str | None = None


def _tokenize_shell_command(command: str) -> tuple[str, ...]:
    lexer = shlex.shlex(
        command,
        posix=True,
        punctuation_chars=_SHELL_PUNCTUATION,
    )
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = tuple(lexer)
    if not tokens:
        raise ValueError("metadata adapter shell command is empty")
    return tokens


def _expected_command(command: str, *, heredoc: str | None = None) -> ParsedShellCommand:
    return ParsedShellCommand(tokens=_tokenize_shell_command(command), heredoc=heredoc)


EXPECTED_METADATA_ADAPTER_SHELL_TOKENS = (
    _tokenize_shell_command(
        'if [ "$CLASSIFIER_RESULT" != "success" ] || '
        '[ "$FALLBACK_IDENTITY_RESULT" != "success" ] || '
        '[ "$GITHUB_EVENT_NAME" != "pull_request" ] || '
        '[ "$CLASSIFICATION" != "metadata-only" ] || '
        '[ "$FALLBACK_KIND" != "pull_request" ] || '
        '[ -z "$FALLBACK_SHA" ] || '
        '[ "$FULL_FALLBACK" != "false" ] || [ "$HEAD_VALID" != "true" ] || '
        '[ "$IDENTITY_VALID" != "true" ] || [ "$RUN_EXPENSIVE" != "false" ] || '
        '[ "$EXPECTED_BUILD_SHA" != "$CLASSIFIED_BUILD_SHA" ] || '
        '[ "$FALLBACK_SHA" != "$CLASSIFIED_BUILD_SHA" ]; then'
    ),
    _tokenize_shell_command(
        'echo "metadata-only branch-protection continuity is not authoritative" >&2'
    ),
    _tokenize_shell_command("exit 1"),
    _tokenize_shell_command("fi"),
    _tokenize_shell_command("/usr/bin/python3 -I - <<'PY'"),
)


def parse_metadata_adapter_shell(script: str) -> tuple[ParsedShellCommand, ...]:
    lines = script.splitlines()
    commands = []
    continued = []
    line_index = 0

    while line_index < len(lines):
        line = lines[line_index]
        if not line.strip():
            line_index += 1
            continue

        rstripped = line.rstrip()
        if rstripped.endswith("\\") and rstripped != line:
            raise ValueError(
                "metadata adapter shell has trailing whitespace after a continuation backslash"
            )

        text = line.strip()
        continued.append(text)
        if line.endswith("\\"):
            continued[-1] = continued[-1][:-1].rstrip()
            line_index += 1
            continue

        logical = " ".join(fragment for fragment in continued if fragment)
        continued = []
        tokens = _tokenize_shell_command(logical)

        if "<<" in tokens:
            if tokens.count("<<") != 1:
                raise ValueError("metadata adapter shell heredoc must have one delimiter")
            if tokens[-2] != "<<" or tokens[-1] != "PY":
                raise ValueError("metadata adapter shell heredoc delimiter differs")
            line_index += 1
            heredoc_lines = []
            while line_index < len(lines):
                heredoc_line = lines[line_index]
                if heredoc_line == "PY":
                    break
                heredoc_lines.append(heredoc_line)
                line_index += 1
            else:
                raise ValueError("metadata adapter shell heredoc is unterminated")
            commands.append(
                ParsedShellCommand(
                    tokens=tokens,
                    heredoc="\n".join(heredoc_lines) + "\n",
                )
            )
            line_index += 1
            continue

        commands.append(ParsedShellCommand(tokens=tokens))
        line_index += 1

    if continued:
        raise ValueError("metadata adapter shell ends with a dangling continuation")
    return tuple(commands)


def metadata_adapter_python_source(script: str) -> str:
    commands = parse_metadata_adapter_shell(script)
    if not commands or commands[-1].heredoc is None:
        raise ValueError("metadata adapter shell must end with one Python heredoc")
    return commands[-1].heredoc


def validate_metadata_adapter_python(source: str) -> None:
    parsed_source = textwrap.dedent(source)
    try:
        tree = ast.parse(parsed_source)
    except SyntaxError as error:
        raise ValueError(f"metadata adapter Python is invalid: {error}") from error

    if any(isinstance(node, ast.ImportFrom) for node in ast.walk(tree)):
        raise ValueError("metadata adapter Python must not use from-imports")

    imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    if tuple(imports) != _ALLOWED_IMPORTS:
        raise ValueError("metadata adapter Python imports differ from the reviewed contract")

    # Exact parsed-tree binding is intentional here because this is a named
    # security boundary: even unreachable or no-op nodes would widen the
    # adapter's side-effect surface beyond the reviewed no-checkout contract.
    digest = hashlib.sha256(
        ast.dump(tree, annotate_fields=True, include_attributes=False).encode("utf-8")
    ).hexdigest()
    if digest != _PYTHON_AST_SHA256:
        raise ValueError("metadata adapter Python AST differs from the reviewed contract")


def validate_metadata_adapter_script(script: str) -> None:
    commands = parse_metadata_adapter_shell(script)
    if len(commands) != len(EXPECTED_METADATA_ADAPTER_SHELL_TOKENS):
        raise ValueError("metadata adapter shell differs from the reviewed contract")
    for index, expected in enumerate(EXPECTED_METADATA_ADAPTER_SHELL_TOKENS):
        if commands[index].tokens != expected:
            raise ValueError("metadata adapter shell differs from the reviewed contract")
        if index < len(commands) - 1 and commands[index].heredoc is not None:
            raise ValueError("metadata adapter shell contains an unexpected heredoc")
    if commands[-1].heredoc is None:
        raise ValueError("metadata adapter shell must end with one Python heredoc")
    validate_metadata_adapter_python(commands[-1].heredoc)
