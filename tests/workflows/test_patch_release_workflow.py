"""Static safety contract for issue #49's trusted patch publisher."""

from __future__ import annotations

import ast
import http.server
import io
import itertools
import json
import os
import re
import resource
import shlex
import socket
import subprocess
import tempfile
import textwrap
import time
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import yaml

from scripts.workflow_pilot import publisher_shell_contract
from scripts.modernize import patch_release


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
PATCH_RELEASE_CASE = ROOT / "docs" / "test-cases" / "patch-release.md"
PATCH_RELEASE_OVERVIEW = ROOT / "docs" / "patch_release.md"
PATCH_RELEASE_REGISTRY = ROOT / "docs" / "test-cases" / "registry.json"
MERGED_MASTER_771 = "771d38c5a531f2d63b269220727b02aa820cc3d4"
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
SUPERVISOR_PARENT_REMOUNT_SEQUENCE = (
    "remount",
    "ro",
    "nosuid",
    "nodev",
    "noexec",
)
SUPERVISOR_PARENT_REMOUNT_INSERTION_MARKER = (
    "        /usr/bin/mount -t tmpfs \\\n"
    "          -o nosuid,mode=0755,size=4m builder-dev /dev"
)


def parse_patch_release_run_commands(workflow: str) -> list[list[list[str]]]:
    """Parse run scalars from the publisher job's YAML sequence structure."""
    commands = []
    for step_block in patch_release_step_blocks(workflow):
        step = step_block.splitlines()
        run = None
        for index, line in enumerate(step):
            inline = re.match(r"^    - run: (?P<value>.+)$", line)
            field = re.match(r"^      run: (?P<value>.+)$", line)
            match = inline or field
            if match is None:
                continue
            value = match.group("value")
            if value.startswith("|"):
                script = named_step_run_script_from_block(step_block)
                try:
                    run = [
                        shlex.split(logical)
                        for logical in publisher_shell_contract.bash_logical_lines(
                            script,
                            label="publisher run block",
                        )
                        if logical.strip() and not logical.lstrip().startswith("#")
                    ]
                except ValueError as error:
                    raise AssertionError(str(error)) from error
            else:
                run = [shlex.split(value)]
            break
        if run is not None:
            commands.append(run)
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


def named_step_run_script_from_block(step_block: str) -> str:
    try:
        return publisher_shell_contract.literal_run_script_from_step_block(
            step_block,
            label="publisher run block",
        )
    except ValueError as error:
        raise AssertionError(str(error)) from error


def named_patch_release_step_block(workflow: str, name: str) -> str:
    steps = patch_release_step_blocks(workflow)
    matches = [
        step for step in steps if f"    - name: {name}\n" in step
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one publisher step named {name!r}")
    return matches[0]


def named_step_run_script(workflow: str, name: str) -> str:
    return named_step_run_script_from_block(named_patch_release_step_block(workflow, name))


def safe_yaml_step_run_script(step_block: str) -> str:
    parsed = yaml.safe_load("steps:\n" + step_block)
    if (
        not isinstance(parsed, dict)
        or not isinstance(parsed.get("steps"), list)
        or len(parsed["steps"]) != 1
        or not isinstance(parsed["steps"][0], dict)
        or "run" not in parsed["steps"][0]
        or not isinstance(parsed["steps"][0]["run"], str)
    ):
        raise AssertionError("safe YAML reference must yield one literal run string")
    return parsed["steps"][0]["run"]


def builder_isolation_shell_source(workflow: str) -> str:
    script = named_step_run_script(
        workflow,
        "Build candidate in isolated namespace and stage public inputs",
    )
    try:
        return publisher_shell_contract.builder_isolation_shell_source(
            script,
            label="publisher builder isolation shell",
        )
    except ValueError as error:
        raise AssertionError(str(error)) from error


def workflow_has_supervisor_parent_readonly_remount(workflow: str) -> bool:
    builder_shell = builder_isolation_shell_source(workflow)
    return publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
        builder_shell,
        label="publisher builder isolation shell",
    )


def render_supervisor_parent_remount_mutation(
    workflow: str,
    *,
    lines: tuple[str, ...],
) -> str:
    rendered = "".join(f"        {line}\n" for line in lines)
    return workflow.replace(
        SUPERVISOR_PARENT_REMOUNT_INSERTION_MARKER,
        rendered + SUPERVISOR_PARENT_REMOUNT_INSERTION_MARKER,
        1,
    )


def generate_supervisor_parent_remount_mutations(workflow: str):
    for ordering in itertools.permutations(SUPERVISOR_PARENT_REMOUNT_SEQUENCE):
        option_text = ",".join(ordering)
        yield (
            "ordering:" + option_text,
            render_supervisor_parent_remount_mutation(
                workflow,
                lines=(f"/usr/bin/mount -o {option_text} /mnt/supervisor",),
            ),
        )

    for label, lines in (
        (
            "duplicate-ro",
            ("/usr/bin/mount -o remount,ro,ro,nosuid,nodev,noexec /mnt/supervisor",),
        ),
        (
            "extra-options",
            (
                "/usr/bin/mount -o "
                "remount,ro,nosuid,nodev,noexec,strictatime /mnt/supervisor",
            ),
        ),
        (
            "comment-late",
            ("/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor # late",),
        ),
        (
            "comment-backslash-whitespace",
            (
                "true # note \\",
                "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor",
            ),
        ),
        (
            "comment-backslash-operator",
            (
                "true; # note \\",
                "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor",
            ),
        ),
        (
            "semicolon-true",
            ("/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor; true",),
        ),
        (
            "and-true",
            ("/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor && true",),
        ),
        (
            "or-true",
            ("/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor || true",),
        ),
        (
            "pipe-cat",
            ("/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor | cat",),
        ),
        (
            "bang-wrapper",
            ("! /usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor",),
        ),
        (
            "if-wrapper",
            (
                "if /usr/bin/mount -o remount,ro,nosuid,nodev,noexec "
                "/mnt/supervisor; then true; fi",
            ),
        ),
        (
            "single-quoted-options",
            ("/usr/bin/mount -o 'remount,ro,nosuid,nodev,noexec' /mnt/supervisor",),
        ),
        (
            "double-quoted-options",
            ('/usr/bin/mount -o "remount,ro,nosuid,nodev,noexec" /mnt/supervisor',),
        ),
        (
            "single-quoted-target",
            ("/usr/bin/mount -o remount,ro,nosuid,nodev,noexec '/mnt/supervisor'",),
        ),
        (
            "double-quoted-target",
            ('/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "/mnt/supervisor"',),
        ),
        (
            "multiple-o-read-only-flag",
            (
                "/usr/bin/mount -o remount,nosuid,nodev "
                "--options noexec -r /mnt/supervisor",
            ),
        ),
        (
            "long-options-read-only",
            (
                "/usr/bin/mount --options remount,nosuid,nodev,noexec "
                "--read-only /mnt/supervisor",
            ),
        ),
        (
            "long-options-equals",
            (
                "/usr/bin/mount --options=remount,nosuid,nodev,noexec "
                "--read-only /mnt/supervisor",
            ),
        ),
        (
            "canonical-dot",
            ("/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/./supervisor",),
        ),
        (
            "canonical-dotdot",
            (
                "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "
                "/mnt/runtime/../supervisor",
            ),
        ),
        (
            "canonical-double-slash",
            ("/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt//supervisor",),
        ),
        (
            "variable-target",
            ('/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$SUPERVISOR_TARGET"',),
        ),
        (
            "variable-options",
            ('/usr/bin/mount -o "$SUPERVISOR_OPTS" /mnt/supervisor',),
        ),
        (
            "generic-target-var",
            (
                "target=/mnt/supervisor",
                '/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$target"',
            ),
        ),
        (
            "exec-var",
            (
                "mount_cmd=/usr/bin/mount",
                '"$mount_cmd" -o remount,ro,nosuid,nodev,noexec /mnt/supervisor',
            ),
        ),
        (
            "command-substitution-exec",
            ('$(printf /usr/bin/mount) -o remount,ro,nosuid,nodev,noexec /mnt/supervisor',),
        ),
        (
            "backtick-exec",
            ('`printf /usr/bin/mount` -o remount,ro,nosuid,nodev,noexec /mnt/supervisor',),
        ),
        (
            "option-var",
            (
                "opts=remount,ro,nosuid,nodev,noexec",
                '/usr/bin/mount -o "$opts" /mnt/supervisor',
            ),
        ),
        (
            "split-option-vars",
            (
                "opt_a=remount,ro",
                "opt_b=nosuid,nodev,noexec",
                '/usr/bin/mount -o "$opt_a,$opt_b" /mnt/supervisor',
            ),
        ),
        (
            "indirect-target",
            (
                "target=/mnt/supervisor",
                "name=target",
                '/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "${!name}"',
            ),
        ),
        (
            "array-target",
            (
                "targets[0]=/mnt/supervisor",
                '/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "${targets[0]}"',
            ),
        ),
        (
            "concat-target",
            (
                "base=/mnt",
                '/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "${base}/supervisor"',
            ),
        ),
        (
            "eval-wrapper",
            (
                "target=/mnt/supervisor",
                'eval "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec \\"$target\\""',
            ),
        ),
        (
            "env-wrapper",
            ("env /usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor",),
        ),
        (
            "command-wrapper",
            ("command /usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor",),
        ),
        (
            "multiple-o-variable",
            (
                '/usr/bin/mount --options remount,nosuid,nodev '
                '--options "$SUPERVISOR_OPTS" -r /mnt/supervisor',
            ),
        ),
        (
            "option-indirect",
            (
                "opts=remount,ro,nosuid,nodev,noexec",
                "name=opts",
                '/usr/bin/mount -o "${!name}" /mnt/supervisor',
            ),
        ),
        (
            "fully-variable",
            (
                "mount_cmd=/usr/bin/mount",
                "opts=remount,ro,nosuid,nodev,noexec",
                "target=/mnt/supervisor",
                '"$mount_cmd" -o "$opts" "$target"',
            ),
        ),
        (
            "split-cmd-target-options",
            (
                "mount_cmd=/usr/bin/mount",
                "opt_left=remount,ro",
                "opt_right=nosuid,nodev,noexec",
                "target_root=/mnt",
                "target_leaf=supervisor",
                'target="${target_root}/${target_leaf}"',
                '"$mount_cmd" -o "$opt_left,$opt_right" "$target"',
            ),
        ),
        (
            "repurposed-hidden-direct",
            workflow.replace(
                '          /usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$hidden"\n',
                '          hidden=/mnt/supervisor\n'
                '          /usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$hidden"\n',
                1,
            ),
        ),
        (
            "repurposed-hidden-loop",
            workflow.replace(
                "        for hidden in /home/runner /root /var /run /sys; do\n",
                "        for hidden in /mnt/supervisor /home/runner /root /var /run /sys; do\n",
                1,
            ),
        ),
        (
            "repurposed-hidden-split",
            workflow.replace(
                '          /usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$hidden"\n',
                '          hidden_base=/mnt\n'
                '          hidden_leaf=supervisor\n'
                '          hidden="${hidden_base}/${hidden_leaf}"\n'
                '          /usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$hidden"\n',
                1,
            ),
        ),
    ):
        if isinstance(lines, str):
            yield label, lines
        else:
            yield label, render_supervisor_parent_remount_mutation(workflow, lines=lines)

    pieces: list[str] = []
    for index, token in enumerate(SUPERVISOR_PARENT_REMOUNT_SEQUENCE):
        pieces.append(token)
        if index + 1 < len(SUPERVISOR_PARENT_REMOUNT_SEQUENCE):
            pieces.append(",")

    def render_split(
        boundaries: tuple[int, ...],
        indent: int,
        trailing_backslashes: int,
    ) -> tuple[str, ...]:
        lines: list[str] = []
        current = ["/usr/bin/mount -o "]
        for index, piece in enumerate(pieces):
            current.append(piece)
            if index + 1 in boundaries:
                lines.append("".join(current) + ("\\" * trailing_backslashes))
                current = [" " * indent]
        current.append(" /mnt/supervisor")
        lines.append("".join(current))
        return tuple(lines)

    for boundary in range(1, len(pieces)):
        for indent in (0, 2, 8):
            for backslashes in (1, 2, 3):
                yield (
                    f"split:{boundary}:indent:{indent}:backslashes:{backslashes}",
                    render_supervisor_parent_remount_mutation(
                        workflow,
                        lines=render_split((boundary,), indent, backslashes),
                    ),
                )

    all_boundaries = tuple(range(1, len(pieces)))
    for indent in (0, 2, 8):
        for backslashes in (1, 2, 3):
            yield (
                f"multisplit:indent:{indent}:backslashes:{backslashes}",
                render_supervisor_parent_remount_mutation(
                    workflow,
                    lines=render_split(all_boundaries, indent, backslashes),
                ),
            )

    for backslashes in (1, 2, 3):
        yield (
            f"double-quoted-split:backslashes:{backslashes}",
            render_supervisor_parent_remount_mutation(
                workflow,
                lines=(
                    '/usr/bin/mount -o "remount,ro,nosuid,' + ("\\" * backslashes),
                    '        nodev,noexec" /mnt/supervisor',
                ),
            ),
        )
        yield (
            f"single-quoted-split:backslashes:{backslashes}",
            render_supervisor_parent_remount_mutation(
                workflow,
                lines=(
                    "/usr/bin/mount -o 'remount,ro,nosuid," + ("\\" * backslashes),
                    "        nodev,noexec' /mnt/supervisor",
                ),
            ),
        )


def render_isolated_publisher_step_mutation(
    workflow: str,
    *,
    mutate,
) -> str:
    step = named_patch_release_step_block(
        workflow,
        "Build candidate in isolated namespace and stage public inputs",
    )
    changed_step = mutate(step)
    if changed_step == step:
        raise AssertionError("isolated publisher mutation did not change the step")
    return workflow.replace(step, changed_step, 1)


def generate_publisher_raw_identity_mutations(workflow: str):
    mutations = (
        (
            "extra-blank-before-heredoc",
            lambda step: step.replace(
                '        /usr/bin/tee "$BUILDER_ROOT/control/builder-isolation.sh" \\\n',
                '        \n'
                '        /usr/bin/tee "$BUILDER_ROOT/control/builder-isolation.sh" \\\n',
                1,
            ),
        ),
        (
            "extra-blank-in-builder-shell",
            lambda step: step.replace(
                '        builder_gid="$3"\n',
                '        builder_gid="$3"\n'
                '        \n',
                1,
            ),
        ),
        (
            "remove-blank-in-parser-heredoc",
            lambda step: step.replace(
                "        MAX_BYTES = 1048576\n\n\n        def fail(message):\n",
                "        MAX_BYTES = 1048576\n\n        def fail(message):\n",
                1,
            ),
        ),
        (
            "blank-line-extra-indentation",
            lambda step: step.replace(
                "        MAX_BYTES = 1048576\n\n\n        def fail(message):\n",
                "        MAX_BYTES = 1048576\n          \n\n        def fail(message):\n",
                1,
            ),
        ),
        (
            "builder-shell-trailing-space",
            lambda step: step.replace(
                "        cd /\n",
                "        cd / \n",
                1,
            ),
        ),
        (
            "builder-shell-indent-shift",
            lambda step: step.replace(
                "        cd /\n",
                "         cd /\n",
                1,
            ),
        ),
        (
            "run-strip-chomp",
            lambda step: step.replace("      run: |\n", "      run: |-\n", 1),
        ),
    )
    for label, mutate in mutations:
        yield label, render_isolated_publisher_step_mutation(
            workflow,
            mutate=mutate,
        )


def dev_mount_target_parser_source(workflow: str) -> str:
    return textwrap.dedent(raw_dev_mount_target_parser_source(workflow))


def dev_mount_transport_section_source(workflow: str) -> str:
    script = named_step_run_script(
        workflow,
        "Build candidate in isolated namespace and stage public inputs",
    )
    start = script.index("create_supervisor_transport_file() {")
    end_marker = (
        "/usr/bin/mount -t tmpfs \\\n"
        "  -o nosuid,mode=0755,size=4m builder-dev /dev"
    )
    end = script.index(end_marker, start) + len(end_marker)
    return script[start:end]


def writable_mount_record_parser_source(workflow: str) -> str:
    return textwrap.dedent(raw_writable_mount_record_parser_source(workflow))


def raw_dev_mount_target_parser_source(workflow: str) -> str:
    try:
        sources = dict(
            publisher_shell_contract.raw_patch_release_parser_sources(
                builder_isolation_shell_source(workflow)
            )
        )
    except ValueError as error:
        raise AssertionError(str(error)) from error
    try:
        return sources["list_dev_mount_targets"]
    except KeyError as error:
        raise AssertionError(
            "publisher must expose an exact raw /dev mount parser"
        ) from error


