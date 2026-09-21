"""Bounded, private per-invocation supervisor/driver transport; never guest I/O."""

from __future__ import annotations

import os
import hashlib
import json
import selectors
import socket
import stat
import struct
import time
import re

if __package__:
    from .lifecycle import finish_cleanup
else:
    from lifecycle import finish_cleanup


class ChannelError(RuntimeError):
    pass


PUBLICATION_POLICIES = ("replace", "if-content-changed", "if-content-changed-preserve-mode")
PUBLICATION_MAGIC = b"VOGEN2\0\0"

STDERR_ROOT_FLAGS = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
STDERR_NULL_FLAGS = os.O_WRONLY | os.O_CLOEXEC
STDERR_OPEN_HOW = struct.pack("<QQQ", STDERR_NULL_FLAGS, 0, 0x04 | 0x08)
STDERR_LARGEFILE = 0x8000
STDERR_BINDING_FIELDS = (
    "root", "mode", "argv", "environment", "code", "sources", "enumerations",
    "executables", "mounts",
)


def stderr_effects(value, argc):
    if (
        type(value) not in (tuple, list) or type(argc) is not int
        or not 1 <= argc <= 1024 or len(value) + argc > 1024
        or any(type(item) is not str or item not in {"stdout", "null"} for item in value)
    ):
        raise ChannelError("invalid or excessive ordered stderr effects")
    return value


def stderr_json_size(value, reserve=None, _depth=0):
    """Pre-admit the exact ASCII JSON wire before encoding the closed records."""
    if _depth > 8:
        raise ChannelError("stderr record exceeds its closed nesting")
    def account(size):
        if reserve is not None:
            reserve(size)
        return size
    kind = type(value)
    if value is None:
        return account(4)
    if kind is bool:
        return account(4 if value else 5)
    if kind is int:
        if not -(1 << 64) < value < 1 << 64:
            raise ChannelError("stderr record integer is outside its ABI")
        return account(len(str(value)))
    if kind is str:
        total, pending = account(2), 0
        for index, character in enumerate(value):
            number = ord(character)
            pending += (
                2 if character in '"\\\b\f\n\r\t' else
                6 if number < 32 or 128 <= number <= 65535 else
                12 if number > 65535 else 1
            )
            if index % 1024 == 1023:
                total += account(pending)
                pending = 0
        return total + account(pending)
    if kind in (tuple, list):
        return account(2 + max(0, len(value) - 1)) + sum(
            stderr_json_size(item, reserve, _depth + 1) for item in value
        )
    if kind is dict and all(type(key) is str for key in value):
        return account(2 + max(0, len(value) - 1) + len(value)) + sum(
            stderr_json_size(key, reserve, _depth + 1) + stderr_json_size(item, reserve, _depth + 1)
            for key, item in value.items()
        )
    raise ChannelError("stderr record is outside the closed JSON representation")


def stderr_encoded(value, reserve):
    size = stderr_json_size(value, reserve)
    reserve(size)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def stderr_launch_binding(config, value, reserve):
    data = {name: config[name] for name in STDERR_BINDING_FIELDS}
    data["stderr"] = {name: value[name] for name in ("scope", "nonce", "context", "effects")}
    return hashlib.sha256(stderr_encoded(data, reserve)).hexdigest()


def validate_stderr_inputs(config):
    def strings(items, count, size):
        return (
            type(items) is list and len(items) <= count
            and all(type(item) is str and "\0" not in item and len(item) <= size for item in items)
        )
    if (
        type(config) is not dict or any(name not in config for name in STDERR_BINDING_FIELDS)
        or type(config["root"]) is not str or not config["root"].startswith("/")
        or len(config["root"]) > 4096 or os.path.normpath(config["root"]) != config["root"]
        or not strings(config["argv"], 1024, 65536)
        or any(not strings(config[name], 4096, 4096) for name in ("code", "sources", "enumerations"))
        or type(config["environment"]) is not dict or len(config["environment"]) > 1024
        or any(type(name) is not str or not name or "=" in name or "\0" in name
               or type(content) is not str or "\0" in content or len(name) + len(content) > 65536
               for name, content in config["environment"].items())
        or type(config["mounts"]) is not list or len(config["mounts"]) != 4
        or any(type(row) is not dict or set(row) != {"source", "target", "writable", "executable"}
               or type(row["source"]) is not str or not row["source"].startswith("/")
               or len(row["source"]) > 4096 or type(row["target"]) is not str
               or type(row["writable"]) is not bool or type(row["executable"]) is not bool
               for row in config["mounts"])
        or [(row["target"], row["writable"], row["executable"]) for row in config["mounts"]]
        != [("/repo", False, False), ("/usr", False, True), ("/work", True, False), ("/dev/null", True, False)]
        or config["mounts"][1]["source"] != "/usr" or config["mounts"][3]["source"] != "/dev/null"
    ):
        raise ChannelError("stderr launch has malformed command inputs or mounts")


