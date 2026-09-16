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

if __package__:
    from .authority import encoded
    from . import read_epochs
else:
    from authority import encoded
    import read_epochs


DEBUG_REGISTER_OFFSET = 848
GETSIGINFO = 0x4202
TRAP_HWBKPT = 4


class NativeReadTrace:
    def __init__(self, policy, config):
        self.policy, self.config = policy, policy.config
        self.native = sys.modules[type(policy).__module__]
        self.scope = config["scope"]
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
        source = read_epochs.Elf(self.image).bytes(self.abi["source"][0], 8, executable=True)
        if not source.startswith((b"\x55\x48\x89\xe5", b"\xf3\x0f\x1e\xfa\x55\x48\x89\xe5")):
            raise read_epochs.ReadEpochError("source reader lacks its verified frame-pointer ABI")
        self.events, self.sources = [], []
        self.pool = {}
        self.execs = self.passes = self.visits = 0
        self.pid = self.bias = None
        self.slots = {}
        self.active = []
        self.pass_frame = None
        self.goals = {}
        self.io = None

    def event(self, kind, **fields):
        if len(self.events) >= self.config["observation_count"]:
            raise read_epochs.ReadEpochError("original read event count exceeds its existing bound")
        row = {"seq": len(self.events) + 1, "kind": kind, **fields}
        self.policy.charge_metadata(len(encoded(row)))
        self.events.append(row)

    def context(self):
        return {"exec": self.execs, "pass": self.passes}

    def memory(self, address, count):
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
        if value is None:
            return self.native.ptrace(3, pid, DEBUG_REGISTER_OFFSET + 8 * index) & ((1 << 64) - 1)
        self.native.ptrace(6, pid, DEBUG_REGISTER_OFFSET + 8 * index, value)

    def clear(self, pid):
        self.debug(pid, 7, 0)
        self.debug(pid, 6, 0)
        for index in range(4):
            self.debug(pid, index, 0)

    def actual_exec(self, pid, make):
        self.clear(pid)
        if not make:
            return
        if self.active or self.pass_frame is not None or self.io is not None or self.execs != self.passes:
            raise read_epochs.ReadEpochError("Make exec crossed an incomplete original read pass")
        self.pid = pid
        self.bias = None
        self.slots = {}
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
        code = []
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
        if len(bases) != 1:
            raise read_epochs.ReadEpochError("Make read trace has no unique actual image mapping")
        image = read_epochs.Elf(self.image)
        load = [start for start, extent, offset, size, flags in image.loads if offset == 0]
        if len(load) != 1:
            raise read_epochs.ReadEpochError("Make read trace has an unsupported ELF load origin")
        self.bias = bases[0] - load[0]
        for name in ("read_all", "source"):
            start, end = self.abi[name]
            if not any(left <= self.bias + start < self.bias + end <= right for left, right in code):
                raise read_epochs.ReadEpochError("read trace site is not readonly executable Make code")
            if self.memory(self.bias + start, end - start) != image.bytes(start, end - start, executable=True):
                raise read_epochs.ReadEpochError("read trace instruction image differs from captured Make")
        self.arm()

    def arm(self):
        slots = {0: self.bias + self.abi["read_all"][0], 1: self.bias + self.abi["source"][0]}
        if self.active:
            slots[2] = self.active[-1]["return"]
        if self.pass_frame is not None:
            slots[3] = self.pass_frame["return"]
        if len(set(slots.values())) != len(slots):
            raise read_epochs.ReadEpochError("overlapping original read breakpoint sites")
        self.debug(self.pid, 7, 0)
        for index in range(4):
            self.debug(self.pid, index, slots.get(index, 0))
        self.debug(self.pid, 6, 0)
        self.debug(self.pid, 7, sum(1 << (2 * index) for index in slots))
        self.slots = slots

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
        if pid != self.pid or state.role != "make" or self.bias is None:
            raise read_epochs.ReadEpochError("foreign process claimed an original read breakpoint")
        info = (ctypes.c_ubyte * 128)()
        self.native.ptrace(GETSIGINFO, pid, 0, ctypes.byref(info))
        if int.from_bytes(bytes(info[8:12]), "little", signed=True) != TRAP_HWBKPT:
            raise read_epochs.ReadEpochError("original read event is not a kernel hardware breakpoint")
        fired = self.debug(pid, 6) & 15
        indices = [index for index in range(4) if fired & (1 << index)]
        registers = self.native.Registers()
        self.native.ptrace(self.native.GETREGS, pid, 0, ctypes.byref(registers))
        if len(indices) != 1 or self.slots.get(indices[0]) != registers.rip:
            raise read_epochs.ReadEpochError("original read breakpoint is stale or unissued")
        index = indices[0]
        if index == 0:
            if self.pass_frame is not None or self.active or self.passes + 1 != self.execs:
                raise read_epochs.ReadEpochError("repeated original read entry in one exec")
            self.pass_frame = self.caller(registers, registers.rip)
            self.passes += 1
            self.goals = {}
            globals_ = self.abi["globals"]
            inputs = read_epochs.original_inputs(
                self.memory, self.number(self.bias + globals_["current_variable_set_list"]),
                self.number(self.bias + globals_["hash_deleted_item"]),
                count_limit=self.config["observation_count"], string=self.string,
            )
            self.event("pass-entry", **self.context(), inputs=inputs)
        elif index == 1:
            if self.pass_frame is None or self.io is not None:
                raise read_epochs.ReadEpochError("source entry has no original pass")
            frame = self.caller(registers, registers.rip)
            name = self.string(registers.rdi, 4096)
            flags = registers.rsi & 0xFFFFFFFF
            if not name or flags & ~15:
                raise read_epochs.ReadEpochError("source entry has unsupported name/flags")
            self.visits += 1
            parent = self.active[-1]["visit"] if self.active else None
            frame.update({"visit": self.visits, "name": name, "flags": flags, "source": None, "pin": None, "closed": False})
            self.event("source-entry", **self.context(), visit=self.visits, parent=parent, name=name, flags=flags)
            self.active.append(frame)
        elif index == 2:
            self.source_return(registers)
        else:
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
        registers.eflags |= 1 << 16
        self.native.ptrace(self.native.SETREGS, pid, 0, ctypes.byref(registers))
        self.arm()

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
                current.update(source=snapshot, pin=pin, identity=identity, descriptor=descriptor, path=path)
                pin = -1
            finally:
                if pin >= 0:
                    os.close(pin)
        self.event("source-open", **self.context(), visit=visit, name=name, mode=mode, result=result,
                   source=snapshot, identity=None if identity is None else list(identity))

    def fd_closed(self, pid, descriptor):
        if pid == self.pid:
            for visit in self.active:
                if visit.get("descriptor") == descriptor and not visit["closed"]:
                    visit["closed"] = True

    def source_return(self, registers):
        if not self.active or self.io is not None:
            raise read_epochs.ReadEpochError("source return has no matching native entry")
        current = self.active[-1]
        if registers.rsp != current["stack"] + 8:
            raise read_epochs.ReadEpochError("source return has a forged or changed stack")
        pointer = registers.rax
        raw = self.memory(pointer, 64)
        _, name, file_, _, flags, error, _, _, _ = struct.unpack("<QQQQIiQQQ", raw)
        if pointer in self.goals or flags & 255 != current["flags"] or not 0 <= error <= 4095:
            raise read_epochs.ReadEpochError("original source status/goal identity is invalid")
        resolved = self.string(name, 4096) if name else self.string(self.number(file_), 4096)
        if not resolved or (error == 0) != (current["source"] is not None):
            raise read_epochs.ReadEpochError(
                "original source success contradicts its real stream: "
                + repr((current["name"], resolved, error, current["source"]))
            )
        if current["pin"] is not None:
            if not current["closed"] or self.native.publication_identity(os.fstat(current["pin"])) != current["identity"]:
                raise read_epochs.ReadEpochError("original source was changed or not closed before return")
            path = self.native.posixpath.normpath(
                resolved if resolved.startswith("/") else "/repo/" + resolved
            )
            if path != current["path"]:
                raise read_epochs.ReadEpochError("original source status names a different actual stream")
            os.close(current["pin"])
            current["pin"] = None
        self.goals[pointer] = current["visit"]
        self.event("source-exit", **self.context(), visit=current["visit"], resolved=resolved,
                   flags=flags, error=error, source=current["source"])
        self.active.pop()

    def finish(self):
        if self.active or self.pass_frame is not None or self.io is not None or not self.passes or self.passes != self.execs:
            raise read_epochs.ReadEpochError("original read trace ended with incomplete native state")
        self.event("complete", execs=self.execs, passes=self.passes, visits=self.visits)
        result = {"version": 1, "scope": self.scope, "events": self.events, "sources": self.sources, "complete": True}
        read_epochs.validate_trace(result, self.scope, count_limit=self.config["observation_count"],
                                   file_limit=self.config["file_limit"], reserve=self.policy.charge_metadata)
        return result

    def close(self):
        for frame in self.active:
            if frame["pin"] is not None:
                os.close(frame["pin"])
                frame["pin"] = None
