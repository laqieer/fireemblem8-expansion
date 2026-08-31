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
    if (
        workflow.count("actions/upload-artifact@") != 1
        or "actions/download-artifact@" in workflow
    ):
        errors.append("complete ROM artifact transfer is possible")
    required = (
        "Verify exact candidate and stage trusted producer",
        "Install trusted isolated-build dependencies",
        "Build candidate in isolated namespace and stage public inputs",
        "Download private base image",
        "Create and verify patch artifact",
        "Cleanup and verify private base",
        "Revalidate patch-only upload",
    )
    if any(names.count(name) != 1 for name in required):
        return ["publisher boundary steps differ"]
    verify, dependencies, isolated_build, download, create, cleanup, revalidate = (
        names.index(name) for name in required
    )
    if not (
        dependencies == verify + 1
        and isolated_build == dependencies + 1
        and download == isolated_build + 1
        and create == download + 1
        and cleanup == create + 1
        and revalidate == cleanup + 1
    ):
        errors.append("private base lifetime ordering differs")
    if revalidate != len(steps) - 2:
        errors.append("late patch-only revalidation must immediately precede upload")
    candidate_markers = (
        "/usr/bin/apt-get",
        "./build_tools.sh",
        "make expansion-modern",
    )
    for index, step in enumerate(steps):
        if any(marker in step for marker in candidate_markers) and index != isolated_build:
            if index != dependencies:
                errors.append("candidate command escapes isolated builder step")
    producer_step = steps[verify]
    dependency_step = steps[dependencies]
    isolated_step = steps[isolated_build]
    if (
        "ref: ${{ needs.event-identity.outputs.fallback_sha }}" not in steps[0]
        or 'test "$ACTUAL_SHA" = "$PATCH_COMMIT"' not in producer_step
        or '/usr/bin/git cat-file -t "$PATCH_COMMIT"' not in producer_step
        or "PREVIOUS_MASTER_SHA" in producer_step
        or "sha256sum" in producer_step
    ):
        errors.append("exact candidate producer boundary differs")
    if (
        "shell: /bin/bash --noprofile --norc -euo pipefail {0}"
        not in dependency_step
        or "BASH_ENV: ''" not in dependency_step
        or "LD_PRELOAD: ''" not in dependency_step
        or "PYTHONPATH: ''" not in dependency_step
        or "GIT_CONFIG_GLOBAL: /dev/null" not in dependency_step
        or "/usr/bin/env -i" not in dependency_step
        or "PIP_CONFIG_FILE=/dev/null" not in dependency_step
        or "/usr/bin/python3 -I -m pip download" not in dependency_step
    ):
        errors.append("isolated dependency boundary differs")
    if (
        "/usr/bin/unshare" not in isolated_step
        or "--net" not in isolated_step
        or "--pid" not in isolated_step
        or "--kill-child=KILL" not in isolated_step
        or "/usr/bin/setpriv" not in isolated_step
        or "--no-new-privs" not in isolated_step
        or "--bounding-set=-all" not in isolated_step
        or "/usr/bin/env -i" not in isolated_step
        or "GITHUB_ENV=\"$GITHUB_ENV\"" in isolated_step
        or "BASH_ENV=\"$BASH_ENV\"" in isolated_step
        or "/usr/bin/mount --make-rprivate /" not in isolated_step
        or "/usr/bin/mount -o remount,bind,ro /" not in isolated_step
        or "runner temp is outside the masked host tree" not in isolated_step
        or "for hidden in /home/runner /root /var /run /sys; do"
        not in isolated_step
        or "builder-tmp /tmp" not in isolated_step
        or "builder-dev /dev" not in isolated_step
        or "builder-shm /dev/shm" not in isolated_step
        or "/usr/share/dbus-1/system-services" not in isolated_step
        or "/run/dbus/system_bus_socket" not in isolated_step
        or "/run/docker.sock" not in isolated_step
        or "/run/containerd/containerd.sock" not in isolated_step
        or "/run/systemd/private" not in isolated_step
        or "/run/snapd.socket" not in isolated_step
        or 'test ! -e /sys/fs/cgroup/cgroup.procs' not in isolated_step
        or "unexpected writable mount" not in isolated_step
        or re.search(
            r"/usr/bin/mount -o remount,bind,rw /(?:opt|usr(?:/share)?)"
            r"(?:\s|$)",
            isolated_step,
        )
        or "/sys/fs/cgroup/cgroup.controllers" not in isolated_step
        or 'test -f "$builder_cgroup/cgroup.kill"' not in isolated_step
        or 'test -f "$builder_cgroup/cgroup.procs"' not in isolated_step
        or 'test -r "$builder_cgroup/cgroup.procs"' not in isolated_step
        or 'test -z "$(builder_cgroup_pids)"' not in isolated_step
        or 'test ! -e "$builder_cgroup"' not in isolated_step
        or 'builder_cgroup_owned=1' not in isolated_step
        or '/usr/bin/sudo /usr/bin/rmdir -- "$builder_cgroup"'
        not in isolated_step
        or "$builder_cgroup/cgroup.kill" not in isolated_step
        or "printf '1\\n'" not in isolated_step
        or "/usr/bin/sudo /usr/bin/tee" not in isolated_step
        or re.search(r"/bin/kill[^\n]*[\"']?\$pid", isolated_step)
        or 'test ! -L "$source"' not in isolated_step
        or 'test "$(/usr/bin/stat -c %h "$source")" = 1' not in isolated_step
        or "handoff_names=" not in isolated_step
        or "metadata.json\\ntarget.gba" not in isolated_step
        or 'test "$handoff_names" = ' not in isolated_step
        or 'test -z "$(builder_uid_pids "$builder_uid")"' not in isolated_step
        or 'test -z "$(builder_group_pids "$builder_pgid")"' not in isolated_step
        or "userdel" not in isolated_step
        or "builder_user_created=0" not in isolated_step
        or "builder_user_created=1" not in isolated_step
        or "builder_root_owned=0" not in isolated_step
        or "builder_root_owned=1" not in isolated_step
        or "wheelhouse_owned=0" not in isolated_step
        or "pkill" in isolated_step
        or "killall" in isolated_step
        or '/usr/bin/find "$GITHUB_WORKSPACE_PATH" -mindepth 1 -delete'
        not in isolated_step
        or 'test ! -e "$BUILDER_ROOT"' not in isolated_step
        or 'test ! -e "$PATCH_WHEELHOUSE"' not in isolated_step
        or (
            'test -z "$(builder_group_pids "$builder_pgid")"\n'
            '        test -z "$(builder_cgroup_pids)"\n'
            '        test -z "$(builder_uid_pids "$builder_uid")"\n'
            '        remove_builder_cgroup\n'
            '        test ! -e "$builder_cgroup"\n'
            '        handoff_root='
        )
        not in isolated_step
        or (
            'remove_builder_cgroup\n'
            '        remove_builder_state\n'
            '        trap - EXIT INT TERM\n'
            '        test -z "$(builder_group_pids "$builder_pgid")"\n'
            '        test ! -e "$builder_cgroup"\n'
            '        test -z "$(builder_uid_pids "$builder_uid")"\n'
            '        test -z "$(/usr/bin/getent passwd "$builder_user" || true)"\n'
            '        test ! -e "$BUILDER_ROOT"\n'
            '        test ! -e "$PATCH_WHEELHOUSE"\n'
            '        input_names='
        )
        not in isolated_step
        or "build_commit" not in isolated_step
    ):
        errors.append("isolated candidate builder boundary differs")
    secret_step = steps[download]
    create_step = steps[create]
    cleanup_step = steps[cleanup]
    revalidate_step = steps[revalidate]
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
        or "BASE_IMAGE" in revalidate_step
        or "BASE_IMAGE" in steps[-1]
    ):
        errors.append("private cleanup boundary differs")
    if (
        "PATCH_ARTIFACT_DIR: ${{ runner.temp }}/patch-artifact"
        not in revalidate_step
        or "artifact_names=" not in revalidate_step
        or "README.txt" not in revalidate_step
        or "fireemblem8-expansion-all-locales-all-features-aapcs.bps"
        not in revalidate_step
        or "manifest.json" not in revalidate_step
        or 'test ! -L "$artifact"' not in revalidate_step
        or 'test "$(/usr/bin/stat -c %F "$artifact")" = "regular file"'
        not in revalidate_step
        or 'test "$(/usr/bin/stat -c %h "$artifact")" = 1'
        not in revalidate_step
        or "PATCH_INPUT_ROOT" in revalidate_step
        or "target.gba" in revalidate_step
    ):
        errors.append("late patch-only upload revalidation differs")
    if (
        "actions/upload-artifact@" not in steps[-1]
        or "path: ${{ runner.temp }}/patch-artifact" not in steps[-1]
        or "PATCH_INPUT_ROOT" in steps[-1]
        or "target.gba" in steps[-1]
    ):
        errors.append("final upload is not patch-only")
    return errors