def validate_stderr_launch(value, config, reserve):
    validate_stderr_inputs(config)
    if (
        type(value) is not dict or set(value) != {"version", "scope", "nonce", "context", "effects", "binding"}
        or type(value["version"]) is not int or value["version"] != 1
        or type(value["scope"]) is not str
        or value["scope"] != config["root"].rstrip("/").rsplit("/", 1)[-1]
        or not re.fullmatch(r"[A-Za-z0-9_-]+", value["scope"])
        or type(value["nonce"]) is not str or not re.fullmatch("[0-9a-f]{32}", value["nonce"])
        or type(value["binding"]) is not str or not re.fullmatch("[0-9a-f]{64}", value["binding"])
        or type(value["context"]) is not str or not re.fullmatch("[0-9a-f]{64}", value["context"])
        or type(value["effects"]) is not list or not value["effects"]
        or config["mode"] != "command" or config["executables"] != ["/usr/bin/python3"]
        or config["argv"][:4] != ["/usr/bin/python3", "-I", "-S", "-B"]
        or any(config.get(name) for name in (
            "dependency", "metadata_validation", "private_install", "header_runtime",
            "toolchain_runtime", "producer_endpoint", "file_cleanup", "observe_recipe_dispatch",
        ))
    ):
        raise ChannelError("unbound or incompatible stderr launch")
    stderr_effects(value["effects"], len(config["argv"]))
    if value["binding"] != stderr_launch_binding(config, value, reserve):
        raise ChannelError("stderr launch changed after issuance")
    return value


def stderr_operations(effects):
    for effect in effects:
        if effect == "stdout":
            yield "dup-stdout"
        else:
            yield from ("open-dev", "open-null", "dup-null", "close-null", "close-dev")
    if "null" in effects:
        yield "close-root"


def stderr_fd_state(value):
    if (
        type(value) not in (tuple, list) or len(value) != 9
        or any(type(item) is not int or not 0 <= item < 1 << 64 for item in value)
        or not value[1] or not value[7]
    ):
        raise ChannelError("invalid stderr kernel descriptor identity")
    return tuple(value)


