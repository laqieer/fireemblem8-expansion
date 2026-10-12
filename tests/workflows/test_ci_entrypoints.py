"""Behavioral checks for the extracted post-checkout CI programs."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.upstream_port import constants


ROOT = Path(__file__).resolve().parents[2]
HELPERS = ROOT / "scripts" / "ci"


class CiEntrypointTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ci-entrypoints-")
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name) / "source with spaces"
        self.work.mkdir()
        self.bin = self.work / "bin"
        self.bin.mkdir()
        self.env = {
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "PYTHONPATH": str(ROOT),
            "REAL_PYTHON": sys.executable,
            "GITHUB_OUTPUT": str(self.work / "outputs"),
            "GITHUB_STEP_SUMMARY": str(self.work / "summary"),
            "RUNNER_TEMP": str(self.work / "runner temp"),
            "GITHUB_WORKSPACE": str(self.work),
            "AGBCC_COMMIT": "a" * 40,
            "MGFEMBP_AGBCC_COMMIT": "b" * 40,
            "CALLS": str(self.work / "calls.jsonl"),
        }
        Path(self.env["RUNNER_TEMP"]).mkdir()

    def executable(self, name, body):
        path = self.bin / name
        path.write_text("#!" + sys.executable + "\n" + body, encoding="utf-8")
        path.chmod(0o755)
        return path

    def run_helper(self, name):
        return subprocess.run(
            ["bash", str(HELPERS / name)], cwd=self.work, env=self.env,
            text=True, capture_output=True, timeout=30,
        )

    def calls(self):
        return [json.loads(line) for line in Path(self.env["CALLS"]).read_text().splitlines()]

    def test_remote_configuration_preserves_identity_and_refuses_replacement(self):
        subprocess.run(["git", "init", "-q", str(self.work)], check=True)
        first = self.run_helper("configure_upstream_remote.sh")
        self.assertEqual(first.returncode, 0, first.stderr)
        actual = subprocess.check_output(
            ["git", "-C", str(self.work), "remote", "get-url", constants.DEFAULT_REMOTE_NAME],
            text=True,
        ).strip()
        self.assertEqual(actual, constants.CANONICAL_UPSTREAM_URL)
        expected = (
            f"remote_name={constants.DEFAULT_REMOTE_NAME}\n"
            f"canonical_url={constants.CANONICAL_UPSTREAM_URL}\n"
        )
        self.assertEqual(Path(self.env["GITHUB_OUTPUT"]).read_text(), expected)
        Path(self.env["GITHUB_OUTPUT"]).unlink()
        repeat = self.run_helper("configure_upstream_remote.sh")
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertEqual(Path(self.env["GITHUB_OUTPUT"]).read_text(), expected)
        subprocess.run(
            ["git", "-C", str(self.work), "remote", "set-url",
             constants.DEFAULT_REMOTE_NAME, "https://example.invalid/other.git"],
            check=True,
        )
        Path(self.env["GITHUB_OUTPUT"]).unlink()
        refused = self.run_helper("configure_upstream_remote.sh")
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("does not match pinned canonical URL", refused.stdout)
        self.assertFalse(Path(self.env["GITHUB_OUTPUT"]).exists())
        actual = subprocess.check_output(
            ["git", "-C", str(self.work), "remote", "get-url", constants.DEFAULT_REMOTE_NAME],
            text=True,
        ).strip()
        self.assertEqual(actual, "https://example.invalid/other.git")

    def drift_boundary(self, drift_exit, scan_exit):
        state = self.work / "config" / "upstream-port-state.json"
        state.parent.mkdir()
        state.write_text(json.dumps({
            "last_ported": {"sha": "c" * 40, "ref": "decomp/master"},
        }))
        self.env["DRIFT_EXIT"] = str(drift_exit)
        self.env["SCAN_EXIT"] = str(scan_exit)
        self.executable("python3", """
import json, os, sys
args = sys.argv[1:]
if args[0] == "-c":
    os.execv(os.environ["REAL_PYTHON"], [os.environ["REAL_PYTHON"], *args])
with open(os.environ["CALLS"], "a") as stream:
    stream.write(json.dumps(args) + "\\n")
if args[:2] != ["-m", "scripts.upstream_port"]:
    raise SystemExit(99)
command = args[2]
if command == "drift":
    print(json.dumps({"has_drift": int(os.environ["DRIFT_EXIT"]) == 2}))
    raise SystemExit(int(os.environ["DRIFT_EXIT"]))
