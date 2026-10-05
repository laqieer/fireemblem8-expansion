"""Bounded GNU4.3 read ABI and trace data; no namespace exception is granted."""

from __future__ import annotations

import base64
from collections import Counter
import hashlib
import posixpath
import re
import struct
import sys
from types import MappingProxyType
from typing import NamedTuple

if __package__:
    from . import make_lexical
    from .authority import encoded
    from .budget import MakeProbeError
    from .producer_channel import ChannelError, validate_publication_identity
else:
    import make_lexical
    from authority import encoded
    from budget import MakeProbeError
    from producer_channel import ChannelError, validate_publication_identity


GLOBALS = ("current_variable_set_list", "reading_file", "hash_deleted_item")
NORETURN = frozenset({"fatal", "die", "out_of_memory", "__stack_chk_fail", "__assert_fail", "abort"})
MAX_INSTRUCTIONS = 4096
COMPLETION_VERSION = 4


class ReadEpochError(MakeProbeError):
    pass


def _resolved_source_path(resolved):
    return posixpath.normpath(resolved if resolved.startswith("/") else "/repo/" + resolved)


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


def control_graph(image, begin, decoded):
    pending, seen, returns, edges = [begin], set(), [], {}
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
        edges[address] = []
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
            edges[address] = [following]
            pending.append(following)
            continue
        if raw[0] in {0xE9, 0xEB} or 0x70 <= raw[0] <= 0x7F or raw[:1] == b"\x0f" and len(raw) == 6 and 0x80 <= raw[1] <= 0x8F:
            start = 2 if raw[0] == 0x0F else 1
            target = following + int.from_bytes(raw[start:], "little", signed=True)
            pending.append(target)
            edges[address].append(target)
            if raw[0] not in {0xE9, 0xEB}:
                pending.append(following)
                edges[address].append(following)
            continue
        opcode = 0
        while opcode < len(raw) and (
            raw[opcode] in {0x26, 0x2E, 0x36, 0x3E, 0x64, 0x65, 0x66, 0x67, 0xF0, 0xF2, 0xF3}
            or 0x40 <= raw[opcode] <= 0x4F
        ):
            opcode += 1
        if (
            opcode + 1 < len(raw) and raw[opcode] == 0xFF
            and (raw[opcode + 1] >> 3) & 7 in {2, 3, 4, 5}
        ):
            raise ReadEpochError("source-reader indirect control flow is unsupported")
        pending.append(following)
        edges[address] = [following]
    return seen, returns, edges


def source_graph(image, begin, decoded):
    seen, returns, _ = control_graph(image, begin, decoded)
    if not returns:
        raise ReadEpochError("source reader lacks its real fopen call sites")
    return [begin, max(address + len(decoded[address]) for address in seen)], sorted(set(returns))


def _unique(pattern, body, label):
    matches = list(re.finditer(pattern, body, re.S))
    if len(matches) != 1:
        raise ReadEpochError("completion ABI lacks one machine predicate: " + label + " (matches=" + str(len(matches)) + ")")
    return matches[0]


def evaluator_target(image, source):
    start, end = source
    body = image.bytes(start, end - start, executable=True)
    call = _unique(rb"\x4d\x89\x55\x00(\xe8....)\x48\x8b\x7d.\x4d\x89\x7d\x00",
                   body, "direct reader evaluator call")
    return direct_call(start + call.start(1), call[1])


