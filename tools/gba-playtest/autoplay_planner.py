"""Bounded local planner bridge v2.

This module is intentionally transport-agnostic: an emulator adapter may read
the three planner symbols, but it can submit only typed mailbox commands. It
does not expose a raw address or arbitrary-memory write API.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from enum import Enum
from typing import Callable, Iterable


PROTOCOL_VERSION = 2
MAX_MAP_CELLS = 64 * 64
MAX_UNITS = 62 + 20 + 50
MAX_ACTIONS = 512
MAX_PAGE_COUNT = 92
MAX_TRACE_ACTIONS = 4096
MAX_TRACE_BYTES = 2 * 1024 * 1024
MAX_TRANSCRIPT_EXCHANGE_BYTES = 64 * 1024
MAX_SEARCH_BYTES = 64 * 1024 * 1024
MAX_JSON_DEPTH = 64
PAGE_MAX_BYTES = 1024
OBSERVATION_HEADER_BYTES = 100
OBSERVATION_PAYLOAD_BYTES = 896
ACTION_RECORD_BYTES = 40
ACTIONS_PER_PAGE = OBSERVATION_PAYLOAD_BYTES // ACTION_RECORD_BYTES
SEMANTIC_FIELD_COUNT = 8
UNIT_ITEM_COUNT = 5
CONVOY_ITEM_COUNT = 100
AUTOPLAY_TELEMETRY_WORDS = 16


class PageKind(str, Enum):
    CONTROL = "CONTROL"
    SUMMARY = "SUMMARY"
    MAP = "MAP"
    UNITS = "UNITS"
    ACTIONS = "ACTIONS"
    INVENTORY = "INVENTORY"
    RESOURCES = "RESOURCES"
    FLAGS = "FLAGS"


class ValueKind(int, Enum):
    UNIT_ITEM = 1
    GOLD = 2
    CONVOY_ITEM = 3
    PERMANENT_FLAG = 4
    CHAPTER_FLAG = 5
    AUTOPLAY_TELEMETRY = 6


class PlannerError(ValueError):
    """A protocol violation that must never be converted into success."""


class Availability(str, Enum):
    AVAILABLE = "AVAILABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_VISIBLE = "NOT_VISIBLE"
    UNSUPPORTED_RULE = "UNSUPPORTED_RULE"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    UNINITIALIZED = "UNINITIALIZED"
    UNAVAILABLE = "UNAVAILABLE"
    EMPTY = "EMPTY"


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


_COMMAND_KIND_CODES = {
    CommandKind.START.value: 1,
    CommandKind.COMMIT.value: 2,
    CommandKind.CANCEL.value: 3,
    CommandKind.PAGE.value: 4,
}
_VALID_WIRE_REJECTION_CODES = frozenset(range(1, 11))
_WIRE_STALE_OBSERVATION = 2


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

    def __post_init__(self) -> None:
        for name, slot in (
            ("item_slot", self.item_slot),
            ("target_item_slot", self.target_item_slot),
        ):
            if slot is not None and not 0 <= slot < UNIT_ITEM_COUNT:
                raise PlannerError(f"invalid optional {name} sentinel")


@dataclass(frozen=True)
class OpaqueToken:
    word0: int
    word1: int
    word2: int
    word3: int

    def __post_init__(self) -> None:
        if any(not 0 <= word <= 0xFFFFFFFF for word in self.words):
            raise PlannerError("opaque token word is outside u32 range")

    @property
    def words(self) -> tuple[int, int, int, int]:
        return (self.word0, self.word1, self.word2, self.word3)


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
class InventoryRecord:
    unit: int
    slot: int
    item_id: int
    uses: int
    raw_item: int
    availability: Availability


@dataclass(frozen=True)
class ResourceRecord:
    kind: ValueKind
    slot: int | None
    value: int
    item_id: int | None
    uses: int | None
    availability: Availability


@dataclass(frozen=True)
class FlagRecord:
    kind: ValueKind
    flag_id: int
    state: int | None
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
    inventory: tuple[InventoryRecord, ...] = ()
    resources: tuple[ResourceRecord, ...] = ()
    flags: tuple[FlagRecord, ...] = ()
    state: int = 0
    rejection: int = 0
    chapter_turn: int = 0
    rng_state: tuple[int, int, int] = (0, 0, 0)
    rng_lcg: int = 0
    rng_consumption: int = 0
    actual_rom_identity: int = 0
    actual_config_identity: int = 0
    actual_scenario_identity: int = 0
    actual_seed_identity: int = 0
    record_start: int = 0
    record_count: int = 0
    total_record_count: int = 0


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    run_id: int
    observation_id: int
    action_ordinal: int | None = None
    token: OpaqueToken | None = None
    page_index: int | None = None


def _validate_json_structure(value: object) -> None:
    stack = [(value, 1, False)]
    active: set[int] = set()

    while stack:
        current, depth, leaving = stack.pop()
        if leaving:
            active.remove(id(current))
            continue
        if not isinstance(current, (dict, list, tuple)):
            continue
        if depth > MAX_JSON_DEPTH:
            raise PlannerError("invalid planner transcript JSON depth")
        identity = id(current)
        if identity in active:
            raise PlannerError("invalid planner transcript JSON structure")
        active.add(identity)
        stack.append((current, depth, True))
        values = (
            current.values()
            if isinstance(current, dict)
            else current
        )
        for child in values:
            stack.append((child, depth + 1, False))


def _validate_json_text_depth(data: bytes) -> None:
    depth = 0
    in_string = False
    escaped = False

    for value in data:
        if in_string:
            if escaped:
                escaped = False
            elif value == ord("\\"):
                escaped = True
            elif value == ord('"'):
                in_string = False
            continue
        if value == ord('"'):
            in_string = True
        elif value in (ord("{"), ord("[")):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise PlannerError(
                    "invalid planner transcript JSON depth"
                )
        elif value in (ord("}"), ord("]")):
            depth -= 1


def _canonical(value: object) -> bytes:
    _validate_json_structure(value)
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    except RecursionError as error:
        raise PlannerError(
            "invalid planner transcript JSON recursion"
        ) from error
    return encoded.encode("ascii")


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
        "SUMMON": 14,
    }
    action_id = action.action_id
    if action_id is None:
        action_id = action_ids[action.kind]
    target_id = action.target or 0
    item_slot = 0xFF if action.item_slot is None else action.item_slot
    target_item_slot = (
        0xFF if action.target_item_slot is None else action.target_item_slot
    )
    x_target, y_target = action.target_position or (0, 0)
    x_move, y_move = action.destination
    values = (
        run_id,
        observation_id,
        ordinal,
        action.actor
        | (action_id << 8)
        | (target_id << 16)
        | (item_slot << 24),
        (x_move & 0xFFFF) | ((y_move & 0xFFFF) << 16),
        x_target | (y_target << 8) | (target_item_slot << 16),
    )
    words = []
    for domain in (0x243F6A88, 0x85A308D3, 0x13198A2E, 0x03707344):
        digest = _mix_digest(2166136261, domain)
        for value in values:
            digest = _mix_digest(digest, value)
        words.append(digest)
    return OpaqueToken(*words)


def semantic_state_digest(state: object) -> str:
    return hashlib.sha256(_canonical(state)).hexdigest()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _observation_semantics(observation: Observation) -> dict[str, object]:
    serialized = asdict(observation)
    return {
        key: serialized[key]
        for key in (
            "chapter",
            "chapter_turn",
            "fields",
            "flags",
            "inventory",
            "map_cells",
            "resources",
            "rng_consumption",
            "rng_lcg",
            "rng_state",
            "units",
        )
    }


class PlannerTranscript:
    """Canonical bounded transcript shared by mirror and live transports."""

    SCHEMA = "fe8.autoplay-planner-transcript.v2"

    def __init__(self, max_bytes: int = MAX_TRACE_BYTES) -> None:
        if not 1 <= max_bytes <= MAX_TRACE_BYTES:
            raise PlannerError("transcript byte limit is outside v2 bounds")
        self.max_bytes = max_bytes
        self._events: list[dict[str, object]] = []

    @property
    def events(self) -> tuple[dict[str, object], ...]:
        return tuple(self._events)

    def _document(self, events: list[dict[str, object]]) -> dict[str, object]:
        return {
            "schema": self.SCHEMA,
            "events": events,
        }

    def _append_many(self, events: Iterable[dict[str, object]]) -> None:
        prospective = list(self._events)
        for event in events:
            chained = {
                "sequence": len(prospective),
                "previous_digest": (
                    prospective[-1]["event_digest"]
                    if prospective
                    else "0" * 64
                ),
                **event,
            }
            chained["event_digest"] = _digest(chained)
            prospective.append(chained)
        if len(_canonical(self._document(prospective))) > self.max_bytes:
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)
        self._events = prospective

    def _append(self, event: dict[str, object]) -> None:
        self._append_many((event,))

    def reserve_exchange(self) -> None:
        if (
            len(_canonical(self._document(self._events)))
            + MAX_TRANSCRIPT_EXCHANGE_BYTES
            > self.max_bytes
        ):
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)

    @staticmethod
    def _session_event(
        provenance: dict[str, object],
    ) -> dict[str, object]:
        return {
            "event": "session",
            "provenance": json.loads(_canonical(provenance)),
        }

    @staticmethod
    def _observation_page_event(
        observation: Observation,
    ) -> dict[str, object]:
        return {
            "event": "observation_page",
            "observation": asdict(observation),
        }

    def record_session(self, provenance: dict[str, object]) -> None:
        self._append(self._session_event(provenance))

    def record_observation_page(self, observation: Observation) -> None:
        self._append(self._observation_page_event(observation))

    def record_session_observation(
        self,
        provenance: dict[str, object],
        observation: Observation,
        checkpoint: Iterable[int],
        command_words: Iterable[int],
    ) -> None:
        self._append_many(
            (
                self._session_event(provenance),
                self._observation_page_event(observation),
                self._settled_event(
                    observation,
                    checkpoint,
                    command_words,
                ),
            )
        )

    @staticmethod
    def _complete_observation_event(
        observation: Observation,
    ) -> dict[str, object]:
        actions = tuple(asdict(record) for record in observation.actions)
        semantics = _observation_semantics(observation)
        return {
            "event": "observation_complete",
            "observation": asdict(observation),
            "candidate_set_digest": _digest(actions),
            "semantic_digest": _digest(semantics),
            "page_identity": [
                observation.run_id,
                observation.observation_id,
                observation.page_count,
                observation.total_action_count,
            ],
        }

    def record_complete_observation(self, observation: Observation) -> None:
        self._append(self._complete_observation_event(observation))

    def record_command(self, command: dict[str, object]) -> None:
        self._append(
            {
                "event": "command",
                "command": json.loads(_canonical(command)),
            }
        )

    def record_acknowledgement(
        self,
        command_id: int,
        kind: int,
        result: int,
        rejection: int,
    ) -> None:
        self._append(
            {
                "event": "acknowledgement",
                "command_id": command_id,
                "kind": kind,
                "result": result,
                "rejection": rejection,
            }
        )

    def record_completion(
        self,
        command_id: int,
        kind: int,
        response_frames: int,
    ) -> None:
        self._append(
            {
                "event": "completion",
                "command_id": command_id,
                "kind": kind,
                "response_frames": response_frames,
            }
        )

    @staticmethod
    def _settled_event(
        observation: Observation,
        checkpoint: Iterable[int],
        command_words: Iterable[int],
    ) -> dict[str, object]:
        checkpoint_values = tuple(checkpoint)
        command_values = tuple(command_words)
        if len(checkpoint_values) != 13 or len(command_values) != 16:
            raise PlannerError("settled transcript record has invalid width")
        telemetry = tuple(
            record.value
            for record in observation.resources
            if record.kind is ValueKind.AUTOPLAY_TELEMETRY
        )
        return {
            "event": "settled",
            "observation_identity": [
                observation.run_id,
                observation.observation_id,
                observation.page_index,
                observation.page_count,
                observation.page_kind,
                observation.total_action_count,
            ],
            "observation_digest": _digest(asdict(observation)),
            "checkpoint": checkpoint_values,
            "command_words": command_values,
            "telemetry": telemetry,
            "rng": {
                "state": observation.rng_state,
                "lcg": observation.rng_lcg,
                "consumption": observation.rng_consumption,
            },
            "terminal": {
                "state": observation.state,
                "rejection": observation.rejection,
            },
        }

    def record_settled(
        self,
        observation: Observation,
        checkpoint: Iterable[int],
        command_words: Iterable[int],
    ) -> None:
        self._append(
            self._settled_event(observation, checkpoint, command_words)
        )

    def record_complete_and_settled(
        self,
        observation: Observation,
        checkpoint: Iterable[int],
        command_words: Iterable[int],
    ) -> None:
        self._append_many(
            (
                self._complete_observation_event(observation),
                self._settled_event(
                    observation,
                    checkpoint,
                    command_words,
                ),
            )
        )

    def record_transport_error(
        self,
        code: str,
        command_id: int,
        kind: int,
    ) -> None:
        self._append(
            {
                "event": "transport_error",
                "code": code,
                "command_id": command_id,
                "kind": kind,
            }
        )

    def export(self) -> bytes:
        return _canonical(self._document(self._events))

    def digest(self) -> str:
        return hashlib.sha256(self.export()).hexdigest()

    @classmethod
    def import_bytes(cls, data: bytes) -> "PlannerTranscript":
        if len(data) > MAX_TRACE_BYTES:
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)
        _validate_json_text_depth(data)
        try:
            document = json.loads(data)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
        ) as error:
            raise PlannerError("invalid planner transcript JSON") from error
        if _canonical(document) != data:
            raise PlannerError("planner transcript is not canonical")
        if (
            not isinstance(document, dict)
            or document.get("schema") != cls.SCHEMA
            or not isinstance(document.get("events"), list)
        ):
            raise PlannerError("invalid planner transcript envelope")
        events = document["events"]
        if (
            not events
            or not isinstance(events[0], dict)
            or events[0].get("event") != "session"
            or sum(
                isinstance(event, dict)
                    and event.get("event") == "session"
                for event in events
            ) != 1
        ):
            raise PlannerError(
                "planner transcript requires exactly one leading session"
            )
        session_provenance = events[0].get("provenance")
        required_session_fields = {
            "transport",
            "rom_identity",
            "config_identity",
            "scenario_identity",
            "seed_identity",
            "ready_run_id",
            "run_id",
        }
        if (
            not isinstance(session_provenance, dict)
            or not required_session_fields.issubset(session_provenance)
            or not isinstance(session_provenance["transport"], str)
            or not session_provenance["transport"]
            or any(
                type(session_provenance[field]) is not int
                or not 0 <= session_provenance[field] <= 0xFFFFFFFF
                for field in required_session_fields - {"transport"}
            )
            or session_provenance["run_id"]
                != session_provenance["ready_run_id"] + 1
        ):
            raise PlannerError("invalid planner transcript session provenance")
        transcript = cls()
        previous_digest = "0" * 64
        pending_command: dict[str, object] | None = None
        pending_ack: dict[str, object] | None = None
        pending_completion = False
        pending_response = False
        awaiting_settlement = False
        expected_command_id = 1
        actions_by_observation: dict[
            tuple[int, int],
            list[object],
        ] = {}
        latest_observation: dict[str, object] | None = None
        observation_pages: dict[tuple[int, int], list[dict[str, object]]] = {}
        active_identity_bound = False
        for sequence, event in enumerate(events):
            if not isinstance(event, dict):
                raise PlannerError("invalid planner transcript event")
            if (
                event.get("sequence") != sequence
                or event.get("previous_digest") != previous_digest
            ):
                raise PlannerError("planner transcript order is invalid")
            event_without_digest = dict(event)
            event_digest = event_without_digest.pop("event_digest", None)
            if event_digest != _digest(event_without_digest):
                raise PlannerError("planner transcript digest mismatch")
            previous_digest = event_digest
            kind = event.get("event")
            if kind == "session":
                if sequence != 0:
                    raise PlannerError(
                        "planner transcript session must be first"
                    )
            elif kind == "command":
                if pending_command is not None or awaiting_settlement:
                    raise PlannerError("planner transcript command overlap")
                command = event.get("command")
                if (
                    not isinstance(command, dict)
                    or type(command.get("run_id")) is not int
                    or not 0 <= command["run_id"] <= 0xFFFFFFFF
                    or not (
                        isinstance(command.get("kind"), str)
                        or type(command.get("kind")) is int
                    )
                ):
                    raise PlannerError("invalid transcript command")
                pending_command = event
                pending_ack = None
                pending_completion = False
                pending_response = False
            elif kind == "acknowledgement":
                if (
                    pending_command is None
                    or pending_ack is not None
                    or event.get("command_id") != expected_command_id
                ):
                    raise PlannerError("planner transcript acknowledgement order")
                command = pending_command.get("command")
                if not isinstance(command, dict):
                    raise PlannerError("invalid transcript command")
                command_kind = command.get("kind")
                command_kind_code = _COMMAND_KIND_CODES.get(
                    command_kind,
                    command_kind if type(command_kind) is int else None,
                )
                if (
                    type(event.get("command_id")) is not int
                    or type(event.get("kind")) is not int
                    or type(command_kind_code) is not int
                    or event.get("kind") != command_kind_code
                ):
                    raise PlannerError("acknowledgement kind mismatch")
                result = event.get("result")
                rejection = event.get("rejection")
                if type(result) is not int or type(rejection) is not int:
                    raise PlannerError(
                        "invalid acknowledgement result/rejection pair"
                    )
                accepted = result == 1 and rejection == 0
                rejected = (
                    result == 0
                    and rejection in _VALID_WIRE_REJECTION_CODES
                )
                if not (accepted or rejected) or (
                    accepted
                    and command_kind_code not in _COMMAND_KIND_CODES.values()
                ):
                    raise PlannerError(
                        "invalid acknowledgement result/rejection pair"
                    )
                if accepted and (
                    latest_observation is None
                    or command.get("run_id")
                        != latest_observation.get("run_id")
                    or command.get("run_id")
                        != (
                            session_provenance["ready_run_id"]
                            if command_kind_code == 1
                            else session_provenance["run_id"]
                        )
                ):
                    raise PlannerError(
                        "accepted command run identity mismatch"
                    )
                if (
                    command_kind_code != 1
                    and rejection != _WIRE_STALE_OBSERVATION
                    and (
                        latest_observation is None
                        or type(command.get("observation_id")) is not int
                        or command.get("observation_id")
                            != latest_observation.get("observation_id")
                    )
                ):
                    raise PlannerError(
                        "command observation identity mismatch"
                    )
                if accepted and command_kind_code == 4 and (
                    type(command.get("page_index")) is not int
                    or latest_observation is None
                    or not 0 <= command["page_index"]
                        < latest_observation.get("page_count", 0)
                ):
                    raise PlannerError("PAGE command identity mismatch")
                if accepted and command_kind_code == 1 and (
                    command.get("expected_identities")
                    != [
                        session_provenance["rom_identity"],
                        session_provenance["config_identity"],
                        session_provenance["scenario_identity"],
                        session_provenance["seed_identity"],
                    ]
                ):
                    raise PlannerError(
                        "START command session identity mismatch"
                    )
                pending_ack = event
                expected_command_id += 1
                if accepted and command_kind_code == 2:
                    ordinal = command.get("action_ordinal")
                    actions = actions_by_observation.get(
                        (
                            command["run_id"],
                            command.get("observation_id"),
                        ),
                        [],
                    )
                    action = (
                        actions[ordinal]
                        if type(ordinal) is int
                        and 0 <= ordinal < len(actions)
                        else None
                    )
                    if (
                        type(ordinal) is not int
                        or not 0 <= ordinal < len(actions)
                        or not isinstance(action, dict)
                        or command.get("token")
                            != action.get("token")
                    ):
                        raise PlannerError("accepted transcript token mismatch")
            elif kind == "completion":
                if (
                    pending_ack is None
                    or pending_completion
                    or event.get("command_id")
                        != pending_ack.get("command_id")
                    or event.get("kind") != pending_ack.get("kind")
                ):
                    raise PlannerError("planner transcript completion order")
                pending_completion = True
            elif kind == "settled":
                settled_command = pending_command
                settled_ack = pending_ack
                if not awaiting_settlement:
                    raise PlannerError(
                        "settled event has no response observation"
                    )
                if pending_command is not None:
                    if not pending_completion or not pending_response:
                        raise PlannerError(
                            "settled event precedes command response"
                        )
                    pending_command = None
                    pending_ack = None
                    pending_completion = False
                    pending_response = False
                awaiting_settlement = False
                if (
                    not isinstance(event.get("observation_identity"), list)
                    or len(event["observation_identity"]) != 6
                    or not isinstance(event.get("checkpoint"), list)
                    or len(event["checkpoint"]) != 13
                    or not isinstance(event.get("command_words"), list)
                    or len(event["command_words"]) != 16
                    or not isinstance(event.get("telemetry"), list)
                    or len(event["telemetry"]) > AUTOPLAY_TELEMETRY_WORDS
                    or latest_observation is None
                ):
                    raise PlannerError("invalid settled transcript record")
                terminal = event.get("terminal")
                rng = event.get("rng")
                if (
                    not isinstance(terminal, dict)
                    or set(terminal) != {"state", "rejection"}
                    or not isinstance(rng, dict)
                    or set(rng) != {"state", "lcg", "consumption"}
                    or not isinstance(rng.get("state"), list)
                    or len(rng["state"]) != 3
                ):
                    raise PlannerError("invalid settled runtime state")
                expected_identity = [
                    latest_observation.get("run_id"),
                    latest_observation.get("observation_id"),
                    latest_observation.get("page_index"),
                    latest_observation.get("page_count"),
                    latest_observation.get("page_kind"),
                    latest_observation.get("total_action_count"),
                ]
                expected_telemetry = [
                    record.get("value")
                    for record in latest_observation.get("resources", [])
                    if record.get("kind")
                        == ValueKind.AUTOPLAY_TELEMETRY.value
                ]
                if (
                    event["observation_identity"] != expected_identity
                    or event.get("observation_digest")
                        != _digest(latest_observation)
                    or terminal != {
                        "state": latest_observation.get("state"),
                        "rejection": latest_observation.get("rejection"),
                    }
                    or rng != {
                        "state": latest_observation.get("rng_state"),
                        "lcg": latest_observation.get("rng_lcg"),
                        "consumption": latest_observation.get(
                            "rng_consumption"
                        ),
                    }
                    or event["telemetry"] != expected_telemetry
                ):
                    raise PlannerError("settled runtime state mismatch")
                if settled_ack is not None:
                    acknowledgement_accepted = (
                        settled_ack.get("result") == 1
                        and settled_ack.get("rejection") == 0
                    )
                    expected_rejection = (
                        0
                        if acknowledgement_accepted
                        else settled_ack.get("rejection")
                    )
                    if terminal["rejection"] != expected_rejection:
                        raise PlannerError(
                            "settled rejection does not match acknowledgement"
                        )
                    command = settled_command.get("command")
                    if (
                        not acknowledgement_accepted
                        and isinstance(command, dict)
                        and command.get("kind")
                            == CommandKind.COMMIT.value
                        and terminal["state"] == 3
                    ):
                        raise PlannerError(
                            "rejected COMMIT cannot settle as COMMITTED"
                        )
            elif kind == "transport_error":
                if (
                    sequence != len(document["events"]) - 1
                    or awaiting_settlement
                ):
                    raise PlannerError("transport error must terminate transcript")
                pending_command = None
                pending_ack = None
            elif kind == "observation_complete":
                if pending_command is not None or awaiting_settlement:
                    raise PlannerError(
                        "complete observation violates command ordering"
                    )
                observation = event.get("observation")
                if not isinstance(observation, dict):
                    raise PlannerError("invalid complete observation")
                active_identity_bound = cls._validate_session_observation(
                    observation,
                    session_provenance,
                    active_identity_bound,
                )
                expected_page_identity = [
                    observation.get("run_id"),
                    observation.get("observation_id"),
                    observation.get("page_count"),
                    observation.get("total_action_count"),
                ]
                if event.get("page_identity") != expected_page_identity:
                    raise PlannerError(
                        "complete observation page identity mismatch"
                    )
                actions = observation.get("actions")
                if (
                    not isinstance(actions, list)
                    or len(actions)
                        != observation.get("total_action_count")
                    or [
                        action.get("ordinal")
                        for action in actions
                        if isinstance(action, dict)
                    ] != list(range(len(actions)))
                ):
                    raise PlannerError(
                        "complete observation action identity mismatch"
                    )
                if event.get("candidate_set_digest") != _digest(
                    actions
                ):
                    raise PlannerError("candidate-set transcript digest mismatch")
                semantics = {
                    key: observation.get(key)
                    for key in (
                        "chapter",
                        "chapter_turn",
                        "fields",
                        "flags",
                        "inventory",
                        "map_cells",
                        "resources",
                        "rng_consumption",
                        "rng_lcg",
                        "rng_state",
                        "units",
                    )
                }
                if event.get("semantic_digest") != _digest(semantics):
                    raise PlannerError("semantic transcript digest mismatch")
                page_key = (
                    observation.get("run_id"),
                    observation.get("observation_id"),
                )
                pages = observation_pages.pop(page_key, [])
                if (
                    session_provenance["transport"]
                        == "restricted-libmgba"
                    and not pages
                ):
                    raise PlannerError(
                        "complete observation has no transport pages"
                    )
                if pages:
                    if (
                        len(pages) != observation.get("page_count")
                        or [page.get("page_index") for page in pages]
                            != list(range(len(pages)))
                    ):
                        raise PlannerError(
                            "complete observation page order mismatch"
                        )
                    common_fields = (
                        "run_id",
                        "observation_id",
                        "chapter",
                        "chapter_turn",
                        "page_count",
                        "total_action_count",
                        "state",
                        "rejection",
                        "rng_state",
                        "rng_lcg",
                        "rng_consumption",
                        "actual_rom_identity",
                        "actual_config_identity",
                        "actual_scenario_identity",
                        "actual_seed_identity",
                    )
                    if any(
                        any(
                            page.get(field) != observation.get(field)
                            for field in common_fields
                        )
                        for page in pages
                    ):
                        raise PlannerError(
                            "complete observation page state mismatch"
                        )
                    page_kind_order = {
                        PageKind.SUMMARY.value: 0,
                        PageKind.MAP.value: 1,
                        PageKind.UNITS.value: 2,
                        PageKind.INVENTORY.value: 3,
                        PageKind.RESOURCES.value: 4,
                        PageKind.FLAGS.value: 5,
                        PageKind.ACTIONS.value: 6,
                    }
                    try:
                        ranks = [
                            page_kind_order[page.get("page_kind")]
                            for page in pages
                        ]
                    except KeyError as error:
                        raise PlannerError(
                            "complete observation contains a control page"
                        ) from error
                    if (
                        ranks[0] != 0
                        or ranks != sorted(ranks)
                        or ranks.count(0) != 1
                    ):
                        raise PlannerError(
                            "complete observation page-kind order mismatch"
                        )
                    for field in (
                        "fields",
                        "map_cells",
                        "units",
                        "inventory",
                        "resources",
                        "flags",
                        "actions",
                    ):
                        flattened = [
                            record
                            for page in pages
                            for record in page.get(field, [])
                        ]
                        if flattened != observation.get(field):
                            raise PlannerError(
                                "complete observation payload mismatch"
                            )
                actions_by_observation[
                    (
                        observation.get("run_id"),
                        observation.get("observation_id"),
                    )
                ] = actions
                latest_observation = observation
                awaiting_settlement = True
            elif kind == "observation_page":
                if awaiting_settlement:
                    raise PlannerError(
                        "response observation is missing settlement"
                    )
                if pending_command is not None and not pending_completion:
                    raise PlannerError(
                        "response observation precedes completion"
                    )
                observation = event.get("observation")
                if not isinstance(observation, dict):
                    raise PlannerError("invalid observation page")
                active_identity_bound = cls._validate_session_observation(
                    observation,
                    session_provenance,
                    active_identity_bound,
                )
                page_key = (
                    observation.get("run_id"),
                    observation.get("observation_id"),
                )
                page_index = observation.get("page_index")
                pages = observation_pages.get(page_key)
                if page_index == 0:
                    observation_pages[page_key] = [observation]
                elif pages is not None and page_index == len(pages):
                    pages.append(observation)
                if pending_command is not None:
                    command = pending_command["command"]
                    command_kind = command.get("kind")
                    accepted = (
                        pending_ack is not None
                        and pending_ack.get("result") == 1
                        and pending_ack.get("rejection") == 0
                    )
                    if accepted and command_kind == CommandKind.PAGE.value:
                        if (
                            observation.get("run_id")
                                != command.get("run_id")
                            or observation.get("observation_id")
                                != command.get("observation_id")
                            or observation.get("page_index")
                                != command.get("page_index")
                        ):
                            raise PlannerError(
                                "PAGE response identity mismatch"
                            )
                    pending_response = True
                latest_observation = observation
                awaiting_settlement = True
            else:
                raise PlannerError("unknown planner transcript event")
        if pending_command is not None or awaiting_settlement:
            raise PlannerError("planner transcript is truncated")
        transcript._events = document["events"]
        return transcript

    @staticmethod
    def _validate_session_observation(
        observation: dict[str, object],
        session: dict[str, object],
        active_identity_bound: bool,
    ) -> bool:
        run_id = observation.get("run_id")
        identity_fields = (
            "run_id",
            "actual_rom_identity",
            "actual_config_identity",
            "actual_scenario_identity",
            "actual_seed_identity",
        )
        if (
            any(
                type(observation.get(field)) is not int
                or not 0 <= observation[field] <= 0xFFFFFFFF
                for field in identity_fields
            )
            or run_id not in {session["ready_run_id"], session["run_id"]}
            or observation.get("actual_rom_identity")
                != session["rom_identity"]
            or observation.get("actual_config_identity")
                != session["config_identity"]
        ):
            raise PlannerError("observation session identity mismatch")
        if run_id == session["ready_run_id"] or not active_identity_bound:
            if (
                observation.get("actual_scenario_identity")
                    != session["scenario_identity"]
                or observation.get("actual_seed_identity")
                    != session["seed_identity"]
            ):
                raise PlannerError(
                    "observation session scenario/seed mismatch"
                )
        return active_identity_bound or run_id == session["run_id"]


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
        self.transcript = PlannerTranscript()
        self._committed_count = 0
        self._next_command_id = 1
        self._active = False
        self.cancelled = False

    @property
    def trace(self) -> tuple[dict[str, object], ...]:
        return self.transcript.events

    def begin(self, provenance: dict[str, object]) -> int:
        if self._active:
            raise PlannerError(Rejection.PROTOCOL_ERROR.value)
        if json.loads(_canonical(provenance)) != self._provenance:
            raise PlannerError("provenance mismatch")
        self._run_id += 1
        self._next_observation_id = 1
        self._observation = None
        self._all_actions = ()
        self.transcript = PlannerTranscript()
        self.transcript.record_session(
            {
                "transport": "mirror",
                "rom_identity": 0,
                "config_identity": 0,
                "scenario_identity": 0,
                "seed_identity": 0,
                "ready_run_id": self._run_id - 1,
                "run_id": self._run_id,
                "source": self._provenance,
            }
        )
        self._committed_count = 0
        self._next_command_id = 1
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
            raise PlannerError("chapter is outside the v2 range")
        if len(action_tuple) > MAX_ACTIONS:
            raise PlannerError("legal action count exceeds v2 resource limit")
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
        complete_observation = Observation(
            self._run_id,
            observation_id,
            chapter,
            field_tuple,
            records,
            page_count=page_count,
            total_action_count=len(records),
            record_count=len(field_tuple),
            total_record_count=len(field_tuple),
        )
        self.transcript.record_complete_and_settled(
            complete_observation,
            (0,) * 13,
            (0,) * 16,
        )
        observation = Observation(
            self._run_id,
            observation_id,
            chapter,
            field_tuple,
            (),
            page_count=page_count,
            total_action_count=len(records),
            record_count=len(field_tuple),
            total_record_count=len(field_tuple),
        )
        self._next_observation_id += 1
        self._observation = observation
        self._all_actions = records
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
        page = Observation(
            observation.run_id,
            observation.observation_id,
            observation.chapter,
            (),
            actions,
            page_index=command.page_index,
            page_count=observation.page_count,
            page_kind=PageKind.ACTIONS,
            total_action_count=len(self._all_actions),
            record_start=start,
            record_count=len(actions),
            total_record_count=len(self._all_actions),
        )
        self.transcript.reserve_exchange()
        self.transcript.record_command(asdict(command))
        command_id = self._next_command_id
        self._next_command_id += 1
        self.transcript.record_acknowledgement(command_id, 4, 1, 0)
        self.transcript.record_completion(command_id, 4, 0)
        self.transcript.record_observation_page(page)
        self.transcript.record_settled(page, (0,) * 13, (0,) * 16)
        return page

    def commit(self, command: Command) -> ActionRecord:
        observation = self._observation
        if observation is None:
            raise PlannerError(Rejection.NOT_READY.value)
        if command.kind is CommandKind.CANCEL:
            if command.run_id != observation.run_id or command.observation_id != observation.observation_id:
                raise PlannerError(Rejection.STALE_OBSERVATION.value)
            self.transcript.reserve_exchange()
            self.transcript.record_command(asdict(command))
            command_id = self._next_command_id
            self._next_command_id += 1
            self.transcript.record_acknowledgement(
                command_id,
                3,
                0,
                8,
            )
            self.transcript.record_completion(command_id, 3, 0)
            settled = replace(observation, state=4, rejection=8)
            self.transcript.record_observation_page(settled)
            self.transcript.record_settled(
                settled,
                (0,) * 13,
                (0,) * 16,
            )
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
        if self._committed_count >= MAX_TRACE_ACTIONS:
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)
        self.transcript.reserve_exchange()
        self.transcript.record_command(asdict(command))
        command_id = self._next_command_id
        self._next_command_id += 1
        self.transcript.record_acknowledgement(command_id, 2, 1, 0)
        self.transcript.record_completion(command_id, 2, 0)
        settled = replace(observation, state=3)
        self.transcript.record_observation_page(settled)
        self.transcript.record_settled(
            settled,
            (0,) * 13,
            (0,) * 16,
        )
        self._committed_count += 1
        self._observation = None
        self._all_actions = ()
        return record

    def trace_digest(self) -> str:
        return self.transcript.digest()


_AVAILABILITY_BY_VALUE = {
    0: Availability.AVAILABLE,
    1: Availability.NOT_APPLICABLE,
    2: Availability.NOT_VISIBLE,
    3: Availability.UNSUPPORTED_RULE,
    4: Availability.OUT_OF_RANGE,
    5: Availability.UNINITIALIZED,
    6: Availability.UNAVAILABLE,
    7: Availability.EMPTY,
}
_PAGE_KIND_BY_VALUE = {
    0: PageKind.CONTROL,
    1: PageKind.SUMMARY,
    2: PageKind.MAP,
    3: PageKind.UNITS,
    4: PageKind.ACTIONS,
    5: PageKind.INVENTORY,
    6: PageKind.RESOURCES,
    7: PageKind.FLAGS,
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
_PAGE_RECORD_CAPACITIES = {
    PageKind.CONTROL: 0,
    PageKind.SUMMARY: SEMANTIC_FIELD_COUNT,
    PageKind.MAP: 224,
    PageKind.UNITS: 56,
    PageKind.INVENTORY: 112,
    PageKind.RESOURCES: 112,
    PageKind.FLAGS: 112,
    PageKind.ACTIONS: ACTIONS_PER_PAGE,
}
_PAGE_TOTAL_LIMITS = {
    PageKind.CONTROL: 0,
    PageKind.SUMMARY: SEMANTIC_FIELD_COUNT,
    PageKind.MAP: MAX_MAP_CELLS,
    PageKind.UNITS: MAX_UNITS,
    PageKind.INVENTORY: MAX_UNITS * UNIT_ITEM_COUNT,
    PageKind.RESOURCES: 1 + CONVOY_ITEM_COUNT + AUTOPLAY_TELEMETRY_WORDS,
    PageKind.FLAGS: 2 * 256 * 8,
    PageKind.ACTIONS: MAX_ACTIONS,
}


def _decode_optional_item_slot(value: int) -> int | None:
    if value == 0xFF:
        return None
    if 0 <= value < UNIT_ITEM_COUNT:
        return value
    raise PlannerError("invalid optional item-slot sentinel")


def _decode_item(raw_item: int) -> tuple[int, int]:
    if not 0 <= raw_item <= 0xFFFF:
        raise PlannerError("item state exceeds fixed u16 representation")
    return raw_item & 0xFF, (raw_item >> 8) & 0xFF


def parse_transport_observation(words: Iterable[int]) -> Observation:
    values = tuple(words)
    if len(values) != _OBSERVATION_WORD_COUNT:
        raise PlannerError("malformed fixed-width observation")
    if any(type(value) is not int or not 0 <= value <= 0xFFFFFFFF
           for value in values):
        raise PlannerError("observation word is outside fixed u32 range")
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
    if (
        not 1 <= values[7] <= MAX_PAGE_COUNT
        or values[6] >= values[7]
    ):
        raise PlannerError("planner page identity is outside v2 bounds")
    try:
        page_kind = _PAGE_KIND_BY_VALUE[values[8]]
    except KeyError as error:
        raise PlannerError("unknown planner page kind") from error
    record_start = values[9]
    record_count = values[10]
    total_records = values[11]
    total_actions = values[12]
    if (
        record_count > _PAGE_RECORD_CAPACITIES[page_kind]
        or total_records > _PAGE_TOTAL_LIMITS[page_kind]
        or record_start + record_count > total_records
        or total_actions > MAX_ACTIONS
        or page_kind is not PageKind.CONTROL and record_count == 0
    ):
        raise PlannerError("planner page bounds are inconsistent")

    payload = values[_OBSERVATION_HEADER_WORDS:]
    fields: list[Field] = []
    map_cells: list[MapCell] = []
    units: list[UnitRecord] = []
    inventory_records: list[InventoryRecord] = []
    resource_records: list[ResourceRecord] = []
    flag_records: list[FlagRecord] = []
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
    elif page_kind is PageKind.ACTIONS:
        if record_count * 10 > len(payload):
            raise PlannerError("action page exceeds fixed payload")
        for index in range(record_count):
            (
                kind,
                actor,
                destination,
                target,
                item_slot,
                token0,
                token1,
                token2,
                token3,
                action_id,
            ) = payload[index * 10 : index * 10 + 10]
            if kind not in _ACTION_KIND_BY_VALUE:
                raise PlannerError("unknown planner action kind")
            if item_slot >> 16:
                raise PlannerError("action item-slot reserved bits are nonzero")
            actions.append(
                ActionRecord(
                    record_start + index,
                    Action(
                        _ACTION_KIND_BY_VALUE[kind],
                        actor,
                        (destination & 0xFFFF, destination >> 16),
                        target & 0xFF,
                        _decode_optional_item_slot(item_slot & 0xFF),
                        ((target >> 8) & 0xFF, (target >> 16) & 0xFF),
                        action_id,
                        _decode_optional_item_slot((item_slot >> 8) & 0xFF),
                    ),
                    OpaqueToken(token0, token1, token2, token3),
                )
            )
    elif page_kind is PageKind.INVENTORY:
        if record_count * 2 > len(payload):
            raise PlannerError("inventory page exceeds fixed payload")
        for index in range(record_count):
            identity, raw_item = payload[index * 2 : index * 2 + 2]
            if (identity & 0xFF) != ValueKind.UNIT_ITEM.value:
                raise PlannerError("invalid inventory value kind")
            availability_value = identity >> 24
            try:
                availability = _AVAILABILITY_BY_VALUE[availability_value]
            except KeyError as error:
                raise PlannerError("unknown inventory availability") from error
            value_index = (identity >> 8) & 0xFFFF
            unit = value_index & 0xFF
            slot = value_index >> 8
            if slot >= UNIT_ITEM_COUNT:
                raise PlannerError(
                    "inventory slot exceeds fixed unit capacity: "
                    f"identity={identity:#010x}, slot={slot}"
                )
            item_id, uses = _decode_item(raw_item)
            inventory_records.append(
                InventoryRecord(
                    unit,
                    slot,
                    item_id,
                    uses,
                    raw_item,
                    availability,
                )
            )
    elif page_kind is PageKind.RESOURCES:
        if record_count * 2 > len(payload):
            raise PlannerError("resource page exceeds fixed payload")
        for index in range(record_count):
            identity, value = payload[index * 2 : index * 2 + 2]
            availability_value = identity >> 24
            try:
                availability = _AVAILABILITY_BY_VALUE[availability_value]
            except KeyError as error:
                raise PlannerError("unknown resource availability") from error
            try:
                kind = ValueKind(identity & 0xFF)
            except ValueError as error:
                raise PlannerError("unknown resource value kind") from error
            value_index = (identity >> 8) & 0xFFFF
            if kind is ValueKind.GOLD:
                if value_index != 0:
                    raise PlannerError("gold resource index must be zero")
                resource_records.append(
                    ResourceRecord(
                        kind,
                        None,
                        value,
                        None,
                        None,
                        availability,
                    )
                )
            elif kind is ValueKind.CONVOY_ITEM:
                if value_index >= CONVOY_ITEM_COUNT:
                    raise PlannerError("convoy slot exceeds fixed capacity")
                item_id, uses = _decode_item(value)
                resource_records.append(
                    ResourceRecord(
                        kind,
                        value_index,
                        value,
                        item_id,
                        uses,
                        availability,
                    )
                )
            elif kind is ValueKind.AUTOPLAY_TELEMETRY:
                resource_records.append(
                    ResourceRecord(
                        kind,
                        value_index,
                        value,
                        None,
                        None,
                        availability,
                    )
                )
            else:
                raise PlannerError("invalid resource value kind")
    elif page_kind is PageKind.FLAGS:
        if record_count * 2 > len(payload):
            raise PlannerError("flag page exceeds fixed payload")
        for index in range(record_count):
            identity, value = payload[index * 2 : index * 2 + 2]
            availability_value = identity >> 24
            try:
                availability = _AVAILABILITY_BY_VALUE[availability_value]
            except KeyError as error:
                raise PlannerError("unknown flag availability") from error
            try:
                kind = ValueKind(identity & 0xFF)
            except ValueError as error:
                raise PlannerError("unknown flag value kind") from error
            if kind not in {
                ValueKind.PERMANENT_FLAG,
                ValueKind.CHAPTER_FLAG,
            }:
                raise PlannerError("invalid flag value kind")
            if availability is Availability.AVAILABLE and value not in {0, 1}:
                raise PlannerError("flag state must be zero or one")
            flag_records.append(
                FlagRecord(
                    kind,
                    (identity >> 8) & 0xFFFF,
                    value
                    if availability is Availability.AVAILABLE
                    else None,
                    availability,
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
        inventory=tuple(inventory_records),
        resources=tuple(resource_records),
        flags=tuple(flag_records),
        state=values[5],
        rejection=values[13],
        chapter_turn=values[15],
        rng_state=(values[16], values[17], values[18]),
        rng_lcg=values[19],
        rng_consumption=values[20],
        actual_rom_identity=values[21],
        actual_config_identity=values[22],
        actual_scenario_identity=values[23],
        actual_seed_identity=values[24],
        record_start=record_start,
        record_count=record_count,
        total_record_count=total_records,
    )


def collect_observation_pages(transport: object, first: Observation) -> Observation:
    if (
        not 1 <= first.page_count <= MAX_PAGE_COUNT
        or first.page_index != 0
        or first.page_count * PAGE_MAX_BYTES > MAX_SEARCH_BYTES
    ):
        raise PlannerError("planner page traversal exceeds host bounds")
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
    page_ranks = {
        PageKind.SUMMARY: 0,
        PageKind.MAP: 1,
        PageKind.UNITS: 2,
        PageKind.INVENTORY: 3,
        PageKind.RESOURCES: 4,
        PageKind.FLAGS: 5,
        PageKind.ACTIONS: 6,
    }
    try:
        ranks = [page_ranks[page.page_kind] for page in pages]
    except KeyError as error:
        raise PlannerError("control page cannot enter PAGE traversal") from error
    if (
        ranks[0] != 0
        or ranks != sorted(ranks)
        or ranks.count(0) != 1
        or first.actual_rom_identity != 0
            and set(ranks) != set(page_ranks.values())
    ):
        raise PlannerError("planner typed-page sequence is not canonical")
    for page_kind in page_ranks:
        kind_pages = tuple(
            page for page in pages if page.page_kind is page_kind
        )
        if not kind_pages:
            continue
        total = kind_pages[0].total_record_count
        expected_start = 0
        for page in kind_pages:
            if (
                page.total_record_count != total
                or page.record_start != expected_start
                or page.record_count
                    > _PAGE_RECORD_CAPACITIES[page_kind]
            ):
                raise PlannerError(
                    "planner typed-page record span is not canonical"
                )
            expected_start += page.record_count
        if expected_start != total:
            raise PlannerError(
                "planner typed-page sequence is incomplete"
            )
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
    inventory = tuple(
        record
        for page in pages
        if page.page_kind is PageKind.INVENTORY
        for record in page.inventory
    )
    resources = tuple(
        record
        for page in pages
        if page.page_kind is PageKind.RESOURCES
        for record in page.resources
    )
    flags = tuple(
        record
        for page in pages
        if page.page_kind is PageKind.FLAGS
        for record in page.flags
    )
    units = tuple(unit for page in pages for unit in page.units)
    if inventory or resources or flags:
        expected_inventory = tuple(
            (unit.slot, slot)
            for unit in units
            for slot in range(UNIT_ITEM_COUNT)
        )
        if (
            tuple((record.unit, record.slot) for record in inventory)
            != expected_inventory
        ):
            raise PlannerError("inventory PAGE traversal is not canonical")
        if len(resources) != (
            1 + CONVOY_ITEM_COUNT + AUTOPLAY_TELEMETRY_WORDS
        ):
            raise PlannerError("resource PAGE traversal is incomplete")
        if resources[0].kind is not ValueKind.GOLD:
            raise PlannerError("resource PAGE traversal omitted canonical gold")
        if tuple(
            record.slot
            for record in resources[1 : 1 + CONVOY_ITEM_COUNT]
        ) != tuple(range(CONVOY_ITEM_COUNT)):
            raise PlannerError("convoy PAGE traversal is not canonical")
        if tuple(
            record.slot
            for record in resources[1 + CONVOY_ITEM_COUNT :]
        ) != tuple(range(AUTOPLAY_TELEMETRY_WORDS)):
            raise PlannerError("telemetry PAGE traversal is not canonical")
        for kind in (ValueKind.PERMANENT_FLAG, ValueKind.CHAPTER_FLAG):
            kind_flags = tuple(record for record in flags if record.kind is kind)
            if tuple(record.flag_id for record in kind_flags) != tuple(
                range(len(kind_flags))
            ):
                raise PlannerError("flag PAGE traversal is not canonical")
    complete = Observation(
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
        units=units,
        inventory=inventory,
        resources=resources,
        flags=flags,
        state=first.state,
        rejection=first.rejection,
        chapter_turn=first.chapter_turn,
        rng_state=first.rng_state,
        rng_lcg=first.rng_lcg,
        rng_consumption=first.rng_consumption,
        actual_rom_identity=first.actual_rom_identity,
        actual_config_identity=first.actual_config_identity,
        actual_scenario_identity=first.actual_scenario_identity,
        actual_seed_identity=first.actual_seed_identity,
        record_start=first.record_start,
        record_count=first.record_count,
        total_record_count=first.total_record_count,
    )
    record_complete = getattr(
        transport,
        "record_complete_observation",
        None,
    )
    if callable(record_complete):
        record_complete(complete)
    return complete


def replay_transcript_on_clean_transport(
    data: bytes,
    transport_factory: Callable[[], object],
) -> bytes:
    expected = PlannerTranscript.import_bytes(data)
    transport = transport_factory()
    pages: dict[tuple[int, int], dict[int, Observation]] = {}

    class CapturedPageTransport:
        def __init__(self, captured: dict[int, Observation]) -> None:
            self.captured = captured

        def exchange(self, command: Command) -> Observation:
            return self.captured[command.page_index]

    try:
        for event in expected.events:
            event_kind = event["event"]
            response = None
            if event_kind == "command":
                command = event["command"]
                kind = command["kind"]
                if kind == CommandKind.START.value:
                    response = transport.start(
                        scenario_identity=command[
                            "expected_identities"
                        ][2],
                    )
                elif kind == CommandKind.PAGE.value:
                    response = transport.exchange(
                        Command(
                            CommandKind.PAGE,
                            command["run_id"],
                            command["observation_id"],
                            page_index=command["page_index"],
                        )
                    )
                elif kind == CommandKind.COMMIT.value:
                    response = transport.exchange(
                        Command(
                            CommandKind.COMMIT,
                            command["run_id"],
                            command["observation_id"],
                            command["action_ordinal"],
                            OpaqueToken(**command["token"]),
                        )
                    )
                elif kind == CommandKind.CANCEL.value:
                    response = transport.exchange(
                        Command(
                            CommandKind.CANCEL,
                            command["run_id"],
                            command["observation_id"],
                        )
                    )
                else:
                    raise PlannerError(
                        "transcript contains an unsupported command"
                    )
                if isinstance(response, Observation):
                    key = (response.run_id, response.observation_id)
                    pages.setdefault(key, {})[response.page_index] = response
            elif event_kind == "observation_complete":
                identity = event["page_identity"]
                key = (identity[0], identity[1])
                captured = pages.pop(key, {})
                if set(captured) != set(range(identity[2])):
                    raise PlannerError(
                        "clean replay did not capture every observation page"
                    )
                complete = collect_observation_pages(
                    CapturedPageTransport(captured),
                    captured[0],
                )
                transport.record_complete_observation(complete)
        actual = transport.transcript.export()
        if actual != data:
            raise PlannerError("clean transport transcript replay mismatch")
        return actual
    finally:
        close = getattr(transport, "close", None)
        if callable(close):
            close()


def _consume_semantic_observation(observation: Observation) -> str:
    if (
        len(observation.map_cells) > MAX_MAP_CELLS
        or len(observation.units) > MAX_UNITS
        or len(observation.inventory) > MAX_UNITS * UNIT_ITEM_COUNT
        or len(observation.resources)
            > 1 + CONVOY_ITEM_COUNT + AUTOPLAY_TELEMETRY_WORDS
        or len(observation.flags) > 2 * 256 * 8
        or len(observation.actions) > MAX_ACTIONS
    ):
        raise PlannerError(Rejection.RESOURCE_LIMIT.value)
    unavailable = {
        unit.slot
        for unit in observation.units
        if unit.availability is not Availability.AVAILABLE
    }
    if any(
        record.action.actor in unavailable
        or (
            record.action.target not in {None, 0}
            and record.action.target in unavailable
        )
        for record in observation.actions
    ):
        raise PlannerError("candidate references an unavailable unit")
    if observation.inventory or observation.resources or observation.flags:
        if len(observation.inventory) != len(observation.units) * UNIT_ITEM_COUNT:
            raise PlannerError("typed inventory semantics are incomplete")
        if len(observation.resources) != (
            1 + CONVOY_ITEM_COUNT + AUTOPLAY_TELEMETRY_WORDS
        ):
            raise PlannerError("typed resource semantics are incomplete")
        if any(
            record.availability is Availability.EMPTY
                and record.raw_item != 0
            or record.availability is Availability.AVAILABLE
                and record.raw_item == 0
            for record in observation.inventory
        ):
            raise PlannerError("typed inventory availability is inconsistent")
        if any(
            record.kind is ValueKind.CONVOY_ITEM
                and (
                    record.availability is Availability.EMPTY
                        and record.value != 0
                    or record.availability is Availability.AVAILABLE
                        and record.value == 0
                )
            for record in observation.resources
        ):
            raise PlannerError("typed convoy availability is inconsistent")
    return _digest(_observation_semantics(observation))


class ScriptedPlanner:
    def __init__(self) -> None:
        self.last_semantic_digest: str | None = None

    def choose(self, observation: Observation) -> ActionRecord:
        self.last_semantic_digest = _consume_semantic_observation(observation)
        for record in observation.actions:
            if record.action.kind in {"MOVE_WAIT", "COMBAT"}:
                return record
        raise PlannerError(Rejection.CAPABILITY_UNAVAILABLE.value)


class BoundedSearchPlanner:
    def __init__(self, max_nodes: int = 32) -> None:
        self.max_nodes = max_nodes
        self.last_semantic_digest: str | None = None

    def choose(self, observation: Observation) -> ActionRecord:
        self.last_semantic_digest = _consume_semantic_observation(observation)
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
