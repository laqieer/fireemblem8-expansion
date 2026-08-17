"""Build deterministic CJK corpora and FEBuilderGBA schema-v1 manifests."""

from __future__ import annotations

import hashlib
import json
import re
import struct
import unicodedata
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from scripts.localization.game_locales.parsers import parse_hash_indexed
from scripts.localization.game_locales.width_contract import (
    classify_targets,
    load_width_registry,
)

LOCALES = ("ja", "zh-Hans")
STYLES = ("system", "talk")
RUNTIME_USAGE_PATH = "fonts/cjk/runtime_usage.json"
WIDTH_REGISTRY_PATH = Path("texts/locales/mapping/text_width_contexts.json")
TEXT_TOKEN_RE = re.compile(r"\[[^\[\]\r\n]+\]")
PLACEHOLDER_RE = re.compile(r"\{[0-9]+\}")
NOTO_COMMIT = "f8d157532fbfaeda587e826d4cd5b21a49186f7c"
NOTO_RAW_ROOT = (
    "https://raw.githubusercontent.com/googlefonts/noto-cjk/" + NOTO_COMMIT
)
LICENSE_SOURCE_URL = NOTO_RAW_ROOT + "/Sans/LICENSE"

FONT_SOURCES = {
    "ja": {
        "path": "fonts/cjk/upstream/NotoSansJP-Regular.otf",
        "sha256": "dff723ba59d57d136764a04b9b2d03205544f7cd785a711442d6d2d085ac5073",
        "byte_length": 4533028,
        "family": "Noto Sans JP",
        "full_name": "Noto Sans JP",
        "version": "Version 2.004;hotconv 1.0.118;makeotfexe 2.5.65603",
        "copyright": "© 2014-2021 Adobe (http://www.adobe.com/).",
        "source_url": (
            NOTO_RAW_ROOT + "/Sans/SubsetOTF/JP/NotoSansJP-Regular.otf"
        ),
    },
    "zh-Hans": {
        "path": "fonts/cjk/upstream/NotoSansSC-Regular.otf",
        "sha256": "faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9",
        "byte_length": 8331336,
        "family": "Noto Sans SC",
        "full_name": "Noto Sans SC",
        "version": "Version 2.004;hotconv 1.0.118;makeotfexe 2.5.65603",
        "copyright": "© 2014-2021 Adobe (http://www.adobe.com/).",
        "source_url": (
            NOTO_RAW_ROOT + "/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf"
        ),
    },
}

LICENSE_SOURCE = {
    "path": "fonts/cjk/licenses/Noto-CJK-OFL-1.1.txt",
    "license_id": "OFL-1.1",
    "sha256": "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2",
    "source_url": LICENSE_SOURCE_URL,
}


