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


SCHEMA_VERSION = 1
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
    r"\b(?:CONST_DATA|SHOULD_BE_CONST|EWRAM_DATA|IWRAM_DATA|EWRAM_OVERLAY)\b"
    r"|__attribute__\s*\(\([^)]*\bsection\s*\("
    r"|\bSECTION\s*\("
)
LEGACY_PIPELINE_RE = re.compile(
    r"\b(?:old_agbcc|agbcc)\b|CP932|iconv\s+-f\s+UTF-8\s+-t\s+CP932",
    re.IGNORECASE,
)
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

        if "NAKEDFUNC" in code and not code.lstrip().startswith("#define"):
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
            findings.append(make_finding("raw-rom-address", path, index, match.group(0)))
    return findings


def scan_source_file(path: str, lines: list[str]) -> list[dict]:
    findings: list[dict] = []
    if is_target_c(path):
        findings.extend(scan_c_file(path, lines))
    else:
        for index, original in enumerate(lines, 1):
            code = raw_code_for_line(path, original)
            for match in RAW_ROM_RE.finditer(code):
                findings.append(make_finding("raw-rom-address", path, index, match.group(0)))

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
        if LEGACY_PIPELINE_RE.search(code):
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
    for path in tracked_files(root):
        if not (is_source(path) or is_build_file(path) or is_linker_file(path)):
            continue
        try:
            lines = (root / path).read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
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
    assign_ids(findings)
    decisions = load_decisions(decisions_path) if decisions_path else []
    ledger = apply_decisions(findings, decisions)

    by_category = Counter(item["category"] for item in findings)
    by_subsystem = Counter(item["subsystem"] for item in findings)
    by_priority = Counter(item["priority"] for item in findings)
    by_status = Counter(item["status"] for item in findings)
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "total": len(findings),
            "by_category": dict(sorted(by_category.items())),
            "by_subsystem": dict(sorted(by_subsystem.items())),
            "by_priority": dict(sorted(by_priority.items())),
            "by_status": dict(sorted(by_status.items())),
        },
        "findings": findings,
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
