"""Direct unit tests for ``scripts.generated_data.character_refs`` -- the
shared, enum-scoped ``CHARACTER_*`` designator reader used by every table
that references characters (``characters``, ``units``, ``supports``,
``eventlists``, ``chapterbundle``).

``include/constants/characters.h`` contains two back-to-back ``enum``
blocks that happen to share the ``CHARACTER_`` textual prefix:

    enum { CHARACTER_NONE = 0x00, ..., CHARACTER_SNAG = 0xFF };

    enum event_autoload_pid_idx {
        CHARACTER_EVT_LEADER = 0,
        CHARACTER_EVT_ACTIVE = -1,
        CHARACTER_EVT_SLOTB = -2,
        CHARACTER_EVT_SLOT2 = -3,
    };

A naive ``extract_enum_constants(header, name_prefix="CHARACTER_")`` scan
(line-by-line, prefix-only, blind to enum-block boundaries) would wrongly
admit the second block's event-engine slot indices -- including negative
values -- as if they were valid ``CharacterData`` designators. These tests
prove ``read_character_designators()`` never makes that mistake, both
against the real header and against a synthetic sibling-enum fixture that
isolates the scoping mechanism from the real header's current contents.
"""

import unittest

from scripts.generated_data import character_refs
from scripts.generated_data.tests._util import fixture_path

_MINI_SIBLING_ENUM_HEADER = fixture_path("character_refs", "mini_characters_sibling_enum.h")


class CharacterRefsRealHeaderTests(unittest.TestCase):
    def test_designators_include_primary_block_members(self):
        designators = character_refs.read_character_designators()
        self.assertIn(character_refs.CHARACTER_NONE_NAME, designators)
        self.assertEqual(designators["CHARACTER_NONE"][0], 0)
        self.assertIn("CHARACTER_EIRIKA", designators)
        self.assertEqual(designators["CHARACTER_EIRIKA"][0], 1)
        self.assertIn("CHARACTER_SNAG", designators)

    def test_designators_exclude_event_autoload_sentinels(self):
        designators = character_refs.read_character_designators()
        for sentinel in (
            "CHARACTER_EVT_LEADER", "CHARACTER_EVT_ACTIVE",
            "CHARACTER_EVT_SLOTB", "CHARACTER_EVT_SLOT2",
        ):
            self.assertNotIn(sentinel, designators)

    def test_default_header_argument_matches_module_constant(self):
        self.assertEqual(
            character_refs.read_character_designators(),
            character_refs.read_character_designators(character_refs.CHARACTERS_HEADER),
        )


class CharacterRefsSyntheticSiblingEnumTests(unittest.TestCase):
    """Proves the scoping mechanism itself (anchored on the primary
    block's own opening/closing braces), independent of the real
    header's current contents -- using a minimal fixture that mirrors
    the real collision shape (a named sibling enum reusing the same
    textual prefix, including a negative value)."""

    def test_primary_block_members_present(self):
        designators = character_refs.read_character_designators(_MINI_SIBLING_ENUM_HEADER)
        self.assertEqual(designators["CHARACTER_NONE"][0], 0)
        self.assertEqual(designators["CHARACTER_ALPHA"][0], 1)
        self.assertEqual(designators["CHARACTER_BETA"][0], 2)

    def test_sibling_enum_members_excluded(self):
        designators = character_refs.read_character_designators(_MINI_SIBLING_ENUM_HEADER)
        self.assertNotIn("CHARACTER_SIBLING_LEADER", designators)
        self.assertNotIn("CHARACTER_SIBLING_FAKE", designators)

    def test_sibling_enum_has_exactly_three_entries(self):
        # Guards against the scan silently over- or under-shooting the
        # primary block's boundaries.
        designators = character_refs.read_character_designators(_MINI_SIBLING_ENUM_HEADER)
        self.assertEqual(sorted(designators), ["CHARACTER_ALPHA", "CHARACTER_BETA", "CHARACTER_NONE"])


if __name__ == "__main__":
    unittest.main()
