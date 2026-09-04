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
import signal
import socket
import subprocess
import tempfile
import textwrap
import time
import threading
import unittest
from collections import Counter
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import publisher_shell_contract
from scripts.modernize import patch_release


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
PATCH_RELEASE_REGISTRY = ROOT / "docs" / "test-cases" / "registry.json"
MERGED_MASTER_771 = "771d38c5a531f2d63b269220727b02aa820cc3d4"
FAILING_MASTER_8D81 = "8d81c30b298ef6265ba9c5335c3ca8c8f94e60e6"
FAILING_MASTER_0456 = "0456f181ad53645a7bc2b677abab05978ab9f35c"
REVIEWED_RUNTIME_3_6EE = "6ee4766e6204d01f76b334edd2085e965fac5a66"
FAILING_MASTER_5779 = "5779c38e245d9a14f063338b53851a97bb92d0c0"
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


_REFERENCE_LITERAL_RUN_HEADER_RE = re.compile(
    r"^ {6}run:[ \t]*(?P<style>\|(?:[-+])?)(?:[ \t]*(?:#.*)?)?(?:\r?\n|\Z)$"
)


def _reference_split_line(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\n"
    return line, ""


def _reference_indent_width(text: str) -> int:
    index = 0
    while index < len(text) and text[index] == " ":
        index += 1
    if index < len(text) and text[index] == "\t":
        raise AssertionError("reference literal parser rejects tab indentation")
    return index


def reference_literal_run_step_script(step_block: str) -> str:
    """Independent constrained oracle for one literal run block.

    This parser deliberately supports only the workflow's reviewed surface:
    one direct `run: |`, `run: |-`, or `run: |+` field inside a single step
    block. It preserves blank lines, indentation-derived content, and YAML's
    chomping semantics, but rejects folded or advisory YAML forms.
    """

    lines = step_block.splitlines(keepends=True)
    headers = [
        (index, match)
        for index, line in enumerate(lines)
        if (match := _REFERENCE_LITERAL_RUN_HEADER_RE.match(line))
    ]
    if len(headers) != 1:
        raise AssertionError("reference literal parser requires one direct literal run")
    header_index, header_match = headers[0]
    style = header_match.group("style")
    if style not in {"|", "|-", "|+"}:
        raise AssertionError("reference literal parser rejects complex YAML styles")

    content_lines = lines[header_index + 1 :]
    leading_blank_indent = 0
    content_indent: int | None = None
    for line in content_lines:
        raw_line, _line_break = _reference_split_line(line)
        indent = _reference_indent_width(raw_line)
        if raw_line[indent:] == "":
            leading_blank_indent = max(leading_blank_indent, indent)
            continue
        content_indent = max(leading_blank_indent, indent)
        break
    if content_indent is None:
        content_indent = leading_blank_indent

    parts: list[str] = []
    for line in content_lines:
        raw_line, line_break = _reference_split_line(line)
        indent = _reference_indent_width(raw_line)
        body = raw_line[indent:]
        if body == "":
            parts.append(raw_line[content_indent:] if indent >= content_indent else "")
        else:
            if indent < content_indent:
                raise AssertionError("reference literal parser found early dedent")
            parts.append(raw_line[content_indent:])
        if line_break:
            parts.append("\n")

    script = "".join(parts)
    if style == "|-":
        return script.rstrip("\n")
    if style == "|+":
        return script
    if not script:
        return ""
    return script.rstrip("\n") + "\n"


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


def workflow_has_raw_builder_cgroup_membership_read(workflow: str) -> bool:
    builder_shell = builder_isolation_shell_source(workflow)
    return publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
        builder_shell,
        label="publisher builder raw cgroup membership read",
        require_production_helpers=True,
    )


def generate_raw_builder_cgroup_membership_mutations(workflow: str):
    marker = "        isolated_stage=export\n"
    cgroup_init_marker = '        cgroup_path="$1"\n'
    checker_marker = (
        '        /usr/bin/python3 -I -S - "$$" <<\'PY\'\n'
    )
    mutations = [
        '        /bin/cat "$1/cgroup.procs" > /dev/null\n',
        '        mapfile -t leaked < "${1}/cgroup.procs"\n',
        "        readarray -t leaked < $1/cgroup.procs\n",
        '        /usr/bin/env /bin/cat "$1/cgroup.procs" > /dev/null\n',
        "        /bin/bash -c "
        "'/bin/cat \"$1/cgroup.procs\" > /dev/null'\n",
        '        raw_root="$1"\n'
        '        /bin/cat "$raw_root/cgroup.procs" > /dev/null\n',
        '        raw_members="$1/cgroup"\n'
        "        raw_members+=.procs\n"
        '        /bin/cat "$raw_members" > /dev/null\n',
        '        raw[0]="$1"\n'
        '        mapfile -t leaked < "${raw[0]}/cgroup.procs"\n',
        '        raw[key]="${1}"\n'
        '        readarray -t leaked < "${raw[key]}/cgroup.procs"\n',
        "        indirect_position=1\n"
        '        /bin/cat "${!indirect_position}/cgroup.procs" > /dev/null\n',
        '        /bin/cat "$unknown_root/cgroup.procs" > /dev/null\n',
        "        unknown_root=/safe\n"
        "        unknown_leaf=cgroup.procs\n"
        '        /bin/cat "$unknown_root/$unknown_leaf" > /dev/null\n',
        '        /bin/cat "$cgroup_path"/cgroup{.,_}procs > /dev/null\n',
        '        /bin/cat "$cgroup_path"/cgroup.proc? > /dev/null\n',
        '        /bin/cat "$cgroup_path"/cgroup.proc* > /dev/null\n',
        '        /bin/cat "$cgroup_path"/cgroup.proc[s] > /dev/null\n',
        "        shopt -s extglob\n"
        '        /bin/cat "$cgroup_path"/cgroup.@(procs|events) > /dev/null\n',
        '        /bin/cat "$cgroup_path/$(printf cgroup.procs)" > /dev/null\n',
        '        /bin/cat "$cgroup_path"/<(printf cgroup.procs) > /dev/null\n',
        '        /bin/cat "$cgroup_path/cgroup.proc$((1 + 1))" > /dev/null\n',
        '        /bin/cat "$cgroup_path"/~ > /dev/null\n',
        '        mapfile -t leaked < "$cgroup_path/cgroup.procs"\n',
        '        /bin/cat -- "${cgroup_path}/cgroup.procs" > /dev/null\n',
        '        /usr/bin/sort -n "$cgroup_path"/cgroup.procs > /dev/null\n',
        '        readarray -t leaked < "$cgroup_path/cgroup.procs"\n',
        '        /usr/bin/env /bin/cat "$cgroup_path/cgroup.procs" > /dev/null\n',
        '        command /bin/cat "$cgroup_path/cgroup.procs" > /dev/null\n',
        "        /bin/bash -c "
        "'/bin/cat \"$cgroup_path/cgroup.procs\" > /dev/null'\n",
        '        /bin/printf \'%s\\n\' "$$" > "$cgroup_path/cgroup.procs"\n',
        '        raw_root="$cgroup_path"\n'
        '        mapfile -t leaked < "$raw_root/cgroup.procs"\n',
        '        raw_members="$cgroup_path/cgroup.procs"\n'
        '        /bin/cat "$raw_members" > /dev/null\n',
        '        raw_root="${cgroup_path}"\n'
        "        raw_leaf=cgroup.procs\n"
        '        /bin/cat "$raw_root/$raw_leaf" > /dev/null\n',
        '        mapfile\t-t\tleaked\t<\t"${cgroup_path}/cgroup.procs"\n',
        '        raw_root="$cgroup_path"\n'
        "        raw_leaf=cgroup\n"
        "        raw_leaf+=.procs\n"
        '        /bin/cat "$raw_root/$raw_leaf" > /dev/null\n',
        '        raw_members="$cgroup_path/cgroup"\n'
        "        raw_members+=.procs\n"
        '        /bin/cat "$raw_members" > /dev/null\n',
        "        indirect_name=cgroup_path\n"
        '        /bin/cat "${!indirect_name}/cgroup.procs" > /dev/null\n',
        '        raw[0]="$cgroup_path"\n'
        '        /bin/cat "${raw[0]}/cgroup.procs" > /dev/null\n',
        '        raw[key]="${cgroup_path}"\n'
        '        mapfile -t leaked < "${raw[key]}/cgroup.procs"\n',
        '        raw[0]+="$cgroup_path"\n'
        '        readarray -t leaked < "${raw[0]}/cgroup.procs"\n',
        '        raw=("$cgroup_path")\n'
        '        /bin/cat ${raw[0]}/cgroup.procs > /dev/null\n',
        '        declare -a raw=("$cgroup_path")\n'
        '        /bin/cat "${raw[@]}/cgroup.procs" > /dev/null\n',
        '        declare -A raw=([key]="$cgroup_path")\n'
        '        /bin/cat "${raw[key]}/cgroup.procs" > /dev/null\n',
        "        declare -a raw\n"
        '        raw[0]="$cgroup_path"\n'
        '        /bin/cat "${raw[0]}/cgroup.procs" > /dev/null\n',
        "        declare -A raw\n"
        '        raw[key]="$cgroup_path"\n'
        '        /bin/cat "${raw[key]}/cgroup.procs" > /dev/null\n',
        "        index=0\n"
        '        raw[$index]="$cgroup_path"\n'
        '        /bin/cat "${raw[$index]}/cgroup.procs" > /dev/null\n',
        "        index=0\n"
        '        raw[0]="$cgroup_path"\n'
        '        /bin/cat "${raw[${index}]}/cgroup.procs" > /dev/null\n',
        '        raw[0]="$cgroup_path"\n'
        '        /bin/cat "${!raw[@]}/cgroup.procs" > /dev/null\n',
        "        raw[0]=cgroup\n"
        "        raw[0]+=.procs\n"
        '        /bin/cat "$cgroup_path/${raw[0]}" > /dev/null\n',
        "        raw[0]=/safe\n"
        '        /bin/cat "${raw[0]}/cgroup.procs" > /dev/null\n',
        "        raw[key]=/safe\n"
        '        readarray -t leaked < "${raw[key]}/cgroup.procs"\n',
    ]
    parameter_expressions = (
        "${cgroup_path:-/fallback}/cgroup.procs",
        "${cgroup_path:=/fallback}/cgroup.procs",
        "${cgroup_path:?required}/cgroup.procs",
        "${cgroup_path:+/alternate}/cgroup.procs",
        "${cgroup_path#prefix}/cgroup.procs",
        "${cgroup_path##prefix}/cgroup.procs",
        "${cgroup_path%suffix}/cgroup.procs",
        "${cgroup_path%%suffix}/cgroup.procs",
        "${cgroup_path:0}/cgroup.procs",
        "${cgroup_path^}/cgroup.procs",
        "${cgroup_path^^}/cgroup.procs",
        "${cgroup_path,}/cgroup.procs",
        "${cgroup_path,,}/cgroup.procs",
        "${cgroup_path@Q}/cgroup.procs",
        "${cgroup_path@P}/cgroup.procs",
        "${#cgroup_path}/cgroup.procs",
        "${!cgroup_path}/cgroup.procs",
    )
    positional_parameter_expressions = (
        "${1:-/fallback}/cgroup.procs",
        "${1:=/fallback}/cgroup.procs",
        "${1:?required}/cgroup.procs",
        "${1:+/alternate}/cgroup.procs",
        "${1#prefix}/cgroup.procs",
        "${1##prefix}/cgroup.procs",
        "${1%suffix}/cgroup.procs",
        "${1%%suffix}/cgroup.procs",
        "${1:0}/cgroup.procs",
        "${1^}/cgroup.procs",
        "${1^^}/cgroup.procs",
        "${1,}/cgroup.procs",
        "${1,,}/cgroup.procs",
        "${1@Q}/cgroup.procs",
        "${1@P}/cgroup.procs",
        "${#1}/cgroup.procs",
        "${!1}/cgroup.procs",
    )
    mutations.extend(
        f'        /bin/cat "{expression}" > /dev/null\n'
        for expression in parameter_expressions
    )
    mutations.extend(
        f"        /bin/cat {expression} > /dev/null\n"
        for expression in parameter_expressions
    )
    mutations.extend(
        f'        /bin/cat "{expression}" > /dev/null\n'
        for expression in positional_parameter_expressions
    )
    mutations.extend(
        f"        /bin/cat {expression} > /dev/null\n"
        for expression in positional_parameter_expressions
    )
    mutations.extend(
        (
            '        raw_root="$cgroup_path"\n'
            f'        /bin/cat "${{raw_root{operator}}}/cgroup.procs" '
            "> /dev/null\n"
        )
        for operator in (
            ":-/fallback",
            ":=/fallback",
            ":?required",
            ":+/alternate",
            "#prefix",
            "##prefix",
            "%suffix",
            "%%suffix",
            ":0",
            "^",
            "^^",
            ",",
            ",,",
            "@Q",
            "@P",
        )
    )
    supervisor_reassignments = (
        "        supervisor_cgroup=/mnt/home\n",
        "        alternate_supervisor=/mnt/home\n"
        '        supervisor_cgroup="$alternate_supervisor"\n',
        '        supervisor_cgroup="$cgroup_path"\n',
        '        supervisor_cgroup="${supervisor_cgroup%/}"\n',
        "        supervisor_cgroup+=/other\n",
        "        supervisor_cgroup[0]=/mnt/supervisor/cgroup\n",
        "        supervisor_cgroup[key]=/mnt/supervisor/cgroup\n",
        "        declare -a supervisor_cgroup=(/mnt/supervisor/cgroup)\n",
        "        declare -A supervisor_cgroup=([key]=/mnt/supervisor/cgroup)\n",
        "        unset supervisor_cgroup\n",
        "        command unset supervisor_cgroup\n",
        "        command -- unset supervisor_cgroup\n",
        "        command -p unset supervisor_cgroup\n",
        "        command -p -- unset supervisor_cgroup\n",
        "        builtin unset supervisor_cgroup\n",
        "        wrapper=command\n"
        "        mutation=unset\n"
        "        mutation_target=supervisor_cgroup\n"
        '        "$wrapper" -- "$mutation" "$mutation_target"\n',
        "        mutation=unset\n"
        "        mutation_target=supervisor_cgroup\n"
        '        builtin "$mutation" "$mutation_target"\n',
        "        command -x unset supervisor_cgroup\n",
        "        command -px unset supervisor_cgroup\n",
        "        command -pZ unset supervisor_cgroup\n",
        "        command -vX unset supervisor_cgroup\n",
        "        command -pvx unset supervisor_cgroup\n",
        "        command -p-v unset supervisor_cgroup\n",
        "        declare supervisor_cgroup=/mnt/home\n",
        "        typeset supervisor_cgroup=/mnt/home\n",
        "        local supervisor_cgroup=/mnt/home\n",
        "        export supervisor_cgroup=/mnt/home\n",
        "        readonly supervisor_cgroup=/mnt/home\n",
        "        printf -v supervisor_cgroup %s /mnt/home\n",
        "        read supervisor_cgroup < /dev/null\n",
        "        mapfile -t supervisor_cgroup < /dev/null\n",
        "        eval 'supervisor_cgroup=/mnt/home'\n",
        "        source /dev/null\n",
        "        . /dev/null\n",
        "        mutate_supervisor() { supervisor_cgroup=/mnt/home; }\n"
        "        mutate_supervisor\n",
        "        wrapper[0]=command\n"
        '        "${wrapper[0]}" unset supervisor_cgroup\n',
        "        wrapper[key]=builtin\n"
        '        "${wrapper[key]}" unset supervisor_cgroup\n',
        "        declare -a wrapper=(command)\n"
        '        "${wrapper[0]}" unset supervisor_cgroup\n',
        "        declare -A wrapper=([key]=builtin)\n"
        '        "${wrapper[key]}" unset supervisor_cgroup\n',
        "        index=0\n"
        "        wrapper[0]=command\n"
        '        "${wrapper[$index]}" unset supervisor_cgroup\n',
        "        wrapper[0]=command\n"
        '        "${!wrapper[@]}" unset supervisor_cgroup\n',
        "        flags[0]=-pp\n"
        '        command "${flags[0]}" unset supervisor_cgroup\n',
        "        flags[key]=-pp\n"
        '        command "${flags[key]}" unset supervisor_cgroup\n',
        "        flags[0]=-pv\n"
        '        command "${flags[0]}" supervisor_cgroup\n',
        "        flags[key]=-pV\n"
        '        command "${flags[key]}" supervisor_cgroup\n',
        "        mutator[0]=printf\n"
        "        flags[0]=-v\n"
        '        "${mutator[0]}" "${flags[0]}" '
        "supervisor_cgroup /mnt/home\n",
        "        mutator[key]=unset\n"
        "        target[key]=supervisor_cgroup\n"
        '        "${mutator[key]}" "${target[key]}"\n',
        "        wrapper[0]=builtin\n"
        "        mutator[0]=unset\n"
        '        "${wrapper[0]}" "${mutator[0]}" supervisor_cgroup\n',
        '        target="$(printf supervisor_cgroup)"\n'
        '        unset "$target"\n',
        "        target=`printf supervisor_cgroup`\n"
        '        unset "$target"\n',
        '        target="$(printf supervisor_cgroup)"\n'
        '        printf -v "$target" %s /mnt/home\n',
        '        target="$(printf supervisor_cgroup)"\n'
        '        read "$target" < /dev/null\n',
        '        target="$(printf supervisor_cgroup)"\n'
        '        readarray -t "$target" < /dev/null\n',
        '        target="$(printf supervisor_cgroup)"\n'
        '        mapfile -t "$target" < /dev/null\n',
        '        target="$(printf supervisor_cgroup)"\n'
        '        declare "$target=/mnt/home"\n',
        '        target="$(printf supervisor_cgroup)"\n'
        '        typeset "$target=/mnt/home"\n',
        '        target="$(printf supervisor_cgroup)"\n'
        '        export "$target=/mnt/home"\n',
        '        target="$(printf supervisor_cgroup)"\n'
        '        readonly "$target=/mnt/home"\n',
        '        wrapper="$(printf command)"\n'
        '        "$wrapper" unset supervisor_cgroup\n',
        '        wrapper=`printf builtin`\n'
        '        "$wrapper" unset supervisor_cgroup\n',
        '        flag="$(printf -- -p)"\n'
        '        command "$flag" unset supervisor_cgroup\n',
        '        mutator="$(printf unset)"\n'
        '        "$mutator" supervisor_cgroup\n',
        "        target_name=supervisor_cgroup\n"
        '        target="${target_name@P}"\n'
        '        unset "$target"\n',
        "        target=supervisor\n"
        '        target+="$(printf _cgroup)"\n'
        '        unset "$target"\n',
        "        target=<(printf supervisor_cgroup)\n"
        '        unset "$target"\n',
        "        target='$((1 + 1))'\n"
        '        printf "%s\\n" "$target" > /dev/null\n'
        '        unset "$(printf supervisor_cgroup)"\n',
        "        target=supervisor_cgrou?\n"
        '        unset "$target"\n',
        "        target=supervisor_{cgroup,other}\n"
        '        unset "$target"\n',
        "        target=~\n"
        '        unset "$target"\n',
        "        declare -n target=supervisor_cgroup\n"
        "        unset target\n",
        "        declare -n target=$1\n"
        "        unset target\n",
        "        typeset -n target=supervisor_cgroup\n"
        "        target=/mnt/home\n",
        "        local -n target=supervisor_cgroup\n"
        "        unset target\n",
        "        export -n target=supervisor_cgroup\n",
        "        readonly -n target=supervisor_cgroup\n",
        "        declare -gn target=supervisor_cgroup\n",
        "        declare -ng target=supervisor_cgroup\n",
        "        declare +n target=supervisor_cgroup\n",
        "        declare +xn target=supervisor_cgroup\n",
        "        declare -nn target=supervisor_cgroup\n",
        "        declare -n target\n",
        "        declare -n target='supervisor_cgroup[0]'\n",
        "        command declare -n target=supervisor_cgroup\n",
        "        command -- typeset -n target=supervisor_cgroup\n",
        "        builtin declare -n target=supervisor_cgroup\n",
        "        declaration=declare\n"
        "        option=-n\n"
        '        "$declaration" "$option" target=supervisor_cgroup\n',
        '        option="$(printf -- -n)"\n'
        '        declare "$option" target=supervisor_cgroup\n',
        "        option[0]=-n\n"
        '        declare "${option[0]}" target=supervisor_cgroup\n',
        "        eval 'declare -n target=supervisor_cgroup'\n",
        "        create_nameref() { "
        "declare -n target=supervisor_cgroup; }\n"
        "        create_nameref\n",
        "        shopt -s expand_aliases\n"
        "        alias rewrite='printf -v supervisor_cgroup %s /mnt/home'\n"
        "        rewrite\n",
        "        shopt -u expand_aliases\n",
        "        shopt -q expand_aliases\n",
        "        shopt -su expand_aliases\n",
        "        shopt -sq expand_aliases\n",
        "        command shopt -s expand_aliases\n",
        "        command -- shopt -q expand_aliases\n",
        "        builtin shopt -s expand_aliases\n",
        "        dispatch=shopt\n"
        '        "$dispatch" -s expand_aliases\n',
        '        dispatch="$(printf shopt)"\n'
        '        "$dispatch" -s expand_aliases\n',
        "        shopt_option=-s\n"
        '        shopt "$shopt_option" expand_aliases\n',
        '        shopt_option="$(printf -- -s)"\n'
        '        shopt "$shopt_option" expand_aliases\n',
        "        shopt_options[0]=-s\n"
        '        shopt "${shopt_options[0]}" expand_aliases\n',
        "        alias rewrite='unset supervisor_cgroup'\n",
        "        command alias rewrite='unset supervisor_cgroup'\n",
        "        builtin alias rewrite='unset supervisor_cgroup'\n",
        "        dispatch=alias\n"
        '        "$dispatch" rewrite="unset supervisor_cgroup"\n',
        '        dispatch="$(printf alias)"\n'
        '        "$dispatch" rewrite="unset supervisor_cgroup"\n',
        "        unalias rewrite\n",
        "        command unalias rewrite\n",
        "        builtin unalias rewrite\n",
        "        enable -n printf\n",
        "        command enable -n printf\n",
        "        builtin enable -n printf\n",
        "        enable -f /dev/null replacement\n",
        "        hash -p /bin/false printf\n",
        "        command hash -r\n",
        "        builtin hash -r\n",
        "        set -h\n",
        "        set +h\n",
        "        set -o hashall\n",
        "        set -o posix\n",
        "        BASHOPTS=expand_aliases\n",
        "        SHELLOPTS=hashall\n",
        "        BASH_ENV=/dev/null\n",
        "        ENV=/dev/null\n",
        "        PATH=/mnt/home\n",
        "        /usr/bin/env BASHOPTS=expand_aliases /bin/bash -c true\n",
        "        eval 'shopt -s expand_aliases'\n",
        "        target=PATH\n"
        '        printf -v "$target" %s /mnt/home\n',
        "        target=BASH_ENV\n"
        '        unset "$target"\n',
        "        target=ENV\n"
        '        read "$target" < /dev/null\n',
        "        target=BASHOPTS\n"
        '        readarray -t "$target" < /dev/null\n',
        "        target=SHELLOPTS\n"
        '        mapfile -t "$target" < /dev/null\n',
        "        first=PATH\n"
        '        second="$first"\n'
        '        printf -v "$second" %s /mnt/home\n',
        "        target=PA\n"
        "        target+=TH\n"
        '        unset "$target"\n',
        "        targets[0]=PATH\n"
        '        printf -v "${targets[0]}" %s /mnt/home\n',
        "        declare -A targets=([key]=BASH_ENV)\n"
        '        unset "${targets[key]}"\n',
        '        target="$(printf PATH)"\n'
        '        printf -v "$target" %s /mnt/home\n',
        "        target_name=PATH\n"
        '        target="${target_name@P}"\n'
        '        unset "$target"\n',
        "        target_name=PATH\n"
        "        target_ref=target_name\n"
        '        unset "${!target_ref}"\n',
        "        target=PATH\n"
        '        declare "$target=/mnt/home"\n',
        "        target=BASH_ENV\n"
        '        typeset "$target=/dev/null"\n',
        "        target=ENV\n"
        '        local "$target=/dev/null"\n',
        "        target=PATH\n"
        '        export "$target=/mnt/home"\n',
        "        target=SHELLOPTS\n"
        '        readonly "$target=hashall"\n',
        "        option=posix\n"
        '        set -o "$option"\n',
        "        first_option=posix\n"
        '        second_option="$first_option"\n'
        '        set -o "$second_option"\n',
        "        option=hash\n"
        "        option+=all\n"
        '        set +o "$option"\n',
        "        options[0]=posix\n"
        '        set -o "${options[0]}"\n',
        "        declare -A options=([key]=hashall)\n"
        '        set +o "${options[key]}"\n',
        '        option="$(printf posix)"\n'
        '        set -o "$option"\n',
        "        option_name=posix\n"
        '        option="${option_name@P}"\n'
        '        set +o "$option"\n',
        "        option_name=posix\n"
        "        option_ref=option_name\n"
        '        set +o "${!option_ref}"\n',
        "        flag=-o\n"
        "        option=posix\n"
        '        set "$flag" "$option"\n',
        "        set -eh\n",
        "        set +eh\n",
        "        set -oposix\n",
        "        set +ohashall\n",
        "        command set -o posix\n",
        "        builtin set +o hashall\n",
        "        raw_root=/safe\n"
        '        printf -v raw_root %s "$cgroup_path"\n'
        '        /bin/cat "$raw_root"/cgroup.proc?\n',
        "        raw_root=/safe\n"
        "        target=raw_root\n"
        '        command printf -v "$target" %s "$cgroup_path"\n'
        '        /bin/cat "$raw_root"/cgroup.proc?\n',
        "        raw_root=/safe\n"
        '        printf -v raw_root %q "$cgroup_path"\n'
        '        /bin/cat "$raw_root"/cgroup.proc?\n',
        "        raw_root=/safe\n"
        '        read raw_root <<< "$cgroup_path"\n'
        '        /bin/cat "$raw_root"/cgroup.proc?\n',
        "        raw_root=(/safe)\n"
        '        mapfile -t raw_root <<< "$cgroup_path"\n'
        '        /bin/cat "${raw_root[0]}"/cgroup.proc?\n',
        "        raw_root=(/safe)\n"
        '        readarray -t raw_root <<< "$cgroup_path"\n'
        '        /bin/cat "${raw_root[0]}"/cgroup.proc?\n',
        "        raw_root=/safe\n"
        '        declare raw_root="$cgroup_path"\n'
        '        /bin/cat "$raw_root"/cgroup.proc?\n',
        "        raw_root=/safe\n"
        '        raw_root="$cgroup_path"\n'
        '        /bin/cat "$raw_root"/cgroup.proc?\n',
        "        raw_root=/safe\n"
        '        target="$(printf raw_root)"\n'
        '        printf -v "$target" %s "$cgroup_path"\n',
    )
    declaration_builtins = (
        "declare",
        "typeset",
        "export",
        "readonly",
        "local",
    )
    mutations.extend(
        (
            "        raw_root=/safe\n"
            "        target=raw_root\n"
            f'        {builtin} "$target=$cgroup_path"\n'
            '        /bin/cat "$raw_root"/cgroup.proc?\n'
        )
        for builtin in declaration_builtins
    )
    mutations.extend(
        (
            "        raw_root=/safe\n"
            '        target="$(printf raw_root)"\n'
            f'        {builtin} "$target=/safe"\n'
        )
        for builtin in declaration_builtins
    )
    mutations.extend(
        (
            "        raw_root=/safe\n"
            "        target=raw_root\n"
            f'        {builtin} "$target=$(printf %s "$cgroup_path")"\n'
            '        /bin/cat "$raw_root"/cgroup.proc?\n'
        )
        for builtin in declaration_builtins
    )
    mutations.extend(
        (
            "        helper() {\n"
            '          /bin/cat "$1/$2$3" > /dev/null\n'
            "        }\n"
            '        helper "$cgroup_path" cgroup .procs\n',
            "        function helper {\n"
            '          /bin/cat "$1/$2$3" > /dev/null\n'
            "        }\n"
            '        helper "$cgroup_path" cgroup .procs\n',
            "        outer_helper() {\n"
            "          nested_helper() {\n"
            '            /bin/cat "$1/$2$3" > /dev/null\n'
            "          }\n"
            '          nested_helper "$cgroup_path" cgroup .procs\n'
            "        }\n"
            "        outer_helper\n",
            "        helper() {\n"
            '          /bin/cat "$1/$2$3" > /dev/null\n'
            "        }\n"
            "        helper_alias=helper\n"
            '        "$helper_alias" "$cgroup_path" cgroup .procs\n',
            "        helper() {\n"
            '          /bin/cat "$@" > /dev/null\n'
            "        }\n"
            '        helper "$cgroup_path/cgroup.procs"\n',
            "        raw_root=/safe\n"
            "        helper() {\n"
            '          /bin/cat "$raw_root"/cgroup.proc? > /dev/null\n'
            "        }\n"
            '        raw_root="$cgroup_path"\n'
            "        helper\n",
            "        raw_root=/safe\n"
            "        helper() {\n"
            '          /bin/cat "$raw_root"/cgroup.proc? > /dev/null\n'
            "        }\n"
            '        printf -v raw_root %s "$cgroup_path"\n'
            "        helper\n",
        )
    )
    function_shadow_mutations = [
        (
            "        mapfile() {\n"
            '          cgroup_members=("$$")\n'
            "        }\n"
        ),
        (
            "        function mapfile {\n"
            '          cgroup_members=("$$")\n'
            "        }\n"
        ),
        (
            "        function mapfile() {\n"
            '          cgroup_members=("$$")\n'
            "        }\n"
        ),
        (
            "        test()\n"
            "        {\n"
            "          true\n"
            "        }\n"
        ),
        (
            "        function test()\n"
            "        {\n"
            "          true\n"
            "        }\n"
        ),
        (
            "        shadow_name=mapfile\n"
            '        function "$shadow_name" {\n'
            '          cgroup_members=("$$")\n'
            "        }\n"
        ),
        (
            "        shadow_name=mapfile\n"
            "        function $shadow_name {\n"
            '          cgroup_members=("$$")\n'
            "        }\n"
        ),
    ]
    function_shadow_mutations.extend(
        f"        {name}() {{\n          true\n        }}\n"
        for name in (
            "test",
            "read",
            "stat",
            "printf",
            "readarray",
            "local",
            "unset",
            "builtin",
            "command",
        )
    )
    pre_checker_mutations = (
        '        /bin/cat "${cgroup_path:-${HOME}}/cgroup.procs" '
        "> /dev/null\n",
        '        raw_root="$cgroup_path"\n'
        '        /bin/cat "${raw_root:-${HOME}}/cgroup.procs" '
        "> /dev/null\n",
        "        safe_suffix=safe\n"
        "        value='literal-'\"$cgroup_path\"\"-$safe_suffix\"\n"
        '        printf "%s\\n" "$value" > /dev/null\n',
        '        trap \'/bin/cat "$cgroup_path/cgroup.procs"\' DEBUG\n',
        "        trap true RETURN\n",
        "        trap true EXIT\n",
        "        raw_root=/safe\n"
        '        LC_ALL=C printf -v raw_root %s "$cgroup_path"\n'
        '        /bin/cat "$raw_root"/cgroup.proc? > /dev/null\n',
        "        raw_root=/safe\n"
        "        if true; then\n"
        '          printf -v raw_root %s "$cgroup_path"\n'
        "        fi\n"
        '        /bin/cat "$raw_root"/cgroup.proc? > /dev/null\n',
        "        raw_root=/safe\n"
        "        if false; then\n"
        '          printf -v raw_root %s "$cgroup_path"\n'
        "        fi\n"
        '        /bin/cat "$raw_root"/cgroup.proc? > /dev/null\n',
        "        raw_root=/safe\n"
        "        true && "
        'printf -v raw_root %s "$cgroup_path"\n'
        '        /bin/cat "$raw_root"/cgroup.proc? > /dev/null\n',
        "        raw_root=/safe\n"
        "        ! "
        'printf -v raw_root %s "$cgroup_path"\n'
        '        /bin/cat "$raw_root"/cgroup.proc? > /dev/null\n',
        "        command trap true DEBUG\n",
        "        trap_command=trap\n"
        '        "$trap_command" true DEBUG\n',
        '        /usr/bin/find "$cgroup_path" -name "cgroup.p*" '
        "-exec /bin/cat {} \\;\n",
        "        mapfile -C callback -c 1 -t callback_data < /dev/null\n",
        "        readarray -Ccallback -c1 -t callback_data < /dev/null\n",
        '        callback_option="$(printf -- -C)"\n'
        '        mapfile "$callback_option" callback -t callback_data '
        "< /dev/null\n",
        '        cgroup_members=("$$")\n',
        '        cgroup_members[0]="$$"\n',
        '        declare -a cgroup_members=("$$")\n',
        '        printf -v cgroup_members %s "$$"\n',
        "        read cgroup_members < /dev/null\n",
        "        mapfile -t cgroup_members < /dev/null\n",
        "        forge_members() {\n"
        '          cgroup_members=("$$")\n'
        "        }\n"
        "        forge_members\n",
        '        trap \'cgroup_members=("$$")\' DEBUG\n',
        "        printf '%s\\n' $'<<FAKE # ) &&' > /dev/null\n"
        '        /bin/cat "$cgroup_path/cgroup.procs" > /dev/null\n',
        '        printf \'%s\\n\' $"$cgroup_path" > /dev/null\n',
        "        arithmetic_value=$((1 << 2))\n"
        '        /bin/cat "$cgroup_path/cgroup.procs" > /dev/null\n',
        "        (( arithmetic_value = 1 << 3 ))\n"
        "        cgroup_path=/tmp/fake\n",
        "        arithmetic_value=$[1<<2]\n"
        "        cgroup_path=/tmp/fake\n",
    )
    cgroup_path_mutations = (
        "        cgroup_path=/tmp/fake\n",
        "        cgroup_path[0]=/tmp/fake\n",
        "        declare -a cgroup_path=(/tmp/fake)\n",
        "        declare cgroup_path=/tmp/fake\n",
        "        typeset cgroup_path=/tmp/fake\n",
        "        local cgroup_path=/tmp/fake\n",
        "        export cgroup_path=/tmp/fake\n",
        "        readonly cgroup_path=/tmp/fake\n",
        "        printf -v cgroup_path %s /tmp/fake\n",
        "        read cgroup_path < /dev/null\n",
        "        mapfile -t cgroup_path < /dev/null\n",
        "        readarray -t cgroup_path < /dev/null\n",
        '        read cgroup_path<<<"/tmp/fake"\n',
        '        read cgroup_path<"/dev/null"\n',
        '        read cgroup_path 0<<<"/tmp/fake"\n',
        "        target=cgroup_path\n"
        '        read "$target"<<<"/tmp/fake"\n',
        '        mapfile -t cgroup_path<<<"/tmp/fake"\n',
        '        readarray -t cgroup_path<<<"/tmp/fake"\n',
        "        read cgroup_path<<CGROUP_PATH_VALUE\n"
        "        /tmp/fake\n"
        "        CGROUP_PATH_VALUE\n",
        "        unset cgroup_path\n",
        "        target=cgroup_path\n"
        '        declare "$target=/tmp/fake"\n',
        '        target="$(printf cgroup_path)"\n'
        '        printf -v "$target" %s /tmp/fake\n',
        "        mutate_cgroup_path() {\n"
        "          cgroup_path=/tmp/fake\n"
        "        }\n"
        "        mutate_cgroup_path\n",
        "        for cgroup_path in /tmp/fake; do\n"
        "          true\n"
        "        done\n",
        "        for supervisor_cgroup in /tmp/fake; do\n"
        "          true\n"
        "        done\n",
        "        for PATH in /tmp/fake; do\n"
        "          true\n"
        "        done\n",
        '        raw_root="$cgroup_path"\n'
        "        for raw_root in /tmp/fake; do\n"
        "          true\n"
        "        done\n",
        "        select cgroup_path in /tmp/fake; do\n"
        "          break\n"
        "        done < /dev/null\n",
        "        loop_target=cgroup_path\n"
        '        for "$loop_target" in /tmp/fake; do\n'
        "          true\n"
        "        done\n",
        '        cgroup_path="$1"\n',
    )
    for index, mutation in enumerate(supervisor_reassignments):
        changed = workflow.replace(marker, mutation + marker, 1)
        if changed == workflow:
            raise AssertionError(
                "supervisor reassignment mutation marker differs"
            )
        yield f"supervisor-reassignment-{index}", changed
    for index, mutation in enumerate(mutations):
        changed = workflow.replace(marker, marker + mutation, 1)
        if changed == workflow:
            raise AssertionError("raw cgroup membership mutation marker differs")
        yield f"raw-membership-{index}", changed
    for index, mutation in enumerate(function_shadow_mutations):
        changed = workflow.replace(marker, mutation + marker, 1)
        if changed == workflow:
            raise AssertionError(
                "function shadow mutation marker differs"
            )
        yield f"function-shadow-{index}", changed
    for index, mutation in enumerate(pre_checker_mutations):
        changed = workflow.replace(
            checker_marker,
            mutation + checker_marker,
            1,
        )
        if changed == workflow:
            raise AssertionError(
                "pre-checker mutation marker differs"
            )
        yield f"pre-checker-{index}", changed
    for index, mutation in enumerate(cgroup_path_mutations):
        changed = workflow.replace(
            cgroup_init_marker,
            cgroup_init_marker + mutation,
            1,
        )
        if changed == workflow:
            raise AssertionError(
                "cgroup path mutation marker differs"
            )
        yield f"cgroup-path-{index}", changed
    removed_init = workflow.replace(cgroup_init_marker, "", 1)
    if removed_init == workflow:
        raise AssertionError("cgroup path removal marker differs")
    yield "cgroup-path-missing", removed_init


