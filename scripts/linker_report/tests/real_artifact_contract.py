"""Shared assertions for modern linker-report integration tests."""

REQUIRED_OVERLAYS = {
    "ewram_overlay_0",
    "ewram_overlay_gameending",
    "ewram_overlay_bmstart",
    "ewram_overlay_gamestartsave",
    "ewram_overlay_gamestart",
    "ewram_overlay_banim",
    "ewram_overlay_sio",
    "ewram_overlay_worldmap",
    "ewram_overlay_debugtools",
}


def assert_modern_overlays(test, overlays):
    by_name = {overlay["name"]: overlay for overlay in overlays}
    test.assertEqual(len(by_name), len(overlays))
    test.assertTrue(REQUIRED_OVERLAYS <= by_name.keys(), REQUIRED_OVERLAYS - by_name.keys())
    for overlay in overlays:
        test.assertEqual(overlay["address"], 0x02000000)
        test.assertGreaterEqual(overlay["size_bytes"], 0)
        test.assertLessEqual(overlay["address"] + overlay["size_bytes"], 0x02040000)


def assert_iwram_margin(test, report):
    iwram = next(reg for reg in report["regions"] if reg["name"] == "iwram")
    test.assertTrue(iwram["static_budget_available"])
    test.assertFalse(iwram["overflow"])
    test.assertFalse(iwram["stack_margin_violation"])
    test.assertEqual(iwram["reserved_stack_bytes"], 0x200)
    test.assertEqual(iwram["minimum_user_stack_margin_bytes"], 0x1000)
    test.assertEqual(
        iwram["physical_free_bytes"], iwram["capacity_bytes"] - iwram["occupied_bytes"]
    )
    test.assertEqual(
        iwram["reserved_stack_bytes"],
        iwram["origin"] + iwram["capacity_bytes"] - iwram["user_stack_pointer_address"],
    )
    test.assertEqual(
        iwram["minimum_user_stack_margin_bytes"],
        iwram["user_stack_pointer_address"] - iwram["static_limit_address"],
    )
    test.assertEqual(
        iwram["usable_static_headroom_bytes"],
        iwram["user_stack_pointer_address"] - iwram["static_end_address"],
    )
    test.assertGreaterEqual(
        iwram["usable_static_headroom_bytes"], iwram["minimum_user_stack_margin_bytes"]
    )
    test.assertEqual(
        iwram["static_growth_headroom_bytes"],
        iwram["static_limit_address"] - iwram["static_end_address"],
    )


def assert_iwram_symbols(test, report, symbols):
    iwram = next(reg for reg in report["regions"] if reg["name"] == "iwram")
    for symbol, field in (
        ("__iwram_static_end", "static_end_address"),
        ("__iwram_static_limit", "static_limit_address"),
        ("__sp_usr", "user_stack_pointer_address"),
    ):
        test.assertIn(symbol, symbols)
        test.assertEqual(iwram[field], symbols[symbol])


def assert_locale_identity(test, report):
    locales = [section for section in report["sections"] if section["name"] == ".locale_data"]
    elf_locales = [
        section for section in report["elf"]["sections"] if section["name"] == ".locale_data"
    ]
    test.assertEqual(len(locales), 1)
    test.assertEqual(len(elf_locales), 1)
    locale, elf_locale = locales[0], elf_locales[0]
    test.assertGreater(locale["size_bytes"], 0)
    test.assertIn("A", elf_locale["flags"])
    test.assertEqual(locale["address"], elf_locale["address"])
    test.assertEqual(locale["size_bytes"], elf_locale["size_bytes"])
