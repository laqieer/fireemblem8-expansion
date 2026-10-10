"""Bounded GNU4.3 read ABI and trace data; no namespace exception is granted."""

from __future__ import annotations

import base64
from array import array
from bisect import bisect_right
import errno
from collections import Counter
from collections.abc import Mapping
import hashlib
import os
import posixpath
import re
import stat
import struct
import sys
from types import MappingProxyType
from typing import NamedTuple

if __package__:
    from . import make_lexical
    from .authority import encoded, native_command_owner, relative_path
    from .budget import MakeProbeError
    from .producer_channel import ChannelError, validate_publication_identity
else:
    import make_lexical
    from authority import encoded, native_command_owner, relative_path
    from budget import MakeProbeError
    from producer_channel import ChannelError, validate_publication_identity


GLOBALS = ("current_variable_set_list", "reading_file", "hash_deleted_item")
NORETURN = frozenset({"fatal", "die", "out_of_memory", "__stack_chk_fail", "__assert_fail", "abort"})
MAX_INSTRUCTIONS = 4096
COMPLETION_VERSION = 4
RUNTIME_VERSION = 5
WRITABLE_VERSION = 6
PATTERN_RUNTIME_VERSION = 7
PATTERN_WRITABLE_VERSION = 8
PATTERN_VERSIONS = frozenset({PATTERN_RUNTIME_VERSION, PATTERN_WRITABLE_VERSION})
RUNTIME_VERSIONS = frozenset({RUNTIME_VERSION, WRITABLE_VERSION, *PATTERN_VERSIONS})
WRITABLE_VERSIONS = frozenset({WRITABLE_VERSION, PATTERN_WRITABLE_VERSION})
MACHINE_VERSIONS = frozenset({COMPLETION_VERSION, *RUNTIME_VERSIONS})
LOCATION_VERSIONS = frozenset({3, *MACHINE_VERSIONS})
FINITE_MACHINE_VERSIONS = frozenset({2, 4})


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


def runtime_expansion_abi(image):
    def function(name):
        start, size = image.symbol(name, 2)
        if not 0 < size <= 65536:
            raise ReadEpochError("runtime expansion has an unbounded function")
        return start, image.bytes(start, size, executable=True)

    expansion, code = function("variable_expand_for_file")
    entry = _unique(
        rb"\xf3\x0f\x1e\xfa\x48\x85\xf6\x74.\x55\x48\x89\xe5"
        rb"\x41\x56\x41\x55\x41\x54\x4c\x8d\x25(?P<variables_anchor>....)\x53"
        rb"\x48\x8b\x46(?P<variables>.)\x4d\x8b\x34\x24"
        rb"\x48\x8d\x1d(?P<reading_anchor>....)\x49\x89\x04\x24"
        rb"\x48\x8b\x46(?P<commands>.)\x4c\x8b\x2b\x48\x85\xc0"
        rb"\x74.\x31\xd2\x48\x83\x38\x00\x48\x0f\x44\xc2"
        rb"\x48\x89\xfe\x48\xc7\xc2\xff\xff\xff\xff\x31\xff"
        rb"\x48\x89\x03(?P<call>\xe8....)"
        rb"\x4d\x89\x34\x24\x4c\x89\x2b"
        rb"\x5b\x41\x5c\x41\x5d\x41\x5e\x5d\xc3",
        code, "target-aware expansion frame and restored anchors",
    )
    if entry.start() != 0:
        raise ReadEpochError("runtime expansion has a foreign actual entry")
    for group, offset, symbol in (
        ("variables_anchor", 19, "current_variable_set_list"), ("reading_anchor", 35, "reading_file"),
    ):
        if expansion + offset + 7 + int.from_bytes(entry[group], "little", signed=True) != image.symbol(symbol, 1)[0]:
            raise ReadEpochError("runtime expansion substituted an actual anchor")
    call = expansion + entry.start("call")
    if direct_call(call, entry["call"]) != image.symbol("variable_expand_string", 2)[0]:
        raise ReadEpochError("runtime expansion substituted its original callee")
    if entry["variables"] != b"\x50" or entry["commands"] != b"\x20":
        raise ReadEpochError("runtime expansion has a foreign file layout")

    job, job_code = function("new_job")
    if not job_code.startswith(b"\xf3\x0f\x1e\xfa\x55\x48\x89\xe5"):
        raise ReadEpochError("runtime recipe has a foreign actual frame entry")
    recipe = _unique(
        rb"\x48\x8b\x43\x20\x48\x8b\x75(?P<file>.)\x4c\x89\x63\x10"
        rb"\x4a\x8b\x3c\x30\x48\x8d\x05....\x4c\x8b\x35...."
        rb"\x4c\x8b\x38\x48\xc7\x00\x00\x00\x00\x00(?P<call>\xe8....)"
        rb"\x48\x8d\x0d....\x4c\x89\x35....\x4c\x89\x39",
        job_code, "original recipe command-line expansion",
    )
    recipe_call = job + recipe.start("call")
    if direct_call(recipe_call, recipe["call"]) != expansion:
        raise ReadEpochError("runtime recipe expansion substituted its original callee")
    saved_file = _unique(
        rb"\x48\x89\x7d(?P<file>.)\x48\x8b\x5f\x20",
        job_code[:64], "recipe entry file/commands association",
    )
    if saved_file["file"] != recipe["file"]:
        raise ReadEpochError("runtime recipe borrowed another saved file")

    snap, snap_code = function("snap_deps")
    if not snap_code.startswith(b"\xf3\x0f\x1e\xfa\x55\x48\x89\xe5"):
        raise ReadEpochError("runtime secondary has a foreign actual frame entry")
    loops = [
        _unique(pattern, snap_code, "secondary " + label + " target loop")
        for label, pattern in (
            ("suffix", rb"\x48\x8b\x18\x4c\x89\xe7(?P<call>\xe8....)\x4d\x8b\x64\x24\x38\x4d\x85\xe4\x75."),
            ("ordinary", rb"\x49\x39\x1f\x74\x08\x4c\x89\xff(?P<call>\xe8....)\x4d\x8b\x7f\x38\x4d\x85\xff\x75."),
        )
    ]
    targets = [direct_call(snap + loop.start("call"), loop["call"]) for loop in loops]
    secondary = targets[0]
    if secondary is None or targets[1] != secondary:
        raise ReadEpochError("runtime secondary loops have different actual helpers")
    helper = image.bytes(secondary, 1024, executable=True)
    initialized = _unique(
        rb"\x55\x48\x89\xe5\x41\x57\x41\x56\x41\x55\x41\x54\x53"
        rb"\x48\x83\xec\x18\x48\x8b\x5f\x18"
        rb"\x80\xa7\x89\x00\x00\x00\xfd\x48\x8b\x4f\x28\x48\x85\xdb"
        rb"\x0f\x84....\x48\x89\x4d.\x49\x89\xfe\x4c\x8d\x67\x18\x45\x31\xff",
        helper, "secondary entry target and prerequisite chain",
    )
    if initialized.start() != 0:
        raise ReadEpochError("runtime secondary helper has a foreign frame entry")
    match = _unique(
        rb"\x4c\x89\xf7(?P<set>\xe8....)\x48\x8b\x7b\x08"
        rb"\x4c\x89\xf6(?P<expand>\xe8....)\x48\x83\x7b\x18\x00"
        rb"\x49\x89\xc7\x74.\x48\x8b\x45.\x49\x89\x46\x28"
        rb"\x4c\x89\xef\xe8....\x4c\x8b\x6b\x18\x4c\x89\xff"
        rb"(?P<split>\xe8....)\x48\x89\xc7\x4c\x89\xee(?P<enter>\xe8....)",
        helper, "original secondary prerequisite expansion",
    )
    if any(
        direct_call(secondary + match.start(group), match[group]) != image.symbol(name, 2)[0]
        for group, name in (
            ("set", "set_file_variables"), ("expand", "variable_expand_for_file"),
            ("split", "split_prereqs"), ("enter", "enter_prereqs"),
        )
    ):
        raise ReadEpochError("runtime secondary expansion substituted an actual callee")
    return {
        "function": [expansion, expansion + len(code)],
        "recipe": [job, job + len(job_code)],
        "secondary": [secondary, secondary + match.end()],
        "snap": [snap, snap + len(snap_code)],
        "recipe_return": recipe_call + 5,
        "recipe_file": int.from_bytes(recipe["file"], "little", signed=True),
        "secondary_return": secondary + match.end("expand"),
        "secondary_snap_returns": [snap + loop.end("call") for loop in loops],
        "file_commands": 32, "file_variables": 80,
    }


def runtime_effect_abi(image, source, evaluator, ordinary_return):
    def function(name):
        address, extent = image.symbol(name, 2)
        if not 0 < extent <= 65536:
            raise ReadEpochError("runtime effect function has an unbounded code span")
        code = image.bytes(address, extent, executable=True)
        if not code.startswith(b"\xf3\x0f\x1e\xfa\x55\x48\x89\xe5"):
            raise ReadEpochError("runtime effect function lacks its actual frame entry")
        return address, extent, code

    def returned(start, code, target, label):
        calls = [
            start + offset + 5 for offset in range(len(code) - 4)
            if direct_call(start + offset, code[offset:offset + 5]) == target
        ]
        if len(calls) != 1:
            raise ReadEpochError("runtime effect lacks one actual caller: " + label)
        return calls[0]

    definition, definition_size, _ = function("do_variable_definition")
    trial, trial_size, trial_code = function("try_variable_definition")
    ordinary_definition = returned(trial, trial_code, definition, "ordinary definition")
    _unique(
        rb"\x4c\x89\xfe\x41\x83\xe0\x07\xe8....\x4c\x89\xff\x48\x89\xc3",
        trial_code, "ordinary parsed name/flavor and effective result",
    )
    evaluator_code = image.bytes(evaluator, source[0] - evaluator, executable=True)
    define_definition = returned(evaluator, evaluator_code, definition, "multiline define")
    define_arguments = _unique(
        rb"\x44\x0f\xb7\x45.\x45\x31\xc9\x44\x89\xf9\x4c\x89\xea"
        rb"\x48\x8d\xbd(....)\x44\x88\x95....\x66\x41\xc1\xe8\x07"
        rb"\x41\x83\xe0\x07\xe8....\x4c\x89\xef\x48\x89\xc3",
        evaluator_code, "direct define name/flavor and effective result",
    )
    target_returns = [
        evaluator + offset + 5 for offset in range(len(evaluator_code) - 4)
        if direct_call(evaluator + offset, evaluator_code[offset:offset + 5]) == trial
        and evaluator + offset + 5 != ordinary_return
    ]
    if len(target_returns) != 1:
        raise ReadEpochError("runtime effect lacks one target-specific definition caller")
    target_modifiers = _unique(
        rb"\x8b\x85....\x83\xe0\x03\xc1\xe0\x1d\x89\xc2"
        rb"\x0f\xb6\x85....\xc0\xe8\x05\x83\xe0\x01\xc1\xe0\x07"
        rb"\x83\xc8\x08\x09\xd0\x41\x8b\x55\x2c\x81\xe2\x77\xff\xff\x9f"
        rb"\x09\xd0\x89\xc2\x41\x89\x45\x2c\xc1\xea\x18\x89\xd0"
        rb"\x83\xe0\x1c\x3c\x14\x74.\x4d\x8b\x75\x00\x4c\x89\xf7"
        rb"\xe8....\x4c\x89\xf7\x48\x89\xc6(?P<lookup>\xe8....)"
        rb"\x49\x89\xc6\x48\x85\xc0\x74.\x49\x39\xc5\x74."
        rb"\x0f\xb6\x40\x2f\x83\xe0\x1c\x83\xe8\x0c\xa8\xf8\x0f\x84...."
        rb"(?=\x4d\x85\xe4)",
        evaluator_code, "target private/export flags and effective-origin merge",
    )
    if direct_call(
        evaluator + target_modifiers.start("lookup"), target_modifiers["lookup"],
    ) != image.symbol("lookup_variable", 2)[0]:
        raise ReadEpochError("target origin merge has a foreign actual lookup")
    target_return_setup = _unique(
        rb"\x49\x89\xc5\x48\x85\xc0\x0f\x84....\x48\x8d\x05...."
        rb"\x48\x8b\x95....\x48\x89\x10\xe9(....)",
        evaluator_code[target_returns[0] - evaluator:target_returns[0] - evaluator + 64],
        "target effective result and restored variable-set continuation",
    )
    if (
        target_return_setup.start() != 0
        or target_returns[0] + target_return_setup.end()
        + int.from_bytes(target_return_setup[1], "little", signed=True)
        != evaluator + target_modifiers.start()
    ):
        raise ReadEpochError("target definition bypasses its actual post-modifier continuation")
    reader_code = image.bytes(source[0], source[1] - source[0], executable=True)
    reader_definition = returned(source[0], reader_code, definition, "reader internal definition")
    buffer, buffer_size, buffer_code = function("eval_buffer")
    size_store = _unique(
        rb"\x48\x89\x45(.)\x48\xc7\x45(.)\x00\x00\x00\x00",
        buffer_code, "pristine eval size and null file",
    )
    buffer_setup = _unique(
        rb"\x48\x89\x03\x48\x8d\x7d(.)", buffer_code, "eval ebuffer argument",
    )
    floc_setup = _unique(
        rb"\x48\x8d\x45(.)\x66\x0f\xef\xc0\xbe\x01\x00\x00\x00",
        buffer_code, "eval copied floc argument",
    )
    ebuffer = int.from_bytes(buffer_setup[1], "little", signed=True)
    floc = int.from_bytes(floc_setup[1], "little", signed=True)
    if (
        floc - ebuffer != 40
        or int.from_bytes(size_store[1], "little", signed=True) - ebuffer != 24
        or int.from_bytes(size_store[2], "little", signed=True) - ebuffer != 32
    ):
        raise ReadEpochError("runtime eval buffer fields have a foreign LP64 layout")
    anchor = _unique(
        rb"\x48\x8d\x1d(....)\x66\x0f\x6c\xc0", buffer_code, "eval reading_file anchor",
    )
    if buffer + anchor.start() + 7 + int.from_bytes(anchor[1], "little", signed=True) != image.symbol("reading_file", 1)[0]:
        raise ReadEpochError("runtime eval floc lacks its original reading_file anchor")
    return {
        "definition": [definition, definition + definition_size],
        "try_definition": [trial, trial + trial_size],
        "eval_buffer": [buffer, buffer + buffer_size],
        "ordinary_definition_return": ordinary_definition,
        "ordinary_assignment_return": ordinary_return,
        "define_definition_return": define_definition,
        "define_floc": int.from_bytes(define_arguments[1], "little", signed=True),
        "reader_definition_return": reader_definition,
        "target_assignment_return": target_returns[0],
        "target_completion": evaluator + target_modifiers.end(),
        "eval_ebuffer": ebuffer, "eval_floc": floc,
    }


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
        "runtime": runtime_effect_abi(image, source, evaluator, evaluator + calls[0] + 5),
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
    effects: tuple = ()
    evaluations: tuple = ()
    expansions: tuple = ()
    pattern_templates: tuple = ()
    patterns: tuple = ()


class OriginalArchive(NamedTuple):
    scope: str
    passes: tuple[OriginalPass, ...]
    sources: tuple[OriginalSource, ...]
    version: int = 1
    selection: tuple = ()
    selection_inventory: tuple = ()
    compiler_profile: OriginalCompilerProfile | None = None
    compiler_executions: tuple[OriginalCompilerExecution, ...] = ()


class OriginalCompletion(NamedTuple):
    seq: int
    visit: int
    source: int
    site: tuple
    name: str
    operator: str
    cwd: str
    variable: OriginalVariable


class RuntimeLocation(NamedTuple):
    visit: int | None
    source: int
    evaluation: int | None
    span: tuple[int, int, int]
    offsets: tuple[int, int] | None


class ExpansionLocation(NamedTuple):
    expansion: int


class PatternLocation(NamedTuple):
    pattern: int


class PatternDefinition(NamedTuple):
    pattern: str
    length: int
    percent: int
    name: str
    value: str
    file: str
    line: int
    offset: int
    flags: int
    name_length: int


class OriginalPatternTemplate(NamedTuple):
    number: int
    entry_seq: int
    completion_seq: int
    owner: tuple[str, int]
    source: OriginalSource
    definition: PatternDefinition


class OriginalPattern(NamedTuple):
    number: int
    entry_seq: int
    completion_seq: int
    template: OriginalPatternTemplate
    target: str
    cwd: str
    variable: OriginalVariable
    definition_seq: int | None
    return_seq: int | None


class OriginalExpansion(NamedTuple):
    number: int
    entry_seq: int
    exit_seq: int
    family: str
    target: str
    text: str
    cwd: str


class OriginalEffect(NamedTuple):
    number: int
    entry_seq: int
    return_seq: int
    completion_seq: int
    parent: tuple[str, int]
    caller: str
    name: str
    value: str
    flavor: int
    origin: int
    target: bool
    declaration: tuple[str, int, int]
    location: RuntimeLocation | ExpansionLocation | None
    cwd: str
    variable: OriginalVariable


class OriginalEvaluation(NamedTuple):
    number: int
    entry_seq: int
    exit_seq: int
    parent: tuple[str, int]
    location: RuntimeLocation | ExpansionLocation | PatternLocation
    source: OriginalSource


def runtime_location_value(value):
    if value is None:
        return None
    if set(value) == {"expansion"}:
        return ExpansionLocation(value["expansion"])
    if set(value) == {"pattern"}:
        return PatternLocation(value["pattern"])
    return RuntimeLocation(
        value["visit"], value["source"], value["evaluation"], tuple(value["span"]),
        None if value["offsets"] is None else tuple(value["offsets"]),
    )


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
        if len(name) <= 128 and re.fullmatch(make_lexical.LITERAL_NAME, name) and name not in names:
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
            if character and character in make_lexical.LITERAL_NAME_CHARACTERS:
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
        if text[index] == "$" and len(pair) == 2 and pair[1] in make_lexical.SHORT_REFERENCE_CHARACTERS:
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
        rf"(?<![{make_lexical.LITERAL_NAME_CHARACTERS}])(?:ifdef|ifndef)\s+({make_lexical.LITERAL_NAME})",
        collapsed,
    ):
        checkpoint()
        retain(match[1])
    for match in re.finditer(r"(?m)^[ \t]*(?:(?:override|private)[ \t]+)*export[ \t]+([^\r\n]*)", collapsed):
        checkpoint()
        for name in match[1].split():
            retain(name)
    checkpoint()
    return names


def completion_source_facts(path, data, *, checkpoint=lambda: None, count_limit=None, charge=lambda size: None):
    if not isinstance(path, str) or not path or not isinstance(data, bytes):
        raise ReadEpochError("completion source facts require an exact byte source")
    if b"\0" in data:
        raise ReadEpochError("completion source has unsupported bytes")
    rows, dependencies, roots = [], {}, set()
    limits = {"checkpoint": checkpoint, "charge": charge}
    definition, depth, recipe_allowed = None, 0, False
    conditional_depth = 0
    digest = hashlib.sha256(data).hexdigest()
    for logical, first, last, raw in physical_statements(
        data, checkpoint=checkpoint, count_limit=count_limit,
    ):
        statement = make_lexical._collapse_make_continuations(raw)
        if not depth:
            statement = make_lexical.strip_comment(statement, recipe_context=True, **limits)
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
            names = make_lexical.references(statement, directives=False, **limits)
            charge(len(encoded(sorted(names))))
            dependencies[definition].update(names)
            continue
        conditional = (
            None if raw.startswith("\t") else
            re.match(r"^(ifeq|ifneq|ifdef|ifndef|else|endif)(?:[ \t]|$)", header)
        )
        if conditional:
            recipe_allowed = False
            if conditional[1] in {"ifeq", "ifneq", "ifdef", "ifndef"}:
                conditional_depth += 1
            elif not conditional_depth:
                raise ReadEpochError("unsupported completion unmatched conditional")
            elif conditional[1] == "endif":
                conditional_depth -= 1
        if raw.startswith("\t"):
            if not recipe_allowed:
                raise ReadEpochError("unsupported completion tab statement before admitted rule")
            assignment, macro, rule = None, None, False
        else:
            assignment, macro, rule = make_lexical.completion_declaration(statement, **limits)
        if assignment is not None or macro is not None or make_lexical.MODE_TARGET_ASSIGNMENT.fullmatch(statement):
            recipe_allowed = False
        else:
            recipe_allowed |= rule and not conditional_depth
        if assignment is None:
            names = make_lexical.references(statement, directives=not raw.startswith("\t"), **limits)
            charge(len(encoded(sorted(names))))
            if macro is not None:
                definition, depth = macro[1], 1
                dependencies.setdefault(definition, set()).update(names)
            else:
                roots.update(names)
            continue
        name = assignment["name"]
        names = make_lexical.references(assignment["value"], directives=False, **limits)
        charge(len(encoded((name, sorted(names)))))
        dependencies.setdefault(name, set()).update(names)
        prefix = statement[:assignment.start("name")].split()
        if "export" in prefix:
            roots.add(name)
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
    if conditional_depth:
        raise ReadEpochError("unsupported completion unterminated conditional")
    return rows, roots, dependencies


