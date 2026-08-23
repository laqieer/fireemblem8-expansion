"""Focused persistent-layout regression for issue #125's unit editor."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CC = shutil.which("gcc") or shutil.which("cc")
NM = shutil.which("nm")


class DebugToolsUnitEditorLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if CC is None or NM is None:
            raise unittest.SkipTest("host C compiler and nm are required")

    @staticmethod
    def _compile(work: Path, source: str, output: str) -> Path:
        obj = work / output
        result = subprocess.run(
            [
                CC,
                "-c",
                "-w",
                "-I",
                str(ROOT / "include"),
                "-I",
                str(ROOT / "include" / "generated"),
                "-DFE8_EXPANSION_DEBUGTOOLS_ENABLED=1",
                str(ROOT / source),
                "-o",
                str(obj),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise AssertionError(result.stdout + result.stderr)
        return obj

    @staticmethod
    def _symbol_size(obj: Path, symbol: str) -> int:
        result = subprocess.run(
            [NM, "-S", str(obj)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        pattern = re.compile(
            rf"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+\S\s+{re.escape(symbol)}$"
        )
        for line in result.stdout.splitlines():
            match = pattern.match(line.strip())
            if match:
                return int(match.group(1), 16)
        raise AssertionError(f"{symbol} missing from {obj}")

    def test_private_snapshot_compacts_without_shrinking_public_telemetry(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            tools = self._compile(work, "src/debugtools_tools.c", "tools.o")
            registry = self._compile(
                work, "src/debugtools_registry.c", "registry.o"
            )

            self.assertEqual(
                self._symbol_size(tools, "sUnitEditor"),
                0x24,
                "private editor snapshot must stay at the reviewed 36-byte layout",
            )
            self.assertEqual(
                self._symbol_size(registry, "gDebugToolsUnitEditorProbe"),
                0x48,
                "public issue #125 telemetry must retain all 72 bytes",
            )


if __name__ == "__main__":
    unittest.main()
