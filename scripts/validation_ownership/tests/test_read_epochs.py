"""Original native input/source epochs; these tests grant no namespace exception."""

import base64
import copy
from contextlib import ExitStack
from dataclasses import replace
import errno
import json
from pathlib import Path
import posixpath
import shlex
import signal
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.validation_ownership import read_epochs
from scripts.validation_ownership import make_probe
from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.make_probe import Command
from scripts.validation_ownership.tests import test_foundation as foundation


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