def generate_safe_declaration_alias_controls(workflow: str):
    marker = "        isolated_stage=export\n"
    for builtin in (
        "declare",
        "typeset",
        "export",
        "readonly",
        "local",
    ):
        control = (
            '        raw_root="$cgroup_path"\n'
            "        target=raw_root\n"
            f'        {builtin} "$target=/safe"\n'
            '        test "$raw_root" = /safe\n'
        )
        changed = workflow.replace(marker, marker + control, 1)
        if changed == workflow:
            raise AssertionError(
                "safe declaration alias control marker differs"
            )
        yield builtin, changed


def generate_quote_context_controls(workflow: str):
    marker = "        isolated_stage=export\n"
    controls = (
        "        printf '%s\\n' '$cgroup_path' > /dev/null\n",
        "        printf '%s\\n' \\$cgroup_path > /dev/null\n",
        "        safe_suffix=safe\n"
        "        value='literal-$cgroup_path-'\"$safe_suffix\"\n"
        '        test "$value" = \'literal-$cgroup_path-safe\'\n',
    )
    for label, control in zip(
        ("single-quoted", "escaped", "mixed"),
        controls,
    ):
        changed = workflow.replace(marker, marker + control, 1)
        if changed == workflow:
            raise AssertionError(
                f"{label} quote control marker differs"
            )
        yield label, changed


def generate_ansi_arithmetic_controls(workflow: str):
    marker = "        isolated_stage=export\n"
    controls = (
        (
            "ansi-token",
            "        ansi_value=$'abc\\'#notcomment'\n"
            "        test \"$ansi_value\" = \"abc'#notcomment\"\n",
        ),
        (
            "ansi-heredoc",
            "        cat <<$'ANSI_EOF' > /dev/null\n"
            "        harmless body\n"
            "        ANSI_EOF\n",
        ),
        (
            "locale-token",
            '        locale_value=$"literal#value"\n'
            '        test "$locale_value" = "literal#value"\n',
        ),
        (
            "arithmetic-expansion",
            "        arithmetic_value=$((1 << 2))\n"
            '        test "$arithmetic_value" -eq 4\n',
        ),
        (
            "arithmetic-command",
            "        (( arithmetic_value = 1 << 3 ))\n"
            '        test "$arithmetic_value" -eq 8\n',
        ),
        (
            "arithmetic-command-comment",
            "        (( arithmetic_value = 1 ))# inert comment\n"
            '        test "$arithmetic_value" -eq 1\n',
        ),
        (
            "arithmetic-expansion-suffix",
            "        arithmetic_value=$((1))#suffix\n"
            '        test "$arithmetic_value" = "1#suffix"\n',
        ),
        (
            "legacy-arithmetic",
            "        arithmetic_value=$[1<<2]\n"
            '        test "$arithmetic_value" -eq 4\n',
        ),
    )
    for label, control in controls:
        changed = workflow.replace(marker, marker + control, 1)
        if changed == workflow:
            raise AssertionError(
                f"{label} control marker differs"
            )
        yield label, changed


def generate_helper_inventory_mutations(workflow: str):
    marker = "        isolated_stage=export\n"
    unmount_helper = (
        "        unmount_if_mounted() {\n"
        '          if /usr/bin/mountpoint -q "$1"; then\n'
        '            /usr/bin/umount --recursive "$1"\n'
        "          fi\n"
        "        }\n"
    )
    mutations = {
        "added": workflow.replace(
            marker,
            "        added_helper() {\n"
            "          true\n"
            "        }\n"
            + marker,
            1,
        ),
        "duplicate": workflow.replace(
            marker,
            "        unmount_if_mounted() {\n"
            "          true\n"
            "        }\n"
            + marker,
            1,
        ),
        "modified": workflow.replace(
            '        unmount_if_mounted() {\n'
            '          if /usr/bin/mountpoint -q "$1"; then\n',
            '        unmount_if_mounted() {\n'
            "          true\n"
            '          if /usr/bin/mountpoint -q "$1"; then\n',
            1,
        ),
        "background-topology": workflow.replace(
            '          fi\n'
            '        }\n'
            '        unmount_if_mounted /home/runner\n',
            '          fi &\n'
            '        }\n'
            '        unmount_if_mounted /home/runner\n',
            1,
        ),
        "pipeline-topology": workflow.replace(
            '            /usr/bin/umount --recursive "$1"\n',
            '            /usr/bin/umount --recursive "$1" | /bin/true\n',
            1,
        ),
        "list-topology": workflow.replace(
            '            /usr/bin/umount --recursive "$1"\n',
            '            /usr/bin/umount --recursive "$1" && true\n',
            1,
        ),
        "conditional-declaration": workflow.replace(
            unmount_helper,
            "        if false; then\n"
            + unmount_helper
            + "        fi\n",
            1,
        ),
        "subshell-declaration": workflow.replace(
            unmount_helper,
            "        (\n"
            + unmount_helper
            + "        )\n",
            1,
        ),
        "call-before-definition": workflow.replace(
            unmount_helper
            + "        unmount_if_mounted /home/runner\n",
            "        unmount_if_mounted /home/runner\n"
            + unmount_helper,
            1,
        ),
    }
    for label, changed in mutations.items():
        if changed == workflow:
            raise AssertionError(
                f"{label} helper inventory mutation marker differs"
            )
        yield label, changed


def reordered_helper_inventory_control(workflow: str) -> str:
    first = (
        "        read_checked_supervisor_transport_file() {\n"
        '          local path="$1"\n'
        '          local size_limit="$2"\n'
        "          local signature\n"
        '          signature="$(checked_supervisor_transport_signature "$path" "$size_limit")" || return 125\n'
        "          mapfile -d '' -t checked_supervisor_transport_output \\\n"
        '            < "$path" || return 125\n'
        '          test "$(checked_supervisor_transport_signature "$path" "$size_limit")" = \\\n'
        '            "$signature" || return 125\n'
        "        }\n"
    )
    second = (
        "        remove_supervisor_transport_file() {\n"
        '          local path="$1"\n'
        '          /bin/rm -f -- "$path" || return 125\n'
        '          test ! -e "$path" || return 125\n'
        "        }\n"
    )
    adjacent = first + second
    changed = workflow.replace(adjacent, second + first, 1)
    if changed == workflow:
        raise AssertionError(
            "helper inventory reorder marker differs"
        )
    return changed


def reformatted_membership_checker_control(workflow: str) -> str:
    changed = workflow.replace(
        "        members = {int(record, 10) for record in records}\n",
        "        members={int(record,10) for record in records}\n",
        1,
    )
    if changed == workflow:
        raise AssertionError(
            "membership checker formatting marker differs"
        )
    return changed


def generate_membership_checker_ast_mutations(workflow: str):
    replacements = {
        "path": (
            '        MEMBERSHIP_PATH = "/mnt/supervisor/cgroup/cgroup.procs"\n',
            '        MEMBERSHIP_PATH = "/mnt/home/cgroup.procs"\n',
        ),
        "member-count": (
            "        if len(records) != 2 or any(\n",
            "        if len(records) != 3 or any(\n",
        ),
        "expected-set": (
            "            or members != {expected_pid, checker_pid}\n",
            "            or members != {expected_pid}\n",
        ),
    }
    for label, (before, after) in replacements.items():
        changed = workflow.replace(before, after, 1)
        if changed == workflow:
            raise AssertionError(
                f"{label} membership checker mutation marker differs"
            )
        yield label, changed


def generate_membership_checker_control_flow_mutations(workflow: str):
    checker = '        /usr/bin/python3 -I -S - "$$" <<\'PY\'\n'
    checker_end = "        PY\n        isolated_stage=export\n"
    wrappers = {
        "if-false": (
            "        if false; then\n",
            "        fi\n",
        ),
        "if-dynamic": (
            '        if test "$builder_uid" -gt 0; then\n',
            "        fi\n",
        ),
        "for-loop": (
            "        for checker_attempt in one; do\n",
            "        done\n",
        ),
        "while-false": (
            "        while false; do\n",
            "        done\n",
        ),
    }
    for label, (prefix, suffix) in wrappers.items():
        changed = workflow.replace(checker, prefix + checker, 1)
        changed = changed.replace(
            checker_end,
            "        PY\n" + suffix + "        isolated_stage=export\n",
            1,
        )
        if changed == workflow:
            raise AssertionError(
                f"{label} membership control mutation marker differs"
            )
        yield label, changed


def generate_membership_checker_nested_execution_mutations(workflow: str):
    checker = '        /usr/bin/python3 -I -S - "$$" <<\'PY\'\n'
    checker_end = "        PY\n        isolated_stage=export\n"
    wrappers = {
        "command-substitution": (
            "        checker_result=$(\n",
            "        ) || true\n",
        ),
        "command-substitution-quoted-paren": (
            "        checker_result=$(\n"
            "          printf '%s' \")\" > /dev/null\n",
            "        ) || true\n",
        ),
        "nested-command-substitution": (
            "        checker_result=$(\n"
            "          nested_result=$(\n"
            "            printf '%s' \")\" > /dev/null\n"
            "          )\n",
            "        ) || true\n",
        ),
        "command-substitution-escaped-paren": (
            "        checker_result=$(\n"
            "          printf '%s' \\) > /dev/null\n",
            "        ) || true\n",
        ),
        "backtick-substitution": (
            "        checker_result=`\n",
            "        ` || true\n",
        ),
        "subshell": (
            "        (\n",
            "        ) || true\n",
        ),
        "input-process-substitution": (
            "        checker_result=<(\n",
            "        )\n",
        ),
        "output-process-substitution": (
            "        checker_result=>(\n",
            "        )\n",
        ),
    }
    for label, (prefix, suffix) in wrappers.items():
        changed = workflow.replace(checker, prefix + checker, 1)
        changed = changed.replace(
            checker_end,
            "        PY\n" + suffix + "        isolated_stage=export\n",
            1,
        )
        if changed == workflow:
            raise AssertionError(
                f"{label} membership nesting marker differs"
            )
        yield label, changed


def generate_cgroup_initializer_control_flow_mutations(workflow: str):
    initializer = '        cgroup_path="$1"\n'
    wrappers = {
        "if-false": (
            "        if false; then\n",
            "        fi\n",
        ),
        "if-else": (
            "        if true; then\n"
            "          true\n"
            "        else\n",
            "        fi\n",
        ),
        "case": (
            "        case one in\n"
            "          one)\n",
            "            ;;\n"
            "        esac\n",
        ),
        "for-loop": (
            "        for init_attempt in one; do\n",
            "        done\n",
        ),
        "while-false": (
            "        while false; do\n",
            "        done\n",
        ),
        "brace-group": (
            "        {\n",
            "        }\n",
        ),
        "negation": (
            "        ! ",
            "",
        ),
        "dynamic": (
            "        eval '",
            "        '\n",
        ),
    }
    for label, (prefix, suffix) in wrappers.items():
        changed = workflow.replace(
            initializer,
            prefix + initializer + suffix,
            1,
        )
        if changed == workflow:
            raise AssertionError(
                f"{label} cgroup initializer mutation marker differs"
            )
        yield label, changed


def reformatted_cgroup_initializer_control(workflow: str) -> str:
    initializer = '        cgroup_path="$1"\n'
    changed = workflow.replace(
        initializer,
        '          cgroup_path="$1"\n',
        1,
    )
    if changed == workflow:
        raise AssertionError(
            "cgroup initializer formatting marker differs"
        )
    return changed


def generate_mandatory_operator_mutations(workflow: str):
    actions = {
        "initializer": '        cgroup_path="$1"\n',
        "trap": "        trap isolated_stage_failure ERR\n",
        "join": (
            '        printf \'%s\\n\' "$$" > '
            '"$cgroup_path/cgroup.procs"\n'
        ),
        "bind": (
            '        /usr/bin/mount --bind "$cgroup_path" '
            "/mnt/supervisor/cgroup\n"
        ),
        "remount": (
            "        /usr/bin/mount -o "
            "remount,bind,ro,nosuid,nodev,noexec \\\n"
        ),
        "supervisor-alias": (
            "        supervisor_cgroup=/mnt/supervisor/cgroup\n"
        ),
        "inode-check": (
            '        test "$(/usr/bin/stat -Lc %d:%i "$cgroup_path")" = \\\n'
        ),
        "checker": (
            '        /usr/bin/python3 -I -S - "$$" <<\'PY\'\n'
        ),
    }
    operator_prefixes = {
        "and": "        false && \\\n",
        "or": "        true || \\\n",
        "pipe": "        true | \\\n",
        "pipe-stderr": "        true |& \\\n",
        "background": "        true & \\\n",
    }
    for action, marker in actions.items():
        for operator, prefix in operator_prefixes.items():
            changed = workflow.replace(marker, prefix + marker, 1)
            if changed == workflow:
                raise AssertionError(
                    f"{action} {operator} mutation marker differs"
                )
            yield f"{action}-{operator}", changed
    initializer = actions["initializer"]
    following = workflow.replace(
        initializer,
        initializer.rstrip("\n") + " && true\n",
        1,
    )
    if following == workflow:
        raise AssertionError(
            "initializer following operator marker differs"
        )
    yield "initializer-following-and", following


def generate_mandatory_control_scope_mutations(workflow: str):
    actions = {
        "trap": "        trap isolated_stage_failure ERR\n",
        "join": (
            '        printf \'%s\\n\' "$$" > '
            '"$cgroup_path/cgroup.procs"\n'
        ),
        "bind": (
            '        /usr/bin/mount --bind "$cgroup_path" '
            "/mnt/supervisor/cgroup\n"
        ),
        "remount": (
            "        /usr/bin/mount -o "
            "remount,bind,ro,nosuid,nodev,noexec \\\n"
            "          /mnt/supervisor/cgroup\n"
        ),
        "supervisor-alias": (
            "        supervisor_cgroup=/mnt/supervisor/cgroup\n"
        ),
        "inode-check": (
            '        test "$(/usr/bin/stat -Lc %d:%i "$cgroup_path")" = \\\n'
            '          "$(/usr/bin/stat -Lc %d:%i "$supervisor_cgroup")"\n'
        ),
    }
    for label, action in actions.items():
        changed = workflow.replace(
            action,
            "        if false; then\n"
            + action
            + "        fi\n",
            1,
        )
        if changed == workflow:
            raise AssertionError(
                f"{label} control scope mutation marker differs"
            )
        yield label, changed


def generate_generic_heredoc_spoof_mutations(workflow: str):
    checker_start = workflow.index(
        '        /usr/bin/python3 -I -S - "$$" <<\'PY\'\n'
    )
    checker_end = (
        workflow.index("        PY\n", checker_start)
        + len("        PY\n")
    )
    checker = workflow[checker_start:checker_end]
    helper = (
        "        unmount_if_mounted() {\n"
        '          if /usr/bin/mountpoint -q "$1"; then\n'
        '            /usr/bin/umount --recursive "$1"\n'
        "          fi\n"
        "        }\n"
    )
    mandatory_actions = {
        "initializer": '        cgroup_path="$1"\n',
        "trap": "        trap isolated_stage_failure ERR\n",
        "join": (
            '        printf \'%s\\n\' "$$" > '
            '"$cgroup_path/cgroup.procs"\n'
        ),
        "checker": checker,
        "helper": helper,
    }
    for label, action in mandatory_actions.items():
        wrapped = (
            "        cat <<'FAKE_SECURITY_ACTION' > /dev/null\n"
            + action
            + "        FAKE_SECURITY_ACTION\n"
        )
        changed = workflow.replace(action, wrapped, 1)
        if changed == workflow:
            raise AssertionError(
                f"{label} heredoc spoof marker differs"
            )
        yield f"{label}-quoted", changed

    initializer = mandatory_actions["initializer"]
    variants = {
        "double-quoted": (
            '        cat <<"FAKE_SECURITY_ACTION" > /dev/null\n'
            + initializer
            + "        FAKE_SECURITY_ACTION\n"
        ),
        "escaped": (
            "        cat <<\\FAKE_SECURITY_ACTION > /dev/null\n"
            + initializer
            + "        FAKE_SECURITY_ACTION\n"
        ),
        "unquoted": (
            "        cat <<FAKE_SECURITY_ACTION > /dev/null\n"
            + initializer
            + "        FAKE_SECURITY_ACTION\n"
        ),
        "attached": (
            "        cat<<'FAKE_SECURITY_ACTION' > /dev/null\n"
            + initializer
            + "        FAKE_SECURITY_ACTION\n"
        ),
        "descriptor": (
            "        : 3<<'FAKE_SECURITY_ACTION'\n"
            + initializer
            + "        FAKE_SECURITY_ACTION\n"
        ),
        "strip-tabs": (
            "        cat <<-'FAKE_SECURITY_ACTION' > /dev/null\n"
            + initializer
            + "        FAKE_SECURITY_ACTION\n"
        ),
        "line-continuation": (
            "        cat <<\\\n"
            "        'FAKE_SECURITY_ACTION' > /dev/null\n"
            + initializer
            + "        FAKE_SECURITY_ACTION\n"
        ),
        "multiple": (
            "        cat <<'FIRST_FAKE' <<'SECOND_FAKE' > /dev/null\n"
            "        harmless\n"
            "        FIRST_FAKE\n"
            + initializer
            + "        SECOND_FAKE\n"
        ),
    }
    for label, replacement in variants.items():
        changed = workflow.replace(initializer, replacement, 1)
        if changed == workflow:
            raise AssertionError(
                f"{label} heredoc variant marker differs"
            )
        yield f"initializer-{label}", changed


def generate_commented_heredoc_hide_mutations(workflow: str):
    initializer = '        cgroup_path="$1"\n'
    trap = "        trap isolated_stage_failure ERR\n"
    checker_start = workflow.index(
        '        /usr/bin/python3 -I -S - "$$" <<\'PY\'\n'
    )
    checker_end = (
        workflow.index("        PY\n", checker_start)
        + len("        PY\n")
    )
    join = (
        '        printf \'%s\\n\' "$$" > '
        '"$cgroup_path/cgroup.procs"\n'
    )
    helper_end = (
        "        }\n"
        "        unmount_if_mounted /home/runner\n"
    )
    mutations = {
        "initializer-mutation": workflow.replace(
            initializer,
            initializer
            + "        : # <<':'\n"
            + "        cgroup_path=/tmp/fake\n"
            + "        :\n",
            1,
        ),
        "trap-mutation": workflow.replace(
            trap,
            trap
            + "        : # 3<<-':'\n"
            + "        trap - ERR\n"
            + "        :\n",
            1,
        ),
        "checker-state": (
            workflow[:checker_end]
            + "        : # <<':'\n"
            + '        cgroup_members=("$$")\n'
            + "        :\n"
            + workflow[checker_end:]
        ),
        "join-read": workflow.replace(
            join,
            join
            + "        : # <<':'\n"
            + '        /bin/cat "$cgroup_path/cgroup.procs" '
            + "> /dev/null\n"
            + "        :\n",
            1,
        ),
        "helper-redefinition": workflow.replace(
            helper_end,
            "        }\n"
            + "        : # <<':'\n"
            + "        unmount_if_mounted() {\n"
            + "          true\n"
            + "        }\n"
            + "        :\n"
            + "        unmount_if_mounted /home/runner\n",
            1,
        ),
    }
    comment_variants = {
        "plain": "        : # <<':'\n",
        "strip-tabs": "        : # <<-':'\n",
        "descriptor": "        : # 3<<':'\n",
        "here-string": "        : # <<<':'\n",
        "multiple": "        : # <<'FIRST' <<'SECOND'\n",
    }
    marker = "        isolated_stage=export\n"
    for label, comment in comment_variants.items():
        suffix = (
            "        FIRST\n"
            "        SECOND\n"
            if label == "multiple"
            else "        :\n"
        )
        mutations[f"comment-{label}"] = workflow.replace(
            marker,
            marker
            + comment
            + '        /bin/cat "$cgroup_path/cgroup.procs" '
            + "> /dev/null\n"
            + suffix,
            1,
        )
    boundary_variants = {
        "open-paren": (
            "        (# <<':'\n",
            "        :\n"
            "        )\n",
        ),
        "close-paren": (
            "        (:)# <<':'\n",
            "        :\n",
        ),
        "semicolon": (
            "        :;# <<':'\n",
            "        :\n",
        ),
        "and": (
            "        :&&# <<':'\n",
            "        :\n",
        ),
        "or": (
            "        :||# <<':'\n",
            "        :\n",
        ),
        "background": (
            "        :&# <<':'\n",
            "        :\n",
        ),
        "case-terminator": (
            "        case one in\n"
            "          one)# <<':'\n",
            "            :\n"
            "            ;;\n"
            "        esac\n",
        ),
    }
    for label, (comment, suffix) in boundary_variants.items():
        mutations[f"boundary-{label}"] = workflow.replace(
            marker,
            marker
            + comment
            + '        /bin/cat "$cgroup_path/cgroup.procs" '
            + "> /dev/null\n"
            + suffix,
            1,
        )
    for label, changed in mutations.items():
        if changed == workflow:
            raise AssertionError(
                f"{label} commented heredoc mutation marker differs"
            )
        yield label, changed


def generate_substitution_word_heredoc_spoofs(workflow: str):
    initializer = '        cgroup_path="$1"\n'
    wrappers = {
        "command-substitution": (
            "        result=$(printf '%s' x)# <<'FAKE_INIT' > /dev/null\n"
        ),
        "command-substitution-quoted-paren": (
            "        result=$(printf '%s' \")\")# "
            "<<'FAKE_INIT' > /dev/null\n"
        ),
        "nested-command-substitution": (
            "        result=$(printf '%s' \"$(printf x)\")# "
            "<<'FAKE_INIT' > /dev/null\n"
        ),
        "double-quoted-command-substitution": (
            '        result="$(printf x)"# '
            "<<'FAKE_INIT' > /dev/null\n"
        ),
        "locale-command-substitution": (
            '        result=$"$(printf x)"# '
            "<<'FAKE_INIT' > /dev/null\n"
        ),
        "backtick-substitution": (
            "        result=`printf x`# <<'FAKE_INIT' > /dev/null\n"
        ),
        "input-process-substitution": (
            "        result=<(printf x)# <<'FAKE_INIT' > /dev/null\n"
        ),
        "output-process-substitution": (
            "        result=>(/bin/cat > /dev/null)# "
            "<<'FAKE_INIT' > /dev/null\n"
        ),
    }
    for label, opener in wrappers.items():
        changed = workflow.replace(
            initializer,
            opener
            + initializer
            + "        FAKE_INIT\n",
            1,
        )
        if changed == workflow:
            raise AssertionError(
                f"{label} substitution heredoc marker differs"
            )
        yield label, changed


def generate_arithmetic_command_comment_hide_mutations(workflow: str):
    marker = "        isolated_stage=export\n"
    contexts = {
        "standalone-read": (
            "        ((1))# <<':'\n"
            '        /bin/cat "$cgroup_path/cgroup.procs" '
            "> /dev/null\n"
            "        :\n"
        ),
        "standalone-write": (
            "        ((1))# <<':'\n"
            "        cgroup_path=/tmp/fake\n"
            "        :\n"
        ),
        "if-condition": (
            "        if ((1))# <<':'\n"
            "        then\n"
            '          /bin/cat "$cgroup_path/cgroup.procs" '
            "> /dev/null\n"
            "        fi\n"
            "        :\n"
        ),
        "while-condition": (
            "        while ((0))# <<':'\n"
            "        do\n"
            "          true\n"
            "        done\n"
            '        /bin/cat "$cgroup_path/cgroup.procs" '
            "> /dev/null\n"
            "        :\n"
        ),
        "for-clause": (
            "        for ((i=0;i<1;i++))# <<':'\n"
            "        do\n"
            '          /bin/cat "$cgroup_path/cgroup.procs" '
            "> /dev/null\n"
            "        done\n"
            "        :\n"
        ),
        "nested-substitution": (
            "        (( value = $(printf 1) << 2 ))# <<':'\n"
            '        /bin/cat "$cgroup_path/cgroup.procs" '
            "> /dev/null\n"
            "        :\n"
        ),
    }
    for label, mutation in contexts.items():
        changed = workflow.replace(marker, marker + mutation, 1)
        if changed == workflow:
            raise AssertionError(
                f"{label} arithmetic comment mutation marker differs"
            )
        yield label, changed