def validate_stderr_receipt(value, launch):
    if (
        type(value) is not dict or set(value) != {
            "version", "scope", "nonce", "context", "binding", "effects", "pid", "credentials",
            "references", "initial", "operations", "final", "complete",
        }
        or type(value["version"]) is not int or value["version"] != 1
        or any(value[name] != launch[name] for name in ("scope", "nonce", "context", "binding", "effects"))
        or value["complete"] is not True or type(value["pid"]) is not int or not 0 < value["pid"] < 1 << 31
        or type(value["references"]) is not list or len(value["references"]) != 4
        or type(value["initial"]) is not list or type(value["final"]) is not list
        or type(value["operations"]) is not list
    ):
        raise ChannelError("incomplete or unbound stderr setup receipt")
    principal = value["credentials"]
    if (
        type(principal) is not dict or set(principal) != {"uid", "gid", "groups", "caps", "nnp"}
        or any(type(principal[name]) is not list for name in ("uid", "gid", "groups", "caps"))
        or len(principal["uid"]) != 4 or len(principal["gid"]) != 4
        or len(principal["groups"]) > 1024 or principal["caps"] != [0] * 5
        or type(principal["nnp"]) is not int or principal["nnp"] != 1
        or any(type(number) is not int or not 0 <= number < 1 << 32
               for name in ("uid", "gid", "groups", "caps") for number in principal[name])
    ):
        raise ChannelError("stderr setup lacks its permanently dropped caller")
    root, dev, null, mounted = map(stderr_fd_state, value["references"])
    if (
        not stat.S_ISDIR(root[2]) or not stat.S_ISDIR(dev[2])
        or root[6] != STDERR_ROOT_FLAGS or dev[6] != STDERR_ROOT_FLAGS
        or not stat.S_ISCHR(null[2]) or null[5] != os.makedev(1, 3)
        or null[:6] != mounted[:6] or null[8] & os.ST_NODEV
        or mounted[8] & (os.ST_NODEV | os.ST_NOSUID | os.ST_NOEXEC)
        != os.ST_NODEV | os.ST_NOSUID | os.ST_NOEXEC
    ):
        raise ChannelError("stderr setup changed its null object or mount boundary")

    def descriptors(rows, expected):
        if (
            len(rows) != len(expected)
            or any(type(row) is not list or len(row) != 2 for row in rows)
            or [row[0] for row in rows] != expected
            or any(type(row[0]) is not int for row in rows)
        ):
            raise ChannelError("stderr setup has an unexpected descriptor set")
        return {row[0]: stderr_fd_state(row[1]) for row in rows}

    initial = descriptors(value["initial"], [0, 1, 2, 3] if "null" in launch["effects"] else [0, 1, 2])
    final = descriptors(value["final"], [0, 1, 2])
    if (
        initial[0][:6] != null[:6] or initial[0][6] != os.O_RDWR | STDERR_LARGEFILE
        or not all(stat.S_ISFIFO(initial[fd][2]) and initial[fd][6] == os.O_WRONLY for fd in (1, 2))
        or initial[1][:6] == initial[2][:6]
        or initial[0] != final[0] or initial[1] != final[1]
        or 3 in initial and initial[3] != root
    ):
        raise ChannelError("stderr setup lost its original standard streams")
    expected = tuple(stderr_operations(launch["effects"]))
    if len(value["operations"]) != len(expected):
        raise ChannelError("stderr setup omitted or repeated an operation")
    opened = {3: root} if 3 in initial else {}
    selected = initial[2]
    directory = leaf = None
    for expected_name, row in zip(expected, value["operations"]):
        if (
            type(row) is not list or len(row) != 4 or row[0] != expected_name
            or any(type(row[index]) is not int for index in (1, 2))
        ):
            raise ChannelError("stderr setup changed its ordered operation")
        name, descriptor, result, actual = row
        if name.startswith("open-"):
            wanted = root if name == "open-dev" else dev
            if descriptor not in opened or opened[descriptor][:6] != wanted[:6] or not 3 <= result < 128 or result in opened:
                raise ChannelError("stderr setup opened through a foreign descriptor")
            actual = stderr_fd_state(actual)
            reference = dev if name == "open-dev" else null
            flags = STDERR_ROOT_FLAGS if name == "open-dev" else os.O_WRONLY | STDERR_LARGEFILE | os.O_CLOEXEC
            if actual[:6] != reference[:6] or actual[6] != flags or actual[7:] != reference[7:]:
                raise ChannelError("stderr setup opened a different object or status mode")
            opened[result] = actual
            if name == "open-dev":
                directory = result
            else:
                leaf = result
        elif name.startswith("dup-"):
            source = initial[1] if name == "dup-stdout" else opened.get(leaf)
            if descriptor != (1 if name == "dup-stdout" else leaf) or result != 2 or source is None:
                raise ChannelError("stderr setup duplicated a foreign descriptor")
            actual = stderr_fd_state(actual)
            if actual != (*source[:6], source[6] & ~os.O_CLOEXEC, *source[7:]):
                raise ChannelError("stderr setup lost its actual descriptor alias or flags")
            selected = actual
        else:
            wanted = {"close-null": leaf, "close-dev": directory, "close-root": 3}[name]
            if descriptor != wanted or descriptor not in opened or result != 0 or actual is not None:
                raise ChannelError("stderr setup did not retire its actual descriptor")
            del opened[descriptor]
    if opened or final[2] != selected:
        raise ChannelError("stderr setup retained authority or substituted final stderr")
    return value


def publication_identity(info):
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns, info.st_nlink,
    )


