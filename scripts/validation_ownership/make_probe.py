"""Authentic GNU Make observation behind a nonexecuting candidate boundary."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from scripts.validation_ownership.budget import (
    ProbeBudget,
    ProbeCache,
    bounded_collect,
)
from scripts.validation_ownership.sandbox import (
    MAKE,
    ExecutionSnapshot,
    Mount,
    ProbeSandboxError,
    RegisteredCommand,
    SandboxRunner,
    compile_interceptor,
    sha256_bytes,
    strict_utf8,
)


OVERRIDE_CONTROL_RE = re.compile(
    r"(?<![A-Za-z0-9_.])override\s+"
    r"(?:export\s+|private\s+)*"
    r"(?P<name>SHELL|\.SHELLFLAGS|MAKE|MAKEFLAGS|MFLAGS|GNUMAKEFLAGS)"
    r"\s*(?:=|:=|::=|\?=|\+=|!=)"
)
EVAL_CONTROL_RE = re.compile(
    r"\$\(\s*eval\b[^\n]*"
    r"(?P<name>SHELL|\.SHELLFLAGS|MAKE|MAKEFLAGS|MFLAGS|GNUMAKEFLAGS)"
)
MAKE_FLAG_CONTROL_RE = re.compile(
    r"(?<![A-Za-z0-9_.])"
    r"(?:override\s+|export\s+|private\s+|unexport\s+)*"
    r"(?P<name>MAKE|MAKEFLAGS|MFLAGS|GNUMAKEFLAGS)"
    r"\s*(?:=|:=|::=|\?=|\+=|!=)"
)
LOAD_DIRECTIVE_RE = re.compile(r"(?m)^[ \t]*(?:-?load)(?:[ \t]|$)")
TRACE_RE = re.compile(
    r"^(?P<source>.+?):[0-9]+: "
    r"(?P<record>(?:(?:update )?target) '(?P<target>[^']+)'"
    r"(?: due to: (?P<due>.*))?"
    r"|target '(?P<missing>[^']+)' does not exist)$"
)
ENTERING_RE = re.compile(r"^make(?:\[[0-9]+\])?: (?:Entering|Leaving) directory ")
READING_RE = re.compile(
    r"^Reading makefile '(?P<path>[^']+)'(?P<details>.*)\.\.\.$"
)


class MakeProbeError(ProbeSandboxError):
    """Raised when candidate Make semantics cannot be observed safely."""


@dataclass(frozen=True)
class MakeVariant:
    assignments: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_dict(cls, assignments: dict[str, str]) -> "MakeVariant":
        return cls(tuple(sorted(assignments.items())))


@dataclass(frozen=True)
class MakeObservation:
    target: str
    assignments: tuple[tuple[str, str], ...]
    execution_snapshot_sha256: str
    semantic_fingerprint: str
    semantic_record: dict[str, object]
    raw_stdout: bytes
    raw_stderr: bytes
    command_events: tuple[dict[str, object], ...]


def _validate_make_controls(
    snapshot: ExecutionSnapshot,
    paths: Iterable[str],
    budget: ProbeBudget,
) -> None:
    def strip_comment(line: str) -> str:
        for index, character in enumerate(line):
            if character != "#":
                continue
            escapes = 0
            cursor = index - 1
            while cursor >= 0 and line[cursor] == "\\":
                escapes += 1
                cursor -= 1
            if escapes % 2 == 0:
                return line[:index]
        return line

    selected_paths = bounded_collect(
        budget,
        paths,
        limit=budget.limits.counts["snapshot_files"],
        label="loaded Make inputs",
        unique=True,
    )
    for path in sorted(selected_paths):
        entry = snapshot.entry(path)
        try:
            text = strict_utf8(entry.data, f"GNU Make input {entry.path!r}")
        except ProbeSandboxError as error:
            raise MakeProbeError(str(error)) from error
        for line in text.splitlines():
            recipe = line.startswith("\t")
            semantic_line = line if recipe else strip_comment(line)
            assignment = EVAL_CONTROL_RE.search(semantic_line)
            if not recipe and assignment is None:
                assignment = OVERRIDE_CONTROL_RE.search(semantic_line)
            if not recipe and assignment is None:
                assignment = MAKE_FLAG_CONTROL_RE.search(semantic_line)
            if assignment is not None:
                raise MakeProbeError(
                    f"GNU Make input {entry.path!r} assigns reserved execution "
                    f"control {assignment.group('name')}"
                )
            if not recipe and LOAD_DIRECTIVE_RE.search(semantic_line) is not None:
                raise MakeProbeError(
                    f"GNU Make input {entry.path!r} contains a "
                    "loadable-module directive"
                )


def _loaded_makefiles(
    snapshot: ExecutionSnapshot,
    stdout: bytes,
    budget: ProbeBudget,
) -> set[str]:
    try:
        text = strict_utf8(stdout, "GNU Make verbose output")
    except ProbeSandboxError as error:
        raise MakeProbeError(str(error)) from error
    loaded = set()
    for line in text.splitlines():
        match = READING_RE.match(line)
        if match is None:
            continue
        path = match.group("path")
        if path == "/probe/probe.mk":
            continue
        if path.startswith("/repo/"):
            path = path.removeprefix("/repo/")
        candidate = Path(path)
        if (
            candidate.is_absolute()
            or candidate.as_posix() != path
            or ".." in candidate.parts
        ):
            raise MakeProbeError(
                f"GNU Make loaded noncanonical candidate input {path!r}"
            )
        if path not in snapshot.paths():
            if "don't care" in match.group("details"):
                continue
            raise MakeProbeError(
                f"GNU Make loaded input outside the execution snapshot: {path!r}"
            )
        if (
            path not in loaded
            and len(loaded) >= budget.limits.counts["snapshot_files"]
        ):
            raise MakeProbeError("GNU Make loaded input count exceeds bound")
        loaded.add(path)
    if "Makefile" not in loaded:
        raise MakeProbeError("GNU Make did not report its candidate Makefile")
    return loaded


def _validate_variant(variant: MakeVariant) -> None:
    names = []
    for name, value in variant.assignments:
        if (
            not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
            or "\0" in value
        ):
            raise MakeProbeError("GNU Make variant has an invalid assignment")
        if name in {
            "SHELL",
            "MAKE",
            "MAKEFLAGS",
            "MFLAGS",
            "GNUMAKEFLAGS",
        }:
            raise MakeProbeError(
                f"GNU Make variant assigns reserved execution control {name}"
            )
        names.append(name)
    if len(names) != len(set(names)):
        raise MakeProbeError("GNU Make variant assigns one name more than once")


def _semantic_output(stdout: bytes, stderr: bytes) -> dict[str, object]:
    try:
        stdout_text = strict_utf8(stdout, "GNU Make stdout")
        stderr_text = strict_utf8(stderr, "GNU Make stderr")
    except ProbeSandboxError as error:
        raise MakeProbeError(str(error)) from error
    trace = []
    output = []
    for line in stdout_text.splitlines():
        if ENTERING_RE.match(line):
            continue
        match = TRACE_RE.match(line)
        if match is None:
            output.append(line)
            continue
        trace.append(
            {
                "due": [] if match.group("due") is None else match.group("due").split(),
                "record": match.group("record"),
                "source": match.group("source"),
                "target": match.group("target") or match.group("missing"),
            }
        )
    return {
        "stderr": stderr_text.splitlines(),
        "stdout": output,
        "trace": trace,
    }


def _owner_input_record(
    snapshot: ExecutionSnapshot,
    owner_inputs: Iterable[str],
    budget: ProbeBudget,
) -> list[dict[str, object]]:
    records = []
    selected_inputs = bounded_collect(
        budget,
        owner_inputs,
        limit=budget.limits.counts["snapshot_files"],
        label="Make owner inputs",
        unique=True,
    )
    for path in sorted(selected_inputs):
        entry = snapshot.entry(path)
        records.append(
            {
                "gid": entry.gid,
                "kind": entry.kind,
                "mode": entry.mode,
                "mtime_ns": entry.mtime_ns,
                "path": path,
                "sha256": sha256_bytes(entry.data),
                "size": entry.size,
                "uid": entry.uid,
            }
        )
    if not records:
        raise MakeProbeError("semantic Make owner inputs must not be empty")
    return records


def run_make_probe(
    snapshot: ExecutionSnapshot,
    *,
    targets: Iterable[str],
    variants: Iterable[MakeVariant],
    owner_inputs: dict[str, Iterable[str]],
    registered_commands: Iterable[RegisteredCommand],
    scratch_root: Path,
    budget: ProbeBudget,
) -> list[MakeObservation]:
    """Observe every target/state under one aggregate probe authority."""
    selected_targets = sorted(
        bounded_collect(
            budget,
            targets,
            limit=budget.limits.variants,
            label="GNU Make targets",
            unique=True,
        )
    )
    selected_variants = bounded_collect(
        budget,
        variants,
        limit=budget.limits.variants,
        label="GNU Make variant/state inputs",
    )
    if not selected_targets or any(
        not target
        or "\0" in target
        or target.startswith("-")
        or "=" in target
        or any(character.isspace() for character in target)
        for target in selected_targets
    ):
        raise MakeProbeError("GNU Make probe targets are invalid")
    if not selected_variants:
        selected_variants = [MakeVariant()]
    bounded_variants = []
    for variant in selected_variants:
        bounded_variant = MakeVariant(
            tuple(
                bounded_collect(
                    budget,
                    variant.assignments,
                    limit=budget.limits.variants,
                    label="GNU Make variant assignments",
                )
            )
        )
        _validate_variant(bounded_variant)
        bounded_variants.append(bounded_variant)
    selected_variants = bounded_variants
    missing_owner_inputs = {
        target for target in selected_targets if target not in owner_inputs
    }
    if missing_owner_inputs:
        raise MakeProbeError(
            f"GNU Make targets lack declared owner inputs: "
            f"{sorted(missing_owner_inputs)}"
        )
    budget.preflight_variants(len(selected_targets) * len(selected_variants))
    _validate_make_controls(snapshot, ("Makefile",), budget)
    registered_commands = tuple(
        bounded_collect(
            budget,
            registered_commands,
            limit=budget.limits.counts["mappings"],
            label="registered commands",
        )
    )
    scratch_root = scratch_root.resolve(strict=True)
    with tempfile.TemporaryDirectory(
        prefix="make-probe-",
        dir=scratch_root,
    ) as temporary:
        base = Path(temporary)
        tree = base / "tree"
        snapshot.materialize(tree, budget)
        (tree / "build").mkdir(exist_ok=True)
        work = base / "work"
        build = base / "build"
        work.mkdir()
        build.mkdir()
        postlude = base / "probe.mk"
        postlude.write_text(
            "ifneq (,$(filter guile,$(.FEATURES)))\n"
            "$(error trusted GNU Make must not embed Guile)\n"
            "endif\n"
            "override SHELL := /bin/vo-shell\n"
            "override .SHELLFLAGS := -c\n"
            "override MAKE := /bin/vo-shell\n",
            encoding="ascii",
        )
        postlude.chmod(0o400)
        interceptor = base / "shell-interceptor"
        interceptor_authority = compile_interceptor(interceptor, budget)
        runner = SandboxRunner(scratch_root, budget)
        cache = ProbeCache(budget)
        observations = []
        for target in selected_targets:
            target_owner_inputs = _owner_input_record(
                snapshot,
                owner_inputs[target],
                budget,
            )
            for variant in selected_variants:
                assignments = list(variant.assignments)
                argv = [
                    "/usr/bin/make",
                    "--no-print-directory",
                    "--debug=v",
                    "--trace",
                    "--warn-undefined-variables",
                    "-n",
                    "-B",
                    "-f",
                    "/probe/probe.mk",
                    "-f",
                    "Makefile",
                    "-f",
                    "/probe/probe.mk",
                    "MAKE=/bin/vo-shell",
                    *(f"{name}={value}" for name, value in assignments),
                    target,
                ]
                try:
                    completed, events = runner.run(
                        MAKE,
                        argv,
                        read_only=[
                            Mount(tree, "/repo", noexec=True),
                            Mount(postlude, "/probe/probe.mk", noexec=True),
                        ],
                        writable=[
                            Mount(work, "/work"),
                            Mount(build, "/repo/build"),
                        ],
                        dispatcher=registered_commands,
                        interceptor=interceptor,
                        cache=cache,
                        cache_namespace=(snapshot.digest,),
                    )
                except ProbeSandboxError as error:
                    raise MakeProbeError(str(error)) from error
                _validate_make_controls(
                    snapshot,
                    _loaded_makefiles(snapshot, completed.stdout, budget),
                    budget,
                )
                if completed.returncode != 0 or completed.stderr:
                    raise MakeProbeError(
                        f"GNU Make target {target!r} failed safely: "
                        + strict_utf8(
                            completed.stderr,
                            f"GNU Make target {target!r} stderr",
                        )
                    )
                observed = _semantic_output(
                    completed.stdout,
                    completed.stderr,
                )
                semantic = {
                    "assignments": assignments,
                    "command_events": events,
                    "make": {
                        "path": str(MAKE),
                        "sha256": sha256_bytes(MAKE.read_bytes()),
                    },
                    "observed": observed,
                    "owner_inputs": target_owner_inputs,
                    "target": target,
                }
                encoded = json.dumps(
                    semantic,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("ascii")
                observations.append(
                    MakeObservation(
                        target=target,
                        assignments=variant.assignments,
                        execution_snapshot_sha256=snapshot.digest,
                        semantic_fingerprint=hashlib.sha256(
                            b"validation-ownership-make-semantics-v1\0" + encoded
                        ).hexdigest(),
                        semantic_record={
                            **semantic,
                            "interceptor": interceptor_authority,
                            "launcher": runner.launcher_authority,
                        },
                        raw_stdout=completed.stdout,
                        raw_stderr=completed.stderr,
                        command_events=tuple(events),
                    )
                )
        return observations
