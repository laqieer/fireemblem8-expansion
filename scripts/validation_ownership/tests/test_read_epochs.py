"""Original native input/source epochs; these tests grant no namespace exception."""

import base64
import copy
import ctypes
from contextlib import ExitStack
from dataclasses import replace
import errno
import hashlib
import json
from pathlib import Path
import posixpath
import shlex
import signal
import stat
import struct
from types import MappingProxyType, SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.validation_ownership import read_epochs
from scripts.validation_ownership import read_trace
from scripts.validation_ownership import make_probe
from scripts.validation_ownership.authority import encoded
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.make_probe import Command
from scripts.validation_ownership.tests import test_foundation as foundation


class CompletionTraceDataApiTests(unittest.TestCase):
    """Unissued archive, frame and slot models; not hardware qualification."""

    @staticmethod
    def trace_data():
        data, child = b"CAP := $(shell model-only)\ninclude child.mk\n", b"CHILD := yes\n"
        source = {
            "id": 1, "mode": 0o644, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "data": base64.b64encode(data).decode(),
        }
        selection = [
            "Makefile", source["sha256"], 1, 1, 1, "CAP", ":=",
            hashlib.sha256(b"CAP := $(shell model-only)").hexdigest(), False,
        ]
        inputs = [{"parent": False, "variables": []}]
        events = [
            {"kind": "exec", "exec": 1},
            {"kind": "pass-entry", "exec": 1, "pass": 1, "inputs": inputs},
            {"kind": "entry-image", "exec": 1, "pass": 1, "barrier": 1,
             "input_sha256": hashlib.sha256(encoded(inputs)).hexdigest(), "image_sha256": "a" * 64},
            {"kind": "source-entry", "exec": 1, "pass": 1, "visit": 1, "parent": None,
             "name": "Makefile", "flags": 0, "location": None},
            {"kind": "source-open", "exec": 1, "pass": 1, "visit": 1, "name": "Makefile",
             "mode": "r", "result": 3, "source": 1, "identity": [1, 2, 0o100644, len(data), 4, 5, 1]},
            {"kind": "assignment-completion", "exec": 1, "pass": 1, "visit": 1, "source": 1,
             "site": selection, "name": "CAP", "operator": ":=", "cwd": "/repo",
             "variable": ["CAP", "model-only", 2 << 26, "Makefile", 1, 0]},
            {"kind": "source-entry", "exec": 1, "pass": 1, "visit": 2, "parent": 1,
             "name": "child.mk", "flags": 0, "location": [1, 1, 2, 2, 2]},
            {"kind": "source-open", "exec": 1, "pass": 1, "visit": 2, "name": "child.mk",
             "mode": "r", "result": 4, "source": 2, "identity": [1, 3, 0o100644, len(child), 4, 5, 1]},
            {"kind": "source-exit", "exec": 1, "pass": 1, "visit": 2, "resolved": "child.mk",
             "flags": 0, "error": 0, "source": 2},
            {"kind": "source-exit", "exec": 1, "pass": 1, "visit": 1, "resolved": "Makefile",
             "flags": 0, "error": 0, "source": 1},
            {"kind": "pass-exit", "exec": 1, "pass": 1, "goals": [1, 2]},
            {"kind": "complete", "execs": 1, "passes": 1, "visits": 2},
        ]
        for sequence, event in enumerate(events, 1):
            event["seq"] = sequence
        return {
            "version": 3, "scope": "unissued-model", "selection": [selection], "events": events,
            "sources": [source, {
                "id": 2, "mode": 0o644, "bytes": len(child), "sha256": hashlib.sha256(child).hexdigest(),
                "data": base64.b64encode(child).decode(),
            }], "complete": True,
        }

    @staticmethod
    def trace_data_v4():
        trace = CompletionTraceDataApiTests.trace_data()
        trace["version"] = read_epochs.COMPLETION_VERSION
        source_rows = trace["sources"]
        trace["selection"] = {
            "version": 1,
            "snapshot_sha256": "b" * 64,
            "names": ["CAP"],
            "inventory": [
                {
                    "path": name, "kind": "snapshot", "mode": row["mode"],
                    "size": row["bytes"], "sha256": row["sha256"], "screen": "text",
                }
                for name, row in zip(("Makefile", "child.mk"), source_rows)
            ],
            "scan": {
                "entries": 2, "bytes": sum(row["bytes"] for row in source_rows),
                "text": 2, "binary": 0, "invalid_utf8": 0, "oversize": 0,
            },
        }
        for event in trace["events"]:
            if event["kind"] == "source-open":
                event["path"] = event["name"]
                event["custody"] = {"kind": "snapshot"}
        return trace

    def test_v4_binds_sparse_sites_to_exact_opened_source_versions(self):
        trace = self.trace_data_v4()
        self.assertIs(read_epochs.validate_trace(
            trace, trace["scope"], count_limit=32, file_limit=1024,
        ), trace)
        archive = read_epochs.reconstruct_archive(trace, budget=ProbeBudget())
        self.assertEqual(archive.version, 4)
        self.assertEqual(archive.selection, ("CAP",))
        self.assertEqual(
            (archive.passes[0].visits[0].opens[0].path,
             archive.passes[0].visits[0].opens[0].custody),
            ("Makefile", {"kind": "snapshot"}),
        )
        for defect in (
            "foreign-path", "foreign-version", "binary-source", "invalid-screen", "unknown-late-consumer",
        ):
            changed = copy.deepcopy(trace)
            opened = next(row for row in changed["events"] if row["kind"] == "source-open")
            if defect == "foreign-path":
                opened["path"] = "foreign.mk"
            elif defect == "foreign-version":
                opened["custody"] = {"kind": "prior-publication", "owner": "c" * 64, "serial": 1}
            elif defect == "binary-source":
                source = changed["sources"][0]
                data = b"CAP := \0hidden\n"
                source.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                              data=base64.b64encode(data).decode())
                opened["identity"][3] = len(data)
                row = changed["selection"]["inventory"][0]
                row.update(size=len(data), sha256=hashlib.sha256(data).hexdigest(), screen="binary")
                changed["selection"]["scan"]["bytes"] += len(data) - trace["sources"][0]["bytes"]
                changed["selection"]["scan"]["text"] -= 1
                changed["selection"]["scan"]["binary"] += 1
            elif defect == "invalid-screen":
                changed["selection"]["inventory"][0]["screen"] = 17
            else:
                source = changed["sources"][1]
                data = b"include $(LATE)\n"
                source.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                              data=base64.b64encode(data).decode())
                changed["events"][7]["identity"][3] = len(data)
            with self.subTest(defect=defect), self.assertRaises(read_epochs.ReadEpochError):
                read_epochs.validate_trace(changed, changed["scope"], count_limit=32, file_limit=1024)

    def test_v4_binds_resolved_source_to_descriptor_path_not_callback_spelling(self):
        for callback_name, resolved in (
            ("./sub.mk", "sub.mk"),
            ("sub.mk", "./sub.mk"),
            ("sub.mk", "/repo/sub.mk"),
            ("/repo/sub.mk", "/repo/sub.mk"),
            ("dir/../sub.mk", "dir/../sub.mk"),
        ):
            trace = self.trace_data_v4()
            opened = [row for row in trace["events"] if row["kind"] == "source-open"][1]
            exited = next(
                row for row in trace["events"]
                if row["kind"] == "source-exit" and row["visit"] == 2
            )
            opened["name"] = callback_name
            opened["path"] = "sub.mk"
            trace["selection"]["inventory"][1]["path"] = "sub.mk"
            exited["resolved"] = resolved
            with self.subTest(callback_name=callback_name, resolved=resolved):
                self.assertIs(read_epochs.validate_trace(
                    trace, trace["scope"], count_limit=32, file_limit=1024,
                ), trace)
                archive = read_epochs.reconstruct_archive(trace, budget=ProbeBudget())
                opened_archive = archive.passes[0].visits[1].opens[0]
                self.assertEqual((opened_archive.name, opened_archive.path), (callback_name, "sub.mk"))

        for resolved in ("other.mk", "../sub.mk", "/outside/sub.mk", "/repo/../outside/sub.mk"):
            trace = self.trace_data_v4()
            opened = [row for row in trace["events"] if row["kind"] == "source-open"][1]
            exited = next(
                row for row in trace["events"]
                if row["kind"] == "source-exit" and row["visit"] == 2
            )
            opened["name"] = "./sub.mk"
            opened["path"] = "sub.mk"
            trace["selection"]["inventory"][1]["path"] = "sub.mk"
            exited["resolved"] = resolved
            with self.subTest(rejected_resolved=resolved), self.assertRaises(read_epochs.ReadEpochError):
                read_epochs.validate_trace(trace, trace["scope"], count_limit=32, file_limit=1024)

        foreign = self.trace_data_v4()
        opened = [row for row in foreign["events"] if row["kind"] == "source-open"][1]
        exited = next(
            row for row in foreign["events"]
            if row["kind"] == "source-exit" and row["visit"] == 2
        )
        opened["name"] = "./sub.mk"
        opened["path"] = "sub.mk"
        foreign["selection"]["inventory"][1]["path"] = "sub.mk"
        exited["resolved"] = "sub.mk"
        exited["source"] = 1
        with self.assertRaises(read_epochs.ReadEpochError):
            read_epochs.validate_trace(foreign, foreign["scope"], count_limit=32, file_limit=1024)

    def test_prior_publication_open_requires_its_exact_frozen_pin(self):
        trace = self.trace_data_v4()
        source = trace["sources"][1]
        identity = [1, 3, 0o100644, source["bytes"], 4, 5, 1]
        entry = trace["selection"]["inventory"][1]
        entry.update(kind="prior-publication", owner="e" * 64, serial=2, identity=identity)
        opened = [row for row in trace["events"] if row["kind"] == "source-open"][1]
        opened["custody"] = {"kind": "prior-publication", "owner": "e" * 64, "serial": 2}
        self.assertIs(read_epochs.validate_trace(
            trace, trace["scope"], count_limit=32, file_limit=1024,
        ), trace)
        for defect in ("serial", "owner", "identity"):
            changed = copy.deepcopy(trace)
            selected = changed["selection"]["inventory"][1]
            opened = [row for row in changed["events"] if row["kind"] == "source-open"][1]
            if defect == "serial":
                opened["custody"]["serial"] += 1
            elif defect == "owner":
                opened["custody"]["owner"] = "f" * 64
            else:
                selected["identity"][3] += 1
            with self.subTest(defect=defect), self.assertRaises(read_epochs.ReadEpochError):
                read_epochs.validate_trace(changed, changed["scope"], count_limit=32, file_limit=1024)

    def test_newly_published_source_versions_can_contribute_only_frozen_names(self):
        trace = self.trace_data_v4()
        opened = [row for row in trace["events"] if row["kind"] == "source-open"][1]
        source = trace["sources"][1]
        data = b"CAP := generated-value\n"
        source.update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                      data=base64.b64encode(data).decode())
        opened["path"] = "build/remade.mk"
        opened["name"] = "./build/remade.mk"
        trace["events"][6]["name"] = "build/remade.mk"
        trace["events"][8]["resolved"] = "build/remade.mk"
        opened["custody"] = {
            "kind": "publication", "event": 1, "producer": 1, "slot": 0, "owner": "d" * 64,
        }
        opened["identity"][3] = len(data)
        trace["events"][7]["identity"][3] = len(data)
        site, = read_epochs.completion_sites("build/remade.mk", data, trace["selection"]["names"])
        completion = {
            "kind": "assignment-completion", "exec": 1, "pass": 1, "visit": 2, "source": 2,
            "site": site, "name": "CAP", "operator": ":=", "cwd": "/repo",
            "variable": ["CAP", "generated-value", 2 << 26, "build/remade.mk", 1, 0],
        }
        trace["events"].insert(8, completion)
        for sequence, event in enumerate(trace["events"], 1):
            event["seq"] = sequence
        self.assertIs(read_epochs.validate_trace(
            trace, trace["scope"], count_limit=32, file_limit=1024,
        ), trace)
        archive = read_epochs.reconstruct_archive(trace, budget=ProbeBudget())
        self.assertEqual(
            (archive.passes[0].visits[1].opens[0].name, archive.passes[0].visits[1].opens[0].path),
            ("./build/remade.mk", "build/remade.mk"),
        )
        wrong_exit = copy.deepcopy(trace)
        next(
            row for row in wrong_exit["events"]
            if row["kind"] == "source-exit" and row["visit"] == 2
        )["resolved"] = "build/foreign.mk"
        with self.assertRaises(read_epochs.ReadEpochError):
            read_epochs.validate_trace(wrong_exit, wrong_exit["scope"], count_limit=32, file_limit=1024)
        late = copy.deepcopy(trace)
        late_data = b"include $(LATE)\n"
        late["sources"][1].update(
            bytes=len(late_data), sha256=hashlib.sha256(late_data).hexdigest(),
            data=base64.b64encode(late_data).decode(),
        )
        late_open = next(row for row in late["events"] if row["kind"] == "source-open")
        late_open["identity"][3] = len(late_data)
        with self.assertRaises(read_epochs.ReadEpochError):
            read_epochs.validate_trace(late, late["scope"], count_limit=32, file_limit=1024)

    def test_actual_source_open_pins_sites_before_resuming_or_refuses_late_consumers(self):
        for data, accepted in ((b"CAP := generated-value\n", True), (b"include $(LATE)\n", False)):
            trace = object.__new__(read_trace.NativeReadTrace)
            caller, frame, address, descriptor = 0x1020, 0x2000, 0x3000, 5
            mode = stat.S_IFREG | 0o644
            identity = (1, 3, mode, len(data), 4, 5, 1)
            info = SimpleNamespace(st_mode=mode, st_size=len(data))
            closed, events = [], []
            notifications = {address: struct.pack("<QQQQqII", caller, frame, 1, 2, -1, 0, 0)}
            trace.pid, trace.bias, trace.version = 17, 0x1000, 4
            trace.abi = {"source_opens": [0x20]}
            trace.active = [{"frame": frame, "return": 0x5555, "visit": 2, "source": None}]
            trace.io = None
            trace.config = {"file_limit": 1024, "observation_count": 32}
            trace.selection_names = frozenset({"CAP"})
            trace.selection_inventory = {}
            trace.native = SimpleNamespace(publication_identity=lambda value: identity)
            trace.policy = SimpleNamespace(
                charge_metadata=lambda size: None,
                source_effects=SimpleNamespace(
                    publication_entry=lambda path, actual: {
                        "kind": "publication", "event": 1, "producer": 1, "slot": 0, "owner": "d" * 64,
                    },
                ),
            )
            trace.execs, trace.passes = 1, 1
            trace.events, trace.sources, trace.pool, trace.statement_indexes = [], [], {}, {}
            trace.deadline = lambda: None
            trace.context = lambda: {"exec": 1, "pass": 1}
            trace.number = lambda location: 0x5555 if location == frame + 8 else 0
            trace.string = lambda pointer, limit: {1: "build/remade.mk", 2: "r"}.get(pointer)
            trace.memory = lambda location, size: notifications[location]
            trace.event = lambda kind, **fields: events.append({"kind": kind, **fields})
            fake_os = SimpleNamespace(
                O_RDONLY=0, O_CLOEXEC=0, open=lambda *args, **kwargs: 77,
                fstat=lambda fd: info, pread=lambda fd, count, offset: data[offset:offset + count],
                close=lambda fd: closed.append(fd),
            )
            state = SimpleNamespace(fds={descriptor: "/repo/build/remade.mk"})
            with patch.object(read_trace, "os", fake_os):
                trace.source_io(17, state, address, 48)
                notifications[address] = struct.pack(
                    "<QQQQqII", caller, frame, 1, 2, descriptor, 1, 0,
                )
                if accepted:
                    trace.source_io(17, state, address, 48)
                else:
                    with self.assertRaises(read_epochs.ReadEpochError):
                        trace.source_io(17, state, address, 48)
            with self.subTest(accepted=accepted):
                self.assertEqual(bool(events), accepted)
                self.assertEqual(closed, [] if accepted else [77])
                if accepted:
                    self.assertEqual(events[0]["custody"]["kind"], "publication")
                    self.assertEqual(
                        [row[5] for row in trace.active[-1]["selection_index"].values()],
                        ["CAP"],
                    )

    def test_repeated_path_versions_keep_visit_specific_completion_sites(self):
        trace = self.trace_data_v4()
        root_data = b"CAP := $(shell model-only)\ninclude child.mk\ninclude child.mk\n"
        first_data, second_data = b"CAP := first\n", b"CAP := second\n"
        root, first = trace["sources"]
        root.update(bytes=len(root_data), sha256=hashlib.sha256(root_data).hexdigest(),
                    data=base64.b64encode(root_data).decode())
        first.update(bytes=len(first_data), sha256=hashlib.sha256(first_data).hexdigest(),
                     data=base64.b64encode(first_data).decode())
        trace["selection"]["inventory"][0].update(size=len(root_data), sha256=root["sha256"])
        trace["selection"]["inventory"][1].update(size=len(first_data), sha256=first["sha256"])
        trace["selection"]["scan"]["bytes"] = len(root_data) + len(first_data)
        trace["events"][4]["identity"][3] = len(root_data)
        trace["events"][5]["site"], = read_epochs.completion_sites(
            "Makefile", root_data, trace["selection"]["names"],
        )
        trace["events"][7]["identity"][3] = len(first_data)
        first_site, = read_epochs.completion_sites("child.mk", first_data, trace["selection"]["names"])
        first_completion = {
            "kind": "assignment-completion", "exec": 1, "pass": 1, "visit": 2, "source": 2,
            "site": first_site, "name": "CAP", "operator": ":=", "cwd": "/repo",
            "variable": ["CAP", "first", 2 << 26, "child.mk", 1, 0],
        }
        trace["events"].insert(8, first_completion)

        trace["sources"].append({
            "id": 3, "mode": 0o644, "bytes": len(second_data),
            "sha256": hashlib.sha256(second_data).hexdigest(),
            "data": base64.b64encode(second_data).decode(),
        })
        second_site, = read_epochs.completion_sites("child.mk", second_data, trace["selection"]["names"])
        trace["events"][10:10] = [
            {"kind": "source-entry", "exec": 1, "pass": 1, "visit": 3, "parent": 1,
             "name": "child.mk", "flags": 0, "location": [1, 1, 3, 3, 3]},
            {"kind": "source-open", "exec": 1, "pass": 1, "visit": 3, "name": "child.mk",
             "mode": "r", "result": 5, "source": 3,
             "identity": [1, 4, 0o100644, len(second_data), 4, 5, 1],
             "path": "child.mk",
             "custody": {"kind": "publication", "event": 2, "producer": 1, "slot": 1,
                         "owner": "d" * 64}},
            {"kind": "assignment-completion", "exec": 1, "pass": 1, "visit": 3, "source": 3,
             "site": second_site, "name": "CAP", "operator": ":=", "cwd": "/repo",
             "variable": ["CAP", "second", 2 << 26, "child.mk", 1, 0]},
            {"kind": "source-exit", "exec": 1, "pass": 1, "visit": 3, "resolved": "child.mk",
             "flags": 0, "error": 0, "source": 3},
        ]
        trace["events"][-2]["goals"] = [1, 2, 3]
        trace["events"][-1]["visits"] = 3
        for sequence, event in enumerate(trace["events"], 1):
            event["seq"] = sequence
        self.assertIs(read_epochs.validate_trace(
            trace, trace["scope"], count_limit=32, file_limit=1024,
        ), trace)

    def test_closed_archive_reconstructs_immutable_sparse_rows_and_original_locations(self):
        trace = self.trace_data()
        budget = ProbeBudget()
        archive = read_epochs.reconstruct_archive(trace, budget=budget)
        part, = archive.passes
        receipt, = part.completions
        self.assertEqual(archive.version, 3)
        self.assertEqual(receipt.variable.value, "model-only")
        self.assertEqual((receipt.seq, receipt.visit, receipt.cwd), (6, 1, "/repo"))
        self.assertEqual(part.visits[1].location, (1, 1, 2, 2, 2))
        with self.assertRaises(AttributeError):
            receipt.cwd = "/foreign"
        trace["events"][5]["variable"][1] = "mutated-after-data-reconstruction"
        self.assertEqual(receipt.variable.value, "model-only")
        self.assertGreater(budget.bytes["cache"], 0)
        self.assertEqual(budget.runs, 0)

    def test_old_archives_cannot_smuggle_new_rows_or_locations(self):
        for version in (1, 2, True, 4):
            trace = self.trace_data()
            trace["version"] = version
            with self.subTest(version=version), self.assertRaises(read_epochs.ReadEpochError):
                read_epochs.validate_trace(trace, trace["scope"], count_limit=32, file_limit=1024)

    def test_completion_foreign_corrupt_unordered_and_exact_over_bounds_reject(self):
        for defect in (
            "exec", "pass", "visit", "source", "name", "operator", "cwd", "variable",
            "site", "selection", "bytes", "location", "repeat", "count", "file",
        ):
            trace = self.trace_data()
            row = trace["events"][5]
            count, file_limit = len(trace["events"]), 1024
            if defect in {"exec", "pass", "visit", "source"}:
                row[defect] += 1
            elif defect in {"name", "operator", "cwd"}:
                row[defect] = "foreign"
            elif defect == "variable":
                row["variable"][2] |= 128
            elif defect == "site":
                row["site"][3] += 1
            elif defect == "selection":
                trace["selection"].append(list(trace["selection"][0]))
            elif defect == "bytes":
                trace["sources"][0]["data"] = ""
            elif defect == "location":
                trace["events"][6]["location"][1] = 2
            elif defect == "repeat":
                trace["events"].insert(6, copy.deepcopy(row))
                for sequence, event in enumerate(trace["events"], 1):
                    event["seq"] = sequence
                count += 1
            elif defect == "count":
                count -= 1
            else:
                file_limit = trace["sources"][0]["bytes"] - 1
            with self.subTest(defect=defect), self.assertRaises(read_epochs.ReadEpochError):
                read_epochs.validate_trace(trace, trace["scope"], count_limit=count, file_limit=file_limit)
        exact = self.trace_data()
        self.assertIs(read_epochs.validate_trace(
            exact, exact["scope"], count_limit=len(exact["events"]), file_limit=1024,
        ), exact)

    def test_physical_spans_preserve_crlf_continuations_and_final_no_lf(self):
        for data in (b"A := first\\\n next\nB := final", b"A := first\\\r\n next\r\nB := final"):
            self.assertEqual(read_epochs.statement_at(data, 1, 2), (1, 1, 2, "A := first\\\n next"))
            self.assertEqual(read_epochs.statement_at(data, 3, 1), (2, 3, 3, "B := final"))
            with self.assertRaises(read_epochs.ReadEpochError):
                read_epochs.statement_at(data, 1, 1)

    def model_trace(self):
        trace = object.__new__(read_trace.NativeReadTrace)
        trace.version, trace.bias, trace.pid = 3, 0x10000, 17
        trace.abi = {
            "read_all": [0x100, 0x200], "source": [0x300, 0x400],
            "globals": {"reading_file": 0x600},
            "completion": {
                "pc": 0x500, "reader_ebuffer": -128, "reader_floc": -88,
                "reader_return": 0x380, "eval_return": 0x900,
                "eval_ebuffer": -304, "eval_floc": -352, "modifiers": -236, "nlines": -288,
            },
        }
        trace.pass_frame = {"return": 0x20000}
        trace.config = {"observation_count": 1000, "deadline": 1e20}
        trace.events, trace.traps, trace.writes, trace.reads = [], 0, [], []
        trace.policy = SimpleNamespace(charge_metadata=lambda size: trace.writes.append(("charge", size)))
        trace.debug = lambda pid, index, value: trace.writes.append((index, value))
        trace.execs = trace.passes = 1
        trace.io = None
        trace.sources = self.trace_data()["sources"]
        trace.selection = tuple(tuple(row) for row in self.trace_data()["selection"])
        trace.selection_index = MappingProxyType({row[:5]: row for row in trace.selection})
        trace.statement_indexes = {
            1: read_epochs._statement_index(base64.b64decode(trace.sources[0]["data"], validate=True)),
        }
        trace.active = [{
            "return": 0x20100, "frame": 0x1000, "visit": 1, "name": "Makefile",
            "source": 1, "pin": 37, "closed": False, "identity": ("modeled-pin",),
        }]
        trace.native = SimpleNamespace(publication_identity=lambda value: value)
        self.memory_values = {
            (0xE00, 16): struct.pack("<QQ", 0x1000, trace.bias + 0x380),
            (0xE00 - 304, 8): (0xF80).to_bytes(8, "little"),
            (0xE00 - 352, 8): (0xFA8).to_bytes(8, "little"),
            (trace.bias + 0x600, 8): (0xFA8).to_bytes(8, "little"),
            (0xF80 + 32, 8): (0x7000).to_bytes(8, "little"),
            (0xFA8, 8): (0x8000).to_bytes(8, "little"),
            (0xFA8 + 8, 16): struct.pack("<QQ", 1, 999),
            (0xE00 - 288, 8): (1).to_bytes(8, "little"),
            (0xE00 - 236, 4): (1).to_bytes(4, "little"),
            (0x9000, 48): struct.pack("<QQQQQII", 0x8001, 0x8002, 0x8000, 1, 999, 3, 2 << 26),
        }
        def memory(address, count):
            trace.reads.append((address, count))
            if (address, count) not in self.memory_values:
                raise AssertionError("unadmitted memory read " + repr((address, count)))
            return self.memory_values[address, count]
        trace.memory = memory
        strings = {0x8000: "Makefile", 0x8001: "CAP", 0x8002: "model-only"}
        trace.string = lambda address, maximum: strings[address]
        return trace, SimpleNamespace(rsp=0xD00, rbp=0xE00, rbx=0x9000)

    def test_four_slots_keep_entries_and_restore_pass_return_at_every_depth(self):
        for depth in range(1, 9):
            trace, _ = self.model_trace()
            trace.active = [{"return": 0x20100 + index} for index in range(depth)]
            for count in range(depth, 0, -1):
                trace.arm()
                self.assertEqual(trace.slots[0], trace.bias + 0x100)
                self.assertEqual(trace.slots[1], trace.bias + 0x300)
                self.assertEqual(trace.slots[2], 0x20100 + count - 1)
                self.assertEqual(trace.purposes[3], "assignment-completion")
                self.assertLessEqual(len(trace.slots), 4)
                trace.active.pop()
            trace.arm()
            self.assertNotIn(2, trace.slots)
            self.assertEqual(trace.slots[3], trace.pass_frame["return"])
            self.assertEqual(trace.purposes[3], "pass-return")
        trace.active = [{"return": trace.bias + 0x500}]
        with self.assertRaises(read_epochs.ReadEpochError):
            trace.arm()

    def test_completion_predicate_and_undefine_guard_precede_variable_dereference(self):
        for defect in (None, "undefine", "define", "private", "saved", "return", "buffer", "floc", "fp", "nlines", "pin"):
            trace, registers = self.model_trace()
            if defect in {"undefine", "define", "private"}:
                modifiers = {"undefine": 4, "define": 3, "private": 33}[defect]
                self.memory_values[0xE00 - 236, 4] = modifiers.to_bytes(4, "little")
                registers.rbx = 0xBAD
            elif defect == "saved":
                self.memory_values[0xE00, 16] = struct.pack("<QQ", 0x1001, trace.bias + 0x380)
            elif defect == "return":
                self.memory_values[0xE00, 16] = struct.pack("<QQ", 0x1000, trace.bias + 0x381)
            elif defect in {"buffer", "floc", "fp", "nlines"}:
                address = {"buffer": 0xE00 - 304, "floc": trace.bias + 0x600,
                           "fp": 0xF80 + 32, "nlines": 0xE00 - 288}[defect]
                self.memory_values[address, 8] = bytes(8)
            with patch.object(read_trace, "os", SimpleNamespace(
                fstat=lambda pin: ("foreign-pin",) if defect == "pin" else ("modeled-pin",),
            )):
                if defect is None:
                    trace.assignment_completion(registers, SimpleNamespace(cwd="/repo"))
                    self.assertEqual(trace.events[0]["variable"][1], "model-only")
                    self.assertEqual(trace.events[0]["cwd"], "/repo")
                    self.assertEqual(trace.events[0]["site"][2:5], [1, 1, 1])
                else:
                    with self.subTest(defect=defect), self.assertRaises(read_epochs.ReadEpochError):
                        trace.assignment_completion(registers, SimpleNamespace(cwd="/repo"))
                    self.assertNotIn((registers.rbx, 48), trace.reads)
                    self.assertEqual(trace.events, [])

    def test_copied_floc_eval_is_not_original_source_or_variable_authority(self):
        trace, registers = self.model_trace()
        self.memory_values[0xE00, 16] = struct.pack("<QQ", 0xF00, trace.bias + 0x900)
        self.memory_values[0xE00 - 304, 8] = (0xA000).to_bytes(8, "little")
        self.memory_values[0xE00 - 352, 8] = (0xA028).to_bytes(8, "little")
        self.memory_values[trace.bias + 0x600, 8] = (0xA028).to_bytes(8, "little")
        self.memory_values[0xA000 + 32, 8] = bytes(8)
        trace.assignment_completion(registers, SimpleNamespace(cwd="/repo"))
        self.assertEqual(trace.events, [])
        self.assertNotIn((registers.rbx, 48), trace.reads)
        with self.assertRaises(read_epochs.ReadEpochError):
            trace.source_location(registers, trace.active[-1], allow_eval=False)

    def test_modeled_kernel_purpose_dispatch_and_outer_return_restore(self):
        class Registers(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("rip", "rsp", "rax", "eflags")]

        for defect in (None, "pid", "signal", "pc", "multiple", "purpose", "reentry", "outer"):
            trace, _ = self.model_trace()
            trace.arm()
            seen = []
            registers = Registers(trace.slots[3], 0, 0, 0)
            fired = 8
            if defect == "pc":
                registers.rip += 1
            elif defect == "multiple":
                fired = 12
            elif defect == "purpose":
                trace.purposes[3] = "unissued"
            elif defect == "reentry":
                fired, registers.rip = 1, trace.slots[0]
            elif defect == "outer":
                current = trace.active[-1]
                current.update(stack=0x1008, source=None, pin=None, flags=0)
                registers.rip, registers.rsp, registers.rax = trace.slots[2], 0x1010, 0xA000
                self.memory_values[0xA000, 64] = struct.pack(
                    "<QQQQIiQQQ", 0, 0x8000, 0, 0, 0, 2, 0, 0, 0,
                )
                fired, trace.goals = 4, {}
            def ptrace(request, pid, address, pointer):
                if request == read_trace.GETSIGINFO:
                    info = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ubyte * 128)).contents
                    info[8:12] = (3 if defect == "signal" else 4).to_bytes(4, "little")
                elif request == 12:
                    ctypes.memmove(pointer, ctypes.byref(registers), ctypes.sizeof(registers))
                elif request == 13:
                    changed = ctypes.cast(pointer, ctypes.POINTER(Registers)).contents
                    seen.append(("resume-flag", bool(changed.eflags & (1 << 16))))
                else:
                    raise AssertionError("unadmitted ptrace request")
            def debug(pid, index, value=None):
                if value is None:
                    self.assertEqual(index, 6)
                    return fired
                trace.writes.append((index, value))
            trace.native = SimpleNamespace(Registers=Registers, GETREGS=12, SETREGS=13, ptrace=ptrace)
            trace.debug = debug
            trace.assignment_completion = lambda *args: seen.append(("completion",))
            if defect in {None, "outer"}:
                trace.trap(17, SimpleNamespace(role="make"))
                self.assertIn(("resume-flag", True), seen)
                if defect is None:
                    self.assertIn(("completion",), seen)
                else:
                    self.assertEqual(trace.purposes[3], "pass-return")
                    self.assertEqual(trace.slots[3], trace.pass_frame["return"])
                    self.assertEqual(trace.events[0]["kind"], "source-exit")
            else:
                with self.subTest(defect=defect), self.assertRaises(read_epochs.ReadEpochError):
                    trace.trap(18 if defect == "pid" else 17, SimpleNamespace(role="make"))
                self.assertEqual(seen, [])