def validate_publication_identity(value, mode, size):
    if (
        not isinstance(value, (list, tuple)) or len(value) != 7
        or any(type(item) is not int for item in value)
        or any(not 0 <= value[index] < 1 << 64 for index in (0, 1, 2, 3, 6))
        or any(not -(1 << 63) <= value[index] < 1 << 63 for index in (4, 5))
        or value[2] != stat.S_IFREG | mode or value[3] != size or value[6] < 1
    ):
        raise ChannelError("invalid effective publication object identity")
    return tuple(value)


def validate_publication_confirmation(value, *, count_limit, file_limit):
    if isinstance(value, dict) and value.get("kind") == "filesystem":
        if __package__:
            from .header_effects import validate_confirmation
        else:
            from header_effects import validate_confirmation
        return validate_confirmation(value, count_limit=count_limit, file_limit=file_limit)
    if (
        not isinstance(value, dict) or set(value) != {"slot", "owner", "policy", "outputs"}
        or type(value["slot"]) is not int or value["slot"] < 0
        or not isinstance(value["owner"], str) or not re.fullmatch("[0-9a-f]{64}", value["owner"])
        or type(value["policy"]) is not str or value["policy"] not in PUBLICATION_POLICIES
        or not isinstance(value["outputs"], list) or len(value["outputs"]) > count_limit
        or value["policy"] != "replace" and not value["outputs"]
    ):
        raise ChannelError("malformed effective publication confirmation")
    names = set()
    for output in value["outputs"]:
        if (
            not isinstance(output, dict)
            or set(output) != {"path", "mode", "size", "sha256", "effect", "identity"}
            or not isinstance(output["path"], str) or not 1 <= len(output["path"].encode("utf-8")) <= 4096
            or output["path"] in names
            or type(output["mode"]) is not int or not 0 <= output["mode"] <= 0o777
            or type(output["size"]) is not int or not 0 <= output["size"] <= file_limit
            or not isinstance(output["sha256"], str) or not re.fullmatch("[0-9a-f]{64}", output["sha256"])
            or type(output["effect"]) is not str or output["effect"] not in {"created", "replaced", "retained"}
            or value["policy"] == "replace" and output["effect"] == "retained"
        ):
            raise ChannelError("invalid effective publication result")
        validate_publication_identity(output["identity"], output["mode"], output["size"])
        names.add(output["path"])
    return value


def validate_dispatch_context(value, arguments=None):
    if (
        not isinstance(value, dict)
        or set(value) != {"sequence", "executable", "arguments", "cwd", "environment", "rebuilding_makefiles"}
        or type(value["sequence"]) is not int or value["sequence"] < 1
        or not isinstance(value["executable"], str) or not isinstance(value["cwd"], str)
        or not value["executable"].startswith("/") or not value["cwd"].startswith("/")
        or os.path.normpath(value["executable"]) != value["executable"]
        or os.path.normpath(value["cwd"]) != value["cwd"]
        or not isinstance(value["arguments"], list) or not 1 <= len(value["arguments"]) <= 1024
        or not isinstance(value["environment"], dict) or type(value["rebuilding_makefiles"]) is not bool
        or any(not isinstance(item, str) or "\0" in item for item in value["arguments"])
        or any(not isinstance(name, str) or not name or "=" in name or "\0" in name
               or not isinstance(content, str) or "\0" in content
               for name, content in value["environment"].items())
        or "\0" in value["executable"] or "\0" in value["cwd"]
        or arguments is not None and value["arguments"] != arguments
    ):
        raise ChannelError("malformed or mismatched native dispatch context")
    try:
        size = sum(len(item.encode("utf-8", "strict")) + 1 for item in value["arguments"])
        size += sum(len((name + "=" + content).encode("utf-8", "strict")) + 1
                    for name, content in value["environment"].items())
        if size > 65536 or any(
            not 0 < len(value[name].encode("utf-8", "strict")) <= 4096 for name in ("executable", "cwd")
        ):
            raise ChannelError("native dispatch context exceeds its existing frame bounds")
    except UnicodeEncodeError as error:
        raise ChannelError("native dispatch context is not strict UTF-8") from error
    return value


