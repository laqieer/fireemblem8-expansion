"""Configured nm selection and actionable probe-binding diagnostics."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYTEST_DIR = REPO_ROOT / "tools" / "gba-playtest"
sys.path.insert(0, str(PLAYTEST_DIR))

import gba_playtest  # noqa: E402
import probe_bindings  # noqa: E402


class ProbeBindingToolTests(unittest.TestCase):
    def setUp(self):
        build_root = REPO_ROOT / "build"
        build_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=build_root)
        self.work = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _fake_nm(self, path: Path, address: int = 0x02001234) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "#!/bin/sh\n"
            f"printf '%08x 00000010 B TestSymbol\\n' {address}\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    def test_modern_nm_works_with_empty_path(self):
        nm = self._fake_nm(self.work / "configured-nm")
        with mock.patch.dict(
            os.environ,
            {"PATH": "", "MODERN_NM": str(nm)},
            clear=True,
        ):
            self.assertEqual(
                probe_bindings.resolve_elf_symbol(
                    self.work / "input.elf",
                    "TestSymbol",
                ),
                (0x02001234, 0x10),
            )

    def test_toolchain_root_works_with_empty_path(self):
        toolchain_root = self.work / "toolchain"
        self._fake_nm(toolchain_root / "bin" / "arm-none-eabi-nm")
        with mock.patch.dict(
            os.environ,
            {"PATH": "", "MODERN_TOOLCHAIN_ROOT": str(toolchain_root)},
            clear=True,
        ):
            self.assertEqual(
                probe_bindings.resolve_elf_symbol(
                    self.work / "input.elf",
                    "TestSymbol",
                ),
                (0x02001234, 0x10),
            )

    def test_explicit_nm_precedes_environment(self):
        explicit = self._fake_nm(self.work / "explicit-nm", 0x02002000)
        configured = self._fake_nm(self.work / "configured-nm", 0x02003000)
        with mock.patch.dict(
            os.environ,
            {"PATH": "", "MODERN_NM": str(configured)},
            clear=True,
        ):
            self.assertEqual(
                probe_bindings.resolve_elf_symbol(
                    self.work / "input.elf",
                    "TestSymbol",
                    explicit,
                ),
                (0x02002000, 0x10),
            )

    def test_missing_nm_is_a_probe_binding_error(self):
        missing = self.work / "missing-nm"
        with self.assertRaisesRegex(
            probe_bindings.ProbeBindingError,
            r"cannot launch ELF symbol tool.*MODERN_NM.*MODERN_TOOLCHAIN_ROOT",
        ):
            probe_bindings.resolve_elf_symbol(
                self.work / "input.elf",
                "TestSymbol",
                missing,
            )

    def test_playtest_cli_reports_missing_nm_without_traceback(self):
        scenario = self.work / "scenario.json"
        scenario.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "name": "missing-nm",
                    "frames": [],
                    "checkpoints": [
                        {
                            "name": "probe",
                            "frame": 1,
                            "framebuffer": False,
                            "probes": [
                                {
                                    "address": "TestSymbol+0x00",
                                    "size": 1,
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = gba_playtest.main(
                [
                    "capture",
                    "--rom",
                    str(self.work / "input.gba"),
                    "--elf",
                    str(self.work / "input.elf"),
                    "--nm",
                    str(self.work / "missing-nm"),
                    "--scenario",
                    str(scenario),
                ]
            )
        self.assertEqual(result, 2)
        self.assertIn("gba-playtest: cannot launch ELF symbol tool", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_make_dry_run_forwards_configured_nm_to_all_production_starter_bindings(self):
        configured_nm = "/semantic/toolchain/arm-none-eabi-nm"
        expected_runs = (
            (
                "debug",
                "expansion-modern-starter-hook-check",
                {
                    (
                        "tools/gba-playtest/scenarios/starter-hook-modern-debug.json",
                        "tools/gba-playtest/fingerprints/starter-hook-modern-debug.json",
                        None,
                    ),
                    (
                        "tools/gba-playtest/scenarios/starter-hook-negative-modern-debug.json",
                        "tools/gba-playtest/fingerprints/starter-hook-negative-modern-debug.json",
                        None,
                    ),
                },
            ),
            (
                "release",
                "expansion-modern-starter-hook-check",
                {
                    (
                        "tools/gba-playtest/scenarios/starter-hook-clean-modern-release.json",
                        "tools/gba-playtest/fingerprints/starter-hook-clean-modern-release.json",
                        None,
                    ),
                    (
                        "tools/gba-playtest/scenarios/starter-hook-clean-negative-modern-release.json",
                        "tools/gba-playtest/fingerprints/starter-hook-clean-negative-modern-release.json",
                        None,
                    ),
                },
            ),
            (
                "debug",
                "expansion-modern-starter-qol-check",
                {
                    (
                        "tools/gba-playtest/scenarios/starter-danger-overlay-modern-debug.json",
                        "tools/gba-playtest/fingerprints/starter-danger-overlay-modern-debug.json",
                        "gExpansionDangerOverlayProbe",
                    ),
                    (
                        "tools/gba-playtest/scenarios/starter-danger-overlay-negative-modern-debug.json",
                        "tools/gba-playtest/fingerprints/starter-danger-overlay-negative-modern-debug.json",
                        "gExpansionDangerOverlayProbe",
                    ),
                },
            ),
            (
                "release",
                "expansion-modern-starter-qol-check",
                {
                    (
                        "tools/gba-playtest/scenarios/starter-danger-overlay-modern-release.json",
                        "tools/gba-playtest/fingerprints/starter-danger-overlay-modern-release.json",
                        "gExpansionDangerOverlayProbe",
                    ),
                    (
                        "tools/gba-playtest/scenarios/starter-danger-overlay-negative-modern-release.json",
                        "tools/gba-playtest/fingerprints/starter-danger-overlay-negative-modern-release.json",
                        "gExpansionDangerOverlayProbe",
                    ),
                },
            ),
        )

        for config, target, expected_bindings in expected_runs:
            with self.subTest(config=config, target=target):
                completed = subprocess.run(
                    [
                        "make",
                        "-n",
                        "--old-file=expansion-modern-boot-preflight",
                        "--old-file=expansion-modern-rom",
                        "--old-file=expansion-modern-starter-profile-rom",
                        target,
                        "MODERN_CONFIG=" + config,
                        "MODERN_NM=" + configured_nm,
                    ],
                    cwd=str(REPO_ROOT),
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )
                commands = [
                    shlex.split(line)
                    for line in completed.stdout.splitlines()
                    if "check_starter_probe_addresses.py" in line
                ]
                self.assertEqual(
                    len(commands),
                    len(expected_bindings),
                    completed.stdout,
                )

                bindings = set()
                for command in commands:
                    self.assertGreaterEqual(len(command), 9)
                    self.assertEqual(
                        command[1],
                        "tools/gba-playtest/check_starter_probe_addresses.py",
                    )
                    options = {
                        command[index]: command[index + 1]
                        for index in range(2, len(command) - 1, 2)
                        if command[index].startswith("--")
                    }
                    self.assertEqual(options.get("--nm"), configured_nm)
                    self.assertTrue(options.get("--elf", "").endswith(".elf"))
                    bindings.add(
                        (
                            options.get("--scenario"),
                            options.get("--fingerprint"),
                            options.get("--symbol"),
                        )
                    )
                self.assertSetEqual(bindings, expected_bindings)

if __name__ == "__main__":
    unittest.main()