def raw_writable_mount_record_parser_source(workflow: str) -> str:
    try:
        sources = dict(
            publisher_shell_contract.raw_patch_release_parser_sources(
                builder_isolation_shell_source(workflow)
            )
        )
    except ValueError as error:
        raise AssertionError(str(error)) from error
    try:
        return sources["list_writable_mount_records"]
    except KeyError as error:
        raise AssertionError(
            "publisher must expose an exact raw writable mount parser"
        ) from error


def writable_mount_transport_section_source(workflow: str) -> str:
    script = named_step_run_script(
        workflow,
        "Build candidate in isolated namespace and stage public inputs",
    )
    start = script.index("list_writable_mount_records() {")
    end_marker = "exec < /dev/null > /dev/null 2>&1\n"
    end = script.index(end_marker, start)
    return script[start:end]


def run_dev_mount_target_parser(
    source: str,
    *,
    stdout: bytes,
    returncode: int = 0,
    stderr: bytes = b"",
) -> tuple[int, bytes, str]:
    class StdoutCapture:
        def __init__(self):
            self.buffer = io.BytesIO()

        def write(self, text):
            return len(text)

        def flush(self):
            return None

    stdout_capture = StdoutCapture()
    stderr_capture = io.StringIO()
    completed = subprocess.CompletedProcess(
        ["/usr/bin/findmnt"],
        returncode,
        stdout,
        stderr,
    )
    with (
        mock.patch("subprocess.run", return_value=completed),
        mock.patch("sys.stdout", stdout_capture),
        redirect_stderr(stderr_capture),
    ):
        code = 0
        try:
            exec(
                compile(source, "<decoded-dev-mount-parser>", "exec"),
                {"__name__": "__main__"},
            )
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, stdout_capture.buffer.getvalue(), stderr_capture.getvalue()


