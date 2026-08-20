"""Configured nm selection and actionable probe-binding diagnostics."""

from __future__ import annotations

import contextlib
import io
import json
import os
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

    def test_make_threads_modern_nm_to_playtest_and_binding_tools(self):
        modern_mk = (REPO_ROOT / "modern.mk").read_text(encoding="utf-8")
        self.assertIn(
            '--elf "$(1)" --nm "$(MODERN_NM)"',
            modern_mk,
        )
        self.assertGreaterEqual(
            modern_mk.count('--elf "$(MODERN_ELF)" --nm "$(MODERN_NM)"'),
            2,
        )
        self.assertGreaterEqual(
            modern_mk.count(
                '--elf "$(MODERN_STARTER_PROFILE_ELF)" --nm "$(MODERN_NM)"'
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
