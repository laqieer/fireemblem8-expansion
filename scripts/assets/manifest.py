"""Strict schema, validation, and deterministic rendering for asset manifests."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import struct
import unicodedata
import zlib

from scripts.assets import tmx
from scripts.generated_data.diagnostics import (
    DiagnosticCollector,
    GeneratedDataError,
    GeneratedDataValidationError,
)
from scripts.generated_data.json_loader import load_json_file

from . import banim, custom_spell


SCHEMA_VERSION = 1
OUTPUT_MAKEFILE = "asset_manifest.mk"
OUTPUT_INVENTORY = "asset_inventory.md"
OUTPUT_PORTRAIT_DATA = "portrait_data.inc"
OUTPUT_PORTRAIT_COMPONENTS = "portrait_components.inc"
OUTPUT_PORTRAIT_SYMBOLS = "portrait_components.h"
ATOMIC_WRITE_TEMP_PREFIX = ".asset-manifest-write-"
GENERATION_LOCK_SUFFIX = ".asset-manifest-generate.lock"
OUTPUT_NAMES = (
    OUTPUT_MAKEFILE,
    OUTPUT_INVENTORY,
    OUTPUT_PORTRAIT_DATA,
    OUTPUT_PORTRAIT_COMPONENTS,
    OUTPUT_PORTRAIT_SYMBOLS,
)
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ASSET_BUILD_ROOT = os.path.abspath(os.path.join(REPO_ROOT, "build"))
# Keep the source-root anchor without probing the mutable output tree on import.
ASSET_BUILD_ROOT_REAL = os.path.join(os.path.realpath(REPO_ROOT), "build")
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
        "provenance", "provenance_locs", "loc", "banim_package",
        "custom_spell_package", "custom_spell_fallback_id",
        "custom_spell_item_type", "custom_spell_render",
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
        self.banim_package = None
        self.custom_spell_package = None
        self.custom_spell_fallback_id = None
        self.custom_spell_item_type = None
        self.custom_spell_render = None


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


def load_discovery(path, *, tracked_sources=None):
    """Validate discovery using Git, or an already captured tracked-source set."""
    if tracked_sources is not None and (
        not isinstance(tracked_sources, (set, frozenset))
        or any(
            not isinstance(source, str) or not source or os.path.isabs(source)
            or "\\" in source or any(part in ("", ".", "..") for part in source.split("/"))
            or any(ord(character) < 32 or ord(character) == 127 for character in source)
            for source in tracked_sources
        )
    ):
        raise GeneratedDataError("captured tracked sources must be a set of canonical repository paths")
    records = load_manifest(path)
    diagnostics = DiagnosticCollector()
    tracked_paths = []
    for record in records:
        if not ID_RE.fullmatch(record.id):
            diagnostics.add(GeneratedDataError(
                "asset id must match {}".format(ID_RE.pattern),
                record.id_loc,
                "asset.id",
            ))
        kind = KIND_REGISTRY.resolve(record.kind)
        if kind is None:
            diagnostics.add(GeneratedDataError(
                "unknown asset kind '{}'".format(record.kind),
                record.kind_loc,
                "{}.kind".format(record.id),
            ))
            continue
        for source, loc in zip(record.sources, record.source_locs):
            try:
                tracked_paths.append(
                    (
                        _repo_path(
                            source,
                            loc,
                            "{}.sources".format(record.id),
                            verify_tracked=False,
                        ),
                        loc,
                        "{}.sources".format(record.id),
                    )
                )
            except GeneratedDataError as error:
                diagnostics.add(error)
        _validate_discovery_source_dependencies(
            kind, record, diagnostics, tracked_paths
        )
    diagnostics.raise_if_any()
    tracked = {path for path, _loc, _reference in tracked_paths}
    for source in discovery_sources(records):
        if source in tracked:
            continue
        try:
            tracked_paths.append(
                (
                    _repo_path(
                        source,
                        None,
                        "discovery source dependency",
                        verify_tracked=False,
                    ),
                    None,
                    "discovery source dependency",
                )
            )
            tracked.add(source)
        except GeneratedDataError as error:
            diagnostics.add(error)
    _validate_tracked_paths(tracked_paths, diagnostics, tracked_sources=tracked_sources)
    diagnostics.raise_if_any()
    return records


def discovery_sources(records):
    sources = {"assets/portrait_registry.json"}
    for record in records:
        sources.update(record.sources)
        kind = KIND_REGISTRY.resolve(record.kind)
        if kind is not None:
            sources.update(kind.source_dependencies(record))
    return tuple(sorted(sources))


def render_source_stamp(records):
    entries = []
    for source in discovery_sources(records):
        source_path = os.path.join(REPO_ROOT, source)
        entries.append(
            {
                "mtime_ns": os.stat(source_path).st_mtime_ns,
                "path": source,
            }
        )
    return json.dumps({"sources": entries}, indent=2, sort_keys=True) + "\n"


def render_discovery_makefile(records):
    source_stamp = render_source_stamp(records).encode("utf-8")
    groups = (
        ("ASSET_PORTRAIT_INCBIN_CONSUMERS", portrait_incbin_consumer_ids(records)),
        ("ASSET_TMX_INCBIN_CONSUMERS", tmx_incbin_consumer_ids(records)),
        ("ASSET_BANIM_INCBIN_CONSUMERS", banim_incbin_consumer_ids(records)),
        (
            "ASSET_CUSTOM_SPELL_INCBIN_CONSUMERS",
            custom_spell_incbin_consumer_ids(records),
        ),
    )
    lines = [
        "# AUTO-GENERATED by scripts.assets -- DO NOT EDIT BY HAND.\n",
        "ASSET_MANIFEST_SOURCE_DIGEST := {}\n".format(
            hashlib.sha256(source_stamp).hexdigest()
        ),
    ]
    lines.extend(
        "{} := {}\n".format(name, " ".join(values)) for name, values in groups
    )
    return "".join(lines)





def write_discovery_makefile(manifest_path, path):
    records = load_discovery(manifest_path)
    destination = _safe_output_path(path, ASSET_BUILD_ROOT)
    _write_if_changed(destination, render_discovery_makefile(records))
    return records


def _validate_discovery_source_dependencies(kind, record, diagnostics, tracked_paths):
    for field in getattr(kind, "_discovery_ownership_source_fields", ()):
        reference = "{}.ownership.{}".format(record.id, field)
        location = record.ownership_locs.get(field, record.loc)
        if field not in record.ownership:
            diagnostics.add(
                GeneratedDataError(
                    "missing ownership field '{}' required for dependency discovery".format(
                        field
                    ),
                    location,
                    reference,
                )
            )
            continue
        try:
            tracked_paths.append(
                (
                    _repo_path(
                        record.ownership[field],
                        location,
                        reference,
                        verify_tracked=False,
                    ),
                    location,
                    reference,
                )
            )
        except GeneratedDataError as error:
            diagnostics.add(error)


def _validate_tracked_paths(paths, diagnostics, *, tracked_sources=None):
    if not paths:
        return
    requested = sorted({path for path, _loc, _reference in paths})
    if tracked_sources is not None:
        for path, loc, reference in paths:
            if path not in tracked_sources:
                diagnostics.add(GeneratedDataError(
                    "declared source '{}' is not a tracked committed source".format(path),
                    loc,
                    reference,
                ))
        return
    checkout = subprocess.run(
        ["git", "-C", REPO_ROOT, "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if checkout.returncode or os.path.realpath(
            checkout.stdout.decode("utf-8", "replace").strip()
        ) != os.path.realpath(REPO_ROOT):
        # Source-archive and sandbox fixtures can be nested under an
        # unrelated outer checkout. Preserve path existence validation there
        # without accidentally querying the outer repository's index.
        for path, loc, reference in paths:
            if not os.path.exists(os.path.join(REPO_ROOT, path)):
                diagnostics.add(
                    GeneratedDataError(
                        "declared source '{}' does not exist".format(path),
                        loc,
                        reference,
                    )
                )
        return
    result = subprocess.run(
        ["git", "-C", REPO_ROOT, "ls-files", "-z", "--", *requested],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode:
        diagnostics.add(
            GeneratedDataError(
                "cannot verify tracked manifest sources: {}".format(
                    result.stderr.decode("utf-8", "replace").strip()
                )
            )
        )
        return
    tracked = {
        path.decode("utf-8")
        for path in result.stdout.split(b"\0")
        if path
    }
    for path, loc, reference in paths:
        if path not in tracked:
            diagnostics.add(
                GeneratedDataError(
                    "declared source '{}' is not a tracked committed source".format(
                        path
                    ),
                    loc,
                    reference,
                )
            )


def _repo_path(path, loc, reference_path, verify_tracked=True):
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
    if verify_tracked:
        diagnostics = DiagnosticCollector()
        _validate_tracked_paths([(normalized, loc, reference_path)], diagnostics)
        diagnostics.raise_if_any()
    return normalized


def _canonical_source_key(path):
    return unicodedata.normalize("NFC", path).casefold()


def safe_output_dir(path):
    requested = os.path.abspath(path)
    if os.path.commonpath((ASSET_BUILD_ROOT, requested)) != ASSET_BUILD_ROOT:
        raise GeneratedDataError(
            "generated output directory '{}' must stay under {}".format(path, ASSET_BUILD_ROOT)
        )
    relative = os.path.relpath(requested, REPO_ROOT)
    components = relative.split(os.sep)
    if not any(
        components[index:index + 2] == ["generated", "assets"]
        for index in range(len(components) - 1)
    ):
        raise GeneratedDataError(
            "generated output directory '{}' must stay under a build generated/assets root".format(
                path
            )
        )
    current = REPO_ROOT
    for component in relative.split(os.sep):
        current = os.path.join(current, component)
        if os.path.islink(current):
            raise GeneratedDataError(
                "generated output directory '{}' must not traverse a symbolic link".format(path)
            )
    resolved = os.path.realpath(requested)
    if os.path.commonpath((ASSET_BUILD_ROOT_REAL, resolved)) != ASSET_BUILD_ROOT_REAL:
        raise GeneratedDataError(
            "generated output directory '{}' must stay under {}".format(path, ASSET_BUILD_ROOT)
        )
    return requested


def _safe_output_path(path, out_dir):
    requested = os.path.abspath(path)
    if os.path.commonpath((out_dir, requested)) != out_dir:
        raise GeneratedDataError(
            "generated output '{}' must stay under {}".format(path, out_dir)
        )
    relative = os.path.relpath(requested, out_dir)
    current = out_dir
    for component in relative.split(os.sep):
        current = os.path.join(current, component)
        if os.path.islink(current):
            raise GeneratedDataError(
                "generated output '{}' must not traverse a symbolic link".format(path)
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
        prefix=ATOMIC_WRITE_TEMP_PREFIX,
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
        prefix=ATOMIC_WRITE_TEMP_PREFIX,
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
    if (width, height) != (128, 112):
        raise ValueError("portrait sheet must be exactly 128x112 pixels")
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
    expected_scanline_size = height * (stride + 1)
    max_decompressed_size = expected_scanline_size + 1
    try:
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(idat, max_decompressed_size)
        if len(raw) > expected_scanline_size or decompressor.unconsumed_tail:
            raise ValueError("PNG scanline data exceeds the expected length")
        raw += decompressor.flush(max_decompressed_size - len(raw))
    except zlib.error as exc:
        raise ValueError("PNG IDAT stream is invalid") from exc
    if len(raw) > expected_scanline_size:
        raise ValueError("PNG scanline data exceeds the expected length")
    if not decompressor.eof:
        raise ValueError("PNG IDAT stream is incomplete")
    if decompressor.unused_data:
        raise ValueError("PNG IDAT stream has trailing data")
    if len(raw) != expected_scanline_size:
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
    _discovery_ownership_source_fields = (
        "chapterSettings",
        "tableSource",
    )

    @staticmethod
    def _map_payload_bytes(tile_count):
        return 2 + tile_count * 2

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
        dimensions_valid = all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in (width, height)
        )
        if dimensions_valid and self._map_payload_bytes(width * height) > self._map_buffer_bytes:
            diagnostics.add(
                GeneratedDataError(
                    "map dimensions {}x{} exceed the {}-byte gBmMapBuffer contract".format(
                        width, height, self._map_buffer_bytes
                    ),
                    record.resource_locs["mapWidth"],
                    "{}.resources".format(record.id),
                )
            )
            return

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

    def source_dependencies(self, record):
        return (
            record.ownership["chapterSettings"],
            record.ownership["tableSource"],
        )

    def generated_outputs(self, record, out_dir):
        del record, out_dir
        return {}

    def transient_outputs(self, record, out_dir):
        del record, out_dir
        return ()


class TiledTmxMapLayoutKind(ChapterMapLayoutKind):
    """TMX source adapter sharing the existing chapter-map ownership seam."""

    name = "tiled-tmx-map-layout"
    _options = {
        "format": "tmx-safe-v1",
        "compression": "lz77",
        "layer": "Main",
        "tilesetId": tmx.TILESET_NAME,
    }
    _map_object = "src/data/const_data_chapter_maps.o"
    _modern_map_object = "$(MODERN_OUTPUT_DIR)/src/data/const_data_chapter_maps.o"
    _map_data_source = "src/data/const_data_chapter_maps.c"

    @staticmethod
    def _output_paths(record, out_dir):
        stem = os.path.join(out_dir, "tmx", record.id)
        return stem + ".mar", stem + ".json", stem + ".bin.lz"

    def validate(self, record, diagnostics):
        options_valid = _validate_exact_values(
            record.options, record.option_locs, self._options, diagnostics,
            record.loc, "{}.options".format(record.id),
        )
        ownership_valid = _validate_exact_values(
            record.ownership, record.ownership_locs,
            (
                "seam", "tableSource", "chapterSettings", "chapterSettingsIndex",
                "mainLayerId", "symbol", "consumer",
            ),
            diagnostics, record.loc, "{}.ownership".format(record.id),
        )
        resources_valid = _validate_exact_values(
            record.resources, record.resource_locs,
            ("mapWidth", "mapHeight", "mapBufferBytes"),
            diagnostics, record.loc, "{}.resources".format(record.id),
        )
        if not options_valid or not ownership_valid or not resources_valid:
            return
        for key, expected in self._options.items():
            if record.options[key] != expected:
                diagnostics.add(GeneratedDataError(
                    "unsupported {} '{}'; expected '{}'".format(
                        key, record.options[key], expected
                    ),
                    record.option_locs[key], "{}.options.{}".format(record.id, key),
                ))
        for key, expected in self._ownership.items():
            if record.ownership[key] != expected:
                diagnostics.add(GeneratedDataError(
                    "tiled-tmx-map-layout ownership.{} must be '{}'".format(key, expected),
                    record.ownership_locs[key], "{}.ownership.{}".format(record.id, key),
                ))
        if len(record.sources) != 1 or not record.sources[0].endswith(".tmx"):
            diagnostics.add(GeneratedDataError(
                "tiled-tmx-map-layout requires exactly one .tmx source file",
                record.loc, "{}.sources".format(record.id),
            ))
            return

        width = record.resources["mapWidth"]
        height = record.resources["mapHeight"]
        buffer_bytes = record.resources["mapBufferBytes"]
        for key, value in (("mapWidth", width), ("mapHeight", height), ("mapBufferBytes", buffer_bytes)):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                diagnostics.add(GeneratedDataError(
                    "resources.{} must be a positive integer".format(key),
                    record.resource_locs[key], "{}.resources.{}".format(record.id, key),
                ))
        if buffer_bytes != self._map_buffer_bytes:
            diagnostics.add(GeneratedDataError(
                "resources.mapBufferBytes must state the runtime map buffer capacity {}".format(
                    self._map_buffer_bytes
                ),
                record.resource_locs["mapBufferBytes"],
                "{}.resources.mapBufferBytes".format(record.id),
            ))
        dimensions_valid = all(
            isinstance(value, int) and not isinstance(value, bool) and value > 0
            for value in (width, height)
        )
        if dimensions_valid and self._map_payload_bytes(width * height) > self._map_buffer_bytes:
            diagnostics.add(GeneratedDataError(
                "map dimensions {}x{} exceed the {}-byte gBmMapBuffer contract".format(
                    width, height, self._map_buffer_bytes
                ),
                record.resource_locs["mapWidth"], "{}.resources".format(record.id),
            ))
            return
        try:
            source_width, source_height, values = tmx.parse_tmx(
                os.path.join(REPO_ROOT, record.sources[0])
            )
        except (OSError, tmx.TmxError) as exc:
            diagnostics.add(GeneratedDataError(
                str(exc), record.source_locs[0], "{}.sources[0]".format(record.id)
            ))
            return
        if source_width != width or source_height != height:
            diagnostics.add(GeneratedDataError(
                "TMX dimensions {}x{} do not match declared resources {}x{}".format(
                    source_width, source_height, width, height
                ),
                record.source_locs[0], "{}.resources".format(record.id),
            ))
        if self._map_payload_bytes(len(values)) > self._map_buffer_bytes:
            diagnostics.add(GeneratedDataError(
                "TMX payload exceeds the {}-byte gBmMapBuffer contract".format(
                    self._map_buffer_bytes
                ),
                record.source_locs[0], "{}.sources[0]".format(record.id),
            ))

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
            entries = _chapter_table_entries(record.ownership["tableSource"])
        except (OSError, ValueError, TypeError) as exc:
            diagnostics.add(GeneratedDataError(
                str(exc), record.ownership_locs["tableSource"],
                "{}.ownership".format(record.id),
            ))
            return
        if row is None or row.get("map", {}).get("mainLayerId") != slot:
            diagnostics.add(GeneratedDataError(
                "chapter settings index {} does not select mainLayerId {}".format(index, slot),
                record.ownership_locs["mainLayerId"],
                "{}.ownership.mainLayerId".format(record.id),
            ))
        if slot >= len(entries) or entries[slot] != record.ownership["symbol"]:
            actual = entries[slot] if slot < len(entries) else "<out of range>"
            diagnostics.add(GeneratedDataError(
                "gChapterDataAssetTable[{}] is '{}', not '{}'".format(
                    slot, actual, record.ownership["symbol"]
                ),
                record.ownership_locs["mainLayerId"],
                "{}.ownership.mainLayerId".format(record.id),
            ))
        generated_path = "build/generated/assets/tmx/{}.bin.lz".format(record.id)
        expected_incbin = '{}[] = INCBIN_U8("{}")'.format(
            record.ownership["symbol"], generated_path
        )
        try:
            with open(os.path.join(REPO_ROOT, self._map_data_source), encoding="utf-8") as handle:
                map_data_source = handle.read()
        except OSError as exc:
            diagnostics.add(GeneratedDataError(
                str(exc), record.loc, "{}.ownership".format(record.id)
            ))
            return
        if expected_incbin not in map_data_source:
            diagnostics.add(GeneratedDataError(
                "{} must own generated '{}' through {}".format(
                    record.ownership["symbol"], generated_path, self._map_data_source
                ),
                record.ownership_locs["symbol"], "{}.ownership.symbol".format(record.id),
            ))

    def generated_outputs(self, record, out_dir):
        width, height, values = tmx.parse_tmx(os.path.join(REPO_ROOT, record.sources[0]))
        mar_path, metadata_path, unused_lz_path = self._output_paths(record, out_dir)
        del unused_lz_path
        return {
            mar_path: tmx.render_mar(values),
            metadata_path: tmx.render_metadata(record.ownership["symbol"], width, height),
        }

    def transient_outputs(self, record, out_dir):
        mar_path, metadata_path, lz_path = self._output_paths(record, out_dir)
        del mar_path, metadata_path
        return (lz_path[:-3], lz_path)

    def make_dependencies(self, record):
        mar_path, metadata_path, lz_path = self._output_paths(record, "$(ASSET_OUTPUT_DIR)")
        bin_path = lz_path[:-3]
        generated_sources = (mar_path, metadata_path)
        return (
            (self._object, record.sources),
            (self._modern_object, record.sources),
            (self._map_object, (lz_path,) + tuple(record.sources)),
            (self._modern_map_object, (lz_path,) + tuple(record.sources)),
            (generated_sources, tuple(record.sources) + ("$(ASSET_MANIFEST)",)),
            (bin_path, generated_sources),
        )

    def source_dependencies(self, record):
        return super().source_dependencies(record) + (self._map_data_source,)


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
    _discovery_ownership_source_fields = ("registrySource",)

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
        return self._validate_metadata(record, diagnostics)

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

    def source_dependencies(self, record):
        return (record.ownership["registrySource"],)

    def generated_outputs(self, record, out_dir):
        del record, out_dir
        return {}

    def transient_outputs(self, record, out_dir):
        del record, out_dir
        return ()


class BattleAnimationPackageKind:
    """Own a package-backed additive entry through existing banim seams."""

    name = "battle-animation-package"
    _options = {"format": "community-text-png-v1"}
    _ownership = {
        "seam": "battle-animation-table",
        "tableSource": "src/banim_data.c",
        "classData": "src/data/classes.json",
        "linkerScript": "linker_script_banim.txt",
        "consumer": "GetBattleAnimationId",
    }
    _discovery_ownership_source_fields = (
        "classData",
        "tableSource",
        "linkerScript",
    )
    _object = "src/banim_data.o"
    _definitions_object = "src/data_banimconf.o"
    _banim_object = "banim/data_banim.o"

    def validate(self, record, diagnostics):
        valid = _validate_exact_values(
            record.options, record.option_locs, self._options, diagnostics, record.loc,
            "{}.options".format(record.id),
        )
        valid = _validate_exact_values(
            record.ownership, record.ownership_locs,
            ("seam", "tableSource", "classData", "linkerScript", "consumer"),
            diagnostics, record.loc, "{}.ownership".format(record.id),
        ) and valid
        valid = _validate_exact_values(
            record.resources, record.resource_locs,
            ("romBytes", "objVramBytes", "oamEntries", "paletteColors"),
            diagnostics, record.loc, "{}.resources".format(record.id),
        ) and valid
        if not valid:
            return
        for key, expected in self._options.items():
            if record.options[key] != expected:
                diagnostics.add(GeneratedDataError(
                    "battle-animation-package options.{} must be '{}'".format(key, expected),
                    record.option_locs[key], "{}.options.{}".format(record.id, key),
                ))
        for key, expected in self._ownership.items():
            if record.ownership[key] != expected:
                diagnostics.add(GeneratedDataError(
                    "battle-animation-package ownership.{} must be '{}'".format(key, expected),
                    record.ownership_locs[key], "{}.ownership.{}".format(record.id, key),
                ))
        for key, capacity in (
            ("romBytes", 0x40000),
            ("objVramBytes", 0x8000),
            ("oamEntries", 128),
            ("paletteColors", 16),
        ):
            value = record.resources[key]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0 or value > capacity:
                diagnostics.add(GeneratedDataError(
                    "resources.{} must be within 1..{}".format(key, capacity),
                    record.resource_locs[key], "{}.resources.{}".format(record.id, key),
                ))
        if (
            len(record.sources) < 3
            or not record.sources[0].endswith("package.json")
            or not record.sources[1].endswith(".txt")
        ):
            diagnostics.add(GeneratedDataError(
                "battle-animation-package requires ordered package.json, script.txt, and indexed PNG sources",
                record.loc, "{}.sources".format(record.id),
            ))
            return
        try:
            package = banim.load_package(REPO_ROOT, record.sources[0], record.sources[1], set(record.sources))
            if package.data["id"] != record.id:
                raise ValueError(
                    "package id '{}' does not match manifest id '{}'".format(package.data["id"], record.id)
                )
            if not package.data["animConf"].startswith("AnimConf_"):
                raise ValueError("animConf must name an existing AnimConf_* declaration")
            _outputs, _paths, metadata = banim.runtime_outputs(
                package, os.path.join(ASSET_BUILD_ROOT, "generated", "assets", "validation", "banim")
            )
            if metadata["total_oam_entries"] > record.resources["oamEntries"]:
                raise ValueError(
                    "generated OAM count {} exceeds resources.oamEntries".format(
                        metadata["total_oam_entries"]
                    )
                )
            if metadata["unique_frame_bytes"] > record.resources["objVramBytes"]:
                raise ValueError("generated frame data exceeds resources.objVramBytes")
            if max(png["colors"] for png in package.pngs.values()) > record.resources["paletteColors"]:
                raise ValueError("generated palette exceeds resources.paletteColors")
            if metadata["runtime_bytes"] > record.resources["romBytes"]:
                raise ValueError("generated runtime data exceeds resources.romBytes")
            self._validate_class_binding(package.data, record)
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            custom_spell.generated_idspace.CapError,
        ) as exc:
            diagnostics.add(GeneratedDataError(str(exc), record.loc, "{}.package".format(record.id)))
            return
        record.banim_package = package

    def _validate_class_binding(self, package, record):
        with open(os.path.join(REPO_ROOT, record.ownership["classData"]), encoding="utf-8") as handle:
            classes = json.load(handle)["classes"]
        matches = [entry for entry in classes if entry.get("class") == package["class"]]
        if len(matches) != 1 or matches[0].get("battleAnim") != package["animConf"]:
            raise ValueError("class '{}' does not bind '{}'".format(package["class"], package["animConf"]))
        if package["weaponType"] not in matches[0].get("baseRanks", {}):
            raise ValueError(
                "class '{}' cannot use '{}'".format(package["class"], package["weaponType"])
            )

    def ownership_key(self, record):
        return "{}:{}".format(record.ownership["seam"], record.banim_package.data["animConf"])

    def make_dependencies(self, record):
        sources = tuple(record.sources)
        return (
            (self._object, sources),
            (self._definitions_object, sources),
            (self._banim_object, sources),
            ("$(MODERN_OUTPUT_DIR)/src/banim_data.o", sources),
            ("$(MODERN_OUTPUT_DIR)/src/data_banimconf.o", sources),
        )

    def source_dependencies(self, record):
        return (
            record.ownership["classData"],
            record.ownership["tableSource"],
            record.ownership["linkerScript"],
        )

    def generated_outputs(self, record, out_dir):
        return {}

    def transient_outputs(self, record, out_dir):
        return banim_derived_outputs([record], out_dir)


class CustomSpellEffectKind:
    """Generate one bounded custom-spell descriptor through the #77 ABI."""

    name = "custom-spell-effect"
    _options = {
        "importFormat": custom_spell.IMPORT_FORMAT,
        "runtimeAbi": custom_spell.RUNTIME_ABI,
        "compression": custom_spell.COMPRESSION,
    }
    _ownership = {
        "seam": "spell-effect-dispatch",
        "spellAssociationSource": "src/spellassoc-data.c",
    }
    _resources = (
        "frames", "totalFrames", "hitFrame", "objBytes", "bgBytes",
        "bgTsaBytes", "objOamEntries", "objPalettes", "bgPalettes",
        "soundEvents", "romBytes",
    )

    def validate(self, record, diagnostics, item_id_cap=None):
        valid = _validate_exact_values(
            record.options, record.option_locs, self._options, diagnostics,
            record.loc, "{}.options".format(record.id),
        )
        valid = _validate_exact_values(
            record.ownership, record.ownership_locs,
            (
                "seam", "item", "effectSymbol", "fallbackVanillaEffect",
                "spellAssociationSource",
            ),
            diagnostics, record.loc, "{}.ownership".format(record.id),
        ) and valid
        valid = _validate_exact_values(
            record.resources, record.resource_locs, self._resources, diagnostics,
            record.loc, "{}.resources".format(record.id),
        ) and valid
        if not valid:
            return
        fixed_values_valid = True
        for key, expected in self._options.items():
            if not _has_exact_value(record.options[key], expected):
                diagnostics.add(GeneratedDataError(
                    "custom-spell-effect options.{} must be {!r}".format(
                        key, expected
                    ),
                    record.option_locs[key],
                    "{}.options.{}".format(record.id, key),
                ))
                fixed_values_valid = False
        for key, expected in self._ownership.items():
            if record.ownership[key] != expected:
                diagnostics.add(GeneratedDataError(
                    "custom-spell-effect ownership.{} must be '{}'".format(
                        key, expected
                    ),
                    record.ownership_locs[key],
                    "{}.ownership.{}".format(record.id, key),
                ))
                fixed_values_valid = False
        if not fixed_values_valid:
            return
        if (
            len(record.sources) < 4
            or os.path.basename(record.sources[0]) != "spell.json"
            or os.path.basename(record.sources[1]) != "animation.txt"
            or any(not path.lower().endswith(".png") for path in record.sources[2:])
        ):
            diagnostics.add(GeneratedDataError(
                "custom-spell-effect requires ordered spell.json, animation.txt, and referenced PNG sources",
                record.loc,
                "{}.sources".format(record.id),
            ))
            return
        effect_symbol = record.ownership["effectSymbol"]
        if (
            isinstance(effect_symbol, str)
            and effect_symbol in custom_spell.public_effect_symbols(REPO_ROOT)
        ):
            diagnostics.add(GeneratedDataError(
                "ownership.effectSymbol '{}' collides with a public/test "
                "CUSTOM_SPELL_* symbol".format(effect_symbol),
                record.ownership_locs["effectSymbol"],
                "{}.ownership.effectSymbol".format(record.id),
            ))
            return
        try:
            fallback, item_type = custom_spell.validate_runtime_binding(
                REPO_ROOT, record.ownership, item_id_cap=item_id_cap
            )
            package = custom_spell.load_package(
                REPO_ROOT,
                record.sources[0],
                record.sources[1],
                record.sources,
                "include/constants/songs.h",
                record.ownership["effectSymbol"],
            )
            custom_spell.validate_declared_resources(package, record.resources)
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            custom_spell.generated_idspace.CapError,
        ) as exc:
            diagnostics.add(GeneratedDataError(
                str(exc), record.loc, "{}.package".format(record.id)
            ))
            return
        record.custom_spell_package = package
        record.custom_spell_fallback_id = fallback
        record.custom_spell_item_type = item_type

    def ownership_key(self, record):
        return "{}:{}:{}".format(
            record.ownership["seam"],
            record.ownership["spellAssociationSource"],
            record.ownership["item"],
        )

    def make_dependencies(self, record):
        sources = tuple(record.sources) + self.source_dependencies(record)
        return (
            ("src/custom_spell_effect.o", sources),
            ("src/data/custom_spell_effect_data.o", sources),
            ("src/spellassoc-data.o", sources),
            ("$(MODERN_OUTPUT_DIR)/src/custom_spell_effect.o", sources),
            ("$(MODERN_OUTPUT_DIR)/src/data/custom_spell_effect_data.o", sources),
            ("$(MODERN_OUTPUT_DIR)/src/spellassoc-data.o", sources),
        )

    def source_dependencies(self, record):
        del record
        return (
            "include/constants/songs.h",
            "include/constants/items.h",
            "include/constants/items_expansion.h",
            "include/custom_spell_effect.h",
            "include/spellassoc.h",
            "src/banim-efxmagic.c",
            "src/data/custom_spell_effect_data.c",
            "src/data/items.json",
            "src/data/items_expansion.json",
            "src/spellassoc-data.c",
        )

    def generated_outputs(self, record, out_dir):
        del record, out_dir
        return {}

    def transient_outputs(self, record, out_dir):
        del record, out_dir
        return ()


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
KIND_REGISTRY.register(TiledTmxMapLayoutKind())
KIND_REGISTRY.register(BattleAnimationPackageKind())
KIND_REGISTRY.register(CustomSpellEffectKind())


