import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from scripts.localization import schema
from scripts.localization.pseudo import (
    apply_pseudo_policy,
    pseudoize,
    pseudoize_compact,
    pseudoize_catalog,
)
from scripts.localization.schema import SchemaError


class PseudoizeTests(unittest.TestCase):
    def test_deterministic_repeatable(self):
        self.assertEqual(pseudoize("Hello World"), pseudoize("Hello World"))

    def test_wrapped_in_brackets(self):
        result = pseudoize("Hello")
        self.assertTrue(result.startswith("[["))
        self.assertTrue(result.endswith("]]"))

    def test_ascii_only(self):
        result = pseudoize("Hello, World! 123")
        for ch in result:
            self.assertTrue(0x20 <= ord(ch) <= 0x7E or ch == "\n")

    def test_placeholder_tokens_preserved_verbatim(self):
        result = pseudoize("Sample {0} of {1} things")
        self.assertIn("{0}", result)
        self.assertIn("{1}", result)

    def test_placeholder_count_preserved(self):
        text = "{0}{1}{2}"
        result = pseudoize(text)
        for token in ("{0}", "{1}", "{2}"):
            self.assertEqual(result.count(token), 1)

    def test_control_token_newline_preserved(self):
        result = pseudoize("Line one\nLine two")
        self.assertEqual(result.count("\n"), 1)

    def test_length_expands(self):
        text = "aeiou aeiou aeiou"
        result = pseudoize(text)
        # Bracket wrapping alone adds 4; vowel doubling should add more.
        self.assertGreater(len(result), len(text) + 4)

    def test_not_identical_to_source(self):
        self.assertNotEqual(pseudoize("English"), "English")

    def test_empty_string(self):
        self.assertEqual(pseudoize(""), "[[]]")

    def test_pseudoize_catalog_preserves_keys(self):
        catalog = {"a.b": "Hello", "c.d": "World"}
        result = pseudoize_catalog(catalog)
        self.assertEqual(set(result.keys()), set(catalog.keys()))
        for key in catalog:
            self.assertEqual(result[key], pseudoize(catalog[key]))

    def test_apply_policy_defaults_to_transform(self):
        self.assertEqual(apply_pseudo_policy("English"), pseudoize("English"))

    def test_apply_policy_preserves_exact_bytes(self):
        text = "2005/02/04(FRI) 16:55:40"
        self.assertEqual(
            apply_pseudo_policy(text, schema.PSEUDO_POLICY_PRESERVE),
            text,
        )

    def test_compact_policy_keeps_pseudo_signal_without_expansion(self):
        text = "Receiving {0}\nNow"
        result = pseudoize_compact(text)
        self.assertEqual(result, "ReCeIvInG {0}\nnOw")
        self.assertEqual(
            apply_pseudo_policy(text, schema.PSEUDO_POLICY_COMPACT),
            result,
        )
        self.assertEqual(len(result), len(text))

    def test_invalid_policy_rejected(self):
        with self.assertRaises(SchemaError):
            apply_pseudo_policy("English", "decorate-sometimes")

    def test_catalog_policy_only_changes_selected_key(self):
        catalog = {
            "identifier": "2005/02/04(FRI) 16:55:40",
            "label": "English",
        }
        result = pseudoize_catalog(
            catalog,
            {"identifier": schema.PSEUDO_POLICY_PRESERVE},
        )
        self.assertEqual(result["identifier"], catalog["identifier"])
        self.assertEqual(result["label"], pseudoize(catalog["label"]))


if __name__ == "__main__":
    unittest.main()
