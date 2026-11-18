"""Audit compact display aliases against real fixed-width UI allocations."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Mapping, Sequence, Tuple

from scripts.generated_data.diagnostics import GeneratedDataError
from scripts.generated_data.schema import SchemaInputs
from scripts.localization.legacy_spacing import LEGACY_SJIS_SPACE_SCALAR

from .ending_metrics import (
    EndingLayoutError,
    _ascii_widths,
    _cjk_widths,
    _line_width,
)

if TYPE_CHECKING:
    from scripts.localization.game_catalog.compiled_fonts import SelectedFontMetrics


ALIASES_PATH = Path("texts/locales/fixed_width_display_aliases.json")
SURFACE_VALUES = {
    "character_name_40": 1,
    "class_name_64": 2,
    "item_name_56": 3,
}
SURFACES = {
    "character_name_40": {
        "allocation_pixels": 40,
        "api": "GetCharacterDisplayNameForWidth",
        "category": "character",
        "data_path": Path("src/data_characters.c"),
        "call_sites": (
            {
                "allocation": "InitText(&gUnitlistscreen_2[i], 5);",
                "allocation_function": "UnitList_SetupDisplay",
                "fallback": (
                    "GetStringFromIndex(\n"
                    "                gSortedUnits[unitNum]->unit->"
                    "pCharacterData->nameTextId)"
                ),
                "path": Path("src/unitlistscreen.c"),
                "resolver_function": "UnitList_PutRow",
                "resolver": (
                    "GetCharacterDisplayNameForWidth(\n"
                    "                gSortedUnits[unitNum]->unit->"
                    "pCharacterData, 40)"
                ),
            },
        ),
    },
    "class_name_64": {
        "allocation_pixels": 64,
        "api": "GetClassDisplayNameForWidth",
        "category": "class",
        "data_path": Path("src/data_classes.c"),
        "call_sites": (
            {
                "allocation": (
                    "InitText(&texts[TEXT_PREPITEM_CLASS], 8);"
                ),
                "allocation_function": "PrepItemUse_InitDisplay",
                "fallback": (
                    "str = GetStringFromIndex(unit->pClassData->nameTextId);"
                ),
                "path": Path("src/prep_itemuse.c"),
                "resolver": (
                    "str = GetClassDisplayNameForWidth(unit->pClassData, 64);"
                ),
                "resolver_function": "DrawPrepScreenItemUseStatLabels",
            },
        ),
    },
    "item_name_56": {
        "allocation_pixels": 56,
        "api": "GetItemDisplayNameForWidth",
        "category": "item",
        "data_path": Path("src/data_items.c"),
        "call_sites": (
            {
                "allocation": (
                    "InitTextDb(&proc->itemNameText, 7);"
                ),
                "allocation_function": "BattleForecast_Init",
                "fallback": "char* str = GetItemName(itemIdx);",
                "path": Path("src/bksel.c"),
                "resolver": (
                    "char* str = GetItemDisplayNameForWidth(itemIdx, 56);"
                ),
                "resolver_function": "PutBattleForecastItemName",
            },
        ),
    },
}
_TARGET_RE = re.compile(r"0x[0-9A-F]{4}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class FixedWidthLabelError(ValueError):
    """Raised when a final localized label can overrun a fixed UI field."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load_json(path: Path) -> Tuple[Any, bytes]:
    raw = Path(path).read_bytes()
    try:
        return json.loads(raw.decode("utf-8")), raw
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FixedWidthLabelError(f"{path}: expected strict UTF-8 JSON") from error