def validate(records, item_id_cap=None):
    diagnostics = DiagnosticCollector()
    by_id = {}
    ownership = {}
    sources = {}
    portrait_metadata = {}
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
        if len(normalized_sources) != len(record.sources):
            continue
        if isinstance(kind, CustomSpellEffectKind):
            metadata = kind.validate(
                record, diagnostics, item_id_cap=item_id_cap
            )
        else:
            metadata = kind.validate(record, diagnostics)
        if record.kind == FormattedPortraitPackageKind.name:
            portrait_metadata[id(record)] = metadata
        if isinstance(kind, BattleAnimationPackageKind) and record.banim_package is None:
            continue
        if isinstance(kind, CustomSpellEffectKind) and record.custom_spell_package is None:
            continue
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
                    metadata = portrait_metadata.get(id(record))
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
    try:
        custom_spell.validate_collection(records)
    except ValueError as exc:
        diagnostics.add(GeneratedDataError(str(exc), reference_path="custom-spell-effect"))
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


def validate_custom_spell_selection(records, enabled):
    if enabled not in (0, 1):
        raise GeneratedDataError("EXPANSION_CUSTOM_SPELL_EFFECTS must be 0 or 1")
    count = sum(
        record.kind == CustomSpellEffectKind.name for record in records
    )
    if enabled == 0 and count:
        raise GeneratedDataError(
            "custom-spell-effect record(s) require EXPANSION_CUSTOM_SPELL_EFFECTS=1"
        )
    if enabled == 1 and count == 0:
        raise GeneratedDataError(
            "EXPANSION_CUSTOM_SPELL_EFFECTS=1 requires at least one custom-spell-effect record"
        )


