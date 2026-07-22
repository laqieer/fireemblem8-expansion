"""``chapterbundle`` schema: the Chapter 2 whole-bundle manifest (Issue #5
Batch C).

Batch A modeled Chapter 2's leaf tables (``units``/``shops``/``traps``/
``eventscripts``) and Batch B modeled their composition into
``EventListScr_Ch2_*``/``Ch2Events`` (the ``eventlists`` table). Both
validate *within* their own table, and ``eventlists`` cross-validates its
own direct references against the other tables -- but nothing yet proves
the *whole* Chapter 2 slice is internally coherent as one bundle: that the
chapter-settings row that actually selects Chapter 2 at runtime
(``chapter_settings.json`` + ``gChapterDataAssetTable``) really does point
at the ``Ch2Events`` manifest this package validates, that every
table-owned symbol is reachable from that manifest (directly or via an
explicitly-cited hand-written reference), that Chapter 2's required
support owners exist (and reciprocate), and that the character/class/item
IDs Chapter 2's data depends on are declared -- exactly once -- and
genuinely exist.

This table is deliberately metadata-only, like ``eventscripts``: there is
no single hand-written C file that *is* "the bundle" to round-trip
against (the bundle is a cross-table view over Batch A/B's own hand
sources, each of which is still round-tripped by its own table). See
``docs/generated_data.md`` for the full write-up and explicit non-goals
(this does **not** claim the referenced character/class/item IDs are
"authored" -- only that Chapter 2's own data references them and they
exist).

JSON source shape (see ``src/data/ch2_bundle.json``)::

    {
      "$schema": "fe8.chapterbundle.v1",
      "chapter": {
        "id": "CHAPTER_L_2",
        "name": "Ch2",
        "chapterSettingsIndex": 2,
        "internalName": "L02",
        "mapEventDataId": 13
      },
      "manifest": { "table": "eventlists", "symbol": "Ch2Events" },
      "tables": {
        "units": { "source": "src/data/ch2_units.json", "symbols": [...] },
        "shops": { "source": "src/data/ch2_shops.json", "symbols": [...] },
        "traps": { "source": "src/data/ch2_traps.json", "symbols": [...] },
        "eventscripts": { "source": "src/data/ch2_eventscripts.json", "symbols": [...] },
        "eventlists": { "source": "src/data/ch2_eventlists.json", "symbols": [...] }
      },
      "supportOwners": { "source": "src/data/supports.json", "required": [...] },
      "externalReferences": [
        { "table": "units", "symbol": "UnitDef_Ch2Enemy_0", "file": "src/events/ch2-eventscript.h" },
        ...
      ],
      "dependencies": { "characters": [...], "classes": [...], "items": [...] }
    }

``tables`` covers exactly the 5 registered per-symbol Chapter 2 tables this
bundle composes (``supports`` is chapter-agnostic and is referenced only
via ``supportOwners``, not listed under ``tables``). ``dependencies`` is
explicitly reference-only: it records which global ``CHARACTER_*``/
``CLASS_*``/``ITEM_*`` IDs Chapter 2's own ``units``/``shops``/
``supportOwners`` data uses, cross-checked against the live enum headers
and against what Chapter 2's data actually references -- it does **not**
claim those characters/classes/items are themselves authored by this
slice (see "Remaining Issue #5 scope" in the docs).
"""

from __future__ import annotations

import json
import os

from ..diagnostics import GeneratedDataError
from ..cparse import find_c_array_blocks, split_top_level_entries
from ..json_loader import load_json_file
from ..schema import DependencyGraph, TableSchema
from ..validators import extract_enum_constants, validate_reference, validate_unique

SCHEMA_NAME = "chapterbundle"
SCHEMA_VERSION = 1
SCHEMA_ID = "fe8.chapterbundle.v1"

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

CHAPTERS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "chapters.h")
CHARACTERS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "characters.h")
CLASSES_HEADER = os.path.join(REPO_ROOT, "include", "constants", "classes.h")
ITEMS_HEADER = os.path.join(REPO_ROOT, "include", "constants", "items.h")
CHAPTER_SETTINGS_JSON = os.path.join(REPO_ROOT, "src", "data", "chapter_settings.json")
CHAPTER_DATA_ASSET_TABLE_SOURCE = os.path.join(REPO_ROOT, "src", "data", "data_8B363C.c")
CHAPTER_DATA_ASSET_TABLE_SYMBOL = "gChapterDataAssetTable"
CHAPTER_DATA_ASSET_TABLE_DECL = r"const\s+void\s*\*"

