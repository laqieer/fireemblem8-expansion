"""Bounded, private per-invocation supervisor/driver transport; never guest I/O."""

from __future__ import annotations

import os
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