def load_fixed_width_aliases(
    path: Path = ALIASES_PATH,
) -> Dict[str, Dict[str, Dict[int, str]]]:
    data, _ = _load_json(path)
    if not isinstance(data, dict):
        raise FixedWidthLabelError(f"{path}: alias registry root must be an object")
    if data.get("schema_version") != 1:
        raise FixedWidthLabelError(f"{path}: schema_version must be 1")
    if data.get("kind") != "fe8u-fixed-width-display-aliases":
        raise FixedWidthLabelError(f"{path}: alias registry kind is invalid")
    if data.get("policy") != {
        "aliases_are_surface_specific": True,
        "canonical_catalog_payloads_unchanged": True,
        "every_canonical_overflow_requires_alias": True,
        "non_overflow_aliases_forbidden": True,
    }:
        raise FixedWidthLabelError(f"{path}: alias registry policy drifted")
    raw_aliases = data.get("aliases")
    if not isinstance(raw_aliases, dict) or set(raw_aliases) != {"ja", "zh-Hans"}:
        raise FixedWidthLabelError(f"{path}: aliases must contain exact JA/ZH maps")

    aliases: Dict[str, Dict[str, Dict[int, str]]] = {}
    for locale, raw_surfaces in raw_aliases.items():
        if not isinstance(raw_surfaces, dict) or set(raw_surfaces) != set(SURFACES):
            raise FixedWidthLabelError(
                f"{path}: {locale} must contain every fixed-width surface"
            )
        locale_aliases = {}
        for surface, raw_entries in raw_surfaces.items():
            if not isinstance(raw_entries, dict):
                raise FixedWidthLabelError(
                    f"{path}: {locale}/{surface} aliases must be an object"
                )
            entries = {}
            for target, text in raw_entries.items():
                if not isinstance(target, str) or not _TARGET_RE.fullmatch(target):
                    raise FixedWidthLabelError(
                        f"{path}: {locale}/{surface} target {target!r} is invalid"
                    )
                if (
                    not isinstance(text, str)
                    or not text
                    or "\n" in text
                    or "\r" in text
                    or "[CTRL:" in text
                ):
                    raise FixedWidthLabelError(
                        f"{path}: {locale}/{surface}/{target} alias is invalid"
                    )
                entries[int(target, 16)] = text
            locale_aliases[surface] = entries
        aliases[locale] = locale_aliases
    return aliases


def _message_ids(path: Path) -> Tuple[int, ...]:
    text = path.read_text(encoding="utf-8")
    ids = tuple(
        sorted(
            {
                int(value, 0)
                for value in re.findall(
                    r"\.nameTextId\s*=\s*(0x[0-9A-Fa-f]+|\d+)",
                    text,
                )
            }
        )
    )
    if not ids:
        raise FixedWidthLabelError(f"{path}: no nameTextId values found")
    return ids


def _function_body(source: str, function: str, *, path: Path) -> str:
    match = re.search(
        rf"\b{re.escape(function)}\s*\([^;]*?\)\s*\{{",
        source,
        flags=re.DOTALL,
    )
    if match is None:
        raise FixedWidthLabelError(
            f"{path}: fixed-width audit function {function} is missing"
        )
    depth = 1
    index = match.end()
    while index < len(source) and depth:
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
        index += 1
    if depth:
        raise FixedWidthLabelError(
            f"{path}: fixed-width audit function {function} is unterminated"
        )
    return source[match.start() : index]


def _source_contract(repo_root: Path) -> Dict[str, Any]:
    inputs = {}
    api_counts = {specification["api"]: 0 for specification in SURFACES.values()}
    for surface, specification in SURFACES.items():
        call_sites = []
        for call_site in specification["call_sites"]:
            path = repo_root / call_site["path"]
            raw = path.read_bytes()
            text = raw.decode("utf-8")
            allocation_body = _function_body(
                text,
                call_site["allocation_function"],
                path=path,
            )
            resolver_body = _function_body(
                text,
                call_site["resolver_function"],
                path=path,
            )
            if allocation_body.count(call_site["allocation"]) != 1:
                raise FixedWidthLabelError(
                    f"{path}: expected one {surface} allocation in "
                    f"{call_site['allocation_function']}"
                )
            for field in ("resolver", "fallback"):
                if resolver_body.count(call_site[field]) != 1:
                    raise FixedWidthLabelError(
                        f"{path}: expected one {surface} {field} in "
                        f"{call_site['resolver_function']}"
                    )
            resolver_offset = resolver_body.index(call_site["resolver"])
            fallback_offset = resolver_body.index(call_site["fallback"])
            guard_offset = resolver_body.rfind(
                "#if FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED",
                0,
                resolver_offset,
            )
            else_offset = resolver_body.find("#else", resolver_offset)
            endif_offset = resolver_body.find("#endif", fallback_offset)
            if not (
                guard_offset >= 0
                and resolver_offset < else_offset < fallback_offset < endif_offset
            ):
                raise FixedWidthLabelError(
                    f"{path}: {surface} resolver/fallback must be guarded by "
                    "FE8_LOCALIZED_GAME_TEXT_CJK_PROFILE_ENABLED"
                )
            api_counts[specification["api"]] += text.count(
                specification["api"] + "("
            )
            call_sites.append(
                {
                    "allocation_anchor": call_site["allocation"],
                    "allocation_function": call_site["allocation_function"],
                    "fallback_anchor": call_site["fallback"],
                    "path": call_site["path"].as_posix(),
                    "resolver_anchor": call_site["resolver"],
                    "resolver_function": call_site["resolver_function"],
                    "sha256": _sha256(raw),
                }
            )
        data_path = repo_root / specification["data_path"]
        data_raw = data_path.read_bytes()
        inputs[surface] = {
            "allocation_pixels": specification["allocation_pixels"],
            "call_sites": call_sites,
            "category": specification["category"],
            "data_source": {
                "path": specification["data_path"].as_posix(),
                "sha256": _sha256(data_raw),
            },
            "target_ids": [
                f"0x{target_id:04X}" for target_id in _message_ids(data_path)
            ],
        }
    for api, call_count in api_counts.items():
        definition_count = sum(
            path.read_text(encoding="utf-8").count(api + "(")
            for path in (repo_root / "src").glob("*.c")
        )
        if definition_count != call_count + 1:
            raise FixedWidthLabelError(
                f"{api}: expected one definition plus {call_count} audited "
                f"call(s), found {definition_count}"
            )
    return inputs