def completion_sites(path, data, names, *, checkpoint=lambda: None, count_limit=None, charge=lambda size: None):
    rows, _, _ = completion_source_facts(
        path, data, checkpoint=checkpoint, count_limit=count_limit, charge=charge,
    )
    selected = set(names)
    return [row for row in rows if row[5] in selected]


def require_completion_reference_closure(references, selected_names, *, dependencies=None):
    if any(
        name not in selected_names and make_lexical.SCOPED.fullmatch("$(" + name + ")") is None
        for name in references
    ):
        raise ReadEpochError("opened source adds a consumer outside the frozen name closure")
    if dependencies is not None:
        for names in dependencies.values():
            require_completion_reference_closure(names, selected_names)


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


class _CompactStatementIndex(Mapping):
    __slots__ = ("_data", "_spans", "_count", "_reserve", "_values")

    def __init__(self, data, *, checkpoint, count_limit, reserve):
        if not isinstance(data, bytes) or b"\0" in data:
            raise ReadEpochError("completion source has unsupported bytes")
        validated = data.decode("utf-8", "strict")
        reserve(sys.getsizeof(validated))
        del validated
        physical_count = data.count(b"\n") + 1
        if count_limit is not None and physical_count > count_limit:
            raise ReadEpochError("completion physical source scan exceeds observation bound")
        self._data, self._reserve, self._count = data, reserve, 0
        self._values = {}
        reserve(sys.getsizeof(self._values))
        seed = array("I", [0])
        if seed.itemsize != 4 or len(data) >= 1 << 32:
            raise ReadEpochError("physical source offsets lack bounded 32-bit storage")
        reserve(sys.getsizeof(seed))
        self._spans = seed * (physical_count * 4)
        reserve(sys.getsizeof(self._spans) + sys.getsizeof(self) + sys.getsizeof(reserve))
        cursor = begin = 0
        first = 1
        for line in range(1, physical_count + 1):
            checkpoint()
            end = data.find(b"\n", cursor)
            has_lf = end >= 0
            if not has_lf:
                end = len(data)
            physical_end = end - 1 if has_lf and end > cursor and data[end - 1] == 13 else end
            slash = physical_end
            while slash > cursor and data[slash - 1] == 92:
                slash -= 1
            cursor = end + 1 if has_lf else end
            if has_lf and (physical_end - slash) % 2:
                continue
            base = self._count * 4
            self._spans[base] = first
            self._spans[base + 1] = line
            self._spans[base + 2] = begin
            self._spans[base + 3] = end
            self._count += 1
            first, begin = line + 1, cursor

    def __len__(self):
        return self._count

    def __iter__(self):
        for number in range(self._count):
            yield self._spans[number * 4]

    def __getitem__(self, key):
        if key in self._values:
            return self._values[key]
        if not isinstance(key, (int, float)):
            raise KeyError(key)
        lower, upper = 0, self._count
        while lower < upper:
            middle = (lower + upper) // 2
            if self._spans[middle * 4] < key:
                lower = middle + 1
            else:
                upper = middle
        if lower == self._count or self._spans[lower * 4] != key:
            raise KeyError(key)
        base = lower * 4
        first, last, begin, end = (self._spans[base + offset] for offset in range(4))
        chunk = self._data[begin:end]
        decoded = chunk.decode("utf-8", "strict")
        parts = decoded.split("\n")
        self._reserve(
            sys.getsizeof(chunk) + sys.getsizeof(decoded) + sys.getsizeof(parts)
            + sum(sys.getsizeof(part) for part in parts),
        )
        for number, part in enumerate(parts):
            if (number < len(parts) - 1 or end < len(self._data)) and part.endswith("\r"):
                parts[number] = part[:-1]
                self._reserve(sys.getsizeof(parts[number]))
        raw = "\n".join(parts)
        encoded_raw = raw.encode()
        self._reserve(sys.getsizeof(raw) + sys.getsizeof(encoded_raw))
        value = (lower + 1, first, last, hashlib.sha256(encoded_raw).hexdigest())
        self._reserve(
            sys.getsizeof(begin) + sys.getsizeof(end) + sys.getsizeof(value)
            + sum(sys.getsizeof(item) for item in value),
        )
        table_before = sys.getsizeof(self._values)
        self._values[first] = value
        table_after = sys.getsizeof(self._values)
        if table_after > table_before:
            self._reserve(table_after)
        return value


def _statement_index(
    data, *, checkpoint=lambda: None, count_limit=None, reserve=lambda size: None, compact=False,
):
    if compact:
        return _CompactStatementIndex(
            data, checkpoint=checkpoint, count_limit=count_limit, reserve=reserve,
        )
    rows = {}
    table_bytes = sys.getsizeof(rows)
    reserve(table_bytes)
    for logical, first, last, raw in physical_statements(
        data, checkpoint=checkpoint, count_limit=count_limit,
    ):
        value = (logical, first, last, hashlib.sha256(raw.encode()).hexdigest())
        reserve(
            sys.getsizeof(first) + sys.getsizeof(value)
            + sum(sys.getsizeof(item) for item in value),
        )
        rows[first] = value
        current_bytes = sys.getsizeof(rows)
        if current_bytes > table_bytes:
            reserve(current_bytes)
            table_bytes = current_bytes
    result = MappingProxyType(rows)
    reserve(sys.getsizeof(result))
    return result


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
    name, value = string(name_ptr, 129), string(value_ptr, 65537)
    if name is None or value is None or len(name.encode()) != length:
        raise ReadEpochError("completed original variable lacks its bounded raw name/value")
    return list(variable_row([name, value, flags, string(filename, 4096), line, offset]))


def pattern_source_coordinates(definition, owner, source, events, data, *, checkpoint=lambda: None,
                               reserve=lambda size: None, event_limit=None):
    """Bind coordinates to the actual reader or evaluated-buffer occurrence."""
    sources, evaluations, opened = {}, {}, {}
    reserve(sys.getsizeof(sources) + sys.getsizeof(evaluations) + sys.getsizeof(opened))
    for index, row in enumerate(events):
        if event_limit is not None and index >= event_limit:
            break
        checkpoint()
        if row["kind"] == "source-entry":
            table, key, item = sources, row["visit"], row
        elif row["kind"] == "eval-entry":
            table, key, item = evaluations, row["evaluation"], row
        elif row["kind"] == "source-open":
            table, key, item = opened, row["visit"], row["source"]
        else:
            continue
        size = sys.getsizeof(table)
        table[key] = item
        if sys.getsizeof(table) > size:
            reserve(sys.getsizeof(table))
    kind, number = owner
    if kind == "source":
        if opened.get(number) != source:
            raise ReadEpochError("pattern declaration borrowed another original source")
        expected_file = sources[number]["name"]
        expected_line = definition["line"]
    else:
        occurrence = evaluations[number]
        location = occurrence["location"]
        visited = set()
        reserve(sys.getsizeof(visited))
        while isinstance(location, dict) and location.get("evaluation") is not None:
            checkpoint()
            parent = location["evaluation"]
            if parent in visited or parent not in evaluations:
                raise ReadEpochError("pattern declaration has cyclic or foreign eval ancestry")
            visited.add(parent)
            reserve(sys.getsizeof(visited))
            location = evaluations[parent]["location"]
        if not isinstance(location, dict) or location.get("visit") not in sources:
            raise ReadEpochError("pattern declaration lacks its source-backed eval location")
        expected_file = sources[location["visit"]]["name"]
        expected_line = location["span"][1]
    if (
        definition["file"] != expected_file or definition["line"] != expected_line
        or definition["offset"] != 0
    ):
        raise ReadEpochError("pattern declaration coordinates differ from its owning source occurrence")
    begin = cursor = 0
    first = line = 1
    while cursor <= len(data):
        checkpoint()
        end = data.find(b"\n", cursor)
        has_lf = end >= 0
        if not has_lf:
            end = len(data)
        physical_end = end - 1 if has_lf and end > cursor and data[end - 1] == 13 else end
        slash = physical_end
        while slash > cursor and data[slash - 1] == 92:
            slash -= 1
        cursor = end + 1
        if has_lf and (physical_end - slash) % 2:
            line += 1
            continue
        start = first
        first, line = line + 1, line + 1
        span_begin = begin
        begin = cursor
        if kind == "source" and start != definition["line"]:
            continue
        chunk = data[span_begin:end]
        decoded = chunk.decode("utf-8", "strict")
        reserve(sys.getsizeof(chunk) + sys.getsizeof(decoded))
        normalized = decoded.replace("\r\n", "\n")
        if normalized is not decoded:
            reserve(sys.getsizeof(normalized))
        raw = normalized.removesuffix("\r")
        if raw is not normalized:
            reserve(sys.getsizeof(raw))
        statement = make_lexical._collapse_make_continuations(raw)
        reserve(sys.getsizeof(statement))
        statement = make_lexical.strip_comment(statement, checkpoint=checkpoint, charge=reserve)
        reserve(sys.getsizeof(statement))
        boundary, colon = make_lexical._statement_boundary(statement, checkpoint=checkpoint, charge=reserve)
        if boundary != "rule":
            continue
        target, assignment = statement[:colon], statement[colon + 1:]
        reserve(sys.getsizeof(target) + sys.getsizeof(assignment))
        boundary, operator = make_lexical._statement_boundary(assignment, checkpoint=checkpoint, charge=reserve)
        if boundary != "assignment":
            continue
        name = re.sub(r"^\s*(?:(?:export|private|override)\s+)*", "", assignment[:operator]).strip()
        reserve(sys.getsizeof(name))
        if "$" not in name and name != definition["name"]:
            continue
        targets = target.replace("\\%", "%").split()
        reserve(sys.getsizeof(targets) + sum(sys.getsizeof(item) for item in targets))
        if "$" not in target and definition["pattern"] not in targets:
            continue
        value = re.sub(r"^(?:::=|::=|:=|\?=|\+=|!=|=)[ \t]*", "", assignment[operator:])
        reserve(sys.getsizeof(value))
        if not assignment[operator:].startswith("!=") and (
                (definition["flags"] >> 23) & 7 != 1 or "$" not in value
        ) and definition["value"] != value:
            continue
        return
    raise ReadEpochError("pattern declaration lacks its pristine source statement")


