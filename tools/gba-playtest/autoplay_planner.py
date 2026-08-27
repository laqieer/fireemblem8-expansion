"""Bounded local planner bridge v1.

This module is intentionally transport-agnostic: an emulator adapter may read
the three planner symbols, but it can submit only typed mailbox commands. It
does not expose a raw address or arbitrary-memory write API.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Callable, Iterable


PROTOCOL_VERSION = 2
MAX_MAP_CELLS = 64 * 64
MAX_UNITS = 62 + 20 + 50
MAX_ACTIONS = 512
MAX_TRACE_ACTIONS = 4096
MAX_TRACE_BYTES = 2 * 1024 * 1024
PAGE_MAX_BYTES = 1024
OBSERVATION_HEADER_BYTES = 92
ACTION_RECORD_BYTES = 32
ACTIONS_PER_PAGE = (PAGE_MAX_BYTES - OBSERVATION_HEADER_BYTES) // ACTION_RECORD_BYTES


class PlannerError(ValueError):
    """A protocol violation that must never be converted into success."""


class Availability(str, Enum):
    AVAILABLE = "AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_VISIBLE = "NOT_VISIBLE"
    UNSUPPORTED_RULE = "UNSUPPORTED_RULE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    UNINITIALIZED = "UNINITIALIZED"


class Rejection(str, Enum):
    NOT_READY = "NOT_READY"
    STALE_OBSERVATION = "STALE_OBSERVATION"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"
    TOKEN_MISMATCH = "TOKEN_MISMATCH"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    ACTION_BECAME_ILLEGAL = "ACTION_BECAME_ILLEGAL"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    CANCELLED = "CANCELLED"
    PROTOCOL_ERROR = "PROTOCOL_ERROR"


class CommandKind(str, Enum):
    START = "START"
    COMMIT = "COMMIT"
    CANCEL = "CANCEL"
    PAGE = "PAGE"


@dataclass(frozen=True)
class Field:
    name: str
    source: str
    bound: int
    availability: Availability
    value: object | None


@dataclass(frozen=True)
class Action:
    kind: str
    actor: int
    destination: tuple[int, int]
    target: int | None = None
    item_slot: int | None = None


@dataclass(frozen=True)
class ActionRecord:
    ordinal: int
    action: Action
    token: str


@dataclass(frozen=True)
class Observation:
    run_id: int
    observation_id: int
    chapter: int
    fields: tuple[Field, ...]
    actions: tuple[ActionRecord, ...]


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    run_id: int
    observation_id: int
    action_ordinal: int | None = None
    token: str | None = None


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _action_token(run_id: int, observation_id: int, ordinal: int, action: Action) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "action": asdict(action),
                "observation_id": observation_id,
                "ordinal": ordinal,
                "protocol_version": PROTOCOL_VERSION,
                "run_id": run_id,
            }
        )
    ).hexdigest()[:32]


class Mailbox:
    """The sole host-to-ROM command surface; it intentionally has no address API."""

    def __init__(self) -> None:
        self._command: Command | None = None

    def submit(self, command: Command) -> None:
        if self._command is not None:
            raise PlannerError("mailbox already contains an unconsumed command")
        self._command = command

    def consume(self) -> Command | None:
        command = self._command
        self._command = None
        return command


class PlannerBridge:
    """In-memory mirror of the ROM's paged observation/token state machine."""

    def __init__(self, provenance: dict[str, object]) -> None:
        self._provenance = json.loads(_canonical(provenance))
        self.mailbox = Mailbox()
        self._run_id = 0
        self._next_observation_id = 1
        self._observation: Observation | None = None
        self._trace: list[dict[str, object]] = []
        self.cancelled = False

    @property
    def trace(self) -> tuple[dict[str, object], ...]:
        return tuple(self._trace)

    def begin(self, provenance: dict[str, object]) -> int:
        if json.loads(_canonical(provenance)) != self._provenance:
            raise PlannerError("provenance mismatch")
        self._run_id += 1
        self._next_observation_id = 1
        self._observation = None
        self._trace = [{"event": "run_start", "provenance": self._provenance, "run_id": self._run_id}]
        self.cancelled = False
        return self._run_id

    def observe(self, chapter: int, fields: Iterable[Field], actions: Iterable[Action]) -> Observation:
        if self._run_id == 0 or self.cancelled:
            raise PlannerError("bridge is not ready")
        if self._observation is not None:
            raise PlannerError("previous observation has not been committed or cancelled")
        field_tuple = tuple(fields)
        action_tuple = tuple(actions)
        if not 1 <= chapter <= 0xFF:
            raise PlannerError("chapter is outside the v1 range")
        if len(action_tuple) > MAX_ACTIONS:
            raise PlannerError("legal action count exceeds v1 resource limit")
        for field in field_tuple:
            if field.bound < 0 or field.availability is Availability.AVAILABLE and field.value is None:
                raise PlannerError(f"invalid availability state for {field.name!r}")
        records = tuple(
            ActionRecord(index, action, _action_token(self._run_id, self._next_observation_id, index, action))
            for index, action in enumerate(action_tuple)
        )
        observation = Observation(
            self._run_id,
            self._next_observation_id,
            chapter,
            field_tuple,
            records,
        )
        self._next_observation_id += 1
        self._observation = observation
        self._trace.append(
            {
                "action_count": len(records),
                "chapter": chapter,
                "event": "observation",
                "observation_id": observation.observation_id,
                "run_id": self._run_id,
            }
        )
        return observation

    @staticmethod
    def action_pages(observation: Observation) -> tuple[tuple[ActionRecord, ...], ...]:
        return tuple(
            observation.actions[index : index + ACTIONS_PER_PAGE]
            for index in range(0, len(observation.actions), ACTIONS_PER_PAGE)
        ) or ((),)

    def commit(self, command: Command) -> ActionRecord:
        observation = self._observation
        if observation is None:
            raise PlannerError(Rejection.NOT_READY.value)
        if command.kind is CommandKind.CANCEL:
            if command.run_id != observation.run_id or command.observation_id != observation.observation_id:
                raise PlannerError(Rejection.STALE_OBSERVATION.value)
            self._trace.append({"event": "cancelled", "observation_id": observation.observation_id})
            self._observation = None
            self.cancelled = True
            raise PlannerError(Rejection.CANCELLED.value)
        if command.kind is not CommandKind.COMMIT:
            raise PlannerError(Rejection.PROTOCOL_ERROR.value)
        if command.run_id != observation.run_id or command.observation_id != observation.observation_id:
            raise PlannerError(Rejection.STALE_OBSERVATION.value)
        if command.action_ordinal is None or not 0 <= command.action_ordinal < len(observation.actions):
            raise PlannerError(Rejection.UNKNOWN_ACTION.value)
        record = observation.actions[command.action_ordinal]
        if command.token != record.token:
            raise PlannerError(Rejection.TOKEN_MISMATCH.value)
        self._trace.append(
            {
                "action": asdict(record.action),
                "event": "committed",
                "observation_id": observation.observation_id,
                "ordinal": record.ordinal,
                "token": record.token,
            }
        )
        self._observation = None
        if len([entry for entry in self._trace if entry["event"] == "committed"]) > MAX_TRACE_ACTIONS:
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)
        if len(_canonical(self._trace)) > MAX_TRACE_BYTES:
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)
        return record

    def trace_digest(self) -> str:
        return hashlib.sha256(_canonical(self._trace)).hexdigest()


