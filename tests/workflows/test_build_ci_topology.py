"""Structural contract for consolidated candidate and master Build CI."""

from __future__ import annotations

import fnmatch
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.workflow_pilot import hydrate_authority, reporter


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"
PYTHON_REQUIREMENTS = ROOT / ".github" / "requirements" / "build.txt"
RETIRED_WORKFLOW_FILENAME = "full" + "-matrix.yml"
RETIRED_WORKFLOW = ROOT / ".github" / "workflows" / RETIRED_WORKFLOW_FILENAME
MAKEFILE = ROOT / "Makefile"
MASTER_PUBLISHER_CONDITION = (
    "${{ github.event_name == 'push' && github.ref == 'refs/heads/master' }}"
)
COMBINED_WORKERS = ("host-tests", "build", "extended-host-tests", "legacy")
INDEPENDENT_JOBS = COMBINED_WORKERS + ("patch-release",)
SUMMARY_NEEDS = "needs: [host-tests, build, extended-host-tests, legacy]"
HASHED_PIP_INSTALL = (
    "python3 -m pip install --require-hashes --only-binary=:all: --no-deps "
    "-r .github/requirements/build.txt"
)
EXPECTED_HASHED_REQUIREMENTS = {
    "numpy": (
        "2.5.2",
        "sha256:3cdec01fa790a186d430433fdd4d4ffb70eed6f0eeb4bf05c8dbe2dce0a9bcb8",
    ),
    "pillow": (
        "12.3.0",
        "sha256:78cb2c6865a35ab8ff8b75fd122f6033b92a62c82801110e48ddd6c936a45d91",
    ),
    "ttp": (
        "0.10.1",
        "sha256:2c8bc871f7740b690c6df6fb8c9633be58fcda123eea3e53be40a79e4af54b83",
    ),
}
PIP_INVOCATION_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"(?:python(?:3(?:\.[0-9]+)?)?\s+-m\s+pip|pip(?:3(?:\.[0-9]+)?)?)"
    r"(?=\s|$)"
)
PULL_REQUEST_TRIGGER = "  pull_request:\n"
PULL_REQUEST_ACTIONS = ("opened", "synchronize", "reopened", "edited")
PUSH_TRIGGER = 'push:\n    branches: [ "master" ]'
SUMMARY_RESULTS = (
    '"$HOST_TESTS_RESULT"',
    '"$BUILD_RESULT"',
    '"$EXTENDED_HOST_TESTS_RESULT"',
    '"$LEGACY_RESULT"',
)
MAP_MENU_PRESENTATION_GATE = (
    "make expansion-modern-map-menu-presentation-check -j1"
)
WORKFLOW_PILOT_GATE = (
    "/usr/bin/python3 -m unittest discover -s scripts/workflow_pilot/tests "
    "-p 'test_*.py' -v"
)
WORKFLOW_PILOT_BASELINE_GATE = (
    "/usr/bin/python3 -m scripts.workflow_pilot.reporter "
    '--repository-root "$GITHUB_WORKSPACE" '
    "--fixture scripts/workflow_pilot/tests/fixtures/baseline.json "
    "--decisions .github/workflow-pilot-decisions.json "
    "--expected scripts/workflow_pilot/tests/fixtures/baseline_expected.json "
    "> /dev/null"
)
EXPECTED_BUILD_SHA_EXPRESSION = (
    "${{ github.event_name == 'pull_request' && "
    "github.event.pull_request.head.sha || github.sha }}"
)
HOST_ENV_LINE = f"      EXPECTED_BUILD_SHA: {EXPECTED_BUILD_SHA_EXPRESSION}"
COMBINED_JOB_ENV = {
    "host-tests": (HOST_ENV_LINE,),
    "build": (HOST_ENV_LINE,),
    "extended-host-tests": (HOST_ENV_LINE,),
    "legacy": (
        HOST_ENV_LINE,
        "      AGBCC_COMMIT: da598c1d918402c42c0c0d7128ba14567f3175e9",
        "      MGFEMBP_AGBCC_COMMIT: 63b22f3eb8a8051af30bd80c4795b355e439e7ef",
    ),
}
WORKFLOW_PILOT_AUTHORITY_HYDRATION = (
    "/usr/bin/python3 -m scripts.workflow_pilot.hydrate_authority "
    '--repository-root "$GITHUB_WORKSPACE" '
    "--fixture scripts/workflow_pilot/tests/fixtures/baseline.json "
    '--expected-head "$EXPECTED_BUILD_SHA"'
)
SCRUBBED_STEP_ENV = (
    "        BASH_ENV: ''",
    "        ENV: ''",
    "        PATH: /usr/bin:/bin",
    "        PYTHONPATH: ''",
)


def _trigger_block(header: str, event_name: str) -> str:
    event = re.search(
        rf"^  {re.escape(event_name)}:[ \t]*(?P<inline>.*)$",
        header,
        re.MULTILINE,
    )
    if event is None:
        raise ValueError(f"missing {event_name} trigger")
    if event.group("inline"):
        raise ValueError(f"{event_name} trigger must use a block mapping")

    next_event = re.search(r"^  [A-Za-z_][A-Za-z0-9_-]*:", header[event.end():], re.MULTILINE)
    end = event.end() + next_event.start() if next_event is not None else len(header)
    return header[event.end():end]


def _flow_sequence(block: str, field: str) -> tuple[str, ...] | None:
    key = rf"(?:{re.escape(field)}|\"{re.escape(field)}\"|'{re.escape(field)}')"
    sequence = re.search(
        rf"^    {key}[ \t]*:[ \t]*\[(?P<values>[^\]]*)\][ \t]*$",
        block,
        re.MULTILINE,
    )
    if sequence is None:
        if re.search(rf"^    {key}[ \t]*:", block, re.MULTILINE):
            raise ValueError(f"{field} must use the reviewed flow sequence")
        return None

    values = tuple(
        value.strip().strip("\"'")
        for value in sequence.group("values").split(",")
        if value.strip()
    )
    if not values or any(not value for value in values):
        raise ValueError(f"{field} is empty")
    return values


def _pull_request_actions(header: str) -> tuple[str, ...]:
    block = _trigger_block(header, "pull_request")
    for field in ("branches", "branches-ignore"):
        key = rf"(?:{re.escape(field)}|\"{re.escape(field)}\"|'{re.escape(field)}')"
        if re.search(rf"^    {key}[ \t]*:", block, re.MULTILINE):
            raise ValueError(
                "pull_request must not define branches or branches-ignore filters"
            )

    actions = _flow_sequence(block, "types")
    if (
        actions is None
        or len(actions) != len(PULL_REQUEST_ACTIONS)
        or set(actions) != set(PULL_REQUEST_ACTIONS)
    ):
        raise ValueError(
            "pull_request types must be opened, synchronize, reopened, and edited"
        )
    return actions


def _push_branches(header: str) -> tuple[str, ...] | None:
    return _flow_sequence(_trigger_block(header, "push"), "branches")


def _event_branch(event: dict) -> str:
    if event["event_name"] == "pull_request":
        return event["pull_request"]["base"]["ref"]
    prefix = "refs/heads/"
    ref = event["ref"]
    return ref[len(prefix):] if ref.startswith(prefix) else ref


def _triggered_jobs(text: str, event: dict) -> set[str]:
    header = text[: text.index("\njobs:\n")]
    try:
        if event["event_name"] == "pull_request":
            actions = _pull_request_actions(header)
            if event["action"] not in actions:
                return set()
            branches = None
        elif event["event_name"] == "push":
            branches = _push_branches(header)
        else:
            return set()
    except ValueError:
        return set()
    if branches is not None and not any(
        fnmatch.fnmatchcase(_event_branch(event), pattern) for pattern in branches
    ):
        return set()

    jobs = set(_job_blocks(text))
    if not (
        event["event_name"] == "push"
        and event["ref"] == "refs/heads/master"
    ):
        jobs.discard("patch-release")
    return jobs


def _job_blocks(text: str) -> dict[str, str]:
    jobs_start = text.index("\njobs:\n") + len("\njobs:\n")
    jobs_text = text[jobs_start:]
    matches = list(
        re.finditer(r"^  (?P<name>[A-Za-z][A-Za-z0-9_-]*):\n", jobs_text, re.MULTILINE)
    )
    return {
        match.group("name"): jobs_text[
            match.end(): matches[index + 1].start() if index + 1 < len(matches) else len(jobs_text)
        ]
        for index, match in enumerate(matches)
    }


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _run_block_commands(job: str) -> list[str]:
    lines = job.splitlines()
    commands = []
    index = 0
    while index < len(lines):
        inline = re.match(r"^    - run[ \t]*:[ \t]+(?P<value>.+)$", lines[index])
        field = re.match(r"^      run[ \t]*:[ \t]+(?P<value>.+)$", lines[index])
        match = inline or field
        if match is None:
            index += 1
            continue
        if re.fullmatch(
            r"[|>](?:(?:[1-9][+-]?)|(?:[+-][1-9]?))?",
            match.group("value"),
        ):
            index += 1
            block = []
            while index < len(lines) and lines[index].startswith("        "):
                line = lines[index].strip()
                if line and not line.startswith("#"):
                    block.append(line)
                index += 1
            commands.extend(block)
            continue
        value = match.group("value").strip()
        if value and not value.startswith("#"):
            commands.append(value)
        index += 1
    return commands


