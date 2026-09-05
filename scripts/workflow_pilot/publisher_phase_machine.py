#!/usr/bin/env python3
"""Typed phase contract for the trusted patch publisher."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Iterable

if __package__:
    from . import publisher_command_signatures as command_authority
else:
    _AUTHORITY_PATH = Path(__file__).resolve().parent / "publisher_command_signatures.py"
    _AUTHORITY_SPEC = importlib.util.spec_from_file_location(
        "_publisher_phase_command_authority",
        _AUTHORITY_PATH,
    )
    if _AUTHORITY_SPEC is None or _AUTHORITY_SPEC.loader is None:
        raise ImportError("publisher command authority is unavailable")
    command_authority = importlib.util.module_from_spec(_AUTHORITY_SPEC)
    sys.modules[_AUTHORITY_SPEC.name] = command_authority
    _AUTHORITY_SPEC.loader.exec_module(command_authority)


class PublisherPhaseState(str, Enum):
    INITIAL = "initial"
    CANDIDATE_RUNNING = "candidate-running"
    CANDIDATE_REAPED = "candidate-reaped"
    MEMBERSHIP_VERIFIED = "membership-verified"
    EXPORTING = "exporting"
    EXPORT_COMMITTED = "export-committed"
    COMPLETE = "complete"


class PublisherPhaseEventKind(str, Enum):
    SUPERVISOR_LAUNCH_STARTED = command_authority.PHASE_SUPERVISOR_LAUNCH
    SUPERVISOR_AUTHENTICATED = command_authority.PHASE_SUPERVISOR_AUTHENTICATED
    SUPERVISOR_RUNNING = command_authority.PHASE_SUPERVISOR_RUNNING
    CANDIDATE_LAUNCH_STARTED = command_authority.PHASE_CANDIDATE_LAUNCH
    CANDIDATE_OUTPUT_WRITE = command_authority.PHASE_CANDIDATE_OUTPUT_WRITE
    CONTROL_BYPASS = command_authority.PHASE_CONTROL_BYPASS
    CANDIDATE_COMPLETED = command_authority.PHASE_CANDIDATE_COMPLETED
    MEMBERSHIP_COMPLETED = command_authority.PHASE_MEMBERSHIP_COMPLETED
    ISOLATED_HANDOFF_VALIDATED = (
        command_authority.PHASE_ISOLATED_HANDOFF_VALIDATED
    )
    EXPORT_STARTED = command_authority.PHASE_EXPORT_STARTED
    EXPORT_WRITE = command_authority.PHASE_EXPORT_WRITE
    EXPORT_SEALED = command_authority.PHASE_EXPORT_SEALED
    SUPERVISOR_REAPED = command_authority.PHASE_SUPERVISOR_REAPED
    PRE_EXPORT_CONTAINMENT = command_authority.PHASE_PRE_EXPORT_CONTAINMENT
    HOST_HANDOFF_VALIDATED = command_authority.PHASE_HOST_HANDOFF_VALIDATED
    EXPORT_RESET = command_authority.PHASE_EXPORT_RESET
    EXPORT_STAGE_READY = command_authority.PHASE_EXPORT_STAGE_READY
    EXPORT_COMMITTED = command_authority.PHASE_EXPORT_COMMITTED
    FINAL_CONTAINMENT = command_authority.PHASE_FINAL_CONTAINMENT
    FINAL_POST_CHECK = command_authority.PHASE_FINAL_POST_CHECK


@dataclass(frozen=True)
class PublisherPhaseEvent:
    kind: PublisherPhaseEventKind
    generation: str
    source_signature: str
    frame_id: str
    layer: str
    owner: str
    process_id: str
    session_id: str
    result: str
    terminal: bool
    reaped: bool
    synchronous: bool
    writes: tuple[str, ...]


@dataclass(frozen=True)
class PublisherPhaseResult:
    state: PublisherPhaseState
    errors: tuple[str, ...]

    @property
    def accepted(self) -> bool:
        return self.state is PublisherPhaseState.COMPLETE and not self.errors


@dataclass(frozen=True)
class _ExpectedEvent:
    kind: PublisherPhaseEventKind
    layer: str
    owner: str
    context: tuple[str, ...]
    result: str
    terminal: bool
    reaped: bool
    synchronous: bool
    writes: tuple[str, ...] = ()


_HOST_PROCESS = "$builder_supervisor_pid"
_HOST_SESSION = "$builder_session_id"
_CANDIDATE_PROCESS = "/mnt/control/candidate-launcher.py"
_CANDIDATE_SESSION = "$builder_supervisor_pid:$builder_session_id"
_TRUSTED_PROCESS = "trusted-publisher"
_TRUSTED_SESSION = "$builder_session_id"
_CONTROL_TRANSFER_EXECUTABLES = frozenset(
    {"break", "continue", "exec", "exit", "return", "trap"}
)
REVIEWED_CONTROL_TRANSFER_COUNT = 116
REVIEWED_CONTROL_TRANSFER_SHA256 = (
    "7c618f8624a7a260a5a84ef829fc702c3fab5dad69080e40fc7705524d7fed23"
)


def _frame_id(layer: str, owner: str, context: tuple[str, ...]) -> str:
    payload = "\0".join((layer, owner, "\x1f".join(context)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _semantic_signature_identity(
    signature: command_authority.CommandSignature,
) -> str:
    payload = {
        "kind": signature.kind,
        "layer": signature.layer,
        "owner": signature.owner,
        "control_context": signature.control_context,
        "preceding_operator": signature.preceding_operator,
        "following_operator": signature.following_operator,
        "executable": signature.executable,
        "wrappers": signature.wrappers,
        "argv": signature.argv,
        "stdin": signature.stdin,
        "stdout": signature.stdout,
        "stderr": signature.stderr,
        "redirections": signature.redirections,
        "accesses": signature.accesses,
        "writes": signature.writes,
        "events": tuple(
            event
            for event in signature.events
            if event.startswith(command_authority.PHASE_EVENT_PREFIX)
        ),
        "program_sha256": signature.program_sha256,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def phase_generation(
    signatures: Iterable[command_authority.CommandSignature],
) -> str:
    identities = sorted(
        _semantic_signature_identity(signature)
        for signature in signatures
        if signature.kind == "shell"
        and any(
            event in command_authority.PHASE_EVENT_NAMES
            for event in signature.events
        )
    )
    encoded = json.dumps(identities, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def control_transfer_digest(
    signatures: Iterable[command_authority.CommandSignature],
) -> tuple[int, str]:
    identities = sorted(
        _semantic_signature_identity(signature)
        for signature in signatures
        if signature.kind == "shell"
        and signature.executable in _CONTROL_TRANSFER_EXECUTABLES
    )
    encoded = json.dumps(identities, separators=(",", ":")).encode("ascii")
    return len(identities), hashlib.sha256(encoded).hexdigest()


def _event_metadata(
    kind: PublisherPhaseEventKind,
    signature: command_authority.CommandSignature,
) -> tuple[str, str, str, bool, bool, bool]:
    synchronous = (
        signature.preceding_operator is None
        and signature.following_operator is None
        and not any(
            context.startswith(
                (
                    "command-substitution:",
                    "process-substitution-input:",
                    "process-substitution-output:",
                    "subshell:",
                )
            )
            for context in signature.control_context
        )
    )
    if kind is PublisherPhaseEventKind.SUPERVISOR_LAUNCH_STARTED:
        return _HOST_PROCESS, _HOST_SESSION, "background-start", False, False, False
    if kind is PublisherPhaseEventKind.SUPERVISOR_AUTHENTICATED:
        return _HOST_PROCESS, _HOST_SESSION, "identity-authenticated", True, False, synchronous
    if kind is PublisherPhaseEventKind.SUPERVISOR_RUNNING:
        return _HOST_PROCESS, _HOST_SESSION, "session-running", True, False, synchronous
    if kind is PublisherPhaseEventKind.CANDIDATE_LAUNCH_STARTED:
        return _CANDIDATE_PROCESS, _CANDIDATE_SESSION, "foreground-start", False, False, synchronous
    if kind is PublisherPhaseEventKind.CANDIDATE_OUTPUT_WRITE:
        return _CANDIDATE_PROCESS, _CANDIDATE_SESSION, "write-completed", True, False, synchronous
    if kind is PublisherPhaseEventKind.CANDIDATE_COMPLETED:
        return _CANDIDATE_PROCESS, _CANDIDATE_SESSION, "isolated-exit-0", True, True, synchronous
    if kind is PublisherPhaseEventKind.MEMBERSHIP_COMPLETED:
        return _CANDIDATE_PROCESS, _CANDIDATE_SESSION, "membership-exit-0", True, False, synchronous
    if kind is PublisherPhaseEventKind.CONTROL_BYPASS:
        result = (
            "candidate-failure-only"
            if signature.argv == ("$candidate_status",)
            else "builder-success-return"
        )
        return _CANDIDATE_PROCESS, _CANDIDATE_SESSION, result, True, False, synchronous
    if kind is PublisherPhaseEventKind.SUPERVISOR_REAPED:
        return _HOST_PROCESS, _HOST_SESSION, "isolated-exit-0", True, True, synchronous
    if kind in {
        PublisherPhaseEventKind.PRE_EXPORT_CONTAINMENT,
        PublisherPhaseEventKind.FINAL_CONTAINMENT,
    }:
        return _TRUSTED_PROCESS, _TRUSTED_SESSION, "containment-empty", True, False, synchronous
    if kind in {
        PublisherPhaseEventKind.HOST_HANDOFF_VALIDATED,
        PublisherPhaseEventKind.EXPORT_RESET,
        PublisherPhaseEventKind.EXPORT_STAGE_READY,
        PublisherPhaseEventKind.EXPORT_COMMITTED,
        PublisherPhaseEventKind.FINAL_POST_CHECK,
    }:
        results = {
            PublisherPhaseEventKind.HOST_HANDOFF_VALIDATED: "handoff-valid",
            PublisherPhaseEventKind.EXPORT_RESET: "destination-reset",
            PublisherPhaseEventKind.EXPORT_STAGE_READY: "destination-ready",
            PublisherPhaseEventKind.EXPORT_COMMITTED: "commit-completed",
            PublisherPhaseEventKind.FINAL_POST_CHECK: "metadata-valid",
        }
        return _TRUSTED_PROCESS, _TRUSTED_SESSION, results[kind], True, False, synchronous
    results = {
        PublisherPhaseEventKind.ISOLATED_HANDOFF_VALIDATED: "handoff-valid",
        PublisherPhaseEventKind.EXPORT_STARTED: "export-rw-open",
        PublisherPhaseEventKind.EXPORT_WRITE: "write-completed",
        PublisherPhaseEventKind.EXPORT_SEALED: "export-ro-sealed",
    }
    return _CANDIDATE_PROCESS, _CANDIDATE_SESSION, results[kind], True, False, synchronous


def _phase_events_from_signature(
    signature: command_authority.CommandSignature,
    *,
    generation: str,
) -> tuple[PublisherPhaseEvent, ...]:
    events = []
    for value in signature.events:
        if value not in command_authority.PHASE_EVENT_NAMES:
            continue
        kind = PublisherPhaseEventKind(value)
        process_id, session_id, result, terminal, reaped, synchronous = (
            _event_metadata(kind, signature)
        )
        context = signature.control_context
        if kind in {
            PublisherPhaseEventKind.CANDIDATE_COMPLETED,
            PublisherPhaseEventKind.SUPERVISOR_REAPED,
        }:
            context = context[:-1]
        events.append(
            PublisherPhaseEvent(
                kind=kind,
                generation=generation,
                source_signature=_semantic_signature_identity(signature),
                frame_id=_frame_id(signature.layer, signature.owner, context),
                layer=signature.layer,
                owner=signature.owner,
                process_id=process_id,
                session_id=session_id,
                result=result,
                terminal=terminal,
                reaped=reaped,
                synchronous=synchronous,
                writes=signature.writes,
            )
        )
    return tuple(events)


def publisher_phase_events(
    signatures: Iterable[command_authority.CommandSignature],
) -> tuple[PublisherPhaseEvent, ...]:
    signatures = tuple(signatures)
    generation = phase_generation(signatures)
    shell = tuple(signature for signature in signatures if signature.kind == "shell")
    host = tuple(signature for signature in shell if signature.layer == "publisher-host")
    builder = tuple(
        signature for signature in shell if signature.layer == "builder-isolation"
    )
    candidate = tuple(
        signature for signature in shell if signature.layer == "candidate-build"
    )

    def collect(
        source: Iterable[command_authority.CommandSignature],
        kinds: frozenset[PublisherPhaseEventKind] | None = None,
    ) -> list[PublisherPhaseEvent]:
        result = []
        for signature in source:
            for event in _phase_events_from_signature(
                signature,
                generation=generation,
            ):
                if kinds is None or event.kind in kinds:
                    result.append(event)
        return result

    pre_kinds = frozenset(
        {
            PublisherPhaseEventKind.SUPERVISOR_LAUNCH_STARTED,
            PublisherPhaseEventKind.SUPERVISOR_AUTHENTICATED,
            PublisherPhaseEventKind.SUPERVISOR_RUNNING,
        }
    )
    post_kinds = frozenset(PublisherPhaseEventKind) - pre_kinds
    candidate_events = collect(candidate)
    builder_events = collect(builder)
    logical_builder = []
    inserted_candidate = False
    for event in builder_events:
        logical_builder.append(event)
        if event.kind is PublisherPhaseEventKind.CANDIDATE_LAUNCH_STARTED:
            logical_builder.extend(candidate_events)
            inserted_candidate = True
    if not inserted_candidate:
        logical_builder.extend(candidate_events)
    return tuple(
        (
            *collect(host, pre_kinds),
            *logical_builder,
            *collect(host, post_kinds),
        )
    )


def _expected_events() -> tuple[_ExpectedEvent, ...]:
    host = ("publisher-host", "<main>", ())
    builder = ("builder-isolation", "builder_main", ())
    candidate = ("candidate-build", "<main>", ())
    return (
        _ExpectedEvent(
            PublisherPhaseEventKind.SUPERVISOR_LAUNCH_STARTED,
            *host,
            "background-start",
            False,
            False,
            False,
            ("/dev/null",),
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.SUPERVISOR_AUTHENTICATED,
            "publisher-host",
            "<main>",
            ("loop", "if", "if"),
            "identity-authenticated",
            True,
            False,
            True,
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.SUPERVISOR_RUNNING,
            "publisher-host",
            "<main>",
            ("loop", "if"),
            "session-running",
            True,
            False,
            True,
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.CANDIDATE_LAUNCH_STARTED,
            *builder,
            "foreground-start",
            False,
            False,
            True,
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.CANDIDATE_OUTPUT_WRITE,
            *candidate,
            "write-completed",
            True,
            False,
            True,
            ("$HANDOFF/target.gba",),
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.CANDIDATE_OUTPUT_WRITE,
            *candidate,
            "write-completed",
            True,
            False,
            True,
            ("$HANDOFF/metadata.json",),
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.CONTROL_BYPASS,
            "builder-isolation",
            "builder_main",
            ("if",),
            "candidate-failure-only",
            True,
            False,
            True,
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.CANDIDATE_COMPLETED,
            *builder,
            "isolated-exit-0",
            True,
            True,
            True,
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.MEMBERSHIP_COMPLETED,
            *builder,
            "membership-exit-0",
            True,
            False,
            True,
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.ISOLATED_HANDOFF_VALIDATED,
            *builder,
            "handoff-valid",
            True,
            False,
            True,
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.EXPORT_STARTED,
            *builder,
            "export-rw-open",
            True,
            False,
            True,
            ("/mnt/export",),
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.EXPORT_WRITE,
            *builder,
            "write-completed",
            True,
            False,
            True,
            ("/mnt/export/target.gba",),
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.EXPORT_WRITE,
            *builder,
            "write-completed",
            True,
            False,
            True,
            ("/mnt/export/metadata.json",),
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.EXPORT_WRITE,
            *builder,
            "write-completed",
            True,
            False,
            True,
            ("/mnt/export/target.gba", "/mnt/export/metadata.json"),
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.EXPORT_SEALED,
            *builder,
            "export-ro-sealed",
            True,
            False,
            True,
            ("/mnt/export",),
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.CONTROL_BYPASS,
            *builder,
            "builder-success-return",
            True,
            False,
            True,
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.SUPERVISOR_REAPED,
            *host,
            "isolated-exit-0",
            True,
            True,
            True,
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.PRE_EXPORT_CONTAINMENT,
            *host,
            "containment-empty",
            True,
            False,
            True,
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.HOST_HANDOFF_VALIDATED,
            *host,
            "handoff-valid",
            True,
            False,
            True,
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.EXPORT_RESET,
            *host,
            "destination-reset",
            True,
            False,
            True,
            ("$PATCH_INPUT_ROOT",),
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.EXPORT_STAGE_READY,
            *host,
            "destination-ready",
            True,
            False,
            True,
            ("$PATCH_INPUT_ROOT",),
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.EXPORT_WRITE,
            *host,
            "write-completed",
            True,
            False,
            True,
            ("$PATCH_INPUT_ROOT/target.gba",),
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.EXPORT_WRITE,
            *host,
            "write-completed",
            True,
            False,
            True,
            ("$PATCH_INPUT_ROOT/metadata.json",),
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.EXPORT_COMMITTED,
            *host,
            "commit-completed",
            True,
            False,
            True,
            ("$PATCH_INPUT_ROOT/metadata.json",),
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.FINAL_CONTAINMENT,
            *host,
            "containment-empty",
            True,
            False,
            True,
        ),
        _ExpectedEvent(
            PublisherPhaseEventKind.FINAL_POST_CHECK,
            *host,
            "metadata-valid",
            True,
            False,
            True,
        ),
    )


def _expected_identity(
    kind: PublisherPhaseEventKind,
) -> tuple[str, str]:
    if kind in {
        PublisherPhaseEventKind.SUPERVISOR_LAUNCH_STARTED,
        PublisherPhaseEventKind.SUPERVISOR_AUTHENTICATED,
        PublisherPhaseEventKind.SUPERVISOR_RUNNING,
        PublisherPhaseEventKind.SUPERVISOR_REAPED,
    }:
        return _HOST_PROCESS, _HOST_SESSION
    if kind in {
        PublisherPhaseEventKind.PRE_EXPORT_CONTAINMENT,
        PublisherPhaseEventKind.FINAL_CONTAINMENT,
        PublisherPhaseEventKind.HOST_HANDOFF_VALIDATED,
        PublisherPhaseEventKind.EXPORT_RESET,
        PublisherPhaseEventKind.EXPORT_STAGE_READY,
        PublisherPhaseEventKind.EXPORT_COMMITTED,
        PublisherPhaseEventKind.FINAL_POST_CHECK,
    }:
        return _TRUSTED_PROCESS, _TRUSTED_SESSION
    return _CANDIDATE_PROCESS, _CANDIDATE_SESSION


def evaluate_phase_events(
    events: Iterable[PublisherPhaseEvent],
    *,
    expected_generation: str,
    expected_sources: tuple[str, ...] | None = None,
) -> PublisherPhaseResult:
    events = tuple(events)
    expected = _expected_events()
    errors = []
    if len(events) != len(expected):
        errors.append(
            "publisher phase event count differs: "
            f"expected {len(expected)}, got {len(events)}"
        )
    if expected_sources is not None and len(expected_sources) != len(expected):
        errors.append("publisher phase source contract count differs")
    state = PublisherPhaseState.INITIAL
    for index, (event, contract) in enumerate(zip(events, expected)):
        if event.kind is not contract.kind:
            errors.append(
                f"publisher phase event {index} differs: "
                f"expected {contract.kind.value}, got {event.kind.value}"
            )
            continue
        expected_process, expected_session = _expected_identity(event.kind)
        expected_frame = _frame_id(contract.layer, contract.owner, contract.context)
        checks = (
            (event.generation == expected_generation, "generation"),
            (
                re.fullmatch(r"[0-9a-f]{64}", event.source_signature) is not None,
                "source signature",
            ),
            (event.frame_id == expected_frame, "control frame"),
            (event.layer == contract.layer, "layer"),
            (event.owner == contract.owner, "owner"),
            (event.process_id == expected_process, "process"),
            (event.session_id == expected_session, "session"),
            (event.result == contract.result, "result"),
            (event.terminal is contract.terminal, "terminal state"),
            (event.reaped is contract.reaped, "reap state"),
            (event.synchronous is contract.synchronous, "execution mode"),
            (event.writes == contract.writes, "write set"),
        )
        for valid, label in checks:
            if not valid:
                errors.append(
                    f"publisher phase event {index} {label} differs"
                )
        if (
            expected_sources is not None
            and index < len(expected_sources)
            and event.source_signature != expected_sources[index]
        ):
            errors.append(
                f"publisher phase event {index} source signature differs"
            )
    transition_kinds = {
        PublisherPhaseEventKind.CANDIDATE_LAUNCH_STARTED: (
            PublisherPhaseState.INITIAL,
            PublisherPhaseState.CANDIDATE_RUNNING,
        ),
        PublisherPhaseEventKind.CANDIDATE_COMPLETED: (
            PublisherPhaseState.CANDIDATE_RUNNING,
            PublisherPhaseState.CANDIDATE_REAPED,
        ),
        PublisherPhaseEventKind.MEMBERSHIP_COMPLETED: (
            PublisherPhaseState.CANDIDATE_REAPED,
            PublisherPhaseState.MEMBERSHIP_VERIFIED,
        ),
        PublisherPhaseEventKind.EXPORT_STARTED: (
            PublisherPhaseState.MEMBERSHIP_VERIFIED,
            PublisherPhaseState.EXPORTING,
        ),
        PublisherPhaseEventKind.EXPORT_COMMITTED: (
            PublisherPhaseState.EXPORTING,
            PublisherPhaseState.EXPORT_COMMITTED,
        ),
        PublisherPhaseEventKind.FINAL_POST_CHECK: (
            PublisherPhaseState.EXPORT_COMMITTED,
            PublisherPhaseState.COMPLETE,
        ),
    }
    for event in events:
        transition = transition_kinds.get(event.kind)
        if transition is None:
            continue
        before, after = transition
        if state is not before:
            errors.append(
                f"publisher phase transition {event.kind.value} is invalid "
                f"from {state.value}"
            )
            continue
        state = after
    if state is not PublisherPhaseState.COMPLETE:
        errors.append(f"publisher phase terminated in {state.value}")
    return PublisherPhaseResult(state=state, errors=tuple(dict.fromkeys(errors)))


def phase_machine_errors(
    signatures: Iterable[command_authority.CommandSignature],
) -> tuple[str, ...]:
    signatures = tuple(signatures)
    shell = tuple(signature for signature in signatures if signature.kind == "shell")
    generation = phase_generation(shell)
    events = publisher_phase_events(signatures)
    result = evaluate_phase_events(events, expected_generation=generation)
    errors = list(result.errors)
    control_count, control_digest = control_transfer_digest(signatures)
    if (
        control_count != REVIEWED_CONTROL_TRANSFER_COUNT
        or control_digest != REVIEWED_CONTROL_TRANSFER_SHA256
    ):
        errors.append("publisher control-transfer phase evidence differs")

    membership_shell = [
        signature
        for signature in shell
        if command_authority.PHASE_MEMBERSHIP_COMPLETED in signature.events
    ]
    membership_python = [
        signature
        for signature in signatures
        if signature.kind == "python"
        and "cgroup-membership-check" in signature.events
        and command_authority.PHASE_MEMBERSHIP_COMPLETED in signature.events
    ]
    if len(membership_shell) != 1 or len(membership_python) != 1:
        errors.append("publisher membership phase signature count differs")
    elif membership_python[0].command != (
        "python:" + membership_shell[0].signature_id
    ):
        errors.append("publisher membership phase program binding differs")

    for signature in signatures:
        phase_events = tuple(
            event
            for event in signature.events
            if event in command_authority.PHASE_EVENT_NAMES
        )
        if any(
            path.startswith(("$HANDOFF/", "/mnt/export/", "$PATCH_INPUT_ROOT/"))
            for path in signature.writes
        ) and not any(
            event
            in {
                command_authority.PHASE_CANDIDATE_OUTPUT_WRITE,
                command_authority.PHASE_EXPORT_WRITE,
            }
            for event in phase_events
        ):
            errors.append(
                "publisher artifact writer lacks a phase event: "
                + signature.signature_id
            )
    return tuple(dict.fromkeys(errors))


def publisher_phase_errors(
    run_script: str,
    *,
    registry_path=command_authority.REGISTRY_PATH,
    require_authority_path: bool = True,
    require_reviewed_digest: bool = True,
) -> tuple[str, ...]:
    errors = list(
        command_authority.semantic_command_inventory_errors(
            run_script,
            registry_path=registry_path,
            require_authority_path=require_authority_path,
            require_reviewed_digest=require_reviewed_digest,
        )
    )
    try:
        signatures = command_authority.build_command_signatures(run_script)
    except ValueError as error:
        errors.append(str(error))
    else:
        errors.extend(phase_machine_errors(signatures))
    return tuple(dict.fromkeys(errors))


def assert_publisher_phase(run_script: str) -> None:
    errors = publisher_phase_errors(run_script)
    if errors:
        raise ValueError("; ".join(errors))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args(argv)
    if not arguments.check:
        parser.error("select --check")
    try:
        workflow = command_authority.WORKFLOW_PATH.read_text(encoding="utf-8")
        run_script = command_authority.publisher_builder_run_script(workflow)
        command_errors = command_authority.command_inventory_errors(run_script)
        if command_errors:
            raise ValueError("; ".join(command_errors))
        errors = phase_machine_errors(
            command_authority.build_command_signatures(run_script)
        )
        if errors:
            raise ValueError("; ".join(errors))
    except (OSError, ValueError) as error:
        print(f"publisher-phase-machine: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
