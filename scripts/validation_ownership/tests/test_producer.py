"""Real live-Make producer/publication controls, independent of extension V/R/D."""

import json
import os
import signal
import stat
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.validation_ownership.make_probe import Command, TRUSTED_ROOT
from scripts.validation_ownership.budget import MakeProbeError
from scripts.validation_ownership.authority import ENVIRONMENT
from scripts.validation_ownership.producer_channel import ProducerChannel
from scripts.validation_ownership.syscall_guard import VO_PRODUCE
from scripts.validation_ownership.tests import test_foundation as foundation


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.FoundationTests()
        self.fixture.setUp()
        self.root = self.fixture.root

    def tearDown(self):
        self.fixture.tearDown()

    def producer_fixture(self):
        self.fixture.add("producer.py", "open('/work/generated.txt','w').write('actual')\nprint('observed')\n")
        self.fixture.add("Makefile", "VALUE := $(shell python3 producer.py)\nall: ;\n")
        return Command(
            ("/usr/bin/python3", "/repo/producer.py"), code=("producer.py",), outputs=("generated.txt",),
        )

    def test_unreachable_producer_is_not_executed_by_an_empty_speculative_pass(self):
        self.fixture.add("choice.py", "print('observed')\n")
        self.fixture.add("unreachable.py", (
            "from pathlib import Path\nimport sys\n"
            "(Path(sys.argv[1])/'unreachable.txt').write_text('executed')\n"
        ))
        self.fixture.add("Makefile", (
            "CHOICE := $(shell python3 choice.py)\nifeq ($(CHOICE),)\n"
            "UNUSED := $(shell python3 unreachable.py .)\nendif\n"
            "all:\n\t@printf '%s\\n' '$(CHOICE)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(ordinary.stdout, b"observed\n")
        self.assertFalse((self.root / "unreachable.txt").exists())
        calls = []
        command = Command(("/usr/bin/python3", "/repo/choice.py"), code=("choice.py",))
        registrations = {
            "python3 choice.py": command,
            "python3 unreachable.py .": Command(
                ("/usr/bin/python3", "/repo/unreachable.py", "/work"),
                code=("unreachable.py",), outputs=("unreachable.txt",),
            ),
        }
        class Commands:
            def __contains__(self, value):
                calls.append(value)
                return value in registrations
            def __getitem__(self, value):
                return registrations[value]
        with self.fixture.session(seconds=30) as session:
            observed = session.make("all", variables=("CHOICE",), commands=Commands())
            self.assertEqual(observed.semantics["domains"]["CHOICE"]["value"], "observed")
            self.assertEqual(calls, ["python3 choice.py"])
            self.assertEqual(len(observed.events), 1)
            self.assertFalse((session.tree / "unreachable.txt").exists())
        self.fixture.assert_clean(session)

    def test_invalid_request_rejects_before_any_producer_execution(self):
        command = self.producer_fixture()
        for defect in ("scope", "sequence", "completed", "hash", "count", "truncated"):
            with self.subTest(defect=defect):
                received = ProducerChannel.receive
                requests = []
                class Commands:
                    def __contains__(self, value):
                        requests.append(value)
                        return True
                    def __getitem__(self, value):
                        return command
                def corrupt(channel):
                    payload = received(channel)
                    if payload is None or channel.charge is None:
                        return payload
                    record = json.loads(payload)
                    if record["kind"] != "request":
                        return payload
                    if defect == "scope":
                        record["scope"] += "-foreign"
                    elif defect == "sequence":
                        record["sequence"] += 1
                    elif defect == "completed":
                        record["completed"] += 1
                    else:
                        frame = bytearray.fromhex(record["frame"])
                        if defect == "hash":
                            frame[8] ^= 1
                        elif defect == "count":
                            frame[4:8] = (1).to_bytes(4, "little")
                        else:
                            frame.pop()
                        record["frame"] = frame.hex()
                    return json.dumps(record).encode()
                with self.fixture.session(seconds=30) as session:
                    with patch.object(ProducerChannel, "receive", corrupt):
                        with self.assertRaises(MakeProbeError):
                            session.make("all", commands=Commands())
                    self.assertEqual(requests, [])
                    self.assertFalse((session.tree / "generated.txt").exists())
                self.fixture.assert_clean(session)

    def test_invalid_reply_never_retries_an_effectful_producer(self):
        command = self.producer_fixture()
        for defect in ("scope", "sequence", "slot", "owner", "stdout", "outputs"):
            with self.subTest(defect=defect):
                sent = ProducerChannel.send
                calls = []
                def corrupt(channel, payload):
                    record = json.loads(payload)
                    if channel.charge is not None and record.get("kind") == "result":
                        if defect == "scope":
                            record["scope"] += "-foreign"
                        elif defect in {"sequence", "slot"}:
                            record[defect] += 1
                        elif defect == "owner":
                            record["owner"] = "0"*64
                        elif defect == "stdout":
                            record["stdout_sha256"] = "0"*64
                        else:
                            record["outputs"] = ["Makefile"]
                        payload = json.dumps(record).encode()
                    return sent(channel, payload)
                with self.fixture.session(seconds=30) as session:
                    execute = session.command
                    def record(value):
                        calls.append(value)
                        return execute(value)
                    with patch.object(ProducerChannel, "send", corrupt), patch.object(session, "command", record):
                        with self.assertRaises(MakeProbeError):
                            session.make("all", commands={"python3 producer.py": command})
                    self.assertEqual(calls, [command])
                    self.assertFalse((session.tree / "generated.txt").exists())
                    self.assertIn("VALUE :=", (self.root / "Makefile").read_text())
                self.fixture.assert_clean(session)

    def test_duplicate_later_request_fails_after_one_real_effect(self):
        command = self.producer_fixture()
        self.fixture.add("Makefile", (
            "FIRST := $(shell python3 producer.py)\n"
            "SECOND := $(shell python3 producer.py)\nall: ;\n"
        ))
        received, calls = ProducerChannel.receive, []
        def duplicate(channel):
            payload = received(channel)
            if payload is not None and channel.charge is not None:
                record = json.loads(payload)
                if record.get("kind") == "request" and record["sequence"] == 2:
                    record["sequence"] = 1
                    return json.dumps(record).encode()
            return payload
        with self.fixture.session(seconds=30) as session:
            execute = session.command
            def record(value):
                calls.append(value)
                return execute(value)
            with patch.object(ProducerChannel, "receive", duplicate), patch.object(session, "command", record):
                with self.assertRaisesRegex(MakeProbeError, "out-of-order producer request"):
                    session.make("all", commands={"python3 producer.py": command})
            self.assertEqual(calls, [command])
            self.assertFalse((session.tree / "generated.txt").exists())
        self.fixture.assert_clean(session)

    def test_parked_make_resources_are_not_granted_again_to_the_producer(self):
        command = self.producer_fixture()
        for limit in ({"processes": 2}, {"descendants": 2}):
            with self.subTest(limit=limit):
                reached = []
                class Commands:
                    def __contains__(self, value):
                        return True
                    def __getitem__(self, value):
                        reached.append(value)
                        return command
                with self.fixture.session(seconds=30, **limit) as session:
                    with self.assertRaisesRegex(MakeProbeError, "resource budget exhausted"):
                        session.make("all", commands=Commands())
                    self.assertEqual(reached, ["python3 producer.py"])
                    self.assertEqual(session.processes_used, 2)
                    self.assertFalse((session.tree / "generated.txt").exists())
                self.fixture.assert_clean(session)

    def test_all_funded_parked_vm_credits_remain_reserved(self):
        command = self.producer_fixture()
        self.fixture.add("producer.py", (
            "data=bytearray(16*1024*1024)\n"
            "open('/work/generated.txt','w').write('actual')\nprint('observed')\n"
        ))
        reached = []
        class Commands:
            def __contains__(self, value):
                return True
            def __getitem__(self, value):
                reached.append(value)
                return command
        with self.fixture.session(seconds=30, address_space_bytes=64*1024*1024) as session:
            self.assertEqual(session.command(command).stdout, b"observed\n")
            with self.assertRaisesRegex(MakeProbeError, "address-space"):
                session.make("all", commands=Commands())
            self.assertEqual(reached, ["python3 producer.py"])
            self.assertFalse((session.tree / "generated.txt").exists())
        self.fixture.assert_clean(session)

    def test_reserved_absent_source_cannot_be_published(self):
        command = self.producer_fixture()
        self.fixture.add("reserved.txt", "admitted")
        (self.root / "reserved.txt").unlink()
        command = Command(command.argv, code=command.code, outputs=("reserved.txt",))
        with self.fixture.session(seconds=30) as session:
            self.assertIn("reserved.txt", session.snapshot.absent_paths)
            with self.assertRaisesRegex(MakeProbeError, "conflicts with immutable source"):
                session.make("all", commands={"python3 producer.py": command})
            self.assertFalse((session.tree / "reserved.txt").exists())
        self.fixture.assert_clean(session)

    def test_parked_helper_death_aborts_without_publication(self):
        command = self.producer_fixture()
        killed = []
        with self.fixture.session(seconds=30) as session:
            class Commands:
                def __contains__(self, value):
                    return True
                def __getitem__(self, value):
                    leader, = session.budget.children
                    current = leader.pid
                    descriptors = []
                    try:
                        for _ in range(16):
                            children = Path(f"/proc/{current}/task/{current}/children").read_text().split()
                            if not children:
                                break
                            if len(children) != 1:
                                raise AssertionError("owned parked fixture is not one descendant chain")
                            current = int(children[0])
                            descriptors.append(os.pidfd_open(current))
                        else:
                            raise AssertionError("owned descendant chain exceeded its bound")
                        if len(descriptors) < 3:
                            raise AssertionError("owned native Make helper was not reached")
                        signal.pidfd_send_signal(descriptors[-1], signal.SIGKILL)
                        killed.append(current)
                    finally:
                        for descriptor in descriptors:
                            os.close(descriptor)
                    return command
            with self.assertRaises(MakeProbeError):
                session.make("all", commands=Commands())
            self.assertEqual(len(killed), 1)
            self.assertFalse((session.tree / "generated.txt").exists())
        self.fixture.assert_clean(session)

    def test_producer_cannot_reach_callback_descriptors_or_private_channels(self):
        command = self.producer_fixture()
        for operation, denial in (
            ("os.fstat(3)", "unavailable inherited/unknown descriptor"),
            ("open('/control/map/count').read()", "supervisor channel denied"),
            ("open('/repo/Makefile','w').write('changed')", "write outside"),
            (f"ctypes.CDLL(None).syscall(39,ctypes.c_ulong({VO_PRODUCE}),1,20)", "unauthenticated"),
        ):
            with self.subTest(operation=operation):
                self.fixture.add("producer.py", (
                    "import ctypes,os\nprint('producer-started',flush=True)\n" + operation + "\n"
                ))
                outputs = []
                with self.fixture.session(seconds=30) as session:
                    original = session.budget.run
                    def record(argv, **kwargs):
                        result = original(argv, **kwargs)
                        if result.stdout:
                            outputs.append(result.stdout)
                        return result
                    with patch.object(session.budget, "run", record):
                        with self.assertRaisesRegex(MakeProbeError, denial):
                            session.make("all", commands={"python3 producer.py": command})
                    self.assertIn(b"producer-started\n", outputs)
                    self.assertFalse((session.tree / "generated.txt").exists())
                self.fixture.assert_clean(session)

    def test_parent_lifetime_loss_interrupts_nested_producer_and_cleans(self):
        command = self.producer_fixture()
        self.fixture.add("producer.py", (
            "import time\nprint('producer-started',flush=True)\n"
            "time.sleep(20)\nopen('/work/generated.txt','w').write('too late')\n"
        ))
        interrupted = []
        with self.fixture.session(seconds=30) as session:
            capsule, charge = session._sandbox_run, session.budget.charge
            inside = False
            def run(root, **kwargs):
                nonlocal inside
                producer = kwargs["mode"] == "command" and "/repo/producer.py" in kwargs["argv"]
                previous = inside
                inside = producer or inside
                try:
                    return capsule(root, **kwargs)
                finally:
                    inside = previous
            def lose_lifetime(category, size):
                result = charge(category, size)
                if category == "output" and size and inside and not interrupted:
                    outer = next(iter(session.budget.children))
                    outer.stdin.close()
                    interrupted.append(outer.pid)
                return result
            with patch.object(session, "_sandbox_run", run), patch.object(session.budget, "charge", lose_lifetime):
                with self.assertRaisesRegex(MakeProbeError, "producer|rendezvous|lifetime"):
                    session.make("all", commands={"python3 producer.py": command})
            self.assertEqual(len(interrupted), 1)
            self.assertFalse((session.tree / "generated.txt").exists())
        self.fixture.assert_clean(session)

    def test_missing_extra_and_failed_sources_never_publish(self):
        for program, sources, expected in (
            ("print('no output')\n", (), "missing declared generated"),
            ("open('/work/generated.txt','w').write('actual')\nopen('/work/extra','w').write('extra')\n",
             (), "undeclared or nonregular generated output"),
            ("open('/work/generated.txt','w').write('actual')\n", ("required.txt",), "declared/consumed"),
        ):
            with self.subTest(expected=expected):
                command = self.producer_fixture()
                self.fixture.add("required.txt", "required")
                self.fixture.add("producer.py", program)
                command = Command(command.argv, code=command.code, sources=sources, outputs=command.outputs)
                with self.fixture.session(seconds=30) as session:
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        session.make("all", commands={"python3 producer.py": command})
                    self.assertFalse((session.tree / "generated.txt").exists())
                self.fixture.assert_clean(session)

    def test_output_capture_rejects_real_nonregular_and_oversized_files(self):
        self.fixture.add("Makefile", "all: ;\n")
        root = self.fixture.directory / "captured-output"
        root.mkdir()
        target = root / "result"
        for kind in ("symlink", "fifo", "oversized"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    target.symlink_to(self.root)
                elif kind == "fifo":
                    os.mkfifo(target)
                else:
                    with target.open("wb") as stream:
                        stream.truncate(16*1024*1024 + 1)
                with self.fixture.session(seconds=30) as session:
                    with self.assertRaises(MakeProbeError):
                        session._capture_outputs(root, ("result",))
                self.fixture.assert_clean(session)
                target.unlink()
        command = self.producer_fixture()
        with self.fixture.session(seconds=30) as session:
            with self.assertRaisesRegex(MakeProbeError, "canonical and repository-relative"):
                session.make("all", commands={
                    "python3 producer.py": Command(command.argv, code=command.code, outputs=("../escape",)),
                })
        self.fixture.assert_clean(session)

    def test_real_repository_scaninc_native_registration_is_a_live_result(self):
        sources, headers = [], []
        for path in sorted((foundation.ROOT / "tools/scaninc").iterdir()):
            if path.suffix not in {".cpp", ".h"}:
                continue
            name = path.relative_to(foundation.ROOT).as_posix()
            self.fixture.add(name, path.read_bytes())
            (sources if path.suffix == ".cpp" else headers).append(name)
        self.fixture.add("proof.s", '.incbin "proof.bin"\n')
        actual = 'tools/scaninc/scaninc -I include -I "" proof.s'
        self.fixture.add("Makefile", f"INPUTS := $(shell {actual})\nall: $(INPUTS)\nproof.bin: ;\n")
        with self.fixture.session(seconds=45) as session:
            tool = session.compile_native(tuple(sources), headers=tuple(headers), cxx=True)
            registration = Command(
                ("/native/tool", "-I", "include", "-I", "", "proof.s"),
                sources=("proof.s",), native_tool=tool,
            )
            observed = session.make("all", variables=("INPUTS",), commands={actual: registration})
            self.assertEqual(observed.semantics["domains"]["INPUTS"]["value"], "proof.bin")
            self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                {"name": "proof.bin", "order_only": False},
            ])
            dynamic, = observed.semantics["dynamic_commands"]
            self.assertEqual(dynamic["command"]["native_tool"]["sha256"], tool.digest)
            self.assertEqual(dynamic["command"]["inputs"], session.snapshot.owners(("proof.s",)))
            self.assertFalse((session.tree / "native/tool").exists())
        self.fixture.assert_clean(session)

    def test_repeated_effects_execute_while_alias_identity_deduplicates(self):
        command = self.producer_fixture()
        alias = "python3 producer.py; printf ''"
        self.fixture.add("Makefile", (
            "FIRST := $(shell python3 producer.py)\n"
            f"SECOND := $(shell {alias})\nall: ;\n"
        ))
        with self.fixture.session(seconds=30) as session:
            run, executed = session._sandbox_run, []
            def count(root, **kwargs):
                if kwargs["mode"] == "command" and "/repo/producer.py" in kwargs["argv"]:
                    executed.append(tuple(kwargs["argv"]))
                return run(root, **kwargs)
            with patch.object(session, "_sandbox_run", count):
                result = session.make("all", commands={
                    "python3 producer.py": command, alias: command,
                })
            self.assertEqual(len(executed), 2)
            self.assertEqual(len(result.events), 2)
            self.assertEqual(len(result.semantics["dynamic_commands"]), 1)
        self.fixture.assert_clean(session)
        with self.fixture.session(seconds=30) as session:
            with self.assertRaisesRegex(MakeProbeError, "conflicting generated output producers"):
                session.make("all", commands={
                    "python3 producer.py": command,
                    alias: Command((*command.argv, "different"), code=command.code, outputs=command.outputs),
                })
            self.assertFalse((session.tree / "generated.txt").exists())
        self.fixture.assert_clean(session)

    def test_chained_outputs_have_two_authentic_native_restarts(self):
        names = ("SELECTED", "MAKEFILE_LIST", "MAKE_RESTARTS")
        recipe = " ".join(f"'$({form}{name})'" for name in names for form in ("", "origin ", "flavor "))
        self.fixture.add("first.in", "include second.mk\n")
        self.fixture.add("second.in", "SELECTED := observed\nobserved: ;\n")
        self.fixture.add("producer.py", "import sys\nopen(sys.argv[2],'wb').write(open(sys.argv[1],'rb').read())\n")
        self.fixture.add("Makefile", (
            "include first.mk\nfirst.mk: first.in\n\t@python3 producer.py first.in first.mk\n"
            "second.mk: second.in\n\t@python3 producer.py second.in second.mk\n"
            f"all: $(SELECTED)\n\t@printf '%s\\n' '$^' {recipe}\n"
        ))
        ordinary = self.fixture.ordinary_assignment_context((), names)
        (self.root / "first.mk").unlink()
        (self.root / "second.mk").unlink()
        registrations = {
            f"python3 producer.py {name}.in {name}.mk": Command(
                ("/usr/bin/python3", "/repo/producer.py", name + ".in", "/work/" + name + ".mk"),
                code=("producer.py",), sources=(name + ".in",), outputs=(name + ".mk",),
            ) for name in ("first", "second")
        }
        with self.fixture.session(seconds=30) as session:
            result = session.make("all", variables=names, commands=registrations)
            self.assertEqual(result.semantics["domains"], ordinary[1])
            self.assertEqual(result.semantics["files"][0]["prerequisites"], ordinary[0])
            self.assertEqual(result.semantics["domains"]["MAKE_RESTARTS"]["value"], "2")
            self.assertEqual(len(result.semantics["dynamic_commands"]), 2)
        self.fixture.assert_clean(session)

    def test_native_parallel_dispatch_keeps_distinct_request_receipts(self):
        self.fixture.add("producer.py", (
            "import sys\n"
            "open('/work/'+sys.argv[1]+'.txt','w').write(sys.argv[1])\n"
            "print(sys.argv[1])\n"
        ))
        self.fixture.add("Makefile", (
            "MAKEFLAGS += -j2\nall: one two\n"
            "one:\n\t+@python3 producer.py one\n"
            "two:\n\t+@python3 producer.py two\n"
        ))
        registrations = {
            "python3 producer.py " + name: Command(
                ("/usr/bin/python3", "/repo/producer.py", name),
                code=("producer.py",), outputs=(name + ".txt",),
            ) for name in ("one", "two")
        }
        with self.fixture.session(seconds=30) as session:
            observed = session.make("all", commands=registrations)
            self.assertEqual(set(observed.stdout.splitlines()), {b"one", b"two"})
            self.assertEqual({event["match"] for event in observed.events}, {0, 1})
            self.assertEqual(len(observed.events), 2)
            self.assertEqual(len(observed.semantics["dynamic_commands"]), 2)
            self.assertLessEqual(session.pending_commands_peak, session.budget.limits.pending)
            self.assertFalse((session.tree / "one.txt").exists())
            self.assertFalse((session.tree / "two.txt").exists())
        self.fixture.assert_clean(session)

    def test_generated_source_replacement_invalidates_a_reader_without_stat_calls(self):
        self.fixture.add("state/current", "original")
        self.fixture.add("writer.py", (
            "from pathlib import Path\nimport os,sys\n"
            "root=Path(sys.argv[1])/'generated'\nroot.mkdir(exist_ok=True)\n"
            "(root/'input').write_text(str(len(os.listdir('state'))))\n"
        ))
        self.fixture.add("marker.py", (
            "from pathlib import Path\nimport sys\nroot=Path(sys.argv[1])/'state'\n"
            "root.mkdir(exist_ok=True)\n(root/'new').write_text('new')\n"
        ))
        self.fixture.add("reader.py", (
            "import os,sys\nfd=os.open('generated/input',os.O_RDONLY)\n"
            "sys.stdout.write(os.read(fd,16).decode())\nos.close(fd)\n"
        ))
        self.fixture.add("Makefile", (
            "WRITE1 := $(shell python3 writer.py .)\n"
            "FIRST := $(shell python3 reader.py)\n"
            "UNCHANGED := $(shell python3 reader.py)\n"
            "MARK := $(shell python3 marker.py .)\n"
            "WRITE2 := $(shell python3 writer.py .)\n"
            "SECOND := $(shell python3 reader.py)\n"
            "all:\n\t@printf '%s\\n' '$(FIRST)' '$(SECOND)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(ordinary.stdout.splitlines(), [b"1", b"2"])
        (self.root / "generated/input").unlink()
        (self.root / "generated").rmdir()
        (self.root / "state/new").unlink()
        registrations = {
            "python3 writer.py .": Command(
                ("/usr/bin/python3", "/repo/writer.py", "/work"),
                code=("writer.py",), directories=("state",), outputs=("generated/input",),
            ),
            "python3 marker.py .": Command(
                ("/usr/bin/python3", "/repo/marker.py", "/work"),
                code=("marker.py",), outputs=("state/new",),
            ),
            "python3 reader.py": Command(
                ("/usr/bin/python3", "/repo/reader.py"),
                code=("reader.py",), sources=("generated/input",),
            ),
        }
        with self.fixture.session(seconds=30) as session:
            execute, readers = session.command, []
            def record(command):
                result = execute(command)
                if command is registrations["python3 reader.py"]:
                    readers.append(result)
                return result
            with patch.object(session, "command", record):
                observed = session.make(
                    "all", variables=("FIRST", "UNCHANGED", "SECOND"), commands=registrations,
                )
            self.assertEqual(
                [observed.semantics["domains"][name]["value"] for name in ("FIRST", "UNCHANGED", "SECOND")],
                ["1", "1", "2"],
            )
            self.assertIs(readers[0], readers[1])
            self.assertIsNot(readers[1], readers[2])
        self.fixture.assert_clean(session)

    def test_live_include_preserves_actual_metadata_and_residual_resources(self):
        add = self.fixture.add
        add("choice.txt", "observed")
        add("data/current", "original")
        add("producer.py", (
            "from pathlib import Path\nimport sys\nroot=Path(sys.argv[1])\n"
            "choice=Path('choice.txt').read_text()\n"
            "(root/'data/new').mkdir(parents=True)\n"
            "(root/'data/new/value').write_text(choice)\n"
            "(root/'generated.mk').write_text('SELECTED := '+choice+'\\n'"
            "+'VALUE := $(shell python3 reader.py)\\nobserved: ;\\n')\n"
        ))
        add("reader.py", (
            "import json,os\nvalue=os.stat('data')\n"
            "print(json.dumps({name:getattr(value,name) for name in "
            "('st_ino','st_nlink','st_mtime_ns','st_ctime_ns')},sort_keys=True))\n"
        ))
        names = ("SELECTED", "MAKEFILE_LIST", "MAKE_RESTARTS")
        recipe = " ".join(f"'$({form}{name})'" for name in names for form in ("", "origin ", "flavor "))
        add("Makefile", (
            "include generated.mk\ngenerated.mk: choice.txt\n\t@python3 producer.py .\n"
            f"all: $(SELECTED)\n\t@printf '%s\\n' '$^' {recipe}\n"
        ))
        ordinary = self.fixture.ordinary_assignment_context((), names)
        (self.root / "generated.mk").unlink()
        (self.root / "data/new/value").unlink()
        (self.root / "data/new").rmdir()
        reader = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), directories=("data",),
        )
        producer = Command(
            ("/usr/bin/python3", "/repo/producer.py", "/work"), code=("producer.py",),
            sources=("choice.txt",), outputs=("generated.mk", "data/new/value"),
        )
        reports, launches, final = [], [], []
        with self.fixture.session(seconds=45) as session:
            before = session.command(reader)
            self.assertEqual(json.loads(before.stdout)["st_nlink"], 2)
            original_run, original_capsule = session.budget.run, session._sandbox_run

            def record_launch(argv, **kwargs):
                if len(argv) >= 2 and argv[-2] == str(TRUSTED_ROOT / "sandbox_exec.py"):
                    config = json.loads(Path(argv[-1]).read_bytes())
                    launches.append((config, [dict(item) for item in session.parked_capsules]))
                return original_run(argv, **kwargs)

            def record_capsule(root, **kwargs):
                result, observed = original_capsule(root, **kwargs)
                reports.append(observed)
                if kwargs["mode"] == "make":
                    info = (session.tree / "data").stat()
                    final.append({name: getattr(info, name) for name in (
                        "st_ino", "st_nlink", "st_mtime_ns", "st_ctime_ns",
                    )})
                    self.assertEqual((session.tree / "data/new/value").read_text(), "observed")
                    self.assertTrue(stat.S_ISREG((session.tree / "generated.mk").stat().st_mode))
                return result, observed

            initial = {
                "processes": session.processes_used, "syscalls": session.syscalls_used,
                "observations": session.observations_used, "created": session.files_created,
                "sandbox": session.budget.bytes.get("sandbox", 0),
            }
            with patch.object(session.budget, "run", record_launch), patch.object(
                session, "_sandbox_run", record_capsule,
            ):
                result = session.make(
                    "all", variables=(*names, "VALUE"),
                    commands={"python3 producer.py .": producer, "python3 reader.py": reader},
                )
            self.assertEqual({name: result.semantics["domains"][name] for name in names}, ordinary[1])
            self.assertEqual(result.semantics["files"][0]["prerequisites"], ordinary[0])
            self.assertEqual(result.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
            self.assertEqual(json.loads(result.semantics["domains"]["VALUE"]["value"]), final[-1])
            self.assertEqual(final[-1]["st_nlink"], 3)
            self.assertEqual(sum(config["mode"] == "make" for config, _ in launches), 1)
            nested = [(config, held) for config, held in launches if held]
            self.assertTrue(nested)
            for config, held in nested:
                self.assertGreaterEqual(sum(item["live"] for item in held), 2)
                self.assertEqual(
                    config["memory_limit"] + sum(item["memory"] for item in held),
                    session.budget.limits.address_space_bytes,
                )
                self.assertEqual(
                    config["process_limit"] + sum(item["processes"] for item in held),
                    session.budget.limits.processes,
                )
                if not config["metadata_validation"]:
                    self.assertFalse(any(item["target"] == "/control" for item in config["mounts"]))
                    self.assertTrue(any(
                        item["target"] == "/repo" and not item["writable"] and not item["executable"]
                        for item in config["mounts"]
                    ))
            self.assertEqual(session.processes_used - initial["processes"], sum(row["processes"] for row in reports))
            self.assertEqual(session.syscalls_used - initial["syscalls"], sum(row["syscalls"] for row in reports))
            self.assertEqual(session.observations_used - initial["observations"], sum(row["observations"] for row in reports))
            self.assertEqual(session.files_created - initial["created"], sum(row["created_files"] for row in reports))
            self.assertEqual(
                session.budget.bytes["sandbox"] - initial["sandbox"], sum(row["written_bytes"] for row in reports),
            )
            self.assertGreaterEqual(session.live_process_peak, 3)
            self.assertLessEqual(session.live_process_peak, session.budget.limits.processes)
            self.assertLessEqual(session.memory_peak, session.budget.limits.address_space_bytes)
            self.assertFalse((session.tree / "generated.mk").exists())
            self.assertFalse((session.tree / "data/new").exists())
        self.fixture.assert_clean(session)


if __name__ == "__main__":
    unittest.main()
