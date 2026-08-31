"""Static safety contract for issue #49's trusted patch publisher."""

from __future__ import annotations

import ast
import http.server
import json
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
AUDITED_PATCH_TOOL_FILES = (
    "scripts/modernize/patch_release.py",
    "scripts/modernize/bps_patch.py",
    "scripts/modernize/verify_rom_header.py",
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
                physical = [
                    following[8:]
                    for following in step[index + 1:]
                    if following.startswith("        ")
                ]
                run = []
                continued = ""
                for line in physical:
                    logical = continued + line.strip()
                    if logical.endswith("\\"):
                        continued = logical[:-1] + " "
                        continue
                    run.append(logical)
                    continued = ""
                if continued:
                    raise AssertionError("publisher run block has dangling continuation")
            else:
                run = [value]
            break
        if run is not None:
            commands.append([shlex.split(line) for line in run if line])
    return commands


def patch_release_step_blocks(workflow: str) -> list[str]:
    job = re.search(
        r"(?ms)^  patch-release:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        workflow,
    )
    if job is None:
        raise AssertionError("workflow must define a jobs.patch-release job")
    steps = job.group("body").split("\n    steps:\n", 1)
    if len(steps) != 2:
        raise AssertionError("publisher job must define a steps sequence")
    lines = steps[1].splitlines(keepends=True)
    starts = [
        index for index, line in enumerate(lines) if re.match(r"^    - ", line)
    ]
    return [
        "".join(lines[start : starts[index + 1] if index + 1 < len(starts) else len(lines)])
        for index, start in enumerate(starts)
    ]


def named_step_run_script(workflow: str, name: str) -> str:
    steps = patch_release_step_blocks(workflow)
    matches = [
        step for step in steps if f"    - name: {name}\n" in step
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one publisher step named {name!r}")
    lines = matches[0].splitlines()
    run_index = lines.index("      run: |")
    return "\n".join(
        line[8:] for line in lines[run_index + 1:] if line.startswith("        ")
    )


def patch_release_download_command(workflow: str) -> list[str]:
    commands = [
        command
        for step in parse_patch_release_run_commands(workflow)
        for command in step
        if command and command[0] in {"curl", "/usr/bin/curl"}
    ]
    if len(commands) != 1:
        raise AssertionError("publisher job must define exactly one curl download command")
    return commands[0]


def publisher_boundary_errors(workflow: str) -> list[str]:
    steps = patch_release_step_blocks(workflow)
    names = [
        match.group(1) if (match := re.search(r"^    - name: (.+)$", step, re.MULTILINE)) else None
        for step in steps
    ]
    errors = []
    build = re.search(
        r"(?ms)^  build:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
        workflow,
    ).group("body")
    if "BASEROM_URL" in build or "patch-private." in build:
        errors.append("private base can enter candidate build job")
    required = (
        "Verify and stage trusted previous-master producer",
        "Download inert patch-release inputs",
        "Validate inert patch-release inputs",
        "Download private base image",
        "Create and verify patch artifact",
        "Cleanup and verify private base",
    )
    if any(names.count(name) != 1 for name in required):
        return ["publisher boundary steps differ"]
    verify, inert_download, inert_validate, download, create, cleanup = (
        names.index(name) for name in required
    )
    if not (
        verify < inert_download
        and inert_validate == inert_download + 1
        and download == inert_validate + 1
        and create == download + 1
        and cleanup == create + 1
    ):
        errors.append("private base lifetime ordering differs")
    if cleanup != len(steps) - 2:
        errors.append("private cleanup must immediately precede upload")
    candidate_markers = ("sudo apt-get", "./build_tools.sh", "make expansion-modern")
    for index, step in enumerate(steps):
        if any(marker in step for marker in candidate_markers):
            errors.append("candidate command exists in fresh publisher job")
    producer_step = steps[verify]
    inert_step = steps[inert_validate]
    if (
        "ref: ${{ needs.event-identity.outputs.previous_sha }}" not in steps[0]
        or 'test "$ACTUAL_SHA" = "$PREVIOUS_MASTER_SHA"' not in producer_step
        or "/usr/bin/git merge-base --is-ancestor" not in producer_step
        or "sha256sum" in producer_step
    ):
        errors.append("previous-master producer boundary differs")
    if (
        'test ! -L "$PATCH_INPUT_ROOT/target.gba"' not in inert_step
        or 'test ! -L "$PATCH_INPUT_ROOT/metadata.json"' not in inert_step
        or "build_commit" not in inert_step
    ):
        errors.append("inert candidate artifact validation differs")
    secret_step = steps[download]
    create_step = steps[create]
    cleanup_step = steps[cleanup]
    if (
        "BASEROM_URL: ${{ secrets.BASEROM_URL }}" not in secret_step
        or "/usr/bin/mktemp -d" not in secret_step
        or '>> "$GITHUB_OUTPUT"' not in secret_step
        or "$RUNNER_TEMP/base-image" in secret_step
    ):
        errors.append("private download boundary differs")
    if (
        "BASE_IMAGE: ${{ steps.private-base.outputs.base_path }}" not in create_step
        or "/usr/bin/env -i" not in create_step
        or "/usr/bin/python3 -I -S -c" not in create_step
        or "cleanup_private_base" not in create_step
        or '/bin/rm -f -- "$BASE_IMAGE"' not in create_step
        or any(marker in create_step for marker in candidate_markers)
        or "BASEROM_URL" in create_step
    ):
        errors.append("audited patch boundary differs")
    if (
        "      if: always()" not in cleanup_step
        or
        'test ! -e "$BASE_IMAGE"' not in cleanup_step
        or "BASEROM_URL" in cleanup_step
        or "BASE_IMAGE" in steps[-1]
    ):
        errors.append("private cleanup boundary differs")
    return errors


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
        self.assertIn("needs.event-identity.outputs.fallback_kind == 'push'", self.patch_job)
        self.assertIn(
            "needs.event-identity.outputs.fallback_sha == github.event.after",
            self.patch_job,
        )
        self.assertIn(
            "needs.event-identity.outputs.fallback_sha == github.sha",
            self.patch_job,
        )
        self.assertIn("needs: [event-identity, build]", self.patch_job)
        self.assertIn("needs.build.result == 'success'", self.patch_job)
        self.assertIn("needs.event-identity.outputs.previous_sha != ''", self.patch_job)
        self.assertNotIn("needs: [event-classifier", self.patch_job)
        self.assertIn(
            "PATCH_COMMIT: ${{ needs.event-identity.outputs.fallback_sha }}",
            self.patch_job,
        )
        self.assertIn(
            "ref: ${{ needs.event-identity.outputs.previous_sha }}",
            self.patch_job,
        )
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
        secret_step = re.search(
            r"(?ms)^    - name: Download private base image\n"
            r"(?P<body>.*?)(?=^    - )",
            self.patch_job,
        )
        self.assertIsNotNone(secret_step)
        secret_body = secret_step.group("body")
        self.assertIn("/usr/bin/curl", secret_body)
        self.assertIn("id: private-base", self.patch_job)
        self.assertIn(
            "shell: /bin/bash --noprofile --norc -euo pipefail {0}",
            secret_body,
        )
        secret_env = secret_body.split("      run: |", 1)[0]
        self.assertIn("BASEROM_URL: ${{ secrets.BASEROM_URL }}", secret_env)
        for cleared in (
            "BASH_ENV: ''",
            "CDPATH: ''",
            "ENV: ''",
            "GLOBIGNORE: ''",
            "LD_LIBRARY_PATH: ''",
            "LD_PRELOAD: ''",
            "PYTHONPATH: ''",
            "SHELLOPTS: ''",
            "GIT_CONFIG_GLOBAL: /dev/null",
            "GIT_CONFIG_NOSYSTEM: '1'",
        ):
            self.assertIn(cleared, secret_env)
        for candidate_command in ("python3", "./", "make ", "scripts."):
            self.assertNotIn(candidate_command, secret_body)
        self.assertIn(
            '/usr/bin/mktemp -d "$RUNNER_TEMP/patch-private.XXXXXXXXXX"',
            secret_body,
        )
        self.assertIn('test ! -L "$base_image"', secret_body)
        self.assertIn(
            'test "$(/usr/bin/stat -c %a "$base_image")" = 400',
            secret_body,
        )
        self.assertIn(
            'test "$(/usr/bin/stat -c %s "$base_image")" = 16777216',
            secret_body,
        )
        self.assertIn(
            'printf \'base_path=%s\\n\' "$base_image" >> "$GITHUB_OUTPUT"',
            secret_body,
        )
        self.assertNotIn("$RUNNER_TEMP/base-image", self.patch_job)

    def test_exact_revision_is_verified_before_code_or_secret_access(self):
        checkout = self.patch_job.index("uses: actions/checkout@")
        verification = self.patch_job.index(
            "- name: Verify and stage trusted previous-master producer"
        )
        artifact_download = self.patch_job.index(
            "- name: Download inert patch-release inputs"
        )
        artifact_validation = self.patch_job.index(
            "- name: Validate inert patch-release inputs"
        )
        secret = self.patch_job.index("BASEROM_URL: ${{ secrets.BASEROM_URL }}")
        self.assertLess(checkout, verification)
        self.assertLess(verification, artifact_download)
        self.assertLess(artifact_download, artifact_validation)
        self.assertLess(artifact_validation, secret)
        verification_step = self.patch_job[verification:secret]
        self.assertIn('ACTUAL_SHA="$(/usr/bin/git rev-parse HEAD)"', verification_step)
        self.assertIn('test "$ACTUAL_SHA" = "$PREVIOUS_MASTER_SHA"', verification_step)
        self.assertIn("/usr/bin/git merge-base --is-ancestor", verification_step)
        self.assertIn("/usr/bin/git rev-list --first-parent", verification_step)
        self.assertNotIn("BASEROM_URL", verification_step)

    def test_secret_publisher_is_a_fresh_candidate_free_job(self):
        build = re.search(
            r"(?ms)^  build:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
            self.text,
        ).group("body")
        self.assertIn("Stage inert patch-release inputs", build)
        self.assertIn("Upload inert patch-release inputs", build)
        self.assertIn("retention-days: 1", build)
        self.assertIn(
            "name: patch-input-${{ needs.event-identity.outputs.fallback_sha }}",
            build,
        )
        inert_stage = build[
            build.index("- name: Stage inert patch-release inputs"):
            build.index("- name: Upload inert patch-release inputs")
        ]
        self.assertIn('"$PATCH_INPUT_ROOT/target.gba"', inert_stage)
        self.assertIn('"$PATCH_INPUT_ROOT/metadata.json"', inert_stage)
        for source in AUDITED_PATCH_TOOL_FILES:
            self.assertNotIn(source, inert_stage)
        for candidate_marker in (
            "sudo apt-get",
            "./build_tools.sh",
            "make expansion-modern-all-locales-all-features-check",
            "$GITHUB_ENV",
            "$GITHUB_PATH",
        ):
            self.assertNotIn(candidate_marker, self.patch_job)

        attack = (
            "\n    - name: Candidate persistence attack\n"
            "      run: |\n"
            "        echo 'BASH_ENV=attacker' >> \"$GITHUB_ENV\"\n"
            "        (while true; do test -e \"$RUNNER_TEMP/base\"; done) &\n"
        )
        changed = self.text.replace("\n  extended-host-tests:\n", attack + "\n  extended-host-tests:\n", 1)
        changed_patch = re.search(
            r"(?ms)^  patch-release:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
            changed,
        ).group("body")
        self.assertEqual(changed_patch, self.patch_job)

    def test_private_base_lifetime_is_fixed_and_candidate_free(self):
        self.assertEqual(publisher_boundary_errors(self.text), [])
        steps = patch_release_step_blocks(self.text)
        names = [
            re.search(r"^    - name: (.+)$", step, re.MULTILINE).group(1)
            if re.search(r"^    - name: (.+)$", step, re.MULTILINE)
            else None
            for step in steps
        ]
        download = names.index("Download private base image")
        create = names.index("Create and verify patch artifact")
        cleanup = names.index("Cleanup and verify private base")
        self.assertEqual(create, download + 1)
        self.assertEqual(cleanup, create + 1)
        self.assertEqual(len(steps) - 1, cleanup + 1)
        create_step = steps[create]
        for forbidden in (
            "./",
            "make ",
            "scripts/modernize/patch_release.py",
            "python3 -m scripts",
            "sudo ",
        ):
            self.assertNotIn(forbidden, create_step)
        self.assertIn("/usr/bin/env -i", create_step)
        self.assertIn("/usr/bin/python3 -I -S -c", create_step)
        self.assertIn('cd "$PATCH_RUNTIME_ROOT"', create_step)
        self.assertIn("cleanup_private_base", create_step)
        self.assertIn('/bin/rm -f -- "$BASE_IMAGE"', create_step)
        self.assertIn(
            "BASE_IMAGE: ${{ steps.private-base.outputs.base_path }}",
            create_step,
        )
        self.assertNotIn("BASEROM_URL", create_step)
        self.assertIn('test ! -e "$BASE_IMAGE"', steps[cleanup])
        self.assertIn("      if: always()", steps[cleanup])
        self.assertNotIn("BASE_IMAGE", steps[-1])

    def test_every_private_boundary_step_scrubs_ambient_execution_state(self):
        steps = patch_release_step_blocks(self.text)
        for step_name in (
            "Verify and stage trusted previous-master producer",
            "Validate inert patch-release inputs",
            "Download private base image",
            "Create and verify patch artifact",
            "Cleanup and verify private base",
        ):
            with self.subTest(step=step_name):
                step = next(item for item in steps if f"- name: {step_name}" in item)
                for cleared in (
                    "BASH_ENV: ''",
                    "CDPATH: ''",
                    "ENV: ''",
                    "GLOBIGNORE: ''",
                    "LD_LIBRARY_PATH: ''",
                    "LD_PRELOAD: ''",
                    "PYTHONPATH: ''",
                    "SHELLOPTS: ''",
                    "GIT_ALTERNATE_OBJECT_DIRECTORIES: ''",
                    "GIT_CONFIG_GLOBAL: /dev/null",
                    "GIT_CONFIG_NOSYSTEM: '1'",
                    "GIT_CONFIG_SYSTEM: /dev/null",
                    "GIT_NO_REPLACE_OBJECTS: '1'",
                ):
                    self.assertIn(cleared, step)

    def test_private_base_boundary_mutations_fail(self):
        steps = patch_release_step_blocks(self.text)
        download = next(step for step in steps if "Download private base image" in step)
        create = next(step for step in steps if "Create and verify patch artifact" in step)
        cleanup = next(step for step in steps if "Cleanup and verify private base" in step)
        moved_early = self.text.replace(
            "    - name: Install dependencies\n",
            "    - name: Candidate-job private download\n"
            "      env:\n"
            "        BASEROM_URL: ${{ secrets.BASEROM_URL }}\n"
            "      run: /usr/bin/curl \"$BASEROM_URL\" "
            "\"$RUNNER_TEMP/patch-private.base\"\n\n"
            "    - name: Install dependencies\n",
            1,
        )
        inserted_candidate = self.text.replace(
            create,
            "    - run: ./build_tools.sh\n\n" + create,
            1,
        )
        predictable_path = self.text.replace(
            '/usr/bin/mktemp -d "$RUNNER_TEMP/patch-private.XXXXXXXXXX"',
            'printf "$RUNNER_TEMP/base-image"',
            1,
        )
        leaked_secret = self.text.replace(
            "      env:\n"
            "        BASE_IMAGE: ${{ steps.private-base.outputs.base_path }}",
            "      env:\n"
            "        BASEROM_URL: ${{ secrets.BASEROM_URL }}\n"
            "        BASE_IMAGE: ${{ steps.private-base.outputs.base_path }}",
            1,
        )
        removed_cleanup = self.text.replace(
            '/bin/rm -f -- "$BASE_IMAGE" || cleanup_failed=1',
            "true",
            1,
        )
        disabled_cleanup_step = cleanup.replace(
            "      if: always()",
            "      if: false",
            1,
        )
        disabled_cleanup = self.text.replace(cleanup, disabled_cleanup_step, 1)
        for name, changed in (
            ("download-before-candidate", moved_early),
            ("candidate-between-download-and-patch", inserted_candidate),
            ("predictable-private-path", predictable_path),
            ("secret-leak", leaked_secret),
            ("missing-cleanup", removed_cleanup),
            ("disabled-cleanup-step", disabled_cleanup),
        ):
            with self.subTest(name=name):
                self.assertNotEqual(changed, self.text)
                self.assertTrue(publisher_boundary_errors(changed))

    def test_previous_master_patch_tool_imports_are_closed(self):
        allowed_import_roots = {
            "__future__",
            "argparse",
            "dataclasses",
            "hashlib",
            "json",
            "pathlib",
            "scripts",
            "struct",
            "sys",
            "typing",
            "zlib",
        }
        self.assertNotRegex(
            self.patch_job,
            r"[0-9a-f]{64}\s+scripts/modernize/",
        )
        self.assertIn(
            "ref: ${{ needs.event-identity.outputs.previous_sha }}",
            self.patch_job,
        )
        for relative in AUDITED_PATCH_TOOL_FILES:
            with self.subTest(relative=relative):
                data = (ROOT / relative).read_bytes()
                self.assertIn(relative, self.patch_job)
                tree = ast.parse(data, filename=relative)
                imports = {
                    alias.name.split(".", 1)[0]
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import)
                    for alias in node.names
                }
                imports.update(
                    node.module.split(".", 1)[0]
                    for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module
                )
                self.assertLessEqual(imports, allowed_import_roots)

        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="audited-patch-tool-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            tool_root = sandbox / "tool"
            runtime_root = sandbox / "runtime"
            (tool_root / "scripts" / "modernize").mkdir(parents=True)
            runtime_root.mkdir()
            for relative in AUDITED_PATCH_TOOL_FILES:
                target = tool_root / relative
                target.write_bytes((ROOT / relative).read_bytes())
            base = sandbox / "base.gba"
            target = sandbox / "target.gba"
            metadata = sandbox / "metadata.json"
            base.write_bytes(b"invalid")
            target.write_bytes(b"invalid")
            metadata.write_text("{}\n", encoding="ascii")
            completed = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-S",
                    "-c",
                    "import sys; sys.path.insert(0, sys.argv.pop(1)); "
                    "from scripts.modernize.patch_release import main; "
                    "raise SystemExit(main(sys.argv[1:]))",
                    str(tool_root),
                    "create",
                    "--base",
                    str(base),
                    "--target",
                    str(target),
                    "--metadata",
                    str(metadata),
                    "--output-dir",
                    str(sandbox / "artifact"),
                    "--commit",
                    "1" * 40,
                ],
                cwd=runtime_root,
                env={
                    "HOME": str(runtime_root),
                    "LC_ALL": "C",
                    "PATH": "/usr/bin:/bin",
                },
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("base validation failed: size mismatch", completed.stderr)

    def test_previous_master_relationship_is_proven_before_staging(self):
        script = named_step_run_script(
            self.text,
            "Verify and stage trusted previous-master producer",
        )
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="previous-master-producer-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            origin = sandbox / "origin"
            checkout = sandbox / "checkout"
            subprocess.run(
                ["/usr/bin/git", "init", "-q", "-b", "master", str(origin)],
                check=True,
            )
            subprocess.run(
                ["/usr/bin/git", "-C", str(origin), "config", "user.name", "test"],
                check=True,
            )
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(origin),
                    "config",
                    "user.email",
                    "test@example.invalid",
                ],
                check=True,
            )
            for relative in AUDITED_PATCH_TOOL_FILES:
                target = origin / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((ROOT / relative).read_bytes())
            subprocess.run(
                ["/usr/bin/git", "-C", str(origin), "add", "."],
                check=True,
            )
            subprocess.run(
                ["/usr/bin/git", "-C", str(origin), "commit", "-q", "-m", "before"],
                check=True,
            )
            before = subprocess.check_output(
                ["/usr/bin/git", "-C", str(origin), "rev-parse", "HEAD"],
                text=True,
            ).strip()
            (origin / "marker").write_text("after\n", encoding="ascii")
            subprocess.run(
                ["/usr/bin/git", "-C", str(origin), "add", "marker"],
                check=True,
            )
            subprocess.run(
                ["/usr/bin/git", "-C", str(origin), "commit", "-q", "-m", "after"],
                check=True,
            )
            after = subprocess.check_output(
                ["/usr/bin/git", "-C", str(origin), "rev-parse", "HEAD"],
                text=True,
            ).strip()
            subprocess.run(
                ["/usr/bin/git", "-C", str(origin), "checkout", "-q", "--orphan", "unrelated"],
                check=True,
            )
            subprocess.run(
                ["/usr/bin/git", "-C", str(origin), "rm", "-q", "-rf", "."],
                check=True,
            )
            (origin / "unrelated").write_text("unrelated\n", encoding="ascii")
            subprocess.run(
                ["/usr/bin/git", "-C", str(origin), "add", "unrelated"],
                check=True,
            )
            subprocess.run(
                ["/usr/bin/git", "-C", str(origin), "commit", "-q", "-m", "unrelated"],
                check=True,
            )
            unrelated = subprocess.check_output(
                ["/usr/bin/git", "-C", str(origin), "rev-parse", "HEAD"],
                text=True,
            ).strip()
            subprocess.run(
                ["/usr/bin/git", "clone", "-q", str(origin), str(checkout)],
                check=True,
            )
            subprocess.run(
                ["/usr/bin/git", "-C", str(checkout), "checkout", "-q", before],
                check=True,
            )

            def verify(previous_sha, patch_commit, expected):
                case_root = sandbox / f"case-{len(list(sandbox.glob('case-*')))}"
                environment = {
                    **os.environ,
                    "PATCH_COMMIT": patch_commit,
                    "PATCH_RUNTIME_ROOT": str(case_root / "runtime"),
                    "PATCH_TOOL_ROOT": str(case_root / "tool"),
                    "PREVIOUS_MASTER_SHA": previous_sha,
                    "RUNNER_TEMP": str(case_root),
                }
                completed = subprocess.run(
                    ["/bin/bash", "-c", script],
                    cwd=checkout,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, expected, completed.stderr)

            verify(before, after, 0)
            verify("0" * 40, after, 1)
            verify("A" * 40, after, 1)
            verify(after, after, 1)
            verify(before, before, 1)
            verify(before, unrelated, 1)

    def test_inert_candidate_artifact_is_validated_without_execution(self):
        script = named_step_run_script(
            self.text,
            "Validate inert patch-release inputs",
        )
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="inert-patch-input-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            runtime = sandbox / "runtime"
            runtime.mkdir()

            def make_input(name):
                input_root = sandbox / name
                input_root.mkdir()
                with (input_root / "target.gba").open("wb") as target:
                    target.truncate(32 * 1024 * 1024)
                (input_root / "metadata.json").write_text(
                    json.dumps({"build_commit": "1" * 40}),
                    encoding="ascii",
                )
                return input_root

            def validate(input_root):
                return subprocess.run(
                    [
                        "/bin/bash",
                        "--noprofile",
                        "--norc",
                        "-euo",
                        "pipefail",
                        "-c",
                        script,
                    ],
                    cwd=runtime,
                    env={
                        **os.environ,
                        "PATCH_COMMIT": "1" * 40,
                        "PATCH_INPUT_ROOT": str(input_root),
                        "PATCH_RUNTIME_ROOT": str(runtime),
                    },
                    check=False,
                    capture_output=True,
                    text=True,
                )

            valid = make_input("valid")
            self.assertEqual(validate(valid).returncode, 0)

            symlink = make_input("symlink")
            (symlink / "target.gba").unlink()
            (symlink / "target.gba").symlink_to(valid / "target.gba")
            self.assertNotEqual(validate(symlink).returncode, 0)

            nested = make_input("nested")
            (nested / "traversal").mkdir()
            self.assertNotEqual(validate(nested).returncode, 0)

            code_like = make_input("code-like")
            marker = sandbox / "must-not-exist"
            (code_like / "metadata.json").write_text(
                f'__import__("pathlib").Path({str(marker)!r}).touch()\n',
                encoding="ascii",
            )
            self.assertNotEqual(validate(code_like).returncode, 0)
            self.assertFalse(marker.exists())

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
                    if argument in {"$base_image", "$RUNNER_TEMP/base-image"}:
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
            "modern-release-all-locales-all-features-aapcs-bps-${{ "
            "needs.event-identity.outputs.fallback_sha }}",
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
            "        /usr/bin/find \"$PATCH_ARTIFACT_DIR\" -maxdepth 1 -type f "
            "-printf '%f\\n' \\\n"
            "          | /usr/bin/sort \\\n"
            "          | /usr/bin/diff -u \\\n"
            "            <(printf '%s\\n' README.txt \\\n"
            "              fireemblem8-expansion-all-locales-all-features-aapcs.bps \\\n"
            "              manifest.json | /usr/bin/sort) -"
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
        build = re.search(
            r"(?ms)^  build:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
            self.text,
        ).group("body")
        self.assertIn("make expansion-modern-map-menu-presentation-check -j1", build)
        self.assertIn(
            "build/expansion-modern-all-locales-all-features/release/aapcs/"
            "fireemblem8.gba",
            build,
        )
        self.assertEqual(self.patch_job.count("from scripts.modernize.patch_release import main"), 2)
        self.assertIn('"$PATCH_TOOL_ROOT" create', self.patch_job)
        self.assertIn('"$PATCH_TOOL_ROOT" verify', self.patch_job)
        self.assertIn("--commit \"$PATCH_COMMIT\"", self.patch_job)

    def test_publisher_uses_absolute_isolated_python_after_install(self):
        install_interpreters = set()
        publisher_interpreters = set()
        for step in parse_patch_release_run_commands(self.text):
            for command in step:
                for index in range(len(command) - 3):
                    if command[index + 1:index + 4] == ["-m", "pip", "install"]:
                        install_interpreters.add(command[index])
                for index in range(len(command) - 2):
                    if command[index:index + 4] == [
                        "/usr/bin/python3",
                        "-I",
                        "-S",
                        "-c",
                    ]:
                        publisher_interpreters.add(command[index])

        self.assertEqual(install_interpreters, set())
        self.assertEqual(publisher_interpreters, {"/usr/bin/python3"})


if __name__ == "__main__":
    unittest.main()