class SourcePinLifetimeTests(unittest.TestCase):
    """Actual source-return proofs and pin retirement with only inert effects."""

    def setUp(self):
        from scripts.validation_ownership import lifecycle, read_trace
        self.life, self.subject = lifecycle, read_trace
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(read_trace, "os", SimpleNamespace(
            fstat=self.fstat, close=self.close_descriptor,
        )))
        self.stack.enter_context(patch.object(lifecycle, "signal", SimpleNamespace(
            SIG_BLOCK=signal.SIG_BLOCK, SIG_SETMASK=signal.SIG_SETMASK,
            pthread_sigmask=lambda *args: set(), sigpending=lambda: set(),
        )))
        self.make_trace()

    def make_trace(self, pins=(17,)):
        self.live, self.frames, self.closes, self.stats, self.faults = {}, [], [], [], {}
        for visit, pin in enumerate(pins, 1):
            identity = (3, 100 + visit, 0o100644, 7, 11, 12, 1)
            if pin is not None:
                self.live[pin] = ("source-" + str(visit), identity)
            self.frames.append({
                "visit": visit, "stack": 100, "flags": 0,
                "source": visit if pin is not None else None, "pin": pin,
                "closed": True, "identity": identity, "name": "Makefile", "path": "/repo/Makefile",
            })
        self.trace = object.__new__(self.subject.NativeReadTrace)
        self.trace.active, self.trace.io, self.trace.goals = list(self.frames), None, {}
        self.trace.execs = self.trace.passes = 1
        self.trace.events, self.charges = [], []
        self.trace.config = {"observation_count": 8}
        self.trace.policy = SimpleNamespace(charge_metadata=self.charges.append)
        self.trace.native = SimpleNamespace(publication_identity=lambda info: info, posixpath=posixpath)
        self.trace.pass_frame = {"stack": 80}
        self.trace.memory, self.trace.string = self.memory, self.string
        self.registers = SimpleNamespace(rsp=108, rax=900)
        self.values = [0, 200, 300, 0, 0, 0, 0, 0, 0]
        self.resolved, self.reads, self.strings = "Makefile", [], []
        self.raw = None
        return self.trace

    def memory(self, address, count):
        self.reads.append((address, count))
        if (address, count) == (900, 64):
            return struct.pack("<QQQQIiQQQ", *self.values) if self.raw is None else self.raw
        self.assertEqual((address, count), (300, 8))
        return (200).to_bytes(8, "little")

    def string(self, address, maximum):
        self.assertEqual((address, maximum), (200, 4096))
        self.strings.append((address, maximum))
        return self.resolved

    def fstat(self, pin):
        self.stats.append(pin)
        self.assertIn(pin, self.live)
        return self.live[pin][1]

    def close_descriptor(self, pin):
        self.assertIn(pin, self.live, "close of an unavailable modeled descriptor")
        owner = self.live[pin]
        self.closes.append((pin, owner, tuple(frame["pin"] for frame in self.frames)))
        error, after = self.faults.get(pin, (None, False))
        if error is not None and not after:
            raise error
        del self.live[pin]
        if error is not None:
            raise error

    def test_source_return_proves_identity_status_and_name_then_closes_once(self):
        for pin in (0, 17):
            for indirect in (False, True):
                with self.subTest(pin=pin, indirect=indirect):
                    trace = self.make_trace((pin,))
                    frame = self.frames[0]
                    self.values[1] = 0 if indirect else 200
                    self.values[4] = 1 << 16
                    self.resolved = "/repo/Makefile" if indirect else "Makefile"
                    trace.source_return(self.registers)
                    self.assertEqual(self.stats, [pin])
                    self.assertEqual(self.reads, [(900, 64), (300, 8)] if indirect else [(900, 64)])
                    self.assertEqual(self.strings, [(200, 4096)])
                    self.assertEqual(self.closes, [(pin, ("source-1", frame["identity"]), (None,))])
                    self.assertIsNone(frame["pin"])
                    self.assertEqual(trace.goals, {900: 1})
                    self.assertEqual(trace.events, [{
                        "seq": 1, "kind": "source-exit", "exec": 1, "pass": 1, "visit": 1,
                        "resolved": self.resolved, "flags": 1 << 16, "error": 0, "source": 1,
                    }])
                    self.assertEqual(self.charges, [len(self.subject.encoded(trace.events[0]))])
                    self.assertEqual(trace.active, [])
                    self.assertEqual(self.live, {})
                    trace.close()
                    trace.close()
                    self.assertEqual(len(self.closes), 1)

    def test_source_return_preserves_missing_source_status_without_a_pin(self):
        trace = self.make_trace((None,))
        self.values[5] = errno.ENOENT
        self.resolved = "missing.mk"
        trace.source_return(self.registers)
        trace.close()
        self.assertEqual(trace.events, [{
            "seq": 1, "kind": "source-exit", "exec": 1, "pass": 1, "visit": 1,
            "resolved": "missing.mk", "flags": 0, "error": errno.ENOENT, "source": None,
        }])
        self.assertEqual(trace.goals, {900: 1})
        self.assertEqual(trace.active, [])
        self.assertEqual(self.closes, [])
        self.assertEqual(self.stats, [])

    def test_post_release_return_error_never_retries_a_reused_descriptor(self):
        for error in (OSError(errno.EINTR, "inert post-release close"), KeyboardInterrupt("inert interrupt")):
            with self.subTest(error=type(error).__name__):
                trace = self.make_trace()
                frame = self.frames[0]
                self.faults[17] = (error, True)
                with self.assertRaises(type(error)) as caught:
                    trace.source_return(self.registers)
                self.assertIs(caught.exception, error)
                self.assertIsNone(frame["pin"])
                self.assertEqual(self.live, {})
                self.assertEqual(trace.events, [])
                self.assertEqual(trace.goals, {})
                self.assertEqual(trace.active, [frame])
                foreign = ("foreign-reused", (9, 999, 0o100600, 1, 1, 1, 1))
                self.live[17] = foreign
                trace.close()
                trace.close()
                self.assertIs(self.live[17], foreign)
                self.assertEqual(self.closes, [(17, ("source-1", frame["identity"]), (None,))])
                with self.assertRaises(read_epochs.ReadEpochError):
                    trace.finish()
                self.assertEqual(trace.events, [])

    def test_pre_release_close_fault_does_not_restore_uncertain_pin_authority(self):
        error = OSError(errno.EIO, "inert ambiguous close")
        self.faults[17] = (error, False)
        original = self.live[17]
        with self.assertRaises(OSError) as caught:
            self.trace.source_return(self.registers)
        self.assertIs(caught.exception, error)
        self.assertIsNone(self.frames[0]["pin"])
        self.trace.close()
        self.trace.close()
        self.assertIs(self.live[17], original)
        self.assertEqual(len(self.closes), 1)
        self.assertEqual(self.trace.events, [])
        with self.assertRaises(read_epochs.ReadEpochError):
            self.trace.finish()

    def test_source_return_rejections_keep_proof_checks_and_cleanup_custody(self):
        defects = (
            "entry", "io", "stack", "goal", "flags", "negative-error", "overflow-error",
            "missing-source", "source-error", "name", "stream", "path", "truncated",
            *(("identity", index) for index in range(7)),
        )
        for defect in defects:
            with self.subTest(defect=defect):
                trace = self.make_trace(() if defect == "entry" else (17,))
                if defect == "io":
                    trace.io = ("pending-source-stream",)
                elif defect == "stack":
                    self.registers.rsp += 8
                elif defect == "goal":
                    trace.goals[900] = 5
                elif defect == "flags":
                    self.values[4] = 1
                elif defect in ("negative-error", "overflow-error", "source-error"):
                    self.values[5] = {"negative-error": -1, "overflow-error": 4096, "source-error": 2}[defect]
                elif defect == "missing-source":
                    self.frames[0]["source"] = None
                elif defect == "name":
                    self.resolved = ""
                elif defect == "stream":
                    self.frames[0]["closed"] = False
                elif defect == "path":
                    self.resolved = "different.mk"
                elif defect == "truncated":
                    self.raw = bytes(63)
                elif type(defect) is tuple:
                    owner, identity = self.live[17]
                    changed = list(identity)
                    changed[defect[1]] += 1
                    self.live[17] = (owner, tuple(changed))
                with self.assertRaises((read_epochs.ReadEpochError, struct.error)):
                    trace.source_return(self.registers)
                self.assertEqual(trace.events, [])
                self.assertEqual(self.closes, [])
                if self.frames:
                    self.assertEqual(self.frames[0]["pin"], 17)
                trace.close()
                self.assertEqual([call[0] for call in self.closes], [] if defect == "entry" else [17])
                self.assertEqual(self.live, {})
                self.assertTrue(all(frame["pin"] is None for frame in self.frames))

    def test_cleanup_detaches_every_pin_and_attempts_all_closes_without_retry(self):
        for faults in ((), (17,), (18,), (0, 17, 18)):
            for after in (False, True):
                with self.subTest(faults=faults, after=after):
                    trace = self.make_trace((0, None, 17, 18))
                    proof = [{key: value for key, value in frame.items() if key != "pin"}
                             for frame in self.frames]
                    self.faults = {pin: (OSError(errno.EIO, "inert pin " + str(pin)), after) for pin in faults}
                    if faults:
                        with self.assertRaises(OSError) as caught:
                            trace.close()
                        self.assertIs(caught.exception, self.faults[faults[0]][0])
                        notes = getattr(caught.exception, "cleanup_errors", ())
                        self.assertEqual(len(notes), len(faults) - 1)
                        for pin in faults[1:]:
                            self.assertTrue(any(str(self.faults[pin][0]) in note for note in notes), notes)
                    else:
                        trace.close()
                    self.assertEqual({call[0] for call in self.closes}, {0, 17, 18})
                    self.assertEqual(len(self.closes), 3)
                    self.assertTrue(all(call[2] == (None,) * 4 for call in self.closes))
                    self.assertTrue(all(frame["pin"] is None for frame in self.frames))
                    self.assertEqual(self.live.keys(), set(faults) if not after else set())
                    self.assertEqual(proof, [{key: value for key, value in frame.items() if key != "pin"}
                                             for frame in self.frames])
                    if after:
                        self.live.update({pin: ("foreign-reused", (9, pin)) for pin in faults})
                    retained = dict(self.live)
                    trace.close()
                    trace.close()
                    self.assertEqual(self.live, retained)
                    self.assertEqual(len(self.closes), 3)
                    self.assertEqual(trace.events, [])
                    self.assertEqual(trace.goals, {})
                    with self.assertRaises(read_epochs.ReadEpochError):
                        trace.finish()

    def test_earlier_primary_survives_pin_cleanup_and_remaining_owned_actions(self):
        for after in (False, True):
            with self.subTest(after=after):
                trace = self.make_trace((17, 18, 19))
                original = read_epochs.ReadEpochError("inert original source read failure")
                close_error = OSError(errno.EIO, "inert pin cleanup")
                later_error = OSError(errno.ENOSPC, "inert later cleanup")
                self.faults[17] = (close_error, after)
                remaining = []
                def read(address, count):
                    raise original
                def later():
                    remaining.append("attempted")
                    raise later_error
                trace.memory = read
                with self.assertRaises(read_epochs.ReadEpochError) as caught:
                    with self.life.cleanup_scope([trace.close, later]):
                        trace.source_return(self.registers)
                self.assertIs(caught.exception, original)
                self.assertEqual({call[0] for call in self.closes}, {17, 18, 19})
                self.assertEqual(len(self.closes), 3)
                self.assertTrue(all(call[2] == (None,) * 3 for call in self.closes))
                self.assertEqual(remaining, ["attempted"])
                self.assertEqual(len(original.cleanup_errors), 2)
                for error in (close_error, later_error):
                    self.assertTrue(any(str(error) in note for note in original.cleanup_errors))
                trace.close()
                self.assertEqual(len(self.closes), 3)
                self.assertEqual(trace.events, [])


