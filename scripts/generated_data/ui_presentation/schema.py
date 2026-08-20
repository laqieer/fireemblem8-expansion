"""Validated presentation manifests for chapter and screen UI contexts."""

from __future__ import annotations

import json
from pathlib import Path

from ..schema import DependencyGraph, TableSchema
from ..diagnostics import GeneratedDataError

SCHEMA_NAME = "ui_presentation"
SCHEMA_VERSION = 1
DEFAULT_SOURCE = "src/data/ui_presentation.json"
DEFAULT_INVENTORY = "reports/generated_data_ui_presentation_inventory.md"

KIND_IDS = {"chapter_title": 0, "screen": 1}
MAX_VRAM = 0x8000
MAX_PALETTES = 16
MAX_OAM = 128
MAX_RECORDS = 32


def _encode_fallback_text(value, path, diagnostics):
    if not isinstance(value, str) or not value:
        diagnostics.add(GeneratedDataError("fallback_text must be non-empty", reference_path=path))
        return None

    if "\x00" in value:
        diagnostics.add(GeneratedDataError(
            "fallback_text must not contain NUL; C strings cannot represent embedded NUL bytes",
            reference_path=path,
        ))
        return None

    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        diagnostics.add(GeneratedDataError(
            "fallback_text must contain valid Unicode scalar values",
            reference_path=path,
        ))
        return None


def _escape_c_string(value):
    """Return a deterministic C string literal body for a Unicode value."""
    encoded = value.encode("utf-8")
    escapes = {
        0x07: r"\a",
        0x08: r"\b",
        0x09: r"\t",
        0x0A: r"\n",
        0x0B: r"\v",
        0x0C: r"\f",
        0x0D: r"\r",
        0x22: r"\"",
        0x5C: r"\\",
    }
    result = []
    for byte in encoded:
        if byte in escapes:
            result.append(escapes[byte])
        elif 0x20 <= byte <= 0x7E:
            result.append(chr(byte))
        else:
            result.append("\\{:03o}".format(byte))
    return "".join(result)


def _require_int(value, field, path):
    if isinstance(value, bool) or not isinstance(value, int):
        raise GeneratedDataError("{} must be an integer".format(field), reference_path=path)
    return value


