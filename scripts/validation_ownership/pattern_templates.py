"""Captured Make ABI and occurrence-owned native pattern templates."""

from __future__ import annotations

import struct
import sys
import hashlib

if __package__:
    from .authority import encoded
    from . import read_epochs
else:
    from authority import encoded
    import read_epochs


def pattern_abi(image):
    start, extent = image.symbol("initialize_file_variables", 2)
    creation, creation_extent = image.symbol("create_pattern_var", 2)
    if not 0 < extent <= 65536 or not 0 < creation_extent <= 65536:
        raise read_epochs.ReadEpochError("pattern code has an unsupported extent")
    code = image.bytes(start, extent, executable=True)
    creator = image.bytes(creation, creation_extent, executable=True)
    for body in (code, creator):
        if not body.startswith(b"\xf3\x0f\x1e\xfa\x55\x48\x89\xe5"):
            raise read_epochs.ReadEpochError("pattern code lacks its captured frame ABI")

    def unique(sequence, body=code):
        positions = [offset for offset in range(len(body)) if body.startswith(sequence, offset)]
        if len(positions) != 1:
            raise read_epochs.ReadEpochError("pattern code predicate is absent or ambiguous")
        return positions[0]

    selection = unique(bytes.fromhex(
        "410fb644244f450fb744244e498b5424284d8b7c2420c0e80289c14489c0"
        "6625800383e107663d8000"
    ))
    modifiers = unique(bytes.fromhex(
        "410fb64c244c0fb6422c4c89e783e10883e0f709c80fb64a2f88422c"
        "410fb674244f83e07f83e19f83e66009f1884a2f410fb64c244c"
        "83e18009c888422c"
    ))
    completion = modifiers + 62
    if code[completion:completion + 3] != b"\x48\x8b\x33":
        raise read_epochs.ReadEpochError("pattern modifiers lost their shared completion")
    if code[selection + 41:selection + 43] != b"\x0f\x85":
        raise read_epochs.ReadEpochError("pattern flavor lost its actual branch")
    branch = start + selection + 47 + int.from_bytes(
        code[selection + 43:selection + 47], "little", signed=True,
    )
    calls = {}
    for name in ("do_variable_definition", "define_variable_in_set"):
        target = image.symbol(name, 2)[0]
        sites = [
            start + offset for offset in range(len(code) - 4)
            if read_epochs.direct_call(start + offset, code[offset:offset + 5]) == target
        ]
        if len(sites) != 1:
            raise read_epochs.ReadEpochError("pattern definition lacks its unique actual caller")
        calls[name] = sites[0]
    if not branch < calls["do_variable_definition"] < start + modifiers < start + selection:
        raise read_epochs.ReadEpochError("pattern non-simple branch lost modifier convergence")
    if not start + selection < calls["define_variable_in_set"] < start + extent:
        raise read_epochs.ReadEpochError("pattern simple branch lost its actual caller")
    simple_return = calls["define_variable_in_set"] + 5 - start
    if code[simple_return:simple_return + 24] != bytes.fromhex(
        "4889c20fb7402e66257ffc0c806689422e5859e9"
    ) + (start + modifiers - (start + simple_return + 24)).to_bytes(4, "little", signed=True):
        raise read_epochs.ReadEpochError("pattern simple result lost shared modifier convergence")

    head_load = read_epochs._unique(
        rb"\x48\x8b\x15(....)\x49\x83\xc4\x01\x48\x85\xd2",
        creator, "pattern list head",
    )
    head = creation + head_load.start() + 7 + int.from_bytes(
        head_load[1], "little", signed=True,
    )
    empty = read_epochs._unique(
        rb"\x48\x89\x05(....)\x48\xc7\x00\x00\x00\x00\x00",
        creator, "empty pattern list insertion",
    )
    if creation + empty.start() + 7 + int.from_bytes(empty[1], "little", signed=True) != head:
        raise read_epochs.ReadEpochError("pattern insertion names another list")
    walk = read_epochs._unique(
        rb"\x48\x8d\x0d(....)\xeb.\x0f\x1f\x80\x00\x00\x00\x00"
        rb"\x48\x89\xd1\x48\x8b\x12\x48\x85\xd2\x74.\x48\x3b\x5a\x18"
        rb"\x73.\x48\x89\x10\x48\x89\x01",
        creator, "length-sorted pattern insertion",
    )
    if creation + walk.start() + 7 + int.from_bytes(walk[1], "little", signed=True) != head:
        raise read_epochs.ReadEpochError("pattern sorted insertion names another list")
    unique(bytes.fromhex("be01000000bf50000000"), creator)
    layout = bytes.fromhex("4c896810488958184c896008")
    if creator.count(layout) != 2:
        raise read_epochs.ReadEpochError("pattern allocation lost its actual field layout")
    if not any(
        address <= head and head + 8 <= address + size and flags & 2 and not flags & 1
        for address, size, _, _, flags in image.loads
    ):
        raise read_epochs.ReadEpochError("pattern list head has no writable captured-image extent")
    return {
        "initializer": [start, start + extent],
        "creator": [creation, creation + creation_extent],
        "head": head, "selection": start + selection, "completion": start + completion,
        "calls": calls, "object_bytes": 80,
        "pattern_register": "r12", "file_register": "rbx",
        "completed_variable_register": "rdx", "variable_offset": 32,
        "pattern_target_offset": 16, "file_pattern_set_offset": 88,
    }