class CjkFontError(ValueError):
    """Raised when a CJK font input or generated artifact violates its contract."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def scalar_text(value: int) -> str:
    return f"U+{value:04X}"


def _input_record(path: Path, root: Path) -> Dict[str, object]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(root).as_posix(),
        "byte_count": len(data),
        "sha256": sha256_bytes(data),
    }


def _strip_tokens(text: str, *, expansion: bool = False) -> str:
    text = TEXT_TOKEN_RE.sub("", text)
    if expansion:
        text = PLACEHOLDER_RE.sub("", text)
    return unicodedata.normalize("NFC", text)


def _load_expansion_strings(root: Path, locale: str) -> Tuple[List[str], Dict[str, object]]:
    registry_path = root / "texts/expansion/registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    active = [
        row["key"]
        for row in registry["messages"]
        if row["status"] == "active"
    ]
    catalogs: Dict[str, Mapping[str, str]] = {}
    catalog_paths = sorted((root / "texts/expansion").glob("catalog.*.json"))
    for path in catalog_paths:
        catalog = json.loads(path.read_text(encoding="utf-8"))
        catalog_locale = catalog["locale"]
        if catalog_locale in catalogs:
            raise CjkFontError(f"duplicate expansion catalog locale {catalog_locale}")
        catalogs[catalog_locale] = catalog["strings"]
    if "en" not in catalogs:
        raise CjkFontError("texts/expansion/catalog.en.json is required as fallback")

    localized = catalogs.get(locale, {})
    strings: List[str] = []
    fallback_count = 0
    for key in active:
        if key in localized:
            strings.append(localized[key])
        elif key in catalogs["en"]:
            strings.append(catalogs["en"][key])
            fallback_count += 1
        else:
            raise CjkFontError(f"active expansion key {key!r} has no fallback text")
    metadata = {
        "active_key_count": len(active),
        "catalogs": [path.relative_to(root).as_posix() for path in catalog_paths],
        "fallback_catalog": "en",
        "fallback_string_count": fallback_count,
        "placeholder_count": sum(
            len(PLACEHOLDER_RE.findall(text)) for text in strings
        ),
    }
    return strings, metadata


def _load_game_authored_strings(
    root: Path, locale: str
) -> Tuple[List[str], Dict[str, object]]:
    path = root / f"texts/locales/authored/catalog.{locale}.json"
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if (
        catalog.get("kind") != "fe8u-game-authored-catalog"
        or catalog.get("locale") != locale
        or catalog.get("schema_version") != 1
        or not isinstance(catalog.get("strings"), dict)
    ):
        raise CjkFontError(f"{path}: canonical authored catalog is malformed")
    strings = catalog["strings"]
    if catalog.get("target_count") != len(strings):
        raise CjkFontError(f"{path}: canonical authored target_count drift")
    return list(strings.values()), {
        "catalog": path.relative_to(root).as_posix(),
        "string_count": len(strings),
        "source_queue_sha256": catalog["source_queue"]["sha256"],
    }


def _locale_texts(root: Path, locale: str) -> Tuple[Dict[str, List[str]], Dict[str, object]]:
    indexed_path = root / f"texts/locales/{locale}/indexed.txt"
    indexed = parse_hash_indexed(
        indexed_path.read_text(encoding="utf-8"),
        source_name=indexed_path.as_posix(),
    )
    sources: Dict[str, List[str]] = {
        "indexed": [message.text for message in indexed],
    }
    if locale == "ja":
        raw_path = root / "texts/locales/ja/raw.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        sources["raw"] = [
            provider["text"] for provider in raw["providers"].values()
        ]
    else:
        raw_path = root / "texts/locales/zh-Hans/raw.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        seen: Set[str] = set()
        raw_texts = []
        for record in raw["records"]:
            import_id = record["import_id"]
            if import_id in seen:
                raise CjkFontError(f"duplicate raw import_id {import_id}")
            seen.add(import_id)
            raw_texts.append(record["text"])
        sources["raw"] = raw_texts

    expansion, expansion_metadata = _load_expansion_strings(root, locale)
    authored_game, authored_game_metadata = _load_game_authored_strings(root, locale)
    sources["expansion"] = expansion
    sources["authored_game"] = authored_game
    return sources, {
        "authored_game": authored_game_metadata,
        "expansion": expansion_metadata,
    }


def _scalar_sets(texts: Iterable[str], *, expansion: bool = False) -> Set[int]:
    scalars: Set[int] = set()
    for text in texts:
        normalized = _strip_tokens(text, expansion=expansion)
        scalars.update(ord(character) for character in normalized)
    return scalars


def _description_target_ids(root: Path, target_count: int) -> Set[int]:
    """Return all data-owned descriptions, which have both UI and talk users."""

    ids: Set[int] = set()
    pattern = re.compile(r"\.descTextId\s*=\s*(0x[0-9A-Fa-f]+|\d+)")
    for relative in ("src/data_characters.c", "src/data_classes.c", "src/data_items.c"):
        source = (root / relative).read_text(encoding="utf-8")
        ids.update(
            int(value, 0)
            for value in pattern.findall(source)
            if int(value, 0) < target_count
        )
    return ids


def _tactician_grid_strings(root: Path, locale: str) -> Tuple[Tuple[str, str], ...]:
    """Static locale input grids are system-font UI, not catalog messages."""

    source = (root / "src/sio_tactician.c").read_text(encoding="utf-8")
    prefix = "sTacticianGridJa" if locale == "ja" else "sTacticianGridZhHans"
    pattern = re.compile(
        rf"static const char ({prefix}[A-Za-z0-9_]+)\[\]\s*=\s*"
        r"((?:\s*\"[^\"]*\")+)\s*;"
    )
    grids = []
    for name, literals in pattern.findall(source):
        grids.append((name, "".join(re.findall(r'"([^"]*)"', literals))))
    if len(grids) != 2 or any(len(text) != 75 for _, text in grids):
        raise CjkFontError(f"{locale}: static tactician grid contract drifted")
    return tuple(grids)


def _direct_talk_target_ids(root: Path, target_count: int) -> Dict[int, str]:
    """Discover non-event talk-font message paths with literal/table IDs."""

    result: Dict[int, str] = {}

    def add(value: int, reason: str) -> None:
        if value < target_count:
            result[value] = reason

    arena = (root / "src/uiarena.c").read_text(encoding="utf-8")
    for value in re.findall(r"\bStartArenaDialogue\s*\(\s*(0x[0-9A-Fa-f]+|\d+)", arena):
        add(int(value, 0), "arena StartArenaDialogue talk-font consumer")

    shop = (root / "src/bmshop.c").read_text(encoding="utf-8")
    offsets = [
        int(value, 0)
        for value in re.findall(
            r"\[SHOP_TYPE_[A-Z_]+\]\s*=\s*(0x[0-9A-Fa-f]+|\d+)",
            shop,
        )
    ]
    if not offsets:
        raise CjkFontError("shop dialogue offset table is unavailable")
    for value in re.findall(r"\bStartShopDialogue\s*\(\s*(0x[0-9A-Fa-f]+|\d+)", shop):
        base = int(value, 0)
        for offset in offsets:
            add(base + offset, "shop dialogue base plus type-offset talk consumer")

    for path in (root / "src").glob("*.c"):
        source = path.read_text(encoding="utf-8")
        for value in re.findall(
            r"\bStartTalkMsg(?:Ext)?\s*\([^;{}]*?,\s*"
            r"(0x[0-9A-Fa-f]+|\d+)\s*(?:,|\))",
            source,
        ):
            add(int(value, 0), f"literal StartTalkMsg consumer in {path.name}")
        for value in re.findall(
            r"\bStartTalkExt\s*\([^;{}]*?GetStringFromIndex\s*\(\s*"
            r"(0x[0-9A-Fa-f]+|\d+)\s*\)",
            source,
        ):
            add(int(value, 0), f"literal StartTalkExt consumer in {path.name}")
    return result


def _runtime_catalog_usage(
    root: Path, locale: str
) -> Tuple[Dict[str, List[str]], List[Dict[str, object]], Dict[str, int]]:
    """Classify every emitted runtime string before deriving a font corpus.

    The game catalog is the authoritative FE8U target resolver.  The width
    registry supplies source-visible dialogue geometry; descriptions are
    explicitly ``both`` because they are consumed by both menu/help UI and
    talk/text presentations.  The remaining target strings have only
    system-font consumers in the supported runtime path.  This is
    intentionally an allow-nothing-by-default contract: each target gets one
    of system, talk, or both and no target is silently omitted.
    """

    from scripts.localization.game_catalog.build import build_game_catalog
    from scripts.localization.game_catalog.control_streams import tokenize_payload

    build = build_game_catalog(enabled_locales=(locale,))
    registry = load_width_registry(root / WIDTH_REGISTRY_PATH)
    classifications, _ = classify_targets(
        root, target_count=build.target_count, registry=registry
    )
    description_ids = _description_target_ids(root, build.target_count)
    direct_talk_ids = _direct_talk_target_ids(root, build.target_count)
    strings = {style: [] for style in STYLES}
    records: List[Dict[str, object]] = []
    counts = {"system": 0, "talk": 0, "both": 0}
    for entry in build.locale_bundle(locale).entries:
        if entry.source_text is None or entry.encoded_bytes is None:
            raise CjkFontError(
                f"{locale} 0x{entry.target_id:04X}: runtime catalog payload is absent"
            )
        context, context_reason = classifications[entry.target_id]
        has_talk_control = any(
            token.kind == "control"
            and token.control is not None
            and 0x08 <= token.control <= 0x11
            for token in tokenize_payload(
                entry.encoded_bytes,
                source_name=f"{locale} 0x{entry.target_id:04X}",
            )
        )
        if entry.target_id in description_ids:
            styles = STYLES
            category = "both"
            reason = "data description: UI/help and talk/text consumers"
        elif (
            context in (registry.talk_context, registry.subtitle_context)
            or has_talk_control
            or entry.target_id in direct_talk_ids
        ):
            styles = ("talk",)
            category = "talk"
            if entry.target_id in direct_talk_ids:
                reason = direct_talk_ids[entry.target_id]
            else:
                reason = context_reason if not has_talk_control else "talk control stream"
        else:
            styles = ("system",)
            category = "system"
            reason = context_reason
        for style in styles:
            strings[style].append(entry.source_text)
        counts[category] += 1
        records.append(
            {
                "id": f"game:0x{entry.target_id:04X}",
                "mapping_source_kind": entry.mapping_source_kind,
                "reason": reason,
                "styles": list(styles),
            }
        )

    expansion, expansion_metadata = _load_expansion_strings(root, locale)
    for key, text in zip(
        (
            row["key"]
            for row in json.loads(
                (root / "texts/expansion/registry.json").read_text(encoding="utf-8")
            )["messages"]
            if row["status"] == "active"
        ),
        expansion,
    ):
        strings["system"].append(text)
        records.append(
            {
                "id": f"expansion:{key}",
                "mapping_source_kind": "expansion",
                "reason": "expansion UI registry system-font consumer",
                "styles": ["system"],
            }
        )
        counts["system"] += 1

    for name, text in _tactician_grid_strings(root, locale):
        strings["system"].append(text)
        records.append(
            {
                "id": f"static:{name}",
                "mapping_source_kind": "static_ui",
                "reason": "tactician name-entry grid system-font consumer",
                "styles": ["system"],
            }
        )
        counts["system"] += 1

    if len(records) != build.target_count + expansion_metadata["active_key_count"] + 2:
        raise CjkFontError(f"{locale}: runtime usage coverage is incomplete")
    return strings, records, counts


def collect_inventory(root: Path) -> Dict[str, object]:
    locale_records: Dict[str, object] = {}
    locale_glyphs: Dict[str, Dict[str, Tuple[int, ...]]] = {}
    runtime_usage: Dict[str, object] = {}
    union_source: Set[int] = set()
    union_glyphs: Set[int] = set()
    union_spacing: Set[int] = set()

    for locale in LOCALES:
        style_strings, usage_records, usage_counts = _runtime_catalog_usage(root, locale)
        _, locale_metadata = _locale_texts(root, locale)
        contribution_sets = {
            style: _scalar_sets(texts, expansion=True)
            for style, texts in style_strings.items()
        }
        all_scalars = set().union(*contribution_sets.values())
        non_ascii = {value for value in all_scalars if value > 0x7F}
        spacing = {value for value in non_ascii if chr(value).isspace()}
        nonrendering = {
            value
            for value in non_ascii
            if unicodedata.category(chr(value)) in {"Cc", "Cf", "Cs"}
        }
        glyphs_by_style = {
            style: tuple(
                sorted(
                    {
                        value
                        for value in contribution_sets[style]
                        if value > 0x7F
                        and value not in spacing
                        and value not in nonrendering
                    }
                )
            )
            for style in STYLES
        }
        glyphs = tuple(sorted(set().union(*glyphs_by_style.values())))
        locale_glyphs[locale] = glyphs_by_style
        union_source.update(non_ascii)
        union_glyphs.update(glyphs)
        union_spacing.update(spacing)
        locale_records[locale] = {
            "source_non_ascii_scalar_count": len(non_ascii),
            "glyph_scalar_count": len(glyphs),
            "spacing_scalars": [scalar_text(value) for value in sorted(spacing)],
            "nonrendering_scalars": [
                scalar_text(value) for value in sorted(nonrendering)
            ],
            "runtime_usage": {
                "record_count": len(usage_records),
                "game_target_count": 3414,
                "expansion_key_count": locale_metadata["expansion"][
                    "active_key_count"
                ],
                "classifications": usage_counts,
                "unclassified_count": 0,
            },
            "contributions": {
                name: {
                    "record_count": len(style_strings[name]),
                    "non_ascii_scalar_count": len(
                        {value for value in values if value > 0x7F}
                    ),
                }
                for name, values in contribution_sets.items()
            },
            **locale_metadata,
            "styles": {
                style: {
                    "glyph_scalar_count": len(glyphs_by_style[style]),
                    "corpus": f"fonts/cjk/corpora/{locale}.{style}.txt",
                    "runtime_contract": (
                        "system/item" if style == "system" else "talk/text"
                    ),
                }
                for style in STYLES
            },
        }
        runtime_usage[locale] = {
            "records": usage_records,
            "summary": locale_records[locale]["runtime_usage"],
        }

    return {
        "locales": locale_records,
        "locale_glyphs": locale_glyphs,
        "union_source": tuple(sorted(union_source)),
        "union_glyphs": tuple(sorted(union_glyphs)),
        "union_spacing": tuple(sorted(union_spacing)),
        "runtime_usage": runtime_usage,
    }


def _corpus_bytes(scalars: Sequence[int]) -> bytes:
    return "".join(chr(value) for value in scalars).encode("utf-8")


def _map_bytes(scalars: Sequence[int]) -> bytes:
    lines = [
        f"{scalar_text(value)}\t{chr(value)}\t{unicodedata.name(chr(value), '<unnamed>')}"
        for value in scalars
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _font_source_document(root: Path) -> Dict[str, object]:
    sources = {}
    for locale, expected in FONT_SOURCES.items():
        path = root / expected["path"]
        data = path.read_bytes()
        if len(data) != expected["byte_length"]:
            raise CjkFontError(f"{expected['path']}: byte length drift")
        if sha256_bytes(data) != expected["sha256"]:
            raise CjkFontError(f"{expected['path']}: SHA-256 drift")
        identity = read_sfnt_identity(data)
        if identity["family"] != expected["family"]:
            raise CjkFontError(f"{expected['path']}: family identity drift")
        if identity["full_name"] != expected["full_name"]:
            raise CjkFontError(f"{expected['path']}: full-name identity drift")
        if identity["version"] != expected["version"]:
            raise CjkFontError(f"{expected['path']}: version identity drift")
        if identity["copyright"] != expected["copyright"]:
            raise CjkFontError(f"{expected['path']}: copyright identity drift")
        if "Open Font License" not in identity["license"]:
            raise CjkFontError(f"{expected['path']}: embedded OFL notice is missing")
        sources[locale] = {
            **expected,
            "source_url": expected["source_url"],
            "license_id": LICENSE_SOURCE["license_id"],
            "embedded_license": identity["license"],
            "embedded_license_url": identity["license_url"],
        }

    license_path = root / LICENSE_SOURCE["path"]
    license_data = license_path.read_bytes()
    if sha256_bytes(license_data) != LICENSE_SOURCE["sha256"]:
        raise CjkFontError(f"{LICENSE_SOURCE['path']}: SHA-256 drift")
    return {
        "schema_version": 1,
        "fonts": sources,
        "license": {
            **LICENSE_SOURCE,
            "byte_count": len(license_data),
        },
        "normal_build_dependency": "vendored files only; no network or system font lookup",
    }


def read_sfnt_identity(data: bytes) -> Dict[str, str]:
    if len(data) < 12 or data[:4] == b"ttcf":
        raise CjkFontError("font must be a single bounded SFNT file")
    table_count = struct.unpack_from(">H", data, 4)[0]
    if table_count < 1 or 12 + table_count * 16 > len(data):
        raise CjkFontError("invalid SFNT table directory")
    tables = {}
    for index in range(table_count):
        tag, _, offset, length = struct.unpack_from(
            ">4sIII", data, 12 + index * 16
        )
        if offset + length > len(data):
            raise CjkFontError("SFNT table exceeds file bounds")
        tables[tag] = (offset, length)
    if b"name" not in tables:
        raise CjkFontError("SFNT name table is missing")
    offset, length = tables[b"name"]
    _, count, string_offset = struct.unpack_from(">HHH", data, offset)
    if 6 + count * 12 > length or string_offset > length:
        raise CjkFontError("invalid SFNT name table")
    names: Dict[int, List[Tuple[int, int, str]]] = {}
    for index in range(count):
        record = offset + 6 + index * 12
        platform, encoding, language, name_id, size, relative = struct.unpack_from(
            ">HHHHHH", data, record
        )
        start = offset + string_offset + relative
        end = start + size
        if end > offset + length:
            raise CjkFontError("SFNT name string exceeds table bounds")
        raw = data[start:end]
        try:
            if platform in (0, 3):
                value = raw.decode("utf-16-be")
            elif platform == 1:
                value = raw.decode("mac_roman")
            else:
                continue
        except UnicodeDecodeError:
            continue
        names.setdefault(name_id, []).append((platform, language, value.rstrip("\0")))

    def pick(name_ids: Sequence[int]) -> str:
        for name_id in name_ids:
            candidates = names.get(name_id, [])
            if candidates:
                candidates.sort(
                    key=lambda row: (
                        0 if row[0] == 3 and row[1] == 0x0409 else 1,
                        0 if row[0] == 0 else 1,
                        row[2],
                    )
                )
                return candidates[0][2]
        return ""

    identity = {
        "family": pick((16, 1)),
        "full_name": pick((4,)),
        "version": pick((5,)),
        "copyright": pick((0,)),
        "license": pick((13,)),
        "license_url": pick((14,)),
    }
    if not identity["family"] or not identity["version"]:
        raise CjkFontError("SFNT family/version identity is unavailable")
    return identity


def _febuilder_job(
    locale: str,
    style: str,
    corpus_path: str,
    corpus_sha256: str,
) -> Dict[str, object]:
    source = FONT_SOURCES[locale]
    febuilder_style = "item" if style == "system" else "text"
    return {
        "id": f"{locale.lower()}-{style}".replace("-hans", "-hans"),
        "locale": locale,
        "format": "main-16x16",
        "styles": [febuilder_style],
        "corpus": {
            "path": corpus_path,
            "sha256": corpus_sha256,
        },
        "scalarRanges": [],
        "font": {
            "path": Path(source["path"]).relative_to("fonts/cjk").as_posix(),
            "sha256": source["sha256"],
            "byteLength": source["byte_length"],
            "family": source["family"],
            "version": source["version"],
            "sourceUrl": source["source_url"],
            "size": 12 if style == "system" else 11,
        },
        "license": {
            "licenseId": LICENSE_SOURCE["license_id"],
            "licenseFile": Path(LICENSE_SOURCE["path"])
            .relative_to("fonts/cjk")
            .as_posix(),
            "sha256": LICENSE_SOURCE["sha256"],
            "sourceUrl": LICENSE_SOURCE_URL,
        },
        "verticalOffset": 0,
        "mapping": {
            "mode": "range",
            "unicodeStart": "U+0080",
            "unicodeEnd": "U+10FFFF",
            "mojiStart": 32768,
            "mojiEnd": 65535,
        },
    }


def build_generated_files(root: Path) -> Dict[str, bytes]:
    inventory = collect_inventory(root)
    locale_glyphs = inventory["locale_glyphs"]
    generated: Dict[str, bytes] = {}

    for locale in LOCALES:
        for style in STYLES:
            generated[f"fonts/cjk/corpora/{locale}.{style}.txt"] = _corpus_bytes(
                locale_glyphs[locale][style]
            )
        generated[f"fonts/cjk/maps/{locale}.txt"] = _map_bytes(
            tuple(
                sorted(
                    set().union(
                        *(locale_glyphs[locale][style] for style in STYLES)
                    )
                )
            )
        )
    generated["fonts/cjk/corpora/union.txt"] = _corpus_bytes(
        inventory["union_glyphs"]
    )
    generated["fonts/cjk/maps/union.txt"] = _map_bytes(
        inventory["union_glyphs"]
    )
    generated[RUNTIME_USAGE_PATH] = json_bytes(
        {
            "kind": "fe8u-cjk-runtime-font-usage",
            "schema_version": 1,
            "locales": inventory["runtime_usage"],
            "policy": {
                "description_strings_are_both": True,
                "every_supported_runtime_string_is_classified": True,
                "unclassified_strings_are_forbidden": True,
            },
        }
    )

    font_sources = _font_source_document(root)
    generated["fonts/cjk/font-sources.json"] = json_bytes(font_sources)

    jobs = []
    for locale in LOCALES:
        for style in STYLES:
            relative = f"corpora/{locale}.{style}.txt"
            corpus = generated[f"fonts/cjk/{relative}"]
            jobs.append(
                _febuilder_job(locale, style, relative, sha256_bytes(corpus))
            )
    manifest = {
        "schemaVersion": 1,
        "jobs": jobs,
    }
    generated["fonts/cjk/febuilder-manifest.json"] = json_bytes(manifest)

    input_paths = [
        root / "texts/locales/ja/indexed.txt",
        root / "texts/locales/ja/raw.json",
        root / "texts/locales/zh-Hans/indexed.txt",
        root / "texts/locales/zh-Hans/raw.json",
        root / "texts/expansion/registry.json",
        *sorted((root / "texts/expansion").glob("catalog.*.json")),
        root / "texts/locales/authored/manifest.json",
        root / "texts/locales/authored/catalog.ja.json",
        root / "texts/locales/authored/catalog.zh-Hans.json",
        root / WIDTH_REGISTRY_PATH,
    ]
    outputs = {
        path: {
            "byte_count": len(data),
            "sha256": sha256_bytes(data),
        }
        for path, data in sorted(generated.items())
    }
    inventory_document = {
        "schema_version": 1,
        "normalization": "NFC",
        "font_asset_scope": "non-ASCII visible Unicode scalars",
        "token_rules": {
            "ignored_control_token": (
                "all bracketed FE control/placeholder tokens, including "
                "[CTRL:HHHH] and named text controls"
            ),
            "ignored_expansion_placeholder": "{N}",
            "ascii": "uses the existing runtime ASCII fonts and is reported but not packed",
            "unicode_spacing": (
                "reported separately; spacing is a renderer advance contract and "
                "is not sent to FEBuilder because schema-v1 rejects whitespace"
            ),
        },
        "inputs": {
            record["path"]: {
                "byte_count": record["byte_count"],
                "sha256": record["sha256"],
            }
            for record in (_input_record(path, root) for path in input_paths)
        },
        "locales": inventory["locales"],
        "union": {
            "source_non_ascii_scalar_count": len(inventory["union_source"]),
            "glyph_scalar_count": len(inventory["union_glyphs"]),
            "spacing_scalars": [
                scalar_text(value) for value in inventory["union_spacing"]
            ],
            "astral_scalar_count": sum(
                value > 0xFFFF for value in inventory["union_glyphs"]
            ),
            "maximum_scalar": scalar_text(max(inventory["union_glyphs"])),
            "corpus": "fonts/cjk/corpora/union.txt",
        },
        "outputs": outputs,
    }
    generated["fonts/cjk/inventory.json"] = json_bytes(inventory_document)
    return generated


def write_generated_files(root: Path) -> Dict[str, bytes]:
    generated = build_generated_files(root)
    for relative_path, data in generated.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return generated


def check_generated_files(root: Path) -> Dict[str, bytes]:
    generated = build_generated_files(root)
    mismatches = []
    for relative_path, expected in generated.items():
        path = root / relative_path
        if not path.is_file() or path.read_bytes() != expected:
            mismatches.append(relative_path)
    if mismatches:
        raise CjkFontError(
            "generated inventory artifacts drifted: " + ", ".join(mismatches)
        )
    return generated
