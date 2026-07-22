"""Tests for the ``chapterbundle`` schema (Issue #5 Chapter 2 slice, Batch
C): the whole-bundle manifest tying units/shops/traps/eventscripts/
eventlists/supports together with the chapter-settings/asset-table wiring.

Fixtures live under ``tests/fixtures/chapterbundle/`` and reuse the "EL"
fixture family already established by ``tests/fixtures/eventlists/`` (same
``UnitDef_EL_*``/``ShopList_EL_*``/``TrapData_EL_*``/``EventScr_EL_*``/
``EventListScr_EL_*``/``ELEvents`` symbols) plus a small ``deps_supports.json``
and tiny stand-in fixtures for the chapter-settings/chapters-enum/asset-table
cross-check (``chapters.h``, ``chapter_settings.json``, ``data_8B363C.c``),
so each negative-path scenario only has to vary the one field under test.
"""

import os
import unittest

from scripts.generated_data.diagnostics import DiagnosticCollector, GeneratedDataError
from scripts.generated_data.chapterbundle import schema as chapterbundle_schema
from scripts.generated_data.eventlists import schema as eventlists_schema
from scripts.generated_data.eventscripts import schema as eventscripts_schema
from scripts.generated_data.schema import DependencyGraph
from scripts.generated_data.shops import schema as shops_schema
from scripts.generated_data.supports import schema as supports_schema
from scripts.generated_data.traps import schema as traps_schema
from scripts.generated_data.units import schema as units_schema
from scripts.generated_data.tests._util import fixture_path

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def repo_path(*parts):
    return os.path.join(REPO_ROOT, *parts)


DEFAULT_DEP_SOURCES = {
    "units": "deps_units.json",
    "shops": "deps_shops.json",
    "traps": "deps_traps.json",
    "eventscripts": "deps_eventscripts.json",
    "eventlists": "deps_eventlists.json",
    "supports": "deps_supports.json",
}

DEP_LOADERS = {
    "units": units_schema.load_records,
    "shops": shops_schema.load_records,
    "traps": traps_schema.load_records,
    "eventscripts": eventscripts_schema.load_records,
    "eventlists": eventlists_schema.load_records,
    "supports": supports_schema.load_records,
}


def cb_fixture(*parts):
    return fixture_path("chapterbundle", *parts)


def _load_dependency_records(overrides=None):
    overrides = overrides or {}
    sources = dict(DEFAULT_DEP_SOURCES)
    sources.update(overrides)
    return {
        name: DEP_LOADERS[name](cb_fixture(filename))
        for name, filename in sources.items()
    }


def _validate(bundle_fixture, dep_overrides=None, **validate_kwargs):
    records = chapterbundle_schema.load_records(cb_fixture(bundle_fixture))
    diagnostics = DiagnosticCollector()
    kwargs = dict(
        chapters_header=cb_fixture("chapters.h"),
        chapter_settings_path=cb_fixture("chapter_settings.json"),
        asset_table_path=cb_fixture("data_8B363C.c"),
    )
    kwargs.update(validate_kwargs)
    chapterbundle_schema.validate(
        records, diagnostics, _load_dependency_records(dep_overrides), **kwargs
    )
    return records, diagnostics


def _messages(diagnostics):
    return [str(e) for e in diagnostics.errors]


class ChapterBundleValidFixtureTests(unittest.TestCase):
    def test_valid_fixture_has_no_diagnostics(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=_messages(diagnostics))
        self.assertEqual(len(records), 1)
        self.assertEqual(records.chapter.id, "CHAPTER_EL")
        self.assertEqual(records.manifest.symbol, "ELEvents")
        self.assertEqual(
            sorted(records.tables_by_name),
            ["eventlists", "eventscripts", "shops", "traps", "units"],
        )


class ChapterCrossCheckTests(unittest.TestCase):
    """chapter.id / chapterSettingsIndex / internalName / mapEventDataId /
    manifest.symbol cross-checked against the fixture chapters.h,
    chapter_settings.json, and gChapterDataAssetTable."""

    def test_chapter_settings_index_mismatch_detected(self):
        _, diagnostics = _validate("chapter_index_mismatch.json")
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any("chapterSettingsIndex" in m and "does not match" in m for m in _messages(diagnostics)),
            _messages(diagnostics),
        )

    def test_internal_name_mismatch_detected(self):
        _, diagnostics = _validate("chapter_internal_name_mismatch.json")
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any("internalName" in m and "does not match" in m for m in _messages(diagnostics)),
            _messages(diagnostics),
        )

    def test_map_event_data_id_mismatch_detected(self):
        _, diagnostics = _validate("chapter_map_event_data_id_mismatch.json")
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any("mapEventDataId" in m and "does not match" in m for m in _messages(diagnostics)),
            _messages(diagnostics),
        )

    def test_manifest_wrong_symbol_detected(self):
        _, diagnostics = _validate("manifest_wrong_symbol.json")
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any("resolves to '0'" in m and "manifest.symbol 'ELEvents'" in m for m in _messages(diagnostics)),
            _messages(diagnostics),
        )

    def test_unknown_chapter_id_detected(self):
        _, diagnostics = _validate("unknown_chapter_id.json")
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any("CHAPTER_DOES_NOT_EXIST" in m for m in _messages(diagnostics)), _messages(diagnostics)
        )