def load_records(source_path):
    path = Path(source_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        raise GeneratedDataError(
            "manifest must be an object with version {}".format(SCHEMA_VERSION),
            reference_path=str(path),
        )
    records = data.get("contexts")
    if not isinstance(records, list):
        raise GeneratedDataError("contexts must be an array", reference_path=str(path))
    return records


def _load_localization():
    from scripts.localization.catalog import load_catalog

    return load_catalog()


def validate(records, diagnostics, dependency_records=None):
    catalog = _load_localization()
    active = catalog.active_by_key()

    if len(records) > MAX_RECORDS:
        diagnostics.add(GeneratedDataError(
            "contexts has {} records; fixed manifest capacity is {}".format(
                len(records), MAX_RECORDS
            ),
            reference_path="contexts",
        ))

    for index, record in enumerate(records):
        ref = "contexts[{}]".format(index)
        if not isinstance(record, dict):
            diagnostics.add(GeneratedDataError("record must be an object", reference_path=ref))
            continue

        if record.get("id") != index:
            diagnostics.add(GeneratedDataError("ids must be dense and sorted", reference_path=ref))

        kind = record.get("kind")
        if kind not in KIND_IDS:
            diagnostics.add(GeneratedDataError("unknown kind {!r}".format(kind), reference_path=ref))

        chapter_id = _require_int(record.get("chapter_id"), "chapter_id", ref)
        if not 0 <= chapter_id <= 0xFF:
            diagnostics.add(GeneratedDataError("chapter_id must fit u8", reference_path=ref))

        title_key = record.get("title_key")
        if title_key not in active:
            diagnostics.add(GeneratedDataError(
                "title_key {!r} is not an active localization id".format(title_key),
                reference_path=ref,
            ))

        _encode_fallback_text(record.get("fallback_text"), ref, diagnostics)

        asset_id = record.get("asset_id")
        if asset_id is not None and not 0 <= _require_int(asset_id, "asset_id", ref) <= 0xFFFF:
            diagnostics.add(GeneratedDataError("asset_id must fit u16", reference_path=ref))

        resource = record.get("resources", {})
        if not isinstance(resource, dict):
            diagnostics.add(GeneratedDataError("resources must be an object", reference_path=ref))
            continue
        vram = _require_int(resource.get("vram_bytes", 0), "resources.vram_bytes", ref)
        palettes = _require_int(resource.get("palette_slots", 0), "resources.palette_slots", ref)
        oam = _require_int(resource.get("oam_entries", 0), "resources.oam_entries", ref)
        required = resource.get("required", False)
        if not isinstance(required, bool):
            diagnostics.add(GeneratedDataError("resources.required must be boolean", reference_path=ref))
        if not 0 <= vram <= MAX_VRAM:
            diagnostics.add(GeneratedDataError("VRAM requirement exceeds 0x8000", reference_path=ref))
        if not 0 <= palettes <= MAX_PALETTES:
            diagnostics.add(GeneratedDataError("palette requirement exceeds 16 slots", reference_path=ref))
        if not 0 <= oam <= MAX_OAM:
            diagnostics.add(GeneratedDataError("OAM requirement exceeds 128 entries", reference_path=ref))
        if required and asset_id is None:
            diagnostics.add(GeneratedDataError(
                "required resources need an asset_id",
                reference_path=ref,
            ))


def generate_c(records, source_path):
    catalog = _load_localization()
    active = catalog.active_by_key()
    lines = [
        '#include "expansion_ui_presentation.h"',
        '#include "expansion_ui_presentation_manifest.h"',
        "",
        "struct ExpansionUiPresentationManifest const gExpansionUiPresentationManifest[] =",
        "{",
    ]
    for record in records:
        resource = record.get("resources", {})
        flags = 0
        if record.get("asset_id") is not None:
            flags |= 0x01
        if resource.get("required", False):
            flags |= 0x02
        fallback = _escape_c_string(record["fallback_text"])
        title_id = active[record["title_key"]].id
        asset_id = record.get("asset_id") or 0
        lines.append(
            "    {%d, %d, %d, %d, %d, %d, %d, %d, %d, \"%s\"},"
            % (
                record["id"],
                KIND_IDS[record["kind"]],
                record["chapter_id"],
                flags,
                title_id,
                asset_id,
                resource.get("vram_bytes", 0),
                resource.get("palette_slots", 0),
                resource.get("oam_entries", 0),
                fallback,
            )
        )
    lines.extend([
        "};",
        "",
        "u8 const gExpansionUiPresentationManifestCount = %d;" % len(records),
        "",
    ])
    return "\n".join(lines)


class UiPresentationTableSchema(TableSchema):
    name = SCHEMA_NAME
    version = SCHEMA_VERSION
    default_source = DEFAULT_SOURCE
    default_hand_source = None
    default_output_name = "expansion_ui_presentation_manifest.c"
    default_inventory_path = DEFAULT_INVENTORY
    record_budget = MAX_RECORDS
    record_budget_reason = "fixed runtime manifest capacity"

    def dependencies(self):
        return ("texts/expansion/registry.json", "texts/expansion/catalog.*.json")

    def load_records(self, source_path):
        return load_records(source_path)

    def validate(self, records, diagnostics, dependency_records=None):
        validate(records, diagnostics, dependency_records)

    def generate_c(self, records, source_path):
        return generate_c(records, source_path)

    def build_inventory(self, records):
        return (
            "# UI presentation manifest inventory\n\n"
            "- Schema: `{}.{}`\n"
            "- Records: `{}/32`\n"
            "- Resource bounds: VRAM `0x8000`, palettes `16`, OAM `128`\n"
            "- Missing optional assets use the localized/static fallback.\n"
        ).format(SCHEMA_NAME, SCHEMA_VERSION, len(records))


def dependency_graph():
    graph = DependencyGraph()
    for dependency in UiPresentationTableSchema().dependencies():
        graph.add_dependency(SCHEMA_NAME, dependency)
    return graph
