"""Closed parsed contract for issue #177 metadata continuity adapters."""

from __future__ import annotations

import ast
import hashlib
import json
import shlex
from dataclasses import dataclass


_SHELL_PUNCTUATION = "|;&<>()"
_PYTHON_SEMANTIC_SHA256 = "230946bc7f4f3f00e1785cd2056083070ae2c8df3c81d32877447d6e4d858ad3"
_ALLOWED_IMPORTS = ("decimal", "json", "math", "os", "re", "stat", "sys")
_EXPECTED_HEREDOC_INTRODUCER = "/usr/bin/python3 -I - <<'PY'"
MAX_PYTHON_SOURCE_BYTES = 16384
MAX_AST_DEPTH = 256
MAX_AST_NODES = 4096
_ALLOWED_AST_FIELDS = {
    "Add": (),
    "And": (),
    "Assign": ("targets", "value", "type_comment"),
    "Attribute": ("value", "attr", "ctx"),
    "BinOp": ("left", "op", "right"),
    "BitOr": (),
    "BoolOp": ("op", "values"),
    "Break": (),
    "Call": ("func", "args", "keywords"),
    "Compare": ("left", "ops", "comparators"),
    "Constant": ("value", "kind"),
    "Dict": ("keys", "values"),
    "Eq": (),
    "ExceptHandler": ("type", "name", "body"),
    "Expr": ("value",),
    "For": ("target", "iter", "body", "orelse", "type_comment"),
    "FormattedValue": ("value", "conversion", "format_spec"),
    "FunctionDef": (
        "name",
        "args",
        "body",
        "decorator_list",
        "returns",
        "type_comment",
    ),
    "GeneratorExp": ("elt", "generators"),
    "Gt": (),
    "If": ("test", "body", "orelse"),
    "Import": ("names",),
    "In": (),
    "Is": (),
    "JoinedStr": ("values",),
    "Load": (),
    "Lt": (),
    "Module": ("body", "type_ignores"),
    "Name": ("id", "ctx"),
    "Not": (),
    "NotEq": (),
    "NotIn": (),
    "Or": (),
    "Raise": ("exc", "cause"),
    "Return": ("value",),
    "Set": ("elts",),
    "Store": (),
    "Sub": (),
    "Subscript": ("value", "slice", "ctx"),
    "Try": ("body", "handlers", "orelse", "finalbody"),
    "Tuple": ("elts", "ctx"),
    "UnaryOp": ("op", "operand"),
    "While": ("test", "body", "orelse"),
    "alias": ("name", "asname"),
    "arg": ("arg", "annotation", "type_comment"),
    "arguments": ("posonlyargs", "args", "vararg", "kwonlyargs", "kw_defaults", "kwarg", "defaults"),
    "comprehension": ("target", "iter", "ifs", "is_async"),
    "keyword": ("arg", "value"),
}
_EMPTY_COMPATIBILITY_FIELDS = {
    "FunctionDef": ("type_params",),
}


@dataclass(frozen=True)
class ParsedShellCommand:
    tokens: tuple[str, ...]
    raw: str
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
    _tokenize_shell_command(_EXPECTED_HEREDOC_INTRODUCER),
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
            if logical != _EXPECTED_HEREDOC_INTRODUCER:
                raise ValueError(
                    "metadata adapter shell heredoc introducer differs from the reviewed contract"
                )
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
                    raw=logical,
                    heredoc="\n".join(heredoc_lines) + "\n",
                )
            )
            line_index += 1
            continue

        commands.append(ParsedShellCommand(tokens=tokens, raw=logical))
        line_index += 1

    if continued:
        raise ValueError("metadata adapter shell ends with a dangling continuation")
    return tuple(commands)


def metadata_adapter_python_source(script: str) -> str:
    commands = parse_metadata_adapter_shell(script)
    if not commands or commands[-1].heredoc is None:
        raise ValueError("metadata adapter shell must end with one Python heredoc")
    return commands[-1].heredoc


