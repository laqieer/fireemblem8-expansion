import unittest

from scripts.generated_data.diagnostics import DiagnosticCollector
from scripts.generated_data.eventscripts import schema as eventscripts_schema
from scripts.generated_data.tests._util import fixture_path


def _validate(fixture_name):
    records = eventscripts_schema.load_records(fixture_path("eventscripts", fixture_name))
    diagnostics = DiagnosticCollector()
    eventscripts_schema.validate(records, diagnostics)
    return records, diagnostics


class EventScriptsSchemaValidTests(unittest.TestCase):
    def test_valid_fixture_has_no_diagnostics(self):
        records, diagnostics = _validate("valid.json")
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].owner, "turn_based")
        self.assertEqual(records[1].kind, "event_scr_wm")


class EventScriptsSchemaDuplicateTests(unittest.TestCase):
    def test_duplicate_symbol_detected(self):
        _, diagnostics = _validate("duplicate_symbol.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("duplicate event-script symbol 'EventScr_Fixture_TurnOne'" in m for m in messages), messages
        )


class EventScriptsSchemaEnumTests(unittest.TestCase):
    def test_bad_owner_detected(self):
        _, diagnostics = _validate("bad_owner.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("unknown owner 'not_a_real_owner'" in m for m in messages), messages)

    def test_bad_kind_detected(self):
        _, diagnostics = _validate("bad_kind.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("unknown kind 'not_a_real_kind'" in m for m in messages), messages)


class EventScriptsSchemaSymbolRefTests(unittest.TestCase):
    def test_undeclared_symbol_detected(self):
        _, diagnostics = _validate("undeclared_symbol.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(
            any("'EventScr_Totally_Undeclared' is not declared in" in m for m in messages), messages
        )

    def test_missing_header_detected(self):
        _, diagnostics = _validate("missing_header.json")
        self.assertFalse(diagnostics.ok)
        messages = [str(e) for e in diagnostics.errors]
        self.assertTrue(any("does not exist" in m for m in messages), messages)


class EventScriptsRealHeaderTests(unittest.TestCase):
    def test_full_ch2_source_validates_against_real_header(self):
        import os

        repo_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        source = os.path.join(repo_root, "src", "data", "ch2_eventscripts.json")
        records = eventscripts_schema.load_records(source)
        diagnostics = DiagnosticCollector()
        eventscripts_schema.validate(records, diagnostics)
        self.assertTrue(diagnostics.ok, msg=diagnostics.render())
        self.assertEqual(len(records), 43)


if __name__ == "__main__":
    unittest.main()
