"""Bounded GNU4.3 read ABI and trace data; no namespace exception is granted."""

from __future__ import annotations

import base64
from collections import Counter
import hashlib
import re
import struct

if __package__:
    from .authority import encoded
    from .budget import MakeProbeError
    from .producer_channel import ChannelError, validate_publication_identity
else:
    from authority import encoded
    from budget import MakeProbeError
    from producer_channel import ChannelError, validate_publication_identity


GLOBALS = ("current_variable_set_list", "reading_file", "hash_deleted_item")
NORETURN = frozenset({"fatal", "die", "out_of_memory", "__stack_chk_fail"})
MAX_INSTRUCTIONS = 4096


class ReadEpochError(MakeProbeError):
    pass


class Elf:
    def __init__(self, data):
        if (
            not isinstance(data, bytes) or len(data) < 64 or data[:6] != b"\x7fELF\x02\x01"
            or int.from_bytes(data[18:20], "little") != 62
            or int.from_bytes(data[16:18], "little") not in {2, 3}
        ):
            raise ReadEpochError("read ABI requires the actual x86-64 Make ELF")
        self.data = data
        self.loads = []
        phoff = int.from_bytes(data[32:40], "little")
        phsize, phcount = struct.unpack_from("<HH", data, 54)
        if phsize != 56 or not 1 <= phcount <= 64 or phoff + phsize * phcount > len(data):
            raise ReadEpochError("read ABI ELF has invalid load headers")
        for index in range(phcount):
            kind, flags, offset, address, _, size, extent, _ = struct.unpack_from("<IIQQQQQQ", data, phoff + index * phsize)
            if offset + size > len(data) or extent < size:
                raise ReadEpochError("read ABI ELF segment escapes captured bytes")
            if kind == 1:
                self.loads.append((address, extent, offset, size, flags))
        shoff = int.from_bytes(data[40:48], "little")
        shsize, shcount = struct.unpack_from("<HH", data, 58)
        if shsize != 64 or not 1 <= shcount <= 4096 or shoff + shsize * shcount > len(data):
            raise ReadEpochError("read ABI ELF has invalid section headers")
        sections = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + index * shsize) for index in range(shcount)]
        self.symbols, symbols_by_section = {}, {}
        for index, section in enumerate(sections):
            _, kind, _, _, offset, size, linked, _, _, stride = section
            if kind != 11:
                continue
            if stride != 24 or size % stride or offset + size > len(data) or linked >= len(sections):
                raise ReadEpochError("read ABI dynamic symbols are malformed")
            strings = sections[linked]
            if strings[4] + strings[5] > len(data):
                raise ReadEpochError("read ABI string table escapes captured bytes")
            names = data[strings[4]:strings[4] + strings[5]]
            rows = []
            for cursor in range(offset, offset + size, stride):
                name, info, _, section_index, value, extent = struct.unpack_from("<IBBHQQ", data, cursor)
                if name >= len(names) or b"\0" not in names[name:]:
                    raise ReadEpochError("read ABI symbol name escapes its string table")
                label = names[name:names.index(b"\0", name)].decode("ascii", "strict")
                rows.append(label)
                if section_index and label:
                    if label in self.symbols:
                        raise ReadEpochError("read ABI symbol is ambiguous")
                    self.symbols[label] = (value, extent, info & 15)
            symbols_by_section[index] = rows
        self.relocations = {}
        for section in sections:
            _, kind, _, _, offset, size, linked, _, _, stride = section
            if kind != 4 or linked not in symbols_by_section:
                continue
            if stride != 24 or size % stride or offset + size > len(data):
                raise ReadEpochError("read ABI relocations are malformed")
            symbols = symbols_by_section[linked]
            for cursor in range(offset, offset + size, stride):
                target, info, _ = struct.unpack_from("<QQq", data, cursor)
                symbol = info >> 32
                if symbol >= len(symbols):
                    raise ReadEpochError("read ABI relocation has a foreign symbol")
                if info & 0xFFFFFFFF == 7:
                    self.relocations[target] = symbols[symbol]

    def bytes(self, address, count, *, executable=False):
        for start, _, offset, size, flags in self.loads:
            if start <= address and address + count <= start + size and (not executable or flags & 1):
                begin = offset + address - start
                return self.data[begin:begin + count]
        raise ReadEpochError("read ABI address escapes its captured ELF segment")

    def symbol(self, name, kind):
        result = self.symbols.get(name)
        if result is None or result[2] != kind or not result[1]:
            raise ReadEpochError("read ABI lacks its exact exported anchor: " + name)
        return result[:2]

    def plt_name(self, address):
        if not any(start <= address and address + 16 <= start + size and flags & 1
                   for start, extent, offset, size, flags in self.loads):
            return None
        raw = self.bytes(address, 16, executable=True)
        position = 4 if raw.startswith(b"\xf3\x0f\x1e\xfa") else 0
        if raw[position:position + 1] == b"\xf2":
            position += 1
        if raw[position:position + 2] != b"\xff\x25":
            return None
        displacement = int.from_bytes(raw[position + 2:position + 6], "little", signed=True)
        return self.relocations.get(address + position + 6 + displacement)