def _records(
    *,
    locale: str,
    surface: str,
    payloads: Mapping[int, str],
    aliases: Mapping[int, str],
    target_ids: Sequence[int],
    allocation_pixels: int,
    ascii_widths: Mapping[int, int],
    cjk_widths: Mapping[int, int],
    payload_widths: Mapping[int, int] | None = None,
) -> Tuple[Dict[str, Any], ...]:
    target_set = set(target_ids)
    stale_aliases = sorted(set(aliases) - target_set)
    if stale_aliases:
        raise FixedWidthLabelError(
            f"{locale}/{surface}: aliases target non-{SURFACES[surface]['category']} "
            + ", ".join(f"0x{target_id:04X}" for target_id in stale_aliases)
        )

    records = []
    overflow_ids = set()
    display_to_targets: Dict[Tuple[str, int], list[int]] = {}
    for target_id in target_ids:
        canonical = payloads.get(target_id)
        if not isinstance(canonical, str) or not canonical:
            raise FixedWidthLabelError(
                f"{locale}/{surface}: 0x{target_id:04X} has no final payload"
            )
        try:
            canonical_width = (
                payload_widths[target_id]
                if payload_widths is not None and target_id in payload_widths
                else _line_width(
                    canonical,
                    locale=locale,
                    ascii_widths=ascii_widths,
                    cjk_widths=cjk_widths,
                )
            )
        except EndingLayoutError as error:
            raise FixedWidthLabelError(
                f"{locale}/{surface}/0x{target_id:04X}: {error}"
            ) from error
        alias = aliases.get(target_id)
        if canonical_width > allocation_pixels:
            overflow_ids.add(target_id)
            if alias is None:
                raise FixedWidthLabelError(
                    f"{locale}/{surface}/0x{target_id:04X}: canonical width "
                    f"{canonical_width}px exceeds {allocation_pixels}px without "
                    "a display alias"
                )
        elif alias is not None:
            raise FixedWidthLabelError(
                f"{locale}/{surface}/0x{target_id:04X}: non-overflow alias is "
                "forbidden"
            )
        display = alias if alias is not None else canonical
        try:
            display_width = canonical_width if alias is None else _line_width(
                display,
                locale=locale,
                ascii_widths=ascii_widths,
                cjk_widths=cjk_widths,
            )
        except EndingLayoutError as error:
            raise FixedWidthLabelError(
                f"{locale}/{surface}/0x{target_id:04X} alias: {error}"
            ) from error
        if display_width > allocation_pixels:
            raise FixedWidthLabelError(
                f"{locale}/{surface}/0x{target_id:04X}: display width "
                f"{display_width}px exceeds {allocation_pixels}px"
            )
        display_to_targets.setdefault((display, display_width), []).append(target_id)
        records.append(
            {
                "alias_applied": alias is not None,
                "canonical_text": canonical,
                "canonical_width": canonical_width,
                "display_text": display,
                "display_width": display_width,
                "target_id": f"0x{target_id:04X}",
            }
        )
    if set(aliases) != overflow_ids:
        raise FixedWidthLabelError(
            f"{locale}/{surface}: alias set does not exactly match overflows"
        )
    collisions = {
        display: targets
        for display, targets in display_to_targets.items()
        if len(targets) > 1 and any(target in aliases for target in targets)
    }
    if collisions:
        detail = "; ".join(
            f"{text!r}@{width}px="
            + ",".join(f"0x{target_id:04X}" for target_id in targets)
            for (text, width), targets in sorted(collisions.items())
        )
        raise FixedWidthLabelError(
            f"{locale}/{surface}: compact display aliases collide: {detail}"
        )
    return tuple(records)