def simple_pattern_binding(definition, variable):
    if (definition["flags"] >> 23) & 7 == 1 and (
        variable[1] != definition["value"] or tuple(variable[3:]) != (
            definition["file"], definition["line"], definition["offset"],
        )
    ):
        raise ReadEpochError("simple pattern lost its effective value/source fields")


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
            or re.fullmatch(make_lexical.LITERAL_NAME, row[5]) is None
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
            and re.fullmatch(make_lexical.LITERAL_NAME, name) is not None
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
    executions, visits, effects, evaluations, expansions = {}, {}, {}, {}, {}
    templates, patterns = {}, {}
    for event in trace["events"]:
        budget.remaining()
        kind = event["kind"]
        if kind == "exec":
            executions[event["exec"]] = {
                "visits": [], "other": [], "image": None, "completions": [],
                "effects": [], "evaluations": [], "expansions": [],
                "pattern_templates": [], "patterns": [],
            }
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
        elif kind == "effect-entry":
            effects[event["effect"]] = {"entry": event}
        elif kind == "effect-return":
            effects[event["effect"]]["return"] = event["seq"]
        elif kind == "effect-completion":
            effect = effects[event["effect"]]
            entry = effect["entry"]
            executions[event["exec"]]["effects"].append(OriginalEffect(
                event["effect"], entry["seq"], effect["return"], event["seq"],
                tuple(entry["parent"]), entry["caller"], entry["name"], entry["value"],
                entry["flavor"], entry["origin"], entry["target"], tuple(entry["declaration"]),
                runtime_location_value(entry["location"]), event["cwd"],
                OriginalVariable(*event["variable"]),
            ))
        elif kind == "eval-entry":
            evaluations[event["evaluation"]] = event
        elif kind == "eval-exit":
            entry = evaluations[event["evaluation"]]
            executions[event["exec"]]["evaluations"].append(OriginalEvaluation(
                event["evaluation"], entry["seq"], event["seq"], tuple(entry["parent"]),
                runtime_location_value(entry["location"]), sources[event["source"]],
            ))
        elif kind == "expansion-entry":
            expansions[event["expansion"]] = event
        elif kind == "expansion-exit":
            entry = expansions[event["expansion"]]
            executions[event["exec"]]["expansions"].append(OriginalExpansion(
                event["expansion"], entry["seq"], event["seq"], entry["family"],
                entry["target"], entry["text"], entry["cwd"],
            ))
        elif kind == "pattern-template-entry":
            templates[event["template"]] = {"entry": event}
        elif kind == "pattern-template-completion":
            retained = templates[event["template"]]
            entry = retained["entry"]
            retained["value"] = OriginalPatternTemplate(
                event["template"], entry["seq"], event["seq"], tuple(event["owner"]),
                sources[event["source"]], PatternDefinition(**event["definition"]),
            )
            executions[event["exec"]]["pattern_templates"].append(retained["value"])
        elif kind == "pattern-entry":
            patterns[event["pattern"]] = {"entry": event, "definition": None, "return": None}
        elif kind == "pattern-definition":
            patterns[event["pattern"]]["definition"] = event["seq"]
        elif kind == "pattern-definition-return":
            patterns[event["pattern"]]["return"] = event["seq"]
        elif kind == "pattern-completion":
            retained = patterns[event["pattern"]]
            entry = retained["entry"]
            executions[event["exec"]]["patterns"].append(OriginalPattern(
                event["pattern"], entry["seq"], event["seq"], templates[entry["template"]]["value"],
                entry["target"], event["cwd"], OriginalVariable(*event["variable"]),
                retained["definition"], retained["return"],
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
                runtime_location_value(started.get("location")) if trace["version"] in RUNTIME_VERSIONS
                else None if started.get("location") is None else tuple(started["location"]),
            ))
        passes.append(OriginalPass(
            execution, entry["pass"], entry["seq"], returned["seq"],
            tuple(OriginalScope(scope["parent"], tuple(OriginalVariable(*row) for row in scope["variables"]))
                  for scope in entry["inputs"]),
            tuple(ordered), tuple(value["other"]), tuple(returned["goals"]), value["image"],
            tuple(value["completions"]),
            tuple(sorted(value["effects"], key=lambda effect: effect.number)),
            tuple(sorted(value["evaluations"], key=lambda evaluation: evaluation.number)),
            tuple(value["expansions"]),
            tuple(value["pattern_templates"]), tuple(value["patterns"]),
        ))
    compiler_profile = None
    compiler_executions = []
    if "compiler" in trace.get("output_authority", {}):
        reserve = lambda size: budget.charge("cache", size)
        compiler_profile = native_compiler_profile(
            trace["output_authority"]["compiler"],
            count_limit=budget.limits.observation_count, reserve=reserve,
        )
        inventory = tuple(row["path"] for row in trace["selection"]["inventory"])
        reserve(sys.getsizeof(inventory) + sys.getsizeof(compiler_executions))
        for job in trace["output_authority"]["jobs"]:
            tree = [row["event"] for row in trace["machine"]["events"]
                    if row["kind"] == "native-tree" and row["dispatch"] == job["sequence"]]
            reserve(sys.getsizeof(tree))
            if any(row["kind"] == "exec" and (
                row["path"] in {compiler_profile.driver, compiler_profile.frontend}
                or "compiler" in row["admission"]
            ) for row in tree):
                compiler_executions.extend(native_compiler_lineage(
                    tree, job, compiler_profile, sources=inventory,
                    count_limit=budget.limits.observation_count, reserve=reserve,
                ))
                reserve(sys.getsizeof(compiler_executions))
        reserve(sys.getsizeof(tuple(compiler_executions)))
    return OriginalArchive(
        trace["scope"], tuple(passes), tuple(sources.values()), trace["version"],
        tuple(trace["selection"]["names"]) if trace["version"] in MACHINE_VERSIONS
        else tuple(tuple(row) for row in trace.get("selection", ())),
        tuple(trace["selection"]["inventory"]) if trace["version"] in MACHINE_VERSIONS else (),
        compiler_profile, tuple(compiler_executions),
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


def native_execution_input(argv, cwd):
    if (
        not isinstance(argv, list) or not 1 <= len(argv) <= 1024
        or any(
            not isinstance(item, str) or "\0" in item
            or any(0xD800 <= ord(char) <= 0xDFFF for char in item)
            for item in argv
        )
        or not argv[0] or sum(len(item.encode("utf-8")) + 1 for item in argv) > 65536
        or not isinstance(cwd, str) or not cwd.startswith("/") or "\0" in cwd
        or any(0xD800 <= ord(char) <= 0xDFFF for char in cwd)
        or len(cwd.encode("utf-8")) > 4096
    ):
        raise ReadEpochError("native job has invalid actual execution inputs")
    return {"argv": argv, "cwd": cwd}


class OriginalCompilerProfile(NamedTuple):
    identity: str
    driver: str
    frontend: str
    files: tuple
    directories: tuple
    probes: tuple
    interpreter: str
    libc: str
    environment: tuple


class OriginalCompilerExecution(NamedTuple):
    dispatch: int
    pid: int
    generation: int
    admission: int
    role: str
    profile: OriginalCompilerProfile
    driver: tuple | None
    argv: tuple
    cwd: str
    sources: tuple
    code: tuple
    includes: tuple
    outputs: tuple
    resources: tuple
    environment: tuple


def dependency_arguments(argv, sources, outputs, *, driver_spellings=("/usr/bin/cc",)):
    if not argv or argv[0] not in driver_spellings or len(outputs) != 1 or not outputs[0].endswith(".d"):
        raise MakeProbeError("dependency profile requires host cc and one declared .d output")
    modes, includes = set(), []
    translation_unit = target = None
    arguments = iter(argv[1:])
    for argument in arguments:
        if argument in {"-E", "-MM", "-MG", "-nostdinc", "-undef"}:
            if argument in modes:
                raise MakeProbeError("duplicate dependency mode")
            modes.add(argument)
            continue
        option = next((name for name in ("-iquote", "-MT", "-I", "-D", "-U") if argument.startswith(name)), None)
        if option is not None:
            value = argument[len(option):] if argument != option else next(arguments, "")
            if not value or value.startswith("@") or "\n" in value or "\r" in value:
                raise MakeProbeError("invalid or missing dependency option value")
            if option in {"-I", "-iquote"}:
                if value.startswith(("=", "$SYSROOT")):
                    raise MakeProbeError("sysroot-special dependency include operand is unsupported")
                if value.startswith("-"):
                    raise MakeProbeError("dependency include path is not repository-relative")
                includes.append(value if value == "." else relative_path(value))
            elif option in {"-D", "-U"}:
                name, separator, _ = value.partition("=")
                if not re.fullmatch("[A-Za-z_][A-Za-z0-9_]*", name) or option == "-U" and separator:
                    raise MakeProbeError("dependency macro requires a symbolic name")
            else:
                if target is not None or not re.fullmatch("[A-Za-z0-9_./+%-]+", value) or value.startswith("-"):
                    raise MakeProbeError("invalid or duplicate dependency target")
                target = relative_path(value)
            continue
        if argument.startswith(("-", "@")) or translation_unit is not None or not argument.endswith(".c"):
            raise MakeProbeError("unsupported dependency compiler option or source")
        translation_unit = relative_path(argument)
    if not {"-E", "-MM", "-nostdinc", "-undef"} <= modes or target is None or translation_unit not in sources:
        raise MakeProbeError("dependency profile requires exact modes, target and declared C source")
    return tuple(dict.fromkeys(includes))


def compiler_environment(environment, expected, *, frontend, driver):
    """Validate the closed baseline and the immutable driver's finite additions."""
    baseline = {
        "HOME", "LANG", "LC_ALL", "PATH", "TZ", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL",
        "GIT_NO_REPLACE_OBJECTS", "GIT_OPTIONAL_LOCKS", "PYTHONDONTWRITEBYTECODE",
        "SOURCE_DATE_EPOCH", "TMPDIR", "PWD", "MAKELEVEL", "MAKEFLAGS", "MFLAGS",
    }
    additions = {"COLLECT_GCC", "COLLECT_GCC_OPTIONS", "OFFLOAD_TARGET_NAMES", "OFFLOAD_TARGET_DEFAULT"}
    for values in (environment, expected):
        if (
            not isinstance(values, dict) or len(values) > len(baseline) + len(additions)
            or any(
                not isinstance(key, str) or not isinstance(value, str)
                or "\0" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
                for key, value in values.items()
            )
            or sum(len(key.encode()) + len(value.encode()) + 2 for key, value in values.items()) > 65536
        ):
            raise ReadEpochError("compiler environment exceeds its closed actual input extent")
    if (
        type(frontend) is not bool or not isinstance(driver, str) or not driver
        or not set(expected) <= baseline
        or any(environment.get(key) != value for key, value in expected.items())
        or not set(environment) - set(expected) <= (additions if frontend else set())
        or frontend and environment.get("COLLECT_GCC") != driver
        or any(not environment[key] for key in set(environment) - set(expected))
    ):
        raise ReadEpochError("compiler environment differs from its issued baseline/driver transformation")


def native_compiler_profile(value, *, count_limit, reserve=lambda size: None):
    fields = {
        "identity", "driver", "frontend", "files", "directories", "probes",
        "interpreter", "libc", "environment",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ReadEpochError("compiler profile has an open or incomplete shape")
    reserve(len(encoded(value)))

    def path(name):
        return (
            isinstance(name, str) and name.startswith("/")
            and posixpath.normpath(name) == name and not name.startswith("//")
            and not any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in name)
            and len(name.encode("utf-8")) <= 4096
        )

    def ordered(rows):
        return (
            isinstance(rows, list) and len(rows) <= count_limit
            and all(path(name) for name in rows) and rows == sorted(set(rows))
        )

    files = value["files"]
    if (
        not isinstance(files, list) or not 2 <= len(files) <= min(count_limit, 64)
        or any(
            not isinstance(row, list) or len(row) != 3 or not path(row[0])
            or type(row[1]) is not int or not 0 < row[1] < 1 << 63
            or not isinstance(row[2], str) or re.fullmatch("[0-9a-f]{64}", row[2]) is None
            for row in files
        )
        or [row[0] for row in files] != sorted({row[0] for row in files})
        or not ordered(value["directories"]) or not ordered(value["probes"])
        or any(not path(value[key]) for key in ("driver", "frontend", "interpreter", "libc"))
        or value["driver"] == value["frontend"]
        or not {value[key] for key in ("driver", "frontend", "interpreter", "libc")}
        <= {row[0] for row in files}
    ):
        raise ReadEpochError("compiler profile differs from its finite complete image/search closure")
    environment = value["environment"]
    if (
        not isinstance(environment, dict) or len(environment) > count_limit
        or any(
            not isinstance(key, str) or re.fullmatch("[A-Za-z_][A-Za-z0-9_]*", key) is None
            or not isinstance(item, str) or "\0" in item
            or any(0xD800 <= ord(char) <= 0xDFFF for char in item)
            or len(item.encode("utf-8")) > 65536
            for key, item in environment.items()
        )
        or not isinstance(value["identity"], str)
        or value["identity"] != hashlib.sha256(encoded({
            key: item for key, item in value.items() if key != "identity"
        })).hexdigest()
    ):
        raise ReadEpochError("compiler profile lost its closed environment or issued identity")
    compiler_environment(environment, environment, frontend=False, driver=value["driver"])
    result = OriginalCompilerProfile(
        value["identity"], value["driver"], value["frontend"], tuple(tuple(row) for row in files),
        tuple(value["directories"]), tuple(value["probes"]), value["interpreter"], value["libc"],
        tuple(sorted(environment.items())),
    )
    reserve(sys.getsizeof(result) + sum(sys.getsizeof(row) for row in result if isinstance(row, tuple)))
    reserve(sum(sys.getsizeof(row) for row in result.files) + sum(sys.getsizeof(row) for row in result.environment))
    return result


def native_compiler_lineage(events, job, profile, *, sources, count_limit, reserve=lambda size: None):
    """Compiler semantics after the ordinary closed native job tree is validated."""
    if not isinstance(profile, OriginalCompilerProfile):
        raise ReadEpochError("compiler lineage requires a validated issued profile")
    nodes, executions = {}, []
    reserve(sys.getsizeof(nodes) + sys.getsizeof(executions))
    root = job["pid"]
    if not isinstance(events, list) or not events or len(events) > count_limit:
        raise ReadEpochError("compiler lineage exceeds its finite native tree extent")
    for event in events:
        reserve(len(encoded(event)))
        kind, pid = event["kind"], event["pid"]
        if not nodes:
            if kind != "exec" or pid != root:
                raise ReadEpochError("compiler lineage lost its original native root")
            nodes[pid] = {"parent": None, "generation": 0, "actor": None, "fork": None, "retired": False}
            reserve(sys.getsizeof(nodes) + sys.getsizeof(nodes[pid]))
        node = nodes.get(pid)
        if node is None or node["retired"]:
            raise ReadEpochError("compiler lineage refers to an unknown or retired process")
        if kind == "fork":
            child = event["child"]
            if child in nodes:
                raise ReadEpochError("compiler lineage reused an actual child")
            nodes[child] = {
                "parent": pid, "generation": 0, "actor": None,
                "fork": node["actor"], "retired": False,
            }
            reserve(sys.getsizeof(nodes) + sys.getsizeof(nodes[child]))
        elif kind == "exec":
            inputs = native_execution_input(event["argv"], event["cwd"])
            reserve(sys.getsizeof(inputs))
            if event["generation"] != node["generation"] + 1:
                raise ReadEpochError("compiler lineage changed its actual exec generation")
            node["generation"] = event["generation"]
            binding = event["admission"].get("compiler")
            previous = node["actor"]
            inherited, node["fork"] = node["fork"], None
            node["actor"] = None
            if binding is None:
                if event["path"] in {profile.driver, profile.frontend}:
                    raise ReadEpochError("compiler image lacks its issued actor binding")
                continue
            if (
                not isinstance(binding, dict)
                or set(binding) != {"profile", "role", "driver", "sources", "code", "includes", "environment"}
                or binding["profile"] != profile.identity or not isinstance(binding["role"], str)
                or binding["role"] not in {"driver", "frontend"}
                or inputs["cwd"] != "/repo"
                or type(event["admission"]["sequence"]) is not int or event["admission"]["sequence"] < 1
            ):
                raise ReadEpochError("compiler actor has an open or foreign profile binding")
            compiler_environment(
                binding["environment"], dict(profile.environment),
                frontend=binding["role"] == "frontend", driver=profile.driver,
            )
            environment = tuple(sorted(binding["environment"].items()))
            reserve(sys.getsizeof(environment) + sum(sys.getsizeof(row) for row in environment))
            for key in ("sources", "code", "includes"):
                rows = binding[key]
                if (
                    not isinstance(rows, list) or len(rows) > count_limit
                    or any(not isinstance(name, str) for name in rows) or len(set(rows)) != len(rows)
                ):
                    raise ReadEpochError("compiler actor scope is not finite and unique")
                for name in rows:
                    if key == "includes" and name == ".":
                        continue
                    relative_path(name)
                if key != "includes" and any(name not in sources for name in rows):
                    raise ReadEpochError("compiler actor source scope escapes its immutable inventory")
            reference = (pid, event["generation"], event["admission"]["sequence"])
            if binding["role"] == "driver":
                if event["path"] != profile.driver or binding["driver"] is not None:
                    raise ReadEpochError("compiler driver differs from its issued image/operation")
                includes = dependency_arguments(
                    inputs["argv"], binding["sources"], event["admission"]["outputs"],
                    driver_spellings=("cc", "/usr/bin/cc", profile.driver),
                )
                if tuple(binding["includes"]) != includes:
                    raise ReadEpochError("compiler driver include scope differs from its actual safe arguments")
            else:
                parent = nodes.get(node["parent"])
                if (
                    event["path"] != profile.frontend or parent is None or parent["retired"]
                    or inherited is None or inherited.role != "driver"
                    or not isinstance(binding["driver"], list) or len(binding["driver"]) != 3
                    or any(type(number) is not int or number < 1 for number in binding["driver"])
                    or binding["driver"] != [inherited.pid, inherited.generation, inherited.admission]
                    or parent["actor"] is not inherited or event["parent"] != inherited.pid
                    or any(tuple(binding[key]) != getattr(inherited, key)
                           for key in ("sources", "code", "includes"))
                    or tuple(event["admission"]["outputs"]) != inherited.outputs
                    or tuple(tuple(row) for row in event["admission"].get("resources", ())) != inherited.resources
                    or previous is not None
                ):
                    raise ReadEpochError("compiler frontend lost its exact live driver-at-fork lineage/scope")
            actor = OriginalCompilerExecution(
                job["sequence"], pid, event["generation"], event["admission"]["sequence"],
                binding["role"], profile,
                None if binding["driver"] is None else tuple(binding["driver"]),
                tuple(inputs["argv"]), inputs["cwd"], tuple(binding["sources"]),
                tuple(binding["code"]), tuple(binding["includes"]), tuple(event["admission"]["outputs"]),
                tuple(tuple(row) for row in event["admission"].get("resources", ())),
                environment,
            )
            node["actor"] = actor
            executions.append(actor)
            reserve(sys.getsizeof(actor) + sys.getsizeof(executions))
            reserve(sum(sys.getsizeof(value) for value in actor if isinstance(value, tuple)))
        elif kind == "exit":
            node["actor"] = None
            node["retired"] = True
    if any(not node["retired"] for node in nodes.values()):
        raise ReadEpochError("compiler lineage omitted an actual terminal process")
    if not executions:
        raise ReadEpochError("compiler lineage omitted its issued actual driver/frontend")
    drivers = {(row.pid, row.generation, row.admission) for row in executions if row.role == "driver"}
    frontends = [row.driver for row in executions if row.role == "frontend"]
    reserve(sys.getsizeof(drivers) + sys.getsizeof(frontends))
    if len(frontends) != len(set(frontends)) or set(frontends) != drivers:
        raise ReadEpochError("compiler lineage omitted or reused an issued driver/frontend operation")
    result = tuple(executions)
    reserve(sys.getsizeof(result))
    return result


def native_fork_references(events, *, reserve=lambda size: None):
    """Replay parent occurrences after the ordinary writable tree is validated."""
    actors, pending, result = {}, {}, {}
    reserve(sum(sys.getsizeof(value) for value in (actors, pending, result)))
    for event in events:
        pid, kind = event["pid"], event["kind"]
        if kind == "fork":
            pending[event["child"]] = actors.get(pid)
            reserve(sys.getsizeof(pending))
        elif kind == "exec":
            reference = (pid, event["generation"], event["admission"]["sequence"])
            index = (pid, event["generation"])
            result[index] = pending.pop(pid, None)
            actors[pid] = reference
            reserve(sys.getsizeof(reference) + sys.getsizeof(index) + sys.getsizeof(actors) + sys.getsizeof(result))
        elif kind == "exit":
            actors.pop(pid, None)
            pending.pop(pid, None)
    return result


def native_image_admission(admission, inputs, outputs, resources, *, resource_field, compiler_profile=None):
    if __package__:
        from .native_resources import resource_plan
    else:
        from native_resources import resource_plan
    if (
        not isinstance(admission, dict)
        or set(admission) != {"sequence", "owner", "closure", "input_sha256", "outputs"} | (
            {"resources"} if resource_field else set()
        ) | ({"compiler"} if compiler_profile is not None and "compiler" in admission else set())
        or type(admission["sequence"]) is not int or admission["sequence"] < 1
        or any(not isinstance(admission[key], str) or re.fullmatch("[0-9a-f]{64}", admission[key]) is None
               for key in ("owner", "closure", "input_sha256"))
        or not isinstance(admission["outputs"], list)
        or any(not isinstance(path, str) or path not in outputs for path in admission["outputs"])
        or len(set(admission["outputs"])) != len(admission["outputs"])
        or inputs is not None and admission["input_sha256"] != hashlib.sha256(encoded(inputs)).hexdigest()
    ):
        raise ReadEpochError("native image lacks its closed Command owner and operands")
    try:
        selected = resource_plan(admission.get("resources", ()))
        if any(row not in resource_plan(resources) for row in selected):
            raise ReadEpochError("native image resources escape its original Command owner")
    except MakeProbeError as error:
        raise ReadEpochError(str(error)) from error
    if admission["owner"] != native_command_owner(
        admission["closure"], admission["outputs"], admission.get("resources", ()),
    ):
        raise ReadEpochError("native image operands differ from its issued Command owner")
    if "compiler" in admission:
        binding = admission["compiler"]
        if (
            not isinstance(compiler_profile, OriginalCompilerProfile) or not isinstance(binding, dict)
            or set(binding) != {"profile", "role", "driver", "sources", "code", "includes", "environment"}
            or binding["profile"] != compiler_profile.identity or not isinstance(binding["role"], str)
            or binding["role"] not in {"driver", "frontend"}
        ):
            raise ReadEpochError("native compiler admission lacks its issued profile")
        compiler_environment(
            binding["environment"], dict(compiler_profile.environment),
            frontend=binding["role"] == "frontend", driver=compiler_profile.driver,
        )


def _native_range_failure_binding(event, binding):
    if (
        event.get("owner") != binding[3] or event.get("source") != binding[2]
        or event.get("destination") is not None
    ):
        raise ReadEpochError("native failed close_range changed its affected object binding")


def _native_lock_range(event):
    if (
        set(event) != {"seq", "kind", "pid", "first", "last", "flags", "result", "bindings"}
        or type(event["first"]) is not int or type(event["last"]) is not int
        or not 0 <= event["first"] <= event["last"] < 1 << 32
        or type(event["flags"]) is not int or event["flags"] != 0
        or type(event["result"]) is not int or not -4095 <= event["result"] <= 0
        or not isinstance(event["bindings"], list) or not event["bindings"]
        or any(
            not isinstance(binding, dict) or set(binding) != {"fd", "serial", "description"}
            or any(type(binding[key]) is not int for key in binding)
            or not event["first"] <= binding["fd"] <= event["last"]
            or binding["serial"] < 1 or binding["description"] < 1
            for binding in event["bindings"]
        )
        or [binding["fd"] for binding in event["bindings"]] != sorted({
            binding["fd"] for binding in event["bindings"]
        })
    ):
        raise ReadEpochError("native job tree has an invalid inherited lock close_range")


def native_root_inputs(command_line, environment):
    def words(data):
        if not isinstance(data, bytes) or not data or not data.endswith(b"\0"):
            raise ReadEpochError("native root input lacks its complete NUL extent")
        try:
            return [word.decode("utf-8", "strict") for word in data[:-1].split(b"\0")]
        except UnicodeDecodeError as error:
            raise ReadEpochError("native root input is not strict UTF-8") from error

    argv = words(command_line)
    values = {}
    for word in words(environment):
        key, separator, value = word.partition("=")
        if not separator or not key or key in values:
            raise ReadEpochError("native root environment has an invalid or duplicate key")
        values[key] = value
    return argv, values


def validate_native_root(value, *, argv, cwd, environment, returncode, machine=None):
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "pid", "argv", "cwd", "environment", "exit_stop", "wait"}
        or type(value["version"]) is not int or value["version"] != 1
        or type(value["pid"]) is not int or not 0 < value["pid"] < 1 << 31
        or value["argv"] != argv or value["cwd"] != cwd or value["environment"] != environment
        or type(value["exit_stop"]) is not int or type(value["wait"]) is not int
        or not 0 <= value["wait"] < 1 << 32 or value["exit_stop"] != value["wait"]
        or not (os.WIFEXITED(value["wait"]) or os.WIFSIGNALED(value["wait"]))
        or type(returncode) is not int or os.waitstatus_to_exitcode(value["wait"]) != returncode
    ):
        raise ReadEpochError("native root report differs from its actual request or terminal")
    native_execution_input(value["argv"], value["cwd"])
    if machine is not None and {
        row["pid"] for row in machine["events"] if row["kind"] == "execute" and row["make"]
    } != {value["pid"]}:
        raise ReadEpochError("native root report differs from its actual machine Make PID")
    if machine is not None and machine["version"] in FINITE_MACHINE_VERSIONS and (
        len(machine["roots"]) != 1 or machine["roots"][0]["initial"] != value
    ):
        raise ReadEpochError("native root report differs from its actual machine root inputs")
    return value


def native_request_plan(value, *, count_limit, file_limit):
    if not isinstance(value, list) or not 1 <= len(value) <= count_limit:
        raise ReadEpochError("finite native plan has an invalid request extent")
    result = []
    total_size = 0
    for request in value:
        if (
            not isinstance(request, dict) or set(request) != {"argv", "cwd", "environment"}
            or not isinstance(request["environment"], dict) or not request["environment"]
            or any(
                not isinstance(key, str) or not key or "=" in key or "\0" in key
                or not isinstance(text, str) or "\0" in text
                or any(0xD800 <= ord(char) <= 0xDFFF for char in key + text)
                for key, text in request["environment"].items()
            )
        ):
            raise ReadEpochError("finite native plan has invalid initial inputs")
        native_execution_input(request["argv"], request["cwd"])
        try:
            size = len(encoded(request))
        except UnicodeError as error:
            raise ReadEpochError("finite native plan inputs are not UTF-8") from error
        total_size += size
        if total_size > file_limit:
            raise ReadEpochError("finite native plan exceeds its existing file bound")
        result.append((
            tuple(request["argv"]), request["cwd"],
            tuple(sorted(request["environment"].items())),
        ))
    return tuple(result)