def instructions(data, image):
    if not isinstance(data, bytes) or len(data) > 1024 * 1024:
        raise ReadEpochError("read ABI disassembly exceeds its existing output bound")
    result = {}
    for line in data.decode("ascii", "strict").splitlines():
        match = re.match(r"^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2}\s+)+)\s*(.*?)\s*$", line)
        if match is None:
            continue
        address = int(match[1], 16)
        raw = bytes.fromhex(match[2])
        if not 1 <= len(raw) <= 15 or image.bytes(address, len(raw), executable=True) != raw or address in result:
            raise ReadEpochError("decoded read ABI instruction differs from the actual image")
        result[address] = raw
    if not result:
        raise ReadEpochError("read ABI has no decoded instructions")
    return result


def direct_call(address, raw):
    return address + 5 + int.from_bytes(raw[1:], "little", signed=True) if len(raw) == 5 and raw[0] == 0xE8 else None


def source_target(image, decoded):
    begin, size = image.symbol("read_all_makefiles", 2)
    known = {value for value, _, kind in image.symbols.values() if kind == 2}
    calls = Counter(
        target for address, raw in decoded.items()
        if begin <= address < begin + size
        for target in (direct_call(address, raw),)
        if target is not None and target not in known and image.plt_name(target) is None
    )
    candidates = [target for target, count in calls.items() if count >= 2]
    if len(candidates) != 1:
        raise ReadEpochError("read ABI lacks one repeated actual source-reader call target")
    return candidates[0]


def source_graph(image, begin, decoded):
    pending, seen, returns = [begin], set(), []
    names = {value: name for name, (value, _, kind) in image.symbols.items() if kind == 2}
    while pending:
        address = pending.pop()
        if address in seen:
            continue
        if len(seen) >= MAX_INSTRUCTIONS or address not in decoded:
            raise ReadEpochError("source-reader control flow is unclosed or oversized")
        seen.add(address)
        raw = decoded[address]
        following = address + len(raw)
        if raw in (b"\xc3", b"\xf3\xc3"):
            continue
        target = direct_call(address, raw)
        if target is not None:
            image.bytes(target, 1, executable=True)
            name = names.get(target) or image.plt_name(target)
            if name in NORETURN:
                continue
            if name == "fopen":
                returns.append(following)
            pending.append(following)
            continue
        if raw[0] in {0xE9, 0xEB} or 0x70 <= raw[0] <= 0x7F or raw[:1] == b"\x0f" and len(raw) == 6 and 0x80 <= raw[1] <= 0x8F:
            start = 2 if raw[0] == 0x0F else 1
            target = following + int.from_bytes(raw[start:], "little", signed=True)
            pending.append(target)
            if raw[0] not in {0xE9, 0xEB}:
                pending.append(following)
            continue
        if raw[0] == 0xFF and len(raw) > 1 and (raw[1] >> 3) & 7 in {2, 3, 4, 5}:
            raise ReadEpochError("source-reader indirect control flow is unsupported")
        pending.append(following)
    if not returns:
        raise ReadEpochError("source reader lacks its real fopen call sites")
    return [begin, max(address + len(decoded[address]) for address in seen)], sorted(set(returns))


