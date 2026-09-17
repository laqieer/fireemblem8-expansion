"""Native originating-read/publication records, not a complete mutation journal."""

from __future__ import annotations

import hashlib
if __package__:
    from .authority import encoded
    from . import read_epochs
    from .source_phases import digest
    from .producer_channel import (
        ChannelError, validate_dispatch_context, validate_job_context, validate_publication_confirmation,
    )
else:
    from authority import encoded
    import read_epochs
    from source_phases import digest
    from producer_channel import (
        ChannelError, validate_dispatch_context, validate_job_context, validate_publication_confirmation,
    )


ORIGIN_FIELDS = frozenset({
    "origin", "exec", "pass", "stage", "visit", "executable", "rebuilding_makefiles",
})
PACKET_FIELDS = ORIGIN_FIELDS | {"scope", "trace_seq", "dispatch", "producer"}
DISPATCH_FIELDS = frozenset({
    "sequence", "executable", "arguments", "cwd", "environment", "rebuilding_makefiles",
})
EVENT_FIELDS = {
    "origin": ORIGIN_FIELDS,
    "dispatch": {"origin", "dispatch", "context_sha256"},
    "producer": {"origin", "dispatch", "producer", "context_sha256", "job_sha256", "frame_sha256"},
    "adoption": {"origin", "producer", "records"},
    "publication": {"origin", "producer", "confirmation"},
}


def dispatch_context(value):
    if not isinstance(value, dict) or not DISPATCH_FIELDS <= set(value):
        raise ChannelError("source effect lacks its actual native dispatch")
    return validate_dispatch_context({name: value[name] for name in DISPATCH_FIELDS})


def origin_packet(scope, origin, dispatch, producer):
    return {
        "scope": scope, **{name: origin[name] for name in ORIGIN_FIELDS | {"trace_seq"}},
        "dispatch": dispatch, "producer": producer,
    }


def validate_origin(value, *, scope, dispatch, producer):
    if (
        not isinstance(value, dict) or set(value) != PACKET_FIELDS or value["scope"] != scope
        or any(type(value[name]) is not int or value[name] < 1
               for name in ("origin", "exec", "trace_seq", "dispatch", "producer"))
        or value["dispatch"] != dispatch["sequence"] or value["producer"] != producer
        or value["executable"] != dispatch["executable"]
        or type(value["rebuilding_makefiles"]) is not bool
        or value["rebuilding_makefiles"] != dispatch["rebuilding_makefiles"]
        or not isinstance(value["stage"], str)
        or value["stage"] not in {"before-read", "source-read", "after-read"}
        or value["pass"] is not None and (type(value["pass"]) is not int or value["pass"] < 1)
        or value["visit"] is not None and (type(value["visit"]) is not int or value["visit"] < 1)
        or value["stage"] == "before-read" and (value["pass"] is not None or value["visit"] is not None)
        or value["stage"] != "before-read" and value["pass"] != value["exec"]
        or value["stage"] != "source-read" and value["visit"] is not None
    ):
        raise ChannelError("malformed, foreign or mismatched native source origin")
    return value


def request_record(origin, dispatch, job, frame):
    validate_job_context(job, dispatch["sequence"])
    return {
        "source_origin": origin, "dispatch": dispatch, "job": job,
        "frame_sha256": hashlib.sha256(frame).hexdigest(), "adopt_sha256": None,
    }


def _read_contexts(trace):
    contexts = {}
    current = None
    visits = []
    for event in trace["events"]:
        kind = event["kind"]
        if kind == "exec":
            current = {"exec": event["exec"], "pass": None, "stage": "before-read", "visit": None}
        elif kind == "pass-entry":
            current = {"exec": event["exec"], "pass": event["pass"], "stage": "source-read", "visit": None}
        elif kind == "source-entry":
            visits.append(event["visit"])
        elif kind == "source-exit":
            visits.pop()
        elif kind == "pass-exit":
            current = {**current, "stage": "after-read"}
        if current is not None:
            current = {**current, "visit": visits[-1] if visits else None}
        contexts[event["seq"]] = None if kind in {"pass-entry", "complete"} else current
    return contexts


