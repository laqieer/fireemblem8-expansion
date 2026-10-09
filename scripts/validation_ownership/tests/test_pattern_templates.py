"""Actual-image mutation and finite occurrence/topology contracts."""

from pathlib import Path
import hashlib
import json
import struct
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.validation_ownership import read_epochs
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.make_probe import Command, Limits
from scripts.validation_ownership.pattern_templates import PatternTemplates, pattern_abi
from scripts.validation_ownership.tests import test_foundation as foundation


class MemoryTrace:
    def __init__(self):
        self.memory_image = bytearray(16384)
        self.cursor = 8192
        self.bias = 0
        self.version = read_epochs.RUNTIME_VERSION
        self.config = {"file_limit": 4096, "observation_count": 32}
        self.invocations = [{"kind": "source", "visit": 1}]
        self.sources = [{"id": 1}, {"id": 2}]
        self.events = []
        self.charged = 0
        self.transferred = []
        self.policy = SimpleNamespace(charge_metadata=self.charge)

    def charge(self, count):
        self.charged += count

    def deadline(self):
        pass

    def context(self):
        return {"exec": 1, "pass": 1}

    def event(self, kind, **fields):
        row = {"seq": len(self.events) + 1, "kind": kind, **fields}
        self.events.append(row)
        return row

    def memory(self, pointer, size):
        self.charge(size)
        self.transferred.append((pointer, size))
        if not 0 <= pointer <= pointer + size <= len(self.memory_image):
            raise read_epochs.ReadEpochError("test memory escaped its finite extent")
        return bytes(self.memory_image[pointer:pointer + size])

    def number(self, pointer):
        return int.from_bytes(self.memory(pointer, 8), "little")

    def string(self, pointer, maximum):
        if not pointer:
            return None
        result = bytearray()
        for offset in range(maximum):
            value = self.memory(pointer + offset, 1)
            if value == b"\0":
                return result.decode("utf-8", "strict")
            result.extend(value)
        raise read_epochs.ReadEpochError("test string exceeded its finite extent")

    def text(self, value):
        if value is None:
            return 0
        pointer = self.cursor
        data = value.encode("utf-8") + b"\0"
        self.memory_image[pointer:pointer + len(data)] = data
        self.cursor += len(data)
        return pointer

    def put_number(self, pointer, value):
        struct.pack_into("<Q", self.memory_image, pointer, value)

    def template(self, pointer, pattern, *, following=0, value="value", name="VALUE", file="Makefile"):
        target = self.text(pattern)
        struct.pack_into(
            "<QQQQQQQQQII", self.memory_image, pointer,
            following, target + pattern.encode().index(b"%") + 1,
            target, len(pattern.encode()), self.text(name), self.text(value),
            self.text(file), 1, 0, len(name.encode()) if name else 0,
            2 << 23 | 2 << 26,
        )


