import unittest

from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.eventlists import schema as eventlists_schema
from scripts.generated_data.eventscripts import schema as eventscripts_schema
from scripts.generated_data.shops import schema as shops_schema
from scripts.generated_data.traps import schema as traps_schema
from scripts.generated_data.units import schema as units_schema
from scripts.generated_data.tests._util import fixture_path


def _load_dependency_records():
    return {
        "units": units_schema.load_records(fixture_path("eventlists", "deps_units.json")),
        "shops": shops_schema.load_records(fixture_path("eventlists", "deps_shops.json")),
        "traps": traps_schema.load_records(fixture_path("eventlists", "deps_traps.json")),
        "eventscripts": eventscripts_schema.load_records(fixture_path("eventlists", "deps_eventscripts.json")),
    }


def _validate(fixture_name):
    records = eventlists_schema.load_records(fixture_path("eventlists", fixture_name))
    diagnostics = DiagnosticCollector()
    eventlists_schema.validate(records, diagnostics, _load_dependency_records())
    return records, diagnostics


class EventListsSchemaValidTests(unittest.TestCase):
    def test_valid_fixture_has_no_diagnostics(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=[str(e) for e in diagnostics.errors])
        self.assertEqual(len(records), 9)
        self.assertEqual(len(records.lists), 7)
        self.assertEqual(len(records.tutorial.entries), 30)
        self.assertEqual(records.manifest.symbol, "ELEvents")

    def test_macro_call_structural_shape(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=[str(e) for e in diagnostics.errors])
        turn_list = records.lists_by_field["turnBasedEvents"]
        first_call = turn_list.entries[0]
        self.assertEqual(first_call.macro, "TURN")
        self.assertEqual(
            first_call.as_tuple(),
            ("TURN", (("int", 0), ("symbol", "EventScr_EL_Turn1"), ("int", 1), ("int", 0), ("symbol", "FACTION_ID_BLUE"))),
        )
        char_list = records.lists_by_field["characterBasedEvents"]
        char_call = char_list.entries[0]
        self.assertEqual(char_call.args[0].kind, "call")
        self.assertEqual(char_call.args[0].value.macro, "EVFLAG_TMP")

    def test_bare_zero_arg_macro(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=[str(e) for e in diagnostics.errors])
        misc_list = records.lists_by_field["miscBasedEvents"]
        self.assertEqual(misc_list.entries[1].macro, "CauseGameOverIfLordDies")
        self.assertEqual(misc_list.entries[1].args, [])


