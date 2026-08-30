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
SCENARIO_NAMESPACE = 0x00009201
MAX_MAP_CELLS = 64 * 64
MAX_UNITS = 62 + 20 + 50
MAX_ACTIONS = 512
MAX_PAGE_COUNT = 92
MAX_TRACE_ACTIONS = 4096
MAX_TRACE_BYTES = 2 * 1024 * 1024
MAX_TRANSCRIPT_EXCHANGE_BYTES = 64 * 1024
MAX_SEARCH_BYTES = 64 * 1024 * 1024
MAX_JSON_DEPTH = 64
COMMAND_RESPONSE_FRAME_LIMIT = 600
COMMIT_COMPLETION_FRAME_LIMIT = 18000
PAGE_MAX_BYTES = 1024
OBSERVATION_HEADER_BYTES = 100
OBSERVATION_PAYLOAD_BYTES = 924
OBSERVATION_DIGEST_WORD = 255
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


class AssignmentSource(int, Enum):
    NONE = 0
    CHAPTER = 1
    GROUP = 2
    UNIT = 3


class ValidationMode(str, Enum):
    SYNTHETIC = "SYNTHETIC"
    PRODUCTION = "PRODUCTION"


class PlannerError(ValueError):
    """A protocol violation that must never be converted into success."""


class PlannerTransportFailure(RuntimeError):
    """A typed restricted-transport failure recorded in the transcript."""


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
_ACTION_IDS_BY_KIND = {
    "MOVE_WAIT": frozenset({0}),
    "COMBAT": frozenset({1}),
    "STAFF": frozenset({5}),
    "USE_ITEM": frozenset({6}),
    "PICK": frozenset({13}),
    "SUMMON": frozenset({12, 14}),
}
_DEFAULT_ACTION_ID = {kind: max(action_ids) for kind, action_ids in _ACTION_IDS_BY_KIND.items()}
_TILE_TARGET_ITEM_IDS = frozenset({0x7A, 0x7B})
_DANCE_RING_ITEM_IDS = frozenset({0x7D, 0x7E, 0x7F, 0x80})
_OBSTACLE_TERRAIN_IDS = frozenset({0x1B, 0x33})
_WEAPON_IDS = frozenset((*range(1, 0x4B), 0x5A, 0x78, *range(0x81, 0x88), 0x8B, *range(0x8D, 0x97), 0xA1, *range(0xA7, 0xB7), *range(0xBC, 0xC1), *range(0xC2, 0xCC)))
_STAFF_IDS = frozenset((*range(0x4B, 0x5A), 0x8C, 0xA6))
_SELF_USE_IDS = frozenset((*range(0x5B, 0x69), *range(0x6C, 0x71), 0x88, 0x89, 0x8A, 0x97, 0x98, 0x99, 0xA2, 0xB7, 0xC1))
_PICK_IDS_BY_TERRAIN = {0x14: {0x6B}, 0x1E: {0x6A, 0x6B}, 0x21: {0x69, 0x6B, 0x79}}
_HAMMERNE_IDS = (_WEAPON_IDS | _STAFF_IDS) - {0x57, 0x8B, 0x8F, 0x90, *range(0xA7, 0xAC), *range(0xAD, 0xB5)}
_MAX_USES = bytes.fromhex("002e1e1e1423190f28282d1e1414121e190f0f122d1e1e14281e1410140f0f2d1e14281e14121414140f0f14322d1e1428141e161405050528231e0514141e231e190514142d1e1405141e1e140f0f080a03030305030a030a0f1e010101010101010101010101010101010f0303030305000101010101012d050101000f0f0f0f3c3c3c3c1e141e01010100031e1e00001e1e1e1e101201010101010101010101283c3c3c0001000000320005000000000000000005140101010101000000000000000000000000000000000100")
_VALID_WIRE_REJECTION_CODES = frozenset(range(1, 11))
_WIRE_STALE_OBSERVATION = 2
_REJECTIONS_BY_COMMAND = {
    CommandKind.START.value: {1, 9, 10},
    CommandKind.PAGE.value: {2, 3, 6, 9, 10},
    CommandKind.COMMIT.value: {2, 3, 4, 6, 7, 9, 10},
    CommandKind.CANCEL.value: {2, 8, 9, 10},
}


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
        if self.kind in _DEFAULT_ACTION_ID and self.action_id is None:
            object.__setattr__(self, "action_id", _DEFAULT_ACTION_ID[self.kind])
        if self.target is None:
            object.__setattr__(self, "target", 0)
        if self.target_position is None:
            object.__setattr__(self, "target_position", (0, 0))
        _validate_action_contract(asdict(self), "action")


@dataclass(frozen=True)
class OpaqueToken:
    word0: int
    word1: int
    word2: int
    word3: int
    def __post_init__(self) -> None:
        _validate_opaque_token(self, "opaque token")
    @property
    def words(self) -> tuple[int, int, int, int]:
        return (self.word0, self.word1, self.word2, self.word3)


@dataclass(frozen=True)
class ActionRecord:
    ordinal: int
    action: Action
    token: OpaqueToken
    def __post_init__(self) -> None:
        _validate_opaque_token(self.token, "action token")


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
    status_index: int = 0
    status_duration: int = 0
    deployed: int = 1
    dead: int = 0
    moved: int = 0
    acted: int = 0
    rescued: int = 0
    rescuing: int = 0
    rescue_partner: int = 0
    equipped_slot: int | None = None
    equipped_item: int = 0
    level: int = 0
    exp: int = 0
    power: int = 0
    skill: int = 0
    speed: int = 0
    luck: int = 0
    defense: int = 0
    resistance: int = 0
    constitution: int = 0
    movement: int = 0
    weapon_ranks: tuple[int, ...] = (0, ) * 8


@dataclass(frozen=True)
class ObjectiveRecord:
    objective_id: int
    completion_objective_id: int
    group_id: int
    activation_flag: int
    deactivation_flag: int
    event_flag: int
    completion_flag: int
    until_turn: int
    kind: int
    protected_character: int
    area: tuple[int, int, int, int]
    state: int
    progress: int
    availability: Availability


@dataclass(frozen=True)
class GroupRecord:
    group_id: int
    members: tuple[int, ...]
    availability: Availability


@dataclass(frozen=True)
class StrategyRecord:
    strategy_id: int
    objective_capabilities: int
    action_capabilities: int
    flags: int
    availability: Availability


@dataclass(frozen=True)
class AssignmentRecord:
    source: AssignmentSource
    subject_id: int
    strategy_id: int
    activation_flag: int
    active: int
    current: int
    availability: Availability


@dataclass(frozen=True)
class CampaignRecord:
    phase: int
    chapter: int
    mode: int
    phase_availability: Availability
    objective_availability: Availability
    strategy_availability: Availability
    assignment_availability: Availability
    current_strategy_id: int
    current_objective_capabilities: int
    current_action_capabilities: int
    current_strategy_flags: int
    current_assignment_source: AssignmentSource
    current_assignment_subject: int
    current_assignment_availability: Availability
    objectives: tuple[ObjectiveRecord, ...]
    groups: tuple[GroupRecord, ...]
    strategies: tuple[StrategyRecord, ...]
    assignments: tuple[AssignmentRecord, ...]


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
    campaign: CampaignRecord | None = None


@dataclass(frozen=True)
class Command:
    kind: CommandKind
    run_id: int
    observation_id: int
    action_ordinal: int | None = None
    token: OpaqueToken | None = None
    page_index: int | None = None
    def __post_init__(self) -> None:
        if self.token is not None:
            _validate_opaque_token(self.token, "command token")


def _command_payload(command: Command) -> dict[str, object]:
    payload: dict[str, object] = {
        "kind": command.kind.value,
        "run_id": command.run_id,
        "observation_id": command.observation_id,
    }
    if command.kind is CommandKind.PAGE:
        payload["page_index"] = command.page_index
    elif command.kind is CommandKind.COMMIT:
        _validate_opaque_token(command.token, "command token")
        payload["action_ordinal"] = command.action_ordinal
        payload["token"] = (asdict(command.token) if command.token is not None else None)
    return payload


def _cleared_command_words(
    command: dict[str, object],
    acknowledgement: dict[str, object],
) -> list[int]:
    words = [0] * 16
    words[:6] = [
        0x41504C4E, PROTOCOL_VERSION, 64, 0,
        command["run_id"], command["observation_id"],
    ]
    kind = command["kind"]
    if kind == CommandKind.PAGE.value:
        words[6] = command["page_index"]
    elif kind == CommandKind.COMMIT.value:
        words[7] = command["action_ordinal"]
        words[8:12] = [
            command["token"][f"word{index}"] for index in range(4)]
    elif kind == CommandKind.START.value:
        words[8:12] = command["expected_identities"]
    words[14:16] = [
        acknowledgement["result"], acknowledgement["rejection"]]
    return words


def _validate_checkpoint_binding(
    checkpoint: list[int],
    session: dict[str, object],
    previous_checkpoint: list[int] | None,
    command: dict[str, object] | None,
    acknowledgement: dict[str, object] | None,
    previous_observation: dict[str, object] | None,
    current_observation: dict[str, object],
    terminal: dict[str, object],
    initial_settlement: bool,
) -> None:
    accepted_kind = (
        command["kind"] if command is not None
        and acknowledgement is not None
        and acknowledgement["result"] == 1
        and acknowledgement["rejection"] == 0 else None)
    accepted_transition = (
        accepted_kind == CommandKind.COMMIT.value
        and previous_observation is not None
        and current_observation["chapter"] != previous_observation["chapter"])
    if terminal["state"] in {4, 5}:
        if any(checkpoint):
            raise PlannerError("terminal response retained checkpoint")
        return
    if not any(checkpoint):
        if (accepted_transition
                or (previous_checkpoint is not None
                    and any(previous_checkpoint)
                    and accepted_kind != CommandKind.START.value)):
            raise PlannerError("nonterminal response cleared checkpoint")
        return
    if checkpoint[3] == 0 or checkpoint[3] not in {
            session["ready_run_id"], session["run_id"]} or checkpoint[12] == 0:
        raise PlannerError("checkpoint run or semantic identity mismatch")
    if previous_checkpoint is not None and any(previous_checkpoint):
        checkpoint_unchanged = checkpoint == previous_checkpoint
        if accepted_transition == checkpoint_unchanged:
            raise PlannerError("checkpoint changed after publication")
        if checkpoint_unchanged:
            return
    if command is None:
        if (not initial_settlement
                or checkpoint[3] != session["ready_run_id"]):
            raise PlannerError("checkpoint appeared without accepted COMMIT")
        return
    if not accepted_transition:
        raise PlannerError("checkpoint appeared without accepted COMMIT")
    campaign = current_observation.get("campaign")
    previous_rng, current_rng = (
        [*observation["rng_state"], observation["rng_lcg"], observation["rng_consumption"]]
        for observation in (previous_observation, current_observation)
    )
    expected_rng = (current_rng if checkpoint[11] == current_rng[4]
                    else previous_rng if checkpoint[11] == previous_rng[4] else None)
    if (checkpoint[3] != command["run_id"]
            or checkpoint[4] != previous_observation["chapter"]
            or checkpoint[6] != previous_observation["chapter_turn"]
            or not previous_rng[4] <= checkpoint[11] <= current_rng[4]
            or (expected_rng is not None and checkpoint[7:12] != expected_rng)
            or (campaign is not None and checkpoint[5] != campaign["mode"])):
        raise PlannerError("checkpoint does not bind settled campaign state")


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
        values = (current.values() if isinstance(current, dict) else current)
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
                raise PlannerError("invalid planner transcript JSON depth")
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
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise PlannerError("invalid planner transcript JSON value") from error
    except RecursionError as error:
        raise PlannerError("invalid planner transcript JSON recursion") from error
    return encoded.encode("ascii")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"nonfinite JSON constant {value}")


_OBSERVATION_KEYS = {
    "run_id",
    "observation_id",
    "chapter",
    "fields",
    "actions",
    "page_index",
    "page_count",
    "page_kind",
    "total_action_count",
    "map_cells",
    "units",
    "inventory",
    "resources",
    "flags",
    "state",
    "rejection",
    "chapter_turn",
    "rng_state",
    "rng_lcg",
    "rng_consumption",
    "actual_rom_identity",
    "actual_config_identity",
    "actual_scenario_identity",
    "actual_seed_identity",
    "record_start",
    "record_count",
    "total_record_count",
    "campaign",
}
_TRANSCRIPT_EVENT_KEYS = {
    "session": {"provenance"},
    "observation_page": {"observation"},
    "observation_complete": {
        "observation",
        "candidate_set_digest",
        "semantic_digest",
        "page_identity",
    },
    "command": {"command"},
    "acknowledgement": {"command_id", "kind", "result", "rejection"},
    "completion": {"command_id", "kind", "response_frames"},
    "settled": {
        "observation_identity",
        "observation_digest",
        "checkpoint",
        "command_words",
        "telemetry",
        "rng",
        "terminal",
    },
    "transport_error": {"code", "command_id", "kind"},
}
_TRANSCRIPT_COMMON_EVENT_KEYS = {"sequence", "previous_digest", "event", "event_digest"}
_U32_MAX = 0xFFFFFFFF
_AVAILABILITY_VALUES = frozenset(value.value for value in Availability)
_PAGE_KIND_VALUES = frozenset(value.value for value in PageKind)
_ACTION_KIND_VALUES = frozenset({
    "MOVE_WAIT",
    "COMBAT",
    "STAFF",
    "USE_ITEM",
    "PICK",
    "SUMMON",
})
_ACTION_ID_VALUES = frozenset().union(*_ACTION_IDS_BY_KIND.values())
_TRANSPORT_ERROR_CODES = frozenset({
    "ACTION_COMPLETION_TIMEOUT",
    "COMMAND_ACK_TIMEOUT",
    "COMMAND_RESPONSE_TIMEOUT",
    "INVALID_COMMAND_ACK",
})