def make_abi(data, read_disassembly, source_disassembly):
    image = Elf(data)
    read = instructions(read_disassembly, image)
    target = source_target(image, read)
    source, opens = source_graph(image, target, instructions(source_disassembly, image))
    start, size = image.symbol("read_all_makefiles", 2)
    result = {
        "version": 1, "image_sha256": hashlib.sha256(data).hexdigest(),
        "read_all": [start, start + size], "source": source,
        "globals": {name: image.symbol(name, 1)[0] for name in GLOBALS},
        "source_opens": opens,
    }
    validate_abi(result, data)
    return result


def validate_abi(value, data):
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "image_sha256", "read_all", "source", "globals", "source_opens"}
        or type(value["version"]) is not int or value["version"] != 1
        or value["image_sha256"] != hashlib.sha256(data).hexdigest()
        or not isinstance(value["globals"], dict) or set(value["globals"]) != set(GLOBALS)
        or not isinstance(value["source_opens"], list) or not 1 <= len(value["source_opens"]) <= 64
        or any(type(address) is not int for address in value["source_opens"])
        or value["source_opens"] != sorted(set(value["source_opens"]))
    ):
        raise ReadEpochError("unbound or malformed original-read ABI")
    image = Elf(data)
    for name in ("read_all", "source"):
        span = value[name]
        if (
            not isinstance(span, list) or len(span) != 2 or any(type(item) is not int for item in span)
            or not 0 < span[1] - span[0] <= 65536
        ):
            raise ReadEpochError("invalid original-read executable span")
        image.bytes(span[0], span[1] - span[0], executable=True)
    start, size = image.symbol("read_all_makefiles", 2)
    if value["read_all"] != [start, start + size]:
        raise ReadEpochError("read entry differs from its actual exported function")
    source_start, source_end = value["source"]
    prologue = image.bytes(source_start, 8, executable=True)
    if not prologue.startswith((b"\x55\x48\x89\xe5", b"\xf3\x0f\x1e\xfa\x55\x48\x89\xe5")):
        raise ReadEpochError("source reader lacks the validated frame ABI")
    read_body = image.bytes(start, size, executable=True)
    calls = sum(
        direct_call(start + offset, read_body[offset:offset + 5]) == source_start
        for offset in range(max(0, len(read_body) - 4))
    )
    if calls < 2:
        raise ReadEpochError("source reader is not the repeated actual read-entry callee")
    # Discovery uses decoded CFGs; this independent byte check refuses a
    # dropped fopen site even when a supplied subset is otherwise well formed.
    body = image.bytes(source_start, source_end - source_start, executable=True)
    opens = []
    for offset in range(max(0, len(body) - 4)):
        target = direct_call(source_start + offset, body[offset:offset + 5])
        if target is not None and image.plt_name(target) == "fopen":
            opens.append(source_start + offset + 5)
    if value["source_opens"] != opens:
        raise ReadEpochError("source open call-site capture is incomplete")
    for name in GLOBALS:
        if type(value["globals"][name]) is not int or value["globals"][name] != image.symbol(name, 1)[0]:
            raise ReadEpochError("read input anchor differs from its exported data")
    for address in value["source_opens"]:
        if not value["source"][0] < address <= value["source"][1]:
            raise ReadEpochError("source fopen call escapes the source-reader span")
        target = direct_call(address - 5, image.bytes(address - 5, 5, executable=True))
        if target is None or image.plt_name(target) != "fopen":
            raise ReadEpochError("source open is not an actual fopen call")
    return value


