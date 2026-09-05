from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import threading
import time
import unittest
from pathlib import Path

from scripts.validation_ownership.authority import AuthorityLoader, ENVIRONMENT, GitTreeEntry, git_tree_entries
from scripts.validation_ownership.budget import Limits, MakeProbeError, ProbeBudget
from scripts.validation_ownership.make_probe import Command, ProbeSession, _read_events, _read_observation


ROOT = Path(__file__).resolve().parents[3]


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "build/test-artifacts/ownership-foundation-tests" / secrets.token_hex(12)
        self.root = self.directory / "repo"
        self.root.mkdir(parents=True)
        self.entries = {}
        self.scratch = self.root / "build/probe"

    def tearDown(self):
        shutil.rmtree(self.directory)

    def add(self, path, value, mode="100644"):
        data = value.encode("utf-8") if isinstance(value, str) else value
        destination = self.root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        self.entries[path] = GitTreeEntry(path, mode, "blob", hashlib.sha1(data).hexdigest())

    def session(self, **limits):
        return ProbeSession(
            AuthorityLoader(self.root, dict(self.entries)),
            scratch_root=self.scratch,
            budget=ProbeBudget(Limits(**limits)),
        )

    def assert_clean(self, session):
        self.assertFalse(session.cache)
        self.assertFalse(session.mappings)
        self.assertFalse(session.budget.children)
        self.assertIsNone(session.snapshot)
        self.assertIsNone(session.base)
        self.assertFalse(self.scratch.exists())

    def test_authentic_make_target_and_domain_semantics(self):
        self.add("Makefile", "MODE ?= red\ninclude rules.mk\n")
        self.add("rules.mk", (
            "MODE_DEPS = dep-$(MODE)\n"
            "define rule\n"
            "$(1): $$(MODE_DEPS) | order\n"
            "\t@printf '%s\\n' '$$@'\n"
            "endef\n"
            "$(eval $(call rule,owned))\n"
            "dep-red dep-blue order: ;\n"
            ".PHONY: owned\n"
        ))
        with self.session() as session:
            observations = session.variants(
                "owned", [(), (("command-line", "MODE", "blue"),)],
                variables=("MODE",), owner_inputs=("rules.mk",),
            )
            for observation, expected in zip(observations, ("red", "blue")):
                target = observation.semantics["files"][0]
                self.assertEqual(target["target"], "owned")
                self.assertEqual(target["prerequisites"], [
                    {"name": "dep-" + expected, "order_only": False},
                    {"name": "order", "order_only": True},
                ])
                self.assertIn("printf", target["recipe"])
                self.assertEqual(observation.semantics["domains"]["MODE"]["value"], expected)
            self.assertNotEqual(observations[0].semantic_digest, observations[1].semantic_digest)
        self.assert_clean(session)

    def test_raw_binary_registered_output_and_concrete_source(self):
        self.add("data/value.bin", b"\x00\xff\r\n")
        self.add("reader.py", "import os\nos.write(1, open('data/value.bin', 'rb').read())\n")
        with self.session() as session:
            output = session.command(Command(
                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                code=("reader.py",), sources=("data/value.bin",),
            ))
            self.assertEqual(output.stdout, b"\x00\xff\r\n")
            self.assertEqual(output.consumed, ("data/value.bin",))
        self.assert_clean(session)

    def test_native_prefixed_controls_reproduce_descriptor_forgery(self):
        """Real benign pre-fix Make load/SHELL controls write their inherited FD."""
        channel_path = self.directory / "pre-fix-channel"
        with channel_path.open("wb") as channel:
            fd = channel.fileno()
            self.add("payload.c", (
                "#include <unistd.h>\nint plugin_is_GPL_compatible;\n"
                f"static void payload(void) {{ write({fd}, \"forged\", 6); }}\n"
                "int payload_gmk_setup(void) { payload(); return 1; }\n"
                "int main(void) { payload(); return 0; }\n"
            ))
            for flags, name in ((("-shared", "-fPIC"), "payload.so"), ((), "payload")):
                compiled = subprocess.run(
                    ["/usr/bin/cc", *flags, str(self.root / "payload.c"), "-o", str(self.root / name)],
                    env={**ENVIRONMENT, "TMPDIR": str(self.directory)}, capture_output=True, timeout=20,
                )
                self.assertEqual(compiled.returncode, 0, compiled.stderr)
                self.add(name, (self.root / name).read_bytes(), "100755")
            for prefix in (
                "load ./payload.so\n",
                "override SHELL := ./payload\nX := $(shell ignored)\n",
            ):
                with self.subTest(prefix=prefix):
                    self.add("Makefile", prefix + "all: ;\n")
                    channel.seek(0)
                    channel.truncate()
                    before = subprocess.run(
                        ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
                        cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                        pass_fds=(fd,), capture_output=True, timeout=10,
                    )
                    self.assertEqual(before.returncode, 0, before.stderr)
                    self.assertEqual(channel_path.read_bytes(), b"forged")
                    channel.seek(0)
                    channel.truncate()
                    session = self.session()
                    with self.assertRaises(MakeProbeError):
                        with session:
                            session.make("all")
                    self.assertEqual(channel_path.read_bytes(), b"")
                    self.assert_clean(session)

    def test_make_cannot_open_observation_mapping_event_or_fd_paths(self):
        controls = [
            "$(file >/control/events,forged)",
            "$(file >/control/result,VOMAKE1)",
            "$(file </control/map/count)",
            "include /control/result",
            "$(eval $(file >/control/events,forged))",
            "$(file >/proc/self/fd/3,forged)",
            "$(file >/dev/fd/3,forged)",
            "$(file >/repo/../control/result,forged)",
        ]
        for payload in controls:
            with self.subTest(payload=payload):
                self.add("Makefile", payload + "\nall: ;\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "channel denied|namespace denied"):
                    with session:
                        session.make("all")
                self.assert_clean(session)

    def test_make_native_dispatch_and_shell_flags_are_not_authority(self):
        controls = [
            "override SHELL := /usr/bin/make\nX := $(shell --version)\n",
            "override SHELL := /lib64/ld-linux-x86-64.so.2\nX := $(shell ignored)\n",
            "override .SHELLFLAGS := -ec\nX := $(shell printf ok)\n",
            "override SHELL := /repo/native\nX := $(shell ignored)\n",
        ]
        self.add("native", Path("/usr/bin/true").read_bytes(), "100755")
        for payload in controls:
            with self.subTest(payload=payload):
                self.add("Makefile", payload + "all: ;\n")
                session = self.session()
                with self.assertRaises(MakeProbeError):
                    with session:
                        session.make("all")
                self.assert_clean(session)

    def test_stdout_cannot_forge_native_target_or_domain_results(self):
        self.add("Makefile", (
            "$(info VOMAKE1 fake-domain blue)\n"
            "$(info Considering target file 'forged'.)\n"
            "$(info Makefile:1: update target 'all' due to: forged)\n"
            "MODE := red\nall: genuine\n\t@printf ok\n"
            "genuine: ;\n"
        ))
        with self.session() as session:
            result = session.make("all", variables=("MODE",))
            self.assertIn(b"forged", result.stdout)
            self.assertEqual(result.semantics["domains"]["MODE"]["value"], "red")
            self.assertEqual(result.semantics["files"][0]["prerequisites"], [
                {"name": "genuine", "order_only": False},
            ])
            self.assertEqual({item["target"] for item in result.semantics["files"]}, {"all", "genuine"})
        self.assert_clean(session)

    def test_parse_time_file_reads_are_confined_and_source_symlinks_reject(self):
        self.add("owner.txt", b"real")
        self.add("Makefile", "VALUE := $(file <owner.txt)\nall: ;\n")
        with self.session() as session:
            result = session.make("all", variables=("VALUE",), owner_inputs=("owner.txt",))
            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "real")
        self.assert_clean(session)
        (self.root / "link").symlink_to("owner.txt")
        self.entries["link"] = GitTreeEntry("link", "120000", "blob", "0" * 40)
        self.add("Makefile", "VALUE := $(file <link)\nall: ;\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "nonregular candidate source"):
            with session:
                session.make("all")
        self.assert_clean(session)

    def test_eager_command_replay_after_parse_failure_is_exact_and_real(self):
        self.add("value.txt", "alpha\n")
        self.add("reader.py", "import os\nos.write(1, open('value.txt', 'rb').read())\n")
        self.add("Makefile", (
            "VALUE := $(shell python3 -I -B reader.py)\n"
            "ifeq ($(VALUE),)\n$(error value has not been supplied)\nendif\nall: ;\n"
        ))
        command = Command(
            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
            code=("reader.py",), sources=("value.txt",),
        )
        with self.session() as session:
            result = session.make(
                "all", variables=("VALUE",), owner_inputs=("Makefile", "value.txt"),
                commands={"python3 -I -B reader.py": command},
            )
            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "alpha")
            self.assertTrue(result.events)
            self.assertTrue(all(event["match"] == 0 for event in result.events))
            self.assertEqual(len(session.cache), 1)
        self.assert_clean(session)

    def test_registry_source_open_mmap_stat_and_directory_glob_are_real(self):
        self.add("data/a.bin", b"ab")
        self.add("data/b.bin", b"cd")
        self.add("reader.py", (
            "import glob, mmap, os\n"
            "paths = sorted(glob.glob('data/*.bin'))\n"
            "for path in paths:\n"
            " assert os.stat(path).st_size == 2\n"
            " with open(path, 'rb') as source:\n"
            "  with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as view:\n"
            "   os.write(1, view[:])\n"
        ))
        with self.session() as session:
            output = session.command(Command(
                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                code=("reader.py",), sources=("data/*.bin",), directories=("data",),
            ))
            self.assertEqual(output.stdout, b"abcd")
            self.assertEqual(output.consumed, ("data/a.bin", "data/b.bin"))
        self.assert_clean(session)

    def test_undeclared_sources_reject_even_when_errors_are_caught(self):
        self.add("data/admitted", "yes")
        self.add("hidden/value", "secret-test-fixture")
        operations = [
            "open('hidden/value').read()",
            "os.stat('hidden/value')",
            "os.lstat('hidden/value')",
            "os.access('hidden/value', os.R_OK)",
            "os.listdir('hidden')",
            "list(glob.iglob('hidden/*'))",
            "os.readlink('hidden/value')",
            "mmap.mmap(os.open('hidden/value', os.O_RDONLY), 0, access=mmap.ACCESS_READ)",
            "os.stat('hid' + 'den/' + 'value')",
            "os.stat('/usr/share/doc')",
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import glob, mmap, os\nopen('data/admitted').read()\n"
                    "try:\n " + operation + "\nexcept OSError:\n pass\nprint('accepted')\n"
                ))
                # Negative control uses the same real function and files without
                # confinement, rather than a string assertion about its source.
                before = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(self.root / "reader.py")],
                    cwd=self.root, capture_output=True, timeout=10, env=ENVIRONMENT,
                )
                self.assertEqual(before.returncode, 0, before.stderr)
                self.assertIn(b"accepted", before.stdout)
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "undeclared source"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                            code=("reader.py",), sources=("data/admitted",),
                        ))
                self.assert_clean(session)

    def test_registry_cannot_reach_channels_descriptors_or_escape_syscalls(self):
        operations = [
            "open('/control/events', 'wb').write(b'forged')",
            "open('/control/result', 'wb').write(b'forged')",
            "open('/proc/self/fd/3', 'rb').read()",
            "os.fstat(3)",
            "os.open('/repo', os.O_PATH); os.fstat(100)",
            "ctypes.CDLL(None).syscall(101, 0, 0, 0, 0)",
            "ctypes.CDLL(None).syscall(319, b'payload', 0)",
        ]
        for operation in operations:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes, os\ntry:\n " + operation
                    + "\nexcept OSError:\n pass\nprint('accepted')\n"
                ))
                session = self.session()
                with self.assertRaises(MakeProbeError):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_declared_reported_and_consumed_sources_must_agree(self):
        self.add("data/a", "a")
        self.add("data/b", "b")
        for read, reported, expected in (
            ("open('data/a').read()", ["data/a"], "declared/consumed"),
            ("open('data/a').read(); open('data/b').read()", ["data/a"], "declared/reported/consumed"),
        ):
            self.add("reader.py", read + "\nprint(" + repr(json.dumps({
                "name": "fixture", "version": 1, "record_count": 2, "source_paths": reported,
            })) + ")\n")
            session = self.session()
            with self.assertRaisesRegex(MakeProbeError, expected):
                with session:
                    session.registry(Command(
                        ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                        code=("reader.py",), sources=("data/a", "data/b"),
                    ))
            self.assert_clean(session)

    def test_semantic_identity_excludes_unrelated_snapshot_and_live_cache_drift(self):
        self.add("Makefile", "VALUE := $(file <owner.txt)\nall: ;\n")
        self.add("owner.txt", "one")
        self.add("notes.md", "documentation")
        self.add("other.c", "int unrelated;\n")
        identities = []
        for path, content in (
            (None, None), ("notes.md", "new unrelated documentation"),
            ("other.c", "int unrelated = 2;\n"), ("owner.txt", "two"),
        ):
            if path:
                # Deliberately do not change Git entry IDs: live-byte identity
                # must not accidentally reuse an index-only command namespace.
                (self.root / path).write_text(content)
            with self.session() as session:
                result = session.make("all", variables=("VALUE",), owner_inputs=("Makefile", "owner.txt"))
                output = session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "-c", "import os; os.write(1, open('owner.txt','rb').read())"),
                    sources=("owner.txt",),
                ))
                identities.append((result.semantic_digest, result.execution_digest, output.stdout))
            self.assert_clean(session)
        self.assertEqual(len({row[0] for row in identities[:3]}), 1)
        self.assertEqual(len({row[1] for row in identities}), 4)
        self.assertNotEqual(identities[0][0], identities[3][0])
        self.assertEqual([row[2] for row in identities], [b"one", b"one", b"one", b"two"])

    def test_snapshot_is_stable_within_one_session(self):
        self.add("owner.txt", "one")
        command = Command(
            ("/usr/bin/python3", "-I", "-B", "-c", "import os; os.write(1,open('owner.txt','rb').read())"),
            sources=("owner.txt",),
        )
        with self.session() as session:
            first = session.command(command)
            (self.root / "owner.txt").write_text("changed")
            second = session.command(command)
            self.assertIs(first, second)
            self.assertEqual(second.stdout, b"one")
        self.assert_clean(session)

    def test_variant_limit_rejects_before_any_variant_launch(self):
        self.add("Makefile", "all: ;\n")
        with self.session(states=2) as session:
            runs = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "before launch"):
                session.variants("all", [(), (), ()])
            self.assertEqual(session.budget.runs, runs)
        self.assert_clean(session)

    def test_global_deadline_is_not_reset_per_process(self):
        before = time.monotonic()
        for _ in range(2):
            subprocess.run(
                ["/usr/bin/python3", "-I", "-c", "import time; time.sleep(0.18)"],
                env=ENVIRONMENT, timeout=0.3, check=True,
            )
        self.assertGreater(time.monotonic() - before, 0.3)
        budget = ProbeBudget(Limits(seconds=0.3))
        with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline"):
            for _ in range(2):
                budget.run(
                    ["/usr/bin/python3", "-I", "-c", "import time; time.sleep(0.18)"],
                    env=ENVIRONMENT,
                )
        self.assertFalse(budget.children)
        self.assertLess(time.monotonic() - budget.started, 1.5)

    def test_streaming_output_cache_and_scratch_storage_are_aggregate_bounded(self):
        for limits, program, expected in (
            ({"process_output_bytes": 512}, "import os\nos.write(1,b'x'*10000)", "output"),
            ({"cache_bytes": 200}, "import os\nos.write(1,b'x'*300)", "cache"),
            ({"sandbox_bytes": 2048}, "open('/work/a','wb').write(b'x'*5000)", "aggregate sandbox"),
            ({"created_files": 2}, "[open('/work/'+str(n),'wb').close() for n in range(4)]", "creation"),
        ):
            with self.subTest(limits=limits):
                self.add("reader.py", program)
                session = self.session(**limits)
                with self.assertRaisesRegex(MakeProbeError, expected):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_fanout_bound_and_parent_interrupt_clean_descendants(self):
        self.add("reader.py", (
            "import os,time\n"
            "for n in range(5):\n"
            " if os.fork()==0:\n"
            "  time.sleep(20)\n"
            "  os._exit(0)\n"
            "time.sleep(20)\n"
        ))
        session = self.session(processes=3)
        with self.assertRaisesRegex(MakeProbeError, "descendant-process"):
            with session:
                session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
        self.assert_clean(session)
        self.add("reader.py", "import os,time\nif os.fork()==0: time.sleep(20)\ntime.sleep(20)\n")
        session = self.session()
        with self.assertRaises(KeyboardInterrupt):
            with session:
                timer = threading.Timer(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM))
                timer.start()
                try:
                    session.command(Command(
                        ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                    ))
                finally:
                    timer.cancel()
                    timer.join()
        self.assert_clean(session)

    def test_strict_named_protocols_reject_binary_and_truncated_frames(self):
        self.add("reader.py", "import os\nos.write(1,b'\\xff\\x00\\r\\n')\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "strict utf-8"):
            with session:
                session.registry(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
        self.assert_clean(session)
        for raw in (b"", b"VOMAKE1\0", b"forged", b"VOMAKE1\0" + b"\xff" * 4):
            with self.assertRaises(MakeProbeError):
                _read_observation(raw, "all", ())
        for raw in (b"x", b"\xff" * 20, b"\0" * 20):
            with self.assertRaises(MakeProbeError):
                _read_events(raw, expected_mapping_count=0)

    def test_worktree_symlink_escape_and_invalid_limit_values_fail(self):
        self.add("Makefile", "all: ;\n")
        self.add("nested/owner", "data")
        (self.root / "nested/owner").unlink()
        (self.root / "nested").rmdir()
        (self.root / "nested").symlink_to(self.directory)
        with self.assertRaises(MakeProbeError):
            with self.session():
                self.fail("symlinked source must not be materialized")
        self.assertFalse(self.scratch.exists())
        for value in (0, -1, float("nan"), float("inf"), 3601, True):
            with self.assertRaises(MakeProbeError):
                Limits(seconds=value)

    def test_pattern_rules_and_target_specific_variable_values_are_native(self):
        self.add("Makefile", (
            "LOCAL = global\n"
            "%.out: LOCAL = pattern-$*\n"
            "%.out: %.src | order\n\t@printf '%s' '$(LOCAL)'\n"
            "foo.out: LOCAL = target-$*\n"
            "foo.src bar.src order: ;\n"
        ))
        with self.session() as session:
            for target, expected in (("foo.out", "target-foo"), ("bar.out", "pattern-bar")):
                result = session.make(target, variables=("LOCAL",))
                self.assertEqual(result.semantics["files"][0]["variables"]["LOCAL"]["value"], expected)
                self.assertEqual(result.semantics["domains"]["LOCAL"]["value"], "global")
                self.assertEqual(result.semantics["files"][0]["prerequisites"], [
                    {"name": target.replace(".out", ".src"), "order_only": False},
                    {"name": "order", "order_only": True},
                ])
        self.assert_clean(session)

    def test_event_mapping_and_pending_byte_bounds_cover_real_commands(self):
        value = "x" * 512
        source_command = "printf %s " + value
        self.add("Makefile", "VALUE := $(shell " + source_command + ")\nall: ;\n")
        commands = {source_command: Command(("/usr/bin/printf", "%s", value))}
        with self.session() as session:
            result = session.make("all", variables=("VALUE",), commands=commands)
            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], value)
        self.assert_clean(session)
        for limits in ({"event_bytes": 96}, {"mapping_bytes": 32}):
            with self.subTest(limits=limits):
                session = self.session(**limits)
                with self.assertRaises(MakeProbeError):
                    with session:
                        session.make("all", variables=("VALUE",), commands=commands)
                self.assert_clean(session)
        with self.session(pending_bytes=8192) as session:
            remaining = session.budget.limits.pending_bytes - session.budget.bytes.get("pending", 0)
            previous_runs = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "pending byte"):
                session.command(Command(("/usr/bin/printf", "%s", "x" * remaining)))
            self.assertEqual(previous_runs, session.budget.runs)
        self.assert_clean(session)

    def test_native_tools_compile_and_run_only_in_channel_free_capsules(self):
        self.add("native.c", (
            "#include <stdio.h>\n"
            "int main(void) { int ch; FILE *f=fopen(\"data/value\", \"rb\");"
            "if(!f) return 2; while((ch=fgetc(f))!=EOF) putchar(ch); return fclose(f); }\n"
        ))
        self.add("data/value", b"native\x00\xff")
        with self.session() as session:
            tool = session.compile_native(("native.c",))
            output = session.native(tool, sources=("data/value",))
            self.assertEqual(output.stdout, b"native\x00\xff")
            self.assertEqual(output.consumed, ("data/value",))
            self.assertTrue(tool.path.is_file())
            self.assertEqual(tool.path.read_bytes()[:4], b"\x7fELF")
            tool.path.chmod(0o700)
            tool.path.write_bytes(b"not the sealed ELF")
            with self.assertRaisesRegex(MakeProbeError, "sealed native tool changed"):
                session.native(tool, sources=("data/value",))
        self.assert_clean(session)

    def test_native_candidate_cannot_write_channels_or_read_inherited_fds(self):
        for operation in (
            'fopen("/control/events", "wb")',
            'fdopen(3, "w")',
        ):
            self.add("native.c", (
                "#define _POSIX_C_SOURCE 200809L\n#include <stdio.h>\n"
                f"int main(void) {{ FILE *f = {operation};"
                'if(f) { fputs("forged", f); fclose(f); } return 0; }\n'
            ))
            session = self.session()
            with self.assertRaises(MakeProbeError):
                with session:
                    tool = session.compile_native(("native.c",))
                    session.native(tool)
            self.assert_clean(session)

    def test_real_immutable_tree_consumer_reports_make_and_bundle_sources(self):
        from scripts.validation_ownership.consumer import check
        result = check(ROOT, "HEAD")
        self.assertEqual(result["scope"], "ownership-probe-foundation")
        self.assertEqual(result["make"]["target"], "localization-check")
        self.assertEqual(result["make"]["semantics"]["files"][0]["prerequisites"], [
            {"name": "localization-generate", "order_only": False},
        ])
        self.assertEqual(result["generated_registry"]["name"], "chapterbundle")
        self.assertEqual(result["generated_registry"]["source_paths"], ["src/data/ch2_bundle.json"])
        self.assertGreater(result["generated_registry"]["record_count"], 0)

    def test_cxx_native_compilation_and_worker_failure_are_confined(self):
        self.add("native.cpp", "#include <cstdio>\nint main() { std::puts(\"cxx\"); }\n")
        with self.session() as session:
            tool = session.compile_native(("native.cpp",), cxx=True)
            self.assertEqual(session.native(tool).stdout, b"cxx\n")
        self.assert_clean(session)
        self.add("reader.py", "import os\nos._exit(7)\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "unsuccessfully: 7"):
            with session:
                session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
        self.assert_clean(session)

    def test_fifo_input_and_malformed_native_result_do_not_leave_residue(self):
        self.add("Makefile", "all: ;\n")
        (self.root / "Makefile").unlink()
        os.mkfifo(self.root / "Makefile")
        started = time.monotonic()
        with self.assertRaises(MakeProbeError):
            with self.session():
                self.fail("FIFO input was admitted as a regular source")
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(self.scratch.exists())
        for data in (b"", b"\x7fELF", b"\0" * 128):
            with self.assertRaises(MakeProbeError):
                ProbeSession._validate_native(data)

    def test_direct_argument_boundaries_cannot_collide_and_quote_refactors_survive(self):
        registration = Command(("/usr/bin/printf", "%s", "a b"))
        commands = {
            "printf %s 'a b'": registration,
            'printf "%s" "a b"': registration,
        }
        values = []
        for expression in ("printf %s 'a b'", 'printf "%s" "a b"'):
            # Isolate argv semantics from the separate identity of recipe-owning
            # source bytes: this goal deliberately has no recipe.
            self.add("Makefile", "VALUE := $(shell " + expression + ")\n.PHONY: all\nall:\n")
            with self.session() as session:
                result = session.make("all", variables=("VALUE",), commands=commands)
                self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "a b")
                values.append(result.semantic_digest)
            self.assert_clean(session)
        self.assertEqual(values[0], values[1])
        self.add("Makefile", "VALUE := $(shell printf %s a b)\nall: ;\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "unregistered eager"):
            with session:
                session.make("all", variables=("VALUE",), commands=commands)
        self.assert_clean(session)

    def test_live_symlink_and_mode_state_bind_execution_not_unrelated_owner(self):
        self.add("Makefile", "all: ;\n")
        self.add("data/one", "one")
        self.add("data/two", "two")
        link = self.root / "data/link"
        link.symlink_to("one")
        self.entries["data/link"] = GitTreeEntry("data/link", "120000", "blob", "0" * 40)
        states = []
        for change in (None, "mode", "symlink"):
            if change == "mode":
                (self.root / "data/one").chmod(0o755)
            elif change == "symlink":
                link.unlink()
                link.symlink_to("two")
            with self.session() as session:
                result = session.make("all")
                states.append((result.execution_digest, result.semantic_digest))
            self.assert_clean(session)
        self.assertEqual(len({row[0] for row in states}), 3)
        self.assertEqual(len({row[1] for row in states}), 1)
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "symlink/gitlink"):
                session.sources(("data/*",))

    def test_memory_and_filesystem_observations_are_aggregate_bounded(self):
        self.add("reader.py", (
            "import os,time\n"
            "allocation = bytearray(16*1024*1024)\n"
            "for index in range(6):\n"
            " if os.fork()==0:\n"
            "  time.sleep(20)\n"
            "  os._exit(0)\n"
            "time.sleep(20)\n"
        ))
        session = self.session(address_space_bytes=96 * 1024 * 1024)
        with self.assertRaisesRegex(MakeProbeError, "aggregate address-space"):
            with session:
                session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
        self.assert_clean(session)
        self.add("Makefile", "all: ;\n")
        with self.session(entries=64) as session:
            session.make("all")
        self.assert_clean(session)
        self.add("Makefile", "$(foreach n," + " ".join(map(str, range(200))) + ",$(file <missing$(n)))\nall: ;\n")
        session = self.session(entries=64)
        with self.assertRaisesRegex(MakeProbeError, "filesystem-observation"):
            with session:
                session.make("all")
        self.assert_clean(session)

    def test_one_session_cannot_hide_parallel_workers(self):
        self.add("Makefile", "all: ;\n")
        errors = []
        with self.session() as session:
            runs = session.budget.runs
            def other_worker():
                try:
                    session.make("all")
                except MakeProbeError as error:
                    errors.append(error)
            worker = threading.Thread(target=other_worker)
            worker.start()
            worker.join()
            self.assertEqual(len(errors), 1)
            self.assertEqual(session.budget.runs, runs)
            self.assertTrue(session.budget.failed)
        self.assert_clean(session)


if __name__ == "__main__":
    unittest.main()
