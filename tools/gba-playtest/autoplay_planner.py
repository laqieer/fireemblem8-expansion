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
MAX_SEARCH_BYTES = 64 * 1024 * 1024
PAGE_MAX_BYTES = 1024
OBSERVATION_HEADER_BYTES = 100
ACTION_RECORD_BYTES = 32
ACTIONS_PER_PAGE = (PAGE_MAX_BYTES - OBSERVATION_HEADER_BYTES) // ACTION_RECORD_BYTES
SEMANTIC_FIELD_COUNT = 8


class PageKind(str, Enum):
    CONTROL = "CONTROL"
    SUMMARY = "SUMMARY"
    MAP = "MAP"
    UNITS = "UNITS"
    ACTIONS = "ACTIONS"


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
    target_position: tuple[int, int] | None = None
    action_id: int | None = None
    target_item_slot: int | None = None


@dataclass(frozen=True)
class OpaqueToken:
    lo: int
    hi: int


@dataclass(frozen=True)
class ActionRecord:
    ordinal: int
    action: Action
    token: OpaqueToken


@dataclass(frozen=True)
class MapCell:
    x: int
    y: int
    terrain: int
    unit: int
    availability: Availability


@dataclass(frozen=True)
class UnitRecord:
    slot: int
    character: int
    unit_class: int
    position: tuple[int, int]
    hp: tuple[int, int]
    state: int
    inventory_digest: int
    availability: Availability


@dataclass(frozen=True)
class Observation:
    run_id: int
    observation_id: int
    chapter: int
    fields: tuple[Field, ...]
    actions: tuple[ActionRecord, ...]
    page_index: int = 0
    page_count: int = 1
    page_kind: PageKind = PageKind.SUMMARY
    total_action_count: int = 0
    map_cells: tuple[MapCell, ...] = ()
    units: tuple[UnitRecord, ...] = ()
    state: int = 0
    rejection: int = 0
    actual_rom_identity: int = 0
    actual_config_identity: int = 0
    actual_scenario_identity: int = 0
    actual_seed_identity: int = 0


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    run_id: int
    observation_id: int
    action_ordinal: int | None = None
    token: OpaqueToken | None = None
    page_index: int | None = None


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _mix_digest(digest: int, value: int) -> int:
    return ((digest ^ (value & 0xFFFFFFFF)) * 16777619) & 0xFFFFFFFF


def _fixture_action_token(
    run_id: int, observation_id: int, ordinal: int, action: Action
) -> OpaqueToken:
    action_ids = {
        "MOVE_WAIT": 0,
        "COMBAT": 1,
        "STAFF": 5,
        "USE_ITEM": 6,
        "PICK": 13,
        "SUMMON": 12,
    }
    action_id = action.action_id
    if action_id is None:
        action_id = action_ids[action.kind]
    target_id = action.target or 0
    item_slot = action.item_slot or 0
    target_item_slot = (
        0xFF if action.target_item_slot is None else action.target_item_slot
    )
    x_target, y_target = action.target_position or (0, 0)
    x_move, y_move = action.destination
    digest = 2166136261
    for value in (
        run_id,
        observation_id,
        ordinal,
        action.actor,
        (x_move & 0xFFFF) | ((y_move & 0xFFFF) << 16),
        action_id,
        target_id | (item_slot << 8),
        x_target | (y_target << 8),
        target_item_slot,
    ):
        digest = _mix_digest(digest, value)
    return OpaqueToken(digest, _mix_digest(digest, 0x92A11A9F))


