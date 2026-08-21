"""Strict schema, validation, and deterministic rendering for asset manifests."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import unicodedata

from scripts.generated_data.diagnostics import (
    DiagnosticCollector,
    GeneratedDataError,
    GeneratedDataValidationError,
)
from scripts.generated_data.json_loader import load_json_file


SCHEMA_VERSION = 1
OUTPUT_MAKEFILE = "asset_manifest.mk"
OUTPUT_INVENTORY = "asset_inventory.md"
OUTPUT_NAMES = (OUTPUT_MAKEFILE, OUTPUT_INVENTORY)
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
ASSET_OUTPUT_ROOT = os.path.abspath(
    os.path.join(REPO_ROOT, "build", "generated", "assets")
)
ASSET_OUTPUT_ROOT_REAL = os.path.realpath(ASSET_OUTPUT_ROOT)
ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


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
    if schema_node.as_int() != SCHEMA_VERSION:
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


def expected_outputs(records, out_dir):
    out_dir = safe_output_dir(out_dir)
    return {
        os.path.join(out_dir, OUTPUT_MAKEFILE): render_makefile(records),
        os.path.join(out_dir, OUTPUT_INVENTORY): render_inventory(records),
    }


def generate(manifest_path, out_dir):
    records = load_and_validate(manifest_path)
    for path, content in expected_outputs(records, out_dir).items():
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
        with open(path, encoding="utf-8") as handle:
            if handle.read() != content:
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