# The 5 per-chapter tables this bundle composes ("supports" is a global,
# chapter-agnostic table -- see `supportOwners` instead).
BUNDLE_TABLE_NAMES = ("units", "shops", "traps", "eventscripts", "eventlists")

# Every table dependency this schema needs loaded for cross-table
# validation (see `dependency_tables()`/`cli.py`); `supports` is included
# for the support-owner reciprocal check even though it isn't one of the
# 5 `BUNDLE_TABLE_NAMES`.
DEPENDENCY_TABLE_NAMES = BUNDLE_TABLE_NAMES + ("supports",)


class ChapterInfo:
    __slots__ = (
        "id", "id_loc", "name", "name_loc",
        "chapter_settings_index", "chapter_settings_index_loc",
        "internal_name", "internal_name_loc",
        "map_event_data_id", "map_event_data_id_loc",
        "loc",
    )

    def __init__(self, id_, id_loc, name, name_loc, chapter_settings_index, chapter_settings_index_loc,
                 internal_name, internal_name_loc, map_event_data_id, map_event_data_id_loc, loc):
        self.id = id_
        self.id_loc = id_loc
        self.name = name
        self.name_loc = name_loc
        self.chapter_settings_index = chapter_settings_index
        self.chapter_settings_index_loc = chapter_settings_index_loc
        self.internal_name = internal_name
        self.internal_name_loc = internal_name_loc
        self.map_event_data_id = map_event_data_id
        self.map_event_data_id_loc = map_event_data_id_loc
        self.loc = loc


class ManifestRef:
    __slots__ = ("table", "table_loc", "symbol", "symbol_loc", "loc")

    def __init__(self, table, table_loc, symbol, symbol_loc, loc):
        self.table = table
        self.table_loc = table_loc
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.loc = loc


class TableRef:
    __slots__ = ("name", "source", "source_loc", "symbols", "symbol_locs", "loc")

    def __init__(self, name, source, source_loc, symbols, symbol_locs, loc):
        self.name = name
        self.source = source
        self.source_loc = source_loc
        self.symbols = symbols
        self.symbol_locs = symbol_locs
        self.loc = loc


class SupportOwners:
    __slots__ = ("source", "source_loc", "required", "required_locs", "loc")

    def __init__(self, source, source_loc, required, required_locs, loc):
        self.source = source
        self.source_loc = source_loc
        self.required = required
        self.required_locs = required_locs
        self.loc = loc


class ExternalReference:
    __slots__ = ("table", "table_loc", "symbol", "symbol_loc", "file", "file_loc", "loc")

    def __init__(self, table, table_loc, symbol, symbol_loc, file_, file_loc, loc):
        self.table = table
        self.table_loc = table_loc
        self.symbol = symbol
        self.symbol_loc = symbol_loc
        self.file = file_
        self.file_loc = file_loc
        self.loc = loc


class Dependencies:
    __slots__ = ("characters", "character_locs", "classes", "class_locs", "items", "item_locs", "loc")

    def __init__(self, characters, character_locs, classes, class_locs, items, item_locs, loc):
        self.characters = characters
        self.character_locs = character_locs
        self.classes = classes
        self.class_locs = class_locs
        self.items = items
        self.item_locs = item_locs
        self.loc = loc


class ChapterBundleRecord:
    """The full parsed ``ch2_bundle.json`` document."""

    def __init__(self, chapter, manifest, tables, support_owners, external_references, dependencies, loc):
        self.chapter = chapter
        self.manifest = manifest
        self.tables = tables
        self.tables_by_name = {t.name: t for t in tables}
        self.support_owners = support_owners
        self.external_references = external_references
        self.dependencies = dependencies
        self.loc = loc

    def __len__(self):
        # 1 chapter row + 1 manifest ref + N tables + 1 supportOwners block
        # + 1 dependencies block (externalReferences are counted as part
        # of the record, not separately -- there's exactly one bundle).
        return 1