class PatternTemplates:
    """Address bindings retire on exec; IDs and accounting never restart."""

    def __init__(self, trace, abi):
        self.trace, self.abi = trace, abi
        self.objects = {}
        self.serial = 0
        self.trace.policy.charge_metadata(sys.getsizeof(self.__dict__) + sys.getsizeof(self.objects))

    def retire(self):
        if any(row["definition"] is None for row in self.objects.values()):
            raise read_epochs.ReadEpochError("Make retired an incomplete pattern template")
        self.objects.clear()

    def owner(self):
        for frame in reversed(self.trace.invocations):
            if frame["kind"] in {"source", "eval"}:
                return [frame["kind"], frame.get("visit") if frame["kind"] == "source" else frame["number"]]
        return None

    def decode(self, pointer, retained=None):
        trace = self.trace
        raw = trace.memory(pointer, self.abi["object_bytes"])
        following, suffix, target, length, name, value, file, line, offset, nlen, flags = struct.unpack(
            "<QQQQQQQQQII", raw,
        )
        pattern = trace.string(target, 4097)
        if not pattern or len(pattern.encode("utf-8")) != length or "%" not in pattern:
            raise read_epochs.ReadEpochError("pattern template has an invalid actual target/length")
        suffix_value = trace.string(suffix, 4097)
        pattern_bytes = pattern.encode("utf-8")
        suffix_offset = suffix - target
        if (
            not 0 < suffix_offset <= length or pattern_bytes[suffix_offset - 1:suffix_offset] != b"%"
            or suffix_value is None or suffix_value.encode("utf-8") != pattern_bytes[suffix_offset:]
        ):
            raise read_epochs.ReadEpochError("pattern template has a substituted actual suffix")
        keys = ("pattern", "length", "percent", "name", "value", "file",
                "line", "offset", "flags", "name_length")
        values = (
            pattern, length, suffix_offset - 1,
            trace.string(name, 129) if name else None,
            trace.string(value, trace.config["file_limit"] + 1) if value else None,
            trace.string(file, 4097) if file else None,
            line, offset, flags, nlen,
        )
        trace.policy.charge_metadata(sys.getsizeof(values))
        if retained is not None:
            if any(retained[key] != value for key, value in zip(keys, values)):
                raise read_epochs.ReadEpochError("retained pattern changed its source-bound full fields")
            return following, retained
        definition = dict(zip(keys, values))
        trace.policy.charge_metadata(len(encoded(definition)) + sys.getsizeof(definition))
        return following, definition

    def observe(self):
        trace = self.trace
        pointer = trace.number(trace.bias + self.abi["head"])
        seen, ordered = set(), []
        previous = 0
        while pointer:
            trace.deadline()
            if pointer in seen or len(seen) >= trace.config["observation_count"]:
                raise read_epochs.ReadEpochError("pattern topology is cyclic or oversized")
            trace.policy.charge_metadata(sys.getsizeof(pointer) + 2 * sys.getsizeof((pointer,)))
            seen.add(pointer)
            retained = self.objects.get(pointer)
            following, definition = self.decode(
                pointer, None if retained is None else retained["definition"],
            )
            if retained is None or retained["definition"] is None:
                if retained is None:
                    owner = self.owner()
                    if owner is None:
                        raise read_epochs.ReadEpochError("new pattern has no actual parser occurrence")
                    self.serial += 1
                    entry = trace.event(
                        "pattern-template-entry", **trace.context(),
                        template=self.serial, owner=owner,
                    )
                    if trace.version in read_epochs.PATTERN_VERSIONS:
                        trace.machine_event(
                            "pattern-template-input", trace.pid, template=self.serial,
                            sha256=hashlib.sha256(encoded(entry)).hexdigest(),
                        )
                    retained = {
                        "id": self.serial, "owner": owner,
                        "entry": entry["seq"], "definition": None,
                    }
                    trace.policy.charge_metadata(len(encoded(retained)) + sys.getsizeof(retained))
                    self.objects[pointer] = retained
                    trace.policy.charge_metadata(sys.getsizeof(self.objects))
            if definition["length"] < previous:
                raise read_epochs.ReadEpochError("pattern list lost length-sorted topology")
            previous = definition["length"]
            ordered.append((pointer, definition))
            pointer = following
        if set(self.objects) != seen:
            raise read_epochs.ReadEpochError("pattern list lost an occurrence-owned object")
        return ordered

    def validate_retirement(self):
        self.observe()
        if any(row["definition"] is None for row in self.objects.values()):
            raise read_epochs.ReadEpochError("Make retired an incomplete pattern template")

    def complete(self, owner, source):
        trace = self.trace
        for pointer, definition in self.observe():
            row = self.objects[pointer]
            if row["owner"] != owner or row["definition"] is not None:
                continue
            if (
                type(source) is not int or not 1 <= source <= len(trace.sources)
                or not definition["name"] or definition["value"] is None
                or definition["name_length"] != len(definition["name"].encode("utf-8"))
                or not definition["file"] or definition["line"] < 1
                or not 1 <= (definition["flags"] >> 23) & 7 <= 6
                or (definition["flags"] >> 26) & 7 > 6
            ):
                raise read_epochs.ReadEpochError("completed pattern lacks source-bound actual fields")
            event = trace.event(
                "pattern-template-completion", **trace.context(),
                template=row["id"], owner=owner, source=source, definition=definition,
            )
            if trace.version in read_epochs.PATTERN_VERSIONS:
                trace.machine_event(
                    "pattern-template-result", trace.pid, template=row["id"],
                    sha256=hashlib.sha256(encoded(event)).hexdigest(),
                )
            row["definition"] = definition

    def selected(self, pointer):
        self.observe()
        row = self.objects.get(pointer)
        if row is None or row["definition"] is None:
            raise read_epochs.ReadEpochError("selected pattern has no completed actual parser owner")
        return row


