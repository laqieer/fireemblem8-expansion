"""Actual publication pins and verified removal; reservations are not ownership."""

from __future__ import annotations

import ctypes
import errno
import io
import os
from pathlib import PurePosixPath
import re
import secrets
import stat

if __package__:
    from .authority import encoded, relative_path
    from .budget import MakeProbeError
    from .lifecycle import LIBC, finish_cleanup
    from .producer_channel import ChannelError, publication_identity, validate_publication_identity
else:
    from authority import encoded, relative_path
    from budget import MakeProbeError
    from lifecycle import LIBC, finish_cleanup
    from producer_channel import ChannelError, publication_identity, validate_publication_identity


FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOATIME
REQUEST_KEYS = frozenset({"kind", "scope", "producer", "owner", "path", "identity", "parent", "counters", "reserved"})
BINDING_KEYS = frozenset({"scope", "producer", "owner", "path", "identity", "parent"})
LIBC.renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
LIBC.renameat2.restype = ctypes.c_int


def object_identity(info):
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def directory_identity(info):
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def rename_noreplace(source, name, destination, target):
    if LIBC.renameat2(source, os.fsencode(name), destination, os.fsencode(target), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), name)


def validate_config(value, config):
    if (
        not isinstance(value, dict) or set(value) != {"version", "scope"}
        or type(value["version"]) is not int or value["version"] != 1
        or not isinstance(value["scope"], str) or not value["scope"]
        or value["scope"] != config.get("producer_scope") or config["mode"] != "make"
    ):
        raise ChannelError("unbound generated-file cleanup registration")


def validate_request(value, scope, producer):
    if (
        not isinstance(value, dict) or set(value) != REQUEST_KEYS or value["kind"] != "file-opened"
        or value["scope"] != scope or type(value["producer"]) is not int
        or value["producer"] != producer or producer < 1
        or not isinstance(value["owner"], str) or not re.fullmatch("[0-9a-f]{64}", value["owner"])
        or not isinstance(value["parent"], list) or len(value["parent"]) != 3
        or any(type(item) is not int or item < 0 for item in value["parent"])
        or not stat.S_ISDIR(value["parent"][2])
    ):
        raise ChannelError("invalid actual-file registration")
    relative_path(value["path"])
    identity = value["identity"]
    if not isinstance(identity, list) or len(identity) != 7:
        raise ChannelError("file registration lacks its actual descriptor identity")
    validate_publication_identity(identity, stat.S_IMODE(identity[2]), identity[3])
    if identity[3] != 0 or identity[6] != 1:
        raise ChannelError("file registration is not a newly opened exclusive output")


def validate_reply(value, request):
    if (
        not isinstance(value, dict) or set(value) != BINDING_KEYS | {"kind", "limits"}
        or value["kind"] != "file-pinned"
        or any(value[key] != request[key] for key in BINDING_KEYS)
    ):
        raise ChannelError("actual-file registration acknowledgement changed")


class FilePin:
    def __init__(self, descriptor):
        self.handle = io.FileIO(descriptor, "rb", closefd=True)
        self.identity = object_identity(os.fstat(self.handle.fileno()))
        self.paths = set()
        self.approved = False
        self.removed = False

    def info(self):
        info = os.fstat(self.handle.fileno())
        if object_identity(info) != self.identity or not stat.S_ISREG(info.st_mode):
            raise MakeProbeError("owned publication descriptor identity changed")
        return info