class SupportOwnerTests(unittest.TestCase):
    def test_missing_support_owner_detected(self):
        _, diagnostics = _validate("missing_support_owner.json")
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any(
                "CHARACTER_EIRIKA" in m and "missing from supportOwners.required" in m
                for m in _messages(diagnostics)
            ),
            _messages(diagnostics),
        )

    def test_support_owner_without_own_record_detected(self):
        _, diagnostics = _validate("valid.json", dep_overrides={"supports": "deps_supports_empty.json"})
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any("has no SupportData record" in m for m in _messages(diagnostics)), _messages(diagnostics)
        )

    def test_missing_reciprocal_support_detected(self):
        _, diagnostics = _validate(
            "valid.json", dep_overrides={"supports": "deps_supports_no_reciprocal.json"}
        )
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any(
                "lists partner 'CHARACTER_SETH'" in m and "no SupportData record of its own" in m
                for m in _messages(diagnostics)
            ),
            _messages(diagnostics),
        )


class OrphanRecordTests(unittest.TestCase):
    """Every Ch2-owned unit/shop/trap/event-script record must be reachable
    from the manifest or explicitly cited in externalReferences -- an
    un-reachable, un-cited *declared* record is an orphan. (There is no
    equivalent "orphan eventlist" scenario: an eventlists table's own
    list/tutorial/manifest symbols constitute the manifest itself, so they
    are trivially always reachable by construction.)"""

    def test_orphan_unit_detected(self):
        _, diagnostics = _validate("orphan_unit.json", dep_overrides={"units": "deps_units_orphan.json"})
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any("orphan units record 'UnitDef_EL_Orphan'" in m for m in _messages(diagnostics)),
            _messages(diagnostics),
        )

    def test_orphan_shop_detected(self):
        _, diagnostics = _validate("orphan_shop.json", dep_overrides={"shops": "deps_shops_orphan.json"})
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any("orphan shops record 'ShopList_EL_OrphanShop'" in m for m in _messages(diagnostics)),
            _messages(diagnostics),
        )

    def test_orphan_trap_detected(self):
        _, diagnostics = _validate("orphan_trap.json", dep_overrides={"traps": "deps_traps_orphan.json"})
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any("orphan traps record 'TrapData_EL_OrphanTrap'" in m for m in _messages(diagnostics)),
            _messages(diagnostics),
        )

    def test_orphan_eventscript_detected(self):
        _, diagnostics = _validate(
            "orphan_eventscript.json", dep_overrides={"eventscripts": "deps_eventscripts_orphan.json"}
        )
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any("orphan eventscripts record 'EventScr_EL_OrphanScript'" in m for m in _messages(diagnostics)),
            _messages(diagnostics),
        )


class DependencySetTests(unittest.TestCase):
    def test_undeclared_character_dependency_detected(self):
        _, diagnostics = _validate("undeclared_character_dependency.json")
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any(
                "character 'CHARACTER_EIRIKA'" in m and "undeclared dependency" in m
                for m in _messages(diagnostics)
            ),
            _messages(diagnostics),
        )

    def test_undeclared_class_dependency_detected(self):
        _, diagnostics = _validate("undeclared_class_dependency.json")
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any(
                "class 'CLASS_EIRIKA_LORD'" in m and "undeclared dependency" in m
                for m in _messages(diagnostics)
            ),
            _messages(diagnostics),
        )

    def test_undeclared_item_dependency_detected(self):
        _, diagnostics = _validate("undeclared_item_dependency.json")
        self.assertFalse(diagnostics.ok)
        messages = _messages(diagnostics)
        self.assertTrue(
            any(
                "undeclared dependency" in m
                and ("ITEM_SWORD_RAPIER" in m or "ITEM_SWORD_SLIM" in m or "ITEM_VULNERARY" in m)
                for m in messages
            ),
            messages,
        )

    def test_duplicate_dependency_detected(self):
        _, diagnostics = _validate("duplicate_dependency.json")
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any("duplicate dependencies.characters entry 'CHARACTER_EIRIKA'" in m for m in _messages(diagnostics)),
            _messages(diagnostics),
        )


