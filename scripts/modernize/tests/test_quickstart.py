import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
QUICKSTART = ROOT / "scripts" / "quickstart.sh"


class QuickstartTests(unittest.TestCase):
    def helper_environment(self, commands: dict[str, str]) -> Path:
        temporary = Path(self.temporary.name)
        bin_dir = temporary / "fixture bin"
        bin_dir.mkdir()
        for name, body in commands.items():
            command = bin_dir / name
            command.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
            command.chmod(0o755)
        return bin_dir

    def run_helper(
        self, helper: str, commands: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        bin_dir = self.helper_environment(commands)
        return subprocess.run(
            [
                "/bin/bash",
                "-c",
                'source "$1"; PATH="$2"; "$3"',
                "quickstart-test",
                str(QUICKSTART),
                str(bin_dir),
                helper,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )

    def test_parallel_job_fallback_order(self):
        nproc = self.run_helper(
            "job_count",
            {"nproc": 'printf "%s\\n" 7', "sysctl": 'printf "%s\\n" 9'},
        )
        self.assertEqual((nproc.returncode, nproc.stdout), (0, "7\n"))

        sysctl = self.run_helper(
            "job_count", {"sysctl": 'printf "%s\\n" 6'}
        )
        self.assertEqual((sysctl.returncode, sysctl.stdout), (0, "6\n"))

        single = self.run_helper("job_count", {})
        self.assertEqual((single.returncode, single.stdout), (0, "1\n"))

    def test_checksum_fallback_order(self):
        sha1sum = self.run_helper(
            "verify_rom_checksum",
            {
                "sha1sum": 'printf "sha1sum:%s\\n" "$*"',
                "shasum": 'printf "shasum:%s\\n" "$*"',
            },
        )
        self.assertEqual(sha1sum.returncode, 0)
        self.assertEqual(sha1sum.stdout, "sha1sum:-c checksum.sha1\n")

        shasum = self.run_helper(
            "verify_rom_checksum", {"shasum": 'printf "shasum:%s\\n" "$*"'}
        )
        self.assertEqual(shasum.returncode, 0)
        self.assertEqual(shasum.stdout, "shasum:-a 1 -c checksum.sha1\n")

    def test_package_commands_and_shell_syntax(self):
        syntax = subprocess.run(
            ["/bin/bash", "-n", str(QUICKSTART)],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        self.assertEqual(syntax.returncode, 0, syntax.stdout)

        script = QUICKSTART.read_text(encoding="utf-8")
        self.assertIn(
            "binutils-arm-none-eabi gcc-arm-none-eabi libnewlib-arm-none-eabi",
            script,
        )
        self.assertIn("pacman -S --needed --noconfirm", script)
        self.assertNotIn("pacman -Sy", script)
        self.assertIn("brew install --cask gcc-arm-embedded", script)
        self.assertIn("brew uninstall arm-none-eabi-gcc", script)
        self.assertNotRegex(script, re.compile(r"make [^\n]*\$\(nproc\)"))
        self.assertEqual(script.count("sha1sum -c checksum.sha1"), 1)
        self.assertLess(
            script.index("if have_cmd sha1sum"),
            script.index("sha1sum -c checksum.sha1"),
        )


if __name__ == "__main__":
    unittest.main()