if command == "scan":
    if int(os.environ["SCAN_EXIT"]):
        print("controlled scan failure", file=sys.stderr)
    else:
        print(json.dumps({"unreviewed_count": 1}))
    raise SystemExit(int(os.environ["SCAN_EXIT"]))
raise SystemExit(98)
""")
        before = state.read_bytes()
        result = self.run_helper("render_upstream_drift.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state.read_bytes(), before)
        self.assertEqual(self.calls(), [
            ["-m", "scripts.upstream_port", "drift", "--ref", "decomp/master", "--format", "json"],
            ["-m", "scripts.upstream_port", "scan", "--ref", "decomp/master", "--format", "json"],
        ])
        self.assertEqual(Path(self.env["GITHUB_OUTPUT"]).read_text(), f"exit_code={drift_exit}\n")
        summary = Path(self.env["GITHUB_STEP_SUMMARY"]).read_text()
        self.assertIn((self.work / "drift-report.json").read_text().strip(), summary)
        if scan_exit:
            self.assertIn((self.work / "scan-report.err").read_text().strip(), summary)
        else:
            self.assertIn((self.work / "scan-report.json").read_text().strip(), summary)

    def test_drift_and_failed_scan_keep_reports_and_authoritative_signal(self):
        self.drift_boundary(2, 3)

    def test_clean_drift_and_successful_scan_keep_complete_reports(self):
        self.drift_boundary(0, 0)

    def test_missing_state_publishes_error_signal_without_running_cli(self):
        result = self.run_helper("render_upstream_drift.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(self.env["GITHUB_OUTPUT"]).read_text(), "exit_code=3\n")
        self.assertIn("no state file found", Path(self.env["GITHUB_STEP_SUMMARY"]).read_text())
        self.assertFalse(Path(self.env["CALLS"]).exists())

    def archival_boundary(self):
        self.executable("git", """
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ["CALLS"], "a") as stream:
    stream.write(json.dumps(args) + "\\n")
directory = pathlib.Path(args[1]) if args[0] == "-C" else None
command = args[2] if directory else args[0]
if command == "init":
    pathlib.Path(args[-1]).mkdir(parents=True)
elif command == "fetch":
    if os.environ.get("FAIL_FETCH"):
        raise SystemExit(17)
    (directory / "pin").write_text(args[-1])
elif command == "checkout":
    for name in ("build.sh", "install.sh"):
        script = directory / name
        script.write_text("#!" + os.environ["REAL_PYTHON"] + "\\n"
            + "import json, pathlib, sys\\n"
            + "pathlib.Path(sys.argv[0] + '.result').write_text(json.dumps(sys.argv[1:]))\\n")
        script.chmod(0o755)
elif command == "rev-parse":
    print("0" * 40 if os.environ.get("WRONG_PIN") else (directory / "pin").read_text())
""")

    def test_archival_install_builds_both_exact_pins_and_preserves_destinations(self):
        self.archival_boundary()
        result = self.run_helper("install_archival_compilers.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        locations = (
            (self.work / ".deps" / "agbcc-ci", self.work, "a" * 40),
            (Path(self.env["RUNNER_TEMP"]) / "mgfembp-agbcc-ci", self.work / "mgfembp", "b" * 40),
        )
        for directory, destination, pin in locations:
            self.assertEqual((directory / "pin").read_text(), pin)
            self.assertEqual(json.loads((directory / "build.sh.result").read_text()), [])
            self.assertEqual(json.loads((directory / "install.sh.result").read_text()), [str(destination)])
        remotes = [args[-1] for args in self.calls() if "remote" in args]
        self.assertEqual(remotes, ["https://github.com/pret/agbcc.git", "https://github.com/StanHash/agbcc.git"])

    def test_failed_fetch_stops_before_checkout_or_compiler_execution(self):
        self.archival_boundary()
        self.env["FAIL_FETCH"] = "1"
        result = self.run_helper("install_archival_compilers.sh")
        self.assertEqual(result.returncode, 17, result.stderr)
        self.assertFalse(any("checkout" in args for args in self.calls()))
        self.assertFalse(list(self.work.glob("**/*.result")))

    def test_wrong_pin_stops_before_compiler_execution(self):
        self.archival_boundary()
        self.env["WRONG_PIN"] = "1"
        result = self.run_helper("install_archival_compilers.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(list(self.work.glob("**/*.result")))