def semantic_state_digest(state: object) -> str:
    return hashlib.sha256(_canonical(state)).hexdigest()


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
        self._all_actions: tuple[ActionRecord, ...] = ()
        self._trace: list[dict[str, object]] = []
        self._active = False
        self.cancelled = False

    @property
    def trace(self) -> tuple[dict[str, object], ...]:
        return tuple(self._trace)

    def begin(self, provenance: dict[str, object]) -> int:
        if self._active:
            raise PlannerError(Rejection.PROTOCOL_ERROR.value)
        if json.loads(_canonical(provenance)) != self._provenance:
            raise PlannerError("provenance mismatch")
        self._run_id += 1
        self._next_observation_id = 1
        self._observation = None
        self._all_actions = ()
        self._trace = [{"event": "run_start", "provenance": self._provenance, "run_id": self._run_id}]
        self._active = True
        self.cancelled = False
        return self._run_id

    def observe(self, chapter: int, fields: Iterable[Field], actions: Iterable[Action]) -> Observation:
        if not self._active or self.cancelled:
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
        if not action_tuple:
            raise PlannerError(Rejection.CAPABILITY_UNAVAILABLE.value)
        records = tuple(
            ActionRecord(
                index,
                action,
                _fixture_action_token(
                    self._run_id,
                    self._next_observation_id,
                    index,
                    action,
                ),
            )
            for index, action in enumerate(action_tuple)
        )
        observation_id = self._next_observation_id
        page_count = 1 + (len(records) + ACTIONS_PER_PAGE - 1) // ACTIONS_PER_PAGE
        event = {
            "action_count": len(records),
            "chapter": chapter,
            "event": "observation",
            "observation_id": observation_id,
            "run_id": self._run_id,
        }
        prospective = [*self._trace, event]
        if len(_canonical(prospective)) > MAX_TRACE_BYTES:
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)
        observation = Observation(
            self._run_id,
            observation_id,
            chapter,
            field_tuple,
            (),
            page_count=page_count,
            total_action_count=len(records),
        )
        self._next_observation_id += 1
        self._observation = observation
        self._all_actions = records
        self._trace = prospective
        return observation

    def exchange(self, command: Command) -> Observation | ActionRecord:
        if command.kind is CommandKind.PAGE:
            return self.page(command)
        return self.commit(command)

    def page(self, command: Command) -> Observation:
        observation = self._observation
        if observation is None:
            raise PlannerError(Rejection.NOT_READY.value)
        if command.kind is not CommandKind.PAGE:
            raise PlannerError(Rejection.PROTOCOL_ERROR.value)
        if (
            command.run_id != observation.run_id
            or command.observation_id != observation.observation_id
        ):
            raise PlannerError(Rejection.STALE_OBSERVATION.value)
        if command.page_index is None or not 1 <= command.page_index < observation.page_count:
            raise PlannerError(Rejection.UNKNOWN_ACTION.value)
        start = (command.page_index - 1) * ACTIONS_PER_PAGE
        actions = self._all_actions[start : start + ACTIONS_PER_PAGE]
        return Observation(
            observation.run_id,
            observation.observation_id,
            observation.chapter,
            (),
            actions,
            page_index=command.page_index,
            page_count=observation.page_count,
            page_kind=PageKind.ACTIONS,
            total_action_count=len(self._all_actions),
        )

    def commit(self, command: Command) -> ActionRecord:
        observation = self._observation
        if observation is None:
            raise PlannerError(Rejection.NOT_READY.value)
        if command.kind is CommandKind.CANCEL:
            if command.run_id != observation.run_id or command.observation_id != observation.observation_id:
                raise PlannerError(Rejection.STALE_OBSERVATION.value)
            self._trace.append({"event": "cancelled", "observation_id": observation.observation_id})
            self._observation = None
            self._all_actions = ()
            self._active = False
            self.cancelled = True
            raise PlannerError(Rejection.CANCELLED.value)
        if command.kind is not CommandKind.COMMIT:
            raise PlannerError(Rejection.PROTOCOL_ERROR.value)
        if command.run_id != observation.run_id or command.observation_id != observation.observation_id:
            raise PlannerError(Rejection.STALE_OBSERVATION.value)
        if command.action_ordinal is None or not 0 <= command.action_ordinal < len(self._all_actions):
            raise PlannerError(Rejection.UNKNOWN_ACTION.value)
        record = self._all_actions[command.action_ordinal]
        if command.token != record.token:
            raise PlannerError(Rejection.TOKEN_MISMATCH.value)
        committed = sum(
            entry.get("event") == "committed" for entry in self._trace
        )
        if committed >= MAX_TRACE_ACTIONS:
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)
        entry = {
            "action": asdict(record.action),
            "event": "committed",
            "observation_id": observation.observation_id,
            "ordinal": record.ordinal,
            "token": asdict(record.token),
        }
        prospective = [*self._trace, entry]
        if len(_canonical(prospective)) > MAX_TRACE_BYTES:
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)
        self._trace = prospective
        self._observation = None
        self._all_actions = ()
        return record

    def trace_digest(self) -> str:
        return hashlib.sha256(_canonical(self._trace)).hexdigest()


_AVAILABILITY_BY_VALUE = {
    0: Availability.AVAILABLE,
    1: Availability.NOT_APPLICABLE,
    2: Availability.NOT_VISIBLE,
    3: Availability.UNSUPPORTED_RULE,
    4: Availability.OUT_OF_RANGE,
    5: Availability.UNINITIALIZED,
}
_PAGE_KIND_BY_VALUE = {
    0: PageKind.CONTROL,
    1: PageKind.SUMMARY,
    2: PageKind.MAP,
    3: PageKind.UNITS,
    4: PageKind.ACTIONS,
}
_ACTION_KIND_BY_VALUE = {
    1: "MOVE_WAIT",
    2: "COMBAT",
    3: "STAFF",
    4: "USE_ITEM",
    5: "PICK",
    6: "SUMMON",
}
_SEMANTIC_FIELD_NAMES = {
    1: ("map_dimensions", "gBmMapSize", MAX_MAP_CELLS),
    2: ("map_state_digest", "gBmMapTerrain/gBmMapUnit/gBmMapFog", MAX_MAP_CELLS),
    3: ("active_unit", "gActiveUnit", MAX_UNITS),
    4: ("active_unit_state", "gActiveUnit", 0xFFFFFFFF),
    5: ("objective_id", "gExpansionChapterObjectiveTelemetry", 0xFFFFFFFF),
    6: ("objective_state", "gExpansionChapterObjectiveTelemetry", 0xFFFFFFFF),
    7: ("flags_digest", "event flag storage", 0xFFFFFFFF),
    8: ("resource_digest", "party gold and convoy", 0xFFFFFFFF),
}
_OBSERVATION_WORD_COUNT = 249
_OBSERVATION_HEADER_WORDS = 25