class PatternTemplateTests(unittest.TestCase):
    def test_compact_physical_spans_preserve_complete_mapping_and_exact_charges(self):
        sources = [b"", b"\n", b"\r\n", b"x\r", b"x\r\n", b"x\n\n",
                   "TEXT := caf\u00e9\r\n".encode()]
        for terminator in (b"\n", b"\r\n"):
            for count in range(1, 12):
                for slashes in range(4):
                    source = terminator.join([
                        b"A := " + b"\\" * slashes, b" second", b"THIRD := value",
                    ] * count)
                    sources.extend((source, source + terminator))
        for source in sources:
            with self.subTest(source=source):
                expected = {
                    first: (logical, first, last, hashlib.sha256(raw.encode()).hexdigest())
                    for logical, first, last, raw in read_epochs.physical_statements(source)
                }
                charges, checkpoints = [], []
                observed = read_epochs._statement_index(
                    source, compact=True, reserve=charges.append,
                    checkpoint=lambda: checkpoints.append(None),
                )
                self.assertEqual(len(checkpoints), source.count(b"\n") + 1)
                self.assertEqual(list(observed), list(expected))
                self.assertEqual(dict(observed), expected)
                complete = sum(charges)
                self.assertEqual(list(observed.values()), list(expected.values()))
                self.assertEqual(dict(observed.items()), expected)
                self.assertIsNone(observed.get(99999))
                self.assertIsNone(observed.get("not-a-line"))
                self.assertEqual(sum(charges), complete)
                with self.assertRaises(TypeError):
                    observed[1] = observed[1]
                for offset in (0, 1):
                    reserve = lambda size: budget.charge("control", size)
                    exact = complete + sys.getsizeof(reserve) - sys.getsizeof(charges.append)
                    budget = ProbeBudget(Limits(control_bytes=exact - offset))
                    if offset:
                        with self.assertRaisesRegex(MakeProbeError, "control byte budget exhausted"):
                            dict(read_epochs._statement_index(source, compact=True, reserve=reserve))
                        self.assertTrue(budget.failed)
                    else:
                        self.assertEqual(
                            dict(read_epochs._statement_index(source, compact=True, reserve=reserve)),
                            expected,
                        )
                        self.assertEqual(budget.bytes["control"], exact)
        source = b"TEXT := value\n" * 2000
        eager, compact = [], []
        read_epochs._statement_index(source, reserve=eager.append)
        observed = read_epochs._statement_index(source, compact=True, reserve=compact.append)
        self.assertLess(sum(compact), sum(eager) // 2)
        before = sum(compact)
        self.assertEqual(observed[1], read_epochs._statement_index(source)[1])
        self.assertGreater(sum(compact), before)
        before = sum(compact)
        self.assertEqual(observed[1], observed[1])
        self.assertEqual(sum(compact), before)

    def test_compact_physical_spans_refuse_invalid_bytes_and_count_overflow(self):
        for source, count_limit, error in (
            (b"A := a\n", 1, read_epochs.ReadEpochError),
            (b"A := \0", None, read_epochs.ReadEpochError),
            (b"A := \xff", None, UnicodeDecodeError),
        ):
            with self.subTest(source=source), self.assertRaises(error):
                read_epochs._statement_index(source, compact=True, count_limit=count_limit)

    def setUp(self):
        self.trace = MemoryTrace()
        self.templates = PatternTemplates(self.trace, {"head": 8, "object_bytes": 80})

    def test_sorted_insertions_after_known_objects_and_full_selected_mutation(self):
        trace = self.trace
        trace.template(128, "%.a")
        trace.put_number(8, 128)
        self.templates.complete(["source", 1], 1)
        first = self.templates.selected(128)
        trace.transferred.clear()
        trace.template(256, "long%.a", value="second")
        trace.put_number(128, 256)
        trace.invocations = [{"kind": "source", "visit": 2}]
        self.templates.complete(["source", 2], 2)
        self.assertEqual(self.templates.selected(256)["id"], first["id"] + 1)
        self.assertEqual(self.templates.objects[128]["owner"], ["source", 1])
        self.assertNotIn((128, 80), trace.transferred)
        trace.put_number(128 + 40, trace.text("substituted"))
        with self.assertRaisesRegex(read_epochs.ReadEpochError, "full fields"):
            self.templates.selected(128)

    def test_incomplete_enclosing_object_keeps_parent_occurrence_across_nested_eval(self):
        trace = self.trace
        trace.template(128, "outer%.a", value=None)
        trace.put_number(8, 128)
        self.templates.observe()
        trace.invocations.append({"kind": "eval", "number": 1})
        trace.template(256, "inner%.a", value="nested")
        trace.put_number(128, 256)
        self.templates.complete(["eval", 1], 2)
        self.assertEqual(self.templates.objects[128]["owner"], ["source", 1])
        self.assertIsNone(self.templates.objects[128]["definition"])
        trace.invocations.pop()
        trace.put_number(128 + 40, trace.text("outer"))
        self.templates.complete(["source", 1], 1)
        rows = [row for row in trace.events if row["kind"] == "pattern-template-completion"]
        self.assertEqual(
            [(row["owner"], row["source"], row["definition"]["value"]) for row in rows],
            [(["eval", 1], 2, "nested"), (["source", 1], 1, "outer")],
        )

    def test_same_floc_versions_and_address_retirement_keep_distinct_ids(self):
        trace = self.trace
        trace.template(128, "%.a", value="first", file="generated.mk")
        trace.put_number(8, 128)
        self.templates.complete(["source", 1], 1)
        first = self.templates.selected(128)["id"]
        spent = trace.charged
        self.templates.retire()
        trace.invocations = [{"kind": "source", "visit": 2}]
        trace.template(128, "%.a", value="second", file="generated.mk")
        self.templates.complete(["source", 2], 2)
        self.assertGreater(self.templates.selected(128)["id"], first)
        self.assertGreater(trace.charged, spent)
        completed = [row for row in trace.events if row["kind"] == "pattern-template-completion"]
        self.assertEqual([row["source"] for row in completed], [1, 2])

    def test_invalid_topology_source_and_incomplete_retirement_are_refused(self):
        for kind in ("cycle", "missing", "order", "no-owner", "source", "incomplete"):
            with self.subTest(kind=kind):
                self.setUp()
                trace = self.trace
                trace.template(128, "long%.a")
                trace.put_number(8, 128)
                if kind == "no-owner":
                    trace.invocations = []
                    with self.assertRaises(read_epochs.ReadEpochError):
                        self.templates.observe()
                    continue
                if kind == "source":
                    with self.assertRaises(read_epochs.ReadEpochError):
                        self.templates.complete(["source", 1], None)
                    continue
                self.templates.observe()
                if kind == "cycle":
                    trace.put_number(128, 128)
                elif kind == "missing":
                    trace.put_number(8, 0)
                elif kind == "order":
                    trace.template(256, "%.a")
                    trace.put_number(128, 256)
                elif kind == "incomplete":
                    with self.assertRaises(read_epochs.ReadEpochError):
                        self.templates.retire()
                    continue
                with self.assertRaises(read_epochs.ReadEpochError):
                    self.templates.observe()

    def test_actual_image_binds_both_branches_sorted_list_and_field_layout(self):
        data = Path("/usr/bin/make").read_bytes()
        image = read_epochs.Elf(data)
        abi = pattern_abi(image)
        self.assertNotEqual(abi["calls"]["do_variable_definition"], abi["calls"]["define_variable_in_set"])
        self.assertLess(abi["completion"], abi["selection"])
        self.assertEqual(abi["object_bytes"], 80)
        mutations = [
            abi["selection"] + 4,
            abi["completion"] - 1,
            abi["calls"]["do_variable_definition"] + 1,
            abi["calls"]["define_variable_in_set"] + 1,
            abi["calls"]["define_variable_in_set"] + 5 + 20,
        ]
        creation, end = abi["creator"]
        body = image.bytes(creation, end - creation, executable=True)
        for sequence in (
            bytes.fromhex("bf50000000"), bytes.fromhex("483b5a1873"),
            bytes.fromhex("4c896810488958184c896008"),
            bytes.fromhex("488905"),
        ):
            mutations.append(creation + body.index(sequence) + len(sequence) - 1)
        for address in mutations:
            with self.subTest(address=address):
                mutant = bytearray(data)
                for begin, _, offset, size, _ in image.loads:
                    if begin <= address < begin + size:
                        mutant[offset + address - begin] ^= 1
                        break
                with self.assertRaises(read_epochs.ReadEpochError):
                    pattern_abi(read_epochs.Elf(bytes(mutant)))


class NativePatternTemplateTests(unittest.TestCase):
    setUp = foundation.FoundationTests.setUp
    tearDown = foundation.FoundationTests.tearDown
    add = foundation.FoundationTests.add
    session = foundation.FoundationTests.session
    assert_clean = foundation.FoundationTests.assert_clean
    native_supervisor = foundation.FoundationTests.native_supervisor

    def test_actual_writable_command_rejection_reports_mode_with_and_without_patterns(self):
        self.add("Makefile", "all: ; @exit 7\n")
        argv = ("/bin/sh", "-c", "exit 7")
        for patterns in (False, True):
            for command in (False, Command(("/bin/sh", "-c", "exit 0"))):
                session = self.session()
                with self.subTest(patterns=patterns, command=command), session:
                    with self.assertRaisesRegex(
                        MakeProbeError, "native writable Command differs from actual argv or requests output authority",
                    ):
                        session._native_make_writable(
                            "all", outputs=("made",), commands={argv: command},
                            observe_reads=True, observe_runtime_completions=True,
                            observe_patterns=patterns,
                        )
                    self.assertTrue(session.budget.failed)
                self.assert_clean(session)

    def test_actual_failed_make_result_reports_requested_mode_for_old_and_pattern_requests(self):
        self.add("Makefile", "all: ; @exit 7\n")
        failed = subprocess.run(
            ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(failed.returncode, 2)
        self.assertIn(b"Error 7", failed.stderr)
        for patterns in (False, True):
            for writable in (False, True):
                with self.subTest(patterns=patterns, writable=writable):
                    session = self.session()
                    with session, patch.object(
                        session, "_sandbox_run", return_value=(failed, {}),
                    ):
                        mode = "writable" if writable else "readonly"
                        with self.assertRaises(MakeProbeError) as failure:
                            session._native_make_run(
                                "all", writable_outputs=("made",) if writable else (),
                                commands={},
                                observe_reads=True, observe_runtime_completions=True,
                                observe_patterns=patterns,
                            )
                        self.assertEqual(
                            str(failure.exception),
                            f"{mode} native GNU Make failed: 2; {failed.stderr!r}",
                        )
                        if writable:
                            dependency_error = (
                                "invalid native read observation request or completion dependency"
                                if patterns else
                                "native Command admission requires complete runtime job inputs"
                            )
                            with self.assertRaisesRegex(
                                MakeProbeError, "^" + dependency_error + "$",
                            ):
                                session._native_make_run(
                                    "all", writable_outputs=("made",), commands={},
                                    observe_reads=True, observe_runtime_completions=False,
                                    observe_patterns=patterns,
                                )
                    self.assert_clean(session)

    def test_public_pattern_protocol_both_branches_nested_eval_include_and_typed_archive(self):
        self.add("Makefile", (
            "%.out: VALUE = recursive\n"
            "simple%.out: VALUE := simple\n"
            "append%.out: VALUE += appended\n"
            "conditional%.out: VALUE ?= conditional\n"
            "shell%.out: VALUE != v=fixture; printf shell\n"
            "nested%.out: VALUE := initial\n"
            "nested%.out: VALUE += $(eval include nested.mk)$(HELPER)\n"
            "private%.out: private VALUE = private\n"
            "export%.out: export VALUE = exported\n"
            "override%.out: override VALUE = override\n"
            ".PHONY: all\n"
            "all: ordinary.out simple-one.out append-one.out conditional-one.out private-one.out export-one.out override-one.out shell-one.out nested-one.out\n"
            "ordinary.out simple-one.out append-one.out conditional-one.out private-one.out export-one.out override-one.out shell-one.out nested-one.out:\n"
            "\t@v=recipe; printf '%s:%s\\n' '$@' '$(VALUE)'\n"
        ))
        self.add("nested.mk", "HELPER := nested\n")
        ordinary = subprocess.run(
            ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        session = self.session()
        with session:
            completed, _, observed = session._native_make_readonly(
                "all", observe_reads=True, observe_runtime_completions=True, observe_patterns=True,
            )
            self.assertEqual(
                (completed.returncode, completed.stdout, completed.stderr),
                (ordinary.returncode, ordinary.stdout, ordinary.stderr),
            )
            trace = observed["read_trace"]
            self.assertEqual(trace["version"], read_epochs.PATTERN_RUNTIME_VERSION)
            self.assertEqual(trace["machine"]["version"], 3)
            archive = read_epochs.reconstruct_archive(trace, budget=session.budget)
            templates = archive.passes[0].pattern_templates
            patterns = archive.passes[0].patterns
            self.assertEqual(len(templates), 10)
            self.assertEqual(len(patterns), 18)
            self.assertEqual(sum(row.return_seq is not None for row in patterns), 16)
            self.assertTrue(all(isinstance(row.definition, read_epochs.PatternDefinition) for row in templates))
            self.assertTrue(all(row.template in templates for row in patterns))
            self.assertTrue(any(isinstance(row.location, read_epochs.PatternLocation) for row in archive.passes[0].evaluations))
            self.assertEqual({row.target for row in patterns}, {
                "ordinary.out", "simple-one.out", "append-one.out", "conditional-one.out",
                "private-one.out", "export-one.out", "override-one.out", "shell-one.out", "nested-one.out",
            })
            mutations = (
                ("trace-downgrade", lambda value: value.update(version=read_epochs.RUNTIME_VERSION)),
                ("machine-downgrade", lambda value: value["machine"].update(version=1)),
                ("finite-machine-without-roots", lambda value: value["machine"].update(version=4)),
                ("template-counter", lambda value: value["events"][-1].update(templates=9)),
                ("pattern-counter", lambda value: value["events"][-1].update(patterns=17)),
                ("return-counter", lambda value: value["events"][-1].update(pattern_returns=15)),
                ("template-owner", lambda value: self.event_row(value, "pattern-template-completion").update(owner=["eval", 1])),
                ("boolean-template-entry-owner", lambda value: self.event_row(value, "pattern-template-entry").update(owner=["source", True])),
                ("boolean-template-completion-owner", lambda value: self.event_row(value, "pattern-template-completion").update(owner=["source", True])),
                ("template-source", lambda value: self.event_row(value, "pattern-template-completion").update(source=2)),
                ("incomplete-template", lambda value: self.event_row(value, "pattern-template-completion").update(kind="pattern-template-entry")),
                ("template-id", lambda value: self.event_row(value, "pattern-template-entry").update(template=2)),
                ("unknown-template", lambda value: self.event_row(value, "pattern-entry").update(template=99)),
                ("foreign-target", lambda value: self.event_row(value, "pattern-entry").update(target="foreign.txt")),
                ("wildcard-offset", lambda value: self.event_row(value, "pattern-template-completion")["definition"].update(percent=1)),
                ("reused-pattern", lambda value: self.event_row(value, "pattern-entry").update(pattern=2)),
                ("missing-return", lambda value: self.event_row(value, "pattern-definition-return").update(kind="pattern-completion", cwd="/repo")),
                ("flavor-substitution", lambda value: self.event_row(value, "pattern-definition").update(flavor=1)),
                ("definition-value", lambda value: self.event_row(value, "pattern-definition").update(value="foreign")),
                ("completed-name", lambda value: self.event_row(value, "pattern-completion")["variable"].__setitem__(0, "OTHER")),
                ("completed-modifier", lambda value: self.event_row(value, "pattern-completion")["variable"].__setitem__(2, self.event_row(value, "pattern-completion")["variable"][2] ^ 128)),
                ("foreign-root", lambda value: self.event_row(value, "eval-entry").update(location={"pattern": 99})),
                ("unexpected-keys", lambda value: self.event_row(value, "pattern-entry").update(owner=["source", 1])),
                ("payload-digest", lambda value: self.machine_row(value, "pattern-result").update(sha256="0" * 64)),
                ("payload-pattern", lambda value: self.machine_row(value, "pattern-input").update(pattern=99)),
                ("template-payload", lambda value: self.machine_row(value, "pattern-template-result").update(template=99)),
                ("foreign-slot-purpose", lambda value: self.machine_row(value, "trap", purpose="pattern-selection").update(purpose="source-entry")),
                ("unissued-slot", lambda value: self.machine_row(value, "trap", purpose="pattern-completion").update(pc=1)),
            )
            for name, mutation in mutations:
                with self.subTest(replay_mutation=name):
                    invalid = json.loads(json.dumps(trace))
                    mutation(invalid)
                    with self.assertRaises(read_epochs.ReadEpochError):
                        read_epochs.validate_trace(
                            invalid, invalid["scope"], count_limit=session.budget.limits.observation_count,
                            file_limit=session.budget.limits.file_bytes,
                        )
        self.assert_clean(session)

    @staticmethod
    def event_row(trace, kind):
        return next(row for row in trace["events"] if row["kind"] == kind)

    @staticmethod
    def machine_row(trace, kind, **fields):
        return next(
            row for row in trace["machine"]["events"]
            if row["kind"] == kind and all(row.get(key) == value for key, value in fields.items())
        )

    def test_public_pattern_live_object_and_entire_register_controls_refuse(self):
        self.add("Makefile", "%.out: VALUE = value\nall: one.out\none.out: ; @:\n")
        for kind, body, error in (
            (
                "live-object",
                "from pattern_templates import PatternMaterializations\n"
                "before=PatternMaterializations.enter\n"
                "def enter(self,r,state):\n"
                " trace=self.trace;pointer=r.r12+72\n"
                " actual=trace.number(pointer)\n"
                " trace.native.ptrace(5,trace.pid,pointer,actual^(1<<39))\n"
                " return before(self,r,state)\n"
                "PatternMaterializations.enter=enter\n",
                "selected pattern changed its source-bound full fields",
            ),
            (
                "callback",
                "from pattern_templates import PatternMaterializations\n"
                "before=PatternMaterializations.enter\n"
                "def enter(self,r,state):\n"
                " before(self,r,state)\n"
                " r.r12^=1\n"
                "PatternMaterializations.enter=enter\n",
                "original read callback changed its register state",
            ),
            (
                "kernel-restoration",
                "before=guard.NativeReadTrace.__init__\n"
                "current=None\n"
                "def init(self,*args,**kwargs):\n"
                " global current\n"
                " before(self,*args,**kwargs);current=self\n"
                "guard.NativeReadTrace.__init__=init\n"
                "ptrace_before=guard.ptrace\n"
                "def ptrace(request,pid,address=0,data=0):\n"
                " result=ptrace_before(request,pid,address,data)\n"
                " if (current is not None and current.patterns.materializations"
                " and request==guard.GETREGS"
                " and getattr(data,'_obj',None) is current.register_work[2]):\n"
                "  data._obj.r12^=1\n"
                " return result\n"
                "guard.ptrace=ptrace\n",
                "original read register restoration failed kernel readback",
            ),
        ):
            session = self.session()
            with self.subTest(control=kind), session, self.native_supervisor(body):
                from scripts.validation_ownership.budget import MakeProbeError
                with self.assertRaisesRegex(MakeProbeError, error):
                    session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True, observe_patterns=True,
                    )
            self.assert_clean(session)

    def test_pattern_completion_cwd_matches_entry_in_live_and_archive_consumers(self):
        from scripts.validation_ownership.authority import encoded
        self.add(
            "Makefile",
            "plain%.out: VALUE = recursive\nsimple%.out: VALUE := simple\n"
            "all: plain-one.out simple-one.out\n"
            "%.out: ; @v=done; printf '%s\\n' '$(VALUE)'\n",
        )
        session = self.session()
        with session:
            completed, _, observed = session._native_make_readonly(
                "all", observe_reads=True, observe_runtime_completions=True, observe_patterns=True,
            )
            self.assertEqual((completed.returncode, completed.stdout, completed.stderr),
                             (0, b"recursive\nsimple\n", b""))
            trace = observed["read_trace"]
            rows = [row for row in trace["events"] if row["kind"] == "pattern-completion"]
            self.assertEqual(len(rows), 2)
            for row in rows:
                invalid = json.loads(json.dumps(trace))
                result = next(
                    event for event in invalid["events"]
                    if event["kind"] == "pattern-completion" and event["pattern"] == row["pattern"]
                )
                result["cwd"] = "/different"
                evidence = self.machine_row(invalid, "pattern-result", pattern=row["pattern"])
                evidence["sha256"] = hashlib.sha256(encoded(result)).hexdigest()
                with self.subTest(archive_pattern=row["pattern"]), self.assertRaisesRegex(
                    read_epochs.ReadEpochError, "pattern completion",
                ):
                    read_epochs.validate_trace(
                        invalid, invalid["scope"], count_limit=session.budget.limits.observation_count,
                        file_limit=session.budget.limits.file_bytes,
                    )
        self.assert_clean(session)
        for simple in (False, True):
            body = (
                "from pattern_templates import PatternMaterializations\n"
                "before=PatternMaterializations.completion\n"
                "def completion(self,r,state):\n"
                " original=state.cwd\n"
                " flags=self.trace.invocations[-1]['template']['definition']['flags']\n"
                " if (((flags>>23)&7)==1)==" + repr(simple) + ":\n"
                "  state.cwd='/different'\n"
                " try:\n"
                "  return before(self,r,state)\n"
                " finally:\n"
                "  state.cwd=original\n"
                "PatternMaterializations.completion=completion\n"
            )
            session = self.session()
            with self.subTest(live_simple=simple), session, self.native_supervisor(body):
                with self.assertRaisesRegex(MakeProbeError, "pattern completion"):
                    session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True, observe_patterns=True,
                    )
            self.assert_clean(session)

    def test_public_pattern_requested_version_and_captured_abi_are_fail_closed(self):
        from scripts.validation_ownership.budget import MakeProbeError
        self.add("Makefile", "simple%.out: VALUE := simple\nall: simple-one.out\nsimple-one.out: ; @:\n")
        for mutation, expected in (
            ("request.pop('patterns')", "invalid readonly native read-trace authority"),
            ("request['version']=5", "invalid readonly native read-trace authority"),
            ("request['version']=8", "invalid readonly native read-trace authority"),
            ("request['patterns']['calls']['do_variable_definition']+=1", "pattern ABI differs from its captured image"),
            ("request['patterns']['head']+=8", "pattern ABI differs from its captured image"),
            ("request['patterns']['object_bytes']=72", "pattern ABI differs from its captured image"),
        ):
            body = (
                "before=guard.Policy.__init__\n"
                "def init(self,config):\n"
                " request=config['read_epochs']\n"
                " " + mutation + "\n"
                " return before(self,config)\n"
                "guard.Policy.__init__=init\n"
            )
            session = self.session()
            with self.subTest(admission=mutation), session, self.native_supervisor(body):
                with self.assertRaisesRegex(MakeProbeError, expected):
                    session._native_make_readonly(
                        "all", observe_reads=True, observe_runtime_completions=True, observe_patterns=True,
                    )
            self.assert_clean(session)

    def test_public_pattern_escaped_percent_binds_actual_wildcard_and_target(self):
        self.add("Makefile", (
            "literal\\%%.out: VALUE := escaped\n"
            ".PHONY: all\nall: literal%one.out\n"
            "%.out: ; @v=recipe; printf '%s' '$(VALUE)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        self.assertEqual(ordinary.stdout, b"escaped")
        session = self.session()
        with session:
            completed, _, observed = session._native_make_readonly(
                "all", observe_reads=True, observe_runtime_completions=True, observe_patterns=True,
            )
            self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, ordinary.stdout, ordinary.stderr))
            archive = read_epochs.reconstruct_archive(observed["read_trace"], budget=session.budget)
            definition = archive.passes[0].pattern_templates[0].definition
            self.assertEqual((definition.pattern, definition.percent), ("literal%%.out", 8))
            self.assertEqual(archive.passes[0].patterns[0].target, "literal%one.out")
        self.assert_clean(session)

    def test_public_pattern_finite_roots_retire_addresses_and_refuse_cross_root_templates(self):
        self.add("Makefile", "%.out: VALUE = $(PROFILE)\nall: one.out\none.out: ; @v=recipe; printf '%s' '$(VALUE)'\n")
        session = self.session()
        with session:
            results, observed = session._native_make_cohort(
                (
                    ("all", "Makefile", (("command-line", "PROFILE", "first"),)),
                    ("all", "Makefile", (("command-line", "PROFILE", "second"),)),
                ),
                observe_patterns=True,
            )
            self.assertEqual([row.stdout for row, _, _ in results], [b"first", b"second"])
            trace = observed["read_trace"]
            self.assertEqual(trace["version"], read_epochs.PATTERN_RUNTIME_VERSION)
            self.assertEqual(trace["machine"]["version"], 4)
            self.assertEqual(len(trace["machine"]["roots"]), 2)
            archive = read_epochs.reconstruct_archive(trace, budget=session.budget)
            first, second = archive.passes
            self.assertEqual([row.number for row in first.pattern_templates], [1])
            self.assertEqual([row.number for row in second.pattern_templates], [2])
            self.assertNotEqual(first.pattern_templates[0].owner, second.pattern_templates[0].owner)
            self.assertEqual([row.variable.value for row in first.patterns], ["$(PROFILE)"])
            for kind in ("template", "owner", "exec", "root-range"):
                invalid = json.loads(json.dumps(trace))
                second_entry = next(
                    row for row in invalid["events"] if row["kind"] == "pattern-entry" and row["exec"] == 2
                )
                if kind == "template":
                    second_entry["template"] = 1
                elif kind == "owner":
                    next(row for row in invalid["events"] if row["kind"] == "pattern-template-completion" and row["exec"] == 2)["owner"] = ["source", 1]
                elif kind == "exec":
                    second_entry["exec"] = 1
                else:
                    invalid["machine"]["roots"][1]["first_exec"] = 1
                with self.subTest(cross_root=kind), self.assertRaises(read_epochs.ReadEpochError):
                    read_epochs.validate_trace(
                        invalid, invalid["scope"], count_limit=session.budget.limits.observation_count,
                        file_limit=session.budget.limits.file_bytes,
                    )
        self.assert_clean(session)

    def test_public_generated_pattern_versions_and_internal_make_reexec_keep_custody(self):
        from scripts.validation_ownership.make_probe import Command
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <sys/stat.h>\n#include <fcntl.h>\n"
            "#include <unistd.h>\n#include <stdio.h>\n#include <string.h>\n"
            "int main(int argc,char **argv){int fd;const char *data;"
            "if(argc!=2)return 1;"
            'if(!strcmp(argv[1],"create")){if(mkdir("/repo/stage",0700))return 2;'
            'fd=open("/repo/stage/generated.mk",O_CREAT|O_EXCL|O_WRONLY,0644);'
            'data="%.out: VALUE = first\\n";'
            '}else{fd=open("/repo/stage/.asset-manifest-write-abcdefgh",O_CREAT|O_EXCL|O_WRONLY,0644);'
            'data="%.out: VALUE = second\\n";}'
            "if(fd<0||write(fd,data,strlen(data))!=(ssize_t)strlen(data)||close(fd))return 3;"
            'if(!strcmp(argv[1],"replace")&&rename("/repo/stage/.asset-manifest-write-abcdefgh",'
            '"/repo/stage/generated.mk"))return 4;return 0;}\n'
        ))
        resources = (("directory", "stage"), ("atomic-temporary", "stage/.asset-manifest-write-"))
        for remake in (False, True):
            self.add("Makefile", (
                "-include stage/generated.mk\nstage/generated.mk:\n\t@/native/tool create\n"
                if remake else
                "CREATE := $(shell /native/tool create)\ninclude stage/generated.mk\n"
                "REPLACE := $(shell /native/tool replace)\ninclude stage/generated.mk\n"
            ) + ".PHONY: all\nall: one.out\none.out: ; @v=recipe; printf '%s' '$(VALUE)'\n")
            session = self.session()
            with self.subTest(remake=remake), session:
                tool = session.compile_native(("native.c",))

                class Commands:
                    def __getitem__(self, argv):
                        if argv in {("/native/tool", "create"), ("/native/tool", "replace")}:
                            return Command(
                                argv, native_tool=tool, outputs=("stage/generated.mk",), native_resources=resources,
                            )
                        if argv[0] == "/bin/sh":
                            return Command(argv)
                        raise KeyError(argv)

                completed, _, observed, generated = session._native_make_writable(
                    "all", outputs=("stage/generated.mk",), native_resources=resources,
                    native_tool=tool, commands=Commands(), observe_reads=True,
                    observe_runtime_completions=True, observe_patterns=True, observe_root=True,
                )
                expected = b"first" if remake else b"second"
                self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, expected, b""))
                self.assertEqual([(row.path, row.data, row.mode) for row in generated], [
                    ("stage/generated.mk", b"%.out: VALUE = " + expected + b"\n", 0o644),
                ])
                trace = observed["read_trace"]
                self.assertEqual(trace["version"], read_epochs.PATTERN_WRITABLE_VERSION)
                self.assertEqual(trace["machine"]["version"], 3)
                archive = read_epochs.reconstruct_archive(trace, budget=session.budget)
                templates = [row for execution in archive.passes for row in execution.pattern_templates]
                self.assertEqual([row.definition.value for row in templates], ["first"] if remake else ["first", "second"])
                self.assertTrue(all(
                    (row.definition.file, row.definition.line, row.definition.offset) == ("stage/generated.mk", 1, 0)
                    for row in templates
                ))
                leases = []
                for template in templates:
                    opened = next(
                        event for event in trace["events"]
                        if event["kind"] == "source-open" and event["visit"] == template.owner[1]
                    )
                    lease = trace["machine"]["events"][opened["custody"]["entry"] - 1]
                    self.assertEqual(lease["kind"], "generated-source-entry")
                    self.assertEqual(lease["sha256"], template.source.sha256)
                    self.assertEqual(template.source.data, (
                        "%.out: VALUE = " + template.definition.value + "\n"
                    ).encode())
                    leases.append(lease)
                if remake:
                    self.assertEqual(len(archive.passes), 2)
                    self.assertEqual([row.number for row in archive.passes[0].pattern_templates], [])
                    self.assertEqual([row.number for row in archive.passes[1].pattern_templates], [1])
                else:
                    self.assertNotEqual(leases[0]["serial"], leases[1]["serial"])
                    self.assertNotEqual(leases[0]["owner"], leases[1]["owner"])
                    self.assertNotEqual(templates[0].source.sha256, templates[1].source.sha256)
            self.assert_clean(session)

    def test_actual_partial_outer_nested_eval_and_repeated_floc_keep_occurrence_owners(self):
        self.add("first.mk", "$(eval %.out: VALUE = first)\n")
        self.add("second.mk", "$(eval %.out: VALUE = second)\n")
        self.add("Makefile", (
            "include first.mk second.mk\n"
            "outer%.out: VALUE := $(eval inner%.out: VALUE = nested)outer\n"
            ".PHONY: all\nall:\n\t@v=recipe; printf original\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
            cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        report = self.root.parent / (self.root.name + "-pattern-template-proof.json")
        instrumentation = r'''
from pattern_templates import PatternTemplates,pattern_abi
template_rows=[]
class TraceView:
 def __init__(self,trace):
  self.trace=trace
 def __getattr__(self,name):
  return getattr(self.trace,name)
 def event(self,kind,**fields):
  row={"seq":len(template_rows)+1,"kind":kind,**fields}
  self.trace.policy.charge_metadata(len(guard.encoded(row)))
  template_rows.append(row)
  return row
init_before=guard.NativeReadTrace.__init__
def init(self,*args,**kwargs):
 init_before(self,*args,**kwargs)
 self.test_templates=PatternTemplates(TraceView(self),pattern_abi(guard.read_epochs.Elf(self.image)))
guard.NativeReadTrace.__init__=init
event_before=guard.NativeReadTrace.event
def event(self,kind,**fields):
 if kind in {"source-entry","eval-entry"}:
  self.test_templates.observe()
 return event_before(self,kind,**fields)
guard.NativeReadTrace.event=event
source_before=guard.NativeReadTrace.source_return
def source_return(self,registers):
 current=self.active[-1]
 self.test_templates.complete(["source",current["visit"]],current["source"])
 source_before(self,registers)
guard.NativeReadTrace.source_return=source_return
eval_before=guard.NativeReadTrace.runtime_eval_return
def eval_return(self,registers):
 current=self.invocations[-1]
 self.test_templates.complete(["eval",current["number"]],current["source"])
 eval_before(self,registers)
guard.NativeReadTrace.runtime_eval_return=eval_return
execute_before=guard.NativeReadTrace.actual_exec
def execute(self,pid,make,dispatch=None,inputs=None):
 if make:
  self.test_templates.retire()
 execute_before(self,pid,make,dispatch,inputs)
guard.NativeReadTrace.actual_exec=execute
supervise_before=guard.supervise
def supervise(config,*args,**kwargs):
 try:
  return supervise_before(config,*args,**kwargs)
 finally:
  Path(REPORT).write_text(json.dumps(template_rows))
guard.supervise=supervise
'''.replace("REPORT", repr(str(report)))
        session = self.session()
        try:
            with session, self.native_supervisor(instrumentation):
                completed, _, observed = session._native_make_readonly(
                    "all", observe_reads=True, observe_runtime_completions=True,
                )
                self.assertEqual(
                    (completed.returncode, completed.stdout, completed.stderr),
                    (ordinary.returncode, ordinary.stdout, ordinary.stderr),
                )
                self.assertEqual(observed["read_trace"]["version"], read_epochs.RUNTIME_VERSION)
                rows = json.loads(report.read_bytes())
                entries = [row for row in rows if row["kind"] == "pattern-template-entry"]
                endings = [row for row in rows if row["kind"] == "pattern-template-completion"]
                self.assertEqual(len(entries), 4)
                self.assertEqual(len(endings), 4)
                definitions = {row["definition"]["value"]: row for row in endings}
                self.assertEqual(set(definitions), {"first", "second", "nested", "outer"})
                self.assertEqual(definitions["outer"]["owner"][0], "source")
                self.assertEqual(definitions["nested"]["owner"][0], "eval")
                self.assertEqual(len({tuple(row["owner"]) for row in endings}), 4)
                self.assertEqual(len({row["source"] for row in endings}), 4)
                self.assertEqual(
                    {row["template"]: row["owner"] for row in entries},
                    {row["template"]: row["owner"] for row in endings},
                )
                captured = {row["id"]: row for row in observed["read_trace"]["sources"]}
                self.assertTrue(all(row["source"] in captured for row in endings))
            self.assert_clean(session)
        finally:
            report.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