class ReadEpochTests(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.FoundationTests()
        self.fixture.setUp()
        self.source = (
            ".DEFAULT_GOAL := all\n"
            "unexport CLI_LAZY\n"
            "SEEN := $(FLAG)\n"
            "override FLAG := source-final\n"
            "FILE_ONLY := file-value\n"
            "COPY := $(file <Makefile)\n"
            "$(eval EVAL_ONLY ?= eval-value)\n"
            "include child.mk\n"
            "-include build/remade.mk\n"
            "build/remade.mk:\n\tpython3 writer.py\n"
            "all: ;\n"
        )
        self.fixture.add("Makefile", self.source)
        self.fixture.add("child.mk", "CHILD_ONLY := child-value\nAGAIN := $(file <Makefile)\n")
        self.fixture.add("writer.py", (
            "from pathlib import Path\n"
            "out=Path('/work/build/remade.mk');out.parent.mkdir(parents=True,exist_ok=True)\n"
            "out.write_text('REMADE_ONLY := generated-value\\n')\n"
        ))

    def tearDown(self):
        self.fixture.tearDown()

    def observe(self, session, enabled):
        return session.make(
            "all", variables=("FLAG", "SEEN"), observe_read_epochs=enabled,
            assignments=(("command-line", "FLAG", "command-line"),
                         ("environment", "EPOCH_ENV", "original-environment"),
                         ("environment", "CLI_LAZY", "$(error snapshot must stay raw)")),
            commands={"python3 writer.py": Command(
                ("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",),
                outputs=("build/remade.mk",),
            )},
        )

    def archive_observation(self, session):
        original = session._make
        def with_phases(*args, **kwargs):
            kwargs["observe_source_phases"] = True
            return original(*args, **kwargs)
        with patch.object(session, "_make", with_phases):
            return self.observe(session, True)

    def test_original_archive_keeps_every_pass_input_status_and_source_version(self):
        with self.fixture.session(runtime_files=("/usr/include/build",)) as session:
            observed = self.archive_observation(session)
            runs = session.budget.runs
            archive = session._original_source_archive(observed)
            self.assertEqual(session.budget.runs, runs)
            self.assertEqual(archive.scope, observed.read_trace["scope"])
            self.assertEqual([part.number for part in archive.passes], [1, 2])
            for part in archive.passes:
                values = {row.name: row for scope in reversed(part.inputs) for row in scope.variables}
                self.assertEqual(values["FLAG"].value, "command-line")
                self.assertEqual((values["FLAG"].flags >> 26) & 7, 4)
                self.assertEqual(values["CLI_LAZY"].value, "$(error snapshot must stay raw)")
                self.assertTrue({"FILE_ONLY", "CHILD_ONLY", "EVAL_ONLY"}.isdisjoint(values))
                self.assertEqual([visit.name for visit in part.visits],
                                 ["Makefile", "child.mk", "build/remade.mk"])
                main, child, included = part.visits
                self.assertEqual(child.parent, main.number)
                self.assertGreater(main.exit_seq, child.exit_seq)
                self.assertEqual(main.source.data, self.source.encode())
                self.assertEqual(part.entry_image[0], part.entry_seq + 1)
                goals, = [event["goals"] for event in observed.read_trace["events"]
                          if event["kind"] == "pass-exit" and event["pass"] == part.number]
                self.assertEqual(part.goal_visits, tuple(goals))
            failed, loaded = [part.visits[-1] for part in archive.passes]
            self.assertEqual((failed.error, failed.source, failed.opens[0].result), (2, None, -2))
            self.assertEqual(loaded.error, 0)
            self.assertGreaterEqual(loaded.opens[0].result, 0)
            self.assertIs(loaded.source, loaded.opens[0].source)
            self.assertEqual(loaded.source.data, b"REMADE_ONLY := generated-value\n")
            self.assertIs(archive.passes[0].visits[0].source, archive.passes[1].visits[0].source)
            self.assertEqual(observed.semantics["domains"]["FLAG"]["value"], "source-final")
            self.assertIs(session._original_source_archive(observed), archive)
            self.assertEqual(session.budget.runs, runs)
        self.fixture.assert_clean(session)
        self.assertFalse(session._source_pass_archives)

    def test_archive_repeated_visits_and_nonsource_reads_are_not_collapsed(self):
        source = self.source.replace("include child.mk\n", "include child.mk\ninclude child.mk\n")
        source = source.replace("all: ;", "all: ; @printf '%s' '$(file <child.mk)'")
        self.fixture.add("Makefile", source)
        with self.fixture.session(runtime_files=("/usr/include/build",)) as session:
            observed = self.archive_observation(session)
            archive = session._original_source_archive(observed)
            for part in archive.passes:
                self.assertEqual([visit.name for visit in part.visits],
                                 ["Makefile", "child.mk", "child.mk", "build/remade.mk"])
                first, second = part.visits[1:3]
                self.assertNotEqual(first.number, second.number)
                self.assertLess(first.exit_seq, second.entry_seq)
                self.assertIs(first.source, second.source)
                self.assertEqual(len([item for item in part.other_opens if item.name == "Makefile"]), 3)
            outside = [item for part in archive.passes for item in part.other_opens if item.pass_number is None]
            self.assertTrue(outside)
            self.assertTrue(all(item.visit is None for item in outside))
            self.assertEqual(sum(len(part.visits) for part in archive.passes), 8)
        self.fixture.assert_clean(session)

    def test_archive_preserves_replaced_include_contents_between_real_visits(self):
        self.fixture.add("Makefile", (
            ".DEFAULT_GOAL := all\nexport MAKE_RESTARTS\n"
            "include build/remade.mk\n"
            "ifeq ($(MAKE_RESTARTS),1)\n"
            "TRIGGER := $(shell python3 writer.py)\ninclude build/remade.mk\nendif\n"
            "build/remade.mk:\n\tpython3 writer.py\nall: ;\n"
        ))
        self.fixture.add("writer.py", (
            "import os\nfrom pathlib import Path\n"
            "out=Path('/work/build/remade.mk');out.parent.mkdir(parents=True,exist_ok=True)\n"
            "out.write_text('VALUE := '+os.environ.get('MAKE_RESTARTS','initial')+'\\n')\n"
        ))
        with self.fixture.session(runtime_files=("/usr/include/build",)) as session:
            command = session._native_context_command(Command(
                ("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",), outputs=("build/remade.mk",),
            ))
            observed = session.make(
                "all", variables=("VALUE",), commands={"python3 writer.py": command}, observe_source_phases=True,
            )
            archive = session._original_source_archive(observed)
            pairs = [
                [visit for visit in part.visits if visit.name == "build/remade.mk"]
                for part in archive.passes
            ]
            repeated, = [visits for visits in pairs if len(visits) == 2]
            self.assertEqual([visit.error for visit in repeated], [0, 0])
            self.assertLess(repeated[0].exit_seq, repeated[1].entry_seq)
            self.assertNotEqual(repeated[0].source.data, repeated[1].source.data)
            self.assertNotEqual(repeated[0].opens[-1].identity, repeated[1].opens[-1].identity)
            writers = [row for row in observed.semantics["native_dispatches"] if row["arguments"][-1] == "writer.py"]
            expected = [("VALUE := " + row["environment"].get("MAKE_RESTARTS", "initial") + "\n").encode()
                        for row in writers]
            self.assertEqual([visit.source.data for visit in repeated], expected)
            self.assertTrue(any(visit.error == 2 and visit.source is None for visit in pairs[0]))
            self.assertEqual(len(archive.passes),
                             len([row for row in observed.read_trace["events"] if row["kind"] == "exec"]))
        self.fixture.assert_clean(session)

    def test_archive_rejects_unqualified_changed_copied_foreign_and_expired_observations(self):
        with self.fixture.session(runtime_files=("/usr/include/build",)) as session:
            unqualified = self.observe(session, True)
            with self.assertRaises(MakeProbeError):
                session._original_source_archive(unqualified)
            observed = self.archive_observation(session)
            archive = session._original_source_archive(observed)
            self.assertFalse(hasattr(archive, "__dict__"))
            with self.assertRaises(AttributeError):
                archive.passes[0].number = 99
            with self.assertRaises(TypeError):
                archive.passes[0].inputs[0].variables[0] = None
            with self.assertRaises(MakeProbeError):
                session._original_source_archive(replace(observed))
            entry = next(row for row in observed.read_trace["events"] if row["kind"] == "pass-entry")
            entry["inputs"][0]["variables"][0][1] += "-changed"
            with self.assertRaises(MakeProbeError):
                session._original_source_archive(observed)
        self.fixture.assert_clean(session)
        with self.fixture.session(runtime_files=("/usr/include/build",)) as other:
            with self.assertRaises(MakeProbeError):
                other._original_source_archive(observed)
        self.fixture.assert_clean(other)
        with self.assertRaises(MakeProbeError):
            session._original_source_archive(observed)

    def test_archive_decoder_revalidates_actual_status_order_inputs_and_source_bytes(self):
        with self.fixture.session(runtime_files=("/usr/include/build",)) as session:
            observed = self.archive_observation(session)
            for defect in ("status", "missing-visit", "input", "source", "negative-return-flags", "overflow-return-flags"):
                changed = copy.deepcopy(observed.read_trace)
                if defect == "status":
                    next(row for row in changed["events"] if row["kind"] == "source-exit" and row["error"])["error"] = 0
                elif defect == "missing-visit":
                    changed["events"] = [row for row in changed["events"]
                                         if not (row["kind"] == "source-entry" and row["visit"] == 2)]
                    for number, row in enumerate(changed["events"], 1):
                        row["seq"] = number
                elif defect == "input":
                    next(row for row in changed["events"] if row["kind"] == "pass-entry")["inputs"][0]["variables"][0][2] = True
                elif defect == "source":
                    changed["sources"][0]["data"] = ""
                else:
                    event = next(row for row in changed["events"] if row["kind"] == "source-exit")
                    event["flags"] += -(1 << 32) if defect == "negative-return-flags" else 1 << 32
                with self.subTest(defect=defect), self.assertRaises(read_epochs.ReadEpochError):
                    read_epochs.reconstruct_archive(changed, budget=session.budget)
        self.fixture.assert_clean(session)

    def test_actual_inputs_source_status_and_reexec_are_independent_of_final_values(self):
        with self.fixture.session(runtime_files=("/usr/include/build",)) as session:
            result = self.observe(session, True)
            trace = result.read_trace
            self.assertIsNotNone(trace)
            self.assertTrue(trace["complete"])
            entries = [row for row in trace["events"] if row["kind"] == "pass-entry"]
            self.assertEqual(len(entries), 2)
            for entry in entries:
                values = {
                    row[0]: row for scope in reversed(entry["inputs"]) for row in scope["variables"]
                }
                self.assertEqual(values["FLAG"][1], "command-line")
                self.assertEqual((values["FLAG"][2] >> 26) & 7, 4)
                self.assertEqual(values["EPOCH_ENV"][1], "original-environment")
                self.assertEqual(values["CLI_LAZY"][1], "$(error snapshot must stay raw)")
                self.assertTrue({"FILE_ONLY", "CHILD_ONLY", "EVAL_ONLY"}.isdisjoint(values))
            self.assertEqual(result.semantics["domains"]["FLAG"]["value"], "source-final")
            visits = [row for row in trace["events"] if row["kind"] == "source-entry"]
            self.assertEqual([row["name"] for row in visits],
                             ["Makefile", "child.mk", "build/remade.mk"] * 2)
            returns = [row for row in trace["events"] if row["kind"] == "source-exit"
                       and row["resolved"] == "build/remade.mk"]
            self.assertEqual([row["error"] for row in returns], [2, 0])
            other = [row for row in trace["events"] if row["kind"] == "other-open" and row["name"] == "Makefile"]
            self.assertEqual(len(other), 4)
            snapshots = [base64.b64decode(row["data"]) for row in trace["sources"]]
            self.assertIn(self.source.encode(), snapshots)
            self.assertIn(b"REMADE_ONLY := generated-value\n", snapshots)
            self.assertEqual(len([row for row in trace["events"] if row["kind"] == "exec"]), 2)
        self.fixture.assert_clean(session)

    def test_default_off_make_retains_existing_observation_shape(self):
        with self.fixture.session(runtime_files=("/usr/include/build",)) as session:
            result = self.observe(session, False)
            self.assertIsNone(result.read_trace)
            self.assertIsNone(session._read_epoch_abi)
            self.assertEqual(result.semantics["domains"]["FLAG"]["value"], "source-final")
        self.fixture.assert_clean(session)

    def test_real_default_message_header_pipeline_keeps_all_original_read_passes(self):
        from scripts.validation_ownership.tests import test_header_pipeline as header_tests

        case = header_tests.ArmHeaderPipelineTests(
            "test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps",
        )
        case.setUp()
        captured = []
        original = make_probe.ProbeSession._make
        def observe(session, *args, **kwargs):
            kwargs["observe_read_epochs"] = True
            result = original(session, *args, **kwargs)
            captured.append(result)
            return result
        try:
            with patch.object(make_probe.ProbeSession, "_make", observe):
                case.test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps()
            self.assertEqual(len(captured), 1)
            trace = captured[0].read_trace
            entries = [row for row in trace["events"] if row["kind"] == "pass-entry"]
            self.assertEqual(len(entries), 2)
            visits = [row for row in trace["events"] if row["kind"] == "source-exit"]
            self.assertTrue(any(row["error"] == 2 for row in visits))
            self.assertTrue(any(row["resolved"] == case.target and row["error"] == 0 for row in visits))
            self.assertFalse(any(row["resolved"] == "src/msg_data.c" for row in visits))
            self.assertTrue(trace["complete"])
        finally:
            case.tearDown()

    def test_parse_time_producer_cannot_inherit_parent_read_breakpoints(self):
        self.fixture.add("Makefile", "CHECK := $(shell printf visible)\n" + self.source)
        with self.fixture.session(runtime_files=("/usr/include/build",)) as session:
            result = session.make(
                "all", variables=("CHECK",), observe_read_epochs=True,
                commands={
                    "printf visible": Command(("/usr/bin/printf", "visible")),
                    "python3 writer.py": Command(
                        ("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",),
                        outputs=("build/remade.mk",),
                    ),
                },
            )
            self.assertEqual(result.semantics["domains"]["CHECK"]["value"], "visible")
            self.assertEqual([row["exec"] for row in result.read_trace["events"] if row["kind"] == "exec"], [1, 2])
            self.assertEqual(len([row for row in result.read_trace["events"] if row["kind"] == "pass-entry"]), 2)
        self.fixture.assert_clean(session)

    def test_real_source_notification_cannot_be_forged_by_an_ordinary_command(self):
        body = (
            "import ctypes\n"
            "lib=ctypes.CDLL(None)\n"
            "record=(ctypes.c_ulonglong*6)()\n"
            "lib.syscall(ctypes.c_long(39),ctypes.c_ulonglong(0x564f4d4b00000008),"
            "ctypes.byref(record),ctypes.c_long(48))\n"
        )
        with self.fixture.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "unauthenticated"):
                session.command(Command(("/usr/bin/python3", "-c", body)))
        self.fixture.assert_clean(session)

    def test_native_self_signal_and_changed_observer_frame_do_not_forge_read_events(self):
        original = make_probe.ProbeSession._compile_interceptor
        marker = "record.caller = (uintptr_t)__builtin_return_address(0);"
        for defect in ("signal", "frame"):
            with self.subTest(defect=defect):
                directory = self.fixture.directory / ("observer-" + defect)
                directory.mkdir()
                for name in ("dispatch.h", "shell_interceptor.c"):
                    (directory / name).write_bytes((foundation.ROOT / "scripts/validation_ownership" / name).read_bytes())
                source = (foundation.ROOT / "scripts/validation_ownership/make_observer.c").read_text()
                if defect == "signal":
                    self.assertEqual(source.count(marker), 1)
                    source = source.replace(marker, "raw_call(SYS_tkill, make_pid, 5, 0);\n        " + marker)
                else:
                    needle = "record.frame = *(uintptr_t *)__builtin_frame_address(0);"
                    self.assertEqual(source.count(needle), 1)
                    source = source.replace(needle, "record.frame = *(uintptr_t *)__builtin_frame_address(0) + 8;")
                (directory / "make_observer.c").write_text(source)
                def compile_mutant(session):
                    with patch.object(make_probe, "TRUSTED_ROOT", directory):
                        original(session)
                with patch.object(make_probe.ProbeSession, "_compile_interceptor", compile_mutant):
                    with self.fixture.session(runtime_files=("/usr/include/build",)) as session:
                        with self.assertRaises(MakeProbeError):
                            self.observe(session, True)
                    self.fixture.assert_clean(session)

    def test_captured_runtime_abi_rejects_changed_anchors_and_missing_call_sites(self):
        with self.fixture.session() as session:
            abi = session._original_read_abi()
            image = dict(session.make_runtime)["/usr/bin/make"]
            self.assertIs(read_epochs.validate_abi(abi, image), abi)
            cases = []
            for key in ("read_all", "source"):
                changed = copy.deepcopy(abi)
                changed[key][0] += 1
                cases.append(changed)
            changed = copy.deepcopy(abi)
            changed["globals"]["current_variable_set_list"] += 8
            cases.append(changed)
            changed = copy.deepcopy(abi)
            changed["source_opens"] = changed["source_opens"][:-1]
            cases.append(changed)
            cases.extend(({**abi, "image_sha256": "0" * 64}, {**abi, "extra": True}))
            for changed in cases:
                with self.subTest(abi=changed), self.assertRaises(read_epochs.ReadEpochError):
                    read_epochs.validate_abi(changed, image)
        self.fixture.assert_clean(session)

    def test_actual_trace_corruption_cannot_erase_epochs_inputs_status_or_bytes(self):
        for defect in ("missing", "scope", "epoch", "variable", "status", "source-bytes", "goals", "complete"):
            with self.subTest(defect=defect), self.fixture.session(runtime_files=("/usr/include/build",)) as session:
                read, changed = session.budget.read_bytes, []
                def corrupt(path, category):
                    data = read(path, category)
                    if category != "control" or not Path(path).name.startswith("report-"):
                        return data
                    report = json.loads(data)
                    trace = report.get("read_trace")
                    if trace is None:
                        return data
                    changed.append(defect)
                    if defect == "missing":
                        del report["read_trace"]
                    elif defect == "scope":
                        trace["scope"] += "-foreign"
                    elif defect == "epoch":
                        next(event for event in trace["events"] if event["kind"] == "exec")["exec"] = 2
                    elif defect == "variable":
                        entry = next(event for event in trace["events"] if event["kind"] == "pass-entry")
                        entry["inputs"][0]["variables"][0][2] = True
                    elif defect == "status":
                        next(event for event in trace["events"]
                             if event["kind"] == "source-exit" and event["error"])["error"] = 0
                    elif defect == "source-bytes":
                        trace["sources"][0]["data"] = "AA==" + trace["sources"][0]["data"][4:]
                    elif defect == "goals":
                        next(event for event in trace["events"] if event["kind"] == "pass-exit")["goals"].pop()
                    else:
                        trace["complete"] = False
                    return json.dumps(report).encode()
                with patch.object(session.budget, "read_bytes", corrupt), self.assertRaises(MakeProbeError):
                    self.observe(session, True)
                self.assertEqual(changed, [defect])
            self.fixture.assert_clean(session)

    def test_nonsource_file_read_after_parse_has_no_original_epoch_claim(self):
        self.fixture.add("Makefile", self.source.replace("all: ;", "all: ; @printf '%s' '$(file <child.mk)'"))
        with self.fixture.session(runtime_files=("/usr/include/build",)) as session:
            result = self.observe(session, True)
            outside = [event for event in result.read_trace["events"]
                       if event["kind"] == "other-open" and event["pass"] is None]
            self.assertTrue(outside)
            self.assertTrue(all(event["visit"] is None for event in outside))
            self.assertEqual(len([event for event in result.read_trace["events"]
                                  if event["kind"] == "source-entry"]), 6)
        self.fixture.assert_clean(session)

    def test_trace_selection_and_source_failure_remain_fail_closed(self):
        for selection in (None, 1, "true"):
            with self.subTest(selection=selection), self.fixture.session() as session:
                runs = session.budget.runs
                with self.assertRaises(MakeProbeError):
                    self.observe(session, selection)
                self.assertEqual(session.budget.runs, runs)
            self.fixture.assert_clean(session)
        self.fixture.add("Makefile", "include absent-required.mk\nall: ;\n")
        with self.fixture.session(runtime_files=("/usr/include/absent-required.mk",)) as session:
            with self.assertRaises(MakeProbeError):
                session.make("all", observe_read_epochs=True)
        self.fixture.assert_clean(session)

    def test_exact_native_owner_and_existing_human_case_cover_read_trace(self):
        from scripts import check_docs
        from scripts.validation_ownership import ci_verifier, reporter
        from scripts.validation_ownership.authority import AuthorityLoader, git_tree_entries
        from scripts.validation_ownership.budget import ProbeBudget

        root = foundation.ROOT
        graph = reporter.load_json(root / reporter.GRAPH_PATH)
        budget = ProbeBudget()
        try:
            entries = git_tree_entries(root, budget=budget)
            sources = reporter._path_admission_sources(AuthorityLoader(root, entries, "HEAD", budget=budget), set())
            for path, owner in (
                ("scripts/validation_ownership/read_epochs.py", "paths.ownership"),
                ("scripts/validation_ownership/read_trace.py", "paths.ownership"),
                ("scripts/validation_ownership/tests/test_read_epochs.py", "paths.ownership-native"),
            ):
                rule, = [row for row in graph["path_rules"] if reporter._path_rule_matches(row, path, set())]
                self.assertEqual(rule["id"], owner)
                self.assertEqual(reporter._path_admission(path, rule, sources), "exact-ownership-rule")
                if "/tests/" not in path:
                    self.assertIn(path, ci_verifier.TRUSTED_RUNTIME_PATHS)
                else:
                    without = {**rule, "include": [item for item in rule["include"]
                                                  if item != {"kind": "exact", "path": path}]}
                    with self.assertRaises(reporter.OwnershipError):
                        reporter._path_admission(path, without, sources)
        finally:
            budget.close()
        registry, errors = check_docs.parse_test_case_registry(str(root))
        self.assertEqual(errors, [])
        case, = [item for item in registry["cases"] if item["id"] == "TC-WORKFLOW-GATE-OWNERSHIP-001"]
        feature, = [item for item in registry["features"] if item["id"] == case["feature_id"]]
        selected = {
            **registry, "features": [{**feature, "required_cases": [case["id"]]}], "cases": [case],
            "coverage": {**registry["coverage"], "expected_feature_ids": [feature["id"]]},
        }
        self.assertIn(
            (("python3", "-m", "unittest", __name__, "-v"), "scripts/validation_ownership/tests/test_read_epochs.py"),
            {(tuple(shlex.split(item["command"])), item["evidence"]) for item in case["automation"]},
        )
        with patch.object(check_docs, "parse_test_case_registry", return_value=(selected, [])):
            self.assertEqual(check_docs.check_test_case_registry(str(root)), [])


if __name__ == "__main__":
    unittest.main()
