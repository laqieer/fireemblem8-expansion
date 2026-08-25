"""Strict source adapter for version-1 custom battle spell effects."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
import hashlib
import json
import os
import re
import struct
import zlib

from scripts.generated_data import idspace as generated_idspace
from scripts.generated_data.items import schema as items_schema


IMPORT_FORMAT = "feditor-magic-v1"
RUNTIME_ABI = 1
COMPRESSION = "lz77"
SPELL_SCHEMA_VERSION = 1
MAX_EFFECTS = 16
MAX_FRAMES = 64
MAX_TOTAL_FRAMES = 255
MAX_SOUND_EVENTS = 8
MAX_OBJ_BYTES = 0x1000
MAX_BG_BYTES = 0x2000
BG_TSA_BYTES = 1200
MAX_OAM_ENTRIES = 16
MAX_ROM_BYTES = 0x40000
MAX_PNG_SOURCE_BYTES = 2 * 1024 * 1024
MAX_SPELL_JSON_BYTES = 64 * 1024
MAX_ANIMATION_TEXT_BYTES = 1024 * 1024
SPELL_ASSOC_ENTRY_BYTES = 0x10
OBJ_WIDTH = 480
OBJ_HEIGHT = 160
BG_WIDTH = 240
BG_HEIGHT = 64
BG_OUTPUT_HEIGHT = 160
OBJ_SEAT_WIDTH = 256
OBJ_SEAT_HEIGHT = 32
CUSTOM_SPELL_BASE = 0x80
PUBLIC_EFFECT_SYMBOL_PATHS = (
    "include/custom_spell_effect.h",
    "include/custom_spell_effect_test.h",
    "src/custom_spell_effect.c",
    "src/custom_spell_effect_test.c",
    "tools/gba-playtest/tests/c/custom_spell_effect_host_driver.c",
    "tools/gba-playtest/tests/c/custom_spell_effect_layout_driver.c",
)

FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
EFFECT_SYMBOL_RE = re.compile(r"^CUSTOM_SPELL_[A-Z0-9_]+$")
ITEM_SYMBOL_RE = re.compile(r"^ITEM_[A-Z0-9_]+$")
FALLBACK_SYMBOL_RE = re.compile(r"^SASSOC_EFX_[A-Za-z0-9_]+$")
SONG_SYMBOL_RE = re.compile(r"^SONG_[A-Z0-9_]+$")
SOUND_ID_RE = re.compile(r"^[1-9A-F][0-9A-F]{0,3}$")
ITXT_LANGUAGE_TAG_RE = re.compile(
    rb"^(?:[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,8})*"
    rb"|[iIxX](?:-[A-Za-z0-9]{1,8})+)$"
)

RECTANGLES = (
    (8, 4, "ATTR0_WIDE", "ATTR1_SIZE_64"),
    (4, 4, "ATTR0_SQUARE", "ATTR1_SIZE_32"),
    (4, 2, "ATTR0_WIDE", "ATTR1_SIZE_32"),
    (2, 4, "ATTR0_TALL", "ATTR1_SIZE_32"),
    (2, 2, "ATTR0_SQUARE", "ATTR1_SIZE_16"),
    (4, 1, "ATTR0_WIDE", "ATTR1_SIZE_16"),
    (1, 4, "ATTR0_TALL", "ATTR1_SIZE_16"),
    (2, 1, "ATTR0_WIDE", "ATTR1_SIZE_8"),
    (1, 2, "ATTR0_TALL", "ATTR1_SIZE_8"),
    (1, 1, "ATTR0_SQUARE", "ATTR1_SIZE_8"),
)


class CustomSpellPackage:
    """Validated package and its deterministic converted frame data."""

    def __init__(self, spell, frames, sound_ids, referenced_sources):
        self.spell = spell
        self.frames = frames
        self.sound_ids = sound_ids
        self.referenced_sources = referenced_sources
        self.bg_bytes = 0
        self.obj_oam_entries = 0
        self.runtime_bytes = 0


def public_effect_symbols(root):
    symbols = set()
    for relative_path in PUBLIC_EFFECT_SYMBOL_PATHS:
        with open(os.path.join(root, relative_path), encoding="utf-8") as handle:
            source = handle.read()
        if relative_path.startswith("include/"):
            symbols.update(re.findall(r"\b(CUSTOM_SPELL_[A-Z0-9_]+)\b", source))
        else:
            symbols.update(
                re.findall(
                    r"^\s*#\s*define\s+(CUSTOM_SPELL_[A-Z0-9_]+)\b",
                    source,
                    re.MULTILINE,
                )
            )
            symbols.update(
                re.findall(
                    r"^\s*(CUSTOM_SPELL_[A-Z0-9_]+)\s*(?:=|,)",
                    source,
                    re.MULTILINE,
                )
            )
    return symbols


def _exact_keys(value, keys, reference):
    if not isinstance(value, dict):
        raise ValueError("{} must be an object".format(reference))
    actual = set(value)
    expected = set(keys)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        raise ValueError("{} is missing {}".format(reference, ", ".join(missing)))
    if extra:
        raise ValueError("{} has unknown field '{}'".format(reference, extra[0]))


def _reject_duplicate_json_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key {!r}".format(key))
        value[key] = item
    return value


def _read_json(path):
    try:
        if os.path.getsize(path) > MAX_SPELL_JSON_BYTES:
            raise ValueError("source exceeds {} bytes".format(MAX_SPELL_JSON_BYTES))
        with open(path, encoding="utf-8") as handle:
            return json.load(handle, object_pairs_hook=_reject_duplicate_json_keys)
    except (OSError, ValueError) as exc:
        raise ValueError("{}: {}".format(path, exc)) from exc


def _read_song_symbols(path):
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    return {
        name: int(value, 0)
        for name, value in re.findall(
            r"\b(SONG_[A-Z0-9_]+)\s*=\s*(0x[0-9A-Fa-f]+|\d+)\s*,",
            source,
        )
    }


def _read_spell(path, songs_path):
    data = _read_json(path)
    _exact_keys(data, ("schemaVersion", "soundTable"), path)
    if type(data["schemaVersion"]) is not int or data["schemaVersion"] != SPELL_SCHEMA_VERSION:
        raise ValueError("{}.schemaVersion must be integer 1".format(path))
    table = data["soundTable"]
    if not isinstance(table, list) or len(table) > MAX_SOUND_EVENTS:
        raise ValueError("{}.soundTable must contain 0..8 entries".format(path))
    songs = _read_song_symbols(songs_path)
    by_id = {}
    seen_symbols = set()
    for index, row in enumerate(table):
        reference = "{}.soundTable[{}]".format(path, index)
        _exact_keys(row, ("id", "song"), reference)
        sound_id = row["id"]
        song = row["song"]
        if not isinstance(sound_id, str) or not SOUND_ID_RE.fullmatch(sound_id):
            raise ValueError(
                "{}.id must be canonical uppercase hexadecimal in 1..FFFF".format(reference)
            )
        numeric = int(sound_id, 16)
        if not isinstance(song, str) or not SONG_SYMBOL_RE.fullmatch(song):
            raise ValueError("{}.song must be a SONG_* symbol".format(reference))
        if song not in songs:
            raise ValueError("{}.song '{}' is not declared".format(reference, song))
        if songs[song] != numeric:
            raise ValueError(
                "{} maps {} to 0x{:X}, not 0x{:X}".format(
                    reference, song, songs[song], numeric
                )
            )
        if numeric in by_id:
            raise ValueError("{} duplicates sound id {}".format(reference, sound_id))
        if song in seen_symbols:
            raise ValueError("{} duplicates song {}".format(reference, song))
        by_id[numeric] = song
        seen_symbols.add(song)
    return {"data": data, "by_id": by_id}


def _png_chunk_stream(path):
    if os.path.getsize(path) > MAX_PNG_SOURCE_BYTES:
        raise ValueError("{} exceeds the PNG source-size limit".format(path))
    with open(path, "rb") as handle:
        data = handle.read()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("{} is not a PNG".format(path))
    chunks = []
    offset = 8
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("{} has a truncated PNG chunk".format(path))
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        name = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("{} has truncated PNG chunk data".format(path))
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", data[offset + 8 + length:end])[0]
        if zlib.crc32(name + payload) & 0xFFFFFFFF != crc:
            raise ValueError("{} has an invalid PNG chunk CRC".format(path))
        if name == b"IEND" and payload:
            raise ValueError("{} has a non-empty IEND payload".format(path))
        chunks.append((name, payload))
        offset = end
        if name == b"IEND":
            break
    if offset != len(data):
        raise ValueError("{} has trailing data after IEND".format(path))
    if not chunks or chunks[-1][0] != b"IEND":
        raise ValueError("{} must end with IEND".format(path))
    return chunks


PNG_ANCILLARY_BEFORE_PLTE = {
    b"cHRM",
    b"gAMA",
    b"iCCP",
    b"sBIT",
    b"sRGB",
}
PNG_ANCILLARY_BEFORE_IDAT = {b"pHYs", b"sPLT"}
PNG_ANCILLARY_AFTER_PLTE = {b"bKGD", b"hIST"}
PNG_ANCILLARY_TEXT_OR_TIME = {b"tEXt", b"zTXt", b"iTXt", b"tIME"}
PNG_SINGLETON_ANCILLARY = (
    PNG_ANCILLARY_BEFORE_PLTE
    | (PNG_ANCILLARY_BEFORE_IDAT - {b"sPLT"})
    | PNG_ANCILLARY_AFTER_PLTE
    | {b"tRNS", b"tIME"}
)
MAX_ANCILLARY_DECOMPRESSED_BYTES = 0x10000


def _validate_bounded_zlib_stream(path, chunk, payload):
    try:
        decoder = zlib.decompressobj()
        output = decoder.decompress(payload, MAX_ANCILLARY_DECOMPRESSED_BYTES + 1)
        if len(output) > MAX_ANCILLARY_DECOMPRESSED_BYTES or decoder.unconsumed_tail:
            raise ValueError(
                "{} {} decompression exceeds {} bytes".format(
                    path, chunk, MAX_ANCILLARY_DECOMPRESSED_BYTES
                )
            )
        output += decoder.flush(MAX_ANCILLARY_DECOMPRESSED_BYTES + 1 - len(output))
    except zlib.error as exc:
        raise ValueError("{} {} has an invalid zlib stream".format(path, chunk)) from exc
    if (
        len(output) > MAX_ANCILLARY_DECOMPRESSED_BYTES
        or not decoder.eof
        or decoder.unused_data
    ):
        raise ValueError("{} {} has an incomplete or trailing zlib stream".format(path, chunk))
    return output


def _validate_utf8_text(path, chunk, field, value):
    try:
        value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("{} {} {} must be valid UTF-8".format(path, chunk, field)) from exc


def _validate_png_keyword(path, chunk, payload):
    terminator = payload.find(b"\0")
    if terminator <= 0 or terminator > 79:
        raise ValueError("{} {} has an invalid keyword terminator".format(path, chunk))
    keyword = payload[:terminator]
    if (
        keyword[:1] == b" "
        or keyword[-1:] == b" "
        or b"  " in keyword
        or any(not (32 <= byte <= 126 or 161 <= byte <= 255) for byte in keyword)
    ):
        raise ValueError("{} {} has an invalid keyword".format(path, chunk))
    return keyword, payload[terminator + 1:]


def _validate_known_ancillary(path, name, payload, palette_count, splt_names):
    chunk = name.decode("ascii")
    if name == b"gAMA":
        if len(payload) != 4 or struct.unpack(">I", payload)[0] == 0:
            raise ValueError("{} gAMA must contain one nonzero u32".format(path))
    elif name == b"cHRM":
        if len(payload) != 32:
            raise ValueError("{} cHRM must contain eight u32 values".format(path))
    elif name == b"sRGB":
        if len(payload) != 1 or payload[0] > 3:
            raise ValueError("{} sRGB rendering intent must be 0..3".format(path))
    elif name == b"iCCP":
        _keyword, remainder = _validate_png_keyword(path, chunk, payload)
        if len(remainder) < 2 or remainder[0] != 0 or not remainder[1:]:
            raise ValueError("{} iCCP must use method 0 with profile data".format(path))
        _validate_bounded_zlib_stream(path, chunk, remainder[1:])
    elif name == b"sBIT":
        if len(payload) != 3 or any(value < 1 or value > 8 for value in payload):
            raise ValueError("{} sBIT values must be three values in 1..8".format(path))
    elif name == b"pHYs":
        if len(payload) != 9 or payload[8] not in (0, 1):
            raise ValueError("{} pHYs must contain two u32 values and unit 0 or 1".format(path))
    elif name == b"sPLT":
        palette_name, remainder = _validate_png_keyword(path, chunk, payload)
        if palette_name in splt_names:
            raise ValueError("{} sPLT suggested-palette name is duplicated".format(path))
        if len(remainder) < 2 or remainder[0] not in (8, 16):
            raise ValueError("{} sPLT sample depth must be 8 or 16".format(path))
        entry_size = 6 if remainder[0] == 8 else 10
        if not remainder[1:] or len(remainder[1:]) % entry_size:
            raise ValueError("{} sPLT entries do not match its sample depth".format(path))
        splt_names.add(palette_name)
    elif name == b"bKGD":
        if len(payload) != 1 or palette_count is None or payload[0] >= palette_count:
            raise ValueError("{} bKGD index must name a PLTE entry".format(path))
    elif name == b"hIST":
        if palette_count is None or len(payload) != palette_count * 2:
            raise ValueError("{} hIST must contain one u16 per PLTE entry".format(path))
    elif name == b"tEXt":
        _validate_png_keyword(path, chunk, payload)
    elif name == b"zTXt":
        _keyword, remainder = _validate_png_keyword(path, chunk, payload)
        if len(remainder) < 2 or remainder[0] != 0 or not remainder[1:]:
            raise ValueError("{} zTXt must use method 0 with text data".format(path))
        _validate_bounded_zlib_stream(path, chunk, remainder[1:])
    elif name == b"iTXt":
        _keyword, remainder = _validate_png_keyword(path, chunk, payload)
        if len(remainder) < 3 or remainder[0] not in (0, 1) or remainder[1] != 0:
            raise ValueError("{} iTXt has invalid compression fields".format(path))
        language_end = remainder.find(b"\0", 2)
        if language_end < 2:
            raise ValueError("{} iTXt is missing a language terminator".format(path))
        language = remainder[2:language_end]
        if language and not ITXT_LANGUAGE_TAG_RE.fullmatch(language):
            raise ValueError("{} iTXt language tag has invalid grammar".format(path))
        translated_end = remainder.find(b"\0", language_end + 1)
        if translated_end < 0:
            raise ValueError("{} iTXt is missing a translated-keyword terminator".format(path))
        _validate_utf8_text(
            path,
            chunk,
            "translated keyword",
            remainder[language_end + 1:translated_end],
        )
        text = remainder[translated_end + 1:]
        if remainder[0] and not text:
            raise ValueError("{} compressed iTXt must contain text data".format(path))
        if remainder[0]:
            text = _validate_bounded_zlib_stream(path, chunk, text)
        _validate_utf8_text(path, chunk, "text", text)
    elif name == b"tIME":
        if (
            len(payload) != 7
            or not 1 <= payload[2] <= 12
            or not 1 <= payload[3] <= 31
            or payload[4] > 23
            or payload[5] > 59
            or payload[6] > 60
        ):
            raise ValueError("{} tIME has an invalid UTC timestamp".format(path))


def _validate_png_chunk_type(path, name):
    if len(name) != 4 or any(
        not (ord("A") <= byte <= ord("Z") or ord("a") <= byte <= ord("z"))
        for byte in name
    ):
        raise ValueError("{} PNG chunk type must contain exactly four ASCII letters".format(path))
    if not ord("A") <= name[2] <= ord("Z"):
        raise ValueError("{} PNG chunk type reserved third letter must be uppercase".format(path))


def read_indexed_png(path, width, height):
    """Read the exact indexed 4bpp PNG subset accepted by the adapter."""
    chunks = _png_chunk_stream(path)
    if not chunks or chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise ValueError("{} has an invalid IHDR".format(path))
    ihdr = chunks[0][1]
    palette_bytes = None
    transparency = None
    idat_parts = []
    idat_finished = False
    seen_ancillary = set()
    splt_names = set()
    for index, (name, payload) in enumerate(chunks):
        _validate_png_chunk_type(path, name)
        if name == b"IHDR":
            if index != 0:
                raise ValueError("{} must contain exactly one IHDR before all chunks".format(path))
        elif name == b"PLTE":
            if palette_bytes is not None or idat_parts:
                raise ValueError("{} must contain exactly one PLTE before IDAT".format(path))
            palette_bytes = payload
        elif name == b"tRNS":
            if transparency is not None:
                raise ValueError("{} must contain at most one tRNS chunk".format(path))
            if palette_bytes is None or idat_parts:
                raise ValueError("{} tRNS must occur after PLTE and before IDAT".format(path))
            transparency = payload
        elif name == b"IDAT":
            if palette_bytes is None:
                raise ValueError("{} PLTE must occur before IDAT".format(path))
            if idat_finished:
                raise ValueError("{} IDAT chunks must be contiguous".format(path))
            idat_parts.append(payload)
        elif name == b"IEND":
            if index != len(chunks) - 1:
                raise ValueError("{} IEND must be the final chunk".format(path))
        else:
            if not name[0] & 0x20:
                raise ValueError("{} has an unsupported critical chunk".format(path))
            if name in PNG_SINGLETON_ANCILLARY:
                if name in seen_ancillary:
                    raise ValueError("{} has more than one {} chunk".format(path, name.decode("ascii")))
                seen_ancillary.add(name)
            if name in PNG_ANCILLARY_BEFORE_PLTE:
                if palette_bytes is not None:
                    raise ValueError(
                        "{} {} must occur before PLTE and IDAT".format(
                            path, name.decode("ascii")
                        )
                    )
            elif name in PNG_ANCILLARY_BEFORE_IDAT:
                if idat_parts:
                    raise ValueError(
                        "{} {} must occur before IDAT".format(path, name.decode("ascii"))
                    )
            elif name in PNG_ANCILLARY_AFTER_PLTE:
                if palette_bytes is None or idat_parts:
                    raise ValueError(
                        "{} {} must occur after PLTE and before IDAT".format(
                            path, name.decode("ascii")
                        )
                    )
            elif name in PNG_ANCILLARY_TEXT_OR_TIME:
                pass
            _validate_known_ancillary(
                path,
                name,
                payload,
                len(palette_bytes) // 3 if palette_bytes is not None else None,
                splt_names,
            )
            if idat_parts:
                idat_finished = True
    if palette_bytes is None:
        raise ValueError("{} must contain exactly one PLTE before IDAT".format(path))
    if not idat_parts:
        raise ValueError("{} must contain IDAT image data".format(path))
    png_width, png_height, depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    if (png_width, png_height) != (width, height):
        raise ValueError(
            "{} must be exactly {}x{} pixels".format(path, width, height)
        )
    if (depth, color_type, compression, filtering, interlace) != (4, 3, 0, 0, 0):
        raise ValueError(
            "{} must be non-interlaced indexed 4bpp PNG".format(path)
        )
    if not palette_bytes or len(palette_bytes) % 3 or len(palette_bytes) // 3 > 16:
        raise ValueError("{} palette must contain 1..16 RGB entries".format(path))
    palette = [
        tuple(palette_bytes[index:index + 3])
        for index in range(0, len(palette_bytes), 3)
    ]
    alpha = list(transparency or b"")
    if not alpha or len(alpha) > len(palette):
        raise ValueError("{} tRNS exceeds PLTE".format(path))
    alpha.extend([255] * (len(palette) - len(alpha)))
    if alpha[0] != 0:
        raise ValueError(
            "{} must make only palette index 0 transparent".format(path)
        )
    row_bytes = width // 2
    expected = height * (row_bytes + 1)
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(b"".join(idat_parts), expected + 1)
        if len(raw) > expected or decompressor.unconsumed_tail:
            raise ValueError("{} PNG scanlines exceed expected size".format(path))
        raw += decompressor.flush(expected + 1 - len(raw))
    except zlib.error as exc:
        raise ValueError("{} has invalid PNG image data".format(path)) from exc
    if (
        len(raw) != expected
        or not decompressor.eof
        or decompressor.unused_data
    ):
        raise ValueError("{} has invalid PNG scanline length".format(path))
    pixels = bytearray()
    previous = bytearray(row_bytes)
    offset = 0
    for _ in range(height):
        filter_type = raw[offset]
        offset += 1
        if filter_type > 4:
            raise ValueError("{} has unsupported PNG filter".format(path))
        row = bytearray(raw[offset:offset + row_bytes])
        offset += row_bytes
        for index, value in enumerate(row):
            left = row[index - 1] if index else 0
            up = previous[index]
            upper_left = previous[index - 1] if index else 0
            if filter_type == 1:
                row[index] = (value + left) & 0xFF
            elif filter_type == 2:
                row[index] = (value + up) & 0xFF
            elif filter_type == 3:
                row[index] = (value + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                prediction = left + up - upper_left
                distances = (
                    abs(prediction - left),
                    abs(prediction - up),
                    abs(prediction - upper_left),
                )
                predictor = (left, up, upper_left)[distances.index(min(distances))]
                row[index] = (value + predictor) & 0xFF
        previous = row
        for value in row:
            pixels.extend((value >> 4, value & 0x0F))
    if any(value >= len(palette) for value in pixels):
        raise ValueError("{} uses a palette index outside PLTE".format(path))
    if any(alpha[value] != 255 for value in set(pixels) if value != 0):
        raise ValueError(
            "{} must make used nonzero palette indices opaque".format(path)
        )
    return {"pixels": bytes(pixels), "palette": palette}


def _line_error(path, line, message):
    return ValueError("{}:{} {}".format(path, line, message))


def _parse_image_line(path, line_number, line, kind):
    match = re.fullmatch(r"{}\s+p-\s+(\S+)".format(kind), line)
    if match is None:
        raise _line_error(path, line_number, "has malformed {} frame line".format(kind))
    filename = match.group(1)
    if not FILENAME_RE.fullmatch(filename):
        raise _line_error(path, line_number, "has unsafe image filename '{}'".format(filename))
    return filename


def parse_animation(path, sound_table):
    if os.path.getsize(path) > MAX_ANIMATION_TEXT_BYTES:
        raise ValueError("{} exceeds the animation source-size limit".format(path))
    with open(path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    if "\r" in text:
        raise ValueError("{} must use LF line endings".format(path))
    frames = []
    current = None
    pending_sounds = []
    sound_ids = []
    used_sounds = set()
    referenced = []
    terminated = False
    for line_number, source in enumerate(text.splitlines(), 1):
        line = source.strip()
        if not line:
            continue
        if terminated:
            raise _line_error(path, line_number, "has content after final terminator")
        if line.startswith("#") or line.startswith("@") or line.startswith("///"):
            continue
        if line == "~~~":
            if current is not None:
                raise _line_error(path, line_number, "terminates an incomplete O/B/wait frame")
            if pending_sounds:
                raise _line_error(path, line_number, "has sound event after the last frame")
            terminated = True
            continue
        if line.startswith("S"):
            if current is not None:
                raise _line_error(path, line_number, "places sound inside an O/B/wait frame")
            if not frames:
                raise _line_error(path, line_number, "places sound before frame 0")
            token = line[1:]
            if not re.fullmatch(r"[0-9A-Fa-f]{1,4}", token):
                raise _line_error(path, line_number, "has malformed sound token")
            sound_id = int(token, 16)
            if sound_id not in sound_table:
                raise _line_error(
                    path, line_number, "uses undeclared sound S{:X}".format(sound_id)
                )
            if len(sound_ids) + len(pending_sounds) >= MAX_SOUND_EVENTS:
                raise _line_error(path, line_number, "exceeds 8 sound events")
            pending_sounds.append(sound_id)
            used_sounds.add(sound_id)
            continue
        if line.startswith("O"):
            if current is not None:
                raise _line_error(path, line_number, "starts O before completing prior frame")
            filename = _parse_image_line(path, line_number, line, "O")
            current = {
                "obj": filename,
                "obj_line": line_number,
                "sounds": tuple(pending_sounds),
            }
            sound_ids.extend(pending_sounds)
            pending_sounds = []
            if "images/" + filename not in referenced:
                referenced.append("images/" + filename)
            continue
        if line.startswith("B"):
            if current is None or "bg" in current:
                raise _line_error(path, line_number, "has B outside ordered O/B/wait frame")
            filename = _parse_image_line(path, line_number, line, "B")
            current["bg"] = filename
            current["bg_line"] = line_number
            if "images/" + filename not in referenced:
                referenced.append("images/" + filename)
            continue
        if re.fullmatch(r"[0-9]+", line):
            if current is None or "bg" not in current:
                raise _line_error(path, line_number, "has wait outside ordered O/B/wait frame")
            duration = int(line)
            if not 1 <= duration <= 255:
                raise _line_error(path, line_number, "wait must be within 1..255")
            current["duration"] = duration
            current["wait_line"] = line_number
            frames.append(current)
            current = None
            if len(frames) > MAX_FRAMES:
                raise _line_error(path, line_number, "exceeds 64 frames")
            if sum(frame["duration"] for frame in frames) > MAX_TOTAL_FRAMES:
                raise _line_error(path, line_number, "exceeds 255 total frames")
            continue
        raise _line_error(path, line_number, "has unsupported token '{}'".format(line))
    if not terminated:
        raise ValueError("{} is missing final terminator ~~~".format(path))
    if not frames:
        raise ValueError("{} must contain 1..64 frames".format(path))
    unused = sorted(set(sound_table) - used_sounds)
    if unused:
        raise ValueError(
            "{} declares unused sound(s): {}".format(
                path, ", ".join("S{:X}".format(value) for value in unused)
            )
        )
    return frames, sound_ids, referenced


def _tile_bytes(pixels, width, tile_x, tile_y, tile_width=1, tile_height=1):
    output = bytearray()
    for ty in range(tile_height):
        for tx in range(tile_width):
            base_x = (tile_x + tx) * 8
            base_y = (tile_y + ty) * 8
            for py in range(8):
                row = (base_y + py) * width + base_x
                for px in range(0, 8, 2):
                    output.append(pixels[row + px] | (pixels[row + px + 1] << 4))
    return bytes(output)


def _block_pixels(pixels, width, tile_x, tile_y, tile_width, tile_height):
    block_width = tile_width * 8
    output = bytearray()
    for y in range(tile_height * 8):
        row = (tile_y * 8 + y) * width + tile_x * 8
        output.extend(pixels[row:row + block_width])
    return bytes(output)


def _seat_block(seat, tile_x, tile_y, tile_width, tile_height):
    output = bytearray()
    for y in range(tile_height * 8):
        row = (tile_y * 8 + y) * OBJ_SEAT_WIDTH + tile_x * 8
        output.extend(seat[row:row + tile_width * 8])
    return bytes(output)


def _mark(flags, width, x, y, block_width, block_height):
    for dy in range(block_height):
        for dx in range(block_width):
            flags[(y + dy) * width + x + dx] = True


def _copy_to_seat(seat, block, seat_x, seat_y, block_width, block_height):
    pixel_width = block_width * 8
    for y in range(block_height * 8):
        destination = (seat_y * 8 + y) * OBJ_SEAT_WIDTH + seat_x * 8
        source = y * pixel_width
        seat[destination:destination + pixel_width] = block[source:source + pixel_width]


def _pack_plane(pixels, seat, seat_used):
    tile_width = 31
    tile_height = 20
    processed = []
    for tile_y in range(tile_height):
        for tile_x in range(tile_width):
            processed.append(
                not any(_tile_bytes(pixels, 248, tile_x, tile_y))
            )
    entries = []
    for index in range(len(processed)):
        if processed[index]:
            continue
        source_x = index % tile_width
        source_y = index // tile_width
        placed = False
        for block_width, block_height, shape, size in RECTANGLES:
            if source_x + block_width > tile_width or source_y + block_height > tile_height:
                continue
            if any(
                processed[(source_y + dy) * tile_width + source_x + dx]
                for dy in range(block_height)
                for dx in range(block_width)
            ):
                continue
            block = _block_pixels(
                pixels, 248, source_x, source_y, block_width, block_height
            )
            seat_x = seat_y = None
            for candidate_y in range(4 - block_height + 1):
                for candidate_x in range(32 - block_width + 1):
                    if not all(
                        seat_used[(candidate_y + dy) * 32 + candidate_x + dx]
                        for dy in range(block_height)
                        for dx in range(block_width)
                    ):
                        continue
                    if _seat_block(
                        seat,
                        candidate_x,
                        candidate_y,
                        block_width,
                        block_height,
                    ) == block:
                        seat_x, seat_y = candidate_x, candidate_y
                        break
                if seat_x is not None:
                    break
            if seat_x is None:
                for candidate_y in range(4 - block_height + 1):
                    for candidate_x in range(32 - block_width + 1):
                        if any(
                            seat_used[(candidate_y + dy) * 32 + candidate_x + dx]
                            for dy in range(block_height)
                            for dx in range(block_width)
                        ):
                            continue
                        seat_x, seat_y = candidate_x, candidate_y
                        _copy_to_seat(
                            seat,
                            block,
                            seat_x,
                            seat_y,
                            block_width,
                            block_height,
                        )
                        _mark(
                            seat_used,
                            32,
                            seat_x,
                            seat_y,
                            block_width,
                            block_height,
                        )
                        break
                    if seat_x is not None:
                        break
            if seat_x is None:
                continue
            _mark(
                processed,
                tile_width,
                source_x,
                source_y,
                block_width,
                block_height,
            )
            entries.append(
                {
                    "source_x": source_x,
                    "source_y": source_y,
                    "seat_x": seat_x,
                    "seat_y": seat_y,
                    "width": block_width,
                    "height": block_height,
                    "shape": shape,
                    "size": size,
                }
            )
            placed = True
            break
        if not placed:
            raise ValueError("OBJ frame exceeds the 0x1000-byte magic tile seat")
    return entries


def _pack_obj(png):
    source = png["pixels"]
    planes = []
    for start_x in (0, 240):
        plane = bytearray(248 * OBJ_HEIGHT)
        for y in range(OBJ_HEIGHT):
            source_row = y * OBJ_WIDTH + start_x
            destination = y * 248
            plane[destination:destination + 240] = source[source_row:source_row + 240]
        planes.append(bytes(plane))
    seat = bytearray(OBJ_SEAT_WIDTH * OBJ_SEAT_HEIGHT)
    seat_used = [False] * (32 * 4)
    front = _pack_plane(planes[0], seat, seat_used)
    back = _pack_plane(planes[1], seat, seat_used)
    entries = front + back
    if not entries:
        raise ValueError("OBJ frame must contain at least one visible OAM entry")
    if len(entries) > MAX_OAM_ENTRIES:
        raise ValueError(
            "OBJ frame requires {} OAM entries, exceeding 16".format(len(entries))
        )
    output = bytearray()
    for tile_y in range(4):
        for tile_x in range(32):
            output.extend(_tile_bytes(seat, OBJ_SEAT_WIDTH, tile_x, tile_y))
    if len(output) != MAX_OBJ_BYTES:
        raise AssertionError("OBJ seat did not encode to 0x1000 bytes")
    return bytes(output), entries


def _scale_bg(png):
    source = png["pixels"]
    output = bytearray(BG_WIDTH * BG_OUTPUT_HEIGHT)
    for y in range(BG_OUTPUT_HEIGHT):
        source_y = (2 * y * BG_HEIGHT + BG_OUTPUT_HEIGHT) // (2 * BG_OUTPUT_HEIGHT)
        if source_y >= BG_HEIGHT:
            continue
        output[y * BG_WIDTH:(y + 1) * BG_WIDTH] = source[
            source_y * BG_WIDTH:(source_y + 1) * BG_WIDTH
        ]
    return bytes(output)


def _pack_bg(png):
    pixels = _scale_bg(png)
    blank = bytes(32)
    tiles = [blank]
    indexes = {blank: 0}
    tsa = []
    for tile_y in range(20):
        for tile_x in range(30):
            tile = _tile_bytes(pixels, BG_WIDTH, tile_x, tile_y)
            index = indexes.get(tile)
            if index is None:
                index = len(tiles)
                if index >= 256:
                    raise ValueError("BG frame exceeds 256 unique tiles")
                indexes[tile] = index
                tiles.append(tile)
            tsa.append(index)
    return b"".join(tiles), b"".join(struct.pack("<H", value) for value in tsa)


def _palette_bytes(png):
    output = bytearray()
    for index in range(16):
        if index < len(png["palette"]):
            red, green, blue = png["palette"][index]
            value = (red >> 3) | ((green >> 3) << 5) | ((blue >> 3) << 10)
        else:
            value = 0
        output.extend(struct.pack("<H", value))
    return bytes(output)


def _lz77_prefix_index(data):
    indexes = [None] * 19
    for length in range(3, 19):
        index = {}
        for start in range(len(data) - length + 1):
            index.setdefault(data[start:start + length], []).append(start)
        indexes[length] = index
    return indexes


def gba_lz77(data):
    """Encode deterministic GBA LZ77 with exact longest/earliest tie behavior."""
    data = bytes(data)
    indexes = _lz77_prefix_index(data)
    output = bytearray(b"\x10")
    output.extend(len(data).to_bytes(3, "little"))
    position = 0
    while position < len(data):
        flag_offset = len(output)
        output.append(0)
        flags = 0
        for bit in range(8):
            if position >= len(data):
                break
            best_length = 0
            best_distance = 0
            start = max(0, position - 0x1000)
            max_length = min(18, len(data) - position)
            for length in range(max_length, 2, -1):
                candidates = indexes[length].get(data[position:position + length])
                if candidates is None:
                    continue
                first = bisect_left(candidates, start)
                last = bisect_right(candidates, position - length)
                if first < last:
                    best_length = length
                    best_distance = position - candidates[first]
                    break
            if best_length >= 3:
                flags |= 0x80 >> bit
                output.append(((best_length - 3) << 4) | ((best_distance - 1) >> 8))
                output.append((best_distance - 1) & 0xFF)
                position += best_length
            else:
                output.append(data[position])
                position += 1
        output[flag_offset] = flags
    return bytes(output)


def load_package(
    root,
    spell_source,
    animation_source,
    declared_sources,
    songs_source,
    effect_symbol,
):
    package_dir = os.path.dirname(spell_source)
    if (
        os.path.dirname(animation_source) != package_dir
        or os.path.basename(spell_source) != "spell.json"
        or os.path.basename(animation_source) != "animation.txt"
    ):
        raise ValueError("custom spell package must begin with colocated spell.json and animation.txt")
    absolute_dir = os.path.join(root, package_dir)
    root_real = os.path.realpath(root)
    if (
        os.path.islink(absolute_dir)
        or not os.path.isdir(absolute_dir)
        or os.path.commonpath((root_real, os.path.realpath(absolute_dir))) != root_real
    ):
        raise ValueError("custom spell package directory must be a real repository directory")
    package_real = os.path.realpath(absolute_dir)
    entries = sorted(os.listdir(absolute_dir))
    if entries != ["animation.txt", "images", "spell.json"]:
        raise ValueError(
            "custom spell package must contain only spell.json, animation.txt, and images/"
        )

    def direct_child(name, kind):
        path = os.path.join(absolute_dir, name)
        if (
            os.path.islink(path)
            or not os.path.isfile(path)
            or os.path.dirname(os.path.realpath(path)) != package_real
        ):
            raise ValueError(
                "custom spell package {} must be a real direct package child".format(
                    kind
                )
            )
        return path

    spell_path = direct_child("spell.json", "spell.json")
    animation_path = direct_child("animation.txt", "animation.txt")
    images_dir = os.path.join(absolute_dir, "images")
    if (
        not os.path.isdir(images_dir)
        or os.path.islink(images_dir)
        or os.path.dirname(os.path.realpath(images_dir)) != package_real
    ):
        raise ValueError("custom spell package images must be a real directory")
    spell = _read_spell(spell_path, os.path.join(root, songs_source))
    frames, sound_ids, referenced = parse_animation(animation_path, spell["by_id"])
    expected_sources = [spell_source, animation_source] + [
        os.path.join(package_dir, value).replace(os.sep, "/") for value in referenced
    ]
    if list(declared_sources) != expected_sources:
        raise ValueError(
            "custom spell sources must list spell.json, animation.txt, then images in first-reference order"
        )
    expected_images = sorted(os.path.basename(value) for value in referenced)
    actual_images = sorted(os.listdir(images_dir))
    if actual_images != expected_images or any(
        not os.path.isfile(os.path.join(images_dir, name))
        or os.path.islink(os.path.join(images_dir, name))
        or os.path.dirname(os.path.realpath(os.path.join(images_dir, name)))
        != os.path.realpath(images_dir)
        for name in actual_images
    ):
        raise ValueError("custom spell images/ must contain exactly the referenced PNG files")
    png_cache = {}
    for frame in frames:
        for role, width, height in (
            ("obj", OBJ_WIDTH, OBJ_HEIGHT),
            ("bg", BG_WIDTH, BG_HEIGHT),
        ):
            filename = frame[role]
            key = (role, filename)
            if key not in png_cache:
                png_cache[key] = read_indexed_png(
                    os.path.join(images_dir, filename), width, height
                )
            frame[role + "_png"] = png_cache[key]
    package = CustomSpellPackage(spell, frames, sound_ids, expected_sources)
    _convert_package(package)
    package.runtime_bytes = runtime_bytes(package, effect_symbol)
    return package


def _convert_package(package):
    max_bg_tiles = 1
    max_oam = 0
    for frame in package.frames:
        frame["obj_tiles"], frame["oam"] = _pack_obj(frame["obj_png"])
        frame["bg_tiles"], frame["tsa"] = _pack_bg(frame["bg_png"])
        frame["obj_palette"] = _palette_bytes(frame["obj_png"])
        frame["bg_palette"] = _palette_bytes(frame["bg_png"])
        max_bg_tiles = max(max_bg_tiles, len(frame["bg_tiles"]) // 32)
        max_oam = max(max_oam, len(frame["oam"]))
    package.bg_bytes = max_bg_tiles * 32
    package.obj_oam_entries = max_oam
    for frame in package.frames:
        frame["bg_tiles"] += b"\0" * (package.bg_bytes - len(frame["bg_tiles"]))
        frame["obj_lz"] = gba_lz77(frame["obj_tiles"])
        frame["bg_lz"] = gba_lz77(frame["bg_tiles"])
        frame["tsa_lz"] = gba_lz77(frame["tsa"])


def _align4(value):
    return (value + 3) & ~3


def runtime_bytes(package, effect_symbol):
    binary_bytes = sum(
        _align4(len(payload))
        for frame in package.frames
        for payload in (
            frame["obj_lz"],
            frame["bg_lz"],
            frame["tsa_lz"],
            frame["obj_palette"],
            frame["bg_palette"],
        )
    )
    oam_bytes = sum(
        (len(frame["oam"]) + 1) * 12 * 2
        for frame in package.frames
    )
    script_words = 2 * (
        sum((frame["duration"] + 62) // 63 for frame in package.frames) + 1
    )
    metadata_bytes = (
        len(package.frames) * 24
        + _align4(len(package.sound_ids) * 2)
        + len(package.frames) * 8
        + 0x34
        + script_words * 4
        + len(effect_symbol.encode("ascii")) + 1
        + SPELL_ASSOC_ENTRY_BYTES
    )
    return _align4(binary_bytes + oam_bytes + metadata_bytes)


def validate_declared_resources(package, resources):
    exact = {
        "frames": len(package.frames),
        "totalFrames": sum(frame["duration"] for frame in package.frames),
        "objBytes": MAX_OBJ_BYTES,
        "bgBytes": package.bg_bytes,
        "bgTsaBytes": BG_TSA_BYTES,
        "objOamEntries": package.obj_oam_entries,
        "objPalettes": 1,
        "bgPalettes": 1,
        "soundEvents": len(package.sound_ids),
    }
    for key, expected in exact.items():
        if type(resources[key]) is not int or resources[key] != expected:
            raise ValueError(
                "resources.{} must equal generated value {}".format(key, expected)
            )
    hit_frame = resources["hitFrame"]
    if type(hit_frame) is not int or not 0 <= hit_frame < exact["totalFrames"]:
        raise ValueError("resources.hitFrame must be within 0..totalFrames-1")
    rom_bytes = resources["romBytes"]
    if (
        type(rom_bytes) is not int
        or not 1 <= rom_bytes <= MAX_ROM_BYTES
        or package.runtime_bytes > rom_bytes
    ):
        raise ValueError(
            "resources.romBytes must cover generated {} bytes within 0x40000".format(
                package.runtime_bytes
            )
        )


def _c_stem(record_id):
    return "sCustomSpell_{}".format(record_id)


def _asset_paths(record_id, frame_index, out_dir):
    directory = os.path.join(out_dir, "custom_spell", record_id.lower())
    stem = "frame_{:02d}".format(frame_index)
    return {
        "obj": os.path.join(directory, stem + "_obj.4bpp.lz"),
        "bg": os.path.join(directory, stem + "_bg.4bpp.lz"),
        "tsa": os.path.join(directory, stem + "_bg.tsa.lz"),
        "obj_palette": os.path.join(directory, stem + "_obj.gbapal"),
        "bg_palette": os.path.join(directory, stem + "_bg.gbapal"),
    }


def _root_relative(path):
    marker = os.path.join("build", "generated", "assets")
    normalized = path.replace("\\", "/")
    index = normalized.find(marker)
    return normalized[index:] if index >= 0 else normalized


def _oam_array(lines, name, entries, mirrored):
    lines.append("static const struct AnimSpriteData {}[] =\n{{\n".format(name))
    for entry in entries:
        x = entry["source_x"] * 8 - 0xAC
        if mirrored:
            x = -(entry["width"] * 8) - x
        attr1 = entry["size"]
        if mirrored:
            attr1 = "({} + ATTR1_FLIP_X)".format(attr1)
        lines.append(
            "    {{ .header = (u32)({shape}) | ((u32)({size}) << 16), "
            ".as = {{ .object = {{ {tile}, {x}, {y} }} }} }},\n".format(
                shape=entry["shape"],
                size=attr1,
                tile=entry["seat_y"] * 32 + entry["seat_x"],
                x=x,
                y=entry["source_y"] * 8 - 0x58,
            )
        )
    lines.append("    ANIM_SPRITE_END,\n};\n\n")


def _script(lines, name, frame_names, frames):
    lines.append("static const u32 {}[] =\n{{\n".format(name))
    for frame_name, frame in zip(frame_names, frames):
        remaining = frame["duration"]
        while remaining:
            duration = min(remaining, 63)
            lines.append(
                "    ANIMSCR_FORCE_SPRITE({}, {}),\n".format(frame_name, duration)
            )
            remaining -= duration
    lines.append("    ANIMSCR_BLOCKED,\n};\n\n")


def _byte_array(lines, name, data):
    lines.append(
        "static const u8 __attribute__((aligned(4))) {}[] =\n{{\n".format(name)
    )
    for offset in range(0, len(data), 16):
        lines.append(
            "    {},\n".format(
                ", ".join(
                    "0x{:02X}".format(value)
                    for value in data[offset:offset + 16]
                )
            )
        )
    lines.append("};\n")


def _render_data_include(records, out_dir):
    lines = ["/* AUTO-GENERATED by scripts.assets; do not edit. */\n\n"]
    effects = sorted(records, key=lambda record: record.id)
    for effect_index, record in enumerate(effects):
        package = record.custom_spell_package
        stem = _c_stem(record.id)
        right_frames = []
        left_frames = []
        for frame_index, frame in enumerate(package.frames):
            for data, suffix in (
                (frame["obj_lz"], "ObjGfx"),
                (frame["bg_lz"], "BgGfx"),
                (frame["tsa_lz"], "BgTsa"),
                (frame["obj_palette"], "ObjPalette"),
                (frame["bg_palette"], "BgPalette"),
            ):
                _byte_array(
                    lines,
                    "{}Frame{}{}".format(stem, frame_index, suffix),
                    data,
                )
            lines.append("\n")
            left_name = "{}Frame{}LeftOam".format(stem, frame_index)
            right_name = "{}Frame{}RightOam".format(stem, frame_index)
            _oam_array(lines, left_name, frame["oam"], False)
            _oam_array(lines, right_name, frame["oam"], True)
            left_frames.append(left_name)
            right_frames.append(right_name)
        left_script = stem + "LeftScript"
        right_script = stem + "RightScript"
        _script(lines, left_script, left_frames, package.frames)
        _script(lines, right_script, right_frames, package.frames)
        lines.append(
            "static const struct CustomSpellEffectFrameAssets {}FrameAssets[] =\n{{\n".format(
                stem
            )
        )
        for frame_index, _frame in enumerate(package.frames):
            lines.append(
                "    {{ (const u16 *){s}Frame{i}ObjGfx, "
                "(const u16 *){s}Frame{i}BgGfx, "
                "(const u16 *){s}Frame{i}BgTsa, "
                "(const u16 *){s}Frame{i}BgTsa, "
                "(const u16 *){s}Frame{i}ObjPalette, "
                "(const u16 *){s}Frame{i}BgPalette }},\n".format(
                    s=stem, i=frame_index
                )
            )
        lines.append("};\n\n")
        if package.sound_ids:
            lines.append(
                "static const u16 {}SoundIds[] = {{ {} }};\n\n".format(
                    stem,
                    ", ".join(
                        package.spell["by_id"][sound_id]
                        for sound_id in package.sound_ids
                    ),
                )
            )
        lines.append(
            "static const struct CustomSpellEffectFrame {}Frames[] =\n{{\n".format(
                stem
            )
        )
        sound_start = 0
        for frame_index, frame in enumerate(package.frames):
            count = len(frame["sounds"])
            lines.append(
                "    {{ {duration}, 0, {start}, {count}, &{stem}FrameAssets[{index}] }},\n".format(
                    duration=frame["duration"],
                    start=sound_start,
                    count=count,
                    stem=stem,
                    index=frame_index,
                )
            )
            sound_start += count
        lines.append("};\n\n")
        record.custom_spell_render = {
            "stem": stem,
            "left_script": left_script,
            "right_script": right_script,
            "effect_index": effect_index,
        }
    lines.append(
        "const struct CustomSpellEffect gGeneratedCustomSpellEffects[] =\n{\n"
    )
    for effect_index, record in enumerate(effects):
        package = record.custom_spell_package
        render = record.custom_spell_render
        sound_pointer = render["stem"] + "SoundIds" if package.sound_ids else "NULL"
        lines.extend(
            (
                "    {\n",
                '        "{}",\n'.format(record.ownership["effectSymbol"]),
                "        {}Frames,\n".format(render["stem"]),
                "        {{ {}, {}, {}, {}, {}, {}, {}, {{ 0, 0 }}, {} }},\n".format(
                    MAX_OBJ_BYTES,
                    package.bg_bytes,
                    BG_TSA_BYTES,
                    2,
                    1,
                    package.obj_oam_entries,
                    len(package.sound_ids),
                    package.runtime_bytes,
                ),
                "        {{ {}, {}, {}, {} }},\n".format(
                    render["right_script"],
                    render["left_script"],
                    render["right_script"],
                    render["left_script"],
                ),
                "        {},\n".format(sound_pointer),
                "        {}, {}, {}, {}, {}, {{ 0, 0, 0 }},\n".format(
                    CUSTOM_SPELL_BASE + effect_index,
                    record.custom_spell_fallback_id,
                    len(package.frames),
                    sum(frame["duration"] for frame in package.frames),
                    record.resources["hitFrame"],
                ),
                "    },\n",
            )
        )
    lines.append("};\n")
    lines.append(
        "#define CUSTOM_SPELL_EFFECT_GENERATED_COUNT {}\n".format(len(effects))
    )
    return "".join(lines)


def _render_generated_header(records):
    effects = sorted(records, key=lambda record: record.id)
    lines = [
        "/* AUTO-GENERATED by scripts.assets; do not edit. */\n"
        "#ifndef GUARD_GENERATED_CUSTOM_SPELL_EFFECTS_H\n"
        "#define GUARD_GENERATED_CUSTOM_SPELL_EFFECTS_H\n\n"
        "#define CUSTOM_SPELL_EFFECT_GENERATED_COUNT {}\n".format(len(effects))
    ]
    lines.extend(
        "#define {} (CUSTOM_SPELL_EFFECT_BASE + {})\n".format(
            record.ownership["effectSymbol"], index
        )
        for index, record in enumerate(effects)
    )
    lines.append(
        "\n"
        "extern const struct CustomSpellEffect "
        "gGeneratedCustomSpellEffects[{}];\n\n".format(len(effects))
    )
    lines.append("#endif\n")
    return "".join(lines)


def _render_runtime_test_header(records):
    if not records:
        return "/* AUTO-GENERATED by scripts.assets; no custom spell test binding. */\n"
    first = sorted(records, key=lambda record: record.id)[0]
    return (
        "/* AUTO-GENERATED by scripts.assets; do not edit. */\n"
        "#define CUSTOM_SPELL_EFFECT_TEST_ITEM {item}\n"
        "#define CUSTOM_SPELL_EFFECT_TEST_ANIMATION_ID {animation}\n"
    ).format(
        item=first.ownership["item"],
        animation=CUSTOM_SPELL_BASE,
    )


def _render_spellassoc(records):
    lines = ["/* AUTO-GENERATED by scripts.assets; do not edit. */\n"]
    for index, record in enumerate(sorted(records, key=lambda item: item.id)):
        lines.append(
            "    SPELL_ASSOC_DATA_WPN_MAGIC({}, {}, SPELL_ASSOC_MCOLOR_NORMAL),\n".format(
                record.ownership["item"], CUSTOM_SPELL_BASE + index
            )
        )
    return "".join(lines)


def canonical_contract(records):
    effects = []
    resource_effects = []
    custom_records = [
        record for record in records
        if getattr(record, "custom_spell_package", None) is not None
    ]
    for index, record in enumerate(sorted(custom_records, key=lambda item: item.id)):
        package = record.custom_spell_package
        frame_inventory = []
        for frame in package.frames:
            frame_inventory.append(
                {
                    "bg_gfx_sha256": hashlib.sha256(frame["bg_lz"]).hexdigest(),
                    "bg_palette_sha256": hashlib.sha256(frame["bg_palette"]).hexdigest(),
                    "bg_tsa_sha256": hashlib.sha256(frame["tsa_lz"]).hexdigest(),
                    "duration": frame["duration"],
                    "obj_gfx_sha256": hashlib.sha256(frame["obj_lz"]).hexdigest(),
                    "obj_palette_sha256": hashlib.sha256(frame["obj_palette"]).hexdigest(),
                    "oam_sha256": hashlib.sha256(
                        json.dumps(
                            frame["oam"], sort_keys=True, separators=(",", ":")
                        ).encode("ascii")
                    ).hexdigest(),
                    "sound_count": len(frame["sounds"]),
                }
            )
        effects.append(
            {
                "animation_id": CUSTOM_SPELL_BASE + index,
                "effect_symbol": record.ownership["effectSymbol"],
                "fallback_animation_id": record.custom_spell_fallback_id,
                "frames": frame_inventory,
                "hit_frame": record.resources["hitFrame"],
                "id": record.id,
                "item": record.ownership["item"],
                "provenance": getattr(record, "provenance", {}),
                "sound_ids": list(package.sound_ids),
                "sources": list(getattr(record, "sources", ())),
                "total_frames": sum(frame["duration"] for frame in package.frames),
            }
        )
        resource_effects.append(
            {
                "bg_bytes": package.bg_bytes,
                "bg_tsa_bytes": BG_TSA_BYTES,
                "id": record.id,
                "obj_bytes": MAX_OBJ_BYTES,
                "obj_oam_entries": package.obj_oam_entries,
                "rom_bytes": package.runtime_bytes,
                "sound_events": len(package.sound_ids),
            }
        )
    inventory = {"runtime_abi": RUNTIME_ABI, "effects": effects}
    resources = {
        "aggregate_rom_bytes": sum(item["rom_bytes"] for item in resource_effects),
        "effects": resource_effects,
        "max_effects": MAX_EFFECTS,
    }
    canonical_inventory = json.dumps(
        inventory, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    canonical_resources = json.dumps(
        resources, sort_keys=True, separators=(",", ":")
    ).encode("ascii")
    return {
        "inventory": inventory,
        "resources": resources,
        "inventory_digest": hashlib.sha256(canonical_inventory).hexdigest(),
        "resource_digest": hashlib.sha256(canonical_resources).hexdigest(),
    }


def expected_outputs(records, out_dir):
    effects = [
        record for record in records
        if getattr(record, "custom_spell_package", None) is not None
    ]
    if not effects:
        return {}
    outputs = {}
    for record in effects:
        for frame_index, frame in enumerate(record.custom_spell_package.frames):
            paths = _asset_paths(record.id, frame_index, out_dir)
            outputs.update(
                {
                    paths["obj"]: frame["obj_lz"],
                    paths["bg"]: frame["bg_lz"],
                    paths["tsa"]: frame["tsa_lz"],
                    paths["obj_palette"]: frame["obj_palette"],
                    paths["bg_palette"]: frame["bg_palette"],
                }
            )
    contract = canonical_contract(effects)
    outputs[os.path.join(out_dir, "custom_spell", "custom_spell_effect_data.inc")] = (
        _render_data_include(effects, out_dir).encode("utf-8")
    )
    outputs[os.path.join(out_dir, "custom_spell", "custom_spell_effect_generated.h")] = (
        _render_generated_header(effects).encode("utf-8")
    )
    outputs[os.path.join(out_dir, "custom_spell", "custom_spell_effect_runtime_test.h")] = (
        _render_runtime_test_header(effects).encode("utf-8")
    )
    outputs[os.path.join(out_dir, "custom_spell", "custom_spell_effect_spellassoc.inc")] = (
        _render_spellassoc(effects).encode("utf-8")
    )
    outputs[os.path.join(out_dir, "custom_spell", "custom_spell_effect_inventory.json")] = (
        json.dumps(contract, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    )
    return outputs


def output_paths(records, out_dir):
    return tuple(sorted(expected_outputs(records, out_dir)))


def _spell_fallbacks(path):
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    match = re.search(
        r"enum\s+spellassoc_efxmagic_idx\s*\{(?P<body>.*?)\};",
        source,
        re.DOTALL,
    )
    if match is None:
        raise ValueError("cannot find spellassoc_efxmagic_idx")
    values = {}
    current = -1
    for raw in match.group("body").split(","):
        token = re.sub(r"//.*", "", raw).strip()
        if not token:
            continue
        if "=" in token:
            name, value = (part.strip() for part in token.split("=", 1))
            if not re.fullmatch(r"(?:0x[0-9A-Fa-f]+|\d+)", value):
                raise ValueError("unsupported spell fallback expression '{}'".format(value))
            current = int(value, 0)
        else:
            name = token
            current += 1
        values[name] = current
    return values


def _spell_lut(path):
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    match = re.search(
        r"gEkrSpellAnimLut\s*\[\s*\]\s*=\s*\{(?P<body>.*?)\};",
        source,
        re.DOTALL,
    )
    if match is None:
        raise ValueError("cannot find gEkrSpellAnimLut")
    return [
        value.strip()
        for value in match.group("body").split(",")
        if value.strip()
    ]


def _active_item_record(root, item, item_id_cap=None):
    cap = (
        generated_idspace.resolve_item_id_cap()
        if item_id_cap is None
        else generated_idspace.validate_domain_cap(
            generated_idspace.domain_by_key("item"), item_id_cap
        )
    )
    items_header = os.path.join(root, "include", "constants", "items.h")
    expansion_header = os.path.join(
        root, "include", "constants", "items_expansion.h"
    )
    item_values = items_schema.extract_enum_constants(
        items_header, name_prefix="ITEM_"
    )
    if os.path.exists(expansion_header):
        item_values.update(
            items_schema.extract_enum_constants(
                expansion_header, name_prefix="ITEM_"
            )
        )
    if item not in item_values:
        raise ValueError("item '{}' is not a declared ITEM_* value".format(item))
    item_value = item_values[item][0]
    if item_value > cap:
        raise ValueError(
            "item '{}' (index 0x{:02X}) is beyond configured "
            "FE8_ITEM_ID_CAP=0x{:02X}".format(item, item_value, cap)
        )

    records = items_schema.load_records(
        os.path.join(root, "src", "data", "items.json"),
        item_cap=cap,
        overlay_source=os.path.join(
            root, "src", "data", "items_expansion.json"
        ),
    )
    record = next((record for record in records if record.item == item), None)
    if record is None:
        raise ValueError(
            "item '{}' has no active generated ItemData record under "
            "FE8_ITEM_ID_CAP=0x{:02X}".format(item, cap)
        )
    return record


def validate_runtime_binding(root, ownership, item_id_cap=None):
    item = ownership["item"]
    effect_symbol = ownership["effectSymbol"]
    fallback_symbol = ownership["fallbackVanillaEffect"]
    if not isinstance(item, str) or not ITEM_SYMBOL_RE.fullmatch(item):
        raise ValueError("ownership.item must be an ITEM_* symbol")
    if (
        not isinstance(effect_symbol, str)
        or not EFFECT_SYMBOL_RE.fullmatch(effect_symbol)
    ):
        raise ValueError("ownership.effectSymbol must be a CUSTOM_SPELL_* symbol")
    if effect_symbol in public_effect_symbols(root):
        raise ValueError(
            "ownership.effectSymbol '{}' collides with a public/test CUSTOM_SPELL_* symbol".format(
                effect_symbol
            )
        )
    if (
        not isinstance(fallback_symbol, str)
        or not FALLBACK_SYMBOL_RE.fullmatch(fallback_symbol)
    ):
        raise ValueError(
            "ownership.fallbackVanillaEffect must be a SASSOC_EFX_* symbol"
        )
    fallback_values = _spell_fallbacks(os.path.join(root, "include", "spellassoc.h"))
    if fallback_symbol not in fallback_values:
        raise ValueError(
            "fallback '{}' is not declared in spellassoc.h".format(fallback_symbol)
        )
    fallback = fallback_values[fallback_symbol]
    lut = _spell_lut(os.path.join(root, "src", "banim-efxmagic.c"))
    if (
        fallback >= len(lut)
        or lut[fallback] in ("NULL", "(void *)NULL", "(void*)NULL")
        or "Null" in lut[fallback]
        or "_Null" in lut[fallback]
    ):
        raise ValueError(
            "fallback '{}' does not select a valid vanilla spell LUT entry".format(
                fallback_symbol
            )
        )
    item_record = _active_item_record(root, item, item_id_cap=item_id_cap)
    weapon_type = item_record.weapon_type
    attributes = set(item_record.attributes)
    if (
        weapon_type not in ("ITYPE_ANIMA", "ITYPE_LIGHT", "ITYPE_DARK")
        or "IA_WEAPON" not in attributes
        or "IA_MAGIC" not in attributes
        or "IA_STAFF" in attributes
        or "IA_UNUSABLE" in attributes
    ):
        raise ValueError(
            "item '{}' must be a battle-capable anima/light/dark magic weapon".format(
                item
            )
        )
    association_source = ownership["spellAssociationSource"]
    with open(os.path.join(root, association_source), encoding="utf-8") as handle:
        associations = handle.read()
    if re.search(
        r"SPELL_ASSOC_DATA(?:_WPN(?:_MAGIC|_DEFAULT)?)?\(\s*{}\b".format(
            re.escape(item)
        ),
        associations,
    ):
        raise ValueError(
            "item '{}' already owns a SpellAssoc entry".format(item)
        )
    return fallback, weapon_type


def validate_collection(records):
    effects = [
        record for record in records
        if getattr(record, "custom_spell_package", None) is not None
    ]
    if len(effects) > MAX_EFFECTS:
        raise ValueError("custom spell effect count exceeds reserved capacity 16")
    seen_items = set()
    seen_symbols = set()
    aggregate_rom = 0
    for record in effects:
        item = record.ownership["item"]
        symbol = record.ownership["effectSymbol"]
        if item in seen_items:
            raise ValueError("duplicate custom spell item ownership '{}'".format(item))
        if symbol in seen_symbols:
            raise ValueError("duplicate custom spell effect symbol '{}'".format(symbol))
        seen_items.add(item)
        seen_symbols.add(symbol)
        aggregate_rom += record.custom_spell_package.runtime_bytes
    if aggregate_rom > MAX_ROM_BYTES:
        raise ValueError(
            "aggregate custom spell runtime payload {} exceeds 0x40000".format(
                aggregate_rom
            )
        )
