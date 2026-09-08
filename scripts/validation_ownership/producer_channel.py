"""Bounded, private per-invocation supervisor/driver transport; never guest I/O."""

from __future__ import annotations

import selectors
import socket
import struct
import time


class ChannelError(RuntimeError):
    pass


class ProducerChannel:
    def __init__(self, connection, *, deadline, limit, charge=None, peer=None):
        self.connection = connection
        self.connection.setblocking(False)
        self.deadline = deadline
        self.limit = limit
        self.charge = charge
        self.peer = peer
        self.buffer = bytearray()
        self.expected = None
        self.closed = False

    def fileno(self):
        return self.connection.fileno()

    def close_peer(self):
        if self.peer is not None:
            self.peer.close()
            self.peer = None

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
        if not data:
            raise ChannelError("producer supervisor channel closed during nested work")
        raise ChannelError("unexpected producer message during nested work")

    def receive(self):
        self.remaining()
        try:
            data = self.connection.recv(65536)
        except BlockingIOError:
            return None
        if not data:
            raise ChannelError("producer rendezvous EOF")
        if self.charge is not None:
            self.charge(len(data))
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
        self.remaining()
        if not isinstance(data, bytes) or not 1 <= len(data) <= self.limit:
            raise ChannelError("invalid producer reply byte bound")
        if self.charge is not None:
            self.charge(len(data) + 4)
        frame = memoryview(struct.pack("<I", len(data)) + data)
        offset = 0
        with selectors.DefaultSelector() as selector:
            selector.register(self.connection, selectors.EVENT_WRITE)
            while offset < len(frame):
                if not selector.select(min(self.remaining(), 0.05)):
                    continue
                try:
                    written = self.connection.send(frame[offset:offset + 65536])
                except BlockingIOError:
                    continue
                if not written:
                    raise ChannelError("producer reply channel closed")
                offset += written

    def exchange(self, data, *, watch=()):
        self.send(data)
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
            self.close_peer()