def build_fixed_width_label_metrics(
    repo_root: Path,
    *,
    localized_payloads: Mapping[str, Mapping[int, str | bytes]],
    aliases_path: Path | None = None,
    fallback_payloads: Mapping[int, bytes] | None = None,
    schema_inputs: SchemaInputs | None = None,
    font_metrics: SelectedFontMetrics | None = None,
) -> Dict[str, Any]:
    repo_root = Path(repo_root)
    aliases_path = (
        repo_root / ALIASES_PATH if aliases_path is None else Path(aliases_path)
    )
    aliases, alias_raw = _load_json(aliases_path)
    parsed_aliases = load_fixed_width_aliases(aliases_path)
    if not _SHA256_RE.fullmatch(_sha256(alias_raw)):
        raise FixedWidthLabelError("alias registry SHA-256 generation failed")
    source_contract = _source_contract(repo_root)
    if schema_inputs is not None:
        for surface, specification in SURFACES.items():
            table = {"character": "characters", "class": "classes", "item": "items"}[
                specification["category"]
            ]
            if table not in schema_inputs.sources:
                continue
            schema = schema_inputs.resolve(table)
            source = Path(schema_inputs.source(schema))
            if not source.is_absolute():
                source = repo_root / source
            try:
                records = schema.load_records(str(source))
            except GeneratedDataError as error:
                raise FixedWidthLabelError(str(error)) from error
            targets = {
                record.name_text_id for record in records
                if record.name_text_id or record.name_text_loc is not None
            }
            if fallback_payloads is not None and not targets <= set(fallback_payloads):
                raise FixedWidthLabelError(f"{table}: selected name IDs are outside the active set")
            source_contract[surface]["target_ids"] = [
                f"0x{target:04X}" for target in sorted(targets)
            ]
            source_contract[surface]["data_source"] = {
                "path": str(schema_inputs.source(schema)), "sha256": _sha256(source.read_bytes()),
            }
    if font_metrics is None:
        ascii_widths = _ascii_widths(repo_root)
    else:
        try:
            ascii_widths = font_metrics["ascii"]["system"]
        except KeyError as error:
            raise FixedWidthLabelError("selected native system-font metrics are unavailable") from error
    locales = {}
    font_inputs = {}
    total_aliases = 0
    total_labels = 0
    for locale in ("ja", "zh-Hans", "fr", "de", "es", "it"):
        if locale not in localized_payloads:
            continue
        if font_metrics is None and locale in ("ja", "zh-Hans"):
            cjk_widths, font_input = _cjk_widths(repo_root, locale)
        elif font_metrics is None:
            codepoint_path = repo_root / "graphics/fonts/eu/eu.system.codepoints.u32le"
            width_path = repo_root / "graphics/fonts/eu/eu.system.widths.u8"
            codepoint_data = codepoint_path.read_bytes()
            width_data = width_path.read_bytes()
            if len(codepoint_data) != len(width_data) * 4:
                raise FixedWidthLabelError(
                    f"{locale}: EU codepoint/width count mismatch"
                )
            cjk_widths = {
                int.from_bytes(codepoint_data[index * 4:index * 4 + 4], "little"): width
                for index, width in enumerate(width_data)
            }
            font_input = {
                "codepoints_path": codepoint_path.relative_to(repo_root).as_posix(),
                "widths_path": width_path.relative_to(repo_root).as_posix(),
            }
        else:
            try:
                cjk_widths = font_metrics["localized"][locale]["system"]
            except KeyError as error:
                raise FixedWidthLabelError(
                    f"{locale}: selected native system-font metrics are unavailable"
                ) from error
            font_input = {
                "native": font_metrics["inputs"],
                "locale": locale,
                "style": "system",
                "glyph_count": len(cjk_widths),
            }
        font_inputs[locale] = font_input
        surfaces = {}
        for surface, specification in SURFACES.items():
            contract = source_contract[surface]
            target_ids = tuple(
                int(target, 16) for target in contract["target_ids"]
                if fallback_payloads is None or int(target, 16) in fallback_payloads
            )
            payloads = localized_payloads[locale]
            effective_payloads = {}
            payload_widths = {}
            for target in target_ids:
                payload = payloads.get(target)
                if payload is None and fallback_payloads is not None:
                    payload = fallback_payloads.get(target)
                if payload is None:
                    continue
                if isinstance(payload, bytes):
                    from scripts.localization.game_catalog.control_streams import (
                        ControlStreamError,
                        tokenize_payload,
                    )

                    try:
                        tokens = tokenize_payload(payload, source_name=f"name 0x{target:04X}")
                    except (ControlStreamError, ValueError) as error:
                        raise FixedWidthLabelError(
                            f"name 0x{target:04X}: invalid rendered payload"
                        ) from error
                    if any(
                        token.kind == "extended"
                        and token.scalar in (0x12, 0x13, 0x14, 0x15, 0x20, 0x22)
                        for token in tokens
                    ):
                        raise FixedWidthLabelError(
                            f"name 0x{target:04X}: substitutions are unsupported in fixed labels"
                        )
                    if any(
                        token.kind == "control" and token.control in (0x01, 0x02)
                        for token in tokens
                    ):
                        raise FixedWidthLabelError(
                            f"name 0x{target:04X}: line controls are unsupported in fixed labels"
                        )
                    from .width_contract import TextWidthContractError, WidthMetrics, _scalar_width

                    scalars = tuple(
                        token.scalar for token in tokens
                        if token.kind == "scalar" and token.scalar is not None
                    )
                    try:
                        metrics = WidthMetrics(locale, "system", ascii_widths, cjk_widths)
                        payload_widths[target] = sum(
                            _scalar_width(scalar, metrics) for scalar in scalars
                        )
                    except TextWidthContractError as error:
                        raise FixedWidthLabelError(f"name 0x{target:04X}: {error}") from error
                    payload = "".join(
                        "\u3000" if scalar == LEGACY_SJIS_SPACE_SCALAR else chr(scalar)
                        for scalar in scalars
                    )
                effective_payloads[target] = payload
            scoped_aliases = (
                parsed_aliases[locale][surface]
                if locale in parsed_aliases else {}
            )
            if fallback_payloads is not None:
                scoped_aliases = {
                    target: text for target, text in scoped_aliases.items()
                    if target in fallback_payloads and target in payloads
                }
            records = _records(
                locale=locale,
                surface=surface,
                payloads=effective_payloads,
                aliases=scoped_aliases,
                target_ids=target_ids,
                allocation_pixels=specification["allocation_pixels"],
                ascii_widths=ascii_widths,
                cjk_widths=cjk_widths,
                payload_widths=payload_widths,
            )
            alias_count = sum(record["alias_applied"] for record in records)
            total_aliases += alias_count
            total_labels += len(records)
            surfaces[surface] = {
                "allocation_pixels": specification["allocation_pixels"],
                "alias_count": alias_count,
                "label_count": len(records),
                "overflow_count": 0,
                "records": list(records),
            }
        locales[locale] = {"surfaces": surfaces}
    return {
        "alias_registry": {
            "path": aliases_path.relative_to(repo_root).as_posix(),
            "sha256": _sha256(alias_raw),
        },
        "font_inputs": font_inputs,
        "kind": "fe8u-fixed-width-label-metrics",
        "locales": locales,
        "schema_version": 1,
        "source_contract": source_contract,
        "summary": {
            "alias_count": total_aliases,
            "label_count": total_labels,
            "locale_count": len(locales),
            "overflow_count": 0,
            "surface_count": len(SURFACES),
        },
    }