def load_and_validate(path, custom_spell_effects=None, item_id_cap=None):
    records = validate(load_manifest(path), item_id_cap=item_id_cap)
    if custom_spell_effects is not None:
        validate_custom_spell_selection(records, custom_spell_effects)
    return records


def portrait_incbin_consumer_ids(records):
    """Return portrait records whose static C consumers require the default output root."""

    return tuple(
        record.id
        for record in records
        if record.kind == FormattedPortraitPackageKind.name
    )


def tmx_incbin_consumer_ids(records):
    """Return TMX records whose static C consumer requires the default output root."""

    return tuple(
        record.id
        for record in records
        if record.kind == TiledTmxMapLayoutKind.name
    )


def banim_incbin_consumer_ids(records):
    """Return battle-animation package records whose consumers require the default output root."""

    return tuple(
        record.id
        for record in records
        if record.kind == BattleAnimationPackageKind.name
    )


def custom_spell_incbin_consumer_ids(records):
    """Return custom-spell records whose generated includes require the default root."""

    return tuple(
        record.id
        for record in records
        if record.kind == CustomSpellEffectKind.name
    )


def render_makefile(records):
    lines = [
        "# AUTO-GENERATED by scripts.assets -- DO NOT EDIT BY HAND.\n",
        "# Source: assets/manifest.json\n",
        "# This is an ordinary Make dependency fragment, not a runtime registry.\n",
        "ASSET_GENERATE_TOOL ?= $(ASSET_TOOL)\n",
        "\n",
    ]
    by_id = {record.id: record for record in records}
    repository_sources = set()
    for record in records:
        kind = KIND_REGISTRY.resolve(record.kind)
        repository_sources.update(record.sources)
        repository_sources.update(kind.source_dependencies(record))
    def render_prerequisites(prerequisites):
        non_sources = [
            path for path in prerequisites if path not in repository_sources
        ]
        if any(path in repository_sources for path in prerequisites):
            non_sources.append("$(ASSET_MANIFEST_SOURCE_STAMP)")
        return " ".join(non_sources)

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
            if isinstance(target, tuple):
                target = " ".join(target)
            lines.append("{}: {}\n".format(target, render_prerequisites(prerequisites)))
    packages = [
        record for record in records if record.kind == BattleAnimationPackageKind.name
    ]
    generated = []
    for record in packages:
        _outputs, paths, _metadata = banim.runtime_outputs(
            record.banim_package, "$(ASSET_OUTPUT_DIR)"
        )
        generated.extend(paths.values())
    generated.append("$(ASSET_BANIM_COMBINED_LINKER_SCRIPT)")
    if packages:
        lines.append("\n{} &: $(ASSET_OUTPUT_MK)\n".format(" ".join(generated)))
    else:
        lines.append("\n{}: $(ASSET_OUTPUT_MK)\n".format(generated[0]))
    lines.append(
        '\t$(ASSET_GENERATE_TOOL) --manifest "$(ASSET_MANIFEST)" --out-dir "$(ASSET_OUTPUT_DIR)" generate\n'
    )
    for output in generated:
        lines.append("\t@test -f {}\n".format(output))
    for record in packages:
        _outputs, paths, _metadata = banim.runtime_outputs(
            record.banim_package, "$(ASSET_OUTPUT_DIR)"
        )
        motion_object = paths["motion"][:-1] + "o"
        lines.append("\n{}: {}\n".format(motion_object, paths["motion"]))
        lines.append("\t$(AS) $(ASFLAGS) $< -o $@\n")
        lines.append(
            "banim/data_banim.o: {} {} {} {} {} {}\n".format(
                motion_object,
                paths["modes"],
                paths["oam_left"],
                paths["oam_right"],
                paths["palette"],
                " ".join(
                    paths["frame_" + frame_id] for frame_id in record.banim_package.frames
                ),
            )
        )
    custom_records = [
        record for record in records
        if record.kind == CustomSpellEffectKind.name
    ]
    if custom_records:
        generated = custom_spell.output_paths(
            custom_records, "$(ASSET_OUTPUT_DIR)"
        )
        custom_dependencies = []
        seen_dependencies = set()
        for record in custom_records:
            kind = KIND_REGISTRY.resolve(record.kind)
            for source in tuple(record.sources) + kind.source_dependencies(record):
                if source not in seen_dependencies:
                    seen_dependencies.add(source)
                    custom_dependencies.append(source)
        lines.append(
            "\n{} &: $(ASSET_OUTPUT_MK) $(ASSET_MANIFEST_SOURCE_STAMP)\n".format(
                " ".join(generated)
            )
        )
        lines.append(
            '\t$(ASSET_GENERATE_TOOL) --manifest "$(ASSET_MANIFEST)" '
            '--out-dir "$(ASSET_OUTPUT_DIR)" generate\n'
        )
        for output in generated:
            lines.append("\t@test -f {}\n".format(output))
        data_include = (
            "$(ASSET_OUTPUT_DIR)/custom_spell/custom_spell_effect_data.inc"
        )
        assoc_include = (
            "$(ASSET_OUTPUT_DIR)/custom_spell/"
            "custom_spell_effect_spellassoc.inc"
        )
        generated_header = (
            "$(ASSET_OUTPUT_DIR)/custom_spell/custom_spell_effect_generated.h"
        )
        runtime_test_header = (
            "$(ASSET_OUTPUT_DIR)/custom_spell/custom_spell_effect_runtime_test.h"
        )
        binary_outputs = [
            path for path in generated
            if not path.endswith((".h", ".inc", ".json"))
        ]
        lines.append(
            "\nsrc/custom_spell_effect.o $(MODERN_OUTPUT_DIR)/src/custom_spell_effect.o: "
            "{}\n".format(generated_header)
        )
        lines.append(
            "src/data/custom_spell_effect_data.o "
            "$(MODERN_OUTPUT_DIR)/src/data/custom_spell_effect_data.o: "
            "{} {}\n".format(data_include, " ".join(binary_outputs))
        )
        lines.append(
            "src/spellassoc-data.o $(MODERN_OUTPUT_DIR)/src/spellassoc-data.o: "
            "{}\n".format(assoc_include)
        )
        lines.append(
            "$(MODERN_OUTPUT_DIR)/src/custom_spell_effect_test.o: {}\n".format(
                runtime_test_header
            )
        )
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
                record.ownership["seam"],
                record.ownership.get(
                    "consumer",
                    "CustomSpellEffect_Lookup"
                    if record.kind == CustomSpellEffectKind.name
                    else "<unspecified>",
                ),
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


