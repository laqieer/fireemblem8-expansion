"""Registers every table schema with the schema/version dispatch registry.

Import this module (rather than importing table packages directly) to
guarantee registration has happened before resolving a table by name.
"""

from __future__ import annotations

from .schema import REGISTRY
from .supports.schema import SupportsTableSchema

REGISTRY.register(SupportsTableSchema())