def run_writable_mount_record_parser(
    source: str,
    *,
    stdout: bytes,
    returncode: int = 0,
    stderr: bytes = b"",
) -> tuple[int, bytes, str]:
    class StdoutCapture:
        def __init__(self):
            self.buffer = io.BytesIO()

        def write(self, text):
            return len(text)

        def flush(self):
            return None

    stdout_capture = StdoutCapture()
    stderr_capture = io.StringIO()
    completed = subprocess.CompletedProcess(
        ["/usr/bin/findmnt"],
        returncode,
        stdout,
        stderr,
    )
    with (
        mock.patch("subprocess.run", return_value=completed),
        mock.patch("sys.stdout", stdout_capture),
        redirect_stderr(stderr_capture),
    ):
        code = 0
        try:
            exec(
                compile(source, "<writable-mount-record-parser>", "exec"),
                {"__name__": "__main__"},
            )
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, stdout_capture.buffer.getvalue(), stderr_capture.getvalue()


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
    try:
        run_script = named_step_run_script(
            workflow,
            "Build candidate in isolated namespace and stage public inputs",
        )
        publisher_shell_contract.assert_reviewed_patch_release_run_script_identity(
            run_script,
            label="publisher isolated candidate build run script",
        )
        builder_shell = publisher_shell_contract.builder_isolation_shell_source(
            run_script,
            label="publisher builder isolation shell",
        )
        publisher_shell_contract.assert_reviewed_builder_isolation_shell_identity(
            builder_shell,
            label="publisher builder isolation shell",
        )
        publisher_shell_contract.validate_patch_release_parser_heredocs(
            builder_shell,
            label="publisher builder isolation shell",
        )
    except (AssertionError, ValueError):
        errors.append("publisher builder isolation shell differs")
    if workflow_has_supervisor_parent_readonly_remount(workflow):
        errors.append("supervisor parent remount differs")
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
        or '"GITHUB_ENV": os.environ' in isolated_step
        or '"BASH_ENV": os.environ' in isolated_step
        or '"GITHUB_OUTPUT": os.environ' in isolated_step
        or '"GITHUB_PATH": os.environ' in isolated_step
        or '"GITHUB_STEP_SUMMARY": os.environ' in isolated_step
        or "close_inherited_fds" in isolated_step
        or "/proc/$$/fd" in isolated_step
        or "candidate-launcher.py" not in isolated_step
        or 'getattr(os, "close_range", None)' not in isolated_step
        or "os.closerange(3, MAX_FD)" not in isolated_step
        or "os.execve(candidate_argv[0], candidate_argv, candidate_env)"
        not in isolated_step
        or "MAX_FD = 1_048_576" not in isolated_step
        or "raise SystemExit(125 if bad else 0)" not in isolated_step
        or isolated_step.count('exec < /dev/null > /dev/null 2>&1') != 2
        or '< /dev/null > /dev/null 2>&1 &' not in isolated_step
        or "builder-capture" in isolated_step
        or "candidate-output.log" in isolated_step
        or "candidate_sink" in isolated_step
        or "sink_size" in isolated_step
        or "list_dev_mount_targets() {" not in isolated_step
        or (
            '["/usr/bin/findmnt", "--json", "--submounts", "--output", "TARGET", "/dev"]'
            not in isolated_step
        )
        or "object_pairs_hook=reject_duplicates" not in isolated_step
        or "findmnt target escapes /dev" not in isolated_step
        or "findmnt target contains NUL" not in isolated_step
        or "builder-supervisor /mnt/supervisor" not in isolated_step
        or 'path="$(/usr/bin/mktemp "/mnt/supervisor/$1.XXXXXXXXXX")"'
        not in isolated_step
        or 'path="$(/usr/bin/mktemp "/dev/shm/$1.XXXXXXXXXX")"'
        not in isolated_step
        or "create_supervisor_transport_file() {" not in isolated_step
        or "checked_supervisor_transport_signature() {" not in isolated_step
        or "read_checked_supervisor_transport_file() {" not in isolated_step
        or "remove_supervisor_transport_file() {" not in isolated_step
        or "list_writable_mount_records() {" not in isolated_step
        or (
            '["/usr/bin/findmnt", "--json", "--list", "--uniq", "--output", "TARGET,OPTIONS", "-R", "/"]'
            not in isolated_step
        )
        or "duplicate writable mount JSON key" not in isolated_step
        or "findmnt target is not absolute" not in isolated_step
        or 'options = validate_options(filesystem.get("options"))' not in isolated_step
        or "findmnt option tokens are invalid" not in isolated_step
        or "unexpected writable mount audit row keys" not in isolated_step
        or "create_runtime_transport_file() {" not in isolated_step
        or "checked_runtime_transport_signature() {" not in isolated_step
        or "read_checked_runtime_transport_file() {" not in isolated_step
        or "remove_runtime_transport_file() {" not in isolated_step
        or isolated_step.count('test ! -L "$path" || return 125') != 4
        or 'test "$(/usr/bin/stat -c %a "$path")" = 600' not in isolated_step
        or isolated_step.count(
            'test "$(/usr/bin/stat -c %h "$path")" = 1 || return 125'
        )
        != 4
        or 'size="$(/usr/bin/stat -c %s "$path")"' not in isolated_step
        or 'test "$size" -le "$size_limit" || return 125' not in isolated_step
        or "stat -Lc '%d:%i:%f:%u:%g:%s:%h:%a' \"$path\"" not in isolated_step
        or isolated_step.count('test ! -e "$path" || return 125') != 2
        or 'list_dev_mount_targets > "$dev_mounts_file"' not in isolated_step
        or (
            'read_checked_supervisor_transport_file \\\n'
            '          "$dev_mounts_file" "$dev_mount_targets_max_bytes" dev_mounts'
        )
        not in isolated_step
        or 'remove_supervisor_transport_file "$dev_mounts_file"'
        not in isolated_step
        or (
            "for ((index=${#dev_mounts[@]} - 1; index >= 0; index--)); do"
        )
        not in isolated_step
        or '/dev/*) /usr/bin/umount -- "$dev_mount" ;;'
        not in isolated_step
        or 'list_dev_mount_targets > "$remaining_dev_mounts_file"'
        not in isolated_step
        or (
            'read_checked_supervisor_transport_file \\\n'
            '          "$remaining_dev_mounts_file" \\\n'
            '          "$dev_mount_targets_max_bytes" \\\n'
            "          remaining_dev_mounts"
        )
        not in isolated_step
        or 'remove_supervisor_transport_file "$remaining_dev_mounts_file"'
        not in isolated_step
        or 'test "${#remaining_dev_mounts[@]}" -eq 1' not in isolated_step
        or 'test "${remaining_dev_mounts[0]}" = /dev' not in isolated_step
        or 'list_writable_mount_records > "$writable_mount_records_file"' not in isolated_step
        or (
            'read_checked_runtime_transport_file \\\n'
            '          "$writable_mount_records_file" \\\n'
            '          "$writable_mount_records_max_bytes" \\\n'
            '          writable_mount_records'
        )
        not in isolated_step
        or 'remove_runtime_transport_file "$writable_mount_records_file"'
        not in isolated_step
        or 'test "$(( ${#writable_mount_records[@]} % 2 ))" -eq 0'
        not in isolated_step
        or 'mount_target="${writable_mount_records[index]}"' not in isolated_step
        or 'mount_options="${writable_mount_records[index + 1]}"' not in isolated_step
        or "/usr/bin/findmnt -Rrno TARGET /dev" in isolated_step
        or "/usr/bin/findmnt --raw" in isolated_step
        or "< <(list_dev_mount_targets)" in isolated_step
        or "< <(list_writable_mount_records)" in isolated_step
        or "/usr/bin/findmnt -Rrno TARGET,OPTIONS /" in isolated_step
        or "/usr/bin/findmnt -Rno TARGET,OPTIONS /" in isolated_step
        or 'builder_isolation_script="$(/bin/cat \\\n'
        not in isolated_step
        or '/bin/bash -c "$builder_isolation_script" builder-isolation'
        not in isolated_step
        or '/bin/bash "$BUILDER_ROOT/control/builder-isolation.sh"'
        in isolated_step
        or "unmount_if_mounted /dev" in isolated_step
        or '/usr/bin/sudo /bin/rm -rf -- "$BUILDER_ROOT"'
        not in isolated_step
        or "ulimit -c 0" not in isolated_step
        or "ulimit -f 131072" not in isolated_step
        or "ulimit -n 128" not in isolated_step
        or "ulimit -u 512" not in isolated_step
        or "ulimit -v 8388608" not in isolated_step
        or 'test "$cgroup_members" = "$$"' not in isolated_step
        or "size=6g builder-source /mnt/source" not in isolated_step
        or "size=1g builder-home /mnt/home" not in isolated_step
        or "size=1g builder-temp /mnt/tmp" not in isolated_step
        or "size=40m" not in isolated_step
        or "builder-handoff /mnt/handoff" not in isolated_step
        or 'test -z "${GITHUB_OUTPUT-}"' not in isolated_step
        or 'test -z "${GITHUB_PATH-}"' not in isolated_step
        or 'test -z "${GITHUB_STEP_SUMMARY-}"' not in isolated_step
        or 'test -z "${BASH_XTRACEFD-}"' not in isolated_step
        or 'test ! -e /dev/console' not in isolated_step
        or 'test ! -e /dev/kmsg' not in isolated_step
        or "candidate build failed: stage=launch detail=%s exit=%d"
        not in isolated_step
        or "candidate build failed: stage=isolated exit=%d"
        not in isolated_step
        or "candidate build cleanup failed: process=%d cgroup=%d state=%d primary=%d"
        not in isolated_step
        or "$builder_cgroup/cgroup.kill\" > /dev/null 2>&1" not in isolated_step
        or "/usr/bin/rmdir -- \"$builder_cgroup\" \\\n                 > /dev/null 2>&1"
        not in isolated_step
        or "/usr/sbin/userdel \"$builder_user\" \\\n                > /dev/null 2>&1"
        not in isolated_step
        or "/bin/rm -rf -- \"$BUILDER_ROOT\" \\\n              > /dev/null 2>&1"
        not in isolated_step
        or "/bin/rm -rf -- \"$PATCH_WHEELHOUSE\" > /dev/null 2>&1"
        not in isolated_step
        or "candidate build status: success" not in isolated_step
        or "/usr/bin/mount --make-rprivate /" not in isolated_step
        or "/usr/bin/mount -o remount,bind,ro /" not in isolated_step
        or "runner temp is outside the masked host tree" not in isolated_step
        or "for hidden in /home/runner /root /var /run /sys; do"
        not in isolated_step
        or "builder-tmp /tmp" not in isolated_step
        or "builder-dev /dev" not in isolated_step
        or "builder-shm /dev/shm" not in isolated_step
        or "hidepid=2 /proc" not in isolated_step
        or "/usr/bin/mkdir -m 0700 /mnt/supervisor\n" not in isolated_step
        or (
            "/usr/bin/mount -t tmpfs \\\n"
            "          -o nosuid,nodev,noexec,mode=0700,size=1m \\\n"
            "          builder-supervisor /mnt/supervisor"
        )
        not in isolated_step
        or "/usr/bin/mkdir -m 0700 /mnt/supervisor/cgroup"
        not in isolated_step
        or 'test "$(/usr/bin/stat -c %u /mnt/supervisor)" = 0'
        not in isolated_step
        or 'test "$(/usr/bin/stat -c %a /mnt/supervisor)" = 700'
        not in isolated_step
        or '/usr/bin/mount --bind "$cgroup_path" /mnt/supervisor/cgroup'
        not in isolated_step
        or (
            "/usr/bin/mount -o remount,bind,ro,nosuid,nodev,noexec \\\n"
            "          /mnt/supervisor/cgroup"
        )
        not in isolated_step
        or "supervisor_cgroup=/mnt/supervisor/cgroup" not in isolated_step
        or 'stat -Lc %d:%i "$supervisor_cgroup/cgroup.procs"'
        not in isolated_step
        or "for option in ro nosuid nodev noexec; do" not in isolated_step
        or 'test ! -r /mnt/supervisor' not in isolated_step
        or 'test ! -w /mnt/supervisor' not in isolated_step
        or 'test ! -x /mnt/supervisor' not in isolated_step
        or 'test ! -r /mnt/supervisor/cgroup/cgroup.procs'
        not in isolated_step
        or '"$supervisor_cgroup/cgroup.procs"' not in isolated_step
        or (
            'cgroup_members="$(LC_ALL=C /usr/bin/sort -n \\\n'
            '          "$cgroup_path/cgroup.procs")"'
        )
        in isolated_step
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
        or "builder_cgroup_is_empty" not in isolated_step
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
        or isolated_step.count('test "$handoff_names" = ') != 2
        or 'builder_uid_is_empty "$builder_uid"' not in isolated_step
        or 'builder_group_is_empty "$builder_pgid"' not in isolated_step
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
            'builder_group_is_empty "$builder_pgid"\n'
            '        builder_cgroup_is_empty\n'
            '        builder_uid_is_empty "$builder_uid"\n'
            '        remove_builder_cgroup\n'
            '        test ! -e "$builder_cgroup"\n'
            '        handoff_root='
        )
        not in isolated_step
        or (
            'remove_builder_cgroup\n'
            '        remove_builder_state\n'
            '        trap - EXIT INT TERM\n'
            '        builder_group_is_empty "$builder_pgid"\n'
            '        test ! -e "$builder_cgroup"\n'
            '        builder_uid_is_empty "$builder_uid"\n'
            '        builder_passwd_entry_absent "$builder_user"\n'
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


def _bounded_process_text(stream: str | bytes | None, limit: int = 120) -> str:
    if stream is None:
        return "<empty>"
    if isinstance(stream, bytes):
        stream = stream.decode("utf-8", errors="replace")
    collapsed = " ".join(stream.split())
    if not collapsed:
        return "<empty>"
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def _bounded_process_diagnostic(
    completed: subprocess.CompletedProcess,
    *,
    stream_limit: int = 120,
    total_limit: int = 240,
) -> str:
    diagnostic = (
        f"rc={completed.returncode}, "
        f"stdout={_bounded_process_text(completed.stdout, stream_limit)!r}, "
        f"stderr={_bounded_process_text(completed.stderr, stream_limit)!r}"
    )
    if len(diagnostic) <= total_limit:
        return diagnostic
    return diagnostic[: total_limit - 3] + "..."


FINDMNT_UNIQ_NAMESPACE_PREFLIGHT_SCRIPT = """\
set -euo pipefail
root="$1"
cleanup() {
  local status=0
  if [ -e "$root/target-upper-bound" ]; then
    umount -- "$root/target" || status=1
    rm -f -- "$root/target-upper-bound"
  fi
  if [ -e "$root/target-lower-bound" ]; then
    umount -- "$root/target" || status=1
    rm -f -- "$root/target-lower-bound"
  fi
  if [ -e "$root/upper-mounted" ]; then
    umount -- "$root/upper" || status=1
    rm -f -- "$root/upper-mounted"
  fi
  if [ -e "$root/lower-mounted" ]; then
    umount -- "$root/lower" || status=1
    rm -f -- "$root/lower-mounted"
  fi
  return "$status"
}
trap cleanup EXIT
mkdir -p "$root/lower" "$root/upper" "$root/target"
mount -t tmpfs tmpfs "$root/lower"
: > "$root/lower-mounted"
mount -t tmpfs tmpfs "$root/upper"
: > "$root/upper-mounted"
mount --bind "$root/lower" "$root/target"
: > "$root/target-lower-bound"
mount -o remount,bind,ro "$root/target"
mount --bind "$root/upper" "$root/target"
: > "$root/target-upper-bound"
mount -o remount,bind,ro "$root/target"
cleanup
trap - EXIT
"""


FINDMNT_UNIQ_NAMESPACE_PROBE_SCRIPT = """\
set -euo pipefail
root="$1"
lower_mode="$2"
upper_mode="$3"
cleanup() {
  local status=0
  if [ -e "$root/target-upper-bound" ]; then
    umount -- "$root/target" || status=1
    rm -f -- "$root/target-upper-bound"
  fi
  if [ -e "$root/target-lower-bound" ]; then
    umount -- "$root/target" || status=1
    rm -f -- "$root/target-lower-bound"
  fi
  if [ -e "$root/upper-mounted" ]; then
    umount -- "$root/upper" || status=1
    rm -f -- "$root/upper-mounted"
  fi
  if [ -e "$root/lower-mounted" ]; then
    umount -- "$root/lower" || status=1
    rm -f -- "$root/lower-mounted"
  fi
  return "$status"
}
trap cleanup EXIT
mkdir -p "$root/lower" "$root/upper" "$root/target"
mount -t tmpfs tmpfs "$root/lower"
: > "$root/lower-mounted"
mount -t tmpfs tmpfs "$root/upper"
: > "$root/upper-mounted"
mount --bind "$root/lower" "$root/target"
: > "$root/target-lower-bound"
if [ "$lower_mode" = ro ]; then
  mount -o remount,bind,ro "$root/target"
fi
mount --bind "$root/upper" "$root/target"
: > "$root/target-upper-bound"
if [ "$upper_mode" = ro ]; then
  mount -o remount,bind,ro "$root/target"
fi
findmnt --json --list --output TARGET,OPTIONS,ID,PARENT -R "$root/target" > "$root/all.json"
findmnt --json --list --uniq --output TARGET,OPTIONS,ID,PARENT -R "$root/target" > "$root/uniq.json"
cleanup
trap - EXIT
"""


def run_rootless_mount_namespace(
    script: str,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "unshare",
            "--user",
            "--map-root-user",
            "--mount",
            "--pid",
            "--fork",
            "/bin/bash",
            "-ceu",
            script,
            "--",
            *args,
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def require_findmnt_uniq_namespace_capability(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    completed = run_rootless_mount_namespace(
        FINDMNT_UNIQ_NAMESPACE_PREFLIGHT_SCRIPT,
        str(root),
    )
    if completed.returncode != 0:
        raise unittest.SkipTest(
            "mount namespace capability unavailable: "
            + _bounded_process_diagnostic(completed)
        )


def _load_findmnt_probe_payload(path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AssertionError(f"namespace semantic probe missing {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise AssertionError(f"namespace semantic probe malformed {path.name}") from exc
    if not isinstance(payload, dict) or set(payload) != {"filesystems"}:
        raise AssertionError(f"namespace semantic probe malformed {path.name}")
    rows = payload["filesystems"]
    if not isinstance(rows, list):
        raise AssertionError(f"namespace semantic probe malformed {path.name}")
    normalized = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "options", "parent", "target"}:
            raise AssertionError(f"namespace semantic probe malformed {path.name}")
        normalized.append(row)
    return normalized


def run_findmnt_uniq_namespace_semantic_probe(
    root: Path,
    lower_mode: str,
    upper_mode: str,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    root.mkdir(parents=True, exist_ok=True)
    completed = run_rootless_mount_namespace(
        FINDMNT_UNIQ_NAMESPACE_PROBE_SCRIPT,
        str(root),
        lower_mode,
        upper_mode,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "namespace semantic probe failed: " + _bounded_process_diagnostic(completed)
        )
    return (
        _load_findmnt_probe_payload(root / "all.json"),
        _load_findmnt_probe_payload(root / "uniq.json"),
    )


EXTRACTED_SUPERVISOR_NAMESPACE_HARNESS = """\
set -euo pipefail
section_path="$1"
fake_cgroup="$2"
cleanup() {
  local status=0
  if mountpoint -q /mnt/supervisor/cgroup; then
    umount /mnt/supervisor/cgroup || status=1
  fi
  if mountpoint -q /mnt/supervisor; then
    umount /mnt/supervisor || status=1
  fi
  if mountpoint -q /mnt; then
    umount /mnt || status=1
  fi
  return "$status"
}
trap cleanup EXIT
mount -t tmpfs -o nosuid,nodev,noexec,mode=0755,size=16m probe-work /mnt
mkdir -m 0700 /mnt/supervisor
mount -t tmpfs -o nosuid,nodev,noexec,mode=0700,size=1m probe-supervisor /mnt/supervisor
mkdir -m 0700 /mnt/supervisor/cgroup
mount --bind "$fake_cgroup" /mnt/supervisor/cgroup
mount -o remount,bind,ro,nosuid,nodev,noexec /mnt/supervisor/cgroup
options="$(findmnt -n -o OPTIONS --target /mnt/supervisor/cgroup)"
for option in ro nosuid nodev noexec; do
  case ",$options," in
    *,"$option",*) ;;
    *) exit 125 ;;
  esac
done
path="$(mktemp "/mnt/supervisor/test.XXXXXXXXXX")"
test -f "$path"
test ! -L "$path"
test "$(/usr/bin/stat -c %u "$path")" = 0
test "$(/usr/bin/stat -c %a "$path")" = 600
test "$(/usr/bin/stat -c %h "$path")" = 1
/bin/rm -f -- "$path"
test ! -e "$path"
list_dev_mount_targets() {
  printf '%s\\0' /dev
}
source "$section_path"
cleanup
trap - EXIT
"""


def run_extracted_supervisor_parent_probe(
    root: Path,
    workflow: str,
) -> subprocess.CompletedProcess[str]:
    root.mkdir(parents=True, exist_ok=True)
    fake_cgroup = root / "fake-cgroup"
    fake_cgroup.mkdir(parents=True, exist_ok=True)
    for name in ("cgroup.procs", "cgroup.kill"):
        (fake_cgroup / name).write_text("", encoding="utf-8")
    section = dev_mount_transport_section_source(workflow)
    end_marker = (
        "/usr/bin/mount -t tmpfs \\\n"
        "  -o nosuid,mode=0755,size=4m builder-dev /dev"
    )
    if end_marker not in section:
        raise AssertionError("exact workflow probe must end at the /dev overmount")
    section = section.replace(end_marker, "printf 'PASS\\n'\n", 1)
    section_path = root / "section.sh"
    section_path.write_text(section, encoding="utf-8")
    return run_rootless_mount_namespace(
        EXTRACTED_SUPERVISOR_NAMESPACE_HARNESS,
        str(section_path),
        str(fake_cgroup),
    )


def builder_cleanup_functions_source(workflow: str) -> str:
    script = named_step_run_script(
        workflow,
        "Build candidate in isolated namespace and stage public inputs",
    )
    start = script.index('shell_pgid="$(/usr/bin/ps -o pgid= -p "$$"')
    end = script.index("trap cleanup_builder EXIT")
    return script[start:end]


def builder_passwd_helpers_source(workflow: str) -> str:
    section = builder_cleanup_functions_source(workflow)
    start = section.index("builder_passwd_entry_exists() {")
    end = section.index("builder_group_pids() {", start)
    return section[start:end]


def builder_uid_selection_helpers_source(workflow: str) -> str:
    section = builder_cleanup_functions_source(workflow)
    start = section.index("builder_passwd_entry_exists() {")
    end = section.index("builder_cgroup_is_empty() {", start)
    return section[start:end]


def builder_uid_occupancy_helpers_source(workflow: str) -> str:
    section = builder_cleanup_functions_source(workflow)
    start = section.index("builder_uid_pids() {")
    end = section.index("builder_cgroup_is_empty() {", start)
    return section[start:end]


def builder_user_selection_source(workflow: str) -> str:
    script = named_step_run_script(
        workflow,
        "Build candidate in isolated namespace and stage public inputs",
    )
    start = script.index(
        'builder_passwd_entry_absent "$builder_user"',
        script.index("wheelhouse_owned=1"),
    )
    end_marker = 'test "$builder_uid" -ge 50000'
    end = script.index(end_marker, start) + len(end_marker)
    return script[start:end]


def private_base_cleanup_function_source(workflow: str) -> str:
    script = named_step_run_script(
        workflow,
        "Create and verify patch artifact",
    )
    start = script.index("cleanup_private_base() {")
    end = script.index("trap cleanup_private_base EXIT")
    return script[start:end]


def download_cleanup_function_source(workflow: str) -> str:
    script = named_step_run_script(
        workflow,
        "Download private base image",
    )
    start = script.index("cleanup_download() {")
    end = script.index("trap cleanup_download EXIT")
    return script[start:end]


def launch_validation_source(workflow: str) -> str:
    script = named_step_run_script(
        workflow,
        "Build candidate in isolated namespace and stage public inputs",
    )
    start = script.index('builder_supervisor_pid="$!"')
    end = script.index('set +e\nwait "$builder_supervisor_pid"', start)
    return script[start:end]


def patch_release_python_c_snippets(workflow: str) -> list[tuple[int, int, str]]:
    snippets: list[tuple[int, int, str]] = []
    for step_index, commands in enumerate(parse_patch_release_run_commands(workflow)):
        for command_index, command in enumerate(commands):
            if "/usr/bin/python3" not in command:
                continue
            python_index = command.index("/usr/bin/python3")
            if python_index + 4 >= len(command):
                continue
            if command[python_index + 1 : python_index + 4] != ["-I", "-S", "-c"]:
                continue
            snippets.append((step_index, command_index, command[python_index + 4]))
    return snippets


def assert_patch_release_python_c_snippets_compile(testcase: unittest.TestCase, workflow: str) -> None:
    snippets = patch_release_python_c_snippets(workflow)
    testcase.assertEqual(len(snippets), 4)
    for step_index, command_index, source in snippets:
        with testcase.subTest(
            step=step_index,
            command=command_index,
            language="python",
        ):
            compile(source, "<patch-release-workflow>", "exec")


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
        self.assertIn(
            '/usr/bin/mount --bind "$cgroup_path" /mnt/supervisor/cgroup',
            self.patch_job,
        )
        self.assertIn("supervisor_cgroup=/mnt/supervisor/cgroup", self.patch_job)
        self.assertIn('test ! -r /mnt/supervisor', self.patch_job)
        self.assertIn('test ! -w /mnt/supervisor', self.patch_job)
        self.assertIn('test ! -x /mnt/supervisor', self.patch_job)
        self.assertIn(
            "builder-supervisor /mnt/supervisor",
            self.patch_job,
        )
        supervisor_bind = self.patch_job.index(
            '/usr/bin/mount --bind "$cgroup_path" /mnt/supervisor/cgroup'
        )
        sys_mask = self.patch_job.index(
            "unmount_if_mounted /sys",
            supervisor_bind,
        )
        membership = self.patch_job.index(
            'cgroup_members="$(LC_ALL=C /usr/bin/sort -n',
            sys_mask,
        )
        self.assertLess(supervisor_bind, sys_mask)
        self.assertLess(sys_mask, membership)
        self.assertIn("for hidden in /home/runner /root /var /run /sys; do", self.patch_job)
        self.assertIn("/run/dbus/system_bus_socket", self.patch_job)
        self.assertIn("/run/docker.sock", self.patch_job)
        self.assertIn("/run/containerd/containerd.sock", self.patch_job)
        self.assertIn("/run/systemd/private", self.patch_job)
        self.assertIn("/run/snapd.socket", self.patch_job)
        self.assertIn("/usr/bin/setpriv", self.patch_job)
        self.assertIn("--bounding-set=-all", self.patch_job)
        self.assertIn('"$builder_cgroup/cgroup.kill"', self.patch_job)
        self.assertIn("builder_cgroup_is_empty", self.patch_job)
        self.assertIn('test ! -e "$builder_cgroup"', self.patch_job)
        self.assertNotRegex(self.patch_job, r"/bin/kill[^\n]*[\"']?\$pid")
        self.assertNotIn("close_inherited_fds", self.patch_job)
        self.assertNotIn("/proc/$$/fd", self.patch_job)
        self.assertIn("candidate-launcher.py", self.patch_job)
        self.assertIn('getattr(os, "close_range", None)', self.patch_job)
        self.assertIn("os.closerange(3, MAX_FD)", self.patch_job)
        self.assertIn(
            "os.execve(candidate_argv[0], candidate_argv, candidate_env)",
            self.patch_job,
        )
        self.assertEqual(
            self.patch_job.count('exec < /dev/null > /dev/null 2>&1'),
            2,
        )
        self.assertNotIn("builder-capture", self.patch_job)
        self.assertNotIn("candidate-output.log", self.patch_job)
        self.assertIn("ulimit -f 131072", self.patch_job)
        self.assertIn("size=6g builder-source /mnt/source", self.patch_job)
        self.assertIn(
            "candidate build failed: stage=launch detail=%s exit=%d",
            self.patch_job,
        )
        self.assertIn("candidate build failed: stage=isolated exit=%d", self.patch_job)
        self.assertIn(
            "candidate build cleanup failed: process=%d cgroup=%d state=%d primary=%d",
            self.patch_job,
        )
        self.assertIn("builder_passwd_entry_absent", self.patch_job)
        self.assertIn('"$builder_cgroup/cgroup.kill" > /dev/null 2>&1', self.patch_job)
        self.assertIn(
            '/usr/bin/sudo /usr/bin/rmdir -- "$builder_cgroup" \\\n'
            '                 > /dev/null 2>&1',
            self.patch_job,
        )
        self.assertIn(
            '/usr/bin/sudo /usr/sbin/userdel "$builder_user" \\\n'
            '                > /dev/null 2>&1',
            self.patch_job,
        )
        self.assertIn(
            '/usr/bin/sudo /bin/rm -rf -- "$BUILDER_ROOT" \\\n'
            '              > /dev/null 2>&1',
            self.patch_job,
        )
        self.assertIn(
            '/bin/rm -rf -- "$PATCH_WHEELHOUSE" > /dev/null 2>&1',
            self.patch_job,
        )
        self.assertIn("candidate build status: success", self.patch_job)
        stop = self.patch_job.index(
            "builder_cgroup_is_empty",
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
            'builder_uid_is_empty "$builder_uid"',
            self.patch_job,
        )
        self.assertIn(
            'builder_group_is_empty "$builder_pgid"',
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

    def test_device_mount_teardown_executes_deepest_first(self):
        section = dev_mount_transport_section_source(self.text)
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="device-mount-transport-order-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            trace = sandbox / "trace"
            supervisor_root = sandbox / "supervisor"
            supervisor_root.mkdir(mode=0o700)
            section = section.replace(
                "/mnt/supervisor",
                "$SUPERVISOR_ROOT",
            )
            section = section.replace(
                'test "$(/usr/bin/stat -c %u "$path")" = 0 || return 125',
                'test "$(/usr/bin/stat -c %u "$path")" = "$SUPERVISOR_UID" || return 125',
            )
            section = section.replace(
                "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec $SUPERVISOR_ROOT",
                "printf 'OVERMOUNT\\n' >> \"$TRACE_PATH\"",
            )
            section = section.replace(
                '/usr/bin/umount -- "$dev_mount"',
                "printf '%s\\0' \"$dev_mount\"",
            )
            section = section.replace(
                "/usr/bin/mount -t tmpfs \\\n"
                "  -o nosuid,mode=0755,size=4m builder-dev /dev",
                "true",
            )
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    "set -euo pipefail\n"
                    "umask 077\n"
                    "list_dev_mount_targets_calls=0\n"
                    "list_dev_mount_targets() {\n"
                    "  list_dev_mount_targets_calls=$((list_dev_mount_targets_calls + 1))\n"
                    "  if [ \"$list_dev_mount_targets_calls\" -eq 1 ]; then\n"
                    "    printf '%s\\0' \\\n"
                    "      /dev \\\n"
                    "      $'/dev/name with space' \\\n"
                    "      $'/dev/back\\\\slash' \\\n"
                    "      $'/dev/back\\\\slash/tab\\tchild' \\\n"
                    "      $'/dev/back\\\\slash/new\\nline' \\\n"
                    "      /dev/pts \\\n"
                    "      /dev/pts/9\n"
                    "  else\n"
                    "    printf '%s\\0' /dev\n"
                    "  fi\n"
                    "}\n"
                    'TRACE_PATH="$1"\n'
                    'SUPERVISOR_ROOT="$2"\n'
                    'SUPERVISOR_UID="$3"\n'
                    + section,
                    "--",
                    str(trace),
                    str(supervisor_root),
                    str(os.getuid()),
                ],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=ROOT,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout.split(b"\0")[:-1],
                [
                    b"/dev/pts/9",
                    b"/dev/pts",
                    b"/dev/back\\slash/new\nline",
                    b"/dev/back\\slash/tab\tchild",
                    b"/dev/back\\slash",
                    b"/dev/name with space",
                ],
            )
            self.assertFalse(trace.exists())
            self.assertEqual(list(supervisor_root.iterdir()), [])

    def test_device_mount_transport_propagates_producer_failure_before_unmount_or_overmount(self):
        section = dev_mount_transport_section_source(self.text)
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="device-mount-transport-failure-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            trace = sandbox / "trace"
            supervisor_root = sandbox / "supervisor"
            supervisor_root.mkdir(mode=0o700)
            section = section.replace("/mnt/supervisor", "$SUPERVISOR_ROOT")
            section = section.replace(
                'test "$(/usr/bin/stat -c %u "$path")" = 0 || return 125',
                'test "$(/usr/bin/stat -c %u "$path")" = "$SUPERVISOR_UID" || return 125',
            )
            section = section.replace(
                '/usr/bin/umount -- "$dev_mount"',
                "printf 'UMOUNT:%s\\n' \"$dev_mount\" >> \"$TRACE_PATH\"",
            )
            section = section.replace(
                "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec $SUPERVISOR_ROOT",
                "printf 'SUPERVISOR-LOCK\\n' >> \"$TRACE_PATH\"",
            )
            section = section.replace(
                "/usr/bin/mount -t tmpfs \\\n"
                "  -o nosuid,mode=0755,size=4m builder-dev /dev",
                "printf 'OVERMOUNT\\n' >> \"$TRACE_PATH\"",
            )
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    "set -euo pipefail\n"
                    "umask 077\n"
                    "list_dev_mount_targets() {\n"
                    "  printf 'parser failed\\n' >&2\n"
                    "  return 125\n"
                    "}\n"
                    'TRACE_PATH="$1"\n'
                    'SUPERVISOR_ROOT="$2"\n'
                    'SUPERVISOR_UID="$3"\n'
                    + section,
                    "--",
                    str(trace),
                    str(supervisor_root),
                    str(os.getuid()),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 125, completed.stderr)
            self.assertIn("parser failed", completed.stderr)
            self.assertFalse(trace.exists())

    def test_device_mount_transport_rejects_symlink_hardlink_and_stale_files(self):
        base_section = dev_mount_transport_section_source(self.text)
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="device-mount-transport-controls-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            trace = sandbox / "trace"
            supervisor_root = sandbox / "supervisor"
            supervisor_root.mkdir(mode=0o700)

            def clear_supervisor_root():
                for path in list(supervisor_root.iterdir()):
                    if path.is_dir():
                        for child in list(path.iterdir()):
                            child.unlink()
                        path.rmdir()
                    else:
                        path.unlink()

            section = base_section.replace("/mnt/supervisor", "$SUPERVISOR_ROOT")
            section = section.replace(
                'test "$(/usr/bin/stat -c %u "$path")" = 0',
                'test "$(/usr/bin/stat -c %u "$path")" = "$SUPERVISOR_UID"',
            )
            section = section.replace(
                'path="$(/usr/bin/mktemp "$SUPERVISOR_ROOT/$1.XXXXXXXXXX")" || return 125',
                'case "$1" in\n'
                '    dev-mount-targets) path="$SUPERVISOR_ROOT/dev-mount-targets.fixture" ;;\n'
                '    *) path="$(/usr/bin/mktemp "$SUPERVISOR_ROOT/$1.XXXXXXXXXX")" || return 125 ;;\n'
                "  esac",
            )
            section = section.replace(
                '/usr/bin/umount -- "$dev_mount"',
                "printf 'UMOUNT:%s\\n' \"$dev_mount\" >> \"$TRACE_PATH\"",
            )
            section = section.replace(
                "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec $SUPERVISOR_ROOT",
                "printf 'SUPERVISOR-LOCK\\n' >> \"$TRACE_PATH\"",
            )
            section = section.replace(
                "/usr/bin/mount -t tmpfs \\\n"
                "  -o nosuid,mode=0755,size=4m builder-dev /dev",
                "printf 'OVERMOUNT\\n' >> \"$TRACE_PATH\"",
            )

            def run_case(setup):
                clear_supervisor_root()
                if trace.exists():
                    trace.unlink()
                setup()
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        "set -euo pipefail\n"
                        "umask 077\n"
                        "list_dev_mount_targets() {\n"
                        "  printf '%s\\0' /dev /dev/pts\n"
                        "}\n"
                        'TRACE_PATH="$1"\n'
                        'SUPERVISOR_ROOT="$2"\n'
                        'SUPERVISOR_UID="$3"\n'
                        + section,
                        "--",
                        str(trace),
                        str(supervisor_root),
                        str(os.getuid()),
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertFalse(trace.exists())
                return completed

            def make_symlink():
                target = supervisor_root / "symlink-target"
                target.write_bytes(b"")
                (supervisor_root / "dev-mount-targets.fixture").symlink_to(target)

            def make_hardlink():
                target = supervisor_root / "hardlink-target"
                target.write_bytes(b"")
                os.link(target, supervisor_root / "dev-mount-targets.fixture")

            for name, setup, error in (
                ("symlink", make_symlink, ""),
                ("hardlink", make_hardlink, ""),
            ):
                with self.subTest(case=name):
                    completed = run_case(setup)
                    self.assertNotEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout, "")

            clear_supervisor_root()
            success_section = base_section.replace("/mnt/supervisor", "$SUPERVISOR_ROOT")
            success_section = success_section.replace(
                'test "$(/usr/bin/stat -c %u "$path")" = 0 || return 125',
                'test "$(/usr/bin/stat -c %u "$path")" = "$SUPERVISOR_UID" || return 125',
            )
            success_section = success_section.replace(
                "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec $SUPERVISOR_ROOT",
                "true",
            )
            success_section = success_section.replace(
                '/usr/bin/umount -- "$dev_mount"',
                "true",
            )
            success_section = success_section.replace(
                "/usr/bin/mount -t tmpfs \\\n"
                "  -o nosuid,mode=0755,size=4m builder-dev /dev",
                "true",
            )
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    "set -euo pipefail\n"
                    "umask 077\n"
                    "list_dev_mount_targets() {\n"
                    "  printf '%s\\0' /dev\n"
                    "}\n"
                    'SUPERVISOR_ROOT="$1"\n'
                    'SUPERVISOR_UID="$2"\n'
                    + success_section,
                    "--",
                    str(supervisor_root),
                    str(os.getuid()),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(list(supervisor_root.iterdir()), [])

    def test_dev_mount_target_parser_supports_decoded_paths_and_rejects_bad_json(self):
        source = dev_mount_target_parser_source(self.text)
        payload = {
            "filesystems": [
                {
                    "target": "/dev",
                    "children": [
                        {"target": "/dev/name with space"},
                        {
                            "target": "/dev/back\\slash",
                            "children": [
                                {"target": "/dev/back\\slash/tab\tchild"},
                                {"target": "/dev/back\\slash/new\nline"},
                            ],
                        },
                        {
                            "target": "/dev/pts",
                            "children": [{"target": "/dev/pts/9"}],
                        },
                    ],
                }
            ]
        }
        code, output, stderr = run_dev_mount_target_parser(
            source,
            stdout=json.dumps(payload).encode("utf-8"),
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(
            output.split(b"\0")[:-1],
            [
                b"/dev",
                b"/dev/name with space",
                b"/dev/back\\slash",
                b"/dev/back\\slash/tab\tchild",
                b"/dev/back\\slash/new\nline",
                b"/dev/pts",
                b"/dev/pts/9",
            ],
        )
        for name, stdout, expected in (
            ("malformed", b'{"filesystems":[', "invalid findmnt JSON"),
            (
                "duplicate-key",
                b'{"filesystems":[{"target":"/dev","target":"/dev/dup"}]}',
                "duplicate findmnt JSON key",
            ),
            (
                "outside-dev",
                json.dumps({"filesystems": [{"target": "/etc"}]}).encode("utf-8"),
                "findmnt target escapes /dev",
            ),
            (
                "nul-target",
                json.dumps({"filesystems": [{"target": "/dev/\u0000bad"}]}).encode("utf-8"),
                "findmnt target contains NUL",
            ),
        ):
            with self.subTest(case=name):
                code, _output, stderr = run_dev_mount_target_parser(
                    source,
                    stdout=stdout,
                )
                self.assertEqual(code, 125)
                self.assertIn(expected, stderr)

    def test_local_findmnt_json_shape_is_supported(self):
        completed = subprocess.run(
            ["/usr/bin/findmnt", "--json", "--submounts", "--output", "TARGET", "/dev"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(set(payload), {"filesystems"})
        self.assertIsInstance(payload["filesystems"], list)
        self.assertEqual(len(payload["filesystems"]), 1)

        targets = []

        def visit(node):
            targets.append(node["target"])
            for child in node.get("children", []):
                visit(child)

        visit(payload["filesystems"][0])
        self.assertEqual(targets[0], "/dev")
        for target in targets:
            self.assertTrue(target == "/dev" or target.startswith("/dev/"))

    def test_writable_mount_record_parser_supports_decoded_targets_and_rejects_bad_json(self):
        source = writable_mount_record_parser_source(self.text)
        payload = {
            "filesystems": [
                {"target": "/", "options": "ro,relatime"},
                {"target": "/mnt/home", "options": "rw,nosuid,nodev"},
                {"target": "/mnt/name with space", "options": "rw,relatime"},
                {"target": "/mnt/back\\slash", "options": "errors=remount-ro,data=ordered"},
                {"target": "/tmp", "options": "rw,nosuid,nodev"},
            ]
        }
        code, output, stderr = run_writable_mount_record_parser(
            source,
            stdout=json.dumps(payload).encode("utf-8"),
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(
            output.split(b"\0")[:-1],
            [
                b"/",
                b"ro,relatime",
                b"/mnt/home",
                b"rw,nosuid,nodev",
                b"/mnt/name with space",
                b"rw,relatime",
                b"/mnt/back\\slash",
                b"errors=remount-ro,data=ordered",
                b"/tmp",
                b"rw,nosuid,nodev",
            ],
        )
        oversized = {
            "filesystems": [
                {"target": f"/mnt/{index}", "options": "ro"}
                for index in range(513)
            ]
        }
        for name, stdout, expected in (
            ("malformed", b'{"filesystems":[', "invalid writable mount audit JSON"),
            (
                "duplicate-key",
                b'{"filesystems":[{"target":"/","target":"/dup","options":"ro"}]}',
                "duplicate writable mount JSON key",
            ),
            (
                "missing-options",
                json.dumps({"filesystems": [{"target": "/mnt/home"}]}).encode("utf-8"),
                "findmnt options is invalid",
            ),
            (
                "empty-options-token",
                json.dumps({"filesystems": [{"target": "/mnt/home", "options": "rw,,nodev"}]}).encode("utf-8"),
                "findmnt option tokens are invalid",
            ),
            (
                "spaced-options-token",
                json.dumps({"filesystems": [{"target": "/mnt/home", "options": "rw, relatime"}]}).encode("utf-8"),
                "findmnt option tokens are invalid",
            ),
            (
                "control-char-target",
                json.dumps({"filesystems": [{"target": "/mnt/bad\tpath", "options": "rw"}]}).encode("utf-8"),
                "findmnt target contains control character",
            ),
            (
                "nonabsolute-target",
                json.dumps({"filesystems": [{"target": "mnt/home", "options": "rw"}]}).encode("utf-8"),
                "findmnt target is not absolute",
            ),
            (
                "extra-row-keys",
                json.dumps(
                    {"filesystems": [{"target": "/mnt/home", "options": "rw", "children": []}]}
                ).encode("utf-8"),
                "unexpected writable mount audit row keys",
            ),
            (
                "too-many-rows",
                json.dumps(oversized).encode("utf-8"),
                "writable mount audit mount count exceeds bounds",
            ),
        ):
            with self.subTest(case=name):
                code, _output, stderr = run_writable_mount_record_parser(
                    source,
                    stdout=stdout,
                )
                self.assertEqual(code, 125)
                self.assertIn(expected, stderr)

        for name, returncode, stderr, expected in (
            ("findmnt-failed", 1, b"", "findmnt writable mount audit failed"),
            ("findmnt-stderr", 0, b"warning", "findmnt writable mount audit wrote stderr"),
        ):
            with self.subTest(case=name):
                code, _output, captured = run_writable_mount_record_parser(
                    source,
                    stdout=b"",
                    returncode=returncode,
                    stderr=stderr,
                )
                self.assertEqual(code, 125)
                self.assertIn(expected, captured)

    def test_local_writable_mount_findmnt_json_shape_is_supported(self):
        all_completed = subprocess.run(
            ["/usr/bin/findmnt", "--json", "--list", "--output", "TARGET,OPTIONS,ID,PARENT", "-R", "/"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(all_completed.returncode, 0, all_completed.stderr)
        all_payload = json.loads(all_completed.stdout)
        completed = subprocess.run(
            ["/usr/bin/findmnt", "--json", "--list", "--uniq", "--output", "TARGET,OPTIONS,ID,PARENT", "-R", "/"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(set(payload), {"filesystems"})
        self.assertIsInstance(payload["filesystems"], list)
        self.assertGreater(len(payload["filesystems"]), 0)
        duplicate_targets = set()
        seen_all = set()
        for row in all_payload["filesystems"]:
            if row["target"] in seen_all:
                duplicate_targets.add(row["target"])
            seen_all.add(row["target"])
        seen = set()
        for row in payload["filesystems"]:
            self.assertEqual(set(row), {"id", "options", "parent", "target"})
            self.assertIsInstance(row["target"], str)
            self.assertTrue(row["target"].startswith("/"))
            self.assertIsInstance(row["options"], str)
            self.assertTrue(row["options"])
            self.assertNotIn(row["target"], seen)
            seen.add(row["target"])
        for target in duplicate_targets:
            self.assertIn(target, seen_all)
            self.assertIn(target, seen)

    def test_findmnt_uniq_selects_effective_topmost_mount_in_namespace(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="findmnt-uniq-effective-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            require_findmnt_uniq_namespace_capability(sandbox / "preflight")
            for name, lower_mode, upper_mode, expected_top in (
                ("lower-rw-top-ro", "rw", "ro", "ro"),
                ("lower-ro-top-rw", "ro", "rw", "rw"),
            ):
                case_root = sandbox / name
                all_rows, uniq_rows = run_findmnt_uniq_namespace_semantic_probe(
                    case_root,
                    lower_mode,
                    upper_mode,
                )
                target = str(case_root / "target")
                all_rows = [row for row in all_rows if row["target"] == target]
                uniq_rows = [row for row in uniq_rows if row["target"] == target]
                self.assertEqual(len(all_rows), 2)
                self.assertEqual(len(uniq_rows), 1)
                self.assertIsInstance(uniq_rows[0]["id"], int)
                uniq_tokens = set(uniq_rows[0]["options"].split(","))
                if expected_top == "ro":
                    self.assertIn("ro", uniq_tokens)
                    self.assertNotIn("rw", uniq_tokens)
                    self.assertTrue(
                        any("rw" in set(row["options"].split(",")) for row in all_rows)
                    )
                else:
                    self.assertIn("rw", uniq_tokens)
                self.assertTrue(
                    any("ro" in set(row["options"].split(",")) for row in all_rows)
                )
                self.assertGreaterEqual(uniq_rows[0]["id"], min(row["id"] for row in all_rows))

    def test_findmnt_uniq_namespace_preflight_failure_skips_with_bounded_diagnostic(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        completed = subprocess.CompletedProcess(
            args=["unshare"],
            returncode=1,
            stdout="",
            stderr=("Operation not permitted " * 40).strip(),
        )
        with tempfile.TemporaryDirectory(
            prefix="findmnt-uniq-preflight-",
            dir=artifact_root,
        ) as temporary, mock.patch(
            f"{__name__}.run_rootless_mount_namespace",
            return_value=completed,
        ):
            with self.assertRaises(unittest.SkipTest) as context:
                require_findmnt_uniq_namespace_capability(Path(temporary))
        message = str(context.exception)
        self.assertIn("mount namespace capability unavailable:", message)
        self.assertIn("rc=1", message)
        self.assertIn("Operation not permitted", message)
        self.assertLessEqual(len(message), 240)

    def test_findmnt_uniq_namespace_probe_failure_is_assertion_failure(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        completed = subprocess.CompletedProcess(
            args=["unshare"],
            returncode=17,
            stdout="",
            stderr="forced probe failure",
        )
        with tempfile.TemporaryDirectory(
            prefix="findmnt-uniq-probe-",
            dir=artifact_root,
        ) as temporary, mock.patch(
            f"{__name__}.run_rootless_mount_namespace",
            return_value=completed,
        ):
            with self.assertRaises(AssertionError) as context:
                run_findmnt_uniq_namespace_semantic_probe(Path(temporary), "rw", "ro")
        self.assertIn("namespace semantic probe failed:", str(context.exception))
        self.assertIn("forced probe failure", str(context.exception))

    def test_writable_mount_audit_rejects_unexpected_rw_targets_and_preserves_allowed_private_mounts(self):
        base_section = writable_mount_transport_section_source(self.text)
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="writable-mount-audit-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            transport_root = sandbox / "transport"
            transport_root.mkdir(mode=0o700)
            records_path = sandbox / "records"

            section = base_section.replace(
                'path="$(/usr/bin/mktemp "/dev/shm/$1.XXXXXXXXXX")" || return 125',
                'path="$(/usr/bin/mktemp "$TRANSPORT_ROOT/$1.XXXXXXXXXX")" || return 125',
            )
            section = section.replace(
                'test "$(/usr/bin/stat -c %u "$path")" = 0',
                'test "$(/usr/bin/stat -c %u "$path")" = "$TRANSPORT_UID"',
            )
            section = re.sub(
                r"(?ms)^list_writable_mount_records\(\) \{\n.*?\n\}\n",
                "list_writable_mount_records() {\n"
                '  /bin/cat -- "$RECORDS_PATH"\n'
                "}\n",
                section,
                count=1,
            )

            def run_case(records: list[str]) -> subprocess.CompletedProcess[str]:
                if records_path.exists():
                    records_path.unlink()
                records_path.write_bytes(
                    b"".join(record.encode("utf-8") + b"\0" for record in records)
                )
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        "set -euo pipefail\n"
                        "umask 077\n"
                        'TRANSPORT_ROOT="$1"\n'
                        'TRANSPORT_UID="$2"\n'
                        'RECORDS_PATH="$3"\n'
                        + section,
                        "--",
                        str(transport_root),
                        str(os.getuid()),
                        str(records_path),
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                return completed

            completed = run_case(
                [
                    "/",
                    "ro,relatime",
                    "/mnt/home",
                    "rw,nosuid,nodev",
                    "/mnt/source",
                    "rw,nosuid,nodev",
                    "/mnt/handoff",
                    "rw,nosuid,nodev",
                    "/mnt/tmp",
                    "rw,nosuid,nodev",
                    "/tmp",
                    "rw,nosuid,nodev",
                    "/dev/shm",
                    "rw,nosuid,nodev",
                    "/mnt/name with space",
                    "errors=remount-ro,data=ordered",
                ]
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "")

            completed = run_case(
                [
                    "/",
                    "ro,relatime",
                    "/mnt/name with space",
                    "rw,relatime",
                ]
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn(
                "unexpected writable mount: /mnt/name with space",
                completed.stderr,
            )

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
        self.assertIn('/bin/rm -f -- "$BASE_IMAGE" > /dev/null 2>&1', create_step)
        self.assertIn('/usr/bin/rmdir -- "$private_dir" > /dev/null 2>&1', create_step)
        self.assertIn(
            "BASE_IMAGE: ${{ steps.private-base.outputs.base_path }}",
            create_step,
        )
        self.assertNotIn("BASEROM_URL", create_step)
        self.assertIn('test ! -e "$BASE_IMAGE"', steps[cleanup])
        self.assertIn('/bin/rm -f -- "$BASE_IMAGE" > /dev/null 2>&1', steps[cleanup])
        self.assertIn('/usr/bin/rmdir -- "$private_dir" > /dev/null 2>&1', steps[cleanup])
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
            '/bin/rm -f -- "$BASE_IMAGE" > /dev/null 2>&1 || cleanup_failed=1',
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
                "builder_cgroup_is_empty",
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
            '            "GITHUB_WORKSPACE": "/mnt/source",',
            '            "GITHUB_ENV": os.environ["GITHUB_ENV"],\n'
            '            "GITHUB_WORKSPACE": "/mnt/source",',
            1,
        )
        leaked_bash_env = self.text.replace(
            '            "GITHUB_WORKSPACE": "/mnt/source",',
            '            "BASH_ENV": os.environ["BASH_ENV"],\n'
            '            "GITHUB_WORKSPACE": "/mnt/source",',
            1,
        )
        inherited_actions_log = self.text.replace(
            "          < /dev/null > /dev/null 2>&1 &",
            "          &",
            1,
        )
        disabled_child_fd_close = self.text.replace(
            "                os.closerange(3, MAX_FD)",
            "                pass",
        )
        unbounded_source = self.text.replace(
            "size=6g builder-source /mnt/source",
            "size=100% builder-source /mnt/source",
            1,
        )
        removed_file_limit = self.text.replace(
            "        ulimit -f 131072",
            "        true",
            1,
        )
        candidate_output_regular_file = self.text.replace(
            "        exec < /dev/null > /dev/null 2>&1",
            "        exec < /dev/null > /mnt/source/candidate-output.log 2>&1",
            1,
        )
        missing_supervisor_bind = self.text.replace(
            '        /usr/bin/mount --bind "$cgroup_path" '
            "/mnt/supervisor/cgroup",
            "        true",
            1,
        )
        weak_supervisor_permissions = self.text.replace(
            "        /usr/bin/mkdir -m 0700 /mnt/supervisor",
            "        /usr/bin/mkdir -m 0755 /mnt/supervisor",
            1,
        )
        hidden_sys_membership = self.text.replace(
            'cgroup_members="$(LC_ALL=C /usr/bin/sort -n \\\n'
            '          "$supervisor_cgroup/cgroup.procs")"',
            'cgroup_members="$(LC_ALL=C /usr/bin/sort -n \\\n'
            '          "$cgroup_path/cgroup.procs")"',
            1,
        )
        exposed_supervisor_to_candidate = self.text.replace(
            "        test ! -r /mnt/supervisor\n"
            "        test ! -w /mnt/supervisor\n"
            "        test ! -x /mnt/supervisor",
            "        true",
            1,
        )
        leaked_github_output = self.text.replace(
            '            "GITHUB_WORKSPACE": "/mnt/source",',
            '            "GITHUB_OUTPUT": os.environ["GITHUB_OUTPUT"],\n'
            '            "GITHUB_WORKSPACE": "/mnt/source",',
            1,
        )
        leaked_step_summary = self.text.replace(
            '            "GITHUB_WORKSPACE": "/mnt/source",',
            '            "GITHUB_STEP_SUMMARY": os.environ["GITHUB_STEP_SUMMARY"],\n'
            '            "GITHUB_WORKSPACE": "/mnt/source",',
            1,
        )
        launch_stage_free_text = self.text.replace(
            "candidate build failed: stage=launch detail=%s exit=%d",
            "candidate build failed: exit=%d",
            1,
        )
        late_supervisor_parent_remount = self.text.replace(
            "        /usr/bin/mount -t tmpfs \\\n"
            "          -o nosuid,mode=0755,size=4m builder-dev /dev",
            "        /usr/bin/mount -o remount,ro,nosuid,nodev,noexec "
            "/mnt/supervisor\n"
            "        /usr/bin/mount -t tmpfs \\\n"
            "          -o nosuid,mode=0755,size=4m builder-dev /dev",
            1,
        )
        reordered_supervisor_parent_remount = self.text.replace(
            "        /usr/bin/mount -t tmpfs \\\n"
            "          -o nosuid,mode=0755,size=4m builder-dev /dev",
            "        /usr/bin/mount -o nodev,ro,noexec,nosuid,remount "
            "/mnt/supervisor\n"
            "        /usr/bin/mount -t tmpfs \\\n"
            "          -o nosuid,mode=0755,size=4m builder-dev /dev",
            1,
        )
        cleanup_stage_free_text = self.text.replace(
            "candidate build cleanup failed: process=%d cgroup=%d state=%d primary=%d",
            "candidate build cleanup failed",
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
        file_backed_wrapper = self.text.replace(
            '/bin/bash -c "$builder_isolation_script" builder-isolation',
            '/bin/bash "$BUILDER_ROOT/control/builder-isolation.sh"',
            1,
        )
        unmounted_open_dev = self.text.replace(
            "        /usr/bin/mount -t tmpfs \\\n"
            "          -o nosuid,mode=0755,size=4m builder-dev /dev",
            "        unmount_if_mounted /dev\n"
            "        /usr/bin/mount -t tmpfs \\\n"
            "          -o nosuid,mode=0755,size=4m builder-dev /dev",
            1,
        )
        unprivileged_builder_cleanup = self.text.replace(
            '/usr/bin/sudo /bin/rm -rf -- "$BUILDER_ROOT"',
            '/bin/rm -rf -- "$BUILDER_ROOT"',
            1,
        )
        structured_writable_mount_targets = self.text.replace(
            '["/usr/bin/findmnt", "--json", "--list", "--uniq", "--output", "TARGET,OPTIONS", "-R", "/"]',
            '["/usr/bin/findmnt", "-Rrno", "TARGET,OPTIONS", "/"]',
            1,
        )
        nonuniq_writable_mount_targets = self.text.replace(
            '["/usr/bin/findmnt", "--json", "--list", "--uniq", "--output", "TARGET,OPTIONS", "-R", "/"]',
            '["/usr/bin/findmnt", "--json", "--list", "--output", "TARGET,OPTIONS", "-R", "/"]',
            1,
        )
        retained_dev_descendants = self.text.replace(
            '/dev/*) /usr/bin/umount -- "$dev_mount" ;;',
            "/dev/*) true ;;",
            1,
        )
        escaped_dev_targets = self.text.replace(
            '["/usr/bin/findmnt", "--json", "--submounts", "--output", "TARGET", "/dev"]',
            '["/usr/bin/findmnt", "-Rrno", "TARGET", "/dev"]',
            1,
        )
        unchecked_dev_process_substitution = self.text.replace(
            '        dev_mounts_file="$(create_supervisor_transport_file dev-mount-targets)"\n'
            '        list_dev_mount_targets > "$dev_mounts_file"\n'
            "        read_checked_supervisor_transport_file \\\n"
            '          "$dev_mounts_file" "$dev_mount_targets_max_bytes" dev_mounts\n'
            '        remove_supervisor_transport_file "$dev_mounts_file"',
            "        mapfile -d '' -t dev_mounts < <(list_dev_mount_targets)",
            1,
        )
        unchecked_writable_mount_process_substitution = self.text.replace(
            '        writable_mount_records_file="$(create_runtime_transport_file writable-mount-records)"\n'
            '        list_writable_mount_records > "$writable_mount_records_file"\n'
            "        read_checked_runtime_transport_file \\\n"
            '          "$writable_mount_records_file" \\\n'
            '          "$writable_mount_records_max_bytes" \\\n'
            '          writable_mount_records\n'
            '        remove_runtime_transport_file "$writable_mount_records_file"',
            "        mapfile -d '' -t writable_mount_records < <(list_writable_mount_records)",
            1,
        )
        missing_dev_transport_link_guard = self.text.replace(
            '          test "$(/usr/bin/stat -c %h "$path")" = 1 || return 125',
            "          true",
            1,
        )
        missing_dev_transport_symlink_guard = self.text.replace(
            '          test ! -L "$path" || return 125',
            "          true",
            1,
        )
        missing_dev_transport_cleanup = self.text.replace(
            '          test ! -e "$path" || return 125',
            "          true",
            1,
        )
        forward_dev_teardown = self.text.replace(
            "for ((index=${#dev_mounts[@]} - 1; "
            "index >= 0; index--)); do",
            "for ((index=0; index < ${#dev_mounts[@]}; index++)); do",
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
            ("inherited-actions-log", inherited_actions_log),
            ("disabled-child-fd-close", disabled_child_fd_close),
            ("unbounded-source", unbounded_source),
            ("removed-file-limit", removed_file_limit),
            ("candidate-output-regular-file", candidate_output_regular_file),
            ("missing-supervisor-bind", missing_supervisor_bind),
            ("weak-supervisor-permissions", weak_supervisor_permissions),
            ("hidden-sys-membership", hidden_sys_membership),
            ("candidate-can-access-supervisor", exposed_supervisor_to_candidate),
            ("leaked-github-output", leaked_github_output),
            ("leaked-step-summary", leaked_step_summary),
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
            ("file-backed-wrapper", file_backed_wrapper),
            ("unmounted-open-dev", unmounted_open_dev),
            ("unprivileged-builder-cleanup", unprivileged_builder_cleanup),
            ("structured-writable-mount-targets", structured_writable_mount_targets),
            ("nonuniq-writable-mount-targets", nonuniq_writable_mount_targets),
            ("retained-dev-descendants", retained_dev_descendants),
            ("escaped-dev-targets", escaped_dev_targets),
            ("unchecked-dev-process-substitution", unchecked_dev_process_substitution),
            (
                "unchecked-writable-mount-process-substitution",
                unchecked_writable_mount_process_substitution,
            ),
            ("missing-dev-transport-symlink-guard", missing_dev_transport_symlink_guard),
            ("missing-dev-transport-link-guard", missing_dev_transport_link_guard),
            ("missing-dev-transport-cleanup", missing_dev_transport_cleanup),
            ("forward-dev-teardown", forward_dev_teardown),
            ("ambient-dependency-python", ambient_dependency_python),
            ("unverified-builder-state", unverified_builder_state),
            ("launch-stage-free-text", launch_stage_free_text),
            ("late-supervisor-parent-remount", late_supervisor_parent_remount),
            (
                "reordered-supervisor-parent-remount",
                reordered_supervisor_parent_remount,
            ),
            ("cleanup-stage-free-text", cleanup_stage_free_text),
            ("allowed-unexpected-handoff", allowed_unexpected_handoff),
            ("disabled-late-revalidation", disabled_late_revalidation),
            ("candidate-patch-artifact-mutation", candidate_patch_artifact_mutation),
            ("complete-rom-artifact-transfer", rom_artifact_transfer),
        ):
            with self.subTest(name=name):
                self.assertNotEqual(changed, self.text)
                self.assertTrue(publisher_boundary_errors(changed))

    def test_extracted_supervisor_transport_probe_fails_on_master_and_passes_current_workflow(self):
        self.assertFalse(workflow_has_supervisor_parent_readonly_remount(self.text))
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="supervisor-parent-remount-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            require_findmnt_uniq_namespace_capability(sandbox / "preflight")
            success = run_extracted_supervisor_parent_probe(
                sandbox / "success",
                self.text,
            )
            self.assertEqual(success.returncode, 0, _bounded_process_diagnostic(success))
            self.assertEqual(success.stdout.strip(), "PASS")
            failed_workflow = subprocess.check_output(
                [
                    "git",
                    "--no-pager",
                    "show",
                    f"{MERGED_MASTER_771}:.github/workflows/build.yml",
                ],
                cwd=ROOT,
                text=True,
            )
            self.assertTrue(workflow_has_supervisor_parent_readonly_remount(failed_workflow))
            failure = run_extracted_supervisor_parent_probe(
                sandbox / "failure",
                failed_workflow,
            )
            self.assertNotEqual(failure.returncode, 0)
            self.assertIn("/mnt/supervisor", failure.stderr)
            self.assertIn("bad option", failure.stderr)

    def test_supervisor_parent_remount_variants_are_rejected_bash_equivalently(self):
        self.assertFalse(workflow_has_supervisor_parent_readonly_remount(self.text))
        for label, changed in generate_supervisor_parent_remount_mutations(self.text):
            with self.subTest(variant=label):
                self.assertTrue(workflow_has_supervisor_parent_readonly_remount(changed))
                self.assertTrue(publisher_boundary_errors(changed))

    def test_publisher_run_scalar_matches_reference_yaml_bytes(self):
        step_block = named_patch_release_step_block(
            self.text,
            "Build candidate in isolated namespace and stage public inputs",
        )
        actual_run = named_step_run_script_from_block(step_block)
        reference_run = safe_yaml_step_run_script(step_block)
        self.assertEqual(actual_run.encode("utf-8"), reference_run.encode("utf-8"))
        self.assertEqual(
            publisher_shell_contract.reviewed_patch_release_run_sha256(actual_run),
            publisher_shell_contract.REVIEWED_PATCH_RELEASE_RUN_SHA256,
        )
        publisher_shell_contract.assert_reviewed_patch_release_run_script_identity(
            actual_run,
            label="publisher isolated candidate build run script",
        )

        actual_shell = publisher_shell_contract.builder_isolation_shell_source(
            actual_run,
            label="publisher builder isolation shell",
        )
        reference_shell = publisher_shell_contract.builder_isolation_shell_source(
            reference_run,
            label="publisher builder isolation shell",
        )
        self.assertEqual(
            actual_shell.encode("utf-8"),
            reference_shell.encode("utf-8"),
        )
        self.assertEqual(
            publisher_shell_contract.reviewed_builder_isolation_sha256(actual_shell),
            publisher_shell_contract.REVIEWED_BUILDER_ISOLATION_SHA256,
        )
        self.assertEqual(
            publisher_shell_contract.reviewed_hidden_mask_loop_sha256(
                actual_shell,
                label="publisher builder isolation shell",
            ),
            publisher_shell_contract.REVIEWED_HIDDEN_MASK_LOOP_SHA256,
        )
        publisher_shell_contract.assert_reviewed_builder_isolation_shell_identity(
            actual_shell,
            label="publisher builder isolation shell",
        )

    def test_bash_comment_backslash_does_not_hide_following_mount(self):
        mount = "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor"
        cases = {
            "comment-after-whitespace": (
                "echo ok # note \\\n"
                f"{mount}\n",
                ("echo ok", mount),
            ),
            "comment-after-operator": (
                "echo ok; # note \\\n"
                f"{mount}\n",
                ("echo ok", mount),
            ),
            "hash-inside-word": (
                f"echo foo#bar\n{mount}\n",
                ("echo foo#bar", mount),
            ),
            "hash-inside-quotes": (
                f'echo "#still-not-comment" # note \\\n{mount}\n',
                ('echo "#still-not-comment"', mount),
            ),
        }
        for label, (script, expected_commands) in cases.items():
            with self.subTest(case=label):
                self.assertEqual(
                    publisher_shell_contract.split_bash_simple_command_strings(
                        script,
                        label=label,
                    ),
                    expected_commands,
                )
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                        script,
                        label=label,
                    )
                )

    def test_hidden_loop_scope_is_independent_of_full_shell_identity(self):
        builder_shell = builder_isolation_shell_source(self.text)
        harmless_outside_loop = builder_shell.replace("cd /\n", "cd /\n\n", 1)
        self.assertNotEqual(
            publisher_shell_contract.reviewed_builder_isolation_sha256(
                harmless_outside_loop
            ),
            publisher_shell_contract.REVIEWED_BUILDER_ISOLATION_SHA256,
        )
        with self.assertRaisesRegex(
            ValueError,
            "raw identity differs from the reviewed security boundary",
        ):
            publisher_shell_contract.assert_reviewed_builder_isolation_shell_identity(
                harmless_outside_loop,
                label="publisher builder isolation shell",
            )
        self.assertEqual(
            publisher_shell_contract.reviewed_hidden_mask_loop_sha256(
                harmless_outside_loop,
                label="publisher builder isolation shell",
            ),
            publisher_shell_contract.REVIEWED_HIDDEN_MASK_LOOP_SHA256,
        )
        self.assertFalse(
            publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                harmless_outside_loop,
                label="publisher builder isolation shell",
            )
        )

        inserted_parent_remount = builder_shell.replace(
            "unmount_if_mounted /sys\n",
            "unmount_if_mounted /sys\n"
            "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor\n",
            1,
        )
        self.assertNotEqual(
            publisher_shell_contract.reviewed_builder_isolation_sha256(
                inserted_parent_remount
            ),
            publisher_shell_contract.REVIEWED_BUILDER_ISOLATION_SHA256,
        )
        with self.assertRaisesRegex(
            ValueError,
            "raw identity differs from the reviewed security boundary",
        ):
            publisher_shell_contract.assert_reviewed_builder_isolation_shell_identity(
                inserted_parent_remount,
                label="publisher builder isolation shell",
            )
        self.assertTrue(
            publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                inserted_parent_remount,
                label="publisher builder isolation shell",
            )
        )

    def test_publisher_raw_identity_variants_are_rejected(self):
        current_run = named_step_run_script(
            self.text,
            "Build candidate in isolated namespace and stage public inputs",
        )
        current_shell = builder_isolation_shell_source(self.text)
        for label, changed in generate_publisher_raw_identity_mutations(self.text):
            with self.subTest(variant=label):
                changed_step = named_patch_release_step_block(
                    changed,
                    "Build candidate in isolated namespace and stage public inputs",
                )
                changed_run = named_step_run_script_from_block(changed_step)
                reference_run = safe_yaml_step_run_script(changed_step)
                self.assertEqual(
                    changed_run.encode("utf-8"),
                    reference_run.encode("utf-8"),
                )
                self.assertNotEqual(
                    changed_run.encode("utf-8"),
                    current_run.encode("utf-8"),
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "raw identity differs from the reviewed security boundary",
                ):
                    publisher_shell_contract.assert_reviewed_patch_release_run_script_identity(
                        changed_run,
                        label="publisher isolated candidate build run script",
                    )
                changed_shell = publisher_shell_contract.builder_isolation_shell_source(
                    changed_run,
                    label="publisher builder isolation shell",
                )
                if label not in {"extra-blank-before-heredoc", "run-strip-chomp"}:
                    self.assertNotEqual(
                        changed_shell.encode("utf-8"),
                        current_shell.encode("utf-8"),
                    )
                else:
                    self.assertEqual(
                        changed_shell.encode("utf-8"),
                        current_shell.encode("utf-8"),
                    )
                self.assertTrue(publisher_boundary_errors(changed))

    def test_builder_isolation_shell_identity_rejects_single_byte_mutations(self):
        builder_shell = builder_isolation_shell_source(self.text)
        publisher_shell_contract.assert_reviewed_builder_isolation_shell_identity(
            builder_shell,
            label="publisher builder isolation shell",
        )
        for index, character in enumerate(builder_shell):
            replacement = "X" if character != "X" else "Y"
            mutated = builder_shell[:index] + replacement + builder_shell[index + 1 :]
            with self.assertRaisesRegex(
                ValueError,
                "raw identity differs from the reviewed security boundary",
            ):
                publisher_shell_contract.assert_reviewed_builder_isolation_shell_identity(
                    mutated,
                    label="publisher builder isolation shell",
                )

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
        git_path_redirects = (
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_CEILING_DIRECTORIES",
            "GIT_COMMON_DIR",
            "GIT_DIR",
            "GIT_EXEC_PATH",
            "GIT_INDEX_FILE",
            "GIT_NAMESPACE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_REPLACE_REF_BASE",
            "GIT_WORK_TREE",
        )
        unset_match = re.search(
            r"(?ms)^unset (?P<variables>.*?)^ACTUAL_SHA=",
            script,
        )
        self.assertIsNotNone(unset_match)
        unset_variables = shlex.split(
            unset_match.group("variables").replace("\\\n", " ")
        )
        self.assertCountEqual(unset_variables, git_path_redirects)
        self.assertEqual(len(unset_variables), len(git_path_redirects))
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
                environment.update(
                    {name: "" for name in git_path_redirects}
                )
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
                        "host_uid": str(os.getuid()),
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

    def test_candidate_output_is_private_null_and_never_replayed(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        marker = "Uk9NX0xPR19MRUFLX01BUktFUl80ZjZmNmQ="
        with tempfile.TemporaryDirectory(
            prefix="candidate-output-boundary-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            command_files = {
                name: sandbox / name.lower()
                for name in (
                    "GITHUB_ENV",
                    "GITHUB_OUTPUT",
                    "GITHUB_PATH",
                    "GITHUB_STEP_SUMMARY",
                )
            }
            for path in command_files.values():
                path.write_bytes(b"")
            inherited_path = sandbox / "inherited-helper-pipe"
            inherited_fd = os.open(
                inherited_path,
                os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            os.set_inheritable(inherited_fd, True)

            def limits():
                resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                resource.setrlimit(resource.RLIMIT_FSIZE, (65536, 65536))
                resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))

            def run_adversary(script, expected_status=None):
                completed = subprocess.run(
                    [
                        "/usr/bin/env",
                        "-i",
                        "HOME=" + str(sandbox),
                        "PATH=/usr/bin:/bin",
                        "/bin/bash",
                        "--noprofile",
                        "--norc",
                        "-c",
                        script,
                        "--",
                        marker,
                        str(inherited_fd),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    cwd=sandbox,
                    close_fds=True,
                    preexec_fn=limits,
                    check=False,
                )
                visible = (
                    "candidate build status: success"
                    if completed.returncode == 0
                    else (
                        "candidate build failed: stage=isolated "
                        f"exit={completed.returncode}"
                    )
                )
                self.assertNotIn(marker, visible)
                self.assertFalse(
                    (sandbox / "private-candidate-output.log").exists()
                )
                if expected_status is not None:
                    self.assertEqual(completed.returncode, expected_status)
                return completed.returncode

            try:
                status = run_adversary(
                    r'''
set +e
marker="$1"
inherited_fd="$2"
printf '%s\n' "$marker"
printf '%s\n' "$marker" >&2
printf '%s\n' "$marker" > /proc/self/fd/1
printf '%s\n' "$marker" > /dev/stdout
printf '%s\n' "$marker" | /usr/bin/tee /dev/stdout > /dev/null
BASH_XTRACEFD=2
PS4="$marker"
set -x
:
set +x
for name in GITHUB_ENV GITHUB_OUTPUT GITHUB_PATH GITHUB_STEP_SUMMARY; do
  value="${!name-}"
  if [ -n "$value" ]; then
    printf '%s\n' "$marker" >> "$value"
  fi
done
if [ -e "/proc/self/fd/$inherited_fd" ]; then
  eval "printf '%s\n' \"\$marker\" >&$inherited_fd"
fi
(printf '%s\n' "$marker" >&2) &
wait
exit 23
''',
                    expected_status=23,
                )
                self.assertEqual(status, 23)
                huge_status = run_adversary(
                    r'''
exec /usr/bin/python3 -c \
  'import os,sys; marker=sys.argv[1].encode(); data=(marker+b"\n")*1000000; os.write(1,data); os.write(2,data)' \
  "$1"
''',
                    expected_status=0,
                )
                self.assertEqual(huge_status, 0)
            finally:
                os.close(inherited_fd)

            self.assertEqual(inherited_path.read_bytes(), b"")
            for path in command_files.values():
                self.assertEqual(path.read_bytes(), b"")

    def test_child_launcher_closes_bash_memfd_pipe_and_socket_fds(self):
        isolated_step = next(
            step
            for step in patch_release_step_blocks(self.text)
            if "Build candidate in isolated namespace and stage public inputs"
            in step
        )
        launcher_match = re.search(
            r"(?ms)<<'CANDIDATE_LAUNCHER'\n"
            r"(?P<body>.*?)^        CANDIDATE_LAUNCHER$",
            isolated_step,
        )
        self.assertIsNotNone(launcher_match)
        launcher_source = "\n".join(
            line[8:] if line.startswith("        ") else line
            for line in launcher_match.group("body").splitlines()
        )
        rootless_launcher, replacement_count = re.subn(
            r'(?ms)^candidate_argv = \[\n.*?^    "/bin/bash",',
            'candidate_argv = [\n    "/bin/bash",',
            launcher_source,
            count=1,
        )
        self.assertEqual(replacement_count, 1)
        rootless_launcher, replacement_count = re.subn(
            r"script_stat\.st_uid != 0",
            "script_stat.st_uid != os.getuid()",
            rootless_launcher,
            count=1,
        )
        self.assertEqual(replacement_count, 1)

        old_script = r'''
old_close() {
  for fd_path in /proc/$$/fd/*; do
    fd="${fd_path##*/}"
    case "$fd" in
      0|1|2) ;;
      ''|*[!0-9]*) exit 125 ;;
      *) eval "exec ${fd}>&-" 2>/dev/null || true ;;
    esac
  done
  for fd_path in /proc/$$/fd/*; do
    case "${fd_path##*/}" in
      0|1|2) ;;
      *) exit 125 ;;
    esac
  done
}
old_close
exit 37
'''
        old_memfd = os.memfd_create("old-bash-fd-closer", 0)
        try:
            os.write(old_memfd, old_script.encode("ascii"))
            os.lseek(old_memfd, 0, os.SEEK_SET)
            old_result = subprocess.run(
                ["/bin/bash", f"/proc/self/fd/{old_memfd}"],
                pass_fds=(old_memfd,),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            self.assertEqual(old_result.returncode, 125)
        finally:
            os.close(old_memfd)

        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="candidate-child-launcher-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            launcher = sandbox / "candidate-launcher.py"
            launcher.write_text(rootless_launcher, encoding="ascii")
            launcher.chmod(0o400)
            candidate = sandbox / "candidate-build.sh"
            candidate.write_text(
                "/usr/bin/python3 -I -S -c "
                "'import errno,fcntl; bad=[]; "
                "exec(\"for fd in range(3, 1024):\\n try: "
                "fcntl.fcntl(fd, fcntl.F_GETFD)\\n except OSError as error:"
                "\\n  if error.errno != errno.EBADF: raise\\n else: "
                "bad.append(fd)\"); raise SystemExit(125 if bad else 0)'\n"
                "exit 37\n",
                encoding="ascii",
            )
            candidate.chmod(0o555)

            pipe_read, pipe_write = os.pipe()
            inherited_memfd = os.memfd_create("candidate-inherited", 0)
            socket_left, socket_right = socket.socketpair()
            inherited_fds = (
                pipe_read,
                pipe_write,
                inherited_memfd,
                socket_left.fileno(),
                socket_right.fileno(),
            )
            for fd in inherited_fds:
                os.set_inheritable(fd, True)
            try:
                result = subprocess.run(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-S",
                        str(launcher),
                        str(os.getuid()),
                        str(os.getgid()),
                        str(candidate),
                        "/home/runner/work/_temp",
                    ],
                    pass_fds=inherited_fds,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                self.assertEqual(result.returncode, 37)
            finally:
                os.close(pipe_read)
                os.close(pipe_write)
                os.close(inherited_memfd)
                socket_left.close()
                socket_right.close()

    def test_supervisor_membership_view_allows_only_wrapper_pid(self):
        full_script = named_step_run_script(
            self.text,
            "Build candidate in isolated namespace and stage public inputs",
        )
        start = full_script.index(
            'cgroup_members="$(LC_ALL=C /usr/bin/sort -n'
        )
        end_marker = 'test "$cgroup_members" = "$$"'
        end = full_script.index(end_marker, start) + len(end_marker)
        membership_check = full_script[start:end]
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="supervisor-cgroup-view-",
            dir=artifact_root,
        ) as temporary:
            supervisor = Path(temporary) / "supervisor"
            supervisor.mkdir(mode=0o700)
            (supervisor / "cgroup.procs").write_text("", encoding="ascii")

            def run(extra_pid):
                setup = (
                    'supervisor_cgroup="$1"\n'
                    'printf \'%s\\n\' "$$" > '
                    '"$supervisor_cgroup/cgroup.procs"\n'
                )
                if extra_pid is not None:
                    setup += (
                        f"printf '%s\\n' {extra_pid} >> "
                        '"$supervisor_cgroup/cgroup.procs"\n'
                    )
                return subprocess.run(
                    ["/bin/bash", "-c", setup + membership_check, "--", str(supervisor)],
                    check=False,
                    capture_output=True,
                    text=True,
                )

            self.assertEqual(run(None).returncode, 0)
            self.assertNotEqual(run(999999).returncode, 0)

    def test_builder_cleanup_suppresses_utility_path_stderr(self):
        section = builder_cleanup_functions_source(self.text)
        section = section.replace(
            'builder_passwd_entry_exists() {\n'
            '  /usr/bin/getent passwd "$1" > /dev/null 2>&1\n'
            '}\n'
            'probe_builder_passwd_entry() {\n'
            '  local status\n'
            '  if builder_passwd_entry_exists "$1"; then\n'
            '    builder_passwd_probe_state=present\n'
            '    return 0\n'
            '  else\n'
            '    status="$?"\n'
            '    case "$status" in\n'
            '      2)\n'
            '        builder_passwd_probe_state=absent\n'
            '        return 0\n'
            '        ;;\n'
            '      *)\n'
            '        builder_passwd_probe_state=error\n'
            '        return "$status"\n'
            '        ;;\n'
            '    esac\n'
            '  fi\n'
            '}\n'
            'builder_passwd_entry_absent() {\n'
            '  probe_builder_passwd_entry "$1" || return "$?"\n'
            '  test "$builder_passwd_probe_state" = absent\n'
            '}\n',
            'builder_passwd_entry_exists() {\n'
            '  [ "$1" = "$builder_user" ]\n'
            '}\n'
            'probe_builder_passwd_entry() {\n'
            '  if builder_passwd_entry_exists "$1"; then\n'
            '    builder_passwd_probe_state=present\n'
            '  else\n'
            '    builder_passwd_probe_state=absent\n'
            '  fi\n'
            '  return 0\n'
            '}\n'
            'builder_passwd_entry_absent() {\n'
            '  [ "$1" != "$builder_user" ] || [ "$builder_user_created" = 0 ]\n'
            '}\n',
            1,
        )
        section = section.replace(
            '/usr/bin/sudo /usr/bin/rmdir -- "$builder_cgroup" \\\n'
            '                 > /dev/null 2>&1',
            'cleanup_rmdir "$builder_cgroup" > /dev/null 2>&1',
            1,
        )
        section = section.replace(
            '/usr/bin/sudo /usr/sbin/userdel "$builder_user" \\\n'
            '                > /dev/null 2>&1',
            'cleanup_userdel "$builder_user" > /dev/null 2>&1',
            1,
        )
        section = section.replace(
            '/usr/bin/sudo /bin/rm -rf -- "$BUILDER_ROOT" \\\n'
            '              > /dev/null 2>&1',
            'cleanup_rm_builder "$BUILDER_ROOT" > /dev/null 2>&1',
            1,
        )
        section = section.replace(
            '/bin/rm -rf -- "$PATCH_WHEELHOUSE" > /dev/null 2>&1',
            'cleanup_rm_wheelhouse "$PATCH_WHEELHOUSE" > /dev/null 2>&1',
            1,
        )
        for primary_status, expected_exit in ((37, 37), (0, 1)):
            with self.subTest(primary_status=primary_status):
                status_command = f"(exit {primary_status})" if primary_status else "true"
                harness = (
                    'builder_pgid=""\n'
                    'builder_supervisor_pid=""\n'
                    'builder_user="ci-patch-builder"\n'
                    'builder_uid="60000"\n'
                    'builder_cgroup="/home/runner/work/_temp/cgroups/builder"\n'
                    'builder_cgroup_owned=1\n'
                    'builder_root_owned=1\n'
                    'builder_user_created=1\n'
                    'wheelhouse_owned=1\n'
                    'BUILDER_ROOT="/home/runner/work/_temp/patch-builder"\n'
                    'PATCH_WHEELHOUSE="/home/runner/work/_temp/patch-wheelhouse"\n'
                    'cleanup_rmdir() { printf "%s\\n" "$1/path-leak" >&2; return 1; }\n'
                    'cleanup_userdel() { printf "/home/runner/%s\\n" "$1" >&2; return 1; }\n'
                    'cleanup_rm_builder() { printf "%s/path-leak\\n" "$1" >&2; return 1; }\n'
                    'cleanup_rm_wheelhouse() { printf "%s/path-leak\\n" "$1" >&2; return 1; }\n'
                    + section
                    + "set +e\n"
                    + status_command
                    + "\ncleanup_builder\n"
                )
                completed = subprocess.run(
                    ["/bin/bash", "-c", harness],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, expected_exit)
                self.assertIn(
                    "candidate build cleanup failed: process=0 cgroup=1 state=1 "
                    f"primary={primary_status}",
                    completed.stderr,
                )
                self.assertEqual(completed.stdout, "")
                self.assertNotIn("/home/runner/work/_temp", completed.stderr)
                self.assertNotIn("path-leak", completed.stderr)
                self.assertNotIn("ci-patch-builder", completed.stderr)

    def test_builder_cleanup_probe_helpers_suppress_output(self):
        section = builder_cleanup_functions_source(self.text)
        section = section.replace(
            "/usr/bin/ps -eo pgid=,pid= 2>/dev/null",
            "cleanup_ps -eo pgid=,pid= 2>/dev/null",
            1,
        )
        section = section.replace(
            "/usr/bin/ps -eo uid=,pid= 2>/dev/null",
            "cleanup_ps -eo uid=,pid= 2>/dev/null",
            1,
        )
        section = section.replace(
            "/usr/bin/awk -v pgid=\"$1\" '$1 == pgid {print $2}' 2>/dev/null",
            "cleanup_awk -v pgid=\"$1\" '$1 == pgid {print $2}' 2>/dev/null",
            1,
        )
        section = section.replace(
            "/usr/bin/awk -v uid=\"$1\" '$1 == uid {print $2}' 2>/dev/null",
            "cleanup_awk -v uid=\"$1\" '$1 == uid {print $2}' 2>/dev/null",
            1,
        )
        section = section.replace(
            '/bin/cat "$builder_cgroup/cgroup.procs" 2>/dev/null',
            'cleanup_cat "$builder_cgroup/cgroup.procs" 2>/dev/null',
            1,
        )
        section = section.replace(
            '/usr/bin/getent passwd "$1" > /dev/null 2>&1',
            'cleanup_getent passwd "$1" > /dev/null 2>&1',
            1,
        )
        harness = (
            'builder_pgid="12345"\n'
            'builder_supervisor_pid=""\n'
            'builder_user="ci-patch-builder"\n'
            'builder_uid="60000"\n'
            'builder_cgroup="/home/runner/work/_temp/cgroups/builder"\n'
            'builder_cgroup_owned=1\n'
            'builder_root_owned=0\n'
            'builder_user_created=1\n'
            'wheelhouse_owned=0\n'
            'BUILDER_ROOT="/home/runner/work/_temp/patch-builder"\n'
            'PATCH_WHEELHOUSE="/home/runner/work/_temp/patch-wheelhouse"\n'
            'cleanup_ps() { printf "/home/runner/work/_temp/ps-out\\n"; printf "/home/runner/work/_temp/ps-err\\n" >&2; return 1; }\n'
            'cleanup_awk() { printf "/home/runner/work/_temp/awk-out\\n"; printf "/home/runner/work/_temp/awk-err\\n" >&2; return 1; }\n'
            'cleanup_cat() { printf "/home/runner/work/_temp/cat-out\\n"; printf "/home/runner/work/_temp/cat-err\\n" >&2; return 1; }\n'
            'cleanup_getent() { printf "/home/runner/work/_temp/getent-out\\n"; printf "/home/runner/work/_temp/getent-err\\n" >&2; return 1; }\n'
            + section
            + 'shell_pgid="54321"\n'
            + "set +e\n"
            + "true\n"
            + "cleanup_builder\n"
        )
        completed = subprocess.run(
            ["/bin/bash", "-c", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, "")
        self.assertIn(
            "candidate build cleanup failed: process=1 cgroup=1 state=1 primary=0",
            completed.stderr,
        )
        self.assertNotIn("/home/runner/work/_temp", completed.stderr)
        self.assertNotIn("/home/runner/work/_temp", completed.stdout)

    def test_builder_passwd_entry_absent_handles_getent_statuses_under_bash_e(self):
        original_helpers = builder_passwd_helpers_source(self.text)
        helpers = original_helpers.replace(
            '/usr/bin/getent passwd "$1" > /dev/null 2>&1',
            'fake_getent "$1" > /dev/null 2>&1',
            1,
        )
        status_cases = (
            (2, 0, True),
            (0, 1, False),
            (1, 1, False),
            (125, 125, False),
            (143, 143, False),
        )
        for fake_status, expected_status, expect_sentinel in status_cases:
            with self.subTest(fake_status=fake_status):
                harness = (
                    "set -e\n"
                    "fake_getent() {\n"
                    f"  return {fake_status}\n"
                    "}\n"
                    + helpers
                    + 'builder_passwd_entry_absent "ci-patch-builder"\n'
                    + "printf 'SENTINEL\\n'\n"
                )
                completed = subprocess.run(
                    ["/bin/bash", "-c", harness],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, expected_status)
                self.assertEqual(completed.stderr, "")
                if expect_sentinel:
                    self.assertEqual(completed.stdout, "SENTINEL\n")
                else:
                    self.assertEqual(completed.stdout, "")

        broken_probe_body = (
            '  builder_passwd_entry_exists "$1"\n'
            '  status="$?"\n'
            '  case "$status" in\n'
            '    0) return 1 ;;\n'
            '    2) return 0 ;;\n'
            '    *) return "$status" ;;\n'
            '  esac\n'
        )
        broken_helpers = helpers.replace(
            '  if builder_passwd_entry_exists "$1"; then\n'
            '    builder_passwd_probe_state=present\n'
            '    return 0\n'
            '  else\n'
            '    status="$?"\n'
            '    case "$status" in\n'
            '      2)\n'
            '        builder_passwd_probe_state=absent\n'
            '        return 0\n'
            '        ;;\n'
            '      *)\n'
            '        builder_passwd_probe_state=error\n'
            '        return "$status"\n'
            '        ;;\n'
            '    esac\n'
            '  fi\n',
            broken_probe_body,
            1,
        )
        broken_real_helpers = original_helpers.replace(
            '  if builder_passwd_entry_exists "$1"; then\n'
            '    builder_passwd_probe_state=present\n'
            '    return 0\n'
            '  else\n'
            '    status="$?"\n'
            '    case "$status" in\n'
            '      2)\n'
            '        builder_passwd_probe_state=absent\n'
            '        return 0\n'
            '        ;;\n'
            '      *)\n'
            '        builder_passwd_probe_state=error\n'
            '        return "$status"\n'
            '        ;;\n'
            '    esac\n'
            '  fi\n',
            broken_probe_body,
            1,
        )
        broken_run_script = named_step_run_script(
            self.text,
            "Build candidate in isolated namespace and stage public inputs",
        ).replace(original_helpers, broken_real_helpers, 1)
        with self.assertRaisesRegex(
            ValueError,
            "raw identity differs from the reviewed security boundary",
        ):
            publisher_shell_contract.assert_reviewed_patch_release_run_script_identity(
                broken_run_script,
                label="publisher isolated candidate build run script",
            )
        broken_runtime = subprocess.run(
            [
                "/bin/bash",
                "-c",
                "set -e\n"
                "fake_getent() {\n"
                "  return 2\n"
                "}\n"
                + broken_helpers
                + 'builder_passwd_entry_absent "ci-patch-builder"\n'
                + "printf 'SENTINEL\\n'\n",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(broken_runtime.returncode, 0)
        self.assertEqual(broken_runtime.stdout, "")
        self.assertEqual(broken_runtime.stderr, "")

    def test_probe_builder_uid_occupancy_preserves_lookup_status_under_bash_e(self):
        original_helpers = builder_uid_occupancy_helpers_source(self.text)
        helpers = original_helpers
        status_cases = (
            ("", "0", 0, True, "free"),
            ("4242", "0", 0, True, "occupied"),
            ("", "1", 1, False, "error"),
            ("", "125", 125, False, "error"),
            ("", "143", 143, False, "error"),
        )
        for output, fake_status, expected_status, expect_sentinel, expected_state in status_cases:
            with self.subTest(fake_status=fake_status, expected_state=expected_state):
                harness = (
                    "set -e\n"
                    + helpers
                    + "builder_uid_pids() {\n"
                    + '  status="${FAKE_UID_PIDS_STATUS}"\n'
                    + '  if [ "$status" -ne 0 ]; then\n'
                    + '    return "$status"\n'
                    + "  fi\n"
                    + '  printf "%s" "${FAKE_UID_PIDS_OUTPUT-}"\n'
                    + "}\n"
                    + f'FAKE_UID_PIDS_STATUS="{fake_status}"\n'
                    + f'FAKE_UID_PIDS_OUTPUT={output!r}\n'
                    + 'probe_builder_uid_occupancy "60000"\n'
                    + "printf 'STATE:%s\\n' \"$builder_uid_occupancy_state\"\n"
                    + "printf 'SENTINEL\\n'\n"
                )
                completed = subprocess.run(
                    ["/bin/bash", "-c", harness],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, expected_status)
                self.assertEqual(completed.stderr, "")
                if expect_sentinel:
                    self.assertEqual(
                        completed.stdout,
                        f"STATE:{expected_state}\nSENTINEL\n",
                    )
                else:
                    self.assertEqual(completed.stdout, "")

        broken_probe_helpers = original_helpers.replace(
            '  else\n'
            '    status="$?"\n'
            '  fi\n'
            '  builder_uid_occupancy_state=error\n'
            '  return "$status"\n',
            '  fi\n'
            '  builder_uid_occupancy_state=error\n'
            '  status="$?"\n'
            '  return "$status"\n',
            1,
        )
        broken_real_script = named_step_run_script(
            self.text,
            "Build candidate in isolated namespace and stage public inputs",
        ).replace(original_helpers, broken_probe_helpers, 1)
        with self.assertRaisesRegex(
            ValueError,
            "raw identity differs from the reviewed security boundary",
        ):
            publisher_shell_contract.assert_reviewed_patch_release_run_script_identity(
                broken_real_script,
                label="publisher isolated candidate build run script",
            )
        broken_runtime = subprocess.run(
            [
                "/bin/bash",
                "-c",
                "set -e\n"
                + broken_probe_helpers
                + "builder_uid_pids() {\n"
                + '  status="${FAKE_UID_PIDS_STATUS}"\n'
                + '  if [ "$status" -ne 0 ]; then\n'
                + '    return "$status"\n'
                + "  fi\n"
                + '  printf "%s" "${FAKE_UID_PIDS_OUTPUT-}"\n'
                + "}\n"
                + 'FAKE_UID_PIDS_STATUS="125"\n'
                + 'FAKE_UID_PIDS_OUTPUT=""\n'
                + 'probe_builder_uid_occupancy "60000"\n'
                + "printf 'STATE:%s\\n' \"$builder_uid_occupancy_state\"\n"
                + "printf 'SENTINEL\\n'\n",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(broken_runtime.returncode, 0)
        self.assertEqual(
            broken_runtime.stdout,
            "STATE:error\nSENTINEL\n",
        )
        self.assertEqual(broken_runtime.stderr, "")

    def test_builder_user_selection_path_uses_tri_state_occupancy_under_bash_e(self):
        original_helpers = builder_uid_selection_helpers_source(self.text)
        helpers = original_helpers.replace(
            '/usr/bin/getent passwd "$1" > /dev/null 2>&1',
            'fake_getent "$1" > /dev/null 2>&1',
            1,
        )
        selection = builder_user_selection_source(self.text)
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="builder-passwd-selection-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            workspace = sandbox / "workspace"
            wheelhouse = sandbox / "wheelhouse"
            workspace.mkdir()
            wheelhouse.mkdir()

            def run_selection(
                *,
                getent_cases: tuple[str, ...],
                uid_pids_cases: tuple[str, ...] = (),
                selection_script: str = selection,
                builder_root_name: str = "builder-root",
            ) -> subprocess.CompletedProcess[str]:
                harness = (
                    "set -e\n"
                    "fake_getent() {\n"
                    "  case \"$1\" in\n"
                    + "".join(f"    {line}\n" for line in getent_cases)
                    + "    *) return 125 ;;\n"
                    + "  esac\n"
                    + "}\n"
                    + helpers
                    + "builder_uid_pids() {\n"
                    + "  case \"$1\" in\n"
                    + "".join(f"    {line}\n" for line in uid_pids_cases)
                    + "    *) return 0 ;;\n"
                    + "  esac\n"
                    + "}\n"
                    + f'builder_user="ci-patch-builder"\n'
                    + f'BUILDER_ROOT="{sandbox / builder_root_name}"\n'
                    + f'PATCH_WHEELHOUSE="{wheelhouse}"\n'
                    + f'GITHUB_WORKSPACE_PATH="{workspace}"\n'
                    + selection_script
                    + "\nprintf 'CREATION_SENTINEL:%s\\n' \"$builder_uid\"\n"
                )
                return subprocess.run(
                    ["/bin/bash", "-c", harness],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )

            for error_status in (1, 125, 143):
                with self.subTest(name_lookup_error=error_status):
                    completed = run_selection(
                        getent_cases=(
                            f'ci-patch-builder) return {error_status} ;;',
                            "60000|59999) return 2 ;;",
                        ),
                        builder_root_name=f"name-error-{error_status}",
                    )
                    self.assertEqual(completed.returncode, error_status)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(completed.stderr, "")

                with self.subTest(numeric_lookup_error=error_status):
                    completed = run_selection(
                        getent_cases=(
                            "ci-patch-builder) return 2 ;;",
                            f'60000) return {error_status} ;;',
                            "59999) return 2 ;;",
                        ),
                        builder_root_name=f"numeric-error-{error_status}",
                    )
                    self.assertEqual(completed.returncode, error_status)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(completed.stderr, "")

            with self.subTest(passwd_occupied_continues=True):
                completed = run_selection(
                    getent_cases=(
                        "ci-patch-builder) return 2 ;;",
                        "60000) return 0 ;;",
                        "59999) return 2 ;;",
                    ),
                    builder_root_name="passwd-occupied",
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, "CREATION_SENTINEL:59999\n")
                self.assertEqual(completed.stderr, "")

            with self.subTest(uid_occupied_continues=True):
                completed = run_selection(
                    getent_cases=(
                        "ci-patch-builder) return 2 ;;",
                        "60000|59999) return 2 ;;",
                    ),
                    uid_pids_cases=(
                        '60000) printf "4242\\n"; return 0 ;;',
                        "59999) return 0 ;;",
                    ),
                    builder_root_name="uid-occupied",
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, "CREATION_SENTINEL:59999\n")
                self.assertEqual(completed.stderr, "")

            with self.subTest(both_absent_selects=True):
                completed = run_selection(
                    getent_cases=(
                        "ci-patch-builder|60000) return 2 ;;",
                    ),
                    builder_root_name="both-absent",
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, "CREATION_SENTINEL:60000\n")
                self.assertEqual(completed.stderr, "")

            with self.subTest(exhaustion_rejects=True):
                short_selection = selection.replace("builder_uid=60000", "builder_uid=50001", 1)
                completed = run_selection(
                    getent_cases=(
                        "ci-patch-builder) return 2 ;;",
                        "50001|50000) return 0 ;;",
                    ),
                    selection_script=short_selection,
                    builder_root_name="exhaustion",
                )
                self.assertEqual(completed.returncode, 1)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(completed.stderr, "")

            broken_selection = selection.replace(
                '  probe_builder_passwd_entry "$builder_uid"\n'
                '  if [ "$builder_passwd_probe_state" = absent ]; then\n'
                '    probe_builder_uid_occupancy "$builder_uid"\n'
                '    if [ "$builder_uid_occupancy_state" = free ]; then\n'
                '      break\n'
                '    fi\n'
                '  fi\n',
                '  if builder_passwd_entry_absent "$builder_uid" && \\\n'
                '     builder_uid_is_empty "$builder_uid"; then\n'
                '    break\n'
                '  fi\n',
                1,
            )
            self.assertNotEqual(broken_selection, selection)
            broken_run_script = named_step_run_script(
                self.text,
                "Build candidate in isolated namespace and stage public inputs",
            ).replace(selection, broken_selection, 1)
            with self.assertRaisesRegex(
                ValueError,
                "raw identity differs from the reviewed security boundary",
            ):
                publisher_shell_contract.assert_reviewed_patch_release_run_script_identity(
                    broken_run_script,
                    label="publisher isolated candidate build run script",
                )
            with self.subTest(broken_and_mutation_masks_numeric_error=True):
                broken = run_selection(
                    getent_cases=(
                        "ci-patch-builder) return 2 ;;",
                        "60000) return 1 ;;",
                        "59999) return 2 ;;",
                    ),
                    selection_script=broken_selection,
                    builder_root_name="broken-and-mutation",
                )
                self.assertEqual(broken.returncode, 0, broken.stderr)
                self.assertEqual(broken.stdout, "CREATION_SENTINEL:59999\n")
                self.assertEqual(broken.stderr, "")

    def test_launch_validation_failure_kills_live_child_without_waiting(self):
        section = builder_cleanup_functions_source(self.text)
        launch = launch_validation_source(self.text)
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="launch-validation-cleanup-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            pid_file = sandbox / "child.pid"
            harness = (
                "set -euo pipefail\n"
                + section
                + 'builder_uid="60000"\n'
                + 'builder_user="ci-patch-builder"\n'
                + 'builder_cgroup=""\n'
                + 'builder_cgroup_owned=0\n'
                + 'builder_pgid=""\n'
                + 'builder_supervisor_pid=""\n'
                + 'builder_root_owned=0\n'
                + 'builder_user_created=0\n'
                + 'wheelhouse_owned=0\n'
                + f'BUILDER_ROOT="{sandbox / "builder-root"}"\n'
                + f'PATCH_WHEELHOUSE="{sandbox / "wheelhouse"}"\n'
                + "trap cleanup_builder EXIT\n"
                + f'/bin/sleep 60 < /dev/null > /dev/null 2>&1 & printf "%s\\n" "$!" > "{pid_file}"\n'
                + launch
            )
            monotonic_start = time.monotonic()
            completed = subprocess.run(
                ["/bin/bash", "-c", harness],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            duration = time.monotonic() - monotonic_start
            child_pid = int(pid_file.read_text(encoding="ascii").strip())
            self.assertEqual(completed.returncode, 125)
            self.assertRegex(
                completed.stderr,
                r"candidate build failed: stage=launch detail=pgid-(?:mismatch|parent) exit=125",
            )
            self.assertIn(
                "candidate build cleanup failed: process=1 cgroup=0 state=0 primary=125",
                completed.stderr,
            )
            self.assertLess(duration, 10.0)
            with self.assertRaises(ProcessLookupError):
                os.kill(child_pid, 0)

    def test_private_base_cleanup_suppresses_utility_path_stderr(self):
        function_section = private_base_cleanup_function_source(self.text)
        function_section = function_section.replace(
            '/bin/chmod u+w -- "$BASE_IMAGE" > /dev/null 2>&1 || cleanup_failed=1',
            'cleanup_chmod "$BASE_IMAGE" > /dev/null 2>&1 || cleanup_failed=1',
            1,
        )
        function_section = function_section.replace(
            '/bin/rm -f -- "$BASE_IMAGE" > /dev/null 2>&1 || cleanup_failed=1',
            'cleanup_rm "$BASE_IMAGE" > /dev/null 2>&1 || cleanup_failed=1',
            1,
        )
        function_section = function_section.replace(
            '/usr/bin/rmdir -- "$private_dir" > /dev/null 2>&1 || cleanup_failed=1',
            'cleanup_rmdir "$private_dir" > /dev/null 2>&1 || cleanup_failed=1',
            1,
        )
        for primary_status, expected_exit in ((23, 23), (0, 1)):
            with self.subTest(primary_status=primary_status, path="create-step"):
                status_command = f"(exit {primary_status})" if primary_status else "true"
                harness = (
                    'RUNNER_TEMP="/home/runner/work/_temp"\n'
                    'BASE_IMAGE="/home/runner/work/_temp/patch-private.ABCDEFGHIJ/base.gba"\n'
                    'cleanup_chmod() { printf "%s\\n" "$1/path-leak" >&2; return 1; }\n'
                    'cleanup_rm() { printf "%s/path-leak\\n" "$1" >&2; return 1; }\n'
                    'cleanup_rmdir() { printf "%s/path-leak\\n" "$1" >&2; return 1; }\n'
                    + function_section
                    + "set +e\n"
                    + status_command
                    + "\ncleanup_private_base\n"
                )
                completed = subprocess.run(
                    ["/bin/bash", "-c", harness],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, expected_exit)
                self.assertEqual(completed.stderr, "")

        cleanup_script = named_step_run_script(
            self.text,
            "Cleanup and verify private base",
        )
        cleanup_script = cleanup_script.replace(
            '/bin/chmod u+w -- "$BASE_IMAGE" > /dev/null 2>&1',
            'cleanup_chmod "$BASE_IMAGE" > /dev/null 2>&1',
            1,
        )
        cleanup_script = cleanup_script.replace(
            '/bin/rm -f -- "$BASE_IMAGE" > /dev/null 2>&1',
            'cleanup_rm "$BASE_IMAGE" > /dev/null 2>&1',
            1,
        )
        cleanup_script = cleanup_script.replace(
            '/usr/bin/rmdir -- "$private_dir" > /dev/null 2>&1',
            'cleanup_rmdir "$private_dir" > /dev/null 2>&1',
            1,
        )
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="private-cleanup-step-",
            dir=artifact_root,
        ) as temporary, self.subTest(path="cleanup-step"):
            sandbox = Path(temporary)
            harness = (
                "set -euo pipefail\n"
                f'RUNNER_TEMP="{sandbox}"\n'
                'BASE_IMAGE="$RUNNER_TEMP/patch-private.ABCDEFGHIJ/base.gba"\n'
                'private_dir="${BASE_IMAGE%/base.gba}"\n'
                'cleanup_chmod() { printf "%s\\n" "$1/path-leak" >&2; return 1; }\n'
                'cleanup_rm() { printf "%s/path-leak\\n" "$1" >&2; return 1; }\n'
                'cleanup_rmdir() { printf "%s/path-leak\\n" "$1" >&2; return 1; }\n'
                "mkdir -p \"$private_dir\"\n"
                ": > \"$BASE_IMAGE\"\n"
                + cleanup_script
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", harness],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 1)
            self.assertEqual(completed.stderr, "")

    def test_private_download_cleanup_suppresses_utility_path_stderr(self):
        function_section = download_cleanup_function_source(self.text)
        function_section = function_section.replace(
            '/bin/chmod -R u+w -- "$private_dir" > /dev/null 2>&1 || true',
            'cleanup_chmod "$private_dir" > /dev/null 2>&1 || true',
            1,
        )
        function_section = function_section.replace(
            '/bin/rm -f -- "$base_image" > /dev/null 2>&1',
            'cleanup_rm "$base_image" > /dev/null 2>&1',
            1,
        )
        function_section = function_section.replace(
            '/usr/bin/rmdir -- "$private_dir" > /dev/null 2>&1 || true',
            'cleanup_rmdir "$private_dir" > /dev/null 2>&1 || true',
            1,
        )
        harness = (
            'private_dir="/home/runner/work/_temp/patch-private.ABCDEFGHIJ"\n'
            'base_image="$private_dir/base.gba"\n'
            'cleanup_chmod() { printf "%s/path-leak\\n" "$1" >&2; return 1; }\n'
            'cleanup_rm() { printf "%s/path-leak\\n" "$1" >&2; return 1; }\n'
            'cleanup_rmdir() { printf "%s/path-leak\\n" "$1" >&2; return 1; }\n'
            + function_section
            + "set +e\n"
            + "(exit 19)\n"
            + "cleanup_download\n"
        )
        completed = subprocess.run(
            ["/bin/bash", "-c", harness],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 19)
        self.assertEqual(completed.stderr, "")

    def test_pre_summary_cleanup_utilities_suppress_output(self):
        verify_step = named_step_run_script(
            self.text,
            "Verify exact candidate and stage trusted producer",
        )
        self.assertIn(
            '/bin/rm -rf -- "$PATCH_RUNTIME_ROOT" "$PATCH_TOOL_ROOT" > /dev/null 2>&1',
            verify_step,
        )
        dependency_step = named_step_run_script(
            self.text,
            "Install trusted isolated-build dependencies",
        )
        self.assertIn(
            '/bin/rm -rf -- "$PATCH_WHEELHOUSE" > /dev/null 2>&1',
            dependency_step,
        )
        download_step = named_step_run_script(
            self.text,
            "Download private base image",
        )
        self.assertIn(
            '/bin/chmod -R u+w -- "$private_dir" > /dev/null 2>&1 || true',
            download_step,
        )
        self.assertIn(
            '/bin/rm -f -- "$base_image" > /dev/null 2>&1',
            download_step,
        )
        self.assertIn(
            '/usr/bin/rmdir -- "$private_dir" > /dev/null 2>&1 || true',
            download_step,
        )

    def test_patch_release_docs_publish_no_internal_rom_artifact(self):
        text = PATCH_RELEASE_CASE.read_text(encoding="utf-8")
        compact = " ".join(text.split())
        self.assertIn(
            "target ROM remains only in the publisher-local isolated handoff "
            "and private\nstaging",
            text,
        )
        self.assertIn(
            "Actions uploads only BPS/manifest/README with 30-day retention",
            text,
        )
        self.assertIn("there\nis no internal or final ROM artifact", text)
        self.assertNotIn("one-day internal Actions artifact", text)
        self.assertIn(
            "decodes recursive `/dev` mount targets from structured `findmnt\n"
            "--json --submounts --output TARGET /dev` output",
            text,
        )
        self.assertIn(
            "unmounts exact descendant paths deepest-first, removes the temp files",
            text,
        )
        self.assertIn(
            "root-owned mode-`0700` `/mnt/supervisor` parent denies candidate read",
            text,
        )
        self.assertIn(
            "raw escaped or whitespace-delimited mount-target transport",
            compact,
        )
        self.assertIn(
            "paths outside `/dev`",
            text,
        )
        self.assertIn(
            "unsafe transport files are rejected",
            compact,
        )
        self.assertIn(
            "structured\n`findmnt --json --list --uniq --output TARGET,OPTIONS -R /` output, "
            "writes\nchecked NUL-framed mount target/option records",
            text,
        )
        self.assertIn(
            "Only `/dev/shm`, `/mnt/handoff`, `/mnt/home`, `/mnt/source`, `/mnt/tmp`, and",
            text,
        )
        self.assertIn(
            "`/tmp` may carry an exact `rw` option token",
            text,
        )
        self.assertIn(
            "util-linux documents `--uniq` as \"effectively skipping over-mounted\nmount points\"",
            text,
        )
        self.assertIn(
            "control-character targets,",
            text,
        )
        self.assertIn(
            "malformed option-token grammar",
            text,
        )

    def test_patch_release_overview_docs_require_structured_mount_records(self):
        text = PATCH_RELEASE_OVERVIEW.read_text(encoding="utf-8")
        compact = " ".join(text.split())
        self.assertIn(
            "consumes only decoded structured JSON target records through checked NUL-delimited "
            "transport",
            compact,
        )
        self.assertIn(
            "raw escaped or whitespace-delimited mount text can never be "
            "mistaken for an "
            "unapproved path",
            compact,
        )
        self.assertNotIn(
            "consumes raw `findmnt` targets",
            compact,
        )

    def test_patch_release_docs_use_runtime_diagnostic_stage_enum(self):
        overview = " ".join(PATCH_RELEASE_OVERVIEW.read_text(encoding="utf-8").split())
        case = " ".join(PATCH_RELEASE_CASE.read_text(encoding="utf-8").split())
        registry = json.loads(PATCH_RELEASE_REGISTRY.read_text(encoding="utf-8"))
        entry = next(item for item in registry["cases"] if item["id"] == "TC-CI-PATCH-049-002")
        expected_result = " ".join(entry["expected_result"].split())
        for text in (overview, case, expected_result):
            self.assertIn("post-spawn", text)
            self.assertIn("launch", text)
            self.assertIn("isolated", text)
            self.assertIn("cleanup", text)
            self.assertIn("pre-spawn", text)
            self.assertIn("post-child handoff validation", text)
            self.assertIn("only stage text", text)
            self.assertNotIn("isolated-build", text)

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
        builder_shell = builder_isolation_shell_source(self.text)
        publisher_shell_contract.assert_reviewed_builder_isolation_shell_identity(
            builder_shell,
            label="publisher builder isolation shell",
        )
        publisher_shell_contract.validate_patch_release_parser_heredocs(
            builder_shell,
            label="publisher builder isolation shell",
        )
        assert_patch_release_python_c_snippets_compile(self, self.text)

        for label, source in (
            ("dev-mount-target-parser", raw_dev_mount_target_parser_source(self.text)),
            (
                "writable-mount-record-parser",
                raw_writable_mount_record_parser_source(self.text),
            ),
        ):
            with self.subTest(language="embedded-python-heredoc", parser=label):
                ast.parse(source)

    def test_each_patch_release_python_c_snippet_is_checked(self):
        snippets = patch_release_python_c_snippets(self.text)
        self.assertEqual(len(snippets), 4)
        assert_patch_release_python_c_snippets_compile(self, self.text)
        for index, (step_index, command_index, _source) in enumerate(snippets):
            with self.subTest(step=step_index, command=command_index):
                mutated = list(snippets)
                mutated[index] = (step_index, command_index, "if (")
                with self.assertRaises(SyntaxError):
                    for _, _, source in mutated:
                        compile(source, "<patch-release-workflow>", "exec")

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

        launcher_match = re.search(
            r"(?ms)<<'CANDIDATE_LAUNCHER'\n"
            r"(?P<body>.*?)^        CANDIDATE_LAUNCHER$",
            isolated_step,
        )
        self.assertIsNotNone(launcher_match)
        launcher_source = "\n".join(
            line[8:] if line.startswith("        ") else line
            for line in launcher_match.group("body").splitlines()
        )
        compile(
            launcher_source,
            "<candidate-launcher>",
            "exec",
        )


if __name__ == "__main__":
    unittest.main()