def _contains_command(job: str, command: str) -> bool:
    return any(
        _normalise(command) in _normalise(run)
        for run in _run_block_commands(job)
    )


def _step_blocks(job: str) -> list[str]:
    matches = list(re.finditer(r"^    -(?:[ \t]|\Z)", job, re.MULTILINE))
    return [
        job[
            match.start():
            matches[index + 1].start() if index + 1 < len(matches) else len(job)
        ]
        for index, match in enumerate(matches)
    ]


def _direct_step_mapping_fields(step: str) -> list[str] | None:
    sequence_key = re.compile(
        r"^    -[ \t]+(?P<field>[A-Za-z_][A-Za-z0-9_-]*)[ \t]*:"
    )
    continuation_key = re.compile(
        r"^      (?P<field>[A-Za-z_][A-Za-z0-9_-]*)[ \t]*:"
    )
    fields = []
    sequence_entries = 0
    for line in step.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 4:
            match = sequence_key.match(line)
            if match is None:
                return None
            sequence_entries += 1
            fields.append(match.group("field"))
        elif indent == 6:
            match = continuation_key.match(line)
            if match is None:
                return None
            fields.append(match.group("field"))
        elif indent < 8:
            return None
    if sequence_entries != 1:
        return None
    return fields


def _contains_exact_command(job: str, command: str) -> bool:
    expected = _normalise(command)
    for step in _step_blocks(job):
        commands = _run_block_commands(step)
        if len(commands) != 1 or _normalise(commands[0]) != expected:
            continue
        fields = _direct_step_mapping_fields(step)
        if fields is not None and (
            (len(fields) == 2 and set(fields) == {"name", "run"})
            or (
                len(fields) == 3
                and set(fields) == {"name", "env", "run"}
                and _step_has_scrubbed_environment(step)
            )
        ):
            return True
    return False


def _step_has_scrubbed_environment(step: str) -> bool:
    lines = step.splitlines()
    try:
        env_index = lines.index("      env:")
    except ValueError:
        return False
    entries = []
    for line in lines[env_index + 1 :]:
        if line.strip() and len(line) - len(line.lstrip(" ")) <= 6:
            break
        if line.strip() and not line.lstrip().startswith("#"):
            entries.append(line)
    return tuple(entries) == SCRUBBED_STEP_ENV


def _step_name(step: str) -> str | None:
    match = re.search(r"^    - name: (?P<name>.+)$", step, re.MULTILINE)
    return match.group("name") if match is not None else None


def _checkout_step_is_exact(step: str) -> bool:
    action = (
        "    - uses: actions/checkout@"
        "3d3c42e5aac5ba805825da76410c181273ba90b1"
    )
    action_lines = [
        line.split(" #", 1)[0]
        for line in step.splitlines()
        if line.startswith("    - uses:")
    ]
    if action_lines != [action]:
        return False
    if _direct_step_mapping_fields(step) != ["uses", "with"]:
        return False
    expected = (
        "        ref: ${{ github.event_name == 'pull_request' && "
        "github.event.pull_request.head.sha || github.sha }}",
        "        fetch-depth: 0",
        "        submodules: recursive",
        "        persist-credentials: false",
    )
    lines = step.splitlines()
    try:
        with_index = lines.index("      with:")
    except ValueError:
        return False
    entries = []
    for line in lines[with_index + 1 :]:
        if line.strip() and len(line) - len(line.lstrip(" ")) <= 6:
            break
        if line.strip() and not line.lstrip().startswith("#"):
            entries.append(line)
    return tuple(entries) == expected


def _run_step_is_exact(
    step: str,
    name: str,
    commands: tuple[str, ...],
    scrubbed: bool = False,
) -> bool:
    if _step_name(step) != name:
        return False
    if tuple(_run_block_commands(step)) != commands:
        return False
    fields = _direct_step_mapping_fields(step)
    expected_fields = (
        {"name", "env", "run"} if scrubbed else {"name", "run"}
    )
    if fields is None or len(fields) != len(expected_fields):
        return False
    if set(fields) != expected_fields:
        return False
    return not scrubbed or _step_has_scrubbed_environment(step)


def _protected_host_prefix_errors(host: str) -> list[str]:
    steps = _step_blocks(host)
    if len(steps) < 9:
        return ["host-tests lacks the complete protected pre-pilot sequence"]
    expected = (
        _checkout_step_is_exact(steps[0]),
        _run_step_is_exact(
            steps[1],
            "Verify checked-out revision",
            (
                'ACTUAL_SHA="$(git rev-parse HEAD)"',
                "printf 'checkout.sha=%s\\n' \"$ACTUAL_SHA\"",
                'test "$ACTUAL_SHA" = "$EXPECTED_BUILD_SHA"',
            ),
        ),
        _run_step_is_exact(
            steps[2],
            "Hydrate workflow-pilot Git authority",
            (WORKFLOW_PILOT_AUTHORITY_HYDRATION,),
            scrubbed=True,
        ),
        _run_step_is_exact(
            steps[3],
            "Install host-only dependencies (no arm-none-eabi toolchain)",
            (
                "sudo apt-get update && sudo apt-get install -y "
                "build-essential libmgba-dev",
            ),
        ),
        _run_step_is_exact(
            steps[4],
            "Run gba-playtest host test suite",
            (
                "GBA_PLAYTEST_HOST_ONLY=1 python3 -m unittest discover "
                "-s tools/gba-playtest/tests -v",
            ),
        ),
        _run_step_is_exact(
            steps[5],
            "Run upstream-port tooling test suite",
            ("python3 -m unittest discover -s tests/upstream_port -v",),
        ),
        _run_step_is_exact(
            steps[6],
            "Run workflow contract test suite",
            (
                "python3 -m unittest discover -s tests/workflows "
                '-p "test_*.py" -v',
            ),
        ),
        _run_step_is_exact(
            steps[7],
            "Run workflow-pilot reporter regression suite (issue #176)",
            (WORKFLOW_PILOT_GATE,),
            scrubbed=True,
        ),
        _run_step_is_exact(
            steps[8],
            "Validate workflow-pilot baseline against checked-out Git history",
            (WORKFLOW_PILOT_BASELINE_GATE,),
            scrubbed=True,
        ),
    )
    if all(expected):
        return []
    return [
        "host-tests protected pre-pilot step sequence differs from reviewed "
        "actions, commands, fields, order, or scrubbed environments"
    ]


def _has_execution_defaults(text: str, workflow_scope: bool) -> bool:
    indent = "" if workflow_scope else r" {4,}"
    key = r"(?:defaults|\"defaults\"|'defaults')"
    return re.search(
        rf"^{indent}{key}[ \t]*:",
        text,
        re.MULTILINE,
    ) is not None


def _has_unsupported_direct_key(text: str, indent: int, allow_sequence: bool) -> bool:
    simple_key = re.compile(
        rf"^{' ' * indent}[A-Za-z_][A-Za-z0-9_-]*[ \t]*:"
    )
    sequence = re.compile(
        rf"^{' ' * indent}-[ \t]+[A-Za-z_][A-Za-z0-9_-]*[ \t]*:"
    )
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        line_indent = len(line) - len(line.lstrip(" "))
        if line_indent != indent:
            continue
        if simple_key.match(line):
            continue
        if allow_sequence and sequence.match(line):
            continue
        return True
    return False


def _has_direct_key(text: str, indent: int, key: str) -> bool:
    return re.search(
        rf"^{' ' * indent}{re.escape(key)}[ \t]*:",
        text,
        re.MULTILINE,
    ) is not None


def _host_environment_errors(job: str) -> list[str]:
    lines = job.splitlines()
    env_indices = [
        index for index, line in enumerate(lines) if line == "    env:"
    ]
    if len(env_indices) != 1:
        return ["host-tests must define exactly one reviewed env mapping"]
    index = env_indices[0] + 1
    entries = []
    while index < len(lines):
        line = lines[index]
        if line.strip() and len(line) - len(line.lstrip(" ")) <= 4:
            break
        if line.strip() and not line.lstrip().startswith("#"):
            entries.append(line)
        index += 1
    if entries != [HOST_ENV_LINE]:
        return [
            "host-tests env must contain only the reviewed EXPECTED_BUILD_SHA"
        ]
    return []


def _combined_job_contract_errors(job_name: str, job: str) -> list[str]:
    direct_lines = []
    for line in job.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent != 4 or line.startswith("    -"):
            continue
        direct_lines.append(line)
    expected_direct = [
        "    runs-on: ubuntu-latest",
        "    timeout-minutes: 60",
        "    env:",
        "    steps:",
    ]
    errors = []
    if direct_lines != expected_direct:
        errors.append(
            f"{job_name} direct job mapping differs from the reviewed "
            "runs-on, timeout, env, and steps contract"
        )

    lines = job.splitlines()
    try:
        env_index = lines.index("    env:")
    except ValueError:
        return errors + [f"{job_name} lacks its reviewed env mapping"]
    entries = []
    for line in lines[env_index + 1 :]:
        if line.strip() and len(line) - len(line.lstrip(" ")) <= 4:
            break
        if line.strip() and not line.lstrip().startswith("#"):
            entries.append(line)
    if tuple(entries) != COMBINED_JOB_ENV[job_name]:
        errors.append(f"{job_name} env differs from its reviewed exact mapping")
    return errors