def generate_reserved_transport_output_mutations(workflow: str):
    arrays = {
        "supervisor": (
            "checked_supervisor_transport_output",
            '        read_checked_supervisor_transport_file \\\n'
            '          "$dev_mounts_file" "$dev_mount_targets_max_bytes"\n',
        ),
        "runtime": (
            "checked_runtime_transport_output",
            '        read_checked_runtime_transport_file \\\n'
            '          "$writable_mount_records_file" \\\n'
            '          "$writable_mount_records_max_bytes"\n',
        ),
    }
    writers = (
        "{name}=()\n",
        "{name}=(/dev)\n",
        "{name}[0]=/dev\n",
        "unset {name}\n",
        "declare -a {name}=()\n",
        "printf -v {name} %s /dev\n",
        "read {name} < /dev/null\n",
        "mapfile -t {name} < /dev/null\n",
        "for {name} in /dev; do true; done\n",
        "target={name}\nread \"$target\" < /dev/null\n",
        "mutate() {{ {name}=(); }}\nmutate\n",
        "trap '{name}=()' DEBUG\n",
        "callback() {{ {name}=(); }}\n"
        "mapfile -C callback -c 1 -t ordinary < /dev/null\n",
        "time read -a {name} <<< /dev\n",
        "time -p read -a {name} <<< /dev\n",
        "time -- read -a {name} <<< /dev\n",
        "time -p -- read -a {name} <<< /dev\n",
        "case x in\nx) read -a {name} <<< /dev\n;;\nesac\n",
        "case x in\nx)read -a {name} <<< /dev\n;;\nesac\n",
    )
    consumers = (
        'printf "%s\\n" "${#ARRAY_NAME[@]}" > /dev/null\n',
        'printf "%s\\n" "${!ARRAY_NAME[@]}" > /dev/null\n',
        'copy=("${ARRAY_NAME[@]}")\n',
        'printf "%s\\n" "${ARRAY_NAME[0]}" > /dev/null\n',
        'test "$(( ${#ARRAY_NAME[@]} % 2 ))" -eq 0\n',
        'for value in "${ARRAY_NAME[@]}"; do true; done\n',
        'read ordinary <<< "${ARRAY_NAME[0]}"\n',
        'name=ARRAY_NAME\nprintf "%s\\n" "${!name}" > /dev/null\n',
    )
    for family, (name, marker) in arrays.items():
        for index, template in enumerate(consumers):
            mutation = "".join(
                f"        {line}\n"
                for line in template.replace(
                    "ARRAY_NAME",
                    name,
                ).splitlines()
            )
            changed = workflow.replace(marker, mutation + marker, 1)
            yield f"{family}-preuse-{index}", changed
        for index, template in enumerate(writers):
            mutation = "".join(
                f"        {line}\n"
                for line in template.format(name=name).splitlines()
            )
            changed = workflow.replace(marker, marker + mutation, 1)
            if changed == workflow:
                raise AssertionError(
                    f"{family} reserved mutation marker differs"
                )
            yield f"{family}-writer-{index}", changed
        conditional = workflow.replace(
            marker,
            "        if true; then\n"
            + marker
            + "        fi\n",
            1,
        )
        yield f"{family}-conditional-producer", conditional
        duplicate = workflow.replace(marker, marker + marker, 1)
        yield f"{family}-duplicate-producer", duplicate
    second_supervisor = (
        '        read_checked_supervisor_transport_file \\\n'
        '          "$remaining_dev_mounts_file" \\\n'
        '          "$dev_mount_targets_max_bytes"\n'
    )
    stale = (
        '        printf "%s\\n" '
        '"${checked_supervisor_transport_output[0]}" > /dev/null\n'
    )
    yield "supervisor-stale-interphase", workflow.replace(
        second_supervisor,
        stale + second_supervisor,
        1,
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
            "short-cluster-readonly-remount",
            ("/usr/bin/mount -ro remount /mnt/supervisor",),
        ),
        (
            "short-attached-options-remount-readonly",
            ("/usr/bin/mount -oremount,ro /mnt/supervisor",),
        ),
        (
            "split-readonly-short-then-options-remount",
            ("/usr/bin/mount -r -o remount /mnt/supervisor",),
        ),
        (
            "split-options-remount-then-readonly-short",
            ("/usr/bin/mount -o remount -r /mnt/supervisor",),
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
            "assignment-command-substitution-direct",
            (
                "root=/mnt",
                'ignored=$(/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$root/supervisor")',
            ),
        ),
        (
            "assignment-backtick-direct",
            (
                "root=/mnt",
                'ignored=`/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$root/supervisor"`',
            ),
        ),
        (
            "input-process-substitution-direct",
            (
                "root=/mnt",
                'cat <(/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$root/supervisor") >/dev/null',
            ),
        ),
        (
            "output-process-substitution-direct",
            (
                "root=/mnt",
                ': >(/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$root/supervisor")',
            ),
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
            "dynamic-bash-c-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                '/bin/bash -c "$cmd"',
            ),
        ),
        (
            "dynamic-sh-c-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                '/bin/sh -c "$cmd"',
            ),
        ),
        (
            "dynamic-dash-c-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                '/bin/dash -c "$cmd"',
            ),
        ),
        (
            "literal-bash-c-wrapper",
            (
                '/bin/bash -c "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor"',
            ),
        ),
        (
            "shell-alias-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'bash -c "$cmd"',
            ),
        ),
        (
            "split-interpreter-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                "shell=/bin/bash",
                '"$shell" -c "$cmd"',
            ),
        ),
        (
            "env-shell-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'env /bin/bash -c "$cmd"',
            ),
        ),
        (
            "env-short-chdir-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'env -C /tmp /bin/bash -c "$cmd"',
            ),
        ),
        (
            "env-long-chdir-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'env --chdir /tmp /bin/bash -c "$cmd"',
            ),
        ),
        (
            "env-long-chdir-equals-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'env --chdir=/tmp /bin/bash -c "$cmd"',
            ),
        ),
        (
            "env-unset-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'env -u HOME /bin/bash -c "$cmd"',
            ),
        ),
        (
            "env-unset-long-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'env --unset HOME /bin/bash -c "$cmd"',
            ),
        ),
        (
            "env-unset-equals-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'env --unset=HOME /bin/bash -c "$cmd"',
            ),
        ),
        (
            "env-ignore-assign-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'env -i ROOT=/mnt /bin/bash -c "$cmd"',
            ),
        ),
        (
            "env-option-terminator-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'env -i -- /bin/bash -c "$cmd"',
            ),
        ),
        (
            "env-split-string-wrapper",
            (
                'env -S "/bin/bash -c /usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor"',
            ),
        ),
        (
            "bin-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                '/bin/env -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "bin-env-split-string-attached-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                '/bin/env \'-S/bin/bash -c "$cmd"\'',
            ),
        ),
        (
            "env-split-string-long-wrapper",
            (
                'env --split-string "/bin/bash -c /usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor"',
            ),
        ),
        (
            "bin-env-split-string-long-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                '/bin/env --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "env-split-string-equals-wrapper",
            (
                'env \'--split-string=/bin/bash -c /usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor\'',
            ),
        ),
        (
            "bin-env-split-string-equals-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                '/bin/env \'--split-string=/bin/bash -c "$cmd"\'',
            ),
        ),
        (
            "path-alias-env-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                '/usr/local/bin/env -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "relative-env-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                './env --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "busybox-env-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                '/bin/busybox env -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "busybox-variable-env-unset-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "ENV_APPLET=env",
                '/bin/busybox "$ENV_APPLET" --unset HOME --split-string '
                '"/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "variable-env-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                '"$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "variable-env-ignore-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                '"$env_cmd" -i -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "variable-env-unset-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                '"$env_cmd" --unset HOME --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "variable-env-chdir-split-equals-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                '"$env_cmd" --chdir=/tmp \'--split-string=/bin/bash -c "$cmd"\'',
            ),
        ),
        (
            "command-variable-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                'command -- "$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "nice-variable-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                'nice -n 5 "$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "sudo-variable-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                'sudo -u root "$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "timeout-variable-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                'timeout 5 "$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "timeout-variable-busybox-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "busybox_cmd=/bin/busybox",
                "ENV_APPLET=env",
                'timeout 5 "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "setsid-variable-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                '/usr/bin/setsid --wait "$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "setsid-short-option-variable-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                '/usr/bin/setsid -w "$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "timeout-setsid-variable-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                'timeout 5 /usr/bin/setsid --wait "$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "setsid-variable-busybox-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "busybox_cmd=/bin/busybox",
                "ENV_APPLET=env",
                '/usr/bin/setsid --wait "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "timeout-setsid-variable-busybox-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "busybox_cmd=/bin/busybox",
                "ENV_APPLET=env",
                'timeout 5 /usr/bin/setsid --wait "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "nohup-variable-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                'nohup "$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "nohup-setsid-variable-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                'nohup /usr/bin/setsid --wait "$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "taskset-variable-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                'taskset -c 0 "$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "ionice-variable-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                'ionice -c3 "$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "flock-variable-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                'flock -n /dev/null "$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "flock-relative-lockfile-variable-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "env_cmd=/bin/env",
                'flock -n lockfile "$env_cmd" -S "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "nohup-variable-busybox-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "busybox_cmd=/bin/busybox",
                "ENV_APPLET=env",
                'nohup "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "nohup-setsid-variable-busybox-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "busybox_cmd=/bin/busybox",
                "ENV_APPLET=env",
                'nohup /usr/bin/setsid --wait "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "taskset-variable-busybox-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "busybox_cmd=/bin/busybox",
                "ENV_APPLET=env",
                'taskset -c 0 "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "ionice-variable-busybox-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "busybox_cmd=/bin/busybox",
                "ENV_APPLET=env",
                'ionice -c3 "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "flock-variable-busybox-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "busybox_cmd=/bin/busybox",
                "ENV_APPLET=env",
                'flock -n /dev/null "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "flock-absolute-lockfile-variable-busybox-env-split-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                "busybox_cmd=/bin/busybox",
                "ENV_APPLET=env",
                'flock -n /var/lock/ci-patch.lock "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "flock-lockfile-command-string-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                'flock -n lockfile -c "$cmd"',
            ),
        ),
        (
            "env-combined-options-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'env -iu HOME /bin/bash -c "$cmd"',
            ),
        ),
        (
            "command-shell-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'command /bin/bash -c "$cmd"',
            ),
        ),
        (
            "command-option-shell-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'command -- /bin/bash -c "$cmd"',
            ),
        ),
        (
            "sudo-shell-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'sudo /bin/bash -c "$cmd"',
            ),
        ),
        (
            "sudo-option-shell-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'sudo -u root /bin/bash -c "$cmd"',
            ),
        ),
        (
            "timeout-shell-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'timeout 5 /bin/bash -c "$cmd"',
            ),
        ),
        (
            "timeout-option-shell-wrapper",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                'timeout --signal TERM --kill-after 1 5 /bin/bash -c "$cmd"',
            ),
        ),
        (
            "question-glob-target-alias",
            (
                "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/superviso?",
            ),
        ),
        (
            "globbed-env-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                '/bin/e?v --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "globbed-busybox-wrapper",
            (
                "ROOT=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"',
                '/bin/busybo? env --split-string "/bin/bash -c \\"$cmd\\""',
            ),
        ),
        (
            "extglob-target-alias",
            (
                "shopt -s extglob",
                "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/superviso@(r)",
            ),
        ),
        (
            "command-substitution-shell",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                '$(printf /bin/bash) -c "$cmd"',
            ),
        ),
        (
            "backtick-shell",
            (
                "root=/mnt",
                'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${root}/supervisor"',
                '`printf /bin/bash` -c "$cmd"',
            ),
        ),
        (
            "nested-shell-c",
            (
                '/bin/bash -c \'/bin/sh -c "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor"\'',
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
    if workflow_has_raw_builder_cgroup_membership_read(workflow):
        errors.append("raw builder cgroup membership read differs")
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
    strict_shell = "shell: /bin/bash --noprofile --norc -euo pipefail {0}"
    if any(steps[index].count(strict_shell) != 1 for index in (
        isolated_build,
        create,
        cleanup,
        revalidate,
    )):
        errors.append("publisher step shell boundary differs")
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
        or "local -n" in isolated_step
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
            '          "$dev_mounts_file" "$dev_mount_targets_max_bytes"'
        )
        not in isolated_step
        or 'remove_supervisor_transport_file "$dev_mounts_file"'
        not in isolated_step
        or (
            "for ((index=${#checked_supervisor_transport_output[@]} - 1; index >= 0; index--)); do"
        )
        not in isolated_step
        or '/dev/*) /usr/bin/umount -- "$dev_mount" ;;'
        not in isolated_step
        or 'list_dev_mount_targets > "$remaining_dev_mounts_file"'
        not in isolated_step
        or (
            'read_checked_supervisor_transport_file \\\n'
            '          "$remaining_dev_mounts_file" \\\n'
            '          "$dev_mount_targets_max_bytes"'
        )
        not in isolated_step
        or 'remove_supervisor_transport_file "$remaining_dev_mounts_file"'
        not in isolated_step
        or 'test "${#checked_supervisor_transport_output[@]}" -eq 1'
        not in isolated_step
        or 'test "${checked_supervisor_transport_output[0]}" = /dev'
        not in isolated_step
        or 'list_writable_mount_records > "$writable_mount_records_file"' not in isolated_step
        or (
            'read_checked_runtime_transport_file \\\n'
            '          "$writable_mount_records_file" \\\n'
            '          "$writable_mount_records_max_bytes"'
        )
        not in isolated_step
        or 'remove_runtime_transport_file "$writable_mount_records_file"'
        not in isolated_step
        or 'test "$(( ${#checked_runtime_transport_output[@]} % 2 ))" -eq 0'
        not in isolated_step
        or 'mount_target="${checked_runtime_transport_output[index]}"'
        not in isolated_step
        or 'mount_options="${checked_runtime_transport_output[index + 1]}"'
        not in isolated_step
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
        or "candidate build failed: stage=isolated detail=%s exit=%d"
        not in isolated_step
        or "isolated_stage_failure" not in isolated_step
        or "candidate_stage_failure" not in isolated_step
        or "builder_isolated_detail=transport" not in isolated_step
        or '125|126) exit "$candidate_status" ;;' not in isolated_step
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
        or (
            'test "$(/usr/bin/stat -Lc %d:%i "$cgroup_path")" = \\\n'
            '          "$(/usr/bin/stat -Lc %d:%i "$supervisor_cgroup")"'
        )
        not in isolated_step
        or "for option in ro nosuid nodev noexec; do" not in isolated_step
        or 'test ! -r /mnt/supervisor' not in isolated_step
        or 'test ! -w /mnt/supervisor' not in isolated_step
        or 'test ! -x /mnt/supervisor' not in isolated_step
        or 'test ! -r /mnt/supervisor/cgroup/cgroup.procs'
        not in isolated_step
        or 'MEMBERSHIP_PATH = "/mnt/supervisor/cgroup/cgroup.procs"'
        not in isolated_step
        or (
            'cgroup_members="$(LC_ALL=C /usr/bin/sort -n \\\n'
            '          "$supervisor_cgroup/cgroup.procs")"'
        )
        in isolated_step
        or '/usr/bin/python3 -I -S - "$$" <<\'PY\''
        not in isolated_step
        or "members != {expected_pid, checker_pid}" not in isolated_step
        or re.search(r"\bcgroup_members\b", isolated_step)
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
        or 'builder_group_is_empty "$builder_session_id"' not in isolated_step
        or "builder_session_authenticated=1" not in isolated_step
        or "builder_supervisor_identity_matches" not in isolated_step
        or "read_builder_identity" not in isolated_step
        or "builder_supervisor_parent_pid" not in isolated_step
        or "builder_supervisor_starttime" not in isolated_step
        or "builder_supervisor_wait_pid" not in isolated_step
        or "supervisor-launcher.py" not in isolated_step
        or "os.setsid()" not in isolated_step
        or "signal.SIGSTOP" not in isolated_step
        or "libc.prctl(1, signal.SIGKILL" not in isolated_step
        or '/bin/kill -TERM "$builder_supervisor_pid"' in isolated_step
        or '/bin/kill -KILL "$builder_supervisor_pid"' in isolated_step
        or "set +m" not in isolated_step
        or "builder_launch_detail=session-ready" not in isolated_step
        or 'kill -CONT "$builder_supervisor_pid"' not in isolated_step
        or "/usr/bin/setsid --wait /usr/bin/timeout" in isolated_step
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
            'builder_group_is_empty "$builder_session_id"\n'
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
            '        builder_group_is_empty "$builder_session_id"\n'
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


SUPERVISOR_WRITABLE_MOUNT_NAMESPACE_HARNESS = """\
set -euo pipefail
section_path="$1"
cleanup() {
  local status=0
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
mount -t tmpfs -o nosuid,nodev,noexec,mode=0700,size=1m \
  probe-supervisor /mnt/supervisor
source "$section_path"
cleanup
trap - EXIT
"""


def run_extracted_supervisor_writable_mount_probe(
    root: Path,
    workflow: str,
) -> subprocess.CompletedProcess[str]:
    root.mkdir(parents=True, exist_ok=True)
    section = writable_mount_transport_section_source(workflow)
    findmnt_scope = '"TARGET,OPTIONS", "-R", "/"],'
    replacement_count = section.count(findmnt_scope)
    section = section.replace(
        findmnt_scope,
        '"TARGET,OPTIONS", "-R", "/mnt/supervisor"],',
        1,
    )
    if replacement_count != 1:
        raise AssertionError("exact workflow probe must contain one writable mount scope")
    section_path = root / "section.sh"
    section_path.write_text(section, encoding="utf-8")
    return run_rootless_mount_namespace(
        SUPERVISOR_WRITABLE_MOUNT_NAMESPACE_HARNESS,
        str(section_path),
    )


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


def supervisor_launcher_source(workflow: str) -> str:
    script = named_step_run_script(
        workflow,
        "Build candidate in isolated namespace and stage public inputs",
    )
    match = re.search(
        r"(?ms)<<'SUPERVISOR_LAUNCHER'\n"
        r"(?P<body>.*?)^SUPERVISOR_LAUNCHER$",
        script,
    )
    if match is None:
        raise AssertionError("publisher must expose the trusted supervisor launcher")
    return match.group("body")


def candidate_build_shell_source(workflow: str) -> str:
    script = named_step_run_script(
        workflow,
        "Build candidate in isolated namespace and stage public inputs",
    )
    match = re.search(
        r"(?ms)<<'CANDIDATE_BUILD'\n"
        r"(?P<body>.*?)^CANDIDATE_BUILD$",
        script,
    )
    if match is None:
        raise AssertionError("publisher must expose the candidate build shell")
    return match.group("body")


def isolated_failure_report_source(workflow: str) -> str:
    script = named_step_run_script(
        workflow,
        "Build candidate in isolated namespace and stage public inputs",
    )
    start = script.index('if [ "$builder_status" -ne 0 ]; then')
    end = script.index("printf 'candidate build status: success", start)
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
            '/usr/bin/python3 -I -S - "$$" <<\'PY\'',
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
        self.assertIn("supervisor-launcher.py", self.patch_job)
        self.assertIn("os.setsid()", self.patch_job)
        self.assertIn("signal.SIGSTOP", self.patch_job)
        self.assertIn("libc.prctl(1, signal.SIGKILL", self.patch_job)
        self.assertIn("builder_session_authenticated=1", self.patch_job)
        self.assertIn("builder_supervisor_identity_matches", self.patch_job)
        self.assertIn("read_builder_identity", self.patch_job)
        self.assertIn("builder_supervisor_starttime", self.patch_job)
        self.assertIn("builder_supervisor_wait_pid", self.patch_job)
        self.assertIn("builder_supervisor_parent_pid", self.patch_job)
        self.assertNotIn(
            '/bin/kill -TERM "$builder_supervisor_pid"',
            self.patch_job,
        )
        self.assertNotIn(
            '/bin/kill -KILL "$builder_supervisor_pid"',
            self.patch_job,
        )
        self.assertIn("builder_launch_detail=session-ready", self.patch_job)
        self.assertIn('kill -CONT "$builder_supervisor_pid"', self.patch_job)
        self.assertNotIn(
            "/usr/bin/setsid --wait /usr/bin/timeout",
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
        self.assertIn(
            "candidate build failed: stage=isolated detail=%s exit=%d",
            self.patch_job,
        )
        self.assertIn("isolated_stage_failure", self.patch_job)
        self.assertIn("candidate_stage_failure", self.patch_job)
        self.assertIn("builder_isolated_detail=transport", self.patch_job)
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
            'builder_group_is_empty "$builder_session_id"',
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
        builder = builder_isolation_shell_source(self.text)
        protocol_start = builder.index("isolated_stage=namespace")
        protocol_marker = "trap isolated_stage_failure ERR"
        protocol_end = (
            builder.index(protocol_marker, protocol_start)
            + len(protocol_marker)
        )
        protocol = builder[protocol_start:protocol_end]
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
                        "set -Eeuo pipefail\n"
                        "umask 077\n"
                        'TRANSPORT_ROOT="$1"\n'
                        'TRANSPORT_UID="$2"\n'
                        'RECORDS_PATH="$3"\n'
                        + protocol
                        + "\nisolated_stage=mount-audit\n"
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
                    "/mnt/supervisor",
                    "rw,nosuid,nodev,noexec,mode=700",
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
            self.assertEqual(completed.returncode, 82)
            self.assertIn(
                "unexpected writable mount: /mnt/name with space",
                completed.stderr,
            )

    def test_supervisor_rw_mount_audit_fails_on_exact_master_and_passes_current_workflow(
        self,
    ):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="supervisor-writable-mount-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            require_findmnt_uniq_namespace_capability(sandbox / "preflight")
            failed_workflow = subprocess.check_output(
                [
                    "git",
                    "--no-pager",
                    "show",
                    f"{FAILING_MASTER_8D81}:.github/workflows/build.yml",
                ],
                cwd=ROOT,
                text=True,
            )
            failure = run_extracted_supervisor_writable_mount_probe(
                sandbox / "failure",
                failed_workflow,
            )
            self.assertEqual(failure.returncode, 1)
            self.assertIn(
                "unexpected writable mount: /mnt/supervisor",
                failure.stderr,
            )
            success = run_extracted_supervisor_writable_mount_probe(
                sandbox / "success",
                self.text,
            )
            self.assertEqual(
                success.returncode,
                0,
                _bounded_process_diagnostic(success),
            )
            self.assertEqual(success.stdout, "")
            self.assertEqual(success.stderr, "")

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

    def test_every_private_boundary_step_requires_exact_shell(self):
        shell = "shell: /bin/bash --noprofile --norc -euo pipefail {0}"
        for step_name in (
            "Build candidate in isolated namespace and stage public inputs",
            "Create and verify patch artifact",
            "Cleanup and verify private base",
            "Revalidate patch-only upload",
        ):
            with self.subTest(step=step_name):
                step = next(
                    item
                    for item in patch_release_step_blocks(self.text)
                    if f"- name: {step_name}" in item
                )
                changed_step = step.replace(
                    shell,
                    "shell: /bin/bash --noprofile --norc -xeuo pipefail {0}",
                    1,
                )
                changed = self.text.replace(step, changed_step, 1)
                self.assertNotEqual(changed, self.text)
                self.assertIn(
                    "publisher step shell boundary differs",
                    publisher_boundary_errors(changed),
                )

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
            'MEMBERSHIP_PATH = "/mnt/supervisor/cgroup/cgroup.procs"',
            'MEMBERSHIP_PATH = "/sys/fs/cgroup/cgroup.procs"',
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
            '          "$dev_mounts_file" "$dev_mount_targets_max_bytes"\n'
            '        remove_supervisor_transport_file "$dev_mounts_file"',
            "        mapfile -d '' -t dev_mounts < <(list_dev_mount_targets)",
            1,
        )
        unchecked_writable_mount_process_substitution = self.text.replace(
            '        writable_mount_records_file="$(create_runtime_transport_file writable-mount-records)"\n'
            '        list_writable_mount_records > "$writable_mount_records_file"\n'
            "        read_checked_runtime_transport_file \\\n"
            '          "$writable_mount_records_file" \\\n'
            '          "$writable_mount_records_max_bytes"\n'
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
            "for ((index=${#checked_supervisor_transport_output[@]} - 1; "
            "index >= 0; index--)); do",
            "for ((index=0; "
            "index < ${#checked_supervisor_transport_output[@]}; "
            "index++)); do",
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

    def test_mount_short_option_cluster_runtime_matches_canonical_remount_parsing(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="mount-short-cluster-runtime-",
            dir=artifact_root,
        ) as temporary:
            target = Path(temporary)
            remount_forms = {
                "cluster-ro-remount": ["/usr/bin/mount", "-f", "-v", "-ro", "remount", str(target)],
                "split-r-o-remount": ["/usr/bin/mount", "-f", "-v", "-r", "-o", "remount", str(target)],
                "attached-o-remount-ro": ["/usr/bin/mount", "-f", "-v", "-oremount,ro", str(target)],
            }
            nonremount_forms = {
                "cluster-or-remount": ["/usr/bin/mount", "-f", "-v", "-or", "remount", str(target)],
                "cluster-orw-remount": ["/usr/bin/mount", "-f", "-v", "-orw", "remount", str(target)],
            }
            expected_remount = f"mount: (null) mounted on {target}.\n"
            expected_source = f"mount: remount mounted on {target}.\n"

            for label, argv in remount_forms.items():
                with self.subTest(case=label):
                    completed = subprocess.run(
                        argv,
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout, expected_remount)
                    self.assertEqual(completed.stderr, "")

            for label, argv in nonremount_forms.items():
                with self.subTest(case=label):
                    completed = subprocess.run(
                        argv,
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout, expected_source)
                    self.assertEqual(completed.stderr, "")

    def test_mount_short_option_cluster_parser_tracks_effective_remount_readonly_state(self):
        cases = (
            ("cluster-ro-remount", "/usr/bin/mount -ro remount /mnt/supervisor", True),
            ("cluster-fnv-ro-remount", "/usr/bin/mount -fnv -ro remount /mnt/supervisor", True),
            ("attached-o-remount-ro", "/usr/bin/mount -oremount,ro /mnt/supervisor", True),
            ("split-r-o-remount", "/usr/bin/mount -r -o remount /mnt/supervisor", True),
            ("split-o-r-remount", "/usr/bin/mount -o remount -r /mnt/supervisor", True),
            ("rw-option-list-overrides-readonly", "/usr/bin/mount -o rw,remount /mnt/supervisor", False),
            ("short-rw-cluster-overrides-readonly", "/usr/bin/mount -rw -o remount /mnt/supervisor", False),
            ("split-r-w-overrides-readonly", "/usr/bin/mount -r -w -o remount /mnt/supervisor", False),
            ("split-w-r-restores-readonly", "/usr/bin/mount -w -r -o remount /mnt/supervisor", True),
            ("cluster-or-consumes-r-as-option-arg", "/usr/bin/mount -or remount /mnt/supervisor", False),
            ("cluster-orw-consumes-rw-as-option-arg", "/usr/bin/mount -orw remount /mnt/supervisor", False),
            ("missing-mountpoint-after-cluster-fails-closed", "/usr/bin/mount -ro /mnt/supervisor", True),
            ("unknown-short-cluster-fails-closed", "/usr/bin/mount -rz remount /mnt/supervisor", True),
        )

        for label, command_text, expected in cases:
            with self.subTest(case=label):
                tokens = publisher_shell_contract._parse_shell_tokens(
                    command_text,
                    label=label,
                )
                self.assertEqual(
                    publisher_shell_contract._mount_command_targets_supervisor_parent(
                        tokens,
                        allow_reviewed_nonliteral_hidden=False,
                    ),
                    expected,
                )

    def test_mount_short_option_clusters_are_rejected_in_wrapper_and_substitution_contexts(self):
        self.assertFalse(workflow_has_supervisor_parent_readonly_remount(self.text))
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="mount-short-cluster-shared-",
            dir=artifact_root,
        ) as temporary:
            target = Path(temporary)
            cases = (
                (
                    "if-wrapper-short-cluster",
                    f'if /usr/bin/mount -f -v -ro remount "{target}" >/dev/null; then printf RUNTIME_IF_CLUSTER; fi\n',
                    "RUNTIME_IF_CLUSTER",
                    'root=/mnt\nif /usr/bin/mount -ro remount "$root/supervisor"; then :; fi\n',
                ),
                (
                    "assignment-substitution-short-cluster",
                    f'ignored="$("/usr/bin/mount" -f -v -ro remount "{target}")"\n'
                    'printf "%s" "$ignored"\n',
                    f"mount: (null) mounted on {target}.",
                    'root=/mnt\nignored=$(/usr/bin/mount -ro remount "$root/supervisor")\n',
                ),
            )

            for label, runtime_script, expected_stdout, semantic_script in cases:
                with self.subTest(case=label):
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "--noprofile",
                            "--norc",
                            "-eu",
                            "-o",
                            "pipefail",
                            "-c",
                            runtime_script,
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout, expected_stdout)
                    self.assertEqual(completed.stderr, "")
                    self.assertTrue(
                        publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                            semantic_script,
                            label=label,
                        )
                    )

    def test_publisher_run_scalar_matches_reference_yaml_bytes(self):
        step_block = named_patch_release_step_block(
            self.text,
            "Build candidate in isolated namespace and stage public inputs",
        )
        actual_run = named_step_run_script_from_block(step_block)
        reference_run = reference_literal_run_step_script(step_block)
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

    def test_reference_literal_run_parser_rejects_complex_yaml_styles(self):
        step_block = named_patch_release_step_block(
            self.text,
            "Build candidate in isolated namespace and stage public inputs",
        )
        mutations = (
            step_block.replace("      run: |\n", "      run: >\n", 1),
            step_block.replace("      run: |\n", "      run: |2\n", 1),
            step_block.replace("      run: |\n", "      run: &anchor |\n", 1),
        )
        for mutated in mutations:
            with self.subTest(header=mutated.splitlines()[3]):
                with self.assertRaisesRegex(
                    AssertionError,
                    "reference literal parser",
                ):
                    reference_literal_run_step_script(mutated)

    def test_env_split_string_runtime_executes_shell_c_and_detector_rejects_it(self):
        cases = (
            (
                ["/bin/env", "-S", '/bin/bash -c "printf RUNTIME_ENV_S"'],
                "RUNTIME_ENV_S",
                'ROOT=/mnt\ncmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
                '/bin/env -S "/bin/bash -c \\"$cmd\\""\n',
            ),
            (
                [
                    "/bin/env",
                    "--split-string",
                    '/bin/bash -c "printf RUNTIME_ENV_SPLIT"',
                ],
                "RUNTIME_ENV_SPLIT",
                'ROOT=/mnt\ncmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
                '/bin/env --split-string "/bin/bash -c \\"$cmd\\""\n',
            ),
            (
                [
                    "/bin/env",
                    '--split-string=/bin/bash -c "printf RUNTIME_ENV_EQUALS"',
                ],
                "RUNTIME_ENV_EQUALS",
                'ROOT=/mnt\ncmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
                '/bin/env \'--split-string=/bin/bash -c "$cmd"\'\n',
            ),
            (
                ["/bin/env", "-i", "-S", '/bin/bash -c "printf RUNTIME_ENV_I_S"'],
                "RUNTIME_ENV_I_S",
                'ROOT=/mnt\ncmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
                '/bin/env -i -S "/bin/bash -c \\"$cmd\\""\n',
            ),
            (
                [
                    "/bin/env",
                    "--unset",
                    "HOME",
                    "--split-string",
                    '/bin/bash -c "printf RUNTIME_ENV_UNSET_SPLIT"',
                ],
                "RUNTIME_ENV_UNSET_SPLIT",
                'ROOT=/mnt\ncmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
                '/bin/env --unset HOME --split-string "/bin/bash -c \\"$cmd\\""\n',
            ),
            (
                [
                    "/bin/env",
                    "--chdir=/tmp",
                    '--split-string=/bin/bash -c "printf RUNTIME_ENV_CHDIR_SPLIT"',
                ],
                "RUNTIME_ENV_CHDIR_SPLIT",
                'ROOT=/mnt\ncmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
                '/bin/env --chdir=/tmp \'--split-string=/bin/bash -c "$cmd"\'\n',
            ),
            (
                ["/usr/bin/timeout", "5", "/bin/env", "-S", '/bin/bash -c "printf RUNTIME_TIMEOUT_ENV_S"'],
                "RUNTIME_TIMEOUT_ENV_S",
                'ROOT=/mnt\ncmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
                'env_cmd=/bin/env\n'
                'timeout 5 "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
            ),
        )
        for argv, expected_stdout, semantic_script in cases:
            with self.subTest(argv=argv[1]):
                completed = subprocess.run(
                    argv,
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, expected_stdout)
                self.assertEqual(completed.stderr, "")
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                        semantic_script,
                        label="runtime env split-string shell wrapper",
                    )
                )

    def test_exact_reviewer_timeout_env_split_string_repros_execute_and_detector_rejects_them(
        self,
    ):
        prefix = (
            'ROOT=/mnt\n'
            'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
        )
        cases = (
            (
                "timeout-attached-kill-wrapper",
                'cmd="printf RUNTIME_TIMEOUT_ATTACHED_KILL"\n'
                'timeout -k1 5 /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
                "RUNTIME_TIMEOUT_ATTACHED_KILL",
                prefix + 'timeout -k1 5 /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
            ),
            (
                "variable-timeout-wrapper",
                'cmd="printf RUNTIME_TIMEOUT_VARIABLE"\n'
                'wrapper=/usr/bin/timeout\n'
                '"$wrapper" 5 /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
                "RUNTIME_TIMEOUT_VARIABLE",
                prefix
                + 'wrapper=/usr/bin/timeout\n'
                + '"$wrapper" 5 /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
            ),
            (
                "globbed-timeout-wrapper",
                'cmd="printf RUNTIME_TIMEOUT_GLOBBED"\n'
                '/usr/bin/timeou? 5 /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
                "RUNTIME_TIMEOUT_GLOBBED",
                prefix + '/usr/bin/timeou? 5 /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
            ),
        )

        for label, runtime_script, expected_stdout, semantic_script in cases:
            with self.subTest(case=label):
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "--noprofile",
                        "--norc",
                        "-eu",
                        "-o",
                        "pipefail",
                        "-c",
                        runtime_script,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, expected_stdout)
                self.assertEqual(completed.stderr, "")
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                        semantic_script,
                        label=label,
                    )
                )

    def test_timeout_env_split_string_wrapper_variants_fail_closed(self):
        prefix = (
            'ROOT=/mnt\n'
            'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
        )
        rejected = {
            "timeout-attached-signal-wrapper": 'timeout -sTERM 5 /bin/env --split-string "/bin/bash -c \\"$cmd\\""\n',
            "timeout-long-option-wrapper": (
                "timeout --foreground --preserve-status --kill-after=1 "
                '--signal=TERM 5 command -- /bin/env \'--split-string=/bin/bash -c "$cmd"\'\n'
            ),
            "nested-timeout-command-env-wrapper": (
                'nice -n 5 timeout --foreground 5 command -- /bin/env -S "/bin/bash -c \\"$cmd\\""\n'
            ),
            "timeout-unknown-option-wrapper": 'timeout --bogus 5 /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
            "nice-unknown-option-wrapper": 'nice --bogus /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
            "sudo-unknown-option-wrapper": 'sudo --bogus /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
            "command-unknown-option-wrapper": 'command --bogus /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
            "variable-nice-wrapper": 'wrapper=/usr/bin/nice\n"$wrapper" -n 5 /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
            "globbed-nice-wrapper": '/usr/bin/ni?e -n 5 /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
            "variable-sudo-wrapper": 'wrapper=/usr/bin/sudo\n"$wrapper" -u root /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
            "globbed-sudo-wrapper": '/usr/bin/sud? -u root /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
            "variable-command-wrapper": 'wrapper=command\n"$wrapper" -- /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
            "globbed-command-wrapper": 'comman? -- /bin/env -S "/bin/bash -c \\"$cmd\\""\n',
        }

        self.assertFalse(workflow_has_supervisor_parent_readonly_remount(self.text))

        for label, command in rejected.items():
            with self.subTest(case=label):
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                        prefix + command,
                        label=label,
                    )
                )

    def test_setsid_wrapped_env_surfaces_execute_and_detector_rejects_them(self):
        prefix = (
            'ROOT=/mnt\n'
            'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
        )
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="setsid-busybox-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            busybox = sandbox / "busybox"
            busybox.write_text(
                "#!/bin/sh\n"
                'if [ "$1" != "env" ]; then\n'
                "  exit 125\n"
                "fi\n"
                "shift\n"
                'exec /bin/env "$@"\n',
                encoding="ascii",
            )
            busybox.chmod(0o755)

            cases = (
                (
                    "setsid-variable-env",
                    'cmd="printf RUNTIME_SETSID_ENV"\n'
                    "env_cmd=/bin/env\n"
                    '/usr/bin/setsid --wait "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_SETSID_ENV",
                    prefix
                    + "env_cmd=/bin/env\n"
                    + '/usr/bin/setsid --wait "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "setsid-short-option-variable-env",
                    'cmd="printf RUNTIME_SETSID_SHORT_OPTION"\n'
                    "env_cmd=/bin/env\n"
                    '/usr/bin/setsid -w "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_SETSID_SHORT_OPTION",
                    prefix
                    + "env_cmd=/bin/env\n"
                    + '/usr/bin/setsid -w "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "timeout-setsid-variable-env",
                    'cmd="printf RUNTIME_TIMEOUT_SETSID_ENV"\n'
                    "env_cmd=/bin/env\n"
                    'timeout 5 /usr/bin/setsid --wait "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_TIMEOUT_SETSID_ENV",
                    prefix
                    + "env_cmd=/bin/env\n"
                    + 'timeout 5 /usr/bin/setsid --wait "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "setsid-variable-busybox-env",
                    'cmd="printf RUNTIME_SETSID_BUSYBOX_ENV"\n'
                    f'busybox_cmd="{busybox}"\n'
                    "ENV_APPLET=env\n"
                    '/usr/bin/setsid --wait "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_SETSID_BUSYBOX_ENV",
                    prefix
                    + f"busybox_cmd={busybox}\n"
                    + "ENV_APPLET=env\n"
                    + '/usr/bin/setsid --wait "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "timeout-setsid-variable-busybox-env",
                    'cmd="printf RUNTIME_TIMEOUT_SETSID_BUSYBOX_ENV"\n'
                    f'busybox_cmd="{busybox}"\n'
                    "ENV_APPLET=env\n"
                    'timeout 5 /usr/bin/setsid --wait "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_TIMEOUT_SETSID_BUSYBOX_ENV",
                    prefix
                    + f"busybox_cmd={busybox}\n"
                    + "ENV_APPLET=env\n"
                    + 'timeout 5 /usr/bin/setsid --wait "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                ),
            )

            for label, runtime_script, expected_stdout, semantic_script in cases:
                with self.subTest(case=label):
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "--noprofile",
                            "--norc",
                            "-eu",
                            "-o",
                            "pipefail",
                            "-c",
                            runtime_script,
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout, expected_stdout)
                    self.assertEqual(completed.stderr, "")
                    self.assertTrue(
                        publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                            semantic_script,
                            label=label,
                        )
                    )

    def test_setsid_literal_arguments_execute_without_false_positive(self):
        cases = (
            (
                "setsid-literal-printf",
                'message="SAFE_SETSID_LITERAL"\n'
                '/usr/bin/setsid --wait /usr/bin/printf "%s" "$message"\n',
                "SAFE_SETSID_LITERAL",
                'message="SAFE_SETSID_LITERAL"\n'
                '/usr/bin/setsid --wait /usr/bin/printf "%s" "$message"\n',
            ),
            (
                "setsid-literal-env-exec",
                'message="SAFE_SETSID_ENV_LITERAL"\n'
                '/usr/bin/setsid --wait /bin/env -i /usr/bin/printf "%s" "$message"\n',
                "SAFE_SETSID_ENV_LITERAL",
                'message="SAFE_SETSID_ENV_LITERAL"\n'
                '/usr/bin/setsid --wait /bin/env -i /usr/bin/printf "%s" "$message"\n',
            ),
            (
                "timeout-setsid-literal-env-exec",
                'message="SAFE_TIMEOUT_SETSID_ENV_LITERAL"\n'
                'timeout 5 /usr/bin/setsid --wait /bin/env -i /usr/bin/printf "%s" "$message"\n',
                "SAFE_TIMEOUT_SETSID_ENV_LITERAL",
                'message="SAFE_TIMEOUT_SETSID_ENV_LITERAL"\n'
                'timeout 5 /usr/bin/setsid --wait /bin/env -i /usr/bin/printf "%s" "$message"\n',
            ),
        )

        for label, runtime_script, expected_stdout, semantic_script in cases:
            with self.subTest(case=label):
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "--noprofile",
                        "--norc",
                        "-eu",
                        "-o",
                        "pipefail",
                        "-c",
                        runtime_script,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, expected_stdout)
                self.assertEqual(completed.stderr, "")
                self.assertFalse(
                    publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                        semantic_script,
                        label=label,
                    )
                )

    def test_unknown_literal_wrappers_execute_hidden_env_surfaces_and_detector_rejects_them(
        self,
    ):
        prefix = (
            'ROOT=/mnt\n'
            'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
        )
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="unknown-wrapper-busybox-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            lock_file = sandbox / "lock"
            lock_file.write_text("", encoding="ascii")
            busybox = sandbox / "busybox"
            busybox.write_text(
                "#!/bin/sh\n"
                'if [ "$1" != "env" ]; then\n'
                "  exit 125\n"
                "fi\n"
                "shift\n"
                'exec /bin/env "$@"\n',
                encoding="ascii",
            )
            busybox.chmod(0o755)

            cases = (
                (
                    "nohup-variable-env",
                    'cmd="printf RUNTIME_NOHUP_ENV"\n'
                    "env_cmd=/bin/env\n"
                    'nohup "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_NOHUP_ENV",
                    prefix
                    + "env_cmd=/bin/env\n"
                    + 'nohup "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "nohup-setsid-variable-env",
                    'cmd="printf RUNTIME_NOHUP_SETSID_ENV"\n'
                    "env_cmd=/bin/env\n"
                    'nohup /usr/bin/setsid --wait "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_NOHUP_SETSID_ENV",
                    prefix
                    + "env_cmd=/bin/env\n"
                    + 'nohup /usr/bin/setsid --wait "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "taskset-variable-env",
                    'cmd="printf RUNTIME_TASKSET_ENV"\n'
                    "env_cmd=/bin/env\n"
                    'taskset -c 0 "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_TASKSET_ENV",
                    prefix
                    + "env_cmd=/bin/env\n"
                    + 'taskset -c 0 "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "ionice-variable-env",
                    'cmd="printf RUNTIME_IONICE_ENV"\n'
                    "env_cmd=/bin/env\n"
                    'ionice -c3 "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_IONICE_ENV",
                    prefix
                    + "env_cmd=/bin/env\n"
                    + 'ionice -c3 "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "flock-variable-env",
                    'cmd="printf RUNTIME_FLOCK_ENV"\n'
                    "env_cmd=/bin/env\n"
                    f'flock -n "{lock_file}" "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_FLOCK_ENV",
                    prefix
                    + "env_cmd=/bin/env\n"
                    + f'flock -n "{lock_file}" "$env_cmd" -S "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "nohup-variable-busybox-env",
                    'cmd="printf RUNTIME_NOHUP_BUSYBOX_ENV"\n'
                    f'busybox_cmd="{busybox}"\n'
                    "ENV_APPLET=env\n"
                    'nohup "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_NOHUP_BUSYBOX_ENV",
                    prefix
                    + f"busybox_cmd={busybox}\n"
                    + "ENV_APPLET=env\n"
                    + 'nohup "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "nohup-setsid-variable-busybox-env",
                    'cmd="printf RUNTIME_NOHUP_SETSID_BUSYBOX_ENV"\n'
                    f'busybox_cmd="{busybox}"\n'
                    "ENV_APPLET=env\n"
                    'nohup /usr/bin/setsid --wait "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_NOHUP_SETSID_BUSYBOX_ENV",
                    prefix
                    + f"busybox_cmd={busybox}\n"
                    + "ENV_APPLET=env\n"
                    + 'nohup /usr/bin/setsid --wait "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "taskset-variable-busybox-env",
                    'cmd="printf RUNTIME_TASKSET_BUSYBOX_ENV"\n'
                    f'busybox_cmd="{busybox}"\n'
                    "ENV_APPLET=env\n"
                    'taskset -c 0 "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_TASKSET_BUSYBOX_ENV",
                    prefix
                    + f"busybox_cmd={busybox}\n"
                    + "ENV_APPLET=env\n"
                    + 'taskset -c 0 "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "ionice-variable-busybox-env",
                    'cmd="printf RUNTIME_IONICE_BUSYBOX_ENV"\n'
                    f'busybox_cmd="{busybox}"\n'
                    "ENV_APPLET=env\n"
                    'ionice -c3 "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_IONICE_BUSYBOX_ENV",
                    prefix
                    + f"busybox_cmd={busybox}\n"
                    + "ENV_APPLET=env\n"
                    + 'ionice -c3 "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "flock-variable-busybox-env",
                    'cmd="printf RUNTIME_FLOCK_BUSYBOX_ENV"\n'
                    f'busybox_cmd="{busybox}"\n'
                    "ENV_APPLET=env\n"
                    f'flock -n "{lock_file}" "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                    "RUNTIME_FLOCK_BUSYBOX_ENV",
                    prefix
                    + f"busybox_cmd={busybox}\n"
                    + "ENV_APPLET=env\n"
                    + f'flock -n "{lock_file}" "$busybox_cmd" "$ENV_APPLET" --split-string "/bin/bash -c \\"$cmd\\""\n',
                ),
                (
                    "flock-nonliteral-command-string",
                    'cmd="printf RUNTIME_FLOCK_COMMAND_STRING"\n'
                    f'flock -n "{lock_file}" -c "$cmd"\n',
                    "RUNTIME_FLOCK_COMMAND_STRING",
                    prefix + f'flock -n "{lock_file}" -c "$cmd"\n',
                ),
            )

            for label, runtime_script, expected_stdout, semantic_script in cases:
                with self.subTest(case=label):
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "--noprofile",
                            "--norc",
                            "-eu",
                            "-o",
                            "pipefail",
                            "-c",
                            runtime_script,
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout, expected_stdout)
                    self.assertEqual(completed.stderr, "")
                    self.assertTrue(
                        publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                            semantic_script,
                            label=label,
                        )
                    )

    def test_unknown_literal_wrappers_keep_literal_command_arguments_semantic_clean(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="unknown-wrapper-flock-safe-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            lock_file = sandbox / "lock"
            lock_file.write_text("", encoding="ascii")
            env_cmd = "/bin/env"
            cases = (
                (
                    "nohup-literal-command-data",
                    f'env_cmd="{env_cmd}"\n'
                    'nohup /usr/bin/printf "%s %s" "$env_cmd" "-S"\n',
                    "/bin/env -S",
                    f'env_cmd="{env_cmd}"\n'
                    'nohup /usr/bin/printf "%s %s" "$env_cmd" "-S"\n',
                ),
                (
                    "nohup-setsid-literal-command-data",
                    f'env_cmd="{env_cmd}"\n'
                    'nohup /usr/bin/setsid --wait /usr/bin/printf "%s %s" "$env_cmd" "-S"\n',
                    "/bin/env -S",
                    f'env_cmd="{env_cmd}"\n'
                    'nohup /usr/bin/setsid --wait /usr/bin/printf "%s %s" "$env_cmd" "-S"\n',
                ),
                (
                    "taskset-literal-command-data",
                    f'env_cmd="{env_cmd}"\n'
                    'taskset -c 0 /usr/bin/printf "%s %s" "$env_cmd" "-S"\n',
                    "/bin/env -S",
                    f'env_cmd="{env_cmd}"\n'
                    'taskset -c 0 /usr/bin/printf "%s %s" "$env_cmd" "-S"\n',
                ),
                (
                    "ionice-literal-command-data",
                    f'env_cmd="{env_cmd}"\n'
                    'ionice -c3 /usr/bin/printf "%s %s" "$env_cmd" "-S"\n',
                    "/bin/env -S",
                    f'env_cmd="{env_cmd}"\n'
                    'ionice -c3 /usr/bin/printf "%s %s" "$env_cmd" "-S"\n',
                ),
                (
                    "flock-literal-command-data",
                    f'env_cmd="{env_cmd}"\n'
                    f'flock -n "{lock_file}" /usr/bin/printf "%s %s" "$env_cmd" "-S"\n',
                    "/bin/env -S",
                    f'env_cmd="{env_cmd}"\n'
                    f'flock -n "{lock_file}" /usr/bin/printf "%s %s" "$env_cmd" "-S"\n',
                ),
                (
                    "flock-literal-command-string",
                    f'flock -n "{lock_file}" -c "printf SAFE_FLOCK_COMMAND_STRING"\n',
                    "SAFE_FLOCK_COMMAND_STRING",
                    f'flock -n "{lock_file}" -c "printf SAFE_FLOCK_COMMAND_STRING"\n',
                ),
            )

            for label, runtime_script, expected_stdout, semantic_script in cases:
                with self.subTest(case=label):
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "--noprofile",
                            "--norc",
                            "-eu",
                            "-o",
                            "pipefail",
                            "-c",
                            runtime_script,
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(completed.stdout, expected_stdout)
                    self.assertEqual(completed.stderr, "")
                    self.assertFalse(
                        publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                            semantic_script,
                            label=label,
                        )
                    )

    def test_substitution_bodies_execute_and_detector_rejects_them(self):
        self.assertFalse(workflow_has_supervisor_parent_readonly_remount(self.text))
        cases = (
            (
                "assignment-command-substitution-direct",
                'ignored=$(/usr/bin/printf RUNTIME_ASSIGN_DIRECT)\n'
                'printf "%s" "$ignored"\n',
                "RUNTIME_ASSIGN_DIRECT",
                'root=/mnt\n'
                'ignored=$(/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$root/supervisor")\n',
            ),
            (
                "assignment-command-substitution-env-shell",
                'ignored=$(/bin/env -S "/bin/bash -c \\"printf RUNTIME_ASSIGN_ENV\\"")\n'
                'printf "%s" "$ignored"\n',
                "RUNTIME_ASSIGN_ENV",
                'ignored=$(/bin/env -S "/bin/bash -c \\"/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor\\"")\n',
            ),
            (
                "assignment-command-substitution-bash-shell",
                'ignored=$(/bin/bash -c "printf RUNTIME_ASSIGN_BASH")\n'
                'printf "%s" "$ignored"\n',
                "RUNTIME_ASSIGN_BASH",
                'ignored=$(/bin/bash -c "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor")\n',
            ),
            (
                "nested-command-substitution-env-shell",
                'ignored="$(/usr/bin/printf "%s" "$(/bin/env -S "/bin/bash -c \\"printf RUNTIME_NESTED_ENV\\"")")"\n'
                'printf "%s" "$ignored"\n',
                "RUNTIME_NESTED_ENV",
                'ignored="$(/usr/bin/printf "%s" "$(/bin/env -S "/bin/bash -c \\"/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor\\"")")"\n',
            ),
            (
                "backtick-command-substitution-bash-shell",
                'ignored=`/bin/bash -c "printf RUNTIME_BACKTICK_BASH"`\n'
                'printf "%s" "$ignored"\n',
                "RUNTIME_BACKTICK_BASH",
                'ignored=`/bin/bash -c "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor"`\n',
            ),
            (
                "input-process-substitution-direct",
                'cat <(/usr/bin/printf RUNTIME_INPUT_PROCESS_DIRECT)\n',
                "RUNTIME_INPUT_PROCESS_DIRECT",
                'root=/mnt\n'
                'cat <(/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$root/supervisor") >/dev/null\n',
            ),
            (
                "input-process-substitution-env-shell",
                'cat <(/bin/env -S "/bin/bash -c \\"printf RUNTIME_INPUT_PROCESS_ENV\\"")\n',
                "RUNTIME_INPUT_PROCESS_ENV",
                'cat <(/bin/env -S "/bin/bash -c \\"/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor\\"") >/dev/null\n',
            ),
            (
                "output-process-substitution-bash-shell",
                ': > >(/bin/bash -c "printf RUNTIME_OUTPUT_PROCESS_BASH")\n',
                "RUNTIME_OUTPUT_PROCESS_BASH",
                ': > >(/bin/bash -c "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor")\n',
            ),
        )

        for label, runtime_script, expected_stdout, semantic_script in cases:
            with self.subTest(case=label):
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "--noprofile",
                        "--norc",
                        "-eu",
                        "-o",
                        "pipefail",
                        "-c",
                        runtime_script,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, expected_stdout)
                self.assertEqual(completed.stderr, "")
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                        semantic_script,
                        label=label,
                    )
                )

    def test_quoted_and_escaped_substitution_literals_do_not_execute_or_trigger_detection(
        self,
    ):
        cases = (
            (
                "single-quoted-command-substitution",
                "printf '%s' '$(/usr/bin/printf SHOULD_NOT_RUN)'\n",
                '$(/usr/bin/printf SHOULD_NOT_RUN)',
                "printf '%s' '$(/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor)'\n",
            ),
            (
                "escaped-command-substitution",
                'printf "%s" "\\$(/usr/bin/printf SHOULD_NOT_RUN_ESCAPED)"\n',
                '$(/usr/bin/printf SHOULD_NOT_RUN_ESCAPED)',
                'printf "%s" "\\$(/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor)"\n',
            ),
            (
                "single-quoted-backtick-substitution",
                "printf '%s' '`/usr/bin/printf SHOULD_NOT_RUN_BACKTICK`'\n",
                '`/usr/bin/printf SHOULD_NOT_RUN_BACKTICK`',
                "printf '%s' '`/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor`'\n",
            ),
            (
                "single-quoted-input-process-substitution",
                "printf '%s' '<(/usr/bin/printf SHOULD_NOT_RUN_PROCESS)'\n",
                '<(/usr/bin/printf SHOULD_NOT_RUN_PROCESS)',
                "printf '%s' '<(/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor)'\n",
            ),
            (
                "double-quoted-output-process-substitution",
                'printf "%s" ">(/usr/bin/printf SHOULD_NOT_RUN_OUTPUT_PROCESS)"\n',
                '>(/usr/bin/printf SHOULD_NOT_RUN_OUTPUT_PROCESS)',
                'printf "%s" ">(/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor)"\n',
            ),
        )

        for label, runtime_script, expected_stdout, semantic_script in cases:
            with self.subTest(case=label):
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "--noprofile",
                        "--norc",
                        "-eu",
                        "-o",
                        "pipefail",
                        "-c",
                        runtime_script,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, expected_stdout)
                self.assertEqual(completed.stderr, "")
                self.assertFalse(
                    publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                        semantic_script,
                        label=label,
                    )
                )

    def test_structural_prefix_env_split_string_repros_execute_and_detector_rejects_them(
        self,
    ):
        prefix = (
            'ROOT=/mnt\n'
            'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
        )
        cases = (
            (
                "inline-else",
                'if false; then :; else /bin/env -S "/bin/bash -c \\"printf RUNTIME_INLINE_ELSE\\""; fi\n',
                "RUNTIME_INLINE_ELSE",
                prefix
                + 'if false; then :; else /bin/env -S "/bin/bash -c \\"$cmd\\""; fi\n',
            ),
            (
                "brace-group",
                '{ /bin/env -S "/bin/bash -c \\"printf RUNTIME_BRACE_GROUP\\""; }\n',
                "RUNTIME_BRACE_GROUP",
                prefix + '{ /bin/env -S "/bin/bash -c \\"$cmd\\""; }\n',
            ),
            (
                "nested-else-brace-group",
                'if false; then :; else { /bin/env -S "/bin/bash -c \\"printf RUNTIME_NESTED_ELSE_BRACE\\""; }; fi\n',
                "RUNTIME_NESTED_ELSE_BRACE",
                prefix
                + 'if false; then :; else { /bin/env -S "/bin/bash -c \\"$cmd\\""; }; fi\n',
            ),
            (
                "then-body",
                'if true; then /bin/env -S "/bin/bash -c \\"printf RUNTIME_THEN_BODY\\""; fi\n',
                "RUNTIME_THEN_BODY",
                prefix + 'if true; then /bin/env -S "/bin/bash -c \\"$cmd\\""; fi\n',
            ),
            (
                "do-body",
                'for iteration in 1; do /bin/env -S "/bin/bash -c \\"printf RUNTIME_DO_BODY\\""; done\n',
                "RUNTIME_DO_BODY",
                prefix
                + 'for iteration in 1; do /bin/env -S "/bin/bash -c \\"$cmd\\""; done\n',
            ),
            (
                "subshell-else-group",
                'if false; then :; else ( /bin/env -S "/bin/bash -c \\"printf RUNTIME_SUBSHELL_GROUP\\""); fi\n',
                "RUNTIME_SUBSHELL_GROUP",
                prefix
                + 'if false; then :; else ( /bin/env -S "/bin/bash -c \\"$cmd\\""); fi\n',
            ),
            (
                "case-arm-brace-group",
                'case x in\n  *) { /bin/env -S "/bin/bash -c \\"printf RUNTIME_CASE_ARM_GROUP\\""; } ;;\nesac\n',
                "RUNTIME_CASE_ARM_GROUP",
                prefix
                + 'case x in\n  *) { /bin/env -S "/bin/bash -c \\"$cmd\\""; } ;;\nesac\n',
            ),
        )

        for label, runtime_script, expected_stdout, semantic_script in cases:
            with self.subTest(case=label):
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "--noprofile",
                        "--norc",
                        "-eu",
                        "-o",
                        "pipefail",
                        "-c",
                        runtime_script,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, expected_stdout)
                self.assertEqual(completed.stderr, "")
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                        semantic_script,
                        label=label,
                    )
                )

    def test_extglob_case_pattern_semantic_surface_strips_only_the_pattern_prefix(self):
        cases = (
            (
                "at-extglob-separated-env",
                '@(x)) /bin/env -S "/bin/bash -c \\"$cmd\\""',
                ("/bin/env", "-S", '/bin/bash -c "$cmd"'),
            ),
            (
                "bang-extglob-attached-bash",
                '!(x))/bin/bash -c "$cmd"',
                ("/bin/bash", "-c", "$cmd"),
            ),
            (
                "plus-extglob-attached-mount",
                "+(x))/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervisor",
                (
                    "/usr/bin/mount",
                    "-o",
                    "remount,ro,nosuid,nodev,noexec",
                    "/mnt/supervisor",
                ),
            ),
            (
                "question-extglob-attached-env",
                '?(x))/bin/env -S "/bin/bash -c \\"$cmd\\""',
                ("/bin/env", "-S", '/bin/bash -c "$cmd"'),
            ),
            (
                "star-extglob-attached-bash",
                '*(x))/bin/bash -c "$cmd"',
                ("/bin/bash", "-c", "$cmd"),
            ),
            (
                "nested-extglob-attached-bash",
                '!(@(x)))/bin/bash -c "$cmd"',
                ("/bin/bash", "-c", "$cmd"),
            ),
            (
                "extglob-attached-brace-group",
                '@(x)){ /bin/env -S "/bin/bash -c \\"$cmd\\""',
                ("/bin/env", "-S", '/bin/bash -c "$cmd"'),
            ),
        )

        for label, command_text, expected in cases:
            with self.subTest(case=label):
                tokens = publisher_shell_contract._semantic_surface_tokens(
                    publisher_shell_contract._parse_shell_tokens(
                        command_text,
                        label=label,
                    ),
                    label=label,
                )
                self.assertEqual(tuple(token.text for token in tokens), expected)

    def test_case_pattern_surface_parser_ignores_pure_closers_and_fails_closed_on_ambiguous_tokens(
        self,
    ):
        for closing in (")", "))", ")))"):
            with self.subTest(closing=closing):
                self.assertEqual(
                    publisher_shell_contract._semantic_surface_tokens(
                        publisher_shell_contract._parse_shell_tokens(
                            closing,
                            label=closing,
                        ),
                        label=closing,
                    ),
                    (),
                )

        for label, command_text in (
            (
                "ambiguous-double-close",
                'foo)) /bin/env -S "/bin/bash -c \\"$cmd\\""',
            ),
            (
                "unterminated-extglob-fragment",
                "@(x",
            ),
            (
                "unterminated-nested-extglob-fragment",
                "!(@(x)",
            ),
        ):
            with self.subTest(case=label):
                with self.assertRaisesRegex(ValueError, "case-pattern token differs"):
                    publisher_shell_contract._semantic_surface_tokens(
                        publisher_shell_contract._parse_shell_tokens(
                            command_text,
                            label=label,
                        ),
                        label=label,
                    )

    def test_unsupported_extglob_case_alternation_fails_closed(self):
        script = (
            'ROOT=/mnt\n'
            'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
            'case x in\n'
            '  @(x|y)) /bin/env -S "/bin/bash -c \\"$cmd\\""\n'
            '  ;;\n'
            'esac\n'
        )
        self.assertTrue(
            publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                script,
                label="unsupported-extglob-alternation",
            )
        )

    def test_extglob_case_arm_runtime_repros_execute_and_detector_rejects_them(self):
        prefix = (
            'ROOT=/mnt\n'
            'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
        )
        cases = (
            (
                "extglob-env-split-string",
                'cmd="printf RUNTIME_EXTGLOB_ENV"\n'
                'case x in\n'
                '  @(x))/bin/env -S "/bin/bash -c \\"$cmd\\""\n'
                '  ;;\n'
                'esac\n',
                "RUNTIME_EXTGLOB_ENV",
                prefix
                + 'case x in\n'
                + '  @(x))/bin/env -S "/bin/bash -c \\"$cmd\\""\n'
                + '  ;;\n'
                + 'esac\n',
            ),
            (
                "nested-extglob-bash-c",
                'cmd="printf RUNTIME_EXTGLOB_BASH"\n'
                'case x in\n'
                '  !(@(y)))/bin/bash -c "$cmd"\n'
                '  ;;\n'
                'esac\n',
                "RUNTIME_EXTGLOB_BASH",
                prefix
                + 'case x in\n'
                + '  !(@(y)))/bin/bash -c "$cmd"\n'
                + '  ;;\n'
                + 'esac\n',
            ),
            (
                "direct-extglob-mount-surface",
                'case x in\n'
                '  +(x))/usr/bin/printf RUNTIME_EXTGLOB_DIRECT\n'
                '  ;;\n'
                'esac\n',
                "RUNTIME_EXTGLOB_DIRECT",
                prefix
                + 'case x in\n'
                + '  +(x))/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor\n'
                + '  ;;\n'
                + 'esac\n',
            ),
        )

        self.assertFalse(workflow_has_supervisor_parent_readonly_remount(self.text))

        for label, runtime_script, expected_stdout, semantic_script in cases:
            with self.subTest(case=label):
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "--noprofile",
                        "--norc",
                        "-O",
                        "extglob",
                        "-eu",
                        "-o",
                        "pipefail",
                        "-c",
                        runtime_script,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, expected_stdout)
                self.assertEqual(completed.stderr, "")
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                        semantic_script,
                        label=label,
                    )
                )

    def test_inline_function_body_env_split_string_runtime_fails_closed(self):
        runtime_script = (
            'wrapped() { /bin/env -S "/bin/bash -c \\"printf RUNTIME_INLINE_FUNCTION\\""; }\n'
            "wrapped\n"
        )
        semantic_script = (
            'ROOT=/mnt\n'
            'cmd="/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ${ROOT}/supervisor"\n'
            'wrapped() { /bin/env -S "/bin/bash -c \\"$cmd\\""; }\n'
            "wrapped\n"
        )
        completed = subprocess.run(
            [
                "/bin/bash",
                "--noprofile",
                "--norc",
                "-eu",
                "-o",
                "pipefail",
                "-c",
                runtime_script,
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "RUNTIME_INLINE_FUNCTION")
        self.assertEqual(completed.stderr, "")
        self.assertTrue(
            publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                semantic_script,
                label="inline-function-body",
            )
        )

    def test_unquoted_glob_brace_and_tilde_shell_surfaces_fail_closed_while_quoted_literals_stay_distinct(
        self,
    ):
        rejected = {
            "question-target-alias": "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/superviso?\n",
            "star-target-alias": "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/supervis*\n",
            "bracket-target-alias": "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/superviso[r]\n",
            "brace-target-alias": "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/superviso{r,rs}\n",
            "tilde-target-alias": "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec ~/supervisor\n",
            "globbed-shell-interpreter": 'cmd="printf ok"\n/bin/ba?h -c "$cmd"\n',
            "globbed-env-wrapper": '/bin/e?v --split-string "/bin/bash -c \\"printf ok\\""\n',
            "bracket-busybox-applet-wrapper": '/bin/busybox e[n]v --split-string "/bin/bash -c \\"printf ok\\""\n',
            "extglob-target-alias": "shopt -s extglob\n/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt/superviso@(r)\n",
        }
        accepted = {
            "quoted-question-target": '/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "/mnt/superviso?"\n',
            "quoted-brace-target": "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec '/mnt/superviso{r,rs}'\n",
            "quoted-globbed-env-wrapper": '"/bin/e?v" --split-string "/bin/bash -c \\"printf ok\\""\n',
            "escaped-shell-question": '/bin/ba\\?h -c "printf ok"\n',
        }

        self.assertFalse(workflow_has_supervisor_parent_readonly_remount(self.text))

        for label, script in rejected.items():
            with self.subTest(case=label):
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                        script,
                        label=label,
                    )
                )

        for label, script in accepted.items():
            with self.subTest(case=label):
                self.assertFalse(
                    publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                        script,
                        label=label,
                    )
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
                reference_run = reference_literal_run_step_script(changed_step)
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

    def test_supervisor_membership_runtime_accepts_wrapper_and_checker_only(
        self,
    ):
        full_script = named_step_run_script(
            self.text,
            "Build candidate in isolated namespace and stage public inputs",
        )
        builder = (
            publisher_shell_contract.builder_isolation_shell_source(
                full_script,
                label="membership checker builder",
            )
        )
        checker = dict(
            publisher_shell_contract.raw_patch_release_parser_sources(
                builder
            )
        )[
            publisher_shell_contract.PATCH_RELEASE_MEMBERSHIP_CHECKER_NAME
        ]
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="supervisor-cgroup-view-",
            dir=artifact_root,
        ) as temporary:
            supervisor = Path(temporary) / "supervisor"
            supervisor.mkdir(mode=0o700)
            membership_path = supervisor / "cgroup.procs"
            runtime_checker = checker.replace(
                '"/mnt/supervisor/cgroup/cgroup.procs"',
                repr(str(membership_path)),
                1,
            )

            def run(payload_factory):
                membership_path.unlink(missing_ok=True)
                os.mkfifo(membership_path)
                expected_pid = os.getpid()
                process = subprocess.Popen(
                    [
                        "/usr/bin/python3",
                        "-I",
                        "-S",
                        "-c",
                        runtime_checker,
                        str(expected_pid),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                payload = payload_factory(expected_pid, process.pid)
                with membership_path.open("wb", buffering=0) as stream:
                    stream.write(payload)
                stdout, stderr = process.communicate(timeout=5)
                return subprocess.CompletedProcess(
                    process.args,
                    process.returncode,
                    stdout,
                    stderr,
                )

            for order in ((0, 1), (1, 0)):
                with self.subTest(valid_order=order):
                    accepted = run(
                        lambda expected, checker_pid, order=order: (
                            f"{(expected, checker_pid)[order[0]]}\n"
                            f"{(expected, checker_pid)[order[1]]}\n"
                        ).encode("ascii")
                    )
                    self.assertEqual(
                        accepted.returncode,
                        0,
                        accepted.stderr,
                    )
                    self.assertEqual(accepted.stdout, "")
                    self.assertEqual(accepted.stderr, "")

            invalid_payloads = {
                "empty": lambda _expected, _checker: b"",
                "empty-line": lambda _expected, _checker: b"\n",
                "nonnumeric": lambda expected, _checker: (
                    f"{expected}\nnot-a-pid\n".encode("ascii")
                ),
                "whitespace": lambda expected, _checker: (
                    f"{expected}\n \n".encode("ascii")
                ),
                "plus-sign": lambda expected, checker_pid: (
                    f"{expected}\n+{checker_pid}\n".encode("ascii")
                ),
                "minus-sign": lambda expected, checker_pid: (
                    f"{expected}\n-{checker_pid}\n".encode("ascii")
                ),
                "zero": lambda expected, _checker: (
                    f"{expected}\n0\n".encode("ascii")
                ),
                "leading-whitespace": lambda expected, checker_pid: (
                    f"{expected}\n {checker_pid}\n".encode("ascii")
                ),
                "trailing-whitespace": lambda expected, checker_pid: (
                    f"{expected}\n{checker_pid} \n".encode("ascii")
                ),
                "missing-newline": lambda expected, checker_pid: (
                    f"{expected}\n{checker_pid}".encode("ascii")
                ),
                "duplicate-wrapper": lambda expected, _checker: (
                    f"{expected}\n{expected}\n".encode("ascii")
                ),
                "duplicate-checker": lambda _expected, checker_pid: (
                    f"{checker_pid}\n{checker_pid}\n".encode("ascii")
                ),
                "external-only": lambda _expected, _checker: (
                    f"{os.getppid()}\n{os.getpid() + 100000}\n".encode(
                        "ascii"
                    )
                ),
                "extra-after": lambda expected, checker_pid: (
                    f"{expected}\n{checker_pid}\n{os.getppid()}\n".encode(
                        "ascii"
                    )
                ),
                "extra-before": lambda expected, checker_pid: (
                    f"{os.getppid()}\n{expected}\n{checker_pid}\n".encode(
                        "ascii"
                    )
                ),
                "oversized": lambda _expected, _checker: b"1" * 4097,
            }
            for name, payload_factory in invalid_payloads.items():
                with self.subTest(case=name):
                    rejected = run(payload_factory)
                    self.assertEqual(
                        rejected.returncode,
                        125,
                        rejected.stderr,
                    )
                    self.assertEqual(rejected.stdout, "")
                    self.assertEqual(rejected.stderr, "")

            failed_workflow = subprocess.check_output(
                [
                    "git",
                    "--no-pager",
                    "show",
                    f"{FAILING_MASTER_5779}:.github/workflows/build.yml",
                ],
                cwd=ROOT,
                text=True,
            )
            failed_script = named_step_run_script(
                failed_workflow,
                "Build candidate in isolated namespace and stage public inputs",
            )
            failed_start = failed_script.index(
                'cgroup_members="$(LC_ALL=C /usr/bin/sort -n'
            )
            failed_marker = 'test "$cgroup_members" = "$$"'
            failed_end = (
                failed_script.index(failed_marker, failed_start)
                + len(failed_marker)
            )
            failed_membership_check = failed_script[
                failed_start:failed_end
            ]
            fifo = membership_path
            fifo.unlink()
            os.mkfifo(fifo)
            old_process = subprocess.Popen(
                [
                    "/bin/bash",
                    "-c",
                    'supervisor_cgroup="$1"\n'
                    + failed_membership_check,
                    "--",
                    str(supervisor),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                start_new_session=True,
            )
            try:
                sort_pid = None
                for _attempt in range(200):
                    completed = subprocess.run(
                        ["/usr/bin/ps", "-eo", "sid=,pid=,comm="],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                    for line in completed.stdout.splitlines():
                        sid_text, pid_text, command = line.split(
                            maxsplit=2
                        )
                        if (
                            int(sid_text) == old_process.pid
                            and command == "sort"
                        ):
                            sort_pid = int(pid_text)
                            break
                    if sort_pid is not None:
                        break
                    time.sleep(0.01)
                self.assertIsNotNone(sort_pid)
                with fifo.open("w", encoding="ascii") as stream:
                    stream.write(f"{old_process.pid}\n{sort_pid}\n")
                stdout, stderr = old_process.communicate(timeout=5)
                self.assertEqual(old_process.returncode, 1)
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, "")
            finally:
                if old_process.poll() is None:
                    os.killpg(old_process.pid, signal.SIGKILL)
                    old_process.wait(timeout=5)

    def test_raw_cgroup_membership_reads_are_rejected_with_safe_line_present(self):
        self.assertFalse(
            workflow_has_raw_builder_cgroup_membership_read(self.text)
        )
        builder = builder_isolation_shell_source(self.text)
        for unrelated in (
            "unrelated=(one two)\n"
            'printf "%s\\n" "${unrelated[0]}"\n',
            "declare -a unrelated=(one)\n"
            "unrelated[0]+=two\n"
            'printf "%s\\n" "${unrelated[@]}"\n',
            "declare -A unrelated=([key]=value)\n"
            'printf "%s\\n" "${unrelated[key]}"\n',
        ):
            with self.subTest(unrelated_array=unrelated.splitlines()[0]):
                self.assertFalse(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        builder + "\n" + unrelated,
                        label="unrelated array control",
                    )
                )
        for unrelated_positional in (
            'printf "%s\\n" "$2"\n',
            'printf "%s\\n" "${2:-fallback}"\n',
            'other="$2"\nprintf "%s\\n" "$other"\n',
        ):
            with self.subTest(
                unrelated_positional=unrelated_positional.splitlines()[0]
            ):
                self.assertFalse(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        builder + "\n" + unrelated_positional,
                        label="unrelated positional control",
                    )
                )
        for unrelated_wrapped in (
            "command -v unset\n",
            "command -V supervisor_cgroup\n",
            "command -v alias\n",
            "command -V shopt\n",
            "command -pv supervisor_cgroup\n",
            "command -vp supervisor_cgroup\n",
            "command -pV supervisor_cgroup\n",
            "command -Vp supervisor_cgroup\n",
            "command -ppv supervisor_cgroup\n",
            "command -vvp supervisor_cgroup\n",
            "command -pVp supervisor_cgroup\n",
            "command -pvV supervisor_cgroup\n",
            "command -pv -- supervisor_cgroup\n",
            "command -p -v supervisor_cgroup\n",
            "command -v -p supervisor_cgroup\n",
            "command -- printf '%s\\n' unrelated\n",
            "command -p -- printf '%s\\n' unrelated\n",
            "builtin printf '%s\\n' unrelated\n",
            "set -e\n",
            "set +e\n",
        ):
            with self.subTest(
                unrelated_wrapped=unrelated_wrapped.strip()
            ):
                self.assertFalse(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        builder + "\n" + unrelated_wrapped,
                        label="unrelated wrapped control",
                    )
                )
        for fixed_literal in (
            "target='$(printf supervisor_cgroup)'\n"
            'printf "%s\\n" "$target"\n',
            "target='supervisor_cgrou?'\n"
            'printf "%s\\n" "$target"\n',
            "target='supervisor_{cgroup,other}'\n"
            'printf "%s\\n" "$target"\n',
        ):
            with self.subTest(fixed_literal=fixed_literal.splitlines()[0]):
                self.assertFalse(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        builder + "\n" + fixed_literal,
                        label="fixed literal alias control",
                    )
                )
        for ordinary_declaration in (
            "declare ordinary=value\n",
            "declare -x ordinary=value\n",
            "typeset -r ordinary=value\n",
            "local ordinary=value\n",
            "export ordinary=value\n",
            "readonly ordinary=value\n",
            "declare -- nameref=value\n",
        ):
            with self.subTest(
                ordinary_declaration=ordinary_declaration.strip()
            ):
                self.assertFalse(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        builder + "\n" + ordinary_declaration,
                        label="ordinary declaration control",
                    )
                )
        for unrelated_dispatch_target in (
            "target=ordinary\n"
            'printf -v "$target" %s value\n',
            "target=ordinary\n"
            'unset "$target"\n',
            "target=ordinary\n"
            'read "$target" < /dev/null\n',
            "target=ordinary\n"
            'declare "$target=value"\n',
            "option=errexit\n"
            'set -o "$option"\n',
            "option=nounset\n"
            'set +o "$option"\n',
        ):
            with self.subTest(
                unrelated_dispatch_target=(
                    unrelated_dispatch_target.splitlines()[0]
                )
            ):
                self.assertFalse(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        builder + "\n" + unrelated_dispatch_target,
                        label="unrelated dispatch target control",
                    )
                )
        for label, changed in generate_safe_declaration_alias_controls(
            self.text
        ):
            with self.subTest(safe_declaration_alias=label):
                self.assertFalse(
                    workflow_has_raw_builder_cgroup_membership_read(changed)
                )
        for label, changed in generate_quote_context_controls(self.text):
            with self.subTest(quote_context_control=label):
                self.assertFalse(
                    workflow_has_raw_builder_cgroup_membership_read(changed)
                )
        for label, changed in generate_ansi_arithmetic_controls(self.text):
            with self.subTest(ansi_arithmetic_control=label):
                self.assertFalse(
                    workflow_has_raw_builder_cgroup_membership_read(changed)
                )
        for label, changed in generate_raw_builder_cgroup_membership_mutations(
            self.text
        ):
            with self.subTest(variant=label):
                self.assertTrue(
                    workflow_has_raw_builder_cgroup_membership_read(changed)
                )
                self.assertIn(
                    "raw builder cgroup membership read differs",
                    publisher_boundary_errors(changed),
                )

    def test_checked_transport_outputs_are_reserved_after_production(self):
        for name in (
            "checked_supervisor_transport_output",
            "checked_runtime_transport_output",
        ):
            with self.subTest(preuse=name):
                script = (
                    "set -u\n"
                    f'printf "%s\\n" "${{#{name}[@]}}"\n'
                )
                completed = subprocess.run(
                    ["/bin/bash", "-c", script],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("unbound variable", completed.stderr)
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        script,
                        label=f"{name} pre-use runtime",
                    )
                )
        self.assertFalse(
            workflow_has_raw_builder_cgroup_membership_read(self.text)
        )
        supervisor = (
            "checked_supervisor_transport_output=(/dev/shm /dev)\n"
            "checked_supervisor_transport_output=(/dev)\n"
            "for ((index=${#checked_supervisor_transport_output[@]} - 1; "
            "index >= 0; index--)); do\n"
            '  printf "%s\\n" "${checked_supervisor_transport_output[index]}"\n'
            "done\n"
            'test "${#checked_supervisor_transport_output[@]}" -eq 1\n'
            'test "${checked_supervisor_transport_output[0]}" = /dev\n'
        )
        completed = subprocess.run(
            ["/bin/bash", "-c", supervisor],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "/dev\n")
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                supervisor,
                label="supervisor transport replacement runtime",
            )
        )

        runtime = (
            "checked_runtime_transport_output=(/unexpected rw)\n"
            "checked_runtime_transport_output=()\n"
            'test "$(( ${#checked_runtime_transport_output[@]} % 2 ))" -eq 0\n'
            "for ((index=0; "
            "index < ${#checked_runtime_transport_output[@]}; "
            "index+=2)); do\n"
            "  false\n"
            "done\n"
        )
        completed = subprocess.run(
            ["/bin/bash", "-c", runtime],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                runtime,
                label="runtime transport replacement runtime",
            )
        )

        with (
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_patch_release_run_script_identity",
            ),
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_builder_isolation_shell_identity",
            ),
        ):
            for label, changed in generate_reserved_transport_output_mutations(
                self.text
            ):
                with self.subTest(reserved_transport=label):
                    self.assertTrue(
                        workflow_has_raw_builder_cgroup_membership_read(
                            changed
                        )
                    )
                    self.assertIn(
                        "raw builder cgroup membership read differs",
                        publisher_boundary_errors(changed),
                    )

    def test_executable_prefixes_cannot_hide_transport_writers(self):
        writer = (
            "read -a checked_supervisor_transport_output <<< /dev"
        )
        cases = (
            ("time", f"time {writer}"),
            ("time-posix", f"time -p {writer}"),
            ("time-end-options", f"time -- {writer}"),
            ("time-posix-end-options", f"time -p -- {writer}"),
            ("negated-time", f"! time {writer}"),
            ("case-spaced", f"case x in\nx) {writer}\n;;\nesac"),
            ("case-attached", f"case x in\nx){writer}\n;;\nesac"),
            (
                "case-time",
                f"case x in\nx) time -p -- {writer}\n;;\nesac",
            ),
        )
        for label, mutation in cases:
            with self.subTest(prefix=label):
                script = (
                    "checked_supervisor_transport_output=(/dev/shm /dev)\n"
                    + mutation
                    + "\n"
                    'test "${#checked_supervisor_transport_output[@]}" -eq 1\n'
                    'test "${checked_supervisor_transport_output[0]}" = /dev\n'
                )
                completed = subprocess.run(
                    ["/bin/bash", "-c", script],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        script,
                        label=label,
                    )
                )

        for command in (
            "time true",
            "time -p true",
            "time -- true",
            "time -p -- true",
        ):
            with self.subTest(reviewed_time=command):
                completed = subprocess.run(
                    ["/bin/bash", "-c", command],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertFalse(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        command,
                        label=command,
                    )
                )

        for label, script in (
            (
                "dynamic-time-option",
                "option=\n"
                "time $option read -a "
                "checked_supervisor_transport_output <<< /dev\n",
            ),
            (
                "unsupported-time-option",
                "time -x read -a "
                "checked_supervisor_transport_output <<< /dev\n",
            ),
            (
                "ambiguous-case-pattern",
                "case x in\n"
                "x)) read -a checked_supervisor_transport_output "
                "<<< /dev\n"
                ";;\n"
                "esac\n",
            ),
        ):
            with self.subTest(closed_prefix=label):
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        script,
                        label=label,
                    )
                )

    def test_case_time_prefixes_reach_recursive_helper_analysis(self):
        helper_script = (
            "mutate() {\n"
            "  read -a checked_supervisor_transport_output <<< /dev\n"
            "}\n"
            "case x in\n"
            "x) time -p -- mutate\n"
            ";;\n"
            "esac\n"
        )
        completed = subprocess.run(
            [
                "/bin/bash",
                "-c",
                helper_script
                + 'test "${#checked_supervisor_transport_output[@]}" -eq 1\n'
                + 'test "${checked_supervisor_transport_output[0]}" = /dev\n',
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                helper_script,
                label="case time helper write",
            )
        )

        hidden_builder = self.text.replace(
            '        builder_main "$@"\n',
            "        case x in\n"
            "        x) time -p -- "
            "builder_main /sys/fs/cgroup/example\n"
            "        ;;\n"
            "        esac\n",
            1,
        )
        self.assertNotEqual(hidden_builder, self.text)
        with (
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_patch_release_run_script_identity",
            ),
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_builder_isolation_shell_identity",
            ),
        ):
            self.assertTrue(
                workflow_has_raw_builder_cgroup_membership_read(
                    hidden_builder
                )
            )

    def test_production_helper_inventory_is_exact_and_order_independent(self):
        self.assertFalse(
            workflow_has_raw_builder_cgroup_membership_read(self.text)
        )
        reordered = reordered_helper_inventory_control(self.text)
        self.assertFalse(
            workflow_has_raw_builder_cgroup_membership_read(reordered)
        )
        with (
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_patch_release_run_script_identity",
            ),
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_builder_isolation_shell_identity",
            ),
        ):
            reordered_errors = publisher_boundary_errors(reordered)
        self.assertNotIn(
            "raw builder cgroup membership read differs",
            reordered_errors,
        )
        for label, changed in generate_helper_inventory_mutations(self.text):
            with self.subTest(helper_inventory_mutation=label):
                self.assertTrue(
                    workflow_has_raw_builder_cgroup_membership_read(changed)
                )
                with (
                    mock.patch.object(
                        publisher_shell_contract,
                        "assert_reviewed_patch_release_run_script_identity",
                    ),
                    mock.patch.object(
                        publisher_shell_contract,
                        "assert_reviewed_builder_isolation_shell_identity",
                    ),
                ):
                    errors = publisher_boundary_errors(changed)
                self.assertIn(
                    "raw builder cgroup membership read differs",
                    errors,
                )

    def test_helper_background_topology_runs_asynchronously_and_is_rejected(
        self,
    ):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="helper-background-topology-",
            dir=artifact_root,
        ) as temporary:
            marker = Path(temporary) / "completed"
            script = (
                "helper() {\n"
                "  if true; then\n"
                "    /bin/sleep 0.2\n"
                '    printf "done\\n" > "$1"\n'
                "  fi &\n"
                "}\n"
                'helper "$1"\n'
                'test ! -e "$1"\n'
                "wait\n"
                'test -e "$1"\n'
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", script, "--", str(marker)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                marker.read_text(encoding="ascii"),
                "done\n",
            )
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    script,
                    label="helper background topology runtime",
                )
            )

    def test_helper_definition_must_execute_before_first_call(self):
        cases = (
            (
                "conditional",
                "if false; then\n"
                "  helper() {\n"
                "    true\n"
                "  }\n"
                "fi\n"
                "helper\n",
            ),
            (
                "subshell",
                "(\n"
                "  helper() {\n"
                "    true\n"
                "  }\n"
                ")\n"
                "helper\n",
            ),
            (
                "call-before-definition",
                "helper\n"
                "status=$?\n"
                "helper() {\n"
                "  true\n"
                "}\n"
                'exit "$status"\n',
            ),
        )
        for label, script in cases:
            with self.subTest(helper_definition=label):
                completed = subprocess.run(
                    ["/bin/bash", "-c", script],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 127)
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        script,
                        label=f"{label} helper definition runtime",
                    )
                )

    def test_indexed_alias_runtime_reaches_raw_membership_and_is_rejected(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="indexed-raw-cgroup-read-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            raw_cgroup = sandbox / "raw"
            supervisor_cgroup = sandbox / "supervisor"
            raw_cgroup.mkdir()
            supervisor_cgroup.mkdir()
            (raw_cgroup / "cgroup.procs").write_text(
                "raw-membership-marker\n",
                encoding="ascii",
            )
            (supervisor_cgroup / "cgroup.procs").write_text(
                "safe-membership-marker\n",
                encoding="ascii",
            )
            script = (
                'cgroup_path="$1"\n'
                'supervisor_cgroup="$2"\n'
                'mapfile -t safe < "$supervisor_cgroup/cgroup.procs"\n'
                'raw[0]="$cgroup_path"\n'
                'mapfile -t leaked < "${raw[0]}/cgroup.procs"\n'
                'printf "%s\\n" "${safe[0]}" "${leaked[0]}"\n'
            )
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    script,
                    "--",
                    str(raw_cgroup),
                    str(supervisor_cgroup),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout,
                "safe-membership-marker\nraw-membership-marker\n",
            )
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    script,
                    label="indexed raw cgroup runtime",
                )
            )

    def test_positional_alias_runtime_reaches_raw_membership_and_is_rejected(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="positional-raw-cgroup-read-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            raw_cgroup = sandbox / "raw"
            supervisor_cgroup = sandbox / "supervisor"
            raw_cgroup.mkdir()
            supervisor_cgroup.mkdir()
            (raw_cgroup / "cgroup.procs").write_text(
                "raw-positional-marker\n",
                encoding="ascii",
            )
            (supervisor_cgroup / "cgroup.procs").write_text(
                "safe-positional-marker\n",
                encoding="ascii",
            )
            script = (
                'supervisor_cgroup="$2"\n'
                'mapfile -t safe < "$supervisor_cgroup/cgroup.procs"\n'
                'raw_root="$1"\n'
                'mapfile -t leaked < "${raw_root%/}/cgroup.procs"\n'
                'printf "%s\\n" "${safe[0]}" "${leaked[0]}"\n'
            )
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    script,
                    "--",
                    str(raw_cgroup),
                    str(supervisor_cgroup),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout,
                "safe-positional-marker\nraw-positional-marker\n",
            )
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    script,
                    label="positional raw cgroup runtime",
                )
            )

    def test_dynamic_filename_runtime_brace_and_glob_reach_raw_membership(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="dynamic-raw-cgroup-read-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            raw_cgroup = sandbox / "raw"
            supervisor_cgroup = sandbox / "supervisor"
            raw_cgroup.mkdir()
            supervisor_cgroup.mkdir()
            (raw_cgroup / "cgroup.procs").write_text(
                "raw-dynamic-marker\n",
                encoding="ascii",
            )
            (raw_cgroup / "cgroup_procs").write_text(
                "brace-decoy-marker\n",
                encoding="ascii",
            )
            (supervisor_cgroup / "cgroup.procs").write_text(
                "safe-dynamic-marker\n",
                encoding="ascii",
            )
            script = (
                'supervisor_cgroup="$2"\n'
                'mapfile -t safe < "$supervisor_cgroup/cgroup.procs"\n'
                'brace="$(/bin/cat "$1"/cgroup{.,_}procs)"\n'
                'glob="$(/bin/cat "$1"/cgroup.proc?)"\n'
                'printf "%s\\n%s\\n%s\\n" '
                '"${safe[0]}" "$brace" "$glob"\n'
            )
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    script,
                    "--",
                    str(raw_cgroup),
                    str(supervisor_cgroup),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout,
                "safe-dynamic-marker\n"
                "raw-dynamic-marker\n"
                "brace-decoy-marker\n"
                "raw-dynamic-marker\n",
            )
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    script,
                    label="dynamic raw cgroup runtime",
                )
            )

    def test_dynamic_scalar_alias_runtime_mutates_supervisor_and_is_rejected(self):
        cases = (
            (
                'target="$(printf supervisor_cgroup)"\n'
                'unset "$target"\n'
                'printf "%s\\n" "$supervisor_cgroup"\n',
                "unbound variable",
            ),
            (
                "target=`printf supervisor_cgroup`\n"
                'unset "$target"\n'
                'printf "%s\\n" "$supervisor_cgroup"\n',
                "unbound variable",
            ),
            (
                "target_name=supervisor_cgroup\n"
                'target="${target_name@P}"\n'
                'unset "$target"\n'
                'printf "%s\\n" "$supervisor_cgroup"\n',
                "unbound variable",
            ),
            (
                'target="$(printf supervisor_cgroup)"\n'
                'printf -v "$target" %s /mnt/home\n'
                'test "$supervisor_cgroup" = /mnt/home\n',
                "",
            ),
        )
        for mutation, expected_stderr in cases:
            with self.subTest(mutation=mutation.splitlines()[0]):
                script = (
                    "set -u\n"
                    'supervisor_cgroup="/mnt/supervisor/cgroup"\n'
                    + mutation
                )
                completed = subprocess.run(
                    ["/bin/bash", "-c", script],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if expected_stderr:
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(expected_stderr, completed.stderr)
                else:
                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual(completed.stderr, "")
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        script,
                        label="dynamic scalar alias runtime",
                    )
                )

    def test_dynamic_dispatch_state_runtime_mutates_shell_and_is_rejected(self):
        cases = (
            (
                "target=PATH\n"
                'printf -v "$target" %s /dispatch-rewritten\n'
                'test "$PATH" = /dispatch-rewritten\n',
                "if command -v sh >/dev/null; then exit 90; fi\n",
                "PATH alias",
            ),
            (
                'target="$(printf PATH)"\n'
                'printf -v "$target" %s /dispatch-dynamic\n'
                'test "$PATH" = /dispatch-dynamic\n',
                "if command -v sh >/dev/null; then exit 90; fi\n",
                "PATH dynamic",
            ),
            (
                "target=BASH_ENV\n"
                'printf -v "$target" %s /dispatch-env\n'
                'test "$BASH_ENV" = /dispatch-env\n',
                "",
                "BASH_ENV",
            ),
            (
                'option="$(printf posix)"\n'
                'set -o "$option"\n'
                "[[ -o posix ]]\n",
                "",
                "posix",
            ),
            (
                "set +h\n"
                "option=hash\n"
                "option+=all\n"
                'set -o "$option"\n'
                "[[ -o hashall ]]\n",
                "",
                "hashall",
            ),
        )
        for script, observable, label in cases:
            with self.subTest(label=label):
                completed = subprocess.run(
                    ["/bin/bash", "-c", script + observable],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, "")
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        script,
                        label=f"{label} dispatch mutation runtime",
                    )
                )

    def test_dynamic_bash_env_target_runtime_dispatches_and_is_rejected(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="dynamic-bash-env-",
            dir=artifact_root,
        ) as temporary:
            startup = Path(temporary) / "startup.sh"
            startup.write_text(
                "printf 'bash-env-dispatched\\n'\n",
                encoding="ascii",
            )
            script = (
                "target=BASH_ENV\n"
                'printf -v "$target" %s "$1"\n'
                'export "$target"\n'
                "/bin/bash -c true\n"
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", script, "--", str(startup)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "bash-env-dispatched\n")
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    script,
                    label="BASH_ENV dispatch runtime",
                )
            )

    def test_variable_writers_retarget_raw_membership_and_are_rejected(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="alias-writer-raw-cgroup-",
            dir=artifact_root,
        ) as temporary:
            raw_cgroup = Path(temporary) / "raw"
            safe_cgroup = Path(temporary) / "safe"
            raw_cgroup.mkdir()
            safe_cgroup.mkdir()
            (raw_cgroup / "cgroup.procs").write_text(
                "hidden-raw-writer-marker\n",
                encoding="ascii",
            )
            (safe_cgroup / "cgroup.procs").write_text(
                "safe-writer-marker\n",
                encoding="ascii",
            )
            cases = (
                "raw_root=/safe\n"
                'printf -v raw_root %s "$1"\n'
                '/bin/cat "$raw_root"/cgroup.proc?\n',
                "raw_root=/safe\n"
                "target=raw_root\n"
                'command printf -v "$target" %s "$1"\n'
                '/bin/cat "$raw_root"/cgroup.proc?\n',
                "raw_root=/safe\n"
                'printf -v raw_root %q "$1"\n'
                '/bin/cat "$raw_root"/cgroup.proc?\n',
                "raw_root=/safe\n"
                'read raw_root <<< "$1"\n'
                '/bin/cat "$raw_root"/cgroup.proc?\n',
                "raw_root=(/safe)\n"
                'mapfile -t raw_root <<< "$1"\n'
                '/bin/cat "${raw_root[0]}"/cgroup.proc?\n',
                "raw_root=(/safe)\n"
                'readarray -t raw_root <<< "$1"\n'
                '/bin/cat "${raw_root[0]}"/cgroup.proc?\n',
                "raw_root=/safe\n"
                'declare raw_root="$1"\n'
                '/bin/cat "$raw_root"/cgroup.proc?\n',
                "raw_root=/safe\n"
                'raw_root="$1"\n'
                '/bin/cat "$raw_root"/cgroup.proc?\n',
                "raw_root=/safe\n"
                'LC_ALL=C printf -v raw_root %s "$1"\n'
                '/bin/cat "$raw_root"/cgroup.proc?\n',
                "raw_root=/safe\n"
                "if true; then\n"
                '  printf -v raw_root %s "$1"\n'
                "fi\n"
                '/bin/cat "$raw_root"/cgroup.proc?\n',
            )
            for script in cases:
                with self.subTest(writer=script.splitlines()[1]):
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            script,
                            "--",
                            str(raw_cgroup),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stderr,
                    )
                    self.assertEqual(
                        completed.stdout,
                        "hidden-raw-writer-marker\n",
                    )
                    self.assertTrue(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            script,
                            label="variable writer raw cgroup runtime",
                        )
                    )

            safe_script = (
                'raw_root="$1"\n'
                f'printf -v raw_root %s "{safe_cgroup}"\n'
                '/bin/cat "$raw_root"/cgroup.proc?\n'
            )
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    safe_script,
                    "--",
                    str(raw_cgroup),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "safe-writer-marker\n")
            parser_safe_script = (
                'raw_root="$1"\n'
                f'printf -v raw_root %s "{safe_cgroup}"\n'
                f'test "$raw_root" = "{safe_cgroup}"\n'
            )
            self.assertFalse(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    parser_safe_script,
                    label="exact safe printf alias writer control",
                )
            )

    def test_declaration_alias_writes_replace_or_taint_runtime_state(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="declaration-alias-writer-",
            dir=artifact_root,
        ) as temporary:
            raw_cgroup = Path(temporary) / "raw"
            safe_cgroup = Path(temporary) / "safe"
            raw_cgroup.mkdir()
            safe_cgroup.mkdir()
            (raw_cgroup / "cgroup.procs").write_text(
                "raw-declaration-marker\n",
                encoding="ascii",
            )
            (safe_cgroup / "cgroup.procs").write_text(
                "safe-declaration-marker\n",
                encoding="ascii",
            )
            for builtin in (
                "declare",
                "typeset",
                "export",
                "readonly",
                "local",
            ):
                with self.subTest(builtin=builtin, behavior="safe overwrite"):
                    safe_script = (
                        "overwrite() {\n"
                        f'  raw_root="{raw_cgroup}"\n'
                        "  target=raw_root\n"
                        f"  declaration={builtin}\n"
                        f'  "$declaration" "$target={safe_cgroup}"\n'
                        '  /bin/cat "$raw_root"/cgroup.proc?\n'
                        "}\n"
                        "overwrite\n"
                    )
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            safe_script,
                            "--",
                            str(raw_cgroup),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stderr,
                    )
                    self.assertEqual(
                        completed.stdout,
                        "safe-declaration-marker\n",
                    )
                    self.assertFalse(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            safe_script,
                            label=f"safe {builtin} alias overwrite",
                        )
                    )

                with self.subTest(builtin=builtin, behavior="raw overwrite"):
                    raw_script = (
                        f'cgroup_path="{raw_cgroup}"\n'
                        "overwrite() {\n"
                        '  raw_root=/safe\n'
                        "  target=raw_root\n"
                        f'  {builtin} "$target=$cgroup_path"\n'
                        '  /bin/cat "$raw_root"/cgroup.proc?\n'
                        "}\n"
                        "overwrite\n"
                    )
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            raw_script,
                            "--",
                            str(raw_cgroup),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stderr,
                    )
                    self.assertEqual(
                        completed.stdout,
                        "raw-declaration-marker\n",
                    )
                    self.assertTrue(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            raw_script,
                            label=f"raw {builtin} alias overwrite",
                        )
                    )

                with self.subTest(builtin=builtin, behavior="dynamic target"):
                    dynamic_script = (
                        "overwrite() {\n"
                        f'  raw_root="{raw_cgroup}"\n'
                        '  target="$(printf raw_root)"\n'
                        f'  {builtin} "$target={safe_cgroup}"\n'
                        '  /bin/cat "$raw_root"/cgroup.proc?\n'
                        "}\n"
                        "overwrite\n"
                    )
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            dynamic_script,
                            "--",
                            str(raw_cgroup),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stderr,
                    )
                    self.assertEqual(
                        completed.stdout,
                        "safe-declaration-marker\n",
                    )
                    self.assertTrue(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            dynamic_script,
                            label=f"dynamic {builtin} alias target",
                        )
                    )

    def test_user_helpers_compose_raw_membership_paths_and_are_rejected(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="helper-raw-membership-",
            dir=artifact_root,
        ) as temporary:
            raw_cgroup = Path(temporary) / "raw"
            safe_cgroup = Path(temporary) / "safe"
            raw_cgroup.mkdir()
            safe_cgroup.mkdir()
            (raw_cgroup / "cgroup.procs").write_text(
                "helper-raw-marker\n",
                encoding="ascii",
            )
            (safe_cgroup / "cgroup.procs").write_text(
                "helper-safe-marker\n",
                encoding="ascii",
            )
            scripts = (
                (
                    "helper() {\n"
                    '  /bin/cat "$1/$2$3"\n'
                    "}\n"
                    'helper "$1" cgroup .procs\n'
                ),
                (
                    "function helper {\n"
                    '  /bin/cat "$1/$2$3"\n'
                    "}\n"
                    'helper "$1" cgroup .procs\n'
                ),
                (
                    "outer_helper() {\n"
                    "  nested_helper() {\n"
                    '    /bin/cat "$1/$2$3"\n'
                    "  }\n"
                    '  nested_helper "$1" cgroup .procs\n'
                    "}\n"
                    'outer_helper "$1"\n'
                ),
                (
                    "helper() {\n"
                    '  /bin/cat "$1/$2$3"\n'
                    "}\n"
                    "helper_alias=helper\n"
                    '  "$helper_alias" "$1" cgroup .procs\n'
                ),
                (
                    "helper() {\n"
                    '  /bin/cat "$@"\n'
                    "}\n"
                    'helper "$1/cgroup.procs"\n'
                ),
                (
                    "raw_root=/safe\n"
                    "helper() {\n"
                    '  /bin/cat "$raw_root"/cgroup.proc?\n'
                    "}\n"
                    'raw_root="$1"\n'
                    "helper\n"
                ),
                (
                    "raw_root=/safe\n"
                    "helper() {\n"
                    '  /bin/cat "$raw_root"/cgroup.proc?\n'
                    "}\n"
                    'printf -v raw_root %s "$1"\n'
                    "helper\n"
                ),
            )
            for script in scripts:
                with self.subTest(
                    declaration=script.splitlines()[0]
                ):
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            script,
                            "--",
                            str(raw_cgroup),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stderr,
                    )
                    self.assertEqual(
                        completed.stdout,
                        "helper-raw-marker\n",
                    )
                    self.assertTrue(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            script,
                            label="user helper raw membership runtime",
                        )
                    )

            safe_scripts = (
                (
                    "local positional overwrite",
                    'raw_root="$1"\n'
                    "helper() {\n"
                    '  local raw_root="$2"\n'
                    '  /bin/cat "$raw_root"/cgroup.proc?\n'
                    "}\n"
                    'helper "$1" "$2"\n',
                ),
                (
                    "assignment overwrite",
                    "helper() {\n"
                    '  local raw_root="$1"\n'
                    '  raw_root="$2"\n'
                    '  /bin/cat "$raw_root"/cgroup.proc?\n'
                    "}\n"
                    'helper "$1" "$2"\n',
                ),
                (
                    "declaration overwrite",
                    "helper() {\n"
                    '  local raw_root="$1"\n'
                    '  local raw_root="$2"\n'
                    '  /bin/cat "$raw_root"/cgroup.proc?\n'
                    "}\n"
                    'helper "$1" "$2"\n',
                ),
                (
                    "printf overwrite",
                    "helper() {\n"
                    '  local raw_root="$1"\n'
                    '  printf -v raw_root %s "$2"\n'
                    '  /bin/cat "$raw_root"/cgroup.proc?\n'
                    "}\n"
                    'helper "$1" "$2"\n',
                ),
            )
            for label, script in safe_scripts:
                with self.subTest(helper_local_state=label):
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            script,
                            "--",
                            str(raw_cgroup),
                            str(safe_cgroup),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stderr,
                    )
                    self.assertEqual(
                        completed.stdout,
                        "helper-safe-marker\n",
                    )
                    self.assertFalse(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            script,
                            label=f"safe {label} helper runtime",
                        )
                    )

            tainted_scripts = (
                (
                    "read",
                    "helper() {\n"
                    '  local raw_root="$1"\n'
                    '  read raw_root <<< "$2"\n'
                    '  /bin/cat "$raw_root"/cgroup.proc?\n'
                    "}\n"
                    'helper "$1" "$2"\n',
                ),
                (
                    "mapfile",
                    "helper() {\n"
                    "  local -a roots\n"
                    '  mapfile -t roots <<< "$2"\n'
                    '  local raw_root="${roots[0]}"\n'
                    '  /bin/cat "$raw_root"/cgroup.proc?\n'
                    "}\n"
                    'helper "$1" "$2"\n',
                ),
                (
                    "readarray",
                    "helper() {\n"
                    "  local -a roots\n"
                    '  readarray -t roots <<< "$2"\n'
                    '  local raw_root="${roots[0]}"\n'
                    '  /bin/cat "$raw_root"/cgroup.proc?\n'
                    "}\n"
                    'helper "$1" "$2"\n',
                ),
                (
                    "dynamic",
                    "helper() {\n"
                    '  local raw_root="$(printf %s "$2")"\n'
                    '  /bin/cat "$raw_root"/cgroup.proc?\n'
                    "}\n"
                    'helper "$1" "$2"\n',
                ),
                (
                    "branch",
                    "helper() {\n"
                    '  local raw_root="$1"\n'
                    "  if true; then\n"
                    '    raw_root="$2"\n'
                    "  fi\n"
                    '  /bin/cat "$raw_root"/cgroup.proc?\n'
                    "}\n"
                    'helper "$1" "$2"\n',
                ),
                (
                    "loop",
                    "helper() {\n"
                    '  local raw_root="$1"\n'
                    '  for raw_root in "$2"; do\n'
                    "    true\n"
                    "  done\n"
                    '  /bin/cat "$raw_root"/cgroup.proc?\n'
                    "}\n"
                    'helper "$1" "$2"\n',
                ),
            )
            for label, script in tainted_scripts:
                with self.subTest(helper_tainted_state=label):
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            script,
                            "--",
                            str(raw_cgroup),
                            str(safe_cgroup),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stderr,
                    )
                    self.assertEqual(
                        completed.stdout,
                        "helper-safe-marker\n",
                    )
                    self.assertTrue(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            script,
                            label=f"tainted {label} helper runtime",
                        )
                    )

            global_script = (
                "raw_root=/safe\n"
                "helper() {\n"
                '  raw_root="$1"\n'
                "}\n"
                'helper "$1"\n'
                '/bin/cat "$raw_root"/cgroup.proc?\n'
            )
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    global_script,
                    "--",
                    str(raw_cgroup),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "helper-raw-marker\n")
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    global_script,
                    label="helper global write-back runtime",
                )
            )

            for label, script in (
                (
                    "recursive",
                    "helper() {\n"
                    "  helper\n"
                    "}\n"
                    "helper\n",
                ),
                (
                    "dynamic",
                    "helper() {\n"
                    "  true\n"
                    "}\n"
                    'callee="$(printf helper)"\n'
                    '"$callee"\n',
                ),
            ):
                with self.subTest(helper_dispatch=label):
                    self.assertTrue(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            script,
                            label=f"{label} helper dispatch",
                        )
                    )

    def test_mapfile_shadow_forges_singleton_but_builtin_read_cannot_be_shadowed(
        self,
    ):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="mapfile-shadow-",
            dir=artifact_root,
        ) as temporary:
            membership = Path(temporary) / "cgroup.procs"
            membership.write_text(
                f"{os.getpid()}\n{os.getppid()}\n",
                encoding="ascii",
            )
            shadow = (
                "mapfile() {\n"
                '  cgroup_members=("$$")\n'
                "}\n"
            )
            vulnerable = (
                shadow
                + 'mapfile -t cgroup_members < "$1"\n'
                + 'test "${#cgroup_members[@]}" -eq 1\n'
                + 'test "${cgroup_members[0]}" = "$$"\n'
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", vulnerable, "--", str(membership)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

            hardened = (
                shadow
                + 'builtin mapfile -t cgroup_members < "$1"\n'
                + 'test "${#cgroup_members[@]}" -eq 2\n'
                + 'test "${cgroup_members[0]}" != "$$"\n'
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", hardened, "--", str(membership)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    hardened,
                    label="mapfile shadow runtime",
                )
            )

    def test_fixed_quoted_dispatch_literals_do_not_execute_or_false_positive(self):
        cases = (
            (
                "before=$PATH\n"
                "target='$(printf PATH)'\n"
                'if printf -v "$target" %s /dispatch-rewritten '
                "2>/dev/null; then exit 91; fi\n"
                'test "$PATH" = "$before"\n',
                "quoted target",
                False,
            ),
            (
                "option='$(printf posix)'\n"
                'if set -o "$option" 2>/dev/null; then exit 92; fi\n'
                "[[ ! -o posix ]]\n",
                "quoted option",
                True,
            ),
        )
        for script, label, expected_rejection in cases:
            with self.subTest(label=label):
                completed = subprocess.run(
                    ["/bin/bash", "-c", script],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        script,
                        label=f"{label} dispatch control runtime",
                    ),
                    expected_rejection,
                )

    def test_nameref_runtime_unsets_and_writes_through_supervisor_alias(self):
        cases = (
            (
                "declare -n target=supervisor_cgroup\n"
                "unset target\n"
                'printf "%s\\n" "$supervisor_cgroup"\n',
                "unbound variable",
            ),
            (
                "declare -n target=supervisor_cgroup\n"
                "target=/mnt/home\n"
                'test "$supervisor_cgroup" = /mnt/home\n',
                "",
            ),
            (
                "declare -n target=supervisor_cgroup\n"
                'read target <<< "/mnt/home"\n'
                'test "$supervisor_cgroup" = /mnt/home\n',
                "",
            ),
        )
        for mutation, expected_stderr in cases:
            with self.subTest(mutation=mutation.splitlines()[1]):
                script = (
                    "set -u\n"
                    'supervisor_cgroup="/mnt/supervisor/cgroup"\n'
                    + mutation
                )
                completed = subprocess.run(
                    ["/bin/bash", "-c", script],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if expected_stderr:
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(expected_stderr, completed.stderr)
                else:
                    self.assertEqual(completed.returncode, 0)
                    self.assertEqual(completed.stderr, "")

    def test_alias_expansion_runtime_rewrites_supervisor_and_is_rejected(self):
        script = (
            "shopt -s expand_aliases\n"
            "alias rewrite='printf -v supervisor_cgroup %s /mnt/home'\n"
            'supervisor_cgroup="/mnt/supervisor/cgroup"\n'
            "rewrite\n"
            'test "$supervisor_cgroup" = /mnt/home\n'
        )
        completed = subprocess.run(
            ["/bin/bash", "-c", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr, "")
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                script,
                label="alias rewrite runtime",
            )
        )

    def test_wrapped_unset_runtime_breaks_safe_read_and_is_rejected(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="wrapped-supervisor-unset-",
            dir=artifact_root,
        ) as temporary:
            supervisor = Path(temporary) / "supervisor"
            supervisor.mkdir()
            (supervisor / "cgroup.procs").write_text(
                "safe-wrapper\n",
                encoding="ascii",
            )
            mutations = (
                "command unset supervisor_cgroup",
                "command -- unset supervisor_cgroup",
                "command -p unset supervisor_cgroup",
                "command -p -- unset supervisor_cgroup",
                "builtin unset supervisor_cgroup",
                "wrapper=command; mutation=unset; "
                'target=supervisor_cgroup; "$wrapper" -- '
                '"$mutation" "$target"',
                "mutation=unset; target=supervisor_cgroup; "
                'builtin "$mutation" "$target"',
                'wrapper=(command); "${wrapper[0]}" '
                "unset supervisor_cgroup",
                "declare -A wrapper=([key]=builtin); "
                '"${wrapper[key]}" unset supervisor_cgroup',
                'flags=(-pp); command "${flags[0]}" '
                "unset supervisor_cgroup",
                "declare -A flags=([key]=-pp); "
                'command "${flags[key]}" unset supervisor_cgroup',
                "index=0; wrapper=(command); "
                '"${wrapper[$index]}" unset supervisor_cgroup',
            )
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    script = (
                        "set -u\n"
                        'supervisor_cgroup="$1"\n'
                        + mutation
                        + "\n"
                        'mapfile -t members < '
                        '"$supervisor_cgroup/cgroup.procs"\n'
                        'printf "success:%s\\n" "${members[0]}"\n'
                    )
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            script,
                            "--",
                            str(supervisor),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertEqual(completed.stdout, "")
                    self.assertIn(
                        "supervisor_cgroup: unbound variable",
                        completed.stderr,
                    )

    def test_split_function_declaration_can_shadow_test_and_is_rejected(self):
        script = (
                    "test()\n"
                    "{\n"
                    "  return 0\n"
                    "}\n"
                    "if test false = true; then\n"
                    "  printf 'shadowed\\n'\n"
                    "fi\n"
        )
        completed = subprocess.run(
                    ["/bin/bash", "-c", script],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "shadowed\n")
        self.assertTrue(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        script,
                        label="split test shadow runtime",
                    )
        )

    def test_reduced_shell_surfaces_execute_and_fail_closed(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
                    prefix="reduced-shell-surfaces-",
                    dir=artifact_root,
        ) as temporary:
                    raw_cgroup = Path(temporary) / "raw"
                    raw_cgroup.mkdir()
                    (raw_cgroup / "cgroup.procs").write_text(
                        "reduced-shell-raw-marker\n",
                        encoding="ascii",
                    )
                    trap_output = Path(temporary) / "trap-output"
                    callback_output = Path(temporary) / "callback-output"
                    cases = (
                        (
                            "nested parameter",
                            '/bin/cat "${1:-${HOME}}/cgroup.procs"\n',
                            "reduced-shell-raw-marker\n",
                        ),
                        (
                            "nested parameter alias",
                            'raw_root="$1"\n'
                            '/bin/cat "${raw_root:-${HOME}}/cgroup.procs"\n',
                            "reduced-shell-raw-marker\n",
                        ),
                        (
                            "absolute find",
                            '/usr/bin/find "$1" -name "cgroup.p*" '
                            "-exec /bin/cat {} \\;\n",
                            "reduced-shell-raw-marker\n",
                        ),
                    )
                    for label, script, expected in cases:
                        with self.subTest(surface=label):
                            completed = subprocess.run(
                                [
                                    "/bin/bash",
                                    "-c",
                                    script,
                                    "--",
                                    str(raw_cgroup),
                                ],
                                cwd=ROOT,
                                check=False,
                                capture_output=True,
                                text=True,
                            )
                            self.assertEqual(
                                completed.returncode,
                                0,
                                completed.stderr,
                            )
                            self.assertEqual(completed.stdout, expected)
                            self.assertTrue(
                                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                                    script,
                                    label=f"{label} runtime",
                                )
                            )

                    trap_script = (
                        'trap \'/bin/cat "$1/cgroup.procs" > "$2"\' DEBUG\n'
                        "true\n"
                    )
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            trap_script,
                            "--",
                            str(raw_cgroup),
                            str(trap_output),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(
                        trap_output.read_text(encoding="ascii"),
                        "reduced-shell-raw-marker\n",
                    )
                    self.assertTrue(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            trap_script,
                            label="trap redirect runtime",
                        )
                    )

                    callback_script = (
                        'callback_output="$1"\n'
                        "callback() {\n"
                        '  printf "callback\\n" > "$callback_output"\n'
                        "}\n"
                        "mapfile -C callback -c 1 -t callback_data <<< value\n"
                    )
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            callback_script,
                            "--",
                            str(callback_output),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertEqual(
                        callback_output.read_text(encoding="ascii"),
                        "callback\n",
                    )
                    self.assertTrue(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            callback_script,
                            label="mapfile callback runtime",
                        )
                    )

                    forged_members = (
                        'cgroup_members=("$$")\n'
                        'test "${cgroup_members[0]}" = "$$"\n'
                    )
                    completed = subprocess.run(
                        ["/bin/bash", "-c", forged_members],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertTrue(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            forged_members,
                            label="reserved membership state runtime",
                        )
                    )

        for label, script in (
                    (
                        "literal nested parameter text",
                        "value='${raw:-${HOME}}'\n"
                        'printf "%s\\n" "$value"\n',
                    ),
                    (
                        "reviewed trap",
                        "isolated_stage_failure() {\n"
                        "  return 125\n"
                        "}\n"
                        "trap isolated_stage_failure ERR\n",
                    ),
                    (
                        "absolute safe command",
                        "/usr/bin/stat -c %u /mnt/supervisor\n",
                    ),
                    (
                        "mapfile without callback",
                        "mapfile -t ordinary < /dev/null\n",
                    ),
                    (
                        "ordinary array",
                        'members=("$$")\n'
                        'test "${members[0]}" = "$$"\n',
                    ),
        ):
                    with self.subTest(valid_control=label):
                        self.assertFalse(
                            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                                script,
                                label=f"{label} control",
                            )
                        )

    def test_quote_context_resolves_only_expansion_active_segments(self):
        inert_cases = (
            (
                        "single quoted",
                        "value='$cgroup_path'\n"
                        'printf "%s\\n" "$value"\n',
                        "$cgroup_path\n",
            ),
            (
                        "escaped",
                        "value=\\$cgroup_path\n"
                        'printf "%s\\n" "$value"\n',
                        "$cgroup_path\n",
            ),
            (
                        "mixed inert",
                        "suffix=safe\n"
                        "value='literal-$cgroup_path-'\"$suffix\"\n"
                        'printf "%s\\n" "$value"\n',
                        "literal-$cgroup_path-safe\n",
            ),
        )
        for label, script, expected in inert_cases:
            with self.subTest(quote_context=label):
                        completed = subprocess.run(
                            ["/bin/bash", "-c", script],
                            cwd=ROOT,
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(completed.returncode, 0, completed.stderr)
                        self.assertEqual(completed.stdout, expected)
                        self.assertFalse(
                            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                                script,
                                label=f"{label} quote control",
                            )
                        )

        expanding = (
            'cgroup_path="$1"\n'
            "value='literal-'\"$cgroup_path\"\n"
            'printf "%s\\n" "$value"\n'
        )
        completed = subprocess.run(
            ["/bin/bash", "-c", expanding, "--", "/raw-root"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "literal-/raw-root\n")
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        expanding,
                        label="mixed expanding quote negative",
            )
        )

    def test_ansi_c_locale_and_arithmetic_lexing_matches_bash(self):
        cases = (
            (
                        "ansi-escaped-quote",
                        "printf '%s\\n' $'abc\\'#notcomment'\n",
                        "abc'#notcomment\n",
            ),
            (
                        "ansi-heredoc-delimiter",
                        "cat <<$'EOF'\n"
                        "ansi body\n"
                        "EOF\n",
                        "ansi body\n",
            ),
            (
                        "ansi-heredoc-escaped-delimiter",
                        "cat <<$'E\\'OF'\n"
                        "escaped delimiter\n"
                        "E'OF\n",
                        "escaped delimiter\n",
            ),
            (
                        "locale-quote",
                        "value=world\n"
                        "LC_ALL=C\n"
                        "printf '%s\\n' $\"hello#$value\"\n",
                        "hello#world\n",
            ),
            (
                        "arithmetic-expansion",
                        "value=$((1 << 2))\n"
                        "printf '%s\\n' \"$value\"\n",
                        "4\n",
            ),
            (
                        "arithmetic-command",
                        "(( value = 1 << 3 ))\n"
                        "printf '%s\\n' \"$value\"\n",
                        "8\n",
            ),
            (
                        "legacy-arithmetic",
                        "value=$[1<<2]\n"
                        "printf '%s\\n' \"$value\"\n",
                        "4\n",
            ),
            (
                        "nested-arithmetic",
                        "value=$(( (1 << 2) + $(printf 1) ))\n"
                        "printf '%s\\n' \"$value\"\n",
                        "5\n",
            ),
            (
                        "arithmetic-base",
                        "value=$((16#10 + 1))\n"
                        "printf '%s\\n' \"$value\"\n",
                        "17\n",
            ),
        )
        for label, script, expected in cases:
            with self.subTest(lexical_form=label):
                        completed = subprocess.run(
                            ["/bin/bash", "-c", script],
                            cwd=ROOT,
                            check=False,
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(completed.returncode, 0, completed.stderr)
                        self.assertEqual(completed.stdout, expected)
                        self.assertFalse(
                            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                                script,
                                label=f"{label} Bash parity",
                            )
                        )

        inert = "printf '%s\\n' $'&& || |& () #'\n"
        records = publisher_shell_contract.split_bash_command_records(
            inert,
            label="ANSI-C inert topology",
        )
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0].preceding_operator)
        self.assertIsNone(records[0].following_operator)
        self.assertEqual(records[0].execution_scopes, ())
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                "printf '%s\\n' $'unterminated\n",
                label="unterminated ANSI-C quote",
            )
        )

        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="ansi-arithmetic-raw-read-",
            dir=artifact_root,
        ) as temporary:
            raw_cgroup = Path(temporary) / "raw"
            raw_cgroup.mkdir()
            (raw_cgroup / "cgroup.procs").write_text(
                        "ansi-arithmetic-raw-marker\n",
                        encoding="ascii",
            )
            for label, script in (
                        (
                            "ansi-prefix",
                            "printf '%s' $'<<FAKE # ) &&' > /dev/null\n"
                            '/bin/cat "$1/cgroup.procs"\n',
                        ),
                        (
                            "arithmetic-prefix",
                            "value=$((1 << 2))\n"
                            '/bin/cat "$1/cgroup.procs"\n',
                        ),
            ):
                        with self.subTest(adversarial_form=label):
                            completed = subprocess.run(
                                [
                                    "/bin/bash",
                                    "-c",
                                    script,
                                    "--",
                                    str(raw_cgroup),
                                ],
                                cwd=ROOT,
                                check=False,
                                capture_output=True,
                                text=True,
                            )
                            self.assertEqual(completed.returncode, 0, completed.stderr)
                            self.assertEqual(
                                completed.stdout,
                                "ansi-arithmetic-raw-marker\n",
                            )
                            self.assertTrue(
                                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                                    script,
                                    label=f"{label} raw read",
                                )
                            )

    def test_arithmetic_command_close_restores_comment_boundary(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="arithmetic-command-comment-",
            dir=artifact_root,
        ) as temporary:
            raw_cgroup = Path(temporary) / "raw"
            raw_cgroup.mkdir()
            (raw_cgroup / "cgroup.procs").write_text(
                "arithmetic-command-raw-read\n",
                encoding="ascii",
            )
            scripts = (
                "((1))# <<':'\n"
                '/bin/cat "$1/cgroup.procs"\n'
                ":\n",
                "if ((1))# <<':'\n"
                "then\n"
                '  /bin/cat "$1/cgroup.procs"\n'
                "fi\n"
                ":\n",
                "for ((i=0;i<1;i++))# <<':'\n"
                "do\n"
                '  /bin/cat "$1/cgroup.procs"\n'
                "done\n"
                ":\n",
                "(( value = $(printf 1) << 2 ))# <<':'\n"
                '/bin/cat "$1/cgroup.procs"\n'
                ":\n",
            )
            for script in scripts:
                with self.subTest(arithmetic_command=script.splitlines()[0]):
                            completed = subprocess.run(
                                [
                                    "/bin/bash",
                                    "-c",
                                    script,
                                    "--",
                                    str(raw_cgroup),
                                ],
                                cwd=ROOT,
                                check=False,
                                capture_output=True,
                                text=True,
                            )
                            self.assertEqual(completed.returncode, 0, completed.stderr)
                            self.assertEqual(
                                completed.stdout,
                                "arithmetic-command-raw-read\n",
                            )
                            self.assertTrue(
                                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                                    script,
                                    label="arithmetic command comment runtime",
                                )
                            )

            redirection = Path(temporary) / "redirected#suffix"
            redirect_script = '((1))>"$1"#suffix\n'
            completed = subprocess.run(
                [
                            "/bin/bash",
                            "-c",
                            redirect_script,
                            "--",
                            str(Path(temporary) / "redirected"),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(redirection.exists())

        for label, opener in (
            ("expansion", "value=$((1))# <<'FAKE'\n"),
            ("legacy", "value=$[1]# <<'FAKE'\n"),
        ):
            script = (
                "set -u\n"
                + opener
                + 'cgroup_path="$1"\n'
                + "FAKE\n"
                + 'printf "%s\\n" "$cgroup_path"\n'
            )
            with self.subTest(arithmetic_word=label):
                completed = subprocess.run(
                            ["/bin/bash", "-c", script, "--", "/owned-cgroup"],
                            cwd=ROOT,
                            check=False,
                            capture_output=True,
                            text=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("unbound variable", completed.stderr)
                records = (
                            publisher_shell_contract.split_bash_simple_command_strings(
                                script,
                                label=f"{label} arithmetic word heredoc",
                            )
                )
                self.assertNotIn('cgroup_path="$1"', records)

        self.assertFalse(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                "printf '%s\\n' '((1))# <<FAKE'\n",
                label="quoted arithmetic command data",
            )
        )
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                "((1)\n",
                label="malformed arithmetic command",
            )
        )

        with (
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_patch_release_run_script_identity",
            ),
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_builder_isolation_shell_identity",
            ),
        ):
            for label, changed in (
                generate_arithmetic_command_comment_hide_mutations(
                            self.text
                )
            ):
                with self.subTest(arithmetic_workflow=label):
                            self.assertTrue(
                                workflow_has_raw_builder_cgroup_membership_read(
                                    changed
                                )
                            )
                            self.assertIn(
                                "raw builder cgroup membership read differs",
                                publisher_boundary_errors(changed),
                            )

    def test_cgroup_path_reassignment_redirects_join_and_is_rejected(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="cgroup-path-reassignment-",
            dir=artifact_root,
        ) as temporary:
            raw_cgroup = Path(temporary) / "raw"
            fake_cgroup = Path(temporary) / "fake"
            raw_cgroup.mkdir()
            fake_cgroup.mkdir()
            script = (
                        'cgroup_path="$1"\n'
                        'cgroup_path="$2"\n'
                        'printf \'%s\\n\' "$$" > "$cgroup_path/cgroup.procs"\n'
            )
            completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            script,
                            "--",
                            str(raw_cgroup),
                            str(fake_cgroup),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse((raw_cgroup / "cgroup.procs").exists())
            self.assertRegex(
                        (fake_cgroup / "cgroup.procs").read_text(
                            encoding="ascii"
                        ),
                        r"^[1-9][0-9]*\n$",
            )
            self.assertTrue(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            script,
                            label="cgroup path reassignment runtime",
                        )
            )
            (fake_cgroup / "cgroup.procs").unlink()
            loop_script = (
                        'cgroup_path="$1"\n'
                        'for cgroup_path in "$2"; do\n'
                        "  true\n"
                        "done\n"
                        'printf \'%s\\n\' "$$" > "$cgroup_path/cgroup.procs"\n'
            )
            completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            loop_script,
                            "--",
                            str(raw_cgroup),
                            str(fake_cgroup),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue((fake_cgroup / "cgroup.procs").exists())
            self.assertTrue(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            loop_script,
                            label="protected loop target runtime",
                        )
            )
            attached_script = (
                        'cgroup_path="$1"\n'
                        'read cgroup_path<<<"$2"\n'
                        'test "$cgroup_path" = "$2"\n'
            )
            completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            attached_script,
                            "--",
                            str(raw_cgroup),
                            str(fake_cgroup),
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertTrue(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            attached_script,
                            label="attached redirection protected target runtime",
                        )
            )

        for valid_loop in (
            "for ordinary in one two; do\n"
            "  true\n"
            "done\n",
            "select ordinary in one; do\n"
            "  break\n"
            "done < /dev/null\n",
        ):
            self.assertFalse(
                        publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                            valid_loop,
                            label="ordinary loop target control",
                        )
            )

    def test_attached_redirections_preserve_targets_and_quoted_data(self):
        ordinary = (
            "read ordinary<<<value\n"
            'test "$ordinary" = value\n'
        )
        completed = subprocess.run(
            ["/bin/bash", "-c", ordinary],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        ordinary,
                        label="ordinary attached redirection",
            )
        )

        quoted = 'read "ordinary<<<value" 2>/dev/null || true\n'
        tokens = publisher_shell_contract._parse_shell_tokens(
            quoted.strip(),
            label="quoted redirection data",
        )
        lexed = publisher_shell_contract._split_attached_redirections(
            tokens
        )
        self.assertIn(
            "ordinary<<<value",
            publisher_shell_contract._token_texts(lexed),
        )

        process_substitution = (
            "read ordinary< <(printf 'value\\n')\n"
            'test "$ordinary" = value\n'
        )
        completed = subprocess.run(
            ["/bin/bash", "-c", process_substitution],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        process_substitution,
                        label="process substitution redirection",
            )
        )

    def test_command_query_clusters_are_nonmutating_and_invalid_clusters_fail(self):
        valid_clusters = (
            "-pv",
            "-vp",
            "-pV",
            "-Vp",
            "-ppv",
            "-vvp",
            "-pVp",
            "-pvV",
        )
        for cluster in valid_clusters:
            with self.subTest(valid_cluster=cluster):
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        'supervisor_cgroup="/mnt/supervisor/cgroup"\n'
                        f"command {cluster} supervisor_cgroup "
                        "> /dev/null 2>&1 || true\n"
                        'printf "%s\\n" "$supervisor_cgroup"\n',
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(
                    completed.stdout,
                    "/mnt/supervisor/cgroup\n",
                )
                self.assertEqual(completed.stderr, "")

        for setup, expansion in (
            ("flags=(-pv)", "${flags[0]}"),
            (
                "declare -A flags=([query]=-pV)",
                "${flags[query]}",
            ),
        ):
            with self.subTest(array_query=setup):
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        'supervisor_cgroup="/mnt/supervisor/cgroup"\n'
                        + setup
                        + "\n"
                        + f'command "{expansion}" supervisor_cgroup '
                        "> /dev/null 2>&1 || true\n"
                        + 'printf "%s\\n" "$supervisor_cgroup"\n',
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(
                    completed.stdout,
                    "/mnt/supervisor/cgroup\n",
                )
                self.assertEqual(completed.stderr, "")

        for cluster in ("-px", "-pZ", "-vX", "-pvx", "-p-v"):
            with self.subTest(invalid_cluster=cluster):
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        'supervisor_cgroup="/mnt/supervisor/cgroup"\n'
                        f"command {cluster} unset supervisor_cgroup "
                        "> /dev/null 2>&1\n"
                        'status="$?"\n'
                        'printf "%s:%s\\n" "$status" '
                        '"$supervisor_cgroup"\n',
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(
                    completed.stdout,
                    "2:/mnt/supervisor/cgroup\n",
                )
                self.assertEqual(completed.stderr, "")

    def test_isolated_exit_status_channel_maps_only_fixed_substages(self):
        report = isolated_failure_report_source(self.text)
        expected = {
            71: "candidate-preflight",
            72: "candidate-venv",
            73: "candidate-pip",
            74: "candidate-build-tools",
            75: "candidate-make",
            76: "candidate-handoff",
            77: "candidate-unknown",
            81: "namespace",
            82: "mount-audit",
            83: "output-validate",
            84: "export",
            85: "post-check",
        }
        for status, detail in expected.items():
            with self.subTest(status=status, detail=detail):
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        f"builder_status={status}\n" + report,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, status)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(
                    completed.stderr,
                    "candidate build failed: stage=isolated "
                    f"detail={detail} exit={status}\n",
                )

        for status in (1, 70, 78, 80, 86, 124, 125, 126, 137, 255):
            with self.subTest(malformed_status=status):
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        f"builder_status={status}\n" + report,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 125)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(
                    completed.stderr,
                    "candidate build failed: stage=isolated "
                    "detail=transport exit=125\n",
                )

    def test_candidate_and_root_stage_failures_encode_closed_exit_statuses(self):
        candidate = candidate_build_shell_source(self.text)
        candidate_start = candidate.index("candidate_stage=preflight")
        candidate_marker = "trap candidate_stage_failure ERR"
        candidate_end = (
            candidate.index(candidate_marker, candidate_start)
            + len(candidate_marker)
        )
        candidate_protocol = candidate[candidate_start:candidate_end]
        builder = builder_isolation_shell_source(self.text)
        builder_start = builder.index("isolated_stage=namespace")
        builder_marker = "trap isolated_stage_failure ERR"
        builder_end = (
            builder.index(builder_marker, builder_start)
            + len(builder_marker)
        )
        builder_protocol = builder[builder_start:builder_end]

        for protocol, variable, cases in (
            (
                candidate_protocol,
                "candidate_stage",
                {
                    "preflight": 71,
                    "venv": 72,
                    "pip": 73,
                    "build-tools": 74,
                    "make": 75,
                    "handoff": 76,
                    "invalid": 77,
                },
            ),
            (
                builder_protocol,
                "isolated_stage",
                {
                    "namespace": 81,
                    "mount-audit": 82,
                    "output-validate": 83,
                    "export": 84,
                    "post-check": 85,
                    "invalid": 125,
                },
            ),
        ):
            for stage, status in cases.items():
                with self.subTest(variable=variable, stage=stage):
                    completed = subprocess.run(
                        [
                            "/bin/bash",
                            "-c",
                            "set -Eeuo pipefail\n"
                            + protocol
                            + f"\n{variable}={stage}\n"
                            + "false\n",
                        ],
                        cwd=ROOT,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(completed.returncode, status)
                    self.assertEqual(completed.stdout, "")
                    self.assertEqual(completed.stderr, "")

    def test_nonzero_candidate_launcher_reaches_outer_detail_without_err_retrap(self):
        builder = builder_isolation_shell_source(self.text)
        protocol_start = builder.index("isolated_stage=namespace")
        protocol_marker = "trap isolated_stage_failure ERR"
        protocol_end = (
            builder.index(protocol_marker, protocol_start)
            + len(protocol_marker)
        )
        builder_protocol = builder[protocol_start:protocol_end]
        capture_start = builder.index("isolated_stage=candidate-preflight")
        capture_marker = "isolated_stage=output-validate"
        capture_end = (
            builder.index(capture_marker, capture_start)
            + len(capture_marker)
        )
        capture = builder[capture_start:capture_end]
        launcher = (
            "/usr/bin/python3 -I -S /mnt/control/candidate-launcher.py \\\n"
            '  "$builder_uid" "$builder_gid" \\\n'
            '  /mnt/control/candidate-build.sh "$host_runner_temp"'
        )
        self.assertIn(launcher, capture)
        capture = capture.replace(
            launcher,
            "fake_candidate_launcher",
            1,
        )

        candidate = candidate_build_shell_source(self.text)
        candidate_start = candidate.index("candidate_stage=preflight")
        candidate_marker = "trap candidate_stage_failure ERR"
        candidate_end = (
            candidate.index(candidate_marker, candidate_start)
            + len(candidate_marker)
        )
        candidate_protocol = candidate[candidate_start:candidate_end]
        report = isolated_failure_report_source(self.text)
        stages = {
            "preflight": (71, "candidate-preflight"),
            "venv": (72, "candidate-venv"),
            "pip": (73, "candidate-pip"),
            "build-tools": (74, "candidate-build-tools"),
            "make": (75, "candidate-make"),
            "handoff": (76, "candidate-handoff"),
        }
        for stage, (status, detail) in stages.items():
            with self.subTest(stage=stage):
                candidate_result = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        "set -Eeuo pipefail\n"
                        + candidate_protocol
                        + f"\ncandidate_stage={stage}\n"
                        + "false\n",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(candidate_result.returncode, status)
                forwarded = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        "set -Eeuo pipefail\n"
                        + builder_protocol
                        + "\n"
                        + "fake_candidate_launcher() { "
                        + f"return {candidate_result.returncode};"
                        + " }\n"
                        + capture
                        + "\n",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(forwarded.returncode, status)
                self.assertNotEqual(forwarded.returncode, 125)
                normalized = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        f"builder_status={forwarded.returncode}\n"
                        + report,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(normalized.returncode, status)
                self.assertEqual(
                    normalized.stderr,
                    "candidate build failed: stage=isolated "
                    f"detail={detail} exit={status}\n",
                )

        for status in (1, 23, 77, 81, 137):
            with self.subTest(candidate_arbitrary_status=status):
                forwarded = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        "set -Eeuo pipefail\n"
                        + builder_protocol
                        + "\n"
                        + f"fake_candidate_launcher() {{ return {status}; }}\n"
                        + capture
                        + "\n",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(forwarded.returncode, 77)
                normalized = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        "builder_status=77\n" + report,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(normalized.returncode, 77)
                self.assertIn(
                    "detail=candidate-unknown exit=77",
                    normalized.stderr,
                )

        for status in (125, 126):
            with self.subTest(launcher_infrastructure_status=status):
                forwarded = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        "set -Eeuo pipefail\n"
                        + builder_protocol
                        + "\n"
                        + f"fake_candidate_launcher() {{ return {status}; }}\n"
                        + capture
                        + "\n",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(forwarded.returncode, status)
                normalized = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        f"builder_status={forwarded.returncode}\n"
                        + report,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(normalized.returncode, 125)
                self.assertEqual(
                    normalized.stderr,
                    "candidate build failed: stage=isolated "
                    "detail=transport exit=125\n",
                )

        success = subprocess.run(
            [
                "/bin/bash",
                "-c",
                "set -Eeuo pipefail\n"
                + builder_protocol
                + "\n"
                + "fake_candidate_launcher() { return 0; }\n"
                + capture
                + "\n"
                + 'test "$candidate_status" -eq 0\n'
                + 'test "$isolated_stage" = output-validate\n',
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertEqual(success.stdout, "")
        self.assertEqual(success.stderr, "")

        buggy = subprocess.run(
            [
                "/bin/bash",
                "-c",
                "set -Eeuo pipefail\n"
                + builder_protocol
                + "\nisolated_stage=candidate-preflight\n"
                + "set +e\n"
                + "false\n"
                + 'candidate_status="$?"\n',
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(buggy.returncode, 125)
        self.assertEqual(buggy.stdout, "")
        self.assertEqual(buggy.stderr, "")

    def test_err_trapped_shells_have_no_set_plus_e_status_capture(self):
        builder = builder_isolation_shell_source(self.text)
        candidate = candidate_build_shell_source(self.text)
        self.assertNotIn("set +e", builder)
        self.assertNotIn("set +e", candidate)
        self.assertIn(
            "if /usr/bin/python3 -I -S "
            "/mnt/control/candidate-launcher.py",
            builder,
        )
        self.assertIn(
            "then\n  candidate_status=0\nelse\n"
            '  candidate_status="$?"\nfi',
            builder,
        )

    def test_explicit_trusted_failures_use_current_namespace_or_mount_stage(self):
        builder = builder_isolation_shell_source(self.text)
        protocol_start = builder.index("isolated_stage=namespace")
        protocol_marker = "trap isolated_stage_failure ERR"
        protocol_end = (
            builder.index(protocol_marker, protocol_start)
            + len(protocol_marker)
        )
        protocol = builder[protocol_start:protocol_end]
        namespace_start = builder.index('case "$host_runner_temp" in')
        namespace_end = (
            builder.index("\nesac", namespace_start) + len("\nesac")
        )
        namespace_site = builder[namespace_start:namespace_end]
        mount_start = builder.index(
            "for ((index=0; index < "
            "${#checked_runtime_transport_output[@]}; index+=2)); do"
        )
        mount_end = builder.index("\ndone", mount_start) + len("\ndone")
        mount_site = builder[mount_start:mount_end]
        report = isolated_failure_report_source(self.text)

        cases = (
            (
                "namespace",
                'host_runner_temp="/outside/runner"\n',
                namespace_site,
                81,
                "namespace",
            ),
            (
                "mount-audit",
                "isolated_stage=mount-audit\n"
                'checked_runtime_transport_output='
                '("/unexpected" "rw,nodev")\n',
                mount_site,
                82,
                "mount-audit",
            ),
        )
        for label, setup, site, status, detail in cases:
            with self.subTest(stage=label, behavior="fixed"):
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        "set -Eeuo pipefail\n"
                        + protocol
                        + "\n"
                        + setup
                        + site
                        + "\n",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, status)
                normalized = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        f"builder_status={completed.returncode}\n" + report,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(normalized.returncode, status)
                self.assertEqual(
                    normalized.stderr,
                    "candidate build failed: stage=isolated "
                    f"detail={detail} exit={status}\n",
                )

            with self.subTest(stage=label, behavior="explicit-exit-mutation"):
                mutated_site = site.replace(
                    "isolated_stage_failure",
                    "exit 1",
                    1,
                )
                self.assertNotEqual(mutated_site, site)
                mutated = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        "set -Eeuo pipefail\n"
                        + protocol
                        + "\n"
                        + setup
                        + mutated_site
                        + "\n",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(mutated.returncode, 1)
                normalized = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        "builder_status=1\n" + report,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(normalized.returncode, 125)
                self.assertIn(
                    "detail=transport exit=125",
                    normalized.stderr,
                )

        for marker in (
            'echo "runner temp is outside the masked host tree" >&2\n'
            "            isolated_stage_failure",
            'echo "unexpected writable mount: $mount_target" >&2\n'
            "                  isolated_stage_failure",
        ):
            changed = self.text.replace(
                marker,
                marker.replace("isolated_stage_failure", "exit 1"),
                1,
            )
            self.assertNotEqual(changed, self.text)
            self.assertTrue(publisher_boundary_errors(changed))

    def test_builder_isolation_explicit_exit_inventory_is_closed(self):
        builder = builder_isolation_shell_source(self.text)

        def exit_inventory(script):
            return Counter(
                command.strip()
                for command in publisher_shell_contract.split_bash_simple_command_strings(
                    script,
                    label="builder explicit exit inventory",
                )
                if re.search(r"\bexit(?:\s|$)", command)
            )

        expected_exits = Counter(
            {
                "namespace) exit 81": 1,
                "mount-audit) exit 82": 1,
                "output-validate) exit 83": 1,
                "export) exit 84": 1,
                "post-check) exit 85": 1,
                "*) exit 125": 1,
                '76) exit "$candidate_status"': 1,
                '126) exit "$candidate_status"': 1,
                "*) exit 77": 1,
                "exit 0": 1,
            }
        )
        self.assertEqual(
            exit_inventory(builder),
            expected_exits,
        )

        reordered_lines = builder.splitlines()
        first = next(
            index
            for index, line in enumerate(reordered_lines)
            if "namespace) exit 81 ;;" in line
        )
        second = next(
            index
            for index, line in enumerate(reordered_lines)
            if "mount-audit) exit 82 ;;" in line
        )
        reordered_lines[first], reordered_lines[second] = (
            reordered_lines[second],
            reordered_lines[first],
        )
        self.assertEqual(
            exit_inventory("\n".join(reordered_lines)),
            expected_exits,
        )
        exit_mutations = {
            "addition": builder + "\nexit 0\n",
            "deletion": builder.replace("namespace) exit 81 ;;", "", 1),
            "duplicate": builder.replace(
                "mount-audit) exit 82 ;;",
                "mount-audit) exit 82 ;;\n"
                "          mount-audit) exit 82 ;;",
                1,
            ),
        }
        for mutation, changed in exit_mutations.items():
            with self.subTest(exit_inventory_mutation=mutation):
                self.assertNotEqual(
                    exit_inventory(changed),
                    expected_exits,
                )

        returns = [
            command.strip()
            for command in publisher_shell_contract.split_bash_simple_command_strings(
                builder,
                label="builder explicit return inventory",
            )
            if command.strip().startswith("return")
        ]
        self.assertEqual(Counter(returns), Counter({"return 125": 40}))

    def test_helper_explicit_return_propagates_through_current_mount_stage(self):
        builder = builder_isolation_shell_source(self.text)
        protocol_start = builder.index("isolated_stage=namespace")
        protocol_marker = "trap isolated_stage_failure ERR"
        protocol_end = (
            builder.index(protocol_marker, protocol_start)
            + len(protocol_marker)
        )
        protocol = builder[protocol_start:protocol_end]
        helper_start = builder.index("remove_runtime_transport_file() {")
        helper_end = builder.index(
            "\nwritable_mount_records_max_bytes=",
            helper_start,
        )
        helper = builder[helper_start:helper_end]
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="explicit-helper-return-",
            dir=artifact_root,
        ) as temporary:
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    "set -Eeuo pipefail\n"
                    + protocol
                    + "\n"
                    + helper
                    + "\n"
                    + "isolated_stage=mount-audit\n"
                    + 'remove_runtime_transport_file "$1"\n',
                    "--",
                    temporary,
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 82)
            self.assertEqual(completed.stdout, "")
            self.assertIn("Is a directory", completed.stderr)

    def test_isolated_substage_channel_is_authenticated_and_nonfilesystem(self):
        script = named_step_run_script(
            self.text,
            "Build candidate in isolated namespace and stage public inputs",
        )
        self.assertIn(
            'wait "$builder_supervisor_pid"\n'
            'builder_status="$?"',
            script,
        )
        self.assertIn(
            "candidate build failed: stage=isolated detail=%s exit=%d",
            script,
        )
        self.assertNotIn("ISOLATED_STAGE", script)
        self.assertNotIn("isolated-stage", script)
        self.assertNotIn("stage-channel", script)
        leaked = self.text.replace(
            'builder_isolated_detail=transport\n'
            '              builder_status=125',
            'builder_isolated_detail="$builder_identity"\n'
            '              builder_status=125',
            1,
        )
        self.assertNotEqual(leaked, self.text)
        self.assertTrue(publisher_boundary_errors(leaked))

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
                    'builder_session_authenticated=0\n'
                    'builder_session_id=""\n'
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
            'builder_session_authenticated=1\n'
            'builder_session_id="12345"\n'
            'builder_supervisor_pid="12345"\n'
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

    def test_legacy_setsid_wrapper_pid_pgid_assumption_reproduces_exact_master_failure(self):
        failed_workflow = subprocess.check_output(
            [
                "git",
                "--no-pager",
                "show",
                f"{FAILING_MASTER_0456}:.github/workflows/build.yml",
            ],
            cwd=ROOT,
            text=True,
        )
        failed_launch = launch_validation_source(failed_workflow)
        self.assertIn(
            'builder_pgid="$(/usr/bin/ps -o pgid= -p "$builder_supervisor_pid"',
            failed_launch,
        )
        self.assertIn(
            'elif [ "$builder_pgid" != "$builder_supervisor_pid" ]; then',
            failed_launch,
        )

        process = subprocess.Popen(
            ["/usr/bin/setsid", "--fork", "--wait", "/bin/sleep", "0.2"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            self.assertNotEqual(os.getpgid(process.pid), process.pid)
            self.assertEqual(process.wait(timeout=5), 0)
            with self.assertRaises(ProcessLookupError):
                os.kill(process.pid, 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

    def test_supervisor_launcher_authenticates_session_before_namespace_and_leaves_no_orphan(
        self,
    ):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="supervisor-launcher-runtime-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            require_findmnt_uniq_namespace_capability(sandbox / "preflight")
            launcher = sandbox / "supervisor-launcher.py"
            launcher.write_text(
                supervisor_launcher_source(self.text),
                encoding="ascii",
            )
            launcher.chmod(0o400)
            process = subprocess.Popen(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-S",
                    str(launcher),
                    str(os.getpid()),
                    "--",
                    "/usr/bin/timeout",
                    "--signal=TERM",
                    "--kill-after=2s",
                    "30s",
                    "/usr/bin/unshare",
                    "--user",
                    "--map-root-user",
                    "--mount",
                    "--pid",
                    "--fork",
                    "--mount-proc",
                    "/bin/bash",
                    "--noprofile",
                    "--norc",
                    "-c",
                    "/bin/sleep 30",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            def session_pids() -> set[int]:
                completed = subprocess.run(
                    ["/usr/bin/ps", "-eo", "sid=,pid="],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                result = set()
                for line in completed.stdout.splitlines():
                    sid_text, pid_text = line.split()
                    if int(sid_text) == process.pid:
                        result.add(int(pid_text))
                return result

            try:
                for _attempt in range(200):
                    if process.poll() is not None:
                        break
                    if (
                        os.getsid(process.pid) == process.pid
                        and os.getpgid(process.pid) == process.pid
                        and "State:\tT" in Path(
                            f"/proc/{process.pid}/status"
                        ).read_text(encoding="ascii")
                    ):
                        break
                    time.sleep(0.01)
                self.assertIsNone(process.poll())
                self.assertEqual(os.getsid(process.pid), process.pid)
                self.assertEqual(os.getpgid(process.pid), process.pid)
                self.assertIn(
                    "State:\tT",
                    Path(f"/proc/{process.pid}/status").read_text(
                        encoding="ascii"
                    ),
                )
                os.kill(process.pid, signal.SIGCONT)
                for _attempt in range(200):
                    if len(session_pids()) >= 3:
                        break
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                self.assertIsNone(process.poll())
                self.assertGreaterEqual(len(session_pids()), 3)
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=5)
                for _attempt in range(200):
                    if not session_pids():
                        break
                    time.sleep(0.01)
                self.assertEqual(session_pids(), set())
            finally:
                if process.poll() is None:
                    try:
                        if os.getsid(process.pid) == process.pid:
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=5)

    def test_stopped_supervisor_dies_with_parent_before_resume_without_orphan(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="supervisor-parent-death-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            launcher = sandbox / "supervisor-launcher.py"
            launcher.write_text(
                supervisor_launcher_source(self.text),
                encoding="ascii",
            )
            launcher.chmod(0o400)
            identity_path = sandbox / "identity.json"
            parent = sandbox / "parent.py"
            parent.write_text(
                textwrap.dedent(
                    """\
                    import json
                    import os
                    from pathlib import Path
                    import subprocess
                    import sys
                    import time


                    def identity(pid):
                        record = Path(f"/proc/{pid}/stat").read_text(
                            encoding="ascii"
                        )
                        marker = record.rfind(") ")
                        if marker < 0:
                            raise RuntimeError("invalid proc stat")
                        fields = record[marker + 2 :].split()
                        return {
                            "pid": pid,
                            "ppid": int(fields[1]),
                            "pgid": int(fields[2]),
                            "sid": int(fields[3]),
                            "state": fields[0],
                            "starttime": int(fields[19]),
                        }


                    launcher, output = sys.argv[1:]
                    child = subprocess.Popen(
                        [
                            "/usr/bin/python3",
                            "-I",
                            "-S",
                            launcher,
                            str(os.getpid()),
                            "--",
                            "/usr/bin/timeout",
                            "30s",
                            "/bin/bash",
                            "--noprofile",
                            "--norc",
                            "-c",
                            "/bin/sleep 30",
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    observed = None
                    for _attempt in range(200):
                        if child.poll() is not None:
                            break
                        try:
                            candidate = identity(child.pid)
                        except (FileNotFoundError, ProcessLookupError):
                            break
                        if (
                            candidate["ppid"] == os.getpid()
                            and candidate["pid"] == candidate["pgid"]
                            and candidate["pid"] == candidate["sid"]
                            and candidate["state"] == "T"
                        ):
                            observed = candidate
                            break
                        time.sleep(0.01)
                    if observed is None:
                        raise SystemExit(125)
                    members = []
                    for entry in Path("/proc").iterdir():
                        if not entry.name.isdecimal():
                            continue
                        try:
                            if os.getsid(int(entry.name)) == child.pid:
                                members.append(int(entry.name))
                        except (FileNotFoundError, PermissionError, ProcessLookupError):
                            pass
                    observed["session_members"] = sorted(members)
                    Path(output).write_text(
                        json.dumps(observed),
                        encoding="ascii",
                    )
                    os._exit(0)
                    """
                ),
                encoding="ascii",
            )
            completed = subprocess.run(
                [
                    "/usr/bin/python3",
                    "-I",
                    "-S",
                    str(parent),
                    str(launcher),
                    str(identity_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            observed = json.loads(identity_path.read_text(encoding="ascii"))
            launcher_pid = observed["pid"]
            launcher_starttime = observed["starttime"]
            self.assertEqual(observed["ppid"] > 1, True)
            self.assertEqual(observed["pgid"], launcher_pid)
            self.assertEqual(observed["sid"], launcher_pid)
            self.assertEqual(observed["state"], "T")
            self.assertEqual(observed["session_members"], [launcher_pid])

            def current_starttime(pid: int) -> int | None:
                try:
                    record = Path(f"/proc/{pid}/stat").read_text(
                        encoding="ascii"
                    )
                except FileNotFoundError:
                    return None
                marker = record.rfind(") ")
                self.assertGreaterEqual(marker, 0)
                fields = record[marker + 2 :].split()
                return int(fields[19])

            monotonic_start = time.monotonic()
            for _attempt in range(200):
                if current_starttime(launcher_pid) != launcher_starttime:
                    break
                time.sleep(0.01)
            self.assertNotEqual(
                current_starttime(launcher_pid),
                launcher_starttime,
            )
            self.assertLess(time.monotonic() - monotonic_start, 2.0)
            remaining_session = []
            for entry in Path("/proc").iterdir():
                if not entry.name.isdecimal():
                    continue
                try:
                    if os.getsid(int(entry.name)) == launcher_pid:
                        remaining_session.append(int(entry.name))
                except (FileNotFoundError, PermissionError, ProcessLookupError):
                    pass
            self.assertEqual(remaining_session, [])

    def test_launch_identity_failures_do_not_signal_external_process(self):
        section = builder_cleanup_functions_source(self.text)
        launch = launch_validation_source(self.text)
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="launch-validation-cleanup-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            for name, identity_override, expected_detail in (
                (
                    "parent-pgid",
                    "read_builder_identity() {\n"
                    '  printf "%s %s %s S 1" '
                    '"$builder_supervisor_parent_pid" '
                    '"$builder_supervisor_pid" "$shell_pgid"\n'
                    "}\n",
                    "session-parent",
                ),
                (
                    "missing-identity",
                    "read_builder_identity() { return 1; }\n",
                    "session-query",
                ),
                (
                    "forged-identity",
                    'read_builder_identity() { printf "1 99998 99999 Ts 123"; }\n',
                    "session-mismatch",
                ),
            ):
                with self.subTest(case=name):
                    pid_file = sandbox / f"{name}.pid"
                    harness = (
                        "set -euo pipefail\n"
                        + section
                        + identity_override
                        + 'builder_uid="60000"\n'
                        + 'builder_user="ci-patch-builder"\n'
                        + 'builder_cgroup=""\n'
                        + 'builder_cgroup_owned=0\n'
                        + 'builder_session_authenticated=0\n'
                        + 'builder_session_id=""\n'
                        + 'builder_supervisor_parent_pid="$$"\n'
                        + 'builder_supervisor_pid=""\n'
                        + 'builder_supervisor_starttime=""\n'
                        + 'builder_supervisor_state=""\n'
                        + 'builder_supervisor_wait_pid=""\n'
                        + 'builder_root_owned=0\n'
                        + 'builder_user_created=0\n'
                        + 'wheelhouse_owned=0\n'
                        + f'BUILDER_ROOT="{sandbox / "builder-root"}"\n'
                        + f'PATCH_WHEELHOUSE="{sandbox / "wheelhouse"}"\n'
                        + "trap cleanup_builder EXIT\n"
                        + "/bin/sleep 60 < /dev/null > /dev/null 2>&1 & "
                        + f'printf "%s\\n" "$!" > "{pid_file}"\n'
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
                    child_pid = int(
                        pid_file.read_text(encoding="ascii").strip()
                    )
                    self.assertEqual(completed.returncode, 125)
                    self.assertIn(
                        "candidate build failed: stage=launch "
                        f"detail={expected_detail} exit=125",
                        completed.stderr,
                    )
                    self.assertNotIn(
                        "candidate build cleanup failed",
                        completed.stderr,
                    )
                    self.assertLess(duration, 10.0)
                    os.kill(child_pid, 0)
                    os.kill(child_pid, signal.SIGTERM)
                    for _attempt in range(100):
                        try:
                            os.kill(child_pid, 0)
                        except ProcessLookupError:
                            break
                        time.sleep(0.01)
                    with self.assertRaises(ProcessLookupError):
                        os.kill(child_pid, 0)

    def test_cleanup_rejects_reused_session_identity_without_signaling_external_process(self):
        section = builder_cleanup_functions_source(self.text)
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="forged-supervisor-identity-",
            dir=artifact_root,
        ) as temporary:
            pid_file = Path(temporary) / "wrapper.pid"
            harness = (
                "set -euo pipefail\n"
                + section
                + 'builder_uid="60000"\n'
                + 'builder_cgroup=""\n'
                + 'builder_cgroup_owned=0\n'
                + 'builder_session_authenticated=1\n'
                + 'builder_session_id=""\n'
                + 'builder_supervisor_parent_pid="$$"\n'
                + 'builder_supervisor_pid=""\n'
                + 'builder_supervisor_starttime="1"\n'
                + 'builder_supervisor_state="running"\n'
                + 'builder_supervisor_wait_pid=""\n'
                + "/usr/bin/python3 -I -S -c "
                + "'import os; os.setsid(); "
                + 'os.execv("/bin/sleep", ["sleep", "60"])\' '
                + "< /dev/null > /dev/null 2>&1 &\n"
                + 'builder_supervisor_pid="$!"\n'
                + 'builder_session_id="$builder_supervisor_pid"\n'
                + 'builder_supervisor_wait_pid="$builder_supervisor_pid"\n'
                + f'printf "%s\\n" "$builder_supervisor_pid" > "{pid_file}"\n'
                + "for attempt in $(/usr/bin/seq 1 100); do\n"
                + '  observed="$(/usr/bin/ps -o sid=,pgid= '
                + '-p "$builder_supervisor_pid" '
                + "| /usr/bin/awk 'NF == 2 {print $1 \" \" $2}')\"\n"
                + '  test "$observed" = "$builder_supervisor_pid '
                + '$builder_supervisor_pid" && break\n'
                + "  /bin/sleep 0.01\n"
                + "done\n"
                + 'test "$observed" = "$builder_supervisor_pid '
                + '$builder_supervisor_pid"\n'
                + "set +e\n"
                + "terminate_builder_processes\n"
                + 'status="$?"\n'
                + "set -e\n"
                + 'test "$status" -eq 1\n'
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", harness],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            wrapper_pid = int(pid_file.read_text(encoding="ascii").strip())
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")
            os.kill(wrapper_pid, 0)
            os.kill(wrapper_pid, signal.SIGTERM)
            for _attempt in range(100):
                try:
                    os.kill(wrapper_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.01)
            with self.assertRaises(ProcessLookupError):
                os.kill(wrapper_pid, 0)

    def test_exact_reviewed_cleanup_kills_stale_external_pid_but_fixed_cleanup_does_not(
        self,
    ):
        reviewed_workflow = subprocess.check_output(
            [
                "git",
                "--no-pager",
                "show",
                f"{REVIEWED_RUNTIME_3_6EE}:.github/workflows/build.yml",
            ],
            cwd=ROOT,
            text=True,
        )
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="stale-external-pid-negative-control-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)

            def run_cleanup(
                name: str,
                section: str,
                *,
                fixed: bool,
            ) -> tuple[subprocess.CompletedProcess[str], int]:
                pid_file = sandbox / f"{name}.pid"
                variables = (
                    'builder_uid="60000"\n'
                    'builder_cgroup=""\n'
                    'builder_cgroup_owned=0\n'
                    'builder_session_authenticated=0\n'
                    'builder_session_id=""\n'
                    'builder_supervisor_pid=""\n'
                )
                if fixed:
                    variables += (
                        'builder_supervisor_parent_pid="$$"\n'
                        'builder_supervisor_starttime=""\n'
                        'builder_supervisor_state=""\n'
                        'builder_supervisor_wait_pid=""\n'
                    )
                harness = (
                    "set -euo pipefail\n"
                    + section
                    + variables
                    + "/bin/sleep 60 < /dev/null > /dev/null 2>&1 &\n"
                    + 'builder_supervisor_pid="$!"\n'
                    + f'printf "%s\\n" "$builder_supervisor_pid" > "{pid_file}"\n'
                    + "terminate_builder_processes\n"
                )
                completed = subprocess.run(
                    ["/bin/bash", "-c", harness],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                return completed, int(
                    pid_file.read_text(encoding="ascii").strip()
                )

            reviewed, reviewed_pid = run_cleanup(
                "reviewed",
                builder_cleanup_functions_source(reviewed_workflow),
                fixed=False,
            )
            self.assertEqual(reviewed.returncode, 0, reviewed.stderr)
            with self.assertRaises(ProcessLookupError):
                os.kill(reviewed_pid, 0)

            fixed, fixed_pid = run_cleanup(
                "fixed",
                builder_cleanup_functions_source(self.text),
                fixed=True,
            )
            self.assertEqual(fixed.returncode, 0, fixed.stderr)
            os.kill(fixed_pid, 0)
            os.kill(fixed_pid, signal.SIGTERM)
            for _attempt in range(100):
                try:
                    os.kill(fixed_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.01)
            with self.assertRaises(ProcessLookupError):
                os.kill(fixed_pid, 0)

    def test_unauthenticated_cleanup_still_kills_owned_cgroup_descendants(self):
        section = builder_cleanup_functions_source(self.text)
        cgroup_kill = (
            "printf '1\\n' \\\n"
            "        | /usr/bin/sudo /usr/bin/tee \\\n"
            '          "$builder_cgroup/cgroup.kill" > /dev/null 2>&1'
        )
        self.assertIn(cgroup_kill, section)
        section = section.replace(
            cgroup_kill,
            'fake_cgroup_kill "$builder_cgroup/cgroup.kill"',
            1,
        )
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="unauthenticated-cgroup-cleanup-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            cgroup = sandbox / "cgroup"
            cgroup.mkdir()
            (cgroup / "cgroup.kill").write_text("", encoding="ascii")
            (cgroup / "cgroup.procs").write_text("", encoding="ascii")
            pid_file = sandbox / "descendant.pid"
            harness = (
                "set -euo pipefail\n"
                + section
                + 'builder_uid="60000"\n'
                + f'builder_cgroup="{cgroup}"\n'
                + 'builder_cgroup_owned=1\n'
                + 'builder_session_authenticated=0\n'
                + 'builder_session_id=""\n'
                + 'builder_supervisor_parent_pid="$$"\n'
                + 'builder_supervisor_pid=""\n'
                + 'builder_supervisor_starttime=""\n'
                + 'builder_supervisor_state=""\n'
                + 'builder_supervisor_wait_pid=""\n'
                + "/bin/sleep 60 < /dev/null > /dev/null 2>&1 &\n"
                + 'DESCENDANT_PID="$!"\n'
                + f'printf "%s\\n" "$DESCENDANT_PID" > "{pid_file}"\n'
                + "builder_cgroup_pids() {\n"
                + '  if /bin/kill -0 "$DESCENDANT_PID" 2>/dev/null; then\n'
                + '    printf "%s" "$DESCENDANT_PID"\n'
                + "  fi\n"
                + "}\n"
                + "fake_cgroup_kill() {\n"
                + '  /bin/kill -TERM "$DESCENDANT_PID"\n'
                + '  wait "$DESCENDANT_PID" 2>/dev/null || true\n'
                + "}\n"
                + "terminate_builder_processes\n"
                + "builder_cgroup_is_empty\n"
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", harness],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            descendant_pid = int(
                pid_file.read_text(encoding="ascii").strip()
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")
            with self.assertRaises(ProcessLookupError):
                os.kill(descendant_pid, 0)

    def test_pre_auth_cleanup_diagnostic_requires_residual_state(self):
        section = builder_cleanup_functions_source(self.text).replace(
            "for attempt in $(/usr/bin/seq 1 50); do",
            "for attempt in $(/usr/bin/seq 1 1); do",
        )
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="pre-auth-residual-cleanup-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            harness = (
                "set -euo pipefail\n"
                + section
                + 'builder_user="ci-patch-builder-residual-test"\n'
                + 'builder_uid="60000"\n'
                + f'builder_cgroup="{sandbox / "residual-cgroup"}"\n'
                + 'builder_cgroup_owned=1\n'
                + 'builder_session_authenticated=0\n'
                + 'builder_session_id=""\n'
                + 'builder_supervisor_parent_pid="$$"\n'
                + 'builder_supervisor_pid=""\n'
                + 'builder_supervisor_starttime=""\n'
                + 'builder_supervisor_state=""\n'
                + 'builder_supervisor_wait_pid=""\n'
                + 'builder_root_owned=0\n'
                + 'builder_user_created=0\n'
                + 'wheelhouse_owned=0\n'
                + f'BUILDER_ROOT="{sandbox / "builder-root"}"\n'
                + f'PATCH_WHEELHOUSE="{sandbox / "wheelhouse"}"\n'
                + "builder_cgroup_pids() { printf '999999'; }\n"
                + "set +e\n"
                + "(exit 125)\n"
                + "cleanup_builder\n"
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", harness],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(completed.returncode, 125)
            self.assertIn(
                "candidate build cleanup failed: "
                "process=1 cgroup=1 state=0 primary=125",
                completed.stderr,
            )
            self.assertNotIn(str(sandbox), completed.stderr)

    def test_cleanup_reauthenticates_and_terminates_owned_supervisor(self):
        section = builder_cleanup_functions_source(self.text)
        section = section.replace(
            "/usr/bin/sudo /bin/kill",
            "/bin/kill",
        )
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="owned-supervisor-cleanup-",
            dir=artifact_root,
        ) as temporary:
            sandbox = Path(temporary)
            launcher = sandbox / "supervisor-launcher.py"
            launcher.write_text(
                supervisor_launcher_source(self.text),
                encoding="ascii",
            )
            launcher.chmod(0o400)
            pid_file = sandbox / "wrapper.pid"
            harness = (
                "set -euo pipefail\n"
                + section
                + 'builder_uid="60000"\n'
                + 'builder_cgroup=""\n'
                + 'builder_cgroup_owned=0\n'
                + 'builder_session_authenticated=0\n'
                + 'builder_session_id=""\n'
                + 'builder_supervisor_parent_pid="$$"\n'
                + 'builder_supervisor_pid=""\n'
                + 'builder_supervisor_starttime=""\n'
                + 'builder_supervisor_state=""\n'
                + 'builder_supervisor_wait_pid=""\n'
                + "set +m\n"
                + f'/usr/bin/python3 -I -S "{launcher}" "$$" -- '
                + "/usr/bin/timeout --signal=TERM --kill-after=2s 30s "
                + "/bin/sleep 30 < /dev/null > /dev/null 2>&1 &\n"
                + 'builder_supervisor_pid="$!"\n'
                + 'builder_supervisor_wait_pid="$builder_supervisor_pid"\n'
                + f'printf "%s\\n" "$builder_supervisor_pid" > "{pid_file}"\n'
                + "for attempt in $(/usr/bin/seq 1 100); do\n"
                + "  set +e\n"
                + '  identity="$(read_builder_identity '
                + '"$builder_supervisor_pid")"\n'
                + '  identity_status="$?"\n'
                + "  set -e\n"
                + "  if [ \"$identity_status\" -eq 0 ] && "
                + "[[ \"$identity\" =~ "
                + "^([1-9][0-9]*)[[:space:]]+"
                + "([1-9][0-9]*)[[:space:]]+"
                + "([1-9][0-9]*)[[:space:]]+"
                + "([^[:space:]]+)[[:space:]]+"
                + "([1-9][0-9]*)$ ]] && "
                + '[[ "${BASH_REMATCH[4]}" = T* ]]; then\n'
                + '    builder_session_id="$builder_supervisor_pid"\n'
                + '    builder_supervisor_starttime="${BASH_REMATCH[5]}"\n'
                + "    builder_supervisor_state=stopped\n"
                + "    builder_session_authenticated=1\n"
                + "    break\n"
                + "  fi\n"
                + "  /bin/sleep 0.01\n"
                + "done\n"
                + "builder_supervisor_identity_matches stopped\n"
                + '/bin/kill -CONT "$builder_supervisor_pid"\n'
                + "builder_supervisor_state=starting\n"
                + "for attempt in $(/usr/bin/seq 1 100); do\n"
                + "  if builder_supervisor_identity_matches running; then\n"
                + "    builder_supervisor_state=running\n"
                + "    break\n"
                + "  fi\n"
                + "  /bin/sleep 0.01\n"
                + "done\n"
                + "builder_supervisor_identity_matches running\n"
                + "terminate_builder_processes\n"
                + 'test -z "$builder_supervisor_pid"\n'
                + 'test -z "$builder_supervisor_wait_pid"\n'
                + 'builder_group_is_empty "$builder_session_id"\n'
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", harness],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
            wrapper_pid = int(pid_file.read_text(encoding="ascii").strip())
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "")
            self.assertEqual(completed.stderr, "")
            with self.assertRaises(ProcessLookupError):
                os.kill(wrapper_pid, 0)

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

    def test_patch_release_security_contract_is_structured_and_prose_independent(
        self,
    ):
        registry = json.loads(PATCH_RELEASE_REGISTRY.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in registry["cases"]
            if item["id"] == "TC-CI-PATCH-049-002"
        )
        expected = {
            "schema_version": 1,
            "published_files": [
                "README.txt",
                "fireemblem8-expansion-all-locales-all-features-aapcs.bps",
                "manifest.json",
            ],
            "complete_rom_artifact": False,
            "diagnostic_stages": ["launch", "isolated", "cleanup"],
            "isolated_details": [
                "namespace",
                "mount-audit",
                "candidate-preflight",
                "candidate-venv",
                "candidate-pip",
                "candidate-build-tools",
                "candidate-make",
                "candidate-handoff",
                "output-validate",
                "export",
                "post-check",
                "candidate-unknown",
                "transport",
            ],
            "candidate_stage_statuses": [71, 72, 73, 74, 75, 76],
            "transport_statuses": [125, 126],
            "candidate_unknown_status": 77,
            "launcher_identity_fields": [
                "parent-pid",
                "pid",
                "sid",
                "pgid",
                "state",
                "start-time",
            ],
            "supervisor_parent_mode": "0700",
            "post_build_membership": "exact-wrapper-and-checker",
            "membership_reader": "isolated-python-ast",
            "membership_checker": {
                "path": "/mnt/supervisor/cgroup/cgroup.procs",
                "maximum_bytes": 4096,
                "members": ["wrapper-pid", "checker-pid"],
                "ordering": "insensitive",
                "shell_snapshot_state": "absent",
                "execution_path": "unconditional-builder-main-once",
            },
            "shell_surface": {
                "cgroup_path_initialization": (
                    "exact-unconditional-main-scope-argument-1-once"
                ),
                "cgroup_path_mutation": "reject-after-initialization",
                "quote_resolution": "expansion-active-segments-only",
                "literal_dollar_reexpansion": "forbidden",
                "assignment_prefix_dispatch": "analyze-executable",
                "control_prefix_dispatch": "analyze-executable",
                "control_operator_topology": (
                    "mandatory-actions-unconditional-and-unique"
                ),
                "mandatory_context": (
                    "central-top-level-control-operator-scope-free"
                ),
                "nested_execution_scopes": "reject-mandatory-actions",
                "parenthesis_scope_ownership": (
                    "syntax-active-quote-context"
                ),
                "attached_redirections": (
                    "lex-unquoted-syntax-segments"
                ),
                "heredoc_bodies": "excluded-from-command-records",
                "heredoc_comment_source": (
                    "syntax-active-pre-comment-only"
                ),
                "hash_comment_boundary": "unquoted-word-boundary",
                "metacharacter_comment_boundary": "restore-word-start",
                "substitution_close_boundary": "resume-containing-word",
                "subshell_close_boundary": "restore-word-start",
                "hash_data_contexts": (
                    "word-parameter-arithmetic-array-quote-escape"
                ),
                "ansi_c_quotes": "explicit-escape-decoding",
                "locale_quotes": "double-quote-expansion",
                "arithmetic_contexts": (
                    "exclude-shifts-from-heredoc-redirection"
                ),
                "arithmetic_command_close": "restore-word-start",
                "arithmetic_expansion_close": "resume-containing-word",
                "unquoted_heredoc_expansion": "reject",
                "here_strings": "ordinary-redirections",
                "protected_loop_targets": "reject",
                "operator_literals": "quoted-or-escaped-data",
                "split_function_declaration": (
                    "bind-pending-name-to-brace"
                ),
                "nested_braced_parameters": "reject-active",
                "trap": "exact-isolated-err-only",
                "absolute_raw_root_commands": "closed-signatures",
                "mapfile_callbacks": "reject",
                "reserved_cgroup_members_state": "absent",
            },
            "helper_calls": {
                "production_signatures": "closed",
                "tracked_or_composed_membership_arguments": "reject",
                "outer_alias_evaluation": "call-time",
                "frame_evaluation": "sequential",
                "local_writeback": "discard",
                "global_writeback": "propagate",
                "unknown_writes": "taint",
                "branch_or_loop_writes": "reject",
                "recursive_or_dynamic": "reject",
            },
            "helper_inventory": {
                "definition_count": 13,
                "body_identity": "parsed-command-topology-scope-digest",
                "declaration_scope": "top-level-unconditional",
                "definition_order": "before-first-use",
                "multiplicity": "exact",
                "ordering": "insensitive",
                "entrypoint_body": "separately-reviewed",
            },
            "function_shadowing": {
                "result": "reject",
                "reviewed_builder_builtins": [
                    "cd",
                    "exec",
                    "exit",
                    "local",
                    "mapfile",
                    "printf",
                    "return",
                    "set",
                    "test",
                    "trap",
                    "ulimit",
                    "wait",
                ],
                "sensitive_names": [
                    "alias",
                    "builtin",
                    "case",
                    "cd",
                    "command",
                    "coproc",
                    "declare",
                    "enable",
                    "env",
                    "eval",
                    "exec",
                    "exit",
                    "export",
                    "getopts",
                    "hash",
                    "local",
                    "mapfile",
                    "mount",
                    "printf",
                    "read",
                    "readarray",
                    "readonly",
                    "return",
                    "set",
                    "shift",
                    "shopt",
                    "sort",
                    "source",
                    "stat",
                    "test",
                    "trap",
                    "typeset",
                    "ulimit",
                    "unalias",
                    "unset",
                    "wait",
                ],
            },
            "raw_membership": {
                "allowed_accesses": [
                    "initial-join-write",
                    "authenticated-supervisor-read",
                ],
                "additional_access": "reject",
            },
            "transport_outputs": {
                "reserved": [
                    "checked_supervisor_transport_output",
                    "checked_runtime_transport_output",
                ],
                "writers": "matching-reviewed-reader-only",
                "supervisor_producer_calls": 2,
                "runtime_producer_calls": 1,
                "initial_state": "unavailable",
                "consumer_protocol": "exact-latest-phase-sequence",
                "supervisor_phases": 2,
                "runtime_phases": 1,
                "invoked_helper_consumers": "reject-unreviewed-runtime-dereference",
                "parameter_name_enumeration": "literal-prefix-not-indirect",
                "array_subscripts": "associative-string-indexed-arithmetic",
                "array_type_lifetime": "assignment-preserve-whole-unset-reset",
                "opposite_array_redeclaration": "error-preserve-type",
                "readonly_state": "rejected-mutation-preserves-value-and-type",
                "readonly_scope": "local-and-subshell",
                "readonly_alias_value": "attribute-separated-semantic-resolution",
                "arithmetic_writes": "protected-and-tracked-reject",
                "readonly_arithmetic_write": "failed-state-preserved",
                "dynamic_arithmetic_alias": "unresolved-target-fail-closed",
                "coprocess": "reject",
                "positional_parameter_mutation": "reject",
                "set_option_grammar": "valid-reviewed-bash-options-only",
                "set_redirections": "remove-all-preserve-argv",
                "fd_redirections": "atomic-not-control-operators",
                "redirection_matching": "canonical-longest-operator",
                "redirection_target": "required-syntax-active-word",
                "set_cluster_o": "ending-o-consumes-next-name",
                "getopts": "reject",
                "wait_output_variable": "reject",
                "dispatch_special_arrays": [
                    "BASH_ALIASES",
                    "BASH_CMDS",
                ],
                "later_or_external_mutation": "reject",
            },
            "alias_state": {
                "executable_prefixes": "time-and-case-normalized-through-helpers",
                "writers": [
                    "assignment",
                    "declaration",
                    "printf-v",
                    "read",
                    "mapfile",
                    "readarray",
                ],
                "exact_write": "replace",
                "resolved_declaration_assignment": "apply-once",
                "unknown_write": "taint",
                "dynamic_target": "reject",
                "membership_sensitive_use": "reject",
            },
            "evidence": {
                "behavior": "executable",
                "registry": "parsed-json",
                "prose": "informational",
            },
        }

        unordered_paths = (
            ("published_files",),
            ("diagnostic_stages",),
            ("isolated_details",),
            ("candidate_stage_statuses",),
            ("transport_statuses",),
            ("launcher_identity_fields",),
            ("function_shadowing", "sensitive_names"),
            ("membership_checker", "members"),
            ("raw_membership", "allowed_accesses"),
            ("transport_outputs", "reserved"),
            ("alias_state", "writers"),
        )

        def normalize_contract(contract):
            normalized = json.loads(json.dumps(contract))
            try:
                for path in unordered_paths:
                    target = normalized
                    for component in path[:-1]:
                        target = target[component]
                    values = target[path[-1]]
                    if not isinstance(values, list):
                        target[path[-1]] = {
                            "invalid_type": type(values).__name__,
                        }
                        continue
                    target[path[-1]] = {
                        "count": len(values),
                        "members": sorted(values),
                    }
            except (KeyError, TypeError):
                return {"invalid_contract": normalized}
            return normalized

        def contract_errors(case):
            return (
                []
                if normalize_contract(case.get("security_contract"))
                == normalize_contract(expected)
                else ["TC-CI-PATCH-049-002 security contract mismatch"]
            )

        self.assertEqual(contract_errors(entry), [])

        paraphrased = json.loads(json.dumps(entry))
        for field in (
            "purpose",
            "actions",
            "expected_result",
            "negative_control",
            "limitations",
        ):
            paraphrased[field] = (
                "Equivalent explanatory wording is intentionally "
                "not a behavioral oracle."
            )
        self.assertEqual(contract_errors(paraphrased), [])

        reordered = json.loads(json.dumps(entry))
        for path in unordered_paths:
            target = reordered["security_contract"]
            for component in path[:-1]:
                target = target[component]
            target[path[-1]].reverse()
        self.assertEqual(contract_errors(reordered), [])

        mutations = []
        for path, value in (
            (("complete_rom_artifact",), True),
            (("candidate_unknown_status",), 78),
            (("membership_reader",), "mapfile"),
            (("membership_checker", "maximum_bytes"), 8192),
            (
                ("membership_checker", "execution_path"),
                "conditional",
            ),
            (("shell_surface", "trap"), "allow"),
            (
                ("shell_surface", "cgroup_path_initialization"),
                "conditional",
            ),
            (
                ("shell_surface", "cgroup_path_mutation"),
                "allow",
            ),
            (
                ("shell_surface", "quote_resolution"),
                "shlex-text-only",
            ),
            (
                ("shell_surface", "assignment_prefix_dispatch"),
                "ignore",
            ),
            (
                ("shell_surface", "control_operator_topology"),
                "discard",
            ),
            (
                ("shell_surface", "mandatory_context"),
                "operator-only",
            ),
            (
                ("shell_surface", "protected_loop_targets"),
                "allow",
            ),
            (
                ("shell_surface", "attached_redirections"),
                "whitespace-only",
            ),
            (
                ("shell_surface", "heredoc_bodies"),
                "commands",
            ),
            (
                ("shell_surface", "heredoc_comment_source"),
                "full-physical-line",
            ),
            (
                ("shell_surface", "metacharacter_comment_boundary"),
                "retain-word-state",
            ),
            (
                ("shell_surface", "substitution_close_boundary"),
                "restore-word-start",
            ),
            (
                ("shell_surface", "ansi_c_quotes"),
                "ordinary-single-quote",
            ),
            (
                ("shell_surface", "arithmetic_contexts"),
                "heredoc",
            ),
            (
                ("shell_surface", "arithmetic_command_close"),
                "resume-containing-word",
            ),
            (
                (
                    "helper_calls",
                    "tracked_or_composed_membership_arguments",
                ),
                "allow",
            ),
            (("helper_calls", "outer_alias_evaluation"), "definition-time"),
            (("helper_calls", "frame_evaluation"), "frozen"),
            (("helper_calls", "local_writeback"), "propagate"),
            (("helper_inventory", "definition_count"), 14),
            (("helper_inventory", "definition_order"), "unordered"),
            (("function_shadowing", "result"), "allow"),
            (("raw_membership", "additional_access"), "allow"),
            (
                ("transport_outputs", "later_or_external_mutation"),
                "allow",
            ),
            (
                ("transport_outputs", "consumer_protocol"),
                "unordered",
            ),
            (("alias_state", "dynamic_target"), "allow"),
        ):
            changed = json.loads(json.dumps(entry))
            target = changed["security_contract"]
            for component in path[:-1]:
                target = target[component]
            target[path[-1]] = value
            mutations.append(changed)
        missing_writer = json.loads(json.dumps(entry))
        missing_writer["security_contract"]["alias_state"]["writers"].remove(
            "printf-v"
        )
        mutations.append(missing_writer)
        duplicate_writer = json.loads(json.dumps(entry))
        duplicate_writer["security_contract"]["alias_state"]["writers"].append(
            "printf-v"
        )
        mutations.append(duplicate_writer)
        missing_contract = json.loads(json.dumps(entry))
        del missing_contract["security_contract"]
        mutations.append(missing_contract)
        for changed in mutations:
            self.assertNotEqual(contract_errors(changed), [])

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

    def test_membership_checker_ast_is_exact_and_format_independent(self):
        builder = builder_isolation_shell_source(self.text)
        publisher_shell_contract.validate_patch_release_parser_heredocs(
            builder,
            label="membership checker",
        )
        reformatted = builder.replace(
            "members = {int(record, 10) for record in records}",
            "members={int(record,10) for record in records}",
            1,
        )
        self.assertNotEqual(reformatted, builder)
        publisher_shell_contract.validate_patch_release_parser_heredocs(
            reformatted,
            label="reformatted membership checker",
        )
        for label, changed in (
            (
                "path",
                builder.replace(
                    'MEMBERSHIP_PATH = "/mnt/supervisor/cgroup/cgroup.procs"',
                    'MEMBERSHIP_PATH = "/mnt/home/cgroup.procs"',
                    1,
                ),
            ),
            (
                "member-count",
                builder.replace(
                    "len(records) != 2",
                    "len(records) != 3",
                    1,
                ),
            ),
            (
                "expected-set",
                builder.replace(
                    "members != {expected_pid, checker_pid}",
                    "members != {expected_pid}",
                    1,
                ),
            ),
        ):
            with self.subTest(membership_ast_mutation=label):
                self.assertNotEqual(changed, builder)
                with self.assertRaisesRegex(
                    ValueError,
                    "membership checker differs",
                ):
                    publisher_shell_contract.validate_patch_release_parser_heredocs(
                        changed,
                        label="mutated membership checker",
                    )
        with (
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_patch_release_run_script_identity",
            ),
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_builder_isolation_shell_identity",
            ),
        ):
            reformatted_workflow = (
                reformatted_membership_checker_control(self.text)
            )
            self.assertNotIn(
                "publisher builder isolation shell differs",
                publisher_boundary_errors(reformatted_workflow),
            )
            for label, changed in (
                generate_membership_checker_ast_mutations(self.text)
            ):
                with self.subTest(
                    membership_workflow_mutation=label
                ):
                    self.assertIn(
                        "publisher builder isolation shell differs",
                        publisher_boundary_errors(changed),
                    )

    def test_membership_checker_must_be_on_unconditional_main_path(self):
        builder = builder_isolation_shell_source(self.text)
        introducer = (
            publisher_shell_contract.PATCH_RELEASE_MEMBERSHIP_CHECKER_INTRODUCER
        )
        start = builder.index(introducer)
        end = builder.index("\nPY", start) + len("\nPY")
        checker = builder[start:end]
        for label, prefix, suffix in (
            ("if-false", "if false; then\n", "\nfi"),
            ("while-false", "while false; do\n", "\ndone"),
            ("and-list", "set -e\nfalse && \\\n", ""),
            (
                "command-substitution",
                "set -e\nchecker_result=$(\n",
                "\n) || true",
            ),
            (
                "command-substitution-quoted-paren",
                "set -e\nchecker_result=$(\n"
                "printf '%s' \")\" > /dev/null\n",
                "\n) || true",
            ),
            ("subshell", "set -e\n(\n", "\n) || true"),
        ):
            with self.subTest(runtime_wrapper=label):
                completed = subprocess.run(
                    [
                        "/bin/bash",
                        "-c",
                        prefix
                        + checker
                        + suffix
                        + "\nprintf 'export-proceeded\\n'\n",
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(
                    completed.stdout,
                    "export-proceeded\n",
                )

        with (
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_patch_release_run_script_identity",
            ),
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_builder_isolation_shell_identity",
            ),
        ):
            for label, changed in (
                generate_membership_checker_control_flow_mutations(
                    self.text
                )
            ):
                with self.subTest(checker_control_flow=label):
                    self.assertTrue(
                        workflow_has_raw_builder_cgroup_membership_read(
                            changed
                        )
                    )
                    self.assertIn(
                        "raw builder cgroup membership read differs",
                        publisher_boundary_errors(changed),
                    )
            for label, changed in (
                generate_membership_checker_nested_execution_mutations(
                    self.text
                )
            ):
                with self.subTest(checker_nested_execution=label):
                    self.assertTrue(
                        workflow_has_raw_builder_cgroup_membership_read(
                            changed
                        )
                    )
                    self.assertIn(
                        "raw builder cgroup membership read differs",
                        publisher_boundary_errors(changed),
                    )

    def test_parenthesis_scopes_close_only_in_their_lexical_context(self):
        nested_cases = (
            (
                "double-quoted",
                "result=$(\n"
                "printf '%s' \")\"\n"
                "security_check\n"
                ")\n",
            ),
            (
                "single-quoted",
                "result=$(\n"
                "printf '%s' ')'\n"
                "security_check\n"
                ")\n",
            ),
            (
                "escaped",
                "result=$(\n"
                "printf '%s' \\)\n"
                "security_check\n"
                ")\n",
            ),
            (
                "mixed-nested",
                "result=$(\n"
                "nested=$(printf '%s' \")\")\n"
                "security_check\n"
                ")\n",
            ),
        )
        for label, script in nested_cases:
            with self.subTest(parenthesis_context=label):
                records = (
                    publisher_shell_contract.split_bash_command_records(
                        script,
                        label=f"{label} parenthesis scope",
                    )
                )
                checker = next(
                    record
                    for record in records
                    if record.text == "security_check"
                )
                self.assertIn(
                    "command-substitution",
                    checker.execution_scopes,
                )

        unquoted_close = (
            "result=$(true\n"
            ")\n"
            "security_check\n"
        )
        records = publisher_shell_contract.split_bash_command_records(
            unquoted_close,
            label="unquoted parenthesis close",
        )
        checker = next(
            record
            for record in records
            if record.text == "security_check"
        )
        self.assertEqual(checker.execution_scopes, ())

    def test_cgroup_initializer_must_be_on_unconditional_main_path(self):
        script = (
            "set -u\n"
            "if false; then\n"
            '  cgroup_path="$1"\n'
            "fi\n"
            'printf "%s\\n" "$cgroup_path"\n'
        )
        completed = subprocess.run(
            ["/bin/bash", "-c", script, "--", "/owned-cgroup"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("unbound variable", completed.stderr)
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                script,
                label="conditional cgroup initializer runtime",
            )
        )
        operator_script = (
            "set -eu\n"
            "false && \\\n"
            '  cgroup_path="$1"\n'
            'printf "%s\\n" "$cgroup_path"\n'
        )
        completed = subprocess.run(
            ["/bin/bash", "-c", operator_script, "--", "/owned-cgroup"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("unbound variable", completed.stderr)
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                operator_script,
                label="operator cgroup initializer runtime",
            )
        )

        reformatted = reformatted_cgroup_initializer_control(self.text)
        self.assertFalse(
            workflow_has_raw_builder_cgroup_membership_read(reformatted)
        )
        with (
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_patch_release_run_script_identity",
            ),
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_builder_isolation_shell_identity",
            ),
        ):
            self.assertNotIn(
                "raw builder cgroup membership read differs",
                publisher_boundary_errors(reformatted),
            )
            for label, changed in (
                generate_cgroup_initializer_control_flow_mutations(
                    self.text
                )
            ):
                with self.subTest(cgroup_initializer_context=label):
                    self.assertTrue(
                        workflow_has_raw_builder_cgroup_membership_read(
                            changed
                        )
                    )
                    self.assertIn(
                        "raw builder cgroup membership read differs",
                        publisher_boundary_errors(changed),
                    )

    def test_mandatory_actions_reject_operator_topology(self):
        controls = (
            "printf '%s\\n' '&& || | |& &' > /dev/null\n",
            "printf '%s\\n' \\&\\& \\|\\| \\|\\& \\& > /dev/null\n",
            "value='literal&&data'\n"
            'test "$value" = \'literal&&data\'\n',
        )
        for script in controls:
            with self.subTest(operator_literal=script.splitlines()[0]):
                records = (
                    publisher_shell_contract.split_bash_command_records(
                        script,
                        label="literal operator control",
                    )
                )
                self.assertTrue(records)
                self.assertTrue(
                    all(
                        record.preceding_operator is None
                        and record.following_operator is None
                        for record in records
                    )
                )

        with (
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_patch_release_run_script_identity",
            ),
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_builder_isolation_shell_identity",
            ),
        ):
            for label, changed in generate_mandatory_operator_mutations(
                self.text
            ):
                with self.subTest(mandatory_operator=label):
                    self.assertTrue(
                        workflow_has_raw_builder_cgroup_membership_read(
                            changed
                        )
                    )
                    self.assertIn(
                        "raw builder cgroup membership read differs",
                        publisher_boundary_errors(changed),
                    )
            for label, changed in (
                generate_mandatory_control_scope_mutations(self.text)
            ):
                with self.subTest(mandatory_control_scope=label):
                    self.assertTrue(
                        workflow_has_raw_builder_cgroup_membership_read(
                            changed
                        )
                    )
                    self.assertIn(
                        "raw builder cgroup membership read differs",
                        publisher_boundary_errors(changed),
                    )

    def test_generic_heredocs_cannot_spoof_mandatory_actions(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        skipped_initializer = (
            "set -u\n"
            "cat <<'FAKE' > /dev/null\n"
            'cgroup_path="$1"\n'
            "FAKE\n"
            'printf "%s\\n" "$cgroup_path"\n'
        )
        completed = subprocess.run(
            [
                "/bin/bash",
                "-c",
                skipped_initializer,
                "--",
                "/owned-cgroup",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("unbound variable", completed.stderr)
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                skipped_initializer,
                label="heredoc initializer spoof runtime",
            )
        )

        skipped_trap = (
            'trap_marker="$1"\n'
            "handler() {\n"
            '  printf "trapped\\n" > "$trap_marker"\n'
            "}\n"
            "cat <<'FAKE' > /dev/null\n"
            "trap handler ERR\n"
            "FAKE\n"
            "false || true\n"
            'test ! -e "$trap_marker"\n'
        )
        with tempfile.TemporaryDirectory(
            prefix="heredoc-trap-spoof-",
            dir=artifact_root,
        ) as temporary:
            marker = Path(temporary) / "trap-marker"
            completed = subprocess.run(
                ["/bin/bash", "-c", skipped_trap, "--", str(marker)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(marker.exists())

        with tempfile.TemporaryDirectory(
            prefix="heredoc-expansion-",
            dir=artifact_root,
        ) as temporary:
            marker = Path(temporary) / "expanded"
            unquoted = (
                "cat <<HEREDOC > /dev/null\n"
                '$(printf "expanded\\n" > "$1")\n'
                "HEREDOC\n"
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", unquoted, "--", str(marker)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                marker.read_text(encoding="ascii"),
                "expanded\n",
            )
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    unquoted,
                    label="unquoted heredoc expansion runtime",
                )
            )

            marker.unlink()
            quoted = (
                "cat <<'HEREDOC' > /dev/null\n"
                '$(printf "expanded\\n" > "$1")\n'
                "HEREDOC\n"
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", quoted, "--", str(marker)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    quoted,
                    label="quoted heredoc literal control",
                )
            )
            unquoted_plain = (
                "cat <<HEREDOC > /dev/null\n"
                "plain literal body\n"
                "HEREDOC\n"
            )
            self.assertFalse(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    unquoted_plain,
                    label="unquoted plain heredoc control",
                )
            )

        here_string = (
            "cat <<<'cgroup_path=\"$1\"' > /dev/null\n"
            'cgroup_path="$1"\n'
        )
        self.assertFalse(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                here_string,
                label="here-string distinction control",
            )
        )
        tab_stripped = (
            "cat <<-'FAKE' > /dev/null\n"
            '\tcgroup_path="$1"\n'
            "\tFAKE\n"
            'cgroup_path="$1"\n'
        )
        records = publisher_shell_contract.split_bash_simple_command_strings(
            tab_stripped,
            label="tab-stripped heredoc control",
        )
        self.assertEqual(
            records,
            ("cat <<-'FAKE' > /dev/null", 'cgroup_path="$1"'),
        )
        self.assertFalse(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                tab_stripped,
                label="tab-stripped heredoc control",
            )
        )
        with tempfile.TemporaryDirectory(
            prefix="commented-heredoc-hide-",
            dir=artifact_root,
        ) as temporary:
            raw_cgroup = Path(temporary) / "raw"
            raw_cgroup.mkdir()
            (raw_cgroup / "cgroup.procs").write_text(
                "comment-hidden-raw-read\n",
                encoding="ascii",
            )
            commented = (
                ": # <<':'\n"
                '/bin/cat "$1/cgroup.procs"\n'
                ":\n"
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", commented, "--", str(raw_cgroup)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout,
                "comment-hidden-raw-read\n",
            )
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    commented,
                    label="commented heredoc hide runtime",
                )
            )
            parenthesized = (
                "(# <<':'\n"
                '/bin/cat "$1/cgroup.procs"\n'
                ":\n"
                ")\n"
            )
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    parenthesized,
                    "--",
                    str(raw_cgroup),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout,
                "comment-hidden-raw-read\n",
            )
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    parenthesized,
                    label="parenthesized comment boundary runtime",
                )
            )

        comment_controls = (
            (
                "trailing-comment",
                "cat <<'FAKE' # trailing comment\n"
                'cgroup_path="$1"\n'
                "FAKE\n"
                "true\n",
                ("cat <<'FAKE'", "true"),
            ),
            (
                "quoted-hash",
                "cat '#' <<'FAKE'\n"
                'cgroup_path="$1"\n'
                "FAKE\n",
                ("cat '#' <<'FAKE'",),
            ),
            (
                "escaped-hash",
                "cat \\# <<'FAKE'\n"
                'cgroup_path="$1"\n'
                "FAKE\n",
                ("cat \\# <<'FAKE'",),
            ),
            (
                "parameter-operator",
                "value=${x#<<FAKE}\n"
                "true\n",
                ("value=${x#<<FAKE}", "true"),
            ),
            (
                "arithmetic-base",
                "value=$((16#10 + 1))\n"
                "true\n",
                ("value=$((16#10 + 1))", "true"),
            ),
            (
                "array-subscript",
                "declare -A array\n"
                "array[key#value]=entry\n"
                "true\n",
                (
                    "declare -A array",
                    "array[key#value]=entry",
                    "true",
                ),
            ),
            (
                "hash-within-word",
                "cat word#<<FAKE\n"
                "body\n"
                "FAKE\n",
                ("cat word#<<FAKE",),
            ),
        )
        for label, script, expected_records in comment_controls:
            with self.subTest(comment_control=label):
                syntax = subprocess.run(
                    ["/bin/bash", "-n", "-c", script],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(syntax.returncode, 0, syntax.stderr)
                self.assertEqual(
                    publisher_shell_contract.split_bash_simple_command_strings(
                        script,
                        label=f"{label} heredoc comment control",
                    ),
                    expected_records,
                )

        with (
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_patch_release_run_script_identity",
            ),
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_builder_isolation_shell_identity",
            ),
        ):
            for label, changed in generate_generic_heredoc_spoof_mutations(
                self.text
            ):
                with self.subTest(heredoc_spoof=label):
                    self.assertTrue(
                        workflow_has_raw_builder_cgroup_membership_read(
                            changed
                        )
                    )
                    self.assertIn(
                        "raw builder cgroup membership read differs",
                        publisher_boundary_errors(changed),
                    )
            for label, changed in (
                generate_commented_heredoc_hide_mutations(self.text)
            ):
                with self.subTest(commented_heredoc_hide=label):
                    self.assertTrue(
                        workflow_has_raw_builder_cgroup_membership_read(
                            changed
                        )
                    )
                    self.assertIn(
                        "raw builder cgroup membership read differs",
                        publisher_boundary_errors(changed),
                    )

    def test_substitution_word_hash_keeps_real_heredoc_body_inert(self):
        openers = (
            "result=$(printf '%s' x)# <<'FAKE_INIT' > /dev/null\n",
            "result=$(printf '%s' \")\")# <<'FAKE_INIT' > /dev/null\n",
            "result=$(printf '%s' \"$(printf x)\")# "
            "<<'FAKE_INIT' > /dev/null\n",
            'result="$(printf x)"# <<\'FAKE_INIT\' > /dev/null\n',
            'result=$"$(printf x)"# <<\'FAKE_INIT\' > /dev/null\n',
            "result=`printf x`# <<'FAKE_INIT' > /dev/null\n",
            "result=<(printf x)# <<'FAKE_INIT' > /dev/null\n",
            "result=>(/bin/cat > /dev/null)# "
            "<<'FAKE_INIT' > /dev/null\n",
        )
        for opener in openers:
            with self.subTest(substitution_word=opener.split("#", 1)[0]):
                script = (
                    "set -u\n"
                    + opener
                    + 'cgroup_path="$1"\n'
                    + "FAKE_INIT\n"
                    + 'printf "%s\\n" "$cgroup_path"\n'
                )
                syntax = subprocess.run(
                    ["/bin/bash", "-n", "-c", script],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(syntax.returncode, 0, syntax.stderr)
                completed = subprocess.run(
                    ["/bin/bash", "-c", script, "--", "/owned-cgroup"],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("unbound variable", completed.stderr)
                records = (
                    publisher_shell_contract.split_bash_simple_command_strings(
                        script,
                        label="substitution word heredoc runtime",
                    )
                )
                self.assertNotIn('cgroup_path="$1"', records)
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        script,
                        label="substitution word heredoc runtime",
                    )
                )

        inner_comment = (
            "result=$(# inner comment\n"
            "  printf x\n"
            ")\n"
            'test "$result" = x\n'
        )
        completed = subprocess.run(
            ["/bin/bash", "-c", inner_comment],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="substitution-comment-boundary-",
            dir=artifact_root,
        ) as temporary:
            raw_cgroup = Path(temporary) / "raw"
            raw_cgroup.mkdir()
            (raw_cgroup / "cgroup.procs").write_text(
                "substitution-boundary-raw-read\n",
                encoding="ascii",
            )
            whitespace = (
                "result=$(printf x) # <<':'\n"
                '/bin/cat "$1/cgroup.procs"\n'
                ":\n"
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", whitespace, "--", str(raw_cgroup)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout,
                "substitution-boundary-raw-read\n",
            )
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    whitespace,
                    label="substitution whitespace comment boundary",
                )
            )

        with (
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_patch_release_run_script_identity",
            ),
            mock.patch.object(
                publisher_shell_contract,
                "assert_reviewed_builder_isolation_shell_identity",
            ),
        ):
            for label, changed in generate_substitution_word_heredoc_spoofs(
                self.text
            ):
                with self.subTest(substitution_workflow=label):
                    self.assertTrue(
                        workflow_has_raw_builder_cgroup_membership_read(
                            changed
                        )
                    )
                    self.assertIn(
                        "raw builder cgroup membership read differs",
                        publisher_boundary_errors(changed),
                    )

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
        compile(
            supervisor_launcher_source(self.text),
            "<supervisor-launcher>",
            "exec",
        )


if __name__ == "__main__":
    unittest.main()