class ScriptedPlanner:
    def choose(self, observation: Observation) -> ActionRecord:
        for record in observation.actions:
            if record.action.kind in {"MOVE_WAIT", "COMBAT"}:
                return record
        raise PlannerError(Rejection.CAPABILITY_UNAVAILABLE.value)


class BoundedSearchPlanner:
    def __init__(self, max_nodes: int = 32) -> None:
        self.max_nodes = max_nodes

    def choose(self, observation: Observation) -> ActionRecord:
        if not 1 <= self.max_nodes <= MAX_ACTIONS:
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)
        candidates = observation.actions[: self.max_nodes]
        if not candidates:
            raise PlannerError(Rejection.CAPABILITY_UNAVAILABLE.value)
        return min(
            candidates,
            key=lambda record: (
                abs(record.action.destination[0]) + abs(record.action.destination[1]),
                record.action.kind,
                record.ordinal,
            ),
        )


def run_two_chapter_replay(
    planner: ScriptedPlanner | BoundedSearchPlanner,
    provenance: dict[str, object],
) -> dict[str, object]:
    """A deterministic two-chapter semantic fixture; no save or snapshot is used."""

    bridge = PlannerBridge(provenance)
    run_id = bridge.begin(provenance)
    first = bridge.observe(
        1,
        (
            Field("chapter", "PlaySt.chapterIndex", 0xFF, Availability.AVAILABLE, 1),
            Field("campaign_flag", "event_flag", 1, Availability.AVAILABLE, 0),
            Field("rng", "rng.c", 3, Availability.AVAILABLE, (1, 2, 3)),
        ),
        (Action("MOVE_WAIT", 1, (1, 0)), Action("COMBAT", 1, (2, 0), target=0x81)),
    )
    first_choice = planner.choose(first)
    bridge.commit(Command(CommandKind.COMMIT, run_id, first.observation_id, first_choice.ordinal, first_choice.token))
    checkpoint = {
        "chapter": 2,
        "inventory": ("fixture-key",),
        "rng": (1, 2, 3),
        "trace_digest": bridge.trace_digest(),
    }
    second = bridge.observe(
        2,
        (
            Field("chapter", "PlaySt.chapterIndex", 0xFF, Availability.AVAILABLE, 2),
            Field("campaign_checkpoint", "normal_chapter_transition", 1, Availability.AVAILABLE, checkpoint),
        ),
        (Action("MOVE_WAIT", 1, (0, 0)),),
    )
    second_choice = planner.choose(second)
    bridge.commit(Command(CommandKind.COMMIT, run_id, second.observation_id, second_choice.ordinal, second_choice.token))
    return {
        "campaign_checkpoint": checkpoint,
        "run_id": run_id,
        "terminal": "success",
        "trace": bridge.trace,
        "trace_digest": bridge.trace_digest(),
    }
