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
import check_starter_probe_addresses  # noqa: E402
import probe_bindings  # noqa: E402


def _logical_commands(output):
    command = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if command or line.endswith("\\"):
            command.append(line.removesuffix("\\").rstrip())
            if not line.endswith("\\"):
                yield shlex.split(" ".join(command))
                command = []
        elif line:
            yield shlex.split(line)


def _options(command):
    return {
        option: command[index + 1]
        for index, option in enumerate(command[:-1])
        if option.startswith("--") and not command[index + 1].startswith("--")
    }


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
        configured_nm = "/semantic/toolchain/arm-none-eabi-nm"
        checks = []
        for prefix, target, symbol in (
            ("starter-hook", "expansion-modern-starter-hook-check",
             check_starter_probe_addresses.PROBE_SYMBOL),
            ("starter-danger-overlay", "expansion-modern-starter-qol-check",
             "gExpansionDangerOverlayProbe"),
        ):
            for config in ("debug", "release"):
                clean = "clean-" if prefix == "starter-hook" and config == "release" else ""
                profile = f"build/expansion-modern-starter/{config}/aapcs/fireemblem8"
                default = f"build/expansion-modern/{config}/aapcs/fireemblem8"
                names = (
                    f"{prefix}-{clean}modern-{config}",
                    f"{prefix}-{clean}negative-modern-{config}",
                )
                checks.append((config, target, ((names[0], profile, symbol),
                                                (names[1], default, symbol))))

        for config, target, expected in checks:
            with self.subTest(config=config, target=target):
                result = subprocess.run(
                    [
                        "make",
                        "-n",
                        "--old-file=expansion-modern-boot-preflight",
                        "--old-file=expansion-modern-rom",
                        "--old-file=expansion-modern-starter-profile-rom",
                        target,
                        f"MODERN_CONFIG={config}",
                        f"MODERN_NM={configured_nm}",
                    ],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

                commands = tuple(_logical_commands(result.stdout))
                bindings = [
                    _options(command)
                    for command in commands
                    if "tools/gba-playtest/check_starter_probe_addresses.py" in command
                ]
                playtests = [
                    _options(command)
                    for command in commands
                    if (
                        "tools/gba-playtest/gba_playtest.py" in command
                        and "verify" in command
                    )
                ]
                self.assertEqual(len(bindings), 2, result.stdout)
                self.assertEqual(len(playtests), 2, result.stdout)

                expected_inputs = {
                    (
                        f"tools/gba-playtest/scenarios/{name}.json",
                        f"tools/gba-playtest/fingerprints/{name}.json",
                        elf,
                        symbol,
                    )
                    for name, elf, symbol in expected
                }
                actual_bindings = {
                    (
                        options.get("--scenario"),
                        options.get("--fingerprint"),
                        options.get("--elf", "").removesuffix(".elf"),
                        options.get(
                            "--symbol",
                            check_starter_probe_addresses.PROBE_SYMBOL,
                        ),
                    )
                    for options in bindings
                }
                self.assertSetEqual(actual_bindings, expected_inputs)

                actual_playtests = {
                    (
                        options.get("--scenario"),
                        options.get("--expected"),
                        options.get("--elf", "").removesuffix(".elf"),
                        options.get("--rom", "").removesuffix(".gba"),
                    )
                    for options in playtests
                }
                expected_playtests = {
                    (
                        f"tools/gba-playtest/scenarios/{name}.json",
                        f"tools/gba-playtest/fingerprints/{name}.json",
                        elf,
                        elf,
                    )
                    for name, elf, _symbol in expected
                }
                self.assertSetEqual(actual_playtests, expected_playtests)
                for options in (*bindings, *playtests):
                    self.assertEqual(options.get("--nm"), configured_nm)


if __name__ == "__main__":
    unittest.main()
