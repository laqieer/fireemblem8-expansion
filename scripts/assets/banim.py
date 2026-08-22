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
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FRAME_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
MAX_MODE_DURATION = 0xFFFF
MAX_SHEET_TILES = 1024
MAX_OAM_PER_FRAME = 32
MAX_PALETTE_COLORS = 16


class BanimPackage:
    """Validated package data used by the static asset adapter."""

    def __init__(self, data, frames, mode_durations, pngs):
        self.data = data
        self.frames = frames
        self.mode_durations = mode_durations
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
    if width == 0 or height == 0 or width % 8 or height % 8:
        raise ValueError("{} dimensions must be positive multiples of 8".format(path))
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
            or any(value not in (0, 255) for value in alpha)
        ):
            raise ValueError(
                "{} tRNS entries must be 0 or 255 with exactly one transparent palette entry".format(
                    path
                )
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
        previous = row
    return {"width": width, "height": height, "tiles": width * height // 64, "colors": len(palette) // 3}


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
    mode = None
    timed = False
    group_duration = None
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
            mode_durations[mode] = 0
        elif command == "end":
            if len(words) != 1 or mode is None or not timed:
                raise ValueError("{}:{} ends an empty or unopened mode".format(path, line_number))
            mode = None
            group_duration = None
        elif command == "frame":
            if mode is None or len(words) not in (3, 4) or words[2] not in frames:
                raise ValueError("{}:{} has an unknown or malformed frame".format(path, line_number))
            if len(words) == 4 and words[3] not in SIDES:
                raise ValueError("{}:{} has an invalid frame side".format(path, line_number))
            duration = _duration(words[1], path, line_number, "frame duration")
            group_duration = duration
            mode_durations[mode] += duration
            timed = True
        elif command == "wait":
            if mode is None or len(words) != 2:
                raise ValueError("{}:{} has an invalid wait".format(path, line_number))
            duration = _duration(words[1], path, line_number, "wait duration")
            group_duration = duration
            mode_durations[mode] += duration
            timed = True
        elif command == "loop":
            if mode is None or len(words) != 2 or group_duration is None:
                raise ValueError("{}:{} has a loop without a preceding timed group".format(path, line_number))
            count = _positive_int(_decimal(words[1], path, line_number), "loop count")
            mode_durations[mode] += group_duration * count
        elif command == "command":
            if mode is None or len(words) != 2 or words[1] not in COMMANDS:
                raise ValueError("{}:{} has an unsupported vanilla command".format(path, line_number))
            group_duration = None
        else:
            raise ValueError("{}:{} has unknown command '{}'".format(path, line_number, command))
        if mode is not None and mode_durations[mode] > MAX_MODE_DURATION:
            raise ValueError("{}:{} exceeds mode timing capacity".format(path, line_number))
    if mode is not None:
        raise ValueError("{} has an unterminated mode".format(path))
    if tuple(sorted(mode_durations)) != tuple(sorted(MODES)):
        raise ValueError("{} must define every v1 mode exactly once".format(path))
    return mode_durations


def _decimal(text, path, line_number):
    if not text.isdecimal():
        raise ValueError("{}:{} requires a decimal integer".format(path, line_number))
    return int(text)


def _duration(text, path, line_number, ref):
    duration = _positive_int(_decimal(text, path, line_number), ref)
    if duration > 255:
        raise ValueError("{}:{} {} must be within 1..255".format(path, line_number, ref))
    return duration


def load_package(root, package_source, script_source, declared_sources):
    package_path = os.path.join(root, package_source)
    with open(package_path, encoding="utf-8") as handle:
        data = json.load(handle)
    _exact_keys(
        data,
        ("schemaVersion", "id", "abbreviation", "animConf", "class", "runtime", "frames", "paletteVariants",
         "resources"),
        package_source,
    )
    if data["schemaVersion"] != PACKAGE_SCHEMA:
        raise ValueError("{} has unsupported schemaVersion".format(package_source))
    for name in ("id", "abbreviation", "animConf", "class"):
        if not isinstance(data[name], str) or not IDENTIFIER_RE.fullmatch(data[name]):
            raise ValueError("{}.{} must be a C identifier".format(package_source, name))
    _exact_keys(data["runtime"], ("modes", "motion", "oamLeft", "oamRight", "palette", "linkerInputs"), "runtime")
    runtime = data["runtime"]
    if not isinstance(runtime["linkerInputs"], list) or not runtime["linkerInputs"]:
        raise ValueError("runtime.linkerInputs must list existing compressor inputs")
    _exact_keys(data["resources"], ("maxFrames", "maxSheetTiles", "maxOamPerFrame", "maxPaletteColors"), "resources")
    for key in data["resources"]:
        _positive_int(data["resources"][key], "resources.{}".format(key))
    if data["resources"]["maxSheetTiles"] > MAX_SHEET_TILES:
        raise ValueError("resources.maxSheetTiles exceeds vanilla OBJ sheet capacity")
    if data["resources"]["maxOamPerFrame"] > MAX_OAM_PER_FRAME:
        raise ValueError("resources.maxOamPerFrame exceeds vanilla OAM frame capacity")
    if data["resources"]["maxPaletteColors"] > MAX_PALETTE_COLORS:
        raise ValueError("resources.maxPaletteColors exceeds one OBJ palette bank")
    if not isinstance(data["paletteVariants"], list) or not data["paletteVariants"]:
        raise ValueError("paletteVariants must be a non-empty list")
    if any(not isinstance(variant, str) or not variant for variant in data["paletteVariants"]):
        raise ValueError("paletteVariants must contain non-empty strings")
    if len(set(data["paletteVariants"])) != len(data["paletteVariants"]):
        raise ValueError("paletteVariants contains a duplicate variant")
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
    if len(frame_paths) > data["resources"]["maxFrames"]:
        raise ValueError("frame count exceeds resources.maxFrames")
    if sum(png["tiles"] for png in pngs.values()) > data["resources"]["maxSheetTiles"]:
        raise ValueError("frame sheet tiles exceed resources.maxSheetTiles")
    if max(png["colors"] for png in pngs.values()) > data["resources"]["maxPaletteColors"]:
        raise ValueError("frame palette colors exceed resources.maxPaletteColors")
    durations = parse_script(os.path.join(root, script_source), frame_paths)
    return BanimPackage(data, frame_paths, durations, pngs)
