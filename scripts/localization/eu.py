#!/usr/bin/env python3
"""Extract and validate authorized FE8EU localization resources."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Iterable

from scripts.localization.extract_ui_graphics import (
    decode_tiled_4bpp_png,
    decomp_lz77,
    encode_tiled_4bpp_png,
)


ROOT = Path(__file__).resolve().parents[2]
ROM_BASE = 0x08000000
EU_ROM_SHA256 = "80f94bf10da412e6d8d1ba11c043107f4873bc17fecceb02e6a7da3d1a261d6d"

TEXT_ROOT = ROOT / "texts" / "locales" / "eu"
TEXT_MANIFEST_PATH = TEXT_ROOT / "manifest.json"
FONT_ROOT = ROOT / "graphics" / "fonts" / "eu"
FONT_MANIFEST_PATH = FONT_ROOT / "manifest.json"
UI_ROOT = ROOT / "graphics" / "localized_ui" / "eu"
UI_MANIFEST_PATH = UI_ROOT / "manifest.json"
UI_DATA_PATH = ROOT / "src" / "data" / "localized_eu_ui_graphics.c"

HUFFMAN_TABLE_OFFSET = 0x00356004
HUFFMAN_ROOT_POINTER_OFFSET = 0x0035B8A8
MESSAGE_POINTER_TABLE_OFFSET = 0x0035B8AC
MESSAGE_BANK_STRIDE = 0x0D35
MESSAGE_MAX_ID = 0x0D35
MESSAGE_COUNT = MESSAGE_MAX_ID + 1
POINTER_TABLE_COUNT = 1 + 5 * MESSAGE_BANK_STRIDE

EU_BANK_ORDER = ("en", "de", "fr", "es", "it")
PRODUCTION_EU_LOCALES = ("fr", "de", "es", "it")
EU_BANK_INDEX = {locale: index for index, locale in enumerate(EU_BANK_ORDER)}

FONT_TABLES = {
    "system": 0x08798340,
    "talk": 0x0879B248,
}
GLYPH_STRUCT_SIZE = 72
GLYPH_BITMAP_OFFSET = 8
GLYPH_BITMAP_SIZE = 64

CHAPTER_TABLE_POINTERS = 0x08A90AD0
CHAPTER_TITLE_COUNT = 88
SUBTITLE_TABLE = 0x084129B8
SUBTITLE_COUNT = 7
SUBTITLE_ENTRY_SIZE = 12
SUBTITLE_LOCALE_STRIDE = SUBTITLE_COUNT * SUBTITLE_ENTRY_SIZE

# Each table is indexed by the native FE8EU order en,de,fr,es,it. The
# English entry is bound to the current FE8U source symbol by independently
# verified decompressed bytes; the four other entries are extracted here.
COMPRESSED_RESOURCE_TABLES = (
    (0x087AB704, "gGfx_MiscUiGraphics"),
    (0x087AC260, "Img_PhaseChangePlayer"),
    (0x087AC274, "Img_PhaseChangeEnemy"),
    (0x087AC288, "Img_PhaseChangeOther"),
    (0x087AC674, "Img_GameOverText"),
    (0x087AEEE4, "Img_PrepHelpButtonSprites"),
    (0x087AFB04, "gTsa_CombatRecordTitle"),
    (0x087BF708, "Img_TacticianSelObj"),
    (0x087BF71C, "Img_LinkArenaMenuBanner"),
    (0x087BF9A0, "Img_LinkArenaPlayerBanners"),
    (0x087BF9B4, "Img_LinkArenaPlacementRanks"),
    (0x087C0108, "Img_LinkArenaMenu"),
    (0x087C0340, "Img_SioPointsBox"),
    (0x087C0C78, "Img_LinkArenaMenuTitle"),
    (0x087C0E70, "Img_LinkArenaPhaseIntroBg"),
    (0x087DBC58, "Img_EfxSideHitDmgCrit"),
    (0x08999D44, "Img_EfxLvupOBJ2"),
    (0x08A46628, "Img_MapAnimMISS"),
    (0x08A46650, "Img_MapAnimNODAMAGE"),
    (0x08A46CC8, "Img_ManimLevelUpText"),
    (0x08A8EDC8, "Img_StatscreenObjs"),
    (0x08A8EDDC, "Img_StatscreenEquipmentText"),
    (0x08A90EF8, "gGfx_PlayerInterfaceFontTiles"),
    (0x08A910A0, "Img_PlayStatusSprites"),
    (0x08A91204, "Img_StatusScreenLabelSprites"),
    (0x08ADC4C4, "Img_UnitListBanner_Animation"),
    (0x08ADCC44, "gImg_PrepBannerText"),
    (0x08ADCC58, "gImg_PrepAtMenuTitleText"),
    (0x08ADCC6C, "gImg_PrepMenuStartButtonSprites"),
    (0x08ADD328, "Img_PrepFunds"),
    (0x08ADE154, "gGfx_SupportScreenBanner"),
    (0x08ADE28C, "gGfx_SupportMenu"),
    (0x08AEC838, "Img_SaveScreenSprits"),
    (0x08AEC84C, "Img_GameMainMenuObjs"),
    (0x08AED208, "Img_DifficultyMenuObjs"),
    (0x08AEDB44, "Img_SoundRoomUiElements"),
    (0x08B0BAFC, "Img_ConfigUiSprites"),
    (0x08B17E64, "Img_OpAnimEphEirikaName"),
    (0x08B8BE68, "Img_GuideScreenBg"),
    (0x08B8BE7C, "Img_GuideScreenPanels"),
    (0x090A8AC4, "Img_Congratulations"),
    (0x090A8AD8, "Img_MapClear"),
    (0x090BE5BC, "Img_FinScreen"),
    (0x090BE5D0, "Tsa_FinScreen"),
    (0x090BF844, "gImg_WorldmapSkirmish"),
    (0x090BF9FC, "gWorldmapMinimap_0"),
)

RAW_RESOURCE_TABLES = (
    (0x087DBC14, "Img_EkrExpBar", 0x380, "4bpp"),
)

AP_RESOURCE_TABLES = (
    (0x08A4663C, "Obj_MapAnimMISS"),
    (0x08A46664, "obj_MapAnimNODAMAGE"),
    (0x08A46CDC, "gMapanimTorchfx_0"),
)

EXTRA_BASE_DECLARATIONS = (
    "Img_PhaseChangePlayer",
    "Img_PhaseChangeEnemy",
    "Img_PhaseChangeOther",
    "Img_DifficultyMenuObjs",
    "Img_SoundRoomUiElements",
    "Img_GuideScreenBg",
    "Img_GuideScreenPanels",
)

_INCBIN_RE_TEMPLATE = (
    r"\b{symbol}\s*\[\s*\][^;\n]*"
    r"INCBIN_U(?:8|16|32)\(\s*\"([^\"]+)\""
)


class EuLocalizationError(ValueError):
    """The FE8EU source or a committed extracted artifact is invalid."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def write_if_changed(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_bytes() != content:
        path.write_bytes(content)