class StaleBundleManifestTests(unittest.TestCase):
    """Content-level staleness of ``tables.<name>.symbols`` vs. the actual
    dependency-table records (distinct from the CLI's committed-inventory
    drift check, exercised separately in the CLI test suite)."""

    def test_missing_declared_symbol_detected(self):
        _, diagnostics = _validate("stale_missing_symbol.json")
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any(
                "EventScr_EL_Turn2" in m and "stale bundle manifest" in m
                for m in _messages(diagnostics)
            ),
            _messages(diagnostics),
        )

    def test_declared_symbol_not_a_real_record_detected(self):
        _, diagnostics = _validate("stale_extra_symbol.json")
        self.assertFalse(diagnostics.ok)
        self.assertTrue(
            any(
                "EventScr_EL_DoesNotExist" in m and "not a real 'eventscripts' table record" in m
                for m in _messages(diagnostics)
            ),
            _messages(diagnostics),
        )


class DependencyGraphAcyclicTests(unittest.TestCase):
    """The whole-bundle DAG check (validate() step 7) must be acyclic and
    deterministic across every registered table, not just chapterbundle's
    own declared dependencies."""

    def test_real_full_dependency_graph_is_acyclic_and_deterministic(self):
        graph = chapterbundle_schema.full_dependency_graph()
        order = graph.topo_order()
        self.assertEqual(order, chapterbundle_schema.full_dependency_graph().topo_order())
        self.assertIn("chapterbundle", order)
        # Every table chapterbundle depends on must be ordered before it.
        for dep in chapterbundle_schema.DEPENDENCY_TABLE_NAMES:
            self.assertLess(order.index(dep), order.index("chapterbundle"))

    def test_synthetic_cycle_is_detected(self):
        # Mirrors full_dependency_graph()'s shape but injects a cycle
        # (chapterbundle -> eventlists -> chapterbundle) to prove the same
        # cycle-detection path validate() step 7 relies on actually fires.
        graph = DependencyGraph()
        graph.add_dependency("chapterbundle", "eventlists")
        graph.add_dependency("eventlists", "chapterbundle")
        with self.assertRaises(GeneratedDataError):
            graph.topo_order()


class EndToEndRealBundleTests(unittest.TestCase):
    """Loads all 7 currently-registered tables (units/shops/traps/
    eventscripts/eventlists/supports/chapterbundle) plus the real,
    read-only chapter_settings.json + gChapterDataAssetTable, and validates
    the exact committed Chapter 2 bundle end-to-end with zero diagnostics."""

    def test_committed_ch2_bundle_validates_cleanly(self):
        records = chapterbundle_schema.load_records(repo_path("src", "data", "ch2_bundle.json"))
        dependency_records = {
            "units": units_schema.load_records(repo_path("src", "data", "ch2_units.json")),
            "shops": shops_schema.load_records(repo_path("src", "data", "ch2_shops.json")),
            "traps": traps_schema.load_records(repo_path("src", "data", "ch2_traps.json")),
            "eventscripts": eventscripts_schema.load_records(repo_path("src", "data", "ch2_eventscripts.json")),
            "eventlists": eventlists_schema.load_records(repo_path("src", "data", "ch2_eventlists.json")),
            "supports": supports_schema.load_records(repo_path("src", "data", "supports.json")),
        }
        diagnostics = DiagnosticCollector()
        chapterbundle_schema.validate(records, diagnostics, dependency_records)
        self.assertTrue(diagnostics.ok, msg=_messages(diagnostics))
        self.assertEqual(len(records.tables), 5)
        self.assertEqual(records.chapter.id, "CHAPTER_L_2")
        self.assertEqual(records.manifest.symbol, "Ch2Events")

    def test_committed_bundle_has_no_undeclared_or_orphan_records(self):
        # A second, more targeted assertion of the same "no orphan / no
        # undeclared dependency" DONE requirement, phrased in terms of the
        # diagnostics' reference paths rather than free-text messages.
        records = chapterbundle_schema.load_records(repo_path("src", "data", "ch2_bundle.json"))
        dependency_records = {
            "units": units_schema.load_records(repo_path("src", "data", "ch2_units.json")),
            "shops": shops_schema.load_records(repo_path("src", "data", "ch2_shops.json")),
            "traps": traps_schema.load_records(repo_path("src", "data", "ch2_traps.json")),
            "eventscripts": eventscripts_schema.load_records(repo_path("src", "data", "ch2_eventscripts.json")),
            "eventlists": eventlists_schema.load_records(repo_path("src", "data", "ch2_eventlists.json")),
            "supports": supports_schema.load_records(repo_path("src", "data", "supports.json")),
        }
        diagnostics = DiagnosticCollector()
        chapterbundle_schema.validate(records, diagnostics, dependency_records)
        messages = _messages(diagnostics)
        self.assertFalse(any("orphan" in m for m in messages), messages)
        self.assertFalse(any("undeclared dependency" in m for m in messages), messages)


if __name__ == "__main__":
    unittest.main()
