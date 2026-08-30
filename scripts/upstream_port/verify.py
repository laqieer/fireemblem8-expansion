"""Orchestrate existing repository gates against the CURRENT TRUSTED WORKTREE
after a maintainer has manually applied a port batch.

WARNING (see docs/upstream-porting.md): this command builds and checks the
repository's *own* current working tree/commit. It never builds, checks out,
or executes the canonical upstream ref/tree. It is a thin, literal mirror of
the four combined workers in `.github/workflows/build.yml`. Before execution,
it parses the selected target checkout's workflow as data and requires exact
semantic equivalence with both the source workflow and this module's reviewed
gate list; target Python is never imported. Only the master-only publisher and
serial summary jobs have no local gate equivalent. The one DELIBERATE
command-level exception is build.yml's
"Check documentation (issues #7/#17)" step, which remains a required
standalone workflow gate outside this mirror. Run that standalone command pair
directly to reproduce it locally; see docs/upstream-porting.md.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import List

# A leading NAME=VALUE token in a gate command is an inline environment
# assignment (POSIX shell semantics), mirrored verbatim from build.yml so
# the gate list stays an argv-identical copy of the workflow. It is applied
# to the child environment, never exec-ed as a program.
_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_TRUSTED_GIT = "/usr/bin/git"
_SOURCE_ROOT = os.path.realpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
_BUILD_WORKFLOW_RELATIVE = os.path.join(".github", "workflows", "build.yml")
_COMBINED_JOBS = ("host-tests", "build", "extended-host-tests", "legacy")
_EXPECTED_JOBS = _COMBINED_JOBS + ("patch-release", "summary")
_CHECKOUT_USES = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
_CHECKOUT_WITH = (
    ("fetch-depth", "0"),
    ("persist-credentials", "false"),
    (
        "ref",
        "${{ github.event_name == 'pull_request' && "
        "github.event.pull_request.head.sha || github.sha }}",
    ),
    ("submodules", "recursive"),
)
_EXPECTED_BUILD_SHA_EXPRESSION = (
    "${{ github.event_name == 'pull_request' && "
    "github.event.pull_request.head.sha || github.sha }}"
)
_EXPECTED_JOB_ENV = {
    "host-tests": (("EXPECTED_BUILD_SHA", _EXPECTED_BUILD_SHA_EXPRESSION),),
    "build": (("EXPECTED_BUILD_SHA", _EXPECTED_BUILD_SHA_EXPRESSION),),
    "extended-host-tests": (
        ("EXPECTED_BUILD_SHA", _EXPECTED_BUILD_SHA_EXPRESSION),
    ),
    "legacy": (
        ("AGBCC_COMMIT", "da598c1d918402c42c0c0d7128ba14567f3175e9"),
        ("EXPECTED_BUILD_SHA", _EXPECTED_BUILD_SHA_EXPRESSION),
        (
            "MGFEMBP_AGBCC_COMMIT",
            "63b22f3eb8a8051af30bd80c4795b355e439e7ef",
        ),
    ),
}
_NON_GATE_STEP_NAMES = {
    "Verify checked-out revision",
    "Hydrate workflow-pilot Git authority",
    "Install host-only dependencies (no arm-none-eabi toolchain)",
    "Install dependencies",
    "Build tools",
    "Install extended host dependencies",
    "Install archival build dependencies",
    "Preflight archival toolchain executables",
    "Install pinned archival agbcc compilers",
}
_DOCS_GOVERNANCE_STEP_NAME = "Check documentation (issues #7/#17)"
_WORKFLOW_PILOT_TEST_STEP_NAME = (
    "Run workflow-pilot reporter regression suite (issue #176)"
)
_WORKFLOW_PILOT_BASELINE_STEP_NAME = (
    "Validate workflow-pilot baseline against checked-out Git history"
)
_SCRUBBED_PILOT_ENV = (
    "BASH_ENV: ''",
    "ENV: ''",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES: ''",
    "GIT_CEILING_DIRECTORIES: ''",
    "GIT_COMMON_DIR: ''",
    "GIT_CONFIG_COUNT: '0'",
    "GIT_CONFIG_GLOBAL: /dev/null",
    "GIT_CONFIG_KEY_0: ''",
    "GIT_CONFIG_NOSYSTEM: '1'",
    "GIT_CONFIG_PARAMETERS: ''",
    "GIT_CONFIG_SYSTEM: /dev/null",
    "GIT_CONFIG_VALUE_0: ''",
    "GIT_DIR: ''",
    "GIT_EXEC_PATH: ''",
    "GIT_INDEX_FILE: ''",
    "GIT_NAMESPACE: ''",
    "GIT_NO_LAZY_FETCH: '1'",
    "GIT_NO_REPLACE_OBJECTS: '1'",
    "GIT_OBJECT_DIRECTORY: ''",
    "GIT_REPLACE_REF_BASE: ''",
    "GIT_WORK_TREE: ''",
    "PATH: /usr/bin:/bin",
    "PYTHONPATH: ''",
)
_EXPECTED_STEP_ROLES = {
    "host-tests": (
        ("setup", None),
        ("setup", "Verify checked-out revision"),
        ("setup", "Hydrate workflow-pilot Git authority"),
        ("setup", "Install host-only dependencies (no arm-none-eabi toolchain)"),
        ("gate", "Run gba-playtest host test suite"),
        ("gate", "Run upstream-port tooling test suite"),
        ("gate", "Run workflow contract test suite"),
        ("gate", _WORKFLOW_PILOT_TEST_STEP_NAME),
        ("gate", _WORKFLOW_PILOT_BASELINE_STEP_NAME),
        ("gate", "Run localization host test suite (issue #18)"),
        ("gate", "Run full-game localization width contract (issue #18)"),
    ),
    "build": (
        ("setup", None),
        ("setup", "Verify checked-out revision"),
        ("gate", "Check tracked artifacts"),
        ("standalone-gate", _DOCS_GOVERNANCE_STEP_NAME),
        ("setup", "Install dependencies"),
        ("setup", "Build tools"),
        ("gate", "Run CodeQL alert regression suite (issue #84)"),
        ("gate", "Check default build lane and quickstart legacy glue (issue #15)"),
        ("gate", "Check generated-data tables for drift"),
        ("gate", "Build and verify modern target ROMs and linker"),
        (
            "gate",
            "Boundary/serialization item-ID-expansion + content runtime "
            "gate (cap 0xCE)",
        ),
        (
            "gate",
            "Build and verify all-locales/all-features map menu "
            "(issues #49/#168)",
        ),
    ),
    "extended-host-tests": (
        ("setup", None),
        ("setup", "Verify checked-out revision"),
        ("setup", "Install extended host dependencies"),
        ("gate", "Run CJK font gates"),
        ("gate", "Run multilang texttools codec gates"),
        ("gate", "Run configuration and linker-budget gates"),
    ),
    "legacy": (
        ("setup", None),
        ("setup", "Verify checked-out revision"),
        ("setup", "Install archival build dependencies"),
        ("setup", "Preflight archival toolchain executables"),
        ("setup", "Install pinned archival agbcc compilers"),
        ("setup", "Build tools"),
        ("gate", "Build archival lane without a copyrighted baserom"),
        ("gate", "Validate pinned archival payload identities"),
    ),
}


def _split_env_prefix(command):
    """Split any leading ``NAME=VALUE`` env-assignment tokens off the front of
    a gate command, returning ``(env_overrides, argv)``. Only a *leading*
    run is treated as env (matching the shell), so a NAME=VALUE that appears
    after the program (e.g. make variable overrides like ``MODERN_CONFIG=debug``)
    stays part of argv."""
    env_overrides = {}
    argv = list(command)
    while argv and _ENV_ASSIGN_RE.match(argv[0]):
        name, _, value = argv[0].partition("=")
        env_overrides[name] = value
        argv = argv[1:]
    return env_overrides, argv


def _split_stdout_redirect(command):
    """Translate the workflow's trailing ``> /dev/null`` without a shell."""
    argv = list(command)
    if argv[-2:] == [">", "/dev/null"]:
        return argv[:-2], subprocess.DEVNULL
    return argv, subprocess.PIPE


