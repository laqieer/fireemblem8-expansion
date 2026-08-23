"""Static safety contract for issue #49's trusted patch publisher."""

from __future__ import annotations

import http.server
import os
import re
import shlex
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from scripts.modernize import patch_release


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
ARTIFACT_FILENAMES = (
    "README.txt",
    "fireemblem8-expansion-all-locales-all-features-aapcs.bps",
    "manifest.json",
)


def parse_patch_release_run_commands(workflow: str) -> list[list[list[str]]]:
    """Parse run scalars from the publisher job's YAML sequence structure."""
    job = re.search(
        r"(?ms)^  patch-release:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        workflow,
    )
    if job is None:
        raise AssertionError("workflow must define a jobs.patch-release job")

    steps = job.group("body").split("\n    steps:\n", 1)
    if len(steps) != 2:
        raise AssertionError("publisher job must define a steps sequence")

    lines = steps[1].splitlines()
    step_starts = [
        index for index, line in enumerate(lines) if re.match(r"^    - ", line)
    ]
    commands = []
    for start, end in zip(step_starts, step_starts[1:] + [len(lines)]):
        step = lines[start:end]
        run = None
        for index, line in enumerate(step):
            inline = re.match(r"^    - run: (?P<value>.+)$", line)
            field = re.match(r"^      run: (?P<value>.+)$", line)
            match = inline or field
            if match is None:
                continue
            value = match.group("value")
            if value == "|":
                run = [
                    following[8:]
                    for following in step[index + 1:]
                    if following.startswith("        ")
                ]
            else:
                run = [value]
            break
        if run is not None:
            commands.append([shlex.split(line) for line in run if line])
    return commands


def patch_release_download_command(workflow: str) -> list[str]:
    commands = [
        command
        for step in parse_patch_release_run_commands(workflow)
        for command in step
        if command and command[0] == "curl"
    ]
    if len(commands) != 1:
        raise AssertionError("publisher job must define exactly one curl download command")
    return commands[0]