def validate_journal(value, trace, *, dispatches, requests, publications, count_limit, file_limit):
    if (
        not isinstance(value, dict) or set(value) != {"version", "scope", "events", "closed"}
        or type(value["version"]) is not int or value["version"] != 1 or value["closed"] is not True
        or not isinstance(value["scope"], str) or not value["scope"]
        or not isinstance(value["events"], list) or len(value["events"]) > count_limit
        or not isinstance(trace, dict) or trace.get("version") != 2
    ):
        raise ChannelError("incomplete or malformed native source-effect observation")
    read_epochs.validate_trace(trace, value["scope"], count_limit=count_limit, file_limit=file_limit)
    contexts = _read_contexts(trace)
    actual = {}
    for item in dispatches:
        core = dispatch_context(item)
        if core["sequence"] in actual:
            raise ChannelError("repeated actual native source-effect dispatch")
        actual[core["sequence"]] = item
    if set(actual) != set(range(1, len(actual) + 1)) or len(requests) != len(publications):
        raise ChannelError("native source effects lack complete actual dispatch/publication coverage")
    origins, dispatched, producers, published, adopted = {}, {}, {}, set(), set()
    previous_trace = 0
    for sequence, event in enumerate(value["events"], 1):
        if (
            not isinstance(event, dict) or not isinstance(event.get("kind"), str)
            or event["kind"] not in EVENT_FIELDS
            or set(event) != EVENT_FIELDS[event["kind"]] | {"seq", "kind", "trace_seq"}
            or type(event["seq"]) is not int or event["seq"] != sequence
            or type(event["trace_seq"]) is not int
            or not previous_trace <= event["trace_seq"] < len(trace["events"])
            or contexts.get(event["trace_seq"]) is None
            or type(event["origin"]) is not int or event["origin"] < 1
        ):
            raise ChannelError("malformed, reordered or unbound native source effect")
        previous_trace = event["trace_seq"]
        kind, origin = event["kind"], event["origin"]
        if kind == "origin":
            if origin != len(origins) + 1:
                raise ChannelError("native source origins are repeated or incomplete")
            context = contexts[event["trace_seq"]]
            if (
                any(event[name] != context[name] for name in ("exec", "pass", "stage", "visit"))
                or type(event["exec"]) is not int
                or event["pass"] is not None and type(event["pass"]) is not int
                or event["visit"] is not None and type(event["visit"]) is not int
                or not isinstance(event["executable"], str) or not event["executable"].startswith("/")
                or type(event["rebuilding_makefiles"]) is not bool
            ):
                raise ChannelError("native source origin differs from its actual original read interval")
            origins[origin] = event
            continue
        if origin not in origins:
            raise ChannelError("native source effect borrowed an unissued origin")
        if kind in {"dispatch", "producer"}:
            dispatch = event["dispatch"]
            if type(dispatch) is not int or dispatch not in actual:
                raise ChannelError("source effect has no actual native dispatch")
            core = dispatch_context(actual[dispatch])
            if any(core[name] != origins[origin][name] for name in ("executable", "rebuilding_makefiles")):
                raise ChannelError("source effect changed its originating executable or remake state")
        if kind == "dispatch":
            if dispatch in dispatched or origin in dispatched.values() or event["context_sha256"] != digest(core):
                raise ChannelError("native source dispatch is duplicated or differs from actual execution")
            dispatched[dispatch] = origin
            continue
        producer = event["producer"]
        if type(producer) is not int or not 1 <= producer <= len(requests):
            raise ChannelError("source effect has no accepted actual producer")
        request = requests[producer - 1]
        if kind == "producer":
            packet = origin_packet(value["scope"], origins[origin], dispatch, producer)
            validate_origin(packet, scope=value["scope"], dispatch=core, producer=producer)
            validate_job_context(request["job"], dispatch)
            if (
                producer != len(producers) + 1 or dispatched.get(dispatch) != origin
                or dispatch in producers.values() or request["source_origin"] != packet
                or request["dispatch"] != core or event["context_sha256"] != digest(packet)
                or event["job_sha256"] != digest(request["job"])
                or event["frame_sha256"] != request["frame_sha256"]
                or "job" in actual[dispatch] and actual[dispatch]["job"] != request["job"]
            ):
                raise ChannelError("native source producer differs from its accepted original request/job/frame")
            producers[producer] = dispatch
            continue
        if producer not in producers or dispatched[producers[producer]] != origin or producer in published:
            raise ChannelError("publication/adoption has a stale or foreign native source producer")
        if kind == "adoption":
            if (
                producer in adopted or not isinstance(event["records"], list)
                or not 1 <= len(event["records"]) <= count_limit
                or digest(event["records"]) != request["adopt_sha256"]
            ):
                raise ChannelError("nested source adoption differs from its actual protected result")
            adopted.add(producer)
        else:
            confirmation = event["confirmation"]
            validate_publication_confirmation(confirmation, count_limit=count_limit, file_limit=file_limit)
            if (
                confirmation["slot"] != producer - 1 or confirmation != publications[producer - 1]
                or (request["adopt_sha256"] is not None) != (producer in adopted)
            ):
                raise ChannelError("native source publication differs from its independently checked outcome")
            published.add(producer)
    if (
        len(origins) != len(actual) or set(dispatched) != set(actual)
        or len(producers) != len(requests) or published != set(producers)
    ):
        raise ChannelError("native source-effect observation omitted an origin, execution or publication")
    return value