def validate_native_results(value, plan, trace, *, count_limit, file_limit, output_limit, reserve):
    roots = trace["machine"].get("roots")
    if (
        trace["machine"]["version"] not in FINITE_MACHINE_VERSIONS or not isinstance(value, list)
        or len(value) != len(plan) or len(value) > count_limit
        or not isinstance(roots, list) or len(roots) != len(plan)
    ):
        raise ReadEpochError("finite native results omit their complete root plan")

    def captured(text):
        if not isinstance(text, str) or len(text) > 4 * ((file_limit + 2) // 3):
            raise ReadEpochError("finite native capture exceeds its existing file bound")
        reserve(3 * ((len(text) + 3) // 4))
        try:
            data = base64.b64decode(text, validate=True)
        except (ValueError, UnicodeError) as error:
            raise ReadEpochError("finite native capture is not bounded base64") from error
        if len(data) > file_limit or base64.b64encode(data).decode("ascii") != text:
            raise ReadEpochError("finite native capture exceeds its existing file bound")
        return data

    total_output = 0
    decoded = []
    latest = {}
    machine_index = 0
    output_paths = {"/repo/" + path for path in trace.get("output_authority", {}).get("paths", ())}
    reserve(
        sys.getsizeof(latest) + sys.getsizeof(output_paths)
        + sum(sys.getsizeof(path) for path in output_paths)
    )
    for ordinal, (row, request, boundary) in enumerate(zip(value, plan, roots), 1):
        if (
            not isinstance(row, dict)
            or set(row) != {"ordinal", "initial", "stdout", "stderr", "observation", "outputs"}
            or type(row["ordinal"]) is not int or row["ordinal"] != ordinal
            or row["initial"] != boundary["initial"]
            or not isinstance(row["outputs"], list) or len(row["outputs"]) > count_limit
        ):
            raise ReadEpochError("finite native result has a foreign or reordered root")
        argv, cwd, environment = request
        validate_native_root(
            row["initial"], argv=list(argv), cwd=cwd, environment=dict(environment), returncode=0,
        )
        stdout, stderr = captured(row["stdout"]), captured(row["stderr"])
        total_output += len(stdout) + len(stderr)
        if total_output > output_limit:
            raise ReadEpochError("finite native results exceed their cumulative process output bound")
        observation = captured(row["observation"])
        while machine_index < boundary["last"]:
            event = trace["machine"]["events"][machine_index]
            machine_index += 1
            if event["kind"] == "native-output":
                effect = event["event"]
                if effect["kind"] == "output-settled":
                    before = sys.getsizeof(latest)
                    latest[effect["path"]] = effect
                    if sys.getsizeof(latest) > before:
                        reserve(sys.getsizeof(latest))
                elif effect["kind"] == "output-retire":
                    latest.pop(effect["destination"], None)
                elif effect["kind"] == "output-replace":
                    previous = latest.pop(effect["source"], None)
                    if previous is None:
                        raise ReadEpochError("finite native output replacement lost its settlement")
                    replacement = dict(effect, sha256=previous["sha256"])
                    reserve(sys.getsizeof(replacement))
                    latest[effect["path"]] = replacement
        outputs = []
        names = set()
        for output in row["outputs"]:
            if (
                not isinstance(output, dict)
                or set(output) != {"path", "owner", "serial", "revision", "identity", "data"}
                or not isinstance(output["path"], str) or output["path"] in names
                or output["path"] not in output_paths
                or any(type(output[key]) is not int for key in ("owner", "serial", "revision"))
                or not isinstance(output["identity"], list) or len(output["identity"]) != 7
                or any(type(value) is not int for value in output["identity"])
                or output["identity"][2] & 0o7000
            ):
                raise ReadEpochError("finite native output escapes its issued namespace")
            data = captured(output["data"])
            try:
                validate_publication_identity(
                    output["identity"], stat.S_IMODE(output["identity"][2]), len(data),
                )
            except ChannelError as error:
                raise ReadEpochError(str(error)) from error
            settlement = latest.get(output["path"])
            if (
                settlement is None
                or any(output[key] != settlement[key] for key in (
                    "path", "owner", "serial", "revision", "identity",
                ))
                or hashlib.sha256(data).hexdigest() != settlement["sha256"]
            ):
                raise ReadEpochError("finite native output differs from its actual settled version")
            names.add(output["path"])
            outputs.append((output["path"][6:], data, stat.S_IMODE(output["identity"][2])))
        if names != latest.keys() & output_paths:
            raise ReadEpochError("finite native result omitted an actual settled output")
        decoded.append((stdout, stderr, observation, tuple(outputs)))
        reserve(sys.getsizeof(outputs) + sys.getsizeof(decoded) + sys.getsizeof(decoded[-1]))
    return tuple(decoded)


def native_job_tree(events, job, parent, executables, *, count_limit, writable=False,
                    compiler_profile=None, compiler_sources=(), reserve=lambda size: None):
    if not isinstance(events, list) or not 2 <= len(events) <= count_limit:
        raise ReadEpochError("native job tree has an incomplete event extent")
    nodes, signals = {}, []
    root = job["pid"]
    if type(root) is not int or not 0 < root < 1 << 31 or root == parent:
        raise ReadEpochError("native job tree has a foreign root process")
    for number, event in enumerate(events, 1):
        if not isinstance(event, dict) or not isinstance(event.get("kind"), str):
            raise ReadEpochError("native job tree has an invalid event")
        kind = event["kind"]
        fields = {
            "exec": {"parent", "generation", "path", "argv", "cwd"} | (
                {"admission"} if writable else set()
            ),
            "fork": {"child"}, "start": set(), "exit": {"status"},
            "signal": {"child", "code", "status"},
            "pipe-error": {"syscall", "error"},
            **({"close-range": {"first", "last", "flags", "result", "bindings"}} if writable else {}),
        }
        if (
            kind not in fields or set(event) != {"seq", "kind", "pid"} | fields[kind]
            or type(event["seq"]) is not int or event["seq"] != number
            or type(event["pid"]) is not int or not 0 < event["pid"] < 1 << 31
        ):
            raise ReadEpochError("native job tree has a foreign event shape/sequence")
        pid = event["pid"]
        if number == 1:
            if kind != "exec" or pid != root:
                raise ReadEpochError("native job tree lost its original root dispatch")
            if parent is None:
                parent = event["parent"]
            if (
                type(parent) is not int or not 0 < parent < 1 << 31 or parent == root
                or event["parent"] != parent
            ):
                raise ReadEpochError("native job tree has a foreign original parent")
            nodes[pid] = {"parent": parent, "generation": 0, "status": None, "started": True}
        node = nodes.get(pid)
        if node is None or node["status"] is not None:
            raise ReadEpochError("native job tree event belongs to an unknown or retired process")
        if kind == "start":
            if node["started"]:
                raise ReadEpochError("native job tree reused its child start")
            node["started"] = True
        elif not node["started"]:
            raise ReadEpochError("native job tree omitted its stopped child start")
        elif kind == "fork":
            child = event["child"]
            if type(child) is not int or not 0 < child < 1 << 31 or child in nodes or child == parent:
                raise ReadEpochError("native job tree reused a child or its original Make parent")
            nodes[child] = {"parent": pid, "generation": 0, "status": None, "started": False}
        elif kind == "signal":
            child = event["child"]
            if (
                type(child) is not int or child not in nodes or nodes[child]["parent"] != pid
                or type(event["code"]) is not int or event["code"] not in {1, 2, 3}
                or type(event["status"]) is not int or not 0 <= event["status"] <= 255
            ):
                raise ReadEpochError("native job tree has a foreign child signal")
            signals.append(event)
        elif kind == "pipe-error":
            if (
                type(event["syscall"]) is not int or event["syscall"] not in {1, 18, 20}
                or type(event["error"]) is not int or event["error"] != errno.EPIPE
            ):
                raise ReadEpochError("native job tree has an invalid owned pipe error")
        elif kind == "close-range":
            _native_lock_range(event)
        elif kind == "exec":
            inputs = native_execution_input(event["argv"], event["cwd"])
            if writable:
                root_admission = job.get("admission")
                if not isinstance(root_admission, dict) or not isinstance(root_admission.get("outputs"), list):
                    raise ReadEpochError("native job tree lacks its root Command owner")
                native_image_admission(
                    event["admission"], inputs, root_admission["outputs"],
                    root_admission.get("resources", ()), resource_field="resources" in root_admission,
                    compiler_profile=compiler_profile,
                )
                if number == 1 and event["admission"] != root_admission:
                    raise ReadEpochError("native root image changed its original Command owner")
            if (
                type(event["parent"]) is not int or event["parent"] != node["parent"]
                or type(event["generation"]) is not int or event["generation"] != node["generation"] + 1
                or not isinstance(event["path"], str) or event["path"] not in executables
                or number == 1 and any(event[key] != job[key] for key in ("argv", "cwd"))
                or number == 1 and event["path"] != job["executable"]
            ):
                raise ReadEpochError("native job tree substituted an original exec/input binding")
            node["generation"] += 1
        else:
            status = event["status"]
            if type(status) is not int or not 0 <= status < 1 << 32 or not (os.WIFEXITED(status) or os.WIFSIGNALED(status)):
                raise ReadEpochError("native job tree has an invalid terminal wait status")
            if pid == root and (number != len(events) or status != job["terminal_status"]):
                raise ReadEpochError("native job root returned before its descendants or with a foreign status")
            node["status"] = status
    if any(node["status"] is None for node in nodes.values()):
        raise ReadEpochError("native job tree omitted a terminal descendant")
    for event in signals:
        status = nodes[event["child"]]["status"]
        expected = os.WEXITSTATUS(status) if os.WIFEXITED(status) else os.WTERMSIG(status)
        code = 1 if os.WIFEXITED(status) else 3 if os.WCOREDUMP(status) else 2
        if event["status"] != expected or event["code"] != code:
            raise ReadEpochError("native child signal differs from its actual terminal status")
    if compiler_profile is not None and any(
        event["kind"] == "exec" and (
            event["path"] in {compiler_profile.driver, compiler_profile.frontend}
            or "compiler" in event["admission"]
        ) for event in events
    ):
        native_compiler_lineage(
            events, job, compiler_profile, sources=compiler_sources, count_limit=count_limit,
            reserve=reserve,
        )
    return nodes


def native_job_context(value):
    if (
        not isinstance(value, dict) or set(value) != {"kind", "target", "command_line"}
        or not isinstance(value["kind"], str) or value["kind"] not in {"expansion", "recipe"}
        or value["kind"] == "expansion" and (
            value["target"] is not None or value["command_line"] is not None
        )
        or value["kind"] == "recipe" and (
            not isinstance(value["target"], str) or not value["target"] or "\0" in value["target"]
            or any(0xD800 <= ord(char) <= 0xDFFF for char in value["target"])
            or len(value["target"].encode("utf-8")) > 4096
            or type(value["command_line"]) is not int or not 0 <= value["command_line"] < 1 << 32
        )
    ):
        raise ReadEpochError("native job has inconsistent context evidence")
    return value


def _machine_events(value, *, count_limit):
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int or value["version"] not in {1, 2, 3, 4}
        or set(value) != {"version", "events", "closed"} | ({"roots"} if value["version"] in FINITE_MACHINE_VERSIONS else set())
        or value["closed"] is not True
        or not isinstance(value["events"], list) or not 1 <= len(value["events"]) <= count_limit
    ):
        raise ReadEpochError("incomplete native machine observations")
    return value["events"]

def native_machine_roots(value, trace, *, count_limit, reserve):
    roots = value["roots"]
    if (
        trace["version"] not in RUNTIME_VERSIONS
        or not isinstance(roots, list) or not 1 <= len(roots) <= count_limit
    ):
        raise ReadEpochError("finite native machine lacks its complete root extent")
    pids, next_machine, next_exec = set(), 1, 1
    reserve(sys.getsizeof(pids))
    for ordinal, row in enumerate(roots, 1):
        if (
            not isinstance(row, dict)
            or set(row) != {"ordinal", "initial", "first", "last", "first_exec", "last_exec"}
            or any(type(row[key]) is not int for key in ("ordinal", "first", "last", "first_exec", "last_exec"))
            or row["ordinal"] != ordinal or row["first"] != next_machine
            or not row["first"] <= row["last"] <= len(value["events"])
            or row["first_exec"] != next_exec
            or not row["first_exec"] <= row["last_exec"] <= len(trace["events"])
            or not isinstance(row["initial"], dict)
        ):
            raise ReadEpochError("finite native machine has a foreign or unordered root range")
        initial = row["initial"]
        if (
            not {"argv", "cwd", "environment"} <= initial.keys()
            or not isinstance(initial["environment"], dict)
            or any(
                not isinstance(key, str) or not key or "=" in key or "\0" in key
                or not isinstance(item, str) or "\0" in item
                or any(0xD800 <= ord(char) <= 0xDFFF for word in (key, item) for char in word)
                for key, item in initial["environment"].items()
            )
        ):
            raise ReadEpochError("finite native root has malformed initial inputs")
        validate_native_root(
            initial, argv=initial["argv"], cwd=initial["cwd"],
            environment=initial["environment"], returncode=0,
        )
        if initial["pid"] in pids:
            raise ReadEpochError("finite native machine reused an actual root PID")
        first = value["events"][row["first"] - 1]
        if (
            not isinstance(first, dict)
            or
            first.get("kind") != "clear" or first.get("pid") != initial["pid"]
            or first.get("exec") != row["first_exec"] - 1
        ):
            raise ReadEpochError("finite native root omits its actual initial register clear")
        previous_size = sys.getsizeof(pids)
        pids.add(initial["pid"])
        if sys.getsizeof(pids) > previous_size:
            reserve(sys.getsizeof(pids))
        next_machine, next_exec = row["last"] + 1, row["last_exec"] + 1
    if next_machine != len(value["events"]) + 1 or next_exec != trace["events"][-1]["execs"] + 1:
        raise ReadEpochError("finite native machine omits actual root rows or executions")
    return roots


def native_root_owner(roots, execution):
    index = bisect_right(roots, execution, key=lambda row: row["first_exec"]) - 1
    if index < 0 or execution > roots[index]["last_exec"]:
        raise ReadEpochError("finite native execution has no actual root owner")
    return roots[index]["initial"]["pid"]


def validate_machine_observations(value, trace, *, count_limit, reserve=lambda size: None):
    _machine_events(value, count_limit=count_limit)
    roots = native_machine_roots(
        value, trace, count_limit=count_limit, reserve=reserve,
    ) if value["version"] in FINITE_MACHINE_VERSIONS else None
    patterns = trace["version"] in PATTERN_VERSIONS
    if (value["version"] in {3, 4}) != patterns:
        raise ReadEpochError("native machine version differs from the issued pattern protocol")
    def make_owner(execution):
        if roots is None:
            return make_pid
        return native_root_owner(roots, execution)
    common = {"seq", "kind", "trace_seq", "pid", "exec", "pass"}
    fields = {
        "clear": {"registers"}, "arm": {"registers", "slots"},
        "trap": {"index", "purpose", "pc", "status", "sigcode"},
        "pin-retired": {"visit", "source", "identity"},
        "execute": {"make", "dispatch"},
        "native-policy": {"dispatch", "child", "context", "ignored", "status"},
    }
    purposes = {"pass-entry", "source-entry", "source-return", "pass-return", "assignment-completion"}
    runtime = trace["version"] in RUNTIME_VERSIONS
    if runtime:
        fields["execute"] |= {"input_sha256"}
        purposes |= {"effect-entry", "effect-return", "effect-completion", "eval-entry", "eval-return", "expansion-entry", "expansion-return"}
        fields.update({
            "effect-input": {"effect", "sha256"}, "effect-result": {"effect", "sha256"},
            "eval-buffer": {"evaluation", "source", "sha256"},
            "expansion-input": {"expansion", "sha256"},
            "native-tree": {"dispatch", "event", "sha256"},
        })
    if trace["version"] in WRITABLE_VERSIONS:
        fields["execute"].add("admission_owner")
        fields["native-output"] = {"dispatch", "event", "sha256"}
        fields["generated-source-entry"] = {
            "visit", "owner", "serial", "revision", "path", "identity", "sha256",
        }
    runtime_bindings = {kind: set() for kind in ("effect-input", "effect-result", "eval-buffer", "expansion-input")}
    pattern_bindings = {
        "pattern-template-input": ("template", "pattern-template-entry"),
        "pattern-template-result": ("template", "pattern-template-completion"),
        "pattern-input": ("pattern", "pattern-entry"),
        "pattern-definition-input": ("pattern", "pattern-definition"),
        "pattern-definition-result": ("pattern", "pattern-definition-return"),
        "pattern-result": ("pattern", "pattern-completion"),
    } if patterns else {}
    for kind, (key, _) in pattern_bindings.items():
        fields[kind] = {key, "sha256"}
        runtime_bindings[kind] = set()
    if patterns:
        purposes |= {"pattern-selection", "pattern-completion", "pattern-definition-return"}
    postread_guards = set()
    armed, retired = {}, set()
    previous = 0
    make_pid = None
    make_execs, child_dispatches = set(), set()
    native_roots, native_trees = {}, {}
    output_bindings, range_closure = {}, None
    native_policies = set()
    native_owners, native_cleared, previous_pid_events = {}, set(), {}
    root_index = 0
    for number, row in enumerate(value["events"], 1):
        if roots is not None:
            if number > roots[root_index]["last"]:
                if native_policies != child_dispatches or range_closure is not None or output_bindings:
                    raise ReadEpochError("finite native root crossed incomplete job or descriptor custody")
                root_index += 1
            root = roots[root_index]
            make_pid = root["initial"]["pid"]
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
        if roots is not None and not root["first_exec"] - 1 <= row["exec"] <= root["last_exec"]:
            raise ReadEpochError("finite native machine row borrowed another root execution")
        prior_pid = previous_pid_events.get(pid)
        previous_pid_events[pid] = row
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
                native_cleared.add(pid)
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
                or not runtime and (
                    issued.get(0, [None, None])[1] != "pass-entry"
                    or issued.get(1, [None, None])[1] != "source-entry"
                    or 2 in issued and issued[2][1] != "source-return"
                    or 3 in issued and issued[3][1] not in {"assignment-completion", "pass-return"}
                )
                or runtime and (
                    not (
                        len(issued) == 2
                        and context is not None and context["kind"] == "exec"
                        and issued.get(0, [None, None])[1] == "pass-entry"
                        and issued.get(1, [None, None])[1] == "source-entry"
                    ) and not (
                        len(issued) == 4
                        and context is not None and context["kind"] in (
                            {"pass-exit", "expansion-exit", "pattern-completion"} if patterns else {"pass-exit", "expansion-exit"}
                        )
                        and issued.get(0, [None, None])[1] == "expansion-entry"
                        and issued.get(1, [None, None])[1] == ("pattern-selection" if patterns else "source-entry")
                        and issued.get(2, [None, None])[1] == "eval-entry"
                        and issued.get(3, [None, None])[1] == "effect-entry"
                    ) and not (
                        len(issued) == 4
                        and context is not None and context["kind"] not in (
                            {"exec", "pass-exit", "expansion-exit", "pattern-completion"} if patterns else {"exec", "pass-exit", "expansion-exit"}
                        )
                        and issued.get(0, [None, None])[1] == "source-entry"
                        and issued.get(1, [None, None])[1] == "eval-entry"
                        and issued.get(2, [None, None])[1] == "effect-entry"
                        and issued.get(3, [None, None])[1] in {
                            "pass-return", "source-return", "eval-return", "effect-return", "effect-completion", "expansion-return",
                        } | ({"pattern-completion", "pattern-definition-return"} if patterns else set())
                    )
                )
                or registers[:4] != [issued.get(index, [0])[0] for index in range(4)]
                or registers[5] != sum(1 << (2 * index) for index in issued)
            ):
                raise ReadEpochError("native readback differs from its issued slots/control")
            if make_pid is not None and pid != make_pid:
                raise ReadEpochError("native arm belongs to a foreign Make child")
            if runtime and context is not None and context["kind"] in (
                {"pass-exit", "expansion-exit", "pattern-completion"} if patterns else {"pass-exit", "expansion-exit"}
            ):
                if previous in postread_guards:
                    raise ReadEpochError("runtime post-read guard is repeated")
                postread_guards.add(previous)
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
                or runtime and (
                    row["make"] and row["input_sha256"] is not None
                    or not row["make"] and (
                        not isinstance(row["input_sha256"], str)
                        or re.fullmatch("[0-9a-f]{64}", row["input_sha256"]) is None
                    )
                )
                or trace["version"] in WRITABLE_VERSIONS and (
                    row["make"] and row["admission_owner"] is not None
                    or not row["make"] and (
                        not isinstance(row["admission_owner"], str)
                        or re.fullmatch("[0-9a-f]{64}", row["admission_owner"]) is None
                    )
                )
            ):
                raise ReadEpochError("native execution lacks its immediately preceding child clear")
            if row["make"]:
                if roots is not None and (
                    pid != make_pid or not root["first_exec"] <= row["exec"] + 1 <= root["last_exec"]
                ):
                    raise ReadEpochError("finite native execution differs from its actual root")
                make_execs.add((previous + 1, row["exec"] + 1, pid))
            else:
                child_dispatches.add(row["dispatch"])
                native_roots[row["dispatch"]] = row
                native_trees[row["dispatch"]] = []
                if runtime and pid in native_owners:
                    raise ReadEpochError("native root execution reused an owned process")
                native_owners[pid] = (row["dispatch"], False)
        elif kind == "native-policy":
            dispatch = row["dispatch"]
            if (
                pid != make_pid or type(dispatch) is not int or dispatch not in native_roots
                or dispatch in native_policies or type(row["child"]) is not int
                or row["child"] != native_roots[dispatch]["pid"]
                or any(row[key] != native_roots[dispatch][key] for key in ("exec", "pass"))
                or type(row["ignored"]) is not bool or type(row["status"]) is not int
                or not 0 <= row["status"] < 1 << 32
                or not (os.WIFEXITED(row["status"]) or os.WIFSIGNALED(row["status"]))
            ):
                raise ReadEpochError("native job policy lacks its actual Make/child dispatch")
            native_job_context(row["context"])
            if runtime and (
                not native_trees[dispatch] or native_trees[dispatch][-1].get("kind") != "exit"
                or native_trees[dispatch][-1].get("pid") != row["child"]
                or native_trees[dispatch][-1].get("status") != row["status"]
            ):
                raise ReadEpochError("native job policy precedes or differs from its actual terminal tree")
            native_policies.add(dispatch)
        elif kind == "native-tree":
            dispatch, event = row["dispatch"], row["event"]
            if (
                type(dispatch) is not int or dispatch not in native_roots
                or not isinstance(event, dict) or event.get("pid") != pid
                or not isinstance(row["sha256"], str)
                or row["sha256"] != hashlib.sha256(encoded(event)).hexdigest()
                or native_owners.get(pid, (None, False))[0] != dispatch or pid not in native_cleared
            ):
                raise ReadEpochError("native machine tree lost its actual root/event payload")
            if event.get("kind") == "fork":
                child = event.get("child")
                if type(child) is not int or child <= 0 or child in native_owners:
                    raise ReadEpochError("native machine tree reused its owned child")
                native_owners[child] = (dispatch, False)
                native_cleared.discard(child)
            elif event.get("kind") == "exec":
                if prior_pid is None or not (
                    prior_pid["kind"] == "clear"
                    or prior_pid["kind"] == "execute" and prior_pid["make"] is False
                    and event.get("generation") == 1 and native_roots[dispatch]["pid"] == pid
                ):
                    raise ReadEpochError("native tree exec omitted its actual register clear")
            elif event.get("kind") == "start" and (prior_pid is None or prior_pid["kind"] != "clear"):
                raise ReadEpochError("native tree start omitted its inherited register clear")
            if event.get("kind") in {"exec", "start"}:
                native_owners[pid] = (dispatch, True)
            if event.get("kind") == "close-range":
                _native_lock_range(event)
                expected_bindings = [
                    {"fd": descriptor, "serial": binding[0], "description": binding[1]}
                    for (process, descriptor), binding in sorted(output_bindings.items())
                    if process == pid and event["first"] <= descriptor <= event["last"]
                ]
                shared_paths = {
                    "/repo/" + path
                    for role, path in trace.get("output_authority", {}).get("resources", ())
                    if role == "shared-lock"
                }
                if (
                    range_closure is not None or event["bindings"] != expected_bindings
                    or not expected_bindings
                    or any(
                        output_bindings[(pid, binding["fd"])][2] not in shared_paths
                        or not any(
                            process != pid and previous[:2] == (binding["serial"], binding["description"])
                            for (process, _), previous in output_bindings.items()
                        )
                        for binding in expected_bindings
                    )
                ):
                    raise ReadEpochError("native close_range lost its complete inherited lock bindings")
                range_closure = (dispatch, pid, event["result"], list(expected_bindings))
            if trace["version"] in WRITABLE_VERSIONS and event.get("kind") == "exit":
                del native_owners[pid]
                native_cleared.discard(pid)
            native_trees[dispatch].append(event)
        elif kind == "native-output":
            dispatch, event = row["dispatch"], row["event"]
            if (
                type(dispatch) is not int or dispatch not in native_roots
                or native_roots[dispatch]["pid"] != pid
                or not isinstance(event, dict)
                or row["sha256"] != hashlib.sha256(encoded(event)).hexdigest()
            ):
                raise ReadEpochError("native output machine event lost its actual job binding")
            actor = event.get("pid")
            if (
                dispatch in native_policies
                or actor is not None and (
                    type(actor) is not int
                    or native_owners.get(actor, (None, False))[0] != dispatch
                    or not native_owners[actor][1] and event.get("kind") != "output-inherit"
                )
                or actor is None and (dispatch, True) not in native_owners.values()
                or event.get("kind") == "output-inherit" and (
                    type(event.get("parent")) is not int
                    or native_owners.get(event["parent"]) != (dispatch, True)
                    or not native_trees[dispatch]
                    or native_trees[dispatch][-1].get("kind") != "fork"
                    or native_trees[dispatch][-1].get("child") != actor
                    or native_trees[dispatch][-1].get("pid") != event["parent"]
                )
            ):
                raise ReadEpochError("native output event lacks its live job actor at the actual sequence")
            if range_closure is not None:
                expected_dispatch, expected_actor, result, pending_bindings = range_closure
                if dispatch != expected_dispatch or actor != expected_actor:
                    raise ReadEpochError("native close_range return lost its actual actor")
                if result == 0:
                    expected = pending_bindings.pop(0)
                    if event.get("kind") != "output-range-close" or any(
                        event.get(key) != expected[key] for key in expected
                    ):
                        raise ReadEpochError("native close_range omitted or changed its descriptor retirement")
                    if not pending_bindings:
                        range_closure = None
                elif (
                    event.get("kind") != "output-operation-failed"
                    or event.get("operation") != "close-range" or event.get("result") != result
                ):
                    raise ReadEpochError("native close_range changed its failed descriptor lifetime")
                else:
                    _native_range_failure_binding(
                        event, output_bindings[(actor, pending_bindings[0]["fd"])],
                    )
                    range_closure = None
            elif event.get("kind") == "output-range-close":
                raise ReadEpochError("native range closure lacks its actual close_range return")
            if event.get("kind") in {"output-open", "output-inherit", "output-dup"}:
                required_binding = {"serial", "description", "path", "owner", "fd"} | (
                    {"result"} if event["kind"] == "output-dup" else set()
                )
                if (
                    not required_binding <= event.keys() or type(actor) is not int
                    or any(type(event[key]) is not int or event[key] < 1 for key in ("serial", "description", "owner"))
                    or type(event["fd"]) is not int or event["fd"] < 0
                    or event["path"] is not None and not isinstance(event["path"], str)
                    or event["kind"] == "output-dup" and (
                        type(event["result"]) is not int or event["result"] < 0
                    )
                ):
                    raise ReadEpochError("native machine output has an invalid descriptor binding")
                descriptor = event["result"] if event["kind"] == "output-dup" else event["fd"]
                output_bindings[(actor, descriptor)] = (
                    event["serial"], event["description"], event["path"], event["owner"],
                )
            elif event.get("kind") in {
                "output-close", "output-range-close", "output-exec-close", "output-close-failed", "output-duplicate-release",
            }:
                if type(actor) is not int or type(event.get("fd")) is not int or event["fd"] < 0:
                    raise ReadEpochError("native machine output has an invalid descriptor retirement")
                output_bindings.pop((actor, event["fd"]), None)
            if event.get("kind") == "output-exec-close":
                execs = [
                    prior for prior in native_trees[dispatch]
                    if prior.get("kind") == "exec" and prior.get("pid") == event.get("pid")
                ]
                if (
                    not execs or type(event.get("generation")) is not int
                    or execs[-1]["generation"] != event["generation"]
                ):
                    raise ReadEpochError("native exec output closure lacks its successful actual image generation")
        elif kind in runtime_bindings:
            event_kind = pattern_bindings[kind][1] if kind in pattern_bindings else {
                "effect-input": "effect-entry", "effect-result": "effect-completion",
                "eval-buffer": "eval-entry", "expansion-input": "expansion-entry",
            }[kind]
            key = pattern_bindings[kind][0] if kind in pattern_bindings else "evaluation" if kind == "eval-buffer" else "expansion" if kind == "expansion-input" else "effect"
            if (
                context is None or context["kind"] != event_kind or pid != make_pid
                or type(row[key]) is not int or row[key] <= 0
                or row[key] != context[key] or row[key] in runtime_bindings[kind]
                or not isinstance(row["sha256"], str) or re.fullmatch("[0-9a-f]{64}", row["sha256"]) is None
            ):
                raise ReadEpochError("runtime machine payload lacks its actual event association")
            if kind == "eval-buffer":
                if (
                    type(row["source"]) is not int or row["source"] != context["source"]
                    or row["sha256"] != trace["sources"][row["source"] - 1]["sha256"]
                ):
                    raise ReadEpochError("runtime machine eval bytes differ from pristine capture")
            elif row["sha256"] != hashlib.sha256(encoded(context)).hexdigest():
                raise ReadEpochError("runtime machine effect payload differs from its observed event")
            runtime_bindings[kind].add(row[key])
        elif kind == "generated-source-entry":
            if (
                pid != make_pid or context is None or context["kind"] != "source-entry"
                or type(row["visit"]) is not int or row["visit"] != context["visit"]
            ):
                raise ReadEpochError("generated source pin lacks its actual Make entry")
        else:
            failed_generated = (
                trace["version"] in WRITABLE_VERSIONS and context is not None
                and context["kind"] == "source-exit" and context["error"] > 0
                and context["source"] is None and row["source"] is None
            )
            if (
                context is None or context["kind"] != "source-exit"
                or pid != make_pid
                or type(row["visit"]) is not int or row["visit"] <= 0
                or not failed_generated and (type(row["source"]) is not int or row["source"] <= 0)
                or (row["visit"], row["source"]) != (context["visit"], context["source"])
                or not failed_generated and context["error"] != 0 or row["visit"] in retired
            ):
                raise ReadEpochError("native pin retirement lacks its successful source return")
            opens = [
                event for event in trace["events"][:previous]
                if event["kind"] == "source-open" and event["visit"] == row["visit"]
                and event["source"] == row["source"]
            ]
            generated = (
                trace["version"] in WRITABLE_VERSIONS and len(opens) == 1
                and isinstance(opens[0]["custody"], dict)
                and opens[0]["custody"]["kind"] == "native-output"
            )
            if failed_generated or generated:
                entries = [
                    entry for entry in value["events"][:number - 1]
                    if entry["kind"] == "generated-source-entry" and entry["visit"] == row["visit"]
                ]
                if (
                    len(opens) != 1 or len(entries) != 1
                    or failed_generated and opens[0]["result"] != -context["error"]
                    or opens[0]["custody"] != {"kind": "native-output", "entry": entries[0]["seq"]}
                    or not isinstance(row["identity"], list) or len(row["identity"]) != 7
                    or any(type(item) is not int for item in row["identity"])
                ):
                    raise ReadEpochError("generated pin retirement lacks its actual open and entry")
            elif len(opens) != 1 or row["identity"] != opens[0]["identity"]:
                raise ReadEpochError("native retired pin identity differs from its actual open")
            retired.add(row["visit"])
    expected = {
        event["visit"] for event in trace["events"]
        if event["kind"] == "source-exit" and event["source"] is not None
    }
    if trace["version"] in WRITABLE_VERSIONS:
        expected.update(
            event["visit"] for event in trace["events"]
            if event["kind"] == "source-open" and event["result"] < 0
            and isinstance(event["custody"], dict) and event["custody"].get("kind") == "native-output"
        )
    if trace["version"] in WRITABLE_VERSIONS and {
        row["seq"] for row in value["events"] if row["kind"] == "generated-source-entry"
    } != {
        event["custody"]["entry"] for event in trace["events"]
        if event["kind"] == "source-open" and isinstance(event["custody"], dict)
        and event["custody"].get("kind") == "native-output"
    }:
        raise ReadEpochError("generated source entries omit their actual opened consumers")
    trap_kinds = {
        "pass-entry": "pass-entry", "source-entry": "source-entry",
        "source-exit": "source-return", "assignment-completion": "assignment-completion",
        "pass-exit": "pass-return",
    }
    if runtime:
        trap_kinds.update({
            "effect-entry": "effect-entry", "effect-return": "effect-return",
            "effect-completion": "effect-completion", "eval-entry": "eval-entry",
            "eval-exit": "eval-return",
            "expansion-entry": "expansion-entry", "expansion-exit": "expansion-return",
        })
    if patterns:
        trap_kinds.update({
            "pattern-entry": "pattern-selection",
            "pattern-definition": "effect-entry",
            "pattern-definition-return": "pattern-definition-return",
            "pattern-completion": "pattern-completion",
        })
    immediate_effects = {
        event["effect"] for event in trace["events"]
        if event["kind"] == "effect-entry" and event["caller"] == "reader"
    } if runtime else set()
    def trap_predecessor(event):
        previous = event["seq"] - 1
        if patterns:
            while previous and trace["events"][previous - 1]["kind"] in {
                "pattern-template-entry", "pattern-template-completion",
            }:
                previous -= 1
        return previous
    required = {
        (trap_predecessor(event), trap_kinds[event["kind"]])
        for event in trace["events"] if event["kind"] in trap_kinds
        and not (runtime and event["kind"] == "effect-completion" and event["effect"] in immediate_effects)
    }
    observed = {
        (row["trace_seq"], row["purpose"]) for row in value["events"] if row["kind"] == "trap"
    }
    if (
        retired != expected or (required != observed if runtime else not required <= observed)
        or len(value["events"]) + len(trace["events"]) + (len(roots) if roots is not None else 0) > count_limit
        or make_execs != {
            (event["seq"], event["exec"], make_owner(event["exec"]))
            for event in trace["events"] if event["kind"] == "exec"
        }
    ):
        raise ReadEpochError("native machine observations omit traps or live pin retirement")
    if range_closure is not None:
        raise ReadEpochError("native machine observations omit close_range completion")
    if native_policies != child_dispatches:
        raise ReadEpochError("native job machine observations omit completed policy bindings")
    if runtime and any(
        runtime_bindings[kind] != {
            event[key] for event in trace["events"] if event["kind"] == event_kind
        }
        for kind, key, event_kind in (
            ("effect-input", "effect", "effect-entry"),
            ("effect-result", "effect", "effect-completion"),
            ("eval-buffer", "evaluation", "eval-entry"),
            ("expansion-input", "expansion", "expansion-entry"),
        ) + tuple((kind, key, event_kind) for kind, (key, event_kind) in pattern_bindings.items())
    ):
        raise ReadEpochError("runtime machine observations omit effect/eval payload bindings")
    if runtime and postread_guards != {
        event["seq"] for event in trace["events"] if event["kind"] in (
            {"pass-exit", "expansion-exit", "pattern-completion"} if patterns else {"pass-exit", "expansion-exit"}
        )
    }:
        raise ReadEpochError("runtime machine observations omit the actual post-read guard")
    if runtime:
        for dispatch, events in native_trees.items():
            if not events:
                raise ReadEpochError("native machine observations omit the original job tree")
            first, last = events[0], events[-1]
            if not isinstance(first, dict) or not isinstance(last, dict) or not {"argv", "cwd", "path"} <= first.keys() or "status" not in last:
                raise ReadEpochError("native machine tree has incomplete root execution/return")
            inputs = native_execution_input(first["argv"], first["cwd"])
            if hashlib.sha256(encoded(inputs)).hexdigest() != native_roots[dispatch]["input_sha256"]:
                raise ReadEpochError("native machine tree differs from its original job inputs")
            native_job_tree(
                events, {
                    "sequence": dispatch, "pid": native_roots[dispatch]["pid"], "argv": first["argv"], "cwd": first["cwd"],
                    "executable": first["path"], "terminal_status": last["status"],
                    **({"admission": first.get("admission")} if trace["version"] in WRITABLE_VERSIONS else {}),
                }, make_owner(native_roots[dispatch]["exec"]), {event["path"] for event in events if isinstance(event, dict) and event.get("kind") == "exec" and isinstance(event.get("path"), str)},
                count_limit=count_limit,
                writable=trace["version"] in WRITABLE_VERSIONS,
                compiler_profile=(
                    native_compiler_profile(trace["output_authority"]["compiler"], count_limit=count_limit, reserve=reserve)
                    if "compiler" in trace.get("output_authority", {}) else None
                ),
                compiler_sources=tuple(row["path"] for row in trace["selection"]["inventory"]),
                reserve=reserve,
            )
    return value