class PatternMaterializations(PatternTemplates):
    def __init__(self, trace, abi):
        super().__init__(trace, abi)
        self.materializations = self.returns = 0
        image = read_epochs.Elf(trace.image)
        first, last = abi["initializer"]
        self.code = image.bytes(first, last - first, executable=True)
        trace.policy.charge_metadata(len(self.code))

    def payload(self, kind, number, event):
        self.trace.machine_event(
            kind, self.trace.pid, pattern=number,
            sha256=hashlib.sha256(encoded(event)).hexdigest(),
        )

    def enter(self, registers, state):
        trace = self.trace
        if trace.invocations or trace.active or trace.pass_frame or trace.io:
            raise read_epochs.ReadEpochError("pattern selection crossed an active native occurrence")
        if trace.memory(trace.bias + self.abi["initializer"][0], len(self.code)) != self.code:
            raise read_epochs.ReadEpochError("live pattern code differs from the captured image")
        row = self.selected(registers.r12)
        target = trace.string(trace.number(registers.rbx), 4097)
        definition = row["definition"]
        pattern = definition["pattern"].encode("utf-8")
        target_bytes = target.encode("utf-8") if target is not None else b""
        percent = definition["percent"]
        if (
            not target or not trace.number(registers.rbx + self.abi["file_pattern_set_offset"])
            or len(target_bytes) < len(pattern) - 1
            or not target_bytes.startswith(pattern[:percent]) or not target_bytes.endswith(pattern[percent + 1:])
            or not isinstance(state.cwd, str) or not state.cwd.startswith("/")
        ):
            raise read_epochs.ReadEpochError("pattern selection lost its actual target/set/CWD")
        self.materializations += 1
        number = self.materializations
        event = trace.event(
            "pattern-entry", **trace.context(), pattern=number,
            template=row["id"], target=target, cwd=state.cwd,
        )
        self.payload("pattern-input", number, event)
        trace.invocations.append({
            "kind": "pattern", "purpose": "pattern-completion",
            "return": trace.bias + self.abi["completion"], "number": number,
            "object": registers.r12, "file": registers.rbx, "frame": registers.rbp,
            "template": row, "defined": False, "returned": False,
            "target": target, "cwd": state.cwd,
            "set": trace.number(registers.rbx + self.abi["file_pattern_set_offset"]),
        })

    def definition(self, registers):
        trace = self.trace
        if not trace.invocations or trace.invocations[-1]["kind"] != "pattern":
            return False
        parent = trace.invocations[-1]
        frame = trace.caller(registers, registers.rip)
        if frame["return"] != trace.bias + self.abi["calls"]["do_variable_definition"] + 5:
            return False
        row = self.selected(parent["object"])
        definition = row["definition"]
        flags = definition["flags"]
        name, value = trace.string(registers.rsi, 129), trace.string(registers.rdx, trace.config["file_limit"] + 1)
        if (
            parent["defined"] or registers.rbp != parent["frame"]
            or registers.rdi != parent["object"] + 48 or registers.r9 != 1
            or name != definition["name"] or value != definition["value"]
            or registers.r8 != (flags >> 23) & 7 or registers.rcx != (flags >> 26) & 7
            or registers.r8 == 1
        ):
            raise read_epochs.ReadEpochError("pattern definition substituted its actual declaration/parameters")
        parent["defined"] = True
        event = trace.event(
            "pattern-definition", **trace.context(), pattern=parent["number"],
            name=name, value=value, flavor=registers.r8, origin=registers.rcx,
        )
        self.payload("pattern-definition-input", parent["number"], event)
        trace.invocations.append({
            **frame, "kind": "pattern-effect", "purpose": "pattern-definition-return",
            "number": parent["number"],
        })
        return True

    def definition_return(self, registers):
        trace = self.trace
        frame = trace.invocations[-1]
        if (
            frame["kind"] != "pattern-effect" or registers.rsp != frame["stack"] + 8
            or not registers.rax or len(trace.invocations) < 2
            or trace.invocations[-2]["kind"] != "pattern"
        ):
            raise read_epochs.ReadEpochError("pattern definition returned across an active occurrence")
        parent = trace.invocations[-2]
        variable = read_epochs.original_variable(trace.memory, registers.rax, trace.string)
        if parent["returned"] or variable[0] != parent["template"]["definition"]["name"]:
            raise read_epochs.ReadEpochError("pattern definition returned a foreign effective binding")
        parent["returned"] = True
        parent["variable"] = registers.rax
        self.returns += 1
        event = trace.event(
            "pattern-definition-return", **trace.context(),
            pattern=parent["number"], variable=variable,
        )
        self.payload("pattern-definition-result", parent["number"], event)
        trace.invocations.pop()

    def completion(self, registers, state):
        trace = self.trace
        frame = trace.invocations[-1]
        if (
            frame["kind"] != "pattern"
            or (registers.r12, registers.rbx, registers.rbp) != (frame["object"], frame["file"], frame["frame"])
            or not registers.rdx or trace.active or trace.io
            or trace.string(trace.number(frame["file"]), 4097) != frame["target"]
            or trace.number(frame["file"] + self.abi["file_pattern_set_offset"]) != frame["set"]
        ):
            raise read_epochs.ReadEpochError("pattern completion substituted its actual object/target/frame")
        row = self.selected(frame["object"])
        definition = row["definition"]
        flavor = (definition["flags"] >> 23) & 7
        if (
            frame["defined"] != (flavor != 1) or frame["returned"] != (flavor != 1)
            or flavor != 1 and frame.get("variable") != registers.rdx
        ):
            raise read_epochs.ReadEpochError("pattern completion omitted its actual flavor branch/return")
        variable = read_epochs.original_variable(trace.memory, registers.rdx, trace.string)
        flags = int.from_bytes(trace.memory(registers.rdx + 44, 4), "little")
        if (
            variable[0] != definition["name"] or flags & 0x60000088 != definition["flags"] & 0x60000088
            or not isinstance(state.cwd, str) or not state.cwd.startswith("/")
            or state.cwd != frame["cwd"]
        ):
            raise read_epochs.ReadEpochError("pattern completion lost its effective modifiers/name/CWD")
        event = trace.event(
            "pattern-completion", **trace.context(),
            pattern=frame["number"], cwd=state.cwd, variable=variable,
        )
        self.payload("pattern-result", frame["number"], event)
        trace.invocations.pop()