def _trusted_git_executable():
    git = os.path.realpath(_TRUSTED_GIT)
    if not os.path.isfile(git) or not os.access(git, os.X_OK):
        raise ValueError(f"trusted Git executable {git!r} is unavailable")
    return git


def _git_environment():
    return {
        "GIT_CONFIG_COUNT": "0",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }


def _git_top_level(path):
    git = _trusted_git_executable()
    try:
        return os.path.realpath(
            subprocess.check_output(
                [
                    git,
                    "--no-replace-objects",
                    "-C",
                    path,
                    "rev-parse",
                    "--show-toplevel",
                ],
                env=_git_environment(),
                stderr=subprocess.PIPE,
                text=True,
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(
            f"{path!r} is not inside a checked-out Git repository"
        ) from error


def _resolve_repository_root(repository_root):
    requested_root = os.path.realpath(os.path.abspath(repository_root))
    target_root = _git_top_level(requested_root)
    if requested_root != target_root:
        raise ValueError(
            f"gate repository must be the exact Git top level {target_root!r}"
        )
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if workspace is None:
        return target_root
    workspace_root = _git_top_level(os.path.abspath(workspace))
    if workspace_root != target_root:
        raise ValueError(
            "GITHUB_WORKSPACE and the target root identify different Git "
            "repositories"
        )
    return target_root


def _expand_workspace(argv, repository_root):
    return [
        repository_root if argument == "$GITHUB_WORKSPACE" else argument
        for argument in argv
    ]


def _workflow_job_entries(text):
    lines = text.splitlines(keepends=True)
    try:
        jobs_index = next(
            index
            for index, line in enumerate(lines)
            if line.rstrip("\r\n") == "jobs:"
        )
    except StopIteration as error:
        raise ValueError("workflow lacks jobs mapping") from error

    starts = []
    for index in range(jobs_index + 1, len(lines)):
        line = lines[index].rstrip("\r\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            raise ValueError("workflow has content after the jobs mapping")
        if indent != 2:
            continue
        match = re.fullmatch(r"  ([A-Za-z_][A-Za-z0-9_-]*):", line)
        if match is None:
            raise ValueError("workflow uses unsupported job-key syntax")
        starts.append((match.group(1), index))
    names = [name for name, _ in starts]
    if len(names) != len(set(names)):
        raise ValueError("workflow contains duplicate job names")
    return tuple(
        (
            name,
            "".join(
                lines[
                    index + 1 :
                    starts[position + 1][1]
                    if position + 1 < len(starts)
                    else len(lines)
                ]
            ),
        )
        for position, (name, index) in enumerate(starts)
    )


def _parse_workflow_context(text):
    lines = text.splitlines()
    direct = []
    for index, line in enumerate(lines):
        if line == "jobs:":
            direct.append(("jobs", "", index))
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            match = re.fullmatch(
                r"([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
                line,
            )
            if match is None:
                raise ValueError(
                    "workflow uses unsupported top-level key syntax"
                )
            direct.append((match.group(1), match.group(2), index))
    names = [name for name, _, _ in direct]
    if len(names) != len(set(names)):
        raise ValueError("workflow contains duplicate top-level keys")
    if names != ["name", "on", "permissions", "jobs"]:
        raise ValueError(
            "workflow top-level execution context must be exactly "
            "name, on, permissions, and jobs"
        )

    values = {}
    for field_index, (name, raw_value, line_index) in enumerate(direct):
        end = (
            direct[field_index + 1][2]
            if field_index + 1 < len(direct)
            else len(lines)
        )
        value = raw_value.strip()
        nested = [
            line
            for line in lines[line_index + 1 : end]
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if name == "name":
            if value != "Build CI" or nested:
                raise ValueError(
                    "workflow name must be exactly 'Build CI'"
                )
            values[name] = value
        elif name == "on":
            if value or not nested:
                raise ValueError(
                    "workflow on must use the reviewed block mapping"
                )
            values[name] = tuple(
                (
                    len(line) - len(line.lstrip(" ")),
                    line.strip(),
                )
                for line in nested
            )
        elif name == "permissions":
            if value:
                raise ValueError(
                    "workflow permissions must use a block mapping"
                )
            entries = {}
            for line in nested:
                if len(line) - len(line.lstrip(" ")) != 2:
                    raise ValueError(
                        "workflow permissions uses unsupported indentation"
                    )
                match = re.fullmatch(
                    r"  ([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
                    line,
                )
                if match is None:
                    raise ValueError(
                        "workflow permissions uses unsupported key syntax"
                    )
                key = match.group(1)
                if key in entries:
                    raise ValueError(
                        f"workflow permissions repeats key {key!r}"
                    )
                entries[key] = match.group(2).strip()
            permissions = tuple(sorted(entries.items()))
            if permissions != (("contents", "read"),):
                raise ValueError(
                    "workflow permissions must be exactly contents: read"
                )
            values[name] = permissions
        else:
            if value:
                raise ValueError("workflow jobs must use a block mapping")
            values[name] = None
    return tuple((name, values[name]) for name in names)


def _parse_job_environment(lines, start, end, job_name):
    entries = {}
    for line in lines[start:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip(" ")) != 6:
            raise ValueError(
                f"job {job_name!r} env uses unsupported indentation"
            )
        match = re.fullmatch(
            r"      ([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
            line,
        )
        if match is None:
            raise ValueError(
                f"job {job_name!r} env uses unsupported key syntax"
            )
        key = match.group(1)
        if key in entries:
            raise ValueError(f"job {job_name!r} env repeats key {key!r}")
        value = match.group(2).strip()
        if not value:
            raise ValueError(f"job {job_name!r} env.{key} is empty")
        entries[key] = value
    return tuple(sorted(entries.items()))


def _parse_job_context(job_name, body):
    lines = body.splitlines()
    direct = []
    for index, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent != 4 or line.startswith("    -"):
            continue
        match = re.fullmatch(
            r"    ([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
            line,
        )
        if match is None:
            raise ValueError(
                f"job {job_name!r} uses unsupported direct key syntax"
            )
        direct.append((match.group(1), match.group(2), index))
    names = [name for name, _, _ in direct]
    if len(names) != len(set(names)):
        raise ValueError(f"job {job_name!r} contains duplicate direct keys")
    if names != ["runs-on", "timeout-minutes", "env", "steps"]:
        raise ValueError(
            f"job {job_name!r} direct mapping must be exactly "
            "runs-on, timeout-minutes, env, and steps"
        )

    values = {}
    for field_index, (name, raw_value, line_index) in enumerate(direct):
        end = (
            direct[field_index + 1][2]
            if field_index + 1 < len(direct)
            else len(lines)
        )
        value = raw_value.strip()
        nested = [
            line
            for line in lines[line_index + 1 : end]
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if name == "runs-on":
            if value != "ubuntu-latest" or nested:
                raise ValueError(
                    f"job {job_name!r} runs-on must be ubuntu-latest"
                )
            values[name] = value
        elif name == "timeout-minutes":
            if value != "60" or nested:
                raise ValueError(
                    f"job {job_name!r} timeout-minutes must be 60"
                )
            values[name] = value
        elif name == "env":
            if value:
                raise ValueError(
                    f"job {job_name!r} env must use a block mapping"
                )
            environment = _parse_job_environment(
                lines,
                line_index + 1,
                end,
                job_name,
            )
            if environment != _EXPECTED_JOB_ENV[job_name]:
                raise ValueError(
                    f"job {job_name!r} env differs from its reviewed mapping"
                )
            values[name] = environment
        else:
            if value:
                raise ValueError(
                    f"job {job_name!r} steps must use a block sequence"
                )
            values[name] = None
    return tuple((name, values[name]) for name in names)


def _parse_nested_mapping(lines, start, end, field, step_label):
    entries = {}
    for line in lines[start:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if len(line) - len(line.lstrip(" ")) != 8:
            raise ValueError(
                f"{step_label} {field} uses unsupported nested indentation"
            )
        match = re.fullmatch(
            r"        ([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
            line,
        )
        if match is None:
            raise ValueError(
                f"{step_label} {field} uses unsupported mapping-key syntax"
            )
        key = match.group(1)
        if key in entries:
            raise ValueError(f"{step_label} {field} repeats key {key!r}")
        value = match.group(2).strip()
        if not value:
            raise ValueError(f"{step_label} {field}.{key} is empty")
        entries[key] = value
    if not entries:
        raise ValueError(f"{step_label} {field} mapping is empty")
    return tuple(sorted(entries.items()))


def _parse_run_value(lines, start, end, value, step_label):
    if value and value != "|":
        commands = (tuple(shlex.split(value)),)
    elif value == "|":
        parsed = []
        for line in lines[start:end]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if len(line) - len(line.lstrip(" ")) < 8:
                raise ValueError(f"{step_label} run block has invalid indentation")
            parsed.append(tuple(shlex.split(line.strip())))
        commands = tuple(parsed)
    else:
        raise ValueError(f"{step_label} run field is empty")
    if not commands or any(not command for command in commands):
        raise ValueError(f"{step_label} run command is empty")
    return commands


def _parse_step(block, job_name, index):
    lines = block.splitlines()
    first_index = next(
        (
            line_index
            for line_index, line in enumerate(lines)
            if line.strip() and not line.lstrip().startswith("#")
        ),
        None,
    )
    step_label = f"job {job_name!r} step {index}"
    if first_index is None:
        raise ValueError(f"{step_label} is empty")
    first = re.fullmatch(
        r"    -[ \t]+([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
        lines[first_index],
    )
    if first is None:
        raise ValueError(f"{step_label} uses unsupported sequence-key syntax")

    direct = [(first.group(1), first.group(2), first_index)]
    for line_index in range(first_index + 1, len(lines)):
        line = lines[line_index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 6:
            match = re.fullmatch(
                r"      ([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)",
                line,
            )
            if match is None:
                raise ValueError(
                    f"{step_label} uses unsupported direct mapping-key syntax"
                )
            direct.append((match.group(1), match.group(2), line_index))
        elif indent < 8:
            raise ValueError(f"{step_label} uses unsupported step indentation")

    direct_names = [name for name, _, _ in direct]
    if len(direct_names) != len(set(direct_names)):
        raise ValueError(f"{step_label} contains duplicate direct fields")
    unknown = sorted(
        set(direct_names) - {"name", "uses", "run", "env", "with"}
    )
    if unknown:
        raise ValueError(
            f"{step_label} has unsupported direct fields: {', '.join(unknown)}"
        )

    values = {}
    for field_index, (name, raw_value, line_index) in enumerate(direct):
        end = (
            direct[field_index + 1][2]
            if field_index + 1 < len(direct)
            else len(lines)
        )
        value = raw_value.strip()
        if name in {"env", "with"}:
            if value:
                raise ValueError(
                    f"{step_label} {name} must use a block mapping"
                )
            values[name] = _parse_nested_mapping(
                lines,
                line_index + 1,
                end,
                name,
                step_label,
            )
        elif name == "run":
            values[name] = _parse_run_value(
                lines,
                line_index + 1,
                end,
                value,
                step_label,
            )
        else:
            scalar = value.strip()
            if name == "uses":
                scalar = scalar.split(" #", 1)[0].strip()
            if not scalar:
                raise ValueError(f"{step_label} {name} is empty")
            if any(
                line.strip() and not line.lstrip().startswith("#")
                for line in lines[line_index + 1 : end]
            ):
                raise ValueError(
                    f"{step_label} {name} cannot contain nested values"
                )
            values[name] = scalar

    name = values.get("name")
    if name is None:
        if (
            set(values) != {"uses", "with"}
            or values["uses"] != _CHECKOUT_USES
            or values["with"] != _CHECKOUT_WITH
            or index != 0
        ):
            raise ValueError(
                f"{step_label} is an unreviewed unnamed step"
            )
        role = "setup"
    else:
        expected_fields = (
            {"name", "env", "run"}
            if name
            in {
                _WORKFLOW_PILOT_TEST_STEP_NAME,
                _WORKFLOW_PILOT_BASELINE_STEP_NAME,
                "Hydrate workflow-pilot Git authority",
            }
            else {"name", "run"}
        )
        if set(values) != expected_fields:
            raise ValueError(
                f"{step_label} must contain exactly "
                f"{', '.join(sorted(expected_fields))}"
            )
        if "env" in values and values["env"] != tuple(
            sorted(
                tuple(
                    entry.split(": ", 1)
                    if ": " in entry
                    else (entry[:-1], "")
                )
                for entry in _SCRUBBED_PILOT_ENV
            )
        ):
            raise ValueError(
                f"{step_label} changes its reviewed scrubbed environment"
            )
        if name in _NON_GATE_STEP_NAMES:
            role = "setup"
        elif name == _DOCS_GOVERNANCE_STEP_NAME:
            role = "standalone-gate"
        else:
            role = "gate"
    return (
        role,
        name,
        tuple(sorted(values.items())),
    )


def _parse_job_steps(job_name, body):
    lines = body.splitlines(keepends=True)
    step_headers = [
        index
        for index, line in enumerate(lines)
        if re.match(r"^    -(?:[ \t]|\r?\n?\Z)", line)
    ]
    steps_lines = [
        index
        for index, line in enumerate(lines)
        if line.rstrip("\r\n") == "    steps:"
    ]
    if len(steps_lines) != 1:
        raise ValueError(f"job {job_name!r} must have exactly one steps sequence")
    if not step_headers or step_headers[0] <= steps_lines[0]:
        raise ValueError(f"job {job_name!r} steps sequence is empty")
    for line in lines[steps_lines[0] + 1 : step_headers[0]]:
        if line.strip() and not line.lstrip().startswith("#"):
            raise ValueError(
                f"job {job_name!r} has content before its first step"
            )
    blocks = [
        "".join(
            lines[
                start :
                step_headers[index + 1]
                if index + 1 < len(step_headers)
                else len(lines)
            ]
        )
        for index, start in enumerate(step_headers)
    ]
    steps = tuple(
        _parse_step(block, job_name, index)
        for index, block in enumerate(blocks)
    )
    names = [step[1] for step in steps if step[1] is not None]
    if len(names) != len(set(names)):
        raise ValueError(f"job {job_name!r} contains duplicate step names")
    if sum(step[1] is None for step in steps) != 1:
        raise ValueError(
            f"job {job_name!r} must contain exactly one checkout setup step"
        )
    roles = tuple((role, name) for role, name, _ in steps)
    if roles != _EXPECTED_STEP_ROLES[job_name]:
        raise ValueError(
            f"job {job_name!r} step roles and order differ from reviewed setup"
        )
    return steps


def _parse_workflow_structure_text(text):
    workflow_context = _parse_workflow_context(text)
    jobs = _workflow_job_entries(text)
    names = tuple(name for name, _ in jobs)
    if names != _EXPECTED_JOBS:
        raise ValueError(
            "workflow job order must exactly match the reviewed Build jobs"
        )
    blocks = dict(jobs)
    for required in _COMBINED_JOBS:
        if required not in blocks:
            raise ValueError(f"missing candidate Build job {required!r}")
    structures = tuple(
        (
            name,
            _parse_job_context(name, blocks[name]),
            _parse_job_steps(name, blocks[name]),
        )
        for name in _COMBINED_JOBS
    )
    return workflow_context, names, structures


def _workflow_gate_contract(structure):
    commands = []
    _, _, jobs = structure
    for job_name, _, steps in jobs:
        for role, step_name, fields in steps:
            if role not in {"gate", "standalone-gate"}:
                continue
            values = dict(fields)
            run_commands = values["run"]
            if step_name == "Build archival lane without a copyrighted baserom":
                run_commands = tuple(
                    command
                    for command in run_commands
                    if command and command[0] == "make"
                )
            for command in run_commands:
                commands.append((job_name, step_name, command))
    return tuple(commands)


def _parse_workflow_gate_contract_text(text):
    return _workflow_gate_contract(_parse_workflow_structure_text(text))


def _read_workflow_gate_contract(repository_root):
    path = os.path.join(repository_root, _BUILD_WORKFLOW_RELATIVE)
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            raise ValueError(
                f"target Build workflow {path!r} must be a regular file"
            )
        if os.path.commonpath((repository_root, os.path.realpath(path))) != (
            repository_root
        ):
            raise ValueError(
                f"target Build workflow {path!r} escapes the checkout"
            )
        if os.path.getsize(path) > 1024 * 1024:
            raise ValueError(f"Build workflow {path!r} exceeds 1 MiB")
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeError) as error:
        raise ValueError(f"cannot read target Build workflow {path!r}: {error}") from error
    return _parse_workflow_structure_text(text)


def _mirrored_workflow_commands(contract):
    return [
        list(argv)
        for _, step_name, argv in contract
        if step_name != _DOCS_GOVERNANCE_STEP_NAME
    ]


def _require_target_gate_equivalence(repository_root):
    source = _read_workflow_gate_contract(_SOURCE_ROOT)
    target = _read_workflow_gate_contract(repository_root)
    if target != source:
        raise ValueError(
            "target Build workflow gate contract differs from the reviewed "
            "source checkout"
        )
    source_commands = _mirrored_workflow_commands(
        _workflow_gate_contract(source)
    )
    reviewed_commands = [gate.command for gate in gates(jobs=2)]
    if source_commands != reviewed_commands:
        raise ValueError(
            "source Build workflow gate contract differs from reviewed gates"
        )


@dataclass
class Gate:
    name: str
    command: List[str]
    applicable_note: str


def gates(jobs: int = 2) -> List[Gate]:
    """Return the ordered gate list, mirroring build.yml's CI steps.

    Kept as data (not hardcoded shell text) so tests can assert on the exact
    command list without actually executing a multi-minute native build.
    """
    return [
        Gate(
            name="gba-playtest-host-suite",
            command=[
                "GBA_PLAYTEST_HOST_ONLY=1",
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tools/gba-playtest/tests",
                "-v",
            ],
            applicable_note=(
                "issue #13 host lane (build.yml `host-tests` job, textually "
                "first): every tools/gba-playtest host test -- scenario/schema "
                "parsing, generators, config, save/migration fixtures, "
                "timeouts, retry policy, deterministic sorted-JSON output, "
                "provenance/diagnostics. Host-only (build-essential + "
                "libmgba-dev, no arm-none-eabi toolchain); never builds/links "
                "the ROM, so it does not overlap the modern-linker gates below. "
                "GBA_PLAYTEST_HOST_ONLY=1 (mirrored verbatim from build.yml, "
                "and applied to THIS child process only) makes that host-only "
                "scope explicit: the ROM-dependent live-integration tests skip "
                "by mode instead of by whether a git-ignored build artifact "
                "happens to exist, so this gate cannot be perturbed by the "
                "modern-linker/item-expansion gates below rewriting those "
                "artifacts. Live coverage stays with those ROM gates"
            ),
        ),
        Gate(
            name="upstream-port-tests",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests/upstream_port",
                "-v",
            ],
            applicable_note=(
                "issue #12/#15 host lane (same `host-tests` job): pure-stdlib "
                "upstream-port review tooling tests; re-run this suite for "
                "the current count (classify/scan/drift/state/ref-binding/"
                "output-safety/merge-commit determinism and this "
                "verify.gates() <-> build.yml mirror, which excludes only "
                "the standalone documentation-governance step). Python/stdlib "
                "only, links no C and never rebuilds the ROM"
            ),
        ),
        Gate(
            name="workflow-contract-tests",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests/workflows",
                "-p",
                "test_*.py",
                "-v",
            ],
            applicable_note=(
                "fast host lane (same `host-tests` job): stdlib-only static "
                "contracts for the consolidated Build CI job graph. No "
                "compiler, ROM, linker, network, or subordinate runtime gate "
                "is invoked"
            ),
        ),
        Gate(
            name="workflow-pilot-reporter-tests",
            command=[
                "/usr/bin/python3",
                "-I",
                "scripts/workflow_pilot/isolated_launcher.py",
                "reporter-tests",
            ],
            applicable_note=(
                "issue #176 host lane: pure-stdlib workflow-pilot reporter "
                "regression suite, including immutable baseline validation"
            ),
        ),
        Gate(
            name="workflow-pilot-baseline",
            command=[
                "/usr/bin/python3",
                "-I",
                "scripts/workflow_pilot/isolated_launcher.py",
                "baseline",
                "--repository-root",
                "$GITHUB_WORKSPACE",
                "--fixture",
                "scripts/workflow_pilot/tests/fixtures/baseline.json",
                "--decisions",
                ".github/workflow-pilot-decisions.json",
                "--expected",
                "scripts/workflow_pilot/tests/fixtures/baseline_expected.json",
                ">",
                "/dev/null",
            ],
            applicable_note=(
                "issue #176 host lane: validates the frozen workflow-pilot "
                "baseline against checked-out Git history"
            ),
        ),
        Gate(
            name="localization-host-suite",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/localization/tests",
                "-p",
                "test_*.py",
            ],
            applicable_note=(
                "issue #18 host lane addition (same `host-tests` job, "
                "textually after the workflow-pilot gates): the "
                "scripts/localization package's own pure-stdlib unit test "
                "suite (schema/pseudo/catalog/generate/CLI/determinism plus "
                "the host-native resolver-behavior and vanilla-isolation "
                "source-audit tests, which self-skip without a host `cc`). "
                "Python/stdlib only; never builds/links the ROM, so it does "
                "not overlap the localization-runtime-*-check scenarios "
                "reached through the modern-linker gates below"
            ),
        ),
        Gate(
            name="game-localization-width-contract",
            command=["make", "game-localization-test"],
            applicable_note=(
                "issue #18 full-game host lane addition: validates the 3,414 "
                "entry JA/ZH catalog, typed UI/scene width coverage, "
                "metrics-aware generated line breaks, and native text "
                "consumer behavior before the target-ROM gates"
            ),
        ),
        Gate(
            name="game-localization-catalog-check",
            command=["python3", "-m", "scripts.localization.game_locales", "check"],
            applicable_note="Build host lane closure check for the committed full-game locale catalog",
        ),
        Gate(
            name="game-localization-crosswalk-check",
            command=[
                "python3",
                "-m",
                "scripts.localization.game_locales",
                "check-crosswalk",
            ],
            applicable_note="Build host lane closure check for full-game source/catalog crosswalk coverage",
        ),
        Gate(
            name="game-localization-raw-closure-check",
            command=[
                "python3",
                "-m",
                "scripts.localization.game_locales",
                "check-raw-closure",
            ],
            applicable_note="Build host lane closure check for unresolved raw full-game locale content",
        ),
        Gate(
            name="artifact-guard-tests",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/artifact_guard_tests",
                "-p",
                "test_*.py",
                "-v",
            ],
            applicable_note="Build ROM lane host tests for immutable candidate artifact hygiene",
        ),
        Gate(
            name="artifact-guard",
            command=["python3", "scripts/artifact_guard.py", "--revision", "HEAD"],
            applicable_note="always applicable: rejects prohibited tracked build artifacts",
        ),
        # build.yml's "Check documentation (issues #7/#17)" step
        # (scripts/docs_check_tests followed by scripts/check_docs.py --check
        # --check-examples) intentionally has no Gate(...) entry here. It is
        # independently required immediately after the artifact guard.
        Gate(
            name="codeql-alerts-test",
            command=["make", "codeql-alerts-test", "CODEQL_REQUIRE_FANALYZER=1"],
            applicable_note=(
                "issue #84 host/static-analysis gate: runs the sanitizer-backed "
                "SIO, runtime-bound, and PNG harnesses, required GCC analyzer "
                "checks in this CI-equivalent mirror, and affected host-tool builds"
            ),
        ),
        Gate(
            name="default-lane-check",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/modernize/tests",
                "-p",
                "test_build_default_lane.py",
                "-v",
            ],
            applicable_note=(
                "issue #15 closure: asserts a bare `make`/`make all` always "
                "resolves to the modern release AAPCS lane"
            ),
        ),
        Gate(
            name="quickstart-legacy-check",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/modernize/tests",
                "-p",
                "test_quickstart.py",
                "-v",
            ],
            applicable_note=(
                "issue #15 closure: asserts quickstart.sh only reaches the "
                "archival agbcc lane via explicit `make legacy`/`make "
                "fireemblem8.gba`, never via env/CLI variable overrides"
            ),
        ),
        Gate(
            name="generated-data-test",
            command=["make", "generated-data-test"],
            applicable_note="applicable when generated-data schema and cross-reference tests exist",
        ),
        Gate(
            name="generated-data-check",
            command=["make", "generated-data-check"],
            applicable_note="applicable when generated_data.mk-tracked tables exist",
        ),
        Gate(
            name="modern-linker-check-debug",
            command=[
                "make",
                "expansion-modern-linker-check",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                f"-j{jobs}",
            ],
            applicable_note=(
                "aggregates the full modern DEBUG ROM/ELF runtime + linker "
                "suite off a single reused object/ELF build -- the runtime "
                "scenarios are covered here and are NOT re-run individually by "
                "verify, so no gate triggers a second/redundant ROM build. "
                "expansion-modern-linker-check depends on -budget-check, "
                "-overlay-audit (-> -relocs), -boot-check, -title-check, "
                "-debugtools-check/-timer-check/-map-check/-tools-check, "
                "-debugtools-prep-check, -debugtools-ch4prep-check, "
                "-newgame-check, -combat-check, -saveload-check (incl. the "
                "suspend/resume save scenario), -savefmt-check (save-format "
                "migration) and -shifted-check, then runs the shift/offset "
                "address scan and the raw-pointer cast audit. Net coverage: "
                "boot, title, new-game, map, prep, combat, save-load, "
                "suspend/resume, debugtools-tools, save migration, budget, "
                "shift/offset, raw-pointer, relocation and cross-overlay"
            ),
        ),
        Gate(
            name="modern-linker-check-release",
            command=[
                "make",
                "expansion-modern-linker-check",
                "MODERN_CONFIG=release",
                "MODERN_ABI=aapcs",
                f"-j{jobs}",
            ],
            applicable_note=(
                "release-config counterpart of the debug gate above: the same "
                "aggregated runtime + linker suite off the reused RELEASE "
                "object/ELF build, additionally exercising the release "
                "debugtools-disabled negative scenarios. Runtime scenarios are "
                "covered here, not re-run individually by verify"
            ),
        ),
        Gate(
            name="modern-itemexpansion-check-debug",
            command=[
                "FE8_ITEM_ID_CAP=0xCE",
                "FE8_EXPANSION_ITEMTEST=1",
                "make",
                "expansion-modern-itemexpansion-check",
                "MODERN_CONFIG=debug",
                "MODERN_ABI=aapcs",
                "EXPANSION_STARTER_CONTENT=1",
                "EXPANSION_MECHANICS_HOOKS=1",
                "EXPANSION_MECHANICS_SAMPLE=1",
                f"-j{jobs}",
            ],
            applicable_note=(
                "issue #10 acceptance (build.yml ROM `build` job, after the two "
                "default-cap modern-linker gates above -- never the host lane): "
                "boots the real modern debug ROM at an expanded item cap (0xCE, "
                "FE8_EXPANSION_ITEMTEST=1) and runs the item-ID-expansion runtime "
                "probe (expansion-modern-itemexpansion-check). The same single "
                "ROM build also carries the issue #6 bundled-content profile "
                "(EXPANSION_STARTER_CONTENT=1 + hooks + sample), so the authored "
                "content record and its public-registry mechanic are asserted by "
                "this same probe run -- no extra gate and no extra ROM build"
            ),
        ),
        Gate(
            name="modern-itemexpansion-check-release",
            command=[
                "FE8_ITEM_ID_CAP=0xCE",
                "FE8_EXPANSION_ITEMTEST=1",
                "make",
                "expansion-modern-itemexpansion-check",
                "MODERN_CONFIG=release",
                "MODERN_ABI=aapcs",
                "EXPANSION_STARTER_CONTENT=1",
                "EXPANSION_MECHANICS_HOOKS=1",
                "EXPANSION_MECHANICS_SAMPLE=1",
                f"-j{jobs}",
            ],
            applicable_note=(
                "release-config counterpart of the item-expansion debug gate above, "
                "and the final step of build.yml ROM `build` job"
            ),
        ),
        Gate(
            name="modern-all-locales-all-features-profile",
            command=["make", "expansion-modern-map-menu-presentation-check", "-j1"],
            applicable_note=(
                "issue #49 trusted-patch preflight: builds and validates the "
                "isolated release/AAPCS 32 MiB all-production-locales and "
                "maximal-supported-features profile, then runs issue #168's "
                "deterministic map-menu presentation scenario without reading "
                "a base image, creating a patch, or publishing an artifact"
            ),
        ),
        Gate(
            name="cjk-font-gates",
            command=["make", "-f", "cjk_fonts.mk", "cjk-fonts-check", "cjk-fonts-test"],
            applicable_note="combined-gate unique CJK font inventory and codec coverage",
        ),
        Gate(
            name="multilang-codec-gates",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/texttools/tests",
                "-p",
                "test_multilang_codec*.py",
                "-v",
            ],
            applicable_note="combined-gate unique multilang texttools codec coverage",
        ),
        Gate(
            name="expansion-config-gates",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/modernize/tests",
                "-p",
                "test_expansion_config.py",
                "-v",
            ],
            applicable_note="combined-gate unique expansion configuration coverage",
        ),
        Gate(
            name="linker-budget-gates",
            command=[
                "python3",
                "-m",
                "unittest",
                "discover",
                "-s",
                "scripts/linker_report/tests",
                "-p",
                "test_*.py",
                "-v",
            ],
            applicable_note="combined-gate unique linker-budget coverage",
        ),
        Gate(
            name="legacy-build",
            command=["make", "legacy", "-j2"],
            applicable_note="combined-gate archival no-baserom build",
        ),
        Gate(
            name="legacy-payload-identity",
            command=["make", "-C", "mgfembp", "compare"],
            applicable_note="combined-gate archival payload identity comparison",
        ),
    ]