def _normalize_semantic_ast(node: object) -> object:
    state = {"nodes": 0}

    def normalize(current: object, *, depth: int) -> object:
        state["nodes"] += 1
        if state["nodes"] > MAX_AST_NODES:
            raise ValueError("metadata adapter Python AST exceeds node limit")
        if depth > MAX_AST_DEPTH:
            raise ValueError("metadata adapter Python AST exceeds depth limit")

        if isinstance(current, ast.AST):
            node_type = type(current).__name__
            if node_type not in _ALLOWED_AST_FIELDS:
                raise ValueError(
                    f"metadata adapter Python uses unsupported AST node {node_type}"
                )
            required_fields = _ALLOWED_AST_FIELDS[node_type]
            compatibility_fields = _EMPTY_COMPATIBILITY_FIELDS.get(node_type, ())
            available_fields = tuple(getattr(current, "_fields", ()))
            unknown_fields = [
                field
                for field in available_fields
                if field not in required_fields and field not in compatibility_fields
            ]
            if unknown_fields:
                raise ValueError(
                    "metadata adapter Python uses unsupported AST fields "
                    + ", ".join(f"{node_type}.{field}" for field in unknown_fields)
                )
            missing_fields = [
                field for field in required_fields if field not in available_fields
            ]
            if missing_fields:
                raise ValueError(
                    "metadata adapter Python is missing AST fields "
                    + ", ".join(f"{node_type}.{field}" for field in missing_fields)
                )
            for field in compatibility_fields:
                if field not in available_fields:
                    continue
                value = getattr(current, field)
                if not isinstance(value, (list, tuple)) or value:
                    raise ValueError(
                        "metadata adapter Python uses unsupported nonempty compatibility field "
                        f"{node_type}.{field}"
                    )
            return {
                "node": node_type,
                "fields": [
                    [field, normalize(getattr(current, field), depth=depth + 1)]
                    for field in required_fields
                ],
            }
        if isinstance(current, list):
            return [normalize(item, depth=depth + 1) for item in current]
        if isinstance(current, tuple):
            return [normalize(item, depth=depth + 1) for item in current]
        if current is None or isinstance(current, (bool, int, float, str)):
            return current
        raise ValueError(
            f"metadata adapter Python uses unsupported literal {type(current).__name__}"
        )

    try:
        return normalize(node, depth=0)
    except RecursionError as error:
        raise ValueError("metadata adapter Python AST recursion exceeds limits") from error


def _semantic_ast_digest(tree: ast.AST) -> str:
    normalized = _normalize_semantic_ast(tree)
    return hashlib.sha256(
        json.dumps(
            normalized,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def validate_metadata_adapter_python(source: str) -> None:
    try:
        source_bytes = source.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("metadata adapter Python source is not valid UTF-8") from error
    if len(source_bytes) > MAX_PYTHON_SOURCE_BYTES:
        raise ValueError("metadata adapter Python source exceeds size limit")
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise ValueError(f"metadata adapter Python is invalid: {error}") from error
    except RecursionError as error:
        raise ValueError("metadata adapter Python recursion exceeds limits") from error

    if any(isinstance(node, ast.ImportFrom) for node in ast.walk(tree)):
        raise ValueError("metadata adapter Python must not use from-imports")

    imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
    if tuple(imports) != _ALLOWED_IMPORTS:
        raise ValueError("metadata adapter Python imports differ from the reviewed contract")

    # Exact semantic-tree binding is intentional here because this is a named
    # security boundary: even unreachable or no-op nodes would widen the
    # adapter's side-effect surface beyond the reviewed no-checkout contract.
    try:
        digest = _semantic_ast_digest(tree)
    except RecursionError as error:
        raise ValueError("metadata adapter Python AST recursion exceeds limits") from error
    if digest != _PYTHON_SEMANTIC_SHA256:
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