def _read_event_keys(version):
    keys = {
        "exec": {"exec"}, "pass-entry": {"exec", "pass", "inputs"},
        "source-entry": {"exec", "pass", "visit", "parent", "name", "flags"},
        "source-open": {"exec", "pass", "visit", "name", "mode", "result", "source", "identity"},
        "other-open": {"exec", "pass", "visit", "name", "mode", "result"},
        "source-exit": {"exec", "pass", "visit", "resolved", "flags", "error", "source"},
        "pass-exit": {"exec", "pass", "goals"}, "complete": {"execs", "passes", "visits"},
        "entry-image": {"exec", "pass", "barrier", "input_sha256", "image_sha256"},
    }
    if version in LOCATION_VERSIONS:
        keys["source-entry"] |= {"location"}
    if version in {3, COMPLETION_VERSION}:
        keys["assignment-completion"] = {
            "exec", "pass", "visit", "source", "site", "name", "operator", "cwd", "variable",
        }
    if version in MACHINE_VERSIONS:
        keys["source-open"] |= {"path", "custody"}
    if version in RUNTIME_VERSIONS:
        keys["complete"] |= {"effects", "evaluations", "expansions"}
    if version in PATTERN_VERSIONS:
        keys["complete"] |= {"templates", "patterns", "pattern_returns"}
    return keys


def native_output_effects(trace):
    return [row["event"] for row in trace["machine"]["events"] if row["kind"] == "native-output"]