def completion_abi(image, source):
    """Derive coordinates from code operands, never from a host address table."""
    begin, end = source
    reader = image.bytes(begin, end - begin, executable=True)
    setup = _unique(rb"\x4c\x8d\x55(.)\x48\x8d\x45(.)", reader, "reader ebuffer/floc")
    reader_floc, reader_ebuffer = (int.from_bytes(setup[i], "little", signed=True) for i in (1, 2))
    if reader_floc - reader_ebuffer != 40:
        raise ReadEpochError("completion reader floc is not the LP64 ebuffer member")
    evaluator = evaluator_target(image, source)
    if not 0 < begin - evaluator <= 65536:
        raise ReadEpochError("completion evaluator has no bounded original code span")
    body = image.bytes(evaluator, begin - evaluator, executable=True)
    if not body.startswith(b"\x55\x48\x89\xe5"):
        raise ReadEpochError("completion evaluator lacks its frame-pointer entry")
    entry = _unique(rb"\x48\x89\xbd(....)", body[:128], "saved original ebuffer")
    floc = _unique(rb"\x48\x8d\x47(.)\x48\x89\x85(....)", body[:128], "saved fstart")
    if floc[1] != b"\x28":
        raise ReadEpochError("completion evaluator has a foreign floc layout")
    target, _ = image.symbol("try_variable_definition", 2)
    calls = [
        offset for offset in range(len(body) - 4)
        if direct_call(evaluator + offset, body[offset:offset + 5]) == target
        and body[offset + 5:offset + 9] == b"\x44\x0f\xb6\x95"
    ]
    if len(calls) != 1:
        raise ReadEpochError("completion evaluator lacks one ordinary definition call")
    tail = calls[0] + 5
    modifiers = _unique(
        rb"\x44\x0f\xb6\x95....\x48\x89\xc3\x41\xf6\xc2\x08"
        rb"\x74\x04\x80\x63\x2f\x9f\x41\x83\xe2\x20"
        rb"\x74\x04\x80\x4b\x2c\x80", body[tail:tail + 64], "post-modifier effective variable",
    )
    completion = evaluator + tail + modifiers.end()
    pending = _unique(
        rb"\xc7\x85....\x00\x00\x00\x00\x45\x31\xe4\x45\x31\xed"
        rb"\x48\xc7\x85....\x00\x00\x00\x00.*?\xe9(....)",
        body[completion - evaluator:completion - evaluator + 64], "completion to next-line loop",
    )
    branch = completion + pending.end() - 5
    loop = branch + 5 + int.from_bytes(pending[1], "little", signed=True)
    line = image.bytes(loop, 33, executable=True)
    match = re.match(
        rb"\x48\x8b\x9d(....)\x48\x8b\x85(....)\x48\x01\x43\x30"
        rb"\x48\x89\xdf\xe8....\x48\x89\x85(....)", line, re.S,
    )
    if match is None or match[1] != entry[1] or match[2] != match[3]:
        raise ReadEpochError("completion current physical-line count is not reader-derived")
    original_modifiers = _unique(
        rb"\x44\x0f\xb6\x95(....)\x49\x89\xc3\x41\xf6\xc2\x01", body,
        "original assignment-kind modifiers",
    )
    modifier_address = _unique(
        b"\x48\x8d\x85" + re.escape(original_modifiers[1]), body[:512],
        "address of original modifier structure",
    )
    saved_modifiers = _unique(rb"\x48\x89\x85(....)", body[
        modifier_address.end():modifier_address.end() + 192], "saved original modifier pointer")
    parser_call = _unique(
        b"\x48\x8b\xb5" + re.escape(saved_modifiers[1]) + rb"(\xe8....)"
        + re.escape(original_modifiers[0]), body, "original modifier parser writer/read association",
    )
    parser = direct_call(evaluator + parser_call.start(1), parser_call[1])
    if parser is None or not 0 < evaluator - parser <= 65536:
        raise ReadEpochError("completion original modifier parser has a foreign code extent")
    include_calls = [
        evaluator + offset + 5 for offset in range(len(body) - 4)
        if direct_call(evaluator + offset, body[offset:offset + 5]) == begin
    ]
    if len(include_calls) != 1:
        raise ReadEpochError("completion evaluator lacks one direct original include call")
    returned = _unique(
        rb"\x4d\x89\x55\x00(\xe8....)\x48\x8b\x7d.\x4d\x89\x7d\x00",
        reader, "reader evaluation return",
    )
    relied = []
    for name in ("try_variable_definition", "define_variable_in_set", "do_variable_definition"):
        address, size = image.symbol(name, 2)
        if not 0 < size <= 65536:
            raise ReadEpochError("completion variable decoder code is oversized")
        relied.append([address, address + size])
    definition = image.bytes(*(
        relied[1][0], relied[1][1] - relied[1][0]), executable=True)
    for pattern in (
        rb"\xbf\x30\x00\x00\x00", rb"\x41\x89\x5e\x28",
        rb"\x49\x89\x46\x08", rb"\x49\x89\x46\x20", rb"\x41\x0f\x11\x46\x10",
        rb"\xc1\xe3\x1a", rb"\xc0\xe8\x02",
    ):
        if not 1 <= len(list(re.finditer(pattern, definition, re.S))) <= 16:
            raise ReadEpochError("completion raw variable layout code is absent or ambiguous")
    flavor = image.bytes(relied[2][0], relied[2][1] - relied[2][0], executable=True)
    table_lea = _unique(
        rb"\x48\x8d\x15(....)\x45\x89\xc4\x48\x63\x04\x82\x48\x01\xd0\x3e\xff\xe0",
        flavor, "bounded variable flavor table",
    )
    table = relied[2][0] + table_lea.start() + 7 + int.from_bytes(table_lea[1], "little", signed=True)
    destinations = [table + offset for offset in struct.unpack("<7i", image.bytes(table, 28))]
    if len(set(destinations)) != 6 or destinations[3] != destinations[6]:
        raise ReadEpochError("completion variable flavors have a foreign dispatch table")
    for address in destinations:
        image.bytes(address, 1, executable=True)
    arms = (
        rb"\x4c\x8d\x25....\x4c\x8b\x2d....\x48\x89\xde\x31\xff"
        rb"\x48\xc7\xc2\xff\xff\xff\xff\x4d\x8b\x34\x24"
        rb"\x49\xc7\x04\x24\x00\x00\x00\x00\xe8....\x4c\x89\x2d....\x45\x31\xc0",
        rb"\x45\x31\xe4\x45\x31\xed\x41\xb8\x01\x00\x00\x00\x45\x31\xf6",
        rb"\x4c\x8b\xb5....\x4c\x89\xf7\xe8....\x8b\xb5",
        rb"\x4c\x8b\xb5....\x4c\x89\xf7\xe8....\x4c\x89\xf7\x45\x31\xf6",
        rb"\x4c\x8d\x25....\x48\xc7\xc2\xff\xff\xff\xff\x48\x89\xde\x31\xff",
    )
    expected_arms = [
        relied[2][0] + _unique(arm, flavor, "source-backed flavor arm " + str(index)).start()
        for index, arm in enumerate(arms)
    ]
    if destinations[1:6] != expected_arms:
        raise ReadEpochError("completion flavor table does not enter its independently located code arms")
    abort = direct_call(destinations[0], image.bytes(destinations[0], 5, executable=True))
    if abort is None or image.plt_name(abort) != "abort":
        raise ReadEpochError("completion invalid-flavor arm has no actual abort call")
    relied.append([parser, evaluator])
    eval_buffer, eval_size = image.symbol("eval_buffer", 2)
    eval_code = image.bytes(eval_buffer, eval_size, executable=True)
    eval_calls = [
        eval_buffer + offset + 5 for offset in range(len(eval_code) - 4)
        if direct_call(eval_buffer + offset, eval_code[offset:offset + 5]) == evaluator
    ]
    if len(eval_calls) != 1:
        raise ReadEpochError("completion copied-floc evaluator has no distinct actual caller")
    relied.append([eval_buffer, eval_buffer + eval_size])
    return {
        "evaluator": [evaluator, begin], "pc": completion,
        "reader_return": begin + returned.end(1), "include_return": include_calls[0],
        "reader_ebuffer": reader_ebuffer, "reader_floc": reader_floc,
        "eval_ebuffer": int.from_bytes(entry[1], "little", signed=True),
        "eval_floc": int.from_bytes(floc[2], "little", signed=True),
        "modifiers": int.from_bytes(original_modifiers[1], "little", signed=True),
        "nlines": int.from_bytes(match[2], "little", signed=True),
        "loop": loop, "relied_code": relied, "flavor_table": [table, table + 28],
        "eval_return": eval_calls[0],
    }


def make_abi(data, read_disassembly, source_disassembly, evaluator_disassembly=None):
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
    if evaluator_disassembly is not None:
        completion = completion_abi(image, source)
        decoded = instructions(evaluator_disassembly, image)
        seen, _, edges = control_graph(image, completion["evaluator"][0], decoded)
        pc = completion["pc"]
        predecessors = [address for address, following in edges.items() if pc in following]
        if (
            len(predecessors) != 3 or pc not in seen or completion["loop"] not in seen
            or any(not completion["evaluator"][0] <= address < completion["evaluator"][1] for address in seen)
            or not any(decoded[address][:1] == b"\xe9" for address in predecessors)
        ):
            raise ReadEpochError("completion evaluator assignment/undefine control flow is unclosed")
        for start in (result["read_all"][0], source[0]):
            code = read if start == result["read_all"][0] else instructions(source_disassembly, image)
            nodes, _, _ = control_graph(image, start, code)
            if sum(code[address] == b"\xc3" for address in nodes) != 1:
                raise ReadEpochError("completion slot borrowing lacks one ordinary caller return")
        result.update(version=2, completion=completion)
    validate_abi(result, data)
    return result


