"""Locale probe layout and exact-ELF symbolic-binding contract tests."""

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
SCENARIOS_DIR = PLAYTEST_DIR / "scenarios"
FINGERPRINTS_DIR = PLAYTEST_DIR / "fingerprints"
C_FIXTURES_DIR = Path(__file__).resolve().parent / "c"
DRIVER_SRC = C_FIXTURES_DIR / "expansion_language_menu_probe_offsets_driver.c"
CC = shutil.which("gcc") or shutil.which("cc")

sys.path.insert(0, str(PLAYTEST_DIR))
import gba_playtest  # noqa: E402

PROBE_SYMBOL = "gExpansionLanguageMenuProbe"
PROBE_EXPRESSION_RE = re.compile(
    rf"^{PROBE_SYMBOL}\+(0x[0-9a-fA-F]+)$"
)
PROBE_FIELDS = [
    "active",
    "settingsActive",
    "promptShown",
    "autoSelected",
    "promptReason",
    "prefsState",
    "selectedLocale",
    "currentLocale",
    "enabledLocaleCount",
    "cacheGeneration",
    "startupRunCount",
    "settingsOpenCount",
    "settingsChangeCount",
    "needsPreferenceRepair",
]
PROBE_FIELD_SIZES = {
    "active": 1,
    "settingsActive": 1,
    "promptShown": 1,
    "autoSelected": 1,
    "promptReason": 1,
    "prefsState": 1,
    "selectedLocale": 1,
    "currentLocale": 1,
    "enabledLocaleCount": 1,
    "cacheGeneration": 2,
    "startupRunCount": 2,
    "settingsOpenCount": 2,
    "settingsChangeCount": 2,
    "needsPreferenceRepair": 1,
}
EWRAM_RANGE = (0x02000000, 0x02040000)


def language_probe_offset(address):
    match = PROBE_EXPRESSION_RE.fullmatch(address) if isinstance(address, str) else None
    if match is not None:
        return int(match.group(1), 16)
    if isinstance(address, str) and address.startswith("0x"):
        value = int(address, 16)
        if EWRAM_RANGE[0] <= value < EWRAM_RANGE[1]:
            raise AssertionError(
                f"stale literal EWRAM probe address {address}; use "
                f"{PROBE_SYMBOL}+offsetof(field)"
            )
        return None
    raise AssertionError(
        f"unsupported locale probe address {address!r}; expected {PROBE_SYMBOL}+0xNN "
        "or a non-EWRAM literal such as cart SRAM"
    )


