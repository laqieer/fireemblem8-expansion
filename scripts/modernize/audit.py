#!/usr/bin/env python3
"""Deterministic inventory of FE8 constructs that block a modern-only compiler."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Iterable, NamedTuple


SCHEMA_VERSION = 3
DEFAULT_JSON = "reports/modernize/inventory.json"
DEFAULT_MARKDOWN = "reports/modernize/inventory.md"
DEFAULT_DECISIONS = "scripts/modernize/decisions.json"

# These are output/tool sources, not migration inputs. Excluding only these two
# prefixes prevents the scanner's pattern definitions and generated report from
# recursively reporting themselves.
EXCLUDED_PREFIXES = ("scripts/modernize/", "reports/modernize/")

SOURCE_PREFIXES = ("src/", "include/", "asm/", "data/", "banim/", "sound/", "graphics/")
C_EXTENSIONS = {".c", ".h"}
ASM_EXTENSIONS = {".s", ".S", ".inc"}
SOURCE_EXTENSIONS = C_EXTENSIONS | ASM_EXTENSIONS
BUILD_SUFFIXES = {".mk", ".py", ".sh", ".yml", ".yaml"}
BUILD_BASENAMES = {
    "Makefile",
    "makefile",
    "GNUmakefile",
    "build_tools.sh",
    "clean_tools.sh",
    "buddy.yml",
}
LINKER_BASENAMES = {
    "ldscript.txt",
    "linker_script_banim.txt",
    "linker_script_sound.txt",
    "sym_iwram.txt",
}

SUPPRESSION_RULES = [
    "Ignore scripts/modernize and reports/modernize to prevent scanner/report self-matches.",
    "Inspect compiler-facing C only under src/ and include/; vendored host tools are not target ABI blockers.",
    "Ignore C and GNU ARM @ comment ROM literals while retaining literals used by executable or build syntax.",
    "Ignore marker macro definitions for NAKEDFUNC and MISMATCHED_SIGNATURE; report their call sites instead.",
    "Ignore SHOULD_BE_CONST because it expands to nothing and creates no compiler or placement constraint.",
    "Treat CP932 as compiler-pipeline debt only when a build recipe couples it to agbcc/CC1; asset and text decoding are data-format concerns.",
    "Require pointer/address syntax for ROM-range source literals; ordinary fill values and integer data are not addresses.",
    "Require declaration shape for bitfields so ternary ?: expressions are never treated as layout declarations.",
    "Report one assembly-boundary finding per tracked .s/.S/.inc file rather than every assembly directive.",
    "Recognize old-style declarations only when a src/include line has declaration shape and an empty parameter list.",
]

CATEGORY_META = {
    "register-pinned-local": ("high", "P0", "rewrite-c"),
    "inline-asm": ("high", "P0", "typed-asm-boundary"),
    "inline-asm-barrier": ("high", "P0", "rewrite-c"),
    "naked-function": ("critical", "P0", "typed-asm-boundary"),
    "old-style-function-declaration": ("high", "P0", "rewrite-c"),
    "old-style-function-definition": ("high", "P0", "rewrite-c"),
    "mismatched-signature": ("critical", "P0", "rewrite-c"),
    "layout-sensitive-struct": ("high", "P1", "layout-assert"),
    "forced-data-placement": ("medium", "P1", "linker-migrate"),
    "raw-rom-address": ("critical", "P0", "rewrite-c"),
    "modern-conditional": ("high", "P0", "rewrite-c"),
    "legacy-compiler-pipeline": ("critical", "P0", "toolchain-migrate"),
    "linker-placement-coupling": ("high", "P1", "linker-migrate"),
    "fixed-rom-base": ("critical", "P0", "linker-migrate"),
    "assembly-boundary": ("medium", "P2", "typed-asm-boundary"),
}

REGISTER_RE = re.compile(
    r"\bregister\b[^;\n]*\b(?:asm|__asm__)\s*\(\s*[\"'](?:r(?:1[0-5]|[0-9])|sl|fp|ip|sp|lr)[\"']\s*\)"
)
ASM_RE = re.compile(r"\b(?:asm|__asm__)\s*(?:volatile\s*)?\(")
EMPTY_ASM_RE = re.compile(r"\b(?:asm|__asm__)\s*(?:volatile\s*)?\(\s*\"\"")
NAKED_ATTR_RE = re.compile(r"__attribute__\s*\(\([^)]*\bnaked\b[^)]*\)\)")
ROM_LITERAL = r"0x(?:0[89][0-9A-Fa-f]{6}|[89][0-9A-Fa-f]{6})"
RAW_ROM_RE = re.compile(rf"(?<![0-9A-Za-z_]){ROM_LITERAL}(?![0-9A-Fa-f])")
# Declaration-shaped bitfields only. Requiring a type/declarator sequence from
# the start of the line prevents ``condition ? value : 1;`` from matching.
# The final alternative also covers unnamed integral padding fields (``u8 : 2``).
BITFIELD_RE = re.compile(
    r"^\s*(?:(?:const|volatile|signed|unsigned|struct\s+[A-Za-z_]\w*|"
    r"union\s+[A-Za-z_]\w*|enum\s+[A-Za-z_]\w*|[A-Za-z_]\w*)\s+)+"
    r"(?:[A-Za-z_]\w*\s*)?:\s*(?:\d+|[A-Za-z_]\w*)\s*;"
)
OFFSET_FIELD_RE = re.compile(
    r"/\*\s*(?:0x)?[0-9A-Fa-f]{1,4}\s*\*/[^;{}]*\b[A-Za-z_]\w*(?:\[[^\]]+\])?\s*;"
)
OLD_STYLE_RE = re.compile(
    r"^\s*(?:(?:extern|static|inline|const|volatile|unsigned|signed)\s+)*"
    r"(?:void|char|short|int|long|float|double|bool|[us](?:8|16|32|64)|"
    r"struct\s+[A-Za-z_]\w*|[A-Za-z_]\w*_t|[A-Z][A-Za-z0-9_]*)"
    r"(?:\s+|\s*\*+\s*)[A-Za-z_]\w*\s*\(\s*\)\s*;"
)
FORCED_DATA_RE = re.compile(
    r"\b(?:CONST_DATA|EWRAM_DATA|IWRAM_DATA|EWRAM_OVERLAY)\b"
    r"|__attribute__\s*\(\([^)]*\bsection\s*\("
    r"|\bSECTION\s*\("
)
LEGACY_COMPILER_RE = re.compile(r"\b(?:old_agbcc|agbcc)\b", re.IGNORECASE)
CP932_RE = re.compile(r"\bCP932\b|iconv\s+-f\s+UTF-8\s+-t\s+CP932", re.IGNORECASE)
FIXED_BASE_RE = re.compile(
    r"(?:banim|battle.?animation|arm_compressing_linker).*"
    rf"(?:-b|--base|--section-start|-Ttext)?\s*{ROM_LITERAL}",
    re.IGNORECASE,
)
LINKER_ABSOLUTE_RE = re.compile(
    r"(?:\.\s*=\s*0x[0-9A-Fa-f]+|"
    r"\bORIGIN\s*=\s*0x[0-9A-Fa-f]+|"
    r"^[A-Za-z_]\w*\s*=\s*0x[0-9A-Fa-f]+)"
)
FUNCTION_DECL_RE = re.compile(
    r"^\s*(?:(?:static|inline|extern|const|volatile|unsigned|signed|NAKEDFUNC)\s+)*"
    r"(?:struct\s+[A-Za-z_]\w*|[A-Za-z_]\w*)"
    r"(?:\s+|\s*\*+\s*)([A-Za-z_]\w*)\s*\("
)
NON_FUNCTION_NAMES = {
    "if",
    "for",
    "while",
    "switch",
    "return",
    "sizeof",
    "asm",
    "__asm__",
    "__attribute__",
}
OLD_STYLE_TOKEN_RE = re.compile(
    r"[A-Za-z_]\w*|\.\.\.|->|&&|\|\||==|!=|<=|>=|<<|>>|"
    r"[(){}\[\];,=*?:]"
)


class CToken(NamedTuple):
    text: str
    line: int


class DefinitionMacro(NamedTuple):
    name_parameter: int
    emits_body: bool


def normalize_evidence(line: str, limit: int = 140) -> str:
    evidence = " ".join(line.strip().split())
    if len(evidence) > limit:
        evidence = evidence[: limit - 1] + "…"
    return evidence


def subsystem_for(path: str) -> str:
    lower = path.lower()
    name = Path(path).name.lower()
    if "linker" in name or name in {"ldscript.txt", "sym_iwram.txt"}:
        return "linker"
    if (
        path == "Makefile"
        or lower.endswith((".mk", ".yml", ".yaml"))
        or lower.startswith(("scripts/", ".github/"))
    ):
        return "build-tooling"
    if "banim" in lower or "ekr" in name or "efx" in name:
        return "battle-animation"
    if lower.startswith("sound/") or "m4a" in name or "music" in name:
        return "audio"
    if "worldmap" in lower:
        return "world-map"
    if "mapanim" in lower:
        return "map-animation"
    if "sio" in name or "multiboot" in name:
        return "multiplayer"
    if "event" in name:
        return "events"
    if any(word in name for word in ("menu", "ui", "screen", "popup", "helpbox")):
        return "ui"
    if lower.startswith("asm/"):
        return "assembly"
    if "/data/" in lower or lower.startswith("data/"):
        return "data"
    if lower.startswith("include/"):
        return "headers-api"
    return "core"


def tracked_files(root: Path) -> list[str]:
    if (root / ".git").exists():
        proc = subprocess.run(
            [
                "git",
                "-C",
                os.fspath(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            check=True,
            stdout=subprocess.PIPE,
        )
        names = [item.decode("utf-8", "surrogateescape") for item in proc.stdout.split(b"\0") if item]
    else:
        names = [
            path.relative_to(root).as_posix()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.parts
        ]
    return sorted(
        path
        for path in names
        if not path.startswith(EXCLUDED_PREFIXES) and (root / path).is_file()
    )


def strip_c_comments(lines: list[str]) -> list[str]:
    """Strip comments while retaining strings and line count."""
    result: list[str] = []
    in_block = False
    for line in lines:
        output: list[str] = []
        index = 0
        quote = ""
        escaped = False
        while index < len(line):
            char = line[index]
            pair = line[index : index + 2]
            if in_block:
                if pair == "*/":
                    in_block = False
                    index += 2
                else:
                    index += 1
                continue
            if quote:
                output.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
                index += 1
                continue
            if char in {'"', "'"}:
                quote = char
                output.append(char)
                index += 1
            elif pair == "/*":
                in_block = True
                index += 2
            elif pair == "//":
                break
            else:
                output.append(char)
                index += 1
        result.append("".join(output))
    return result


def mask_c_literals(lines: list[str]) -> list[str]:
    """Mask strings/chars after comment removal while preserving line numbers."""
    result: list[str] = []
    quote = ""
    escaped = False
    for line in strip_c_comments(lines):
        output: list[str] = []
        for char in line:
            if quote:
                output.append(" ")
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = ""
                continue
            if char in {'"', "'"}:
                quote = char
                output.append(" ")
            else:
                output.append(char)
        result.append("".join(output))
        if quote and not escaped:
            # C literals cannot continue without an escaped newline.
            quote = ""
        escaped = False
    return result


def preprocessor_lines(lines: list[str]) -> set[int]:
    """Return one-based physical lines occupied by preprocessor directives."""
    result: set[int] = set()
    continued = False
    for index, line in enumerate(lines, 1):
        directive = continued or line.lstrip().startswith("#")
        if directive:
            result.add(index)
        continued = directive and line.rstrip().endswith("\\")
    return result


def old_style_tokens(lines: list[str]) -> list[CToken]:
    masked = mask_c_literals(lines)
    directives = preprocessor_lines(lines)
    return [
        CToken(match.group(0), line_number)
        for line_number, line in enumerate(masked, 1)
        if line_number not in directives
        for match in OLD_STYLE_TOKEN_RE.finditer(line)
    ]


def matching_paren(tokens: list[CToken], opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(tokens)):
        if tokens[index].text == "(":
            depth += 1
        elif tokens[index].text == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def token_depths(tokens: list[CToken]) -> tuple[list[int], list[int]]:
    brace_depths: list[int] = []
    paren_depths: list[int] = []
    brace_depth = 0
    paren_depth = 0
    for token in tokens:
        brace_depths.append(brace_depth)
        paren_depths.append(paren_depth)
        if token.text == "{":
            brace_depth += 1
        elif token.text == "}":
            brace_depth = max(0, brace_depth - 1)
        elif token.text == "(":
            paren_depth += 1
        elif token.text == ")":
            paren_depth = max(0, paren_depth - 1)
    return brace_depths, paren_depths


def declarator_prefix(
    tokens: list[CToken], name_index: int, minimum_line: int = 0
) -> list[CToken]:
    start = name_index - 1
    while (
        start >= 0
        and tokens[start].line > minimum_line
        and tokens[start].text not in {";", "{", "}"}
    ):
        start -= 1
    return tokens[start + 1 : name_index]


def without_attributes(tokens: list[CToken]) -> list[CToken]:
    result: list[CToken] = []
    index = 0
    while index < len(tokens):
        if tokens[index].text in {"__attribute__", "__attribute"}:
            if index + 1 >= len(tokens) or tokens[index + 1].text != "(":
                return []
            closing = matching_paren(tokens, index + 1)
            if closing is None:
                return []
            index = closing + 1
            continue
        result.append(tokens[index])
        index += 1
    return result


def valid_return_prefix(tokens: list[CToken]) -> bool:
    """Accept type/macro return forms, but not initializers or pointer typedefs."""
    tokens = without_attributes(tokens)
    if not tokens:
        return True  # attribute-only or implicit-int/K&R form
    texts = [token.text for token in tokens]
    if any(text in {"=", ";", "{", "}", "[", "]", ",", "?", ":"} for text in texts):
        return False
    if any(text in {"typedef", "asm", "__asm__"} for text in texts):
        return False
    depth = 0
    for text in texts:
        if text == "(":
            depth += 1
        elif text == ")":
            depth -= 1
            if depth < 0:
                return False
        elif text != "*" and not re.fullmatch(r"[A-Za-z_]\w*", text):
            return False
    return depth == 0


def identifier_parameter_list(tokens: list[CToken]) -> list[str] | None:
    if not tokens or len(tokens) % 2 == 0:
        return None
    names: list[str] = []
    for index, token in enumerate(tokens):
        if index % 2 == 0:
            if not re.fullmatch(r"[A-Za-z_]\w*", token.text):
                return None
            names.append(token.text)
        elif token.text != ",":
            return None
    if names == ["void"]:
        return None
    return names


def skip_post_declarator_attributes(tokens: list[CToken], index: int) -> int:
    while index < len(tokens) and tokens[index].text in {
        "__attribute__",
        "__attribute",
        "asm",
        "__asm__",
    }:
        index += 1
        if index >= len(tokens) or tokens[index].text != "(":
            return index
        closing = matching_paren(tokens, index)
        if closing is None:
            return len(tokens)
        index = closing + 1
    return index


def knr_declarations_end(
    tokens: list[CToken],
    start: int,
    parameter_names: list[str],
    brace_depths: list[int],
) -> int | None:
    """Validate K&R parameter declarations and return the body brace index."""
    if start >= len(tokens) or tokens[start].text == ";":
        return None
    index = start
    declaration_tokens: list[CToken] = []
    saw_semicolon = False
    while index < len(tokens):
        token = tokens[index]
        if brace_depths[index] != 0:
            return None
        if token.text == "{":
            break
        if token.text in {"}", "="}:
            return None
        declaration_tokens.append(token)
        if token.text == ";":
            saw_semicolon = True
        index += 1
    if index >= len(tokens) or tokens[index].text != "{" or not saw_semicolon:
        return None
    texts = [token.text for token in declaration_tokens]
    if not set(parameter_names).issubset(texts):
        return None
    paren_depth = 0
    bracket_depth = 0
    for text in texts:
        if text == "(":
            paren_depth += 1
        elif text == ")":
            paren_depth -= 1
        elif text == "[":
            bracket_depth += 1
        elif text == "]":
            bracket_depth -= 1
        if paren_depth < 0 or bracket_depth < 0:
            return None
    if paren_depth or bracket_depth:
        return None
    return index


def normalized_token_evidence(tokens: list[CToken]) -> str:
    text = " ".join(token.text for token in tokens)
    text = re.sub(r"\s+([(),;])", r"\1", text)
    text = re.sub(r"([(*])\s+", r"\1", text)
    return normalize_evidence(text)


def logical_directives(lines: list[str]) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    cleaned = strip_c_comments(lines)
    index = 0
    while index < len(cleaned):
        if not cleaned[index].lstrip().startswith("#"):
            index += 1
            continue
        start = index + 1
        parts: list[str] = []
        while index < len(cleaned):
            part = cleaned[index].rstrip()
            continued = part.endswith("\\")
            parts.append(part[:-1] if continued else part)
            index += 1
            if not continued:
                break
        result.append((start, " ".join(parts)))
    return result


def conditional_boundary_before(lines: list[str], line: int) -> int:
    boundary = 0
    for directive_line, directive in logical_directives(lines):
        if directive_line >= line:
            break
        if re.match(
            r"^\s*#\s*(?:if|ifdef|ifndef|elif|else|endif)\b",
            directive,
        ):
            boundary = directive_line
    return boundary


def shared_conditional_body(
    tokens: list[CToken],
    closing: int,
    symbol: str,
    brace_depths: list[int],
    lines: list[str],
) -> int | None:
    body = next(
        (
            index
            for index in range(closing + 1, len(tokens))
            if tokens[index].text == "{" and brace_depths[index] == 0
        ),
        None,
    )
    if body is None:
        return None
    directives = [
        directive
        for directive_line, directive in logical_directives(lines)
        if tokens[closing].line < directive_line < tokens[body].line
    ]
    if not any(re.match(r"^\s*#\s*(?:else|elif)\b", item) for item in directives):
        return None
    if not any(re.match(r"^\s*#\s*endif\b", item) for item in directives):
        return None
    between = tokens[closing + 1 : body]
    if any(token.text in {";", "=", "{", "}"} for token in between):
        return None
    return body if any(token.text == symbol for token in between) else None


def complex_declarator_body(
    tokens: list[CToken],
    opening: int,
    closing: int,
    prefix: list[CToken],
    brace_depths: list[int],
    paren_depths: list[int],
) -> tuple[int, int] | None:
    """Handle a function returning a pointer to a function."""
    if paren_depths[opening] != 1:
        return None
    outer = next(
        (
            index
            for index in range(opening - 2, -1, -1)
            if tokens[index].text == "(" and paren_depths[index] == 0
        ),
        None,
    )
    if outer is None or "*" not in {
        token.text for token in tokens[outer + 1 : opening - 1]
    }:
        return None
    # The prefix slice contains the unmatched outer '(' and pointer stars.
    outer_offset = next(
        (index for index, token in enumerate(prefix) if token is tokens[outer]),
        None,
    )
    if outer_offset is None or not valid_return_prefix(prefix[:outer_offset]):
        return None
    if closing + 1 >= len(tokens) or tokens[closing + 1].text != ")":
        return None
    suffix_open = closing + 2
    if suffix_open >= len(tokens) or tokens[suffix_open].text != "(":
        return None
    suffix_close = matching_paren(tokens, suffix_open)
    if suffix_close is None:
        return None
    after = skip_post_declarator_attributes(tokens, suffix_close + 1)
    if (
        after >= len(tokens)
        or tokens[after].text != "{"
        or brace_depths[after] != 0
    ):
        return None
    return after, after - 1


def definition_macros(lines: list[str]) -> dict[str, DefinitionMacro]:
    """Find simple function-like macros whose replacement emits a definition."""
    result: dict[str, DefinitionMacro] = {}
    for _, directive in logical_directives(lines):
        match = re.match(
            r"^\s*#\s*define\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(.*)$",
            directive,
        )
        if not match:
            continue
        macro_name, parameter_text, replacement = match.groups()
        parameters = [
            parameter.strip()
            for parameter in parameter_text.split(",")
            if parameter.strip()
        ]
        replacement = mask_c_literals([replacement])[0]
        for parameter_index, parameter in enumerate(parameters):
            declarator = re.search(
                rf"\b{re.escape(parameter)}\s*\(\s*\)\s*(\{{)?",
                replacement,
            )
            if declarator and valid_return_prefix(
                [
                    CToken(token.group(0), 1)
                    for token in OLD_STYLE_TOKEN_RE.finditer(
                        replacement[: declarator.start()]
                    )
                ]
            ):
                result[macro_name] = DefinitionMacro(
                    parameter_index,
                    declarator.group(1) is not None,
                )
                break
    return result


def macro_arguments(tokens: list[CToken]) -> list[list[CToken]]:
    arguments: list[list[CToken]] = [[]]
    depth = 0
    for token in tokens:
        if token.text == "(":
            depth += 1
        elif token.text == ")":
            depth -= 1
        if token.text == "," and depth == 0:
            arguments.append([])
        else:
            arguments[-1].append(token)
    return arguments


def scan_old_style_definitions(path: str, lines: list[str]) -> list[dict]:
    """Lexically find top-level non-prototype function definitions."""
    tokens = old_style_tokens(lines)
    brace_depths, paren_depths = token_depths(tokens)
    macros = definition_macros(lines)
    findings: list[dict] = []
    seen: set[tuple[int, str]] = set()

    for opening, token in enumerate(tokens):
        if (
            token.text != "("
            or brace_depths[opening] != 0
            or paren_depths[opening] not in {0, 1}
            or opening == 0
            or not re.fullmatch(r"[A-Za-z_]\w*", tokens[opening - 1].text)
        ):
            continue
        name_token = tokens[opening - 1]
        name = name_token.text
        if name in NON_FUNCTION_NAMES:
            continue
        closing = matching_paren(tokens, opening)
        if closing is None:
            continue

        # Expand only the deliberately supported simple definition-macro form.
        macro = macros.get(name)
        if macro:
            arguments = macro_arguments(tokens[opening + 1 : closing])
            if macro.name_parameter >= len(arguments):
                continue
            name_argument = arguments[macro.name_parameter]
            if len(name_argument) != 1 or not re.fullmatch(
                r"[A-Za-z_]\w*", name_argument[0].text
            ):
                continue
            after = closing + 1
            if not macro.emits_body and (
                after >= len(tokens)
                or tokens[after].text != "{"
                or brace_depths[after] != 0
            ):
                continue
            symbol = name_argument[0].text
            key = (name_token.line, symbol)
            if key not in seen:
                finding = make_finding(
                    "old-style-function-definition",
                    path,
                    name_token.line,
                    normalized_token_evidence(tokens[opening - 1 : closing + 1]),
                )
                finding["symbol"] = symbol
                findings.append(finding)
                seen.add(key)
            continue

        prefix = declarator_prefix(
            tokens,
            opening - 1,
            conditional_boundary_before(lines, name_token.line),
        )
        parameters = tokens[opening + 1 : closing]
        body: int | None = None
        evidence_end = closing
        if paren_depths[opening] == 1:
            if parameters:
                continue
            complex_result = complex_declarator_body(
                tokens,
                opening,
                closing,
                prefix,
                brace_depths,
                paren_depths,
            )
            if complex_result:
                body, evidence_end = complex_result
        else:
            if not valid_return_prefix(prefix):
                continue
            after = skip_post_declarator_attributes(tokens, closing + 1)
            if not parameters:
                if (
                    after < len(tokens)
                    and tokens[after].text == "{"
                    and brace_depths[after] == 0
                ):
                    body = after
                else:
                    body = shared_conditional_body(
                        tokens, closing, name, brace_depths, lines
                    )
            else:
                parameter_names = identifier_parameter_list(parameters)
                if parameter_names:
                    body = knr_declarations_end(
                        tokens, after, parameter_names, brace_depths
                    )
        if body is None:
            continue

        signature = [*prefix, name_token, *tokens[opening : evidence_end + 1]]
        line = name_token.line
        key = (line, name)
        if key in seen:
            continue
        finding = make_finding(
            "old-style-function-definition",
            path,
            line,
            normalized_token_evidence(signature),
        )
        finding["symbol"] = name
        findings.append(finding)
        seen.add(key)
    return findings


def is_source(path: str) -> bool:
    return path.startswith(SOURCE_PREFIXES) and Path(path).suffix in SOURCE_EXTENSIONS


def is_target_c(path: str) -> bool:
    return path.startswith(("src/", "include/")) and Path(path).suffix in C_EXTENSIONS


def is_build_file(path: str) -> bool:
    candidate = Path(path)
    return (
        candidate.name in BUILD_BASENAMES
        or candidate.suffix in BUILD_SUFFIXES
        or candidate.name.endswith("Makefile")
    )


def is_linker_file(path: str) -> bool:
    candidate = Path(path)
    return candidate.name in LINKER_BASENAMES or candidate.suffix == ".ld"


def raw_code_for_line(path: str, line: str) -> str:
    stripped = line.lstrip()
    suffix = Path(path).suffix
    if suffix in ASM_EXTENSIONS:
        if stripped.startswith(("@", ";")):
            return ""
        # GNU ARM uses @ for comments. Labels annotated with original ROM
        # offsets are documentation, not address operands.
        line = line.split("@", 1)[0]
    if is_build_file(path) and stripped.startswith("#"):
        return ""
    return line


def make_finding(category: str, path: str, line: int, evidence: str) -> dict:
    severity, priority, disposition = CATEGORY_META[category]
    return {
        "category": category,
        "detected_category": category,
        "severity": severity,
        "priority": priority,
        "file": path,
        "line": line,
        "evidence": normalize_evidence(evidence),
        "subsystem": subsystem_for(path),
        "disposition": disposition,
        "status": "open",
    }


def is_rom_address_context(path: str, code: str, match: re.Match[str]) -> bool:
    """Return true only when a ROM-range literal is used as an address."""
    prefix = code[: match.start()]
    suffix = code[match.end() :]
    if Path(path).suffix in ASM_EXTENSIONS:
        if re.search(r"(?:\.4byte|\.word|\.long)\s*$", prefix, re.IGNORECASE):
            return True
        if re.search(r"\bldr\b[^@;]*=\s*$", prefix, re.IGNORECASE):
            return True
        assignment = re.search(r"\b([A-Za-z_]\w*)\s*=\s*$", prefix)
        return bool(
            assignment
            and re.search(r"(?:ROM|ADDR|ADDRESS|PTR|BASE|START|END)", assignment.group(1), re.I)
        )

    # Explicit pointer casts are the strongest source-level signal.
    if re.search(r"\([^();\n]*\*\s*\)\s*$", prefix):
        return True
    # Pointer declarations/assignments without a cast.
    if re.search(r"\*\s*[A-Za-z_]\w*\s*=\s*$", prefix):
        return True
    assignment = re.search(r"\b([A-Za-z_]\w*)\s*=\s*$", prefix)
    if assignment and re.search(
        r"(?:addr|address|pointer|ptr|rom|base|start|end)", assignment.group(1), re.I
    ):
        return True
    # A literal followed by a pointer cast is uncommon but valid C.
    return bool(re.match(r"\s*(?:[uUlL]*\s*)?\)", suffix) and "*" in prefix.rsplit("(", 1)[-1])


def compile_encoding_context(path: str, index: int, lines: list[str]) -> bool:
    """CP932 is debt only when the surrounding build recipe feeds a compiler."""
    suffix = Path(path).suffix
    if Path(path).name not in BUILD_BASENAMES and suffix not in {".mk", ".sh", ".yml", ".yaml"}:
        return False
    nearby = "\n".join(lines[max(0, index - 3) : min(len(lines), index + 2)])
    return bool(re.search(r"\b(?:CC1|agbcc|old_agbcc|compiler)\b", nearby, re.IGNORECASE))


def scan_c_file(path: str, lines: list[str]) -> list[dict]:
    findings: list[dict] = []
    code_lines = strip_c_comments(lines)
    for index, (original, code) in enumerate(zip(lines, code_lines), 1):
        if REGISTER_RE.search(code):
            findings.append(make_finding("register-pinned-local", path, index, original))

        if ASM_RE.search(code) and not REGISTER_RE.search(code):
            statement = code
            for following in code_lines[index : min(len(code_lines), index + 30)]:
                if ");" in statement:
                    break
                statement += "\n" + following
            category = "inline-asm-barrier" if EMPTY_ASM_RE.search(statement) else "inline-asm"
            findings.append(make_finding(category, path, index, original))

        if (
            ("NAKEDFUNC" in code or NAKED_ATTR_RE.search(code))
            and not code.lstrip().startswith("#define")
        ):
            findings.append(make_finding("naked-function", path, index, original))

        if "MISMATCHED_SIGNATURE" in code and not code.lstrip().startswith("#define"):
            findings.append(make_finding("mismatched-signature", path, index, original))

        if re.match(r"^\s*#\s*(?:if|ifdef|ifndef|elif)\b.*\bMODERN\b", code):
            findings.append(make_finding("modern-conditional", path, index, original))

        if FORCED_DATA_RE.search(code) and not code.lstrip().startswith("#define"):
            findings.append(make_finding("forced-data-placement", path, index, original))

        if (
            "BITPACKED" in code
            or re.search(r"__attribute__\s*\(\([^)]*\bpacked\b", code)
            or BITFIELD_RE.search(code)
            or OFFSET_FIELD_RE.search(original)
        ):
            findings.append(make_finding("layout-sensitive-struct", path, index, original))

        if OLD_STYLE_RE.match(code) and "(*" not in code and not code.lstrip().startswith("typedef"):
            findings.append(make_finding("old-style-function-declaration", path, index, original))

        for match in RAW_ROM_RE.finditer(code):
            if is_rom_address_context(path, code, match):
                findings.append(make_finding("raw-rom-address", path, index, original))
    findings.extend(scan_old_style_definitions(path, lines))
    return findings


def scan_source_file(path: str, lines: list[str]) -> list[dict]:
    findings: list[dict] = []
    if is_target_c(path):
        findings.extend(scan_c_file(path, lines))
    else:
        for index, original in enumerate(lines, 1):
            code = raw_code_for_line(path, original)
            for match in RAW_ROM_RE.finditer(code):
                if is_rom_address_context(path, code, match):
                    findings.append(make_finding("raw-rom-address", path, index, original))

    if Path(path).suffix in ASM_EXTENSIONS:
        first_line = next((index for index, value in enumerate(lines, 1) if value.strip()), 1)
        kind = "handwritten" if path.startswith(("asm/", "src/")) else "non-C"
        findings.append(
            make_finding(
                "assembly-boundary",
                path,
                first_line,
                f"{kind} assembly boundary ({Path(path).suffix})",
            )
        )
    return findings


def scan_build_file(path: str, lines: list[str]) -> list[dict]:
    findings: list[dict] = []
    for index, original in enumerate(lines, 1):
        code = raw_code_for_line(path, original)
        if not code.strip():
            continue
        if LEGACY_COMPILER_RE.search(code) or (
            CP932_RE.search(code) and compile_encoding_context(path, index, lines)
        ):
            findings.append(make_finding("legacy-compiler-pipeline", path, index, original))
        if re.search(r"^\s*#\s*(?:if|ifdef|ifndef|elif)\b.*\bMODERN\b", code):
            findings.append(make_finding("modern-conditional", path, index, original))
        # Fixed placement belongs to command/declarative build inputs. Python
        # tooling often documents or validates the same constant in strings;
        # counting those would duplicate the actual placement constraint.
        if Path(path).suffix != ".py" and FIXED_BASE_RE.search(code):
            findings.append(make_finding("fixed-rom-base", path, index, original))
        if (
            re.search(r"\b(?:ALL_OBJECTS|OBJECTS_LST)\b", code)
            or re.search(r"@\$\((?:OBJECTS_LST)\)", code)
        ):
            findings.append(make_finding("linker-placement-coupling", path, index, original))
    return findings


def scan_linker_file(path: str, lines: list[str]) -> list[dict]:
    findings: list[dict] = []
    if Path(path).name in {"linker_script_banim.txt", "linker_script_sound.txt"}:
        entries = [
            (index, line)
            for index, line in enumerate(lines, 1)
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if entries:
            index, line = entries[0]
            findings.append(
                make_finding(
                    "linker-placement-coupling",
                    path,
                    index,
                    f"ordered manifest with {len(entries)} entries; first: {normalize_evidence(line, 80)}",
                )
            )
        return findings

    for index, original in enumerate(lines, 1):
        code = strip_c_comments([original])[0]
        if LINKER_ABSOLUTE_RE.search(code) or re.search(r"\b[\w./-]+\.o\s*\(", code):
            findings.append(make_finding("linker-placement-coupling", path, index, original))
        if RAW_ROM_RE.search(code) and ("ORIGIN" in code or re.search(r"^\s*\w+\s*=", code)):
            findings.append(make_finding("fixed-rom-base", path, index, original))
    return findings


def struct_contexts(lines: list[str]) -> dict[int, tuple[str, str, int]]:
    """Map lines in named struct/union definitions to their root construct."""
    code_lines = strip_c_comments(lines)
    contexts: dict[int, tuple[str, str, int]] = {}
    stack: list[tuple[int, str, str, int]] = []
    pending: tuple[str, str, int] | None = None
    depth = 0

    for index, code in enumerate(code_lines, 1):
        if stack:
            _, kind, name, start = stack[-1]
            contexts[index] = (kind, name, start)

        inline = re.search(
            r"^\s*(?:typedef\s+)?(struct|union)\s*([A-Za-z_]\w*)?\s*\{",
            code,
        )
        declaration = re.match(
            r"^\s*(?:typedef\s+)?(struct|union)(?:\s+([A-Za-z_]\w*))?\s*$",
            code,
        )
        opens = code.count("{")
        closes = code.count("}")

        if inline:
            kind = inline.group(1)
            name = inline.group(2) or f"anonymous@{index}"
            stack.append((depth + 1, kind, name, index))
            contexts[index] = (kind, name, index)
            pending = None
        elif declaration:
            kind = declaration.group(1)
            name = declaration.group(2) or f"anonymous@{index}"
            pending = (kind, name, index)
        elif pending and opens:
            kind, name, start = pending
            stack.append((depth + 1, kind, name, start))
            contexts[index] = (kind, name, start)
            pending = None
        elif code.strip() and pending and not code.lstrip().startswith(("#", "{")):
            pending = None

        depth += opens - closes
        while stack and depth < stack[-1][0]:
            stack.pop()
    return contexts


def macro_contexts(lines: list[str]) -> dict[int, tuple[str, int]]:
    """Map continued preprocessor macro bodies to their defining macro."""
    contexts: dict[int, tuple[str, int]] = {}
    current: tuple[str, int] | None = None
    for index, line in enumerate(lines, 1):
        match = re.match(r"^\s*#\s*define\s+([A-Za-z_]\w*)", line)
        if match:
            current = (match.group(1), index)
        if current:
            contexts[index] = current
        if current and not line.rstrip().endswith("\\"):
            current = None
    return contexts


def declaration_name(code: str) -> str | None:
    """Best-effort symbol name for a declaration containing a marker."""
    cleaned = re.sub(
        r"\b(?:CONST_DATA|EWRAM_DATA|IWRAM_DATA|EWRAM_OVERLAY)\b(?:\s*\([^)]*\))?",
        " ",
        code,
    )
    cleaned = re.sub(r"__attribute__\s*\(\(.*?\)\)", " ", cleaned)
    head = cleaned.split("=", 1)[0].split(";", 1)[0]
    head = re.sub(r"\[[^\]]*\]", "", head)
    names = re.findall(r"\b[A-Za-z_]\w*\b", head)
    ignored = {
        "extern",
        "static",
        "const",
        "volatile",
        "unsigned",
        "signed",
        "struct",
        "union",
        "enum",
        "void",
        "char",
        "short",
        "int",
        "long",
    }
    candidates = [name for name in names if name not in ignored]
    return candidates[-1] if candidates else None


def placement_mechanism(code: str) -> str:
    """Name the shared placement mechanism independently of each symbol."""
    for marker, label in (
        ("CONST_DATA", "CONST_DATA → .data"),
        ("EWRAM_DATA", "EWRAM_DATA → ewram_data"),
        ("IWRAM_DATA", "IWRAM_DATA → iwram_data"),
        ("EWRAM_OVERLAY", "EWRAM_OVERLAY"),
    ):
        if marker in code:
            return label
    section = re.search(r"\bSECTION\s*\(\s*([^)]+)\)", code)
    if section:
        return f"SECTION({normalize_evidence(section.group(1), 60)})"
    attribute = re.search(
        r"__attribute__\s*\(\([^)]*\bsection\s*\(\s*([^)]+)\)",
        code,
    )
    if attribute:
        return f"section({normalize_evidence(attribute.group(1), 60)})"
    return "custom section placement"


def nearest_function(
    code_lines: list[str], line: int, *, allow_forward: bool = False
) -> tuple[str, int] | None:
    """Find a nearby declaration-shaped function root, avoiding call sites."""
    # Only naked markers/attributes legitimately precede the function they own.
    # All findings inside bodies must bind backwards, never to a later function.
    if allow_forward:
        for index in range(line - 1, min(len(code_lines), line + 8)):
            match = FUNCTION_DECL_RE.match(code_lines[index])
            if match and match.group(1) not in NON_FUNCTION_NAMES:
                return match.group(1), index + 1
    for index in range(line - 1, -1, -1):
        match = FUNCTION_DECL_RE.match(code_lines[index])
        if match and match.group(1) not in NON_FUNCTION_NAMES:
            return match.group(1), index + 1
    return None


def root_for_finding(
    finding: dict,
    lines: list[str],
    code_lines: list[str],
    struct_map: dict[int, tuple[str, str, int]],
    macro_map: dict[int, tuple[str, int]],
) -> tuple[str, str, int]:
    category = finding["detected_category"]
    path = finding["file"]
    line = finding["line"]
    code = code_lines[line - 1] if 0 < line <= len(code_lines) else ""

    if category == "layout-sensitive-struct":
        context = struct_map.get(line)
        if context:
            kind, name, start = context
            return "struct", f"{kind} {name}", start
        macro = macro_map.get(line)
        if macro:
            return "construct", f"macro {macro[0]}", macro[1]
    if category == "linker-placement-coupling":
        return "manifest", path, 1
    if category == "assembly-boundary":
        return "manifest", path, line
    if category == "forced-data-placement":
        declaration = "\n".join(code_lines[line - 1 : min(len(code_lines), line + 4)])
        finding["symbol"] = declaration_name(declaration)
        return "placement", placement_mechanism(declaration), line
    if category == "mismatched-signature":
        match = re.search(r"MISMATCHED_SIGNATURE\s*\(\s*([A-Za-z_]\w*)", code)
        if match:
            return "construct", match.group(1), line
    if category == "old-style-function-declaration":
        match = FUNCTION_DECL_RE.match(code)
        if match:
            return "construct", match.group(1), line
    if category == "old-style-function-definition":
        return "construct", finding.get("symbol", finding["evidence"]), line
    if category == "modern-conditional":
        return "construct", "MODERN conditional", line
    if category == "legacy-compiler-pipeline":
        return "construct", f"compiler pipeline in {path}", 1
    if category == "fixed-rom-base":
        name = "battle-animation ROM base" if "banim" in finding["evidence"].lower() else "ROM base"
        return "construct", name, line

    function = (
        nearest_function(
            code_lines,
            line,
            allow_forward=category == "naked-function",
        )
        if Path(path).suffix in C_EXTENSIONS
        else None
    )
    if function:
        return "construct", function[0], function[1]
    return "construct", path, line


def annotate_roots(findings: list[dict], file_lines: dict[str, list[str]]) -> None:
    by_file: dict[str, list[dict]] = {}
    for finding in findings:
        by_file.setdefault(finding["file"], []).append(finding)
    for path, members in by_file.items():
        lines = file_lines.get(path, [])
        is_c = Path(path).suffix in C_EXTENSIONS
        code_lines = strip_c_comments(lines) if is_c else lines
        struct_map = struct_contexts(lines) if is_c else {}
        macro_map = macro_contexts(lines) if is_c else {}
        for finding in members:
            kind, construct, line = root_for_finding(
                finding, lines, code_lines, struct_map, macro_map
            )
            finding["root_kind"] = kind
            finding["root_construct"] = construct
            finding["root_line"] = line


def assign_ids(findings: list[dict]) -> None:
    occurrences: Counter[tuple[str, str, str]] = Counter()
    for finding in findings:
        key = (
            finding["detected_category"],
            finding["file"],
            finding["evidence"],
        )
        occurrences[key] += 1
        material = "\0".join((*key, str(occurrences[key])))
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
        finding["id"] = f"{finding['detected_category']}:{digest}"


def build_aggregates(findings: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for finding in findings:
        if finding["root_kind"] == "placement":
            # Placement macros/sections are repository-wide migration roots.
            # Symbols and files remain available on the aggregate and through
            # every finding ID, but do not split the actionable root.
            key = (
                finding["category"],
                finding["root_kind"],
                finding["root_construct"],
                finding["priority"],
                finding["severity"],
                finding["disposition"],
            )
        else:
            key = (
                finding["category"],
                finding["root_kind"],
                finding["root_construct"],
                finding["file"],
                finding["root_line"],
                finding["subsystem"],
                finding["priority"],
                finding["severity"],
                finding["disposition"],
            )
        groups.setdefault(key, []).append(finding)

    aggregates: list[dict] = []
    for members in groups.values():
        members = sorted(
            members, key=lambda item: (item["file"], item["line"], item["id"])
        )
        first = members[0]
        category = first["category"]
        root_kind = first["root_kind"]
        root_construct = first["root_construct"]
        priority = first["priority"]
        severity = first["severity"]
        disposition = first["disposition"]
        files = sorted({member["file"] for member in members})
        subsystems = sorted({member["subsystem"] for member in members})
        if root_kind == "placement":
            path = files[0]
            root_line = min(
                member["line"] for member in members if member["file"] == path
            )
            subsystem = subsystems[0] if len(subsystems) == 1 else "cross-subsystem"
            material = "\0".join((category, root_kind, root_construct))
        else:
            path = first["file"]
            root_line = first["root_line"]
            subsystem = first["subsystem"]
            material = "\0".join(
                (category, root_kind, root_construct, path, str(root_line))
            )
        aggregate_id = "root:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
        statuses = Counter(member["status"] for member in members)
        aggregate = {
            "id": aggregate_id,
            "category": category,
            "severity": severity,
            "priority": priority,
            "file": path,
            "line": root_line,
            "root_kind": root_kind,
            "root_construct": root_construct,
            "subsystem": subsystem,
            "disposition": disposition,
            "finding_count": len(members),
            "finding_ids": sorted(member["id"] for member in members),
            "by_status": dict(sorted(statuses.items())),
        }
        if root_kind == "placement":
            aggregate["file_count"] = len(files)
            aggregate["files"] = files
            aggregate["symbols"] = sorted(
                {
                    member["symbol"]
                    for member in members
                    if member.get("symbol")
                }
            )
        aggregates.append(aggregate)
    return sorted(
        aggregates,
        key=lambda item: (
            item["category"],
            item["file"],
            item["line"],
            item["root_construct"],
        ),
    )


def load_decisions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("decisions", [])
    seen: set[str] = set()
    for entry in entries:
        finding_id = entry.get("id")
        status = entry.get("status")
        reason = entry.get("reason")
        if not finding_id or finding_id in seen:
            raise ValueError(f"decision IDs must be present and unique: {finding_id!r}")
        if status not in {"resolved", "retained", "reclassified"}:
            raise ValueError(f"invalid decision status for {finding_id}: {status!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"decision reason is required for {finding_id}")
        if status == "reclassified" and not entry.get("category"):
            raise ValueError(f"reclassified decision needs category for {finding_id}")
        seen.add(finding_id)
    return entries


def apply_decisions(findings: list[dict], decisions: list[dict]) -> list[dict]:
    by_id = {finding["id"]: finding for finding in findings}
    ledger: list[dict] = []
    for decision in sorted(decisions, key=lambda item: item["id"]):
        finding = by_id.get(decision["id"])
        ledger_entry = dict(decision)
        ledger_entry["active"] = finding is not None
        ledger.append(ledger_entry)
        if finding is None:
            continue
        finding["status"] = decision["status"]
        finding["decision_reason"] = decision["reason"]
        if decision["status"] == "reclassified":
            finding["category"] = decision["category"]
            if "disposition" in decision:
                finding["disposition"] = decision["disposition"]
    return ledger


def scan_repository(root: Path, decisions_path: Path | None = None) -> dict:
    findings: list[dict] = []
    file_lines: dict[str, list[str]] = {}
    for path in tracked_files(root):
        if not (is_source(path) or is_build_file(path) or is_linker_file(path)):
            continue
        try:
            lines = (root / path).read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        file_lines[path] = lines
        if is_source(path):
            findings.extend(scan_source_file(path, lines))
        if is_build_file(path):
            findings.extend(scan_build_file(path, lines))
        if is_linker_file(path):
            findings.extend(scan_linker_file(path, lines))

    unique = {
        (
            item["detected_category"],
            item["file"],
            item["line"],
            item["evidence"],
        ): item
        for item in findings
    }
    findings = sorted(
        unique.values(),
        key=lambda item: (
            item["detected_category"],
            item["file"],
            item["line"],
            item["evidence"],
        ),
    )
    annotate_roots(findings, file_lines)
    assign_ids(findings)
    decisions = load_decisions(decisions_path) if decisions_path else []
    ledger = apply_decisions(findings, decisions)
    aggregates = build_aggregates(findings)

    by_category = Counter(item["category"] for item in findings)
    by_subsystem = Counter(item["subsystem"] for item in findings)
    by_priority = Counter(item["priority"] for item in findings)
    by_status = Counter(item["status"] for item in findings)
    by_root_kind = Counter(item["root_kind"] for item in aggregates)
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "total": len(findings),
            "by_category": dict(sorted(by_category.items())),
            "by_subsystem": dict(sorted(by_subsystem.items())),
            "by_priority": dict(sorted(by_priority.items())),
            "by_status": dict(sorted(by_status.items())),
            "aggregate_total": len(aggregates),
            "by_root_kind": dict(sorted(by_root_kind.items())),
        },
        "findings": findings,
        "aggregates": aggregates,
        "decision_ledger": ledger,
        "suppression_rules": SUPPRESSION_RULES,
    }


def json_bytes(inventory: dict) -> bytes:
    return (json.dumps(inventory, indent=2, sort_keys=True) + "\n").encode("utf-8")


def markdown_text(inventory: dict) -> str:
    summary = inventory["summary"]
    lines = [
        "# Modern-Compiler Blocker Inventory",
        "",
        "Generated by `python3 scripts/modernize/audit.py`. "
        "The JSON report is the complete machine-readable inventory.",
        "",
        f"**Total findings:** {summary['total']}",
        "",
        "## Category totals",
        "",
        "| Category | Count |",
        "|---|---:|",
    ]
    lines.extend(f"| `{name}` | {count} |" for name, count in summary["by_category"].items())
    lines.extend(["", "## Subsystem totals", "", "| Subsystem | Count |", "|---|---:|"])
    lines.extend(f"| `{name}` | {count} |" for name, count in summary["by_subsystem"].items())
    lines.extend(["", "## Migration priority", "", "| Priority | Count |", "|---|---:|"])
    lines.extend(f"| {name} | {count} |" for name, count in summary["by_priority"].items())
    lines.extend(
        [
            "",
            "## Root aggregation",
            "",
            f"**Actionable roots:** {summary['aggregate_total']}. "
            "Every aggregate retains its complete `finding_ids` drill-down list in JSON.",
            "",
            "| Root kind | Count |",
            "|---|---:|",
        ]
    )
    lines.extend(f"| `{name}` | {count} |" for name, count in summary["by_root_kind"].items())
    lines.extend(
        [
            "",
            "### Root migration queue",
            "",
            "Up to three largest roots per category.",
            "",
            "| Category | Root | Location | Findings | Disposition |",
            "|---|---|---|---:|---|",
        ]
    )
    aggregate_categories: dict[str, list[dict]] = {}
    for aggregate in inventory["aggregates"]:
        aggregate_categories.setdefault(aggregate["category"], []).append(aggregate)
    for category in sorted(aggregate_categories):
        roots = sorted(
            aggregate_categories[category],
            key=lambda item: (-item["finding_count"], item["file"], item["line"], item["id"]),
        )
        for aggregate in roots[:3]:
            root = aggregate["root_construct"].replace("|", "\\|").replace("`", "\\`")
            lines.append(
                f"| `{category}` | `{aggregate['root_kind']}` {root} | "
                f"`{aggregate['file']}:{aggregate['line']}` | "
                f"{aggregate['finding_count']} | `{aggregate['disposition']}` |"
            )

    lines.extend(
        [
            "",
            "## Representative queue",
            "",
            "Up to five deterministic examples per category; use `inventory.json` for every finding.",
            "",
            "| Category | Priority | Location | Subsystem | Disposition | Evidence |",
            "|---|---|---|---|---|---|",
        ]
    )
    categories: dict[str, list[dict]] = {}
    for finding in inventory["findings"]:
        categories.setdefault(finding["category"], []).append(finding)
    for category in sorted(categories):
        for finding in categories[category][:5]:
            evidence = finding["evidence"].replace("|", "\\|").replace("`", "\\`")
            lines.append(
                f"| `{category}` | {finding['priority']} | "
                f"`{finding['file']}:{finding['line']}` | `{finding['subsystem']}` | "
                f"`{finding['disposition']}` | {evidence} |"
            )

    lines.extend(["", "## Decision ledger", ""])
    if inventory["decision_ledger"]:
        lines.extend(["| Finding ID | Status | Active | Reason |", "|---|---|---|---|"])
        for decision in inventory["decision_ledger"]:
            reason = decision["reason"].replace("|", "\\|")
            lines.append(
                f"| `{decision['id']}` | `{decision['status']}` | "
                f"{str(decision['active']).lower()} | {reason} |"
            )
    else:
        lines.append("No findings have been dispositioned in `scripts/modernize/decisions.json`.")

    lines.extend(["", "## Narrow suppressions", ""])
    lines.extend(f"- {rule}" for rule in inventory["suppression_rules"])
    lines.append("")
    return "\n".join(lines)


def write_or_check(path: Path, expected: bytes, check: bool) -> bool:
    if check:
        try:
            actual = path.read_bytes()
        except FileNotFoundError:
            print(f"missing generated report: {path}", file=sys.stderr)
            return False
        if actual != expected:
            print(f"generated report drift: {path}", file=sys.stderr)
            return False
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(expected)
    return True


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="repository root (default: inferred from this script)",
    )
    parser.add_argument("--json", type=Path, default=None, help="JSON output path")
    parser.add_argument("--markdown", type=Path, default=None, help="Markdown output path")
    parser.add_argument("--decisions", type=Path, default=None, help="decision ledger path")
    parser.add_argument("--check", action="store_true", help="fail instead of writing when reports drift")
    return parser.parse_args(argv)


def resolve_path(root: Path, value: Path | None, default: str) -> Path:
    path = value if value is not None else Path(default)
    return path if path.is_absolute() else root / path


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    root = args.root.resolve()
    json_path = resolve_path(root, args.json, DEFAULT_JSON)
    markdown_path = resolve_path(root, args.markdown, DEFAULT_MARKDOWN)
    decisions_path = resolve_path(root, args.decisions, DEFAULT_DECISIONS)
    try:
        inventory = scan_repository(root, decisions_path)
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"modernize audit failed: {error}", file=sys.stderr)
        return 2

    checks = [
        write_or_check(json_path, json_bytes(inventory), args.check),
        write_or_check(markdown_path, markdown_text(inventory).encode("utf-8"), args.check),
    ]
    if not all(checks):
        return 1
    action = "checked" if args.check else "wrote"
    print(f"{action} {inventory['summary']['total']} findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