def original_inputs(memory, pointer, deleted, *, count_limit, string):
    seen, result = set(), []
    while pointer:
        if pointer in seen or len(seen) >= count_limit:
            raise ReadEpochError("original variable-set scope is cyclic or excessive")
        seen.add(pointer)
        following, table, parent = struct.unpack_from("<QQi", memory(pointer, 24))
        if parent not in (0, 1):
            raise ReadEpochError("original variable-set parent flag is invalid")
        header = memory(table, 88)
        vector = int.from_bytes(header[:8], "little")
        size, capacity, fill, empty = struct.unpack_from("<QQQQ", header, 32)
        if not 1 <= size <= count_limit or size & (size - 1) or not 0 <= fill <= capacity <= size or not 0 <= empty <= size:
            raise ReadEpochError("original variable-set table extent is invalid")
        rows = []
        for offset in range(0, size * 8, 65536):
            block = memory(vector + offset, min(65536, size * 8 - offset))
            for index in range(0, len(block), 8):
                address = int.from_bytes(block[index:index + 8], "little")
                if address in (0, deleted):
                    continue
                name_ptr, value_ptr, filename, line, column, length, flags = struct.unpack("<QQQQQII", memory(address, 48))
                name = string(name_ptr, 129)
                if name is None or len(name.encode("utf-8")) != length:
                    raise ReadEpochError("original variable name disagrees with its actual extent")
                value = string(value_ptr, 65536)
                if value is None:
                    raise ReadEpochError("original variable has no raw value")
                rows.append([name, value, flags, string(filename, 4096), line, column])
        if len(rows) != fill or len({row[0] for row in rows}) != len(rows):
            raise ReadEpochError("original variable-set capture is incomplete")
        result.append({"parent": bool(parent), "variables": sorted(rows)})
        pointer = following
    if not result:
        raise ReadEpochError("original variable-set capture is empty")
    return result