class EventListsSchemaCrossReferenceTests(unittest.TestCase):
    def test_missing_unit_reference_detected(self):
        _, diagnostics = _validate("missing_unit_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("undefined unit group reference 'UnitDef_EL_DoesNotExist'" in m for m in messages), messages)

    def test_missing_shop_reference_detected(self):
        _, diagnostics = _validate("missing_shop_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("undefined shops reference 'ShopList_EL_DoesNotExist'" in m for m in messages), messages)

    def test_missing_trap_reference_detected(self):
        _, diagnostics = _validate("missing_trap_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("undefined trap reference 'TrapData_EL_DoesNotExist'" in m for m in messages), messages)

    def test_missing_eventscript_reference_detected(self):
        _, diagnostics = _validate("missing_eventscript_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("undefined event-script reference 'EventScr_EL_DoesNotExist'" in m for m in messages), messages
        )

    def test_event_autoload_slot_sentinels_rejected_as_char_macro_arg(self):
        """CHARACTER_EVT_LEADER/ACTIVE/SLOTB/SLOT2 belong to the separate
        ``event_autoload_pid_idx`` enum (active-unit-slot indices, two of
        them negative) -- they share the ``CHARACTER_`` textual prefix
        with real designators but must never be accepted as a ``CHAR()``
        macro's ``pid1``/``pid2`` argument."""
        _, diagnostics = _validate("char_evt_sentinels.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        for sentinel in (
            "CHARACTER_EVT_LEADER", "CHARACTER_EVT_ACTIVE", "CHARACTER_EVT_SLOTB", "CHARACTER_EVT_SLOT2",
        ):
            self.assertTrue(
                any("undefined character reference '{}'".format(sentinel) in m for m in messages),
                (sentinel, messages),
            )

    def test_synthetic_sibling_enum_collision_excluded(self):
        """Wiring-level proof (not just the shared reader in isolation):
        a ``CHAR()`` macro's ``pid1`` referencing the synthetic sibling
        enum's ``CHARACTER_SIBLING_FAKE`` must be rejected even though
        the primary block's own ``CHARACTER_ALPHA``/``CHARACTER_BETA``
        members (sharing the mini header's namespace) are accepted."""
        records = eventlists_schema.load_records(fixture_path("eventlists", "valid.json"))
        char_list = records.lists_by_field["characterBasedEvents"]
        char_call = char_list.entries[0]
        char_call.args[2].value = "CHARACTER_ALPHA"
        char_call.args[3].value = "CHARACTER_SIBLING_FAKE"
        diagnostics = DiagnosticCollector()
        eventlists_schema.validate(
            records, diagnostics, _load_dependency_records(),
            characters_header=fixture_path("character_refs", "mini_characters_sibling_enum.h"),
        )
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("undefined character reference 'CHARACTER_SIBLING_FAKE'" in m for m in messages), messages
        )


class EventListsSchemaMacroArityTypeTests(unittest.TestCase):
    def test_wrong_arity_detected(self):
        _, diagnostics = _validate("wrong_arity.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("expects 5 argument(s), got 4" in m for m in messages), messages)

    def test_wrong_arg_type_detected(self):
        _, diagnostics = _validate("wrong_arg_type.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("expected an integer literal" in m for m in messages), messages)


class EventListsSchemaDuplicateTests(unittest.TestCase):
    def test_duplicate_temp_flag_detected(self):
        _, diagnostics = _validate("duplicate_flag.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("duplicate temporary event flag EVFLAG_TMP(9)" in m for m in messages), messages)

    def test_duplicate_location_coordinate_detected(self):
        _, diagnostics = _validate("duplicate_location.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("duplicate location coordinate (4, 2)" in m for m in messages), messages)

    def test_duplicate_list_symbol_detected(self):
        _, diagnostics = _validate("duplicate_list_symbol.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("duplicate event-list symbol 'EventListScr_EL_Turn'" in m for m in messages), messages)


class EventListsSchemaListCompositionTests(unittest.TestCase):
    def test_macro_not_allowed_in_list_detected(self):
        _, diagnostics = _validate("wrong_macro_in_list.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("macro 'CHAR' is not allowed in list 'turnBasedEvents'" in m for m in messages), messages)

    def test_explicit_end_main_rejected(self):
        _, diagnostics = _validate("explicit_end_main.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("must not include the END_MAIN terminator explicitly" in m for m in messages), messages
        )

    def test_missing_list_field_detected(self):
        _, diagnostics = _validate("missing_list_field.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("missing required event list field 'locationBasedEvents'" in m for m in messages), messages)

    def test_evflag_tmp_out_of_range_detected(self):
        _, diagnostics = _validate("evflag_out_of_range.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("out of the documented free temp-flag range" in m for m in messages), messages)


class EventListsSchemaTutorialTests(unittest.TestCase):
    def test_wrong_count_detected(self):
        _, diagnostics = _validate("tutorial_wrong_count.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("must have exactly 30 entries, got 29" in m for m in messages), messages)

    def test_duplicate_entry_detected(self):
        _, diagnostics = _validate("tutorial_duplicate.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("duplicate tutorial event-script symbol 'EventScr_EL_Tutorial1'" in m for m in messages), messages
        )

    def test_explicit_null_terminator_rejected(self):
        _, diagnostics = _validate("tutorial_explicit_null.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("must not include the NULL terminator explicitly" in m for m in messages), messages)

    def test_wrong_owner_detected(self):
        _, diagnostics = _validate("tutorial_wrong_owner.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("expected 'tutorial'" in m for m in messages), messages)


class EventListsSchemaManifestTests(unittest.TestCase):
    def test_missing_field_detected(self):
        _, diagnostics = _validate("manifest_missing_field.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("missing required field 'endingSceneEvents'" in m for m in messages), messages)

    def test_extra_field_detected(self):
        _, diagnostics = _validate("manifest_extra_field.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("unknown field 'notARealField'" in m for m in messages), messages)

    def test_wrong_list_ref_detected(self):
        _, diagnostics = _validate("manifest_wrong_list_ref.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("declared list symbol is 'EventListScr_EL_Turn'" in m for m in messages), messages
        )


class EventListsSchemaRealCh2SourceTests(unittest.TestCase):
    def test_real_ch2_eventlists_source_validates_clean(self):
        import os

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        source = os.path.join(repo_root, "src", "data", "ch2_eventlists.json")
        records = eventlists_schema.load_records(source)
        diagnostics = DiagnosticCollector()
        dependency_records = {
            "units": units_schema.load_records(os.path.join(repo_root, "src", "data", "ch2_units.json")),
            "shops": shops_schema.load_records(os.path.join(repo_root, "src", "data", "ch2_shops.json")),
            "traps": traps_schema.load_records(os.path.join(repo_root, "src", "data", "ch2_traps.json")),
            "eventscripts": eventscripts_schema.load_records(
                os.path.join(repo_root, "src", "data", "ch2_eventscripts.json")
            ),
        }
        eventlists_schema.validate(records, diagnostics, dependency_records)
        self.assertTrue(diagnostics.ok, msg=[str(e) for e in diagnostics.errors])
        self.assertEqual(len(records.lists), 7)
        self.assertEqual(len(records.tutorial.entries), 30)


if __name__ == "__main__":
    unittest.main()