def validate_abi(value, data):
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int or value["version"] not in {1, 2}
        or set(value) != {"version", "image_sha256", "read_all", "source", "globals", "source_opens"} | (
            {"completion"} if value["version"] == 2 else set())
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
    if value["version"] == 2 and value["completion"] != completion_abi(image, value["source"]):
        raise ReadEpochError("completion ABI differs from independent machine operand discovery")
    return value


class OriginalVariable(NamedTuple):
    name: str
    value: str
    flags: int
    filename: str | None
    line: int
    column: int


class OriginalScope(NamedTuple):
    parent: bool
    variables: tuple[OriginalVariable, ...]


class OriginalSource(NamedTuple):
    number: int
    mode: int
    sha256: str
    data: bytes


class OriginalOpen(NamedTuple):
    seq: int
    name: str
    mode: str
    result: int
    source: OriginalSource | None
    identity: tuple | None
    path: str | None = None
    custody: object | None = None


class OriginalVisit(NamedTuple):
    number: int
    parent: int | None
    name: str
    flags: int
    return_flags: int
    entry_seq: int
    exit_seq: int
    resolved: str
    error: int
    source: OriginalSource | None
    opens: tuple[OriginalOpen, ...]
    location: tuple | None = None


class OriginalOtherOpen(NamedTuple):
    seq: int
    pass_number: int | None
    visit: int | None
    name: str
    mode: str
    result: int


class OriginalPass(NamedTuple):
    exec: int
    number: int
    entry_seq: int
    exit_seq: int
    inputs: tuple[OriginalScope, ...]
    visits: tuple[OriginalVisit, ...]
    other_opens: tuple[OriginalOtherOpen, ...]
    goal_visits: tuple[int, ...]
    entry_image: tuple | None
    completions: tuple = ()


class OriginalArchive(NamedTuple):
    scope: str
    passes: tuple[OriginalPass, ...]
    sources: tuple[OriginalSource, ...]
    version: int = 1
    selection: tuple = ()
    selection_inventory: tuple = ()


class OriginalCompletion(NamedTuple):
    seq: int
    visit: int
    source: int
    site: tuple
    name: str
    operator: str
    cwd: str
    variable: OriginalVariable


def physical_statements(data, *, checkpoint=lambda: None, count_limit=None):
    """Physical spans, independent of floc.offset (which is a line offset)."""
    if not isinstance(data, bytes) or b"\0" in data:
        raise ReadEpochError("completion source has unsupported bytes")
    text = data.decode("utf-8", "strict")
    pending, start, logical = [], 1, 0
    newline_count = text.count("\n")
    for index, line in enumerate(text.split("\n"), 1):
        checkpoint()
        if count_limit is not None and index > count_limit:
            raise ReadEpochError("completion physical source scan exceeds observation bound")
        has_lf = index <= newline_count
        if has_lf and line.endswith("\r"):
            line = line[:-1]
        pending.append(line)
        slashes = len(line) - len(line.rstrip("\\"))
        if has_lf and slashes % 2:
            continue
        logical += 1
        yield logical, start, index, "\n".join(pending)
        pending, start = [], index + 1


def completion_reference_names(data, *, names=None, checkpoint=lambda: None, count_limit=None, charge=lambda size: None):
    """Conservative prelaunch names, not Make grammar or assignment-site authority."""
    if not isinstance(data, bytes) or b"\0" in data:
        raise ReadEpochError("completion screening has unsupported bytes")
    text = data.decode("utf-8", "strict")
    names = set() if names is None else names

    def retain(name):
        # A longer maximal token cannot be an admitted literal name. Do not
        # split it into invented shorter references.
        if len(name) <= 128 and name not in names:
            if count_limit is not None and len(names) >= count_limit:
                raise ReadEpochError("completion reference names exceed observation bound")
            charge(128 + len(name))
            names.add(name)

    def tokens(start, stop):
        token, length = [], 0
        for index in range(start, stop + 1):
            if (index - start) % 4096 == 0:
                checkpoint()
            character = text[index] if index < stop else ""
            letter = bool(character) and character in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_"
            if letter or length and character and character in "0123456789":
                length += 1
                if length <= 128:
                    token.append(character)
            elif length:
                if length <= 128:
                    retain("".join(token))
                token, length = [], 0

    stack, start, index, next_checkpoint = [], None, 0, 0
    while index < len(text):
        if index >= next_checkpoint:
            checkpoint()
            next_checkpoint = index + 4096
        pair = text[index:index + 2]
        if pair in {"$(", "${"}:
            if not stack:
                start = index + 2
            charge(64)
            stack.append(")" if pair == "$(" else "}")
            index += 2
            continue
        if text[index] == "$" and len(pair) == 2 and pair[1] in (
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        ):
            retain(pair[1])
        if stack:
            if text[index] == stack[-1]:
                stack.pop()
                if not stack:
                    tokens(start, index)
            elif text[index] == ("(" if stack[-1] == ")" else "{"):
                charge(64)
                stack.append(stack[-1])
        index += 1
    if stack:
        tokens(start, len(text))
    checkpoint()
    collapsed = make_lexical._collapse_make_continuations(text.replace("\r\n", "\n"))
    for match in re.finditer(
        r"(?<![A-Za-z0-9_])(?:ifdef|ifndef)\s+([A-Za-z_][A-Za-z0-9_]*)", collapsed,
    ):
        checkpoint()
        retain(match[1])
    checkpoint()
    return names


