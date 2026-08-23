"""Fail-closed adapter for version-1 community battle-animation packages.

The adapter deliberately publishes declarations into the existing battle
animation and class-data seams.  It does not create a runtime registry or
interpret spell-animation formats.
"""

from __future__ import annotations

import json
import os
import re
import struct
import zlib

PACKAGE_SCHEMA = "fe8.community-banim.v1"
SCRIPT_HEADER = "BANIM 1"
MODES = ("normal", "critical", "ranged", "dodge", "standing")
SIDES = ("left", "right", "both")
COMMANDS = {
    "start_attack_1": "banim_code_start_attack_1",
    "start_attack_2": "banim_code_start_attack_2",
    "hit_normal": "banim_code_hit_normal",
    "hit_critical_1": "banim_code_hit_critical_1",
    "prepare_hp_deplete": "banim_code_prepare_hp_deplete",
    "wait_hp_deplete": "banim_code_wait_hp_deplete",
    "start_dodge": "banim_code_start_dodge",
    "end_dodge": "banim_code_end_dodge",
    "range_attack": "banim_code_range_attack",
    "shake_screen_heavily": "banim_code_shake_screnn_heavily",
    "shake_screen_slightly": "banim_code_shake_screnn_slightly",
}
SOUNDS = {
    "sword_swing_short": ("banim_code_sound_sword_swing_short", 0x85000022),
    "sword_slash_air": ("banim_code_sound_sword_slash_air", 0x85000024),
    "step_heavy": ("banim_code_sound_step_heavy", 0x85000034),
}
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FRAME_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_MODE_DURATION = 0xFFFF
MAX_SHEET_TILES = 1024
MAX_OAM_PER_FRAME = 32
MAX_PALETTE_COLORS = 16
MODE_INDEXES = (
    "normal", "normal", "critical", "critical", "ranged", "ranged",
    "dodge", "dodge", "standing", "standing", "standing", "standing",
    "normal", "normal", "normal", "normal", "normal", "normal",
    "normal", "normal", "normal", "normal", "normal", "normal",
)


class BanimPackage:
    """Validated package data used by the static asset adapter."""

    def __init__(self, data, frames, mode_durations, modes, pngs):
        self.data = data
        self.frames = frames
        self.mode_durations = mode_durations
        self.modes = modes
        self.pngs = pngs


def _exact_keys(data, keys, ref):
    if not isinstance(data, dict):
        raise ValueError("{} must be an object".format(ref))
    actual = set(data)
    required = set(keys)
    missing = sorted(required - actual)
    extra = sorted(actual - required)
    if missing:
        raise ValueError("{} is missing {}".format(ref, ", ".join(missing)))
    if extra:
        raise ValueError("{} has unknown field {}".format(ref, extra[0]))


def _positive_int(value, ref):
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError("{} must be a positive integer".format(ref))
    return value


