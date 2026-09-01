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
# Security-boundary static-contract exception: Bash lexical details such as
# quoting, comments, and other raw-tokenization behavior cannot be reproduced
# safely without executing shell code. The reviewed raw adapter bytes are
# therefore hashed exactly before parsed validation as defense in depth, while
# parsed shell structure and version-stable Python AST semantics remain the
# primary independent acceptance evidence.
_RAW_SCRIPT_SHA256 = "1e8865b83119e25f1ffc9e39af27c34532aa3b30b984cee35083c1f40de63b0b"
MAX_PYTHON_SOURCE_BYTES = 16384
MAX_AST_DEPTH = 256
MAX_AST_NODES = 16384
_ALLOWED_AST_FIELDS = {
    "Add": (),
    "And": (),
    "Assign": ("targets", "value", "type_comment"),
    "Attribute": ("value", "attr", "ctx"),
    "AugAssign": ("target", "op", "value"),
    "BinOp": ("left", "op", "right"),
    "BitOr": (),
    "BoolOp": ("op", "values"),
    "Break": (),
    "Call": ("func", "args", "keywords"),
    "Compare": ("left", "ops", "comparators"),
    "Constant": ("value", "kind"),
    "Continue": (),
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
    "IsNot": (),
    "JoinedStr": ("values",),
    "List": ("elts", "ctx"),
    "Load": (),
    "Lt": (),
    "LtE": (),
    "Module": ("body", "type_ignores"),
    "Mult": (),
    "Name": ("id", "ctx"),
    "Not": (),
    "NotEq": (),
    "NotIn": (),
    "Or": (),
    "Raise": ("exc", "cause"),
    "Return": ("value",),
    "Set": ("elts",),
    "Slice": ("lower", "upper", "step"),
    "Store": (),
    "Sub": (),
    "Subscript": ("value", "slice", "ctx"),
    "Try": ("body", "handlers", "orelse", "finalbody"),
    "Tuple": ("elts", "ctx"),
    "UnaryOp": ("op", "operand"),
    "While": ("test", "body", "orelse"),
    "With": ("items", "body", "type_comment"),
    "alias": ("name", "asname"),
    "arg": ("arg", "annotation", "type_comment"),
    "arguments": ("posonlyargs", "args", "vararg", "kwonlyargs", "kw_defaults", "kwarg", "defaults"),
    "comprehension": ("target", "iter", "ifs", "is_async"),
    "keyword": ("arg", "value"),
    "withitem": ("context_expr", "optional_vars"),
}
_EMPTY_COMPATIBILITY_FIELDS = {
    "FunctionDef": ("type_params",),
}


@dataclass(frozen=True)
class ParsedShellCommand:
    tokens: tuple[str, ...]
    raw: str
    heredoc: str | None = None


def _require_ascii_boundary(text: str, *, label: str) -> bytes:
    try:
        data = text.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must be ASCII") from error
    for byte in data:
        if byte == 0x0A:
            continue
        if 0x20 <= byte <= 0x7E:
            continue
        raise ValueError(
            f"{label} contains unsupported control byte 0x{byte:02x}"
        )
    return data


def _ascii_lstrip_space_tab(text: str) -> str:
    index = 0
    while index < len(text) and text[index] in " \t":
        index += 1
    return text[index:]


def _ascii_rstrip_space_tab(text: str) -> str:
    index = len(text)
    while index > 0 and text[index - 1] in " \t":
        index -= 1
    return text[:index]


def _ascii_strip_space_tab(text: str) -> str:
    return _ascii_rstrip_space_tab(_ascii_lstrip_space_tab(text))


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


def _raw_script_sha256(script: str) -> str:
    return hashlib.sha256(
        _require_ascii_boundary(script, label="metadata adapter shell")
    ).hexdigest()

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
    _require_ascii_boundary(script, label="metadata adapter shell")
    lines = script.split("\n")
    commands = []
    continued = []
    line_index = 0

    while line_index < len(lines):
        line = lines[line_index]
        if _ascii_strip_space_tab(line) == "":
            line_index += 1
            continue

        rstripped = _ascii_rstrip_space_tab(line)
        if rstripped.endswith("\\") and rstripped != line:
            raise ValueError(
                "metadata adapter shell has trailing whitespace after a continuation backslash"
            )

        text = _ascii_strip_space_tab(line)
        continued.append(text)
        if line.endswith("\\"):
            continued[-1] = _ascii_rstrip_space_tab(continued[-1][:-1])
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
    source_bytes = _require_ascii_boundary(
        source,
        label="metadata adapter Python source",
    )
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
    if _raw_script_sha256(script) != _RAW_SCRIPT_SHA256:
        raise ValueError(
            "metadata adapter shell raw identity differs from the reviewed contract"
        )
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