def completion_source_facts(path, data, *, checkpoint=lambda: None, count_limit=None, charge=lambda size: None):
    if not isinstance(path, str) or not path or not isinstance(data, bytes):
        raise ReadEpochError("completion source facts require an exact byte source")
    if b"\0" in data:
        raise ReadEpochError("completion source has unsupported bytes")
    rows, dependencies, roots = [], {}, set()
    definition, depth = None, 0
    digest = hashlib.sha256(data).hexdigest()
    for logical, first, last, raw in physical_statements(
        data, checkpoint=checkpoint, count_limit=count_limit,
    ):
        statement = make_lexical.strip_comment(make_lexical._collapse_make_continuations(raw))
        header = statement.strip(make_lexical.MAKE_SPACE)
        if depth:
            if not raw.startswith("\t"):
                if re.match(r"^define(?:[ \t]|$)", header):
                    depth += 1
                elif re.match(r"^endef(?:[ \t]|$)", header):
                    depth -= 1
                    if not depth:
                        definition = None
                        continue
            names = make_lexical.references(statement)
            charge(len(encoded(sorted(names))))
            dependencies[definition].update(names)
            continue
        assignment = None if raw.startswith("\t") else make_lexical.MODE_ASSIGNMENT.fullmatch(statement)
        if assignment is None:
            names = make_lexical.references(statement)
            charge(len(encoded(sorted(names))))
            macro = None if raw.startswith("\t") else make_lexical.DEFINE.match(header)
            if macro is not None:
                definition, depth = macro[1], 1
                dependencies.setdefault(definition, set()).update(names)
            else:
                roots.update(names)
            continue
        name = assignment["name"]
        names = make_lexical.references(assignment["value"])
        charge(len(encoded((name, sorted(names)))))
        dependencies.setdefault(name, set()).update(names)
        prefix = statement[:assignment.start("name")].split()
        if "private" not in prefix:
            row = [
                path, digest, logical, first, last, name, assignment["operator"],
                hashlib.sha256(raw.encode("utf-8")).hexdigest(), "override" in prefix,
            ]
            charge(len(encoded(row)))
            rows.append(row)
            if count_limit is not None and len(rows) > count_limit:
                raise ReadEpochError("completion source selection exceeds observation count")
    if depth:
        raise ReadEpochError("completion selection encountered an unterminated define body")
    return rows, roots, dependencies


def completion_sites(path, data, names, *, checkpoint=lambda: None, count_limit=None, charge=lambda size: None):
    rows, _, _ = completion_source_facts(
        path, data, checkpoint=checkpoint, count_limit=count_limit, charge=charge,
    )
    selected = set(names)
    return [row for row in rows if row[5] in selected]


def require_completion_reference_closure(references, selected_names):
    if any(
        name not in selected_names and make_lexical.SCOPED.fullmatch("$(" + name + ")") is None
        for name in references
    ):
        raise ReadEpochError("opened source adds a consumer outside the frozen name closure")


def statement_at(data, start, nlines, *, checkpoint=lambda: None, count_limit=None):
    if type(start) is not int or type(nlines) is not int or start < 1 or nlines < 1:
        raise ReadEpochError("completion has an invalid physical statement span")
    for logical, first, last, raw in physical_statements(
        data, checkpoint=checkpoint, count_limit=count_limit,
    ):
        if first == start and last == start + nlines - 1:
            return logical, first, last, raw
        if first > start:
            break
    raise ReadEpochError("completion physical line count differs from original source bytes")


def _statement_index(data, *, checkpoint=lambda: None, count_limit=None, reserve=lambda size: None):
    rows = {}
    reserve(sys.getsizeof(rows))
    for logical, first, last, raw in physical_statements(
        data, checkpoint=checkpoint, count_limit=count_limit,
    ):
        value = (logical, first, last, hashlib.sha256(raw.encode()).hexdigest())
        reserve(
            sys.getsizeof({None: None}) + sys.getsizeof(first) + sys.getsizeof(value)
            + sum(sys.getsizeof(item) for item in value),
        )
        rows[first] = value
    return MappingProxyType(rows)


def variable_row(row):
    if (
        not isinstance(row, (list, tuple)) or len(row) != 6
        or not isinstance(row[0], str) or not 1 <= len(row[0].encode()) <= 128 or "\0" in row[0]
        or not isinstance(row[1], str) or len(row[1].encode()) > 65536 or "\0" in row[1]
        or any(type(row[index]) is not int or not 0 <= row[index] < 1 << 64 for index in (2, 4, 5))
        or row[2] >= 1 << 31 or (row[2] >> 26) & 7 > 6 or (row[2] >> 23) & 7 > 6
        or row[3] is not None and (not isinstance(row[3], str) or len(row[3].encode()) > 4096 or "\0" in row[3])
    ):
        raise ReadEpochError("original raw variable binding is malformed")
    return row


def original_variable(memory, pointer, string):
    if not pointer:
        raise ReadEpochError("ordinary original assignment returned no effective variable")
    name_ptr, value_ptr, filename, line, offset, length, flags = struct.unpack(
        "<QQQQQII", memory(pointer, 48))
    name, value = string(name_ptr, 129), string(value_ptr, 65536)
    if name is None or value is None or len(name.encode()) != length:
        raise ReadEpochError("completed original variable lacks its bounded raw name/value")
    return list(variable_row([name, value, flags, string(filename, 4096), line, offset]))


def validate_completion_sites(selection, *, count_limit, file_limit):
    if not isinstance(selection, list) or len(selection) > count_limit:
        raise ReadEpochError("completion selection exceeds its finite source bound")
    previous = None
    for row in selection:
        if (
            not isinstance(row, list) or len(row) != 9
            or not isinstance(row[0], str) or not row[0] or len(row[0].encode()) > 4096
            or row[0].startswith("/") or ".." in row[0].split("/") or "\0" in row[0]
            or "\\" in row[0] or any(part in {"", "."} for part in row[0].split("/"))
            or any(ord(character) < 32 or ord(character) == 127 for character in row[0])
            or not isinstance(row[1], str) or not re.fullmatch("[0-9a-f]{64}", row[1])
            or any(type(row[index]) is not int or not 1 <= row[index] <= file_limit for index in (2, 3, 4))
            or row[4] < row[3]
            or not isinstance(row[5], str) or not 1 <= len(row[5].encode()) <= 128
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", row[5]) is None
            or len(row[5].encode()) > 128
            or row[6] not in {":=", "::=", "=", "?=", "+=", "!="}
            or not isinstance(row[7], str) or not re.fullmatch("[0-9a-f]{64}", row[7])
            or type(row[8]) is not bool
        ):
            raise ReadEpochError("completion selection has a malformed immutable source site")
        key = tuple(row)
        if previous is not None and key <= previous:
            raise ReadEpochError("completion selected sites are repeated or unordered")
        previous = key
    return selection