@unittest.skipIf(CC is None, "no host C compiler available")
class ExpansionLanguageMenuProbeSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        binary = (
            C_FIXTURES_DIR / "expansion_language_menu_probe_offsets_driver.bin"
        )
        result = subprocess.run(
            [CC, "-I", str(REPO_ROOT / "include"), "-o", str(binary), str(DRIVER_SRC)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(
                "failed to compile the real ExpansionLanguageMenuProbe header:\n"
                f"{result.stderr}"
            )
        try:
            run = subprocess.run(
                [str(binary)], capture_output=True, text=True, check=True
            )
        finally:
            binary.unlink(missing_ok=True)
        layout = {}
        for line in run.stdout.strip().splitlines():
            name, _, value = line.partition("=")
            layout[name] = int(value)
        cls.offsets = {name: layout[name] for name in PROBE_FIELDS}
        cls.struct_size = layout["sizeof"]

    def _probe_files(self):
        return sorted(SCENARIOS_DIR.glob("locale-*.json")) + sorted(
            FINGERPRINTS_DIR.glob("locale-*.json")
        )

    def _probes(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        for checkpoint in data["checkpoints"]:
            yield from checkpoint.get("probes", [])

    def test_driver_reports_current_probe_layout(self):
        offsets = [self.offsets[name] for name in PROBE_FIELDS]
        self.assertEqual(offsets, sorted(offsets))
        self.assertEqual(self.struct_size, 20)

    def test_all_locale_scenarios_and_fingerprints_use_symbolic_probe_addresses(self):
        files = self._probe_files()
        self.assertGreaterEqual(len(files), 52)
        for path in files:
            for probe in self._probes(path):
                try:
                    language_probe_offset(probe["address"])
                except AssertionError as error:
                    self.fail(f"{path.name}: {error}")

    def test_symbolic_offsets_match_real_fields_sizes_and_bounds(self):
        offset_to_field = {offset: field for field, offset in self.offsets.items()}
        for path in self._probe_files():
            for probe in self._probes(path):
                offset = language_probe_offset(probe["address"])
                if offset is None:
                    continue
                self.assertIn(
                    offset,
                    offset_to_field,
                    f"{path.name}: {probe['address']} is not a real probe field offset",
                )
                field = offset_to_field[offset]
                size = int(probe["size"])
                self.assertEqual(
                    size,
                    PROBE_FIELD_SIZES[field],
                    f"{path.name}: {field} uses size {size}",
                )
                self.assertLessEqual(offset + size, self.struct_size)

    def test_runtime_parser_binds_scenario_and_fingerprint_from_exact_elf_symbols(self):
        base = 0x02032120

        def resolver(symbol):
            self.assertEqual(symbol, PROBE_SYMBOL)
            return base, self.struct_size

        scenario_path = SCENARIOS_DIR / "locale-cjk-first-start-ja-modern-debug.json"
        fingerprint_path = (
            FINGERPRINTS_DIR / "locale-cjk-first-start-ja-modern-debug.json"
        )
        scenario = gba_playtest.parse_scenario_data(
            json.loads(scenario_path.read_text(encoding="utf-8")),
            str(scenario_path),
            resolver,
        )
        expected = gba_playtest.validate_fingerprint(
            json.loads(fingerprint_path.read_text(encoding="utf-8")),
            str(fingerprint_path),
            resolver,
            policy="behavior",
        )
        self.assertEqual(scenario.checkpoints[0].probes[0].address, base)
        self.assertEqual(
            expected["checkpoints"][0]["probes"][0]["address"],
            f"{PROBE_SYMBOL}+0x00",
        )

    def test_rebased_symbols_preserve_capture_and_fingerprint_bindings(self):
        data = {
            "schema_version": 1,
            "name": "rebased-symbol-probe",
            "frames": [],
            "checkpoints": [
                {
                    "name": "probe",
                    "frame": 1,
                    "framebuffer": False,
                    "probes": [
                        {
                            "address": f"{PROBE_SYMBOL}+0x04",
                            "size": 1,
                            "expected": "0x01",
                        }
                    ],
                }
            ],
        }

        captures = []
        for base in (0x02030000, 0x02034000):
            scenario = gba_playtest.parse_scenario_data(
                data,
                symbol_resolver=lambda symbol, base=base: (base, self.struct_size),
            )
            self.assertEqual(scenario.checkpoints[0].probes[0].address, base + 4)
            captures.append(
                gba_playtest._parse_backend_output(
                    "CHECKPOINT\t0\t1\t0000000000000000\nPROBE\t0\t0\t1\n",
                    scenario,
                )
            )

        self.assertEqual(captures[0], captures[1])
        self.assertEqual(
            captures[0]["checkpoints"][0]["probes"][0]["address"],
            f"{PROBE_SYMBOL}+0x04",
        )
        self.assertEqual(
            gba_playtest.compare_fingerprints(
                {
                    **captures[0],
                    "rom": {"sha1": "0" * 40, "size": 1, "title": "", "game_code": ""},
                },
                {
                    **captures[1],
                    "rom": {"sha1": "1" * 40, "size": 1, "title": "", "game_code": ""},
                },
                policy="behavior",
            ),
            [],
        )

    def test_stale_literal_negative_control_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, "stale literal EWRAM"):
            language_probe_offset("0x02031a94")

    def test_symbol_expression_loads_without_elf_but_execution_needs_binding(self):
        data = {
            "schema_version": 1,
            "name": "symbol-probe",
            "frames": [],
            "checkpoints": [
                {
                    "name": "probe",
                    "frame": 1,
                    "framebuffer": False,
                    "probes": [
                        {
                            "address": f"{PROBE_SYMBOL}+0x14",
                            "size": 1,
                        }
                    ],
                }
            ],
        }
        scenario = gba_playtest.parse_scenario_data(data)
        self.assertIsNone(scenario.checkpoints[0].probes[0].address)
        self.assertEqual(
            scenario.checkpoints[0].probes[0].binding,
            f"{PROBE_SYMBOL}+0x14",
        )
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "supply.*--elf"):
            import tempfile

            with tempfile.TemporaryDirectory(dir=REPO_ROOT / "build") as work:
                gba_playtest._write_plan(Path(work) / "plan.txt", scenario)
        with self.assertRaisesRegex(gba_playtest.PlaytestError, "past the"):
            gba_playtest.parse_scenario_data(
                data, symbol_resolver=lambda symbol: (0x02030000, self.struct_size)
            )


if __name__ == "__main__":
    unittest.main()
