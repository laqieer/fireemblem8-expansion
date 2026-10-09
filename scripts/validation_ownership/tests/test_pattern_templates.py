"""Actual-image mutation and finite occurrence/topology contracts."""

from pathlib import Path
import json
import struct
import subprocess
from types import SimpleNamespace
import unittest

from scripts.validation_ownership import read_epochs
from scripts.validation_ownership.pattern_templates import PatternTemplates, pattern_abi
from scripts.validation_ownership.tests import test_foundation as foundation


class MemoryTrace:
    def __init__(self):
        self.memory_image = bytearray(16384)
        self.cursor = 8192
        self.bias = 0
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
