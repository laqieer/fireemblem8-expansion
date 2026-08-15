"""Deterministic ledger of localized indexed-text edits versus authorized sources."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

from .controls import (
    FE8CN_NAMED_CONTROL_ALIASES,
    SOURCE_DIALECT_CHINESE,
    SOURCE_DIALECT_JAPANESE,
    normalize_source_controls,
)
from .parsers import parse_control_definitions, parse_fe8cn, parse_hash_indexed


REPORT_PATH = Path("texts/locales/mapping/game_locale_text_edits.json")
DOC_PATH = Path("docs/game_locale_text_edits.md")
JP_ORIGINAL_PATH = Path("../fireemblem8j/texts/jp_texts.txt")
CN_ORIGINAL_PATH = Path("../FE8CN.txt")
JP_CONTROLS_PATH = Path("../fireemblem8j/texts/jp_textdefs.txt")
VENDORED_JP_PATH = Path("texts/locales/source/fe8j/jp_texts.txt")
VENDORED_CN_PATH = Path("texts/locales/source/fe8cn/FE8CN.txt")
VENDORED_JP_CONTROLS_PATH = Path("texts/locales/source/fe8j/jp_textdefs.txt")
OVERRIDE_PATHS = (
    Path("texts/locales/indexed_overrides.json"),
    Path("texts/locales/indexed_audit_overrides.json"),
)


class TextEditLedgerError(ValueError):
    """Raised when source provenance or generated ledger bytes drift."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _aggregate_import_hash(records) -> str:
    digest = hashlib.sha256()
    for source_id, text in records:
        digest.update(source_id.encode("ascii"))
        digest.update(b"\0")
        digest.update(text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _read_utf8(path: Path) -> Tuple[bytes, str]:
    try:
        raw = path.read_bytes()
        return raw, raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError) as error:
        raise TextEditLedgerError(f"{path}: authorized UTF-8 source is unavailable") from error


def _aliases(path: Path) -> Mapping[str, Tuple[int, ...]]:
    _, text = _read_utf8(path)
    return _aliases_from_text(text)


def _aliases_from_text(text: str) -> Mapping[str, Tuple[int, ...]]:
    return {
        definition.name: definition.values
        for definition in parse_control_definitions(text)
    }


def _authorized_source(
    repo_root: Path,
    preferred: Path,
    vendored: Path,
) -> Tuple[bytes, str]:
    """Use a live authorized source when present, otherwise its pinned snapshot."""

    snapshot = repo_root / vendored
    snapshot_raw, _ = _read_utf8(snapshot)
    path = preferred if preferred.is_file() else snapshot
    raw, text = _read_utf8(path)
    if _sha256(raw) != _sha256(snapshot_raw):
        raise TextEditLedgerError(
            f"{preferred}: differs from pinned vendored source {vendored}"
        )
    return raw, text


def _canonical_records(
    repo_root: Path, locale: str, original_path: Path, original_text: str
):
    if locale == "ja":
        _, controls_text = _authorized_source(
            repo_root, Path(JP_CONTROLS_PATH), VENDORED_JP_CONTROLS_PATH
        )
        aliases = _aliases_from_text(controls_text)
        original = {
            item.id: normalize_source_controls(
                item.text, dialect=SOURCE_DIALECT_JAPANESE, aliases=aliases
            )
            for item in parse_hash_indexed(
                original_text, source_name=original_path.as_posix()
            )
        }
    else:
        _, controls_text = _authorized_source(
            repo_root, Path(JP_CONTROLS_PATH), VENDORED_JP_CONTROLS_PATH
        )
        aliases = dict(_aliases_from_text(controls_text))
        aliases.update(FE8CN_NAMED_CONTROL_ALIASES)
        original = {
            item.id: normalize_source_controls(
                item.text,
                dialect=SOURCE_DIALECT_CHINESE,
                aliases=aliases,
            )
            for item in parse_fe8cn(
                original_text, source_name=original_path.as_posix()
            ).indexed
        }
    current_path = repo_root / f"texts/locales/{locale}/indexed.txt"
    current = {
        item.id: item.text
        for item in parse_hash_indexed(
            current_path.read_text(encoding="utf-8"),
            source_name=current_path.as_posix(),
        )
    }
    if set(original) != set(current):
        raise TextEditLedgerError(f"{locale}: original/current indexed ID domains differ")
    return original, current


def _override_metadata(repo_root: Path, locale: str) -> Mapping[int, Mapping[str, Any]]:
    source_key = "fe8j_indexed" if locale == "ja" else "fe8cn_source"
    records: Dict[int, Mapping[str, Any]] = {}
    for relative in OVERRIDE_PATHS:
        data = json.loads((repo_root / relative).read_text(encoding="utf-8"))
        source = data.get("sources", {}).get(source_key, {})
        for source_id, record in source.get("entries", {}).items():
            value = int(source_id, 16)
            if value in records:
                raise TextEditLedgerError(
                    f"{relative}: duplicate override metadata for {locale} {source_id}"
                )
            records[value] = record
    return records