def validate_native_output_authority(trace, *, count_limit, file_limit, reserve):
    authority = trace["output_authority"]
    resources = authority.get("resources", ()) if isinstance(authority, dict) else ()
    source_roots = authority.get("source_roots", []) if isinstance(authority, dict) else []
    if __package__:
        from .native_resources import resource_plan, resource_role, resource_operation, validate_resource_scope, require_retained_source, validate_terminal_resources
    else:
        from native_resources import resource_plan, resource_role, resource_operation, validate_resource_scope, require_retained_source, validate_terminal_resources
    try:
        resources = resource_plan(resources)
    except MakeProbeError as error:
        raise ReadEpochError(str(error)) from error
    if (
        not isinstance(authority, dict) or set(authority) != {"paths", "jobs"} | (
            {"resources"} if resources else set()
        ) | ({"source_roots"} if source_roots else set()) | ({"compiler"} if "compiler" in authority else set())
        or any(not isinstance(authority[key], list) or len(authority[key]) > count_limit
               for key in authority if key != "compiler")
        or not authority["paths"]
        or any(
            not isinstance(path, str) or not path or path.startswith("/")
            or any(part in {"", ".", ".."} for part in path.split("/"))
            for path in authority["paths"]
        )
        or len(set(authority["paths"])) != len(authority["paths"])
    ):
        raise ReadEpochError("malformed native output authority")
    if (
        any(not isinstance(path, str) for path in source_roots)
        or len(set(source_roots)) != len(source_roots)
    ):
        raise ReadEpochError("native output authority has invalid immutable source roots")
    reserve(len(encoded(authority)))
    compiler_profile = (
        native_compiler_profile(authority["compiler"], count_limit=count_limit, reserve=reserve)
        if "compiler" in authority else None
    )
    try:
        for path in source_roots:
            relative_path(path)
        sources = tuple(row["path"] for row in trace["selection"]["inventory"])
        reserve(sys.getsizeof(sources))
        if source_roots:
            root_paths = tuple(source_roots)
            reserve(sys.getsizeof(root_paths))
            sources += root_paths
            reserve(sys.getsizeof(sources))
        validate_resource_scope(resources, authority["paths"], sources)
    except MakeProbeError as error:
        raise ReadEpochError(str(error)) from error
    for path in authority["paths"]:
        try:
            relative_path(path)
        except MakeProbeError as error:
            raise ReadEpochError(f"native output plan has an invalid path: {error}") from error
        if any(
            path == source or path.startswith(source + "/")
            or source.startswith(path + "/")
            for source in sources
        ) or any(other != path and other.startswith(path + "/") for other in authority["paths"]):
            raise ReadEpochError("native output plan conflicts with immutable source or another output")
    jobs = {}
    machine = trace["machine"]["events"]
    effects = native_output_effects(trace)
    for number, record in enumerate(authority["jobs"], 1):
        if (
            not isinstance(record, dict) or set(record) != {"sequence", "pid", "admission"}
            or type(record["sequence"]) is not int or record["sequence"] != number
            or type(record["pid"]) is not int or record["pid"] < 1
            or not isinstance(record["admission"], dict)
            or set(record["admission"]) != {"sequence", "owner", "closure", "input_sha256", "outputs"} | (
                {"resources"} if resources else set()
            ) | ({"compiler"} if compiler_profile is not None and "compiler" in record["admission"] else set())
        ):
            raise ReadEpochError("native output job lacks its closed dispatch binding")
        tree = [
            row["event"] for row in machine
            if row["kind"] == "native-tree" and row["dispatch"] == number
        ]
        reserve(sys.getsizeof(tree))
        if not tree:
            raise ReadEpochError("native output job lacks its closed dispatch binding")
        job = dict(record, tree=tree)
        reserve(sys.getsizeof(job))
        admission = job["admission"]
        native_image_admission(
            admission, None, authority["paths"], resources, resource_field=bool(resources),
            compiler_profile=compiler_profile,
        )
        for index, event in enumerate(job["tree"]):
            if not isinstance(event, dict) or not isinstance(event.get("kind"), str):
                raise ReadEpochError("native output authority has a malformed image tree")
            if event["kind"] == "exec":
                if not {"argv", "cwd", "admission"} <= event.keys():
                    raise ReadEpochError("native image lacks its actual Command owner")
                native_image_admission(
                    event["admission"], native_execution_input(event["argv"], event["cwd"]),
                    admission["outputs"], admission.get("resources", ()),
                    resource_field="resources" in admission,
                    compiler_profile=compiler_profile,
                )
                if index == 0 and event["admission"] != admission:
                    raise ReadEpochError("native root image changed its original Command owner")
        try:
            if any(row not in resources for row in resource_plan(admission.get("resources", ()))):
                raise ReadEpochError("native job resources escape their closed plan")
        except MakeProbeError as error:
            raise ReadEpochError(str(error)) from error
        if (
            any(not isinstance(admission[key], str) or re.fullmatch("[0-9a-f]{64}", admission[key]) is None
                for key in ("owner", "closure", "input_sha256"))
            or not isinstance(admission["outputs"], list)
            or any(not isinstance(path, str) or path not in authority["paths"] for path in admission["outputs"])
            or len(set(admission["outputs"])) != len(admission["outputs"])
            or not any(
                row["kind"] == "execute" and row["dispatch"] == number
                and row["pid"] == job["pid"] and row["input_sha256"] == admission["input_sha256"]
                for row in machine
            )
        ):
            raise ReadEpochError("native output job differs from its actual machine lifecycle")
        if (
            admission["owner"] != native_command_owner(
                admission["closure"], admission["outputs"], admission.get("resources", ()),
            )
            or any(
                row["admission_owner"] != admission["owner"] for row in machine
                if row["kind"] == "execute" and not row["make"] and row["dispatch"] == number
            )
        ):
            raise ReadEpochError("native output plan differs from its issued Command owner")
        jobs[number] = job
    if set(jobs) != {row["dispatch"] for row in machine if row["kind"] == "execute" and not row["make"]}:
        raise ReadEpochError("native output authority omitted or added an actual job dispatch")
    image_sequences = [
        event["admission"]["sequence"] for job in jobs.values()
        for event in job["tree"] if event["kind"] == "exec"
    ]
    if sorted(image_sequences) != list(range(1, len(image_sequences) + 1)):
        raise ReadEpochError("native image admissions omitted or reused an issued request")
    common = {"sequence", "kind", "owner", "serial", "revision", "path"}
    fields = {
        "output-open": {"pid", "fd", "operation_owner", "identity", "writing", "description"},
        "output-write-entry": {"pid", "fd", "request", "requested_count", "offset", "description", "identity"},
        "output-write": {"pid", "fd", "request", "result", "identity"},
        "output-truncate": {"pid", "fd", "identity"},
        "output-write-failed": {"pid", "fd", "request", "result"},
        "output-settled": {"identity", "sha256"},
        "output-close": {"pid", "fd", "description"},
        "output-exec-close": {"pid", "fd", "description", "generation"},
        "output-duplicate-release": {"pid", "fd", "description"},
        "output-close-failed": {"pid", "fd", "result"},
        "output-dup": {"pid", "fd", "result", "description"},
        "output-inherit": {"parent", "pid", "fd", "description"},
        "output-mkdir": {"pid", "identity"},
        "output-rmdir": {"pid", "identity"},
        "output-directory-change": {"pid", "operation", "identity", "source", "destination", "entries", "before"},
    }
    if resources:
        fields["output-settled"].add("pid")
        fields["output-close-failed"].add("description")
        fields["output-range-close"] = {"pid", "fd", "description"}
        fields.update({
            "output-lock": {"pid", "fd", "description", "flags", "result", "mode"},
            "output-mode": {"pid", "fd", "mode", "identity"},
            "output-replace": {"pid", "source", "identity"},
            "output-retire": {"pid", "operation_owner", "identity", "destination"},
            "output-lock-release": {"pid", "fd", "description", "mode"},
        })
    objects, bindings, descriptions, readers, directories = {}, {}, set(), {}, {}
    writes = {}
    lock_modes = {}
    released_locks = {}
    parent_returns = {}
    request_number = 0
    process_plans = {}
    retained_paths = {"/repo/" + path for path in authority["paths"]}
    def settled_retained_read(serial, path):
        return (
            type(serial) is int and serial in objects and objects[serial]["settled"]
            and isinstance(path, str) and path in retained_paths and path == objects[serial]["path"]
            and not any(binding[0] == serial and binding[2] for binding in bindings.values())
            and not any(pending["serial"] == serial for pending in writes.values())
        )
    def parent_request(pid, operation, source, destination=None, *, readonly=False):
        parents = {
            path.rpartition("/")[0] for path in (source, destination)
            if path is not None and path.rpartition("/")[0] in directories
        }
        if parents:
            parent_returns[pid] = {"operation": operation, "source": source,
                                   "destination": destination, "parents": parents,
                                   "readonly": readonly}
    number = 0
    issued_serial = 0
    for observation in machine:
        if observation["kind"] == "native-tree":
            event = observation["event"]
            if event["kind"] == "exec":
                process_plans[event["pid"]] = event["admission"]
            elif event["kind"] == "fork":
                process_plans[event["child"]] = {}
            elif event["kind"] == "exit":
                process_plans.pop(event["pid"], None)
            continue
        if observation["kind"] == "generated-source-entry":
            serial, visit = observation["serial"], observation["visit"]
            try:
                require_retained_source(observation["path"], authority["paths"])
            except MakeProbeError as error:
                raise ReadEpochError(str(error)) from error
            if (
                any(type(observation[key]) is not int or observation[key] < 1 for key in ("owner", "serial", "visit"))
                or type(observation["revision"]) is not int or observation["revision"] < 0
                or visit in readers
            ):
                raise ReadEpochError("generated source entry has a malformed producer version")
            item = objects.get(serial)
            if (
                item is None or not item["settled"]
                or any(binding[0] == serial and binding[2] for binding in bindings.values())
                or observation["owner"] != item["owner"]
                or "/repo/" + observation["path"] != item["path"]
                or observation["revision"] != item["revision"]
                or observation["identity"] != item["identity"]
                or observation["sha256"] != item["sha256"]
            ):
                raise ReadEpochError("generated source entry borrowed an unsettled or foreign version")
            readers[visit] = serial
            continue
        if observation["kind"] == "pin-retired":
            serial = readers.pop(observation["visit"], None)
            if serial is not None and observation["identity"] != objects[serial]["identity"]:
                raise ReadEpochError("generated pin retirement differs from its current generated version")
            continue
        if observation["kind"] != "native-output":
            continue
        number += 1
        row = observation["event"]
        dispatch = observation["dispatch"]
        actor_job = jobs.get(dispatch)
        image_plan = process_plans.get(row["pid"], {}) if (
            isinstance(row, dict) and type(row.get("pid")) is int
        ) else {}
        image_outputs = image_plan.get("outputs", ())
        image_resources = image_plan.get("resources", ())
        def role(path):
            return resource_role(actor_job["admission"].get("resources", ()), path, actor_job["pid"])
        def image_role(path):
            return resource_role(image_resources, path, actor_job["pid"])
        def permitted(path, operation):
            return actor_job is not None and resource_operation(
                image_resources, path, actor_job["pid"], image_outputs, operation,
            )
        readonly_existing = (
            isinstance(row, dict) and row.get("kind") == "output-open"
            and row.get("writing") is False
            and settled_retained_read(row.get("serial"), row.get("path"))
            and not permitted(row["path"], "open")
        )
        if isinstance(row, dict) and row.get("kind") == "output-operation-failed":
            if type(row.get("pid")) is int and row["pid"] in writes:
                raise ReadEpochError("native failed operation omitted its pending write return")
            operation = row.get("operation")
            readonly_failed = (
                operation == "open" and isinstance(row.get("source"), str)
                and type(row.get("flags")) is int and row["flags"] >= 0
                and not row["flags"] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
                and row["flags"] & os.O_TMPFILE != os.O_TMPFILE
                and any(
                    settled_retained_read(serial, row["source"]) for serial in objects
                )
            )
            extra = {"flags"} if operation == "open" else {
                "descriptor", "duplicate_kind", "target", "minimum", "flags",
            } if operation == "dup" else set()
            if resources and isinstance(operation, str) and operation in {"mkdir", "replace"}:
                extra.add("flags")
            if (
                set(row) != {"sequence", "kind", "owner", "pid", "operation", "source", "destination", "result"} | extra | (
                    {"preimages"} if resources else set()
                )
                or type(row["sequence"]) is not int or row["sequence"] != number
                or type(row["owner"]) is not int or row["owner"] not in jobs
                or not isinstance(row["operation"], str)
                or row["operation"] not in {"open", "dup", "duplicate-release", "exec", "close-range", "mkdir", "rmdir", "remove", "replace"}
                or (not isinstance(row["destination"], str) if operation == "replace" else row["destination"] is not None)
                or not isinstance(row["source"], str)
                or operation != "dup" and row["source"] not in {
                    "/repo/" + path for path in jobs[row["owner"]]["admission"]["outputs"]
                } and not (actor_job is not None and role(row["source"]) is not None)
                and not readonly_failed
                or type(row["pid"]) is not int
                or actor_job is None
                or row["pid"] not in {event["pid"] for event in actor_job["tree"]}
                or type(row["result"]) is not int or not -4095 <= row["result"] < 0
            ):
                raise ReadEpochError("native failed descriptor transition lost its actual job or preimage")
            if operation in {"open", "mkdir", "rmdir", "remove", "replace"}:
                if row["owner"] != dispatch:
                    raise ReadEpochError("native failed operation owner differs from its actual pathname dispatch")
                if (
                    not readonly_failed and not permitted(row["source"], operation)
                    or operation == "replace" and not permitted(row["destination"], "replace")
                ):
                    raise ReadEpochError("native failed namespace operation escaped its resource role matrix for the current image")
            if resources:
                if actor_job is None:
                    raise ReadEpochError("native failed operation lost its actual actor")
                expected = []
                for path in (row["source"], row["destination"]) if operation == "replace" else (row["source"],):
                    item = directories.get(path) or next((item for item in objects.values() if item["path"] == path), None)
                    expected.append([path, None if item is None else item["identity"],
                                     None if item is None or "entries" not in item else item["entries"]])
                parents = {path.rpartition("/")[0] for path in (row["source"], row["destination"]) if path is not None}
                for parent in sorted(parents):
                    if parent in directories:
                        item = directories[parent]
                        expected.append([parent, item["identity"], item["entries"]])
                if operation in {"mkdir", "rmdir", "remove", "replace", "open"} and row["preimages"] != expected:
                    raise ReadEpochError("native failed namespace operation changed its observed operands")
            if operation in {"mkdir", "rmdir"}:
                if not resources or image_role(row["source"]) != "directory":
                    raise ReadEpochError("native failed directory operation lacks its exact resource role")
                if operation == "mkdir" and (
                    type(row["flags"]) is not int or row["flags"] & ~0o777 or row["flags"] & 0o700 != 0o700
                ):
                    raise ReadEpochError("native failed mkdir changed its requested mode")
                continue
            if operation in {"replace", "remove"}:
                if not resources:
                    raise ReadEpochError("native failed namespace operation escaped its resource role matrix")
                if operation == "replace" and (
                    type(row["flags"]) is not int or row["flags"] not in {0, 1}
                ):
                    raise ReadEpochError("native failed replacement lost its destination plan")
                continue
            if operation == "open":
                if (
                    type(row["flags"]) is not int or row["flags"] < 0
                    or row["flags"] & os.O_APPEND or row["flags"] & os.O_TMPFILE == os.O_TMPFILE
                    or row["flags"] & os.O_TRUNC and any(
                        binding[2] and objects[binding[0]]["path"] == row["source"]
                        for binding in bindings.values()
                    )
                    or resources and (
                        image_role(row["source"]) == "shared-lock" and row["flags"] & (os.O_WRONLY | os.O_TRUNC | os.O_EXCL)
                    )
                ):
                    raise ReadEpochError("native failed open changed its admitted flags or active writer preimage")
            elif operation == "dup":
                kind, target, minimum, flags = (
                    row["duplicate_kind"], row["target"], row["minimum"], row["flags"],
                )
                binding = bindings.get((row["pid"], row["descriptor"])) if type(row["descriptor"]) is int else None
                if (
                    binding is None or objects[binding[0]]["owner"] != row["owner"]
                    or row["source"] != "source-fd:" + str(row["descriptor"])
                    or not isinstance(kind, str) or kind not in {"dup", "dup2", "dup3", "fcntl-dupfd", "fcntl-dupfd-cloexec"}
                    or type(flags) is not int or flags not in ({0, os.O_CLOEXEC} if kind == "dup3" else {0})
                    or kind in {"dup2", "dup3"} and (
                        type(target) is not int or not -(1 << 31) <= target < 1 << 31 or minimum is not None
                    )
                    or kind == "dup" and (target is not None or minimum is not None)
                    or kind.startswith("fcntl-") and (
                        target is not None or type(minimum) is not int or not -(1 << 31) <= minimum < 1 << 31
                    )
                ):
                    raise ReadEpochError("native failed duplicate changed its actual source or operand plan")
            elif not any(
                pid == row["pid"] and objects[binding[0]]["owner"] == row["owner"]
                and objects[binding[0]]["path"] == row["source"]
                for (pid, descriptor), binding in bindings.items()
            ):
                raise ReadEpochError("native failed descriptor transition lost its live source binding")
            continue
        if (
            not isinstance(row, dict) or not isinstance(row.get("kind"), str) or row["kind"] not in fields
            or set(row) != common | fields[row["kind"]]
            or type(row["sequence"]) is not int or row["sequence"] != number
            or any(type(row[key]) is not int or row[key] < 1 for key in ("owner", "serial"))
            or type(row["revision"]) is not int or row["revision"] < 0
            or row["owner"] not in jobs
            or not isinstance(row["path"], str) and not (
                resources and row["kind"] == "output-retire" and row["path"] is None
                and isinstance(row.get("destination"), str)
            )
            or actor_job is None
            or (
                row.get("destination") if row["kind"] == "output-retire" else row["path"]
            ) not in {"/repo/" + path for path in image_outputs}
            and not image_role(row.get("destination") if row["kind"] == "output-retire" else row["path"])
            and not (
                row["kind"] not in {
                    "output-open", "output-mkdir", "output-rmdir", "output-replace",
                    "output-retire", "output-directory-change",
                } and row["serial"] in objects
            )
            and not (row["kind"] == "output-directory-change" and row["path"] in directories)
            and not readonly_existing
        ):
            raise ReadEpochError("native output effect escapes its actual job plan")
        job = actor_job
        pids = {job["pid"]} | {event["pid"] for event in job["tree"]} | {
            event["child"] for event in job["tree"] if "child" in event
        }
        if "pid" in row and (type(row["pid"]) is not int or row["pid"] not in pids):
            raise ReadEpochError("native output effect borrowed another dispatch process")
        if "identity" in row and (
            not isinstance(row["identity"], list) or len(row["identity"]) != 7
            or any(type(value) is not int for value in row["identity"])
        ):
            raise ReadEpochError("native output effect has a malformed inode identity")
        if "identity" in row:
            identity = row["identity"]
            if row["kind"] in {"output-mkdir", "output-rmdir", "output-directory-change"}:
                parent = parent_returns.get(row.get("pid"))
                directory = directories.get(row["path"])
                readonly_parent = (
                    row["kind"] == "output-directory-change" and parent is not None
                    and parent["readonly"] and directory is not None
                    and identity == row.get("before") == directory["identity"]
                    and row.get("entries") == directory["entries"]
                )
                if (
                    not resources or role(row["path"]) != "directory" and not readonly_parent
                    or not stat.S_ISDIR(identity[2]) or identity[2] & 0o7000
                    or identity[2] & 0o700 != 0o700 or any(value < 0 for value in identity)
                ):
                    raise ReadEpochError("native directory effect lacks its issued traversable identity")
            else:
                try:
                    validate_publication_identity(
                        identity[:6] + [1] if row["kind"] == "output-retire" and identity[6] == 0 else identity,
                        identity[2] & 0o777, identity[3],
                    )
                except ChannelError as error:
                    raise ReadEpochError(f"native output effect has an invalid object identity: {error}") from error
            if identity[3] > file_limit:
                raise ReadEpochError("native output effect exceeds its original file bound")
        if "sha256" in row and (
            not isinstance(row["sha256"], str) or re.fullmatch("[0-9a-f]{64}", row["sha256"]) is None
        ):
            raise ReadEpochError("native settled output lacks its exact content digest")
        if "operation_owner" in row and (
            type(row["operation_owner"]) is not int
            or row["operation_owner"] != dispatch
        ):
            raise ReadEpochError("native output open borrowed another producer")
        if any(
            type(row[key]) is not int or row[key] < 0 for key in ("fd", "description")
            if key in row
        ) or "description" in row and row["description"] == 0:
            raise ReadEpochError("native output effect has an invalid descriptor lineage")
        kind, serial = row["kind"], row["serial"]
        parents = parent_returns.get(row.get("pid"))
        if parents is not None and kind not in {"output-directory-change", "output-settled"} and not (
            kind == "output-replace" and parents["operation"] == "remove" and parents["source"] == row["path"]
        ):
            raise ReadEpochError("native namespace operation omitted its parent return")
        pending = writes.get(row.get("pid"))
        if pending is not None and kind not in {"output-write", "output-write-failed"}:
            raise ReadEpochError("native output process omitted its pending write return")
        if kind == "output-mkdir":
            if row["owner"] != dispatch:
                raise ReadEpochError("native directory owner differs from its creating dispatch")
            if serial <= issued_serial:
                raise ReadEpochError("native directory reused an issued custody serial")
            issued_serial = serial
            if row["path"] in directories or row["identity"][6] != 2 or row["revision"] != 0 or serial in objects or any(item["serial"] == serial for item in directories.values()):
                raise ReadEpochError("native mkdir reused an issued directory object")
            directories[row["path"]] = {
                "serial": serial, "owner": row["owner"], "identity": row["identity"], "entries": [],
            }
            parent_request(row["pid"], "mkdir", row["path"])
            continue
        if kind in {"output-rmdir", "output-directory-change"}:
            item = directories.get(row["path"])
            if item is None or item["serial"] != serial or item["owner"] != row["owner"] or row["revision"] != 0 or row["identity"][:3] != item["identity"][:3]:
                raise ReadEpochError("native directory transition lost its issued object")
            if kind == "output-rmdir":
                if item["entries"] or row["identity"][6] != 0:
                    raise ReadEpochError("native rmdir retired a populated directory")
                del directories[row["path"]]
                parent_request(row["pid"], "rmdir", row["path"])
            else:
                if parents is None or row["path"] not in parents["parents"] or any(
                    row[key] != parents[key] for key in ("operation", "source", "destination")
                ):
                    raise ReadEpochError("native directory change borrowed another namespace operation")
                if row["before"] != item["identity"] or row["operation"] not in {"open", "mkdir", "rmdir", "remove", "replace"}:
                    raise ReadEpochError("native directory change lost its namespace preimage")
                entries = set(item["entries"])
                for selected, removing in (
                    (row["source"], row["operation"] in {"rmdir", "remove", "replace"}),
                    (row["destination"], False),
                ):
                    if selected is None or selected.rpartition("/")[0] != row["path"]:
                        continue
                    name = selected.rpartition("/")[2]
                    if removing:
                        if name not in entries:
                            raise ReadEpochError("native directory change removed an unknown child")
                        entries.remove(name)
                    else:
                        entries.add(name)
                if row["entries"] != sorted(entries):
                    raise ReadEpochError("native directory change absorbed unrelated entries")
                item.update(identity=row["identity"], entries=row["entries"])
                parents["parents"].remove(row["path"])
                if not parents["parents"]:
                    del parent_returns[row["pid"]]
            continue
        item = objects.get(serial)
        if kind == "output-settled" and any(entry["serial"] == serial for entry in writes.values()):
            raise ReadEpochError("native output settlement omitted its pending write return")
        if kind in {"output-write", "output-write-failed"}:
            if (
                pending is None or type(row["request"]) is not int
                or any(row[key] != pending[key] for key in ("request", "owner", "serial", "path", "pid", "fd"))
                or bindings.get((row["pid"], row["fd"])) != (serial, pending["description"], True)
                or item is None or item["identity"] != pending["identity"]
                or item["revision"] != pending["revision"]
            ):
                raise ReadEpochError("native write return lost its stopped request or preimage")
            del writes[row["pid"]]
        if kind == "output-retire":
            if (
                not permitted(row["destination"], "remove")
                or
                item is None or item["owner"] != row["owner"] or item["path"] != row["destination"]
                or row["path"] is not None or row["revision"] != item["revision"]
                or row["identity"][:5] != item["identity"][:5] or row["identity"][6] != item["identity"][6] - 1
                or any(binding[0] == serial and binding[2] for binding in bindings.values())
                or not item["settled"]
            ):
                raise ReadEpochError("native retirement lost its settled prior version")
            item.update(identity=row["identity"], path=None)
            parent_request(row["pid"], "remove", row["destination"])
            continue
        if kind == "output-replace":
            if (
                not isinstance(row["source"], str)
                or not permitted(row["source"], "replace") or not permitted(row["path"], "replace")
                or
                item is None or item["owner"] != row["owner"] or row["source"] != item["path"]
                or row["revision"] != item["revision"] or row["identity"][:5] != item["identity"][:5]
                or row["identity"][6] != item["identity"][6] or not item["settled"]
                or serial in readers.values()
                or any(other is not item and other["path"] == row["path"] for other in objects.values())
                or any(binding[0] == serial and binding[2] for binding in bindings.values())
            ):
                raise ReadEpochError("native replacement lost its settled source or destination")
            item.update(identity=row["identity"], path=row["path"])
            parent_returns.pop(row["pid"], None)
            parent_request(row["pid"], "replace", row["source"], row["path"])
            continue
        if kind == "output-open":
            if (
                resources and not readonly_existing and not permitted(row["path"], "open")
                or
                type(row["writing"]) is not bool or (row["pid"], row["fd"]) in bindings
                or row["description"] in descriptions
                or row["writing"] and serial in readers.values()
            ):
                raise ReadEpochError("native output open reused a live descriptor")
            descriptions.add(row["description"])
            if item is None:
                if row["owner"] != dispatch:
                    raise ReadEpochError("native file owner differs from its creating dispatch")
                if row["revision"] != 0:
                    raise ReadEpochError("native file has a forged initial revision")
                if serial <= issued_serial:
                    raise ReadEpochError("native file reused an issued custody serial")
                issued_serial = serial
                if any(
                    other["path"] == row["path"] or other["identity"][:2] == row["identity"][:2]
                    for other in objects.values()
                ):
                    raise ReadEpochError("native output open issued an alias of an existing object")
                item = {"owner": row["owner"], "path": row["path"], "revision": row["revision"],
                        "identity": row["identity"], "settled": False}
                objects[serial] = item
            elif any(row[key] != item[key] for key in ("owner", "path", "revision", "identity")):
                raise ReadEpochError("native output open adopted a different produced object")
            if role(row["path"]) == "shared-lock" and row["writing"]:
                raise ReadEpochError("native shared lock acquired content writer authority")
            bindings[(row["pid"], row["fd"])] = serial, row["description"], row["writing"]
            if resources:
                parent_request(row["pid"], "open", row["path"], readonly=readonly_existing)
        elif item is None or row["owner"] != item["owner"] or row["path"] != item["path"]:
            raise ReadEpochError("native output effect lacks its issued live object")
        elif kind == "output-mode":
            binding = bindings.get((row["pid"], row["fd"]))
            if (
                binding is None or binding[0] != serial or serial in readers.values()
                or not binding[2]
                or type(row["mode"]) is not int or not 0 <= row["mode"] <= 0o777
                or row["identity"][:2] != item["identity"][:2] or row["identity"][3:5] != item["identity"][3:5]
                or row["identity"][6] != item["identity"][6] or row["identity"][2] != stat.S_IFREG | row["mode"]
                or role(row["path"]) == "shared-lock" or row["revision"] != item["revision"]
            ):
                raise ReadEpochError("native mode transition changed unrelated content or binding")
            item.update(identity=row["identity"], settled=False)
        elif kind == "output-write-entry":
            if (
                type(row["request"]) is not int or row["request"] != request_number + 1
                or any(type(row[key]) is not int or not 0 <= row[key] <= file_limit for key in ("requested_count", "offset"))
                or row["offset"] + row["requested_count"] > file_limit
                or bindings.get((row["pid"], row["fd"])) != (serial, row["description"], True)
                or row["identity"] != item["identity"] or row["revision"] != item["revision"]
                or serial in readers.values() or any(entry["serial"] == serial for entry in writes.values())
            ):
                raise ReadEpochError("native write entry lost its bounded live descriptor and preimage")
            request_number += 1
            writes[row["pid"]] = row
        elif kind == "output-truncate":
            following = effects[number] if number < len(effects) else None
            if (
                row["identity"][:3] != item["identity"][:3]
                or any(binding[0] == serial and binding[2] for binding in bindings.values())
                or serial in readers.values()
                or row["identity"][3] != 0 or row["identity"][6] != item["identity"][6]
                or row["revision"] != item["revision"] + 1
                or not isinstance(following, dict) or following.get("kind") != "output-open"
                or any(following.get(key) != row[key] for key in (
                    "owner", "serial", "revision", "path", "pid", "fd", "identity",
                ))
                or following.get("writing") is not True
            ):
                raise ReadEpochError("native truncating open lost its paired object transition")
            item.update(identity=row["identity"], revision=row["revision"], settled=False)
        elif kind == "output-write":
            binding = bindings.get((row["pid"], row["fd"]))
            if (
                binding is None or binding[0] != serial or not binding[2]
                or serial in readers.values()
                or type(row["result"]) is not int
                or not 0 <= row["result"] <= pending["requested_count"]
                or row["identity"][3] != (
                    max(pending["identity"][3], pending["offset"] + row["result"])
                    if row["result"] > 0 else pending["identity"][3]
                )
                or row["identity"][:3] != item["identity"][:3]
                or row["identity"][6] != item["identity"][6]
                or row["revision"] != item["revision"] + (row["identity"] != item["identity"])
                or row["result"] == 0 and row["identity"] != item["identity"]
            ):
                raise ReadEpochError("native output write changed its binding or unrelated identity")
            item.update(identity=row["identity"], revision=row["revision"], settled=False)
        elif row["revision"] != item["revision"]:
            raise ReadEpochError("native output effect borrowed a stale content revision")
        elif kind == "output-settled":
            if row["identity"] != item["identity"]:
                raise ReadEpochError("native output settlement changed its observed preimage")
            writers = [
                (key, binding) for key, binding in bindings.items()
                if binding[0] == serial and binding[2]
            ]
            if writers:
                following = effects[number] if number < len(effects) else None
                if (
                    len(writers) != 1 or not isinstance(following, dict)
                    or following.get("kind") not in {"output-close", "output-exec-close", "output-duplicate-release", "output-close-failed"}
                    or (following.get("pid"), following.get("fd")) != writers[0][0]
                    or following.get("serial") != serial
                    or (following.get("kind") != "output-close-failed" or resources)
                    and following.get("description") != writers[0][1][1]
                ):
                    raise ReadEpochError("native output settlement precedes its final writable retirement")
            item["settled"] = True
            item["sha256"] = row["sha256"]
        elif kind == "output-inherit":
            if type(row["parent"]) is not int or row["parent"] not in pids:
                raise ReadEpochError("native inherited output has a foreign or malformed parent")
            binding = bindings.get((row["parent"], row["fd"]))
            if binding is None or binding[:2] != (serial, row["description"]):
                raise ReadEpochError("native inherited output lost its original description")
            target = (row["pid"], row["fd"])
            if target in bindings:
                raise ReadEpochError("native inherited output reused a live descriptor")
            bindings[target] = binding
        else:
            binding = bindings.get((row["pid"], row["fd"]))
            if binding is None or binding[0] != serial:
                raise ReadEpochError("native output return lost its actual descriptor")
            if kind == "output-dup":
                target = (row["pid"], row["result"])
                if type(row["result"]) is not int or row["result"] < 0 or binding[1] != row["description"]:
                    raise ReadEpochError("native duplicate output changed its description")
                if target != (row["pid"], row["fd"]) and target in bindings:
                    raise ReadEpochError("native duplicate output reused an unretired descriptor")
                bindings[target] = binding
            elif kind in {"output-close", "output-range-close", "output-exec-close", "output-duplicate-release"}:
                if binding[1] != row["description"]:
                    raise ReadEpochError("native output retirement changed its description")
                if kind == "output-range-close" and (
                    role(row["path"]) != "shared-lock"
                    or not any(
                        process != row["pid"] and value[1] == binding[1]
                        for (process, _), value in bindings.items()
                    )
                ):
                    raise ReadEpochError("native range retirement lacks its surviving inherited lock binding")
                if (
                    lock_modes.get(row["description"], 0)
                    and sum(value[1] == row["description"] for value in bindings.values()) == 1
                ):
                    raise ReadEpochError("native last close omitted its lock release")
                released_locks.pop(row["description"], None)
                del bindings[(row["pid"], row["fd"])]
                if not any(value[1] == row["description"] for value in bindings.values()):
                    lock_modes.pop(row["description"], None)
            elif kind == "output-write-failed":
                if not binding[2] or type(row["result"]) is not int or not -4095 <= row["result"] < 0:
                    raise ReadEpochError("native failed write lacks its writable binding and errno")
            elif kind == "output-lock":
                import fcntl
                if (
                    binding[1] != row["description"] or role(row["path"]) != "shared-lock"
                    or type(row["flags"]) is not int
                    or row["flags"] not in {fcntl.LOCK_SH, fcntl.LOCK_EX, fcntl.LOCK_UN, fcntl.LOCK_SH | fcntl.LOCK_NB, fcntl.LOCK_EX | fcntl.LOCK_NB}
                    or type(row["result"]) is not int or row["result"] not in {0, -errno.EAGAIN, -errno.EINTR}
                    or type(row["mode"]) is not int or row["mode"] not in {0, fcntl.LOCK_SH, fcntl.LOCK_EX}
                    or row["result"] == 0 and row["mode"] != (0 if row["flags"] == fcntl.LOCK_UN else row["flags"] & ~fcntl.LOCK_NB)
                ):
                    raise ReadEpochError("native shared lock lost its actual description or return")
                requested = row["flags"] & ~fcntl.LOCK_NB
                previous = lock_modes.get(row["description"], 0)
                if row["result"] < 0 and (
                    requested == fcntl.LOCK_UN
                    or row["result"] == -errno.EAGAIN and not row["flags"] & fcntl.LOCK_NB
                    or row["mode"] != (previous if previous == requested else 0)
                ):
                    raise ReadEpochError("native failed flock changed its actual description preimage")
                lock_modes[row["description"]] = row["mode"]
            elif kind == "output-lock-release":
                following = effects[number] if number < len(effects) else None
                if (
                    binding[1] != row["description"]
                    or type(row["mode"]) is not int or row["mode"] not in {1, 2}
                    or row["mode"] != lock_modes.get(row["description"], 0)
                    or sum(value[1] == row["description"] for value in bindings.values()) != 1
                    or row["description"] in released_locks
                    or not isinstance(following, dict)
                    or following.get("kind") not in {"output-close", "output-exec-close", "output-duplicate-release", "output-close-failed"}
                    or any(following.get(key) != row[key] for key in ("pid", "fd", "serial", "description"))
                ):
                    raise ReadEpochError("native lock release lost its last actual description binding")
                lock_modes[row["description"]] = 0
                released_locks[row["description"]] = (row["pid"], row["fd"])
            elif kind == "output-close-failed":
                if type(row["result"]) is not int or row["result"] not in {
                    -errno.EINTR, -errno.EIO, -errno.ENOSPC, -errno.EDQUOT,
                }:
                    raise ReadEpochError("native failed close lacks its supported released-FD errno")
                description = row["description"] if resources else binding[1]
                if binding[1] != description or (
                    lock_modes.get(description, 0)
                    and sum(value[1] == description for value in bindings.values()) == 1
                ):
                    raise ReadEpochError("native failed last close omitted its lock release")
                released_locks.pop(description, None)
                del bindings[(row["pid"], row["fd"])]
                if not any(value[1] == description for value in bindings.values()):
                    lock_modes.pop(description, None)
    if bindings or readers or writes or parent_returns or released_locks or any(lock_modes.values()) or any(not item["settled"] for item in objects.values()):
        raise ReadEpochError("native output archive omitted descriptor retirement or content settlement")
    try:
        validate_terminal_resources(
            authority["paths"], resources, (item["path"] for item in objects.values() if item["path"] is not None),
        )
    except MakeProbeError as error:
        raise ReadEpochError(str(error)) from error