def validate_trace(value, scope, *, count_limit, file_limit, reserve=lambda size: None):
    if (
        not isinstance(value, dict) or set(value) != {"version", "scope", "events", "sources", "complete"}
        or type(value["version"]) is not int or value["version"] not in {1, 2} or value["scope"] != scope
        or value["complete"] is not True or not isinstance(value["events"], list)
        or not 1 <= len(value["events"]) <= count_limit or not isinstance(value["sources"], list)
        or len(value["sources"]) > count_limit
    ):
        raise ReadEpochError("incomplete or foreign original read trace")
    sources = {}
    for row in value["sources"]:
        if (
            not isinstance(row, dict) or set(row) != {"id", "mode", "bytes", "sha256", "data"}
            or type(row["id"]) is not int or row["id"] != len(sources) + 1
            or type(row["mode"]) is not int or not 0 <= row["mode"] <= 0o777
            or type(row["bytes"]) is not int or not 0 <= row["bytes"] <= file_limit
            or not isinstance(row["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", row["sha256"])
            or not isinstance(row["data"], str) or len(row["data"]) != 4 * ((row["bytes"] + 2) // 3)
        ):
            raise ReadEpochError("invalid original source snapshot")
        reserve(row["bytes"])
        try:
            data = base64.b64decode(row["data"], validate=True)
        except ValueError as error:
            raise ReadEpochError("invalid original source base64") from error
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"] or base64.b64encode(data).decode() != row["data"]:
            raise ReadEpochError("original source snapshot differs from its captured bytes")
        sources[row["id"]] = row
    execs = passes = visits = 0
    active = []
    opened = {}
    used_sources = set()
    pass_visits = set()
    in_pass = False
    terminal = False
    barriers = 0
    pending_image = None
    keys = {
        "exec": {"exec"}, "pass-entry": {"exec", "pass", "inputs"},
        "source-entry": {"exec", "pass", "visit", "parent", "name", "flags"},
        "source-open": {"exec", "pass", "visit", "name", "mode", "result", "source", "identity"},
        "other-open": {"exec", "pass", "visit", "name", "mode", "result"},
        "source-exit": {"exec", "pass", "visit", "resolved", "flags", "error", "source"},
        "pass-exit": {"exec", "pass", "goals"}, "complete": {"execs", "passes", "visits"},
        "entry-image": {"exec", "pass", "barrier", "input_sha256", "image_sha256"},
    }
    for sequence, event in enumerate(value["events"], 1):
        if (
            terminal or not isinstance(event, dict) or not isinstance(event.get("kind"), str)
            or event["kind"] not in keys or set(event) != keys[event["kind"]] | {"seq", "kind"}
            or type(event["seq"]) is not int or event["seq"] != sequence
        ):
            raise ReadEpochError("malformed or out-of-order original read event")
        kind = event["kind"]
        if pending_image is not None and kind != "entry-image":
            raise ReadEpochError("original read began before its namespace entry image")
        if kind == "exec":
            if in_pass or active or type(event["exec"]) is not int or event["exec"] != execs + 1:
                raise ReadEpochError("original read exec lifetime is incomplete")
            execs += 1
            continue
        if kind == "complete":
            if in_pass or active or not passes or value["version"] == 2 and barriers != passes or any(
                type(event[name]) is not int or event[name] != expected
                for name, expected in (("execs", execs), ("passes", passes), ("visits", visits))
            ):
                raise ReadEpochError("original read terminal counters are inconsistent")
            terminal = True
            continue
        if kind == "other-open" and event["pass"] is None:
            if (
                in_pass or type(event["exec"]) is not int or event["exec"] != execs or event["visit"] is not None
                or not isinstance(event["name"], str) or not event["name"] or len(event["name"].encode()) > 4096 or "\0" in event["name"]
                or not isinstance(event["mode"], str) or not event["mode"] or len(event["mode"]) > 16 or "\0" in event["mode"]
                or type(event["result"]) is not int or not -4095 <= event["result"] <= 127
            ):
                raise ReadEpochError("non-source I/O has a false original pass")
            continue
        if type(event["exec"]) is not int or event["exec"] != execs or type(event["pass"]) is not int:
            raise ReadEpochError("original read event has a foreign exec/pass")
        if kind == "pass-entry":
            if in_pass or event["pass"] != passes + 1 or passes + 1 != execs:
                raise ReadEpochError("original read pass is missing or repeated")
            inputs = event["inputs"]
            if not isinstance(inputs, list) or not 1 <= len(inputs) <= count_limit:
                raise ReadEpochError("original input scopes are invalid")
            total = 0
            for scope_row in inputs:
                if not isinstance(scope_row, dict) or set(scope_row) != {"parent", "variables"} or type(scope_row["parent"]) is not bool or not isinstance(scope_row["variables"], list):
                    raise ReadEpochError("original input scope is malformed")
                names = []
                for row in scope_row["variables"]:
                    total += 1
                    if (
                        total > count_limit or not isinstance(row, list) or len(row) != 6
                        or not isinstance(row[0], str) or not 1 <= len(row[0].encode()) <= 128 or "\0" in row[0]
                        or not isinstance(row[1], str) or len(row[1].encode()) > 65536 or "\0" in row[1]
                        or any(type(row[index]) is not int or not 0 <= row[index] < 1 << 64 for index in (2, 4, 5))
                        or row[2] >= 1 << 31 or (row[2] >> 26) & 7 > 6 or (row[2] >> 23) & 7 > 6
                        or row[3] is not None and (not isinstance(row[3], str) or len(row[3].encode()) > 4096 or "\0" in row[3])
                    ):
                        raise ReadEpochError("original raw variable input is malformed")
                    names.append(row[0])
                if names != sorted(set(names)):
                    raise ReadEpochError("original raw variable inputs are repeated or unordered")
            passes += 1
            in_pass = True
            pass_visits = set()
            if value["version"] == 2:
                pending_image = hashlib.sha256(encoded(inputs)).hexdigest()
            continue
        if not in_pass or event["pass"] != passes:
            raise ReadEpochError("source event has no active original pass")
        if kind == "entry-image":
            if (
                value["version"] != 2 or pending_image is None
                or type(event["barrier"]) is not int or event["barrier"] != barriers + 1
                or event["input_sha256"] != pending_image
                or not isinstance(event["image_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", event["image_sha256"])
            ):
                raise ReadEpochError("original entry image differs from its actual read inputs")
            barriers += 1
            pending_image = None
        elif kind == "source-entry":
            if (
                type(event["visit"]) is not int or event["visit"] != visits + 1
                or event["parent"] is not None and type(event["parent"]) is not int
                or event["parent"] != (active[-1]["visit"] if active else None)
                or not isinstance(event["name"], str) or not event["name"] or len(event["name"].encode()) > 4096 or "\0" in event["name"]
                or type(event["flags"]) is not int or not 0 <= event["flags"] <= 15
            ):
                raise ReadEpochError("invalid original source entry")
            visits += 1
            active.append(event)
            pass_visits.add(visits)
            opened[visits] = None
        elif kind in {"source-open", "other-open"}:
            if (
                not isinstance(event["name"], str) or not event["name"] or len(event["name"].encode()) > 4096 or "\0" in event["name"]
                or not isinstance(event["mode"], str) or not event["mode"] or len(event["mode"]) > 16 or "\0" in event["mode"]
                or type(event["result"]) is not int or not -4095 <= event["result"] <= 127
                or event["visit"] is not None and type(event["visit"]) is not int
                or event["visit"] != (active[-1]["visit"] if active else None)
            ):
                raise ReadEpochError("unbound original source I/O")
            if kind == "source-open":
                if not active or event["mode"] not in {"r", "re"}:
                    raise ReadEpochError("source open has no readonly source entry")
                if event["result"] < 0:
                    if event["source"] is not None or event["identity"] is not None:
                        raise ReadEpochError("failed source open claims captured contents")
                elif type(event["source"]) is not int or event["source"] not in sources or opened[event["visit"]] is not None:
                    raise ReadEpochError("source open has a missing or repeated snapshot")
                else:
                    identity = event["identity"]
                    source = sources[event["source"]]
                    try:
                        validate_publication_identity(identity, source["mode"], source["bytes"])
                    except ChannelError as error:
                        raise ReadEpochError(str(error)) from error
                    opened[event["visit"]] = event["source"]
                    used_sources.add(event["source"])
        elif kind == "source-exit":
            if (
                not active or type(event["visit"]) is not int or event["visit"] != active[-1]["visit"]
                or type(event["error"]) is not int or not 0 <= event["error"] <= 4095
                or type(event["flags"]) is not int or event["flags"] & 255 != active[-1]["flags"]
                or not isinstance(event["resolved"], str) or not event["resolved"] or len(event["resolved"].encode()) > 4096 or "\0" in event["resolved"]
                or event["source"] != opened[event["visit"]]
                or event["source"] is not None and type(event["source"]) is not int
                or (event["error"] == 0) != (event["source"] is not None)
            ):
                raise ReadEpochError("original source return/status is inconsistent")
            active.pop()
        elif kind == "pass-exit":
            goals = event["goals"]
            if active or not isinstance(goals, list) or any(type(item) is not int for item in goals) or len(goals) != len(set(goals)) or set(goals) != pass_visits:
                raise ReadEpochError("original source pass omits a real goal/visit")
            in_pass = False
    if not terminal or passes != execs or not visits or used_sources != set(sources):
        raise ReadEpochError("original read trace has no complete terminal lifetime")
    return value
