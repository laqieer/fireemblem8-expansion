"""Generate the ending-detail layout metric gate from runtime font assets."""

from __future__ import annotations

import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence, Tuple


ENDING_SOURCE_PATH = Path("src/ending_details.c")
FONT_MANIFEST_PATH = Path("graphics/fonts/cjk/manifest.json")
ASCII_FONT_PATH = Path("src/data/fonts/glyphs_1.h")
TITLE_LUT_RE = re.compile(
    r"struct EndingTitleEnt CONST_DATA gCharacterEndingTitleLut\[\] =\s*"
    r"\{(.*?)\n\};",
    re.DOTALL,
)
PAIRED_ENTRY_RE = re.compile(
    r"\{\s*CHARACTER_ENDING_PAIRED,\s*CHARACTER_[A-Z0-9_]+,\s*"
    r"CHARACTER_[A-Z0-9_]+,\s*MSG_([0-9A-F]+),\s*\}"
)
MESSAGE_ID_RE = re.compile(r"MSG_([0-9A-F]+)")
CONTROL_RE = re.compile(r"\[CTRL:([0-9A-F]{4})\]")


class EndingLayoutError(ValueError):
    """Raised when localized ending text exceeds its real Text allocation."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _allocation_width(source: str, pattern: str, label: str) -> int:
    matches = re.findall(pattern, source)
    if len(matches) != 1:
        raise EndingLayoutError(
            f"{ENDING_SOURCE_PATH}: expected one {label} InitText allocation"
        )
    return int(matches[0])


def _ending_contract(repo_root: Path) -> Dict[str, Any]:
    path = repo_root / ENDING_SOURCE_PATH
    source_bytes = path.read_bytes()
    source = source_bytes.decode("utf-8")
    title_lut = TITLE_LUT_RE.search(source)
    if title_lut is None:
        raise EndingLayoutError(f"{path}: ending title LUT is missing")
    title_ids = tuple(
        int(message_id, 16)
        for message_id in MESSAGE_ID_RE.findall(title_lut.group(1))
    )
    paired_ids = tuple(
        sorted(
            {
                int(message_id, 16)
                for message_id in PAIRED_ENTRY_RE.findall(source)
            }
        )
    )
    if len(title_ids) != 33 or len(set(title_ids)) != len(title_ids):
        raise EndingLayoutError(f"{path}: ending title IDs drifted")
    if paired_ids != tuple(range(0x0817, 0x0839)):
        raise EndingLayoutError(f"{path}: paired ending IDs drifted")

    title_tiles = _allocation_width(
        source,
        r"InitText\(gpCharacterEndingTexts \+ 5 \+ i,\s*(\d+)\);",
        "title",
    )
    paired_tiles = _allocation_width(
        source,
        r"InitText\(gpCharacterEndingTexts \+ i,\s*(\d+)\);",
        "paired ending",
    )
    return {
        "source": {
            "path": ENDING_SOURCE_PATH.as_posix(),
            "sha256": _sha256(source_bytes),
        },
        "title_ids": title_ids,
        "paired_ids": paired_ids,
        "title": {
            "text_count": 2,
            "text_index_start": 5,
            "tile_width": title_tiles,
            "pixel_width": title_tiles * 8,
        },
        "paired": {
            "text_count": 5,
            "text_index_start": 0,
            "tile_width": paired_tiles,
            "pixel_width": paired_tiles * 8,
        },
    }


def _ascii_widths(repo_root: Path) -> Dict[int, int]:
    path = repo_root / ASCII_FONT_PATH
    text = path.read_text(encoding="utf-8")
    glyph_widths = {
        int(name): int(width)
        for name, width in re.findall(
            r"struct Glyph gFontgrp_(\d+) =\s*\{.*?\.width = (\d+),",
            text,
            flags=re.DOTALL,
        )
    }
    table = re.search(
        r"struct Glyph \*TextGlyphs_System\[\] =\s*\{(.*?)\n\};",
        text,
        flags=re.DOTALL,
    )
    if table is None:
        raise EndingLayoutError(f"{path}: TextGlyphs_System is missing")
    entries = re.findall(r"NULL|&gFontgrp_\d+", table.group(1))
    return {
        index: glyph_widths[int(entry.removeprefix("&gFontgrp_"))]
        for index, entry in enumerate(entries)
        if entry != "NULL"
    }


def _cjk_widths(repo_root: Path, locale: str) -> Tuple[Dict[int, int], Dict[str, str]]:
    manifest_path = repo_root / FONT_MANIFEST_PATH
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    asset = manifest["assets"][f"{locale}.system"]
    codepoint_path = repo_root / asset["codepoints"]["path"]
    width_path = repo_root / asset["widths"]["path"]
    width_bytes = width_path.read_bytes()
    codepoint_bytes = codepoint_path.read_bytes()
    if len(codepoint_bytes) != len(width_bytes) * 4:
        raise EndingLayoutError(f"{codepoint_path}: codepoint/width count mismatch")
    codepoints = struct.unpack(f"<{len(width_bytes)}I", codepoint_bytes)
    return dict(zip(codepoints, width_bytes)), {
        "codepoints_path": codepoint_path.relative_to(repo_root).as_posix(),
        "codepoints_sha256": _sha256(codepoint_bytes),
        "widths_path": width_path.relative_to(repo_root).as_posix(),
        "widths_sha256": _sha256(width_bytes),
    }


def _runtime_lines(text: str, *, paired: bool) -> Tuple[str, ...]:
    lines = [""]
    cursor = 0
    for match in CONTROL_RE.finditer(text):
        lines[-1] += text[cursor : match.start()]
        control = int(match.group(1), 16)
        if control == 0x0001:
            lines.append("")
        cursor = match.end()
    lines[-1] += text[cursor:]
    if "\r" in text or "\n" in text:
        raise EndingLayoutError(
            "ending text contains a physical newline instead of runtime control 0x0001"
        )
    expected_lines = 5 if paired else 1
    if len(lines) != expected_lines:
        raise EndingLayoutError(
            f"ending text renders {len(lines)} lines; expected {expected_lines}"
        )
    return tuple(lines)


def _line_width(
    line: str,
    *,
    locale: str,
    ascii_widths: Mapping[int, int],
    cjk_widths: Mapping[int, int],
) -> int:
    width = 0
    for character in line:
        scalar = ord(character)
        if scalar < 0x20:
            continue
        if scalar < 0x80:
            if scalar not in ascii_widths:
                raise EndingLayoutError(
                    f"{locale}: ASCII U+{scalar:04X} is absent from the system font"
                )
            width += ascii_widths[scalar]
        elif scalar == 0x3000:
            width += 16
        else:
            if scalar not in cjk_widths:
                raise EndingLayoutError(
                    f"{locale}: U+{scalar:04X} is absent from committed system metrics"
                )
            width += cjk_widths[scalar]
    return width


def _records(
    message_ids: Sequence[int],
    *,
    locale: str,
    payloads: Mapping[int, str],
    paired: bool,
    allocation_pixels: int,
    ascii_widths: Mapping[int, int],
    cjk_widths: Mapping[int, int],
) -> Tuple[Dict[str, Any], ...]:
    records = []
    for message_id in message_ids:
        if message_id not in payloads:
            raise EndingLayoutError(
                f"{locale}: ending target 0x{message_id:04X} has no payload"
            )
        lines = _runtime_lines(payloads[message_id], paired=paired)
        widths = [
            _line_width(
                line,
                locale=locale,
                ascii_widths=ascii_widths,
                cjk_widths=cjk_widths,
            )
            for line in lines
        ]
        if max(widths) > allocation_pixels:
            raise EndingLayoutError(
                f"{locale} 0x{message_id:04X}: line width {max(widths)}px "
                f"exceeds {allocation_pixels}px Text allocation"
            )
        records.append(
            {
                "line_count": len(lines),
                "line_widths": widths,
                "max_line_width": max(widths),
                "target_id": f"0x{message_id:04X}",
            }
        )
    return tuple(records)


def build_ending_layout_metrics(
    repo_root: Path,
    *,
    localized_payloads: Mapping[str, Mapping[int, str]],
) -> Dict[str, Any]:
    repo_root = Path(repo_root)
    contract = _ending_contract(repo_root)
    ascii_widths = _ascii_widths(repo_root)
    locales = {}
    font_inputs = {}
    for locale in ("ja", "zh-Hans"):
        cjk_widths, font_input = _cjk_widths(repo_root, locale)
        font_inputs[locale] = font_input
        titles = _records(
            contract["title_ids"],
            locale=locale,
            payloads=localized_payloads[locale],
            paired=False,
            allocation_pixels=contract["title"]["pixel_width"],
            ascii_widths=ascii_widths,
            cjk_widths=cjk_widths,
        )
        paired = _records(
            contract["paired_ids"],
            locale=locale,
            payloads=localized_payloads[locale],
            paired=True,
            allocation_pixels=contract["paired"]["pixel_width"],
            ascii_widths=ascii_widths,
            cjk_widths=cjk_widths,
        )
        locales[locale] = {
            "paired": list(paired),
            "summary": {
                "max_paired_line_width": max(
                    record["max_line_width"] for record in paired
                ),
                "max_title_width": max(
                    record["max_line_width"] for record in titles
                ),
                "overflow_count": 0,
                "paired_target_count": len(paired),
                "title_target_count": len(titles),
            },
            "titles": list(titles),
        }
    return {
        "allocations": {
            "paired": contract["paired"],
            "title": contract["title"],
        },
        "font_inputs": font_inputs,
        "kind": "fe8u-ending-layout-metrics",
        "locales": locales,
        "schema_version": 1,
        "source": contract["source"],
        "summary": {
            "locale_count": len(locales),
            "overflow_count": 0,
            "paired_target_count": len(contract["paired_ids"]),
            "title_target_count": len(contract["title_ids"]),
        },
    }