def build_ledger(
    repo_root: Path,
    *,
    jp_original: Path = JP_ORIGINAL_PATH,
    cn_original: Path = CN_ORIGINAL_PATH,
) -> Dict[str, Any]:
    repo_root = Path(repo_root)
    sources = {}
    locales = {}
    for locale, source_path, vendored in (
        ("ja", Path(jp_original), VENDORED_JP_PATH),
        ("zh-Hans", Path(cn_original), VENDORED_CN_PATH),
    ):
        raw, original_text = _authorized_source(repo_root, source_path, vendored)
        original, current = _canonical_records(
            repo_root, locale, source_path, original_text
        )
        overrides = _override_metadata(repo_root, locale)
        rows = []
        unchanged_imports = []
        unchanged_raw_imports = []
        raw_exemptions = []
        for source_id in sorted(original):
            changed = original[source_id] != current[source_id]
            override = overrides.get(source_id)
            if changed and override is None:
                raise TextEditLedgerError(
                    f"{locale} 0x{source_id:04X}: changed source lacks reviewed provenance"
                )
            if changed:
                rows.append(
                    {
                    "category": (
                        "reviewed_indexed_override"
                        if changed else "unchanged_direct_import"
                    ),
                    "current_text": current[source_id],
                    "original_text": original[source_id],
                    "provenance": (
                        override["provenance"]
                        if changed
                        else "authorized direct import"
                    ),
                    "reason": override["reason"] if changed else "none",
                    "source_id": f"0x{source_id:04X}",
                    }
                )
            else:
                unchanged_imports.append((f"0x{source_id:04X}", original[source_id]))
        if locale == "zh-Hans":
            controls_raw, controls_text = _authorized_source(
                repo_root, Path(JP_CONTROLS_PATH), VENDORED_JP_CONTROLS_PATH
            )
            del controls_raw
            aliases = dict(_aliases_from_text(controls_text))
            aliases.update(FE8CN_NAMED_CONTROL_ALIASES)
            raw_source = parse_fe8cn(
                original_text, source_name=source_path.as_posix()
            ).raw_strings
            current_raw = json.loads(
                (repo_root / "texts/locales/zh-Hans/raw.json").read_text(encoding="utf-8")
            )["records"]
            source_by_import = {
                record.import_id: normalize_source_controls(
                    record.text,
                    dialect=SOURCE_DIALECT_CHINESE,
                    aliases=aliases,
                )
                for record in raw_source
            }
            if set(source_by_import) != {
                record["import_id"] for record in current_raw
            }:
                raise TextEditLedgerError("zh-Hans raw import domain drifted")
            for record in current_raw:
                source_text = source_by_import[record["import_id"]]
                current_text = record["text"]
                if current_text != source_text:
                    rows.append(
                        {
                        "category": (
                            "raw_source_edit"
                            if current_text != source_text
                            else "unchanged_raw_import"
                        ),
                        "current_text": current_text,
                        "original_text": source_text,
                        "provenance": record["provenance"],
                        "reason": (
                            "raw source differs; review required"
                            if current_text != source_text
                            else "authorized direct raw import"
                        ),
                        "source_id": record["import_id"],
                        }
                    )
                else:
                    unchanged_raw_imports.append((record["import_id"], source_text))
        else:
            raw_document = json.loads(
                (repo_root / "texts/locales/ja/raw.json").read_text(encoding="utf-8")
            )
            providers = raw_document["providers"]
            raw_provenance = {
                "source_revision": raw_document["source_revision"],
                "source_snapshot": raw_document["source_snapshot"],
            }
            for target_id, provider in sorted(providers.items()):
                raw_exemptions.append(
                    (
                        f"raw:{target_id}",
                        provider["text"],
                        {
                            **raw_provenance,
                            "symbol": provider["symbol"],
                        },
                    )
                )
        locales[locale] = {
            "changed_count": sum(
                row["category"] == "reviewed_indexed_override" for row in rows
            ),
            "direct_import_count": len(unchanged_imports),
            "raw_direct_import_count": len(unchanged_raw_imports),
            "raw_exempt_count": len(raw_exemptions),
            "records": rows,
            "source_count": len(original) + len(unchanged_raw_imports) + len(raw_exemptions),
            "unchanged_imports": {
                "count": len(unchanged_imports),
                "ids": [source_id for source_id, _ in unchanged_imports],
                "aggregate_sha256": _aggregate_import_hash(unchanged_imports),
            },
            "unchanged_raw_imports": {
                "count": len(unchanged_raw_imports),
                "ids": [source_id for source_id, _ in unchanged_raw_imports],
                "aggregate_sha256": _aggregate_import_hash(unchanged_raw_imports),
            },
            "raw_provider_exemptions": {
                "count": len(raw_exemptions),
                "ids": [source_id for source_id, _, _ in raw_exemptions],
                "aggregate_sha256": _aggregate_import_hash(
                    [(source_id, text) for source_id, text, _ in raw_exemptions]
                ),
                "provenance": (
                    "FE8J raw providers are separately pinned C/assembly/ROM "
                    "sources and are not jp_texts.txt records"
                ),
            },
        }
        sources[locale] = {
            "path": source_path.as_posix(),
            "sha256": _sha256(raw),
            "vendored_snapshot": vendored.as_posix(),
        }
    registry = json.loads(
        (repo_root / "texts/expansion/registry.json").read_text(encoding="utf-8")
    )
    expansion_catalogs = {
        locale: json.loads(
            (repo_root / f"texts/expansion/catalog.{locale}.json").read_text(
                encoding="utf-8"
            )
        )["strings"]
        for locale in ("ja", "zh-Hans")
    }
    expansion_entries = [
        {
            "key": message["key"],
            "ja": expansion_catalogs["ja"][message["key"]],
            "zh-Hans": expansion_catalogs["zh-Hans"][message["key"]],
        }
        for message in registry["messages"]
        if message["status"] == "active"
    ]
    return {
        "kind": "fe8u-localized-original-text-edit-ledger",
        "schema_version": 1,
        "policy": {
            "authored_expansion_strings_are_separate": True,
            "every_original_indexed_message_is_accounted_for": True,
            "source_edits_require_provenance": True,
        },
        "sources": sources,
        "locales": locales,
        "expansion_only": {
            "catalog_root": "texts/expansion",
            "entries": expansion_entries,
            "reason": "Expansion registry keys have no original FE8J/FE8CN indexed source.",
        },
    }