def _offset(address: int) -> int:
    if not ROM_BASE <= address < ROM_BASE + 0x02000000:
        raise EuLocalizationError(f"address 0x{address:08X} is outside the EU ROM")
    return address - ROM_BASE


def _read_u32(source: bytes, address: int) -> int:
    return struct.unpack_from("<I", source, _offset(address))[0]


def _read_pointer_table(source: bytes, address: int, count: int) -> tuple[int, ...]:
    return struct.unpack_from(f"<{count}I", source, _offset(address))


class EuTextDecoder:
    def __init__(self, source: bytes):
        root_address = struct.unpack_from(
            "<I", source, HUFFMAN_ROOT_POINTER_OFFSET
        )[0]
        root_offset = root_address - ROM_BASE
        if root_offset < HUFFMAN_TABLE_OFFSET or (root_offset - HUFFMAN_TABLE_OFFSET) % 4:
            raise EuLocalizationError("EU Huffman root pointer is invalid")

        self.source = source
        self.root_index = (root_offset - HUFFMAN_TABLE_OFFSET) // 4
        self.nodes = struct.unpack_from(
            f"<{self.root_index + 1}I", source, HUFFMAN_TABLE_OFFSET
        )
        self.pointers = struct.unpack_from(
            f"<{POINTER_TABLE_COUNT}I", source, MESSAGE_POINTER_TABLE_OFFSET
        )

    def _pointer_index(self, locale: str, message_id: int) -> int:
        if locale not in EU_BANK_INDEX:
            raise EuLocalizationError(f"unknown EU source locale {locale!r}")
        if not 0 <= message_id <= MESSAGE_MAX_ID:
            raise EuLocalizationError(
                f"EU message id 0x{message_id:04X} is outside 0x0000..0x{MESSAGE_MAX_ID:04X}"
            )
        if message_id == 0:
            return 0
        return message_id + EU_BANK_INDEX[locale] * MESSAGE_BANK_STRIDE

    def decode(self, locale: str, message_id: int) -> bytes:
        pointer_index = self._pointer_index(locale, message_id)
        pointer = self.pointers[pointer_index]
        position = _offset(pointer)
        bit_position = 0
        node_index = self.root_index
        output = bytearray()

        for _ in range(0x20000):
            byte = self.source[position + bit_position // 8]
            bit = (byte >> (bit_position & 7)) & 1
            bit_position += 1

            node = self.nodes[node_index]
            node_index = (node >> 16) & 0xFFFF if bit else node & 0xFFFF
            if node_index >= len(self.nodes):
                raise EuLocalizationError(
                    f"{locale} 0x{message_id:04X}: invalid Huffman child index"
                )

            node = self.nodes[node_index]
            if node >> 16 != 0xFFFF:
                continue

            symbol = node & 0xFFFF
            output.append(symbol & 0xFF)
            if symbol >> 8:
                output.append(symbol >> 8)
            if symbol == 0:
                return bytes(output)
            node_index = self.root_index

        raise EuLocalizationError(
            f"{locale} 0x{message_id:04X}: Huffman stream has no terminator"
        )


def _control(value: int) -> str:
    return f"[CTRL:{value:04X}]"


def normalize_eu_payload(payload: bytes, *, locale: str, message_id: int) -> str:
    if not payload or payload[-1] != 0 or 0 in payload[:-1]:
        raise EuLocalizationError(
            f"{locale} 0x{message_id:04X}: decoded payload has an invalid NUL layout"
        )

    output: list[str] = []
    index = 0
    end = len(payload) - 1
    while index < end:
        value = payload[index]

        if value == 0x10:
            if index + 2 >= end:
                raise EuLocalizationError(
                    f"{locale} 0x{message_id:04X}: truncated LoadFace operand"
                )
            operand = payload[index + 1] | (payload[index + 2] << 8)
            output.append(_control(value))
            output.append(_control(operand))
            index += 3
            continue

        if value == 0x80:
            if index + 1 >= end:
                raise EuLocalizationError(
                    f"{locale} 0x{message_id:04X}: truncated extended control"
                )
            output.append(_control(value))
            output.append(_control(payload[index + 1]))
            index += 2
            continue

        if value < 0x20:
            output.append(_control(value))
            index += 1
            continue

        if value == 0x7F:
            output.append("-")
            index += 1
            continue

        if value == 0x81 and index + 1 < end and payload[index + 1] == 0x40:
            output.append("\u3000")
            index += 2
            continue

        try:
            character = bytes((value,)).decode("cp1252")
        except UnicodeDecodeError as error:
            raise EuLocalizationError(
                f"{locale} 0x{message_id:04X}: undefined CP1252 byte 0x{value:02X}"
            ) from error

        if character in "[]":
            raise EuLocalizationError(
                f"{locale} 0x{message_id:04X}: literal brackets are unsupported"
            )
        output.append(character)
        index += 1

    return "".join(output)


def render_indexed_locale(
    locale: str,
    normalized: Iterable[str],
    *,
    source_sha256: str,
) -> bytes:
    lines = [
        "# Normalized UTF-8 indexed locale source.",
        f"# Locale ID: {locale}",
        "# Source layout: FE8EU message IDs; these identifiers are not FE8U target identifiers.",
        f"# Authorized ROM SHA-256: {source_sha256}",
        f"# Message range: 0x0000..0x{MESSAGE_MAX_ID:04X}",
        "",
    ]
    for message_id, text in enumerate(normalized):
        lines.append(f"#0x{message_id:04X}")
        lines.append(text)
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def extract_texts(source: bytes) -> dict[str, object]:
    decoder = EuTextDecoder(source)
    locale_reports: dict[str, object] = {}

    for locale in EU_BANK_ORDER:
        raw_messages = [
            decoder.decode(locale, message_id) for message_id in range(MESSAGE_COUNT)
        ]
        normalized = [
            normalize_eu_payload(payload, locale=locale, message_id=message_id)
            for message_id, payload in enumerate(raw_messages)
        ]
        content = render_indexed_locale(
            locale,
            normalized,
            source_sha256=EU_ROM_SHA256,
        )
        relative_path = f"indexed.{locale}.txt"
        write_if_changed(TEXT_ROOT / relative_path, content)
        locale_reports[locale] = {
            "bank_index": EU_BANK_INDEX[locale],
            "message_count": MESSAGE_COUNT,
            "max_message_id": f"0x{MESSAGE_MAX_ID:04X}",
            "raw_payload_sha256": sha256(b"".join(raw_messages)),
            "normalized_path": f"texts/locales/eu/{relative_path}",
            "normalized_byte_count": len(content),
            "normalized_sha256": sha256(content),
            "max_normalized_utf8_bytes": max(
                len(text.encode("utf-8")) for text in normalized
            ),
        }

    manifest = {
        "format": 1,
        "purpose": "FE8EU full-game localization source",
        "source": {
            "sha256": EU_ROM_SHA256,
            "game_code": "BE8P",
            "size": len(source),
        },
        "huffman": {
            "table_address": f"0x{ROM_BASE + HUFFMAN_TABLE_OFFSET:08X}",
            "root_pointer_address": f"0x{ROM_BASE + HUFFMAN_ROOT_POINTER_OFFSET:08X}",
            "root_index": decoder.root_index,
            "node_count": len(decoder.nodes),
            "message_pointer_table_address": (
                f"0x{ROM_BASE + MESSAGE_POINTER_TABLE_OFFSET:08X}"
            ),
            "message_bank_stride": MESSAGE_BANK_STRIDE,
            "pointer_count": len(decoder.pointers),
        },
        "bank_order": list(EU_BANK_ORDER),
        "locales": locale_reports,
    }
    write_if_changed(TEXT_MANIFEST_PATH, canonical_json_bytes(manifest))
    return manifest


def _cp1252_codepoint(value: int) -> int:
    try:
        character = bytes((value,)).decode("cp1252")
    except UnicodeDecodeError as error:
        raise EuLocalizationError(
            f"font table contains undefined CP1252 byte 0x{value:02X}"
        ) from error
    return ord(character)


def extract_fonts(source: bytes, text_manifest: dict[str, object]) -> dict[str, object]:
    styles: dict[str, object] = {}
    available_codepoints: set[int] = set()

    for style, table_address in FONT_TABLES.items():
        pointers = _read_pointer_table(source, table_address, 256)
        glyphs: list[tuple[int, int, int, bytes]] = []
        for source_byte in range(0x80, 0x100):
            pointer = pointers[source_byte]
            if pointer == 0:
                continue
            codepoint = _cp1252_codepoint(source_byte)
            glyph_offset = _offset(pointer)
            glyph = source[glyph_offset : glyph_offset + GLYPH_STRUCT_SIZE]
            if len(glyph) != GLYPH_STRUCT_SIZE:
                raise EuLocalizationError(
                    f"{style} glyph 0x{source_byte:02X}: truncated glyph structure"
                )
            width = glyph[5]
            bitmap = glyph[
                GLYPH_BITMAP_OFFSET : GLYPH_BITMAP_OFFSET + GLYPH_BITMAP_SIZE
            ]
            if not 0 < width <= 16 or len(bitmap) != GLYPH_BITMAP_SIZE:
                raise EuLocalizationError(
                    f"{style} glyph 0x{source_byte:02X}: invalid width or bitmap"
                )
            glyphs.append((codepoint, source_byte, width, bitmap))

        glyphs.sort()
        if len({codepoint for codepoint, _, _, _ in glyphs}) != len(glyphs):
            raise EuLocalizationError(f"{style}: duplicate Unicode codepoint")

        codepoint_bytes = b"".join(
            struct.pack("<I", codepoint) for codepoint, _, _, _ in glyphs
        )
        width_bytes = bytes(width for _, _, width, _ in glyphs)
        bitmap_bytes = b"".join(bitmap for _, _, _, bitmap in glyphs)
        source_byte_bytes = bytes(source_byte for _, source_byte, _, _ in glyphs)

        files = {
            "codepoints": (
                FONT_ROOT / f"eu.{style}.codepoints.u32le",
                codepoint_bytes,
            ),
            "source_bytes": (
                FONT_ROOT / f"eu.{style}.source-bytes.u8",
                source_byte_bytes,
            ),
            "widths": (FONT_ROOT / f"eu.{style}.widths.u8", width_bytes),
            "bitmaps": (FONT_ROOT / f"eu.{style}.glyphs.2bpp", bitmap_bytes),
        }
        for path, content in files.values():
            write_if_changed(path, content)

        available_codepoints.update(codepoint for codepoint, _, _, _ in glyphs)
        styles[style] = {
            "source_table_address": f"0x{table_address:08X}",
            "glyph_count": len(glyphs),
            "glyphs": [
                {
                    "codepoint": f"U+{codepoint:04X}",
                    "source_byte": f"0x{source_byte:02X}",
                    "width": width,
                    "bitmap_sha256": sha256(bitmap),
                }
                for codepoint, source_byte, width, bitmap in glyphs
            ],
            "files": {
                kind: {
                    "path": str(path.relative_to(ROOT)),
                    "byte_count": len(content),
                    "sha256": sha256(content),
                }
                for kind, (path, content) in files.items()
            },
        }

    used_non_ascii: set[int] = set()
    for locale in EU_BANK_ORDER:
        path = TEXT_ROOT / f"indexed.{locale}.txt"
        for character in path.read_text(encoding="utf-8"):
            if ord(character) >= 0x80 and character not in ("\u3000",):
                used_non_ascii.add(ord(character))

    missing = sorted(used_non_ascii - available_codepoints)
    if missing:
        raise EuLocalizationError(
            "EU text uses codepoints absent from the extracted font: "
            + ", ".join(f"U+{value:04X}" for value in missing)
        )

    manifest = {
        "format": 1,
        "purpose": "FE8EU Latin glyph source",
        "source": {
            "sha256": EU_ROM_SHA256,
            "text_manifest_sha256": sha256(canonical_json_bytes(text_manifest)),
        },
        "encoding": "Windows-1252",
        "bitmap_format": "16x16 2bpp, 64 bytes per glyph",
        "styles": styles,
        "used_non_ascii_codepoints": [
            f"U+{value:04X}" for value in sorted(used_non_ascii)
        ],
    }
    write_if_changed(FONT_MANIFEST_PATH, canonical_json_bytes(manifest))
    return manifest


def _find_base_asset(symbol: str) -> str:
    pattern = re.compile(_INCBIN_RE_TEMPLATE.format(symbol=re.escape(symbol)))
    matches: list[str] = []
    for suffix in ("*.c", "*.s"):
        for path in (ROOT / "src").rglob(suffix):
            text = path.read_text(encoding="utf-8")
            match = pattern.search(text)
            if match:
                matches.append(match.group(1))
                continue

            label = re.search(
                rf"(?m)^\s*{re.escape(symbol)}\s*:\s*(?:@[^\n]*)?\n",
                text,
            )
            if label:
                incbin = re.search(
                    r'\.incbin\s+"([^"]+)"',
                    text[label.end() : label.end() + 400],
                )
                if incbin:
                    matches.append(incbin.group(1))
    if not matches:
        raise EuLocalizationError(f"cannot locate INCBIN source for {symbol}")
    unique = sorted(set(matches))
    if len(unique) != 1:
        raise EuLocalizationError(
            f"{symbol}: ambiguous INCBIN sources {unique!r}"
        )
    return unique[0].removeprefix("./")


def _asset_extension(base_path: str) -> tuple[str, str]:
    if base_path.endswith(".4bpp.lz"):
        return "tiled_4bpp_png", ".png"
    for suffix in (".tsa.bin.lz", ".map.bin.lz", ".bin.lz"):
        if base_path.endswith(suffix):
            return "binary", suffix[:-3]
    raise EuLocalizationError(
        f"localized compressed asset has unsupported source type: {base_path}"
    )


def _asset_name(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", symbol)


def _write_decompressed_asset(
    *,
    raw: bytes,
    locale: str,
    category: str,
    name: str,
    base_path: str,
) -> dict[str, object]:
    kind, extension = _asset_extension(base_path)
    relative = Path(locale) / category / f"{_asset_name(name)}{extension}"
    path = UI_ROOT / relative

    if kind == "tiled_4bpp_png":
        png, dimensions = encode_tiled_4bpp_png(raw)
        if decode_tiled_4bpp_png(png)[0] != raw:
            raise EuLocalizationError(f"{relative}: PNG round trip changed bytes")
        write_if_changed(path, png)
        return {
            "kind": kind,
            "path": str(path.relative_to(ROOT)),
            "build_path": str(path.with_suffix(".4bpp").relative_to(ROOT)),
            "raw_size": len(raw),
            "raw_sha256": sha256(raw),
            "png_size": len(png),
            "png_sha256": sha256(png),
            "dimensions": dimensions,
        }

    write_if_changed(path, raw)
    return {
        "kind": kind,
        "path": str(path.relative_to(ROOT)),
        "build_path": str(path.relative_to(ROOT)),
        "size": len(raw),
        "sha256": sha256(raw),
    }


def _extract_compressed_resources(source: bytes) -> list[dict[str, object]]:
    resources: list[dict[str, object]] = []
    for table_address, base_symbol in COMPRESSED_RESOURCE_TABLES:
        pointers = _read_pointer_table(source, table_address, len(EU_BANK_ORDER))
        raw_by_locale = {
            locale: decomp_lz77(source, _offset(pointers[EU_BANK_INDEX[locale]]))
            for locale in EU_BANK_ORDER
        }
        base_path = _find_base_asset(base_symbol)
        variants = {}
        for locale in PRODUCTION_EU_LOCALES:
            variants[locale] = {
                "source_address": f"0x{pointers[EU_BANK_INDEX[locale]]:08X}",
                **_write_decompressed_asset(
                    raw=raw_by_locale[locale],
                    locale=locale,
                    category="resources",
                    name=base_symbol,
                    base_path=base_path,
                ),
            }
        resources.append(
            {
                "table_address": f"0x{table_address:08X}",
                "base_symbol": base_symbol,
                "base_path": base_path,
                "english_source_address": f"0x{pointers[0]:08X}",
                "english_raw_size": len(raw_by_locale["en"]),
                "english_raw_sha256": sha256(raw_by_locale["en"]),
                "variants": variants,
            }
        )
    return resources


def _extract_raw_resources(source: bytes) -> list[dict[str, object]]:
    resources = []
    for table_address, base_symbol, size, kind in RAW_RESOURCE_TABLES:
        pointers = _read_pointer_table(source, table_address, len(EU_BANK_ORDER))
        base_path = _find_base_asset(base_symbol)
        variants = {}
        for locale in PRODUCTION_EU_LOCALES:
            address = pointers[EU_BANK_INDEX[locale]]
            raw = source[_offset(address) : _offset(address) + size]
            if len(raw) != size:
                raise EuLocalizationError(f"{base_symbol} {locale}: truncated raw asset")
            variants[locale] = {
                "source_address": f"0x{address:08X}",
                **_write_decompressed_asset(
                    raw=raw,
                    locale=locale,
                    category="raw",
                    name=base_symbol,
                    base_path=base_path + ("" if base_path.endswith(".lz") else ".lz"),
                ),
            }
        english = source[_offset(pointers[0]) : _offset(pointers[0]) + size]
        resources.append(
            {
                "table_address": f"0x{table_address:08X}",
                "base_symbol": base_symbol,
                "base_path": base_path,
                "kind": kind,
                "size": size,
                "english_source_address": f"0x{pointers[0]:08X}",
                "english_sha256": sha256(english),
                "variants": variants,
            }
        )
    return resources


def _ap_definition_size(source: bytes, address: int) -> int:
    start = _offset(address)
    if start + 4 > len(source):
        raise EuLocalizationError(f"AP definition 0x{address:08X} is truncated")
    frame_table_offset, anim_table_offset = struct.unpack_from("<HH", source, start)
    if frame_table_offset < 4 or anim_table_offset < 4:
        raise EuLocalizationError(f"AP definition 0x{address:08X} has invalid offsets")

    anim_table = start + anim_table_offset
    first_anim_offset = struct.unpack_from("<H", source, anim_table)[0]
    anim_position = anim_table + first_anim_offset
    max_frame = 0
    while True:
        duration, frame = struct.unpack_from("<HH", source, anim_position)
        anim_position += 4
        if duration == 0:
            break
        max_frame = max(max_frame, frame)

    frame_table = start + frame_table_offset
    max_end = anim_position
    for frame_index in range(max_frame + 1):
        frame_offset = struct.unpack_from(
            "<H", source, frame_table + frame_index * 2
        )[0]
        position = frame_table + frame_offset
        count = struct.unpack_from("<H", source, position)[0]
        if count & 0x8000:
            rotation_count = count & 0x7FFF
            position += 2 + rotation_count * 6
            count = struct.unpack_from("<H", source, position)[0]
        position += 2 + count * 8
        max_end = max(max_end, position)

    return (max_end - start + 3) & ~3


def _extract_ap_resources(source: bytes) -> list[dict[str, object]]:
    resources = []
    for table_address, base_symbol in AP_RESOURCE_TABLES:
        pointers = _read_pointer_table(source, table_address, len(EU_BANK_ORDER))
        variants = {}
        for locale in PRODUCTION_EU_LOCALES:
            address = pointers[EU_BANK_INDEX[locale]]
            size = _ap_definition_size(source, address)
            raw = source[_offset(address) : _offset(address) + size]
            relative = (
                Path(locale) / "ap" / f"{_asset_name(base_symbol)}.ap"
            )
            path = UI_ROOT / relative
            write_if_changed(path, raw)
            variants[locale] = {
                "source_address": f"0x{address:08X}",
                "path": str(path.relative_to(ROOT)),
                "size": size,
                "sha256": sha256(raw),
            }
        english_size = _ap_definition_size(source, pointers[0])
        english = source[
            _offset(pointers[0]) : _offset(pointers[0]) + english_size
        ]
        resources.append(
            {
                "table_address": f"0x{table_address:08X}",
                "base_symbol": base_symbol,
                "english_source_address": f"0x{pointers[0]:08X}",
                "english_size": english_size,
                "english_sha256": sha256(english),
                "variants": variants,
            }
        )
    return resources


def _extract_subtitles(source: bytes) -> dict[str, object]:
    variants = {}
    for locale in PRODUCTION_EU_LOCALES:
        table = SUBTITLE_TABLE + EU_BANK_INDEX[locale] * SUBTITLE_LOCALE_STRIDE
        slides = []
        for index in range(SUBTITLE_COUNT):
            gfx, tsa, timer = struct.unpack_from(
                "<III", source, _offset(table) + index * SUBTITLE_ENTRY_SIZE
            )
            gfx_raw = decomp_lz77(source, _offset(gfx))
            tsa_raw = decomp_lz77(source, _offset(tsa))
            gfx_asset = _write_decompressed_asset(
                raw=gfx_raw,
                locale=locale,
                category="subtitle",
                name=f"slide_{index:02d}",
                base_path="subtitle.4bpp.lz",
            )
            tsa_asset = _write_decompressed_asset(
                raw=tsa_raw,
                locale=locale,
                category="subtitle",
                name=f"slide_{index:02d}",
                base_path="subtitle.tsa.bin.lz",
            )
            slides.append(
                {
                    "gfx_source_address": f"0x{gfx:08X}",
                    "tsa_source_address": f"0x{tsa:08X}",
                    "timer": timer,
                    "gfx": gfx_asset,
                    "tsa": tsa_asset,
                }
            )
        variants[locale] = {
            "table_address": f"0x{table:08X}",
            "slides": slides,
        }
    return variants


def _extract_chapter_titles(source: bytes) -> dict[str, object]:
    tables = _read_pointer_table(source, CHAPTER_TABLE_POINTERS, len(EU_BANK_ORDER))
    variants = {}
    for locale in PRODUCTION_EU_LOCALES:
        table = tables[EU_BANK_INDEX[locale]]
        by_address: dict[int, dict[str, object]] = {}
        entries = []
        for title_id in range(CHAPTER_TITLE_COUNT):
            save, left, right = struct.unpack_from(
                "<III", source, _offset(table) + title_id * 12
            )
            if left or right:
                raise EuLocalizationError(
                    f"{locale} chapter title {title_id}: unexpected split graphics"
                )
            if save not in by_address:
                raw = decomp_lz77(source, _offset(save))
                by_address[save] = {
                    "source_address": f"0x{save:08X}",
                    **_write_decompressed_asset(
                        raw=raw,
                        locale=locale,
                        category="chapter",
                        name=f"asset_{save:08X}",
                        base_path="chapter.4bpp.lz",
                    ),
                }
            entries.append(f"0x{save:08X}")
        variants[locale] = {
            "table_address": f"0x{table:08X}",
            "entries": entries,
            "assets": list(by_address.values()),
        }
    return {
        "table_pointer_address": f"0x{CHAPTER_TABLE_POINTERS:08X}",
        "count": CHAPTER_TITLE_COUNT,
        "variants": variants,
    }


def extract_ui(source: bytes) -> dict[str, object]:
    manifest = {
        "format": 1,
        "purpose": "FE8EU localized static and sprite UI resources",
        "source": {
            "sha256": EU_ROM_SHA256,
            "bank_order": list(EU_BANK_ORDER),
        },
        "compressed_resources": _extract_compressed_resources(source),
        "raw_resources": _extract_raw_resources(source),
        "ap_resources": _extract_ap_resources(source),
        "subtitles": _extract_subtitles(source),
        "chapter_titles": _extract_chapter_titles(source),
    }
    write_if_changed(UI_MANIFEST_PATH, canonical_json_bytes(manifest))
    write_if_changed(UI_DATA_PATH, generate_ui_source(manifest))
    return manifest


def _c_ident(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")


def generate_ui_source(manifest: dict[str, object]) -> bytes:
    declarations: dict[str, tuple[str, bool]] = {}

    def register_asset(
        asset: dict[str, object],
        *,
        compressed: bool,
    ) -> str:
        path = str(asset.get("build_path", asset["path"]))
        input_path = path + ".lz" if compressed else path
        name = "sLocalizedEu_" + _c_ident(path)
        declarations[name] = (input_path, compressed)
        return name

    compressed_rows = []
    for resource in manifest["compressed_resources"]:
        variants = {
            locale: register_asset(asset, compressed=True)
            for locale, asset in resource["variants"].items()
        }
        compressed_rows.append((resource["base_symbol"], variants))

    raw_rows = []
    for resource in manifest["raw_resources"]:
        variants = {
            locale: register_asset(asset, compressed=False)
            for locale, asset in resource["variants"].items()
        }
        raw_rows.append((resource["base_symbol"], variants))

    ap_rows = []
    for resource in manifest["ap_resources"]:
        variants = {
            locale: register_asset(asset, compressed=False)
            for locale, asset in resource["variants"].items()
        }
        ap_rows.append((resource["base_symbol"], variants))

    subtitle_rows = {}
    for locale, variant in manifest["subtitles"].items():
        slides = []
        for slide in variant["slides"]:
            slides.append(
                (
                    register_asset(slide["gfx"], compressed=True),
                    register_asset(slide["tsa"], compressed=True),
                    slide["timer"],
                )
            )
        subtitle_rows[locale] = slides

    chapter_rows = {}
    for locale, variant in manifest["chapter_titles"]["variants"].items():
        by_address = {
            asset["source_address"]: register_asset(asset, compressed=True)
            for asset in variant["assets"]
        }
        chapter_rows[locale] = [
            by_address[address] for address in variant["entries"]
        ]

    lines = [
        '#include "global.h"',
        "",
        '#include "efxbattle.h"',
        '#include "mapanim.h"',
        '#include "opanim.h"',
        '#include "prepscreen.h"',
        '#include "sio.h"',
        '#include "uiconfig.h"',
        '#include "worldmap.h"',
        '#include "localized_ui_graphics.h"',
        "",
        "#if LOCALIZED_UI_GRAPHICS_EU_ENABLED",
        "",
        "/* Generated by scripts/localization/eu.py. */",
    ]
    for symbol in EXTRA_BASE_DECLARATIONS:
        lines.append(f"extern u8 {symbol}[];")
    lines.append("")
    for name, (path, _) in sorted(declarations.items()):
        lines.append(
            f'static const u8 {name}[] __attribute__((aligned(4))) = '
            f'INCBIN_U8("{path}");'
        )
    lines.append("")
    lines.extend(
        [
            "struct LocalizedEuResource",
            "{",
            "    const void *base;",
            "    const void *fr;",
            "    const void *de;",
            "    const void *es;",
            "    const void *it;",
            "};",
            "",
        ]
    )

    def render_registry(name: str, rows: list[tuple[str, dict[str, str]]]) -> None:
        lines.extend(
            [
                f"static const struct LocalizedEuResource {name}[] =",
                "{",
            ]
        )
        for base_symbol, variants in rows:
            lines.append(
                "    { (const void *)%s, %s, %s, %s, %s },"
                % (
                    base_symbol,
                    variants["fr"],
                    variants["de"],
                    variants["es"],
                    variants["it"],
                )
            )
        lines.extend(["};", ""])

    render_registry("sLocalizedEuCompressedResources", compressed_rows)
    render_registry("sLocalizedEuRawResources", raw_rows)
    render_registry("sLocalizedEuApResources", ap_rows)

    for locale in PRODUCTION_EU_LOCALES:
        locale_c = locale.title()
        lines.append(
            "static const struct LocalizedUiGraphicsSubtitleSlide "
            f"sLocalizedEu{locale_c}Subtitles[] ="
        )
        lines.append("{")
        for gfx, tsa, timer in subtitle_rows[locale]:
            lines.append(f"    {{ {gfx}, {tsa}, {timer} }},")
        lines.extend(["};", ""])

        lines.append(
            "static const struct LocalizedUiGraphicsChapterTitle "
            f"sLocalizedEu{locale_c}ChapterTitles[] ="
        )
        lines.append("{")
        for asset in chapter_rows[locale]:
            lines.append(f"    {{ {asset}, 0, 0 }},")
        lines.extend(["};", ""])

    lines.extend(
        [
            "static const void *LocalizedEuUiGraphics_Select(",
            "    const struct LocalizedEuResource *entry)",
            "{",
            "    switch (ExpansionLocale_GetCurrent())",
            "    {",
            "    case EXPANSION_LOCALE_FR:",
            "        return entry->fr;",
            "    case EXPANSION_LOCALE_DE:",
            "        return entry->de;",
            "    case EXPANSION_LOCALE_ES:",
            "        return entry->es;",
            "    case EXPANSION_LOCALE_IT:",
            "        return entry->it;",
            "    default:",
            "        return entry->base;",
            "    }",
            "}",
            "",
            "static const void *LocalizedEuUiGraphics_Remap(",
            "    const void *source,",
            "    const struct LocalizedEuResource *resources,",
            "    u32 count)",
            "{",
            "    u32 i;",
            "",
            "    for (i = 0; i < count; i++)",
            "    {",
            "        if (source == resources[i].base)",
            "            return LocalizedEuUiGraphics_Select(&resources[i]);",
            "    }",
            "    return source;",
            "}",
            "",
            "const void *LocalizedEuUiGraphics_RemapCompressed(const void *source)",
            "{",
            "    return LocalizedEuUiGraphics_Remap(",
            "        source,",
            "        sLocalizedEuCompressedResources,",
            "        ARRAY_COUNT(sLocalizedEuCompressedResources));",
            "}",
            "",
            "const void *LocalizedEuUiGraphics_RemapRaw(const void *source)",
            "{",
            "    return LocalizedEuUiGraphics_Remap(",
            "        source,",
            "        sLocalizedEuRawResources,",
            "        ARRAY_COUNT(sLocalizedEuRawResources));",
            "}",
            "",
            "const void *LocalizedEuUiGraphics_RemapAp(const void *source)",
            "{",
            "    return LocalizedEuUiGraphics_Remap(",
            "        source,",
            "        sLocalizedEuApResources,",
            "        ARRAY_COUNT(sLocalizedEuApResources));",
            "}",
            "",
            "const struct LocalizedUiGraphicsSubtitleSlide *",
            "LocalizedEuUiGraphics_GetSubtitleSlides(void)",
            "{",
            "    switch (ExpansionLocale_GetCurrent())",
            "    {",
        ]
    )
    for locale in PRODUCTION_EU_LOCALES:
        lines.extend(
            [
                f"    case EXPANSION_LOCALE_{locale.upper()}:",
                f"        return sLocalizedEu{locale.title()}Subtitles;",
            ]
        )
    lines.extend(
        [
            "    default:",
            "        return 0;",
            "    }",
            "}",
            "",
            "const struct LocalizedUiGraphicsChapterTitle *",
            "LocalizedEuUiGraphics_GetChapterTitle(u32 titleId)",
            "{",
            "    if (titleId >= LOCALIZED_UI_GRAPHICS_CHAPTER_TITLE_COUNT)",
            "        return 0;",
            "",
            "    switch (ExpansionLocale_GetCurrent())",
            "    {",
        ]
    )
    for locale in PRODUCTION_EU_LOCALES:
        lines.extend(
            [
                f"    case EXPANSION_LOCALE_{locale.upper()}:",
                f"        return &sLocalizedEu{locale.title()}ChapterTitles[titleId];",
            ]
        )
    lines.extend(
        [
            "    default:",
            "        return 0;",
            "    }",
            "}",
            "",
            "#endif /* LOCALIZED_UI_GRAPHICS_EU_ENABLED */",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def extract(rom_path: Path) -> None:
    source = Path(rom_path).read_bytes()
    actual_sha256 = sha256(source)
    if actual_sha256 != EU_ROM_SHA256:
        raise EuLocalizationError(
            f"{rom_path}: SHA-256 {actual_sha256}, expected {EU_ROM_SHA256}"
        )
    if len(source) != 0x02000000:
        raise EuLocalizationError(
            f"{rom_path}: expected 32 MiB, got {len(source)} bytes"
        )

    text_manifest = extract_texts(source)
    extract_fonts(source, text_manifest)
    extract_ui(source)


def _check_asset(asset: dict[str, object]) -> None:
    path = ROOT / str(asset["path"])
    data = path.read_bytes()
    if asset["kind"] == "tiled_4bpp_png":
        raw, dimensions = decode_tiled_4bpp_png(data)
        if (
            len(data) != asset["png_size"]
            or sha256(data) != asset["png_sha256"]
            or len(raw) != asset["raw_size"]
            or sha256(raw) != asset["raw_sha256"]
            or dimensions != asset["dimensions"]
        ):
            raise EuLocalizationError(f"{path}: PNG provenance drift")
    elif len(data) != asset["size"] or sha256(data) != asset["sha256"]:
        raise EuLocalizationError(f"{path}: binary provenance drift")


def check() -> None:
    text_manifest = json.loads(TEXT_MANIFEST_PATH.read_text(encoding="utf-8"))
    if text_manifest["source"]["sha256"] != EU_ROM_SHA256:
        raise EuLocalizationError("EU text source hash drift")
    if text_manifest["bank_order"] != list(EU_BANK_ORDER):
        raise EuLocalizationError("EU text bank order drift")
    for locale in EU_BANK_ORDER:
        record = text_manifest["locales"][locale]
        path = ROOT / record["normalized_path"]
        content = path.read_bytes()
        if (
            len(content) != record["normalized_byte_count"]
            or sha256(content) != record["normalized_sha256"]
        ):
            raise EuLocalizationError(f"{path}: normalized source drift")

    font_manifest = json.loads(FONT_MANIFEST_PATH.read_text(encoding="utf-8"))
    if font_manifest["source"]["sha256"] != EU_ROM_SHA256:
        raise EuLocalizationError("EU font source hash drift")
    for style in FONT_TABLES:
        for record in font_manifest["styles"][style]["files"].values():
            path = ROOT / record["path"]
            content = path.read_bytes()
            if (
                len(content) != record["byte_count"]
                or sha256(content) != record["sha256"]
            ):
                raise EuLocalizationError(f"{path}: font artifact drift")

    ui_manifest = json.loads(UI_MANIFEST_PATH.read_text(encoding="utf-8"))
    if ui_manifest["source"]["sha256"] != EU_ROM_SHA256:
        raise EuLocalizationError("EU UI source hash drift")
    for resource in ui_manifest["compressed_resources"]:
        for asset in resource["variants"].values():
            _check_asset(asset)
    for resource in ui_manifest["raw_resources"]:
        for asset in resource["variants"].values():
            _check_asset(asset)
    for resource in ui_manifest["ap_resources"]:
        for asset in resource["variants"].values():
            path = ROOT / asset["path"]
            content = path.read_bytes()
            if len(content) != asset["size"] or sha256(content) != asset["sha256"]:
                raise EuLocalizationError(f"{path}: AP definition drift")
    for variant in ui_manifest["subtitles"].values():
        for slide in variant["slides"]:
            _check_asset(slide["gfx"])
            _check_asset(slide["tsa"])
    for variant in ui_manifest["chapter_titles"]["variants"].values():
        for asset in variant["assets"]:
            _check_asset(asset)
    generated_ui_source = generate_ui_source(ui_manifest)
    if UI_DATA_PATH.read_bytes() != generated_ui_source:
        raise EuLocalizationError(f"{UI_DATA_PATH}: regenerate with extract")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument("--rom", type=Path, required=True)
    subparsers.add_parser("check")
    args = parser.parse_args()

    if args.command == "extract":
        extract(args.rom)
    else:
        check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
