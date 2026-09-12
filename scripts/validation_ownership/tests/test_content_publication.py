"""Content-only publication: real effects, strict confirmations and convergence."""

import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import shlex
import stat
import struct
import subprocess
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from scripts.validation_ownership.authority import ENVIRONMENT, encoded
from scripts.validation_ownership.budget import Limits, MakeProbeError, ProbeBudget
from scripts.validation_ownership.make_probe import Command, ProbeSession
from scripts.validation_ownership.producer_channel import (
    ChannelError, ProducerChannel, PUBLICATION_MAGIC, PUBLICATION_POLICIES,
    publication_identity, validate_publication_confirmation,
)
from scripts.validation_ownership.python_commands import generated_dependency_command, python_command
from scripts.validation_ownership.syscall_guard import Policy, Violation
from scripts.validation_ownership.tests import test_producer as producer_tests


class ContentPublicationTests(unittest.TestCase):
    def setUp(self):
        self.support = producer_tests.ProducerTests()
        self.support.setUp()
        self.fixture = self.support.fixture
        self.root = self.fixture.root

    def tearDown(self):
        self.support.tearDown()

    def makefile(self, command, output="build/deps.mk"):
        return (
            ".DEFAULT_GOAL := all\n"
            "$(info CONTENT_RESTART=$(MAKE_RESTARTS))\n"
            "ifneq (,$(filter-out 1,$(MAKE_RESTARTS)))\n"
            "$(error CONTENT_UNEXPECTED_RESTART_$(MAKE_RESTARTS))\n"
            "endif\n.PHONY: all FORCE\nFORCE:\n"
            f"{output}: FORCE\n\t{command}\ninclude {output}\nall: ;\n"
        )

    def writer(self, policy="if-content-changed", data=b"all: ;\n", output="build/deps.mk"):
        self.fixture.add("writer.py", (
            "from pathlib import Path\n"
            f"out=Path('/work/{output}'); out.parent.mkdir(parents=True,exist_ok=True)\n"
            f"out.write_bytes({data!r})\n"
        ))
        return Command(
            ("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",),
            outputs=(output,), publication_policy=policy,
        )

    def capture_effects(self, session, values):
        original = session._verify_effective_output
        def observe(item, result):
            original(item, result)
            values.append(copy.deepcopy(result))
        return patch.object(session, "_verify_effective_output", observe)

    def test_forced_include_converges_and_default_same_byte_effects_remain(self):
        writer = self.writer()
        self.fixture.add("Makefile", self.makefile("python3 writer.py"))
        for policy in PUBLICATION_POLICIES:
            with self.subTest(policy=policy), self.fixture.session(seconds=20, runs=32) as session:
                effects, outputs = [], []
                execute = session.command
                def capture(command):
                    value = execute(command)
                    outputs.append(value)
                    return value
                with self.capture_effects(session, effects), patch.object(session, "command", capture):
                    if policy == "replace":
                        with self.assertRaisesRegex(MakeProbeError, "CONTENT_UNEXPECTED_RESTART_2"):
                            session.make("all", commands={"python3 writer.py": replace(writer, publication_policy=policy)})
                    else:
                        result = session.make(
                            "all", variables=("MAKE_RESTARTS",), commands={"python3 writer.py": writer},
                        )
                        self.assertEqual(result.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
                        self.assertEqual(len(result.events), 2)
                        self.assertEqual(result.semantics["published_sources"][0][2:5], [
                            0o644, len(b"all: ;\n"), hashlib.sha256(b"all: ;\n").hexdigest(),
                        ])
                self.assertEqual(len(outputs), 2)
                self.assertIsNot(outputs[0], outputs[1])
                self.assertEqual(outputs[0].generated, outputs[1].generated)
                self.assertEqual([item["effect"] for item in effects], [
                    "created", "replaced" if policy == "replace" else "retained",
                ])
                if policy != "replace":
                    self.assertEqual(effects[0]["identity"], effects[1]["identity"])
                self.assertGreater(session.budget.bytes["cache"], 0)
            self.fixture.assert_clean(session)

    def test_native_content_only_retains_actual_mode_and_reports_effective_metadata(self):
        self.fixture.add("writer.py", (
            "import os\nfrom pathlib import Path\n"
            "mode=0o644 if 'output.bin' in os.listdir('/repo') else 0o600\n"
            "descriptor=os.open('/work/output.bin',os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)\n"
            "with os.fdopen(descriptor,'wb') as out: out.write(b'same')\n"
        ))
        self.fixture.add("Makefile", (
            "ONE := $(shell python3 writer.py)\nTWO := $(shell python3 writer.py)\nall: ;\n"
        ))
        writer = Command(
            ("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",), directories=(".",),
            outputs=("output.bin",), publication_policy="if-content-changed",
        )
        effects, produced = [], []
        with self.fixture.session(seconds=20) as session:
            execute = session.command
            def capture(command):
                result = execute(command)
                produced.append(result.generated[0])
                return result
            with self.capture_effects(session, effects), patch.object(session, "command", capture):
                observed = session.make("all", commands={"python3 writer.py": writer})
            self.assertEqual([item.mode for item in produced], [0o600, 0o644])
            self.assertEqual([item["mode"] for item in effects], [0o600, 0o600])
            self.assertEqual(effects[0]["identity"], effects[1]["identity"])
            self.assertEqual(effects[1]["effect"], "retained")
            self.assertEqual(observed.semantics["published_sources"][0][2], 0o600)
            self.assertEqual(len(observed.events), 2)
            differing, = [
                value for value in observed.semantics["dynamic_commands"]
                if value["produced_outputs"][0][1] == "100644"
            ]
            self.assertEqual(differing["generated_outputs"][0][1], "100600")
        self.fixture.assert_clean(session)

    def test_native_changed_content_replaces_then_retains_the_new_effective_mode(self):
        self.fixture.add("writer.py", (
            "import os\n"
            "changed='switch' in os.listdir('/repo')\n"
            "descriptor=os.open('/work/output.bin',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644 if changed else 0o600)\n"
            "with os.fdopen(descriptor,'wb') as stream: stream.write(b'new' if changed else b'old')\n"
        ))
        self.fixture.add("marker.py", "open('/work/switch','wb').write(b'changed')\n")
        self.fixture.add("Makefile", (
            "ONE := $(shell python3 writer.py)\n"
            "SWITCH := $(shell python3 marker.py)\n"
            "TWO := $(shell python3 writer.py)\n"
            "THREE := $(shell python3 writer.py)\nall: ;\n"
        ))
        writer = Command(
            ("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",), directories=(".",),
            outputs=("output.bin",), publication_policy="if-content-changed",
        )
        marker = Command(
            ("/usr/bin/python3", "/repo/marker.py"), code=("marker.py",), outputs=("switch",),
        )
        with self.fixture.session(seconds=25) as session:
            effects = []
            with self.capture_effects(session, effects):
                observed = session.make("all", commands={
                    "python3 writer.py": writer, "python3 marker.py": marker,
                })
            outputs = [item for item in effects if item["path"] == "output.bin"]
            self.assertEqual([item["effect"] for item in outputs], ["created", "replaced", "retained"])
            self.assertEqual([item["mode"] for item in outputs], [0o600, 0o644, 0o644])
            self.assertEqual(outputs[1]["identity"], outputs[2]["identity"])
            self.assertNotEqual(outputs[0]["sha256"], outputs[1]["sha256"])
            final, = [item for item in observed.semantics["published_sources"] if item[0] == "output.bin"]
            self.assertEqual(final[2:5], [0o644, 3, hashlib.sha256(b"new").hexdigest()])
            self.assertEqual(len(observed.events), 4)
        self.fixture.assert_clean(session)

    def policy_fixture(self):
        root = self.fixture.directory / "physical-publication"
        (root / "control/map").mkdir(parents=True)
        policy = Policy({
            "root": str(root), "mode": "make", "code": [], "sources": [], "enumerations": [],
            "executables": [], "python_version": "3.12", "argv": [],
            "mounts": [{"target": "/repo", "source": str(self.root)}],
            "reserved_paths": list(self.fixture.entries), "sudo_drop": False,
            "runner_uid": os.getuid(), "runner_gid": os.getgid(),
            "creation_limit": 16, "file_limit": 4096, "observation_limit": 32768,
            "write_limit": 4096, "deadline": time.monotonic() + 10,
        })
        return policy

    def physical_publish(self, policy, data=b"same", mode=0o644, *, selected="if-content-changed",
                         owner="01"*32, path="output.bin", wire_policy=None):
        name = path.encode()
        frame = (
            PUBLICATION_MAGIC + bytes.fromhex(owner)
            + struct.pack("<II", PUBLICATION_POLICIES.index(selected) if wire_policy is None else wire_policy, 1)
            + struct.pack("<III", len(name), mode, len(data)) + name + data
        )
        (Path(policy.config["root"]) / "control/map/0000000000000000.files").write_bytes(frame)
        return policy.publish(0, owner=owner, outputs=[path], policy=selected)

    def test_physical_missing_equal_content_and_changed_content_account_real_effects(self):
        policy = self.policy_fixture()
        first, = self.physical_publish(policy, mode=0o600)
        before = (self.root / "output.bin").stat()
        writes, creations, control = policy.written, policy.created, policy.observation_bytes
        kept, = self.physical_publish(policy, mode=0o644)
        self.assertEqual(kept["effect"], "retained")
        self.assertEqual(kept["mode"], 0o600)
        self.assertEqual(publication_identity(before), publication_identity((self.root / "output.bin").stat()))
        self.assertEqual((policy.written, policy.created), (writes, creations))
        self.assertGreater(policy.observation_bytes, control)
        replaced, = self.physical_publish(policy, data=b"new!", mode=0o644)
        self.assertEqual((replaced["effect"], replaced["mode"]), ("replaced", 0o644))
        self.assertEqual((self.root / "output.bin").read_bytes(), b"new!")
        self.assertEqual(policy.written, writes + 4)
        self.assertEqual(policy.created, creations + 1)
        (self.root / "output.bin").unlink()
        created, = self.physical_publish(policy, data=b"new!")
        self.assertEqual(created["effect"], "created")

    def test_publication_rejects_policy_owner_source_and_nonregular_boundaries(self):
        for defect in ("wire-policy", "owner", "source", "symlink", "directory", "fifo"):
            with self.subTest(defect=defect):
                self.fixture.entries.clear()
                if defect == "source":
                    self.fixture.add("output.bin", b"immutable")
                policy = self.policy_fixture_for_case(defect)
                if defect in {"wire-policy", "source"}:
                    with self.assertRaises(Violation):
                        self.physical_publish(policy, wire_policy=99 if defect == "wire-policy" else None)
                    if defect == "source":
                        (self.root / "output.bin").unlink()
                    continue
                self.physical_publish(policy)
                path = self.root / "output.bin"
                if defect == "owner":
                    with self.assertRaisesRegex(Violation, "conflicting.*producer"):
                        self.physical_publish(policy, owner="02"*32)
                else:
                    path.unlink()
                    if defect == "symlink":
                        path.symlink_to("other")
                    elif defect == "directory":
                        path.mkdir()
                    else:
                        os.mkfifo(path)
                    with self.assertRaisesRegex(Violation, "identity or type"):
                        self.physical_publish(policy)
                    if path.is_dir():
                        path.rmdir()
                    else:
                        path.unlink()
                if path.exists():
                    path.unlink()

    def policy_fixture_for_case(self, name):
        original = self.fixture.directory
        self.fixture.directory = original / name
        self.fixture.directory.mkdir()
        try:
            return self.policy_fixture()
        finally:
            self.fixture.directory = original

    def test_content_comparison_rejects_read_errors_and_object_races(self):
        for defect in ("read-error", "replace", "mode"):
            with self.subTest(defect=defect):
                policy = self.policy_fixture_for_case(defect)
                self.physical_publish(policy)
                path = self.root / "output.bin"
                original, changed = os.pread, []
                def read(descriptor, size, offset):
                    result = original(descriptor, size, offset)
                    if not changed:
                        changed.append(True)
                        if defect == "read-error":
                            raise OSError(errno.EIO, "actual comparison read failed")
                        if defect == "replace":
                            other = self.root / "replacement"
                            other.write_bytes(b"same")
                            os.replace(other, path)
                        else:
                            path.chmod(0o600)
                    return result
                with patch.object(os, "pread", read):
                    with self.assertRaises((OSError, Violation)):
                        self.physical_publish(policy)
                self.assertTrue(changed)
                path.unlink()

    def test_unknown_or_empty_output_policy_rejects_before_execution(self):
        for policy in (None, True, 1, "", "if-mode-changed"):
            with self.subTest(policy=policy), self.assertRaises(MakeProbeError):
                Command(("/usr/bin/printf", "x"), outputs=("x",), publication_policy=policy)
        with self.assertRaises(MakeProbeError):
            Command(("/usr/bin/printf", "x"), publication_policy="if-content-changed")
        self.fixture.add("Makefile", "all: ;\n")
        with self.fixture.session() as session:
            with self.assertRaises(MakeProbeError):
                python_command(session, "print('x')", outputs=("x",), publication_policy="unknown")
        self.fixture.assert_clean(session)

    def test_effective_confirmation_parser_rejects_invalid_shapes_and_types(self):
        policy = self.policy_fixture()
        result, = self.physical_publish(policy)
        good = {"slot": 0, "owner": "01"*32, "policy": "if-content-changed", "outputs": [result]}
        validate_publication_confirmation(good, count_limit=4, file_limit=4096)
        for field, value in (
            ("mode", True), ("size", -1), ("sha256", "bad"), ("effect", "unknown"),
            ("identity", [0]), ("path", ""),
        ):
            bad = copy.deepcopy(good)
            bad["outputs"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ChannelError):
                validate_publication_confirmation(bad, count_limit=4, file_limit=4096)
        for field, value in (("slot", True), ("owner", "bad"), ("policy", "unknown"), ("extra", 0)):
            bad = copy.deepcopy(good)
            bad[field] = value
            with self.subTest(field=field), self.assertRaises(ChannelError):
                validate_publication_confirmation(bad, count_limit=4, file_limit=4096)

    def test_actual_wire_confirmation_rejects_forged_policy_mode_identity_and_effect(self):
        writer = self.writer(data=b"actual", output="output.bin")
        self.fixture.add("Makefile", "VALUE := $(shell python3 writer.py)\nall: ;\n")
        for defect in ("policy", "mode", "identity", "effect", "digest", "missing"):
            with self.subTest(defect=defect), self.fixture.session(seconds=20) as session:
                receive, changed = ProducerChannel.receive, []
                def corrupt(channel):
                    packet = receive(channel)
                    if packet is None:
                        return None
                    value = json.loads(packet)
                    confirmation = value.get("publication")
                    if confirmation and not changed:
                        changed.append(True)
                        if defect == "policy":
                            confirmation["policy"] = "replace"
                        elif defect == "mode":
                            confirmation["outputs"][0]["mode"] = 0o600
                            confirmation["outputs"][0]["identity"][2] = stat.S_IFREG | 0o600
                        elif defect == "identity":
                            confirmation["outputs"][0]["identity"][1] += 1
                        elif defect == "effect":
                            confirmation["outputs"][0]["effect"] = "retained"
                        elif defect == "digest":
                            confirmation["outputs"][0]["sha256"] = "00"*32
                        else:
                            del value["publication"]
                    return encoded(value)
                with patch.object(ProducerChannel, "receive", corrupt):
                    with self.assertRaises(MakeProbeError):
                        session.make("all", commands={"python3 writer.py": writer})
                self.assertTrue(changed)
            self.fixture.assert_clean(session)

    def assert_real_adapter(self, name, *, membership=False, unconditional=False):
        cases = {item["name"]: item for item in self.support.add_generated_dependency_fixture()}
        case = cases[name]
        options = dict(case["options"])
        options["--bundle-source"] = "testdata/bundles" if membership else "testdata/bundles/el_bundle.json"
        for flag, value in tuple(options.items()):
            if value == "testdata/objectives":
                options[flag] = "testdata/objectives/el_objectives.json"
        arguments = [value for pair in options.items() for value in pair]
        arguments.extend(("--make-target", "all", "--depfile", "build/real.inputs.mk"))
        command = shlex.join(["python3", "-m", case["module"], *arguments])
        self.fixture.add("Makefile", self.makefile(command, "build/real.inputs.mk"))
        if membership:
            seed = subprocess.run(
                ["/usr/bin/python3", "-m", case["module"], *arguments],
                cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=15,
            )
            self.assertEqual(seed.returncode, 0, seed.stderr)
            previous = (self.root / "build/real.inputs.mk").read_text()
            other = json.loads((self.root / "testdata/bundles/el_bundle.json").read_text())
            other["chapter"]["id"] = "CHAPTER_L_2"
            self.fixture.add("testdata/bundles/new_bundle.json", json.dumps(other))
            self.assertNotIn("new_bundle.json", previous)
        normal = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, timeout=20,
        )
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertEqual(
            [line for line in normal.stdout.decode().splitlines() if line.startswith("CONTENT_RESTART=")],
            ["CONTENT_RESTART=", "CONTENT_RESTART=1"],
        )
        ordinary = (self.root / "build/real.inputs.mk").read_text()
        produced, effects = [], []
        with self.fixture.session(seconds=45, runs=64) as session:
            registration = generated_dependency_command(
                session, case["module"], option_values=options,
                make_target="all", depfile="build/real.inputs.mk",
            )
            if unconditional:
                registration = replace(registration, publication_policy="replace")
            execute = session.command
            def capture(command):
                result = execute(command)
                produced.append(result)
                return result
            with self.capture_effects(session, effects), patch.object(session, "command", capture):
                if unconditional:
                    with self.assertRaisesRegex(MakeProbeError, "CONTENT_UNEXPECTED_RESTART_2"):
                        session.make(
                            "all", variables=("MAKE_RESTARTS",), commands={command: registration},
                        )
                else:
                    observed = session.make(
                        "all", variables=("MAKE_RESTARTS",), commands={command: registration},
                    )
            self.assertEqual([item["effect"] for item in effects], [
                "created", "replaced" if unconditional else "retained",
            ])
            if not unconditional:
                self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
                self.assertEqual(registration.publication_policy, "if-content-changed")
                self.assertEqual(len(observed.events), 2)
                self.assertEqual(effects[0]["identity"], effects[1]["identity"])
            self.assertEqual(len(produced), 2)
            expected = [value.removeprefix(str(self.root) + "/") for value in ordinary.partition(": ")[2].split()]
            actual = [
                value.removeprefix("/repo/")
                for value in produced[0].generated[0].data.decode().partition(": ")[2].split()
            ]
            self.assertEqual(actual, expected)
            self.assertEqual(produced[0].generated, produced[1].generated)
            for result in produced:
                self.assertEqual(result.consumed, registration.sources)
                self.assertTrue(result.input_identities)
                if membership:
                    self.assertIn("testdata/bundles/new_bundle.json", result.consumed)
            if not unconditional:
                self.assertEqual(
                    observed.semantics["published_sources"][0][2:5],
                    [effects[-1]["mode"], effects[-1]["size"], effects[-1]["sha256"]],
                )
        self.fixture.assert_clean(session)

    def test_real_eventlists_adapter_converges_like_ordinary_cli(self):
        self.assert_real_adapter("eventlists")

    def test_real_eventlists_unconditional_control_reaches_native_restart_guard(self):
        self.assert_real_adapter("eventlists", unconditional=True)

    def test_real_chapterobjectives_adapter_converges_like_ordinary_cli(self):
        self.assert_real_adapter("chapterobjectives")

    def test_real_autoplay_adapter_converges_like_ordinary_cli(self):
        self.assert_real_adapter("autoplaystrategies")

    def test_real_eventlists_new_membership_is_selected_consumed_and_published(self):
        self.assert_real_adapter("eventlists", membership=True)

    def test_nested_content_only_publication_keeps_effective_mode_and_ownership(self):
        self.fixture.add("writer.py", (
            "import os\n"
            "mode=0o644 if 'output.bin' in os.listdir('/repo') else 0o600\n"
            "descriptor=os.open('/work/output.bin',os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)\n"
            "with os.fdopen(descriptor,'wb') as stream: stream.write(b'same')\n"
        ))
        self.fixture.add("nested.py", "print('done')\n")
        self.fixture.add("Makefile", "ONE := $(shell python3 writer.py)\nTWO := $(shell python3 nested.py)\nall: ;\n")
        self.fixture.add("inner.mk", "VALUE := $(shell python3 writer.py)\ninner: ;\n")
        writer = Command(
            ("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",), directories=(".",),
            outputs=("output.bin",), publication_policy="if-content-changed",
        )
        nested, effects = [], []
        with self.fixture.session(seconds=25) as session:
            class Commands:
                def __contains__(self, name):
                    return name in {"python3 writer.py", "python3 nested.py"}
                def __getitem__(self, name):
                    if name == "python3 nested.py":
                        nested.append(session.make(
                            "inner", makefile="inner.mk", commands={"python3 writer.py": writer},
                        ))
                        return Command(("/usr/bin/printf", "done"))
                    return writer
            with self.capture_effects(session, effects):
                result = session.make("all", commands=Commands())
            self.assertEqual(len(nested), 1)
            self.assertEqual([value["effect"] for value in effects], ["created", "retained"])
            self.assertEqual(effects[0]["identity"], effects[1]["identity"])
            self.assertEqual(result.semantics["published_sources"][0][2], 0o600)
            self.assertEqual(nested[0].semantics["published_sources"], result.semantics["published_sources"])
            self.assertEqual(len(result.events), 2)
            self.assertEqual(len(nested[0].events), 1)
            self.assertFalse(session.published_sources)
            self.assertFalse(session.published_versions)
            self.assertFalse((session.tree / "output.bin").exists())
        self.fixture.assert_clean(session)

    def test_content_only_current_base_current_uses_each_actual_source_view(self):
        self.fixture.add("value.txt", "base")
        self.fixture.add("writer.py", (
            "from pathlib import Path\n"
            "Path('/work/output.bin').write_bytes(Path('/repo/value.txt').read_bytes())\n"
        ))
        self.fixture.add("Makefile", "ONE := $(shell python3 writer.py)\nTWO := $(shell python3 writer.py)\nall: ;\n")
        budget = ProbeBudget(Limits(seconds=35, runs=64))
        try:
            base = self.fixture.capture_view(budget)
            self.fixture.add("value.txt", "current")
            current = self.fixture.capture_view(budget)
            writer = Command(
                ("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",), sources=("value.txt",),
                outputs=("output.bin",), publication_policy="if-content-changed",
            )
            outcomes = []
            with ProbeSession(current, scratch_root=self.fixture.scratch, budget=budget) as session:
                for loader, data in ((current, b"current"), (base, b"base"), (current, b"current")):
                    with session.select_view(loader):
                        effects = []
                        with self.capture_effects(session, effects):
                            observed = session.make("all", commands={"python3 writer.py": writer})
                        self.assertEqual([item["effect"] for item in effects], ["created", "retained"])
                        self.assertEqual(observed.semantics["published_sources"][0][4], hashlib.sha256(data).hexdigest())
                        self.assertEqual(len(observed.events), 2)
                        self.assertFalse(session.published_sources)
                        outcomes.append(observed.semantics["published_sources"])
                self.assertEqual(outcomes[0], outcomes[2])
                self.assertNotEqual(outcomes[0], outcomes[1])
                self.assertIs(session.loader, current)
            self.fixture.assert_clean(session)
        finally:
            budget.close()

    def test_terminal_report_cannot_change_an_acknowledged_publication(self):
        writer = self.writer(data=b"data", output="output.bin")
        self.fixture.add("Makefile", "VALUE := $(shell python3 writer.py)\nall: ;\n")
        with self.fixture.session(seconds=20) as session:
            read, changed = session.budget.read_bytes, []
            def corrupt(path, category):
                data = read(path, category)
                if path.name.startswith("report-") and category == "control":
                    value = json.loads(data)
                    if "rendezvous" in value and value["rendezvous"]["publication"]:
                        value["rendezvous"]["publication"]["owner"] = "00"*32
                        changed.append(True)
                        return encoded(value)
                return data
            with patch.object(session.budget, "read_bytes", corrupt):
                with self.assertRaisesRegex(MakeProbeError, "partial or inconsistent"):
                    session.make("all", commands={"python3 writer.py": writer})
            self.assertTrue(changed)
        self.fixture.assert_clean(session)


if __name__ == "__main__":
    unittest.main()