def _hashed_requirements_errors(text: str) -> list[str]:
    logical_lines = []
    current = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        current = f"{current} {line}".strip()
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        logical_lines.append(current)
        current = ""

    errors = []
    if current:
        errors.append("unterminated requirement continuation")

    records = {}
    for line in logical_lines:
        fields = line.split()
        if not fields or "==" not in fields[0]:
            errors.append(f"invalid requirement record: {line}")
            continue
        name, version = fields[0].split("==", 1)
        hashes = [field.removeprefix("--hash=") for field in fields[1:]]
        if any(not field.startswith("--hash=sha256:") for field in fields[1:]):
            errors.append(f"{name} has a non-SHA256 requirement option")
            continue
        if len(hashes) != 1:
            errors.append(f"{name} must have exactly one reviewed wheel hash")
            continue
        if name in records:
            errors.append(f"duplicate requirement: {name}")
            continue
        records[name] = (version, hashes[0])

    if records != EXPECTED_HASHED_REQUIREMENTS:
        errors.append("Build Python requirements differ from reviewed versions/hashes")
    return errors


def _make_recipe(text: str, target: str) -> str:
    match = re.search(
        rf"^{re.escape(target)}:\n(?P<recipe>(?:\t.*\n?)*)",
        text,
        re.MULTILINE,
    )
    if match is None:
        raise AssertionError(f"missing Make target: {target}")
    return match.group("recipe")


def _errors(text: str, retired_workflow_exists: bool) -> list[str]:
    errors = []
    header = text[: text.index("\njobs:\n")]
    if _has_unsupported_direct_key(header, indent=0, allow_sequence=False):
        errors.append("workflow uses unsupported direct mapping-key syntax")
    if _has_execution_defaults(header, workflow_scope=True):
        errors.append("workflow execution defaults must not alter candidate gates")
    if _has_direct_key(header, indent=0, key="env"):
        errors.append("workflow-level env is forbidden")
    try:
        _pull_request_actions(header)
    except ValueError as exc:
        errors.append(f"Build pull-request trigger is invalid: {exc}")
    try:
        push_branches = _push_branches(header)
    except ValueError as exc:
        errors.append(f"Build push trigger is invalid: {exc}")
    else:
        if push_branches != ("master",):
            errors.append("Build pushes must remain restricted to master")
    if "workflow_dispatch" in header:
        errors.append("Build must not expose a manual retired-workflow trigger")
    if retired_workflow_exists:
        errors.append("the retired standalone CI workflow must be deleted")

    jobs = _job_blocks(text)
    expected_jobs = {
        "host-tests",
        "build",
        "extended-host-tests",
        "legacy",
        "patch-release",
        "summary",
    }
    if set(jobs) != expected_jobs:
        errors.append(f"Build job set differs from consolidated contract: {sorted(jobs)}")
        return errors

    for job_name, job in jobs.items():
        for command in _run_block_commands(job):
            words = set(command.split())
            if "apt-get" in words and "libpng-dev" in words and "pkg-config" not in words:
                errors.append(f"{job_name} installs libpng-dev without pkg-config")
        pip_invocations = [
            command
            for command in _run_block_commands(job)
            for _match in PIP_INVOCATION_RE.finditer(command)
        ]
        if job_name in ("build", "patch-release"):
            if len(pip_invocations) != 1 or _normalise(pip_invocations[0]) != _normalise(
                HASHED_PIP_INSTALL
            ):
                errors.append(f"{job_name} must use the reviewed hash-locked Python requirements")
        elif pip_invocations:
            errors.append(f"{job_name} adds an unreviewed Python package install")

    for job_name in COMBINED_WORKERS:
        if _has_unsupported_direct_key(
            jobs[job_name],
            indent=4,
            allow_sequence=True,
        ):
            errors.append(
                f"{job_name} uses unsupported direct mapping-key syntax"
            )
        if _has_direct_key(jobs[job_name], indent=4, key="if"):
            errors.append(f"{job_name} must run for pull-request candidates and master pushes")
        if _has_direct_key(
            jobs[job_name],
            indent=4,
            key="continue-on-error",
        ):
            errors.append(f"{job_name} must not be advisory")
        errors.extend(_combined_job_contract_errors(job_name, jobs[job_name]))

    errors.extend(_host_environment_errors(jobs["host-tests"]))
    errors.extend(_protected_host_prefix_errors(jobs["host-tests"]))

    if f"if: {MASTER_PUBLISHER_CONDITION}" not in jobs["patch-release"]:
        errors.append("patch-release must remain master-push-only")
    for job_name in INDEPENDENT_JOBS:
        if "needs:" in jobs[job_name]:
            errors.append(f"{job_name} must not create a serial Build critical path")

    summary = jobs["summary"]
    if "if: always()" not in summary:
        errors.append("summary must run after failed combined jobs on both triggers")
    if SUMMARY_NEEDS not in summary:
        errors.append("summary must depend on every required combined Build job")
    loop = summary[summary.index("for result") : summary.index("done", summary.index("for result"))]
    if '[ "$result" != "success" ]' not in loop:
        errors.append("summary loop must fail closed")
    for result in SUMMARY_RESULTS:
        if result not in loop:
            errors.append(f"summary loop omits required result: {result}")

    extended_host = jobs["extended-host-tests"]
    for command in (
        "make -f cjk_fonts.mk cjk-fonts-check cjk-fonts-test",
        "python3 -m unittest discover -s scripts/texttools/tests -p 'test_multilang_codec*.py' -v",
        "python3 -m unittest discover -s scripts/modernize/tests -p 'test_expansion_config.py' -v",
        "python3 -m unittest discover -s scripts/linker_report/tests -p 'test_*.py' -v",
    ):
        if not _contains_command(extended_host, command):
            errors.append(f"extended host lost unique evidence: {command}")

    for duplicate in (
        "scripts/artifact_guard",
        "scripts/docs_check_tests",
        "make generated-data",
        "scripts.localization.game_locales",
        "make game-localization-test",
        "expansion-modern-linker-check",
    ):
        if _contains_command(extended_host, duplicate):
            errors.append(f"extended host repeats Build-owned evidence: {duplicate}")

    for command in (
        "scripts.localization.game_locales check-crosswalk",
        "scripts.localization.game_locales check-raw-closure",
    ):
        if not _contains_command(jobs["host-tests"], command):
            errors.append(f"candidate host lost Build-owned evidence: {command}")
    for command in (WORKFLOW_PILOT_GATE, WORKFLOW_PILOT_BASELINE_GATE):
        if not _contains_exact_command(jobs["host-tests"], command):
            errors.append(
                f"candidate host lost exact fail-closed Build evidence: {command}"
            )
    if not _contains_exact_command(
        jobs["host-tests"],
        WORKFLOW_PILOT_AUTHORITY_HYDRATION,
    ):
        errors.append(
            "candidate host lost exact workflow-pilot Git authority hydration"
        )
    hydration_index = jobs["host-tests"].find(
        "Hydrate workflow-pilot Git authority"
    )
    reporter_index = jobs["host-tests"].find(
        "Run workflow-pilot reporter regression suite (issue #176)"
    )
    if (
        hydration_index < 0
        or reporter_index < 0
        or hydration_index >= reporter_index
    ):
        errors.append(
            "workflow-pilot Git authority hydration must precede reporter tests"
        )
    if _has_execution_defaults(jobs["host-tests"], workflow_scope=False):
        errors.append("candidate host execution defaults must not alter pilot gates")

    legacy = jobs["legacy"]
    for command in ("make legacy -j2", "make -C mgfembp compare"):
        if not _contains_command(legacy, command):
            errors.append(f"legacy job lost unique evidence: {command}")

    build = jobs["build"]
    if not _contains_command(
        build,
        "make codeql-alerts-test CODEQL_REQUIRE_FANALYZER=1",
    ):
        errors.append("build must require analyzer support for codeql-alerts-test")
    for command in (
        "expansion-modern-linker-check MODERN_CONFIG=debug",
        "expansion-modern-linker-check MODERN_CONFIG=release",
    ):
        if not _contains_command(build, command):
            errors.append(f"build lost canonical modern evidence: {command}")
    if not _contains_command(build, MAP_MENU_PRESENTATION_GATE):
        errors.append(
            "build must gate the all-locales profile through map-menu presentation"
        )
    return errors


def _remote_completion_errors(makefile_text: str) -> list[str]:
    recipe = _make_recipe(makefile_text, "remote-completion-check")
    required = (
        "--event push --branch master --commit",
        "--workflow build.yml",
        "requires master, not",
    )
    errors = [f"remote completion lacks {item}" for item in required if item not in recipe]
    if RETIRED_WORKFLOW_FILENAME in recipe:
        errors.append("remote completion still depends on the retired workflow")
    return errors


