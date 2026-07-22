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
from .eventscripts.schema import EventScriptsTableSchema
from .eventlists.schema import EventListsTableSchema
from .chapterbundle.schema import ChapterBundleTableSchema

REGISTRY.register(SupportsTableSchema())
REGISTRY.register(UnitsTableSchema())
REGISTRY.register(ShopsTableSchema())
REGISTRY.register(TrapsTableSchema())
REGISTRY.register(EventScriptsTableSchema())
REGISTRY.register(EventListsTableSchema())
REGISTRY.register(ChapterBundleTableSchema())
