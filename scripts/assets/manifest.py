"""Strict schema, validation, and deterministic rendering for asset manifests."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unicodedata
import struct
import zlib

from scripts.generated_data.diagnostics import (
    DiagnosticCollector,
    GeneratedDataError,
    GeneratedDataValidationError,
)
from scripts.generated_data.json_loader import load_json_file


SCHEMA_VERSION = 1
OUTPUT_MAKEFILE = "asset_manifest.mk"
OUTPUT_INVENTORY = "asset_inventory.md"
OUTPUT_PORTRAIT_DATA = "portrait_data.inc"
OUTPUT_PORTRAIT_COMPONENTS = "portrait_components.inc"
OUTPUT_PORTRAIT_SYMBOLS = "portrait_components.h"
OUTPUT_NAMES = (
    OUTPUT_MAKEFILE,
    OUTPUT_INVENTORY,
    OUTPUT_PORTRAIT_DATA,
    OUTPUT_PORTRAIT_COMPONENTS,
    OUTPUT_PORTRAIT_SYMBOLS,
)
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ASSET_OUTPUT_ROOT = os.path.abspath(
    os.path.join(REPO_ROOT, "build", "generated", "assets")
)
ASSET_OUTPUT_ROOT_REAL = os.path.realpath(ASSET_OUTPUT_ROOT)
ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _has_exact_value(value, expected):
    return type(value) is type(expected) and value == expected


class AssetRecord:
    """A validated manifest record with its source locations preserved."""

    __slots__ = (
        "id", "id_loc", "kind", "kind_loc", "sources", "source_locs",
        "depends_on", "dependency_locs", "options", "option_locs",
        "ownership", "ownership_locs", "resources", "resource_locs",
        "provenance", "provenance_locs", "loc",
    )

    def __init__(
        self, id_, id_loc, kind, kind_loc, sources, source_locs,
        depends_on, dependency_locs, options, option_locs, ownership,
        ownership_locs, resources, resource_locs, provenance, provenance_locs,
        loc,
    ):
        self.id = id_
        self.id_loc = id_loc
        self.kind = kind
        self.kind_loc = kind_loc
        self.sources = sources
        self.source_locs = source_locs
        self.depends_on = depends_on
        self.dependency_locs = dependency_locs
        self.options = options
        self.option_locs = option_locs
        self.ownership = ownership
        self.ownership_locs = ownership_locs
        self.resources = resources
        self.resource_locs = resource_locs
        self.provenance = provenance
        self.provenance_locs = provenance_locs
        self.loc = loc


def _ensure_exact_keys(node, required, reference_path):
    actual = set(node.keys())
    missing = sorted(set(required) - actual)
    extra = sorted(actual - set(required))
    if missing:
        raise GeneratedDataError(
            "missing required field(s): {}".format(", ".join(missing)),
            node.loc,
            reference_path,
        )
    if extra:
        key = extra[0]
        raise GeneratedDataError(
            "unknown field '{}'".format(key),
            node.key_locations[key],
            reference_path,
        )


def _object_values(node):
    values = {}
    locations = {}
    for key, child in node.items():
        values[key] = child.native()
        locations[key] = child.loc
    return values, locations


def _validate_exact_values(values, locations, required, diagnostics, fallback_location, reference_path):
    actual = set(values)
    missing = sorted(set(required) - actual)
    extra = sorted(actual - set(required))
    for key in missing:
        diagnostics.add(
            GeneratedDataError(
                "missing required field '{}'".format(key),
                fallback_location,
                reference_path,
            )
        )
    for key in extra:
        diagnostics.add(
            GeneratedDataError(
                "unknown field '{}'".format(key),
                locations[key],
                reference_path,
            )
        )
    return not missing and not extra


def _parse_record(node):
    required = (
        "id", "kind", "sources", "dependsOn", "options", "ownership",
        "resources", "provenance",
    )
    _ensure_exact_keys(node, required, "asset")
    id_node = node.require("id")
    kind_node = node.require("kind")
    sources_node = node.require("sources")
    dependencies_node = node.require("dependsOn")
    options_node = node.require("options")
    ownership_node = node.require("ownership")
    resources_node = node.require("resources")
    provenance_node = node.require("provenance")

    sources = [item.as_str() for item in sources_node.as_list()]
    source_locs = [item.loc for item in sources_node.as_list()]
    depends_on = [item.as_str() for item in dependencies_node.as_list()]
    dependency_locs = [item.loc for item in dependencies_node.as_list()]
    options, option_locs = _object_values(options_node)
    ownership, ownership_locs = _object_values(ownership_node)
    resources, resource_locs = _object_values(resources_node)
    provenance, provenance_locs = _object_values(provenance_node)
    return AssetRecord(
        id_node.as_str(), id_node.loc, kind_node.as_str(), kind_node.loc,
        sources, source_locs, depends_on, dependency_locs, options, option_locs,
        ownership, ownership_locs, resources, resource_locs, provenance,
        provenance_locs, node.loc,
    )


def load_manifest(path):
    root = load_json_file(path)
    _ensure_exact_keys(root, ("schemaVersion", "assets"), "manifest")
    schema_node = root.require("schemaVersion")
    schema_version = schema_node.as_int()
    if not _is_int(schema_version) or schema_version != SCHEMA_VERSION:
        raise GeneratedDataError(
            "unsupported schema version {}; expected {}".format(
                schema_node.as_int(), SCHEMA_VERSION
            ),
            schema_node.loc,
            "schemaVersion",
        )
    return [_parse_record(node) for node in root.require("assets").as_list()]


def _repo_path(path, loc, reference_path):
    if not isinstance(path, str) or not path:
        raise GeneratedDataError("expected a non-empty path", loc, reference_path)
    if path != unicodedata.normalize("NFC", path):
        raise GeneratedDataError(
            "source path must use NFC-normalized Unicode",
            loc,
            reference_path,
        )
    if "\\" in path:
        raise GeneratedDataError(
            "source path must use normalized POSIX separators",
            loc,
            reference_path,
        )
    normalized = path
    if (
        os.path.isabs(normalized)
        or normalized.startswith("build/")
        or normalized == "build"
        or any(part in ("", ".", "..") for part in normalized.split("/"))
    ):
        raise GeneratedDataError(
            "unsafe source path '{}'; use a tracked repository-relative source outside build/".format(path),
            loc,
            reference_path,
        )
    absolute = os.path.abspath(os.path.join(REPO_ROOT, normalized))
    if os.path.commonpath((REPO_ROOT, absolute)) != REPO_ROOT:
        raise GeneratedDataError("source path escapes repository root", loc, reference_path)
    current = REPO_ROOT
    for component in normalized.split("/"):
        current = os.path.join(current, component)
        if os.path.islink(current):
            raise GeneratedDataError(
                "source path must not traverse a symbolic link",
                loc,
                reference_path,
            )
    resolved = os.path.realpath(absolute)
    resolved_root = os.path.realpath(REPO_ROOT)
    if os.path.commonpath((resolved_root, resolved)) != resolved_root:
        raise GeneratedDataError("source path resolves outside repository root", loc, reference_path)
    if not os.path.isfile(absolute):
        raise GeneratedDataError("declared source '{}' does not exist".format(normalized), loc, reference_path)
    result = subprocess.run(
        ["git", "-C", REPO_ROOT, "ls-files", "--error-unmatch", "--", normalized],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        raise GeneratedDataError(
            "declared source '{}' is not a tracked committed source".format(normalized),
            loc,
            reference_path,
        )
    return normalized


def _canonical_source_key(path):
    return unicodedata.normalize("NFC", path).casefold()


def safe_output_dir(path):
    requested = os.path.abspath(path)
    if os.path.commonpath((ASSET_OUTPUT_ROOT, requested)) != ASSET_OUTPUT_ROOT:
        raise GeneratedDataError(
            "generated output directory '{}' must stay under {}".format(path, ASSET_OUTPUT_ROOT)
        )
    relative = os.path.relpath(requested, REPO_ROOT)
    current = REPO_ROOT
    for component in relative.split(os.sep):
        current = os.path.join(current, component)
        if os.path.islink(current):
            raise GeneratedDataError(
                "generated output directory '{}' must not traverse a symbolic link".format(path)
            )
    resolved = os.path.realpath(requested)
    if os.path.commonpath((ASSET_OUTPUT_ROOT_REAL, resolved)) != ASSET_OUTPUT_ROOT_REAL:
        raise GeneratedDataError(
            "generated output directory '{}' must stay under {}".format(path, ASSET_OUTPUT_ROOT)
        )
    return requested


def _write_if_changed(path, content):
    existing = None
    if os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            existing = handle.read()
    if existing == content:
        return False
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".asset-manifest-",
        dir=directory,
        text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temporary_path, path)
    except OSError:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise
    return True


def _write_bytes_if_changed(path, content):
    existing = None
    if os.path.exists(path):
        with open(path, "rb") as handle:
            existing = handle.read()
    if existing == content:
        return False
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=".asset-manifest-",
        dir=directory,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        os.replace(temporary_path, path)
    except OSError:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
        raise
    return True


def _validate_provenance(record, diagnostics):
    expected = ("origin", "license", "modifications", "tools")
    if not _validate_exact_values(
        record.provenance,
        record.provenance_locs,
        expected,
        diagnostics,
        record.loc,
        "{}.provenance".format(record.id),
    ):
        return
    for key in ("origin", "license", "modifications"):
        value = record.provenance[key]
        if not isinstance(value, str) or not value.strip():
            diagnostics.add(
                GeneratedDataError(
                    "provenance.{} must be a non-empty string".format(key),
                    record.provenance_locs[key],
                    "{}.provenance.{}".format(record.id, key),
                )
            )
    tools = record.provenance["tools"]
    if not isinstance(tools, list) or any(not isinstance(tool, str) or not tool for tool in tools):
        diagnostics.add(
            GeneratedDataError(
                "provenance.tools must be a list of non-empty tool/version strings",
                record.provenance_locs["tools"],
                "{}.provenance.tools".format(record.id),
            )
        )


def _chapter_table_entries(path):
    with open(os.path.join(REPO_ROOT, path), encoding="utf-8") as handle:
        content = handle.read()
    match = re.search(
        r"gChapterDataAssetTable\s*\[\s*\]\s*=\s*\{(?P<body>.*?)\};",
        content,
        re.DOTALL,
    )
    if match is None:
        raise ValueError("cannot find gChapterDataAssetTable initializer")
    return [item.strip() for item in match.group("body").split(",") if item.strip()]


def _chapter_settings_row(path, index):
    with open(os.path.join(REPO_ROOT, path), encoding="utf-8") as handle:
        document = json.load(handle)
    chapters = document.get("chapters") if isinstance(document, dict) else None
    if not isinstance(chapters, list):
        return None
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(chapters):
        return None
    return chapters[index] if isinstance(chapters[index], dict) else None


def _read_png(path):
    """Read the strict indexed PNG subset accepted by portrait packages."""
    with open(path, "rb") as handle:
        data = handle.read()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG file")

    offset = 8
    chunks = []
    while offset < len(data):
        if offset + 12 > len(data):
            raise ValueError("truncated PNG chunk")
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + length
        if end > len(data):
            raise ValueError("truncated PNG chunk data")
        payload = data[offset + 8:offset + 8 + length]
        crc = struct.unpack(">I", data[offset + 8 + length:end])[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != crc:
            raise ValueError("PNG chunk CRC mismatch")
        chunks.append((kind, payload))
        offset = end
        if kind == b"IEND":
            break
    if offset != len(data) or not chunks or chunks[-1][0] != b"IEND":
        raise ValueError("PNG must end with a single IEND chunk")
    if chunks[0][0] != b"IHDR" or len(chunks[0][1]) != 13:
        raise ValueError("PNG must begin with IHDR")
    kinds = [kind for kind, _ in chunks]
    if kinds.count(b"IHDR") != 1 or kinds.count(b"PLTE") != 1 or kinds.count(b"IEND") != 1:
        raise ValueError("PNG must contain exactly one IHDR, PLTE, and IEND chunk")
    if not any(kind == b"IDAT" for kind in kinds):
        raise ValueError("PNG must contain IDAT image data")
    idat_positions = [index for index, kind in enumerate(kinds) if kind == b"IDAT"]
    if idat_positions != list(range(idat_positions[0], idat_positions[-1] + 1)):
        raise ValueError("PNG IDAT chunks must be contiguous")
    plte_index = kinds.index(b"PLTE")
    if not 0 < plte_index < idat_positions[0]:
        raise ValueError("PNG PLTE must occur after IHDR and before IDAT")
    if b"tRNS" in kinds and not plte_index < kinds.index(b"tRNS") < idat_positions[0]:
        raise ValueError("PNG tRNS must occur after PLTE and before IDAT")
    if any(kind[:1].isupper() and kind not in (b"IHDR", b"PLTE", b"IDAT", b"IEND") for kind in kinds):
        raise ValueError("PNG has an unsupported critical chunk")

    width, height, depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", chunks[0][1]
    )
    if color_type != 3 or depth not in (4, 8):
        raise ValueError("PNG must use indexed color with 4-bit or 8-bit indices")
    if compression != 0 or filter_method != 0 or interlace != 0:
        raise ValueError("PNG must be non-interlaced with standard compression and filtering")
    palette_chunks = [payload for kind, payload in chunks if kind == b"PLTE"]
    idat = b"".join(payload for kind, payload in chunks if kind == b"IDAT")
    transparency_chunks = [payload for kind, payload in chunks if kind == b"tRNS"]
    if len(palette_chunks) != 1 or len(palette_chunks[0]) % 3 or not palette_chunks[0]:
        raise ValueError("indexed PNG requires one non-empty PLTE chunk")
    palette = [
        tuple(palette_chunks[0][index:index + 3])
        for index in range(0, len(palette_chunks[0]), 3)
    ]
    if len(palette) != 16:
        raise ValueError("PNG palette must contain exactly 16 colors")
    if len(transparency_chunks) > 1:
        raise ValueError("PNG has more than one tRNS chunk")
    alpha = list(transparency_chunks[0]) if transparency_chunks else []
    if alpha and len(alpha) > len(palette):
        raise ValueError("PNG tRNS exceeds palette length")
    alpha += [255] * (len(palette) - len(alpha))
    if alpha[0] != 0 or any(value not in (0, 255) for value in alpha):
        raise ValueError("PNG palette index 0 must be transparent and all alpha values must be 0 or 255")

    stride = (width * depth + 7) // 8
    raw = zlib.decompress(idat)
    if len(raw) != height * (stride + 1):
        raise ValueError("PNG scanline data has an unexpected length")
    rows = []
    previous = [0] * stride
    position = 0
    for _ in range(height):
        filter_type = raw[position]
        position += 1
        source = list(raw[position:position + stride])
        position += stride
        row = []
        for index, value in enumerate(source):
            left = row[index - 1] if index else 0
            up = previous[index]
            up_left = previous[index - 1] if index else 0
            if filter_type == 0:
                decoded = value
            elif filter_type == 1:
                decoded = (value + left) & 0xFF
            elif filter_type == 2:
                decoded = (value + up) & 0xFF
            elif filter_type == 3:
                decoded = (value + ((left + up) // 2)) & 0xFF
            elif filter_type == 4:
                prediction = left + up - up_left
                distances = (abs(prediction - left), abs(prediction - up), abs(prediction - up_left))
                decoded = (value + (left, up, up_left)[distances.index(min(distances))]) & 0xFF
            else:
                raise ValueError("PNG uses an unsupported filter")
            row.append(decoded)
        previous = row
        indices = []
        for value in row:
            if depth == 4:
                indices.extend((value >> 4, value & 0x0F))
            else:
                indices.append(value)
        indices = indices[:width]
        if any(value >= len(palette) for value in indices):
            raise ValueError("PNG pixel index exceeds PLTE entries")
        rows.append(indices)
    return width, height, palette, rows


def _read_jasc_palette(path):
    with open(path, encoding="ascii", newline="") as handle:
        lines = [line.rstrip("\r\n") for line in handle]
    if len(lines) != 19 or lines[:2] != ["JASC-PAL", "0100"] or lines[2] != "16":
        raise ValueError("JASC-PAL sidecar must be JASC-PAL/0100 with exactly 16 colors")
    colors = []
    for line in lines[3:]:
        fields = line.split()
        if len(fields) != 3:
            raise ValueError("JASC-PAL color rows must contain red, green, and blue")
        color = tuple(int(field) for field in fields)
        if any(value < 0 or value > 255 for value in color):
            raise ValueError("JASC-PAL colors must be in the range 0..255")
        colors.append(color)
    return colors


def _pack_4bpp(rows, x, y, width, height):
    if width % 8 or height % 8:
        raise ValueError("4bpp component dimensions must be multiples of 8 pixels")
    output = bytearray()
    for tile_y in range(y, y + height, 8):
        for tile_x in range(x, x + width, 8):
            for row in rows[tile_y:tile_y + 8]:
                for column in range(tile_x, tile_x + 8, 2):
                    output.append(row[column] | (row[column + 1] << 4))
    return bytes(output)


def _gba_lz77(data):
    """Encode a deterministic GBA LZ77 stream for generated face/chibi data."""
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
            for candidate in range(start, position):
                length = 0
                while (
                    length < 18
                    and position + length < len(data)
                    and data[candidate + length] == data[position + length]
                ):
                    length += 1
                    if candidate + length >= position:
                        break
                if length > best_length:
                    best_length = length
                    best_distance = position - candidate
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


class ChapterMapLayoutKind:
    name = "chapter-map-layout"
    _options = {"format": "mar", "compression": "lz77"}
    _ownership = {
        "seam": "chapter-data-asset-table",
        "tableSource": "src/data/data_8B363C.c",
        "chapterSettings": "src/data/chapter_settings.json",
        "consumer": "GetChapterMapPointer",
    }
    _object = "src/data/data_8B363C.o"
    _modern_object = "$(MODERN_OUTPUT_DIR)/src/data/data_8B363C.o"
    _map_buffer_bytes = 0x800

    def validate(self, record, diagnostics):
        options_valid = _validate_exact_values(
            record.options,
            record.option_locs,
            self._options,
            diagnostics,
            record.loc,
            "{}.options".format(record.id),
        )
        ownership_valid = _validate_exact_values(
            record.ownership,
            record.ownership_locs,
            (
                "seam",
                "tableSource",
                "chapterSettings",
                "chapterSettingsIndex",
                "mainLayerId",
                "symbol",
                "consumer",
            ),
            diagnostics,
            record.loc,
            "{}.ownership".format(record.id),
        )
        resources_valid = _validate_exact_values(
            record.resources,
            record.resource_locs,
            ("mapWidth", "mapHeight", "mapBufferBytes"),
            diagnostics,
            record.loc,
            "{}.resources".format(record.id),
        )
        if not options_valid or not ownership_valid or not resources_valid:
            return
        for key, expected in self._options.items():
            if record.options[key] != expected:
                diagnostics.add(
                    GeneratedDataError(
                        "unsupported {} '{}'; expected '{}'".format(
                            key, record.options[key], expected
                        ),
                        record.option_locs[key],
                        "{}.options.{}".format(record.id, key),
                    )
                )
        for key, expected in self._ownership.items():
            if record.ownership[key] != expected:
                diagnostics.add(
                    GeneratedDataError(
                        "chapter-map-layout ownership.{} must be '{}'".format(key, expected),
                        record.ownership_locs[key],
                        "{}.ownership.{}".format(record.id, key),
                    )
                )
        if len(record.sources) != 2 or not record.sources[0].endswith(".mar") or not record.sources[1].endswith(".json"):
            diagnostics.add(
                GeneratedDataError(
                    "chapter-map-layout requires ordered .mar and .json source files",
                    record.loc,
                    "{}.sources".format(record.id),
                )
            )
            return

        width = record.resources["mapWidth"]
        height = record.resources["mapHeight"]
        buffer_bytes = record.resources["mapBufferBytes"]
        for key, value in (("mapWidth", width), ("mapHeight", height), ("mapBufferBytes", buffer_bytes)):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                diagnostics.add(
                    GeneratedDataError(
                        "resources.{} must be a positive integer".format(key),
                        record.resource_locs[key],
                        "{}.resources.{}".format(record.id, key),
                    )
                )
        if buffer_bytes != self._map_buffer_bytes:
            diagnostics.add(
                GeneratedDataError(
                    "resources.mapBufferBytes must state the runtime map buffer capacity {}".format(
                        self._map_buffer_bytes
                    ),
                    record.resource_locs["mapBufferBytes"],
                    "{}.resources.mapBufferBytes".format(record.id),
                )
            )
        if isinstance(width, int) and isinstance(height, int) and width * height + 2 > self._map_buffer_bytes:
            diagnostics.add(
                GeneratedDataError(
                    "map dimensions {}x{} exceed the {}-byte gBmMapBuffer contract".format(
                        width, height, self._map_buffer_bytes
                    ),
                    record.resource_locs["mapWidth"],
                    "{}.resources".format(record.id),
                )
            )

        try:
            with open(os.path.join(REPO_ROOT, record.sources[1]), encoding="utf-8") as handle:
                metadata = json.load(handle)
        except (OSError, ValueError) as exc:
            diagnostics.add(GeneratedDataError(str(exc), record.source_locs[1], "{}.sources[1]".format(record.id)))
            return
        if not isinstance(metadata, dict):
            diagnostics.add(GeneratedDataError(
                "map metadata must be a JSON object",
                record.source_locs[1],
                "{}.sources[1]".format(record.id),
            ))
            return
        if metadata.get("id") != record.ownership["symbol"]:
            diagnostics.add(
                GeneratedDataError(
                    "map metadata id '{}' does not match ownership symbol '{}'".format(
                        metadata.get("id"), record.ownership["symbol"]
                    ),
                    record.source_locs[1],
                    "{}.ownership.symbol".format(record.id),
                )
            )
        for key, expected in (("width", width), ("height", height)):
            if metadata.get(key) != expected:
                diagnostics.add(
                    GeneratedDataError(
                        "map metadata {} {} does not match declared resource {}".format(
                            key, metadata.get(key), expected
                        ),
                        record.source_locs[1],
                        "{}.resources.{}".format(record.id, "map" + key.title()),
                    )
                )

        index = record.ownership["chapterSettingsIndex"]
        slot = record.ownership["mainLayerId"]
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            diagnostics.add(GeneratedDataError(
                "chapterSettingsIndex must be a non-negative integer",
                record.ownership_locs["chapterSettingsIndex"],
                "{}.ownership.chapterSettingsIndex".format(record.id),
            ))
            return
        if not isinstance(slot, int) or isinstance(slot, bool) or slot < 0:
            diagnostics.add(GeneratedDataError(
                "mainLayerId must be a non-negative integer",
                record.ownership_locs["mainLayerId"],
                "{}.ownership.mainLayerId".format(record.id),
            ))
            return
        try:
            row = _chapter_settings_row(record.ownership["chapterSettings"], index)
        except (OSError, ValueError, TypeError) as exc:
            diagnostics.add(GeneratedDataError(
                str(exc),
                record.ownership_locs["chapterSettings"],
                "{}.ownership.chapterSettings".format(record.id),
            ))
            return
        if row is None or row.get("map", {}).get("mainLayerId") != slot:
            diagnostics.add(GeneratedDataError(
                "chapter settings index {} does not select mainLayerId {}".format(index, slot),
                record.ownership_locs["mainLayerId"],
                "{}.ownership.mainLayerId".format(record.id),
            ))
        try:
            entries = _chapter_table_entries(record.ownership["tableSource"])
        except (OSError, ValueError) as exc:
            diagnostics.add(GeneratedDataError(
                str(exc), record.ownership_locs["tableSource"], "{}.ownership.tableSource".format(record.id)
            ))
            return
        if slot >= len(entries) or entries[slot] != record.ownership["symbol"]:
            actual = entries[slot] if slot < len(entries) else "<out of range>"
            diagnostics.add(GeneratedDataError(
                "gChapterDataAssetTable[{}] is '{}', not '{}'".format(
                    slot, actual, record.ownership["symbol"]
                ),
                record.ownership_locs["mainLayerId"],
                "{}.ownership.mainLayerId".format(record.id),
            ))

    def ownership_key(self, record):
        return "{}:{}:{}".format(
            record.ownership["seam"], record.ownership["tableSource"],
            record.ownership["mainLayerId"],
        )

    def make_dependencies(self, record):
        return ((self._object, record.sources), (self._modern_object, record.sources))


class FormattedPortraitPackageKind:
    """Version-1 FE7/FE8 formatted-sheet adapter at the existing FaceData seam."""

    name = "formatted-portrait-package"
    _options = {
        "format": "fe7-fe8-formatted-png",
        "adapterVersion": 1,
        "jascSidecar": False,
    }
    _ownership = {
        "seam": "portrait-data-table",
        "tableSource": "src/portrait_data.c",
        "consumer": "GetPortraitData",
    }
    _object = "src/portrait_data.o"
    _data_object = "src/data/data_portrait.o"
    _modern_object = "$(MODERN_OUTPUT_DIR)/src/portrait_data.o"
    _modern_data_object = "$(MODERN_OUTPUT_DIR)/src/data/data_portrait.o"
    _frame_layout = {
        "main": (0, 0, 80, 72),
        "minimug": (80, 0, 32, 32),
        "eyeOpen": (0, 72, 32, 16),
        "eyeClosed": (32, 72, 32, 16),
        "mouthClosed": (64, 72, 32, 16),
        "mouthOpen": (96, 72, 32, 16),
    }
    _c_symbol_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def _validate_metadata(self, record, diagnostics):
        metadata_path = record.sources[1]
        try:
            with open(os.path.join(REPO_ROOT, metadata_path), encoding="utf-8") as handle:
                metadata = json.load(handle)
        except (OSError, ValueError) as exc:
            diagnostics.add(GeneratedDataError(
                "cannot load portrait metadata: {}".format(exc),
                record.source_locs[1],
                "{}.sources[1]".format(record.id),
            ))
            return None
        required = ("schemaVersion", "portraitId", "symbol", "blinkKind", "anchors", "frames", "alias")
        if not isinstance(metadata, dict) or set(metadata) != set(required):
            diagnostics.add(GeneratedDataError(
                "portrait metadata must contain exactly {}".format(", ".join(required)),
                record.source_locs[1],
                "{}.sources[1]".format(record.id),
            ))
            return None
        if not _has_exact_value(metadata["schemaVersion"], 1):
            diagnostics.add(GeneratedDataError(
                "portrait metadata schemaVersion must be 1",
                record.source_locs[1],
                "{}.sources[1].schemaVersion".format(record.id),
            ))
        if (
            not _is_int(metadata["portraitId"])
            or metadata["portraitId"] != record.ownership["portraitId"]
        ):
            diagnostics.add(GeneratedDataError(
                "portrait metadata portraitId must match ownership.portraitId",
                record.source_locs[1],
                "{}.sources[1].portraitId".format(record.id),
            ))
        if metadata["symbol"] != record.ownership["symbol"]:
            diagnostics.add(GeneratedDataError(
                "portrait metadata symbol must match ownership.symbol",
                record.source_locs[1],
                "{}.sources[1].symbol".format(record.id),
            ))
        if metadata["blinkKind"] not in ("FACE_BLINK_NORMAL", "FACE_BLINK_CLOSED"):
            diagnostics.add(GeneratedDataError(
                "portrait metadata blinkKind must be FACE_BLINK_NORMAL or FACE_BLINK_CLOSED",
                record.source_locs[1],
                "{}.sources[1].blinkKind".format(record.id),
            ))
        anchors = metadata["anchors"]
        if (
            not isinstance(anchors, dict)
            or set(anchors) != {"mouth", "eyes"}
            or any(
                not isinstance(value, list) or len(value) != 2
                or any(not _is_int(item) or item < 0 or item > 31 for item in value)
                for value in anchors.values()
            )
        ):
            diagnostics.add(GeneratedDataError(
                "portrait metadata anchors must contain 0..31 [x, y] mouth and eyes pairs",
                record.source_locs[1],
                "{}.sources[1].anchors".format(record.id),
            ))
        frames = metadata["frames"]
        if not isinstance(frames, dict) or set(frames) != set(self._frame_layout):
            diagnostics.add(GeneratedDataError(
                "portrait metadata frames must name the six canonical components",
                record.source_locs[1],
                "{}.sources[1].frames".format(record.id),
            ))
        elif any(
            not isinstance(frames[name], list)
            or len(frames[name]) != 4
            or any(not _is_int(value) for value in frames[name])
            or frames[name] != list(expected)
            for name, expected in self._frame_layout.items()
        ):
            diagnostics.add(GeneratedDataError(
                "portrait metadata frame geometry must equal the canonical 128x112 layout",
                record.source_locs[1],
                "{}.sources[1].frames".format(record.id),
            ))
        alias = metadata["alias"]
        if not isinstance(alias, dict) or set(alias) != {"mode", "components"}:
            diagnostics.add(GeneratedDataError(
                "portrait metadata alias must contain mode and components",
                record.source_locs[1],
                "{}.sources[1].alias".format(record.id),
            ))
        elif alias["mode"] not in ("generated", "existing-components"):
            diagnostics.add(GeneratedDataError(
                "portrait metadata alias.mode must be generated or existing-components",
                record.source_locs[1],
                "{}.sources[1].alias.mode".format(record.id),
            ))
        elif alias["mode"] == "existing-components":
            expected = {"img", "imgChibi", "pal", "imgMouth"}
            if (
                not isinstance(alias["components"], dict)
                or set(alias["components"]) != expected
                or any(
                    not isinstance(value, str) or not self._c_symbol_re.fullmatch(value)
                    for value in alias["components"].values()
                )
            ):
                diagnostics.add(GeneratedDataError(
                    "existing-components alias must name exact C identifiers for img, imgChibi, pal, and imgMouth",
                    record.source_locs[1],
                    "{}.sources[1].alias.components".format(record.id),
                ))
        elif alias["components"] != {}:
            diagnostics.add(GeneratedDataError(
                "generated alias must use an empty components object",
                record.source_locs[1],
                "{}.sources[1].alias.components".format(record.id),
            ))
        return metadata

    def validate(self, record, diagnostics):
        options_valid = _validate_exact_values(
            record.options, record.option_locs, self._options, diagnostics, record.loc,
            "{}.options".format(record.id),
        )
        ownership_valid = _validate_exact_values(
            record.ownership, record.ownership_locs,
            ("seam", "tableSource", "registrySource", "portraitId", "symbol", "consumer"),
            diagnostics, record.loc, "{}.ownership".format(record.id),
        )
        resources_valid = _validate_exact_values(
            record.resources, record.resource_locs,
            ("sheetWidth", "sheetHeight", "paletteColors", "mainBytes", "minimugBytes",
             "eyeFrameBytes", "mouthFrameBytes"),
            diagnostics, record.loc, "{}.resources".format(record.id),
        )
        if not options_valid or not ownership_valid or not resources_valid:
            return
        for key, expected in self._options.items():
            if key == "jascSidecar":
                if not isinstance(record.options[key], bool):
                    diagnostics.add(GeneratedDataError(
                        "formatted-portrait-package options.jascSidecar must be a boolean",
                        record.option_locs[key], "{}.options.{}".format(record.id, key),
                    ))
                continue
            if not _has_exact_value(record.options[key], expected):
                diagnostics.add(GeneratedDataError(
                    "formatted-portrait-package options.{} must be {!r}".format(key, expected),
                    record.option_locs[key], "{}.options.{}".format(record.id, key),
                ))
        for key, expected in self._ownership.items():
            if not _has_exact_value(record.ownership[key], expected):
                diagnostics.add(GeneratedDataError(
                    "formatted-portrait-package ownership.{} must be {!r}".format(key, expected),
                    record.ownership_locs[key], "{}.ownership.{}".format(record.id, key),
                ))
        portrait_id = record.ownership["portraitId"]
        if not _is_int(portrait_id) or not 1 <= portrait_id <= 0xAC:
            diagnostics.add(GeneratedDataError(
                "ownership.portraitId must be an existing full portrait ID in 1..172",
                record.ownership_locs["portraitId"], "{}.ownership.portraitId".format(record.id),
            ))
        if not isinstance(record.ownership["symbol"], str) or not self._c_symbol_re.fullmatch(
            record.ownership["symbol"]
        ):
            diagnostics.add(GeneratedDataError(
                "ownership.symbol must be a C identifier",
                record.ownership_locs["symbol"], "{}.ownership.symbol".format(record.id),
            ))
        try:
            record.ownership["registrySource"] = _repo_path(
                record.ownership["registrySource"],
                record.ownership_locs["registrySource"],
                "{}.ownership.registrySource".format(record.id),
            )
        except GeneratedDataError as exc:
            diagnostics.add(exc)
        expected_resources = {
            "sheetWidth": 128, "sheetHeight": 112, "paletteColors": 16, "mainBytes": 2880,
            "minimugBytes": 512, "eyeFrameBytes": 256, "mouthFrameBytes": 256,
        }
        for key, expected in expected_resources.items():
            if not _has_exact_value(record.resources[key], expected):
                diagnostics.add(GeneratedDataError(
                    "resources.{} must be {}".format(key, expected),
                    record.resource_locs[key], "{}.resources.{}".format(record.id, key),
                ))
        expected_sources = 3 if record.options["jascSidecar"] else 2
        if (
            len(record.sources) != expected_sources
            or not record.sources[0].endswith(".png")
            or os.path.basename(record.sources[1]) != "metadata.json"
        ):
            diagnostics.add(GeneratedDataError(
                "formatted-portrait-package requires ordered sheet PNG and metadata.json sources",
                record.loc, "{}.sources".format(record.id),
            ))
            return
        sheet_dir = os.path.dirname(record.sources[0])
        if os.path.dirname(record.sources[1]) != sheet_dir:
            diagnostics.add(GeneratedDataError(
                "portrait sheet and metadata must be in the same package directory",
                record.loc, "{}.sources".format(record.id),
            ))
        if record.options["jascSidecar"]:
            expected_sidecar = os.path.splitext(record.sources[0])[0] + ".pal"
            if record.sources[2] != expected_sidecar:
                diagnostics.add(GeneratedDataError(
                    "JASC-PAL sidecar must be name-matched to the portrait sheet",
                    record.source_locs[2], "{}.sources[2]".format(record.id),
                ))
        try:
            package_files = os.listdir(os.path.join(REPO_ROOT, sheet_dir))
            package_pngs = [name for name in package_files if name.lower().endswith(".png")]
            if package_pngs != [os.path.basename(record.sources[0])]:
                raise ValueError("package directory must contain exactly the declared portrait sheet PNG")
            width, height, palette, _ = _read_png(os.path.join(REPO_ROOT, record.sources[0]))
            if (width, height) != (128, 112):
                raise ValueError("portrait sheet must be exactly 128x112 pixels")
            if record.options["jascSidecar"] and _read_jasc_palette(
                os.path.join(REPO_ROOT, record.sources[2])
            ) != palette + [(0, 0, 0)] * (16 - len(palette)):
                raise ValueError("JASC-PAL colors must equal the PNG palette")
        except (OSError, ValueError, zlib.error) as exc:
            diagnostics.add(GeneratedDataError(
                "invalid formatted portrait package: {}".format(exc),
                record.source_locs[0], "{}.sources".format(record.id),
            ))
        self._validate_metadata(record, diagnostics)

    def ownership_key(self, record):
        return "{}:{}:{}".format(
            record.ownership["seam"], record.ownership["tableSource"], record.ownership["portraitId"]
        )

    def make_dependencies(self, record):
        sources = tuple(record.sources) + (record.ownership["registrySource"],)
        return (
            (self._object, sources),
            (self._data_object, sources),
            (self._modern_object, sources),
            (self._modern_data_object, sources),
        )


class KindRegistry:
    """The sole static extension seam for asset kinds."""

    def __init__(self):
        self._kinds = {}

    def register(self, kind):
        if kind.name in self._kinds:
            raise ValueError("duplicate asset kind '{}'".format(kind.name))
        self._kinds[kind.name] = kind

    def resolve(self, name):
        return self._kinds.get(name)


KIND_REGISTRY = KindRegistry()
KIND_REGISTRY.register(ChapterMapLayoutKind())
KIND_REGISTRY.register(FormattedPortraitPackageKind())


def validate(records):
    diagnostics = DiagnosticCollector()
    by_id = {}
    ownership = {}
    sources = {}
    for record in records:
        if not ID_RE.fullmatch(record.id):
            diagnostics.add(GeneratedDataError(
                "asset id must match {}".format(ID_RE.pattern), record.id_loc, "asset.id"
            ))
        if record.id in by_id:
            diagnostics.add(GeneratedDataError(
                "duplicate asset id '{}' (first defined at {})".format(record.id, by_id[record.id].id_loc),
                record.id_loc, "asset.id",
            ))
        else:
            by_id[record.id] = record
        kind = KIND_REGISTRY.resolve(record.kind)
        if kind is None:
            diagnostics.add(GeneratedDataError(
                "unknown asset kind '{}'; registered kinds: {}".format(
                    record.kind, ", ".join(sorted(KIND_REGISTRY._kinds))
                ),
                record.kind_loc, "{}.kind".format(record.id),
            ))
            continue
        if not record.sources:
            diagnostics.add(GeneratedDataError("asset requires at least one source", record.loc, "{}.sources".format(record.id)))
        normalized_sources = []
        for path, loc in zip(record.sources, record.source_locs):
            try:
                normalized = _repo_path(path, loc, "{}.sources".format(record.id))
                normalized_sources.append(normalized)
                canonical = _canonical_source_key(normalized)
                if canonical in sources:
                    diagnostics.add(GeneratedDataError(
                        "source path collision '{}' with asset '{}' source '{}'".format(
                            normalized, sources[canonical][0].id, sources[canonical][1]
                        ),
                        loc,
                        "{}.sources".format(record.id),
                    ))
                else:
                    sources[canonical] = (record, normalized)
            except GeneratedDataError as exc:
                diagnostics.add(exc)
        if len(normalized_sources) == len(record.sources):
            record.sources = normalized_sources
        _validate_provenance(record, diagnostics)
        kind.validate(record, diagnostics)
        try:
            key = kind.ownership_key(record)
        except (KeyError, TypeError) as exc:
            diagnostics.add(GeneratedDataError(
                "cannot derive ownership key: {}".format(exc),
                record.loc,
                "{}.ownership".format(record.id),
            ))
            continue
        if key in ownership:
            diagnostics.add(GeneratedDataError(
                "ownership conflict '{}' with asset '{}'".format(key, ownership[key].id),
                record.ownership_locs.get("mainLayerId", record.loc),
                "{}.ownership".format(record.id),
            ))
        else:
            ownership[key] = record
    portrait_records = _portrait_records(records)
    if portrait_records:
        registry_paths = {record.ownership.get("registrySource") for record in portrait_records}
        if len(registry_paths) != 1 or None in registry_paths:
            diagnostics.add(GeneratedDataError(
                "formatted portrait packages must share exactly one registrySource",
                portrait_records[0].loc,
                "formatted-portrait-package.ownership.registrySource",
            ))
        else:
            try:
                entries = _read_portrait_registry(next(iter(registry_paths)))
                for record in portrait_records:
                    portrait_id = record.ownership["portraitId"]
                    if portrait_id not in entries:
                        diagnostics.add(GeneratedDataError(
                            "ownership.portraitId {} is absent from the live generated FaceData registry".format(
                                portrait_id
                            ),
                            record.ownership_locs["portraitId"],
                            "{}.ownership.portraitId".format(record.id),
                        ))
                        continue
                    metadata = _read_portrait_metadata(record)
                    entry = entries[portrait_id]
                    anchors = metadata.get("anchors") if isinstance(metadata, dict) else None
                    alias = metadata.get("alias") if isinstance(metadata, dict) else None
                    if (
                        not isinstance(anchors, dict)
                        or not isinstance(anchors.get("mouth"), list)
                        or not isinstance(anchors.get("eyes"), list)
                        or len(anchors["mouth"]) != 2
                        or len(anchors["eyes"]) != 2
                    ):
                        continue
                    if isinstance(alias, dict) and alias.get("mode") == "existing-components" and isinstance(
                        alias.get("components"), dict
                    ):
                        if (
                            entry["xMouth"], entry["yMouth"], entry["xEyes"], entry["yEyes"],
                            entry["blinkKind"],
                        ) != (
                            anchors["mouth"][0], anchors["mouth"][1],
                            anchors["eyes"][0], anchors["eyes"][1],
                            metadata["blinkKind"],
                        ):
                            diagnostics.add(GeneratedDataError(
                                "existing-components metadata anchors/blinkKind must match "
                                "the canonical FaceData registry entry",
                                record.source_locs[1],
                                "{}.sources[1]".format(record.id),
                            ))
                        for field, symbol in alias["components"].items():
                            if field not in entry or entry[field] != symbol:
                                diagnostics.add(GeneratedDataError(
                                    "existing-components alias.{} must match canonical FaceData symbol '{}'".format(
                                        field, entry.get(field, "<unknown>")
                                    ),
                                    record.source_locs[1],
                                    "{}.sources[1]".format(record.id),
                                ))
            except (OSError, ValueError) as exc:
                diagnostics.add(GeneratedDataError(
                    "invalid portrait registry: {}".format(exc),
                    portrait_records[0].ownership_locs["registrySource"],
                    "{}.ownership.registrySource".format(portrait_records[0].id),
                ))
    for record in records:
        for dependency, loc in zip(record.depends_on, record.dependency_locs):
            if dependency == record.id:
                diagnostics.add(GeneratedDataError(
                    "asset cannot depend on itself", loc, "{}.dependsOn".format(record.id)
                ))
            elif dependency not in by_id:
                diagnostics.add(GeneratedDataError(
                    "dangling dependency '{}'".format(dependency), loc, "{}.dependsOn".format(record.id)
                ))
    _validate_dependencies(records, by_id, diagnostics)
    diagnostics.raise_if_any()
    return records


def _validate_dependencies(records, by_id, diagnostics):
    state = {}
    stack = []

    def visit(record):
        state[record.id] = "visiting"
        stack.append(record)
        seen_dependencies = set()
        for dependency, loc in zip(record.depends_on, record.dependency_locs):
            if dependency in seen_dependencies:
                diagnostics.add(GeneratedDataError(
                    "duplicate dependency '{}'".format(dependency),
                    loc,
                    "{}.dependsOn".format(record.id),
                ))
                continue
            seen_dependencies.add(dependency)
            target = by_id.get(dependency)
            if target is None:
                continue
            if state.get(target.id) == "visiting":
                start = next(index for index, item in enumerate(stack) if item.id == target.id)
                cycle = [item.id for item in stack[start:]] + [target.id]
                diagnostics.add(GeneratedDataError(
                    "dependency cycle {}".format(" -> ".join(cycle)),
                    loc,
                    "{}.dependsOn".format(record.id),
                ))
            elif state.get(target.id) is None:
                visit(target)
        stack.pop()
        state[record.id] = "done"

    for record in records:
        if state.get(record.id) is None:
            visit(record)


def load_and_validate(path):
    return validate(load_manifest(path))


def render_makefile(records):
    lines = [
        "# AUTO-GENERATED by scripts.assets -- DO NOT EDIT BY HAND.\n",
        "# Source: assets/manifest.json\n",
        "# This is an ordinary Make dependency fragment, not a runtime registry.\n",
        "\n",
    ]
    by_id = {record.id: record for record in records}

    def dependency_sources(record):
        sources = []
        visited = set()

        def visit(dependency_id):
            if dependency_id in visited:
                return
            visited.add(dependency_id)
            dependency = by_id.get(dependency_id)
            if dependency is None:
                return
            for nested_id in dependency.depends_on:
                visit(nested_id)
            sources.extend(dependency.sources)

        for dependency_id in record.depends_on:
            visit(dependency_id)
        return sources

    for record in records:
        kind = KIND_REGISTRY.resolve(record.kind)
        for target, sources in kind.make_dependencies(record):
            prerequisites = list(sources) + dependency_sources(record)
            lines.append("{}: {}\n".format(target, " ".join(prerequisites)))
    return "".join(lines)


def render_inventory(records):
    lines = [
        "# Asset manifest inventory\n",
        "\n",
        "_Auto-generated by `python3 -m scripts.assets generate`; do not edit by hand._\n",
        "\n",
        "| ID | Kind | Sources | Ownership seam | Runtime consumer |\n",
        "|---|---|---|---|---|\n",
    ]
    for record in records:
        lines.append(
            "| {} | {} | {} | {} | {} |\n".format(
                record.id, record.kind, "<br>".join(record.sources),
                record.ownership["seam"], record.ownership["consumer"],
            )
        )
    return "".join(lines)


def _portrait_records(records):
    return [
        record for record in records
        if record.kind == FormattedPortraitPackageKind.name
    ]


def _read_portrait_metadata(record):
    with open(os.path.join(REPO_ROOT, record.sources[1]), encoding="utf-8") as handle:
        return json.load(handle)


def _read_portrait_registry(path):
    with open(os.path.join(REPO_ROOT, path), encoding="utf-8") as handle:
        document = json.load(handle)
    required = {"schemaVersion", "entries"}
    if (
        not isinstance(document, dict)
        or set(document) != required
        or not _has_exact_value(document["schemaVersion"], 1)
    ):
        raise ValueError("portrait registry must be a version-1 object with entries")
    if not isinstance(document["entries"], list):
        raise ValueError("portrait registry entries must be a list")
    required_entry = {
        "id", "img", "imgChibi", "pal", "imgMouth", "imgCard",
        "xMouth", "yMouth", "xEyes", "yEyes", "blinkKind",
    }
    c_symbol_or_null_re = re.compile(r"^(?:0|[A-Za-z_][A-Za-z0-9_]*)$")
    entries = {}
    for entry in document["entries"]:
        if not isinstance(entry, dict) or set(entry) != required_entry:
            raise ValueError("portrait registry entries must use the exact FaceData schema")
        portrait_id = entry["id"]
        if not _is_int(portrait_id) or portrait_id in entries:
            raise ValueError("portrait registry IDs must be unique integers")
        fields = ("img", "imgChibi", "pal", "imgMouth")
        if any(
            not isinstance(entry[field], str) or not c_symbol_or_null_re.fullmatch(entry[field])
            for field in fields
        ):
            raise ValueError("portrait registry image/palette symbols must be C identifiers or 0")
        if entry["imgCard"] is not None and (
            not isinstance(entry["imgCard"], str)
            or not c_symbol_or_null_re.fullmatch(entry["imgCard"])
        ):
            raise ValueError("portrait registry imgCard must be a C identifier, 0, or null")
        if entry["blinkKind"] not in ("FACE_BLINK_NORMAL", "FACE_BLINK_CLOSED"):
            raise ValueError("portrait registry blinkKind is invalid")
        if any(
            not _is_int(entry[field]) or not 0 <= entry[field] <= 31
            for field in ("xMouth", "yMouth", "xEyes", "yEyes")
        ):
            raise ValueError("portrait registry anchors must be integers in 0..31")
        entries[portrait_id] = entry
    if sorted(entries) != list(range(1, 0xAC + 1)):
        raise ValueError("portrait registry IDs must form the complete 1..172 FaceData table")
    return entries


def portrait_registration_ids(manifest_path=os.path.join(REPO_ROOT, "assets", "manifest.json")):
    """Return the live generated FaceData IDs from the sole asset manifest."""
    records = load_and_validate(manifest_path)
    portrait_records = _portrait_records(records)
    registry_paths = {record.ownership["registrySource"] for record in portrait_records}
    if len(registry_paths) > 1:
        raise GeneratedDataError("formatted portrait packages must share one portrait registry source")
    try:
        entries = _read_portrait_registry(
            next(iter(registry_paths), "assets/portrait_registry.json")
        )
    except (OSError, ValueError) as exc:
        raise GeneratedDataError("invalid portrait registry: {}".format(exc))
    return tuple(sorted(entries))


def render_portrait_data(records):
    portrait_records = _portrait_records(records)
    registry_paths = {record.ownership["registrySource"] for record in portrait_records}
    if len(registry_paths) > 1:
        raise ValueError("formatted portrait packages must share one portrait registry source")
    entries = _read_portrait_registry(
        next(iter(registry_paths), "assets/portrait_registry.json")
    )
    for record in portrait_records:
        metadata = _read_portrait_metadata(record)
        entry = entries[record.ownership["portraitId"]].copy()
        entry["xMouth"], entry["yMouth"] = metadata["anchors"]["mouth"]
        entry["xEyes"], entry["yEyes"] = metadata["anchors"]["eyes"]
        entry["blinkKind"] = metadata["blinkKind"]
        if metadata["alias"]["mode"] == "existing-components":
            entry.update(metadata["alias"]["components"])
        else:
            prefix = "portrait_{}".format(record.ownership["symbol"])
            entry.update({
                "img": prefix + "_tileset",
                "imgChibi": prefix + "_chibi",
                "pal": prefix + "_palette",
                "imgMouth": prefix + "_mouth",
            })
        entries[record.ownership["portraitId"]] = entry
    lines = [
        "/* AUTO-GENERATED by scripts.assets -- DO NOT EDIT BY HAND. */\n",
        "#include \"global.h\"\n\n",
        "#include \"portrait_pointer.h\"\n",
        "#include \"build/generated/assets/portrait_components.h\"\n\n",
        "struct FaceData CONST_DATA portrait_data[] =\n{\n",
    ]
    for portrait_id in sorted(entries):
        entry = entries[portrait_id]
        card = entry["imgCard"] if entry["imgCard"] is not None else "0"
        lines.append(
            "    {{{img}, {imgChibi}, {pal}, {imgMouth}, {card}, {xMouth}, {yMouth}, "
            "{xEyes}, {yEyes}, {blinkKind}}}, // {index}\n".format(
                index=portrait_id - 1, card=card, **entry
            )
        )
    lines.append("};\n")
    return "".join(lines)


def _portrait_component_files(record):
    return {
        "main": os.path.join("portraits", record.id, "main.4bpp"),
        "chibi": os.path.join("portraits", record.id, "minimug.4bpp"),
        "eyes": os.path.join("portraits", record.id, "eyes.4bpp"),
        "mouth": os.path.join("portraits", record.id, "mouth.4bpp"),
        "palette": os.path.join("portraits", record.id, "palette.agbpal"),
        "tileset_lz": os.path.join("portraits", record.id, "tileset.4bpp.lz"),
        "chibi_lz": os.path.join("portraits", record.id, "minimug.4bpp.lz"),
    }


def render_portrait_components(records):
    lines = ["/* AUTO-GENERATED by scripts.assets -- DO NOT EDIT BY HAND. */\n"]
    for record in _portrait_records(records):
        metadata = _read_portrait_metadata(record)
        if metadata["alias"]["mode"] == "existing-components":
            continue
        prefix = "portrait_{}".format(record.ownership["symbol"])
        files = _portrait_component_files(record)
        lines.extend([
            "u8 __attribute__((aligned(4))) {}_tileset[] = INCBIN_U8(\"build/generated/assets/{}\");\n".format(
                prefix, files["tileset_lz"]
            ),
            "u8 __attribute__((aligned(4))) {}_chibi[] = INCBIN_U8(\"build/generated/assets/{}\");\n".format(
                prefix, files["chibi_lz"]
            ),
            "u8 __attribute__((aligned(4))) {}_mouth[] = INCBIN_U8(\"build/generated/assets/{}\");\n".format(
                prefix, files["mouth"]
            ),
            "u16 {}_palette[] = INCBIN_U16(\"build/generated/assets/{}\");\n".format(
                prefix, files["palette"]
            ),
        ])
    return "".join(lines)


def render_portrait_symbols(records):
    lines = [
        "/* AUTO-GENERATED by scripts.assets -- DO NOT EDIT BY HAND. */\n",
        "#ifndef GUARD_GENERATED_ASSET_PORTRAIT_COMPONENTS_H\n",
        "#define GUARD_GENERATED_ASSET_PORTRAIT_COMPONENTS_H\n\n",
    ]
    for record in _portrait_records(records):
        metadata = _read_portrait_metadata(record)
        if metadata["alias"]["mode"] == "existing-components":
            continue
        prefix = "portrait_{}".format(record.ownership["symbol"])
        lines.extend([
            "extern unsigned char {}_tileset[];\n".format(prefix),
            "extern unsigned char {}_chibi[];\n".format(prefix),
            "extern unsigned char {}_mouth[];\n".format(prefix),
            "extern unsigned short {}_palette[];\n".format(prefix),
        ])
    lines.append("\n#endif\n")
    return "".join(lines)


def portrait_component_outputs(records, out_dir):
    outputs = {}
    for record in _portrait_records(records):
        metadata = _read_portrait_metadata(record)
        if metadata["alias"]["mode"] == "existing-components":
            continue
        width, height, palette, rows = _read_png(os.path.join(REPO_ROOT, record.sources[0]))
        del width, height
        frames = FormattedPortraitPackageKind._frame_layout
        main = _pack_4bpp(rows, *frames["main"])
        chibi = _pack_4bpp(rows, *frames["minimug"])
        eyes = _pack_4bpp(rows, *frames["eyeOpen"]) + _pack_4bpp(rows, *frames["eyeClosed"])
        mouth = _pack_4bpp(rows, *frames["mouthClosed"]) + _pack_4bpp(rows, *frames["mouthOpen"])
        files = _portrait_component_files(record)
        palette_bytes = bytearray()
        for red, green, blue in palette:
            value = ((red >> 3) | ((green >> 3) << 5) | ((blue >> 3) << 10))
            palette_bytes.extend(value.to_bytes(2, "little"))
        palette_bytes.extend(b"\0" * (32 - len(palette_bytes)))
        # The face renderer decompresses img; it reads eye tiles after the
        # 80x72 main image, so the generated stream packs them contiguously.
        for name, data in (
            (files["main"], main), (files["chibi"], chibi), (files["eyes"], eyes),
            (files["mouth"], mouth), (files["palette"], bytes(palette_bytes)),
            (files["tileset_lz"], _gba_lz77(main + eyes)),
            (files["chibi_lz"], _gba_lz77(chibi)),
        ):
            outputs[os.path.join(out_dir, name)] = data
    return outputs


def expected_outputs(records, out_dir):
    out_dir = safe_output_dir(out_dir)
    outputs = {
        os.path.join(out_dir, OUTPUT_MAKEFILE): render_makefile(records),
        os.path.join(out_dir, OUTPUT_INVENTORY): render_inventory(records),
        os.path.join(out_dir, OUTPUT_PORTRAIT_DATA): render_portrait_data(records),
        os.path.join(out_dir, OUTPUT_PORTRAIT_COMPONENTS): render_portrait_components(records),
        os.path.join(out_dir, OUTPUT_PORTRAIT_SYMBOLS): render_portrait_symbols(records),
    }
    outputs.update(portrait_component_outputs(records, out_dir))
    return outputs


def generate(manifest_path, out_dir):
    records = load_and_validate(manifest_path)
    for path, content in expected_outputs(records, out_dir).items():
        if isinstance(content, bytes):
            _write_bytes_if_changed(path, content)
        else:
            _write_if_changed(path, content)
    return records


def check(manifest_path, out_dir):
    records = load_and_validate(manifest_path)
    out_dir = safe_output_dir(out_dir)
    expected = expected_outputs(records, out_dir)
    errors = []
    for path, content in expected.items():
        if not os.path.isfile(path):
            errors.append("missing generated output {}".format(path))
            continue
        if isinstance(content, bytes):
            with open(path, "rb") as handle:
                actual = handle.read()
        else:
            with open(path, encoding="utf-8") as handle:
                actual = handle.read()
        if actual != content:
            errors.append("stale generated output {}".format(path))
    if os.path.isdir(out_dir):
        for root, _, files in os.walk(out_dir):
            for filename in files:
                path = os.path.join(root, filename)
                if path not in expected:
                    errors.append("orphan generated output {}".format(path))
    if errors:
        raise GeneratedDataValidationError([GeneratedDataError(error) for error in errors])
    return records
