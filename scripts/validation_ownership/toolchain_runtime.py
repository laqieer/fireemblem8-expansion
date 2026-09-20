"""The original toolchain check's closed grammar and single-dispatch authority."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
from threading import get_ident
import weakref

if __package__:
    from .authority import ENVIRONMENT, encoded, relative_path
    from .budget import MakeProbeError
    from .producer_channel import ChannelError
    from .private_install import directory_identity, launch_scope
    from . import arm_headers
else:
    from authority import ENVIRONMENT, encoded, relative_path
    from budget import MakeProbeError
    from producer_channel import ChannelError
    from private_install import directory_identity, launch_scope
    import arm_headers


CONTRACT = "modern-toolchain-dry-run-recipe"
TARGET = "expansion-modern-toolchain-check"
STAGES = ("version", "target", "assembler", "syntax", "compile")
QUERIES = ("--version", "-dumpmachine", "-print-prog-name=as")
SYNTAX_INPUT = '#include "global.h"\n'
COMPILE_INPUT = "void modern_arm7tdmi_thumb_probe(void) {}\n"
INPUTS = ("", "", "", SYNTAX_INPUT, COMPILE_INPUT)
EXEC_PREFIX = "toolchain-exec:"
INPUT_PREFIX = "toolchain-stdin:"
INTERMEDIATE_PREFIX = "toolchain-intermediate:"
INTERMEDIATE_RECORD_LIMIT = 65536
INTERMEDIATE_NODE_LIMIT = 512
INTERMEDIATE_DEPTH_LIMIT = 10
INTERMEDIATE_PATH_LIMIT = 4096
COMPILE_ARG_LIMIT = 128
COMPILE_ARG_BYTES_LIMIT = 65536
NATIVE_PURPOSE = "toolchain-step-v2"
LAUNCH_FIELDS = (
    "root", "mode", "argv", "environment", "code", "sources", "enumerations",
    "executables", "mounts", "dependency",
)
SYNTAX_FAILURE = (
    "error: modern GCC cannot parse include/global.h and its standard headers.\n"
    "Install newlib headers (Ubuntu: libnewlib-arm-none-eabi) or set MODERN_NEWLIB_INCLUDE.\n"
)
COMPILE_FAILURE = (
    "error: ARM7TDMI Thumb/interwork compile probe failed.\n"
    "Check the GCC/binutils pairing and MODERN_BINUTILS_DIR.\n"
)


@dataclass(frozen=True)
class ArgOperand:
    role: str
    kind: str
    exec_sequence: int
    argv_index: int
    value: str


@dataclass(frozen=True)
class CompileRoles:
    creator_sequence: int
    writer_sequence: int | None
    reader_sequence: int | None
    output: ArgOperand | None
    input: ArgOperand | None


@dataclass(frozen=True)
class IntermediateLimits:
    file_limit: int
    observation_count: int
    observation_limit: int
    write_limit: int
    creation_limit: int
    process_limit: int
    memory_limit: int
    syscall_limit: int
    deadline: int | float

    def __post_init__(self):
        values = (
            self.file_limit, self.observation_count, self.observation_limit,
            self.write_limit, self.creation_limit, self.process_limit,
            self.memory_limit, self.syscall_limit,
        )
        if (
            any(type(value) is not int or not 0 <= value < 1 << 64 for value in values)
            or type(self.deadline) not in (int, float) or not math.isfinite(self.deadline)
            or self.deadline <= 0
        ):
            raise MakeProbeError("toolchain intermediate limits are outside their issued domains")


def _u64(value, boundary, *, positive=False):
    if type(value) is not int or not 0 <= value < 1 << 64 or positive and value == 0:
        raise MakeProbeError(f"toolchain intermediate {boundary} is not a bounded unsigned integer")
    return value


def _s64(value, boundary):
    if type(value) is not int or not -(1 << 63) <= value < 1 << 63:
        raise MakeProbeError(f"toolchain intermediate {boundary} is not a bounded signed result")
    return value


def _exact(value, keys, boundary):
    if type(value) is not dict or set(value) != set(keys):
        raise MakeProbeError(f"toolchain intermediate {boundary} has a foreign schema")
    return value


def _sha256(value, boundary):
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise MakeProbeError(f"toolchain intermediate {boundary} is not an exact SHA-256")
    return value


def _bounded_text(value, boundary, *, limit=INTERMEDIATE_PATH_LIMIT):
    if type(value) is not str:
        raise MakeProbeError(f"toolchain intermediate {boundary} is not bounded text")
    if not value or len(value) > limit:
        raise MakeProbeError(f"toolchain intermediate {boundary} exceeds its byte bound")
    if "\0" in value or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise MakeProbeError(f"toolchain intermediate {boundary} is not bounded text")
    _string_bytes(value, "toolchain intermediate " + boundary, limit)
    return value


def _string_bytes(value, boundary, limit):
    size = 0
    for character in value:
        code = ord(character)
        if 0xD800 <= code <= 0xDFFF:
            raise MakeProbeError(f"{boundary} is not strict UTF-8")
        size += 1 if code < 0x80 else 2 if code < 0x800 else 3 if code < 0x10000 else 4
        if size > limit:
            raise MakeProbeError(f"{boundary} exceeds its byte bound")
    return size


def _intermediate_path(value):
    value = _bounded_text(value, "path")
    path = PurePosixPath(value)
    if not value.startswith("/") or str(path) != value or path.parent != PurePosixPath("/work"):
        raise MakeProbeError("toolchain intermediate path is not one canonical workspace child")
    return value


def _identity(value, boundary):
    if type(value) is not list or len(value) != 7:
        raise MakeProbeError(f"toolchain intermediate {boundary} is not a full object identity")
    result = tuple(_u64(item, boundary) for item in value)
    if result[2] != stat.S_IFREG | 0o600:
        raise MakeProbeError(f"toolchain intermediate {boundary} is not the closed regular 0600 object")
    return result


def _retirement_progression(before, after):
    return after[:5] == before[:5] and after[5] != before[5] and after[6] == 0


def _same_live_object(first, second, *, size):
    return (
        second[:3] == first[:3] and second[3] == size and second[6] == 1
    )


def _execution_row(row):
    if (
        type(row) is not dict or len(row) != 6
        or set(row) != {"stage", "sequence", "path", "identity", "argv", "environment"}
        or type(row["stage"]) is not str or row["stage"] != "compile"
        or type(row["sequence"]) is not int or not 1 <= row["sequence"] <= 3
        or type(row["path"]) is not str
        or type(row["identity"]) is not list or len(row["identity"]) != 6
        or any(type(value) is not int or not 0 <= value < 1 << 64 for value in row["identity"])
        or type(row["argv"]) is not list or not 1 <= len(row["argv"]) <= COMPILE_ARG_LIMIT
        or any(type(value) is not str or "\0" in value for value in row["argv"])
        or type(row["environment"]) is not dict
        or any(type(key) is not str or type(value) is not str for key, value in row["environment"].items())
    ):
        raise MakeProbeError("toolchain compile role actor has malformed execution data")
    _bounded_text(row["path"], "executable path")
    if sum(
        _string_bytes(value, "toolchain compile argv", INTERMEDIATE_PATH_LIMIT)
        for value in row["argv"]
    ) > COMPILE_ARG_BYTES_LIMIT:
        raise MakeProbeError("toolchain compile argv exceeds its aggregate byte bound")
    return row


def _execution_rows(executions, profile, parent_argv, *, complete):
    if (
        type(complete) is not bool or type(executions) not in (list, tuple)
        or type(profile) is not dict
        or type(profile.get("version")) is not int or profile.get("version") != 2
        or type(profile.get("stage")) is not int or profile.get("stage") != 4
        or type(profile.get("images")) is not list or len(profile["images"]) != 3
        or type(parent_argv) not in (list, tuple)
        or not 1 <= len(parent_argv) <= COMPILE_ARG_LIMIT
        or any(type(value) is not str or "\0" in value for value in parent_argv)
    ):
        raise MakeProbeError("toolchain compile roles lack their prospective version-2 execution profile")
    if not 1 <= len(executions) <= 3 or complete and len(executions) != 3:
        raise MakeProbeError("toolchain compile roles omit or add an execution actor")
    rows = []
    for sequence, row in enumerate(executions, 1):
        row = _execution_row(row)
        image = profile["images"][sequence - 1]
        if (
            row["sequence"] != sequence
            or type(image) is not list or len(image) != 7
            or type(image[0]) is not str or row["path"] != image[0]
            or any(type(value) is not int or not 0 <= value < 1 << 64 for value in image[1:])
            or row["identity"] != image[1:]
        ):
            raise MakeProbeError("toolchain compile role actor differs from its authenticated execution row")
        rows.append(row)
    if rows[0]["argv"] != list(parent_argv) or rows[0]["path"] != rows[0]["argv"][0]:
        raise MakeProbeError("toolchain compile role driver differs from its exact parent argv")
    return tuple(rows)


def _operand_argv(row, actor, *, system):
    zero = {
        "cc1": {
            "-quiet", "-mthumb", "-mthumb-interwork", "-ffreestanding", "-fno-pic", "-fno-pie",
        },
        "assembler": {"-mthumb", "-mthumb-interwork"},
    }[actor]
    separate = {
        "cc1": {"-imultilib", "-isystem", "-dumpbase", "-dumpbase-ext", "-o"},
        "assembler": {"-o"},
    }[actor]
    self_contained = {
        "cc1": ("-D", "-mcpu=", "-march=", "-mfloat-abi=", "-mlibarch=", "-mabi="),
        "assembler": ("-march=", "-mcpu=", "-mfpu=", "-mfloat-abi=", "-meabi="),
    }[actor]
    argv = row["argv"]
    positionals, outputs, seen = [], [], set()
    index = 1
    while index < len(argv):
        word = argv[index]
        if word.startswith("@") or word == "--":
            raise MakeProbeError(f"toolchain {actor} role grammar forbids response or alternate option syntax")
        if word in zero:
            if word != "-quiet" and word in seen:
                raise MakeProbeError(f"toolchain {actor} role grammar has a repeated option")
            seen.add(word)
            index += 1
            continue
        if word in separate:
            if word in seen or index + 1 >= len(argv):
                raise MakeProbeError(f"toolchain {actor} role grammar has a missing or repeated option value")
            value = argv[index + 1]
            if not value or value.startswith("@") or "\0" in value:
                raise MakeProbeError(f"toolchain {actor} role grammar has an invalid option value")
            if word == "-isystem" and value not in system:
                raise MakeProbeError("toolchain cc1 search root differs from its parent-admitted SDK")
            seen.add(word)
            if word == "-o":
                outputs.append((index + 1, value))
            index += 2
            continue
        option = next((prefix for prefix in self_contained if word.startswith(prefix)), None)
        if option is not None:
            if len(word) == len(option) or option != "-D" and option in seen:
                raise MakeProbeError(f"toolchain {actor} role grammar has an empty or repeated attached value")
            seen.add(option)
            index += 1
            continue
        if word.startswith("-") and word != "-":
            raise MakeProbeError(f"toolchain {actor} role grammar has an unknown option or arity")
        positionals.append((index, word))
        index += 1
    if len(outputs) != 1 or len(positionals) != 1:
        raise MakeProbeError(f"toolchain {actor} role grammar lacks one output and one positional input")
    return outputs[0], positionals[0]


def compile_operand_roles(executions, profile, parent_argv, *, complete):
    rows = _execution_rows(executions, profile, parent_argv, complete=complete)
    system = options(parent_argv[1:], syntax=False)[1]
    output = input_operand = None
    if len(rows) >= 2:
        (output_index, output_value), (source_index, source) = _operand_argv(
            rows[1], "cc1", system=system,
        )
        if source != "-":
            raise MakeProbeError("toolchain cc1 role grammar lost its sole standard-input source")
        output_value = _intermediate_path(output_value)
        output = ArgOperand("stage4-assembly", "output", 2, output_index, output_value)
    if len(rows) >= 3:
        (assembler_output_index, assembler_output), (input_index, input_value) = _operand_argv(
            rows[2], "assembler", system=system,
        )
        if assembler_output != "/dev/null" or input_value != output.value:
            raise MakeProbeError("toolchain assembler role grammar lost its exact output or writer input")
        input_operand = ArgOperand("stage4-assembly", "input", 3, input_index, input_value)
    if output is not None:
        occurrences = [
            (row["sequence"], index)
            for row in rows
            for index, value in enumerate(row["argv"])
            if output.value in value
        ]
        expected = [(2, output.argv_index)]
        if input_operand is not None:
            expected.append((3, input_operand.argv_index))
        if occurrences != expected or any(
            output.value in key or output.value in value
            for row in rows
            for key, value in row["environment"].items()
        ):
            raise MakeProbeError("toolchain intermediate path appears outside its two parsed operand roles")
    return CompileRoles(
        1, 2 if output is not None else None, 3 if input_operand is not None else None,
        output, input_operand,
    )


def _json_shape(value, *, node_limit=INTERMEDIATE_NODE_LIMIT):
    depth = nodes = index = 0
    while index < len(value):
        character = value[index]
        if character == '"':
            nodes += 1
            index += 1
            while index < len(value):
                if value[index] == "\\":
                    index += 2
                elif value[index] == '"':
                    index += 1
                    break
                else:
                    index += 1
            else:
                raise MakeProbeError("toolchain intermediate record has an unterminated JSON string")
        elif character in "[{":
            depth += 1
            nodes += 1
            if depth > INTERMEDIATE_DEPTH_LIMIT:
                raise MakeProbeError("toolchain intermediate record exceeds its nesting bound")
            index += 1
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise MakeProbeError("toolchain intermediate record has invalid container nesting")
            index += 1
        elif character == "-" or character.isdigit() or character in "tfn":
            nodes += 1
            index += 1
            while index < len(value) and value[index] not in " \t\r\n,]}":
                index += 1
        else:
            index += 1
        if nodes > node_limit:
            raise MakeProbeError("toolchain intermediate record exceeds its node bound")
    if depth:
        raise MakeProbeError("toolchain intermediate record has incomplete container nesting")
    return nodes


def _decode_intermediate(payload, reserve):
    if type(payload) is not str or not payload.isascii():
        raise MakeProbeError("toolchain intermediate record is not its bounded ASCII JSON wire")
    if not payload or len(payload) + len(INTERMEDIATE_PREFIX) > INTERMEDIATE_RECORD_LIMIT:
        raise MakeProbeError("toolchain intermediate record exceeds its wire bound")
    reserve(len(payload) + 4096)
    nodes = _json_shape(payload)
    # Decoded text may use four-byte characters; pairs, memo and containers coexist.
    reserve(4 * len(payload) + 1024 * nodes + 8192)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise MakeProbeError("toolchain intermediate record has duplicate JSON fields")
            result[key] = value
        return result

    def constant(value):
        raise MakeProbeError("toolchain intermediate record has a non-finite JSON value")

    try:
        row = json.loads(payload, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, RecursionError) as error:
        raise MakeProbeError("toolchain intermediate record is malformed JSON") from error
    reserve(_json_cost(row))
    canonical = encoded(row)
    if len(canonical) + len(INTERMEDIATE_PREFIX) > INTERMEDIATE_RECORD_LIMIT:
        raise MakeProbeError("toolchain intermediate canonical record exceeds its wire bound")
    return row, canonical


def _json_cost(value, state=None, depth=0, *, node_limit=4096, work=None):
    if work is not None:
        work(128 + (4 * len(value) if type(value) is str else 0))
    if state is None:
        state = [0, 8192]
    if depth > INTERMEDIATE_DEPTH_LIMIT:
        raise MakeProbeError("toolchain data exceeds its bounded nesting")
    state[0] += 1
    if state[0] > node_limit:
        raise MakeProbeError("toolchain data exceeds its bounded node count")
    state[1] += 1024
    if value is None or type(value) is bool:
        state[1] += 32
    elif type(value) is int:
        if not -(1 << 63) <= value < 1 << 64:
            raise MakeProbeError("toolchain data integer exceeds its bounded domain")
        state[1] += 32
    elif type(value) is float:
        if not math.isfinite(value):
            raise MakeProbeError("toolchain data contains a non-finite scalar")
        state[1] += 32
    elif type(value) is str:
        # Escaped encoder fragments, joined text, bytes and copied data can overlap.
        state[1] += 24 * _string_bytes(value, "toolchain data text", 65536) + 128
    elif type(value) in (list, tuple):
        state[1] += 64 * len(value)
        for item in value:
            _json_cost(item, state, depth + 1, node_limit=node_limit, work=work)
    elif type(value) is dict:
        state[1] += 128 * len(value)
        for key, item in value.items():
            if type(key) is not str:
                raise MakeProbeError("toolchain data has a non-text dictionary key")
            if work is not None:
                work(128 + 4 * len(key))
            state[1] += 24 * _string_bytes(key, "toolchain data key", 65536) + 128
            _json_cost(item, state, depth + 1, node_limit=node_limit, work=work)
    else:
        raise MakeProbeError("toolchain data has an unsupported value")
    return state[1]


def _operation(value, keys, boundary):
    row = _exact(value, keys, boundary)
    row["order"] = _u64(row["order"], boundary + " order", positive=True)
    row["syscall_sequence"] = _u64(row["syscall_sequence"], boundary + " syscall sequence", positive=True)
    if type(row["syscall"]) is not str:
        raise MakeProbeError(f"toolchain intermediate {boundary} syscall is not text")
    return row


def _actor_map(record, executions):
    actors = record["actors"]
    if type(actors) is not list or len(actors) != 3:
        raise MakeProbeError("toolchain intermediate actor table is incomplete")
    result = {}
    for index, actor in enumerate(actors, 1):
        actor = _exact(actor, {"exec_sequence", "pid", "birth_sequence", "exec_record_sha256"}, "actor")
        sequence = _u64(actor["exec_sequence"], "actor execution sequence", positive=True)
        pid = _u64(actor["pid"], "actor pid", positive=True)
        birth = _u64(actor["birth_sequence"], "actor birth sequence", positive=True)
        if sequence != index or sequence in result:
            raise MakeProbeError("toolchain intermediate actors are not the exact ordered execution roles")
        digest = _sha256(actor["exec_record_sha256"], "actor execution record")
        if executions is not None and digest != hashlib.sha256(encoded(executions[index - 1])).hexdigest():
            raise MakeProbeError("toolchain intermediate actor digest differs from its raw execution record")
        result[sequence] = pid, birth
    if len({value[0] for value in result.values()}) != 3 or len({value[1] for value in result.values()}) != 3:
        raise MakeProbeError("toolchain intermediate actors reuse a pid or birth identity")
    return result


def _flags(value, boundary, access, required, optional):
    flags = _u64(value, boundary + " flags")
    allowed = required | optional | access
    if flags & os.O_ACCMODE != access or flags & required != required or flags & ~allowed:
        raise MakeProbeError(f"toolchain intermediate {boundary} flags are outside the closed access form")
    return flags


def _receipt_data(record, roles, *, profile=None, launch=None, executions=None, limits=None):
    if (
        type(roles) is not CompileRoles
        or type(roles.creator_sequence) is not int or roles.creator_sequence != 1
        or type(roles.writer_sequence) is not int or roles.writer_sequence != 2
        or type(roles.reader_sequence) is not int or roles.reader_sequence != 3
        or type(roles.output) is not ArgOperand or type(roles.input) is not ArgOperand
    ):
        raise MakeProbeError("toolchain intermediate receipt lacks complete parsed operand roles")
    for operand, kind, sequence in ((roles.output, "output", 2), (roles.input, "input", 3)):
        if (
            type(operand.role) is not str or operand.role != "stage4-assembly"
            or type(operand.kind) is not str or operand.kind != kind
            or type(operand.exec_sequence) is not int or operand.exec_sequence != sequence
            or type(operand.argv_index) is not int or not 0 <= operand.argv_index < COMPILE_ARG_LIMIT
            or type(operand.value) is not str
        ):
            raise MakeProbeError("toolchain intermediate receipt has malformed operand role data")
    record = _exact(record, {
        "version", "scope", "binding", "stage", "role", "path", "workspace", "actors",
        "creation", "creator_close", "writer", "reader", "retirement", "driver_exit", "complete",
    }, "record")
    path = _intermediate_path(record["path"])
    if (
        type(record["version"]) is not int or record["version"] != 1
        or record["stage"] != "compile" or record["role"] != "stage4-assembly"
        or type(record["complete"]) is not bool or not record["complete"]
        or path != roles.output.value or path != roles.input.value
    ):
        raise MakeProbeError("toolchain intermediate record has a foreign version, stage, role or operand")
    scope = _bounded_text(record["scope"], "scope")
    binding = _sha256(record["binding"], "launch binding")
    if type(record["workspace"]) is not list or len(record["workspace"]) != 3:
        raise MakeProbeError("toolchain intermediate workspace lacks its directory identity")
    workspace = tuple(_u64(value, "workspace identity") for value in record["workspace"])
    if profile is not None and workspace != tuple(profile["workspace"]):
        raise MakeProbeError("toolchain intermediate workspace differs from its consumed launch")
    if launch is not None and (scope != launch["scope"] or binding != launch["binding"]):
        raise MakeProbeError("toolchain intermediate scope or binding differs from its consumed launch")
    actors = _actor_map(record, executions)

    creation = _operation(record["creation"], {
        "order", "syscall_sequence", "syscall", "exec_sequence", "pid", "fd", "flags",
        "requested_mode", "result", "identity",
    }, "creation")
    if creation["syscall"] not in {"open", "openat"}:
        raise MakeProbeError("toolchain intermediate creation is not the exclusive open form")
    creation_sequence = _u64(creation["exec_sequence"], "creation actor", positive=True)
    creation_pid = _u64(creation["pid"], "creation pid", positive=True)
    creation_fd = _u64(creation["fd"], "creation fd")
    if creation_fd >= 128 or _s64(creation["result"], "creation result") != creation_fd:
        raise MakeProbeError("toolchain intermediate creation lacks its actual successful descriptor")
    _flags(
        creation["flags"], "creation", os.O_RDWR, os.O_CREAT | os.O_EXCL,
        os.O_TRUNC | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_LARGEFILE", 0),
    )
    if (
        creation_sequence != roles.creator_sequence or actors.get(creation_sequence, (None,))[0] != creation_pid
        or type(creation["requested_mode"]) is not int or creation["requested_mode"] != 0o600
    ):
        raise MakeProbeError("toolchain intermediate creation is not bound to the driver and mode")
    created_identity = _identity(creation["identity"], "creation identity")
    if created_identity[3] != 0 or created_identity[6] != 1:
        raise MakeProbeError("toolchain intermediate creation did not produce one empty link")

    creator_close = _operation(record["creator_close"], {
        "order", "syscall_sequence", "syscall", "result",
    }, "creator close")
    if creator_close["syscall"] != "close" or _s64(creator_close["result"], "creator close result") != 0:
        raise MakeProbeError("toolchain intermediate creator close was not successful")

    writer = _exact(record["writer"], {"exec_sequence", "pid", "operand", "open", "completed", "exit"}, "writer")
    writer_sequence = _u64(writer["exec_sequence"], "writer actor", positive=True)
    writer_pid = _u64(writer["pid"], "writer pid", positive=True)
    operand = _exact(writer["operand"], {"kind", "option", "argv_index"}, "writer operand")
    if (
        writer_sequence != roles.writer_sequence or actors.get(writer_sequence, (None,))[0] != writer_pid
        or operand["kind"] != "output" or operand["option"] != "-o"
        or _u64(operand["argv_index"], "writer operand index") != roles.output.argv_index
    ):
        raise MakeProbeError("toolchain intermediate writer is not bound to its parsed output role")
    writer_open = _operation(writer["open"], {
        "order", "syscall_sequence", "syscall", "fd", "flags", "requested_mode", "result", "identity",
    }, "writer open")
    if writer_open["syscall"] not in {"open", "openat"}:
        raise MakeProbeError("toolchain intermediate writer lacks its ordinary open")
    writer_fd = _u64(writer_open["fd"], "writer fd")
    if writer_fd >= 128 or _s64(writer_open["result"], "writer open result") != writer_fd:
        raise MakeProbeError("toolchain intermediate writer lacks its actual successful descriptor")
    writer_flags = _flags(
        writer_open["flags"], "writer", os.O_WRONLY, 0,
        os.O_CREAT | os.O_TRUNC | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_LARGEFILE", 0),
    )
    expected_modes = (0o600, 0o666) if writer_flags & os.O_CREAT else (0,)
    if type(writer_open["requested_mode"]) is not int or writer_open["requested_mode"] not in expected_modes:
        raise MakeProbeError("toolchain intermediate writer changed its requested mode")
    writer_open_identity = _identity(writer_open["identity"], "writer open identity")
    if not _same_live_object(created_identity, writer_open_identity, size=0):
        raise MakeProbeError("toolchain intermediate writer opened a different object")
    completed = _exact(writer["completed"], {
        "order", "close_order", "close_syscall_sequence", "close_result", "write_calls",
        "written_bytes", "extent", "sha256", "identity",
    }, "writer completion")
    completed_order = _u64(completed["order"], "writer seal order", positive=True)
    writer_close_order = _u64(completed["close_order"], "writer close order", positive=True)
    writer_close_sequence = _u64(
        completed["close_syscall_sequence"], "writer close syscall sequence", positive=True,
    )
    if _s64(completed["close_result"], "writer close result") != 0:
        raise MakeProbeError("toolchain intermediate writer close was not successful")
    write_calls = _u64(completed["write_calls"], "writer call count", positive=True)
    written = _u64(completed["written_bytes"], "writer byte count")
    extent = _u64(completed["extent"], "writer extent")
    digest = _sha256(completed["sha256"], "writer content")
    sealed_identity = _identity(completed["identity"], "writer sealed identity")
    if written != extent or not _same_live_object(created_identity, sealed_identity, size=extent):
        raise MakeProbeError("toolchain intermediate writer extent or object progression is inconsistent")
    writer_exit = _exact(writer["exit"], {"order", "result"}, "writer exit")
    writer_exit_order = _u64(writer_exit["order"], "writer exit order", positive=True)
    if _s64(writer_exit["result"], "writer exit result") != 0:
        raise MakeProbeError("toolchain intermediate writer did not exit successfully")

    reader = _exact(record["reader"], {"exec_sequence", "pid", "operand", "open", "completed", "exit"}, "reader")
    reader_sequence = _u64(reader["exec_sequence"], "reader actor", positive=True)
    reader_pid = _u64(reader["pid"], "reader pid", positive=True)
    operand = _exact(reader["operand"], {"kind", "argv_index"}, "reader operand")
    if (
        reader_sequence != roles.reader_sequence or actors.get(reader_sequence, (None,))[0] != reader_pid
        or operand["kind"] != "input"
        or _u64(operand["argv_index"], "reader operand index") != roles.input.argv_index
    ):
        raise MakeProbeError("toolchain intermediate reader is not bound to its parsed input role")
    reader_open = _operation(reader["open"], {
        "order", "syscall_sequence", "syscall", "fd", "flags", "result", "identity",
    }, "reader open")
    if reader_open["syscall"] not in {"open", "openat"}:
        raise MakeProbeError("toolchain intermediate reader lacks its ordinary open")
    reader_fd = _u64(reader_open["fd"], "reader fd")
    if reader_fd >= 128 or _s64(reader_open["result"], "reader open result") != reader_fd:
        raise MakeProbeError("toolchain intermediate reader lacks its actual successful descriptor")
    _flags(
        reader_open["flags"], "reader", os.O_RDONLY, 0,
        getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_LARGEFILE", 0),
    )
    if _identity(reader_open["identity"], "reader open identity") != sealed_identity:
        raise MakeProbeError("toolchain intermediate reader opened a different sealed object")
    reader_completed = _exact(reader["completed"], {
        "order", "close_syscall_sequence", "close_result", "read_calls", "read_bytes",
        "extent", "sha256", "eof_observed", "identity",
    }, "reader completion")
    reader_close_order = _u64(reader_completed["order"], "reader close order", positive=True)
    reader_close_sequence = _u64(
        reader_completed["close_syscall_sequence"], "reader close syscall sequence", positive=True,
    )
    if (
        _s64(reader_completed["close_result"], "reader close result") != 0
        or _u64(reader_completed["read_calls"], "reader call count", positive=True) == 0
        or _u64(reader_completed["read_bytes"], "reader byte count") != extent
        or _u64(reader_completed["extent"], "reader extent") != extent
        or _sha256(reader_completed["sha256"], "reader content") != digest
        or type(reader_completed["eof_observed"]) is not bool
        or _identity(reader_completed["identity"], "reader completed identity") != sealed_identity
    ):
        raise MakeProbeError("toolchain intermediate reader did not consume the sealed content")
    read_calls = reader_completed["read_calls"]
    reader_exit = _exact(reader["exit"], {"order", "result"}, "reader exit")
    reader_exit_order = _u64(reader_exit["order"], "reader exit order", positive=True)
    if _s64(reader_exit["result"], "reader exit result") != 0:
        raise MakeProbeError("toolchain intermediate reader did not exit successfully")

    retirement = _operation(record["retirement"], {
        "order", "syscall_sequence", "syscall", "exec_sequence", "pid", "result",
        "before_identity", "after_identity", "path_absent",
    }, "retirement")
    retirement_sequence = _u64(retirement["exec_sequence"], "retirement actor", positive=True)
    retirement_pid = _u64(retirement["pid"], "retirement pid", positive=True)
    before = _identity(retirement["before_identity"], "retirement before identity")
    after = _identity(retirement["after_identity"], "retirement after identity")
    if (
        retirement["syscall"] not in {"unlink", "unlinkat"}
        or retirement_sequence != roles.creator_sequence
        or actors.get(retirement_sequence, (None,))[0] != retirement_pid
        or _s64(retirement["result"], "retirement result") != 0
        or type(retirement["path_absent"]) is not bool or not retirement["path_absent"]
        or before != sealed_identity or not _retirement_progression(before, after)
    ):
        raise MakeProbeError("toolchain intermediate retirement did not remove the exact sealed link")
    driver_exit = _exact(record["driver_exit"], {"order", "result"}, "driver exit")
    driver_exit_order = _u64(driver_exit["order"], "driver exit order", positive=True)
    if _s64(driver_exit["result"], "driver exit result") != 0:
        raise MakeProbeError("toolchain intermediate driver did not exit successfully")

    orders = (
        creation["order"], creator_close["order"], writer_open["order"], writer_close_order,
        writer_exit_order, completed_order, reader_open["order"], reader_close_order,
        reader_exit_order, retirement["order"], driver_exit_order,
    )
    syscall_sequences = (
        creation["syscall_sequence"], creator_close["syscall_sequence"], writer_open["syscall_sequence"],
        writer_close_sequence, reader_open["syscall_sequence"], reader_close_sequence,
        retirement["syscall_sequence"],
    )
    if any(first >= second for first, second in zip(orders, orders[1:])) or any(
        first >= second for first, second in zip(syscall_sequences, syscall_sequences[1:])
    ):
        raise MakeProbeError("toolchain intermediate transitions are not in their strict successful order")
    if limits is not None and (
        extent > limits.file_limit or written > limits.write_limit
        or any(sequence > limits.syscall_limit for sequence in syscall_sequences)
        or write_calls > limits.syscall_limit or read_calls > limits.syscall_limit
        or write_calls + read_calls + len(syscall_sequences) > limits.syscall_limit
        or limits.creation_limit < 1 or limits.process_limit < 3
    ):
        raise MakeProbeError("toolchain intermediate receipt exceeds its actual issued execution bounds")
    return {
        "role": "stage4-assembly", "stage": "compile", "type": "regular", "mode": 0o600,
        "bytes": extent, "sha256": digest,
        "creator": {"exec_sequence": roles.creator_sequence},
        "writer": {"exec_sequence": roles.writer_sequence, "operand_kind": "output"},
        "reader": {"exec_sequence": roles.reader_sequence, "operand_kind": "input"},
        "created": True, "writer_completed": True, "reader_completed": True, "retired": True,
    }


def _intermediate_context(profile, launch, executions, returncode):
    if (
        type(profile) is not dict or len(profile) != 7
        or set(profile) != {"version", "stage", "stdin", "inputs", "driver_identity", "images", "workspace"}
        or type(profile.get("version")) is not int or profile.get("version") != 2
        or type(profile.get("stage")) is not int or profile["stage"] not in range(5)
        or type(profile["stdin"]) is not str or type(profile["inputs"]) is not list
        or type(profile["driver_identity"]) is not list or len(profile["driver_identity"]) != 6
        or any(type(value) is not int or not 0 <= value < 1 << 64 for value in profile["driver_identity"])
        or type(profile["images"]) is not list
        or type(profile["workspace"]) is not list or len(profile["workspace"]) != 3
        or any(type(value) is not int or not 0 <= value < 1 << 64 for value in profile["workspace"])
        or type(launch) is not dict or len(launch) != 3 or set(launch) != {"version", "scope", "binding"}
        or type(launch.get("version")) is not int or launch.get("version") != 2
        or type(returncode) is not int or not -(1 << 63) <= returncode < 1 << 63
    ):
        raise MakeProbeError("toolchain intermediate parser lacks its prospective version-2 launch data")
    _bounded_text(launch["scope"], "launch scope")
    _sha256(launch["binding"], "launch binding")
    if type(executions) not in (list, tuple):
        raise MakeProbeError("toolchain intermediate parser lacks its execution sequence")
    return profile["stage"] == 4 and returncode == 0


def _validate_intermediate_record(row, profile, launch, executions, limits, reserve):
    if len(executions) != 3:
        raise MakeProbeError("toolchain intermediate parser lacks its complete execution sequence")
    reserve(_json_cost(executions))
    driver = _execution_row(executions[0])
    roles = compile_operand_roles(executions, profile, driver["argv"], complete=True)
    _receipt_data(
        row, roles, profile=profile, launch=launch, executions=tuple(executions), limits=limits,
    )


def _intermediate_encoding_plan(record, reserve):
    reserve(16384)
    nodes = size = 0

    def visit(value, depth):
        nonlocal nodes, size
        reserve(128 + (4 * len(value) if type(value) is str else 0))
        nodes += 1
        if nodes > INTERMEDIATE_NODE_LIMIT:
            raise MakeProbeError("toolchain intermediate record exceeds its node bound")
        if type(value) in (dict, list):
            if depth >= INTERMEDIATE_DEPTH_LIMIT:
                raise MakeProbeError("toolchain intermediate record exceeds its nesting bound")
            size += 2 + max(0, len(value) - 1)
            if type(value) is dict:
                size += len(value)
                for key, item in value.items():
                    if type(key) is not str:
                        raise MakeProbeError("toolchain intermediate record has a non-text key")
                    visit(key, depth + 1)
                    visit(item, depth + 1)
            else:
                for item in value:
                    visit(item, depth + 1)
        elif type(value) is str:
            size += 2
            for character in value:
                code = ord(character)
                if 0xD800 <= code <= 0xDFFF:
                    raise MakeProbeError("toolchain intermediate record is not strict UTF-8")
                size += (
                    2 if character in '"\\\b\f\n\r\t'
                    else 6 if code < 32 or 127 <= code <= 0xFFFF
                    else 12 if code > 0xFFFF else 1
                )
                if size + len(INTERMEDIATE_PREFIX) > INTERMEDIATE_RECORD_LIMIT:
                    raise MakeProbeError("toolchain intermediate record exceeds its wire bound")
        elif type(value) is bool or value is None:
            size += 4 if value is None or value else 5
        elif type(value) is int:
            if not -(1 << 63) <= value < 1 << 64:
                raise MakeProbeError("toolchain intermediate record integer exceeds its bounded domain")
            size += 1 if value < 0 else 0
            number = abs(value)
            size += 1
            while number >= 10:
                number //= 10
                size += 1
        else:
            raise MakeProbeError("toolchain intermediate record has an unsupported scalar or container")
        if size + len(INTERMEDIATE_PREFIX) > INTERMEDIATE_RECORD_LIMIT:
            raise MakeProbeError("toolchain intermediate record exceeds its wire bound")

    visit(record, 0)
    wire_size = size + len(INTERMEDIATE_PREFIX)
    # Encoder/key-sort/container/validation scratch plus overlapping ASCII
    # JSON text, bytes, prefixed text and observation encoding; no decoded graph.
    return wire_size, 8192 + 1024 * nodes + 4 * wire_size


def encode_intermediate_record(
    record, *, profile, launch, executions, returncode, limits, reserve,
):
    if type(limits) is not IntermediateLimits or not callable(reserve):
        raise MakeProbeError("toolchain intermediate producer lacks exact issued admission")
    reserve(8192)
    if limits.observation_count < 1 or not _intermediate_context(profile, launch, executions, returncode):
        raise MakeProbeError("toolchain intermediate producer lacks its successful compile context")
    wire_size, workspace = _intermediate_encoding_plan(record, reserve)
    if wire_size > limits.observation_limit:
        raise MakeProbeError("toolchain intermediate receipt exceeds its actual observation-byte remainder")
    reserve(workspace)
    _validate_intermediate_record(record, profile, launch, executions, limits, reserve)
    canonical = encoded(record)
    if not canonical.isascii() or len(canonical) + len(INTERMEDIATE_PREFIX) != wire_size:
        raise MakeProbeError("toolchain intermediate encoder differs from its admitted ASCII extent")
    return INTERMEDIATE_PREFIX + canonical.decode("ascii")


def intermediate_record(
    values, *, profile, launch, executions, returncode, limits, reserve,
):
    if type(limits) is not IntermediateLimits or not callable(reserve):
        raise MakeProbeError("toolchain intermediate parser lacks exact issued admission")
    if type(values) not in (list, tuple) or len(values) > limits.observation_count:
        raise MakeProbeError("toolchain intermediate observations exceed their actual issued count")
    selected = None
    for value in values:
        if type(value) is not str:
            raise MakeProbeError("toolchain intermediate observation is not text")
        if value.startswith(INTERMEDIATE_PREFIX):
            if selected is not None:
                raise MakeProbeError("successful compile stage has repeated toolchain intermediate receipts")
            selected = value
    if not _intermediate_context(profile, launch, executions, returncode):
        if selected is not None:
            raise MakeProbeError("toolchain intermediate receipt exists for an unsuccessful or foreign stage")
        return None
    if selected is None:
        raise MakeProbeError("successful compile stage lacks exactly one toolchain intermediate receipt")
    value = selected
    if not value.isascii():
        raise MakeProbeError("toolchain intermediate record is not its bounded ASCII JSON wire")
    if not len(INTERMEDIATE_PREFIX) < len(value) <= INTERMEDIATE_RECORD_LIMIT:
        raise MakeProbeError("toolchain intermediate record exceeds its wire bound")
    if len(value) > limits.observation_limit:
        raise MakeProbeError("toolchain intermediate receipt exceeds its actual observation-byte remainder")
    reserve(4 * len(value) + 4096)
    row, canonical = _decode_intermediate(value[len(INTERMEDIATE_PREFIX):], reserve)
    if len(canonical) + len(INTERMEDIATE_PREFIX) > limits.observation_limit:
        raise MakeProbeError("toolchain intermediate canonical receipt exceeds its actual observation bound")
    _validate_intermediate_record(row, profile, launch, executions, limits, reserve)
    reserve(len(canonical) + 64)
    return canonical


def _copy_json(value, state, depth=0):
    if depth > INTERMEDIATE_DEPTH_LIMIT:
        raise MakeProbeError("toolchain semantic projection exceeds its bounded nesting")
    state[0] += 1
    if state[0] > 4096:
        raise MakeProbeError("toolchain semantic projection exceeds its bounded data nodes")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not -(1 << 63) <= value < 1 << 64:
            raise MakeProbeError("toolchain semantic projection integer exceeds its bounded domain")
        return value
    if type(value) is float:
        if type(value) is float and not math.isfinite(value):
            raise MakeProbeError("toolchain semantic projection contains a non-finite scalar")
        return value
    if type(value) is str:
        if "\0" in value:
            raise MakeProbeError("toolchain semantic projection contains NUL text")
        _string_bytes(value, "toolchain semantic projection text", 65536)
        return value
    if type(value) is list:
        return [_copy_json(item, state, depth + 1) for item in value]
    if type(value) is tuple:
        return tuple(_copy_json(item, state, depth + 1) for item in value)
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            raise MakeProbeError("toolchain semantic projection has a non-text dictionary key")
        return {key: _copy_json(item, state, depth + 1) for key, item in value.items()}
    raise MakeProbeError("toolchain semantic projection has an unsupported data value")


def project_compile_identity(raw_probes, raw_intermediate, roles, *, reserve):
    if (
        type(raw_probes) not in (list, tuple) or type(raw_intermediate) is not bytes
        or type(roles) is not CompileRoles or not callable(reserve)
    ):
        raise MakeProbeError("toolchain semantic projection lacks its inert typed data")
    if not raw_intermediate or len(raw_intermediate) + len(INTERMEDIATE_PREFIX) > INTERMEDIATE_RECORD_LIMIT:
        raise MakeProbeError("toolchain semantic projection receipt exceeds its wire bound")
    if not raw_intermediate.isascii():
        raise MakeProbeError("toolchain semantic projection receipt is not ASCII")
    reserve(4 * len(raw_intermediate) + 4096)
    payload = raw_intermediate.decode("ascii")
    record, canonical = _decode_intermediate(payload, reserve)
    if canonical != raw_intermediate:
        raise MakeProbeError("toolchain semantic projection requires canonical receipt bytes")
    reserve(_json_cost(raw_probes))
    summary = _receipt_data(record, roles)
    probes = _copy_json(raw_probes, [0])
    if type(probes) is tuple:
        probes = list(probes)
    rows = {}
    for row in probes:
        if type(row) is dict and set(row) == {"stage", "stdin", "eof"}:
            if (
                type(row["stage"]) is not str or row["stage"] != "compile"
                or type(row["stdin"]) is not str or type(row["eof"]) is not bool
            ):
                raise MakeProbeError("toolchain semantic projection has malformed input data")
            continue
        row = _execution_row(row)
        sequence = row["sequence"]
        if sequence in rows:
            raise MakeProbeError("toolchain semantic projection has repeated execution sequences")
        rows[sequence] = row
    if set(rows) != {1, 2, 3}:
        raise MakeProbeError("toolchain semantic projection lacks its complete execution data")
    occurrences = [
        (sequence, index)
        for sequence, row in rows.items()
        for index, value in enumerate(row["argv"])
        if value == roles.output.value
    ]
    if occurrences != [
        (roles.output.exec_sequence, roles.output.argv_index),
        (roles.input.exec_sequence, roles.input.argv_index),
    ]:
        raise MakeProbeError("toolchain semantic projection path appears outside its two roles")
    reference = {"kind": "toolchain-intermediate-ref", "version": 1, "role": "stage4-assembly"}
    for operand in (roles.output, roles.input):
        row = rows.get(operand.exec_sequence)
        if (
            row is None or type(row.get("argv")) is not list
            or not 0 <= operand.argv_index < len(row["argv"])
            or row["argv"][operand.argv_index] != operand.value
        ):
            raise MakeProbeError("toolchain semantic projection operands differ from their raw execution data")
        row["argv"][operand.argv_index] = dict(reference)
    return {
        "runtime_probes": probes,
        "toolchain_semantics": {"version": 1, "intermediates": [summary]},
    }


def input_bytes(profile):
    if profile["stdin"] != INPUTS[profile["stage"]]:
        raise MakeProbeError("toolchain input differs from its exact selected original")
    return profile["stdin"].encode("utf-8")


@dataclass(frozen=True)
class Recipe:
    compiler: str
    binutils: tuple[str, ...]
    syntax: tuple[str, ...]
    compile: tuple[str, ...]
    config: str
    abi: str
    includes: tuple[str, ...]
    system: tuple[str, ...]

    def arguments(self, stage):
        return (*self.binutils, QUERIES[stage]) if stage < 3 else (
            self.syntax if stage == 3 else self.compile
        )


def options(arguments, *, syntax):
    suffix = ("-fsyntax-only", "-x", "c", "-") if syntax else (
        "-ffreestanding", "-fno-pic", "-fno-pie", "-c", "-x", "c", "-o", "/dev/null", "-",
    )
    if tuple(arguments[-len(suffix):]) != suffix:
        raise MakeProbeError("toolchain probe lost its original input/output/mode")
    flags, includes, system, binutils = set(), [], [], []
    words = iter(arguments[:-len(suffix)])
    for word in words:
        if word in {*arm_headers.ARCH_FLAGS, "-std=gnu11", "-fgnu89-inline", "-mabi=aapcs", "-mabi=apcs-gnu"}:
            if word in flags or syntax and word.startswith("-mabi=") or not syntax and word.startswith(("-std=", "-fgnu")):
                raise MakeProbeError("toolchain probe has a repeated or foreign language/ABI flag")
            flags.add(word)
            continue
        if word.startswith("-B"):
            if word not in {"-B/bin/", "-B/usr/bin/"} or binutils:
                raise MakeProbeError("toolchain probe escaped its exact binutils search")
            binutils.append(word)
            continue
        option = next((item for item in ("-isystem", "-iquote", "-I", "-D", "-U") if word.startswith(item)), None)
        if option is None or not syntax and option != "-isystem":
            raise MakeProbeError("unsupported toolchain probe flag")
        value = word[len(option):] if word != option else next(words, "")
        if not value or value.startswith(("-", "@")) or any(char in value for char in "\n\r\0"):
            raise MakeProbeError("invalid toolchain probe operand")
        if option == "-isystem":
            if value != arm_headers.NEWLIB or system:
                raise MakeProbeError("toolchain probe escaped its exact C SDK")
            system.append(value)
        elif option in {"-I", "-iquote"}:
            if value.startswith(("=", "$SYSROOT")):
                raise MakeProbeError("toolchain probe has a sysroot-special include")
            includes.append(value if value == "." else relative_path(value))
        else:
            name, separator, _ = value.partition("=")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or option == "-U" and separator:
                raise MakeProbeError("toolchain probe requires symbolic ordered macros")
    if not set(arm_headers.ARCH_FLAGS) <= flags or syntax and "-std=gnu11" not in flags:
        raise MakeProbeError("toolchain probe lost its original ARM7TDMI/language contract")
    return tuple(includes), tuple(system), tuple(binutils), flags


def parse_recipe(command):
    from .graph_commands import _literal_header_words, _shell_tokens

    tokens = _shell_tokens(command, "original toolchain recipe")
    position = 0

    def pattern_operators(token):
        quote, index, operators = None, 0, []
        while index < len(token.raw):
            character = token.raw[index]
            if character == "\\" and quote != "'" and (
                quote is None or token.raw[index + 1:index + 2] in {'$', '`', '"', "\\"}
            ):
                index += 2
                continue
            if character == quote:
                quote = None
            elif quote is None and character in "'\"":
                quote = character
            elif quote is None and character in "*?[]":
                operators.append(character)
            index += 1
        return tuple(operators)

    def signature(token, role="word"):
        # Only the declared grammar position gives a word a syntactic role.
        # For example, test-command argv may quote !; pipeline negation may not.
        active = token.raw if "$" in token.raw or "`" in token.raw else None
        lexical = (
            token.raw if role == "syntax" else token.assignment if role == "assignment"
            else pattern_operators(token) if role == "pattern" else None
        )
        return token.value, token.operator, token.io_number, active, lexical

    def expect(fragment, *, syntax=(), patterns=(), assignments=()):
        nonlocal position
        expected = _shell_tokens(fragment, "toolchain grammar")
        actual = tokens[position:position + len(expected)]
        if len(actual) != len(expected):
            raise MakeProbeError("toolchain recipe differs from its complete original control flow")
        for item, wanted in zip(actual, expected):
            role = (
                "syntax" if wanted.value in syntax else "pattern" if wanted.value in patterns
                else "assignment" if wanted.value.partition("=")[0] in assignments else "word"
            )
            if signature(item, role) != signature(wanted, role):
                raise MakeProbeError("toolchain recipe differs from its complete original control flow")
        position += len(expected)

    def literals(*, assignment=False):
        nonlocal position
        start = position
        while position < len(tokens) and not tokens[position].operator and not tokens[position].io_number:
            position += 1
        selected = tokens[start:position]
        if assignment and (
            len(selected) != 1
            or signature(selected[0], "assignment") != signature(selected[0]._replace(assignment=True), "assignment")
        ):
            raise MakeProbeError("toolchain recipe requires an unquoted assignment name and equals")
        return tuple(_literal_header_words(selected))

    def errors(messages, *, group=False):
        for message in messages:
            expect("printf '%s\\n' " + repr(message) + " >&2;")
        expect("exit 1; " + ("};" if group else "fi;"), syntax=("}", "fi"))

    expect("set -eu;")
    assignment = literals(assignment=True)
    if len(assignment) != 1 or not assignment[0].startswith("cc=") or not assignment[0][3:]:
        raise MakeProbeError("toolchain recipe lost its literal compiler assignment")
    compiler = assignment[0][3:]
    expect('; cc_path=$(command -v "$cc" 2>/dev/null || true);', assignments=("cc_path",))
    expect('if [ -z "$cc_path" ] || [ ! -x "$cc_path" ]; then', syntax=("if", "then"))
    expect('printf \'%s\\n\' "error: modern compiler not found: $cc" >&2;')
    errors(("Install gcc-arm-none-eabi or set MODERN_TOOLCHAIN_ROOT/MODERN_CC.",))
    query_flags = []
    for variable, query, failure in (
        ("version", QUERIES[0], 'printf \'%s\\n\' "error: failed to run modern compiler: $cc" >&2;'),
        ("target", QUERIES[1], 'printf \'%s\\n\' "error: could not query modern compiler target: $cc" >&2;'),
        ("assembler", QUERIES[2], "printf '%s\\n' 'error: could not resolve the assembler used by modern GCC' >&2;"),
    ):
        expect(variable + '=$("$cc"', assignments=(variable,))
        arguments = literals()
        if not arguments or arguments[-1] != query:
            raise MakeProbeError("toolchain recipe changed its real compiler query")
        query_flags.append(arguments[:-1])
        expect("2>&1) || {", syntax=("{",))
        expect(failure)
        expect("exit 1; };", syntax=("}",))
        if variable == "version":
            expect("""printf 'Modern compiler: %s\\n' "$(printf '%s\\n' "$version" | sed -n '1p')";""")
        elif variable == "target":
            expect('if [ "$target" != arm-none-eabi ]; then', syntax=("if", "then"))
            expect("""printf "error: modern compiler targets '%s'; expected 'arm-none-eabi'\\n" "$target" >&2;""")
            expect('exit 1; fi; printf \'Modern target: %s\\n\' "$target";', syntax=("fi",))
    expect('case "$assembler" in /*) resolved_as="$assembler" ;;',
           syntax=("case", "in"), patterns=("/*",), assignments=("resolved_as",))
    expect('*) resolved_as=$(command -v "$assembler" 2>/dev/null || true) ;; esac;',
           syntax=("esac",), patterns=("*",), assignments=("resolved_as",))
    expect('if [ -z "$resolved_as" ] || [ ! -x "$resolved_as" ]; then', syntax=("if", "then"))
    expect("""printf "error: modern GCC resolved assembler '%s', but it is not executable\\n" "$assembler" >&2;""")
    errors(("Install binutils-arm-none-eabi or set MODERN_BINUTILS_DIR.",))
    expect('printf \'Modern assembler: %s\\n\' "$resolved_as";')
    probes = []
    for stdin, failure in ((SYNTAX_INPUT, SYNTAX_FAILURE), (COMPILE_INPUT, COMPILE_FAILURE)):
        expect("if ! printf '%s\\n' " + repr(stdin.rstrip("\n")) + ' | "$cc"', syntax=("if", "!"))
        probes.append(literals())
        expect("; then", syntax=("then",))
        errors(tuple(failure.rstrip("\n").split("\n")))
    expect("printf 'Modern flags: ARM7TDMI Thumb/interwork; config=%s; ABI=%s\\n'")
    selected = literals()
    if position != len(tokens) or len(selected) != 2 or selected[0] not in {"release", "debug"} or selected[1] not in {"aapcs", "apcs-gnu"}:
        raise MakeProbeError("toolchain recipe has trailing syntax or an unsupported configuration")
    includes, system, binutils, _ = options(probes[0], syntax=True)
    _, _, compile_binutils, compile_flags = options(probes[1], syntax=False)
    abi_flags = {value for value in compile_flags if value.startswith("-mabi=")}
    if (
        any(flags != binutils for flags in query_flags) or compile_binutils != binutils
        or (abi_flags not in (set(), {"-mabi=aapcs"}) if selected[1] == "aapcs"
            else abi_flags != {"-mabi=apcs-gnu"})
    ):
        raise MakeProbeError("toolchain recipe queries/driver/ABI disagree")
    return Recipe(compiler, binutils, *probes, *selected, includes, system)


def environment(values, *, launch=False):
    arm_headers.environment(values)
    if values.get("PATH") != ENVIRONMENT["PATH"] or values.get("TMPDIR") not in (
        ("/work",) if launch else (None, "/work")
    ):
        raise MakeProbeError("toolchain probe requires its exact search and private temporary environment")
    if any(values.get(name) for name in (
        "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "GCC_COMPARE_DEBUG", "GCC_EXTRA_DIAGNOSTIC_OUTPUT",
        "COLLECT_GCC", "COLLECT_GCC_OPTIONS", "LANGUAGE", "TMP", "TEMP",
    )):
        raise MakeProbeError("toolchain probe has a foreign driver/runtime environment")


def assembler_path(value):
    from .make_probe import _trusted_runtime_path
    import shutil

    spelling = value if value.startswith("/") else shutil.which(value, path=ENVIRONMENT["PATH"])
    if not spelling:
        raise MakeProbeError("modern GCC resolved an unavailable assembler")
    if not spelling.startswith(("/usr/", "/bin/")):
        raise MakeProbeError("modern GCC assembler escaped the supported system roots")
    canonical = str(_trusted_runtime_path(str(Path(spelling).resolve(strict=True)), compiler=True))
    if canonical not in {"/usr/bin/arm-none-eabi-as", "/usr/lib/arm-none-eabi/bin/as"} or not os.access(canonical, os.X_OK):
        raise MakeProbeError("modern GCC resolved an unsupported target assembler: " + spelling + " -> " + canonical)
    return spelling, canonical


def launch_binding(config):
    return hashlib.sha256(encoded({name: config[name] for name in LAUNCH_FIELDS})).hexdigest()


def validate_launch(config):
    dependency = config.get("dependency") or {}
    profile = dependency.get("toolchain_probe")
    grant = config.get("toolchain_runtime")
    if profile is None:
        if grant is not None:
            raise ChannelError("unrelated command carries a toolchain launch")
        return None
    if (
        not isinstance(profile, dict) or set(profile) != {"version", "stage", "stdin", "inputs", "driver_identity", "images", "workspace"}
        or type(profile["version"]) is not int or profile["version"] != 2
        or type(profile["stage"]) is not int or profile["stage"] not in range(5)
        or profile["stdin"] != INPUTS[profile["stage"]]
        or not isinstance(profile["inputs"], list)
        or not isinstance(profile["driver_identity"], list) or len(profile["driver_identity"]) != 6
        or any(type(value) is not int or value < 0 for value in profile["driver_identity"])
        or not isinstance(profile["workspace"], list) or len(profile["workspace"]) != 3
        or any(type(value) is not int or value < 0 for value in profile["workspace"])
        or config["mode"] != "compile" or config.get("header_runtime") is not None
        or config.get("private_install") is not None or config.get("producer_endpoint") is not None
        or not isinstance(grant, dict) or set(grant) != {"version", "scope", "binding"}
        or type(grant["version"]) is not int or grant["version"] != 2
        or grant["scope"] != launch_scope(config["root"]) or grant["binding"] != launch_binding(config)
        or config["executables"] != dependency["executables"]
        or not config["argv"] or not config["executables"] or config["argv"][0] != config["executables"][0]
        or config["executables"][0] != "/usr/bin/arm-none-eabi-gcc"
        or config["sources"] or config["enumerations"]
    ):
        raise ChannelError("toolchain launch is unbound, malformed or outside its selected profile")
    stage = profile["stage"]
    try:
        environment(config["environment"], launch=True)
        if stage < 3:
            if config["argv"][1:] not in (
                [QUERIES[stage]], ["-B/bin/", QUERIES[stage]], ["-B/usr/bin/", QUERIES[stage]],
            ):
                raise MakeProbeError("toolchain query changed its exact arguments")
        else:
            options(config["argv"][1:], syntax=stage == 3)
    except MakeProbeError as error:
        raise ChannelError(str(error)) from error
    executables = config["executables"]
    if (
        len(executables) != (1 if stage < 3 else 2 if stage == 3 else 3)
        or stage >= 3 and not re.fullmatch(r"/usr/lib/gcc/arm-none-eabi/[A-Za-z0-9_.+-]+/cc1", executables[1])
        or stage == 4 and executables[2] not in {"/usr/bin/arm-none-eabi-as", "/usr/lib/arm-none-eabi/bin/as"}
        or (stage >= 3) != ("header_search" in dependency)
        or stage != 3 and (config["code"] or profile["inputs"])
    ):
        raise ChannelError("toolchain launch has foreign executable or source authority")
    if (
        not isinstance(profile["images"], list) or len(profile["images"]) != len(executables)
        or any(
            not isinstance(row, list) or len(row) != 7 or row[0] != path
            or any(type(value) is not int or value < 0 for value in row[1:])
            for row, path in zip(profile["images"], executables)
        )
        or profile["images"][0][1:] != profile["driver_identity"]
    ):
        raise ChannelError("toolchain launch lacks its actual executable identities")
    inputs = {}
    for row in profile["inputs"]:
        if (
            not isinstance(row, list) or len(row) != 4 or not isinstance(row[0], str)
            or not row[0].endswith((".h", ".inc")) or row[0] in inputs
            or type(row[1]) is not int or not 0 <= row[1] <= 0o777
            or type(row[2]) is not int or not 0 <= row[2] <= config["file_limit"]
            or not isinstance(row[3], str) or not re.fullmatch("[0-9a-f]{64}", row[3])
        ):
            raise ChannelError("toolchain launch has malformed source identities")
        try:
            relative_path(row[0])
        except MakeProbeError as error:
            raise ChannelError(str(error)) from error
        inputs[row[0]] = row
    if set(inputs) != set(config["code"]) or stage == 3 and "include/global.h" not in inputs:
        raise ChannelError("toolchain launch lost its exact repository header closure")
    return profile


def verify_workspace(path, expected):
    try:
        if list(directory_identity(path.stat(follow_symlinks=False))) != expected:
            raise MakeProbeError("toolchain workspace changed its actual owned directory")
        with os.scandir(path) as entries:
            if next(entries, None) is not None:
                raise MakeProbeError("toolchain workspace is not the original empty private namespace")
    except OSError as error:
        raise MakeProbeError("toolchain workspace is no longer its issued directory") from error


def records(values, profile, executables, *, returncode, argv, environment):
    from json import loads

    executions, inputs = [], []
    for value in values:
        prefix = next((item for item in (EXEC_PREFIX, INPUT_PREFIX) if value.startswith(item)), None)
        if prefix is None:
            continue
        try:
            row = loads(value[len(prefix):])
        except (ValueError, RecursionError) as error:
            raise MakeProbeError("malformed actual toolchain observation") from error
        if prefix == EXEC_PREFIX:
            if (
                not isinstance(row, dict) or set(row) != {"stage", "sequence", "path", "identity", "argv", "environment"}
                or row["stage"] != STAGES[profile["stage"]] or type(row["sequence"]) is not int
                or not 1 <= row["sequence"] <= len(executables)
                or row["path"] != executables[row["sequence"] - 1]
                or not isinstance(row["identity"], list) or len(row["identity"]) != 6
                or any(type(value) is not int or value < 0 for value in row["identity"])
                or row["identity"] != profile["images"][row["sequence"] - 1][1:]
                or not isinstance(row["argv"], list) or not row["argv"]
                or any(not isinstance(value, str) or "\0" in value for value in row["argv"])
                or not row["argv"][0].startswith(("/usr/", "/bin/"))
                or str(Path(row["argv"][0]).resolve(strict=True)) != row["path"]
                or not isinstance(row["environment"], dict)
                or any(not isinstance(key, str) or not isinstance(value, str) for key, value in row["environment"].items())
                or row["sequence"] == 1 and (row["argv"] != list(argv) or row["environment"] != environment)
            ):
                raise MakeProbeError("unbound actual toolchain executable observation")
            executions.append(row)
        else:
            if (
                not isinstance(row, dict) or set(row) != {"stage", "stdin", "eof"}
                or row["stage"] != STAGES[profile["stage"]] or type(row["eof"]) is not bool
                or not isinstance(row["stdin"], str) or not profile["stdin"].startswith(row["stdin"])
                or not returncode and (row["stdin"] != profile["stdin"] or not row["eof"])
            ):
                raise MakeProbeError("unbound actual toolchain stdin observation")
            inputs.append(row)
    executions.sort(key=lambda row: row["sequence"])
    if (
        [row["sequence"] for row in executions] != list(range(1, len(executions) + 1))
        or not executions or not returncode and len(executions) != len(executables)
        or len(inputs) != (1 if profile["stdin"] else 0)
    ):
        raise MakeProbeError("toolchain result omitted actual executable/stdin observations")
    return tuple([*executions, *inputs])


def _admit_completion_records(values, header_search, limits, reserve):
    if type(limits) is not IntermediateLimits or not callable(reserve):
        raise MakeProbeError("toolchain completion records lack exact issued admission")
    reserve(32768)
    if type(values) not in (list, tuple) or len(values) > limits.observation_count:
        raise MakeProbeError("toolchain completion observations exceed their issued count")
    scans = 3 + (header_search is not None)
    selected = header_rows = header_characters = 0
    for value in values:
        if type(value) is not str:
            raise MakeProbeError("toolchain completion observation is not exact text")
        reserve(128 * scans + 4 * len(value))
        _string_bytes(value, "toolchain data text", 65536)
        prefix = next((item for item in (EXEC_PREFIX, INPUT_PREFIX) if value.startswith(item)), None)
        if prefix is None and header_search is not None and value.startswith(arm_headers.PREFIX):
            prefix = arm_headers.PREFIX
            header_rows += 1
            header_characters += len(value)
        if prefix is None:
            continue
        selected += 1
        # The planning slice and parser slice are separate real allocations.
        reserve(256 + 8 * len(value))
        payload = value[len(prefix):]
        nodes = _json_shape(payload, node_limit=limits.file_limit)
        # Default JSON decoding has no object-pairs graph or encoder here.
        # Headers/containers/key memo/reference arrays plus decoded text remain.
        reserve(8192 + 256 * nodes + 4 * len(payload))
        if prefix == EXEC_PREFIX:
            # Path.resolve may retain both parsed and resolved component arrays.
            reserve(3 * len(payload))
            components = payload.count("/") + payload.count("\\u002f") + payload.count("\\u002F") + 1
            reserve(256 * components + 8 * len(payload))
    reserve(
        8192 + 32 * selected * (1 + selected.bit_length())
        + header_characters * (1 + header_rows.bit_length()),
    )
    if header_search is None:
        return
    if type(header_search) is not dict:
        raise MakeProbeError("toolchain completion header search is not a bounded object")
    for name, width, count in (
        ("roots", 2, 5), ("entries", 2, limits.observation_count),
        ("files", 4, limits.observation_count), ("aliases", 3, 2),
    ):
        rows = header_search.get(name)
        if type(rows) is not list or len(rows) > count:
            raise MakeProbeError("toolchain completion header search exceeds its issued shape")
        reserve(1024 + 256 * len(rows))
        for row in rows:
            if type(row) is not list or len(row) != width:
                raise MakeProbeError("toolchain completion header row has an unsupported shape")
            reserve(128 + 16 * width)
            for index, value in enumerate(row):
                if type(value) is str:
                    reserve(128 + 4 * len(value))
                    _string_bytes(value, "toolchain header search text", limits.file_limit)
                    if index == 0 and name in {"roots", "entries"}:
                        reserve(2048 + 8 * len(value) + 256 * (value.count("/") + 1))
                elif type(value) not in (int, bool):
                    raise MakeProbeError("toolchain completion header row has an unsupported scalar")
    excluded = header_search.get("excluded")
    if type(excluded) is not list or len(excluded) > 1:
        raise MakeProbeError("toolchain completion header exclusions exceed their shape")
    for value in excluded:
        if type(value) is not str:
            raise MakeProbeError("toolchain completion header exclusion is not text")
        reserve(256 + 4 * len(value))
        _string_bytes(value, "toolchain header exclusion", limits.file_limit)


def _envelope_encode(session, value, *, file_limit=None):
    limit = session.budget.limits.file_bytes if file_limit is None else file_limit
    def reserve(size):
        session.budget.remaining()
        session.budget.charge("control", size)
    reserve(32768)
    reserve(_json_cost(value, node_limit=limit, work=reserve))
    data = encoded(value)
    if len(data) > limit:
        raise MakeProbeError("toolchain evidence envelope exceeds its issued file bound")
    session.budget.charge("cache", len(data) + 64)
    return data


def _envelope_decode(session, data, *, file_limit=None):
    limit = session.budget.limits.file_bytes if file_limit is None else file_limit
    if type(data) is not bytes or not data or len(data) > limit or not data.isascii():
        raise MakeProbeError("toolchain evidence is not bounded immutable ASCII JSON")
    session.budget.remaining()
    session.budget.charge("control", 4 * len(data) + 8192)
    payload = data.decode("ascii")
    nodes = _json_shape(payload, node_limit=limit)
    session.budget.charge("control", 4 * len(data) + 1024 * nodes + 8192)
    try:
        return json.loads(payload)
    except (ValueError, RecursionError) as error:
        raise MakeProbeError("toolchain evidence is malformed JSON") from error


@dataclass(frozen=True, slots=True, eq=False)
class _LaunchFacts:
    token: object
    scope: str
    binding: str
    report: Path
    profile: bytes
    limits: IntermediateLimits
    snapshot: object
    tree: Path
    epoch: int
    thread: int


@dataclass(frozen=True, slots=True, eq=False)
class _AcceptedToolchainCompletion:
    step: object
    launch: _LaunchFacts
    probes: bytes
    runtime_sources: tuple
    intermediate: bytes | None
    roles: CompileRoles | None


@dataclass(frozen=True, eq=False)
class _StepResult:
    step: object
    result: object
    binding: bytes
    envelope: bytes
    native: object


@dataclass(frozen=True, slots=True, eq=False)
class _ValidatedStage:
    result: object
    binding: bytes
    envelope: bytes
    native: object


@dataclass(frozen=True, eq=False)
class _RecipeResult:
    command: object
    result: object
    parent: object
    snapshot: object
    tree: Path
    epoch: int
    thread: int
    binding: bytes
    evidence: object
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class RecipeEvidence:
    stages: tuple[bytes, ...]
    result: bytes
    command_binding: bytes
    source_snapshot: str
    namespace_epoch: int
    projection: bytes | None


@dataclass(eq=False)
class _RecipeGrant:
    command: object
    recipe: Recipe
    context: object
    binding: bytes
    inputs: tuple
    stage: int = 0
    assembler: str | None = None
    sdk: tuple | None = None
    driver_identity: tuple | None = None
    assembler_spelling: str | None = None
    accepted_stages: list = field(default_factory=list)


@dataclass(eq=False)
class _Step:
    command: object
    parent: _RecipeGrant
    binding: bytes
    stage: int
    launched: bool = False
    dependency: bytes | None = None
    facts: _LaunchFacts | None = None
    phase: str = "issued"
    native_claim: object | None = None


class _Launch:
    pass


class Controller:
    def __init__(self, session):
        session.budget.charge("cache", 1024)
        self.session = session
        self.commands = {}
        self.steps = {}
        self.launches = {}
        self.issued = weakref.WeakSet()
        self.active = None
        self._step_results = {}
        self._recipe_results = {}

    def close(self):
        self.commands.clear()
        self.steps.clear()
        self.launches.clear()
        self.issued.clear()
        self.active = None
        self._step_results.clear()
        self._recipe_results.clear()

    def has_results(self):
        return bool(self._step_results or self._recipe_results or any(
            step.native_claim is not None for step in self.steps.values()
        ))

    def expire_results(self):
        for registry in (self._step_results, self._recipe_results):
            for capability in registry.values():
                self.issued.discard(capability)
            registry.clear()
        for step in self.steps.values():
            step.native_claim = None
            step.phase = "expired"

    def bind(self, command):
        tool = command.runtime_tool
        return encoded((
            self.session._install_command_binding(command).decode("ascii"),
            None if tool is None else (tool.path, tool.canonical, tool.mode, tool.digest),
        ))

    def register(self, original, compiler):
        from .make_probe import Command, _event_command
        from .graph_commands import _resolve_modern_compiler

        session = self.session
        context = session._require_live_dispatch()
        if (
            context.job[:3] != ("recipe", TARGET, 1) or context.cwd != "/repo"
            or _event_command({"arguments": list(context.arguments)}) != original
        ):
            raise MakeProbeError("toolchain check lacks its actual original scheduled job")
        environment(dict(context.environment))
        recipe = parse_recipe(original)
        if _resolve_modern_compiler(session, recipe.compiler) is not compiler:
            raise MakeProbeError("toolchain recipe differs from its exact selected compiler")
        for flag in recipe.binutils:
            from .graph_commands import _resolve_modern_binutils_flag
            _resolve_modern_binutils_flag(flag)
        roots = {"include", *(path for path in recipe.includes if path != ".")}
        headers = tuple(sorted(
            path for path in session.snapshot.files.keys() | session.published_sources.keys()
            if path.endswith((".h", ".inc")) and any(path.startswith(root + "/") for root in roots)
        ))
        if "include/global.h" not in headers:
            raise MakeProbeError("toolchain check lacks its actual global.h input")
        command = session._native_context_command(Command(
            (compiler.path, *recipe.arguments(0)), code=headers, runtime_tool=compiler,
        ))
        key = id(command)
        session.budget.charge("cache", 512)
        grant = _RecipeGrant(
            weakref.ref(command, lambda ref: self.commands.pop(key, None)),
            recipe, context, self.bind(command), tuple(session.source_owners(headers)),
        )
        session.budget.charge("cache", len(grant.binding) + len(encoded(grant.inputs)))
        self.commands[key] = grant
        self.issued.add(grant)
        return command

    def require(self, command):
        session = self.session
        session.budget.remaining()
        grant = self.commands.get(id(command))
        if (
            type(grant) is not _RecipeGrant or grant not in self.issued or grant.command() is not command
            or grant.context is not session._require_live_dispatch()
            or not session._command_dispatches or session._command_dispatches[-1] != (command, grant.context)
            or grant.binding != self.bind(command)
            or tuple(session.source_owners(command.code)) != grant.inputs
        ):
            raise MakeProbeError("toolchain command is unissued, changed, replayed or outside its lifetime")
        environment(session._command_environment(command))
        return grant

    def require_step(self, command):
        step = self.steps.get(id(command))
        if step is None:
            return None
        if (
            type(step) is not _Step or step not in self.issued or step.command is not command
            or step.parent is not self.active or self.require(step.parent.command()) is not step.parent
            or step.stage != step.parent.stage or step.binding != self.bind(command)
            or self.driver_identity(command.runtime_tool) != step.parent.driver_identity
            or step.launched
        ):
            raise MakeProbeError("toolchain substep is unissued, changed or outside its actual parent")
        return step

    def completion_context(self, owner):
        session = self.session
        session.budget.remaining()
        session.budget.charge("control", 1024)
        selected = [step for step in self.steps.values() if step.facts is not None and step.facts.token is owner]
        if len(selected) != 1:
            raise MakeProbeError("toolchain completion lacks one consumed launch")
        step = selected[0]
        facts = step.facts
        session.budget.charge("control", 8 * len(step.binding) + 8192)
        if (
            type(owner) is not _Launch or type(step) is not _Step or step not in self.issued
            or not step.launched or step.phase not in {"launched", "native-returned"}
            or step.parent is not self.active or step.stage != step.parent.stage
            or self.require(step.parent.command()) is not step.parent
            or step.binding != self.bind(step.command)
            or facts.snapshot is not session.snapshot or facts.tree != session.tree
            or facts.epoch != session._namespace_epoch or facts.thread != get_ident()
        ):
            raise MakeProbeError("toolchain completion outlived its original step/job/view")
        return step.command, step.parent.context, step

    def prepare_native(self, owner, completed, observed, config):
        _, _, step = self.completion_context(owner)
        facts = step.facts
        if (
            step.phase != "launched" or Path(config["report"]) != facts.report
            or launch_binding(config) != facts.binding
            or completed.returncode != observed["returncode"]
        ):
            raise MakeProbeError("toolchain native completion changed its consumed launch/report")
        session = self.session
        profile = _envelope_decode(session, facts.profile, file_limit=facts.limits.file_limit)
        _admit_completion_records(
            observed["accessed"], config["dependency"].get("header_search"), facts.limits,
            lambda size: session.budget.charge("control", size),
        )
        probes = records(
            observed["accessed"], profile, config["executables"],
            returncode=completed.returncode, argv=config["argv"], environment=config["environment"],
        )
        executions = tuple(row for row in probes if "sequence" in row)
        intermediate = intermediate_record(
            observed["accessed"], profile=profile, launch=config["toolchain_runtime"],
            executions=executions, returncode=completed.returncode, limits=facts.limits,
            reserve=lambda size: session.budget.charge("control", size),
        )
        roles = None if intermediate is None else compile_operand_roles(
            executions, profile, config["argv"], complete=True,
        )
        runtime_sources = ()
        if "header_search" in config["dependency"]:
            try:
                runtime_sources = arm_headers.records(
                    observed["accessed"], config["dependency"]["header_search"], config["executables"][:2],
                    count_limit=facts.limits.observation_count, file_limit=facts.limits.file_limit,
                )
            except ChannelError as error:
                raise MakeProbeError(str(error)) from error
        session.budget.charge("control", 1024 + 64 * len(runtime_sources))
        return _AcceptedToolchainCompletion(
            step, facts, _envelope_encode(session, probes, file_limit=facts.limits.file_limit),
            runtime_sources, intermediate, roles,
        )

    def native_issued(self, owner, payload):
        _, _, step = self.completion_context(owner)
        if (
            type(payload) is not _AcceptedToolchainCompletion
            or payload.step is not step or payload.launch is not step.facts
            or step.phase != "launched" or step.native_claim is not None
        ):
            raise MakeProbeError("toolchain native payload is not its consumed step")
        step.phase = "native-returned"

    def native_claimed(self, owner, claimed):
        _, _, step = self.completion_context(owner)
        if (
            step.phase != "native-returned" or step.native_claim is not None
            or claimed.payload.step is not step or claimed.payload.launch is not step.facts
        ):
            raise MakeProbeError("toolchain native claim is copied or repeated")
        step.native_claim = claimed

    def _result_value(self, result):
        from .make_probe import ProcessOutput

        if (
            type(result) is not ProcessOutput or type(result.stdout) is not bytes
            or type(result.stderr) is not bytes or type(result.returncode) is not int
            or result.artifact is not None or type(result.generated) is not tuple or result.generated
            or any(type(getattr(result, name)) is not tuple for name in (
                "consumed", "code_consumed", "metadata", "input_identities", "executed",
                "runtime_receipt", "runtime_sources", "runtime_probes",
            ))
            or type(result.toolchain_receipts) is not tuple
            or len(result.toolchain_receipts) > 5
            or any(type(value) is not bytes for value in result.toolchain_receipts)
        ):
            raise MakeProbeError("toolchain result has a foreign shape or exported artifact")
        self.session.budget.charge(
            "control", 8192 + len(result.stdout) + len(result.stderr)
            + sum(len(value) + 256 for value in result.toolchain_receipts),
        )
        return {
            "returncode": result.returncode,
            "stdout_sha256": hashlib.sha256(result.stdout).hexdigest(),
            "stderr_sha256": hashlib.sha256(result.stderr).hexdigest(),
            "consumed": result.consumed, "code_consumed": result.code_consumed,
            "metadata": result.metadata, "input_identities": result.input_identities,
            "executed": result.executed, "runtime_receipt": result.runtime_receipt,
            "runtime_sources": result.runtime_sources, "runtime_probes": result.runtime_probes,
            "receipts": [(len(value), hashlib.sha256(value).hexdigest()) for value in result.toolchain_receipts],
        }

    def _result_binding(self, result):
        return self.session._native_fingerprint(self._result_value(result))

    def seal_step_result(self, step, result, *, native_return):
        session = self.session
        self._result_value(result)
        if (
            type(step) is not _Step or step not in self.issued
            or self.steps.get(id(step.command)) is not step or type(step.facts) is not _LaunchFacts
            or self.completion_context(step.facts.token)[2] is not step
        ):
            raise MakeProbeError("toolchain result requires its identity-issued original step")
        if step.native_claim is not native_return or step.phase != "native-returned" or self._step_results:
            raise MakeProbeError("toolchain step result lacks its original native claim")
        original = native_return.original
        session._verify_native_return(
            original, NATIVE_PURPOSE, step.facts.token, original.completed, original.observed,
        )
        payload = native_return.payload
        probes = _envelope_decode(session, payload.probes, file_limit=step.facts.limits.file_limit)
        dependency = _envelope_decode(session, step.dependency)
        inputs = tuple(session.source_owners(set(result.consumed) | set(result.code_consumed)))
        if (
            result.toolchain_receipts or result.returncode != native_return.returncode
            or result.stdout is not native_return.stdout or result.stderr is not native_return.stderr
            or result.consumed != native_return.consumed or result.code_consumed != native_return.code_consumed
            or result.metadata != native_return.metadata or result.executed != native_return.executed
            or result.input_identities != inputs
            or session._native_fingerprint(result.runtime_receipt) != session._native_fingerprint(dependency["runtime_aliases"])
            or result.runtime_sources != payload.runtime_sources
            or result.runtime_probes != tuple(probes)
        ):
            raise MakeProbeError("toolchain result differs from ORIGINAL accepted native facts")
        step.phase = "sealing"
        session.budget.charge("control", 16384)
        profile = _envelope_decode(session, step.facts.profile)
        result_data = self._result_value(result)
        envelope = _envelope_encode(session, {
            "version": 1, "kind": "toolchain-stage", "stage": STAGES[step.stage],
            "launch_scope": step.facts.scope, "launch_binding": step.facts.binding,
            "source_snapshot": session.snapshot.digest, "namespace_epoch": step.facts.epoch,
            "workspace": profile["workspace"], "images": profile["images"],
            "admission": {name: getattr(step.facts.limits, name) for name in (
                "file_limit", "observation_count", "observation_limit", "write_limit",
                "creation_limit", "process_limit", "memory_limit", "syscall_limit", "deadline",
            )},
            "executions": [row for row in probes if "sequence" in row],
            "stdin": [row for row in probes if "stdin" in row],
            "intermediate": None if payload.intermediate is None else _envelope_decode(session, payload.intermediate),
            "result": {name: result_data[name] for name in (
                "returncode", "stdout_sha256", "stderr_sha256", "consumed", "code_consumed",
                "input_identities", "executed", "runtime_receipt", "runtime_sources",
            )},
        }, file_limit=step.facts.limits.file_limit)
        session.budget.charge("cache", 4096)
        sealed = replace(result, toolchain_receipts=(envelope,))
        key = id(sealed)
        accepted_native = replace(native_return, original=None)
        capability = _StepResult(
            step, weakref.ref(sealed, lambda reference: self._step_results.pop(key, None)),
            self._result_binding(sealed), envelope, accepted_native,
        )
        self._step_results[key] = capability
        self.issued.add(capability)
        step.native_claim = None
        step.phase = "sealed"
        return sealed

    def consume_step_result(self, step, result):
        capability = self._step_results.pop(id(result), None)
        if (
            type(capability) is not _StepResult or capability not in self.issued
            or capability.result() is not result or capability.step is not step
            or type(step) is not _Step or step not in self.issued
            or self.steps.get(id(step.command)) is not step
            or step.phase != "sealed" or step.parent is not self.active
            or self.require(step.parent.command()) is not step.parent
            or step.stage != step.parent.stage
        ):
            raise MakeProbeError("toolchain stage is unissued, copied, stale or replayed")
        self.issued.discard(capability)
        if (
            capability.binding != self._result_binding(result)
            or result.stdout is not capability.native.stdout or result.stderr is not capability.native.stderr
            or result.toolchain_receipts != (capability.envelope,)
        ):
            raise MakeProbeError("toolchain stage changed after native sealing")
        self.session.budget.charge("cache", 2048)
        accepted = _ValidatedStage(result, capability.binding, capability.envelope, capability.native)
        step.parent.accepted_stages.append(accepted)
        step.phase = "consumed"
        return accepted

    def retire_step(self, step):
        for key, capability in tuple(self._step_results.items()):
            if capability.step is step:
                self._step_results.pop(key)
                self.issued.discard(capability)
        step.native_claim = None

    def _recipe_output(self, grant):
        stages = grant.accepted_stages
        self.session.budget.charge(
            "control", 16384 + 4 * sum(
                len(stage.native.stdout) + len(stage.native.stderr) for stage in stages
            ) + 24 * len(grant.recipe.compiler) + 24 * len(grant.assembler_spelling or ""),
        )
        stdout, stderr = bytearray(), bytearray()
        status = 0
        for index, stage in enumerate(stages):
            native = stage.native
            if index < 3:
                value = native.stdout.rstrip(b"\n")
                if native.returncode:
                    status = 1
                    stderr.extend((
                        "error: failed to run modern compiler: " + grant.recipe.compiler + "\n",
                        "error: could not query modern compiler target: " + grant.recipe.compiler + "\n",
                        "error: could not resolve the assembler used by modern GCC\n",
                    )[index].encode())
                elif index == 0:
                    stdout.extend(b"Modern compiler: " + value.split(b"\n")[0] + b"\n")
                elif index == 1:
                    if value != b"arm-none-eabi":
                        stderr.extend(b"error: modern compiler targets '" + value + b"'; expected 'arm-none-eabi'\n")
                        status = 1
                    else:
                        stdout.extend(b"Modern target: " + value + b"\n")
                else:
                    if type(grant.assembler_spelling) is not str or grant.assembler is None:
                        raise MakeProbeError("toolchain recipe lost its accepted assembler resolution")
                    stdout.extend(b"Modern assembler: " + grant.assembler_spelling.encode() + b"\n")
            else:
                stdout.extend(native.stdout)
                stderr.extend(native.stderr)
                if native.returncode:
                    status = 1
                    stderr.extend((SYNTAX_FAILURE if index == 3 else COMPILE_FAILURE).encode())
            if status:
                if index != len(stages) - 1:
                    raise MakeProbeError("toolchain recipe executed after its first failed stage")
                break
        if not status:
            if len(stages) != 5:
                raise MakeProbeError("toolchain recipe ended before its actual final stage")
            stdout.extend((
                "Modern flags: ARM7TDMI Thumb/interwork; config=%s; ABI=%s\n"
                % (grant.recipe.config, grant.recipe.abi)
            ).encode())
        return bytes(stdout), bytes(stderr), status

    def seal_recipe_result(self, grant, command, result):
        session = self.session
        self._result_value(result)
        if (
            self.active is not grant or self.require(command) is not grant
            or not grant.accepted_stages or len(grant.accepted_stages) != grant.stage
            or self._recipe_results
        ):
            raise MakeProbeError("toolchain recipe lacks its actual consumed stage sequence")
        stages = grant.accepted_stages
        for index, stage in enumerate(stages):
            if stage.binding != self._result_binding(stage.result) or stage.native.payload.step.stage != index:
                raise MakeProbeError("toolchain recipe stage changed after consumption")
        if (result.stdout, result.stderr, result.returncode) != self._recipe_output(grant):
            raise MakeProbeError("toolchain aggregate output differs from ORIGINAL accepted native facts")
        session.budget.charge(
            "control", 32768 + 128 * sum(
                sum(len(getattr(stage.result, name)) for name in (
                    "consumed", "code_consumed", "input_identities", "executed",
                    "runtime_receipt", "runtime_sources", "runtime_probes",
                )) for stage in stages
            ),
        )
        expected = {
            "consumed": (),
            "code_consumed": tuple(sorted({name for stage in stages for name in stage.result.code_consumed})),
            "input_identities": tuple(sorted({row for stage in stages for row in stage.result.input_identities})),
            "executed": tuple(name for stage in stages for name in stage.result.executed),
            "runtime_receipt": tuple(sorted({tuple(row) for stage in stages for row in stage.result.runtime_receipt})),
            "runtime_sources": tuple(sorted({row for stage in stages for row in stage.result.runtime_sources})),
            "runtime_probes": tuple(row for stage in stages for row in stage.result.runtime_probes),
        }
        if any(getattr(result, name) != value for name, value in expected.items()) or result.metadata:
            raise MakeProbeError("toolchain aggregate differs from its exact accepted stages")
        if not result.returncode and (len(stages) != 5 or any(stage.result.returncode for stage in stages)):
            raise MakeProbeError("successful toolchain recipe omitted a successful stage")
        session.budget.charge("control", 8192)
        result_data = self._result_value(result)
        del result_data["receipts"]
        raw_result = _envelope_encode(session, result_data)
        projection = None
        if not result.returncode:
            payload = stages[-1].native.payload
            if payload.intermediate is None or payload.roles is None:
                raise MakeProbeError("successful toolchain recipe has no complete intermediate proof")
            compile_probes = tuple(row for row in result.runtime_probes if row.get("stage") == "compile")
            projected = project_compile_identity(
                compile_probes, payload.intermediate, payload.roles,
                reserve=lambda size: session.budget.charge("control", size),
            )
            session.budget.charge("control", 1024 + 16 * len(result.runtime_probes))
            compile_rows = iter(projected["runtime_probes"])
            projected["runtime_probes"] = [
                next(compile_rows) if row.get("stage") == "compile" else row
                for row in result.runtime_probes
            ]
            projection = _envelope_encode(session, projected)
        session.budget.charge("cache", 4096 + 128 * len(stages))
        sealed = replace(result, toolchain_receipts=tuple(stage.envelope for stage in stages))
        evidence = RecipeEvidence(
            sealed.toolchain_receipts, raw_result, grant.binding,
            session.snapshot.digest, session._namespace_epoch, projection,
        )
        key = id(sealed)
        capability = _RecipeResult(
            command, weakref.ref(sealed, lambda reference: self._recipe_results.pop(key, None)), grant,
            session.snapshot, session.tree, session._namespace_epoch, get_ident(),
            self._result_binding(sealed), evidence, sealed.stdout, sealed.stderr,
        )
        self._recipe_results[key] = capability
        self.issued.add(capability)
        return sealed

    def consume_recipe_result(self, command, result):
        session = self.session
        capability = self._recipe_results.pop(id(result), None)
        if type(capability) is not _RecipeResult or capability.result() is not result or capability.command is not command:
            raise MakeProbeError("toolchain recipe result is unissued, copied or replayed")
        session.budget.charge(
            "control", 32768 + 8 * len(capability.parent.binding) + 256 * len(command.code),
        )
        if (
            type(capability) is not _RecipeResult or capability not in self.issued
            or capability.result() is not result or capability.command is not command
            or capability.thread != get_ident() or capability.snapshot is not session.snapshot
            or capability.tree != session.tree or capability.epoch != session._namespace_epoch
            or capability.parent.context is not session._require_live_dispatch()
            or not session._command_dispatches
            or session._command_dispatches[-1] != (command, capability.parent.context)
            or capability.parent.binding != self.bind(command)
            or tuple(session.source_owners(command.code)) != capability.parent.inputs
        ):
            raise MakeProbeError("toolchain recipe result is copied, replayed or outside its live context")
        self.issued.discard(capability)
        if (
            capability.binding != self._result_binding(result)
            or result.stdout is not capability.stdout or result.stderr is not capability.stderr
        ):
            raise MakeProbeError("toolchain aggregate changed after sealing")
        session.budget.charge("cache", 1024)
        return capability.evidence

    def driver_identity(self, tool):
        from .make_probe import _trusted_runtime_path

        if str(_trusted_runtime_path(tool.path)) != tool.canonical:
            raise MakeProbeError("toolchain driver changed its captured canonical path")
        return self.image_identity(tool.path)

    def image_identity(self, path):
        from .make_probe import _trusted_runtime_path

        if str(_trusted_runtime_path(path, compiler=True)) != path:
            raise MakeProbeError("toolchain executable lost its canonical trusted identity")
        info = Path(path).lstat()
        identity = info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns
        self.session.budget.charge("control", len(encoded(identity)))
        return identity

    def launch(self, command, config):
        step = self.require_step(command)
        if step is None:
            raise MakeProbeError("toolchain launch has no issued substep")
        session = self.session
        root = session.base / f"command-root-{session.serial + 1}"
        output = session.base / f"command-{session.serial + 1}" / "output"
        mounts = [session._mount(session.tree, "/repo"), session._mount(Path("/usr"), "/usr", executable=True)]
        if step.stage >= 3:
            backing, sdk = step.parent.sdk
            mounts.extend(
                session._mount(backing / str(index), path)
                for index, (path, present) in enumerate(sdk["roots"]) if present
            )
        mounts.extend((
            session._mount(output, "/work", writable=True),
            session._mount(Path("/dev/null"), "/dev/null", writable=True),
        ))
        if (
            config["argv"] != list(command.argv) or config["code"] != sorted(set(command.code))
            or config["sources"] or config["enumerations"] or config["root"] != str(root)
            or config["mounts"] != mounts
            or config["environment"] != {**session._command_environment(step.parent.command()), "TMPDIR": "/work"}
            or config["dependency"]["toolchain_probe"]["stage"] != step.stage
            or config["dependency"]["toolchain_probe"]["driver_identity"] != list(step.parent.driver_identity)
            or step.dependency is None or encoded(config["dependency"]) != step.dependency
        ):
            raise MakeProbeError("toolchain issuance differs from its original substep and owned namespace")
        verify_workspace(output, config["dependency"]["toolchain_probe"]["workspace"])
        session.budget.charge("control", len(encoded(config["dependency"]["toolchain_probe"]["workspace"])))
        token = _Launch()
        binding = launch_binding(config)
        validate_launch({
            **config, "file_limit": session.budget.limits.file_bytes,
            "toolchain_runtime": {"version": 2, "scope": launch_scope(root), "binding": binding},
        })
        self.launches[id(token)] = token, command, step, binding
        self.issued.add(token)
        return token

    def consume_launch(self, token, config):
        record = self.launches.pop(id(token), None)
        if type(token) is not _Launch or token not in self.issued or record is None or record[0] is not token:
            raise MakeProbeError("toolchain launch is missing, copied, forged, expired or already consumed")
        self.issued.discard(token)
        _, command, step, binding = record
        if self.require_step(command) is not step or binding != launch_binding(config):
            raise MakeProbeError("toolchain launch changed its job, arguments, environment, input or workspace")
        output, = [Path(item["source"]) for item in config["mounts"] if item["target"] == "/work"]
        verify_workspace(output, config["dependency"]["toolchain_probe"]["workspace"])
        self.session.budget.charge("control", len(encoded(config["dependency"]["toolchain_probe"]["workspace"])))
        limits = IntermediateLimits(**{name: config[name] for name in (
            "file_limit", "observation_count", "observation_limit", "write_limit",
            "creation_limit", "process_limit", "memory_limit", "syscall_limit", "deadline",
        )})
        report = self.session.base / f"report-{self.session.serial}.json"
        if Path(config["report"]) != report:
            raise MakeProbeError("toolchain consumed launch has a foreign owned report path")
        self.session.budget.charge("cache", 2048)
        step.facts = _LaunchFacts(
            token, launch_scope(config["root"]), binding, report,
            _envelope_encode(self.session, config["dependency"]["toolchain_probe"], file_limit=limits.file_limit),
            limits, self.session.snapshot, self.session.tree, self.session._namespace_epoch, get_ident(),
        )
        step.launched = True
        step.phase = "launched"
        return {"version": 2, "scope": step.facts.scope, "binding": binding}

    def execute(self, command):
        from .make_probe import Command, ProcessOutput

        grant = self.require(command)
        if self.active is not None or grant.stage:
            raise MakeProbeError("toolchain command is nested or replayed")
        self.active = grant
        results, stdout, stderr = [], bytearray(), bytearray()
        status = 0
        try:
            self.session._verify_runtime_tool(command.runtime_tool)
            grant.driver_identity = self.driver_identity(command.runtime_tool)
            for stage in range(5):
                if stage != grant.stage:
                    raise MakeProbeError("toolchain substep order changed")
                subcommand = Command(
                    (command.runtime_tool.path, *grant.recipe.arguments(stage)),
                    code=command.code if stage == 3 else (), runtime_tool=command.runtime_tool,
                )
                self.session.budget.charge("cache", 2048)
                step = _Step(subcommand, grant, self.bind(subcommand), stage)
                self.steps[id(subcommand)] = step
                self.issued.add(step)
                try:
                    result = self.session._command(subcommand)
                    self.consume_step_result(step, result)
                finally:
                    self.retire_step(step)
                    self.steps.pop(id(subcommand), None)
                    self.issued.discard(step)
                results.append(result)
                grant.stage += 1
                if stage < 3:
                    value = result.stdout.rstrip(b"\n")
                    if result.returncode:
                        status = 1
                        stderr.extend((
                            "error: failed to run modern compiler: " + grant.recipe.compiler + "\n",
                            "error: could not query modern compiler target: " + grant.recipe.compiler + "\n",
                            "error: could not resolve the assembler used by modern GCC\n",
                        )[stage].encode())
                        break
                    if stage == 0:
                        stdout.extend(b"Modern compiler: " + value.split(b"\n")[0] + b"\n")
                    elif stage == 1:
                        if value != b"arm-none-eabi":
                            stderr.extend(b"error: modern compiler targets '" + value + b"'; expected 'arm-none-eabi'\n")
                            status = 1
                            break
                        stdout.extend(b"Modern target: " + value + b"\n")
                    else:
                        spelling, grant.assembler = assembler_path(value.decode("utf-8", "strict"))
                        grant.assembler_spelling = spelling
                        stdout.extend(b"Modern assembler: " + spelling.encode() + b"\n")
                else:
                    stdout.extend(result.stdout)
                    stderr.extend(result.stderr)
                    if result.returncode:
                        status = 1
                        stderr.extend((SYNTAX_FAILURE if stage == 3 else COMPILE_FAILURE).encode())
                        break
            if not status:
                stdout.extend((
                    "Modern flags: ARM7TDMI Thumb/interwork; config=%s; ABI=%s\n"
                    % (grant.recipe.config, grant.recipe.abi)
                ).encode())
            self.session._verify_runtime_tool(command.runtime_tool)
            if self.driver_identity(command.runtime_tool) != grant.driver_identity:
                raise MakeProbeError("toolchain driver changed during its actual original recipe")
            result = ProcessOutput(
                bytes(stdout), bytes(stderr), (), tuple(sorted({name for item in results for name in item.code_consumed})),
                input_identities=tuple(sorted({row for item in results for row in item.input_identities})),
                executed=tuple(name for item in results for name in item.executed),
                runtime_receipt=tuple(sorted({tuple(row) for item in results for row in item.runtime_receipt})),
                runtime_sources=tuple(sorted({row for item in results for row in item.runtime_sources})),
                runtime_probes=tuple(row for item in results for row in item.runtime_probes),
                returncode=status,
            )
            return self.seal_recipe_result(grant, command, result)
        finally:
            self.active = None
            self.commands.pop(id(command), None)
            self.issued.discard(grant)
