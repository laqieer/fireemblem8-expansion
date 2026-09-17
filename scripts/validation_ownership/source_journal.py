"""Independent fixed-directory mutation windows; no general phase authority."""

from __future__ import annotations

import ctypes
import os
from pathlib import PurePosixPath
import re
import struct
from threading import get_ident

if __package__:
    from .authority import encoded, relative_path
    from .budget import MakeProbeError
    from .lifecycle import finish_cleanup
    from .producer_channel import ChannelError
    from .source_phases import digest
else:
    from authority import encoded, relative_path
    from budget import MakeProbeError
    from lifecycle import finish_cleanup
    from producer_channel import ChannelError
    from source_phases import digest


MODE = "fixed-directories"
MODIFY, ATTRIB, CLOSE_WRITE = 2, 4, 8
MOVED_FROM, MOVED_TO, CREATE, DELETE = 0x40, 0x80, 0x100, 0x200
DELETE_SELF, MOVE_SELF, UNMOUNT, OVERFLOW, IGNORED = 0x400, 0x800, 0x2000, 0x4000, 0x8000
ONLYDIR, ISDIR = 0x01000000, 0x40000000
MASK = MODIFY | ATTRIB | CLOSE_WRITE | MOVED_FROM | MOVED_TO | CREATE | DELETE | DELETE_SELF | MOVE_SELF
BARRIER_KEYS = frozenset({
    "kind", "scope", "barrier", "stage", "producer", "origin", "publication", "counters", "reserved",
})
RECEIPT_KEYS = frozenset({"scope", "barrier", "stage", "producer", "origin_sha256", "journal_sha256"})

def validate_config(value, scope):
    if (
        not isinstance(value, dict) or set(value) != {"version", "scope", "mode"}
        or type(value["version"]) is not int or value["version"] != 1
        or value["scope"] != scope or value["mode"] != MODE
    ):
        raise ChannelError("source journal lacks its exact fixed-directory configuration")


def validate_barrier(value, *, scope, producer, barrier, origin):
    if (
        not isinstance(value, dict) or set(value) != BARRIER_KEYS or value["kind"] != "journal-barrier"
        or value["scope"] != scope or type(value["producer"]) is not int or value["producer"] != producer
        or producer < 1 or type(value["barrier"]) is not int or value["barrier"] != barrier
        or not isinstance(value["stage"], str) or value["stage"] not in {"begin", "end"}
        or value["barrier"] != 2 * producer - (value["stage"] == "begin")
        or value["origin"] != origin or value["stage"] == "begin" and value["publication"] is not None
        or value["stage"] == "end" and not isinstance(value["publication"], dict)
    ):
        raise ChannelError("malformed, repeated or foreign source-journal barrier")
    return value


def validate_resume(value, request):
    if (
        not isinstance(value, dict) or set(value) != RECEIPT_KEYS | {"kind", "limits"}
        or value["kind"] != "journal-resume"
        or any(value[name] != request[name] for name in ("scope", "barrier", "stage", "producer"))
        or any(type(value[name]) is not int for name in ("barrier", "producer"))
        or value["origin_sha256"] != digest(request["origin"])
        or not isinstance(value["journal_sha256"], str)
        or not re.fullmatch("[0-9a-f]{64}", value["journal_sha256"])
    ):
        raise ChannelError("source-journal acknowledgement changed its actual window binding")
    return value


def receipt(reply):
    return {name: reply[name] for name in RECEIPT_KEYS}


def validate_native(value, scope, receipts):
    if (
        not isinstance(value, dict) or set(value) != {"version", "scope", "mode", "receipts", "closed"}
        or type(value["version"]) is not int or value["version"] != 1 or value["scope"] != scope
        or value["mode"] != MODE or value["closed"] is not True
        or value["receipts"] != receipts or len(receipts) % 2
    ):
        raise ChannelError("native source journal lacks its complete actual acknowledgement transcript")


def directory_identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