def load_records(source_path):
    root = load_json_file(source_path)
    schema_node = root.require("$schema")
    if schema_node.as_str() != SCHEMA_ID:
        raise GeneratedDataError(
            "unexpected $schema '{}', expected '{}'".format(schema_node.as_str(), SCHEMA_ID),
            schema_node.loc,
        )

    chapter_node = root.require("chapter")
    id_node = chapter_node.require("id")
    name_node = chapter_node.require("name")
    index_node = chapter_node.require("chapterSettingsIndex")
    internal_name_node = chapter_node.require("internalName")
    map_event_data_id_node = chapter_node.require("mapEventDataId")
    chapter = ChapterInfo(
        id_=id_node.as_str(), id_loc=id_node.loc,
        name=name_node.as_str(), name_loc=name_node.loc,
        chapter_settings_index=index_node.as_int(), chapter_settings_index_loc=index_node.loc,
        internal_name=internal_name_node.as_str(), internal_name_loc=internal_name_node.loc,
        map_event_data_id=map_event_data_id_node.as_int(), map_event_data_id_loc=map_event_data_id_node.loc,
        loc=chapter_node.loc,
    )

    manifest_node = root.require("manifest")
    manifest_table_node = manifest_node.require("table")
    manifest_symbol_node = manifest_node.require("symbol")
    manifest = ManifestRef(
        table=manifest_table_node.as_str(), table_loc=manifest_table_node.loc,
        symbol=manifest_symbol_node.as_str(), symbol_loc=manifest_symbol_node.loc,
        loc=manifest_node.loc,
    )

    tables_node = root.require("tables")
    tables = []
    for table_name in tables_node.keys():
        table_node = tables_node.get(table_name)
        source_node = table_node.require("source")
        symbols_node = table_node.require("symbols")
        symbol_nodes = symbols_node.as_list()
        tables.append(
            TableRef(
                name=table_name,
                source=source_node.as_str(), source_loc=source_node.loc,
                symbols=[n.as_str() for n in symbol_nodes],
                symbol_locs=[n.loc for n in symbol_nodes],
                loc=table_node.loc,
            )
        )

    support_owners_node = root.require("supportOwners")
    so_source_node = support_owners_node.require("source")
    so_required_node = support_owners_node.require("required")
    required_nodes = so_required_node.as_list()
    support_owners = SupportOwners(
        source=so_source_node.as_str(), source_loc=so_source_node.loc,
        required=[n.as_str() for n in required_nodes],
        required_locs=[n.loc for n in required_nodes],
        loc=support_owners_node.loc,
    )

    external_references = []
    for ref_node in root.require("externalReferences").as_list():
        table_node = ref_node.require("table")
        symbol_node = ref_node.require("symbol")
        file_node = ref_node.require("file")
        external_references.append(
            ExternalReference(
                table=table_node.as_str(), table_loc=table_node.loc,
                symbol=symbol_node.as_str(), symbol_loc=symbol_node.loc,
                file_=file_node.as_str(), file_loc=file_node.loc,
                loc=ref_node.loc,
            )
        )

    deps_node = root.require("dependencies")
    chars_node = deps_node.require("characters")
    classes_node = deps_node.require("classes")
    items_node = deps_node.require("items")
    char_nodes = chars_node.as_list()
    class_nodes = classes_node.as_list()
    item_nodes = items_node.as_list()
    dependencies = Dependencies(
        characters=[n.as_str() for n in char_nodes], character_locs=[n.loc for n in char_nodes],
        classes=[n.as_str() for n in class_nodes], class_locs=[n.loc for n in class_nodes],
        items=[n.as_str() for n in item_nodes], item_locs=[n.loc for n in item_nodes],
        loc=deps_node.loc,
    )

    return ChapterBundleRecord(
        chapter=chapter, manifest=manifest, tables=tables,
        support_owners=support_owners, external_references=external_references,
        dependencies=dependencies, loc=root.loc,
    )


def _err(message, loc, ref):
    return GeneratedDataError(message, loc, ref)