def parse_transport_observation(words: Iterable[int]) -> Observation:
    values = tuple(words)
    if len(values) != _OBSERVATION_WORD_COUNT:
        raise PlannerError("malformed fixed-width observation")
    if not any(values):
        return Observation(
            0,
            0,
            0,
            (),
            (),
            page_kind=PageKind.CONTROL,
        )
    if values[0] != 0x41504C4E or values[1] != PROTOCOL_VERSION:
        raise PlannerError("unexpected planner protocol identity")
    if values[2] > PAGE_MAX_BYTES or values[2] != _OBSERVATION_WORD_COUNT * 4:
        raise PlannerError("unexpected planner observation size")
    try:
        page_kind = _PAGE_KIND_BY_VALUE[values[8]]
    except KeyError as error:
        raise PlannerError("unknown planner page kind") from error
    record_start = values[9]
    record_count = values[10]
    total_records = values[11]
    total_actions = values[12]
    if record_start + record_count > total_records or total_actions > MAX_ACTIONS:
        raise PlannerError("planner page bounds are inconsistent")

    payload = values[_OBSERVATION_HEADER_WORDS:]
    fields: list[Field] = []
    map_cells: list[MapCell] = []
    units: list[UnitRecord] = []
    actions: list[ActionRecord] = []
    if page_kind is PageKind.CONTROL:
        if record_count != 0 or total_records != 0:
            raise PlannerError("control page must not publish payload records")
    elif page_kind is PageKind.SUMMARY:
        if record_count != SEMANTIC_FIELD_COUNT:
            raise PlannerError("summary field count is not canonical")
        for index in range(record_count):
            descriptor = payload[index * 2]
            field_id = descriptor & 0xFFFF
            availability_value = (descriptor >> 16) & 0xFF
            value_size = descriptor >> 24
            if field_id not in _SEMANTIC_FIELD_NAMES or value_size != 4:
                raise PlannerError("malformed semantic field descriptor")
            try:
                availability = _AVAILABILITY_BY_VALUE[availability_value]
            except KeyError as error:
                raise PlannerError("unknown semantic availability") from error
            name, source, bound = _SEMANTIC_FIELD_NAMES[field_id]
            fields.append(
                Field(
                    name,
                    source,
                    bound,
                    availability,
                    payload[index * 2 + 1]
                    if availability is Availability.AVAILABLE
                    else None,
                )
            )
    elif page_kind is PageKind.MAP:
        if record_count > len(payload):
            raise PlannerError("map page exceeds fixed payload")
        for encoded in payload[:record_count]:
            availability_value = (encoded >> 28) & 0xF
            try:
                availability = _AVAILABILITY_BY_VALUE[availability_value]
            except KeyError as error:
                raise PlannerError("unknown map availability") from error
            map_cells.append(
                MapCell(
                    encoded & 0x3F,
                    (encoded >> 6) & 0x3F,
                    (encoded >> 12) & 0xFF,
                    (encoded >> 20) & 0xFF,
                    availability,
                )
            )
    elif page_kind is PageKind.UNITS:
        if record_count * 4 > len(payload):
            raise PlannerError("unit page exceeds fixed payload")
        for index in range(record_count):
            identity, position, state, inventory = payload[index * 4 : index * 4 + 4]
            availability_value = identity >> 24
            try:
                availability = _AVAILABILITY_BY_VALUE[availability_value]
            except KeyError as error:
                raise PlannerError("unknown unit availability") from error
            units.append(
                UnitRecord(
                    identity & 0xFF,
                    (identity >> 8) & 0xFF,
                    (identity >> 16) & 0xFF,
                    (position & 0xFF, (position >> 8) & 0xFF),
                    ((position >> 16) & 0xFF, (position >> 24) & 0xFF),
                    state,
                    inventory,
                    availability,
                )
            )
    else:
        if record_count * 8 > len(payload):
            raise PlannerError("action page exceeds fixed payload")
        for index in range(record_count):
            (
                kind,
                actor,
                destination,
                target,
                item_slot,
                token_lo,
                token_hi,
                action_id,
            ) = payload[index * 8 : index * 8 + 8]
            if kind not in _ACTION_KIND_BY_VALUE:
                raise PlannerError("unknown planner action kind")
            actions.append(
                ActionRecord(
                    record_start + index,
                    Action(
                        _ACTION_KIND_BY_VALUE[kind],
                        actor,
                        (destination & 0xFFFF, destination >> 16),
                        target & 0xFF,
                        item_slot & 0xFF,
                        ((target >> 8) & 0xFF, (target >> 16) & 0xFF),
                        action_id,
                        None
                        if ((item_slot >> 8) & 0xFF) == 0xFF
                        else (item_slot >> 8) & 0xFF,
                    ),
                    OpaqueToken(token_lo, token_hi),
                )
            )
    return Observation(
        values[3],
        values[4],
        values[14],
        tuple(fields),
        tuple(actions),
        page_index=values[6],
        page_count=values[7],
        page_kind=page_kind,
        total_action_count=total_actions,
        map_cells=tuple(map_cells),
        units=tuple(units),
        state=values[5],
        rejection=values[13],
        actual_rom_identity=values[21],
        actual_config_identity=values[22],
        actual_scenario_identity=values[23],
        actual_seed_identity=values[24],
    )