def validate_job_context(value, sequence=None):
    if (
        not isinstance(value, dict) or set(value) != {"sequence", "kind", "target", "command_line"}
        or type(value["sequence"]) is not int or value["sequence"] < 1
        or sequence is not None and value["sequence"] != sequence
        or not isinstance(value["kind"], str) or value["kind"] not in {"recipe", "expansion"}
    ):
        raise ChannelError("malformed or unbound native job context")
    if value["kind"] == "expansion":
        if value["target"] is not None or value["command_line"] is not None:
            raise ChannelError("expansion context claims a recipe target")
    elif (
        not isinstance(value["target"], str) or not value["target"] or "\0" in value["target"]
        or type(value["command_line"]) is not int or not 0 <= value["command_line"] < 1 << 32
    ):
        raise ChannelError("native recipe context lacks its target/index")
    try:
        if value["target"] is not None and len(value["target"].encode("utf-8", "strict")) > 4096:
            raise ChannelError("native recipe target exceeds its existing bound")
    except UnicodeEncodeError as error:
        raise ChannelError("native recipe target is not strict UTF-8") from error
    return value


class ProducerChannel:
    def __init__(self, connection, *, deadline, limit, charge=None, descriptor_receiver=None):
        self.connection = connection
        self.connection.setblocking(False)
        self.deadline = deadline
        self.limit = limit
        self.charge = charge
        self.descriptor_receiver = descriptor_receiver
        self.buffer = bytearray()
        self.expected = None
        self.closed = False
        self.listening = False
        self.write_closed = False

    @classmethod
    def listen(cls, directory, **kwargs):
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            status = os.fstat(descriptor)
            if stat.S_IMODE(status.st_mode) != 0o700 or status.st_uid != os.geteuid():
                raise ChannelError("producer rendezvous requires an owned private directory")
            # sun_path is short even when the owned report directory is not.
            connection.bind(f"/proc/self/fd/{descriptor}/peer.sock")
            os.chmod("peer.sock", 0o600, dir_fd=descriptor)
            peer = os.stat("peer.sock", dir_fd=descriptor, follow_symlinks=False)
            connection.listen(1)
            result = cls(connection, **kwargs)
            result.listening = True
            result.endpoint = {
                "directory": str(directory),
                "directory_device": status.st_dev, "directory_inode": status.st_ino,
                "socket_device": peer.st_dev, "socket_inode": peer.st_ino,
            }
            return result
        except BaseException:
            connection.close()
            raise
        finally:
            os.close(descriptor)

    @classmethod
    def connect(cls, endpoint, *, owner_uid, server_pid, **kwargs):
        fields = {"directory", "directory_device", "directory_inode", "socket_device", "socket_inode"}
        if (
            not isinstance(endpoint, dict) or set(endpoint) != fields
            or not isinstance(endpoint["directory"], str) or not endpoint["directory"].startswith("/")
            or any(type(endpoint[name]) is not int or endpoint[name] < 0 for name in fields - {"directory"})
        ):
            raise ChannelError("malformed private producer endpoint")
        descriptor = os.open(
            endpoint["directory"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            def validate():
                directory = os.fstat(descriptor)
                peer = os.stat("peer.sock", dir_fd=descriptor, follow_symlinks=False)
                if (
                    (directory.st_dev, directory.st_ino) != (
                        endpoint["directory_device"], endpoint["directory_inode"],
                    )
                    or stat.S_IMODE(directory.st_mode) != 0o700 or directory.st_uid != owner_uid
                    or (peer.st_dev, peer.st_ino) != (endpoint["socket_device"], endpoint["socket_inode"])
                    or not stat.S_ISSOCK(peer.st_mode) or stat.S_IMODE(peer.st_mode) != 0o600
                    or peer.st_uid != owner_uid
                ):
                    raise ChannelError("foreign or replaced private producer endpoint")
            validate()
            connection.settimeout(max(0, kwargs["deadline"] - time.monotonic()))
            connection.connect(f"/proc/self/fd/{descriptor}/peer.sock")
            validate()
            pid, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if (pid, uid) != (server_pid, owner_uid):
                raise ChannelError("foreign private producer listener credentials")
            return cls(connection, **kwargs)
        except BaseException:
            connection.close()
            raise
        finally:
            os.close(descriptor)

    def accept(self, *, launcher_pid, peer_uid, ancestry_limit):
        self.remaining()
        if not self.listening:
            raise ChannelError("duplicate private producer connection")
        connection, _ = self.connection.accept()
        descriptors = []
        try:
            pid, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if pid <= 0 or uid != peer_uid:
                raise ChannelError("foreign private producer peer credentials")
            # SO_PEERCRED identifies the connector in the driver's PID namespace.
            # It must belong to this live sole-reaper launch, not another report.
            current = pid
            for _ in range(ancestry_limit):
                self.remaining()
                if current == os.getpid():
                    raise ChannelError("foreign producer peer outside the owned launch")
                descriptors.append(os.pidfd_open(current))
                if current == launcher_pid:
                    break
                descriptor = os.open(
                    f"/proc/{current}/stat", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                )
                try:
                    data = os.read(descriptor, 65536)
                finally:
                    os.close(descriptor)
                if self.charge is not None:
                    self.charge(len(data) + 64)
                fields = data.rpartition(b") ")[2].split()
                if len(data) == 65536 or len(fields) < 2 or not fields[1].isdigit():
                    raise ChannelError("invalid producer peer ancestry")
                parent = int(fields[1])
                if parent <= 0 or parent == current:
                    raise ChannelError("foreign producer peer outside the owned launch")
                current = parent
            else:
                raise ChannelError("producer peer ancestry exceeds the observation bound")
            self.require_live(descriptors)
            self.connection.close()
            self.connection = connection
            self.connection.setblocking(False)
            self.listening = False
        except BaseException as error:
            connection.close()
            if isinstance(error, OSError):
                raise ChannelError(f"producer peer identity could not be pinned: {error}") from error
            raise
        finally:
            for descriptor in descriptors:
                os.close(descriptor)

    def fileno(self):
        return self.connection.fileno()

    def remaining(self):
        left = self.deadline - time.monotonic()
        if self.closed or left <= 0:
            raise ChannelError("producer rendezvous deadline or lifetime ended")
        return left

    def ensure_idle(self):
        self.remaining()
        if self.buffer:
            raise ChannelError("duplicate or out-of-order producer message")
        try:
            data = self.connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        except BlockingIOError:
            return
        except ConnectionError as error:
            raise ChannelError(f"producer channel failed outside an exchange: {error}") from error
        if not data:
            raise ChannelError("producer channel closed outside its terminal handshake")
        raise ChannelError("unsolicited or duplicate producer message outside an exchange")

    def receive(self):
        result = []
        finish_cleanup([lambda: result.append(self._receive())])
        return result[0]

    def _receive(self):
        self.remaining()
        try:
            data, ancillary, flags, _ = self.connection.recvmsg(
                65536, socket.CMSG_SPACE(struct.calcsize("i")), socket.MSG_CMSG_CLOEXEC,
            )
        except BlockingIOError:
            return None
        except ConnectionError as error:
            raise ChannelError(f"producer rendezvous receive failed: {error}") from error
        descriptors = []
        malformed = bool(flags & socket.MSG_CTRUNC)
        for level, kind, value in ancillary:
            if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS or len(value) % struct.calcsize("i"):
                malformed = True
                continue
            descriptors.extend(struct.unpack(f"{len(value) // struct.calcsize('i')}i", value))
        if malformed or descriptors and (len(descriptors) != 1 or self.descriptor_receiver is None):
            for descriptor in descriptors:
                os.close(descriptor)
            raise ChannelError("unrequested or malformed producer descriptor handoff")
        if descriptors:
            # The receiver owns the actual fd before any byte charge can fail.
            self.descriptor_receiver(descriptors[0])
        if not data:
            raise ChannelError("producer rendezvous EOF")
        if self.charge is not None:
            self.charge(len(data) + len(descriptors) * struct.calcsize("i"))
        self.buffer.extend(data)
        if self.expected is None and len(self.buffer) >= 4:
            self.expected = struct.unpack_from("<I", self.buffer)[0]
            if not 1 <= self.expected <= self.limit:
                raise ChannelError("producer message exceeds its existing byte bound")
        if self.expected is None or len(self.buffer) < self.expected + 4:
            return None
        if len(self.buffer) != self.expected + 4:
            raise ChannelError("duplicate or pipelined producer message")
        result = bytes(self.buffer[4:])
        self.buffer.clear()
        self.expected = None
        return result

    def send(self, data):
        return self._send(data)

    def send_descriptor(self, data, descriptor):
        if type(descriptor) is not int or descriptor < 0:
            raise ChannelError("invalid producer descriptor")
        os.fstat(descriptor)
        return self._send(data, descriptor)

    def _send(self, data, descriptor=None):
        self.remaining()
        if self.write_closed or self.listening:
            raise ChannelError("producer channel has no writable reply phase")
        if not isinstance(data, bytes) or not 1 <= len(data) <= self.limit:
            raise ChannelError("invalid producer reply byte bound")
        if self.charge is not None:
            self.charge(len(data) + 4 + (struct.calcsize("i") if descriptor is not None else 0))
        frame = memoryview(struct.pack("<I", len(data)) + data)
        offset = 0
        with selectors.DefaultSelector() as selector:
            selector.register(self.connection, selectors.EVENT_WRITE)
            while offset < len(frame):
                if not selector.select(min(self.remaining(), 0.05)):
                    continue
                try:
                    if descriptor is not None and offset == 0:
                        written = self.connection.sendmsg(
                            [frame[:65536]],
                            [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack("i", descriptor))],
                        )
                    else:
                        written = self.connection.send(frame[offset:offset + 65536])
                except BlockingIOError:
                    continue
                except ConnectionError as error:
                    raise ChannelError(f"producer rendezvous send failed: {error}") from error
                if not written:
                    raise ChannelError("producer reply channel closed")
                offset += written

    def exchange(self, data, *, watch=()):
        return self._exchange(data, watch=watch)

    def exchange_descriptor(self, data, descriptor, *, watch=()):
        return self._exchange(data, descriptor=descriptor, watch=watch)

    def _exchange(self, data, *, descriptor=None, watch=()):
        self.ensure_idle()
        if descriptor is None:
            self.send(data)
        else:
            self.send_descriptor(data, descriptor)
        with selectors.DefaultSelector() as selector:
            selector.register(self.connection, selectors.EVENT_READ, "reply")
            for descriptor in watch:
                selector.register(descriptor, selectors.EVENT_READ, "death")
            while True:
                ready = selector.select(min(self.remaining(), 0.05))
                if any(key.data == "death" for key, _ in ready):
                    raise ChannelError("parked producer-context process died")
                for key, _ in ready:
                    result = self.receive()
                    if result is not None:
                        return result

    def shutdown_write(self):
        self.remaining()
        if self.write_closed or self.listening:
            raise ChannelError("duplicate producer terminal handshake")
        try:
            self.connection.shutdown(socket.SHUT_WR)
        except ConnectionError as error:
            raise ChannelError(f"producer terminal shutdown failed: {error}") from error
        self.write_closed = True

    def receive_eof(self):
        self.remaining()
        if self.buffer:
            raise ChannelError("partial producer message at the terminal boundary")
        try:
            data = self.connection.recv(1)
        except BlockingIOError:
            return False
        except ConnectionError as error:
            raise ChannelError(f"producer terminal handshake failed: {error}") from error
        if data:
            if self.charge is not None:
                self.charge(len(data))
            raise ChannelError("unexpected producer message at the terminal boundary")
        return True

    def finish(self, data):
        self.send(data)
        # The driver's write-half EOF is a barrier: no later reply can follow it.
        trailing = len(self.buffer)
        self.buffer.clear()
        self.expected = None
        if trailing > self.limit:
            raise ChannelError("producer terminal traffic exceeds the existing byte bound")
        with selectors.DefaultSelector() as selector:
            selector.register(self.connection, selectors.EVENT_READ)
            while True:
                if not selector.select(min(self.remaining(), 0.05)):
                    continue
                try:
                    data = self.connection.recv(min(65536, self.limit - trailing + 1))
                except BlockingIOError:
                    continue
                except ConnectionError as error:
                    raise ChannelError(f"producer terminal handshake failed: {error}") from error
                if not data:
                    if trailing:
                        raise ChannelError("unexpected producer message at the terminal boundary")
                    return
                if self.charge is not None:
                    self.charge(len(data))
                trailing += len(data)
                if trailing > self.limit:
                    raise ChannelError("producer terminal traffic exceeds the existing byte bound")

    @staticmethod
    def require_live(watch):
        with selectors.DefaultSelector() as selector:
            for descriptor in watch:
                selector.register(descriptor, selectors.EVENT_READ)
            if selector.select(0):
                raise ChannelError("parked producer-context process died")

    def close(self):
        if not self.closed:
            self.closed = True
            self.connection.close()
