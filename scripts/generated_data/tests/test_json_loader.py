import unittest

from scripts.generated_data.diagnostics import GeneratedDataError
from scripts.generated_data.json_loader import parse_json_text


class JsonLoaderTests(unittest.TestCase):
    def test_preserves_order_and_scalars(self):
        text = '{"b": 1, "a": [true, false, null, "x", 3.5]}'
        node = parse_json_text(text, path="doc.json")
        self.assertEqual(list(node.keys()), ["b", "a"])
        self.assertEqual(node.get("b").as_int(), 1)
        items = node.get("a").as_list()
        self.assertEqual(items[0].value, True)
        self.assertEqual(items[1].value, False)
        self.assertIsNone(items[2].value)
        self.assertEqual(items[3].as_str(), "x")
        self.assertAlmostEqual(items[4].value, 3.5)

    def test_line_and_column_tracking(self):
        text = '{\n  "owner": "CHARACTER_EIRIKA",\n  "count": 7\n}'
        node = parse_json_text(text, path="doc.json")
        owner_node = node.get("owner")
        self.assertEqual(owner_node.loc.line, 2)
        # column points at the opening quote of the string value.
        self.assertEqual(owner_node.loc.column, text.splitlines()[1].index('"CHARACTER') + 1)
        count_node = node.get("count")
        self.assertEqual(count_node.loc.line, 3)

    def test_duplicate_key_raises_with_both_locations(self):
        text = '{\n  "owner": "A",\n  "owner": "B"\n}'
        with self.assertRaises(GeneratedDataError) as ctx:
            parse_json_text(text, path="dup.json")
        message = str(ctx.exception)
        self.assertIn("duplicate key 'owner'", message)
        self.assertIn("dup.json:2", message)  # first definition location
        self.assertEqual(ctx.exception.location.line, 3)  # the duplicate itself

    def test_require_missing_field_reports_location(self):
        node = parse_json_text('{"a": 1}', path="doc.json")
        with self.assertRaises(GeneratedDataError) as ctx:
            node.require("missing")
        self.assertIn("missing required field 'missing'", str(ctx.exception))
        self.assertEqual(ctx.exception.location, node.loc)

    def test_type_mismatch_helpers_raise(self):
        node = parse_json_text('{"a": "text", "b": 1}', path="doc.json")
        with self.assertRaises(GeneratedDataError):
            node.get("a").as_int()
        with self.assertRaises(GeneratedDataError):
            node.get("b").as_str()

    def test_native_round_trip(self):
        text = '{"list": [1, 2, {"k": "v"}]}'
        node = parse_json_text(text, path="doc.json")
        self.assertEqual(node.native(), {"list": [1, 2, {"k": "v"}]})

    def test_trailing_content_rejected(self):
        with self.assertRaises(GeneratedDataError):
            parse_json_text('{"a": 1} garbage', path="doc.json")

    def test_line_comment_supported(self):
        text = '{\n  // a comment\n  "a": 1\n}'
        node = parse_json_text(text, path="doc.json")
        self.assertEqual(node.get("a").as_int(), 1)


if __name__ == "__main__":
    unittest.main()