def validate_completion_selection(selection, *, count_limit, file_limit):
    if (
        not isinstance(selection, dict)
        or set(selection) != {"version", "snapshot_sha256", "names", "inventory", "scan"}
        or type(selection["version"]) is not int or selection["version"] != 1
        or not isinstance(selection["snapshot_sha256"], str)
        or re.fullmatch("[0-9a-f]{64}", selection["snapshot_sha256"]) is None
        or not isinstance(selection["names"], list) or len(selection["names"]) > count_limit
        or not isinstance(selection["inventory"], list) or len(selection["inventory"]) > count_limit
        or not isinstance(selection["scan"], dict)
        or set(selection["scan"]) != {"entries", "bytes", "text", "binary", "invalid_utf8", "oversize"}
        or any(not isinstance(row, dict) for row in selection["inventory"])
        or type(selection["scan"]["entries"]) is not int
        or selection["scan"]["entries"] != len(selection["inventory"])
        or not 0 <= selection["scan"]["entries"] <= count_limit
        or any(type(selection["scan"][name]) is not int or selection["scan"][name] < 0
               for name in ("bytes", "text", "binary", "invalid_utf8", "oversize"))
        or sum(selection["scan"][name] for name in ("text", "binary", "invalid_utf8", "oversize"))
        != selection["scan"]["entries"]
        or selection["scan"]["text"] != sum(
            isinstance(row.get("screen"), str) and row["screen"] == "text" for row in selection["inventory"]
        )
        or selection["scan"]["binary"] != sum(
            isinstance(row.get("screen"), str) and row["screen"] == "binary" for row in selection["inventory"]
        )
        or selection["scan"]["invalid_utf8"] != sum(
            isinstance(row.get("screen"), str) and row["screen"] == "invalid-utf8"
            for row in selection["inventory"]
        )
        or selection["scan"]["oversize"] != sum(
            isinstance(row.get("screen"), str) and row["screen"].startswith("oversize")
            for row in selection["inventory"]
        )
        or selection["scan"]["bytes"] != sum(
            row.get("size", 0) if type(row.get("size")) is int else 0 for row in selection["inventory"]
            if not (isinstance(row.get("screen"), str) and row["screen"].startswith("oversize"))
        )
    ):
        raise ReadEpochError("completion selection has a malformed frozen name closure")
    names = selection["names"]
    try:
        valid_names = names == sorted(set(names)) and all(
            isinstance(name, str) and 1 <= len(name.encode("utf-8", "strict")) <= 128
            and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name) is not None
            for name in names
        )
    except UnicodeEncodeError as error:
        raise ReadEpochError("completion selection name is not strict UTF-8") from error
    if not valid_names:
        raise ReadEpochError("completion selection names are invalid, repeated or unordered")
    previous = None
    for row in selection["inventory"]:
        base = {"path", "kind", "mode", "size", "sha256"}
        publication = {"owner", "serial", "identity"} if isinstance(row, dict) and row.get("kind") == "prior-publication" else set()
        if (
            not isinstance(row, dict) or set(row) != base | publication | {"screen"}
            or not isinstance(row.get("path"), str) or not row["path"]
            or row["path"].startswith("/") or ".." in row["path"].split("/")
            or "\\" in row["path"] or any(part in {"", "."} for part in row["path"].split("/"))
            or "\0" in row["path"] or len(row["path"].encode("utf-8", "strict")) > 4096
            or not isinstance(row.get("kind"), str) or row["kind"] not in {"snapshot", "prior-publication"}
            or type(row.get("mode")) is not int or not 0 <= row["mode"] <= 0o777
            or type(row.get("size")) is not int or not 0 <= row["size"] < 1 << 63
            or row["screen"] not in {"text", "binary", "invalid-utf8", "oversize-binary"}
            or row["screen"].startswith("oversize") and (
                row["size"] <= file_limit or row["sha256"] is not None
            )
            or not row["screen"].startswith("oversize") and (
                row["size"] > file_limit or not isinstance(row["sha256"], str)
                or re.fullmatch("[0-9a-f]{64}", row["sha256"]) is None
            )
            or row["kind"] == "prior-publication" and (
                not isinstance(row.get("owner"), str) or re.fullmatch("[0-9a-f]{64}", row["owner"]) is None
                or type(row.get("serial")) is not int or row["serial"] < 1
                or not isinstance(row.get("identity"), list)
            )
        ):
            raise ReadEpochError("completion selection has a malformed immutable source inventory")
        if row["kind"] == "prior-publication":
            if row["screen"].startswith("oversize"):
                raise ReadEpochError("prior publication exceeds its selected-file admission")
            try:
                validate_publication_identity(row["identity"], row["mode"], row["size"])
            except ChannelError as error:
                raise ReadEpochError(str(error)) from error
        try:
            path_bytes = row["path"].encode("utf-8", "strict")
        except UnicodeEncodeError as error:
            raise ReadEpochError("completion inventory path is not strict UTF-8") from error
        if len(path_bytes) > 4096:
            raise ReadEpochError("completion inventory path exceeds its byte bound")
        key = row["path"]
        if previous is not None and key <= previous:
            raise ReadEpochError("completion source inventory is repeated or unordered")
        previous = key
    return selection


def validate_selection(selection, *, count_limit, file_limit):
    return validate_completion_sites(selection, count_limit=count_limit, file_limit=file_limit)


