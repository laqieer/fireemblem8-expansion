import re
import shutil
import subprocess
import unittest
from pathlib import Path

from scripts.generated_data.eventlists.generate import generate_c_source
from scripts.generated_data.eventlists.schema import load_records
from scripts.generated_data.tests._util import fixture_path

ROOT = Path(__file__).resolve().parents[3]


class GenerateDeterminismTests(unittest.TestCase):
    def test_generation_is_repeatable(self):
        records = load_records(fixture_path("eventlists", "valid.json"))
        first = generate_c_source(records, "fixtures/eventlists/valid.json")
        second = generate_c_source(records, "fixtures/eventlists/valid.json")
        self.assertEqual(first, second)

    def test_generated_output_contains_expected_macro_calls(self):
        records = load_records(fixture_path("eventlists", "valid.json"))
        content = generate_c_source(records, "fixtures/eventlists/valid.json")
        self.assertIn("CONST_DATA EventListScr EventListScr_EL_Turn[] = {", content)
        self.assertIn("TURN(0, EventScr_EL_Turn1, 1, 0, FACTION_ID_BLUE)", content)
        self.assertIn("CHAR(EVFLAG_TMP(7), EventScr_EL_Talk1, CHARACTER_EIRIKA, CHARACTER_ROSS)", content)
        self.assertIn("Village(EVFLAG_TMP(9), EventScr_EL_Village1, 4, 2)", content)
        self.assertIn("Armory(ShopList_EL_Armory, 5, 7)", content)
        self.assertIn("DefeatAll(EventScr_EL_Ending)", content)
        self.assertIn("CauseGameOverIfLordDies\n", content)
        self.assertIn("    END_MAIN\n", content)

    def test_each_list_terminates_with_end_main(self):
        records = load_records(fixture_path("eventlists", "valid.json"))
        content = generate_c_source(records, "fixtures/eventlists/valid.json")
        for lst in records.lists:
            block_start = content.index("{}[] = {{".format(lst.symbol))
            block_end = content.index("};", block_start)
            block = content[block_start:block_end]
            self.assertIn("END_MAIN", block)
            self.assertNotIn("END_MAIN,", block)

    def test_tutorial_array_rendered_as_pointer_array_with_null_terminator(self):
        records = load_records(fixture_path("eventlists", "valid.json"))
        content = generate_c_source(records, "fixtures/eventlists/valid.json")
        self.assertIn("CONST_DATA EventListScr * EventListScr_EL_Tutorial[] = {", content)
        self.assertIn("EventScr_EL_Tutorial1,", content)
        self.assertIn("EventScr_EL_Tutorial30,\n    NULL\n", content)

    def test_manifest_struct_rendered_with_designated_fields(self):
        records = load_records(fixture_path("eventlists", "valid.json"))
        content = generate_c_source(records, "fixtures/eventlists/valid.json")
        self.assertIn("CONST_DATA struct ChapterEventGroup ELEvents = {", content)
        self.assertIn(".turnBasedEvents = EventListScr_EL_Turn,", content)
        self.assertIn(".playerUnitsChoice1InEncounter = NULL,", content)
        self.assertIn(".beginningSceneEvents = EventScr_EL_Beginning,", content)
        self.assertIn(".endingSceneEvents = EventScr_EL_Ending,", content)

    def test_real_ch2_source_matches_hand_written_text_shape(self):
        import os

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        source = os.path.join(repo_root, "src", "data", "ch2_eventlists.json")
        records = load_records(source)
        content = generate_c_source(records, source)
        self.assertIn("TURN(0, EventScr_Ch2_Turn1Player, 1, 0, FACTION_ID_BLUE)", content)
        self.assertIn("CHAR(EVFLAG_TMP(7), EventScr_Ch2_Talk_EirikaRoss, CHARACTER_EIRIKA, CHARACTER_ROSS)", content)
        self.assertIn("Village(EVFLAG_TMP(9), EventScr_Ch2_Village1, 4, 2)", content)
        self.assertIn("Armory(ShopList_Event_Ch2Armory, 5, 7)", content)
        self.assertIn("DefeatAll(EventScr_Ch2_EndingScene)", content)
        self.assertIn("CauseGameOverIfLordDies\n", content)
        self.assertIn("CONST_DATA struct ChapterEventGroup Ch2Events = {", content)

    def test_typed_helpers_lower_to_established_macros(self):
        records = load_records(fixture_path("eventlists", "helpers_valid.json"))
        content = generate_c_source(records, "fixtures/eventlists/helpers_valid.json")
        self.assertIn("extern EventListScr EventScr_EL_HelperBgm[];", content)
        self.assertLess(
            content.index("extern EventListScr EventScr_EL_HelperBgm[];"),
            content.index("CONST_DATA EventListScr EventListScr_EL_Turn[] = {"),
        )
        for expected in (
            "Armory(ShopList_EL_Armory, 5, 7)",
            "Vendor(ShopList_EL_Armory, 6, 7)",
            "SecretShop(ShopList_EL_Armory, 7, 7)",
            "AREA(EVFLAG_TMP(12), EventScr_EL_Village1, 0, 0, 3, 3)",
            "AFEV(EVFLAG_TMP(13), EventScr_EL_Ending, EVFLAG_DEFEAT_ALL)",
            "ENUT(EVFLAG_TMP(14))",
            "ENUF(EVFLAG_TMP(14))",
            "SPAWN_ENEMY(CHARACTER_ROSS, 10, 10)",
            "LOAD1(1, UnitDef_EL_Ally)",
            "MUSC(SONG_RAID)",
            "EvtBgmFadeIn(SONG_RAID, 8)",
            "MUSS(SONG_RAID)",
            "MURE(2)",
            "SET_HP(CHARACTER_EIRIKA)",
            "WARP_OUT(3, 4)",
        ):
            self.assertIn(expected, content)
        self.assertEqual(content.count("    ENDA\n"), 5)

    def test_valid_typed_helper_fixture_compiles_with_song_constants(self):
        compiler = shutil.which("gcc") or shutil.which("cc")
        if compiler is None:
            self.skipTest("no host C compiler")

        records = load_records(fixture_path("eventlists", "helpers_valid.json"))
        content = generate_c_source(records, "fixtures/eventlists/helpers_valid.json")
        self.assertIn('#include "constants/songs.h"', content)

        symbols = sorted(
            set(re.findall(r"\b(?:EventScr|ShopList|UnitDef|TrapData)_[A-Za-z0-9_]+", content))
        )
        declarations = "".join(
            "extern EventListScr {}[];\n".format(symbol) for symbol in symbols
        )
        content = content.replace(
            '#include "constants/songs.h"\n\n',
            '#include "constants/songs.h"\n\n{}\n'.format(declarations),
            1,
        )
        result = subprocess.run(
            [
                compiler,
                "-std=gnu89",
                "-fsyntax-only",
                "-w",
                "-Iinclude",
                "-I.",
                "-x",
                "c",
                "-",
            ],
            cwd=ROOT,
            input=content,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