def render_markdown(ledger: Mapping[str, Any]) -> str:
    lines = [
        "# Original game-text edit ledger",
        "",
        "<!-- Generated by scripts.localization.game_locales.text_edit_ledger; do not edit. -->",
        "",
        "This ledger compares every indexed localized message with the authorized",
        "FE8J/FE8CN source inputs. Expansion-only registry strings are deliberately",
        "separate because they have no original indexed source.",
        "",
    ]
    for locale in ("ja", "zh-Hans"):
        data = ledger["locales"][locale]
        source = ledger["sources"][locale]
        lines.extend(
            [
                f"## {locale}",
                "",
                f"Source: `{source['path']}` (`{source['sha256']}`).",
                (
                    f"Direct indexed imports: {data['direct_import_count']}; "
                    f"edited originals: {data['changed_count']}; raw direct imports: "
                    f"{data['raw_direct_import_count']}; raw provenance exemptions: "
                    f"{data['raw_exempt_count']}."
                ),
                "",
                "| Source ID | Category | Original | Current | Provenance |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in data["records"]:
            if row["category"] != "reviewed_indexed_override":
                continue
            escape = lambda text: (
                text.replace("\\", "\\\\")
                .replace("|", "\\|")
                .replace("[", "\\[")
                .replace("]", "\\]")
                .replace("\n", "<br>")
            )
            lines.append(
                "| {source_id} | {category} | {original} | {current} | {provenance} |".format(
                    source_id=row["source_id"],
                    category=row["category"],
                    original=escape(row["original_text"]),
                    current=escape(row["current_text"]),
                    provenance=escape(
                        json.dumps(row["provenance"], ensure_ascii=False, sort_keys=True)
                    )
                    + "; "
                    + escape(row["reason"]),
                )
            )
        if not data["changed_count"]:
            lines.append("| — | — | — | — | No indexed edits. |")
        lines.append("")
    lines.extend(
        [
            "## Expansion-only strings",
            "",
            "All active `texts/expansion/` registry/catalog strings are new framework",
            "content and are intentionally not represented as edits to an original game",
            "message. The machine-readable report enumerates all "
            f"{len(ledger['expansion_only']['entries'])} such stable keys.",
            "",
        ]
    )
    return "\n".join(lines)


def write_ledger(repo_root: Path, **kwargs) -> Dict[str, bytes]:
    ledger = build_ledger(repo_root, **kwargs)
    report = _json_bytes(ledger)
    document = render_markdown(ledger).encode("utf-8")
    for path, content in (
        (Path(repo_root) / REPORT_PATH, report),
        (Path(repo_root) / DOC_PATH, document),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return {REPORT_PATH.as_posix(): report, DOC_PATH.as_posix(): document}


def check_ledger(repo_root: Path, **kwargs) -> Dict[str, bytes]:
    ledger = build_ledger(repo_root, **kwargs)
    expected = {
        REPORT_PATH.as_posix(): _json_bytes(ledger),
        DOC_PATH.as_posix(): render_markdown(ledger).encode("utf-8"),
    }
    for relative, content in expected.items():
        path = Path(repo_root) / relative
        if not path.is_file() or path.read_bytes() != content:
            raise TextEditLedgerError(f"{path}: generated edit ledger drifted")
    return expected


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "check"))
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--jp-original", type=Path, default=JP_ORIGINAL_PATH)
    parser.add_argument("--cn-original", type=Path, default=CN_ORIGINAL_PATH)
    args = parser.parse_args(argv)
    try:
        action = write_ledger if args.command == "generate" else check_ledger
        outputs = action(
            args.repo_root,
            jp_original=args.jp_original,
            cn_original=args.cn_original,
        )
    except TextEditLedgerError as error:
        print(f"error: {error}")
        return 1
    verb = "generated" if args.command == "generate" else "checked"
    print(f"{verb} text-edit ledger: {', '.join(sorted(outputs))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