def collect_observation_pages(transport: object, first: Observation) -> Observation:
    pages = [first]
    for page_index in range(1, first.page_count):
        page = transport.exchange(
            Command(
                CommandKind.PAGE,
                first.run_id,
                first.observation_id,
                page_index=page_index,
            )
        )
        if not isinstance(page, Observation):
            raise PlannerError("PAGE did not return an observation")
        if (
            page.run_id != first.run_id
            or page.observation_id != first.observation_id
            or page.page_index != page_index
            or page.page_count != first.page_count
        ):
            raise PlannerError(Rejection.STALE_OBSERVATION.value)
        pages.append(page)
    actions = tuple(
        action
        for page in pages
        if page.page_kind is PageKind.ACTIONS
        for action in page.actions
    )
    if len(actions) != first.total_action_count:
        raise PlannerError("PAGE traversal did not return every legal action")
    if tuple(action.ordinal for action in actions) != tuple(range(len(actions))):
        raise PlannerError("PAGE traversal returned non-canonical action ordinals")
    return Observation(
        first.run_id,
        first.observation_id,
        first.chapter,
        tuple(field for page in pages for field in page.fields),
        actions,
        page_index=first.page_index,
        page_count=first.page_count,
        page_kind=first.page_kind,
        total_action_count=first.total_action_count,
        map_cells=tuple(cell for page in pages for cell in page.map_cells),
        units=tuple(unit for page in pages for unit in page.units),
        state=first.state,
        rejection=first.rejection,
        actual_rom_identity=first.actual_rom_identity,
        actual_config_identity=first.actual_config_identity,
        actual_scenario_identity=first.actual_scenario_identity,
        actual_seed_identity=first.actual_seed_identity,
    )


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
        if len(_canonical([asdict(candidate) for candidate in candidates])) > MAX_SEARCH_BYTES:
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)
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
    first_complete = collect_observation_pages(bridge, first)
    first_choice = planner.choose(first_complete)
    bridge.commit(Command(CommandKind.COMMIT, run_id, first.observation_id, first_choice.ordinal, first_choice.token))
    semantic_state = {
        "accepted_token": asdict(first_choice.token),
        "casualties": {"blue": 0, "green": 0, "red": 0},
        "chapter": 2,
        "chapter_turn": 1,
        "flags": {"objective_complete": False, "village_saved": True},
        "inventory": ("fixture-key",),
        "objectives": {"kind": "seize", "progress": 1},
        "promotions": (),
        "recruitment": ("unit-1",),
        "resources": {"gold": 1000},
        "roster": ("unit-1", "unit-2"),
        "rng": (1, 2, 3),
        "trace_digest": bridge.trace_digest(),
    }
    checkpoint = {
        **semantic_state,
        "semantic_state_digest": semantic_state_digest(semantic_state),
    }
    second = bridge.observe(
        2,
        (
            Field("chapter", "PlaySt.chapterIndex", 0xFF, Availability.AVAILABLE, 2),
            Field("campaign_checkpoint", "normal_chapter_transition", 1, Availability.AVAILABLE, checkpoint),
        ),
        (Action("MOVE_WAIT", 1, (0, 0)),),
    )
    second_complete = collect_observation_pages(bridge, second)
    second_choice = planner.choose(second_complete)
    bridge.commit(Command(CommandKind.COMMIT, run_id, second.observation_id, second_choice.ordinal, second_choice.token))
    return {
        "campaign_checkpoint": checkpoint,
        "run_id": run_id,
        "terminal": "success",
        "trace": bridge.trace,
        "trace_digest": bridge.trace_digest(),
    }