class ConsolidatedBuildTopologyTests(unittest.TestCase):
    def setUp(self):
        self.text = WORKFLOW.read_text(encoding="utf-8")

    def test_real_workflow_consolidates_candidate_and_master_evidence(self):
        self.assertEqual(_errors(self.text, RETIRED_WORKFLOW.exists()), [])
        self.assertEqual(
            _remote_completion_errors(MAKEFILE.read_text(encoding="utf-8")),
            [],
        )

    def test_protected_environment_is_exact_and_cannot_mask_python(self):
        workflow_env_variants = (
            "env:\n  BASH_ENV: build/python-mask.sh\n",
            '"env":\n  PATH: /untrusted\n',
            '"\\u0065nv":\n  PYTHONPATH: build/mask\n',
            "? env\n:\n  SHELLOPTS: sourcepath\n",
            "!!str env:\n  ENV: build/mask\n",
            "env: &shared {BASH_ENV: build/python-mask.sh}\n",
            "env: {BASH_ENV: build/python-mask.sh}\n",
        )
        for variant in workflow_env_variants:
            with self.subTest(workflow_env=variant):
                changed = self.text.replace(
                    "\npermissions:\n",
                    f"\n{variant}\npermissions:\n",
                    1,
                )
                self.assertTrue(
                    any(
                        "workflow-level env is forbidden" in error
                        or "unsupported direct mapping-key syntax" in error
                        for error in _errors(changed, False)
                    )
                )

        env_block = (
            "    env:\n"
            f"{HOST_ENV_LINE}\n"
        )
        value = EXPECTED_BUILD_SHA_EXPRESSION
        host_env_variants = (
            f"    env:\n{HOST_ENV_LINE}\n"
            "      BASH_ENV: build/python-mask.sh\n",
            f"    env:\n{HOST_ENV_LINE}\n      ENV: build/mask\n",
            f"    env:\n{HOST_ENV_LINE}\n      PATH: /untrusted\n",
            f"    env:\n{HOST_ENV_LINE}\n      PYTHONPATH: build/mask\n",
            f"    env:\n{HOST_ENV_LINE}\n      SHELLOPTS: sourcepath\n",
            f'    env:\n      "EXPECTED_BUILD_SHA": {value}\n',
            f'    env:\n      "EXPECTED_\\u0042UILD_SHA": {value}\n',
            f"    env:\n      ? EXPECTED_BUILD_SHA\n      : {value}\n",
            f"    env:\n      !!str EXPECTED_BUILD_SHA: {value}\n",
            f"    env:\n      <<: *shared\n{HOST_ENV_LINE}\n",
            f"    env: &shared\n{HOST_ENV_LINE}\n",
            f"    env: {{EXPECTED_BUILD_SHA: \"{value}\"}}\n",
            f"    env:\n      EXPECTED_BUILD_SHA: \"{value}\"\n",
            f"    env:\n      EXPECTED_BUILD_SHA: !!str {value}\n",
            f"    env:\n      EXPECTED_BUILD_SHA: &sha {value}\n",
            "    env:\n      EXPECTED_BUILD_SHA: *sha\n",
        )
        for variant in host_env_variants:
            with self.subTest(host_env=variant):
                changed = self.text.replace(env_block, variant, 1)
                self.assertTrue(
                    any(
                        "host-tests env must contain only" in error
                        or "exactly one reviewed env mapping" in error
                        or "unsupported direct mapping-key syntax" in error
                        for error in _errors(changed, False)
                    )
                )

        masked = self.text.replace(
            env_block,
            f"    env:\n{HOST_ENV_LINE}\n"
            "      BASH_ENV: build/python-mask.sh\n",
            1,
        ).replace(
            "    - name: Run workflow-pilot reporter regression suite",
            "    - name: Prepare Python function mask\n"
            "      run: printf 'python3() { return 0; }\\n' "
            "> build/python-mask.sh\n\n"
            "    - name: Run workflow-pilot reporter regression suite",
            1,
        )
        self.assertTrue(
            any(
                "host-tests env must contain only" in error
                for error in _errors(masked, False)
            )
        )

    def test_every_pre_pilot_step_is_exact_and_cannot_persist_masks(self):
        mutations = (
            self.text.replace(
                "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
                "actions/checkout@main",
                1,
            ),
            self.text.replace(
                "        persist-credentials: false\n",
                "        persist-credentials: true\n",
                1,
            ),
            self.text.replace(
                '        test "$ACTUAL_SHA" = "$EXPECTED_BUILD_SHA"\n',
                '        test "$ACTUAL_SHA" = "$EXPECTED_BUILD_SHA"\n'
                '        echo "BASH_ENV=build/mask" >> "$GITHUB_ENV"\n',
                1,
            ),
            self.text.replace(
                "sudo apt-get update && sudo apt-get install -y "
                "build-essential libmgba-dev",
                "sudo apt-get update && sudo apt-get install -y "
                "build-essential libmgba-dev && "
                'echo build/bin >> "$GITHUB_PATH"',
                1,
            ),
            self.text.replace(
                "GBA_PLAYTEST_HOST_ONLY=1 python3 -m unittest discover "
                "-s tools/gba-playtest/tests -v",
                "true",
                1,
            ),
            self.text.replace(
                "python3 -m unittest discover -s tests/upstream_port -v",
                "python3 -m unittest discover -s tests/upstream_port -v || true",
                1,
            ),
            self.text.replace(
                'python3 -m unittest discover -s tests/workflows -p "test_*.py" -v',
                'python3 -m unittest discover -s tests/workflows -p "test_*.py" '
                '-v && echo "PYTHONPATH=build/mask" >> "$GITHUB_ENV"',
                1,
            ),
            self.text.replace(
                "    - name: Run workflow-pilot reporter regression suite",
                "    - name: Unreviewed setup\n"
                "      run: echo build/bin >> \"$GITHUB_PATH\"\n\n"
                "    - name: Run workflow-pilot reporter regression suite",
                1,
            ),
            self.text.replace(
                "    - name: Run upstream-port tooling test suite",
                "    - uses: actions/setup-python@main\n\n"
                "    - name: Run upstream-port tooling test suite",
                1,
            ),
            self.text.replace(
                "    - name: Run upstream-port tooling test suite",
                "    - name: Run workflow contract test suite",
                1,
            ),
        )
        protected_names = (
            "Verify checked-out revision",
            "Install host-only dependencies (no arm-none-eabi toolchain)",
            "Run gba-playtest host test suite",
            "Run upstream-port tooling test suite",
            "Run workflow contract test suite",
        )
        for name in protected_names:
            mutations += (
                self.text.replace(
                    f"    - name: {name}\n",
                    f"    - name: {name}\n      shell: bash {{0}}\n",
                    1,
                ),
                self.text.replace(
                    f"    - name: {name}\n",
                    f"    - name: {name}\n      working-directory: /\n",
                    1,
                ),
                self.text.replace(
                    f"    - name: {name}\n",
                    f"    - name: {name}\n      continue-on-error: true\n",
                    1,
                ),
                self.text.replace(
                    f"    - name: {name}\n",
                    f"    - name: {name}\n      if: ${{{{ false }}}}\n",
                    1,
                ),
                self.text.replace(
                    f"    - name: {name}\n",
                    f'    - "name": {name}\n',
                    1,
                ),
            )
        for changed in mutations:
            with self.subTest(mutation=changed[:180]):
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "protected pre-pilot step sequence differs" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_protected_pilot_steps_require_exact_scrubbed_environment(self):
        names = (
            "Hydrate workflow-pilot Git authority",
            "Run workflow-pilot reporter regression suite (issue #176)",
            "Validate workflow-pilot baseline against checked-out Git history",
        )
        env_block = "      env:\n" + "\n".join(SCRUBBED_STEP_ENV) + "\n"
        variants = (
            "",
            env_block.replace("        BASH_ENV: ''\n", ""),
            env_block.replace("        ENV: ''\n", ""),
            env_block.replace("        PYTHONPATH: ''\n", ""),
            env_block.replace("        PATH: /usr/bin:/bin\n", ""),
            env_block.replace("        PATH: /usr/bin:/bin", "        PATH: /untrusted"),
            env_block + "        GITHUB_ENV: build/mask\n",
            env_block.replace("      env:", '      "env":'),
            env_block.replace("        BASH_ENV:", "        BASH_ENV :"),
            env_block.replace("        ENV: ''", "        ENV: &mask ''"),
            env_block.replace("        PYTHONPATH: ''", "        <<: *mask"),
        )
        for name in names:
            for variant in variants:
                with self.subTest(name=name, variant=variant):
                    step_start = self.text.index(f"    - name: {name}\n")
                    env_start = self.text.index("      env:\n", step_start)
                    run_start = self.text.index("      run:", env_start)
                    changed = (
                        self.text[:env_start]
                        + variant
                        + self.text[run_start:]
                    )
                    self.assertTrue(
                        any(
                            "protected pre-pilot step sequence differs" in error
                            or "lost exact workflow-pilot" in error
                            for error in _errors(changed, False)
                        )
                    )

    def test_workflow_pilot_authority_hydration_is_exact_and_ordered(self):
        self.assertEqual(hydrate_authority.GIT, "/usr/bin/git")
        self.assertEqual(hydrate_authority.BATCH_SIZE, 256)
        self.assertEqual(
            hydrate_authority.FETCH_OPTIONS,
            (
                "--quiet",
                "--no-tags",
                "--filter=blob:none",
                "--no-write-fetch-head",
            ),
        )
        host = _job_blocks(self.text)["host-tests"]
        self.assertTrue(
            _contains_exact_command(
                host,
                WORKFLOW_PILOT_AUTHORITY_HYDRATION,
            )
        )
        self.assertLess(
            host.index("Hydrate workflow-pilot Git authority"),
            host.index("Run workflow-pilot reporter regression suite"),
        )
        replacements = (
            "true",
            WORKFLOW_PILOT_AUTHORITY_HYDRATION.replace(
                "/usr/bin/python3",
                "python3",
            ),
            WORKFLOW_PILOT_AUTHORITY_HYDRATION.replace(
                "scripts.workflow_pilot.hydrate_authority",
                "scripts.workflow_pilot.reporter",
            ),
            WORKFLOW_PILOT_AUTHORITY_HYDRATION.replace(
                "--fixture scripts/workflow_pilot/tests/fixtures/baseline.json ",
                "",
            ),
            WORKFLOW_PILOT_AUTHORITY_HYDRATION.replace(
                '--expected-head "$EXPECTED_BUILD_SHA"',
                "--remote untrusted",
            ),
        )
        for replacement in replacements:
            with self.subTest(replacement=replacement):
                changed = self.text.replace(
                    f"      run: {WORKFLOW_PILOT_AUTHORITY_HYDRATION}\n",
                    f"      run: {replacement}\n",
                    1,
                )
                self.assertTrue(
                    any(
                        "lost exact workflow-pilot Git authority hydration"
                        in error
                        for error in _errors(changed, False)
                    )
                )

    def test_exact_fixture_hydration_restores_force_pushed_commit(self):
        artifact_root = ROOT / "build" / "test-artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="workflow-authority-hydration-",
            dir=artifact_root,
        ) as temporary:
            root = Path(temporary)
            remote = root / "remote.git"
            checkout = root / "checkout"
            expected_head = subprocess.check_output(
                ["git", "-C", ROOT, "rev-parse", "HEAD"],
                text=True,
            ).strip()

            subprocess.run(
                ["git", "clone", "-q", "--bare", "--shared", ROOT, remote],
                check=True,
                capture_output=True,
            )
            refs = subprocess.check_output(
                [
                    "git",
                    f"--git-dir={remote}",
                    "for-each-ref",
                    "--format=%(refname)",
                ],
                text=True,
            ).splitlines()
            for ref in refs:
                subprocess.run(
                    ["git", f"--git-dir={remote}", "update-ref", "-d", ref],
                    check=True,
                    capture_output=True,
                )
            subprocess.run(
                [
                    "git",
                    f"--git-dir={remote}",
                    "update-ref",
                    "refs/heads/master",
                    expected_head,
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    f"--git-dir={remote}",
                    "config",
                    "uploadpack.allowAnySHA1InWant",
                    "true",
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "init", "-q", "-b", "master", checkout],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    checkout,
                    "remote",
                    "add",
                    "origin",
                    "https://github.com/laqieer/fireemblem8-expansion.git",
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    checkout,
                    "config",
                    f"url.file://{remote}.insteadOf",
                    "https://github.com/laqieer/fireemblem8-expansion.git",
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    checkout,
                    "fetch",
                    "-q",
                    "--depth=1",
                    "origin",
                    expected_head,
                ],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", checkout, "checkout", "-q", "--detach", "FETCH_HEAD"],
                check=True,
                capture_output=True,
            )
            fixture_path = checkout / (
                "scripts/workflow_pilot/tests/fixtures/baseline.json"
            )
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
            historical = next(
                review["commit_sha"]
                for review in fixture["reviews"]
                if review["id"] == 4989066820
            )
            offline_environment = dict(os.environ)
            offline_environment["GIT_NO_LAZY_FETCH"] = "1"
            self.assertNotEqual(
                subprocess.run(
                    ["git", "-C", checkout, "cat-file", "-e", f"{historical}^{{commit}}"],
                    env=offline_environment,
                    check=False,
                    capture_output=True,
                ).returncode,
                0,
            )

            subprocess.run(
                [
                    "git",
                    "-C",
                    checkout,
                    "fetch",
                    "--quiet",
                    "--no-tags",
                    "--filter=blob:none",
                    "origin",
                    "+refs/heads/*:refs/remotes/origin/*",
                ],
                env=offline_environment,
                check=True,
                capture_output=True,
            )
            self.assertNotEqual(
                subprocess.run(
                    [
                        "git",
                        "-C",
                        checkout,
                        "cat-file",
                        "-e",
                        f"{historical}^{{commit}}",
                    ],
                    env=offline_environment,
                    check=False,
                    capture_output=True,
                ).returncode,
                0,
            )
            refs_before = subprocess.check_output(
                ["git", "-C", checkout, "show-ref"],
            )

            result = hydrate_authority.hydrate_authority(
                checkout,
                fixture_path,
                expected_head,
            )
            self.assertEqual(result["required"], len(fixture["commits"]))
            self.assertGreater(result["fetched"], 0)
            subprocess.run(
                ["git", "-C", checkout, "cat-file", "-e", f"{historical}^{{commit}}"],
                check=True,
                capture_output=True,
            )
            self.assertEqual(
                subprocess.check_output(
                    ["git", "-C", checkout, "rev-parse", "HEAD"],
                    text=True,
                ).strip(),
                expected_head,
            )
            self.assertEqual(
                subprocess.check_output(["git", "-C", checkout, "show-ref"]),
                refs_before,
            )
            alternate_fixture = checkout / "build" / "alternate.json"
            alternate_fixture.parent.mkdir(parents=True)
            alternate_fixture.write_text(
                json.dumps(fixture),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                reporter.PilotDataError,
                "--fixture must identify",
            ):
                hydrate_authority.hydrate_authority(
                    checkout,
                    alternate_fixture,
                    expected_head,
                )

    def test_synthetic_stacked_pull_request_runs_candidate_jobs_on_its_real_base(self):
        event = {
            "event_name": "pull_request",
            "action": "opened",
            "pull_request": {
                "base": {"ref": "agent/issue-170"},
                "head": {"sha": "1" * 40},
            },
        }
        self.assertEqual(
            _triggered_jobs(self.text, event),
            set(COMBINED_WORKERS) | {"summary"},
        )
        self.assertNotIn("patch-release", _triggered_jobs(self.text, event))

    def test_pull_request_branch_filters_fail_closed_in_inline_and_block_forms(self):
        event = {
            "event_name": "pull_request",
            "action": "opened",
            "pull_request": {
                "base": {"ref": "agent/issue-170"},
                "head": {"sha": "1" * 40},
            },
        }
        mutations = (
            '    branches: [ "master" ]\n',
            '    branches:\n      - "master"\n',
            '    branches-ignore: [ "agent/**" ]\n',
            '    branches-ignore:\n      - "agent/**"\n',
            '    "branches": [ "master" ]\n',
            "    'branches':\n      - \"master\"\n",
            '    "branches-ignore": [ "agent/**" ]\n',
            "    'branches-ignore':\n      - \"agent/**\"\n",
            '    branches : [ "master" ]\n',
            '    "branches-ignore" : [ "agent/**" ]\n',
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                changed = self.text.replace(
                    PULL_REQUEST_TRIGGER,
                    PULL_REQUEST_TRIGGER + mutation,
                    1,
                )
                self.assertTrue(
                    any(
                        "must not define branches or branches-ignore filters" in error
                        for error in _errors(changed, False)
                    )
                )
                self.assertEqual(_triggered_jobs(changed, event), set())

    def test_candidate_pull_request_activity_types_are_explicit_and_fail_closed(self):
        for action in ("opened", "synchronize", "reopened"):
            with self.subTest(action=action):
                event = {
                    "event_name": "pull_request",
                    "action": action,
                    "pull_request": {
                        "base": {"ref": "agent/issue-170"},
                        "head": {"sha": "1" * 40},
                    },
                }
                self.assertEqual(
                    _triggered_jobs(self.text, event),
                    set(COMBINED_WORKERS) | {"summary"},
                )

        for action in ("closed", "labeled", "unlabeled", "assigned"):
            with self.subTest(action=action):
                event = {
                    "event_name": "pull_request",
                    "action": action,
                    "pull_request": {
                        "base": {"ref": "agent/issue-170"},
                        "head": {"sha": "1" * 40},
                    },
                }
                self.assertEqual(_triggered_jobs(self.text, event), set())

    def test_edited_base_change_reruns_exact_head_candidate_without_publisher(self):
        unchanged_head = "4" * 40
        event = {
            "event_name": "pull_request",
            "action": "edited",
            "changes": {
                "base": {
                    "ref": {
                        "from": "agent/issue-170",
                    },
                },
            },
            "pull_request": {
                "base": {"ref": "master"},
                "head": {"sha": unchanged_head},
            },
        }
        self.assertEqual(
            _triggered_jobs(self.text, event),
            set(COMBINED_WORKERS) | {"summary"},
        )
        self.assertNotIn("patch-release", _triggered_jobs(self.text, event))

    def test_parent_update_requires_a_child_head_synchronize_event(self):
        parent_push = {
            "event_name": "push",
            "ref": "refs/heads/agent/issue-170",
            "sha": "4" * 40,
        }
        self.assertEqual(_triggered_jobs(self.text, parent_push), set())

        child_synchronize = {
            "event_name": "pull_request",
            "action": "synchronize",
            "pull_request": {
                "base": {"ref": "agent/issue-170"},
                "head": {"sha": "5" * 40},
            },
        }
        self.assertEqual(
            _triggered_jobs(self.text, child_synchronize),
            set(COMBINED_WORKERS) | {"summary"},
        )
        self.assertNotIn("patch-release", _triggered_jobs(self.text, child_synchronize))

    def test_push_remains_master_only_and_prs_exclude_patch_release(self):
        master_push = {
            "event_name": "push",
            "ref": "refs/heads/master",
            "sha": "2" * 40,
        }
        other_push = {
            "event_name": "push",
            "ref": "refs/heads/agent/issue-170",
            "sha": "3" * 40,
        }
        self.assertEqual(
            _triggered_jobs(self.text, master_push),
            set(COMBINED_WORKERS) | {"patch-release", "summary"},
        )
        self.assertEqual(_triggered_jobs(self.text, other_push), set())

    def test_combined_worker_removed_from_pull_request_path_fails(self):
        changed = self.text.replace(
            "  extended-host-tests:\n",
            f"  extended-host-tests:\n    if: {MASTER_PUBLISHER_CONDITION}\n",
            1,
        )
        self.assertTrue(any("must run for pull-request" in error for error in _errors(changed, False)))

    def test_combined_workers_reject_spaced_reviewed_job_keys(self):
        for job_name in COMBINED_WORKERS:
            with self.subTest(job=job_name):
                job = _job_blocks(self.text)[job_name]
                changed_job = job.replace("    runs-on:", "    runs-on :", 1)
                self.assertNotEqual(changed_job, job)
                changed = self.text.replace(job, changed_job, 1)
                self.assertTrue(
                    any(
                        f"{job_name} direct job mapping differs" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_reviewed_job_key_aliases_fail_closed(self):
        allowed = {
            "runs-on": "ubuntu-latest",
            "timeout-minutes": "60",
            "env": "",
            "steps": "",
        }
        for job_name in COMBINED_WORKERS:
            for field, value in allowed.items():
                escaped = f'"\\u{ord(field[0]):04x}{field[1:]}"'
                suffix = f" {value}" if value else ""
                original = f"    {field}:{suffix}"
                variants = (
                    f'    "{field}":{suffix}',
                    f"    {escaped}:{suffix}",
                    f"    !!str {field}:{suffix}",
                    f"    ? {field}\n    :{suffix}",
                    f"    {{{field}:{suffix}}}",
                )
                for variant in variants:
                    with self.subTest(
                        job=job_name,
                        field=field,
                        variant=variant,
                    ):
                        job = _job_blocks(self.text)[job_name]
                        changed_job = job.replace(original, variant, 1)
                        self.assertNotEqual(changed_job, job)
                        changed = self.text.replace(job, changed_job, 1)
                        self.assertTrue(
                            any(
                                f"{job_name} direct job mapping differs" in error
                                or f"{job_name} uses unsupported" in error
                                for error in _errors(changed, False)
                            )
                        )

    def test_every_combined_worker_rejects_spaced_advisory_or_skip_keys(self):
        for job_name in COMBINED_WORKERS:
            for field, message in (
                ("if : ${{ false }}", "must run for pull-request"),
                ("continue-on-error : true", "must not be advisory"),
            ):
                with self.subTest(job=job_name, field=field):
                    changed = self.text.replace(
                        f"  {job_name}:\n",
                        f"  {job_name}:\n    {field}\n",
                        1,
                    )
                    self.assertTrue(
                        any(message in error for error in _errors(changed, False))
                    )

    def test_every_combined_worker_rejects_complex_job_keys(self):
        variants = (
            '"if": ${{ false }}',
            '"continue-\\u006fn-error": true',
            "? if\n    : ${{ false }}",
            "!!str continue-on-error: true",
            "{if: false, continue-on-error: true}",
        )
        for job_name, variant in zip(
            COMBINED_WORKERS,
            variants[: len(COMBINED_WORKERS)],
        ):
            with self.subTest(job=job_name, variant=variant):
                changed = self.text.replace(
                    f"  {job_name}:\n",
                    f"  {job_name}:\n    {variant}\n",
                    1,
                )
                self.assertTrue(
                    any(
                        f"{job_name} uses unsupported direct mapping-key syntax"
                        in error
                        for error in _errors(changed, False)
                    )
                )
        changed = self.text.replace(
            "  host-tests:\n",
            "  host-tests:\n    {if: false, continue-on-error: true}\n",
            1,
        )
        self.assertTrue(
            any(
                "host-tests uses unsupported direct mapping-key syntax" in error
                for error in _errors(changed, False)
            )
        )

    def test_every_combined_worker_has_a_closed_execution_context(self):
        execution_fields = {
            "container": "ubuntu:latest",
            "services": "{}",
            "strategy": "{matrix: {python: [3.12]}}",
            "permissions": "{contents: write}",
            "defaults": "{run: {shell: bash}}",
            "needs": "summary",
            "if": "${{ false }}",
            "continue-on-error": "true",
            "environment": "production",
            "concurrency": "attacker-controlled",
            "uses": "./untrusted-job.yml",
            "secrets": "inherit",
            "shell": "untrusted-shell {0}",
        }
        for job_name in COMBINED_WORKERS:
            for field, value in execution_fields.items():
                with self.subTest(job=job_name, field=field):
                    changed = self.text.replace(
                        f"  {job_name}:\n",
                        f"  {job_name}:\n    {field}: {value}\n",
                        1,
                    )
                    self.assertTrue(
                        any(
                            f"{job_name} direct job mapping differs" in error
                            or f"{job_name} must " in error
                            for error in _errors(changed, False)
                        )
                    )

            for allowed_line in (
                "    runs-on: ubuntu-latest",
                "    timeout-minutes: 60",
                "    env:",
                "    steps:",
            ):
                with self.subTest(job=job_name, duplicate=allowed_line):
                    job = _job_blocks(self.text)[job_name]
                    changed_job = job.replace(
                        allowed_line,
                        f"{allowed_line}\n{allowed_line}",
                        1,
                    )
                    changed = self.text.replace(job, changed_job, 1)
                    self.assertTrue(
                        any(
                            f"{job_name} direct job mapping differs" in error
                            for error in _errors(changed, False)
                        )
                    )

            job = _job_blocks(self.text)[job_name]
            reordered = (
                job.replace(
                    "    runs-on: ubuntu-latest",
                    "    __RUNS_ON__",
                    1,
                )
                .replace(
                    "    timeout-minutes: 60",
                    "    runs-on: ubuntu-latest",
                    1,
                )
                .replace("    __RUNS_ON__", "    timeout-minutes: 60", 1)
            )
            with self.subTest(job=job_name, reordered=True):
                changed = self.text.replace(job, reordered, 1)
                self.assertTrue(
                    any(
                        f"{job_name} direct job mapping differs" in error
                        for error in _errors(changed, False)
                    )
                )

            for original, replacement in (
                ("    runs-on: ubuntu-latest", "    runs-on: self-hosted"),
                ("    timeout-minutes: 60", "    timeout-minutes: 59"),
            ):
                with self.subTest(job=job_name, replacement=replacement):
                    job = _job_blocks(self.text)[job_name]
                    changed = self.text.replace(
                        job,
                        job.replace(original, replacement, 1),
                        1,
                    )
                    self.assertTrue(
                        any(
                            f"{job_name} direct job mapping differs" in error
                            for error in _errors(changed, False)
                        )
                    )

    def test_execution_context_key_syntax_bypasses_fail_closed(self):
        execution_fields = {
            "container": "ubuntu:latest",
            "services": "{}",
            "strategy": "{matrix: {python: [3.12]}}",
            "permissions": "{contents: write}",
            "defaults": "{run: {shell: bash}}",
            "needs": "summary",
            "if": "${{ false }}",
            "continue-on-error": "true",
            "environment": "production",
            "concurrency": "attacker-controlled",
            "uses": "./untrusted-job.yml",
            "secrets": "inherit",
            "shell": "untrusted-shell {0}",
        }
        for job_name in COMBINED_WORKERS:
            for field, value in execution_fields.items():
                escaped = f'"\\u{ord(field[0]):04x}{field[1:]}"'
                variants = (
                    f"{field} : {value}",
                    f'"{field}": {value}',
                    f"{escaped}: {value}",
                    f"!!str {field}: {value}",
                    f"{{{field}: {value}}}",
                    f"? {field}\n    : {value}",
                )
                for variant in variants:
                    with self.subTest(job=job_name, variant=variant):
                        changed = self.text.replace(
                            f"  {job_name}:\n",
                            f"  {job_name}:\n    {variant}\n",
                            1,
                        )
                        self.assertTrue(
                            any(
                                f"{job_name} direct job mapping differs" in error
                                or f"{job_name} uses unsupported" in error
                                or f"{job_name} must " in error
                                for error in _errors(changed, False)
                            )
                        )

    def test_missing_pull_request_trigger_fails(self):
        changed = self.text.replace(PULL_REQUEST_TRIGGER, "", 1)
        self.assertTrue(any("missing pull_request" in error for error in _errors(changed, False)))

    def test_pull_request_activity_type_mutations_fail(self):
        for actions in (
            "opened, synchronize, reopened",
            "opened, synchronize, reopened, edited, closed",
            "opened, synchronize, reopened, edited, labeled",
        ):
            with self.subTest(actions=actions):
                changed = self.text.replace(
                    "types: [opened, synchronize, reopened, edited]",
                    f"types: [{actions}]",
                    1,
                )
                self.assertTrue(
                    any("types must be opened" in error for error in _errors(changed, False))
                )

    def test_pull_request_activity_type_order_is_not_semantic(self):
        changed = self.text.replace(
            "types: [opened, synchronize, reopened, edited]",
            "types: [edited, reopened, opened, synchronize]",
            1,
        )
        self.assertEqual(_errors(changed, False), [])

    def test_missing_push_trigger_fails(self):
        changed = self.text.replace(PUSH_TRIGGER, 'push:\n    branches: [ "other" ]', 1)
        self.assertTrue(any("restricted to master" in error for error in _errors(changed, False)))

    def test_independent_jobs_reject_serial_dependencies(self):
        for job_name in INDEPENDENT_JOBS:
            with self.subTest(job_name=job_name):
                changed = self.text.replace(
                    f"  {job_name}:\n",
                    f"  {job_name}:\n    needs: [host-tests]\n",
                    1,
                )
                self.assertTrue(
                    any(
                        f"{job_name} must not create a serial Build critical path" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_every_libpng_install_lane_declares_pkg_config(self):
        for job_name, job in _job_blocks(self.text).items():
            if "libpng-dev" not in job:
                continue
            with self.subTest(job_name=job_name):
                changed_job = job.replace(" pkg-config", "", 1)
                self.assertNotEqual(changed_job, job)
                changed = self.text.replace(job, changed_job, 1)
                self.assertTrue(
                    any(
                        f"{job_name} installs libpng-dev without pkg-config" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_build_python_dependencies_are_exactly_hash_locked(self):
        self.assertEqual(
            _hashed_requirements_errors(PYTHON_REQUIREMENTS.read_text(encoding="utf-8")),
            [],
        )

    def test_unhashed_privileged_pip_install_fails(self):
        changed = self.text.replace(
            HASHED_PIP_INSTALL,
            "python3 -m pip install ttp numpy pillow",
            1,
        )
        self.assertTrue(
            any(
                "must use the reviewed hash-locked Python requirements" in error
                for error in _errors(changed, False)
            )
        )

    def test_appended_second_pip_install_fails(self):
        changed = self.text.replace(
            HASHED_PIP_INSTALL,
            HASHED_PIP_INSTALL + " && python3 -m pip install evil",
            1,
        )
        self.assertTrue(
            any(
                "must use the reviewed hash-locked Python requirements" in error
                for error in _errors(changed, False)
            )
        )

    def test_separate_bare_or_versioned_pip_install_fails(self):
        for command in ("pip install evil", "pip3.12 install evil"):
            with self.subTest(command=command):
                changed = self.text.replace(
                    "    - name: Build tools\n",
                    f"    - run: {command}\n\n    - name: Build tools\n",
                    1,
                )
                self.assertTrue(
                    any(
                        "must use the reviewed hash-locked Python requirements" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_pip_global_options_before_install_fail(self):
        for command in (
            "python3 -m pip --isolated install evil",
            "pip --proxy https://example.invalid install evil",
        ):
            with self.subTest(command=command):
                changed = self.text.replace(
                    "    - name: Build tools\n",
                    f"    - run: {command}\n\n    - name: Build tools\n",
                    1,
                )
                self.assertTrue(
                    any(
                        "must use the reviewed hash-locked Python requirements" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_folded_block_scalar_pip_install_fails(self):
        for scalar in (">", ">-", ">+2"):
            with self.subTest(scalar=scalar):
                changed = self.text.replace(
                    "    - name: Build tools\n",
                    f"    - run: {scalar}\n"
                    "        echo preparing &&\n"
                    "        python3 -m pip --isolated install evil\n\n"
                    "    - name: Build tools\n",
                    1,
                )
                self.assertTrue(
                    any(
                        "must use the reviewed hash-locked Python requirements" in error
                        for error in _errors(changed, False)
                    )
                )

    def test_changed_requirement_hash_fails(self):
        changed = PYTHON_REQUIREMENTS.read_text(encoding="utf-8").replace(
            EXPECTED_HASHED_REQUIREMENTS["numpy"][1],
            "sha256:" + ("0" * 64),
            1,
        )
        self.assertTrue(
            any(
                "differ from reviewed versions/hashes" in error
                for error in _hashed_requirements_errors(changed)
            )
        )

    def test_missing_summary_dependency_fails(self):
        changed = self.text.replace(
            SUMMARY_NEEDS,
            "needs: [host-tests, build, extended-host-tests]",
            1,
        )
        self.assertTrue(any("summary must depend" in error for error in _errors(changed, False)))

    def test_workflow_pilot_suite_remains_owned_by_required_host_job(self):
        host_tests = _job_blocks(self.text)["host-tests"]
        for command in (WORKFLOW_PILOT_GATE, WORKFLOW_PILOT_BASELINE_GATE):
            with self.subTest(command=command):
                self.assertTrue(
                    _contains_exact_command(
                        host_tests,
                        command,
                    )
                )
        self.assertIn(
            '--repository-root "$GITHUB_WORKSPACE"',
            WORKFLOW_PILOT_BASELINE_GATE,
        )
        changed = self.text.replace(
            f"      run: {WORKFLOW_PILOT_GATE}\n",
            "      run: true\n",
            1,
        )
        self.assertNotEqual(changed, self.text)
        self.assertTrue(
            any(
                "candidate host lost exact fail-closed Build evidence: "
                f"{WORKFLOW_PILOT_GATE}"
                in error
                for error in _errors(changed, False)
            )
        )

        changed = self.text.replace(
            f"      run: {WORKFLOW_PILOT_BASELINE_GATE}\n",
            "      run: true\n",
            1,
        )
        self.assertNotEqual(changed, self.text)
        self.assertTrue(
            any(
                "candidate host lost exact fail-closed Build evidence: "
                f"{WORKFLOW_PILOT_BASELINE_GATE}"
                in error
                for error in _errors(changed, False)
            )
        )

    def test_workflow_pilot_steps_reject_spaced_protected_keys(self):
        changed = self.text
        steps = (
            (
                "Run workflow-pilot reporter regression suite (issue #176)",
                WORKFLOW_PILOT_GATE,
            ),
            (
                "Validate workflow-pilot baseline against checked-out Git history",
                WORKFLOW_PILOT_BASELINE_GATE,
            ),
        )
        for step_name, command in steps:
            changed = changed.replace(
                f"    - name: {step_name}\n",
                f"    - name : {step_name}\n",
                1,
            ).replace(
                f"      run: {command}\n",
                f"      run : {command}\n",
                1,
            )
        self.assertNotEqual(changed, self.text)
        self.assertTrue(
            any(
                "protected pre-pilot step sequence differs" in error
                or "lost exact fail-closed Build evidence" in error
                for error in _errors(changed, False)
            )
        )

    def test_both_workflow_pilot_steps_reject_complex_or_advisory_keys(self):
        variants = (
            "continue-on-error: true",
            "if: ${{ false }}",
            "shell: bash {0} || true",
            "working-directory: /",
            "working-directory : /",
            '"continue-on-error": true',
            '"continue-\\u006fn-error": true',
            "? continue-on-error\n      : true",
            "!!str continue-on-error: true",
            "{continue-on-error: true}",
            '"if": ${{ false }}',
            "@unsupported",
        )
        for command in (WORKFLOW_PILOT_GATE, WORKFLOW_PILOT_BASELINE_GATE):
            for variant in variants:
                with self.subTest(command=command, variant=variant):
                    changed = self.text.replace(
                        f"      run: {command}\n",
                        f"      {variant}\n      run: {command}\n",
                        1,
                    )
                    self.assertNotEqual(changed, self.text)
                    self.assertTrue(
                        any(
                            "candidate host lost exact fail-closed Build evidence"
                            in error
                            or "unsupported direct mapping-key syntax" in error
                            for error in _errors(changed, False)
                        )
                    )

    def test_both_workflow_pilot_steps_reject_advisory_or_complex_first_keys(self):
        steps = (
            (
                "Run workflow-pilot reporter regression suite (issue #176)",
                WORKFLOW_PILOT_GATE,
            ),
            (
                "Validate workflow-pilot baseline against checked-out Git history",
                WORKFLOW_PILOT_BASELINE_GATE,
            ),
        )
        variants = (
            "continue-on-error: true",
            "if: ${{ false }}",
            "shell: bash {0} || true",
            "working-directory: /",
            "working-directory : /",
            '"continue-on-error": true',
            '"continue-\\u006fn-error": true',
            "? continue-on-error\n      : true",
            "!!str continue-on-error: true",
            "{continue-on-error: true}",
            "@unsupported",
        )
        for step_name, command in steps:
            original = f"    - name: {step_name}\n"
            reviewed_key_variants = (
                f'"name": {step_name}',
                f'"n\\u0061me": {step_name}',
                f"? name\n      : {step_name}",
                f"!!str name: {step_name}",
                f"{{name: {step_name}}}",
            )
            for variant in variants + reviewed_key_variants:
                with self.subTest(command=command, variant=variant):
                    changed = self.text.replace(
                        original,
                        f"    - {variant}\n",
                        1,
                    )
                    self.assertNotEqual(changed, self.text)
                    self.assertTrue(
                        any(
                            "candidate host lost exact fail-closed Build evidence"
                            in error
                            or "unsupported direct mapping-key syntax" in error
                            for error in _errors(changed, False)
                        )
                    )

    def test_both_workflow_pilot_steps_reject_complex_run_keys(self):
        variants = (
            '"run"',
            '"r\\u0075n"',
            "!!str run",
        )
        for command in (WORKFLOW_PILOT_GATE, WORKFLOW_PILOT_BASELINE_GATE):
            for variant in variants:
                with self.subTest(command=command, variant=variant):
                    changed = self.text.replace(
                        f"      run: {command}\n",
                        f"      {variant}: {command}\n",
                        1,
                    )
                    self.assertNotEqual(changed, self.text)
                    self.assertTrue(
                        any(
                            "candidate host lost exact fail-closed Build evidence"
                            in error
                            or "unsupported direct mapping-key syntax" in error
                            for error in _errors(changed, False)
                        )
                    )

    def test_workflow_pilot_gates_reject_shell_success_masks_and_wrappers(self):
        mutations = (
            f"{WORKFLOW_PILOT_GATE} || true",
            f"{WORKFLOW_PILOT_GATE}; true",
            f"{WORKFLOW_PILOT_GATE} && true",
            f"sh -c \"{WORKFLOW_PILOT_GATE}\"",
            f"echo {WORKFLOW_PILOT_GATE}",
            f"$({WORKFLOW_PILOT_GATE})",
            f"{WORKFLOW_PILOT_GATE} 2>/dev/null",
        )
        for replacement in mutations:
            with self.subTest(replacement=replacement):
                changed = self.text.replace(
                    f"      run: {WORKFLOW_PILOT_GATE}\n",
                    f"      run: {replacement}\n",
                    1,
                )
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "candidate host lost exact fail-closed Build evidence"
                        in error
                        for error in _errors(changed, False)
                    )
                )

        inherited_defaults = (
            self.text.replace(
                "\njobs:\n",
                "\ndefaults:\n"
                "  run:\n"
                "    shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\ndefaults: # inherited mask\n"
                "  run:\n"
                "    shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\ndefaults:\n"
                "  run: # inherited mask\n"
                "    shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\ndefaults:\n"
                "    run:\n"
                "        shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    defaults:\n"
                "      run:\n"
                "        shell: bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    defaults: # inherited mask\n"
                "      run:\n"
                "        shell: bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    defaults:\n"
                "      run: # inherited mask\n"
                "        shell: bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    defaults:\n"
                "        run:\n"
                "            shell: bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\n\"defaults\" :\n"
                "  \"run\" :\n"
                "    \"shell\" : bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\ndefaults: {run: {shell: \"bash {0} || true\"}}\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    \"defaults\" :\n"
                "      \"run\" :\n"
                "        \"shell\" : bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    defaults: {run: {shell: \"bash {0} || true\"}}\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\n\"def\\u0061ults\":\n"
                "  run:\n"
                "    shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\n? defaults\n"
                ":\n"
                "  run:\n"
                "    shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "\njobs:\n",
                "\n!!str defaults:\n"
                "  run:\n"
                "    shell: bash {0} || true\n\n"
                "jobs:\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    \"def\\u0061ults\":\n"
                "      run:\n"
                "        shell: bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    ? defaults\n"
                "    :\n"
                "      run:\n"
                "        shell: bash {0} || true\n",
                1,
            ),
            self.text.replace(
                "  host-tests:\n",
                "  host-tests:\n"
                "    !!str defaults:\n"
                "      run:\n"
                "        shell: bash {0} || true\n",
                1,
            ),
        )
        for changed in inherited_defaults:
            with self.subTest(inherited_shell_default=changed[:200]):
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "execution defaults must not alter" in error
                        or "unsupported direct mapping-key syntax" in error
                        for error in _errors(changed, False)
                    )
                )

        baseline_mutations = (
            f"{WORKFLOW_PILOT_BASELINE_GATE} || true",
            f"{WORKFLOW_PILOT_BASELINE_GATE}; true",
            f"{WORKFLOW_PILOT_BASELINE_GATE} && true",
            f"sh -c '{WORKFLOW_PILOT_BASELINE_GATE}'",
            f"echo {WORKFLOW_PILOT_BASELINE_GATE}",
            f"$({WORKFLOW_PILOT_BASELINE_GATE})",
            WORKFLOW_PILOT_BASELINE_GATE.replace(
                "> /dev/null", "> /dev/null 2>&1"
            ),
        )
        for replacement in baseline_mutations:
            with self.subTest(replacement=replacement):
                changed = self.text.replace(
                    f"      run: {WORKFLOW_PILOT_BASELINE_GATE}\n",
                    f"      run: {replacement}\n",
                    1,
                )
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "candidate host lost exact fail-closed Build evidence"
                        in error
                        for error in _errors(changed, False)
                    )
                )

        for field in (
            "continue-on-error: true",
            "if: ${{ false }}",
            "shell: bash {0} || true",
        ):
            with self.subTest(advisory_field=field):
                changed = self.text.replace(
                    f"      run: {WORKFLOW_PILOT_BASELINE_GATE}\n",
                    f"      {field}\n      run: {WORKFLOW_PILOT_BASELINE_GATE}\n",
                    1,
                )
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "candidate host lost exact fail-closed Build evidence"
                        in error
                        for error in _errors(changed, False)
                    )
                )

        changed = self.text.replace(
            "  host-tests:\n    runs-on: ubuntu-latest\n",
            "  host-tests:\n    runs-on: ubuntu-latest\n"
            "    continue-on-error: true\n",
            1,
        )
        self.assertNotEqual(changed, self.text)
        self.assertTrue(
            any(
                "host-tests must not be advisory" in error
                for error in _errors(changed, False)
            )
        )

    def test_summary_omitting_legacy_result_fails(self):
        changed = self.text.replace(
            '"$LEGACY_RESULT"\n        do',
            '"$HOST_TESTS_RESULT"\n        do',
            1,
        )
        self.assertTrue(any("summary loop omits" in error for error in _errors(changed, False)))

    def test_summary_comparison_outside_loop_fails(self):
        changed = self.text.replace(
            '[ "$result" != "success" ]',
            '[ "$HOST_TESTS_RESULT" != "success" ]',
            1,
        )
        self.assertTrue(any("summary loop must fail closed" in error for error in _errors(changed, False)))

    def test_comment_text_is_not_treated_as_run_block_evidence(self):
        changed = self.text.replace(
            "        make legacy -j2\n",
            "        true\n        # make legacy -j2\n",
            1,
        )
        self.assertTrue(any("legacy job lost" in error for error in _errors(changed, False)))

    def test_duplicate_modern_gate_in_master_host_fails(self):
        changed = self.text.replace(
            "    - name: Run CJK font gates\n",
            "    - name: Duplicate modern gate\n"
            "      run: make expansion-modern-linker-check MODERN_CONFIG=debug MODERN_ABI=aapcs\n\n"
            "    - name: Run CJK font gates\n",
            1,
        )
        self.assertTrue(any("repeats Build-owned" in error for error in _errors(changed, False)))

    def test_build_cannot_silently_skip_required_analyzer_checks(self):
        changed = self.text.replace(" CODEQL_REQUIRE_FANALYZER=1", "", 1)
        self.assertTrue(
            any(
                "must require analyzer support" in error
                for error in _errors(changed, False)
            )
        )

    def test_all_locales_gate_cannot_regress_to_profile_prerequisite_only(self):
        changed = self.text.replace(
            MAP_MENU_PRESENTATION_GATE,
            "make expansion-modern-all-locales-all-features-check -j1",
            1,
        )
        self.assertNotEqual(changed, self.text)
        self.assertTrue(
            any(
                "must gate the all-locales profile through map-menu presentation"
                in error
                for error in _errors(changed, False)
            )
        )

    def test_retired_workflow_remote_completion_dependency_fails(self):
        changed = MAKEFILE.read_text(encoding="utf-8").replace(
            "--workflow build.yml",
            f"--workflow {RETIRED_WORKFLOW_FILENAME}",
            1,
        )
        self.assertTrue(any("retired workflow" in error for error in _remote_completion_errors(changed)))

    def test_pull_request_remote_completion_dependency_fails(self):
        changed = MAKEFILE.read_text(encoding="utf-8").replace("--event push ", "", 1)
        self.assertTrue(any("--event push" in error for error in _remote_completion_errors(changed)))

    def test_comment_only_change_preserves_contract(self):
        self.assertEqual(_errors(self.text + "\n# no graph change\n", False), [])


if __name__ == "__main__":
    unittest.main()
