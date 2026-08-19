import importlib.util
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "scripts" / "modernize" / "gdb_smoke.py"
SPEC = importlib.util.spec_from_file_location("gdb_smoke", MODULE_PATH)
gdb_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gdb_smoke)


class GdbSmokeTests(unittest.TestCase):
    def test_select_command_prefers_target_gdb(self):
        with mock.patch.object(
            gdb_smoke.shutil,
            "which",
            side_effect=lambda name: f"/tools/{name}" if name in {
                "arm-none-eabi-gdb",
                "gdb-multiarch",
            } else None,
        ):
            self.assertEqual(
                gdb_smoke.select_command(
                    ("arm-none-eabi-gdb", "gdb-multiarch"),
                    "ARM GDB",
                ),
                "/tools/arm-none-eabi-gdb",
            )

    def test_gdb_command_uses_remote_breakpoint_and_disconnect(self):
        command = gdb_smoke.gdb_command(
            "/tools/gdb",
            Path("debug.elf"),
            "AgbMain",
            2345,
        )
        joined = "\n".join(command)
        self.assertIn("target remote 127.0.0.1:2345", joined)
        self.assertIn("break AgbMain", joined)
        self.assertIn("continue", joined)
        self.assertIn("info registers sp lr pc cpsr", joined)
        self.assertIn("disconnect", joined)

    def test_validate_gdb_output_accepts_symbolic_break(self):
        gdb_smoke.validate_gdb_output(
            "\n".join(
                (
                    "Breakpoint 1, AgbMain () at src/main.c:26",
                    "GDB_SMOKE_BREAKPOINT_PC=0x80704d0",
                    "pc 0x80704d0 <AgbMain>",
                    "#0  AgbMain () at src/main.c:26",
                )
            ),
            "AgbMain",
        )

    def test_validate_gdb_output_rejects_missing_evidence(self):
        with self.assertRaisesRegex(gdb_smoke.SmokeError, "missed expected evidence"):
            gdb_smoke.validate_gdb_output("connected", "AgbMain")


if __name__ == "__main__":
    unittest.main()
