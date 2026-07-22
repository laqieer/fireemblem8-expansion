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

REGISTRY.register(SupportsTableSchema())
REGISTRY.register(UnitsTableSchema())
REGISTRY.register(ShopsTableSchema())
REGISTRY.register(TrapsTableSchema())
REGISTRY.register(ItemsTableSchema())
REGISTRY.register(EventScriptsTableSchema())
REGISTRY.register(EventListsTableSchema())
REGISTRY.register(ChapterBundleTableSchema())
REGISTRY.register(ClassesTableSchema())
# Issue #5 Batch 2a: schema/dependency-validation foundation only -- see
# characters/schema.py's module docstring. Deliberately NOT added to
# generated_data.mk's GENERATED_DATA_TABLES (no committed real source,
# no generate/round-trip/inventory support yet); registering it here only
# makes `validate --table characters --source <fixture>` available.
REGISTRY.register(CharactersTableSchema())