def artifact_filename_set_check(directory: Path, inherited_locale: str) -> subprocess.CompletedProcess:
    script = """\
set -euo pipefail
LC_ALL=C
export LC_ALL
artifact_names="$(find "$1" -mindepth 1 -maxdepth 1 -printf '%f\\n' | sort)"
test "$artifact_names" = \\
  "$(printf '%s\\n' README.txt fireemblem8-expansion-all-locales-all-features-aapcs.bps manifest.json | sort)"
for artifact in "$1/README.txt" \\
  "$1/fireemblem8-expansion-all-locales-all-features-aapcs.bps" \\
  "$1/manifest.json"
do
  test -f "$artifact"
  test ! -L "$artifact"
  test "$(stat -c %F "$artifact")" = "regular file"
  test "$(stat -c %h "$artifact")" = 1
done
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
        self.assertIn("needs: [event-identity]", self.patch_job)
        self.assertNotIn("needs.build.result", self.patch_job)
        self.assertNotIn("needs: [event-classifier", self.patch_job)
        self.assertIn(
            "PATCH_COMMIT: ${{ needs.event-identity.outputs.fallback_sha }}",
            self.patch_job,
        )
        self.assertIn(
            "ref: ${{ needs.event-identity.outputs.fallback_sha }}",
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
            "- name: Verify exact candidate and stage trusted producer"
        )
        isolated_build = self.patch_job.index(
            "- name: Build candidate in isolated namespace and stage public inputs"
        )
        secret = self.patch_job.index("BASEROM_URL: ${{ secrets.BASEROM_URL }}")
        self.assertLess(checkout, verification)
        self.assertLess(verification, isolated_build)
        self.assertLess(isolated_build, secret)
        verification_step = self.patch_job[verification:secret]
        self.assertIn('ACTUAL_SHA="$(/usr/bin/git rev-parse HEAD)"', verification_step)
        self.assertIn('test "$ACTUAL_SHA" = "$PATCH_COMMIT"', verification_step)
        self.assertIn('/usr/bin/git cat-file -t "$PATCH_COMMIT"', verification_step)
        self.assertNotIn("PREVIOUS_MASTER_SHA", verification_step)
        self.assertNotIn("sha256sum", verification_step)
        self.assertNotIn("BASEROM_URL", verification_step)

    def test_secret_publisher_is_a_fresh_candidate_free_job(self):
        build = re.search(
            r"(?ms)^  build:\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:\n|\Z)",
            self.text,
        ).group("body")
        self.assertNotIn("Stage inert patch-release inputs", build)
        self.assertNotIn("Upload inert patch-release inputs", build)
        self.assertNotIn("patch-input-${{", self.text)
        self.assertNotIn("actions/download-artifact@", self.text)
        self.assertNotIn("$GITHUB_ENV", self.patch_job)
        self.assertNotIn("$GITHUB_PATH", self.patch_job)
        self.assertIn("/usr/bin/unshare", self.patch_job)
        self.assertIn("--kill-child=KILL", self.patch_job)
        self.assertIn("--net", self.patch_job)
        self.assertIn("/usr/bin/mount --make-rprivate /", self.patch_job)
        self.assertIn("/usr/bin/mount -o remount,bind,ro /", self.patch_job)
        self.assertIn("runner temp is outside the masked host tree", self.patch_job)
        self.assertIn("for hidden in /home/runner /root /var /run /sys; do", self.patch_job)
        self.assertIn("/run/dbus/system_bus_socket", self.patch_job)
        self.assertIn("/run/docker.sock", self.patch_job)
        self.assertIn("/run/containerd/containerd.sock", self.patch_job)
        self.assertIn("/run/systemd/private", self.patch_job)
        self.assertIn("/run/snapd.socket", self.patch_job)
        self.assertIn("/usr/bin/setpriv", self.patch_job)
        self.assertIn("--bounding-set=-all", self.patch_job)
        self.assertIn('"$builder_cgroup/cgroup.kill"', self.patch_job)
        self.assertIn('test -z "$(builder_cgroup_pids)"', self.patch_job)
        self.assertIn('test ! -e "$builder_cgroup"', self.patch_job)
        self.assertNotRegex(self.patch_job, r"/bin/kill[^\n]*[\"']?\$pid")
        stop = self.patch_job.index(
            'test -z "$(builder_cgroup_pids)"',
            self.patch_job.index('wait "$builder_supervisor_pid"'),
        )
        remove = self.patch_job.index("remove_builder_cgroup", stop)
        stage = self.patch_job.index(
            '/usr/bin/install -d -m 0700 "$PATCH_INPUT_ROOT"',
            remove,
        )
        self.assertLess(stop, remove)
        self.assertLess(remove, stage)
        self.assertIn(
            'test -z "$(builder_uid_pids "$builder_uid")"',
            self.patch_job,
        )
        self.assertIn(
            'test -z "$(builder_group_pids "$builder_pgid")"',
            self.patch_job,
        )

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
        revalidate = names.index("Revalidate patch-only upload")
        self.assertEqual(create, download + 1)
        self.assertEqual(cleanup, create + 1)
        self.assertEqual(revalidate, cleanup + 1)
        self.assertEqual(len(steps) - 1, revalidate + 1)
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
        self.assertIn("artifact_names=", steps[revalidate])
        self.assertNotIn("BASE_IMAGE", steps[-1])

    def test_every_private_boundary_step_scrubs_ambient_execution_state(self):
        steps = patch_release_step_blocks(self.text)
        for step_name in (
            "Verify exact candidate and stage trusted producer",
            "Install trusted isolated-build dependencies",
            "Build candidate in isolated namespace and stage public inputs",
            "Download private base image",
            "Create and verify patch artifact",
            "Cleanup and verify private base",
            "Revalidate patch-only upload",
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
        revalidate = next(step for step in steps if "Revalidate patch-only upload" in step)
        isolated = next(
            step
            for step in steps
            if "Build candidate in isolated namespace and stage public inputs" in step
        )
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
        missing_network_namespace = self.text.replace(
            isolated,
            isolated.replace(" --net --ipc --uts", " --ipc --uts"),
            1,
        )
        missing_pid_teardown = self.text.replace(
            isolated,
            isolated.replace(
                'test -z "$(builder_cgroup_pids)"',
                "true",
            ),
            1,
        )
        missing_symlink_guard = self.text.replace(
            'for source in "$target_source" "$metadata_source"; do\n'
            '          test -f "$source"\n'
            '          test ! -L "$source"',
            'for source in "$target_source" "$metadata_source"; do\n'
            '          test -f "$source"\n'
            "          true",
            1,
        )
        missing_hardlink_guard = self.text.replace(
            isolated,
            isolated.replace(
                'test "$(/usr/bin/stat -c %h "$source")" = 1',
                "true",
            ),
            1,
        )
        leaked_github_env = self.text.replace(
            "/usr/bin/env -i HOME=/mnt/home",
            '/usr/bin/env -i GITHUB_ENV="$GITHUB_ENV" HOME=/mnt/home',
            1,
        )
        leaked_bash_env = self.text.replace(
            "/usr/bin/env -i HOME=/mnt/home",
            '/usr/bin/env -i BASH_ENV="$BASH_ENV" HOME=/mnt/home',
            1,
        )
        writable_host_root = self.text.replace(
            "/usr/bin/mount -o remount,bind,ro /",
            "/usr/bin/mount -o remount,bind,rw /",
            1,
        )
        writable_dbus_activation = self.text.replace(
            "/usr/bin/mount -o remount,bind,ro /",
            "/usr/bin/mount -o remount,bind,ro /\n"
            "        /usr/bin/mount --bind /usr/share /usr/share\n"
            "        /usr/bin/mount -o remount,bind,rw /usr/share",
            1,
        )
        writable_opt = self.text.replace(
            "/usr/bin/mount -o remount,bind,ro /",
            "/usr/bin/mount -o remount,bind,ro /\n"
            "        /usr/bin/mount --bind /opt /opt\n"
            "        /usr/bin/mount -o remount,bind,rw /opt",
            1,
        )
        exposed_host_service_sockets = self.text.replace(
            "for hidden in /home/runner /root /var /run /sys; do",
            "for hidden in /home/runner /root /var /sys; do",
            1,
        )
        writable_host_runner_temp = self.text.replace(
            isolated,
            isolated.replace(
                "/usr/bin/mount -o remount,bind,ro /",
                "/usr/bin/mount -o remount,bind,rw /",
                1,
            )
            .replace(
                "for hidden in /home/runner /root /var /run /sys; do",
                "for hidden in /root /var /run /sys; do",
                1,
            ),
            1,
        )
        daemon_escape_without_cgroup = self.text.replace(
            isolated,
            isolated.replace(
                'printf \'1\\n\' \\\n'
                '                | /usr/bin/sudo /usr/bin/tee \\\n'
                '                  "$builder_cgroup/cgroup.kill" > /dev/null',
                "true",
                1,
            ),
            1,
        )
        cgroup_escape_surface = self.text.replace(
            "for hidden in /home/runner /root /var /run /sys; do",
            "for hidden in /home/runner /root /var /run; do",
            1,
        )
        unavailable_cgroup = self.text.replace(
            "        test -r /sys/fs/cgroup/cgroup.controllers",
            "        true",
            1,
        )
        unavailable_cgroup_kill = self.text.replace(
            '        test -f "$builder_cgroup/cgroup.kill"',
            "        true",
            1,
        )
        unavailable_mount_namespace = self.text.replace(
            "        /usr/bin/mount --make-rprivate /",
            "        true",
            1,
        )
        retained_candidate_workspace = self.text.replace(
            '/usr/bin/find "$GITHUB_WORKSPACE_PATH" -mindepth 1 -delete',
            "true",
            1,
        )
        untracked_builder_user = self.text.replace(
            isolated,
            isolated.replace(
                "        builder_user_created=1",
                "        true",
                1,
            ),
            1,
        )
        untracked_builder_root = self.text.replace(
            isolated,
            isolated.replace(
                "        builder_root_owned=1",
                "        true",
                1,
            ),
            1,
        )
        ambient_dependency_python = self.text.replace(
            "/usr/bin/env -i HOME=\"$PATCH_RUNTIME_ROOT\" LC_ALL=C",
            "HOME=\"$PATCH_RUNTIME_ROOT\"",
            1,
        )
        unverified_builder_state = self.text.replace(
            isolated,
            isolated.replace(
                '        test ! -e "$BUILDER_ROOT"\n'
                '        test ! -e "$PATCH_WHEELHOUSE"\n'
                '        input_names=',
                "        input_names=",
                1,
            ),
            1,
        )
        allowed_unexpected_handoff = self.text.replace(
            'test "$handoff_names" = "$(printf \'metadata.json\\ntarget.gba\')"',
            "true",
            1,
        )
        disabled_late_revalidation = self.text.replace(
            revalidate,
            "    - name: Revalidate patch-only upload\n"
            "      run: true\n\n",
            1,
        )
        candidate_patch_artifact_mutation = self.text.replace(
            revalidate,
            revalidate
            + "    - name: Candidate patch artifact mutation\n"
            "      run: touch \"$RUNNER_TEMP/patch-artifact/target.gba\"\n\n",
            1,
        )
        rom_artifact_transfer = self.text.replace(
            "\n  extended-host-tests:\n",
            "\n    - uses: actions/upload-artifact@"
            "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a\n"
            "      with:\n"
            "        name: complete-target-rom\n"
            "        path: build/modern/fireemblem8.gba\n"
            "\n  extended-host-tests:\n",
            1,
        )
        for name, changed in (
            ("download-before-candidate", moved_early),
            ("candidate-between-download-and-patch", inserted_candidate),
            ("predictable-private-path", predictable_path),
            ("secret-leak", leaked_secret),
            ("missing-cleanup", removed_cleanup),
            ("disabled-cleanup-step", disabled_cleanup),
            ("missing-network-namespace", missing_network_namespace),
            ("missing-builder-cgroup-teardown", missing_pid_teardown),
            ("missing-symlink-guard", missing_symlink_guard),
            ("missing-hardlink-guard", missing_hardlink_guard),
            ("leaked-github-env", leaked_github_env),
            ("leaked-bash-env", leaked_bash_env),
            ("writable-host-root", writable_host_root),
            ("writable-dbus-activation", writable_dbus_activation),
            ("writable-opt", writable_opt),
            ("exposed-host-service-sockets", exposed_host_service_sockets),
            ("writable-host-runner-temp", writable_host_runner_temp),
            ("daemon-escape-without-cgroup-kill", daemon_escape_without_cgroup),
            ("cgroup-escape-surface", cgroup_escape_surface),
            ("unavailable-cgroup-v2", unavailable_cgroup),
            ("unavailable-cgroup-kill", unavailable_cgroup_kill),
            ("unavailable-mount-isolation", unavailable_mount_namespace),
            ("retained-candidate-workspace", retained_candidate_workspace),
            ("untracked-builder-user", untracked_builder_user),
            ("untracked-builder-root", untracked_builder_root),
            ("ambient-dependency-python", ambient_dependency_python),
            ("unverified-builder-state", unverified_builder_state),
            ("allowed-unexpected-handoff", allowed_unexpected_handoff),
            ("disabled-late-revalidation", disabled_late_revalidation),
            ("candidate-patch-artifact-mutation", candidate_patch_artifact_mutation),
            ("complete-rom-artifact-transfer", rom_artifact_transfer),
        ):
            with self.subTest(name=name):
                self.assertNotEqual(changed, self.text)
                self.assertTrue(publisher_boundary_errors(changed))

    def test_exact_candidate_patch_tool_imports_are_closed(self):
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
            "ref: ${{ needs.event-identity.outputs.fallback_sha }}",
            self.patch_job,
        )
        self.assertNotIn("previous_sha", self.patch_job)
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

    def test_exact_candidate_revision_is_proven_before_staging(self):
        script = named_step_run_script(
            self.text,
            "Verify exact candidate and stage trusted producer",
        )
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="exact-after-producer-",
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
                ["/usr/bin/git", "clone", "-q", str(origin), str(checkout)],
                check=True,
            )
            subprocess.run(
                ["/usr/bin/git", "-C", str(checkout), "checkout", "-q", after],
                check=True,
            )

            def verify(patch_commit, expected):
                case_root = sandbox / f"case-{len(list(sandbox.glob('case-*')))}"
                environment = {
                    **os.environ,
                    "PATCH_COMMIT": patch_commit,
                    "PATCH_RUNTIME_ROOT": str(case_root / "runtime"),
                    "PATCH_TOOL_ROOT": str(case_root / "tool"),
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

            verify(after, 0)
            verify("0" * 40, 1)
            verify("A" * 40, 1)
            verify(before, 1)

    def test_isolated_builder_output_rejects_symlink_and_hardlink(self):
        full_script = named_step_run_script(
            self.text,
            "Build candidate in isolated namespace and stage public inputs",
        )
        start = full_script.index('handoff_names="$(/usr/bin/find "$handoff_root"')
        end_marker = 'test "$metadata_size" -le 1048576'
        end = full_script.index(end_marker, start) + len(end_marker)
        script = (
            'handoff_root="$BUILDER_ROOT/handoff"\n'
            + full_script[start:end].replace(
                '/usr/bin/sudo /bin/chown "$host_uid:$host_gid" "$source"',
                "true",
            )
        )
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="isolated-builder-output-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)

            def make_builder(name):
                builder_root = sandbox / name
                output = builder_root / "handoff"
                output.mkdir(parents=True)
                with (output / "target.gba").open("wb") as target:
                    target.truncate(32 * 1024 * 1024)
                (output / "metadata.json").write_text(
                    json.dumps({"build_commit": "1" * 40}),
                    encoding="ascii",
                )
                return builder_root, output

            def validate(builder_root):
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
                    cwd=sandbox,
                    env={
                        **os.environ,
                        "BUILDER_ROOT": str(builder_root),
                        "builder_uid": str(os.getuid()),
                    },
                    check=False,
                    capture_output=True,
                    text=True,
                )

            valid, _ = make_builder("valid")
            self.assertEqual(validate(valid).returncode, 0)

            symlink, symlink_output = make_builder("symlink")
            (symlink_output / "target.gba").unlink()
            (symlink_output / "target.gba").symlink_to(
                sandbox / "valid" / "handoff" / "target.gba"
            )
            self.assertNotEqual(validate(symlink).returncode, 0)

            hardlink, hardlink_output = make_builder("hardlink")
            hardlink_target = hardlink_output / "target.gba"
            second_link = hardlink / "second-link"
            os.link(hardlink_target, second_link)
            self.assertNotEqual(validate(hardlink).returncode, 0)
            self.assertEqual(
                os.stat(hardlink_target).st_ino,
                os.stat(second_link).st_ino,
            )

            device, device_output = make_builder("device")
            (device_output / "target.gba").unlink()
            os.mkfifo(device_output / "target.gba")
            self.assertNotEqual(validate(device).returncode, 0)

            unexpected, unexpected_output = make_builder("unexpected")
            (unexpected_output / "extra").write_bytes(b"not an admitted output")
            self.assertNotEqual(validate(unexpected).returncode, 0)

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
        self.assertIn(
            'artifact_names="$(/usr/bin/find "$PATCH_ARTIFACT_DIR" -mindepth 1',
            self.patch_job,
        )
        self.assertIn('test ! -L "$artifact"', self.patch_job)
        self.assertIn(
            'test "$(/usr/bin/stat -c %F "$artifact")" = "regular file"',
            self.patch_job,
        )
        self.assertIn(
            'test "$(/usr/bin/stat -c %h "$artifact")" = 1',
            self.patch_job,
        )

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

                (artifact / "extra-dir").mkdir()
                self.assertNotEqual(
                    artifact_filename_set_check(artifact, "C").returncode,
                    0,
                )
                (artifact / "extra-dir").rmdir()

                (artifact / "README.txt").unlink()
                (artifact / "README.txt").symlink_to("manifest.json")
                self.assertNotEqual(
                    artifact_filename_set_check(artifact, "C").returncode,
                    0,
                )
                (artifact / "README.txt").unlink()
                (artifact / "README.txt").write_bytes(b"artifact")

                outside_link = artifact.parent / "outside-link"
                os.link(artifact / "README.txt", outside_link)
                self.assertNotEqual(
                    artifact_filename_set_check(artifact, "C").returncode,
                    0,
                )
                outside_link.unlink()

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
            self.patch_job,
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

        self.assertEqual(install_interpreters, {"$HOME/venv/bin/python3"})
        self.assertEqual(publisher_interpreters, {"/usr/bin/python3"})

    def test_embedded_publisher_shell_and_python_are_syntactically_valid(self):
        for step_index, step in enumerate(patch_release_step_blocks(self.text)):
            if "      run: |\n" not in step:
                continue
            lines = step.splitlines()
            run_index = lines.index("      run: |")
            script = "\n".join(
                line[8:] for line in lines[run_index + 1:]
                if line.startswith("        ")
            )
            with self.subTest(step=step_index, language="shell"):
                completed = subprocess.run(
                    ["/bin/bash", "-n"],
                    input=script,
                    text=True,
                    check=False,
                    capture_output=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

        for step_index, commands in enumerate(
            parse_patch_release_run_commands(self.text)
        ):
            for command_index, command in enumerate(commands):
                if "/bin/bash" in command and "-c" in command:
                    bash_index = command.index("/bin/bash")
                    command_flag = command.index("-c", bash_index)
                    with self.subTest(
                        step=step_index,
                        command=command_index,
                        language="nested-shell",
                    ):
                        completed = subprocess.run(
                            ["/bin/bash", "-n"],
                            input=command[command_flag + 1],
                            text=True,
                            check=False,
                            capture_output=True,
                        )
                        self.assertEqual(
                            completed.returncode,
                            0,
                            completed.stderr,
                        )
                if "/usr/bin/python3" in command and "-c" in command:
                    python_index = command.index("/usr/bin/python3")
                    command_flag = command.index("-c", python_index)
                    with self.subTest(
                        step=step_index,
                        command=command_index,
                        language="python",
                    ):
                        compile(
                            command[command_flag + 1],
                            "<patch-release-workflow>",
                            "exec",
                        )

        isolated_step = next(
            step
            for step in patch_release_step_blocks(self.text)
            if "Build candidate in isolated namespace and stage public inputs"
            in step
        )
        for delimiter in ("BUILDER_ISOLATION", "CANDIDATE_BUILD"):
            match = re.search(
                rf"(?ms)<<'{delimiter}'\n(?P<body>.*?)^        {delimiter}$",
                isolated_step,
            )
            self.assertIsNotNone(match, delimiter)
            body = "\n".join(
                line[8:] if line.startswith("        ") else line
                for line in match.group("body").splitlines()
            )
            with self.subTest(language="heredoc-shell", delimiter=delimiter):
                completed = subprocess.run(
                    ["/bin/bash", "-n"],
                    input=body,
                    text=True,
                    check=False,
                    capture_output=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
