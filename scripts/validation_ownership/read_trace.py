"""Native read-entry observation on verified Make hardware-breakpoint sites."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import os
from pathlib import Path
import stat
import struct
import sys
import time
from types import MappingProxyType

if __package__:
    from .authority import encoded
    from .budget import MakeProbeError
    from .lifecycle import finish_cleanup
    from .native_resources import require_retained_source
    from . import read_epochs
    from . import source_phases
else:
    from authority import encoded
    from budget import MakeProbeError
    from lifecycle import finish_cleanup
    from native_resources import require_retained_source
    import read_epochs
    import source_phases


DEBUG_REGISTER_OFFSET = 848
GETSIGINFO = 0x4202
TRAP_HWBKPT = 4


class NativeReadTrace:
    def __init__(self, policy, config):
        self.policy, self.config = policy, policy.config
        self.native = sys.modules[type(policy).__module__]
        self.scope = config["scope"]
        self.version = config["version"]
        self.runtime = self.version in read_epochs.RUNTIME_VERSIONS
        self.pending_barrier = None
        self.barriers = 0
        path = Path(self.config["root"]) / "usr/bin/make"
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= self.config["file_limit"]:
                raise read_epochs.ReadEpochError("read trace runtime image has an invalid extent")
            policy.charge_metadata(before.st_size)
            self.image = stream.read(before.st_size + 1)
            after = os.fstat(stream.fileno())
            if before != after or len(self.image) != before.st_size:
                raise read_epochs.ReadEpochError("read trace runtime image changed during capture")
        self.image_identity = before.st_dev, before.st_ino
        self.abi = read_epochs.validate_abi(config["abi"], self.image)
        self.expansion_abi = (
            read_epochs.runtime_expansion_abi(read_epochs.Elf(self.image))
            if self.runtime else None
        )
        self.patterns = None
        if self.version in read_epochs.PATTERN_VERSIONS:
            if __package__:
                from .pattern_templates import PatternMaterializations, pattern_abi
            else:
                from pattern_templates import PatternMaterializations, pattern_abi
            derived = pattern_abi(read_epochs.Elf(self.image))
            if config["patterns"] != derived:
                raise read_epochs.ReadEpochError("pattern ABI differs from its captured image")
            self.patterns = PatternMaterializations(self, derived)
        self.selection = ()
        self.selection_names = frozenset()
        self.selection_inventory = MappingProxyType({})
        self.selection_index = MappingProxyType({})
        if self.version == 3:
            if self.abi["version"] != 2:
                raise read_epochs.ReadEpochError("completion trace lacks its machine-derived completion ABI")
            read_epochs.validate_selection(
                config["selection"], count_limit=self.config["observation_count"],
                file_limit=self.config["file_limit"],
            )
            self.policy.charge_metadata(len(encoded(config["selection"])))
            self.selection = tuple(tuple(row) for row in config["selection"])
            index = {}
            for row in self.selection:
                self.deadline()
                key = row[:5]
                self.policy.charge_metadata(sys.getsizeof({None: None}) + sys.getsizeof(key))
                index[key] = row
            self.selection_index = MappingProxyType(index)
        elif self.version in read_epochs.MACHINE_VERSIONS:
            if self.abi["version"] != 2:
                raise read_epochs.ReadEpochError("completion trace lacks its machine-derived completion ABI")
            read_epochs.validate_completion_selection(
                config["selection"], count_limit=self.config["observation_count"],
                file_limit=self.config["file_limit"],
            )
            self.policy.charge_metadata(len(encoded(config["selection"])))
            self.selection = config["selection"]
            self.selection_names = frozenset(self.selection["names"])
            self.selection_inventory = MappingProxyType({
                row["path"]: row for row in self.selection["inventory"]
            })
        source = read_epochs.Elf(self.image).bytes(self.abi["source"][0], 8, executable=True)
        if not source.startswith((b"\x55\x48\x89\xe5", b"\xf3\x0f\x1e\xfa\x55\x48\x89\xe5")):
            raise read_epochs.ReadEpochError("source reader lacks its verified frame-pointer ABI")
        self.events, self.sources = [], []
        self.root_boundaries = None
        self.pool = {}
        self.statement_indexes = {}
        self.execs = self.passes = self.visits = 0
        self.pid = self.bias = None
        self.slots = {}
        self.purposes = {}
        self.traps = 0
        self.active = []
        self.pass_frame = None
        self.goals = {}
        self.io = None
        self.machine = [] if self.version in read_epochs.MACHINE_VERSIONS else None
        self.invocations = []
        self.effects = self.evaluations = 0
        self.expansions = 0
        previous_size = sys.getsizeof(self.__dict__)
        self.register_work = (
            self.native.Registers(), self.native.Registers(), self.native.Registers(),
        )
        self.register_views = tuple(memoryview(value).cast("B") for value in self.register_work)
        self.siginfo_work = (ctypes.c_ubyte * 128)()
        self.policy.charge_metadata(
            sys.getsizeof(self.register_work) + sys.getsizeof(self.register_views)
            + sum(ctypes.sizeof(value) + sys.getsizeof(value) for value in self.register_work)
            + sum(sys.getsizeof(value) for value in self.register_views)
            + ctypes.sizeof(self.siginfo_work) + sys.getsizeof(self.siginfo_work),
        )
        current_size = sys.getsizeof(self.__dict__)
        if current_size > previous_size:
            self.policy.charge_metadata(current_size)

    def machine_event(self, kind, pid, **fields):
        rows = getattr(self, "machine", None)
        if rows is None:
            return
        self.policy.reserve_trace_observation()
        row = {
            "seq": len(rows) + 1, "kind": kind, "trace_seq": len(self.events), "pid": pid,
            "exec": self.execs, "pass": self.passes,
            **fields,
        }
        self.policy.charge_metadata(len(encoded(row)))
        rows.append(row)

    def event(self, kind, **fields):
        self.policy.reserve_trace_observation()
        row = {"seq": len(self.events) + 1, "kind": kind, **fields}
        self.policy.charge_metadata(len(encoded(row)))
        self.events.append(row)
        return row

    def context(self):
        return {"exec": self.execs, "pass": self.passes}

    def memory(self, address, count):
        self.deadline()
        self.policy.charge_metadata(count)
        return self.native.memory(self.pid, address, count)

    def number(self, address):
        return int.from_bytes(self.memory(address, 8), "little")

    def string(self, address, maximum):
        if not address:
            return None
        value = bytearray()
        while len(value) < maximum:
            self.deadline()
            count = min(8 - ((address + len(value)) & 7), maximum - len(value))
            data = self.memory(address + len(value), count)
            if b"\0" in data:
                value.extend(data.split(b"\0", 1)[0])
                return value.decode("utf-8", "strict")
            value.extend(data)
        raise read_epochs.ReadEpochError("original read ABI string exceeds its exact bound")

    def deadline(self):
        if time.monotonic() >= self.config["deadline"]:
            raise read_epochs.ReadEpochError("original read trace exhausted the existing deadline")

    def debug(self, pid, index, value=None):
        self.deadline()
        if value is None:
            self.policy.charge_metadata(8)
            return self.native.ptrace(3, pid, DEBUG_REGISTER_OFFSET + 8 * index) & ((1 << 64) - 1)
        self.native.ptrace(6, pid, DEBUG_REGISTER_OFFSET + 8 * index, value)
        observed = self.debug(pid, index)
        mask = 0xE00F if index == 6 else (1 << 64) - 1
        if observed & mask != value & mask:
            raise read_epochs.ReadEpochError("original read debug-register write failed kernel readback")
        return observed

    def clear(self, pid):
        control = self.debug(pid, 7, 0)
        status = self.debug(pid, 6, 0)
        addresses = [self.debug(pid, index, 0) for index in range(4)]
        self.machine_event("clear", pid, registers=[*addresses, status, control])

    def actual_exec(self, pid, make, dispatch=None, inputs=None):
        self.clear(pid)
        self.machine_event(
            "execute", pid, make=make, dispatch=dispatch,
            **({"input_sha256": None if make else hashlib.sha256(encoded(inputs)).hexdigest()}
               if self.runtime else {}),
            **({"admission_owner": None if make else self.policy.native_jobs[dispatch]["admission"]["owner"]}
               if self.version in read_epochs.WRITABLE_VERSIONS else {}),
        )
        if not make:
            return
        if self.patterns is not None:
            self.patterns.retire()
        if self.active or self.invocations or self.pass_frame is not None or self.io is not None or self.pending_barrier is not None or self.execs != self.passes:
            raise read_epochs.ReadEpochError("Make exec crossed an incomplete original read pass")
        self.pid = pid
        self.bias = None
        self.slots = {}
        self.purposes = {}
        self.execs += 1
        self.event("exec", exec=self.execs)

    def ready(self, pid):
        if pid != self.pid or self.bias is not None:
            raise read_epochs.ReadEpochError("read trace readiness belongs to a foreign exec")
        with open(f"/proc/{pid}/maps", "rb") as stream:
            data = stream.read(65537)
        self.policy.charge_metadata(len(data))
        if len(data) > 65536:
            raise read_epochs.ReadEpochError("Make runtime mapping exceeds the existing bound")
        bases = []
        code, readonly = [], []
        for line in data.splitlines():
            fields = line.split(None, 5)
            if len(fields) < 5:
                raise read_epochs.ReadEpochError("malformed Make runtime mapping")
            first, last = (int(item, 16) for item in fields[0].split(b"-"))
            major, minor = (int(item, 16) for item in fields[3].split(b":"))
            identity = os.makedev(major, minor), int(fields[4])
            if identity != self.image_identity:
                continue
            offset = int(fields[2], 16)
            if offset == 0:
                bases.append(first)
            if fields[1] == b"r-xp":
                code.append((first, last))
            if fields[1] in {b"r-xp", b"r--p"}:
                readonly.append((first, last))
        if len(bases) != 1:
            raise read_epochs.ReadEpochError("Make read trace has no unique actual image mapping")
        image = read_epochs.Elf(self.image)
        load = [start for start, extent, offset, size, flags in image.loads if offset == 0]
        if len(load) != 1:
            raise read_epochs.ReadEpochError("Make read trace has an unsupported ELF load origin")
        self.bias = bases[0] - load[0]
        spans = [self.abi[name] for name in ("read_all", "source")]
        if self.version in read_epochs.LOCATION_VERSIONS:
            spans += [self.abi["completion"]["evaluator"], *self.abi["completion"]["relied_code"]]
            if self.runtime:
                spans += [self.abi["completion"]["runtime"]["definition"]]
                spans += [self.expansion_abi[key] for key in ("function", "recipe", "secondary", "snap")]
                if self.patterns is not None:
                    spans += [self.patterns.abi[key] for key in ("initializer", "creator")]
        for start, end in spans:
            if not any(left <= self.bias + start < self.bias + end <= right for left, right in code):
                raise read_epochs.ReadEpochError("read trace site is not readonly executable Make code")
            if self.memory(self.bias + start, end - start) != image.bytes(start, end - start, executable=True):
                raise read_epochs.ReadEpochError("read trace instruction image differs from captured Make")
        if self.version in read_epochs.LOCATION_VERSIONS:
            start, end = self.abi["completion"]["flavor_table"]
            if (
                not any(left <= self.bias + start < self.bias + end <= right for left, right in readonly)
                or self.memory(self.bias + start, end - start) != image.bytes(start, end - start)
            ):
                raise read_epochs.ReadEpochError("completion flavor table lacks immutable live Make mapping")
        self.arm()

    def arm(self):
        slots = {0: self.bias + self.abi["read_all"][0], 1: self.bias + self.abi["source"][0]}
        purposes = {0: "pass-entry", 1: "source-entry"}
        if self.runtime and self.invocations:
            runtime = self.abi["completion"]["runtime"]
            slots = {
                0: self.bias + self.abi["source"][0],
                1: self.bias + runtime["eval_buffer"][0],
                2: self.bias + runtime["definition"][0],
                3: self.invocations[-1]["return"],
            }
            purposes = {
                0: "source-entry", 1: "eval-entry", 2: "effect-entry",
                3: self.invocations[-1]["purpose"],
            }
        elif self.runtime and self.passes and self.passes == self.execs:
            runtime = self.abi["completion"]["runtime"]
            slots[0] = self.bias + self.expansion_abi["function"][0]
            purposes[0] = "expansion-entry"
            slots[2] = self.bias + runtime["eval_buffer"][0]
            slots[3] = self.bias + runtime["definition"][0]
            purposes[2] = "eval-entry"
            purposes[3] = "effect-entry"
            if self.patterns is not None:
                slots[1] = self.bias + self.patterns.abi["selection"]
                purposes[1] = "pattern-selection"
        elif self.active:
            slots[2] = self.active[-1]["return"]
            purposes[2] = "source-return"
            if self.version in {3, read_epochs.COMPLETION_VERSION}:
                slots[3] = self.bias + self.abi["completion"]["pc"]
                purposes[3] = "assignment-completion"
        if not self.runtime and self.pass_frame is not None and not (
            self.active and self.version in {3, read_epochs.COMPLETION_VERSION}
        ):
            slots[3] = self.pass_frame["return"]
            purposes[3] = "pass-return"
        if len(set(slots.values())) != len(slots):
            raise read_epochs.ReadEpochError("overlapping original read breakpoint sites")
        self.debug(self.pid, 7, 0)
        addresses = [self.debug(self.pid, index, slots.get(index, 0)) for index in range(4)]
        status = self.debug(self.pid, 6, 0)
        control = self.debug(self.pid, 7, sum(1 << (2 * index) for index in slots))
        self.slots = slots
        self.purposes = purposes
        self.machine_event(
            "arm", self.pid, registers=[*addresses, status, control],
            slots=[[index, address, purposes[index]] for index, address in sorted(slots.items())],
        )

    def caller(self, registers, target):
        returned = self.number(registers.rsp)
        raw = self.memory(returned - 5, 5)
        if read_epochs.direct_call(returned - 5, raw) != target:
            raise read_epochs.ReadEpochError("original source entry lacks its actual direct caller")
        relative = returned - self.bias
        image = read_epochs.Elf(self.image)
        if image.bytes(relative - 5, 5, executable=True) != raw:
            raise read_epochs.ReadEpochError("original source caller differs from its readonly image")
        return {"return": returned, "stack": registers.rsp, "frame": registers.rsp - 8}

    def trap(self, pid, state):
        self.deadline()
        self.policy.reserve_trace_observation()
        self.traps += 1
        self.policy.charge_metadata(64)
        if pid != self.pid or state.role != "make" or self.bias is None:
            raise read_epochs.ReadEpochError("foreign process claimed an original read breakpoint")
        info = self.siginfo_work
        self.policy.charge_metadata(ctypes.sizeof(info))
        self.native.ptrace(GETSIGINFO, pid, 0, ctypes.byref(info))
        if int.from_bytes(bytes(info[8:12]), "little", signed=True) != TRAP_HWBKPT:
            raise read_epochs.ReadEpochError("original read event is not a kernel hardware breakpoint")
        status = self.debug(pid, 6)
        fired = status & 15
        indices = [index for index in range(4) if fired & (1 << index)]
        registers, expected, restored = self.register_work
        self.policy.charge_metadata(ctypes.sizeof(registers))
        self.native.ptrace(self.native.GETREGS, pid, 0, ctypes.byref(registers))
        self.policy.charge_metadata(ctypes.sizeof(registers))
        ctypes.memmove(ctypes.byref(expected), ctypes.byref(registers), ctypes.sizeof(registers))
        if len(indices) != 1 or self.slots.get(indices[0]) != registers.rip:
            raise read_epochs.ReadEpochError("original read breakpoint is stale or unissued")
        index = indices[0]
        purpose = self.purposes.get(index)
        self.machine_event(
            "trap", pid, index=index, purpose=purpose, pc=registers.rip,
            status=status, sigcode=TRAP_HWBKPT,
        )
        if self.runtime and not self.invocations and purpose in {"eval-entry", "effect-entry"}:
            raise read_epochs.ReadEpochError("runtime post-read effect/eval is not qualified")
        if purpose == "pattern-selection" and self.patterns is not None:
            self.patterns.enter(registers, state)
        elif purpose == "pattern-completion" and self.patterns is not None:
            self.patterns.completion(registers, state)
        elif purpose == "pattern-definition-return" and self.patterns is not None:
            self.patterns.definition_return(registers)
        elif purpose == "pass-entry":
            if self.pass_frame is not None or self.active or self.passes + 1 != self.execs:
                raise read_epochs.ReadEpochError("repeated original read entry in one exec")
            self.pass_frame = self.caller(registers, registers.rip)
            if self.runtime:
                self.invocations.append({**self.pass_frame, "kind": "pass", "purpose": "pass-return"})
            self.passes += 1
            self.goals = {}
            globals_ = self.abi["globals"]
            inputs = read_epochs.original_inputs(
                self.memory, self.number(self.bias + globals_["current_variable_set_list"]),
                self.number(self.bias + globals_["hash_deleted_item"]),
                count_limit=self.config["observation_count"], string=self.string,
            )
            entry = self.event("pass-entry", **self.context(), inputs=inputs)
            if self.version in {2, *read_epochs.LOCATION_VERSIONS}:
                self.pending_barrier = {
                    "barrier": self.barriers + 1, "exec": self.execs, "pass": self.passes,
                    "trace_seq": entry["seq"], "input_sha256": source_phases.digest(inputs),
                }
        elif purpose == "source-entry":
            if self.patterns is not None:
                self.patterns.observe()
            if (
                self.pass_frame is None and not self.invocations
                or self.io is not None or self.pending_barrier is not None
            ):
                raise read_epochs.ReadEpochError("source entry has no original pass")
            frame = self.caller(registers, registers.rip)
            name = self.string(registers.rdi, 4096)
            flags = registers.rsi & 0xFFFFFFFF
            if not name or flags & ~15:
                raise read_epochs.ReadEpochError("source entry has unsupported name/flags")
            self.visits += 1
            parent = self.active[-1]["visit"] if self.active else None
            location = None
            if self.version in read_epochs.LOCATION_VERSIONS and (
                self.active or self.runtime and self.pass_frame is None
            ):
                if frame["return"] != self.bias + self.abi["completion"]["include_return"]:
                    raise read_epochs.ReadEpochError("source include entry has a foreign evaluator caller")
                location = (
                    self.runtime_location(registers.rbp)
                    if self.runtime else
                    self.source_location(registers, self.active[-1], allow_eval=False)
                )
            frame.update({"visit": self.visits, "name": name, "flags": flags, "source": None, "pin": None, "closed": False})
            self.event(
                "source-entry", **self.context(), visit=self.visits, parent=parent, name=name, flags=flags,
                **({"location": location} if self.version in read_epochs.LOCATION_VERSIONS else {}),
            )
            self.active.append(frame)
            if self.runtime:
                self.invocations.append({**frame, "kind": "source", "purpose": "source-return"})
        elif purpose == "source-return":
            self.source_return(registers)
        elif purpose == "assignment-completion":
            self.assignment_completion(registers, state)
        elif purpose == "eval-entry":
            self.runtime_eval_entry(registers)
        elif purpose == "eval-return":
            self.runtime_eval_return(registers)
        elif purpose == "expansion-entry":
            self.runtime_expansion_entry(registers, state)
        elif purpose == "expansion-return":
            self.runtime_expansion_return(registers)
        elif purpose == "effect-entry":
            self.runtime_effect_entry(registers)
        elif purpose == "effect-return":
            self.runtime_effect_return(registers)
        elif purpose == "effect-completion":
            self.runtime_effect_completion(registers, state)
        elif purpose == "pass-return":
            if self.active or self.io is not None or self.pass_frame is None or registers.rsp != self.pass_frame["stack"] + 8:
                raise read_epochs.ReadEpochError("original read return has an incomplete source stack")
            goals, visited, pointer = [], set(), registers.rax
            while pointer:
                if pointer in visited or pointer not in self.goals:
                    raise read_epochs.ReadEpochError("original read returned an unknown or repeated goal")
                visited.add(pointer)
                goals.append(self.goals[pointer])
                pointer = self.number(pointer)
            if visited != set(self.goals):
                raise read_epochs.ReadEpochError("original read goal chain omitted a source visit")
            self.event("pass-exit", **self.context(), goals=goals)
            self.pass_frame = None
            if self.runtime:
                frame = self.invocations.pop()
                if frame["kind"] != "pass" or self.invocations:
                    raise read_epochs.ReadEpochError("runtime pass returned across an active invocation")
        else:
            raise read_epochs.ReadEpochError("original read trap has no issued slot purpose")
        registers.eflags |= 1 << 16
        expected.eflags |= 1 << 16
        if self.register_views[0] != self.register_views[1]:
            raise read_epochs.ReadEpochError("original read callback changed its register state")
        self.native.ptrace(self.native.SETREGS, pid, 0, ctypes.byref(registers))
        self.policy.charge_metadata(ctypes.sizeof(restored))
        self.native.ptrace(self.native.GETREGS, pid, 0, ctypes.byref(restored))
        if self.register_views[2] != self.register_views[1]:
            raise read_epochs.ReadEpochError("original read register restoration failed kernel readback")
        self.arm()

    def source_location(self, registers, current, *, allow_eval):
        """Validate ancestry before interpreting source fields or a variable."""
        abi, evaluator, reader = self.abi["completion"], registers.rbp, current["frame"]
        if not registers.rsp <= evaluator < reader:
            raise read_epochs.ReadEpochError("completion evaluator escaped the actual source stack")
        saved, returned = struct.unpack("<QQ", self.memory(evaluator, 16))
        if returned == self.bias + abi["eval_return"]:
            if not allow_eval or saved == reader:
                raise read_epochs.ReadEpochError("include attempted copied-floc original source authority")
            buffer = self.number(evaluator + abi["eval_ebuffer"])
            floc = self.number(evaluator + abi["eval_floc"])
            if (
                not evaluator < saved < reader
                or self.number(buffer + 32) != 0 or floc != buffer + 40
                or self.number(self.bias + self.abi["globals"]["reading_file"]) != floc
            ):
                raise read_epochs.ReadEpochError("unselected eval completion has unverified ancestry")
            return None
        if saved != reader or returned != self.bias + abi["reader_return"]:
            raise read_epochs.ReadEpochError("completion has no direct original reader frame/return")
        buffer, floc = reader + abi["reader_ebuffer"], reader + abi["reader_floc"]
        if (
            self.number(evaluator + abi["eval_ebuffer"]) != buffer
            or self.number(evaluator + abi["eval_floc"]) != floc
            or self.number(self.bias + self.abi["globals"]["reading_file"]) != floc
            or self.number(buffer + 32) == 0
            or current["source"] is None or current["pin"] is None or current["closed"]
            or self.native.publication_identity(os.fstat(current["pin"])) != self.source_identity(current)
        ):
            raise read_epochs.ReadEpochError("completion lost original ebuffer/stream/pin/source custody")
        if self.string(self.number(floc), 4096) != current["name"]:
            raise read_epochs.ReadEpochError("completion floc names another original reader")
        start, offset = struct.unpack("<QQ", self.memory(floc + 8, 16))
        nlines = self.number(evaluator + abi["nlines"])
        span = self.statement_indexes.get(current["source"], {}).get(start)
        if span is None or nlines < 1 or span[2] != start + nlines - 1:
            raise read_epochs.ReadEpochError("completion nlines differs from pinned original source chunk")
        logical, first, last, _ = span
        # offset is intentionally not treated as a character column.
        return [current["visit"], current["source"], logical, first, last]

    def runtime_parent(self):
        frame = self.invocations[-1]
        return [frame["kind"], frame.get("number", frame.get("visit", self.passes))]

    def runtime_expansion_entry(self, registers, state):
        if self.invocations or self.active or self.pass_frame or self.io or not self.passes or not registers.rsi:
            raise read_epochs.ReadEpochError("runtime expansion crossed an active original invocation")
        abi = self.expansion_abi
        frame = self.caller(registers, registers.rip)
        returned = frame["return"] - self.bias
        if returned == abi["recipe_return"]:
            family = "recipe"
            if self.number(registers.rbp + abi["recipe_file"]) != registers.rsi:
                raise read_epochs.ReadEpochError("runtime recipe substituted its original file")
        elif returned == abi["secondary_return"]:
            family = "secondary"
            if registers.r14 != registers.rsi or self.number(registers.rbp + 8) - self.bias not in abi["secondary_snap_returns"]:
                raise read_epochs.ReadEpochError("runtime secondary lost its original file/target loop")
        else:
            raise read_epochs.ReadEpochError("runtime expansion has a foreign actual caller")
        target = self.string(self.number(registers.rsi), 4097)
        text = self.string(registers.rdi, self.config["file_limit"] + 1)
        if not target or text is None or not isinstance(state.cwd, str) or not state.cwd.startswith("/"):
            raise read_epochs.ReadEpochError("runtime expansion lacks bounded original target/input/CWD")
        self.expansions += 1
        entry = self.event(
            "expansion-entry", **self.context(), expansion=self.expansions,
            family=family, target=target, text=text, cwd=state.cwd,
        )
        self.machine_event(
            "expansion-input", self.pid, expansion=self.expansions,
            sha256=hashlib.sha256(encoded(entry)).hexdigest(),
        )
        self.invocations.append({
            **frame, "kind": "expansion", "purpose": "expansion-return", "number": self.expansions,
        })

    def runtime_expansion_return(self, registers):
        frame = self.invocations[-1]
        if frame["kind"] != "expansion" or registers.rsp != frame["stack"] + 8 or self.active or self.io:
            raise read_epochs.ReadEpochError("runtime expansion returned across an active occurrence")
        self.event("expansion-exit", **self.context(), expansion=frame["number"])
        self.invocations.pop()

    def runtime_location(self, evaluator):
        current = self.active[-1] if self.active else None
        abi = self.abi["completion"]
        saved, returned = struct.unpack("<QQ", self.memory(evaluator, 16))
        buffer = self.number(evaluator + abi["eval_ebuffer"])
        floc = self.number(evaluator + abi["eval_floc"])
        if (
            floc != buffer + 40
            or self.number(self.bias + self.abi["globals"]["reading_file"]) != floc
        ):
            raise read_epochs.ReadEpochError("runtime effect lost original evaluator/floc custody")
        if current is not None and saved == current["frame"] and returned == self.bias + abi["reader_return"]:
            if (
                buffer != current["frame"] + abi["reader_ebuffer"]
                or not self.number(buffer + 32) or current["source"] is None
                or current["pin"] is None or current["closed"]
                or self.native.publication_identity(os.fstat(current["pin"])) != self.source_identity(current)
                or self.string(self.number(floc), 4096) != current["name"]
            ):
                raise read_epochs.ReadEpochError("runtime effect lost its live source descriptor/pin")
            start = self.number(floc + 8)
            nlines = self.number(evaluator + abi["nlines"])
            span = self.statement_indexes[current["source"]].get(start)
            if span is None or nlines < 1 or span[2] != start + nlines - 1:
                raise read_epochs.ReadEpochError("runtime effect has an invalid original physical span")
            return {
                "visit": current["visit"], "source": current["source"], "evaluation": None,
                "span": list(span[:3]), "offsets": None,
            }
        evaluations = [
            frame for frame in self.invocations
            if frame["kind"] == "eval" and frame["frame"] == saved
        ]
        if len(evaluations) != 1 or returned != self.bias + abi["eval_return"]:
            raise read_epochs.ReadEpochError("runtime effect lacks an issued eval occurrence")
        evaluation, = evaluations
        runtime = abi["runtime"]
        if (
            buffer != saved + runtime["eval_ebuffer"] or self.number(buffer + 32)
            or self.number(buffer + 16) != evaluation["buffer"]
            or self.number(buffer + 24) != len(evaluation["data"])
        ):
            raise read_epochs.ReadEpochError("runtime effect substituted its pristine eval buffer")
        first, next_ = (self.number(buffer + offset) - evaluation["buffer"] for offset in (0, 8))
        if not 0 <= first < next_ <= len(evaluation["data"]) + 1:
            raise read_epochs.ReadEpochError("runtime eval consumed an invalid buffer interval")
        line = evaluation["data"][:first].count(b"\n") + 1
        span = self.statement_indexes[evaluation["source"]].get(line)
        if span is None:
            raise read_epochs.ReadEpochError("runtime eval interval has no pristine physical statement")
        return {
            "visit": None if current is None else current["visit"], "source": evaluation["source"],
            "evaluation": evaluation["number"], "span": list(span[:3]),
            "offsets": [first, next_],
        }

    def runtime_evaluator(self, frame):
        abi = self.abi["completion"]
        upper = self.active[-1]["frame"] if self.active else self.invocations[0]["frame"] if self.invocations else 0
        for _ in range(512):
            if not 0 < frame < upper:
                break
            saved, returned = struct.unpack("<QQ", self.memory(frame, 16))
            if returned in {self.bias + abi["reader_return"], self.bias + abi["eval_return"]}:
                return frame
            if not frame < saved <= upper:
                break
            frame = saved
        raise read_epochs.ReadEpochError("runtime invocation lacks its actual evaluator ancestor")

    def runtime_effect_entry(self, registers):
        if self.patterns is not None and self.patterns.definition(registers):
            return
        abi = self.abi["completion"]
        runtime = abi["runtime"]
        frame = self.caller(registers, registers.rip)
        returned = frame["return"] - self.bias
        if returned == runtime["ordinary_definition_return"]:
            evaluator = self.number(registers.rbp)
            trial_return = self.number(registers.rbp + 8) - self.bias
            if trial_return == runtime["ordinary_assignment_return"]:
                kind = "assignment"
            elif trial_return == runtime["target_assignment_return"]:
                kind = "target"
            else:
                raise read_epochs.ReadEpochError("runtime definition has a foreign trial caller")
        elif returned == runtime["define_definition_return"]:
            evaluator, kind = registers.rbp, "define"
            if registers.rdi != evaluator + runtime["define_floc"]:
                raise read_epochs.ReadEpochError("runtime define lost its actual copied header floc")
        elif returned == runtime["reader_definition_return"]:
            evaluator, kind = None, "reader"
        else:
            raise read_epochs.ReadEpochError("runtime definition has an unqualified original caller")
        if not self.invocations or registers.r8 not in range(1, 7) or registers.rcx not in range(7) or registers.r9 not in {0, 1}:
            raise read_epochs.ReadEpochError("runtime definition has invalid actual parameters")
        name, value = self.string(registers.rsi, 129), self.string(registers.rdx, 65537)
        if not name or value is None:
            raise read_epochs.ReadEpochError("runtime definition lacks a bounded actual name/value")
        location = None if kind == "reader" else self.runtime_location(evaluator)
        self.effects += 1
        entry = self.event(
            "effect-entry", **self.context(), effect=self.effects, parent=self.runtime_parent(),
            visit=self.active[-1]["visit"] if self.active else None, caller=kind, location=location,
            name=name, value=value, flavor=registers.r8, origin=registers.rcx,
            target=bool(registers.r9),
            declaration=[
                self.string(self.number(registers.rdi), 4096),
                self.number(registers.rdi + 8), self.number(registers.rdi + 16),
            ],
        )
        self.machine_event(
            "effect-input", self.pid, effect=self.effects,
            sha256=hashlib.sha256(encoded(entry)).hexdigest(),
        )
        self.invocations.append({
            **frame, "kind": "effect", "purpose": "effect-return", "number": self.effects,
            "entry": entry, "evaluator": evaluator,
        })

    def runtime_effect_return(self, registers):
        frame = self.invocations[-1]
        if frame["kind"] != "effect" or registers.rsp != frame["stack"] + 8 or not registers.rax:
            raise read_epochs.ReadEpochError("runtime definition returned with a foreign lifetime/result")
        frame["variable"] = registers.rax
        self.event("effect-return", **self.context(), effect=frame["number"])
        if frame["entry"]["caller"] != "reader":
            frame["purpose"] = "effect-completion"
            frame["return"] = self.bias + (
                self.abi["completion"]["runtime"]["target_completion"]
                if frame["entry"]["caller"] == "target" else self.abi["completion"]["pc"]
            )
        else:
            self.runtime_effect_completion(registers, self.policy.processes[self.pid], returned=True)

    def runtime_effect_completion(self, registers, state, *, returned=False):
        frame = self.invocations[-1]
        if frame["kind"] != "effect" or not returned and (
            registers.rbp != frame["evaluator"]
            or (registers.r13 if frame["entry"]["caller"] == "target" else registers.rbx)
            != frame["variable"]
        ):
            raise read_epochs.ReadEpochError("runtime definition lost its post-modifier effective result")
        variable = read_epochs.original_variable(self.memory, frame["variable"], self.string)
        if variable[0] != frame["entry"]["name"] or not isinstance(state.cwd, str) or not state.cwd.startswith("/"):
            raise read_epochs.ReadEpochError("runtime definition returned a foreign effective name/CWD")
        completion = self.event(
            "effect-completion", **self.context(), effect=frame["number"],
            cwd=state.cwd, variable=variable,
        )
        self.machine_event(
            "effect-result", self.pid, effect=frame["number"],
            sha256=hashlib.sha256(encoded(completion)).hexdigest(),
        )
        self.invocations.pop()

    def runtime_eval_entry(self, registers):
        if self.patterns is not None:
            self.patterns.observe()
        if not self.invocations:
            raise read_epochs.ReadEpochError("runtime eval has no original invocation")
        frame = self.caller(registers, registers.rip)
        if any(item["kind"] == "eval" for item in self.invocations) or self.active:
            location = self.runtime_location(self.runtime_evaluator(registers.rbp))
        else:
            root = self.invocations[0]
            if root["kind"] not in ({"expansion", "pattern"} if self.patterns is not None else {"expansion"}):
                raise read_epochs.ReadEpochError("runtime eval lacks an original reader/expansion root")
            pointer = registers.rbp
            for _ in range(512):
                if pointer == root["frame"]:
                    break
                if not registers.rsp < pointer < root["frame"]:
                    raise read_epochs.ReadEpochError("runtime eval escaped its actual expansion frame")
                following = self.number(pointer)
                if not pointer < following <= root["frame"]:
                    raise read_epochs.ReadEpochError("runtime eval has a foreign expansion ancestor")
                pointer = following
            else:
                raise read_epochs.ReadEpochError("runtime eval expansion ancestry exceeds its bound")
            location = {root["kind"]: root["number"]}
        text = self.string(registers.rdi, self.config["file_limit"] + 1)
        if text is None:
            raise read_epochs.ReadEpochError("runtime eval has no bounded pristine buffer")
        data = text.encode("utf-8")
        self.policy.charge_metadata(len(data))
        number = len(self.sources) + 1
        source = {
            "id": number, "mode": 0o600, "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "data": base64.b64encode(data).decode("ascii"),
        }
        self.policy.charge_metadata(len(encoded(source)))
        self.sources.append(source)
        self.statement_indexes[number] = read_epochs._statement_index(
            data, checkpoint=self.deadline, count_limit=self.config["observation_count"],
            reserve=self.policy.charge_metadata,
            compact=self.patterns is not None,
        )
        self.evaluations += 1
        self.event(
            "eval-entry", **self.context(), evaluation=self.evaluations,
            parent=self.runtime_parent(), location=location, source=number,
        )
        self.machine_event(
            "eval-buffer", self.pid, evaluation=self.evaluations, source=number,
            sha256=source["sha256"],
        )
        self.invocations.append({
            **frame, "kind": "eval", "purpose": "eval-return", "number": self.evaluations,
            "source": number, "buffer": registers.rdi, "data": data,
        })

    def runtime_eval_return(self, registers):
        frame = self.invocations[-1]
        if frame["kind"] != "eval" or registers.rsp != frame["stack"] + 8:
            raise read_epochs.ReadEpochError("runtime eval returned across an active occurrence")
        if self.patterns is not None:
            self.patterns.complete(["eval", frame["number"]], frame["source"])
        self.event("eval-exit", **self.context(), evaluation=frame["number"], source=frame["source"])
        self.invocations.pop()

    def assignment_completion(self, registers, state):
        if self.version not in {3, read_epochs.COMPLETION_VERSION} or not self.active or self.pass_frame is None or self.io is not None:
            raise read_epochs.ReadEpochError("completion has no active original source/pass")
        current, abi = self.active[-1], self.abi["completion"]
        location = self.source_location(registers, current, allow_eval=True)
        if location is None:
            if self.version == read_epochs.COMPLETION_VERSION:
                raise read_epochs.ReadEpochError("evaluated assignment lacks admitted source provenance")
            return
        modifiers = int.from_bytes(self.memory(registers.rbp + abi["modifiers"], 4), "little")
        path = current.get("relative", current["name"].removeprefix("/repo/"))
        key = (path, self.sources[current["source"] - 1]["sha256"], *location[2:])
        self.policy.charge_metadata(len(encoded(key)))
        index = current.get("selection_index", self.selection_index)
        row = index.get(key)
        if row is None:
            return
        if modifiers & ~0x3F or not modifiers & 1 or modifiers & (2 | 4 | 32):
            raise read_epochs.ReadEpochError("selected completion is not an ordinary nonprivate assignment")
        if bool(modifiers & 16) != row[8]:
            raise read_epochs.ReadEpochError("completion modifiers differ from original assignment spelling")
        variable = read_epochs.original_variable(self.memory, registers.rbx, self.string)
        if variable[0] != row[5] or variable[2] & (1 << 7):
            raise read_epochs.ReadEpochError("selected completion returned a foreign/private effective variable")
        if not isinstance(state.cwd, str) or not state.cwd.startswith("/"):
            raise read_epochs.ReadEpochError("completion lacks actual supervised CWD")
        self.event(
            "assignment-completion", **self.context(), visit=current["visit"], source=current["source"],
            site=list(row), name=row[5], operator=row[6], cwd=state.cwd, variable=variable,
        )

    def confirm_barrier(self, request, image_sha256):
        if self.pending_barrier != {
            key: request[key] for key in ("barrier", "exec", "pass", "trace_seq", "input_sha256")
        } or not self.pass_frame or self.active:
            raise read_epochs.ReadEpochError("original entry barrier lost its actual stopped read context")
        self.event(
            "entry-image", **self.context(), barrier=request["barrier"],
            input_sha256=request["input_sha256"], image_sha256=image_sha256,
        )
        self.barriers += 1
        self.pending_barrier = None

    def source_io(self, pid, state, address, size):
        if pid != self.pid or size != 48:
            raise read_epochs.ReadEpochError("source stream notification has a foreign process/shape")
        caller, frame, name_ptr, mode_ptr, descriptor, phase, error = struct.unpack("<QQQQqII", self.memory(address, size))
        name, mode = self.string(name_ptr, 4096), self.string(mode_ptr, 17)
        if not name or not mode or phase not in (0, 1) or error > 4095:
            raise read_epochs.ReadEpochError("source stream notification is malformed")
        source = caller - self.bias in self.abi["source_opens"]
        if source and (not self.active or frame != self.active[-1]["frame"]):
            raise read_epochs.ReadEpochError(
                "source stream has a mismatched actual frame: "
                + repr((caller - self.bias, frame, None if not self.active else self.active[-1]["frame"]))
            )
        if source and self.number(frame + 8) != self.active[-1]["return"]:
            raise read_epochs.ReadEpochError("source stream caller lost its native frame")
        binding = address, caller, frame, name_ptr, mode_ptr, name, mode, source
        if phase == 0:
            if self.io is not None or descriptor != -1 or error:
                raise read_epochs.ReadEpochError("overlapping or malformed source stream entry")
            if source and self.version in read_epochs.WRITABLE_VERSIONS:
                current = self.active[-1]
                observer = self.policy.native_outputs
                path = self.policy.resolve(name if name.startswith("/") else state.cwd + "/" + name)
                item = observer.custody.objects.get(path)
                if item is not None:
                    try:
                        require_retained_source(path.removeprefix("/repo/"), self.config["native_output_paths"])
                    except MakeProbeError as error:
                        raise read_epochs.ReadEpochError(str(error)) from error
                    descriptor = observer.operand(path)
                    try:
                        if descriptor is None:
                            raise read_epochs.ReadEpochError("generated source entry lost its produced object")
                        lease = observer.custody.capture(
                            owner=item.owner, path=path, descriptor=descriptor, observed=False,
                        )
                    finally:
                        if descriptor is not None:
                            os.close(descriptor)
                    current["generated"] = lease
                    self.machine_event(
                        "generated-source-entry", pid, visit=current["visit"],
                        owner=item.owner, serial=item.serial, revision=lease.revision,
                        path=path.removeprefix("/repo/"), identity=list(lease.identity),
                        sha256=lease.sha256,
                    )
                    current["generated_entry"] = len(self.machine)
            self.io = binding
            return
        if self.io != binding:
            raise read_epochs.ReadEpochError("source stream return is foreign or unpaired")
        self.io = None
        if descriptor < -1 or descriptor > 127 or descriptor == -1 and not error:
            raise read_epochs.ReadEpochError("source stream returned an invalid real descriptor/status")
        visit = self.active[-1]["visit"] if self.active else None
        result = descriptor if descriptor >= 0 else -error
        if not source:
            self.event("other-open", exec=self.execs, **{"pass": self.passes if self.pass_frame else None},
                       visit=visit, name=name, mode=mode, result=result)
            return
        current = self.active[-1]
        if descriptor < 0 and current.get("generated") is not None:
            current.update(
                relative=current["generated"].object.path.removeprefix("/repo/"),
                custody={"kind": "native-output", "entry": current["generated_entry"]},
            )
        snapshot = identity = None
        if descriptor >= 0:
            if current["source"] is not None or mode not in {"r", "re"}:
                raise read_epochs.ReadEpochError("original source has a repeated or writable stream")
            path = state.fds.get(descriptor)
            if not isinstance(path, str) or not path.startswith("/repo/"):
                raise read_epochs.ReadEpochError("original source stream is outside its admitted repository view")
            pin = os.open(f"/proc/{pid}/fd/{descriptor}", os.O_RDONLY | os.O_CLOEXEC)
            try:
                before = os.fstat(pin)
                if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= self.config["file_limit"]:
                    raise read_epochs.ReadEpochError("original source stream is not a bounded regular input")
                identity = self.native.publication_identity(before)
                remaining, offset, data = before.st_size, 0, bytearray()
                self.policy.charge_metadata(before.st_size)
                while remaining:
                    self.deadline()
                    block = os.pread(pin, min(remaining, 65536), offset)
                    if not block:
                        raise read_epochs.ReadEpochError("original source stream was truncated")
                    data.extend(block)
                    offset += len(block)
                    remaining -= len(block)
                if self.native.publication_identity(os.fstat(pin)) != identity:
                    raise read_epochs.ReadEpochError("original source changed during entry capture")
                mode_bits = stat.S_IMODE(before.st_mode)
                digest = hashlib.sha256(data).hexdigest()
                key = mode_bits, len(data), digest
                snapshot = self.pool.get(key)
                if snapshot is None:
                    snapshot = len(self.sources) + 1
                    row = {"id": snapshot, "mode": mode_bits, "bytes": len(data), "sha256": digest,
                           "data": base64.b64encode(data).decode("ascii")}
                    self.policy.charge_metadata(len(encoded(row)))
                    self.sources.append(row)
                    self.pool[key] = snapshot
                if self.version in read_epochs.LOCATION_VERSIONS and snapshot not in self.statement_indexes:
                    self.policy.charge_metadata(len(data))
                    self.statement_indexes[snapshot] = read_epochs._statement_index(
                        bytes(data), checkpoint=self.deadline,
                        count_limit=self.config["observation_count"], reserve=self.policy.charge_metadata,
                        compact=self.patterns is not None,
                    )
                relative = path.removeprefix("/repo/")
                custody = None
                selection_index = None
                if self.version in read_epochs.MACHINE_VERSIONS:
                    if not relative or relative.startswith("/") or ".." in relative.split("/") or "\\" in relative:
                        raise read_epochs.ReadEpochError("opened source has no exact repository-relative path")
                    content_digest = hashlib.sha256(data).hexdigest()
                    entry = self.selection_inventory.get(relative)
                    if entry is not None and entry["kind"] == "snapshot" and (
                        entry["mode"] == mode_bits and entry["size"] == len(data)
                        and entry["sha256"] == content_digest
                    ):
                        custody = {"kind": "snapshot"}
                    elif self.version in read_epochs.WRITABLE_VERSIONS and current.get("generated") is not None:
                        lease = current["generated"]
                        if (
                            identity != lease.identity or bytes(data) != lease.data
                            or path != lease.object.path or lease.closed
                        ):
                            raise read_epochs.ReadEpochError("generated source stream differs from its pinned entry")
                        custody = {"kind": "native-output", "entry": current["generated_entry"]}
                    else:
                        raise read_epochs.ReadEpochError(
                            "opened source is outside the exact readonly snapshot inventory"
                        )
                    if self.version == read_epochs.COMPLETION_VERSION:
                        rows, references, dependencies = read_epochs.completion_source_facts(
                            relative, bytes(data),
                            checkpoint=self.deadline, count_limit=self.config["observation_count"],
                            charge=self.policy.charge_metadata,
                        )
                        read_epochs.require_completion_reference_closure(
                            references, self.selection_names, dependencies=dependencies,
                        )
                        sites = [site for site in rows if site[5] in self.selection_names]
                        selection_index = MappingProxyType({tuple(site[:5]): site for site in sites})
                current.update(
                    source=snapshot, pin=pin, identity=identity, descriptor=descriptor, path=path,
                    relative=relative, custody=custody, selection_index=selection_index,
                )
                pin = -1
            finally:
                if pin >= 0:
                    os.close(pin)
        self.event(
            "source-open", **self.context(), visit=visit, name=name, mode=mode, result=result,
            source=snapshot, identity=None if identity is None else list(identity),
            **({"path": current.get("relative"), "custody": current.get("custody")}
               if source and self.version in read_epochs.MACHINE_VERSIONS else {}),
        )

    def fd_closed(self, pid, descriptor):
        if pid == self.pid:
            for visit in self.active:
                if visit.get("descriptor") == descriptor and not visit["closed"]:
                    visit["closed"] = True

    def source_return(self, registers):
        if not self.active or self.io is not None:
            raise read_epochs.ReadEpochError("source return has no matching native entry")
        current = self.active[-1]
        if self.patterns is not None:
            self.patterns.complete(["source", current["visit"]], current["source"])
        if registers.rsp != current["stack"] + 8:
            raise read_epochs.ReadEpochError("source return has a forged or changed stack")
        pointer = registers.rax
        raw = self.memory(pointer, 64)
        _, name, file_, _, flags, error, _, _, _ = struct.unpack("<QQQQIiQQQ", raw)
        if self.pass_frame is not None and pointer in self.goals or flags & 255 != current["flags"] or not 0 <= error <= 4095:
            raise read_epochs.ReadEpochError("original source status/goal identity is invalid")
        resolved = self.string(name, 4096) if name else self.string(self.number(file_), 4096)
        if not resolved or (error == 0) != (current["source"] is not None):
            raise read_epochs.ReadEpochError(
                "original source success contradicts its real stream: "
                + repr((current["name"], resolved, error, current["source"]))
            )
        if current["pin"] is not None:
            expected_identity = self.source_identity(current)
            if not current["closed"] or self.native.publication_identity(os.fstat(current["pin"])) != expected_identity:
                raise read_epochs.ReadEpochError("original source was changed or not closed before return")
            path = read_epochs._resolved_source_path(resolved)
            if path != current["path"]:
                raise read_epochs.ReadEpochError("original source status names a different actual stream")
            pin, current["pin"] = current["pin"], None
            os.close(pin)
        if current.get("generated") is not None:
            self.policy.native_outputs.custody.release(current["generated"])
        if self.pass_frame is not None:
            self.goals[pointer] = current["visit"]
        self.event("source-exit", **self.context(), visit=current["visit"], resolved=resolved,
                   flags=flags, error=error, source=current["source"])
        if current["source"] is not None or current.get("generated") is not None:
            self.machine_event(
                "pin-retired", self.pid, visit=current["visit"], source=current["source"],
                identity=list(self.source_identity(current)),
            )
        self.active.pop()
        if self.runtime:
            frame = self.invocations.pop()
            if frame["kind"] != "source" or frame["visit"] != current["visit"]:
                raise read_epochs.ReadEpochError("runtime source returned across an active invocation")

    def retire_root(self):
        policy = self.policy
        if (
            policy.processes or policy.newborn_stops or policy.producer_requests
            or self.active or self.pass_frame is not None or self.io is not None
            or self.pending_barrier is not None or self.invocations
            or self.passes != self.execs or self.barriers != self.passes
            or not self.runtime or policy.native_root is None
            or policy.native_root["pid"] != self.pid
        ):
            raise read_epochs.ReadEpochError("finite native root ended with incomplete actual state")
        policy.finish_native_jobs()
        if policy.native_outputs is not None:
            policy.native_outputs.finish()
        read_epochs.validate_native_root(
            policy.native_root, argv=self.config["argv"], cwd=self.config.get("cwd", "/repo"),
            environment=self.config["environment"], returncode=0,
        )
        if self.patterns is not None:
            self.patterns.retire()
        previous = self.root_boundaries[-1] if self.root_boundaries else None
        row = {
            "ordinal": len(self.root_boundaries or ()) + 1,
            "initial": dict(
                policy.native_root, argv=list(policy.native_root["argv"]),
                environment=dict(policy.native_root["environment"]),
            ),
            "first": previous["last"] + 1 if previous else 1,
            "last": len(self.machine),
            "first_exec": previous["last_exec"] + 1 if previous else 1,
            "last_exec": self.execs,
        }
        if (
            row["first"] > row["last"] or row["first_exec"] > row["last_exec"]
            or any(
                previous["initial"]["pid"] == row["initial"]["pid"]
                for previous in (self.root_boundaries or ())
            )
        ):
            raise read_epochs.ReadEpochError("finite native root reused its completed actual range")
        policy.reserve_trace_observation()
        policy.charge_metadata(
            len(encoded(row)) + sys.getsizeof(row["initial"])
            + sys.getsizeof(row["initial"]["argv"]) + sys.getsizeof(row["initial"]["environment"])
        )
        if self.root_boundaries is None:
            self.root_boundaries = []
            policy.charge_metadata(sys.getsizeof(self.root_boundaries))
        previous_size = sys.getsizeof(self.root_boundaries)
        self.root_boundaries.append(row)
        if sys.getsizeof(self.root_boundaries) > previous_size:
            policy.charge_metadata(sys.getsizeof(self.root_boundaries))

    def finish(self):
        if (
            self.active or self.pass_frame is not None or self.io is not None or self.pending_barrier is not None
            or self.invocations
            or not self.passes or self.passes != self.execs
            or self.version in {2, *read_epochs.LOCATION_VERSIONS} and self.barriers != self.passes
        ):
            raise read_epochs.ReadEpochError("original read trace ended with incomplete native state")
        self.event(
            "complete", execs=self.execs, passes=self.passes, visits=self.visits,
            **({"effects": self.effects, "evaluations": self.evaluations, "expansions": self.expansions} if self.runtime else {}),
            **({"templates": self.patterns.serial, "patterns": self.patterns.materializations,
                "pattern_returns": self.patterns.returns} if self.patterns is not None else {}),
        )
        result = {"version": self.version, "scope": self.scope, "events": self.events, "sources": self.sources, "complete": True}
        if self.version == 3:
            result["selection"] = [list(row) for row in self.selection]
        elif self.version in read_epochs.MACHINE_VERSIONS:
            result["selection"] = self.selection
            result["machine"] = {
                "version": (4 if self.root_boundaries else 3) if self.patterns is not None else (2 if self.root_boundaries else 1),
                "events": self.machine, "closed": True,
                **({"roots": self.root_boundaries} if self.root_boundaries else {}),
            }
        if self.version in read_epochs.WRITABLE_VERSIONS:
            result["output_authority"] = {
                "paths": self.config["native_output_paths"],
                **({"resources": self.config["native_resources"]} if self.config.get("native_resources") else {}),
                **({"source_roots": self.config["native_source_roots"]} if self.config.get("native_source_roots") else {}),
                "jobs": [
                    {key: row[key] for key in ("sequence", "pid", "admission")}
                    for row in self.policy.native_jobs.values()
                ],
            }
        read_epochs.validate_trace(result, self.scope, count_limit=self.config["observation_count"],
                                   file_limit=self.config["file_limit"], reserve=self.policy.charge_metadata)
        return result

    @staticmethod
    def source_identity(current):
        lease = current.get("generated")
        return lease.object.identity if lease is not None else current["identity"]

    def close(self):
        pins = []
        for frame in self.active:
            pin, frame["pin"] = frame["pin"], None
            if pin is not None:
                pins.append(pin)
        finish_cleanup([lambda pin=pin: os.close(pin) for pin in pins])
