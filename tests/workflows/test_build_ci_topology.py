"""Structural contract for consolidated candidate and master Build CI."""

from __future__ import annotations

import fnmatch
import re
import unittest
from pathlib import Path


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
    "python3 -m unittest discover -s scripts/workflow_pilot/tests "
    "-p 'test_*.py' -v"
)
WORKFLOW_PILOT_BASELINE_GATE = (
    "python3 -m scripts.workflow_pilot.reporter "
    '--repository-root "$GITHUB_WORKSPACE" '
    "--fixture scripts/workflow_pilot/tests/fixtures/baseline.json "
    "--decisions .github/workflow-pilot-decisions.json "
    "--expected scripts/workflow_pilot/tests/fixtures/baseline_expected.json "
    "> /dev/null"
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
        inline = re.match(r"^    - run: (?P<value>.+)$", lines[index])
        field = re.match(r"^      run: (?P<value>.+)$", lines[index])
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
    matches = list(re.finditer(r"^    - (?=[A-Za-z])", job, re.MULTILINE))
    return [
        job[
            match.start():
            matches[index + 1].start() if index + 1 < len(matches) else len(job)
        ]
        for index, match in enumerate(matches)
    ]


def _contains_exact_command(job: str, command: str) -> bool:
    expected = _normalise(command)
    for step in _step_blocks(job):
        commands = _run_block_commands(step)
        if len(commands) != 1 or _normalise(commands[0]) != expected:
            continue
        execution_fields = {
            match.group("field")
            for match in re.finditer(
                r"^      (?P<field>[A-Za-z][A-Za-z0-9_-]*):",
                step,
                re.MULTILINE,
            )
        }
        if execution_fields == {"run"}:
            return True
    return False


def _has_run_shell_default(text: str, defaults_indent: int) -> bool:
    lines = text.splitlines()
    block_key = re.compile(
        r"^(?P<indent> *)(?P<key>defaults|run|\"defaults\"|\"run\"|"
        r"'defaults'|'run'):[ \t]*(?:#.*)?$"
    )
    shell_key = re.compile(
        r"^(?P<indent> *)(?:shell|\"shell\"|'shell'):[ \t]*"
    )

    for index, line in enumerate(lines):
        match = block_key.match(line)
        if (
            match is None
            or len(match.group("indent")) != defaults_indent
            or match.group("key").strip("\"'") != "defaults"
        ):
            continue

        child_index = index + 1
        while child_index < len(lines):
            child_line = lines[child_index]
            if not child_line.strip() or child_line.lstrip().startswith("#"):
                child_index += 1
                continue
            child_indent = len(child_line) - len(child_line.lstrip(" "))
            if child_indent <= defaults_indent:
                break
            child_match = block_key.match(child_line)
            if (
                child_match is None
                or child_match.group("key").strip("\"'") != "run"
            ):
                child_index += 1
                continue

            run_indent = len(child_match.group("indent"))
            shell_index = child_index + 1
            while shell_index < len(lines):
                shell_line = lines[shell_index]
                if not shell_line.strip() or shell_line.lstrip().startswith("#"):
                    shell_index += 1
                    continue
                shell_indent = len(shell_line) - len(shell_line.lstrip(" "))
                if shell_indent <= run_indent:
                    break
                if shell_key.match(shell_line):
                    return True
                shell_index += 1
            child_index = shell_index
    return False


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
    if _has_run_shell_default(header, 0):
        errors.append("workflow run shell defaults must not make candidate gates advisory")
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
        if "if:" in jobs[job_name]:
            errors.append(f"{job_name} must run for pull-request candidates and master pushes")
        if re.search(r"^    continue-on-error:", jobs[job_name], re.MULTILINE):
            errors.append(f"{job_name} must not be advisory")

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
    if _has_run_shell_default(jobs["host-tests"], 4):
        errors.append("candidate host run shell defaults must not make pilot gates advisory")

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
        )
        for changed in inherited_defaults:
            with self.subTest(inherited_shell_default=changed[:200]):
                self.assertNotEqual(changed, self.text)
                self.assertTrue(
                    any(
                        "run shell defaults must not make" in error
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
