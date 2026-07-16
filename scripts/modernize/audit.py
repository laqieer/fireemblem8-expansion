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
from typing import Iterable


SCHEMA_VERSION = 2
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
    "Report one assembly-boundary finding per tracked .s/.S/.inc file rather than every assembly directive.",
    "Recognize old-style declarations only when a src/include line has declaration shape and an empty parameter list.",
]

CATEGORY_META = {
    "register-pinned-local": ("high", "P0", "rewrite-c"),
    "inline-asm": ("high", "P0", "typed-asm-boundary"),
    "inline-asm-barrier": ("high", "P0", "rewrite-c"),
    "naked-function": ("critical", "P0", "typed-asm-boundary"),
    "old-style-function-declaration": ("high", "P0", "rewrite-c"),
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
BITFIELD_RE = re.compile(r"\b[A-Za-z_]\w*\s*:\s*\d+\s*;")
OFFSET_FIELD_RE = re.compile(
    r"/\*\s*(?:0x)?[0-9A-Fa-f]{1,4}\s*\*/[^;{}]*\b[A-Za-z_]\w*(?:\[[^\]]+\])?\s*;"
)
OLD_STYLE_RE = re.compile(
    r"^\s*(?:(?:extern|static|inline|const|volatile|unsigned|signed)\s+)*"
    r"(?:void|char|short|int|long|float|double|bool|[us](?:8|16|32|64)|"
    r"struct\s+[A-Za-z_]\w*|[A-Za-z_]\w*_t|[A-Z][A-Za-z0-9_]*)"
    r"(?:\s+|\s*\*+\s*)[A-Za-z_]\w*\s*\(\s*\)\s*(?:;|\{)"
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


def nearest_function(code_lines: list[str], line: int) -> tuple[str, int] | None:
    """Find a nearby declaration-shaped function root, avoiding call sites."""
    # Attributes precede their function, so first inspect a small forward window.
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
        name = declaration_name(declaration)
        if name:
            return "construct", name, line
    if category == "mismatched-signature":
        match = re.search(r"MISMATCHED_SIGNATURE\s*\(\s*([A-Za-z_]\w*)", code)
        if match:
            return "construct", match.group(1), line
    if category == "old-style-function-declaration":
        match = FUNCTION_DECL_RE.match(code)
        if match:
            return "construct", match.group(1), line
    if category == "modern-conditional":
        return "construct", "MODERN conditional", line
    if category == "legacy-compiler-pipeline":
        return "construct", f"compiler pipeline in {path}", 1
    if category == "fixed-rom-base":
        name = "battle-animation ROM base" if "banim" in finding["evidence"].lower() else "ROM base"
        return "construct", name, line

    function = nearest_function(code_lines, line) if Path(path).suffix in C_EXTENSIONS else None
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
    for key, members in groups.items():
        (
            category,
            root_kind,
            root_construct,
            path,
            root_line,
            subsystem,
            priority,
            severity,
            disposition,
        ) = key
        material = "\0".join(
            (category, root_kind, root_construct, path, str(root_line))
        )
        aggregate_id = "root:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]
        statuses = Counter(member["status"] for member in members)
        aggregates.append(
            {
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
        )
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