def read_chapter_settings_row(index, chapter_settings_path=CHAPTER_SETTINGS_JSON):
    """Read one row of ``chapter_settings.json`` (plain ``json.load`` -- this
    is a large, read-only, pre-existing asset this table only consumes, not
    owns; no diagnostics need to point inside it)."""
    with open(chapter_settings_path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    chapters = data["chapters"]
    if not (0 <= index < len(chapters)):
        return None
    return chapters[index]


def read_asset_table_entries(asset_table_path=CHAPTER_DATA_ASSET_TABLE_SOURCE,
                              symbol=CHAPTER_DATA_ASSET_TABLE_SYMBOL,
                              decl_pattern=CHAPTER_DATA_ASSET_TABLE_DECL):
    """Parse ``gChapterDataAssetTable``'s array literal into its ordered,
    flat list of entry tokens (symbol names or ``"0"`` placeholders)."""
    with open(asset_table_path, "r", encoding="utf-8") as handle:
        text = handle.read()
    for name, body, _ in find_c_array_blocks(text, decl_pattern):
        if name == symbol:
            return split_top_level_entries(body)
    raise GeneratedDataError(
        "could not find '{}' array in {}".format(symbol, asset_table_path)
    )


def validate(records, diagnostics, dependency_records=None,
             chapters_header=CHAPTERS_HEADER, characters_header=CHARACTERS_HEADER,
             classes_header=CLASSES_HEADER, items_header=ITEMS_HEADER,
             chapter_settings_path=CHAPTER_SETTINGS_JSON,
             asset_table_path=CHAPTER_DATA_ASSET_TABLE_SOURCE):
    """Validate the whole-bundle manifest against the current
    ``units``/``shops``/``traps``/``eventscripts``/``eventlists``/
    ``supports`` records (``dependency_records``, loaded via
    ``dependency_tables()``/``cli.py``) and the chapter-settings/asset-table
    wiring."""
    dependency_records = dependency_records or {}
    chapter = records.chapter
    manifest = records.manifest

    # -- 1. chapter ID / chapter-settings index / internalName / mapEventDataId --
    chapters_enum = extract_enum_constants(chapters_header, name_prefix="CHAPTER_")
    diagnostics.extend(
        validate_reference(chapter.id, chapters_enum, chapter.id_loc, "chapter.id", kind="chapter")
    )
    if chapter.id in chapters_enum:
        enum_value, _ = chapters_enum[chapter.id]
        if enum_value != chapter.chapter_settings_index:
            diagnostics.add(
                _err(
                    "chapter.chapterSettingsIndex {} does not match {}'s enum value {} "
                    "(include/constants/chapters.h)".format(
                        chapter.chapter_settings_index, chapter.id, enum_value
                    ),
                    chapter.chapter_settings_index_loc, "chapter.chapterSettingsIndex",
                )
            )

    row = read_chapter_settings_row(chapter.chapter_settings_index, chapter_settings_path)
    if row is None:
        diagnostics.add(
            _err(
                "chapterSettingsIndex {} is out of range for {}".format(
                    chapter.chapter_settings_index, chapter_settings_path
                ),
                chapter.chapter_settings_index_loc, "chapter.chapterSettingsIndex",
            )
        )
    else:
        if row.get("internalName") != chapter.internal_name:
            diagnostics.add(
                _err(
                    "chapter.internalName '{}' does not match chapter_settings.json row {}'s "
                    "internalName '{}'".format(
                        chapter.internal_name, chapter.chapter_settings_index, row.get("internalName")
                    ),
                    chapter.internal_name_loc, "chapter.internalName",
                )
            )
        if row.get("mapEventDataId") != chapter.map_event_data_id:
            diagnostics.add(
                _err(
                    "chapter.mapEventDataId {} does not match chapter_settings.json row {}'s "
                    "mapEventDataId {}".format(
                        chapter.map_event_data_id, chapter.chapter_settings_index, row.get("mapEventDataId")
                    ),
                    chapter.map_event_data_id_loc, "chapter.mapEventDataId",
                )
            )

    # -- 2. mapEventDataId -> gChapterDataAssetTable -> manifest.symbol --
    if manifest.table not in BUNDLE_TABLE_NAMES:
        diagnostics.add(
            _err(
                "manifest.table '{}' must be one of {}".format(manifest.table, BUNDLE_TABLE_NAMES),
                manifest.table_loc, "manifest.table",
            )
        )
    try:
        asset_entries = read_asset_table_entries(asset_table_path)
    except (OSError, GeneratedDataError) as exc:
        diagnostics.add(_err(str(exc), manifest.loc, "manifest"))
        asset_entries = None
    if asset_entries is not None:
        if not (0 <= chapter.map_event_data_id < len(asset_entries)):
            diagnostics.add(
                _err(
                    "chapter.mapEventDataId {} is out of range for gChapterDataAssetTable "
                    "({} entries)".format(chapter.map_event_data_id, len(asset_entries)),
                    chapter.map_event_data_id_loc, "chapter.mapEventDataId",
                )
            )
        else:
            actual_symbol = asset_entries[chapter.map_event_data_id]
            if actual_symbol != manifest.symbol:
                diagnostics.add(
                    _err(
                        "chapter.mapEventDataId {} resolves to '{}' in gChapterDataAssetTable, "
                        "not manifest.symbol '{}' (src/data/data_8B363C.c)".format(
                            chapter.map_event_data_id, actual_symbol, manifest.symbol
                        ),
                        manifest.symbol_loc, "manifest.symbol",
                    )
                )

    eventlists_records = dependency_records.get("eventlists")
    if eventlists_records is not None and manifest.symbol != eventlists_records.manifest.symbol:
        diagnostics.add(
            _err(
                "manifest.symbol '{}' does not match the eventlists table's own manifest symbol "
                "'{}' (src/data/ch2_eventlists.json)".format(manifest.symbol, eventlists_records.manifest.symbol),
                manifest.symbol_loc, "manifest.symbol",
            )
        )

    # -- 3. declared table symbol sets vs. actual dependency-table records --
    diagnostics.extend(
        validate_unique(
            ((t.name, t.loc) for t in records.tables),
            "duplicate table entry '{key}' (first defined at {first_loc})",
            "tables[{key}]",
        )
    )
    for extra_name in sorted(set(records.tables_by_name) - set(BUNDLE_TABLE_NAMES)):
        table = records.tables_by_name[extra_name]
        diagnostics.add(
            _err(
                "unknown bundle table '{}', expected one of {}".format(extra_name, BUNDLE_TABLE_NAMES),
                table.loc, "tables[{}]".format(extra_name),
            )
        )
    for missing_name in sorted(set(BUNDLE_TABLE_NAMES) - set(records.tables_by_name)):
        diagnostics.add(
            _err(
                "bundle is missing required table entry '{}'".format(missing_name),
                records.loc, "tables[{}]".format(missing_name),
            )
        )

    actual_symbols_by_table = {}
    for table_name in BUNDLE_TABLE_NAMES:
        table_records = dependency_records.get(table_name)
        if table_records is None:
            continue
        if table_name == "units":
            actual_symbols_by_table[table_name] = {g.symbol for g in table_records}
        elif table_name in ("shops", "traps", "eventscripts"):
            actual_symbols_by_table[table_name] = {r.symbol for r in table_records}
        elif table_name == "eventlists":
            actual_symbols_by_table[table_name] = (
                {lst.symbol for lst in table_records.lists}
                | {table_records.tutorial.symbol, table_records.manifest.symbol}
            )

    for table_name, table in records.tables_by_name.items():
        actual = actual_symbols_by_table.get(table_name)
        if actual is None:
            continue
        declared = set(table.symbols)
        diagnostics.extend(
            validate_unique(
                zip(table.symbols, table.symbol_locs),
                "duplicate symbol '{key}' declared in tables." + table_name + ".symbols "
                "(first at {first_loc})",
                "tables.{}.symbols[{{key}}]".format(table_name),
            )
        )
        for stale_symbol, loc in zip(table.symbols, table.symbol_locs):
            if stale_symbol not in actual:
                diagnostics.add(
                    _err(
                        "declared symbol '{}' is not a real '{}' table record ({})".format(
                            stale_symbol, table_name, table.source
                        ),
                        loc, "tables.{}.symbols[{}]".format(table_name, stale_symbol),
                    )
                )
        for missing_symbol in sorted(actual - declared):
            diagnostics.add(
                _err(
                    "'{}' table record '{}' ({}) is not declared in tables.{}.symbols "
                    "(stale bundle manifest)".format(table_name, missing_symbol, table.source, table_name),
                    table.loc, "tables.{}.symbols[{}]".format(table_name, missing_symbol),
                )
            )

    # -- 4. reachability: no orphan Ch2-owned unit/shop/trap/event-script/list --
    external_refs_by_table = {}
    for ref in records.external_references:
        external_refs_by_table.setdefault(ref.table, {})[ref.symbol] = ref

    repo_root = REPO_ROOT
    for ref in records.external_references:
        if ref.table not in BUNDLE_TABLE_NAMES:
            diagnostics.add(
                _err(
                    "externalReferences entry has unknown table '{}', expected one of {}".format(
                        ref.table, BUNDLE_TABLE_NAMES
                    ),
                    ref.table_loc, "externalReferences[symbol={}]".format(ref.symbol),
                )
            )
        file_path = ref.file if os.path.isabs(ref.file) else os.path.join(repo_root, ref.file)
        if not os.path.exists(file_path):
            diagnostics.add(
                _err(
                    "externalReferences file '{}' does not exist".format(ref.file),
                    ref.file_loc, "externalReferences[symbol={}].file".format(ref.symbol),
                )
            )
            continue
        with open(file_path, "r", encoding="utf-8") as handle:
            file_text = handle.read()
        if not _contains_whole_word(file_text, ref.symbol):
            diagnostics.add(
                _err(
                    "externalReferences symbol '{}' does not literally appear in '{}' -- "
                    "cannot substantiate this reachability claim".format(ref.symbol, ref.file),
                    ref.symbol_loc, "externalReferences[symbol={}]".format(ref.symbol),
                )
            )

    reachable = _compute_reachable_symbols(dependency_records, manifest)
    for table_name in BUNDLE_TABLE_NAMES:
        actual = actual_symbols_by_table.get(table_name)
        if actual is None:
            continue
        table_reachable = reachable.get(table_name, set())
        table_external = external_refs_by_table.get(table_name, {})
        for symbol in sorted(actual):
            if symbol in table_reachable or symbol in table_external:
                continue
            diagnostics.add(
                _err(
                    "orphan {} record '{}': not referenced by the {} manifest and not declared "
                    "in externalReferences".format(table_name, symbol, manifest.symbol),
                    records.tables_by_name.get(table_name, records).loc,
                    "tables.{}.symbols[{}]".format(table_name, symbol),
                )
            )

    # -- 5. support owners + reciprocal dependencies --
    units_records = dependency_records.get("units")
    supports_records = dependency_records.get("supports")
    if units_records is not None:
        computed_required = _compute_required_support_owners(units_records)
        declared_required = set(records.support_owners.required)
        diagnostics.extend(
            validate_unique(
                zip(records.support_owners.required, records.support_owners.required_locs),
                "duplicate supportOwners.required entry '{key}' (first at {first_loc})",
                "supportOwners.required[{key}]",
            )
        )
        for missing in sorted(computed_required - declared_required):
            diagnostics.add(
                _err(
                    "'{}' is a non-enemy Chapter 2 unit but is missing from supportOwners.required".format(
                        missing
                    ),
                    records.support_owners.loc, "supportOwners.required[{}]".format(missing),
                )
            )
        for extra in sorted(declared_required - computed_required):
            diagnostics.add(
                _err(
                    "'{}' is declared in supportOwners.required but is not a non-enemy Chapter 2 "
                    "unit character".format(extra),
                    records.support_owners.loc, "supportOwners.required[{}]".format(extra),
                )
            )

    if supports_records is not None:
        owners_present = {r.owner: r for r in supports_records}
        for required, loc in zip(records.support_owners.required, records.support_owners.required_locs):
            owner_record = owners_present.get(required)
            if owner_record is None:
                diagnostics.add(
                    _err(
                        "required support owner '{}' has no SupportData record ({})".format(
                            required, records.support_owners.source
                        ),
                        loc, "supportOwners.required[{}]".format(required),
                    )
                )
                continue
            for partner in owner_record.characters:
                if partner not in owners_present:
                    diagnostics.add(
                        _err(
                            "required support owner '{}' lists partner '{}', but '{}' has no "
                            "SupportData record of its own (reciprocal support required)".format(
                                required, partner, partner
                            ),
                            loc, "supportOwners.required[{}]".format(required),
                        )
                    )

    # -- 6. character/class/item dependency sets --
    characters_enum = extract_enum_constants(characters_header, name_prefix="CHARACTER_")
    classes_enum = extract_enum_constants(classes_header, name_prefix="CLASS_")
    items_enum = extract_enum_constants(items_header, name_prefix="ITEM_")

    diagnostics.extend(
        validate_unique(
            zip(records.dependencies.characters, records.dependencies.character_locs),
            "duplicate dependencies.characters entry '{key}' (first at {first_loc})",
            "dependencies.characters[{key}]",
        )
    )
    diagnostics.extend(
        validate_unique(
            zip(records.dependencies.classes, records.dependencies.class_locs),
            "duplicate dependencies.classes entry '{key}' (first at {first_loc})",
            "dependencies.classes[{key}]",
        )
    )
    diagnostics.extend(
        validate_unique(
            zip(records.dependencies.items, records.dependencies.item_locs),
            "duplicate dependencies.items entry '{key}' (first at {first_loc})",
            "dependencies.items[{key}]",
        )
    )

    for symbol, loc in zip(records.dependencies.characters, records.dependencies.character_locs):
        diagnostics.extend(validate_reference(symbol, characters_enum, loc,
                                               "dependencies.characters[{}]".format(symbol), kind="character"))
    for symbol, loc in zip(records.dependencies.classes, records.dependencies.class_locs):
        diagnostics.extend(validate_reference(symbol, classes_enum, loc,
                                               "dependencies.classes[{}]".format(symbol), kind="class"))
    for symbol, loc in zip(records.dependencies.items, records.dependencies.item_locs):
        diagnostics.extend(validate_reference(symbol, items_enum, loc,
                                               "dependencies.items[{}]".format(symbol), kind="item"))

    if units_records is not None:
        used_characters, used_classes = _compute_unit_char_class_refs(units_records)
        used_items = set(_compute_unit_item_refs(units_records))
        if "shops" in dependency_records:
            used_items |= _compute_shop_item_refs(dependency_records["shops"])
        used_characters |= set(records.support_owners.required)

        declared_characters = set(records.dependencies.characters)
        declared_classes = set(records.dependencies.classes)
        declared_items = set(records.dependencies.items)

        for missing in sorted(used_characters - declared_characters):
            diagnostics.add(
                _err(
                    "character '{}' is used by Chapter 2 unit/support data but is not declared in "
                    "dependencies.characters (undeclared dependency)".format(missing),
                    records.dependencies.loc, "dependencies.characters[{}]".format(missing),
                )
            )
        for missing in sorted(used_classes - declared_classes):
            diagnostics.add(
                _err(
                    "class '{}' is used by Chapter 2 unit data but is not declared in "
                    "dependencies.classes (undeclared dependency)".format(missing),
                    records.dependencies.loc, "dependencies.classes[{}]".format(missing),
                )
            )
        for missing in sorted(used_items - declared_items):
            diagnostics.add(
                _err(
                    "item '{}' is used by Chapter 2 unit/shop data but is not declared in "
                    "dependencies.items (undeclared dependency)".format(missing),
                    records.dependencies.loc, "dependencies.items[{}]".format(missing),
                )
            )
        for extra in sorted(declared_characters - used_characters):
            diagnostics.add(
                _err(
                    "dependencies.characters declares '{}' but no Chapter 2 unit/support data "
                    "references it".format(extra),
                    records.dependencies.loc, "dependencies.characters[{}]".format(extra),
                )
            )
        for extra in sorted(declared_classes - used_classes):
            diagnostics.add(
                _err(
                    "dependencies.classes declares '{}' but no Chapter 2 unit data references it".format(extra),
                    records.dependencies.loc, "dependencies.classes[{}]".format(extra),
                )
            )
        for extra in sorted(declared_items - used_items):
            diagnostics.add(
                _err(
                    "dependencies.items declares '{}' but no Chapter 2 unit/shop data references it".format(
                        extra
                    ),
                    records.dependencies.loc, "dependencies.items[{}]".format(extra),
                )
            )

    # -- 7. table dependency DAG is acyclic/deterministic --
    graph = full_dependency_graph()
    graph.topo_order()  # raises GeneratedDataError on a cycle


def _contains_whole_word(text, word):
    import re
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])", text) is not None


