"""Closed original entry-image barrier data, not namespace-use authority."""

from __future__ import annotations

import hashlib
import re

if __package__:
    from .authority import encoded
    from .producer_channel import ChannelError
else:
    from authority import encoded
    from producer_channel import ChannelError


BARRIER_KEYS = frozenset({
    "kind", "scope", "barrier", "exec", "pass", "trace_seq", "input_sha256",
    "issued", "completed", "publication", "counters", "reserved",
})
RESUME_KEYS = frozenset({
    "kind", "scope", "barrier", "exec", "pass", "trace_seq", "input_sha256", "image_sha256", "limits",
})


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def validate_identity(value):
    if (
        any(type(value[name]) is not int or value[name] < 1 for name in ("barrier", "exec", "pass", "trace_seq"))
        or not isinstance(value["scope"], str) or not value["scope"]
        or not isinstance(value["input_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", value["input_sha256"])
    ):
        raise ChannelError("malformed original entry barrier identity")


def validate_barrier(value, *, scope, barrier, sequence):
    if not isinstance(value, dict) or set(value) != BARRIER_KEYS or value["kind"] != "read-barrier":
        raise ChannelError("malformed original read barrier")
    validate_identity(value)
    if (
        value["scope"] != scope or value["barrier"] != barrier
        or type(value["issued"]) is not int or type(value["completed"]) is not int
        or value["issued"] != sequence or value["completed"] != sequence
        or value["exec"] != barrier or value["pass"] != barrier
    ):
        raise ChannelError("foreign, incomplete or repeated original read barrier")
    return value


def validate_resume(value, request):
    if not isinstance(value, dict) or set(value) != RESUME_KEYS or value["kind"] != "read-resume":
        raise ChannelError("malformed original read barrier acknowledgement")
    validate_identity(value)
    if (
        any(value[name] != request[name] for name in ("scope", "barrier", "exec", "pass", "trace_seq", "input_sha256"))
        or not isinstance(value["image_sha256"], str) or not re.fullmatch("[0-9a-f]{64}", value["image_sha256"])
    ):
        raise ChannelError("original read acknowledgement changed its exact entry/image binding")
    return value


def validate_capture(value, trace):
    if (
        not isinstance(value, dict) or set(value) != {"version", "scope", "entries", "closed"}
        or type(value["version"]) is not int or value["version"] != 1 or value["closed"] is not True
        or not isinstance(value["entries"], list) or trace.get("version") != 2
        or value["scope"] != trace["scope"]
    ):
        raise ChannelError("incomplete or foreign original entry-image capture")
    events = [event for event in trace["events"] if event["kind"] == "entry-image"]
    if len(events) != len(value["entries"]) or not events:
        raise ChannelError("original entry images omit actual read barriers")
    for entry, event in zip(value["entries"], events):
        if (
            not isinstance(entry, dict)
            or set(entry) != {"barrier", "exec", "pass", "trace_seq", "input_sha256", "image_sha256", "image"}
            or any(type(entry[name]) is not int for name in ("barrier", "exec", "pass", "trace_seq"))
            or any(entry[name] != event[name] for name in ("barrier", "exec", "pass", "input_sha256", "image_sha256"))
            or entry["trace_seq"] != event["seq"] - 1
            or not isinstance(entry["image"], dict)
            or set(entry["image"]) != {"directories", "members", "forbidden", "stamps"}
            or digest(entry["image"]) != entry["image_sha256"]
        ):
            raise ChannelError("original namespace image differs from its actual read barrier")
    return value
