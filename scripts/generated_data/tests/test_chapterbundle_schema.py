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

import copy
import json
import os
import shutil
import unittest
from pathlib import Path

from scripts.generated_data.diagnostics import DiagnosticCollector, GeneratedDataError
from scripts.generated_data.chapterbundle import schema as chapterbundle_schema
from scripts.generated_data.eventlists import schema as eventlists_schema
from scripts.generated_data.eventscripts import schema as eventscripts_schema
from scripts.generated_data.schema import DependencyGraph
from scripts.generated_data.shops import schema as shops_schema
from scripts.generated_data.supports import schema as supports_schema
from scripts.generated_data.traps import schema as traps_schema
from scripts.generated_data.units import schema as units_schema
from scripts.generated_data.tests._util import fixture_path, scratch_dir

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
        records, diagnostics, _load_dependency_records(dep_overrides),
        use_supplied_dependencies=True, **kwargs
    )
    return records, diagnostics


def _messages(diagnostics):
    return [str(e) for e in diagnostics.errors]


class ChapterBundleValidFixtureTests(unittest.TestCase):
    def test_single_bundle_uses_declared_sources_unless_test_hook_is_explicit(self):
        records = chapterbundle_schema.load_records(cb_fixture("valid.json"))
        records[0].tables_by_name["units"].source = (
            "scripts/generated_data/tests/fixtures/chapterbundle/missing.json"
        )
        diagnostics = DiagnosticCollector()
        kwargs = {
            "chapters_header": cb_fixture("chapters.h"),
            "chapter_settings_path": cb_fixture("chapter_settings.json"),
            "asset_table_path": cb_fixture("data_8B363C.c"),
        }
        chapterbundle_schema.validate(records, diagnostics, _load_dependency_records(), **kwargs)
        self.assertTrue(
            any(
                error.reference_path == "bundles[chapter=CHAPTER_EL].tables.units.source"
                for error in diagnostics.errors
            ),
            _messages(diagnostics),
        )

        diagnostics = DiagnosticCollector()
        chapterbundle_schema.validate(
            records,
            diagnostics,
            _load_dependency_records(),
            use_supplied_dependencies=True,
            **kwargs
        )
        self.assertTrue(diagnostics.ok, _messages(diagnostics))

    def test_valid_fixture_has_no_diagnostics(self):
        records = chapterbundle_schema.load_records(cb_fixture("valid.json"))
        records[0].chapter_objectives = chapterbundle_schema.TableRef(
            "chapterobjectives",
            "scripts/generated_data/tests/fixtures/chapterbundle/deps_chapterobjectives.json",
            records[0].loc,
            ["ChapterObjectives_EL"],
            [records[0].loc],
            records[0].loc,
        )
        diagnostics = DiagnosticCollector()
        chapterbundle_schema.validate(
            records,
            diagnostics,
            _load_dependency_records(),
            chapters_header=cb_fixture("chapters.h"),
            chapter_settings_path=cb_fixture("chapter_settings.json"),
            asset_table_path=cb_fixture("data_8B363C.c"),
        )
        self.assertTrue(diagnostics.ok, msg=_messages(diagnostics))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].chapter.id, "CHAPTER_EL")
        self.assertEqual(records[0].manifest.symbol, "ELEvents")
        self.assertEqual(
            sorted(records[0].tables_by_name),
            ["eventlists", "eventscripts", "shops", "traps", "units"],
        )

    def test_autoplay_strategy_owner_source_is_validated(self):
        def validate_owner(records):
            diagnostics = DiagnosticCollector()
            chapterbundle_schema.validate(
                records,
                diagnostics,
                _load_dependency_records(),
                use_supplied_dependencies=True,
                chapters_header=cb_fixture("chapters.h"),
                chapter_settings_path=cb_fixture("chapter_settings.json"),
                asset_table_path=cb_fixture("data_8B363C.c"),
            )
            return diagnostics

        records = chapterbundle_schema.load_records(cb_fixture("valid.json"))
        loc = records[0].loc
        records[0].autoplay_strategies = chapterbundle_schema.TableRef(
            "autoplaystrategies",
            cb_fixture("deps_autoplaystrategies.json"),
            loc,
            ["AutoplayStrategies_EL"],
            [loc],
            loc,
        )
        diagnostics = validate_owner(records)
        self.assertTrue(diagnostics.ok, _messages(diagnostics))

        stale = copy.deepcopy(records)
        stale[0].autoplay_strategies.symbols = ["AutoplayStrategies_Stale"]
        diagnostics = validate_owner(stale)
        self.assertTrue(
            any(
                error.reference_path
                == "bundles[chapter=CHAPTER_EL].autoplayStrategies.symbols[AutoplayStrategies_Stale]"
                for error in diagnostics.errors
            ),
            _messages(diagnostics),
        )

        undeclared = copy.deepcopy(records)
        undeclared[0].autoplay_strategies.symbols = []
        diagnostics = validate_owner(undeclared)
        self.assertTrue(
            any(
                "contains chapter 'CHAPTER_EL' symbol 'AutoplayStrategies_EL'"
                in error.message
                for error in diagnostics.errors
            ),
            _messages(diagnostics),
        )

        wrong_source = copy.deepcopy(records)
        wrong_source[0].autoplay_strategies.source = cb_fixture("deps_units.json")
        diagnostics = validate_owner(wrong_source)
        self.assertTrue(
            any(
                error.reference_path
                == "bundles[chapter=CHAPTER_EL].autoplayStrategies.source"
                and "unexpected $schema" in error.message
                for error in diagnostics.errors
            ),
            _messages(diagnostics),
        )

        wrong_chapter = copy.deepcopy(records)
        wrong_chapter[0].autoplay_strategies.source = fixture_path(
            "autoplaystrategies", "valid.json"
        )
        diagnostics = validate_owner(wrong_chapter)
        self.assertTrue(
            any(
                "is not a record for chapter 'CHAPTER_EL'" in error.message
                for error in diagnostics.errors
            ),
            _messages(diagnostics),
        )

    def test_multi_bundle_dependencies_follow_each_table_ref_source(self):
        first = chapterbundle_schema.load_records(cb_fixture("valid.json"))[0]
        second = copy.deepcopy(first)
        second.chapter.id = "CHAPTER_EL_OTHER"
        second.chapter.chapter_settings_index = 1
        second.chapter.internal_name = "EL1"
        second.chapter.map_event_data_id = 1
        second.tables_by_name["units"].source = (
            "scripts/generated_data/tests/fixtures/chapterbundle/deps_units_second.json"
        )
        records = chapterbundle_schema.ChapterBundleRecords([first, second])
        diagnostics = DiagnosticCollector()
        chapterbundle_schema.validate(
            records,
            diagnostics,
            {},
            chapters_header=cb_fixture("chapters.h"),
            chapter_settings_path=cb_fixture("chapter_settings.json"),
            asset_table_path=cb_fixture("data_8B363C_two_events.c"),
        )
        self.assertTrue(diagnostics.ok, msg=_messages(diagnostics))
        self.assertEqual(
            chapterbundle_schema.resolve_bundle_dependencies(first)["units"][0].units[0].x_position,
            1,
        )
        self.assertEqual(
            chapterbundle_schema.resolve_bundle_dependencies(second)["units"][0].units[0].x_position,
            2,
        )

        second.tables_by_name["units"].source = "scripts/generated_data/tests/fixtures/chapterbundle/missing.json"
        diagnostics = DiagnosticCollector()
        chapterbundle_schema.validate(
            records,
            diagnostics,
            {},
            chapters_header=cb_fixture("chapters.h"),
            chapter_settings_path=cb_fixture("chapter_settings.json"),
            asset_table_path=cb_fixture("data_8B363C_two_events.c"),
        )
        self.assertTrue(
            any(
                error.reference_path == "bundles[chapter=CHAPTER_EL_OTHER].tables.units.source"
                and error.location == second.tables_by_name["units"].source_loc
                for error in diagnostics.errors
            ),
            _messages(diagnostics),
        )

        second.tables_by_name["units"].source = (
            "scripts/generated_data/tests/fixtures/chapterbundle/deps_units.json"
        )
        second.tables_by_name["units"].symbols = ["UnitDef_EL_CrossSource"]
        diagnostics = DiagnosticCollector()
        chapterbundle_schema.validate(
            records,
            diagnostics,
            {},
            chapters_header=cb_fixture("chapters.h"),
            chapter_settings_path=cb_fixture("chapter_settings.json"),
            asset_table_path=cb_fixture("data_8B363C_two_events.c"),
        )
        self.assertTrue(
            any(
                error.reference_path == "tables.units.symbols[UnitDef_EL_CrossSource]"
                for error in diagnostics.errors
            ),
            _messages(diagnostics),
        )

        duplicate = copy.deepcopy(second)
        diagnostics = DiagnosticCollector()
        chapterbundle_schema.validate(
            chapterbundle_schema.ChapterBundleRecords([first, second, duplicate]),
            diagnostics,
            {},
            chapters_header=cb_fixture("chapters.h"),
            chapter_settings_path=cb_fixture("chapter_settings.json"),
            asset_table_path=cb_fixture("data_8B363C_two_events.c"),
        )
        self.assertTrue(
            any(
                error.reference_path == "bundles[chapter=CHAPTER_EL_OTHER].chapter"
                for error in diagnostics.errors
            ),
            _messages(diagnostics),
        )

    def test_multi_bundle_inventory_tracks_source_and_symbol_identity(self):
        first = chapterbundle_schema.load_records(cb_fixture("valid.json"))[0]
        second = copy.deepcopy(first)
        second.chapter.id = "CHAPTER_EL_OTHER"
        second.source_path = cb_fixture("deps_units_second.json")
        second.tables_by_name["units"].symbols = ["UnitDef_EL_Alternate"]
        records = chapterbundle_schema.ChapterBundleRecords([first, second])
        inventory = chapterbundle_schema.ChapterBundleTableSchema().build_inventory(records)

        self.assertIn(
            "scripts/generated_data/tests/fixtures/chapterbundle/deps_units_second.json",
            inventory,
        )
        self.assertIn("UnitDef_EL_Alternate", inventory)
        changed = copy.deepcopy(second)
        changed.tables_by_name["units"].symbols = ["UnitDef_EL_Changed"]
        changed_inventory = chapterbundle_schema.ChapterBundleTableSchema().build_inventory(
            chapterbundle_schema.ChapterBundleRecords([first, changed])
        )
        self.assertNotEqual(inventory, changed_inventory)

    def test_inventory_paths_are_checkout_independent_and_reject_outside_root(self):
        def copy_bundle_checkout(checkout_root, multiple):
            source = repo_path("src", "data", "ch2_bundle.json")
            with open(source, encoding="utf-8") as handle:
                bundle = json.load(handle)
            paths = [table["source"] for table in bundle["tables"].values()]
            paths.append(bundle["supportOwners"]["source"])
            if "chapterObjectives" in bundle:
                paths.append(bundle["chapterObjectives"]["source"])
            if "autoplayStrategies" in bundle:
                paths.append(bundle["autoplayStrategies"]["source"])
            for path in paths:
                destination = os.path.join(checkout_root, path)
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                shutil.copyfile(repo_path(path), destination)

            bundle_dir = os.path.join(checkout_root, "src", "data")
            first = os.path.join(bundle_dir, "ch2_bundle.json")
            with open(first, "w", encoding="utf-8") as handle:
                json.dump(bundle, handle)
            if not multiple:
                return chapterbundle_schema.load_records(first, repository_root=checkout_root)

            second_bundle = copy.deepcopy(bundle)
            second_bundle["chapter"]["id"] = "CHAPTER_L_3"
            with open(os.path.join(bundle_dir, "l3_bundle.json"), "w", encoding="utf-8") as handle:
                json.dump(second_bundle, handle)
            return chapterbundle_schema.load_records(bundle_dir, repository_root=checkout_root)

        with scratch_dir() as tmp:
            first_root = os.path.join(tmp, "first-checkout")
            second_root = os.path.join(tmp, "second-checkout")
            os.mkdir(first_root)
            os.mkdir(second_root)
            schema = chapterbundle_schema.ChapterBundleTableSchema()
            single_first = schema.build_inventory(copy_bundle_checkout(first_root, multiple=False))
            single_second = schema.build_inventory(copy_bundle_checkout(second_root, multiple=False))
            self.assertEqual(single_first, single_second)
            self.assertIn("src/data/ch2_bundle.json", single_first)
            self.assertNotIn(first_root, single_first)

            multi_first_records = copy_bundle_checkout(first_root, multiple=True)
            multi_second_records = copy_bundle_checkout(second_root, multiple=True)
            multi_first = schema.build_inventory(multi_first_records)
            multi_second = schema.build_inventory(multi_second_records)
            self.assertEqual(multi_first, multi_second)
            self.assertIn("src/data/ch2_bundle.json", multi_first)
            self.assertIn("src/data/l3_bundle.json", multi_first)
            self.assertNotIn(first_root, multi_first)
            unit_source = Path(first_root) / "src" / "data" / "ch2_units.json"
            unit_source.write_text(
                unit_source.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            self.assertNotEqual(multi_first, schema.build_inventory(multi_first_records))

            objective_directory = Path(first_root) / "objective_sources"
            objective_directory.mkdir()
            objective_source = Path(first_root) / "src" / "data" / "chapter_objectives.json"
            shutil.copyfile(objective_source, objective_directory / "default_objectives.json")
            directory_owner = copy.deepcopy(copy_bundle_checkout(first_root, multiple=False)[0])
            directory_owner.chapter_objectives.source = "objective_sources"
            directory_inventory = schema.build_inventory(
                chapterbundle_schema.ChapterBundleRecords([directory_owner])
            )
            self.assertIn("objective_sources/default_objectives.json", directory_inventory)
            (objective_directory / "default_objectives.json").write_text(
                (objective_directory / "default_objectives.json").read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            self.assertNotEqual(
                directory_inventory,
                schema.build_inventory(chapterbundle_schema.ChapterBundleRecords([directory_owner])),
            )

            strategy_source = Path(first_root) / "src" / "data" / "autoplay_strategies.json"
            strategy_inventory = schema.build_inventory(multi_first_records)
            strategy_source.write_text(
                strategy_source.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            self.assertNotEqual(
                strategy_inventory,
                schema.build_inventory(multi_first_records),
            )

            outside = copy.deepcopy(multi_first_records[1])
            outside.source_path = os.path.join(tmp, "outside", "l3_bundle.json")
            with self.assertRaises(GeneratedDataError):
                schema.build_inventory(
                    chapterbundle_schema.ChapterBundleRecords([multi_first_records[0], outside])
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

    def test_event_autoload_slot_sentinels_rejected_as_dependency(self):
        """CHARACTER_EVT_LEADER/ACTIVE/SLOTB/SLOT2 belong to the separate
        ``event_autoload_pid_idx`` enum (active-unit-slot indices, two of
        them negative) -- they share the ``CHARACTER_`` textual prefix
        with real designators but must never be accepted as a
        ``dependencies.characters`` entry."""
        _, diagnostics = _validate("char_evt_sentinels.json")
        self.assertFalse(diagnostics.ok)
        messages = _messages(diagnostics)
        for sentinel in (
            "CHARACTER_EVT_LEADER", "CHARACTER_EVT_ACTIVE", "CHARACTER_EVT_SLOTB", "CHARACTER_EVT_SLOT2",
        ):
            self.assertTrue(
                any("undefined character reference '{}'".format(sentinel) in m for m in messages),
                (sentinel, messages),
            )

    def test_synthetic_sibling_enum_collision_excluded(self):
        """Wiring-level proof (not just the shared reader in isolation):
        a ``dependencies.characters`` entry from the synthetic sibling
        enum (``CHARACTER_SIBLING_FAKE``) must be rejected even when the
        header override is swapped to a mini header that otherwise
        validates the real ``CHARACTER_EIRIKA`` entry fine."""
        _, diagnostics = _validate(
            "synthetic_sibling_enum_dependency.json",
            characters_header=fixture_path("character_refs", "mini_characters_sibling_enum.h"),
        )
        self.assertFalse(diagnostics.ok)
        messages = _messages(diagnostics)
        self.assertTrue(
            any("undefined character reference 'CHARACTER_SIBLING_FAKE'" in m for m in messages), messages
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
    """Loads the 6 chapter-bundle dependency tables plus the real,
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
        self.assertEqual(len(records[0].tables), 5)
        self.assertEqual(records[0].chapter.id, "CHAPTER_L_2")
        self.assertEqual(records[0].manifest.symbol, "Ch2Events")

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