def reconstruct_archive(trace, *, budget):
    """Reconstruct data only; the session separately authenticates its observation."""
    budget.remaining()
    if not isinstance(trace, dict) or not isinstance(trace.get("scope"), str):
        raise ReadEpochError("original source archive requires a complete typed trace")
    validate_trace(
        trace, trace["scope"], count_limit=budget.limits.observation_count,
        file_limit=budget.limits.file_bytes, reserve=lambda size: budget.charge("cache", size),
    )
    budget.charge("cache", len(encoded(trace)))
    sources = {
        row["id"]: OriginalSource(row["id"], row["mode"], row["sha256"], base64.b64decode(row["data"], validate=True))
        for row in trace["sources"]
    }
    executions, visits = {}, {}
    for event in trace["events"]:
        budget.remaining()
        kind = event["kind"]
        if kind == "exec":
            executions[event["exec"]] = {"visits": [], "other": [], "image": None, "completions": []}
        elif kind == "pass-entry":
            executions[event["exec"]]["entry"] = event
        elif kind == "entry-image":
            executions[event["exec"]]["image"] = tuple(
                event[name] for name in ("seq", "barrier", "input_sha256", "image_sha256")
            )
        elif kind == "source-entry":
            visits[event["visit"]] = {"entry": event, "opens": []}
            executions[event["exec"]]["visits"].append(event["visit"])
        elif kind == "source-open":
            visits[event["visit"]]["opens"].append(OriginalOpen(
                event["seq"], event["name"], event["mode"], event["result"],
                None if event["source"] is None else sources[event["source"]],
                None if event["identity"] is None else tuple(event["identity"]),
                event.get("path"), event.get("custody"),
            ))
        elif kind == "source-exit":
            visits[event["visit"]]["exit"] = event
        elif kind == "assignment-completion":
            executions[event["exec"]]["completions"].append(OriginalCompletion(
                event["seq"], event["visit"], event["source"], tuple(event["site"]),
                event["name"], event["operator"], event["cwd"], OriginalVariable(*event["variable"]),
            ))
        elif kind == "other-open":
            executions[event["exec"]]["other"].append(OriginalOtherOpen(
                event["seq"], event["pass"], event["visit"], event["name"], event["mode"], event["result"],
            ))
        elif kind == "pass-exit":
            executions[event["exec"]]["exit"] = event
    passes = []
    for execution, value in executions.items():
        budget.remaining()
        entry, returned = value["entry"], value["exit"]
        ordered = []
        for number in value["visits"]:
            visit = visits[number]
            started, ended = visit["entry"], visit["exit"]
            ordered.append(OriginalVisit(
                number, started["parent"], started["name"], started["flags"], ended["flags"],
                started["seq"], ended["seq"], ended["resolved"], ended["error"],
                None if ended["source"] is None else sources[ended["source"]], tuple(visit["opens"]),
                None if started.get("location") is None else tuple(started["location"]),
            ))
        passes.append(OriginalPass(
            execution, entry["pass"], entry["seq"], returned["seq"],
            tuple(OriginalScope(scope["parent"], tuple(OriginalVariable(*row) for row in scope["variables"]))
                  for scope in entry["inputs"]),
            tuple(ordered), tuple(value["other"]), tuple(returned["goals"]), value["image"],
            tuple(value["completions"]),
        ))
    return OriginalArchive(
        trace["scope"], tuple(passes), tuple(sources.values()), trace["version"],
        tuple(trace["selection"]["names"]) if trace["version"] == COMPLETION_VERSION
        else tuple(tuple(row) for row in trace.get("selection", ())),
        tuple(trace["selection"]["inventory"]) if trace["version"] == COMPLETION_VERSION else (),
    )


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


def validate_machine_observations(value, trace, *, count_limit):
    if (
        not isinstance(value, dict) or set(value) != {"version", "events", "closed"}
        or type(value["version"]) is not int or value["version"] != 1 or value["closed"] is not True
        or not isinstance(value["events"], list) or not 1 <= len(value["events"]) <= count_limit
    ):
        raise ReadEpochError("incomplete native machine observations")
    common = {"seq", "kind", "trace_seq", "pid", "exec", "pass"}
    fields = {
        "clear": {"registers"}, "arm": {"registers", "slots"},
        "trap": {"index", "purpose", "pc", "status", "sigcode"},
        "pin-retired": {"visit", "source", "identity"},
        "execute": {"make", "dispatch"},
    }
    purposes = {"pass-entry", "source-entry", "source-return", "pass-return", "assignment-completion"}
    armed, retired = {}, set()
    previous = 0
    make_pid = None
    make_execs, child_dispatches = set(), set()
    for number, row in enumerate(value["events"], 1):
        if (
            not isinstance(row, dict) or not isinstance(row.get("kind"), str)
            or row["kind"] not in fields or set(row) != common | fields[row["kind"]]
            or type(row["seq"]) is not int or row["seq"] != number
            or type(row["trace_seq"]) is not int
            or not previous <= row["trace_seq"] < len(trace["events"])
            or type(row["pid"]) is not int or row["pid"] <= 0
            or any(type(row[key]) is not int or row[key] < 0 for key in ("exec", "pass"))
            or row["pass"] > row["exec"]
        ):
            raise ReadEpochError("malformed or unordered native machine observation")
        previous = row["trace_seq"]
        context = trace["events"][previous - 1] if previous else None
        if (
            previous == 0 and (row["kind"] not in {"clear", "execute"} or row["exec"] or row["pass"])
            or context is not None and "exec" in context and row["exec"] != context["exec"]
            or context is not None and "pass" in context and row["pass"] != context["pass"]
        ):
            raise ReadEpochError("native machine observation has a foreign trace context")
        kind, pid = row["kind"], row["pid"]
        if kind in {"clear", "arm"}:
            registers = row["registers"]
            if (
                not isinstance(registers, list) or len(registers) != 6
                or any(type(item) is not int or not 0 <= item < 1 << 64 for item in registers)
                or registers[4] & 0xE00F
            ):
                raise ReadEpochError("invalid native register readback")
            if kind == "clear":
                if any(registers[:4]) or registers[5]:
                    raise ReadEpochError("native clear retained an enabled or stale slot")
                armed.pop(pid, None)
                continue
            slots = row["slots"]
            if not isinstance(slots, list) or not 2 <= len(slots) <= 4:
                raise ReadEpochError("native arm lacks issued slots")
            issued = {}
            for slot in slots:
                if (
                    not isinstance(slot, list) or len(slot) != 3
                    or type(slot[0]) is not int or not 0 <= slot[0] < 4 or slot[0] in issued
                    or type(slot[1]) is not int or not 0 < slot[1] < 1 << 64
                    or not isinstance(slot[2], str) or slot[2] not in purposes
                ):
                    raise ReadEpochError("native arm has an invalid or duplicate slot")
                issued[slot[0]] = slot[1:]
            if (
                len({slot[0] for slot in issued.values()}) != len(issued)
                or issued.get(0, [None, None])[1] != "pass-entry"
                or issued.get(1, [None, None])[1] != "source-entry"
                or 2 in issued and issued[2][1] != "source-return"
                or 3 in issued and issued[3][1] not in {"assignment-completion", "pass-return"}
                or registers[:4] != [issued.get(index, [0])[0] for index in range(4)]
                or registers[5] != sum(1 << (2 * index) for index in issued)
            ):
                raise ReadEpochError("native readback differs from its issued slots/control")
            if make_pid is not None and pid != make_pid:
                raise ReadEpochError("native arm belongs to a foreign Make child")
            make_pid = pid
            armed[pid] = issued
        elif kind == "trap":
            if (
                any(type(row[key]) is not int for key in ("index", "pc", "status", "sigcode"))
                or not 0 <= row["status"] < 1 << 64 or not 0 < row["pc"] < 1 << 64
                or row["sigcode"] != 4 or not 0 <= row["index"] < 4
                or row["status"] & 15 != 1 << row["index"]
                or armed.get(pid, {}).get(row["index"]) != [row["pc"], row["purpose"]]
            ):
                raise ReadEpochError("native trap lacks its issued kernel slot/purpose")
            armed.pop(pid)
        elif kind == "execute":
            prior = value["events"][number - 2] if number > 1 else None
            if (
                type(row["make"]) is not bool or prior is None or prior["kind"] != "clear"
                or any(prior[key] != row[key] for key in ("trace_seq", "pid", "exec", "pass"))
                or row["make"] and row["dispatch"] is not None
                or not row["make"] and (
                    type(row["dispatch"]) is not int or row["dispatch"] <= 0
                    or row["dispatch"] in child_dispatches
                )
            ):
                raise ReadEpochError("native execution lacks its immediately preceding child clear")
            if row["make"]:
                make_execs.add((previous + 1, row["exec"] + 1, pid))
            else:
                child_dispatches.add(row["dispatch"])
        else:
            if (
                context is None or context["kind"] != "source-exit"
                or pid != make_pid
                or any(type(row[key]) is not int or row[key] <= 0 for key in ("visit", "source"))
                or (row["visit"], row["source"]) != (context["visit"], context["source"])
                or context["error"] != 0 or row["visit"] in retired
            ):
                raise ReadEpochError("native pin retirement lacks its successful source return")
            opens = [
                event for event in trace["events"][:previous]
                if event["kind"] == "source-open" and event["visit"] == row["visit"]
                and event["source"] == row["source"]
            ]
            if len(opens) != 1 or row["identity"] != opens[0]["identity"]:
                raise ReadEpochError("native retired pin identity differs from its actual open")
            retired.add(row["visit"])
    expected = {
        event["visit"] for event in trace["events"]
        if event["kind"] == "source-exit" and event["source"] is not None
    }
    trap_kinds = {
        "pass-entry": "pass-entry", "source-entry": "source-entry",
        "source-exit": "source-return", "assignment-completion": "assignment-completion",
        "pass-exit": "pass-return",
    }
    required = {
        (event["seq"] - 1, trap_kinds[event["kind"]])
        for event in trace["events"] if event["kind"] in trap_kinds
    }
    observed = {
        (row["trace_seq"], row["purpose"]) for row in value["events"] if row["kind"] == "trap"
    }
    if (
        retired != expected or not required <= observed
        or len(value["events"]) + len(trace["events"]) > count_limit
        or make_execs != {
            (event["seq"], event["exec"], make_pid)
            for event in trace["events"] if event["kind"] == "exec"
        }
    ):
        raise ReadEpochError("native machine observations omit traps or live pin retirement")
    return value