def _require_exact_keys(
    value: object,
    keys: set[str],
    context: str,
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise PlannerError(f"invalid planner transcript {context} schema")
    return value


def _require_int(
    value: object,
    context: str,
    minimum: int = 0,
    maximum: int = _U32_MAX,
    allowed: frozenset[int] | None = None,
    message: str | None = None,
) -> int:
    if (type(value) is not int or not minimum <= value <= maximum or allowed is not None and value not in allowed):
        raise PlannerError(message or f"invalid planner transcript {context} scalar")
    return value


def _require_text(
    value: object,
    context: str,
    allowed: frozenset[str] | None = None,
) -> str:
    if (type(value) is not str or not value or len(value) > 256 or allowed is not None and value not in allowed):
        raise PlannerError(f"invalid planner transcript {context} scalar")
    return value


def _require_digest(value: object, context: str) -> str:
    text = _require_text(value, context)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise PlannerError(f"invalid planner transcript {context} scalar")
    return text


def _require_list(
    value: object,
    context: str,
    *,
    length: int | None = None,
    maximum: int | None = None,
    message: str | None = None,
) -> list[object] | tuple[object, ...]:
    if (not isinstance(value, (list, tuple)) or length is not None and len(value) != length or maximum is not None and len(value) > maximum):
        raise PlannerError(message or f"invalid planner transcript {context} schema")
    return value


def _require_int_list(
    value: object,
    context: str,
    *,
    length: int | None = None,
    maximum: int | None = None,
    item_maximum: int = _U32_MAX,
    message: str | None = None,
) -> list[object]:
    values = _require_list(
        value,
        context,
        length=length,
        maximum=maximum,
        message=message,
    )
    for item in values:
        _require_int(
            item,
            context,
            maximum=item_maximum,
            message=message,
        )
    return values


def _require_optional_int(
    value: object,
    context: str,
    maximum: int,
    allowed: frozenset[int] | None = None,
) -> None:
    if value is not None:
        _require_int(
            value,
            context,
            maximum=maximum,
            allowed=allowed,
        )


def _require_coordinate(
    value: object,
    context: str,
    *,
    optional: bool = False,
) -> None:
    if optional and value is None:
        return
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise PlannerError(f"invalid planner transcript {context} schema")
    for coordinate in value:
        _require_int(coordinate, context, maximum=63)


def _require_availability(value: object, context: str) -> str:
    return _require_text(value, context, _AVAILABILITY_VALUES)


def _validate_opaque_token(token: object, context: str) -> None:
    if type(token) is not OpaqueToken:
        raise PlannerError(f"invalid planner {context} type")
    for word in token.words:
        _require_int(word, context)


def _observation_action_item_valid(
    action: dict[str, object],
    inventory: dict[tuple[int, int], dict[str, object]],
    units: dict[int, dict[str, object]],
    map_cells: dict[tuple[int, int], dict[str, object]],
) -> bool:
    kind = action["kind"]
    item_slot = action["item_slot"]
    target = action["target"]
    target_position = tuple(action["target_position"])
    cell = map_cells.get(target_position)
    distance = sum(abs(left - right) for left, right in zip(action["destination"], target_position))
    if item_slot is None:
        return (kind in {"MOVE_WAIT", "SUMMON"}
                or kind == "COMBAT"
                and _observation_action_target_valid(action, map_cells)
                or kind == "PICK" and units.get(action["actor"], {}).get("unit_class") == 0x33
                and cell is not None and cell["terrain"] in _PICK_IDS_BY_TERRAIN
                and distance == (0 if cell["terrain"] == 0x21 else 1))
    item = inventory.get((action["actor"], item_slot))
    if item is None or item["availability"] != Availability.AVAILABLE or item["raw_item"] == 0:
        return False
    item_id = item["item_id"]
    if kind == "COMBAT":
        return item_id in _WEAPON_IDS
    if kind == "STAFF":
        target_slot = action["target_item_slot"]
        if item_id not in _STAFF_IDS - {0xA6} or (item_id == 0x57) != (target_slot is not None):
            return False
        if item_id == 0x57:
            target_item = inventory.get((target, target_slot))
            return ((action["actor"] ^ target) & 0xC0) == 0 and target_item is not None and target_item["availability"] == Availability.AVAILABLE and target_item["raw_item"] != 0 and target_item["item_id"] in _HAMMERNE_IDS and target_item["uses"] != _MAX_USES[target_item["item_id"]]
        if item_id in {0x4F, 0x8C}:
            return target == 0 and target_position == (0, 0)
        if item_id in {0x56, 0x58}:
            return target == 0 and cell is not None and (item_id != 0x58 or cell["terrain"] == 0x1E)
        return target != 0 and (item_id == 0x54 or target_position == (0, 0))
    if kind == "PICK":
        return cell is not None and distance == (0 if cell["terrain"] == 0x21 else 1) and item["uses"] != 0 and item_id in _PICK_IDS_BY_TERRAIN.get(cell["terrain"], ())
    if kind != "USE_ITEM":
        return False
    if item_id in _TILE_TARGET_ITEM_IDS:
        return target == 0 and distance == 1 and cell is not None and cell["unit"] in {0, action["actor"]}
    if item_id in _DANCE_RING_ITEM_IDS:
        unit = units.get(target)
        return (unit is not None and target != action["actor"] and target_position == tuple(unit["position"]) and distance == 1)
    return item_id in _SELF_USE_IDS and target == 0 and target_position == (0, 0)


def _observation_action_target_valid(
    action: dict[str, object],
    map_cells: dict[tuple[int, int], dict[str, object]],
) -> bool:
    if action["kind"] != "COMBAT" or action["target"] != 0:
        return True
    cell = map_cells.get(tuple(action["target_position"]))
    return (cell is not None and cell["unit"] == 0
            and cell["terrain"] in _OBSTACLE_TERRAIN_IDS)


def _validate_token_schema(token: object, context: str) -> None:
    value = _require_exact_keys(token, {"word0", "word1", "word2", "word3"}, context)
    for word in value.values():
        _require_int(word, context)


def _validate_rejected_response(
    command: dict[str, object],
    acknowledgement: dict[str, object],
    previous: dict[str, object] | None,
    response: dict[str, object],
) -> bool:
    rejection = acknowledgement["rejection"]
    kind = command["kind"]
    if (previous is None or rejection not in _REJECTIONS_BY_COMMAND[kind]
            or any(response[field] != previous[field] for field in _OBSERVATION_KEYS - {"state", "rejection"}) or response["rejection"] != rejection):
        raise PlannerError("rejected response changed immutable observation")
    prior_state = previous["state"]
    terminal_state = None
    if rejection == 10 and prior_state == 2:
        terminal_state = 4
    elif kind == CommandKind.CANCEL.value and rejection == 8 and prior_state == 2:
        terminal_state = 4
    elif kind == CommandKind.COMMIT.value and rejection == 7 and prior_state == 2:
        terminal_state = 5
    expected_state = prior_state if terminal_state is None else terminal_state
    if response["state"] != expected_state or (rejection in {7, 8, 10} and terminal_state is None):
        raise PlannerError("rejected response has invalid state transition")
    return terminal_state is not None


def _accepted_terminal(response: dict[str, object]) -> bool:
    return response["rejection"] in {
        4: {8, 10},
        5: {5, 7},
    }.get(response["state"], set())


def _validate_accepted_response(
        command: dict[str, object],
        previous: dict[str, object] | None,
        response: dict[str, object],
        session: dict[str, object],
        production: bool,
) -> bool:
    kind = command["kind"]
    if kind == CommandKind.PAGE.value:
        if (previous is None or response["state"] != previous["state"]
                or response["rejection"] != 0
                or response["run_id"] != command["run_id"]
                or response["observation_id"] != command["observation_id"]
                or response["page_index"] != command["page_index"]):
            raise PlannerError("PAGE response identity mismatch")
        return False
    if not production:
        return False
    if kind not in {CommandKind.START.value, CommandKind.COMMIT.value} or previous is None:
        return False
    terminal = _accepted_terminal(response)
    if production and any(response[field] == 0 for field in (
            "actual_rom_identity", "actual_config_identity",
            "actual_scenario_identity", "actual_seed_identity")):
        raise PlannerError("accepted response provenance is unavailable")
    if kind == CommandKind.START.value:
        expected = command["expected_identities"]
        if (previous["state"] != 1
                or previous["run_id"] != session["ready_run_id"]
                or response["run_id"] != session["run_id"]
                or [response[f"actual_{name}_identity"]
                    for name in ("rom", "config", "scenario", "seed")] != expected
                or not terminal and (
                    response["state"] != 2 or response["rejection"] != 0
                    or response["observation_id"] <= previous["observation_id"]
                    or response["page_index"] != 0
                    or response["page_kind"] != PageKind.SUMMARY.value)
                or terminal and (
                    response["page_index"] != 0 or response["page_count"] != 1
                    or response["page_kind"] != PageKind.CONTROL.value
                    or response["record_count"] != 0
                    or response["total_record_count"] != 0
                    or response["total_action_count"] != 0)):
            raise PlannerError("accepted START response transition mismatch")
        return terminal
    if (previous["state"] != 2 or response["run_id"] != previous["run_id"]
            or response["actual_rom_identity"] != previous["actual_rom_identity"]
            or response["actual_config_identity"] != previous["actual_config_identity"]
            or (response["chapter"] == previous["chapter"]
                and response["actual_scenario_identity"]
                    != previous["actual_scenario_identity"])
            or not terminal and (
                response["state"] != 2 or response["rejection"] != 0
                or response["observation_id"] <= previous["observation_id"]
                or response["page_index"] != 0
                or response["page_kind"] != PageKind.SUMMARY.value)
            or terminal and response["state"] == previous["state"]):
        raise PlannerError("accepted COMMIT response transition mismatch")
    return terminal


def _is_roster_slot(value: int) -> bool:
    return (1 <= value <= 0x3E or 0x41 <= value <= 0x54 or 0x81 <= value <= 0xB2)


def _validate_action_contract(action: object, context: str) -> None:
    value = _require_exact_keys(
        action,
        {
            "kind",
            "actor",
            "destination",
            "target",
            "item_slot",
            "target_position",
            "action_id",
            "target_item_slot",
        },
        context,
    )
    kind = _require_text(value["kind"], f"{context} kind", _ACTION_KIND_VALUES)
    action_id = _require_int(value["action_id"], f"{context} action id", allowed=_ACTION_ID_VALUES)
    if action_id not in _ACTION_IDS_BY_KIND[kind]:
        raise PlannerError(f"invalid planner {context} kind/action mapping")
    actor = _require_int(value["actor"], f"{context} actor", maximum=0xFF)
    target = _require_int(value["target"], f"{context} target", maximum=0xFF)
    if (not _is_roster_slot(actor) or target != 0 and not _is_roster_slot(target)):
        raise PlannerError(f"invalid planner {context} unit identity")
    _require_coordinate(value["destination"], f"{context} destination")
    _require_coordinate(value["target_position"], f"{context} target position")
    item_slot = value["item_slot"]
    target_slot = value["target_item_slot"]
    target_position = tuple(value["target_position"])
    _require_optional_int(item_slot, f"{context} item slot", UNIT_ITEM_COUNT - 1)
    _require_optional_int(target_slot, f"{context} target item slot", UNIT_ITEM_COUNT - 1)
    if (kind in {"STAFF", "USE_ITEM"} and item_slot is None or kind in {"MOVE_WAIT", "SUMMON"} and item_slot is not None
            or target_slot is not None and kind != "STAFF" or target_slot is not None and (target == 0 or target_position != (0, 0))
            or kind in {"MOVE_WAIT", "PICK", "SUMMON"} and target != 0 or kind == "MOVE_WAIT" and target_position != (0, 0)
            or kind == "SUMMON" and action_id == 12 and target_position != (0, 0)
            or kind == "COMBAT" and target != 0 and target_position != (0, 0)):
        raise PlannerError(f"invalid planner {context} sentinel contract")


def _validate_action_schema(record: object) -> None:
    value = _require_exact_keys(record, {"ordinal", "action", "token"}, "action record")
    _require_int(value["ordinal"], "action ordinal", maximum=MAX_ACTIONS - 1)
    _validate_action_contract(value["action"], "action")
    _validate_token_schema(value["token"], "action token")


def _validate_field_schema(record: object) -> None:
    value = _require_exact_keys(record, {"name", "source", "bound", "availability", "value"}, "field")
    _require_text(value["name"], "field name")
    _require_text(value["source"], "field source")
    _require_int(value["bound"], "field bound")
    availability = _require_availability(value["availability"], "field availability")
    if availability == Availability.AVAILABLE.value:
        _require_int(value["value"], "field value")
    elif value["value"] is not None:
        raise PlannerError("invalid planner transcript field value scalar")


def _validate_map_cell_schema(record: object) -> None:
    value = _require_exact_keys(record, {"x", "y", "terrain", "unit", "availability"}, "map cell")
    _require_int(value["x"], "map x", maximum=63)
    _require_int(value["y"], "map y", maximum=63)
    _require_int(value["terrain"], "map terrain", maximum=0xFF)
    _require_int(value["unit"], "map unit", maximum=0xFF)
    _require_availability(value["availability"], "map availability")


def _validate_unit_schema(record: object) -> None:
    value = _require_exact_keys(
        record,
        {
            "slot",
            "character",
            "unit_class",
            "position",
            "hp",
            "state",
            "inventory_digest",
            "availability",
            "status_index",
            "status_duration",
            "deployed",
            "dead",
            "moved",
            "acted",
            "rescued",
            "rescuing",
            "rescue_partner",
            "equipped_slot",
            "equipped_item",
            "level",
            "exp",
            "power",
            "skill",
            "speed",
            "luck",
            "defense",
            "resistance",
            "constitution",
            "movement",
            "weapon_ranks",
        },
        "unit",
    )
    for field in ("slot", "character", "unit_class"):
        _require_int(value[field], f"unit {field}", maximum=0xFF)
    _require_int_list(value["position"], "unit position", length=2, item_maximum=0xFF)
    _require_int_list(value["hp"], "unit hp", length=2, item_maximum=0xFF)
    _require_int(value["state"], "unit state")
    _require_int(value["inventory_digest"], "unit inventory digest")
    availability = _require_availability(value["availability"], "unit availability")
    for field in (
            "status_index",
            "status_duration",
            "rescue_partner",
            "level",
            "exp",
            "power",
            "skill",
            "speed",
            "luck",
            "defense",
            "resistance",
            "constitution",
            "movement",
    ):
        _require_int(value[field], f"unit {field}", maximum=0xFF)
    for field in ("deployed", "dead", "moved", "acted", "rescued", "rescuing"):
        _require_int(value[field], f"unit {field}", maximum=1)
    _require_optional_int(value["equipped_slot"], "unit equipped slot", UNIT_ITEM_COUNT - 1)
    _require_int(value["equipped_item"], "unit equipped item", maximum=0xFFFF)
    _require_int_list(value["weapon_ranks"], "unit weapon ranks", length=8, item_maximum=0xFF)
    if value["equipped_slot"] is None and value["equipped_item"] != 0:
        raise PlannerError("invalid planner transcript equipped item sentinel")
    if availability in {
            Availability.NOT_VISIBLE.value,
            Availability.NOT_APPLICABLE.value,
    } and any((
            value["character"],
            value["unit_class"],
            *value["position"],
            *value["hp"],
            value["state"],
            value["inventory_digest"],
            value["status_index"],
            value["status_duration"],
            value["rescue_partner"],
            value["equipped_item"],
            value["level"],
            value["exp"],
            value["power"],
            value["skill"],
            value["speed"],
            value["luck"],
            value["defense"],
            value["resistance"],
            value["constitution"],
            value["movement"],
            *value["weapon_ranks"],
            value["deployed"],
            value["dead"],
            value["moved"],
            value["acted"],
            value["rescued"],
            value["rescuing"],
    )) or (availability in {
            Availability.NOT_VISIBLE.value,
            Availability.NOT_APPLICABLE.value,
    } and value["equipped_slot"] is not None):
        raise PlannerError("invalid planner transcript unavailable unit semantics")
    if availability not in {
            Availability.NOT_VISIBLE.value,
            Availability.NOT_APPLICABLE.value,
    }:
        state = value["state"]
        expected = (
            int(not state & ((1 << 2) | (1 << 3) | (1 << 16))),
            int(bool(state & (1 << 2))),
            int(bool(state & (1 << 6))),
            int(bool(state & ((1 << 6) | (1 << 10)))),
            int(bool(state & (1 << 5))),
            int(bool(state & (1 << 4))),
        )
        actual = tuple(value[field] for field in ("deployed", "dead", "moved", "acted", "rescued", "rescuing"))
        if actual != expected:
            raise PlannerError("invalid planner transcript unit semantic flags")


def _validate_inventory_schema(record: object) -> None:
    value = _require_exact_keys(
        record,
        {"unit", "slot", "item_id", "uses", "raw_item", "availability"},
        "inventory",
    )
    _require_int(value["unit"], "inventory unit", maximum=0xFF)
    _require_int(value["slot"], "inventory slot", maximum=UNIT_ITEM_COUNT - 1)
    _require_int(value["item_id"], "inventory item id", maximum=0xFF)
    _require_int(value["uses"], "inventory item uses", maximum=0xFF)
    raw_item = _require_int(value["raw_item"], "inventory raw item", maximum=0xFFFF)
    if _decode_item(raw_item) != (value["item_id"], value["uses"]):
        raise PlannerError("invalid planner transcript inventory item state")
    _require_availability(value["availability"], "inventory availability")


def _validate_resource_schema(record: object) -> None:
    value = _require_exact_keys(
        record,
        {"kind", "slot", "value", "item_id", "uses", "availability"},
        "resource",
    )
    kind = _require_int(
        value["kind"],
        "resource kind",
        allowed=frozenset({
            ValueKind.GOLD.value,
            ValueKind.CONVOY_ITEM.value,
            ValueKind.AUTOPLAY_TELEMETRY.value,
        }),
    )
    resource_value = _require_int(value["value"], "resource value")
    if kind == ValueKind.GOLD.value:
        if any(value[field] is not None for field in ("slot", "item_id", "uses")):
            raise PlannerError("invalid planner transcript gold sentinel")
    elif kind == ValueKind.CONVOY_ITEM.value:
        _require_int(value["slot"], "convoy slot", maximum=CONVOY_ITEM_COUNT - 1)
        _require_int(value["item_id"], "convoy item id", maximum=0xFF)
        _require_int(value["uses"], "convoy item uses", maximum=0xFF)
        if (resource_value > 0xFFFF or _decode_item(resource_value) != (value["item_id"], value["uses"])):
            raise PlannerError("invalid planner transcript convoy item state")
    else:
        _require_int(value["slot"], "telemetry slot", maximum=AUTOPLAY_TELEMETRY_WORDS - 1)
        if value["item_id"] is not None or value["uses"] is not None:
            raise PlannerError("invalid planner transcript telemetry sentinel")
    _require_availability(value["availability"], "resource availability")


def _validate_flag_schema(record: object) -> None:
    value = _require_exact_keys(
        record,
        {"kind", "flag_id", "state", "availability"},
        "flag",
    )
    _require_int(
        value["kind"],
        "flag kind",
        allowed=frozenset({
            ValueKind.PERMANENT_FLAG.value,
            ValueKind.CHAPTER_FLAG.value,
        }),
    )
    _require_int(value["flag_id"], "flag id", maximum=2047)
    availability = _require_availability(value["availability"], "flag availability")
    if availability == Availability.AVAILABLE.value:
        _require_int(value["state"], "flag state", maximum=1)
    elif value["state"] is not None:
        raise PlannerError("invalid planner transcript flag state scalar")


def _validate_objective_schema(record: object) -> None:
    value = _require_exact_keys(
        record, {
            "objective_id",
            "completion_objective_id",
            "group_id",
            "activation_flag",
            "deactivation_flag",
            "event_flag",
            "completion_flag",
            "until_turn",
            "kind",
            "protected_character",
            "area",
            "state",
            "progress",
            "availability",
        }, "objective")
    for field in ("objective_id", "completion_objective_id", "group_id"):
        _require_int(value[field], f"objective {field}")
    for field in (
            "activation_flag",
            "deactivation_flag",
            "event_flag",
            "completion_flag",
            "until_turn",
    ):
        _require_int(value[field], f"objective {field}", maximum=0xFFFF)
    _require_int(value["kind"], "objective kind", minimum=1, maximum=5)
    _require_int(value["protected_character"], "objective protected character", maximum=0xFF)
    _require_int_list(value["area"], "objective area", length=4, item_maximum=63)
    _require_int(value["state"], "objective state", maximum=3)
    _require_int(value["progress"], "objective progress", maximum=0xFFFF)
    _require_availability(value["availability"], "objective availability")


def _validate_group_schema(record: object) -> None:
    value = _require_exact_keys(record, {"group_id", "members", "availability"}, "objective group")
    _require_int(value["group_id"], "objective group id")
    members = _require_list(value["members"], "objective group members")
    if len(members) > 16:
        raise PlannerError("invalid planner transcript objective group capacity")
    for member in members:
        _require_int(member, "objective group member", maximum=0xFF)
    _require_availability(value["availability"], "objective group availability")


def _validate_strategy_schema(record: object) -> None:
    value = _require_exact_keys(record, {
        "strategy_id",
        "objective_capabilities",
        "action_capabilities",
        "flags",
        "availability",
    }, "strategy")
    for field in ("strategy_id", "objective_capabilities", "action_capabilities"):
        _require_int(value[field], f"strategy {field}")
    _require_int(value["flags"], "strategy flags", maximum=0xFF)
    _require_availability(value["availability"], "strategy availability")


def _validate_assignment_schema(record: object) -> None:
    value = _require_exact_keys(record, {
        "source",
        "subject_id",
        "strategy_id",
        "activation_flag",
        "active",
        "current",
        "availability",
    }, "strategy assignment")
    _require_int(value["source"], "assignment source", allowed=frozenset(range(1, 4)))
    _require_int(value["subject_id"], "assignment subject")
    _require_int(value["strategy_id"], "assignment strategy")
    _require_int(value["activation_flag"], "assignment activation flag", maximum=0xFFFF)
    _require_int(value["active"], "assignment active", maximum=1)
    _require_int(value["current"], "assignment current", maximum=1)
    _require_availability(value["availability"], "assignment availability")


def _validate_campaign_schema(record: object) -> None:
    value = _require_exact_keys(
        record, {
            "phase",
            "chapter",
            "mode",
            "phase_availability",
            "objective_availability",
            "strategy_availability",
            "assignment_availability",
            "current_strategy_id",
            "current_objective_capabilities",
            "current_action_capabilities",
            "current_strategy_flags",
            "current_assignment_source",
            "current_assignment_subject",
            "current_assignment_availability",
            "objectives",
            "groups",
            "strategies",
            "assignments",
        }, "campaign")
    for field in ("phase", "chapter", "mode", "current_strategy_flags"):
        _require_int(value[field], f"campaign {field}", maximum=0xFF)
    for field in (
            "current_strategy_id",
            "current_objective_capabilities",
            "current_action_capabilities",
            "current_assignment_subject",
    ):
        _require_int(value[field], f"campaign {field}")
    for field in (
            "phase_availability",
            "objective_availability",
            "strategy_availability",
            "assignment_availability",
            "current_assignment_availability",
    ):
        _require_availability(value[field], f"campaign {field}")
    _require_int(
        value["current_assignment_source"],
        "current assignment source",
        maximum=AssignmentSource.UNIT.value,
    )
    for field, capacity, validator in (
        ("objectives", 8, _validate_objective_schema),
        ("groups", 8, _validate_group_schema),
        ("strategies", 8, _validate_strategy_schema),
        ("assignments", 17, _validate_assignment_schema),
    ):
        records = _require_list(value[field], f"campaign {field}")
        if len(records) > capacity:
            raise PlannerError(f"invalid planner transcript campaign {field} capacity")
        for item in records:
            validator(item)


def _validate_observation_schema(observation: object) -> None:
    value = _require_exact_keys(observation, _OBSERVATION_KEYS, "observation")
    for field in ("run_id", "observation_id"):
        _require_int(value[field], f"observation {field}")
    _require_int(value["chapter"], "observation chapter", maximum=0xFF)
    _require_int(value["state"], "observation state", maximum=5)
    _require_int(value["rejection"], "observation rejection", maximum=10)
    page_count = _require_int(
        value["page_count"],
        "observation page count",
        minimum=1,
        maximum=MAX_PAGE_COUNT,
    )
    page_index = _require_int(value["page_index"], "observation page index", maximum=MAX_PAGE_COUNT - 1)
    if page_index >= page_count:
        raise PlannerError("invalid planner transcript observation page identity")
    page_kind_text = _require_text(
        value["page_kind"],
        "observation page kind",
        _PAGE_KIND_VALUES,
    )
    page_kind = PageKind(page_kind_text)
    record_start = _require_int(value["record_start"], "observation record start")
    record_count = _require_int(
        value["record_count"],
        "observation record count",
        maximum=_PAGE_RECORD_CAPACITIES[page_kind],
    )
    total_records = _require_int(
        value["total_record_count"],
        "observation total records",
        maximum=_PAGE_TOTAL_LIMITS[page_kind],
    )
    if record_start + record_count > total_records:
        raise PlannerError("invalid planner transcript observation record span")
    _require_int(value["total_action_count"], "observation total actions", maximum=MAX_ACTIONS)
    _require_int(value["chapter_turn"], "observation chapter turn", maximum=0xFFFF)
    _require_int_list(value["rng_state"], "observation RNG state", length=3, item_maximum=0xFFFF)
    for field in (
            "rng_lcg",
            "rng_consumption",
            "actual_rom_identity",
            "actual_config_identity",
            "actual_scenario_identity",
            "actual_seed_identity",
    ):
        _require_int(value[field], f"observation {field}")
    record_specs = (
        ("fields", SEMANTIC_FIELD_COUNT, _validate_field_schema),
        ("map_cells", MAX_MAP_CELLS, _validate_map_cell_schema),
        ("units", MAX_UNITS, _validate_unit_schema),
        ("inventory", MAX_UNITS * UNIT_ITEM_COUNT, _validate_inventory_schema),
        (
            "resources",
            1 + CONVOY_ITEM_COUNT + AUTOPLAY_TELEMETRY_WORDS,
            _validate_resource_schema,
        ),
        ("flags", 2 * 256 * 8, _validate_flag_schema),
        ("actions", MAX_ACTIONS, _validate_action_schema),
    )
    for name, maximum, validator in record_specs:
        for record in _require_list(value[name], name, maximum=maximum):
            validator(record)
    if value["campaign"] is not None:
        _validate_campaign_schema(value["campaign"])


def _validate_session_schema(provenance: object) -> None:
    required = {
        "transport",
        "rom_identity",
        "config_identity",
        "scenario_identity",
        "seed_identity",
        "ready_run_id",
        "run_id",
    }
    if not isinstance(provenance, dict) or set(provenance) not in (
            required,
            required | {"source"},
    ):
        raise PlannerError("invalid planner transcript session provenance schema")
    if "source" in provenance:
        source = _require_exact_keys(
            provenance["source"],
            {"config", "rom", "scenario"},
            "session source",
        )
        _require_text(source["config"], "session source config")
        rom = _require_exact_keys(
            source["rom"],
            {"sha1", "size"},
            "session ROM",
        )
        _require_text(rom["sha1"], "session ROM identity")
        _require_int(rom["size"], "session ROM size", minimum=1)
        scenario = _require_exact_keys(
            source["scenario"],
            {"name", "schema_version"},
            "session scenario",
        )
        _require_text(scenario["name"], "session scenario name")
        _require_int(
            scenario["schema_version"],
            "session scenario version",
            minimum=1,
        )
    _require_text(provenance["transport"], "session transport")
    for field in required - {"transport"}:
        _require_int(provenance[field], f"session {field}")
    if provenance["run_id"] != provenance["ready_run_id"] + 1:
        raise PlannerError("invalid planner transcript session run identity")


def _validate_command_schema(command: object) -> None:
    if not isinstance(command, dict):
        raise PlannerError("invalid planner transcript command schema")
    kind = command.get("kind")
    keys = {
        CommandKind.START.value: {
            "kind",
            "run_id",
            "observation_id",
            "expected_identities",
        },
        CommandKind.PAGE.value: {
            "kind",
            "run_id",
            "observation_id",
            "page_index",
        },
        CommandKind.COMMIT.value: {
            "kind",
            "run_id",
            "observation_id",
            "action_ordinal",
            "token",
        },
        CommandKind.CANCEL.value: {"kind", "run_id", "observation_id"},
    }.get(kind)
    if keys is None:
        raise PlannerError("transcript contains an unsupported command kind")
    _require_exact_keys(command, keys, "command")
    _require_int(command["run_id"], "command run id")
    _require_int(command["observation_id"], "command observation id")
    if kind == CommandKind.START.value:
        if command["run_id"] != 0 or command["observation_id"] != 0:
            raise PlannerError("invalid planner transcript START identity")
        _require_int_list(
            command["expected_identities"],
            "START identities",
            length=4,
        )
    elif kind == CommandKind.PAGE.value:
        _require_int(command["page_index"], "PAGE index")
    if kind == CommandKind.COMMIT.value:
        _require_int(
            command["action_ordinal"],
            "COMMIT action ordinal",
        )
        _validate_token_schema(command["token"], "command token")


def _validate_event_schema(event: object) -> str:
    if not isinstance(event, dict):
        raise PlannerError("invalid planner transcript event schema")
    kind = event.get("event")
    payload = _TRANSCRIPT_EVENT_KEYS.get(kind)
    if payload is None:
        raise PlannerError("unknown planner transcript event")
    _require_exact_keys(
        event,
        _TRANSCRIPT_COMMON_EVENT_KEYS | payload,
        f"{kind} event",
    )
    _require_int(event["sequence"], "event sequence")
    _require_digest(event["previous_digest"], "previous event digest")
    _require_digest(event["event_digest"], "event digest")
    if kind == "session":
        _validate_session_schema(event["provenance"])
    elif kind == "command":
        _validate_command_schema(event["command"])
    elif kind in {"observation_page", "observation_complete"}:
        _validate_observation_schema(event["observation"])
        if kind == "observation_complete":
            _require_digest(event["candidate_set_digest"], "candidate-set digest")
            _require_digest(event["semantic_digest"], "semantic digest")
            identity = _require_int_list(event["page_identity"], "complete page identity", length=4)
            _require_int(identity[2], "complete page count", minimum=1, maximum=MAX_PAGE_COUNT)
            _require_int(identity[3], "complete action count", maximum=MAX_ACTIONS)
    elif kind == "acknowledgement":
        _require_int(event["command_id"], "acknowledgement command id", minimum=1)
        _require_int(
            event["kind"],
            "acknowledgement kind",
            allowed=frozenset(_COMMAND_KIND_CODES.values()),
            message="acknowledgement kind mismatch",
        )
        _require_int(
            event["result"],
            "acknowledgement result",
            maximum=1,
            message="invalid acknowledgement result/rejection pair",
        )
        _require_int(
            event["rejection"],
            "acknowledgement rejection",
            maximum=10,
            message="invalid acknowledgement result/rejection pair",
        )
    elif kind == "completion":
        _require_int(event["command_id"], "completion command id", minimum=1)
        _require_int(
            event["kind"],
            "completion kind",
            allowed=frozenset(_COMMAND_KIND_CODES.values()),
        )
        _require_int(
            event["response_frames"],
            "completion frames",
            message="planner transcript completion timing is invalid",
        )
    elif kind == "settled":
        identity = _require_list(event["observation_identity"], "settled observation identity", length=6)
        for field in (0, 1, 2, 3, 5):
            _require_int(identity[field], "settled observation identity")
        _require_text(identity[4], "settled page kind", _PAGE_KIND_VALUES)
        if (not 1 <= identity[3] <= MAX_PAGE_COUNT or identity[2] >= identity[3] or identity[5] > MAX_ACTIONS):
            raise PlannerError("invalid planner transcript settled page identity")
        _require_digest(event["observation_digest"], "settled observation digest")
        checkpoint = _require_int_list(
            event["checkpoint"],
            "settled checkpoint",
            length=13,
            message="invalid settled transcript record",
        )
        if any(checkpoint):
            if checkpoint[:3] != [0x41504C4E, PROTOCOL_VERSION, 52]:
                raise PlannerError("invalid planner transcript checkpoint identity")
            _require_int(checkpoint[4], "checkpoint chapter", maximum=0xFF)
            _require_int(checkpoint[5], "checkpoint mode", maximum=0xFF)
            for seed in checkpoint[7:10]:
                _require_int(seed, "checkpoint RNG state", maximum=0xFFFF)
        _require_int_list(
            event["command_words"],
            "settled command words",
            length=16,
            message="invalid settled transcript record",
        )
        _require_int_list(
            event["telemetry"],
            "settled telemetry",
            maximum=AUTOPLAY_TELEMETRY_WORDS,
            message="invalid settled transcript record",
        )
        rng = _require_exact_keys(
            event["rng"],
            {"state", "lcg", "consumption"},
            "settled RNG",
        )
        _require_int_list(rng["state"], "settled RNG state", length=3, item_maximum=0xFFFF)
        _require_int(rng["lcg"], "settled RNG LCG")
        _require_int(rng["consumption"], "settled RNG consumption")
        terminal = _require_exact_keys(
            event["terminal"],
            {"state", "rejection"},
            "settled terminal",
        )
        _require_int(terminal["state"], "terminal state", maximum=5)
        _require_int(terminal["rejection"], "terminal rejection", maximum=10)
    elif kind == "transport_error":
        _require_text(event["code"], "transport error code", _TRANSPORT_ERROR_CODES)
        _require_int(event["command_id"], "transport error command id", minimum=1)
        _require_int(
            event["kind"],
            "transport error kind",
            allowed=frozenset(_COMMAND_KIND_CODES.values()),
        )
    return kind


def _mix_digest(digest: int, value: int) -> int:
    return ((digest ^ (value & 0xFFFFFFFF)) * 16777619) & 0xFFFFFFFF


def _runtime_seed_identity(observation: dict[str, object]) -> int:
    digest = 2166136261
    for value in (*observation["rng_state"], observation["rng_lcg"]):
        digest = _mix_digest(digest, value)
    return digest


def _runtime_scenario_identity(namespace: int, chapter: int, dimensions: int) -> int:
    return _mix_digest(
        _mix_digest(_mix_digest(2166136261, namespace), chapter),
        dimensions)


def wire_page_digest(words: Iterable[int]) -> int:
    values = tuple(words)
    if len(values) != 256:
        raise PlannerError("malformed fixed-width observation")
    digest = 2166136261
    for index, value in enumerate(values):
        digest = _mix_digest(
            digest, 0 if index == OBSERVATION_DIGEST_WORD else value)
    return digest


def _fixture_action_token(run_id: int, observation_id: int, ordinal: int, action: Action) -> OpaqueToken:
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
    target_item_slot = (0xFF if action.target_item_slot is None else action.target_item_slot)
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


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def semantic_state_digest(state: object) -> str:
    return _digest(state)


def _observation_semantics(observation: Observation) -> dict[str, object]:
    serialized = asdict(observation)
    return {
        key: serialized[key]
        for key in (
            "chapter",
            "chapter_turn",
            "campaign",
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
                "previous_digest": (prospective[-1]["event_digest"] if prospective else "0" * 64),
                **event,
            }
            chained["event_digest"] = _digest(chained)
            prospective.append(chained)
        if len(_canonical(self._document(prospective))) > self.max_bytes:
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)
        self._events = prospective
    def _append(self, event: dict[str, object]) -> None:
        self._append_many((event, ))
    def snapshot(self) -> int:
        return len(self._events)
    def restore(self, snapshot: int) -> None:
        if not 0 <= snapshot <= len(self._events):
            raise PlannerError("invalid transcript snapshot")
        del self._events[snapshot:]
    def reserve_exchange(self) -> None:
        if (len(_canonical(self._document(self._events))) + MAX_TRANSCRIPT_EXCHANGE_BYTES > self.max_bytes):
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)
    @staticmethod
    def _session_event(provenance: dict[str, object], ) -> dict[str, object]:
        return {
            "event": "session",
            "provenance": json.loads(_canonical(provenance)),
        }
    @staticmethod
    def _observation_page_event(observation: Observation, ) -> dict[str, object]:
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
        self._append_many((
            self._session_event(provenance),
            self._observation_page_event(observation),
            self._settled_event(
                observation,
                checkpoint,
                command_words,
            ),
        ))
    @staticmethod
    def _complete_observation_event(observation: Observation, ) -> dict[str, object]:
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
        self._append({
            "event": "command",
            "command": json.loads(_canonical(command)),
        })
    def record_acknowledgement(
        self,
        command_id: int,
        kind: int,
        result: int,
        rejection: int,
    ) -> None:
        self._append({
            "event": "acknowledgement",
            "command_id": command_id,
            "kind": kind,
            "result": result,
            "rejection": rejection,
        })
    def record_completion(
        self,
        command_id: int,
        kind: int,
        response_frames: int,
    ) -> None:
        self._append({
            "event": "completion",
            "command_id": command_id,
            "kind": kind,
            "response_frames": response_frames,
        })
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
        telemetry = tuple(record.value for record in observation.resources if record.kind is ValueKind.AUTOPLAY_TELEMETRY)
        return {
            "event":
            "settled",
            "observation_identity": [
                observation.run_id,
                observation.observation_id,
                observation.page_index,
                observation.page_count,
                observation.page_kind,
                observation.total_action_count,
            ],
            "observation_digest":
            _digest(asdict(observation)),
            "checkpoint":
            checkpoint_values,
            "command_words":
            command_values,
            "telemetry":
            telemetry,
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
        self._append(self._settled_event(observation, checkpoint, command_words))
    def record_complete_and_settled(
        self,
        observation: Observation,
        checkpoint: Iterable[int],
        command_words: Iterable[int],
    ) -> None:
        self._append_many((
            self._complete_observation_event(observation),
            self._settled_event(
                observation,
                checkpoint,
                command_words,
            ),
        ))
    def record_transport_error(
        self,
        code: str,
        command_id: int,
        kind: int,
    ) -> None:
        self._append({
            "event": "transport_error",
            "code": code,
            "command_id": command_id,
            "kind": kind,
        })
    def export(self) -> bytes:
        return _canonical(self._document(self._events))
    def digest(self) -> str:
        return hashlib.sha256(self.export()).hexdigest()
    @classmethod
    def import_bytes(
        cls,
        data: bytes,
        validation_mode: ValidationMode = ValidationMode.PRODUCTION,
        scenario_namespace: int = SCENARIO_NAMESPACE,
    ) -> "PlannerTranscript":
        if not isinstance(validation_mode, ValidationMode):
            raise PlannerError("invalid trusted transcript validation mode")
        _require_int(scenario_namespace, "trusted scenario namespace")
        if len(data) > MAX_TRACE_BYTES:
            raise PlannerError(Rejection.RESOURCE_LIMIT.value)
        _validate_json_text_depth(data)
        try:
            document = json.loads(
                data,
                parse_constant=_reject_json_constant,
            )
        except (
                UnicodeDecodeError,
                ValueError,
                RecursionError,
        ) as error:
            raise PlannerError("invalid planner transcript JSON") from error
        if _canonical(document) != data:
            raise PlannerError("planner transcript is not canonical")
        if (not isinstance(document, dict) or document.get("schema") != cls.SCHEMA or not isinstance(document.get("events"), list)):
            raise PlannerError("invalid planner transcript envelope")
        _require_exact_keys(
            document,
            {"schema", "events"},
            "envelope",
        )
        events = document["events"]
        if (not events or not isinstance(events[0], dict) or events[0].get("event") != "session"
                or sum(isinstance(event, dict) and event.get("event") == "session" for event in events) != 1):
            raise PlannerError("planner transcript requires exactly one leading session")
        session_provenance = events[0].get("provenance")
        _validate_session_schema(session_provenance)
        if validation_mode is ValidationMode.PRODUCTION and (session_provenance["transport"] != "restricted-libmgba" or any(session_provenance[field] == 0
                                                                                                                            for field in (
                                                                                                                                "rom_identity",
                                                                                                                                "config_identity",
                                                                                                                                "scenario_identity",
                                                                                                                                "seed_identity",
                                                                                                                            ))):
            raise PlannerError("invalid production transcript provenance")
        transcript = cls()
        previous_digest = "0" * 64
        pending_command: dict[str, object] | None = None
        pending_ack: dict[str, object] | None = None
        pending_completion = False
        pending_response = False
        awaiting_settlement = False
        pending_previous_page = pending_previous_checkpoint = None
        pending_previous_observation = None
        pending_rejection_terminal = False
        pending_accepted_terminal = False
        expected_command_id = 1
        actions_by_observation: dict[
            tuple[int, int],
            list[object],
        ] = {}
        latest_observation: dict[str, object] | None = None
        latest_page = latest_checkpoint = latest_command_words = None
        observation_pages: dict[tuple[int, int], list[dict[str, object]]] = {}
        active_identity_bound = False
        scenario_identities = {}
        for sequence, event in enumerate(events):
            if not isinstance(event, dict):
                raise PlannerError("invalid planner transcript event")
            if (event.get("sequence") != sequence or event.get("previous_digest") != previous_digest):
                raise PlannerError("planner transcript order is invalid")
            event_without_digest = dict(event)
            event_digest = event_without_digest.pop("event_digest", None)
            if event_digest != _digest(event_without_digest):
                raise PlannerError("planner transcript digest mismatch")
            previous_digest = event_digest
            kind = _validate_event_schema(event)
            if kind == "session":
                if sequence != 0:
                    raise PlannerError("planner transcript session must be first")
            elif kind == "command":
                if pending_command is not None or awaiting_settlement:
                    raise PlannerError("planner transcript command overlap")
                pending_command = event
                pending_ack = None
                pending_completion = False
                pending_response = False
                pending_previous_page = (
                    latest_page
                    if latest_page is not None else latest_observation)
                pending_previous_checkpoint = latest_checkpoint
                pending_previous_observation = latest_observation
                pending_rejection_terminal = False
                pending_accepted_terminal = False
            elif kind == "acknowledgement":
                if (pending_command is None or pending_ack is not None or event.get("command_id") != expected_command_id):
                    raise PlannerError("planner transcript acknowledgement order")
                command = pending_command["command"]
                command_kind_code = _COMMAND_KIND_CODES[command["kind"]]
                if (event.get("kind") != command_kind_code):
                    raise PlannerError("acknowledgement kind mismatch")
                result = event.get("result")
                rejection = event.get("rejection")
                accepted = result == 1 and rejection == 0
                rejected = (result == 0 and rejection in _VALID_WIRE_REJECTION_CODES)
                if not (accepted or rejected) or (accepted and command_kind_code not in _COMMAND_KIND_CODES.values()):
                    raise PlannerError("invalid acknowledgement result/rejection pair")
                if command["kind"] == CommandKind.CANCEL.value and (result != 0 or rejection != 8):
                    raise PlannerError("invalid CANCEL acknowledgement")
                if accepted and (latest_observation is None or command.get("run_id") != latest_observation.get("run_id")
                                 or command.get("run_id") != (session_provenance["ready_run_id"] if command_kind_code == 1 else session_provenance["run_id"])):
                    raise PlannerError("accepted command run identity mismatch")
                if (command_kind_code != 1 and rejection != _WIRE_STALE_OBSERVATION
                        and (latest_observation is None or command.get("observation_id") != latest_observation.get("observation_id"))):
                    raise PlannerError("command observation identity mismatch")
                if accepted and command_kind_code == 4 and (latest_observation is None
                                                            or not 0 <= command["page_index"] < latest_observation.get("page_count", 0)):
                    raise PlannerError("PAGE command identity mismatch")
                if accepted and command_kind_code == 1 and (command.get("expected_identities") != [
                        session_provenance["rom_identity"],
                        session_provenance["config_identity"],
                        session_provenance["scenario_identity"],
                        session_provenance["seed_identity"],
                ]):
                    raise PlannerError("START command session identity mismatch")
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
                    action = (actions[ordinal] if 0 <= ordinal < len(actions) else None)
                    if (not 0 <= ordinal < len(actions) or not isinstance(action, dict) or command.get("token") != action.get("token")):
                        raise PlannerError("accepted transcript token mismatch")
            elif kind == "completion":
                if (pending_ack is None or pending_completion or event.get("command_id") != pending_ack.get("command_id")
                        or event.get("kind") != pending_ack.get("kind")):
                    raise PlannerError("planner transcript completion order")
                response_frames = event.get("response_frames")
                completion_limit = (COMMIT_COMPLETION_FRAME_LIMIT if pending_ack.get("kind") == 2 and pending_ack.get("result") == 1
                                    and pending_ack.get("rejection") == 0 else COMMAND_RESPONSE_FRAME_LIMIT)
                if (response_frames > completion_limit):
                    raise PlannerError("planner transcript completion timing is invalid")
                pending_completion = True
            elif kind == "settled":
                settled_command = pending_command
                settled_ack = pending_ack
                if not awaiting_settlement:
                    raise PlannerError("settled event has no response observation")
                if pending_command is not None:
                    if not pending_completion or not pending_response:
                        raise PlannerError("settled event precedes command response")
                    pending_command = None
                    pending_ack = None
                    pending_completion = False
                    pending_response = False
                awaiting_settlement = False
                if latest_observation is None:
                    raise PlannerError("invalid settled transcript record")
                terminal = event.get("terminal")
                rng = event.get("rng")
                expected_identity = [
                    latest_observation.get("run_id"),
                    latest_observation.get("observation_id"),
                    latest_observation.get("page_index"),
                    latest_observation.get("page_count"),
                    latest_observation.get("page_kind"),
                    latest_observation.get("total_action_count"),
                ]
                expected_telemetry = [
                    record.get("value") for record in latest_observation.get("resources", []) if record.get("kind") == ValueKind.AUTOPLAY_TELEMETRY.value
                ]
                if (event["observation_identity"] != expected_identity or event.get("observation_digest") != _digest(latest_observation) or terminal != {
                        "state": latest_observation.get("state"),
                        "rejection": latest_observation.get("rejection"),
                } or rng != {
                        "state": latest_observation.get("rng_state"),
                        "lcg": latest_observation.get("rng_lcg"),
                        "consumption": latest_observation.get("rng_consumption"),
                } or event["telemetry"] != expected_telemetry):
                    raise PlannerError("settled runtime state mismatch")
                _validate_checkpoint_binding(
                    event["checkpoint"], session_provenance,
                    pending_previous_checkpoint, (
                        settled_command["command"]
                        if settled_command is not None else None),
                    settled_ack, pending_previous_observation, latest_observation, terminal,
                    settled_command is None and latest_command_words is None)
                transition_reset = (
                    settled_command is not None and settled_ack is not None
                    and settled_command["command"]["kind"]
                        == CommandKind.COMMIT.value
                    and settled_ack["result"] == 1
                    and any(event["checkpoint"])
                    and pending_previous_observation is not None
                    and latest_observation["chapter"]
                        != pending_previous_observation["chapter"])
                mailbox_ack = settled_ack
                if (settled_command is not None and settled_ack is not None
                        and settled_command["command"]["kind"]
                            == CommandKind.START.value
                        and settled_ack["result"] == 1
                        and terminal["state"] == 5):
                    mailbox_ack = {
                        "result": 0, "rejection": terminal["rejection"]}
                expected_words = (
                    [0] * 16 if transition_reset else
                    _cleared_command_words(
                        settled_command["command"], mailbox_ack)
                    if settled_command is not None and mailbox_ack is not None
                    else latest_command_words or [0] * 16)
                if event["command_words"] != expected_words:
                    raise PlannerError("settled command mailbox mismatch")
                if settled_ack is not None:
                    acknowledgement_accepted = (settled_ack.get("result") == 1 and settled_ack.get("rejection") == 0)
                    if (acknowledgement_accepted
                            and not pending_accepted_terminal
                            and terminal["rejection"] != 0
                            or not acknowledgement_accepted
                            and terminal["rejection"] != settled_ack.get("rejection")):
                        raise PlannerError("settled rejection does not match acknowledgement")
                    if not acknowledgement_accepted:
                        expected_checkpoint = ([0] * 13 if pending_rejection_terminal else pending_previous_checkpoint)
                        if event["checkpoint"] != expected_checkpoint:
                            raise PlannerError("rejected response changed checkpoint")
                    elif pending_accepted_terminal:
                        if event["checkpoint"] != [0] * 13:
                            raise PlannerError("accepted terminal response retained checkpoint")
                    command = settled_command.get("command")
                    if (not acknowledgement_accepted and isinstance(command, dict) and command.get("kind") == CommandKind.COMMIT.value
                            and terminal["state"] == 3):
                        raise PlannerError("rejected COMMIT cannot settle as COMMITTED")
                latest_checkpoint = event["checkpoint"]
                latest_command_words = event["command_words"]
            elif kind == "transport_error":
                code = event["code"]
                if (sequence != len(document["events"]) - 1 or awaiting_settlement or pending_command is None or pending_completion or pending_response):
                    raise PlannerError("transport error must terminate transcript")
                command_kind = _COMMAND_KIND_CODES[pending_command["command"]["kind"]]
                command_id = (pending_ack["command_id"] if pending_ack is not None else expected_command_id)
                if event["kind"] != command_kind or event["command_id"] != command_id:
                    raise PlannerError("transport error command identity mismatch")
                if code in {"COMMAND_ACK_TIMEOUT", "INVALID_COMMAND_ACK"}:
                    valid_stage = pending_ack is None
                elif code == "ACTION_COMPLETION_TIMEOUT":
                    valid_stage = (pending_ack is not None and pending_ack["kind"] == _COMMAND_KIND_CODES[CommandKind.COMMIT.value]
                                   and pending_ack["result"] == 1 and pending_ack["rejection"] == 0)
                else:
                    valid_stage = (pending_ack is not None and not (pending_ack["kind"] == _COMMAND_KIND_CODES[CommandKind.COMMIT.value]
                                                                    and pending_ack["result"] == 1 and pending_ack["rejection"] == 0))
                if not valid_stage:
                    raise PlannerError("transport error command stage mismatch")
                pending_command = None
                pending_ack = None
            elif kind == "observation_complete":
                if pending_command is not None or awaiting_settlement:
                    raise PlannerError("complete observation violates command ordering")
                observation = event["observation"]
                active_identity_bound = cls._validate_session_observation(
                    observation,
                    session_provenance,
                    active_identity_bound,
                    scenario_namespace,
                    scenario_identities,
                    validation_mode is ValidationMode.PRODUCTION,
                )
                expected_page_identity = [
                    observation.get("run_id"),
                    observation.get("observation_id"),
                    observation.get("page_count"),
                    observation.get("total_action_count"),
                ]
                if event.get("page_identity") != expected_page_identity:
                    raise PlannerError("complete observation page identity mismatch")
                actions = observation["actions"]
                if event.get("candidate_set_digest") != _digest(actions):
                    raise PlannerError("candidate-set transcript digest mismatch")
                semantics = {
                    key: observation.get(key)
                    for key in (
                        "chapter",
                        "chapter_turn",
                        "campaign",
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
                if (session_provenance["transport"] == "restricted-libmgba" and not pages):
                    raise PlannerError("complete observation has no transport pages")
                _validate_complete_observation(observation, pages, validation_mode)
                actions_by_observation[(
                    observation.get("run_id"),
                    observation.get("observation_id"),
                )] = actions
                latest_observation = observation
                awaiting_settlement = True
            elif kind == "observation_page":
                if awaiting_settlement:
                    raise PlannerError("response observation is missing settlement")
                if pending_command is not None and not pending_completion:
                    raise PlannerError("response observation precedes completion")
                observation = event["observation"]
                active_identity_bound = cls._validate_session_observation(
                    observation,
                    session_provenance,
                    active_identity_bound,
                    scenario_namespace,
                    scenario_identities,
                    validation_mode is ValidationMode.PRODUCTION,
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
                    accepted = (pending_ack is not None and pending_ack.get("result") == 1 and pending_ack.get("rejection") == 0)
                    if accepted:
                        pending_accepted_terminal = _validate_accepted_response(
                            command, pending_previous_page, observation,
                            session_provenance,
                            validation_mode is ValidationMode.PRODUCTION)
                    elif not accepted:
                        pending_rejection_terminal = _validate_rejected_response(
                            command,
                            pending_ack,
                            pending_previous_page,
                            observation,
                        )
                    pending_response = True
                latest_observation = observation
                latest_page = observation
                awaiting_settlement = True
            else:
                raise PlannerError("unknown planner transcript event")
        if pending_command is not None or awaiting_settlement:
            raise PlannerError("planner transcript is truncated")
        transcript._events = document["events"]
        return transcript
    @classmethod
    def import_production_bytes(
        cls,
        data: bytes,
        scenario_namespace: int = SCENARIO_NAMESPACE,
    ) -> "PlannerTranscript":
        return cls.import_bytes(
            data, ValidationMode.PRODUCTION, scenario_namespace)
    @classmethod
    def import_synthetic_bytes(cls, data: bytes) -> "PlannerTranscript":
        return cls.import_bytes(data, ValidationMode.SYNTHETIC)
    @staticmethod
    def _validate_session_observation(
        observation: dict[str, object],
        session: dict[str, object],
        active_identity_bound: bool,
        scenario_namespace: int,
        scenario_identities: dict[tuple[int, int], int],
        production: bool,
    ) -> bool:
        run_id = observation.get("run_id")
        if (run_id not in {session["ready_run_id"], session["run_id"]} or observation.get("actual_rom_identity") != session["rom_identity"]
                or observation.get("actual_config_identity") != session["config_identity"]):
            raise PlannerError("observation session identity mismatch")
        if run_id == session["ready_run_id"] or not active_identity_bound:
            if (observation.get("actual_scenario_identity") != session["scenario_identity"]
                    or observation.get("actual_seed_identity") != session["seed_identity"]):
                raise PlannerError("observation session scenario/seed mismatch")
        if production and observation["observation_id"] != 0:
            if observation["actual_seed_identity"] != _runtime_seed_identity(observation):
                raise PlannerError("observation runtime seed identity mismatch")
            key = (run_id, observation["observation_id"])
            expected_scenario = scenario_identities.get(key)
            dimensions = next((field["value"] for field in observation["fields"]
                               if field["name"] == "map_dimensions"
                               and field["availability"] == Availability.AVAILABLE), None)
            if dimensions is not None:
                expected_scenario = _runtime_scenario_identity(
                    scenario_namespace, observation["chapter"], dimensions)
                scenario_identities[key] = expected_scenario
            if observation["actual_scenario_identity"] != expected_scenario:
                raise PlannerError("observation runtime scenario identity mismatch")
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
    validation_mode = ValidationMode.SYNTHETIC
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
    def snapshot_collection_state(self) -> tuple[object, ...]:
        return (
            self._next_command_id, self._observation, self._all_actions,
            self._next_observation_id, self._committed_count,
            self._active, self.cancelled)
    def restore_collection_state(self, state: tuple[object, ...]) -> None:
        (
            self._next_command_id, self._observation, self._all_actions,
            self._next_observation_id, self._committed_count,
            self._active, self.cancelled,
        ) = state
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
        self.transcript.record_session({
            "transport": "mirror",
            "rom_identity": 0,
            "config_identity": 0,
            "scenario_identity": 0,
            "seed_identity": 0,
            "ready_run_id": self._run_id - 1,
            "run_id": self._run_id,
            "source": self._provenance,
        })
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
            ) for index, action in enumerate(action_tuple))
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
            (0, ) * 13,
            (0, ) * 16,
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
        if (command.run_id != observation.run_id or command.observation_id != observation.observation_id):
            raise PlannerError(Rejection.STALE_OBSERVATION.value)
        if command.page_index is None or not 1 <= command.page_index < observation.page_count:
            raise PlannerError(Rejection.UNKNOWN_ACTION.value)
        start = (command.page_index - 1) * ACTIONS_PER_PAGE
        actions = self._all_actions[start:start + ACTIONS_PER_PAGE]
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
        self.transcript.record_command(_command_payload(command))
        command_id = self._next_command_id
        self._next_command_id += 1
        self.transcript.record_acknowledgement(command_id, 4, 1, 0)
        self.transcript.record_completion(command_id, 4, 0)
        self.transcript.record_observation_page(page)
        self.transcript.record_settled(
            page, (0, ) * 13,
            _cleared_command_words(
                _command_payload(command), {"result": 1, "rejection": 0}))
        return page
    def commit(self, command: Command) -> ActionRecord:
        observation = self._observation
        if observation is None:
            raise PlannerError(Rejection.NOT_READY.value)
        if command.kind is CommandKind.CANCEL:
            if command.run_id != observation.run_id or command.observation_id != observation.observation_id:
                raise PlannerError(Rejection.STALE_OBSERVATION.value)
            self.transcript.reserve_exchange()
            self.transcript.record_command(_command_payload(command))
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
                (0, ) * 13,
                _cleared_command_words(
                    _command_payload(command),
                    {"result": 0, "rejection": 8}),
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
        self.transcript.record_command(_command_payload(command))
        command_id = self._next_command_id
        self._next_command_id += 1
        self.transcript.record_acknowledgement(command_id, 2, 1, 0)
        self.transcript.record_completion(command_id, 2, 0)
        settled = replace(observation, state=3)
        self.transcript.record_observation_page(settled)
        self.transcript.record_settled(
            settled,
            (0, ) * 13,
            _cleared_command_words(
                _command_payload(command),
                {"result": 1, "rejection": 0}),
        )
        self._committed_count += 1
        self._observation = None
        self._all_actions = ()
        return record
    def trace_digest(self) -> str:
        return self.transcript.digest()


_AVAILABILITY_BY_VALUE = dict(enumerate(Availability))
_PAGE_KIND_BY_VALUE = dict(enumerate(PageKind))
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
_OBSERVATION_WORD_COUNT = 256
_OBSERVATION_HEADER_WORDS = 25
_PAGE_RECORD_CAPACITIES = {
    PageKind.CONTROL: 0,
    PageKind.SUMMARY: SEMANTIC_FIELD_COUNT,
    PageKind.MAP: 230,
    PageKind.UNITS: 23,
    PageKind.INVENTORY: 115,
    PageKind.RESOURCES: 115,
    PageKind.FLAGS: 115,
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
_PAGE_COLLECTION = {
    PageKind.SUMMARY: "fields",
    PageKind.MAP: "map_cells",
    PageKind.UNITS: "units",
    PageKind.INVENTORY: "inventory",
    PageKind.RESOURCES: "resources",
    PageKind.FLAGS: "flags",
    PageKind.ACTIONS: "actions",
}
_PAGE_ORDER = tuple(_PAGE_COLLECTION)
_ROSTER_SLOTS = (
    *range(1, 0x3F),
    *range(0x41, 0x55),
    *range(0x81, 0xB3),
)


def _validate_complete_observation(
        observation: dict[str, object],
        pages: Iterable[dict[str, object]] = (),
        validation_mode: ValidationMode = ValidationMode.SYNTHETIC,
) -> None:
    page_values = tuple(pages)
    strict = validation_mode is ValidationMode.PRODUCTION
    fields = observation["fields"]
    field_ids = tuple((field["name"], field["source"], field["bound"]) for field in fields)
    field_names = tuple(identity[0] for identity in field_ids)
    if len(field_names) != len(set(field_names)):
        raise PlannerError("complete observation has duplicate fields")
    if strict and field_ids != tuple(_SEMANTIC_FIELD_NAMES.values()):
        raise PlannerError("complete observation summary is not canonical")

    dimensions = next(
        (field for field in fields if field["name"] == "map_dimensions"),
        None,
    )
    cells = observation["map_cells"]
    map_cells_by_position = {
        (cell["x"], cell["y"]): cell for cell in cells
    }
    map_size = None
    if dimensions is not None and dimensions["availability"] == Availability.AVAILABLE:
        width = dimensions["value"] & 0xFFFF
        height = dimensions["value"] >> 16
        if (not 1 <= width <= 64 or not 1 <= height <= 64 or len(cells) != width * height or tuple((cell["x"], cell["y"]) for cell in cells) != tuple(
            (index % width, index // width) for index in range(width * height))):
            raise PlannerError("complete observation map dimensions mismatch")
        map_size = (width, height)
    elif strict or dimensions is not None and observation["actions"]:
        raise PlannerError("complete observation omitted map dimensions")

    units = observation["units"]
    unit_slots = tuple(unit["slot"] for unit in units)
    if (len(unit_slots) != len(set(unit_slots)) or any(slot not in _ROSTER_SLOTS for slot in unit_slots)
            or unit_slots != tuple(slot for slot in _ROSTER_SLOTS if slot in unit_slots)):
        raise PlannerError("complete observation roster is not canonical")
    unit_availability = {unit["slot"]: unit["availability"] for unit in units}
    if (strict or units) and any(cell["unit"] != 0 and cell["unit"] not in unit_availability for cell in cells):
        raise PlannerError("complete observation map unit is not in roster")

    inventory = observation["inventory"]
    inventory_ids = tuple((record["unit"], record["slot"]) for record in inventory)
    expected_inventory = tuple((unit, slot) for unit in unit_slots for slot in range(UNIT_ITEM_COUNT))
    if (strict or inventory) and inventory_ids != expected_inventory:
        raise PlannerError("complete observation inventory is not canonical")
    for record in inventory:
        availability = unit_availability.get(record["unit"])
        if availability is None or (availability == Availability.AVAILABLE and record["availability"]
                                    != (Availability.AVAILABLE if record["raw_item"] else Availability.EMPTY)) or (availability != Availability.AVAILABLE
                                                                                                                   and record["availability"] != availability):
            raise PlannerError("complete observation inventory availability mismatch")
    inventory_by_slot = {(record["unit"], record["slot"]): record for record in inventory}
    units_by_slot = {unit["slot"]: unit for unit in units}
    for unit in units:
        if unit["availability"] in {
                Availability.NOT_VISIBLE,
                Availability.NOT_APPLICABLE,
        }:
            if (unit["equipped_slot"] is not None or any((
                    unit["character"],
                    unit["unit_class"],
                    *unit["position"],
                    *unit["hp"],
                    unit["state"],
                    unit["inventory_digest"],
                    unit["status_index"],
                    unit["status_duration"],
                    unit["rescue_partner"],
                    unit["equipped_item"],
                    unit["level"],
                    unit["exp"],
                    unit["power"],
                    unit["skill"],
                    unit["speed"],
                    unit["luck"],
                    unit["defense"],
                    unit["resistance"],
                    unit["constitution"],
                    unit["movement"],
                    *unit["weapon_ranks"],
                    unit["deployed"],
                    unit["dead"],
                    unit["moved"],
                    unit["acted"],
                    unit["rescued"],
                    unit["rescuing"],
            ))):
                raise PlannerError("complete observation unavailable unit semantics mismatch")
            continue
        records = tuple(inventory_by_slot[(unit["slot"], slot)] for slot in range(UNIT_ITEM_COUNT))
        digest = 2166136261
        for record in records:
            digest = _mix_digest(digest, record["raw_item"])
        equipped = unit["equipped_slot"]
        state = unit["state"]
        expected_flags = (
            int(not state & ((1 << 2) | (1 << 3) | (1 << 16))),
            int(bool(state & (1 << 2))),
            int(bool(state & (1 << 6))),
            int(bool(state & ((1 << 6) | (1 << 10)))),
            int(bool(state & (1 << 5))),
            int(bool(state & (1 << 4))),
        )
        if (digest != unit["inventory_digest"] or equipped is None and unit["equipped_item"] != 0
                or equipped is not None and records[equipped]["raw_item"] != unit["equipped_item"]
                or unit["rescue_partner"] != 0 and unit["rescue_partner"] not in unit_availability
                or tuple(unit[field] for field in ("deployed", "dead", "moved", "acted", "rescued", "rescuing")) != expected_flags
                or bool(unit["rescue_partner"]) != bool(unit["rescued"] or unit["rescuing"])):
            raise PlannerError("complete observation unit semantics mismatch")
    actions = observation["actions"]
    for action in actions:
        _validate_action_schema(action)
    if (len(actions) != observation["total_action_count"] or tuple(action["ordinal"] for action in actions) != tuple(range(len(actions)))
            or len({_canonical(action["action"])
                    for action in actions}) != len(actions) or len({tuple(action["token"][f"word{index}"] for index in range(4))
                                                                    for action in actions}) != len(actions)
            or map_size is not None and any(x >= map_size[0] or y >= map_size[1] for record in actions for x, y in (
                record["action"]["destination"],
                record["action"]["target_position"],
            )) or (strict or inventory) and any(not _observation_action_item_valid(action["action"], inventory_by_slot, units_by_slot, map_cells_by_position) for action in actions)
            or any(
                not _observation_action_target_valid(
                    action["action"], map_cells_by_position)
                for action in actions)
            or (strict or units) and any(
                unit_availability.get(action["action"]["actor"]) != Availability.AVAILABLE
                or action["action"]["target"] != 0 and unit_availability.get(action["action"]["target"]) != Availability.AVAILABLE for action in actions)):
        raise PlannerError("complete observation actions are not canonical")

    campaign = observation["campaign"]
    if strict and campaign is None:
        raise PlannerError("complete observation omitted campaign semantics")
    if campaign is not None:
        objectives = campaign["objectives"]
        groups = campaign["groups"]
        strategies = campaign["strategies"]
        assignments = campaign["assignments"]
        objective_ids = tuple(record["objective_id"] for record in objectives)
        group_ids = tuple(record["group_id"] for record in groups)
        strategy_ids = tuple(record["strategy_id"] for record in strategies)
        assignment_ids = tuple((record["source"], record["subject_id"], record["strategy_id"], record["activation_flag"]) for record in assignments)
        if (campaign["chapter"] != observation["chapter"] or campaign["phase_availability"] != Availability.AVAILABLE
                or len(objective_ids) != len(set(objective_ids)) or len(group_ids) != len(set(group_ids)) or len(strategy_ids) != len(set(strategy_ids))
                or len(assignment_ids) != len(set(assignment_ids)) or any(record["group_id"] != 0 and record["group_id"] not in group_ids
                                                                          for record in objectives)
                or any(record["completion_objective_id"] != 0 and record["completion_objective_id"] not in objective_ids
                       for record in objectives) or any(len(record["members"]) != len(set(record["members"]))
                                                        for record in groups) or any(record["strategy_id"] not in strategy_ids for record in assignments)
                or any(record["source"] == AssignmentSource.CHAPTER and record["subject_id"] != campaign["chapter"]
                       or record["source"] == AssignmentSource.GROUP and record["subject_id"] not in group_ids
                       for record in assignments) or tuple(record["source"]
                                                           for record in assignments) != tuple(sorted(record["source"] for record in assignments))):
            raise PlannerError("complete observation campaign semantics mismatch")
        for availability, records in (
            (campaign["objective_availability"], (*objectives, *groups)),
            (campaign["strategy_availability"], strategies),
            (campaign["assignment_availability"], assignments),
        ):
            if (availability == Availability.AVAILABLE and not records or availability != Availability.AVAILABLE and records
                    or any(record["availability"] != Availability.AVAILABLE for record in records)):
                raise PlannerError("complete observation campaign availability mismatch")
        current = tuple(record for record in assignments if record["current"])
        current_strategy = next((record for record in strategies if record["strategy_id"] == campaign["current_strategy_id"]), None)
        if campaign["current_strategy_id"] == 0:
            if (current or campaign["current_assignment_source"] != AssignmentSource.NONE or campaign["current_assignment_subject"] != 0
                    or campaign["current_assignment_availability"] != Availability.NOT_APPLICABLE):
                raise PlannerError("complete observation current assignment mismatch")
        elif (len(current) != 1 or current_strategy is None or campaign["current_assignment_availability"] != Availability.AVAILABLE
              or current[0]["strategy_id"] != campaign["current_strategy_id"] or current[0]["source"] != campaign["current_assignment_source"]
              or current[0]["subject_id"] != campaign["current_assignment_subject"]
              or current_strategy["objective_capabilities"] != campaign["current_objective_capabilities"]
              or current_strategy["action_capabilities"] != campaign["current_action_capabilities"]
              or current_strategy["flags"] != campaign["current_strategy_flags"]):
            raise PlannerError("complete observation current assignment mismatch")

    resources = observation["resources"]
    resource_ids = tuple((record["kind"], record["slot"]) for record in resources)
    expected_resources = (
        (ValueKind.GOLD, None),
        *((ValueKind.CONVOY_ITEM, slot) for slot in range(CONVOY_ITEM_COUNT)),
        *((ValueKind.AUTOPLAY_TELEMETRY, slot) for slot in range(AUTOPLAY_TELEMETRY_WORDS)),
    )
    if (strict or resources) and resource_ids != expected_resources:
        raise PlannerError("complete observation resources are not canonical")
    if resources and (resources[0]["availability"] != Availability.AVAILABLE
                      or any(record["availability"] == Availability.AVAILABLE and record["value"] == 0
                             or record["availability"] == Availability.EMPTY and record["value"] != 0 or record["availability"] not in {
                                 Availability.AVAILABLE,
                                 Availability.EMPTY,
                                 Availability.UNINITIALIZED,
                             } for record in resources[1:1 + CONVOY_ITEM_COUNT]) or any(record["availability"] != Availability.AVAILABLE
                                                                                        for record in resources[1 + CONVOY_ITEM_COUNT:])):
        raise PlannerError("complete observation resource availability mismatch")

    flags = observation["flags"]
    flag_ids = tuple((record["kind"], record["flag_id"]) for record in flags)
    expected_flags = []
    for kind in (ValueKind.PERMANENT_FLAG, ValueKind.CHAPTER_FLAG):
        expected_flags.extend((kind, flag_id) for flag_id in range(sum(record["kind"] == kind for record in flags)))
    if flag_ids != tuple(expected_flags):
        raise PlannerError("complete observation flags are not canonical")

    if not page_values:
        return
    page_order = tuple(dict.fromkeys(PageKind(page["page_kind"]) for page in page_values))
    expected_order = (_PAGE_ORDER if strict else tuple(kind for kind in _PAGE_ORDER if kind in page_order))
    if (len(page_values) != observation["page_count"] or tuple(page["page_index"]
                                                               for page in page_values) != tuple(range(len(page_values))) or page_order != expected_order
            or tuple(_PAGE_ORDER.index(PageKind(page["page_kind"]))
                     for page in page_values) != tuple(sorted(_PAGE_ORDER.index(PageKind(page["page_kind"])) for page in page_values))
            or PageKind(page_values[0]["page_kind"]) is not PageKind.SUMMARY or page_values[0]["campaign"] != campaign or any(page["campaign"] is not None
                                                                                                                              for page in page_values[1:])):
        raise PlannerError("complete observation page sequence is not canonical")
    common = (
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
    if any(any(page[field] != observation[field] for field in common) for page in page_values):
        raise PlannerError("complete observation page state mismatch")
    for kind, name in _PAGE_COLLECTION.items():
        kind_pages = tuple(page for page in page_values if PageKind(page["page_kind"]) is kind)
        if not kind_pages and not strict:
            continue
        total = len(observation[name])
        capacity = _PAGE_RECORD_CAPACITIES[kind]
        expected_counts = ((0, ) if total == 0 else tuple(min(capacity, total - start) for start in range(0, total, capacity)))
        if (not kind_pages or kind is PageKind.SUMMARY and len(kind_pages) != 1 or tuple(page["record_count"] for page in kind_pages) != expected_counts
                or any(page["total_record_count"] != total for page in kind_pages) or any(page["record_count"] != len(page[name]) for page in kind_pages)
                or tuple(page["record_start"]
                         for page in kind_pages) != tuple(sum(previous["record_count"] for previous in kind_pages[:index]) for index in range(len(kind_pages)))
                or sum(page["record_count"] for page in kind_pages) != total or tuple(record for page in kind_pages
                                                                                      for record in page[name]) != tuple(observation[name])):
            raise PlannerError(f"complete observation {name} pages are not canonical")


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


def _decode_availability(value: int, context: str) -> Availability:
    try:
        return _AVAILABILITY_BY_VALUE[value]
    except KeyError as error:
        raise PlannerError(f"unknown {context} availability") from error


def _decode_assignment_source(value: int) -> AssignmentSource:
    try:
        return AssignmentSource(value)
    except ValueError as error:
        raise PlannerError("unknown campaign assignment source") from error


def parse_transport_observation(words: Iterable[int]) -> Observation:
    values = tuple(words)
    if len(values) != _OBSERVATION_WORD_COUNT:
        raise PlannerError("malformed fixed-width observation")
    if any(type(value) is not int or not 0 <= value <= 0xFFFFFFFF for value in values):
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
    if values[OBSERVATION_DIGEST_WORD] != wire_page_digest(values):
        raise PlannerError("planner observation page digest mismatch")
    if values[0] != 0x41504C4E or values[1] != PROTOCOL_VERSION:
        raise PlannerError("unexpected planner protocol identity")
    if values[2] > PAGE_MAX_BYTES or values[2] != _OBSERVATION_WORD_COUNT * 4:
        raise PlannerError("unexpected planner observation size")
    if (not 1 <= values[7] <= MAX_PAGE_COUNT or values[6] >= values[7]):
        raise PlannerError("planner page identity is outside v2 bounds")
    try:
        page_kind = _PAGE_KIND_BY_VALUE[values[8]]
    except KeyError as error:
        raise PlannerError("unknown planner page kind") from error
    record_start = values[9]
    record_count = values[10]
    total_records = values[11]
    total_actions = values[12]
    if (record_count > _PAGE_RECORD_CAPACITIES[page_kind] or total_records > _PAGE_TOTAL_LIMITS[page_kind] or record_start + record_count > total_records
            or total_actions > MAX_ACTIONS
            or (page_kind is not PageKind.CONTROL and record_count == 0 and not (page_kind is PageKind.FLAGS and record_start == 0 and total_records == 0))):
        raise PlannerError("planner page bounds are inconsistent")

    payload = values[
        _OBSERVATION_HEADER_WORDS:OBSERVATION_DIGEST_WORD]
    fields: list[Field] = []
    map_cells: list[MapCell] = []
    units: list[UnitRecord] = []
    inventory_records: list[InventoryRecord] = []
    resource_records: list[ResourceRecord] = []
    flag_records: list[FlagRecord] = []
    actions: list[ActionRecord] = []
    campaign = None
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
            fields.append(Field(
                name,
                source,
                bound,
                availability,
                payload[index * 2 + 1] if availability is Availability.AVAILABLE else None,
            ))
        availability_words = payload[16]
        counts = payload[18]
        objective_count = counts & 0xFF
        group_count = (counts >> 8) & 0xFF
        strategy_count = (counts >> 16) & 0xFF
        assignment_count = counts >> 24
        if (objective_count > 8 or group_count > 8 or strategy_count > 8 or assignment_count > 17):
            raise PlannerError("campaign summary exceeds fixed capacities")
        objectives = []
        for index in range(objective_count):
            start = 24 + index * 8
            record = payload[start:start + 8]
            objectives.append(
                ObjectiveRecord(
                    record[0],
                    record[1],
                    record[2],
                    record[3] & 0xFFFF,
                    record[3] >> 16,
                    record[4] & 0xFFFF,
                    record[4] >> 16,
                    record[5] & 0xFFFF,
                    (record[5] >> 16) & 0xFF,
                    record[5] >> 24,
                    (record[6] & 0xFF, (record[6] >> 8) & 0xFF, (record[6] >> 16) & 0xFF, record[6] >> 24),
                    record[7] & 0xFF,
                    record[7] >> 16,
                    _decode_availability((record[7] >> 8) & 0xFF, "objective"),
                ))
        groups = []
        for index in range(group_count):
            start = 88 + index * 6
            record = payload[start:start + 6]
            member_count = record[1] & 0xFF
            if member_count > 16:
                raise PlannerError("campaign group exceeds fixed capacity")
            members = tuple((record[2 + member // 4] >> (member % 4 * 8)) & 0xFF for member in range(member_count))
            groups.append(GroupRecord(
                record[0],
                members,
                _decode_availability(record[1] >> 24, "objective group"),
            ))
        strategies = []
        for index in range(strategy_count):
            start = 136 + index * 4
            record = payload[start:start + 4]
            strategies.append(StrategyRecord(
                record[0],
                record[1],
                record[2],
                record[3] & 0xFF,
                _decode_availability(record[3] >> 24, "strategy"),
            ))
        assignments = []
        for index in range(assignment_count):
            start = 168 + index * 3
            identity, subject, strategy = payload[start:start + 3]
            assignments.append(
                AssignmentRecord(
                    _decode_assignment_source((identity >> 16) & 0xF),
                    subject,
                    strategy,
                    identity & 0xFFFF,
                    (identity >> 20) & 1,
                    (identity >> 21) & 1,
                    _decode_availability(identity >> 24, "assignment"),
                ))
        current = payload[22]
        campaign = CampaignRecord(
            payload[17] & 0xFF,
            (payload[17] >> 8) & 0xFF,
            (payload[17] >> 16) & 0xFF,
            _decode_availability(availability_words & 0xFF, "phase"),
            _decode_availability((availability_words >> 8) & 0xFF, "objective"),
            _decode_availability((availability_words >> 16) & 0xFF, "strategy"),
            _decode_availability(availability_words >> 24, "assignment"),
            payload[19],
            payload[20],
            payload[21],
            current & 0xFF,
            _decode_assignment_source((current >> 8) & 0xFF),
            payload[23],
            _decode_availability((current >> 16) & 0xFF, "current assignment"),
            tuple(objectives),
            tuple(groups),
            tuple(strategies),
            tuple(assignments),
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
            map_cells.append(MapCell(
                encoded & 0x3F,
                (encoded >> 6) & 0x3F,
                (encoded >> 12) & 0xFF,
                (encoded >> 20) & 0xFF,
                availability,
            ))
    elif page_kind is PageKind.UNITS:
        if record_count * 10 > len(payload):
            raise PlannerError("unit page exceeds fixed payload")
        for index in range(record_count):
            (
                identity,
                position,
                state,
                inventory,
                status,
                rescue,
                stats0,
                stats1,
                ranks0,
                ranks1,
            ) = payload[index * 10:index * 10 + 10]
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
                    status_index=status & 0xF,
                    status_duration=(status >> 4) & 0xF,
                    deployed=(status >> 24) & 1,
                    dead=(status >> 25) & 1,
                    moved=(status >> 26) & 1,
                    acted=(status >> 27) & 1,
                    rescued=(status >> 28) & 1,
                    rescuing=(status >> 29) & 1,
                    rescue_partner=rescue & 0xFF,
                    equipped_slot=_decode_optional_item_slot((rescue >> 8) & 0xFF),
                    equipped_item=rescue >> 16,
                    level=(status >> 8) & 0xFF,
                    exp=(status >> 16) & 0xFF,
                    power=stats0 & 0xFF,
                    skill=(stats0 >> 8) & 0xFF,
                    speed=(stats0 >> 16) & 0xFF,
                    luck=stats0 >> 24,
                    defense=stats1 & 0xFF,
                    resistance=(stats1 >> 8) & 0xFF,
                    constitution=(stats1 >> 16) & 0xFF,
                    movement=stats1 >> 24,
                    weapon_ranks=tuple((ranks0 >> (rank * 8))
                                       & 0xFF if rank < 4 else (ranks1 >> ((rank - 4) * 8)) & 0xFF for rank in range(8)),
                ))
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
            ) = payload[index * 10:index * 10 + 10]
            if kind not in _ACTION_KIND_BY_VALUE:
                raise PlannerError("unknown planner action kind")
            if target >> 24 or item_slot >> 16:
                raise PlannerError("planner action reserved bits are nonzero")
            action_values = {
                "kind": _ACTION_KIND_BY_VALUE[kind],
                "actor": actor,
                "destination": [destination & 0xFFFF, destination >> 16],
                "target": target & 0xFF,
                "item_slot": _decode_optional_item_slot(item_slot & 0xFF),
                "target_position": [
                    (target >> 8) & 0xFF,
                    (target >> 16) & 0xFF,
                ],
                "action_id": action_id,
                "target_item_slot": _decode_optional_item_slot((item_slot >> 8) & 0xFF),
            }
            token_values = {
                "word0": token0,
                "word1": token1,
                "word2": token2,
                "word3": token3,
            }
            _validate_action_contract(action_values, "live action")
            _validate_token_schema(token_values, "live action token")
            action_values["destination"] = tuple(action_values["destination"])
            action_values["target_position"] = tuple(action_values["target_position"])
            actions.append(ActionRecord(
                record_start + index,
                Action(**action_values),
                OpaqueToken(**token_values),
            ))
    elif page_kind is PageKind.INVENTORY:
        if record_count * 2 > len(payload):
            raise PlannerError("inventory page exceeds fixed payload")
        for index in range(record_count):
            identity, raw_item = payload[index * 2:index * 2 + 2]
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
                raise PlannerError("inventory slot exceeds fixed unit capacity: "
                                   f"identity={identity:#010x}, slot={slot}")
            item_id, uses = _decode_item(raw_item)
            inventory_records.append(InventoryRecord(
                unit,
                slot,
                item_id,
                uses,
                raw_item,
                availability,
            ))
    elif page_kind is PageKind.RESOURCES:
        if record_count * 2 > len(payload):
            raise PlannerError("resource page exceeds fixed payload")
        for index in range(record_count):
            identity, value = payload[index * 2:index * 2 + 2]
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
                resource_records.append(ResourceRecord(
                    kind,
                    None,
                    value,
                    None,
                    None,
                    availability,
                ))
            elif kind is ValueKind.CONVOY_ITEM:
                if value_index >= CONVOY_ITEM_COUNT:
                    raise PlannerError("convoy slot exceeds fixed capacity")
                item_id, uses = _decode_item(value)
                resource_records.append(ResourceRecord(
                    kind,
                    value_index,
                    value,
                    item_id,
                    uses,
                    availability,
                ))
            elif kind is ValueKind.AUTOPLAY_TELEMETRY:
                resource_records.append(ResourceRecord(
                    kind,
                    value_index,
                    value,
                    None,
                    None,
                    availability,
                ))
            else:
                raise PlannerError("invalid resource value kind")
    elif page_kind is PageKind.FLAGS:
        if record_count * 2 > len(payload):
            raise PlannerError("flag page exceeds fixed payload")
        for index in range(record_count):
            identity, value = payload[index * 2:index * 2 + 2]
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
            flag_records.append(FlagRecord(
                kind,
                (identity >> 8) & 0xFFFF,
                value if availability is Availability.AVAILABLE else None,
                availability,
            ))
    if page_kind is PageKind.SUMMARY:
        reserved = (
            payload[24 + objective_count * 8:88]
            + payload[88 + group_count * 6:136]
            + payload[136 + strategy_count * 4:168]
            + payload[168 + assignment_count * 3:])
    else:
        used_words = {
            PageKind.CONTROL: 0,
            PageKind.MAP: record_count,
            PageKind.UNITS: record_count * 10,
            PageKind.ACTIONS: record_count * 10,
            PageKind.INVENTORY: record_count * 2,
            PageKind.RESOURCES: record_count * 2,
            PageKind.FLAGS: record_count * 2,
        }[page_kind]
        reserved = payload[used_words:]
    if any(reserved):
        raise PlannerError("planner observation reserved payload is nonzero")
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
        campaign=campaign,
    )


def _assemble_observation_pages(
    pages: Iterable[Observation],
    validation_mode: ValidationMode = ValidationMode.SYNTHETIC,
) -> Observation:
    page_values = tuple(pages)
    if not page_values:
        raise PlannerError("planner observation has no pages")
    first = page_values[0]
    complete = replace(
        first,
        fields=tuple(field for page in page_values for field in page.fields),
        map_cells=tuple(cell for page in page_values for cell in page.map_cells),
        units=tuple(unit for page in page_values for unit in page.units),
        inventory=tuple(record for page in page_values for record in page.inventory),
        resources=tuple(record for page in page_values for record in page.resources),
        flags=tuple(record for page in page_values for record in page.flags),
        actions=tuple(action for page in page_values for action in page.actions),
    )
    _validate_complete_observation(
        asdict(complete),
        (asdict(page) for page in page_values),
        validation_mode,
    )
    return complete


def collect_observation_pages(transport: object, first: Observation) -> Observation:
    transcript = getattr(transport, "transcript", None)
    transcript_snapshot = (
        transcript.snapshot()
        if isinstance(transcript, PlannerTranscript) else None)
    snapshot_state = getattr(transport, "snapshot_collection_state", None)
    state_snapshot = snapshot_state() if callable(snapshot_state) else None
    try:
        if (not 1 <= first.page_count <= MAX_PAGE_COUNT
                or first.page_index != 0
                or first.page_count * PAGE_MAX_BYTES > MAX_SEARCH_BYTES):
            raise PlannerError("planner page traversal exceeds host bounds")
        pages = [first]
        for page_index in range(1, first.page_count):
            page = transport.exchange(Command(
                CommandKind.PAGE, first.run_id, first.observation_id,
                page_index=page_index))
            if not isinstance(page, Observation):
                raise PlannerError("PAGE did not return an observation")
            if (page.run_id != first.run_id
                    or page.observation_id != first.observation_id
                    or page.page_index != page_index
                    or page.page_count != first.page_count):
                raise PlannerError(Rejection.STALE_OBSERVATION.value)
            pages.append(page)
        validation_mode = getattr(
            transport, "validation_mode", ValidationMode.SYNTHETIC)
        if not isinstance(validation_mode, ValidationMode):
            raise PlannerError("invalid trusted transport validation mode")
        complete = _assemble_observation_pages(pages, validation_mode)
        record_complete = getattr(
            transport, "record_complete_observation", None)
        if callable(record_complete):
            record_complete(complete)
        return complete
    except Exception:
        if transcript_snapshot is not None:
            transcript.restore(transcript_snapshot)
        restore_state = getattr(transport, "restore_collection_state", None)
        if callable(restore_state) and state_snapshot is not None:
            restore_state(state_snapshot)
        raise


def replay_transcript_on_clean_transport(
    data: bytes,
    transport_factory: Callable[[], object],
    validation_mode: ValidationMode = ValidationMode.PRODUCTION,
    scenario_namespace: int = SCENARIO_NAMESPACE,
) -> bytes:
    expected = PlannerTranscript.import_bytes(
        data, validation_mode, scenario_namespace)
    transport = transport_factory()
    pages: dict[tuple[int, int], dict[int, Observation]] = {}

    class CapturedPageTransport:

        def __init__(
            self,
            captured: dict[int, Observation],
            mode: ValidationMode,
        ) -> None:
            self.captured = captured
            self.validation_mode = mode

        def exchange(self, command: Command) -> Observation:
            return self.captured[command.page_index]

    try:
        try:
            for event in expected.events:
                event_kind = event["event"]
                response = None
                if event_kind == "command":
                    command = event["command"]
                    kind = command["kind"]
                    if kind == CommandKind.START.value:
                        response = transport.start(scenario_identity=command["expected_identities"][2], )
                    elif kind == CommandKind.PAGE.value:
                        response = transport.exchange(
                            Command(
                                CommandKind.PAGE,
                                command["run_id"],
                                command["observation_id"],
                                page_index=command["page_index"],
                            ))
                    elif kind == CommandKind.COMMIT.value:
                        response = transport.exchange(
                            Command(
                                CommandKind.COMMIT,
                                command["run_id"],
                                command["observation_id"],
                                command["action_ordinal"],
                                OpaqueToken(**command["token"]),
                            ))
                    elif kind == CommandKind.CANCEL.value:
                        response = transport.exchange(Command(
                            CommandKind.CANCEL,
                            command["run_id"],
                            command["observation_id"],
                        ))
                    else:
                        raise PlannerError("transcript contains an unsupported command")
                    if isinstance(response, Observation):
                        key = (response.run_id, response.observation_id)
                        pages.setdefault(key, {})[response.page_index] = response
                elif event_kind == "observation_complete":
                    identity = event["page_identity"]
                    key = (identity[0], identity[1])
                    captured = pages.pop(key, {})
                    if set(captured) != set(range(identity[2])):
                        raise PlannerError("clean replay did not capture every observation page")
                    complete = collect_observation_pages(
                        CapturedPageTransport(captured, validation_mode),
                        captured[0],
                    )
                    transport.record_complete_observation(complete)
        except PlannerTransportFailure as error:
            if (expected.events[-1]["event"] != "transport_error" or transport.transcript.export() != data):
                raise PlannerError("clean transport error replay mismatch") from error
            return data
        actual = transport.transcript.export()
        if actual != data:
            raise PlannerError("clean transport transcript replay mismatch")
        return actual
    finally:
        close = getattr(transport, "close", None)
        if callable(close):
            close()


def _consume_semantic_observation(observation: Observation) -> str:
    if (len(observation.map_cells) > MAX_MAP_CELLS or len(observation.units) > MAX_UNITS or len(observation.inventory) > MAX_UNITS * UNIT_ITEM_COUNT
            or len(observation.resources) > 1 + CONVOY_ITEM_COUNT + AUTOPLAY_TELEMETRY_WORDS or len(observation.flags) > 2 * 256 * 8
            or len(observation.actions) > MAX_ACTIONS):
        raise PlannerError(Rejection.RESOURCE_LIMIT.value)
    _validate_complete_observation(asdict(observation))
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
        candidates = observation.actions[:self.max_nodes]
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
    """Run the deterministic two-chapter in-memory compatibility fixture."""
    bridge = PlannerBridge(provenance)
    run_id = bridge.begin(provenance)
    first = bridge.observe(1, (
        Field("chapter", "PlaySt.chapterIndex", 0xFF, Availability.AVAILABLE, 1),
        Field("campaign_flag", "event_flag", 1, Availability.AVAILABLE, 0),
        Field("rng", "rng.c", 3, Availability.AVAILABLE, (1, 2, 3)),
    ), (
        Action("MOVE_WAIT", 1, (1, 0)),
        Action("COMBAT", 1, (2, 0), target=0x81, item_slot=0),
    ))
    first_choice = planner.choose(collect_observation_pages(bridge, first))
    bridge.commit(Command(
        CommandKind.COMMIT,
        run_id,
        first.observation_id,
        first_choice.ordinal,
        first_choice.token,
    ))
    semantic_state = {
        "accepted_token": asdict(first_choice.token),
        "casualties": {
            "blue": 0,
            "green": 0,
            "red": 0
        },
        "chapter": 2,
        "chapter_turn": 1,
        "flags": {
            "objective_complete": False,
            "village_saved": True
        },
        "inventory": ("fixture-key", ),
        "objectives": {
            "kind": "seize",
            "progress": 1
        },
        "promotions": (),
        "recruitment": ("unit-1", ),
        "resources": {
            "gold": 1000
        },
        "roster": ("unit-1", "unit-2"),
        "rng": (1, 2, 3),
        "trace_digest": bridge.trace_digest(),
    }
    checkpoint = {
        **semantic_state,
        "semantic_state_digest": semantic_state_digest(semantic_state),
    }
    second = bridge.observe(2, (
        Field("chapter", "PlaySt.chapterIndex", 0xFF, Availability.AVAILABLE, 2),
        Field("campaign_checkpoint", "normal_chapter_transition", 1, Availability.AVAILABLE, checkpoint),
    ), (Action("MOVE_WAIT", 1, (0, 0)), ))
    second_choice = planner.choose(collect_observation_pages(bridge, second))
    bridge.commit(Command(
        CommandKind.COMMIT,
        run_id,
        second.observation_id,
        second_choice.ordinal,
        second_choice.token,
    ))
    return {
        "campaign_checkpoint": checkpoint,
        "run_id": run_id,
        "terminal": "success",
        "trace": bridge.trace,
        "trace_digest": bridge.trace_digest(),
    }