class NativeSourceEffects:
    def __init__(self, policy, config):
        if (
            not isinstance(config, dict) or set(config) != {"version", "scope"}
            or type(config["version"]) is not int or config["version"] != 1
            or config["scope"] != policy.config.get("producer_scope")
            or policy.read_trace is None or policy.read_trace.version != 2
        ):
            raise ChannelError("source effects require their exact original-entry Make trace")
        self.policy, self.trace, self.scope = policy, policy.read_trace, config["scope"]
        self.events, self.requests, self.publications = [], [], []
        self.origins, self.bindings = {}, {}

    def event(self, kind, **fields):
        if len(self.events) >= self.policy.config["observation_count"]:
            raise ChannelError("native source-effect count exceeds its existing bound")
        value = {"seq": len(self.events) + 1, "kind": kind, "trace_seq": len(self.trace.events), **fields}
        self.policy.charge_metadata(len(encoded(value)))
        self.events.append(value)
        return value

    def originate(self, pid, state, executable, rebuilding):
        if (
            pid != self.trace.pid or state.role != "make" or not state.observer_ready
            or self.trace.bias is None or self.trace.pending_barrier is not None
            or state.dispatch_origin is not None
        ):
            raise ChannelError("native source origin lacks its actual main-Make dispatch stop")
        reading = self.trace.pass_frame is not None
        before = self.trace.passes != self.trace.execs
        origin = len(self.origins) + 1
        row = self.event(
            "origin", origin=origin, exec=self.trace.execs,
            **{"pass": None if before else self.trace.passes},
            stage="before-read" if before else "source-read" if reading else "after-read",
            visit=self.trace.active[-1]["visit"] if self.trace.active else None,
            executable=executable, rebuilding_makefiles=rebuilding,
        )
        self.origins[origin] = row
        return origin

    def helper_exec(self, pid, state):
        origin, dispatch = state.dispatch_origin, state.native_dispatch_sequence
        core = validate_dispatch_context(state.native_dispatch_context)
        if (
            origin not in self.origins or dispatch in self.bindings
            or any(record[2].dispatch_origin == origin for record in self.bindings.values())
            or state.pidfd < 0
            or any(core[name] != self.origins[origin][name] for name in ("executable", "rebuilding_makefiles"))
        ):
            raise ChannelError("successful source helper exec lost its issued native origin")
        self.event("dispatch", origin=origin, dispatch=dispatch, context_sha256=digest(core))
        self.bindings[dispatch] = pid, state.pidfd, state, core

    def producer(self, pid, state, producer):
        dispatch, origin = state.native_dispatch_sequence, state.dispatch_origin
        bound = self.bindings.get(dispatch)
        if (
            producer != len(self.requests) + 1 or bound is None
            or bound[:2] != (pid, state.pidfd) or bound[2] is not state
            or bound[3] != state.native_dispatch_context
        ):
            raise ChannelError("source producer has a foreign or expired actual helper")
        packet = origin_packet(self.scope, self.origins[origin], dispatch, producer)
        validate_origin(packet, scope=self.scope, dispatch=bound[3], producer=producer)
        job = {"sequence": dispatch, **state.native_job_context}
        request = request_record(packet, bound[3], job, state.producer_frame)
        self.event(
            "producer", origin=origin, dispatch=dispatch, producer=producer,
            context_sha256=digest(packet), job_sha256=digest(job), frame_sha256=request["frame_sha256"],
        )
        self.requests.append(request)
        return packet

    def adoption(self, producer, records):
        request = self.requests[producer - 1]
        request["adopt_sha256"] = digest(records)
        self.event("adoption", origin=request["source_origin"]["origin"], producer=producer, records=records)

    def publication(self, producer, confirmation):
        if producer != len(self.publications) + 1:
            raise ChannelError("native source publication completed out of order")
        self.event(
            "publication", origin=self.requests[producer - 1]["source_origin"]["origin"],
            producer=producer, confirmation=confirmation,
        )
        self.publications.append(confirmation)

    def finish(self, trace):
        value = {"version": 1, "scope": self.scope, "events": self.events, "closed": True}
        return validate_journal(
            value, trace, dispatches=[record[3] for record in self.bindings.values()],
            requests=self.requests, publications=self.publications,
            count_limit=self.policy.config["observation_count"], file_limit=self.policy.config["file_limit"],
        )