def read_indexed_png(path):
    """Read only the indexed-PNG contract needed by the adapter."""
    with open(path, "rb") as handle:
        if handle.read(8) != b"\x89PNG\r\n\x1a\n":
            raise ValueError("{} is not a PNG".format(path))
        chunks = []
        while True:
            length_data = handle.read(4)
            if len(length_data) != 4:
                raise ValueError("{} has a truncated PNG chunk".format(path))
            length = struct.unpack(">I", length_data)[0]
            name = handle.read(4)
            data = handle.read(length)
            crc = handle.read(4)
            if len(name) != 4 or len(data) != length or len(crc) != 4:
                raise ValueError("{} has a truncated PNG chunk".format(path))
            if struct.unpack(">I", crc)[0] != zlib.crc32(name + data) & 0xFFFFFFFF:
                raise ValueError("{} has an invalid PNG chunk CRC".format(path))
            chunks.append((name, data))
            if name == b"IEND":
                break
    if not chunks or chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND":
        raise ValueError("{} must have IHDR first and IEND last".format(path))
    if len(chunks[0][1]) != 13:
        raise ValueError("{} has an invalid IHDR".format(path))
    width, height, depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", chunks[0][1]
    )
    if depth != 4 or color_type != 3 or compression != 0 or filter_method != 0 or interlace != 0:
        raise ValueError(
            "{} must be non-interlaced indexed 4bpp PNG with standard compression/filter".format(path)
        )
    if width == 0 or height == 0 or width % 32 or height % 32:
        raise ValueError("{} dimensions must be positive multiples of 32".format(path))
    names = [name for name, _ in chunks]
    if names.count(b"PLTE") != 1 or names.count(b"IDAT") != 1 or names.count(b"tRNS") > 1:
        raise ValueError("{} must contain one PLTE, one IDAT, and at most one tRNS".format(path))
    allowed = (b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND")
    if any(name not in allowed for name in names):
        raise ValueError("{} contains an unsupported PNG chunk".format(path))
    if names != [b"IHDR", b"PLTE"] + ([b"tRNS"] if b"tRNS" in names else []) + [b"IDAT", b"IEND"]:
        raise ValueError("{} has an invalid indexed-PNG chunk order".format(path))
    palette = next(data for name, data in chunks if name == b"PLTE")
    if not palette or len(palette) % 3 or len(palette) // 3 > MAX_PALETTE_COLORS:
        raise ValueError("{} palette must contain 1..16 RGB entries".format(path))
    transparent = [data for name, data in chunks if name == b"tRNS"]
    if transparent:
        alpha = transparent[0]
        if (
            len(alpha) > len(palette) // 3
            or alpha.count(0) != 1
            or alpha[0] != 0
            or any(value not in (0, 255) for value in alpha)
        ):
            raise ValueError(
                "{} tRNS must make only palette index 0 transparent using binary alpha".format(path)
            )
    encoded = next(data for name, data in chunks if name == b"IDAT")
    try:
        pixels = zlib.decompress(encoded)
    except zlib.error as exc:
        raise ValueError("{} has an invalid PNG IDAT stream: {}".format(path, exc)) from exc
    row_bytes = width // 2
    if len(pixels) != height * (row_bytes + 1):
        raise ValueError("{} has an invalid indexed-PNG scanline size".format(path))
    previous = bytearray(row_bytes)
    decoded = bytearray()
    colors = len(palette) // 3
    offset = 0
    for _ in range(height):
        filter_type = pixels[offset]
        offset += 1
        if filter_type > 4:
            raise ValueError("{} has an invalid PNG scanline filter".format(path))
        row = bytearray(pixels[offset:offset + row_bytes])
        offset += row_bytes
        for index, value in enumerate(row):
            left = row[index - 1] if index else 0
            up = previous[index]
            if filter_type == 1:
                row[index] = (value + left) & 0xFF
            elif filter_type == 2:
                row[index] = (value + up) & 0xFF
            elif filter_type == 3:
                row[index] = (value + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                upper_left = previous[index - 1] if index else 0
                p = left + up - upper_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upper_left)
                predictor = left if pa <= pb and pa <= pc else up if pb <= pc else upper_left
                row[index] = (value + predictor) & 0xFF
        if any((value >> 4) >= colors or (value & 0x0F) >= colors for value in row):
            raise ValueError("{} has a palette index outside PLTE".format(path))
        decoded.extend(row)
        previous = row
    return {
        "width": width,
        "height": height,
        "tiles": width * height // 64,
        "colors": len(palette) // 3,
        "pixels": bytes(decoded),
        "palette": palette,
        "transparency": transparent[0] if transparent else None,
    }


def parse_script(path, frames):
    with open(path, encoding="utf-8", newline="") as handle:
        text = handle.read()
    if "\r" in text:
        raise ValueError("{} must use LF line endings".format(path))
    lines = text.splitlines()
    header_line = None
    for line_number, source_line in enumerate(lines, 1):
        line = source_line.strip()
        if line and not line.startswith("#"):
            header_line = line_number
            if line != SCRIPT_HEADER:
                raise ValueError("{} must begin with '{}'".format(path, SCRIPT_HEADER))
            break
    if header_line is None:
        raise ValueError("{} must begin with '{}'".format(path, SCRIPT_HEADER))
    mode_durations = {}
    modes = {}
    mode = None
    timed = False
    group_duration = None
    group = None
    for line_number, source_line in enumerate(lines[header_line:], header_line + 1):
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        words = line.split()
        command = words[0]
        if command == "mode":
            if len(words) != 2 or words[1] not in MODES or mode is not None or words[1] in mode_durations:
                raise ValueError("{}:{} has invalid or duplicate mode".format(path, line_number))
            mode = words[1]
            timed = False
            group_duration = None
            group = None
            mode_durations[mode] = 0
            modes[mode] = []
        elif command == "end":
            if len(words) != 1 or mode is None or not timed:
                raise ValueError("{}:{} ends an empty or unopened mode".format(path, line_number))
            mode = None
            group_duration = None
            group = None
        elif command == "frame":
            if mode is None or len(words) not in (3, 4) or words[2] not in frames:
                raise ValueError("{}:{} has an unknown or malformed frame".format(path, line_number))
            if len(words) == 4 and words[3] not in SIDES:
                raise ValueError("{}:{} has an invalid frame side".format(path, line_number))
            duration = _duration(words[1], path, line_number, "frame duration")
            entry = ("frame", duration, words[2], words[3] if len(words) == 4 else "both")
            modes[mode].append(entry)
            group_duration = duration
            group = (entry,)
            mode_durations[mode] += duration
            timed = True
        elif command == "wait":
            if mode is None or len(words) != 2:
                raise ValueError("{}:{} has an invalid wait".format(path, line_number))
            duration = _duration(words[1], path, line_number, "wait duration")
            entry = ("wait", duration)
            modes[mode].append(entry)
            group_duration = duration
            group = (entry,)
            mode_durations[mode] += duration
            timed = True
        elif command == "loop":
            if mode is None or len(words) != 2 or group_duration is None or group is None:
                raise ValueError("{}:{} has a loop without a preceding timed group".format(path, line_number))
            count = _positive_int(_decimal(words[1], path, line_number), "loop count")
            mode_durations[mode] += group_duration * count
            for _ in range(count):
                modes[mode].extend(group)
        elif command == "command":
            if mode is None or len(words) != 2 or words[1] not in COMMANDS:
                raise ValueError("{}:{} has an unsupported vanilla command".format(path, line_number))
            modes[mode].append(("command", words[1]))
            group_duration = None
            group = None
        elif command == "sound":
            if mode is None or len(words) != 2 or words[1] not in SOUNDS:
                raise ValueError("{}:{} has an unsupported sound command".format(path, line_number))
            modes[mode].append(("sound", words[1]))
            group_duration = None
            group = None
        else:
            raise ValueError("{}:{} has unknown command '{}'".format(path, line_number, command))
        if mode is not None and mode_durations[mode] > MAX_MODE_DURATION:
            raise ValueError("{}:{} exceeds mode timing capacity".format(path, line_number))
    if mode is not None:
        raise ValueError("{} has an unterminated mode".format(path))
    if tuple(sorted(mode_durations)) != tuple(sorted(MODES)):
        raise ValueError("{} must define every v1 mode exactly once".format(path))
    return mode_durations, modes


def _decimal(text, path, line_number):
    if not text.isdecimal():
        raise ValueError("{}:{} requires a decimal integer".format(path, line_number))
    return int(text)


def _duration(text, path, line_number, ref):
    duration = _positive_int(_decimal(text, path, line_number), ref)
    if duration > 255:
        raise ValueError("{}:{} {} must be within 1..255".format(path, line_number, ref))
    return duration


def _validate_shared_palette(pngs):
    first = next(iter(pngs.values()))
    for frame_id, png in pngs.items():
        if png["palette"] != first["palette"] or png["transparency"] != first["transparency"]:
            raise ValueError(
                "frame '{}' must share the first frame's identical PLTE and tRNS".format(frame_id)
            )


def load_package(root, package_source, script_source, declared_sources):
    package_path = os.path.join(root, package_source)
    with open(package_path, encoding="utf-8") as handle:
        data = json.load(handle)
    _exact_keys(
        data,
        ("schemaVersion", "id", "abbreviation", "animConf", "class", "weaponType", "frames",
         "paletteVariants", "resources"),
        package_source,
    )
    if data["schemaVersion"] != PACKAGE_SCHEMA:
        raise ValueError("{} has unsupported schemaVersion".format(package_source))
    for name in ("id", "abbreviation", "animConf", "class", "weaponType"):
        if not isinstance(data[name], str) or not IDENTIFIER_RE.fullmatch(data[name]):
            raise ValueError("{}.{} must be a C identifier".format(package_source, name))
    if len(data["abbreviation"]) >= 12:
        raise ValueError("{}.abbreviation must fit char abbr[12] with a terminator".format(package_source))
    _exact_keys(data["resources"], ("maxFrames", "maxSheetTiles", "maxOamPerFrame", "maxPaletteColors"), "resources")
    for key in data["resources"]:
        _positive_int(data["resources"][key], "resources.{}".format(key))
    if data["resources"]["maxSheetTiles"] > MAX_SHEET_TILES:
        raise ValueError("resources.maxSheetTiles exceeds vanilla OBJ sheet capacity")
    if data["resources"]["maxOamPerFrame"] > MAX_OAM_PER_FRAME:
        raise ValueError("resources.maxOamPerFrame exceeds vanilla OAM frame capacity")
    if data["resources"]["maxPaletteColors"] > MAX_PALETTE_COLORS:
        raise ValueError("resources.maxPaletteColors exceeds one OBJ palette bank")
    if data["paletteVariants"] != ["default"]:
        raise ValueError("v1 paletteVariants must be exactly ['default']")
    frame_paths = {}
    if not isinstance(data["frames"], list) or not data["frames"]:
        raise ValueError("frames must be a non-empty list")
    for frame in data["frames"]:
        _exact_keys(frame, ("id", "path"), "frame")
        if not isinstance(frame["id"], str) or not FRAME_ID_RE.fullmatch(frame["id"]):
            raise ValueError("frame.id must be a lower-case symbolic identifier")
        if frame["id"] in frame_paths or frame["path"] not in declared_sources:
            raise ValueError("frame IDs and paths must be unique declared manifest sources")
        if not frame["path"].endswith(".png"):
            raise ValueError("frame.path must name an indexed PNG")
        frame_paths[frame["id"]] = frame["path"]
    pngs = {frame_id: read_indexed_png(os.path.join(root, path)) for frame_id, path in frame_paths.items()}
    _validate_shared_palette(pngs)
    if len(frame_paths) > data["resources"]["maxFrames"]:
        raise ValueError("frame count exceeds resources.maxFrames")
    unique_tiles = {_frame_tiles(png) for png in pngs.values()}
    if sum(len(payload) // 32 for payload in unique_tiles) > data["resources"]["maxSheetTiles"]:
        raise ValueError("frame sheet tiles exceed resources.maxSheetTiles")
    if max(png["colors"] for png in pngs.values()) > data["resources"]["maxPaletteColors"]:
        raise ValueError("frame palette colors exceed resources.maxPaletteColors")
    durations, modes = parse_script(os.path.join(root, script_source), frame_paths)
    return BanimPackage(data, frame_paths, durations, modes, pngs)


def runtime_stem(package):
    return "banim_package_{}".format(package.data["id"].lower())


def runtime_paths(package, out_dir, aliases=None):
    stem = runtime_stem(package)
    directory = os.path.join(out_dir, "banim")
    paths = {
        "motion": os.path.join(directory, stem + "_motion.s"),
        "modes": os.path.join(directory, stem + "_modes.bin"),
        "oam_left": os.path.join(directory, stem + "_oam_l.bin"),
        "oam_right": os.path.join(directory, stem + "_oam_r.bin"),
        "palette": os.path.join(directory, stem + "_palette.pal"),
    }
    for frame_id in package.frames:
        source_id = aliases[frame_id] if aliases is not None else frame_id
        paths["frame_" + frame_id] = os.path.join(directory, stem + "_" + source_id + ".4bpp")
    return paths


def _pixel(png, x, y):
    packed = png["pixels"][y * (png["width"] // 2) + x // 2]
    return packed >> 4 if x % 2 == 0 else packed & 0x0F


def _frame_tiles(png):
    data = bytearray()
    for tile_y in range(0, png["height"], 8):
        for tile_x in range(0, png["width"], 8):
            for y in range(tile_y, tile_y + 8):
                for x in range(tile_x, tile_x + 8, 2):
                    data.append(_pixel(png, x, y) | (_pixel(png, x + 1, y) << 4))
    return bytes(data)


def _palette_data(png):
    data = bytearray()
    for index in range(MAX_PALETTE_COLORS):
        if index < png["colors"]:
            red, green, blue = png["palette"][index * 3:index * 3 + 3]
            color = (red >> 3) | ((green >> 3) << 5) | ((blue >> 3) << 10)
        else:
            color = 0
        data.extend(struct.pack("<H", color))
    return bytes(data)


def _oam_frame(png):
    data = bytearray()
    columns = png["width"] // 32
    rows = png["height"] // 32
    for row in range(rows):
        for column in range(columns):
            tile = (row * 4 * (png["width"] // 8)) + column * 4
            data.extend(
                struct.pack(
                    "<6H",
                    0,
                    0x8000,
                    tile,
                    (column * 32 - png["width"] // 2) & 0xFFFF,
                    (row * 32 - png["height"] // 2) & 0xFFFF,
                    0,
                )
            )
    data.extend(struct.pack("<3I", 1, 0, 0))
    return bytes(data), columns * rows


def _oam_data(package):
    offsets = {}
    left = bytearray()
    right = bytearray()
    max_entries = 0
    total_entries = 0
    pairs = []
    fallback_frame = next(iter(package.frames))
    for mode in MODES:
        for entry in package.modes[mode]:
            if entry[0] == "frame":
                pairs.append((entry[2], entry[3]))
            elif entry[0] == "wait":
                pairs.append((fallback_frame, "both"))
    for frame_id, side in pairs:
        key = (frame_id, side)
        if key in offsets:
            continue
        frame, entries = _oam_frame(package.pngs[frame_id])
        if entries > package.data["resources"]["maxOamPerFrame"]:
            raise ValueError(
                "frame '{}' requires {} OAM entries, exceeding resources.maxOamPerFrame".format(
                    frame_id, entries
                )
            )
        offsets[key] = len(left)
        max_entries = max(max_entries, entries)
        total_entries += entries * (2 if side == "both" else 1)
        blank = struct.pack("<3I", 1, 0, 0) + b"\0" * (len(frame) - 12)
        left.extend(frame if side in ("left", "both") else blank)
        right.extend(frame if side in ("right", "both") else blank)
    return bytes(left), bytes(right), offsets, max_entries, total_entries


def _script_word_count(entries):
    words = 1
    for entry in entries:
        words += 3 if entry[0] in ("frame", "wait") else 1
    return words


def _motion_source(package, offsets, aliases):
    stem = runtime_stem(package)
    lines = [
        '@ AUTO-GENERATED by scripts.assets; do not edit.\n',
        '.include "banim_code.inc"\n',
        '.section .data.script\n',
        stem + "_script:\n",
    ]
    for frame_id in sorted(set(aliases.values())):
        lines.append(".extern {}_{}\n".format(stem, frame_id))
    fallback_frame = next(iter(package.frames))
    for mode in MODES:
        lines.append(stem + "_mode_" + mode + ":\n")
        for entry in package.modes[mode]:
            kind = entry[0]
            if kind == "frame":
                _, duration, frame_id, side = entry
                lines.append(
                    "banim_code_frame {}, {}_{}, 0, {}\n".format(
                        duration, stem, aliases[frame_id], offsets[(frame_id, side)]
                    )
                )
            elif kind == "wait":
                lines.append(
                    "banim_code_frame {}, {}_{}, 0, {}\n".format(
                        entry[1], stem, aliases[fallback_frame], offsets[(fallback_frame, "both")]
                    )
                )
            elif kind == "command":
                lines.append(COMMANDS[entry[1]] + "\n")
            else:
                lines.append(SOUNDS[entry[1]][0] + "\n")
        lines.append("banim_code_end_mode\n")
    return "".join(lines)


def runtime_outputs(package, out_dir):
    aliases = {}
    unique_frames = {}
    for frame_id, png in package.pngs.items():
        payload = _frame_tiles(png)
        if payload in unique_frames:
            aliases[frame_id] = unique_frames[payload]
        else:
            aliases[frame_id] = frame_id
            unique_frames[payload] = frame_id
    paths = runtime_paths(package, out_dir, aliases)
    oam_left, oam_right, offsets, max_oam_entries, total_oam_entries = _oam_data(package)
    mode_offsets = {}
    offset = 0
    for mode in MODES:
        mode_offsets[mode] = offset
        offset += _script_word_count(package.modes[mode]) * 4
    modes = b"".join(struct.pack("<I", mode_offsets[mode]) for mode in MODE_INDEXES)
    first_png = next(iter(package.pngs.values()))
    sounds = [
        SOUNDS[entry[1]][1]
        for entries in package.modes.values()
        for entry in entries
        if entry[0] == "sound"
    ]
    outputs = {
        paths["motion"]: _motion_source(package, offsets, aliases),
        paths["modes"]: modes,
        paths["oam_left"]: oam_left,
        paths["oam_right"]: oam_right,
        paths["palette"]: _palette_data(first_png),
    }
    for frame_id, png in package.pngs.items():
        if aliases[frame_id] == frame_id:
            outputs[paths["frame_" + frame_id]] = _frame_tiles(png)
    unique_frame_bytes = sum(
        len(content)
        for path, content in outputs.items()
        if path.endswith(".4bpp")
    )
    runtime_bytes = sum(
        len(content.encode("utf-8")) if isinstance(content, str) else len(content)
        for content in outputs.values()
    )
    metadata = {
        "max_oam_entries": max_oam_entries,
        "total_oam_entries": total_oam_entries,
        "palette_color_1": struct.unpack("<H", outputs[paths["palette"]][2:4])[0],
        "script_word_count": offset // 4,
        "sound_opcode": sounds[0] if sounds else 0,
        "unique_frame_bytes": unique_frame_bytes,
        "runtime_bytes": runtime_bytes,
    }
    return outputs, paths, metadata
