"""Fresh prewatched parent installation, never general directory relocation."""

from __future__ import annotations

import ctypes
import errno
import os
from pathlib import Path, PurePosixPath
import re
import stat

if __package__:
    from .authority import encoded, relative_path
    from .budget import MakeProbeError
    from .lifecycle import finish_cleanup
    from .producer_channel import ChannelError
    from .source_phases import digest
    from . import source_journal as journal
else:
    from authority import encoded, relative_path
    from budget import MakeProbeError
    from lifecycle import finish_cleanup
    from producer_channel import ChannelError
    from source_phases import digest
    import source_journal as journal


MODE = "prewatched-directories"
FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NOATIME
REQUEST_KEYS = frozenset({
    "kind", "scope", "producer", "sequence", "phase", "path", "stage", "source", "parent",
    "origin", "counters", "reserved",
})
IDENTITY_KEYS = frozenset({"scope", "producer", "sequence", "phase", "path", "stage", "source", "parent"})


def identity(info):
    return info.st_dev, info.st_ino, info.st_mode


def checked_identity(value):
    if (
        not isinstance(value, (list, tuple)) or len(value) != 3
        or any(type(item) is not int or not 0 <= item < 1 << 64 for item in value)
        or not stat.S_ISDIR(value[2]) or value[2] & 0o7000
    ):
        raise ChannelError("invalid prewatched directory identity")
    return tuple(value)


def validate_config(value, config):
    if (
        not isinstance(value, dict) or set(value) != {"version", "scope", "mode", "staging"}
        or type(value["version"]) is not int or value["version"] != 2
        or value["scope"] != config.get("producer_scope") or value["mode"] != MODE
        or not isinstance(value["staging"], dict) or set(value["staging"]) != {"path", "identity"}
        or value["staging"]["path"] != config["root"] + "-source-directories"
    ):
        raise ChannelError("prewatched directories lack their exact native private staging scope")
    wanted = checked_identity(value["staging"]["identity"])
    if stat.S_IMODE(wanted[2]) != 0o700:
        raise ChannelError("directory staging parent is not private")
    return value["staging"]


def validate_request(value, *, scope, producer, origin):
    if (
        not isinstance(value, dict) or set(value) != REQUEST_KEYS or value["kind"] != "directory-handoff"
        or value["scope"] != scope or type(value["producer"]) is not int or value["producer"] != producer
        or type(value["sequence"]) is not int or value["sequence"] < 1
        or value["stage"] != f"{value['sequence']:016x}"
        or not isinstance(value["phase"], str) or value["phase"] not in {"watch", "installed"}
        or value["origin"] != origin
    ):
        raise ChannelError("malformed or foreign prewatched directory handoff")
    relative_path(value["path"])
    checked_identity(value["source"])
    checked_identity(value["parent"])
    return value


def validate_reply(value, request):
    if (
        not isinstance(value, dict)
        or set(value) != IDENTITY_KEYS | {"kind", "origin_sha256", "journal_sha256", "limits"}
        or value["kind"] != "directory-ready"
        or any(value[name] != request[name] for name in IDENTITY_KEYS)
        or any(type(value[name]) is not int for name in ("producer", "sequence"))
        or value["origin_sha256"] != digest(request["origin"])
        or not isinstance(value["journal_sha256"], str)
        or not re.fullmatch("[0-9a-f]{64}", value["journal_sha256"])
    ):
        raise ChannelError("prewatched directory acknowledgement changed its actual lease")
    return value


def native_receipt(request, watch_digest, installed_digest):
    return {
        **{key: request[key] for key in ("sequence", "producer", "path", "stage", "source", "parent")},
        "watch_sha256": watch_digest, "installed_sha256": installed_digest,
    }


def validate_native(value, scope, receipts, directories):
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "scope", "mode", "receipts", "directories", "closed"}
        or type(value["version"]) is not int or value["version"] != 2
        or value["scope"] != scope or value["mode"] != MODE or value["closed"] is not True
        or value["receipts"] != receipts or value["directories"] != directories or len(receipts) % 2
    ):
        raise ChannelError("native prewatched directory transcript is incomplete or changed")


def no_default_acl(descriptor):
    if os.fstat(descriptor).st_mode & stat.S_ISGID:
        raise ChannelError("prewatched parent has unsupported SGID inheritance")
    try:
        os.getxattr(descriptor, "system.posix_acl_default")
    except OSError as error:
        if error.errno not in {errno.ENODATA, errno.EOPNOTSUPP}:
            raise
    else:
        raise ChannelError("prewatched parent has unsupported default ACL inheritance")