def validate_trace(value, scope, *, count_limit, file_limit, reserve=lambda size: None):
    if (
        not isinstance(value, dict) or type(value.get("version")) is not int or value["version"] not in {1, 2, 3, 4}
        or set(value) != {"version", "scope", "events", "sources", "complete"} | (
            {"selection"} if value["version"] in {3, COMPLETION_VERSION} else set()) | (
            {"machine"} if value["version"] == COMPLETION_VERSION and "machine" in value else set())
        or value["scope"] != scope
        or value["complete"] is not True or not isinstance(value["events"], list)
        or not 1 <= len(value["events"]) <= count_limit or not isinstance(value["sources"], list)
        or len(value["sources"]) > count_limit
    ):
        raise ReadEpochError("incomplete or foreign original read trace")
    sources, source_indexes = {}, {}
    selection = (
        validate_completion_sites(value["selection"], count_limit=count_limit, file_limit=file_limit)
        if value["version"] == 3 else
        validate_completion_selection(value["selection"], count_limit=count_limit, file_limit=file_limit)
        if value["version"] == COMPLETION_VERSION else []
    )
    selected = {tuple(row) for row in selection} if value["version"] == 3 else set()
    selected_names = set(selection["names"]) if value["version"] == COMPLETION_VERSION else set()
    inventory = {
        row["path"]: row for row in selection.get("inventory", ())
    } if value["version"] == COMPLETION_VERSION else {}
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

    def source_span(number, first, last):
        if number not in source_indexes:
            reserve(sources[number]["bytes"])
            data = base64.b64decode(sources[number]["data"], validate=True)
            source_indexes[number] = _statement_index(
                data, count_limit=count_limit, reserve=reserve,
            )
        span = source_indexes[number].get(first)
        if span is None or span[2] != last:
            raise ReadEpochError("completion location differs from original physical source")
        return span
    execs = passes = visits = 0
    active = []
    opened = {}
    opened_paths = {}
    opened_sites = {}
    used_sources = set()
    pass_visits = set()
    in_pass = False
    terminal = False
    barriers = 0
    pending_image = None
    completed = set()
    previous_sites = {}
    keys = {
        "exec": {"exec"}, "pass-entry": {"exec", "pass", "inputs"},
        "source-entry": {"exec", "pass", "visit", "parent", "name", "flags"},
        "source-open": {"exec", "pass", "visit", "name", "mode", "result", "source", "identity"},
        "other-open": {"exec", "pass", "visit", "name", "mode", "result"},
        "source-exit": {"exec", "pass", "visit", "resolved", "flags", "error", "source"},
        "pass-exit": {"exec", "pass", "goals"}, "complete": {"execs", "passes", "visits"},
        "entry-image": {"exec", "pass", "barrier", "input_sha256", "image_sha256"},
    }
    if value["version"] in {3, COMPLETION_VERSION}:
        keys["source-entry"] |= {"location"}
        keys["assignment-completion"] = {
            "exec", "pass", "visit", "source", "site", "name", "operator", "cwd", "variable",
        }
    if value["version"] == COMPLETION_VERSION:
        keys["source-open"] |= {"path", "custody"}
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
            if in_pass or active or not passes or value["version"] in {2, 3, 4} and barriers != passes or any(
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
            if value["version"] in {2, 3, 4}:
                pending_image = hashlib.sha256(encoded(inputs)).hexdigest()
            continue
        if not in_pass or event["pass"] != passes:
            raise ReadEpochError("source event has no active original pass")
        if kind == "entry-image":
            if (
                value["version"] not in {2, 3, 4} or pending_image is None
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
            if value["version"] in {3, COMPLETION_VERSION}:
                location = event["location"]
                if active:
                    if (
                        not isinstance(location, list) or len(location) != 5
                        or any(type(item) is not int for item in location)
                        or location[0] != active[-1]["visit"]
                        or location[1] != opened[location[0]] or location[1] not in sources
                    ):
                        raise ReadEpochError("include location has no original parent source version")
                    logical, first, last, _ = source_span(location[1], location[3], location[4])
                    if [logical, first, last] != location[2:]:
                        raise ReadEpochError("include directive location differs from original physical source")
                elif location is not None:
                    raise ReadEpochError("root source visit borrowed a directive location")
            visits += 1
            active.append(event)
            pass_visits.add(visits)
            opened[visits] = None
            opened_paths[visits] = None
            opened_sites[visits] = {}
        elif kind == "assignment-completion":
            site = event["site"]
            validate_completion_sites([site], count_limit=count_limit, file_limit=file_limit)
            site_allowed = (
                tuple(site) in selected if value["version"] == 3 else
                tuple(site) in opened_sites.get(event.get("visit"), {})
                if value["version"] == COMPLETION_VERSION else False
            )
            if (
                not active or type(event["visit"]) is not int or event["visit"] != active[-1]["visit"]
                or type(event["source"]) is not int or event["source"] != opened[event["visit"]]
                or event["source"] not in sources or not isinstance(site, list) or len(site) != 9
                or not site_allowed or event["name"] != site[5] or event["operator"] != site[6]
                or not isinstance(event["cwd"], str) or not event["cwd"].startswith("/")
                or len(event["cwd"].encode()) > 4096 or "\0" in event["cwd"]
                or (event["visit"], site[2]) in completed
                or site[2] <= previous_sites.get(event["visit"], 0)
                or (
                    site[0] != opened_paths[event["visit"]]
                    if value["version"] == COMPLETION_VERSION
                    else active[-1]["name"].removeprefix("/repo/") != site[0]
                )
            ):
                raise ReadEpochError("completion has a foreign, repeated, late or unselected occurrence")
            row = variable_row(event["variable"])
            if row[0] != event["name"] or row[2] & (1 << 7):
                raise ReadEpochError("completion returned a foreign or private binding")
            source = sources[event["source"]]
            logical, first, last, raw_digest = source_span(event["source"], site[3], site[4])
            if (
                source["sha256"] != site[1] or [logical, first, last] != site[2:5]
                or raw_digest != site[7]
            ):
                raise ReadEpochError("completion source bytes differ from its pinned selection visit")
            completed.add((event["visit"], site[2]))
            previous_sites[event["visit"]] = site[2]
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
                    if event["source"] is not None or event["identity"] is not None or (
                        value["version"] == COMPLETION_VERSION
                        and (event["path"] is not None or event["custody"] is not None)
                    ):
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
                    if value["version"] == COMPLETION_VERSION:
                        path, custody = event["path"], event["custody"]
                        if (
                            not isinstance(path, str) or not path or path.startswith("/")
                            or ".." in path.split("/") or "\\" in path
                            or any(part in {"", "."} for part in path.split("/"))
                        ):
                            raise ReadEpochError("completion source open has an invalid immutable path")
                        row = inventory.get(path)
                        if isinstance(custody, dict) and custody.get("kind") in {"snapshot", "prior-publication"}:
                            expected_keys = {"kind"} if custody["kind"] == "snapshot" else {"kind", "owner", "serial"}
                            if (
                                row is None or row["screen"] != "text" or row["kind"] != custody["kind"]
                                or set(custody) != expected_keys
                                or row["size"] != source["bytes"] or row["mode"] != source["mode"]
                                or row["sha256"] != source["sha256"]
                                or custody["kind"] == "prior-publication" and (
                                    custody["owner"] != row["owner"] or custody["serial"] != row["serial"]
                                    or identity != row["identity"]
                                )
                            ):
                                raise ReadEpochError("completion source open differs from its frozen inventory entry")
                        elif not (
                            isinstance(custody, dict) and set(custody) == {
                                "kind", "event", "producer", "slot", "owner",
                            } and custody["kind"] == "publication"
                            and all(type(custody[name]) is int and custody[name] > 0
                                    for name in ("event", "producer"))
                            and type(custody["slot"]) is int and custody["slot"] >= 0
                            and isinstance(custody["owner"], str)
                            and re.fullmatch("[0-9a-f]{64}", custody["owner"]) is not None
                        ):
                            raise ReadEpochError("completion source open lacks exact snapshot/publication custody")
                        data = base64.b64decode(source["data"], validate=True)
                        rows, references, _ = completion_source_facts(
                            path, data, checkpoint=lambda: reserve(0),
                            count_limit=count_limit, charge=reserve,
                        )
                        require_completion_reference_closure(references, selected_names)
                        sites = [item for item in rows if item[5] in selected_names]
                        opened_paths[event["visit"]] = path
                        opened_sites[event["visit"]] = {tuple(site): site for site in sites}
        elif kind == "source-exit":
            if (
                not active or type(event["visit"]) is not int or event["visit"] != active[-1]["visit"]
                or type(event["error"]) is not int or not 0 <= event["error"] <= 4095
                or type(event["flags"]) is not int or not 0 <= event["flags"] < 1 << 32
                or event["flags"] & 255 != active[-1]["flags"]
                or not isinstance(event["resolved"], str) or not event["resolved"] or len(event["resolved"].encode()) > 4096 or "\0" in event["resolved"]
                or event["source"] != opened[event["visit"]]
                or event["source"] is not None and type(event["source"]) is not int
                or (event["error"] == 0) != (event["source"] is not None)
            ):
                raise ReadEpochError("original source return/status is inconsistent")
            if value["version"] == COMPLETION_VERSION and event["source"] is not None:
                if _resolved_source_path(event["resolved"]) != "/repo/" + opened_paths[event["visit"]]:
                    raise ReadEpochError("completion source return names a different actual stream")
            active.pop()
        elif kind == "pass-exit":
            goals = event["goals"]
            if active or not isinstance(goals, list) or any(type(item) is not int for item in goals) or len(goals) != len(set(goals)) or set(goals) != pass_visits:
                raise ReadEpochError("original source pass omits a real goal/visit")
            in_pass = False
    if not terminal or passes != execs or not visits or used_sources != set(sources):
        raise ReadEpochError("original read trace has no complete terminal lifetime")
    if "machine" in value:
        reserve(len(encoded(value["machine"])))
        validate_machine_observations(value["machine"], value, count_limit=count_limit)
    return value