class FileReceiver:
    def __init__(self, owner, scope):
        self.owner, self.scope = owner, scope
        self.pending = None

    def receive(self, descriptor):
        if len(self.owner.pins) >= self.owner.session.budget.limits.created_files:
            os.close(descriptor)
            self.owner.hold("descriptor handoff exceeded existing creation admission")
            self.owner.session.budget.reject("file ownership pins exceed creation admission")
        try:
            pin = FilePin(descriptor)
        except (OSError, ValueError) as failure:
            try:
                os.close(descriptor)
            except OSError as error:
                if error.errno != errno.EBADF:
                    failure.add_note("received descriptor close failed: " + str(error))
            self.owner.hold("received file descriptor could not be retained")
            raise
        self.owner.pins.append(pin)
        if self.pending is not None:
            self.owner.hold("multiple file descriptors arrived before their registration")
            raise MakeProbeError("duplicate actual-file descriptor handoff")
        self.pending = pin

    def bind(self, request):
        pin, self.pending = self.pending, None
        if pin is None:
            raise MakeProbeError("actual-file registration has no received descriptor")
        key = self.scope, request["producer"], request["path"]
        wanted = self.owner.authorized.get(key)
        if wanted != request["owner"] or key in self.owner.opened:
            raise MakeProbeError("actual-file registration has no unique issued output")
        info = pin.info()
        if publication_identity(info) != tuple(request["identity"]) or info.st_uid != os.getuid():
            raise MakeProbeError("received publication descriptor differs from its native identity")
        reservation = self.owner.reservations[request["path"]]
        self.owner.pin_parent(reservation)
        if directory_identity(os.fstat(reservation["parent"]))[:3] != tuple(request["parent"]):
            raise MakeProbeError("actual-file registration parent was remapped")
        self.owner.session.budget.charge("cache", len(encoded(request)))
        pin.paths.add(request["path"])
        self.owner.opened[key] = pin
        return pin