def validate_runtime_trace(value, scope, *, count_limit, file_limit, reserve):
    if (
        set(value) != {"version", "scope", "events", "sources", "complete", "selection", "machine"} | (
            {"output_authority"} if value["version"] in WRITABLE_VERSIONS else set()
        )
        or value["scope"] != scope or value["complete"] is not True
        or not isinstance(value["events"], list) or not 1 <= len(value["events"]) <= count_limit
        or not isinstance(value["sources"], list) or len(value["sources"]) > count_limit
    ):
        raise ReadEpochError("incomplete runtime source/effect trace")
    _machine_events(value["machine"], count_limit=count_limit)
    selection = validate_completion_selection(
        value["selection"], count_limit=count_limit, file_limit=file_limit,
    )
    if selection["names"]:
        raise ReadEpochError("runtime effects must not borrow inferred supplier selection")
    inventory = {row["path"]: row for row in selection["inventory"]}
    snapshots, indexes = {}, {}
    for row in value["sources"]:
        if not isinstance(row, dict) or set(row) != {"id", "mode", "bytes", "sha256", "data"}:
            raise ReadEpochError("malformed runtime source snapshot")
        if (
            type(row["id"]) is not int or row["id"] != len(snapshots) + 1
            or type(row["mode"]) is not int or not 0 <= row["mode"] <= 0o777
            or type(row["bytes"]) is not int or not 0 <= row["bytes"] <= file_limit
            or not isinstance(row["data"], str) or len(row["data"]) != 4 * ((row["bytes"] + 2) // 3)
        ):
            raise ReadEpochError("unbounded runtime source snapshot")
        reserve(row["bytes"])
        try:
            data = base64.b64decode(row["data"], validate=True)
        except ValueError as error:
            raise ReadEpochError("invalid runtime source encoding") from error
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"] or base64.b64encode(data).decode() != row["data"]:
            raise ReadEpochError("runtime source differs from captured pristine bytes")
        snapshots[row["id"]] = (row, data)

    stack, opens, opened_paths, evaluations, effects, expansions = [], {}, {}, {}, {}, {}
    templates, patterns = {}, {}
    pattern_protocol = value["version"] in PATTERN_VERSIONS
    source_kinds, basic = {}, []
    common = {"seq", "kind", "exec", "pass"}
    fields = {
        "effect-entry": {"effect", "parent", "visit", "caller", "location", "name", "value", "flavor", "origin", "target", "declaration"},
        "effect-return": {"effect"},
        "effect-completion": {"effect", "cwd", "variable"},
        "eval-entry": {"evaluation", "parent", "location", "source"},
        "eval-exit": {"evaluation", "source"},
        "expansion-entry": {"expansion", "family", "target", "text", "cwd"},
        "expansion-exit": {"expansion"},
    }
    if pattern_protocol:
        fields.update({
            "pattern-template-entry": {"template", "owner"},
            "pattern-template-completion": {"template", "owner", "source", "definition"},
            "pattern-entry": {"pattern", "template", "target", "cwd"},
            "pattern-definition": {"pattern", "name", "value", "flavor", "origin"},
            "pattern-definition-return": {"pattern", "variable"},
            "pattern-completion": {"pattern", "cwd", "variable"},
        })
    basic_fields = _read_event_keys(value["version"])

    def location(row):
        if pattern_protocol and isinstance(row, dict) and set(row) == {"pattern"}:
            if (
                type(row["pattern"]) is not int or not stack or stack[0] != ("pattern", row["pattern"])
                or any(frame[0] in {"source", "eval"} for frame in stack)
            ):
                raise ReadEpochError("runtime eval borrowed a foreign pattern root")
            return
        if isinstance(row, dict) and set(row) == {"expansion"}:
            if (
                type(row["expansion"]) is not int
                or not stack or stack[0] != ("expansion", row["expansion"])
                or any(frame[0] in {"source", "eval"} for frame in stack)
            ):
                raise ReadEpochError("runtime eval borrowed a foreign expansion root")
            return
        if (
            not isinstance(row, dict) or set(row) != {"visit", "source", "evaluation", "span", "offsets"}
            or row["visit"] is not None and (type(row["visit"]) is not int or row["visit"] not in opens)
            or type(row["source"]) is not int or row["source"] not in snapshots
            or not isinstance(row["span"], list) or len(row["span"]) != 3
            or any(type(number) is not int or number < 1 for number in row["span"])
        ):
            raise ReadEpochError("runtime location lacks its actual source occurrence")
        sources = [frame for frame in stack if frame[0] == "source"]
        if (
            sources and sources[-1][1] != row["visit"]
            or not sources and (row["visit"] is not None or not stack or stack[0][0] not in (
                {"expansion", "pattern"} if pattern_protocol else {"expansion"}
            ))
        ):
            raise ReadEpochError("runtime location belongs to another active reader")
        data = snapshots[row["source"]][1]
        if row["source"] not in indexes:
            indexes[row["source"]] = _statement_index(
                data, count_limit=count_limit, reserve=reserve, compact=pattern_protocol,
            )
        span = indexes[row["source"]].get(row["span"][1])
        if span is None or list(span[:3]) != row["span"]:
            raise ReadEpochError("runtime location differs from pristine physical source")
        if row["evaluation"] is None:
            if row["visit"] is None or row["source"] != opens[row["visit"]] or row["offsets"] is not None:
                raise ReadEpochError("runtime file location substituted an evaluated source")
        else:
            number = row["evaluation"]
            offsets = row["offsets"]
            if (
                type(number) is not int or ("eval", number) not in stack
                or evaluations[number]["source"] != row["source"]
                or not isinstance(offsets, list) or len(offsets) != 2
                or any(type(offset) is not int for offset in offsets)
                or not 0 <= offsets[0] < offsets[1] <= len(data) + 1
                or offsets[0] and data[offsets[0] - 1:offsets[0]] != b"\n"
                or data[:offsets[0]].count(b"\n") + 1 != row["span"][1]
                or data[:min(offsets[1], len(data))].count(b"\n") + (0 if data[:min(offsets[1], len(data))].endswith(b"\n") else 1) != row["span"][2]
            ):
                raise ReadEpochError("runtime eval location lacks its exact consumed interval")

    context = None
    for sequence, event in enumerate(value["events"], 1):
        if not isinstance(event, dict) or event.get("seq") != sequence or type(event["seq"]) is not int or not isinstance(event.get("kind"), str):
            raise ReadEpochError("unordered runtime event")
        kind = event["kind"]
        if kind in fields:
            if (
                set(event) != common | fields[kind]
                or any(type(event[key]) is not int for key in ("exec", "pass"))
                or context != (event["exec"], event["pass"]) or not stack and kind not in {"expansion-entry", "pattern-entry"}
            ):
                raise ReadEpochError("runtime event has a foreign pass or wire shape")
            if kind.startswith("pattern-template-"):
                number = event["template"]
                parsers = [frame for frame in stack if frame[0] in {"source", "eval"}]
                if (
                    type(number) is not int or not parsers or event["owner"] != list(parsers[-1])
                ):
                    raise ReadEpochError("pattern template borrowed a foreign parser occurrence")
                if kind == "pattern-template-entry":
                    if number != len(templates) + 1:
                        raise ReadEpochError("pattern template reused an issued ID")
                    templates[number] = {"entry": event, "completion": None}
                else:
                    retained = templates.get(number)
                    owner = tuple(event["owner"])
                    source = opens.get(owner[1]) if owner[0] == "source" else evaluations[owner[1]]["source"]
                    definition = event["definition"]
                    if (
                        retained is None or retained["completion"] is not None
                        or retained["entry"]["owner"] != event["owner"]
                        or retained["entry"]["exec"] != event["exec"]
                        or type(event["source"]) is not int or event["source"] != source
                        or source not in snapshots
                        or not isinstance(definition, dict) or set(definition) != set(PatternDefinition._fields)
                    ):
                        raise ReadEpochError("pattern completion substituted source/version/owner fields")
                    for key, maximum in (("pattern", 4096), ("name", 128), ("value", file_limit), ("file", 4096)):
                        text = definition[key]
                        if (
                            not isinstance(text, str) or "\0" in text
                            or any(0xD800 <= ord(char) <= 0xDFFF for char in text)
                            or len(text.encode("utf-8")) > maximum
                            or key != "value" and not text
                        ):
                            raise ReadEpochError("pattern template has unbounded completed fields")
                    if (
                        any(type(definition[key]) is not int for key in ("length", "percent", "line", "offset", "flags", "name_length"))
                        or definition["length"] != len(definition["pattern"].encode("utf-8"))
                        or "%" not in definition["pattern"] or definition["line"] < 1 or definition["offset"] < 0
                        or not 0 <= definition["percent"] < definition["length"]
                        or definition["pattern"].encode("utf-8")[definition["percent"]:definition["percent"] + 1] != b"%"
                        or definition["name_length"] != len(definition["name"].encode("utf-8"))
                        or not 0 <= definition["flags"] < 1 << 31
                        or not 1 <= (definition["flags"] >> 23) & 7 <= 6
                        or (definition["flags"] >> 26) & 7 > 6
                    ):
                        raise ReadEpochError("pattern template has invalid captured layout values")
                    pattern_source_coordinates(
                        definition, owner, source, value["events"],
                        snapshots[source][1], reserve=reserve, event_limit=sequence,
                    )
                    retained["completion"] = event
            elif kind == "pattern-entry":
                number, template = event["pattern"], event["template"]
                retained = templates.get(template) if type(template) is int else None
                if (
                    stack or type(number) is not int or number != len(patterns) + 1
                    or retained is None or retained["completion"] is None
                    or retained["completion"]["exec"] != event["exec"]
                    or not isinstance(event["target"], str) or not event["target"]
                    or any(0xD800 <= ord(char) <= 0xDFFF for char in event["target"])
                    or "\0" in event["target"] or len(event["target"].encode("utf-8")) > 4096
                    or not isinstance(event["cwd"], str) or not event["cwd"].startswith("/")
                    or any(0xD800 <= ord(char) <= 0xDFFF for char in event["cwd"])
                    or "\0" in event["cwd"] or len(event["cwd"].encode("utf-8")) > 4096
                ):
                    raise ReadEpochError("pattern materialization lacks a completed current-exec template/target")
                definition = retained["completion"]["definition"]
                pattern, target = definition["pattern"].encode("utf-8"), event["target"].encode("utf-8")
                percent = definition["percent"]
                if (
                    len(target) < len(pattern) - 1 or not target.startswith(pattern[:percent])
                    or not target.endswith(pattern[percent + 1:])
                ):
                    raise ReadEpochError("pattern materialization target differs from its actual stem selection")
                patterns[number] = {"entry": event, "definition": None, "return": None, "completion": None}
                stack.append(("pattern", number))
                basic.append({"seq": len(basic) + 1, "kind": "pattern-entry", "exec": event["exec"], "pass": event["pass"], "pattern": number})
            elif kind in {"pattern-definition", "pattern-definition-return", "pattern-completion"}:
                number = event["pattern"]
                expected = "pattern-effect" if kind == "pattern-definition-return" else "pattern"
                if type(number) is not int or not stack or stack[-1] != (expected, number) or number not in patterns:
                    raise ReadEpochError("pattern effect retired across an active occurrence")
                materialized = patterns[number]
                definition = templates[materialized["entry"]["template"]]["completion"]["definition"]
                flavor, origin = (definition["flags"] >> 23) & 7, (definition["flags"] >> 26) & 7
                if kind == "pattern-definition":
                    if (
                        flavor == 1 or materialized["definition"] is not None
                        or (event["name"], event["value"], event["flavor"], event["origin"])
                        != (definition["name"], definition["value"], flavor, origin)
                        or type(event["flavor"]) is not int or type(event["origin"]) is not int
                    ):
                        raise ReadEpochError("pattern definition substituted its captured inputs/flavor")
                    materialized["definition"] = event
                    stack.append(("pattern-effect", number))
                elif kind == "pattern-definition-return":
                    variable = variable_row(event["variable"])
                    simple_pattern_binding(definition, variable)
                    if (
                        materialized["definition"] is None or materialized["return"] is not None
                        or flavor == 1 or variable[0] != definition["name"]
                    ):
                        raise ReadEpochError("pattern definition return lost its effective binding")
                    materialized["return"] = event
                    stack.pop()
                else:
                    variable = variable_row(event["variable"])
                    simple_pattern_binding(definition, variable)
                    if (
                        materialized["completion"] is not None
                        or (materialized["definition"] is not None) != (flavor != 1)
                        or (materialized["return"] is not None) != (flavor != 1)
                        or variable[0] != definition["name"]
                        or variable[2] & 0x60000088 != definition["flags"] & 0x60000088
                        or event["cwd"] != materialized["entry"]["cwd"]
                        or not isinstance(event["cwd"], str) or not event["cwd"].startswith("/")
                        or any(0xD800 <= ord(char) <= 0xDFFF for char in event["cwd"])
                        or "\0" in event["cwd"] or len(event["cwd"].encode("utf-8")) > 4096
                    ):
                        raise ReadEpochError("pattern completion lost its branch/return/effective modifiers")
                    materialized["completion"] = event
                    stack.pop()
                    basic.append({"seq": len(basic) + 1, "kind": "pattern-exit", "exec": event["exec"], "pass": event["pass"], "pattern": number})
            elif kind == "expansion-entry":
                number = event["expansion"]
                if (
                    stack or type(number) is not int or number != len(expansions) + 1
                    or not isinstance(event["family"], str) or event["family"] not in {"recipe", "secondary"}
                    or not isinstance(event["target"], str) or not event["target"]
                    or any(0xD800 <= ord(char) <= 0xDFFF for char in event["target"])
                    or len(event["target"].encode()) > 4096 or "\0" in event["target"]
                    or not isinstance(event["text"], str)
                    or any(0xD800 <= ord(char) <= 0xDFFF for char in event["text"])
                    or len(event["text"].encode()) > file_limit or "\0" in event["text"]
                    or not isinstance(event["cwd"], str) or not event["cwd"].startswith("/")
                    or any(0xD800 <= ord(char) <= 0xDFFF for char in event["cwd"])
                    or len(event["cwd"].encode()) > 4096 or "\0" in event["cwd"]
                ):
                    raise ReadEpochError("runtime expansion has a foreign target/input/root")
                expansions[number] = event
                stack.append(("expansion", number))
                basic.append({
                    "seq": len(basic) + 1, "kind": kind, "exec": event["exec"],
                    "pass": event["pass"], "expansion": number,
                })
            elif kind == "expansion-exit":
                number = event["expansion"]
                if type(number) is not int or stack != [("expansion", number)]:
                    raise ReadEpochError("runtime expansion retired across an active occurrence")
                stack.pop()
                basic.append({
                    "seq": len(basic) + 1, "kind": kind, "exec": event["exec"],
                    "pass": event["pass"], "expansion": number,
                })
            elif kind == "effect-entry":
                number = event["effect"]
                if (
                    type(number) is not int or number != len(effects) + 1
                    or event["parent"] != list(stack[-1])
                    or not isinstance(event["caller"], str)
                    or event["caller"] not in {"assignment", "define", "target", "reader"}
                    or not isinstance(event["name"], str) or not 1 <= len(event["name"].encode()) <= 128 or "\0" in event["name"]
                    or not isinstance(event["value"], str) or len(event["value"].encode()) > 65536 or "\0" in event["value"]
                    or type(event["flavor"]) is not int or not 1 <= event["flavor"] <= 6
                    or type(event["origin"]) is not int or not 0 <= event["origin"] <= 6
                    or type(event["target"]) is not bool
                    or event["target"] != (event["caller"] == "target")
                ):
                    raise ReadEpochError("runtime effect has invalid actual parameters/parent")
                declaration = event["declaration"]
                if (
                    not isinstance(declaration, list) or len(declaration) != 3
                    or not isinstance(declaration[0], str) or not declaration[0]
                    or len(declaration[0].encode()) > 4096 or "\0" in declaration[0]
                    or any(type(number) is not int or number < 0 for number in declaration[1:])
                    or declaration[1] < 1
                ):
                    raise ReadEpochError("runtime effect has invalid original declaration floc")
                if event["caller"] == "reader":
                    if event["location"] is not None or event["name"] != "MAKEFILE_LIST" or (event["flavor"], event["origin"]) != (6, 2) or stack[-1] != ("source", event["visit"]):
                        raise ReadEpochError("runtime internal reader effect borrowed authored authority")
                else:
                    location(event["location"])
                    if event["visit"] != event["location"]["visit"]:
                        raise ReadEpochError("runtime effect substituted its reader occurrence")
                effects[number] = {"entry": event, "returned": False, "complete": False}
                stack.append(("effect", number))
            elif kind in {"effect-return", "effect-completion"}:
                number = event["effect"]
                if type(number) is not int or stack[-1] != ("effect", number) or number not in effects:
                    raise ReadEpochError("runtime effect retired out of order")
                effect = effects[number]
                if kind == "effect-return":
                    if effect["returned"]:
                        raise ReadEpochError("runtime effect returned twice")
                    effect["returned"] = True
                else:
                    variable = variable_row(event["variable"])
                    if (
                        not effect["returned"] or effect["complete"] or variable[0] != effect["entry"]["name"]
                        or not isinstance(event["cwd"], str) or not event["cwd"].startswith("/")
                        or len(event["cwd"].encode()) > 4096 or "\0" in event["cwd"]
                    ):
                        raise ReadEpochError("runtime effect lacks its effective returned binding")
                    effect["complete"] = True
                    stack.pop()
            elif kind == "eval-entry":
                number, source = event["evaluation"], event["source"]
                if type(number) is not int or number != len(evaluations) + 1 or event["parent"] != list(stack[-1]) or type(source) is not int or source not in snapshots or source in source_kinds:
                    raise ReadEpochError("runtime eval reused a foreign occurrence/source")
                location(event["location"])
                if snapshots[source][0]["mode"] != 0o600:
                    raise ReadEpochError("runtime eval has a file-backed mode")
                evaluations[number] = event
                source_kinds[source] = "eval"
                stack.append(("eval", number))
            else:
                number = event["evaluation"]
                if type(number) is not int or stack[-1] != ("eval", number) or evaluations[number]["source"] != event["source"]:
                    raise ReadEpochError("runtime eval retired across an active occurrence")
                if any(
                    row["entry"]["owner"] == ["eval", number] and row["completion"] is None
                    for row in templates.values()
                ):
                    raise ReadEpochError("runtime eval retired an incomplete pattern template")
                stack.pop()
            continue
        if (
            kind not in basic_fields or set(event) != basic_fields[kind] | {"seq", "kind"}
            or any(
                type(event[key]) is not int
                for key in ("exec", "pass", "visit", "result") if key in event
            )
            or "source" in event and event["source"] is not None and type(event["source"]) is not int
        ):
            raise ReadEpochError("malformed runtime read event")
        row = dict(event)
        if kind == "pass-entry":
            if stack:
                raise ReadEpochError("runtime pass entered across an active invocation")
            context = event["exec"], event["pass"]
            stack.append(("pass", event["pass"]))
        elif kind == "source-entry":
            parent_location = row.pop("location", None)
            if any(frame[0] in {"source", "expansion", "pattern"} for frame in stack):
                location(parent_location)
            elif parent_location is not None:
                raise ReadEpochError("runtime root reader borrowed an include location")
            opens[event["visit"]] = None
            opened_paths[event["visit"]] = None
            stack.append(("source", event["visit"]))
        elif kind == "source-open":
            path, custody = row.pop("path"), row.pop("custody")
            if event["result"] >= 0:
                source = event["source"]
                if type(source) is not int or source not in snapshots or source_kinds.get(source) == "eval":
                    raise ReadEpochError("runtime file open substituted evaluated/publication bytes")
                captured = snapshots[source][0]
                if not isinstance(path, str):
                    raise ReadEpochError("runtime file has no exact inventory path")
                if custody == {"kind": "snapshot"}:
                    entry = inventory.get(path)
                    if entry is None or entry["mode"] != captured["mode"] or entry["size"] != captured["bytes"] or entry["sha256"] != captured["sha256"]:
                        raise ReadEpochError("runtime file differs from frozen immutable inventory")
                elif (
                    value["version"] in WRITABLE_VERSIONS and isinstance(custody, dict)
                    and set(custody) == {"kind", "entry"} and custody["kind"] == "native-output"
                    and type(custody["entry"]) is int
                    and 1 <= custody["entry"] <= len(value["machine"]["events"])
                ):
                    entry = value["machine"]["events"][custody["entry"] - 1]
                    if (
                        not isinstance(entry, dict) or entry.get("kind") != "generated-source-entry"
                        or any(key not in entry for key in (
                            "visit", "trace_seq", "path", "identity", "sha256",
                        ))
                        or type(entry["trace_seq"]) is not int
                        or not isinstance(entry["identity"], list) or len(entry["identity"]) != 7
                        or any(type(part) is not int for part in entry["identity"])
                        or entry["visit"] != event["visit"]
                        or entry["trace_seq"] >= event["seq"] or entry["path"] != path
                        or entry["identity"] != event["identity"]
                        or entry["sha256"] != captured["sha256"]
                        or entry["identity"][3] != captured["bytes"]
                        or entry["identity"][2] & 0o777 != captured["mode"]
                    ):
                        raise ReadEpochError("runtime generated file differs from its original pinned entry")
                else:
                    raise ReadEpochError("runtime file open substituted evaluated/publication bytes")
                opens[event["visit"]] = source
                opened_paths[event["visit"]] = path
                source_kinds[source] = "file"
            elif path is not None or custody is not None:
                if (
                    value["version"] not in WRITABLE_VERSIONS or event["source"] is not None
                    or event["identity"] is not None or not isinstance(path, str)
                    or not isinstance(custody, dict) or set(custody) != {"kind", "entry"}
                    or custody["kind"] != "native-output" or type(custody["entry"]) is not int
                    or not 1 <= custody["entry"] <= len(value["machine"]["events"])
                ):
                    raise ReadEpochError("failed runtime open claims source custody")
                entry = value["machine"]["events"][custody["entry"] - 1]
                if (
                    not isinstance(entry, dict) or entry.get("kind") != "generated-source-entry"
                    or any(key not in entry for key in ("visit", "trace_seq", "path"))
                    or type(entry["trace_seq"]) is not int
                    or entry["visit"] != event["visit"]
                    or entry["trace_seq"] >= event["seq"] or entry["path"] != path
                ):
                    raise ReadEpochError("failed runtime open borrowed another generated entry")
        elif kind in {"source-exit", "pass-exit"}:
            expected = ("source", event["visit"]) if kind == "source-exit" else ("pass", event["pass"])
            if not stack or stack[-1] != expected:
                raise ReadEpochError("runtime reader/pass retired across an active invocation")
            if any(
                row["entry"]["owner"] == list(expected) and row["completion"] is None
                for row in templates.values()
            ):
                raise ReadEpochError("runtime reader retired an incomplete pattern template")
            if kind == "source-exit" and event["source"] is not None and (
                not isinstance(event["resolved"], str)
                or _resolved_source_path(event["resolved"]) != "/repo/" + opened_paths[event["visit"]]
            ):
                raise ReadEpochError("runtime file return names a different actual source")
            stack.pop()
        elif kind == "complete":
            if stack or any(not effect["complete"] for effect in effects.values()):
                raise ReadEpochError("runtime terminal event omitted an active invocation/effect")
            if (
                any(type(event.get(key)) is not int for key in ("effects", "evaluations", "expansions"))
                or event["effects"] != len(effects) or event["evaluations"] != len(evaluations)
                or event["expansions"] != len(expansions)
            ):
                raise ReadEpochError("runtime terminal counters omit actual effect/eval occurrences")
            row.pop("effects")
            row.pop("evaluations")
            row.pop("expansions")
            if pattern_protocol:
                if (
                    any(type(event.get(key)) is not int for key in ("templates", "patterns", "pattern_returns"))
                    or event["templates"] != len(templates) or event["patterns"] != len(patterns)
                    or event["pattern_returns"] != sum(item["return"] is not None for item in patterns.values())
                    or any(item["completion"] is None for item in (*templates.values(), *patterns.values()))
                ):
                    raise ReadEpochError("runtime terminal counters omit actual pattern occurrences")
                for key in ("templates", "patterns", "pattern_returns"):
                    row.pop(key)
        row["seq"] = len(basic) + 1
        basic.append(row)
    if set(source_kinds) != set(snapshots):
        raise ReadEpochError("runtime trace has unused or unbound captured sources")
    mapping = {old: new for new, old in enumerate(sorted(number for number, kind in source_kinds.items() if kind == "file"), 1)}
    basic_sources, basic_data = {}, {}
    for old, new in mapping.items():
        row = dict(snapshots[old][0])
        row["id"] = new
        basic_sources[new] = row
        basic_data[new] = snapshots[old][1]
    for row in basic:
        if row["kind"] in {"source-open", "source-exit"} and row["source"] is not None:
            row["source"] = mapping[row["source"]]
    _validate_captured_read_events(
        {"version": 2, "scope": scope, "events": basic, "sources": list(basic_sources.values()), "complete": True},
        basic_sources, basic_data,
        scope, count_limit=count_limit, file_limit=file_limit, reserve=reserve,
        expansion_projection=True, pattern_projection=pattern_protocol,
    )
    reserve(len(encoded(value["machine"])))
    validate_machine_observations(value["machine"], value, count_limit=count_limit, reserve=reserve)
    if value["version"] in WRITABLE_VERSIONS:
        validate_native_output_authority(value, count_limit=count_limit, file_limit=file_limit, reserve=reserve)
    return value


def validate_trace(value, scope, *, count_limit, file_limit, reserve=lambda size: None):
    if isinstance(value, dict) and type(value.get("version")) is int and value["version"] in RUNTIME_VERSIONS:
        return validate_runtime_trace(value, scope, count_limit=count_limit, file_limit=file_limit, reserve=reserve)
    return _validate_read_trace(value, scope, count_limit=count_limit, file_limit=file_limit, reserve=reserve)


def _validate_read_trace(value, scope, *, count_limit, file_limit, reserve, expansion_projection=False):
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
    sources, source_data = {}, {}
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
        source_data[row["id"]] = data
    return _validate_captured_read_events(
        value, sources, source_data, scope, count_limit=count_limit, file_limit=file_limit,
        reserve=reserve, expansion_projection=expansion_projection,
    )


def _validate_captured_read_events(
    value, sources, source_data, scope, *, count_limit, file_limit, reserve,
    expansion_projection=False, pattern_projection=False,
):
    """Validate lifetimes using bytes already checked by the enclosing trace decoder."""
    source_indexes = {}
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

    def source_span(number, first, last):
        if number not in source_indexes:
            source_indexes[number] = _statement_index(
                source_data[number], count_limit=count_limit, reserve=reserve,
                compact=pattern_projection,
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
    keys = _read_event_keys(value["version"])
    expansion = None
    if expansion_projection:
        keys.update({
            "expansion-entry": {"exec", "pass", "expansion"},
            "expansion-exit": {"exec", "pass", "expansion"},
        })
    if pattern_projection:
        keys.update({
            "pattern-entry": {"exec", "pass", "pattern"},
            "pattern-exit": {"exec", "pass", "pattern"},
        })
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
            if in_pass or active or expansion is not None or type(event["exec"]) is not int or event["exec"] != execs + 1:
                raise ReadEpochError("original read exec lifetime is incomplete")
            execs += 1
            continue
        if kind == "complete":
            if in_pass or active or expansion is not None or not passes or value["version"] in {2, 3, 4} and barriers != passes or any(
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
        if kind in {"expansion-entry", "expansion-exit", "pattern-entry", "pattern-exit"}:
            key = "pattern" if kind.startswith("pattern-") else "expansion"
            if in_pass or active or not passes or event["pass"] != passes or type(event[key]) is not int or event[key] < 1:
                raise ReadEpochError("postread source interval crossed its original pass")
            if kind.endswith("-entry"):
                if expansion is not None:
                    raise ReadEpochError("postread source intervals overlap")
                expansion = (key, event[key])
            else:
                if expansion != (key, event[key]):
                    raise ReadEpochError("postread source interval has a foreign return")
                expansion = None
            continue
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
        if not in_pass and expansion is None or event["pass"] != passes:
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
            if in_pass:
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
                        rows, references, dependencies = completion_source_facts(
                            path, source_data[event["source"]], checkpoint=lambda: reserve(0),
                            count_limit=count_limit, charge=reserve,
                        )
                        require_completion_reference_closure(
                            references, selected_names, dependencies=dependencies,
                        )
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
        validate_machine_observations(value["machine"], value, count_limit=count_limit, reserve=reserve)
    return value