class FixedDirectoryJournal:
    def __init__(self, session, image):
        self.session, self.image, self.epoch = session, image, session._namespace_epoch
        self.pins, self.watches = {}, {}
        self.identities = dict(image.directories)
        self.fd = -1
        self.pending = None
        self.native_finished = False
        self.invalid = False
        self.closed = False
        self.payload = {
            "version": 1, "scope": None, "mode": MODE, "events": [], "transactions": [],
            "native_receipts": [], "closed": False,
        }
        try:
            try:
                self.libc = ctypes.CDLL(None, use_errno=True)
                self.libc.inotify_init1.argtypes = [ctypes.c_int]
                self.libc.inotify_init1.restype = ctypes.c_int
                self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
                self.libc.inotify_add_watch.restype = ctypes.c_int
            except (AttributeError, OSError) as error:
                raise MakeProbeError("source journal requires the Linux inotify interface") from error
            self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
            if self.fd < 0:
                raise OSError(ctypes.get_errno(), "source journal requires inotify")
            for name, identity in image.directories.items():
                session.budget.remaining()
                descriptor = session._namespace_directory(name)
                self.pins[name] = descriptor
                if directory_identity(os.fstat(descriptor)) != identity:
                    raise MakeProbeError("source-journal directory changed before watch installation")
                watch = self.libc.inotify_add_watch(
                    self.fd, os.fsencode("/proc/self/fd/" + str(descriptor)), MASK | ONLYDIR,
                )
                if watch < 0:
                    raise OSError(ctypes.get_errno(), "source journal cannot bind an original directory watch")
                if watch in self.watches:
                    raise MakeProbeError("source journal has aliased original directory watches")
                session.budget.charge("cache", len(encoded((name, watch, identity))))
                self.watches[watch] = name
            self.idle()
        except BaseException:
            self.close()
            raise

    def validate_view(self):
        session = self.session
        session.budget.remaining()
        if (
            self.closed or self.invalid or get_ident() != session.owner_thread
            or session.snapshot is not self.image.snapshot or session.tree != self.image.tree
            or self.epoch != session._namespace_epoch or session.base is None
        ):
            raise MakeProbeError("source journal lost its original owning view/lifetime")
        for name, descriptor in self.pins.items():
            identity = self.identities[name]
            if directory_identity(os.fstat(descriptor)) != identity:
                raise MakeProbeError("source-journal original directory pin changed")
            actual = session._namespace_directory(name)
            try:
                if directory_identity(os.fstat(actual)) != identity:
                    raise MakeProbeError("source-journal directory path was remapped")
            finally:
                os.close(actual)

    def _kernel_records(self):
        while True:
            self.session.budget.remaining()
            try:
                data = os.read(self.fd, 65536)
            except BlockingIOError:
                break
            if not data:
                raise MakeProbeError("source-journal kernel stream ended prematurely")
            self.session.budget.charge("control", len(data))
            offset = 0
            while offset < len(data):
                if len(data) - offset < 16:
                    raise MakeProbeError("source-journal kernel event header is truncated")
                watch, mask, cookie, size = struct.unpack_from("iIII", data, offset)
                offset += 16
                if size > len(data) - offset or watch not in self.watches:
                    raise MakeProbeError("source-journal kernel event has a foreign watch or truncated name")
                name = data[offset:offset + size]
                offset += size
                if name and (b"\0" not in name or any(name[name.index(0):])):
                    raise MakeProbeError("source-journal kernel name padding is malformed")
                try:
                    name = name.split(b"\0", 1)[0].decode("utf-8", "strict")
                except UnicodeDecodeError as error:
                    raise MakeProbeError("source-journal event name is not UTF-8") from error
                if name and ("/" in name or relative_path(name) != name):
                    raise MakeProbeError("source-journal event escaped its watched parent")
                yield watch, mask, cookie, name

    def _append_event(self, rows, value):
        row = {"seq": len(self.payload["events"]) + len(rows) + 1, **value}
        if row["seq"] > self.session.budget.limits.observation_count:
            self.session.budget.reject("source-journal event count exceeds the existing observation bound")
        self.session.budget.charge("cache", len(encoded(row)))
        rows.append(row)

    def _read_events(self):
        rows = []
        for watch, mask, cookie, name in self._kernel_records():
            if (
                mask & (ISDIR | DELETE_SELF | MOVE_SELF | UNMOUNT | OVERFLOW | IGNORED)
                or mask not in {MODIFY, ATTRIB, CLOSE_WRITE, MOVED_FROM, MOVED_TO, CREATE, DELETE}
                or not name
            ):
                raise MakeProbeError("source journal lost fixed-directory coverage or its kernel event shape")
            self._append_event(rows, {
                "directory": self.watches[watch], "name": name, "mask": mask, "cookie": cookie,
            })
        self.payload["events"].extend(rows)
        return rows

    def idle(self):
        self.validate_view()
        if self.pending is not None or self._read_events():
            raise MakeProbeError("unreceipted source mutation outside a native publication/cleanup window")
        self.validate_view()

    def begin(self, origin, operation, paths):
        self.idle()
        if self.native_finished:
            raise MakeProbeError("native source publication began after completion")
        scope = origin["scope"]
        if self.payload["scope"] not in {None, scope}:
            raise MakeProbeError("source journal borrowed a foreign native invocation")
        self.payload["scope"] = scope
        self._begin(origin["producer"], origin, operation, paths)

    def _begin(self, producer, origin, operation, paths):
        for name in paths:
            relative_path(name)
            required = name if operation == "directory" else PurePosixPath(name).parent.as_posix()
            if required not in self.pins:
                raise MakeProbeError("source journal cannot cover a newly created directory: " + required)
        value = {
            "seq": len(self.payload["transactions"]) + 1, "producer": producer, "origin": origin,
            "operation": operation, "paths": list(paths),
            "event_start": len(self.payload["events"]), "event_end": None,
        }
        self.session.budget.charge("cache", len(encoded(value)))
        self.pending = value

    @staticmethod
    def _created(masks, *, size, replaced=False):
        if replaced:
            if not masks or masks[0] != DELETE:
                return False
            masks = masks[1:]
        return (
            len(masks) >= 2 and masks[0] == CREATE and masks[-1] == CLOSE_WRITE
            and all(mask in {ATTRIB, MODIFY} for mask in masks[1:-1])
            and (size == 0 or MODIFY in masks)
        )

    def end(self, confirmation):
        if self.pending is None:
            raise MakeProbeError("source-journal completion lacks its actual publication window")
        self.validate_view()
        rows = self._read_events()
        self._validate_window(rows, confirmation)
        self._finish_window()

    def _validate_window(self, rows, confirmation):
        pending = self.pending
        grouped = {path: [] for path in pending["paths"]}
        for row in rows:
            path = row["name"] if row["directory"] == "." else row["directory"] + "/" + row["name"]
            if path not in grouped:
                raise MakeProbeError("source publication mutated an unissued path: " + path)
            grouped[path].append(row)
        operation = pending["operation"]
        if operation == "files":
            if "kind" in confirmation or [item["path"] for item in confirmation["outputs"]] != pending["paths"]:
                raise MakeProbeError("source journal differs from the independently checked output set")
            for item in confirmation["outputs"]:
                records = grouped[item["path"]]
                masks = [row["mask"] for row in records]
                valid = not masks if item["effect"] == "retained" else self._created(
                    masks, size=item["size"], replaced=item["effect"] == "replaced",
                )
                if not valid or any(row["cookie"] for row in records):
                    raise MakeProbeError("source publication has missing, extra or incompatible kernel mutations")
        elif operation in {"retire", "cleanup"}:
            if len(rows) != 1 or rows[0]["mask"] != DELETE or rows[0]["cookie"]:
                raise MakeProbeError("source retirement/cleanup lacks its exact kernel deletion")
        elif operation == "transfer":
            if (
                len(rows) != 2 or [row["mask"] for row in rows] != [MOVED_FROM, MOVED_TO]
                or rows[0]["cookie"] == 0 or rows[0]["cookie"] != rows[1]["cookie"]
                or rows[0]["directory"] != rows[1]["directory"]
                or [row["name"] for row in rows] != [PurePosixPath(path).name for path in pending["paths"]]
            ):
                raise MakeProbeError("source transfer lacks its actual paired same-parent kernel move")
        elif operation != "directory" or rows or confirmation["directories"]:
            raise MakeProbeError("source journal cannot accept directory mutation")
    def _finish_window(self):
        self.validate_view()
        pending = self.pending
        pending["event_end"] = len(self.payload["events"])
        self.payload["transactions"].append(pending)
        self.pending = None

    def state_digest(self):
        return digest({
            key: self.payload[key] for key in ("version", "scope", "mode", "events", "transactions")
        } | {"pending": self.pending})

    def complete_native(self, scope, receipts):
        self.idle()
        if self.payload["scope"] not in {None, scope} or self.native_finished:
            raise MakeProbeError("source journal has a foreign or repeated native completion")
        self.payload["scope"] = scope
        if len(receipts) != 2 * len(self.payload["transactions"]):
            raise MakeProbeError("source journal lacks a native barrier for every publication")
        self.session.budget.charge("cache", len(encoded(receipts)))
        self.payload["native_receipts"] = receipts
        self.native_finished = True
        self._complete_ranges()

    def _complete_ranges(self):
        end = 0
        for transaction in self.payload["transactions"]:
            if transaction["event_start"] != end or transaction["event_end"] is None:
                raise MakeProbeError("source journal contains mutations outside its completed windows")
            end = transaction["event_end"]
        if end != len(self.payload["events"]):
            raise MakeProbeError("source journal omitted observed mutations from its completed windows")

    def cleanup(self, path, action):
        if self.session.budget.failed or self.session.budget.closed:
            self.invalid = True
            return action()
        self.idle()
        if not self.native_finished:
            raise MakeProbeError("source cleanup preceded native completion")
        self._begin(None, None, "cleanup", (path,))
        action()
        self.end(None)

    def finish(self):
        if self.session.budget.failed or self.session.budget.closed:
            self.invalid = True
            self.close()
            return
        self.idle()
        if not self.native_finished:
            raise MakeProbeError("source journal ended without native completion")
        self._complete_ranges()
        self.payload["closed"] = True
        descriptor, self.fd = self.fd, -1
        os.close(descriptor)

    def close(self):
        self.closed = True
        descriptors = [self.fd, *self.pins.values()]
        self.fd = -1
        self.pins.clear()
        self.watches.clear()
        finish_cleanup([lambda descriptor=descriptor: os.close(descriptor)
                        for descriptor in descriptors if descriptor >= 0])
