"""Registers every table schema with the schema/version dispatch registry.

Import this module (rather than importing table packages directly) to
guarantee registration has happened before resolving a table by name.
"""

from __future__ import annotations

from .schema import REGISTRY
from .supports.schema import SupportsTableSchema
from .units.schema import UnitsTableSchema
from .shops.schema import ShopsTableSchema
from .traps.schema import TrapsTableSchema
from .items.schema import ItemsTableSchema
from .eventscripts.schema import EventScriptsTableSchema
from .eventlists.schema import EventListsTableSchema
from .chapterbundle.schema import ChapterBundleTableSchema
from .classes.schema import ClassesTableSchema
from .characters.schema import CharactersTableSchema
from .terrainstats.schema import TerrainStatsTableSchema
from .movecost.schema import MovecostTableSchema
from .weapontriangle.schema import WeaponTriangleTableSchema

REGISTRY.register(SupportsTableSchema())
REGISTRY.register(UnitsTableSchema())
REGISTRY.register(ShopsTableSchema())
REGISTRY.register(TrapsTableSchema())
REGISTRY.register(ItemsTableSchema())
REGISTRY.register(EventScriptsTableSchema())
REGISTRY.register(EventListsTableSchema())
REGISTRY.register(ChapterBundleTableSchema())
REGISTRY.register(ClassesTableSchema())
# Issue #5 Batch 2b: full global table -- real committed
# src/data/characters.json (fullCoverage), generate.py/parser.py/
# inventory.py, and wired into generated_data.mk's GENERATED_DATA_TABLES/
# CI. See characters/schema.py's module docstring for the full write-up.
REGISTRY.register(CharactersTableSchema())
# Issue #5 Batch 1 (mechanics): the 8 TerrainTable_* combat/heal stat
# arrays -- src/data/terrainstats.json, generate.py/parser.py/
# inventory.py, wired into generated_data.mk's GENERATED_DATA_TABLES/CI.
# See terrainstats/schema.py's module docstring for the full write-up.
REGISTRY.register(TerrainStatsTableSchema())
# Issue #5 Batch 2 (mechanics): the 47 TerrainTable_MovCost_*/
# TerrainMoveCost_Ballista movement-cost arrays -- src/data/
# movecost.json, generate.py/parser.py/inventory.py, wired into
# generated_data.mk's GENERATED_DATA_TABLES/CI. See movecost/schema.py's
# module docstring for the full write-up.
REGISTRY.register(MovecostTableSchema())
# Issue #5 mechanics Batch 3: the 12 sWeaponTriangleRules directed
# advantage/disadvantage rules -- src/data/weapontriangle.json,
# generate.py/parser.py/inventory.py, wired into generated_data.mk's
# GENERATED_DATA_TABLES/CI. See weapontriangle/schema.py's module
# docstring for the full write-up.
REGISTRY.register(WeaponTriangleTableSchema())