def artifact_filename_set_check(directory: Path, inherited_locale: str) -> subprocess.CompletedProcess:
    script = """\
set -euo pipefail
LC_ALL=C
export LC_ALL
find "$1" -maxdepth 1 -type f -printf '%f\\n' | sort | diff -u \\
  <(printf '%s\\n' README.txt fireemblem8-expansion-all-locales-all-features-aapcs.bps manifest.json | sort) -
"""
    environment = dict(os.environ, LC_ALL=inherited_locale)
    return subprocess.run(
        ["bash", "-c", script, "--", str(directory)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
        check=False,
    )


class PatchReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        job = re.search(
            r"(?ms)^  patch-release:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
            cls.text,
        )
        if job is None:
            raise AssertionError("workflow must define a jobs.patch-release job")
        cls.patch_job = job.group("body")

    def test_trusted_push_only_and_no_pr_publication(self):
        self.assertIn("github.event_name == 'push'", self.patch_job)
        self.assertIn("github.ref == 'refs/heads/master'", self.patch_job)
        self.assertNotIn("needs:", self.patch_job)
        self.assertNotIn("pull_request_target", self.text)
        self.assertEqual(
            self.patch_job.count(
                "uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
            ),
            1,
        )

    def test_secret_is_scoped_to_the_trusted_download_step_only(self):
        self.assertEqual(self.text.count("secrets.BASEROM_URL"), 1)
        self.assertIn("BASEROM_URL: ${{ secrets.BASEROM_URL }}", self.patch_job)
        job_header = self.patch_job.split("\n    steps:\n", 1)[0]
        self.assertNotIn("BASEROM_URL", job_header)
        self.assertNotIn("BASEROM_URL:", self.text.split("\n  patch-release:\n", 1)[0])
        self.assertIn("--proto '=https'", self.patch_job)
        self.assertNotIn("set -x", self.patch_job)

    def test_redirecting_download_follows_redirects_and_rejects_wrong_content(self):
        download = patch_release_download_command(self.text)
        self.assertIn("--fail", download)
        self.assertIn("--silent", download)
        self.assertIn("--location", download)
        self.assertIn("--proto", download)
        self.assertIn("--proto-redir", download)
        self.assertFalse(any(argument.startswith("--trace") for argument in download))
        self.assertNotIn("--verbose", download)

        payload = b"redirected but invalid base"

        class RedirectHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/wrong-base")
                    self.end_headers()
                    return
                if self.path == "/wrong-base":
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                self.send_error(404)

            def log_message(self, format, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir=artifact_root) as tmp:
                output = Path(tmp) / "base-image"
                command = []
                skip_next = False
                for argument in download:
                    if skip_next:
                        skip_next = False
                        continue
                    if argument in ("--proto", "--proto-redir"):
                        skip_next = True
                        continue
                    if argument == "$base_image":
                        command.append(str(output))
                    elif argument == "$BASEROM_URL":
                        command.append(
                            f"http://127.0.0.1:{server.server_address[1]}/redirect"
                        )
                    else:
                        command.append(argument)
                result = subprocess.run(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
                self.assertEqual(result.stdout, b"")
                self.assertEqual(result.stderr, b"")
                self.assertEqual(output.read_bytes(), payload)
                with self.assertRaises(patch_release.PatchReleaseError) as context:
                    patch_release.validate_base(output.read_bytes())
                self.assertEqual(
                    str(context.exception),
                    "base validation failed: size mismatch "
                    f"(expected {patch_release.BASE_ROM_SIZE} bytes, got {len(payload)} bytes)",
                )
                self.assertNotIn(payload.decode("ascii"), str(context.exception))
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_publisher_actions_are_immutably_pinned(self):
        self.assertIn(
            "uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            self.patch_job,
        )
        self.assertNotRegex(
            self.patch_job,
            r"uses: actions/(?:checkout|upload-artifact)@v[0-9]+",
        )

    def test_runner_context_is_scoped_to_steps(self):
        job_header = self.patch_job.split("\n    steps:\n", 1)[0]
        self.assertNotIn("runner.temp", job_header)
        self.assertIn(
            "PATCH_ARTIFACT_DIR: ${{ runner.temp }}/patch-artifact",
            self.patch_job,
        )

    def test_artifact_is_exactly_named_allowlisted_and_retained_for_30_days(self):
        self.assertIn(
            "modern-release-all-locales-all-features-aapcs-bps-${{ github.sha }}",
            self.patch_job,
        )
        self.assertIn("retention-days: 30", self.patch_job)
        for name in ARTIFACT_FILENAMES:
            with self.subTest(name=name):
                self.assertIn(name, self.patch_job)
        self.assertNotIn("modern-release-aapcs-rom-map", self.text)

    def test_artifact_filename_allowlist_is_locale_independent_and_exact(self):
        expected_check = (
            "LC_ALL=C\n"
            "        export LC_ALL\n"
            "        find \"$PATCH_ARTIFACT_DIR\" -maxdepth 1 -type f -printf '%f\\n' | sort | diff -u "
            "<(printf '%s\\n' README.txt fireemblem8-expansion-all-locales-all-features-aapcs.bps manifest.json | sort) -"
        )
        self.assertIn(expected_check, self.patch_job)

        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(dir=artifact_root) as tmp:
                artifact = Path(tmp)
                for name in reversed(ARTIFACT_FILENAMES):
                    (artifact / name).write_bytes(b"artifact")

                for inherited_locale in ("C", "C.UTF-8"):
                    with self.subTest(inherited_locale=inherited_locale):
                        result = artifact_filename_set_check(artifact, inherited_locale)
                        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))

                (artifact / "extra.bin").write_bytes(b"extra")
                self.assertNotEqual(artifact_filename_set_check(artifact, "C.UTF-8").returncode, 0)
                (artifact / "extra.bin").unlink()

                (artifact / "README.txt").unlink()
                self.assertNotEqual(artifact_filename_set_check(artifact, "C").returncode, 0)
        finally:
            for child in artifact_root.iterdir():
                if child.is_dir() and not any(child.iterdir()):
                    child.rmdir()

    def test_profile_and_local_verifier_are_required_before_upload(self):
        self.assertIn("make expansion-modern-all-locales-all-features-check -j1", self.patch_job)
        self.assertIn("scripts.modernize.patch_release create", self.patch_job)
        self.assertIn("scripts.modernize.patch_release verify", self.patch_job)
        self.assertIn("--commit \"$PATCH_COMMIT\"", self.patch_job)

    def test_publisher_uses_one_python_interpreter_for_install_and_execution(self):
        install_interpreters = set()
        publisher_interpreters = set()
        for step in parse_patch_release_run_commands(self.text):
            for command in step:
                for index in range(len(command) - 3):
                    if command[index + 1:index + 4] == ["-m", "pip", "install"]:
                        install_interpreters.add(command[index])
                if command[1:3] == ["-m", "scripts.modernize.patch_release"]:
                    publisher_interpreters.add(command[0])

        self.assertEqual(install_interpreters, {"python3"})
        self.assertEqual(publisher_interpreters, {"python3"})
        self.assertEqual(install_interpreters, publisher_interpreters)


if __name__ == "__main__":
    unittest.main()