def render_portrait_data(records, out_dir=None):
    del out_dir
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
        "#include \"portrait_components.h\"\n\n",
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


def render_portrait_components(records, out_dir):
    asset_root = os.path.relpath(out_dir, REPO_ROOT).replace(os.sep, "/")
    lines = ["/* AUTO-GENERATED by scripts.assets -- DO NOT EDIT BY HAND. */\n"]
    for record in _portrait_records(records):
        metadata = _read_portrait_metadata(record)
        if metadata["alias"]["mode"] == "existing-components":
            continue
        prefix = "portrait_{}".format(record.ownership["symbol"])
        files = _portrait_component_files(record)
        lines.extend([
            "u8 __attribute__((aligned(4))) {}_tileset[] = INCBIN_U8(\"{}/{}\");\n".format(
                prefix, asset_root, files["tileset_lz"]
            ),
            "u8 __attribute__((aligned(4))) {}_chibi[] = INCBIN_U8(\"{}/{}\");\n".format(
                prefix, asset_root, files["chibi_lz"]
            ),
            "u8 __attribute__((aligned(4))) {}_mouth[] = INCBIN_U8(\"{}/{}\");\n".format(
                prefix, asset_root, files["mouth"]
            ),
            "u16 __attribute__((aligned(4))) {}_palette[] = INCBIN_U16(\"{}/{}\");\n".format(
                prefix, asset_root, files["palette"]
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


def banim_derived_outputs(records, out_dir):
    paths = set()
    for record in records:
        if record.kind != BattleAnimationPackageKind.name:
            continue
        _outputs, runtime, _metadata = banim.runtime_outputs(record.banim_package, out_dir)
        motion_object = runtime["motion"][:-1] + "o"
        paths.update((
            motion_object,
            motion_object + ".bin",
            motion_object + ".bin.lz",
            motion_object + ".bin.lz.o",
            runtime["modes"] + ".o",
        ))
        for key, path in runtime.items():
            if key.startswith("frame_") or key in ("palette", "oam_left", "oam_right"):
                paths.add(path + ".lz")
                paths.add(path + ".lz.o")
    return paths


def banim_expected_outputs(records, out_dir):
    packages = [record for record in records if record.kind == BattleAnimationPackageKind.name]
    entries = ["/* AUTO-GENERATED by scripts.assets; do not edit. */\n"]
    definitions = ["/* AUTO-GENERATED by scripts.assets; do not edit. */\n"]
    declarations = ["/* AUTO-GENERATED by scripts.assets; do not edit. */\n"]
    runtime_test = ["/* AUTO-GENERATED by scripts.assets; do not edit. */\n"]
    symbols = ["/* AUTO-GENERATED by scripts.assets; do not edit. */\n"]
    with open(os.path.join(REPO_ROOT, "linker_script_banim.txt"), encoding="utf-8") as handle:
        linker = [handle.read()]
    if linker[-1] and not linker[-1].endswith("\n"):
        linker.append("\n")
    linker.append("# AUTO-GENERATED package runtime entries; do not edit.\n")
    for offset, record in enumerate(packages):
        abbr = record.banim_package.data["abbreviation"]
        _outputs, paths, metadata = banim.runtime_outputs(record.banim_package, out_dir)
        stem = banim.runtime_stem(record.banim_package)
        index = _banim_table_count() + offset + 1
        symbols.extend((
            "extern int {}_modes_bin;\n".format(stem),
            "extern char {}_motion_o;\n".format(stem),
            "extern char {}_oam_r_bin;\n".format(stem),
            "extern char {}_oam_l_bin;\n".format(stem),
            "extern char {}_palette_pal;\n".format(stem),
        ))
        entries.append(
            '\t{{"{}", &{}, &{}, &{}, &{}, &{}}},\n'.format(
                abbr, stem + "_modes_bin", stem + "_motion_o",
                stem + "_oam_r_bin", stem + "_oam_l_bin", stem + "_palette_pal",
            )
        )
        definitions.extend((
            "CONST_DATA struct BattleAnimDef {}[] = {{\n".format(_banim_package_symbol(record.id)),
            "\t{{ .wtype = 0x100 | {}, .index = {} }},\n".format(
                record.banim_package.data["weaponType"], index
            ),
            "\t{ 0 },\n",
            "};\n",
        ))
        declarations.append(
            "extern CONST_DATA struct BattleAnimDef {}[];\n".format(
                _banim_package_symbol(record.id)
            )
        )
        prefix = "BANIM_PACKAGE_{}".format(record.id)
        runtime_test.extend((
            "#define {}_INDEX {}\n".format(prefix, index - 1),
            "#define {}_MODE_COUNT {}\n".format(prefix, len(record.banim_package.mode_durations)),
            "#define {}_NORMAL_DURATION {}\n".format(
                prefix, record.banim_package.mode_durations["normal"]),
            "#define {}_TOTAL_DURATION {}\n".format(
                prefix, sum(record.banim_package.mode_durations.values())),
            "#define {}_SCRIPT_WORD_COUNT {}\n".format(prefix, metadata["script_word_count"]),
            "#define {}_SOUND_OPCODE 0x{:08X}\n".format(prefix, metadata["sound_opcode"]),
            "#define {}_OAM_ENTRY_COUNT {}\n".format(prefix, metadata["max_oam_entries"]),
            "#define {}_PALETTE_COLOR_1 0x{:04X}\n".format(
                prefix, metadata["palette_color_1"]
            ),
        ))
        seen_frames = set()
        for frame_id in record.banim_package.frames:
            path = paths["frame_" + frame_id]
            if path not in seen_frames:
                linker.append("{}>lz\n".format(path))
                seen_frames.add(path)
        linker.extend((
            "{}>lz\n".format(paths["palette"]),
            "{}>lz\n".format(paths["oam_left"]),
            "{}>lz\n".format(paths["oam_right"]),
            "{}|.data.script>lz\n".format(paths["motion"][:-1] + "o"),
            "{}\n".format(paths["modes"]),
        ))
    outputs = {
        os.path.join(out_dir, "banim", "banim_data_entries.inc"): "".join(entries),
        os.path.join(out_dir, "banim", "banim_defs.inc"): "".join(definitions),
        os.path.join(out_dir, "banim", "banim_defs.h"): "".join(declarations),
        os.path.join(out_dir, "banim", "banim_runtime_test_defs.h"): "".join(runtime_test),
        os.path.join(out_dir, "banim", "banim_runtime_symbols.h"): "".join(symbols),
        os.path.join(out_dir, "banim", "linker_script_banim.txt"): "".join(linker),
    }
    for record in packages:
        outputs.update(banim.runtime_outputs(record.banim_package, out_dir)[0])
    return outputs


def _banim_package_symbol(record_id):
    return "BanimPackage_{}".format(record_id)


def _banim_table_count():
    with open(os.path.join(REPO_ROOT, "src", "banim_data.c"), encoding="utf-8") as handle:
        return len(re.findall(r'^\s*\{"[^"]+"\s*,', handle.read(), re.MULTILINE))


def expected_outputs(records, out_dir):
    out_dir = safe_output_dir(out_dir)
    outputs = {
        os.path.join(out_dir, OUTPUT_MAKEFILE): render_makefile(records).encode("utf-8"),
        os.path.join(out_dir, OUTPUT_INVENTORY): render_inventory(records).encode("utf-8"),
        os.path.join(out_dir, OUTPUT_PORTRAIT_DATA): render_portrait_data(records, out_dir).encode("utf-8"),
        os.path.join(out_dir, OUTPUT_PORTRAIT_COMPONENTS): render_portrait_components(records, out_dir).encode("utf-8"),
        os.path.join(out_dir, OUTPUT_PORTRAIT_SYMBOLS): render_portrait_symbols(records).encode("utf-8"),
    }
    outputs.update(portrait_component_outputs(records, out_dir))
    for record in records:
        kind = KIND_REGISTRY.resolve(record.kind)
        outputs.update(kind.generated_outputs(record, out_dir))
    for path, content in banim_expected_outputs(records, out_dir).items():
        outputs[path] = content.encode("utf-8") if isinstance(content, str) else content
    outputs.update(custom_spell.expected_outputs(records, out_dir))
    return outputs


def _prune_obsolete_custom_spell_outputs(out_dir, expected_paths):
    custom_spell_dir = os.path.join(out_dir, "custom_spell")
    if not os.path.lexists(custom_spell_dir):
        return
    if os.path.islink(custom_spell_dir) or not os.path.isdir(custom_spell_dir):
        raise ValueError(
            "custom spell generated output path must be a real directory"
        )
    expected = {
        os.path.abspath(path)
        for path in expected_paths
        if os.path.commonpath(
            (os.path.abspath(path), os.path.abspath(custom_spell_dir))
        ) == os.path.abspath(custom_spell_dir)
    }
    for directory, directories, files in os.walk(custom_spell_dir, topdown=False):
        for name in files:
            if name.startswith(ATOMIC_WRITE_TEMP_PREFIX):
                continue
            path = os.path.abspath(os.path.join(directory, name))
            if path in expected:
                continue
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
        for name in directories:
            try:
                os.rmdir(os.path.join(directory, name))
            except OSError:
                pass
    try:
        os.rmdir(custom_spell_dir)
    except OSError:
        pass


def _prune_retired_outputs(out_dir):
    path = os.path.join(out_dir, "ch2_main_map.inc")
    if not os.path.lexists(path):
        return
    if os.path.islink(path) or not os.path.isfile(path):
        raise ValueError("retired generated output path must be a real file")
    os.unlink(path)


def _write_selection_stamp(
    path, out_dir, manifest_path, custom_spell_effects, item_id_cap
):
    expected = out_dir + ".manifest-selection"
    if os.path.abspath(path) != expected:
        raise GeneratedDataError(
            "asset selection stamp '{}' must be {}".format(path, expected)
        )
    _write_if_changed(
        expected,
        "manifest={}\ncustom_spell_effects={}\nitem_id_cap=0x{:02X}\n".format(
            os.path.abspath(manifest_path),
            custom_spell_effects,
            item_id_cap,
        ),
    )


@contextlib.contextmanager
def _generation_lock(out_dir):
    lock_path = out_dir + GENERATION_LOCK_SUFFIX
    if os.path.lexists(lock_path) and os.path.islink(lock_path):
        raise GeneratedDataError(
            "generated output lock '{}' must not be a symbolic link".format(lock_path)
        )
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    with os.fdopen(descriptor, "r+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def generate(
    manifest_path,
    out_dir,
    custom_spell_effects=None,
    item_id_cap=None,
    selection_stamp=None,
):
    out_dir = safe_output_dir(out_dir)
    with _generation_lock(out_dir):
        records = load_and_validate(
            manifest_path,
            custom_spell_effects,
            item_id_cap=item_id_cap,
        )
        outputs = expected_outputs(records, out_dir)
        _prune_obsolete_custom_spell_outputs(out_dir, outputs)
        _prune_retired_outputs(out_dir)
        for path in outputs:
            _safe_output_path(path, out_dir)
        for path, content in outputs.items():
            _write_bytes_if_changed(path, content)
        if selection_stamp is not None:
            _write_selection_stamp(
                selection_stamp,
                out_dir,
                manifest_path,
                custom_spell_effects,
                item_id_cap,
            )
    return records


def check(manifest_path, out_dir, custom_spell_effects=None, item_id_cap=None):
    records = load_and_validate(
        manifest_path,
        custom_spell_effects,
        item_id_cap=item_id_cap,
    )
    out_dir = safe_output_dir(out_dir)
    expected = expected_outputs(records, out_dir)
    errors = []
    for path, content in expected.items():
        _safe_output_path(path, out_dir)
        if not os.path.isfile(path):
            errors.append("missing generated output {}".format(path))
            continue
        with open(path, "rb") as handle:
            if handle.read() != content:
                errors.append("stale generated output {}".format(path))
    if os.path.isdir(out_dir):
        transient_outputs = set()
        for record in records:
            kind = KIND_REGISTRY.resolve(record.kind)
            transient_outputs.update(kind.transient_outputs(record, out_dir))
        for root, directories, files in os.walk(out_dir):
            for filename in files:
                path = os.path.join(root, filename)
                if path not in expected and path not in transient_outputs:
                    errors.append("orphan generated output {}".format(path))
            for directory in directories:
                path = os.path.join(root, directory)
                if not any(
                    candidate.startswith(path + os.sep)
                    for candidate in set(expected) | transient_outputs
                ):
                    errors.append("orphan generated output {}".format(path))
    if errors:
        raise GeneratedDataValidationError([GeneratedDataError(error) for error in errors])
    return records