class FileOwnership:
    def __init__(self, session):
        self.session, self.tree, self.snapshot = session, session.tree, session.snapshot
        self.root = None
        self.root_fd = -1
        self.root_identity = None
        self.parents = {}
        self.reservations = {}
        self.authorized = {}
        self.opened = {}
        self.current = {}
        self.pins = []
        self.retained = False
        self.reasons = set()
        self.closed = False

    def hold(self, reason):
        self.retained = True
        self.reasons.add(reason)

    def ensure_root(self):
        if self.root is not None:
            return
        root = self.session.base / ("file-claims-" + secrets.token_hex(12))
        self.session.budget.charge("cache", len(encoded(str(root))))
        def acquire():
            root.mkdir(mode=0o700)
            self.root = root
            self.root_fd = os.open(root, FLAGS)
            self.root_identity = directory_identity(os.fstat(self.root_fd))
        finish_cleanup([acquire])

    def reserve(self, name):
        name = relative_path(name)
        if name in self.reservations:
            return
        if len(self.reservations) >= self.session.budget.limits.created_files:
            self.session.budget.reject("publication cleanup reservations exceed creation admission")
        self.ensure_root()
        value = {
            "name": name, "directory": PurePosixPath(name).parent.as_posix(),
            "leaf": PurePosixPath(name).name, "parent": -1, "identity": None,
            "claim": f"{len(self.reservations):016x}.claim",
        }
        self.session.budget.charge("cache", len(encoded(value)))
        self.reservations[name] = value
        try:
            self.pin_parent(value)
        except FileNotFoundError:
            pass

    def pin_parent(self, value):
        if value["parent"] >= 0:
            self.check_parent(value)
            return
        def acquire(name, parent=None, leaf=None):
            descriptor = os.open(self.tree if parent is None else leaf, FLAGS,
                                 **({} if parent is None else {"dir_fd": parent}))
            record = {"fd": descriptor, "identity": directory_identity(os.fstat(descriptor))}
            self.parents[name] = record
            self.session.budget.charge("cache", len(encoded((name, record["identity"]))))
        if "." not in self.parents:
            finish_cleanup([lambda: acquire(".")])
        parent, components = ".", []
        for component in PurePosixPath(value["directory"]).parts:
            if component == ".":
                continue
            components.append(component)
            name = "/".join(components)
            if name not in self.parents:
                finish_cleanup([lambda name=name, parent=parent, component=component:
                                acquire(name, self.parents[parent]["fd"], component)])
            parent = name
        value["parent"] = self.parents[parent]["fd"]
        value["identity"] = self.parents[parent]["identity"]
        self.check_parent(value)

    def check_parent(self, value):
        root = self.parents.get(".")
        if root is None or directory_identity(self.tree.lstat()) != root["identity"]:
            raise MakeProbeError("owned file cleanup source root changed")
        if directory_identity(os.fstat(root["fd"])) != root["identity"]:
            raise MakeProbeError("owned file cleanup source root pin changed")
        parent, components = root, []
        for component in PurePosixPath(value["directory"]).parts:
            if component == ".":
                continue
            components.append(component)
            current = self.parents.get("/".join(components))
            if (
                current is None
                or directory_identity(os.fstat(current["fd"])) != current["identity"]
                or directory_identity(os.stat(component, dir_fd=parent["fd"], follow_symlinks=False)) != current["identity"]
            ):
                raise MakeProbeError("owned file cleanup parent mapping changed")
            parent = current
        if value["parent"] != parent["fd"] or value["identity"] != parent["identity"]:
            raise MakeProbeError("owned file cleanup parent pin differs from its reservation")

    def authorize(self, scope, sequence, owner, outputs):
        for name in outputs:
            self.reserve(name)
            key = scope, sequence, name
            if key in self.authorized:
                raise MakeProbeError("duplicate publication cleanup reservation")
            self.session.budget.charge("cache", len(encoded((key, owner))))
            self.authorized[key] = owner

    def acknowledge(self, scope, sequence, name, outcome):
        if outcome["effect"] == "retained":
            if (scope, sequence, name) in self.opened:
                raise MakeProbeError("retained output supplied a new creation pin")
            pin = self.current.get(name)
        else:
            pin = self.opened.get((scope, sequence, name))
        if pin is None or not pin.approved or publication_identity(pin.info()) != tuple(outcome["identity"]):
            raise MakeProbeError("publication acknowledgement lacks its actual owned descriptor")
        self.current[name] = pin
        return pin

    def prepare_transfer(self, source, destination):
        self.reserve(destination)
        pin = self.current.get(source)
        if pin is None or not pin.approved:
            raise MakeProbeError("file transfer lacks actual cleanup ownership")
        pin.info()
        pin.paths.add(destination)

    def retire(self, source, destination=None):
        pin = self.current.pop(source, None)
        if pin is None or not pin.approved:
            raise MakeProbeError("file retirement lost its actual cleanup pin")
        if destination is None:
            if pin.info().st_nlink != 0:
                raise MakeProbeError("retired owned output retains namespace links")
        else:
            pin.paths.discard(source)
            self.current[destination] = pin
        return pin

    def _restore_claim(self, reservation):
        try:
            rename_noreplace(self.root_fd, reservation["claim"], reservation["parent"], reservation["leaf"])
        except OSError as error:
            self.hold(f"claim retained at {self.root / reservation['claim']}: {error}")

    def remove(self, name, pin):
        reservation = self.reservations[name]
        info = pin.info()
        if (
            self.closed or self.session.tree != self.tree or self.session.snapshot is not self.snapshot
            or self.session.budget.children or self.session.parked_capsules or self.session.pending_commands
            or pin not in self.pins or not pin.approved or name not in pin.paths or info.st_nlink != 1
        ):
            raise MakeProbeError("file cleanup lacks a unique live owned object")
        self.check_parent(reservation)
        if (
            directory_identity(os.fstat(self.root_fd)) != self.root_identity
            or directory_identity(self.root.lstat()) != self.root_identity
        ):
            raise MakeProbeError("owned file claim directory changed")
        actual = os.stat(reservation["leaf"], dir_fd=reservation["parent"], follow_symlinks=False)
        if object_identity(actual) != pin.identity:
            raise MakeProbeError("foreign replacement is not owned file cleanup")

        def claim_and_remove():
            rename_noreplace(reservation["parent"], reservation["leaf"], self.root_fd, reservation["claim"])
            try:
                self.check_parent(reservation)
                claimed = os.stat(reservation["claim"], dir_fd=self.root_fd, follow_symlinks=False)
                if object_identity(claimed) != pin.identity or object_identity(pin.info()) != pin.identity:
                    raise MakeProbeError("atomic file claim captured a replacement, not the owned object")
            except BaseException:
                self._restore_claim(reservation)
                raise
            os.unlink(reservation["claim"], dir_fd=self.root_fd)
            if pin.info().st_nlink != 0:
                raise MakeProbeError("owned file claim did not remove its actual object")
            pin.removed = True
            self.session._namespace_mutation("removed", name)
        finish_cleanup([claim_and_remove])
        return pin

    def validate_removed(self, name, pin):
        if (
            pin not in self.pins or not pin.approved or not pin.removed
            or name not in pin.paths or pin.info().st_nlink != 0
        ):
            raise MakeProbeError("journal cleanup lacks actual pinned-object removal")

    def _cleanup_pin(self, pin, journal):
        if pin.handle.closed:
            if pin.removed:
                return
            raise MakeProbeError("owned publication pin was closed before cleanup")
        if not pin.approved:
            raise MakeProbeError("unacknowledged descriptor retained without cleanup authority")
        if pin.info().st_nlink == 0:
            pin.removed = True
            pin.handle.close()
            return
        for name in sorted(pin.paths):
            reservation = self.reservations[name]
            if reservation["parent"] < 0:
                continue
            self.check_parent(reservation)
            try:
                actual = os.stat(reservation["leaf"], dir_fd=reservation["parent"], follow_symlinks=False)
            except FileNotFoundError:
                continue
            if object_identity(actual) != pin.identity:
                continue
            if journal is None:
                self.remove(name, pin)
            else:
                journal.cleanup(name, self, pin)
            pin.handle.close()
            return
        raise MakeProbeError("displaced owned file retained; no current owned public entry")

    def cleanup(self, journal=None):
        if self.session.budget.children or self.session.parked_capsules or self.session.pending_commands:
            self.hold("file cleanup preceded owned native quiescence")
            raise MakeProbeError("generated-file cleanup retained live native ownership")
        errors = []
        for pin in self.pins:
            try:
                self._cleanup_pin(pin, journal)
            except (OSError, MakeProbeError, ValueError) as error:
                self.hold(str(error))
                errors.append(error)
        for name in self.reservations:
            try:
                if self.reservations[name]["parent"] >= 0:
                    self.check_parent(self.reservations[name])
            except (OSError, MakeProbeError, ValueError) as error:
                self.hold("reserved output parent is uncertain: " + str(error))
                errors.append(error)
                continue
            try:
                (self.tree / name).lstat()
            except FileNotFoundError:
                continue
            except (OSError, MakeProbeError, ValueError) as error:
                self.hold("reserved output identity is uncertain: " + str(error))
                errors.append(error)
            else:
                self.hold("unowned or uncertain output retained at " + str(self.tree / name))
        if self.retained:
            failure = MakeProbeError("generated-file cleanup retained uncertain ownership: " + "; ".join(sorted(self.reasons)))
            failure.retained_file_ownership = self
            if errors:
                raise failure from errors[0]
            raise failure
        try:
            if self.root is not None:
                if directory_identity(self.root.lstat()) != self.root_identity:
                    raise MakeProbeError("file claim arena path was replaced before retirement")
                self.root.rmdir()
                if os.fstat(self.root_fd).st_nlink != 0:
                    raise MakeProbeError("file claim arena retirement lost its pinned object")
            self.release_handles()
        except (OSError, MakeProbeError, ValueError) as error:
            self.hold("file cleanup resource retirement failed: " + str(error))
            failure = MakeProbeError("generated-file cleanup retained uncertain resources: " + str(error))
            failure.retained_file_ownership = self
            raise failure from error

    def release_handles(self):
        """Release retained pins explicitly, without deleting any retained path."""
        def close_parent(value):
            descriptor = value["fd"]
            if descriptor >= 0:
                os.close(descriptor)
                value["fd"] = -1
        def close_root():
            if self.root_fd >= 0:
                os.close(self.root_fd)
                self.root_fd = -1
        finish_cleanup([
            *(pin.handle.close for pin in self.pins),
            *(lambda value=value: close_parent(value) for value in self.parents.values()),
            close_root,
        ])
        self.closed = True