def _compute_reachable_symbols(dependency_records, manifest):
    """Return ``{table_name: set(symbols)}`` reachable structurally from the
    ``eventlists`` manifest/list entries (units/shops/traps via manifest
    fields and shop macro args; eventscripts via every list/tutorial/
    manifest event-script reference; eventlists' own list/tutorial/manifest
    symbols are always reachable -- they *are* the manifest)."""
    reachable = {name: set() for name in BUNDLE_TABLE_NAMES}
    eventlists_records = dependency_records.get("eventlists")
    if eventlists_records is None:
        return reachable

    from ..eventlists.schema import MACRO_SPECS

    reachable["eventlists"] = (
        {lst.symbol for lst in eventlists_records.lists}
        | {eventlists_records.tutorial.symbol, eventlists_records.manifest.symbol}
    )

    for lst in eventlists_records.lists:
        for call in lst.entries:
            spec = MACRO_SPECS.get(call.macro, ())
            for (arg_name, kind), arg in zip(spec, call.args):
                if kind == "event_scr" and arg.kind == "symbol":
                    reachable["eventscripts"].add(arg.value)
                elif kind == "shop_symbol" and arg.kind == "symbol":
                    reachable["shops"].add(arg.value)
    reachable["eventscripts"].update(eventlists_records.tutorial.entries)

    for field_name, field in eventlists_records.manifest.fields.items():
        if field.value is None:
            continue
        if field_name in ("playerUnitsInNormal", "playerUnitsInHard",
                          "playerUnitsChoice1InEncounter", "playerUnitsChoice2InEncounter",
                          "playerUnitsChoice3InEncounter", "enemyUnitsChoice1InEncounter",
                          "enemyUnitsChoice2InEncounter", "enemyUnitsChoice3InEncounter"):
            reachable["units"].add(field.value)
        elif field_name in ("traps", "extraTrapsInHard"):
            reachable["traps"].add(field.value)
        elif field_name in ("beginningSceneEvents", "endingSceneEvents"):
            reachable["eventscripts"].add(field.value)

    return reachable