@dataclass
class GateResult:
    gate: Gate
    ran: bool
    returncode: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.ran and self.returncode == 0


def run_gates(
    repository_root: str,
    jobs: int = 2,
    dry_run: bool = False,
) -> List[GateResult]:
    """Execute (or, if dry_run, just describe) every gate at the exact target
    repository root, in the fixed order returned by `gates()`.

    Stops at the first failing gate (fail-fast, matching CI). Never
    weakens, reorders, or skips a gate. There is intentionally no gate
    *selection* capability here (no `selected`/subset parameter): closure
    evidence for this tool is only ever the full, ordered gate set --
    partial/unknown/zero-gate "success" is a forged closure signal, not a
    real one. (See docs/upstream-porting.md and cli.py -- the public
    `verify` subcommand has no `--gate` flag for the same reason; this
    function has no internal escape hatch a caller could use to bypass
    that either.)
    """
    results: List[GateResult] = []
    repository_root = _resolve_repository_root(repository_root)
    _require_target_gate_equivalence(repository_root)
    for gate in gates(jobs=jobs):
        if dry_run:
            results.append(GateResult(gate=gate, ran=False, returncode=0, stdout="", stderr=""))
            continue
        env_overrides, argv = _split_env_prefix(gate.command)
        argv, stdout = _split_stdout_redirect(argv)
        argv = _expand_workspace(argv, repository_root)
        child_env = None
        if env_overrides:
            child_env = dict(os.environ)
            child_env.update(env_overrides)
        proc = subprocess.run(
            argv,
            cwd=repository_root,
            env=child_env,
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
        )
        result = GateResult(
            gate=gate,
            ran=True,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr,
        )
        results.append(result)
        if not result.passed:
            break
    return results