def require_empty(descriptor):
    with os.scandir(descriptor) as entries:
        if next(entries, None) is not None:
            raise ChannelError("prewatched directory is not empty")


class NativeDirectoryInstalls:
    def __init__(self, policy, config):
        self.policy = policy
        self.staging = validate_config(config, policy.config)
        self.fd = os.open(self.staging["path"], FLAGS)
        self.sequence = 0
        self.receipts = []
        self.paths = set()
        self.allowed = set()
        self.producer = None
        self.origin = None
        self.exchange = None
        try:
            self.check_staging()
            require_empty(self.fd)
            no_default_acl(self.fd)
            self.libc = ctypes.CDLL(None, use_errno=True)
            self.libc.renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            self.libc.renameat2.restype = ctypes.c_int
        except BaseException:
            self.close()
            raise

    def check_staging(self):
        wanted = tuple(self.staging["identity"])
        if identity(os.fstat(self.fd)) != wanted:
            raise ChannelError("prewatched private staging pin changed")
        actual = os.open(self.staging["path"], FLAGS)
        try:
            if identity(os.fstat(actual)) != wanted:
                raise ChannelError("prewatched private staging path was replaced")
        finally:
            os.close(actual)

    def begin(self, producer, origin, paths, directory=False):
        if self.producer is not None:
            raise ChannelError("overlapping prewatched directory producers")
        allowed = set()
        for name in paths:
            relative_path(name)
            allowed.update(parent.as_posix() for parent in PurePosixPath(name).parents if parent.as_posix() != ".")
            if directory:
                allowed.add(name)
        self.producer, self.origin, self.allowed = producer, origin, allowed

    def end(self):
        self.producer = self.origin = None
        self.allowed.clear()

    def check_parent(self, path, descriptor):
        view = next(item["source"] for item in self.policy.config["mounts"] if item["target"] == "/repo")
        actual = os.open(view, FLAGS)
        try:
            for part in PurePosixPath(path).parent.parts:
                if part == ".":
                    continue
                following = os.open(part, FLAGS, dir_fd=actual)
                os.close(actual)
                actual = following
            if identity(os.fstat(actual)) != identity(os.fstat(descriptor)):
                raise ChannelError("prewatched public parent mapping changed")
            no_default_acl(actual)
        finally:
            os.close(actual)

    def create(self, path, parent):
        if (
            self.producer is None or self.producer != self.policy.producer_issued
            or self.origin is None or self.exchange is None or path not in self.allowed or path in self.paths
        ):
            raise ChannelError("directory install lacks its actual authorized producer/path")
        self.check_staging()
        self.check_parent(path, parent)
        if os.fstat(parent).st_dev != os.fstat(self.fd).st_dev:
            raise ChannelError("prewatched directory handoff is not same-filesystem")
        self.sequence += 1
        stage = f"{self.sequence:016x}"
        self.policy.reserve_creation()
        os.mkdir(stage, 0o755, dir_fd=self.fd)
        descriptor = os.open(stage, FLAGS, dir_fd=self.fd)
        try:
            if self.policy.config["sudo_drop"]:
                os.fchown(descriptor, self.policy.config["runner_uid"], self.policy.config["runner_gid"])
            source = identity(os.fstat(descriptor))
            checked_identity(source)
            require_empty(descriptor)
            request = {
                "scope": self.policy.config["producer_scope"], "producer": self.producer,
                "sequence": self.sequence, "path": path, "stage": stage,
                "source": list(source), "parent": list(identity(os.fstat(parent))), "origin": self.origin,
            }
            watched = self.exchange("watch", request)
            self.check_staging()
            self.check_parent(path, parent)
            if identity(os.stat(stage, dir_fd=self.fd, follow_symlinks=False)) != source:
                raise ChannelError("prewatched source name differs from its original directory pin")
            if identity(os.fstat(descriptor)) != source:
                raise ChannelError("prewatched source inode changed")
            require_empty(descriptor)
            self.paths.add(path)
            if self.libc.renameat2(self.fd, os.fsencode(stage), parent, os.fsencode(PurePosixPath(path).name), 1):
                error = ctypes.get_errno()
                raise OSError(error, "atomic prewatched directory install refused")
            if (
                identity(os.fstat(descriptor)) != source
                or identity(os.stat(PurePosixPath(path).name, dir_fd=parent, follow_symlinks=False)) != source
            ):
                raise ChannelError("prewatched installed directory differs from the issued source")
            try:
                os.stat(stage, dir_fd=self.fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ChannelError("installed directory retained its private source name")
            require_empty(descriptor)
            installed = self.exchange("installed", request)
            value = native_receipt(request, watched, installed)
            self.policy.charge_metadata(len(encoded(value)))
            self.receipts.append(value)
            result, descriptor = descriptor, -1
            return result
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def close(self):
        descriptor, self.fd = self.fd, -1
        if descriptor >= 0:
            os.close(descriptor)


class PrewatchedDirectoryJournal(journal.FixedDirectoryJournal):
    def __init__(self, session, image, root):
        self.staging_root = Path(str(root) + "-source-directories")
        self.stage_fd = -1
        self.stage_identity = None
        self.stage_watch = None
        self.private_watches = {}
        self.private_pins = {}
        self.directory_pending = None
        self.created_directories = set()
        self.retired = set()
        self.stage_removed = False
        super().__init__(session, image)
        try:
            self.payload.update(version=2, mode=MODE, directories=[])
            self.staging_root.mkdir(mode=0o700)
            self.stage_fd = os.open(self.staging_root, FLAGS)
            self.stage_identity = journal.directory_identity(os.fstat(self.stage_fd))
            if stat.S_IMODE(self.stage_identity[2]) != 0o700 or self.stage_identity[3] != os.getuid():
                raise MakeProbeError("prewatched staging parent is not privately owned")
            no_default_acl(self.stage_fd)
            require_empty(self.stage_fd)
            self.stage_watch = self._watch(self.stage_fd, "")
            self.idle()
        except BaseException:
            self.close()
            raise

    def config(self, scope):
        return {
            "version": 2, "scope": scope, "mode": MODE,
            "staging": {"path": str(self.staging_root), "identity": list(self.stage_identity[:3])},
        }

    def _watch(self, descriptor, stage):
        watch = self.libc.inotify_add_watch(
            self.fd, os.fsencode("/proc/self/fd/" + str(descriptor)), journal.MASK | journal.ONLYDIR,
        )
        if watch < 0:
            raise OSError(ctypes.get_errno(), "prewatched directory watch failed")
        if watch in self.watches:
            raise MakeProbeError("prewatched directory reused an original watch")
        self.session.budget.charge("cache", len(encoded((watch, stage, identity(os.fstat(descriptor))))))
        self.watches[watch] = stage
        self.private_watches[watch] = stage
        return watch

    def validate_view(self):
        session = self.session
        session.budget.remaining()
        if (
            self.closed or self.invalid or session.snapshot is not self.image.snapshot
            or session.tree != self.image.tree or self.epoch != session._namespace_epoch
            or session.base is None or journal.get_ident() != session.owner_thread
        ):
            raise MakeProbeError("prewatched journal lost its original view/lifetime")
        for name, descriptor in self.pins.items():
            info = os.fstat(descriptor)
            if journal.directory_identity(info) != self.identities[name]:
                raise MakeProbeError("prewatched source directory pin changed")
            if name in self.retired:
                if info.st_nlink != 0:
                    raise MakeProbeError("retired prewatched directory regained namespace links")
                continue
            actual = session._namespace_directory(name)
            try:
                if journal.directory_identity(os.fstat(actual)) != self.identities[name]:
                    raise MakeProbeError("prewatched source parent path was remapped")
            finally:
                os.close(actual)
        if self.stage_fd >= 0:
            if journal.directory_identity(os.fstat(self.stage_fd)) != self.stage_identity:
                raise MakeProbeError("prewatched private staging pin changed")
            actual = os.open(self.staging_root, FLAGS)
            try:
                if journal.directory_identity(os.fstat(actual)) != self.stage_identity:
                    raise MakeProbeError("prewatched private staging path was remapped")
            finally:
                os.close(actual)

    def _read_events(self):
        rows = []
        allowed = {
            journal.MODIFY, journal.ATTRIB, journal.CLOSE_WRITE, journal.MOVED_FROM,
            journal.MOVED_TO, journal.CREATE, journal.DELETE, journal.MOVE_SELF,
            journal.ATTRIB | journal.ISDIR, journal.MOVED_FROM | journal.ISDIR,
            journal.MOVED_TO | journal.ISDIR, journal.CREATE | journal.ISDIR, journal.DELETE | journal.ISDIR,
        }
        for watch, mask, cookie, name in self._kernel_records():
            if mask not in allowed or not name and mask != journal.MOVE_SELF:
                raise MakeProbeError("prewatched journal lost kernel coverage or event shape")
            self._append_event(rows, {
                "space": "stage" if watch in self.private_watches else "source",
                "directory": self.watches[watch], "name": name, "mask": mask, "cookie": cookie,
            })
        self.payload["events"].extend(rows)
        return rows

    def _begin(self, producer, origin, operation, paths):
        for path in paths:
            relative_path(path)
        value = {
            "seq": len(self.payload["transactions"]) + 1, "producer": producer, "origin": origin,
            "operation": operation, "paths": list(paths), "directories": [],
            "event_start": len(self.payload["events"]), "event_end": None,
        }
        self.session.budget.charge("cache", len(encoded(value)))
        self.pending = value

    def _allowed_parents(self):
        allowed = set()
        if self.pending["operation"] not in {"directory", "files"}:
            return allowed
        for path in self.pending["paths"]:
            allowed.update(parent.as_posix() for parent in PurePosixPath(path).parents if parent.as_posix() != ".")
            if self.pending["operation"] == "directory":
                allowed.add(path)
        return allowed

    def directory_handoff(self, request):
        self.validate_view()
        if (
            self.pending is None or self.native_finished or request["producer"] != self.pending["producer"]
            or request["origin"] != self.pending["origin"] or request["path"] not in self._allowed_parents()
        ):
            raise MakeProbeError("directory handoff is outside its actual registered publication")
        path, stage, sequence = request["path"], request["stage"], request["sequence"]
        parent = PurePosixPath(path).parent.as_posix()
        if parent not in self.pins or parent in self.retired or identity(os.fstat(self.pins[parent])) != tuple(request["parent"]):
            raise MakeProbeError("directory handoff lost its watched actual public parent")
        no_default_acl(self.pins[parent])
        if request["phase"] == "watch":
            if (
                self.directory_pending is not None or sequence != len(self.payload["directories"]) + 1
                or path in self.pins or path in self.created_directories
            ):
                raise MakeProbeError("repeated or foreign prewatched directory lease")
            descriptor = os.open(stage, FLAGS, dir_fd=self.stage_fd)
            self.private_pins[stage] = descriptor
            info = os.fstat(descriptor)
            if identity(info) != tuple(request["source"]) or info.st_uid != os.getuid():
                raise MakeProbeError("private directory stage differs from its native/host ownership")
            require_empty(descriptor)
            watch = self._watch(descriptor, stage)
            rows = self._read_events()
            private = [row for row in rows if row["space"] == "stage"]
            if (
                not private or private[0]["mask"] != journal.CREATE | journal.ISDIR
                or any(row["directory"] != "" or row["name"] != stage or row["cookie"]
                       or row["mask"] not in {journal.CREATE | journal.ISDIR, journal.ATTRIB | journal.ISDIR}
                       for row in private)
                or sum(row["mask"] == journal.CREATE | journal.ISDIR for row in private) != 1
            ):
                raise MakeProbeError("private stage creation has unknown or unobserved activity")
            require_empty(descriptor)
            if journal.directory_identity(os.fstat(descriptor)) != journal.directory_identity(info):
                raise MakeProbeError("private stage changed while its original watch was installed")
            self.directory_pending = {
                "request": {key: request[key] for key in IDENTITY_KEYS | {"origin"}},
                "watch": watch, "identity": journal.directory_identity(info), "links": info.st_nlink,
                "watch_events": [row["seq"] - 1 for row in private],
            }
            value = self.state_digest()
            self.directory_pending["watch_sha256"] = value
            return value
        lease = self.directory_pending
        if lease is None or any(
            request[key] != lease["request"][key] for key in IDENTITY_KEYS | {"origin"} if key != "phase"
        ):
            raise MakeProbeError("installed directory has no matching watched private lease")
        descriptor = self.private_pins[stage]
        info = os.fstat(descriptor)
        if journal.directory_identity(info) != lease["identity"] or info.st_nlink != lease["links"]:
            raise MakeProbeError("installed directory changed its original inode/ownership")
        actual = self.session._namespace_directory(path)
        try:
            if journal.directory_identity(os.fstat(actual)) != lease["identity"]:
                raise MakeProbeError("installed public path differs from the prewatched inode")
            require_empty(actual)
        finally:
            os.close(actual)
        try:
            os.stat(stage, dir_fd=self.stage_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise MakeProbeError("installed directory retained a private source name")
        rows = self._read_events()
        if (
            len(rows) != 3
            or (rows[0]["space"], rows[0]["directory"], rows[0]["name"], rows[0]["mask"])
            != ("stage", "", stage, journal.MOVED_FROM | journal.ISDIR)
            or (rows[1]["space"], rows[1]["directory"], rows[1]["name"], rows[1]["mask"])
            != ("source", parent, PurePosixPath(path).name, journal.MOVED_TO | journal.ISDIR)
            or (rows[2]["space"], rows[2]["directory"], rows[2]["name"], rows[2]["mask"])
            != ("stage", stage, "", journal.MOVE_SELF)
            or not rows[0]["cookie"] or rows[0]["cookie"] != rows[1]["cookie"] or rows[2]["cookie"]
        ):
            raise MakeProbeError("prewatched handoff lacks its exact kernel move or has descendant activity")
        self.pins[path] = self.private_pins.pop(stage)
        self.identities[path] = lease["identity"]
        self.watches[lease["watch"]] = path
        del self.private_watches[lease["watch"]]
        self.created_directories.add(path)
        record = {
            **{key: request[key] for key in ("sequence", "producer", "path", "stage", "source", "parent")},
            "watch_events": lease["watch_events"], "installed_events": [row["seq"] - 1 for row in rows],
            "watch_sha256": lease["watch_sha256"], "installed_sha256": "",
        }
        self.directory_pending = None
        self.payload["directories"].append(record)
        self.pending["directories"].append(sequence)
        value = self.state_digest()
        record["installed_sha256"] = value
        self.session.budget.charge("cache", len(encoded(record)))
        return value

    def state_digest(self):
        pending_directory = None if self.directory_pending is None else {
            key: value for key, value in self.directory_pending.items() if key != "watch_sha256"
        }
        return digest({
            key: self.payload[key] for key in ("version", "scope", "mode", "events", "transactions")
        } | {
            "pending": self.pending, "directory_pending": pending_directory,
            "directories": [
                {key: value for key, value in row.items() if key not in {"watch_sha256", "installed_sha256"}}
                for row in self.payload.get("directories", [])
            ],
        })

    def end(self, confirmation):
        if self.pending is None or self.directory_pending is not None:
            raise MakeProbeError("prewatched publication ended with an incomplete directory handoff")
        self.validate_view()
        self._read_events()
        rows = self.payload["events"][self.pending["event_start"]:]
        directories = [self.payload["directories"][number - 1] for number in self.pending["directories"]]
        covered = {index for row in directories for key in ("watch_events", "installed_events") for index in row[key]}
        remaining = [row for row in rows if row["seq"] - 1 not in covered]
        if any(row["space"] != "source" for row in remaining):
            raise MakeProbeError("publication left unbound private directory activity")
        if self.pending["operation"] == "directory":
            expected = [[row["path"], *row["source"]] for row in directories]
            if remaining or confirmation["directories"] != expected:
                raise MakeProbeError("directory effect differs from actual prewatched installations")
        elif self.pending["operation"] == "cleanup-directory":
            path, = self.pending["paths"]
            if (
                len(remaining) != 1 or remaining[0]["mask"] != journal.DELETE | journal.ISDIR
                or remaining[0]["directory"] != PurePosixPath(path).parent.as_posix()
                or remaining[0]["name"] != PurePosixPath(path).name or remaining[0]["cookie"]
            ):
                raise MakeProbeError("directory cleanup lacks its actual public namespace removal")
        else:
            self._validate_window(remaining, confirmation)
        self._finish_window()

    def directory_receipts(self):
        return [
            {key: value for key, value in row.items() if key not in {"watch_events", "installed_events"}}
            for row in self.payload["directories"]
        ]

    def complete_native(self, scope, receipts, directories):
        if self.directory_pending is not None or self.directory_receipts() != directories or self.private_pins:
            raise MakeProbeError("prewatched native directory transcript is incomplete or changed")
        require_empty(self.stage_fd)
        super().complete_native(scope, receipts)

    def cleanup_directory(self, path, action):
        if self.session.budget.failed or self.session.budget.closed:
            self.invalid = True
            return action()
        self.idle()
        if not self.native_finished or path not in self.created_directories or path in self.retired:
            raise MakeProbeError("directory cleanup is not an owned completed handoff")
        require_empty(self.pins[path])
        self._begin(None, None, "cleanup-directory", (path,))
        action()
        if os.fstat(self.pins[path]).st_nlink != 0:
            raise MakeProbeError("directory cleanup did not retire its actual original inode")
        self.retired.add(path)
        self.end(None)

    def finish(self):
        super().finish()
        if not self.invalid and not self.closed:
            require_empty(self.stage_fd)
            self.staging_root.rmdir()
            descriptor, self.stage_fd = self.stage_fd, -1
            os.close(descriptor)
            self.stage_removed = True

    def close(self):
        descriptors = [self.stage_fd, *self.private_pins.values()]
        self.stage_fd = -1
        self.private_pins.clear()
        finish_cleanup([
            super().close,
            *(lambda descriptor=descriptor: os.close(descriptor) for descriptor in descriptors if descriptor >= 0),
        ])