def _compute_required_support_owners(units_records):
    """Non-enemy (symbolic ``charIndex``, ``allegiance != FACTION_ID_RED``)
    Chapter 2 unit characters -- the set every Chapter 2 unit group commits
    to eventually needing a support record for."""
    required = set()
    for group in units_records:
        for unit in group.units:
            if isinstance(unit.char_index, str) and unit.allegiance != "FACTION_ID_RED":
                required.add(unit.char_index)
    return required


def _compute_unit_char_class_refs(units_records):
    characters = set()
    classes = set()
    for group in units_records:
        for unit in group.units:
            if isinstance(unit.char_index, str):
                characters.add(unit.char_index)
            if isinstance(getattr(unit, "leader_char_index", None), str):
                characters.add(unit.leader_char_index)
            if isinstance(unit.class_index, str):
                classes.add(unit.class_index)
    return characters, classes


def _compute_unit_item_refs(units_records):
    items = set()
    for group in units_records:
        for unit in group.units:
            for item in unit.items:
                items.add(item)
    return items


def _compute_shop_item_refs(shops_records):
    items = set()
    for shop in shops_records:
        for item in shop.items:
            items.add(item)
    return items


class ChapterBundleTableSchema(TableSchema):
    name = SCHEMA_NAME
    version = SCHEMA_VERSION

    default_source = "src/data/ch2_bundle.json"
    # Metadata-only: the bundle is a cross-table view, not itself a single
    # hand-written C file to round-trip against, and there is no C to
    # generate (each composed table already generates/round-trips its own
    # hand source independently).
    default_hand_source = None
    default_output_name = None
    default_inventory_path = "reports/generated_data_bundle_inventory.md"

    def dependencies(self):
        return DEPENDENCY_TABLE_NAMES + (
            "constants.chapters", "constants.characters", "constants.classes", "constants.items",
        )

    def dependency_tables(self):
        return DEPENDENCY_TABLE_NAMES

    def load_records(self, source_path):
        return load_records(source_path)

    def validate(self, records, diagnostics, dependency_records=None):
        validate(records, diagnostics, dependency_records)

    def build_inventory(self, records):
        from . import inventory as chapterbundle_inventory
        return chapterbundle_inventory.build_inventory(records)


def dependency_graph():
    graph = DependencyGraph()
    for dep in ChapterBundleTableSchema().dependencies():
        graph.add_dependency(SCHEMA_NAME, dep)
    return graph


def full_dependency_graph():
    """Merge every registered table schema's own ``dependency_graph()``-style
    edges into one combined graph, proving the *whole* multi-table
    dependency DAG (not just this table's own direct edges) is acyclic and
    has a deterministic topological order."""
    from .. import registry  # noqa: F401  (ensures every table is registered)
    from ..schema import REGISTRY

    graph = DependencyGraph()
    graph.add_dependency(SCHEMA_NAME, "eventlists")
    for name in DEPENDENCY_TABLE_NAMES:
        graph.add_dependency(SCHEMA_NAME, name)
    for name in REGISTRY.all_names():
        schema = REGISTRY.resolve(name)
        for dep in schema.dependency_tables():
            graph.add_dependency(name, dep)
    return graph
