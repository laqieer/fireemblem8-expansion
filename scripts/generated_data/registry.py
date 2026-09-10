"""Registers every table schema with the schema/version dispatch registry.

Import this module (rather than importing table packages directly) to
guarantee registration has happened before resolving a table by name.
"""

from __future__ import annotations

from .schema import REGISTRY


def _supports_schema():
    from .supports.schema import SupportsTableSchema
    return SupportsTableSchema()


def _units_schema():
    from .units.schema import UnitsTableSchema
    return UnitsTableSchema()


def _shops_schema():
    from .shops.schema import ShopsTableSchema
    return ShopsTableSchema()


def _traps_schema():
    from .traps.schema import TrapsTableSchema
    return TrapsTableSchema()


def _items_schema():
    from .items.schema import ItemsTableSchema
    return ItemsTableSchema()


def _eventscripts_schema():
    from .eventscripts.schema import EventScriptsTableSchema
    return EventScriptsTableSchema()


def _eventlists_schema():
    from .eventlists.schema import EventListsTableSchema
    return EventListsTableSchema()


def _chapterbundle_schema():
    from .chapterbundle.schema import ChapterBundleTableSchema
    return ChapterBundleTableSchema()


def _classes_schema():
    from .classes.schema import ClassesTableSchema
    return ClassesTableSchema()


def _characters_schema():
    from .characters.schema import CharactersTableSchema
    return CharactersTableSchema()


def _terrainstats_schema():
    from .terrainstats.schema import TerrainStatsTableSchema
    return TerrainStatsTableSchema()


def _movecost_schema():
    from .movecost.schema import MovecostTableSchema
    return MovecostTableSchema()


def _weapontriangle_schema():
    from .weapontriangle.schema import WeaponTriangleTableSchema
    return WeaponTriangleTableSchema()


def _ui_presentation_schema():
    from .ui_presentation.schema import UiPresentationTableSchema
    return UiPresentationTableSchema()


def _chapterobjectives_schema():
    from .chapterobjectives.schema import ChapterObjectivesTableSchema
    return ChapterObjectivesTableSchema()


def _autoplaystrategies_schema():
    from .autoplaystrategies.schema import AutoplayStrategiesTableSchema
    return AutoplayStrategiesTableSchema()


REGISTRY.register_factory("supports", 1, _supports_schema)
REGISTRY.register_factory("units", 1, _units_schema)
REGISTRY.register_factory("shops", 1, _shops_schema)
REGISTRY.register_factory("traps", 1, _traps_schema)
REGISTRY.register_factory("items", 1, _items_schema)
REGISTRY.register_factory("eventscripts", 1, _eventscripts_schema)
REGISTRY.register_factory("eventlists", 1, _eventlists_schema)
REGISTRY.register_factory("chapterbundle", 1, _chapterbundle_schema)
REGISTRY.register_factory("classes", 1, _classes_schema)
# Issue #5 Batch 2b: full global table -- real committed
# src/data/characters.json (fullCoverage), generate.py/parser.py/
# inventory.py, and wired into generated_data.mk's GENERATED_DATA_TABLES/
# CI. See characters/schema.py's module docstring for the full write-up.
REGISTRY.register_factory("characters", 1, _characters_schema)
# Issue #5 Batch 1 (mechanics): the 8 TerrainTable_* combat/heal stat
# arrays -- src/data/terrainstats.json, generate.py/parser.py/
# inventory.py, wired into generated_data.mk's GENERATED_DATA_TABLES/CI.
# See terrainstats/schema.py's module docstring for the full write-up.
REGISTRY.register_factory("terrainstats", 1, _terrainstats_schema)
# Issue #5 Batch 2 (mechanics): the 47 TerrainTable_MovCost_*/
# TerrainMoveCost_Ballista movement-cost arrays -- src/data/
# movecost.json, generate.py/parser.py/inventory.py, wired into
# generated_data.mk's GENERATED_DATA_TABLES/CI. See movecost/schema.py's
# module docstring for the full write-up.
REGISTRY.register_factory("movecost", 1, _movecost_schema)
# Issue #5 mechanics Batch 3: the 12 sWeaponTriangleRules directed
# advantage/disadvantage rules -- src/data/weapontriangle.json,
# generate.py/parser.py/inventory.py, wired into generated_data.mk's
# GENERATED_DATA_TABLES/CI. See weapontriangle/schema.py's module
# docstring for the full write-up.
REGISTRY.register_factory("weapontriangle", 1, _weapontriangle_schema)
REGISTRY.register_factory("ui_presentation", 1, _ui_presentation_schema)
REGISTRY.register_factory("chapterobjectives", 1, _chapterobjectives_schema)
REGISTRY.register_factory("autoplaystrategies", 1, _autoplaystrategies_schema)
