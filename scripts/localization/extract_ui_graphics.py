#!/usr/bin/env python3
"""Extract provenance-pinned localized UI graphics for issue #18.

The committed files under graphics/localized_ui are decompressed sources.  The
normal Makefile LZ rule recreates build-local .lz files when compiling the
modern ROM; those generated files must not be committed.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import struct
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_ROOT = ROOT / "graphics" / "localized_ui"
MANIFEST_PATH = OUTPUT_ROOT / "manifest.json"
DATA_PATH = ROOT / "src" / "data" / "localized_ui_graphics.c"

JP_SHA256 = "44fd343625ab9e6b90f63a80758c15066d526e6873fae91474006314a5ead464"
CN_SHA256 = "7a9477ec47be4e1cb5d0a1505eab1929b9e7622295464c3a88b919da543015d6"

CHAPTER_TABLE = 0x08A732C0
CHAPTER_TITLE_COUNT = 88
CHAPTER_TSA = 0x08A92410
SUBTITLE_TABLE = 0x081F6C94
SUBTITLE_COUNT = 7

VARIANTS = {
    "ja": {
        "rom_sha256": JP_SHA256,
        "rom_path": "../fireemblem8j/baserom.gba",
        "title": {
            "logo": 0x08B44B40,
            "labels": 0x08B45958,
        },
        "menu": 0x08AA59A0,
        "main_sprites": 0x08AA39DC,
        # InitDifficultySelectScreen's localized literal at 0x080B0C34.
        "difficulty_menu": 0x08AA65A8,
        "chapter_frame": 0x08A8BFA4,
    },
    "zh-Hans": {
        "rom_sha256": CN_SHA256,
        "rom_path": "../FE8CN.gba",
        # These two addresses are the localized literals in
        # Title_SetupMainGraphics at 0x080CA5D8 and 0x080CA600.
        "title": {
            "logo": 0x08B3B74C,
            "labels": 0x08B3C550,
        },
        "menu": 0x08AA59A0,
        "main_sprites": 0x08B44B40,
        # InitDifficultySelectScreen's localized literal at 0x080B0C34.
        "difficulty_menu": 0x08AA39DC,
        # The localized literal in _PutChapterTitleGfx at 0x0808B924.
        "chapter_frame": 0x08A7E188,
    },
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def decomp_lz77(source: bytes, offset: int) -> bytes:
    if source[offset] != 0x10:
        raise ValueError(f"0x{offset + 0x08000000:08X}: expected LZ77 data")

    output_size = int.from_bytes(source[offset + 1 : offset + 4], "little")
    output = bytearray()
    position = offset + 4

    while len(output) < output_size:
        flags = source[position]
        position += 1

        for bit in range(8):
            if flags & (0x80 >> bit):
                first = source[position]
                second = source[position + 1]
                position += 2
                length = (first >> 4) + 3
                distance = ((first & 0x0F) << 8 | second) + 1

                if distance > len(output):
                    raise ValueError(
                        f"0x{offset + 0x08000000:08X}: invalid LZ77 distance"
                    )

                for _ in range(length):
                    output.append(output[-distance])
                    if len(output) == output_size:
                        break
            else:
                output.append(source[position])
                position += 1

            if len(output) == output_size:
                break

    return bytes(output)


def read_word(source: bytes, address: int) -> int:
    return struct.unpack_from("<I", source, address - 0x08000000)[0]


def write_if_changed(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_bytes() != content:
        path.write_bytes(content)


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    )


def tiled_dimensions(raw: bytes, tiles_per_row: int | None = None) -> tuple[int, int, int]:
    if len(raw) == 0 or len(raw) % 32:
        raise ValueError("4bpp tile data must contain a nonzero whole tile count")
    tiles = len(raw) // 32
    if tiles_per_row is None:
        tiles_per_row = max(
            divisor for divisor in range(1, min(32, tiles) + 1) if tiles % divisor == 0
        )
    elif tiles_per_row <= 0 or tiles_per_row > 32 or tiles % tiles_per_row:
        raise ValueError("4bpp tile data dimensions are invalid")
    return tiles_per_row * 8, (tiles // tiles_per_row) * 8, tiles_per_row


def encode_tiled_4bpp_png(
    raw: bytes, tiles_per_row: int | None = None
) -> tuple[bytes, dict[str, int]]:
    """Encode GBA tile-order 4bpp bytes as a canonical indexed PNG source."""

    width, height, tiles_per_row = tiled_dimensions(raw, tiles_per_row)
    pixels = bytearray(width * height)
    for tile in range(len(raw) // 32):
        tile_x = (tile % tiles_per_row) * 8
        tile_y = (tile // tiles_per_row) * 8
        for y in range(8):
            for pair in range(4):
                packed = raw[tile * 32 + y * 4 + pair]
                pixels[(tile_y + y) * width + tile_x + pair * 2] = packed & 0xF
                pixels[(tile_y + y) * width + tile_x + pair * 2 + 1] = packed >> 4

    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        for x in range(0, width, 2):
            scanlines.append((pixels[y * width + x] << 4) | pixels[y * width + x + 1])
    palette = bytes(component for index in range(16) for component in (index * 17,) * 3)
    png = (
        PNG_SIGNATURE
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 4, 3, 0, 0, 0))
        + _png_chunk(b"PLTE", palette)
        + _png_chunk(b"IDAT", zlib.compress(bytes(scanlines), level=9))
        + _png_chunk(b"IEND", b"")
    )
    return png, {"width": width, "height": height, "tiles_per_row": tiles_per_row}


def decode_tiled_4bpp_png(png: bytes) -> tuple[bytes, dict[str, int]]:
    """Strictly decode the extractor's indexed PNG format to raw GBA tiles."""

    if not png.startswith(PNG_SIGNATURE):
        raise ValueError("PNG signature is invalid")
    position = len(PNG_SIGNATURE)
    chunks = []
    while position < len(png):
        if position + 12 > len(png):
            raise ValueError("PNG is truncated")
        size = struct.unpack_from(">I", png, position)[0]
        kind = png[position + 4 : position + 8]
        start = position + 8
        end = start + size
        if end + 4 > len(png):
            raise ValueError("PNG chunk exceeds bounds")
        payload = png[start:end]
        if struct.unpack_from(">I", png, end)[0] != binascii.crc32(kind + payload) & 0xFFFFFFFF:
            raise ValueError("PNG CRC mismatch")
        chunks.append((kind, payload))
        position = end + 4
        if kind == b"IEND":
            break
    if position != len(png) or [kind for kind, _ in chunks] != [
        b"IHDR", b"PLTE", b"IDAT", b"IEND"
    ]:
        raise ValueError("PNG chunk layout is invalid")
    width, height, depth, color, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", chunks[0][1]
    )
    if (
        depth != 4
        or color != 3
        or compression != 0
        or filtering != 0
        or interlace != 0
        or width % 8
        or height % 8
        or width * height % 64
        or len(chunks[1][1]) != 48
    ):
        raise ValueError("PNG dimensions or indexed format are invalid")
    packed_row = width // 2
    raw_scanlines = zlib.decompress(chunks[2][1])
    if len(raw_scanlines) != height * (packed_row + 1):
        raise ValueError("PNG scanline length is invalid")
    pixels = bytearray(width * height)
    for y in range(height):
        offset = y * (packed_row + 1)
        if raw_scanlines[offset] != 0:
            raise ValueError("PNG must use filter None")
        for x in range(packed_row):
            value = raw_scanlines[offset + 1 + x]
            pixels[y * width + x * 2] = value >> 4
            pixels[y * width + x * 2 + 1] = value & 0xF
    tiles_per_row = width // 8
    output = bytearray((width * height // 64) * 32)
    for tile in range(len(output) // 32):
        tile_x = (tile % tiles_per_row) * 8
        tile_y = (tile // tiles_per_row) * 8
        for y in range(8):
            for pair in range(4):
                left = pixels[(tile_y + y) * width + tile_x + pair * 2]
                right = pixels[(tile_y + y) * width + tile_x + pair * 2 + 1]
                output[tile * 32 + y * 4 + pair] = (right << 4) | left
    return bytes(output), {
        "width": width,
        "height": height,
        "tiles_per_row": tiles_per_row,
    }


def c_name(locale: str, name: str) -> str:
    locale_name = {"ja": "Ja", "zh-Hans": "ZhHans"}[locale]
    return "sLocalizedUi%s%s" % (
        locale_name,
        "".join(
            piece.title()
            for piece in name.replace("/", " ").replace("_", " ").split()
        ),
    )


def make_asset(
    locale: str,
    name: str,
    address: int,
    source: bytes,
    extension: str,
    tiles_per_row: int | None = None,
) -> dict[str, object]:
    raw = decomp_lz77(source, address - 0x08000000)
    relative_path = f"{locale}/{name}.{extension}"
    path = OUTPUT_ROOT / relative_path
    asset = {
        "name": name,
        "path": f"graphics/localized_ui/{relative_path}",
        "source_address": f"0x{address:08X}",
        "c_name": c_name(locale, f"{name}_{extension.replace('.', '_')}"),
    }
    if extension == "png":
        png, dimensions = encode_tiled_4bpp_png(raw, tiles_per_row)
        if decode_tiled_4bpp_png(png)[0] != raw:
            raise ValueError(f"{relative_path}: PNG round trip changed tile bytes")
        write_if_changed(path, png)
        raw_path = path.with_suffix(".4bpp")
        raw_path.unlink(missing_ok=True)
        raw_path.with_suffix(".4bpp.lz").unlink(missing_ok=True)
        asset.update(
            {
                "kind": "tiled_4bpp_png",
                "raw_size": len(raw),
                "raw_sha256": sha256(raw),
                "png_size": len(png),
                "png_sha256": sha256(png),
                "dimensions": dimensions,
                "build_path": f"graphics/localized_ui/{locale}/{name}.4bpp",
            }
        )
    else:
        write_if_changed(path, raw)
        asset.update(
            {
                "kind": "tsa",
                "size": len(raw),
                "sha256": sha256(raw),
            }
        )
    return asset


def build_variant(locale: str, source: bytes) -> dict[str, object]:
    spec = VARIANTS[locale]
    assets: list[dict[str, object]] = []
    by_address: dict[int, int] = {}

    def add(
        name: str,
        address: int,
        extension: str = "png",
        tiles_per_row: int | None = None,
    ) -> int:
        if address in by_address:
            return by_address[address]
        by_address[address] = len(assets)
        assets.append(make_asset(locale, name, address, source, extension, tiles_per_row))
        return by_address[address]

    title = {
        key: add(f"title/{key}", value)
        for key, value in spec["title"].items()
    }
    menu = add("menu/main_extra_options", spec["menu"])
    main_sprites = add("menu/main_sprites", spec["main_sprites"], tiles_per_row=32)

    subtitle: list[dict[str, int]] = []
    for index in range(SUBTITLE_COUNT):
        entry = SUBTITLE_TABLE - 0x08000000 + index * 12
        gfx, tsa, timer = struct.unpack_from("<III", source, entry)
        subtitle.append(
            {
                "gfx": add(f"prologue/slide_{index:02d}", gfx),
                "tsa": add(f"prologue/slide_{index:02d}", tsa, "tsa.bin"),
                "timer": timer,
            }
        )

    chapters: list[list[int | None]] = []
    for index in range(CHAPTER_TITLE_COUNT):
        save, left, right = struct.unpack_from(
            "<III", source, CHAPTER_TABLE - 0x08000000 + index * 12
        )
        chapters.append(
            [
                add(f"chapter/asset_{save:08X}", save) if save else None,
                add(f"chapter/asset_{left:08X}", left) if left else None,
                add(f"chapter/asset_{right:08X}", right) if right else None,
            ]
        )

    chapter = {
        "table_address": f"0x{CHAPTER_TABLE:08X}",
        "entries": chapters,
        "frame": add("chapter/frame", spec["chapter_frame"]),
        "tsa": add("chapter/title_layout", CHAPTER_TSA, "tsa.bin"),
    }
    difficulty_menu = add("menu/difficulty_mode", spec["difficulty_menu"], tiles_per_row=32)

    return {
        "rom_sha256": spec["rom_sha256"],
        "rom_path": spec["rom_path"],
        "title": title,
        "menu": menu,
        "main_sprites": main_sprites,
        "difficulty_menu": difficulty_menu,
        "subtitle": subtitle,
        "chapter": chapter,
        "assets": assets,
    }


def asset_ref(variant: dict[str, object], index: int | None) -> str:
    if index is None:
        return "0"
    return str(variant["assets"][index]["c_name"])


def generate_data_source(variants: dict[str, dict[str, object]]) -> bytes:
    lines = [
        '#include "global.h"',
        "",
        '#include "localized_ui_graphics.h"',
        "",
        "#if LOCALIZED_UI_GRAPHICS_CJK_ENABLED",
        "",
        "/* Generated by scripts/localization/extract_ui_graphics.py. */",
    ]

    for locale, variant in variants.items():
        for asset in variant["assets"]:
            lines.append(
                "static const u8 %s[] = INCBIN_U8(\"%s.lz\");"
                % (asset["c_name"], asset.get("build_path", asset["path"]))
            )
        lines.append("")

    for locale, variant in variants.items():
        locale_c = {"ja": "Ja", "zh-Hans": "ZhHans"}[locale]
        lines.append(
            "static const struct LocalizedUiGraphicsSubtitleSlide sLocalizedUi%sSubtitle[] = {"
            % locale_c
        )
        for slide in variant["subtitle"]:
            lines.append(
                "    { %s, %s, %d },"
                % (
                    asset_ref(variant, slide["gfx"]),
                    asset_ref(variant, slide["tsa"]),
                    slide["timer"],
                )
            )
        lines.extend(["};", ""])

        lines.append(
            "static const struct LocalizedUiGraphicsChapterTitle sLocalizedUi%sChapterTitles[] = {"
            % locale_c
        )
        for save, left, right in variant["chapter"]["entries"]:
            lines.append(
                "    { %s, %s, %s },"
                % (
                    asset_ref(variant, save),
                    asset_ref(variant, left),
                    asset_ref(variant, right),
                )
            )
        lines.extend(["};", ""])

    lines.extend(
        [
            "static const u16 sLocalizedUiTitleLogo[] = {",
            "    16,",
            "    0x0000, 0x8000, 0x0000, 0x4020, 0x8000, 0x0080,",
            "    0x0000, 0x8020, 0x0004, 0x4020, 0x8020, 0x0084,",
            "    0x0000, 0x8040, 0x0008, 0x4020, 0x8040, 0x0088,",
            "    0x0000, 0x8060, 0x000C, 0x4020, 0x8060, 0x008C,",
            "    0x0000, 0x8080, 0x0010, 0x4020, 0x8080, 0x0090,",
            "    0x0000, 0x80A0, 0x0014, 0x4020, 0x80A0, 0x0094,",
            "    0x0000, 0x80C0, 0x0018, 0x4020, 0x80C0, 0x0098,",
            "    0x8000, 0x80E0, 0x001C, 0x0020, 0x40E0, 0x009C,",
            "};",
            "static const u16 sLocalizedUiTitleExtra[] = {",
            "    4, 0x4000, 0x8000, 0x0000, 0x4000, 0x8020, 0x0004,",
            "    0x4000, 0x8040, 0x0008, 0x4000, 0x8060, 0x000C,",
            "};",
            "static const u16 sLocalizedUiTitleSubtitle[] = {",
            "    5, 0x4000, 0x8000, 0x0000, 0x4000, 0x8020, 0x0004,",
            "    0x4000, 0x8040, 0x0008, 0x4000, 0x8060, 0x000C,",
            "    0x0000, 0x4080, 0x0010,",
            "};",
            "static const u16 sLocalizedUiTitleBanner[] = {",
            "    2, 0x4000, 0xC000, 0x0000, 0x4000, 0xC040, 0x0008,",
            "};",
            "static const u16 sLocalizedUiTitleCopyright[] = {",
            "    7, 0x4000, 0x4000, 0x0000, 0x4000, 0x4020, 0x0004,",
            "    0x4000, 0x4040, 0x0008, 0x4000, 0x4060, 0x000C,",
            "    0x4000, 0x4080, 0x0010, 0x4000, 0x40A0, 0x0014,",
            "    0x0000, 0x00C0, 0x0018,",
            "};",
            "static const u16 sLocalizedUiTitlePressStart[] = {",
            "    3, 0x4000, 0x8000, 0x0000, 0x4000, 0x8020, 0x0004,",
            "    0x0000, 0x4040, 0x0008,",
            "};",
            "static const struct LocalizedUiGraphicsTitleSprites sLocalizedUiTitleSprites = {",
            "    sLocalizedUiTitleLogo, sLocalizedUiTitleExtra, sLocalizedUiTitleSubtitle,",
            "    sLocalizedUiTitleBanner, sLocalizedUiTitleCopyright, sLocalizedUiTitlePressStart,",
            "};",
            "",
            "static ExpansionLocaleId LocalizedUiGraphics_CurrentCjkLocale(void)",
            "{",
            "    ExpansionLocaleId locale = ExpansionLocale_GetCurrent();",
            "",
            "    if (locale == EXPANSION_LOCALE_JA || locale == EXPANSION_LOCALE_ZH_HANS)",
            "        return locale;",
            "",
            "    return EXPANSION_LOCALE_INVALID;",
            "}",
            "",
            "const struct LocalizedUiGraphicsTitle *LocalizedUiGraphics_GetTitle(void)",
            "{",
            "    static const struct LocalizedUiGraphicsTitle titleJa = {",
            "        %s, %s,"
            % (
                asset_ref(variants["ja"], variants["ja"]["title"]["logo"]),
                asset_ref(variants["ja"], variants["ja"]["title"]["labels"]),
            ),
            "    };",
            "    static const struct LocalizedUiGraphicsTitle titleZhHans = {",
            "        %s, %s,"
            % (
                asset_ref(variants["zh-Hans"], variants["zh-Hans"]["title"]["logo"]),
                asset_ref(variants["zh-Hans"], variants["zh-Hans"]["title"]["labels"]),
            ),
            "    };",
            "",
            "    switch (LocalizedUiGraphics_CurrentCjkLocale()) {",
            "    case EXPANSION_LOCALE_JA:",
            "        return &titleJa;",
            "    case EXPANSION_LOCALE_ZH_HANS:",
            "        return &titleZhHans;",
            "    default:",
            "        return 0;",
            "    }",
            "}",
            "",
            "const u8 *LocalizedUiGraphics_GetSaveMenuOptions(void)",
            "{",
            "    switch (LocalizedUiGraphics_CurrentCjkLocale()) {",
            "    case EXPANSION_LOCALE_JA:",
            "        return %s;" % asset_ref(variants["ja"], variants["ja"]["menu"]),
            "    case EXPANSION_LOCALE_ZH_HANS:",
            "        return %s;" % asset_ref(variants["zh-Hans"], variants["zh-Hans"]["menu"]),
            "    default:",
            "        return 0;",
            "    }",
            "}",
            "",
            "const u8 *LocalizedUiGraphics_GetSaveMenuMainSprites(void)",
            "{",
            "    switch (LocalizedUiGraphics_CurrentCjkLocale()) {",
            "    case EXPANSION_LOCALE_JA:",
            "        return %s;" % asset_ref(variants["ja"], variants["ja"]["main_sprites"]),
            "    case EXPANSION_LOCALE_ZH_HANS:",
            "        return %s;" % asset_ref(variants["zh-Hans"], variants["zh-Hans"]["main_sprites"]),
            "    default:",
            "        return 0;",
            "    }",
            "}",
            "",
            "const u8 *LocalizedUiGraphics_GetDifficultyMenuObjects(void)",
            "{",
            "    switch (LocalizedUiGraphics_CurrentCjkLocale()) {",
            "    case EXPANSION_LOCALE_JA:",
            "        return %s;" % asset_ref(variants["ja"], variants["ja"]["difficulty_menu"]),
            "    case EXPANSION_LOCALE_ZH_HANS:",
            "        return %s;" % asset_ref(variants["zh-Hans"], variants["zh-Hans"]["difficulty_menu"]),
            "    default:",
            "        return 0;",
            "    }",
            "}",
            "",
            "const struct LocalizedUiGraphicsSubtitleSlide *LocalizedUiGraphics_GetSubtitleSlides(void)",
            "{",
            "    switch (LocalizedUiGraphics_CurrentCjkLocale()) {",
            "    case EXPANSION_LOCALE_JA:",
            "        return sLocalizedUiJaSubtitle;",
            "    case EXPANSION_LOCALE_ZH_HANS:",
            "        return sLocalizedUiZhHansSubtitle;",
            "    default:",
            "#if LOCALIZED_UI_GRAPHICS_EU_ENABLED",
            "        return LocalizedEuUiGraphics_GetSubtitleSlides();",
            "#else",
            "        return 0;",
            "#endif",
            "    }",
            "}",
            "",
            "const struct LocalizedUiGraphicsChapterTitle *LocalizedUiGraphics_GetChapterTitle(u32 titleId)",
            "{",
            "    if (titleId >= LOCALIZED_UI_GRAPHICS_CHAPTER_TITLE_COUNT)",
            "        return 0;",
            "",
            "    switch (LocalizedUiGraphics_CurrentCjkLocale()) {",
            "    case EXPANSION_LOCALE_JA:",
            "        return &sLocalizedUiJaChapterTitles[titleId];",
            "    case EXPANSION_LOCALE_ZH_HANS:",
            "        return &sLocalizedUiZhHansChapterTitles[titleId];",
            "    default:",
            "#if LOCALIZED_UI_GRAPHICS_EU_ENABLED",
            "        return LocalizedEuUiGraphics_GetChapterTitle(titleId);",
            "#else",
            "        return 0;",
            "#endif",
            "    }",
            "}",
            "",
            "const u8 *LocalizedUiGraphics_GetChapterTitleFrame(void)",
            "{",
            "    switch (LocalizedUiGraphics_CurrentCjkLocale()) {",
            "    case EXPANSION_LOCALE_JA:",
            "        return %s;" % asset_ref(variants["ja"], variants["ja"]["chapter"]["frame"]),
            "    case EXPANSION_LOCALE_ZH_HANS:",
            "        return %s;" % asset_ref(variants["zh-Hans"], variants["zh-Hans"]["chapter"]["frame"]),
            "    default:",
            "        return 0;",
            "    }",
            "}",
            "",
            "const u8 *LocalizedUiGraphics_GetChapterTitleTsa(void)",
            "{",
            "    switch (LocalizedUiGraphics_CurrentCjkLocale()) {",
            "    case EXPANSION_LOCALE_JA:",
            "        return %s;" % asset_ref(variants["ja"], variants["ja"]["chapter"]["tsa"]),
            "    case EXPANSION_LOCALE_ZH_HANS:",
            "        return %s;" % asset_ref(variants["zh-Hans"], variants["zh-Hans"]["chapter"]["tsa"]),
            "    default:",
            "        return 0;",
            "    }",
            "}",
            "",
            "const struct LocalizedUiGraphicsTitleSprites *LocalizedUiGraphics_GetTitleSprites(void)",
            "{",
            "    return LocalizedUiGraphics_CurrentCjkLocale() == EXPANSION_LOCALE_INVALID",
            "        ? 0 : &sLocalizedUiTitleSprites;",
            "}",
            "",
            "#endif /* LOCALIZED_UI_GRAPHICS_CJK_ENABLED */",
            "",
        ]
    )
    return "\n".join(lines).encode("utf-8")


def extract(fe8j_root: Path, fe8cn_rom: Path) -> None:
    inputs = {
        "ja": fe8j_root / "baserom.gba",
        "zh-Hans": fe8cn_rom,
    }
    variants: dict[str, dict[str, object]] = {}

    for locale, path in inputs.items():
        source = path.read_bytes()
        expected_sha = VARIANTS[locale]["rom_sha256"]
        actual_sha = sha256(source)
        if actual_sha != expected_sha:
            raise ValueError(
                f"{path}: SHA-256 {actual_sha}, expected {expected_sha}"
            )
        variants[locale] = build_variant(locale, source)

    manifest = {
        "format": 1,
        "purpose": "issue-18 localized static UI graphics",
        "sources": {
            locale: {
                "path": variant["rom_path"],
                "sha256": variant["rom_sha256"],
            }
            for locale, variant in variants.items()
        },
        "chapter_title_count": CHAPTER_TITLE_COUNT,
        "subtitle_slide_count": SUBTITLE_COUNT,
        "variants": variants,
        "sprite_layout": build_sprite_layout_provenance(
            inputs["ja"].read_bytes(), inputs["zh-Hans"].read_bytes()
        ),
    }
    write_if_changed(
        MANIFEST_PATH,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    write_if_changed(DATA_PATH, generate_data_source(variants))


def build_sprite_layout_provenance(ja_source: bytes, zh_hans_source: bytes) -> dict[str, object]:
    address = 0x08B3ECE4
    size = 0xEC
    offset = address - 0x08000000
    layout = ja_source[offset : offset + size]

    if zh_hans_source[offset : offset + size] != layout:
        raise ValueError("Chinese title sprite layout differs from its pinned Japanese layout")

    return {
        "source": "../fireemblem8j/src/DrawTitleSprites_Loop.c",
        "ja_address": f"0x{address:08X}",
        "zh_Hans_address": f"0x{address:08X}",
        "size": size,
        "sha256": sha256(layout),
    }


def check() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest["chapter_title_count"] != CHAPTER_TITLE_COUNT:
        raise ValueError("chapter title count drift")
    if manifest["subtitle_slide_count"] != SUBTITLE_COUNT:
        raise ValueError("subtitle slide count drift")
    if manifest["sprite_layout"]["size"] != 0xEC:
        raise ValueError("title sprite layout size drift")

    for locale in ("ja", "zh-Hans"):
        variant = manifest["variants"][locale]
        difficulty_menu = variant.get("difficulty_menu")
        if (
            not isinstance(difficulty_menu, int)
            or difficulty_menu < 0
            or difficulty_menu >= len(variant["assets"])
        ):
            raise ValueError(f"{locale}: difficulty-menu asset is invalid")
        if len(variant["subtitle"]) != SUBTITLE_COUNT:
            raise ValueError(f"{locale}: subtitle slide count drift")
        if len(variant["chapter"]["entries"]) != CHAPTER_TITLE_COUNT:
            raise ValueError(f"{locale}: chapter table count drift")
        for asset in variant["assets"]:
            path = ROOT / asset["path"]
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
                    raise ValueError(f"{path}: PNG provenance or tile round trip drift")
            elif len(data) != asset["size"] or sha256(data) != asset["sha256"]:
                raise ValueError(f"{path}: TSA hash or size drift")

    generated = generate_data_source(manifest["variants"])
    if DATA_PATH.read_bytes() != generated:
        raise ValueError(f"{DATA_PATH}: regenerate with extract")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    extract_parser = subparsers.add_parser("extract")
    extract_parser.add_argument("--fe8j-root", type=Path, required=True)
    extract_parser.add_argument("--fe8cn-rom", type=Path, required=True)
    subparsers.add_parser("check")
    args = parser.parse_args()

    if args.command == "extract":
        extract(args.fe8j_root, args.fe8cn_rom)
    else:
        check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
