"""Read selected ARM relocatable font data, including native pointer addends."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import struct
import subprocess
from typing import TypedDict

from .model import GameCatalogError


class CompiledFontError(GameCatalogError):
    """A selected font object cannot establish its actual compiled metrics."""


class SelectedFontMetrics(TypedDict):
    ascii: dict[str, dict[int, int]]
    localized: dict[str, dict[str, dict[int, int]]]


@dataclass(frozen=True)
class _Section:
    kind: int
    offset: int
    size: int
    link: int
    info: int
    entry_size: int


@dataclass(frozen=True)
class _Symbol:
    name: str
    value: int
    size: int
    kind: int
    section: int


class _ArmObject:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        header = self._unpack("<16sHHIIIIIHHHHHH", 0)
        ident, kind, machine, version = header[:4]
        if ident[:7] != b"\x7fELF\x01\x01\x01" or (kind, machine, version) != (1, 40, 1):
            self._fail("expected a little-endian ELF32 ARM relocatable object")
        section_offset, header_size, entry_size, count = header[6], header[8], header[11], header[12]
        if header_size != 52 or entry_size != 40 or count == 0:
            self._fail("unsupported ELF header or section table")
        self.sections = []
        for index in range(count):
            row = self._unpack("<10I", section_offset + index * entry_size)
            self.sections.append(_Section(row[1], row[4], row[5], row[6], row[7], row[9]))
        self.tables = {}
        for index, section in enumerate(self.sections):
            if section.kind != 2:
                continue
            if section.entry_size != 16 or section.size % 16:
                self._fail("invalid font symbol table")
            strings = self._section_bytes(section.link, expected_kind=3)
            symbols = []
            raw = self._section_bytes(index)
            for offset in range(0, len(raw), 16):
                name, value, size, info, _other, owner = struct.unpack_from("<IIIBBH", raw, offset)
                end = strings.find(b"\0", name)
                if name >= len(strings) or end < 0:
                    self._fail("invalid font symbol name")
                try:
                    label = strings[name:end].decode("utf-8")
                except UnicodeDecodeError as error:
                    raise CompiledFontError(f"{self.path}: invalid UTF-8 symbol name") from error
                symbols.append(_Symbol(label, value, size, info & 15, owner))
            self.tables[index] = tuple(symbols)
        self.relocations = {}
        for index, section in enumerate(self.sections):
            if section.kind != 9:
                continue
            if (section.entry_size != 8 or section.size % 8
                    or section.link not in self.tables or not 0 < section.info < count):
                self._fail("invalid ARM relocation table")
            raw = self._section_bytes(index)
            for offset in range(0, len(raw), 8):
                location, info = struct.unpack_from("<II", raw, offset)
                key = section.info, location
                table = self.tables[section.link]
                if key in self.relocations or info >> 8 >= len(table):
                    self._fail("ambiguous or invalid ARM relocation")
                self.relocations[key] = info & 255, table[info >> 8]

    def _fail(self, message):
        raise CompiledFontError(f"{self.path}: {message}")

    def _bytes(self, offset, size):
        if offset < 0 or size < 0 or offset + size > len(self.data):
            self._fail("font object data exceeds its file")
        return self.data[offset:offset + size]

    def _unpack(self, format, offset):
        return struct.unpack(format, self._bytes(offset, struct.calcsize(format)))

    def _section_bytes(self, index, *, expected_kind=None):
        if not 0 < index < len(self.sections):
            self._fail("font data has no concrete section")
        section = self.sections[index]
        if section.kind == 8 or (expected_kind is not None and section.kind != expected_kind):
            self._fail("font data section has an unsupported kind")
        return self._bytes(section.offset, section.size)

    def symbol(self, name):
        symbols = [symbol for table in self.tables.values() for symbol in table
                   if symbol.name == name and symbol.section != 0 and symbol.kind == 1]
        if len(symbols) != 1:
            self._fail(f"expected exactly one defined font object symbol {name!r}")
        return symbols[0]

    def symbol_bytes(self, symbol):
        return self._data_bytes(symbol.section, symbol.value, symbol.size)

    def _data_bytes(self, index, offset, size):
        if not 0 < index < len(self.sections):
            self._fail("font data has no concrete section")
        section = self.sections[index]
        if section.kind not in (1, 8):
            self._fail("font data requires a PROGBITS or NOBITS section")
        if offset < 0 or size < 0 or offset + size > section.size:
            self._fail("font data exceeds its section")
        if section.kind == 8:
            return bytes(size)
        return self._bytes(section.offset + offset, size)

    def pointed_bytes(self, symbol, offset, size):
        table = self.symbol_bytes(symbol)
        if offset < 0 or offset + 4 > len(table) or offset % 4:
            self._fail("glyph pointer exceeds its table")
        addend = struct.unpack_from("<i", table, offset)[0]
        relocation = self.relocations.get((symbol.section, symbol.value + offset))
        if relocation is None:
            if addend == 0:
                return None
            self._fail("nonzero glyph pointer has no ARM relocation")
        kind, target = relocation
        if kind != 2 or target.kind not in (1, 3):
            self._fail("glyph pointer requires an ARM ABS32 data relocation")
        start = target.value + addend
        if target.kind == 1 and not target.value <= start < start + size <= target.value + target.size:
            self._fail("relocated glyph data exceeds its target object")
        return self._data_bytes(target.section, start, size)


def read_ascii_widths(
    font_object: Path, table_symbol: str, *, layout_object: Path,
) -> dict[int, int]:
    font = _ArmObject(font_object)
    layout = font if Path(font_object) == Path(layout_object) else _ArmObject(layout_object)
    facts = layout.symbol_bytes(layout.symbol("gSelectedTextGlyphLayout"))
    if len(facts) != 16:
        raise CompiledFontError(f"{layout_object}: expected four native glyph layout facts")
    pointer_size, glyph_size, width_offset, width_size = struct.unpack("<4I", facts)
    if pointer_size != 4 or width_size != 1 or not width_offset < glyph_size:
        raise CompiledFontError(f"{layout_object}: unsupported native glyph layout")
    table = font.symbol(table_symbol)
    if table.size % pointer_size:
        raise CompiledFontError(f"{font_object}: glyph pointer count is not integral")
    widths = {}
    for index in range(table.size // pointer_size):
        glyph = font.pointed_bytes(table, index * pointer_size, glyph_size)
        if glyph is not None:
            width = glyph[width_offset]
            widths[index] = width
    return widths


def read_localized_widths(
    font_object: Path, stem: str, *, layout_object: Path,
) -> dict[int, int]:
    font = _ArmObject(font_object)
    layout = font if Path(font_object) == Path(layout_object) else _ArmObject(layout_object)
    facts = layout.symbol_bytes(layout.symbol("gSelectedTextLocalizedLayout"))
    if len(facts) != 12:
        raise CompiledFontError(f"{layout_object}: expected three localized font layout facts")
    scalar_size, width_size, bitmap_stride = struct.unpack("<3I", facts)
    if scalar_size != 4 or width_size != 1 or bitmap_stride == 0:
        raise CompiledFontError(f"{layout_object}: unsupported localized font layout")
    count_data = font.symbol_bytes(font.symbol(stem + "GlyphCount"))
    if len(count_data) != 4:
        raise CompiledFontError(f"{font_object}: invalid compiled glyph count for {stem}")
    count = struct.unpack("<I", count_data)[0]
    codepoints = font.symbol_bytes(font.symbol(stem + "Codepoints"))
    widths = font.symbol_bytes(font.symbol(stem + "Widths"))
    bitmaps = font.symbol_bytes(font.symbol(stem + "Bitmaps"))
    if (len(codepoints) != count * scalar_size or len(widths) != count * width_size
            or len(bitmaps) != count * bitmap_stride):
        raise CompiledFontError(f"{font_object}: compiled localized font lengths differ for {stem}")
    scalars = struct.unpack("<" + "I" * count, codepoints)
    if (scalars != tuple(sorted(set(scalars)))
            or any(value > 0x10FFFF or 0xD800 <= value <= 0xDFFF for value in scalars)
            or any(not 1 <= width <= 16 for width in widths)):
        raise CompiledFontError(f"{font_object}: invalid compiled localized metrics for {stem}")
    return dict(zip(scalars, widths))


def measure_selected_fonts(context_path: Path, *, output_dir: Path) -> SelectedFontMetrics:
    context = json.loads(Path(context_path).read_text(encoding="utf-8"))
    if (not isinstance(context, dict) or type(context.get("schema_version")) is not int
            or context["schema_version"] != 1 or context.get("kind") != "fe8-selected-text-inputs"
            or not isinstance(context.get("source_root"), str)
            or not Path(context["source_root"]).is_absolute()
            or not isinstance(context.get("units"), list)):
        raise CompiledFontError(f"{context_path}: invalid selected compilation context")
    source_root = Path(context["source_root"])
    units = {}
    for unit in context["units"]:
        if (not isinstance(unit, dict) or any(
                not isinstance(unit.get(field), str) or not unit[field]
                for field in ("logical_source", "selected_source", "compiler_input", "object"))
                or not isinstance(unit.get("compiler_argv"), list)
                or any(not isinstance(argument, str) for argument in unit["compiler_argv"])):
            raise CompiledFontError(f"{context_path}: invalid selected compilation unit")
        name = unit["logical_source"]
        argv = unit["compiler_argv"]
        if (name in units or argv.count("-c") != 1 or len(argv) < 5
                or argv[-2:] != ["-o", unit["object"]]
                or argv[argv.index("-c") + 1] != unit["compiler_input"]):
            raise CompiledFontError(f"{context_path}: ambiguous selected compilation unit {name}")
        units[name] = unit

    def selected(name):
        if name not in units:
            raise CompiledFontError(f"{context_path}: required selected font unit {name} is absent")
        return units[name]

    renderer = selected("src/fontgrp.c")
    font = selected("src/fontgrp-data.c")
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    source = output_dir / "selected-font-layout.c"
    layout_path = output_dir / "selected-font-layout.o"
    source.write_text(
        f'#include {json.dumps(str(source_root / renderer["compiler_input"]))}\n'
        "const unsigned int gSelectedTextGlyphLayout[] = {\n"
        "    sizeof(struct Glyph *), sizeof(struct Glyph),\n"
        "    __builtin_offsetof(struct Glyph, width), sizeof(((struct Glyph *)0)->width)\n"
        "};\n"
        "const unsigned int gSelectedTextLocaleMask = FE8_EXPANSION_ENABLED_LOCALE_MASK;\n"
        "#ifdef FE8_LOCALIZED_FONT_ENABLED\n"
        "const unsigned int gSelectedTextLocalizedLayout[] = {\n"
        "    sizeof(u32), sizeof(u8), LOCALIZED_FONT_BITMAP_STRIDE\n"
        "};\n"
        "#endif\n",
        encoding="utf-8",
    )
    command = list(renderer["compiler_argv"])
    command[command.index("-c") + 1] = str(source)
    command[-1] = str(layout_path)
    for index, argument in enumerate(command[:-1]):
        if argument in ("-MF", "-MQ", "-MT"):
            command[index + 1] = str(layout_path.with_suffix(".d") if argument == "-MF" else layout_path)
    compiled = subprocess.run(
        command, cwd=source_root, capture_output=True, text=True, check=False,
    )
    if compiled.returncode:
        raise CompiledFontError(
            f"{context_path}: selected native font layout compile failed:\n"
            + compiled.stdout + compiled.stderr
        )
    layout = _ArmObject(layout_path)
    mask_bytes = layout.symbol_bytes(layout.symbol("gSelectedTextLocaleMask"))
    if len(mask_bytes) != 4:
        raise CompiledFontError(f"{layout_path}: invalid compiled locale mask")
    mask = struct.unpack("<I", mask_bytes)[0]
    if not mask or mask & ~0x7F:
        raise CompiledFontError(f"{layout_path}: unsupported compiled locale mask {mask:#x}")
    result: SelectedFontMetrics = {
        "ascii": {
            style: read_ascii_widths(source_root / font["object"], symbol, layout_object=layout_path)
            for style, symbol in (("system", "TextGlyphs_System"), ("talk", "TextGlyphs_Talk"))
        },
        "localized": {},
    }
    for bit, locale, stem in (
        (1, "ja", "Ja"), (2, "zh-Hans", "ZhHans"), (3, "fr", "Eu"),
        (4, "de", "Eu"), (5, "es", "Eu"), (6, "it", "Eu"),
    ):
        if mask & (1 << bit):
            localized = selected("src/data/localized_font_data.c")
            result["localized"][locale] = {
                style: read_localized_widths(
                    source_root / localized["object"], "gLocalizedFont" + stem + name,
                    layout_object=layout_path,
                )
                for style, name in (("system", "System"), ("talk", "Talk"))
            }
    return result
