from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import resource
import secrets
import selectors
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import sys
import threading
import time
import unittest
import venv
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.validation_ownership.authority import (
    AuthorityLoader, ENVIRONMENT, GitlinkSource, GitTreeEntries, GitTreeEntry, Snapshot, encoded, git_tree_entries,
    parse_json,
)
from scripts.validation_ownership.budget import Limits, MakeProbeError, NAMESPACE_LAUNCHER, ProbeBudget
from scripts.validation_ownership.make_probe import (
    Command, NativeTool, ProbeSession, TRUSTED_ROOT, _command_hash, _event_command, _make_interpreter, _make_runtime,
    _metadata_frame, _read_events, _read_observation, _trusted_runtime_bytes, probe_generated_registry,
)
from scripts.validation_ownership.metadata_transport import (
    HEX_DECODE_SCRATCH_BYTES,
    decode_metadata_transport,
    encode_metadata_transport,
)
from scripts.validation_ownership.python_commands import (
    directory_python_command,
    generated_registry_source_paths_command,
    generated_registry_source_paths,
    python_command,
)


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

    def session(self, *, runtime_files=(), **limits):
        budget = ProbeBudget(Limits(**limits))
        return ProbeSession(
            AuthorityLoader(self.root, GitTreeEntries(self.entries, budget=budget), budget=budget),
            scratch_root=self.scratch,
            budget=budget,
            runtime_files=runtime_files,
        )

    def assert_clean(self, session):
        self.assertFalse(session.cache)
        self.assertFalse(session.mappings)
        self.assertFalse(session.native_tools)
        self.assertFalse(session._views)
        self.assertFalse(session.make_runtime)
        self.assertFalse(session.runtime_inputs)
        self.assertFalse(session.runtime_dispatch)
        self.assertIsNone(session.runtime_root)
        self.assertFalse(session.budget.children)
        self.assertIsNone(session.snapshot)
        self.assertIsNone(session.base)
        self.assertEqual(session.pending_commands, 0)
        self.assertFalse(self.scratch.exists())

    def capture_supervisor_report(self, session, operation):
        original = session.budget.read_bytes
        captured = {}

        def read_bytes(path, category):
            data = original(path, category)
            if category == "control" and Path(path).name.startswith("report-"):
                captured["report"] = data
            return data

        with patch.object(session.budget, "read_bytes", side_effect=read_bytes), patch.object(
            session.budget, "charge", wraps=session.budget.charge,
        ) as charge:
            result = operation()
        self.assertIn("report", captured)
        return result, parse_json(captured["report"], "supervisor JSON"), captured["report"], charge.call_args_list

    def assert_metadata_transport(
        self, session, report, report_bytes, charges, metadata, *,
        runtime_paths=(), runtime_absent=(),
    ):
        frame = _metadata_frame(metadata)
        reserved = []
        decoded = decode_metadata_transport(
            report["metadata"], report["observations"], decoded_limit=len(frame),
            runtime_paths=runtime_paths, runtime_absent=runtime_absent,
            reserve=reserved.append,
        )
        self.assertEqual(decoded, metadata)
        payload_bytes = len(report["metadata"]["payload"])
        self.assertEqual(reserved, [len(frame), payload_bytes])
        self.assertEqual(report["metadata"]["record_count"], len(metadata))
        self.assertEqual(report["metadata"]["decoded_size"], len(frame))
        self.assertIn(("cache", len(encoded(metadata))), [call.args for call in charges])
        self.assertIn(("control", len(frame)), [call.args for call in charges])
        self.assertIn(("control", payload_bytes), [call.args for call in charges])
        legacy_report = {**report, "metadata": metadata}
        old_report_bytes = len(encoded(legacy_report))
        new_report_bytes = len(report_bytes)
        envelope_bytes = len(encoded(report["metadata"]))
        control_saving = old_report_bytes - new_report_bytes - len(frame) - payload_bytes
        self.assertGreater(control_saving, 0)
        return {
            "old_report_bytes": old_report_bytes,
            "new_report_bytes": new_report_bytes,
            "envelope_bytes": envelope_bytes,
            "frame_bytes": len(frame),
            "retained_payload_bytes": payload_bytes,
            "scratch_bytes": HEX_DECODE_SCRATCH_BYTES,
            "control_saving_bytes": control_saving,
        }

    def test_literal_source_selectors_are_repository_relative(self):
        name = "linker_script_banim.txt"
        nested = "scripts/modernize/tests/fixtures/repo/" + name
        self.add(name, "root")
        self.add(nested, "unrelated nested fixture")
        self.add("nested/missing.txt", "not the missing root input")
        self.add("reader.py", f"print(open({name!r}).read())\n")
        with self.session() as session:
            self.assertEqual(session.sources((name,)), (name,))
            self.assertEqual(session.sources(("**/" + name,)), (nested,))
            with self.assertRaisesRegex(MakeProbeError, "resolves no regular inputs"):
                session.sources(("missing.txt",))
            result = session.command(Command(
                ("/usr/bin/python3", "-I", "-S", "-B", "/repo/reader.py"),
                code=("reader.py",), sources=(name,),
            ))
            self.assertEqual(result.stdout, b"root\n")
            self.assertEqual(result.consumed, (name,))
        self.assert_clean(session)

    def test_literal_source_selector_does_not_match_nested_unadmitted_input(self):
        name = "linker_script_banim.txt"
        nested = "nested/" + name
        self.add(name, "root")
        self.add(nested, "../" + name, mode="120000")
        (self.root / nested).unlink()
        (self.root / nested).symlink_to("../" + name)
        with self.session() as session:
            self.assertEqual(session.sources((name,)), (name,))
            with self.assertRaisesRegex(MakeProbeError, "unadmitted symlink/gitlink"):
                session.sources((nested,))
            with self.assertRaisesRegex(MakeProbeError, "unadmitted symlink/gitlink"):
                session.sources(("**/" + name,))
        self.assert_clean(session)

    def capture_tree(self, budget):
        def git(*args):
            return subprocess.run(
                ["/usr/bin/git", "-C", str(self.root), *args],
                env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
            ).stdout
        git("init", "--quiet")
        git("add", "--", *sorted(self.entries))
        revision = git("write-tree").decode("ascii").strip()
        return git_tree_entries(self.root, revision, budget=budget), revision

    def test_authority_stages_require_an_explicit_report_budget(self):
        self.add("Makefile", "all: ;\n")
        entries, revision = self.capture_tree(ProbeBudget())
        loader = self.session().loader
        for stage, operation in (
            ("capture", lambda: git_tree_entries(self.root, revision)),
            ("loader", lambda: AuthorityLoader(self.root, entries, revision)),
            ("session", lambda: ProbeSession(loader, scratch_root=self.scratch)),
        ):
            with self.subTest(stage=stage):
                with self.assertRaises(TypeError):
                    operation()
        self.assertFalse(self.scratch.exists())

    def runtime_data_path(self):
        # os.py is a Python startup landmark; its incidental stat would add
        # incompatible live-inode metadata to the access-only reuse control.
        result = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", "-B", "-c", "import calendar; print(calendar.__file__)"],
            cwd="/", env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        )
        path = result.stdout.decode("utf-8").strip()
        self.assertEqual(result.stdout, (path + "\n").encode("utf-8"))
        self.assertTrue(Path(path).is_absolute())
        info = Path(path).lstat()
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(info.st_uid, 0)
        self.assertFalse(info.st_mode & 0o7022)
        self.assertGreater(info.st_size, 0)
        return path

    def test_runtime_inputs_capture_real_present_absent_and_ancestor_search(self):
        present = "/usr/include/stdio.h"
        absent = "/usr/include/ownership-probe-" + secrets.token_hex(12)
        self.assertTrue(Path(present).is_file())
        self.assertFalse(Path(absent).exists())
        self.add("Makefile", (
            f"PRESENT := $(wildcard {present})\nABSENT := $(wildcard {absent})\n"
            f"CHILD := $(wildcard {absent}/child.h)\n"
            "all:\n\t@printf '%s\\n' '$(PRESENT)' '$(ABSENT)' '$(CHILD)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        ).stdout.decode("ascii").splitlines()
        with self.session(runtime_files=(present, absent)) as session:
            captured = {item.path: item for item in session.runtime_inputs}
            self.assertEqual(captured[present].data, Path(present).read_bytes())
            self.assertEqual(captured[present].mode, stat.S_IMODE(Path(present).stat().st_mode))
            self.assertIsNone(captured[absent].data)
            self.assertIn(("/usr/include", True), captured[absent].parents)
            output = session.make("all", variables=("PRESENT", "ABSENT", "CHILD"))
            for name, value in zip(("PRESENT", "ABSENT", "CHILD"), ordinary):
                self.assertEqual(output.semantics["domains"][name]["value"], value)
            self.assertNotEqual(output.execution_digest, session.snapshot.digest)
            self.assertFalse((session.tree / "usr/include").exists())
        self.assert_clean(session)

    def test_runtime_inputs_native_newlib_discovery_and_include_search(self):
        present = "/usr/include/newlib/stdlib.h"
        names = ("build-" + self.directory.name, ".dep-" + self.directory.name)
        absent = tuple("/usr/include/" + name for name in names)
        for path in absent:
            self.assertFalse(Path(path).exists(), path)
        self.add("Makefile", (
            "ifeq ($(origin MODERN_NEWLIB_INCLUDE),undefined)\n"
            "  ifneq ($(wildcard /usr/include/newlib/stdlib.h),)\n"
            "    MODERN_NEWLIB_INCLUDE := /usr/include/newlib\n"
            "  else\n    MODERN_NEWLIB_INCLUDE :=\n  endif\nendif\n"
            f"-include {names[0]}/optional.d {names[1]}/optional.d\n"
            "all:\n\t@printf '%s\\n' '$(MODERN_NEWLIB_INCLUDE)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(ordinary.stdout.strip(), b"/usr/include/newlib" if Path(present).is_file() else b"")
        with self.session(runtime_files=(present, *absent)) as session:
            result = session.make("all", variables=("MODERN_NEWLIB_INCLUDE",))
            self.assertEqual(result.semantics["domains"]["MODERN_NEWLIB_INCLUDE"]["value"],
                             ordinary.stdout.decode().strip())
            self.assertEqual(result.semantics["domains"]["MODERN_NEWLIB_INCLUDE"]["origin"], "file")
            for name in names:
                self.assertFalse((session.tree / name / "optional.d").exists())
        self.assert_clean(session)

    def test_runtime_inputs_read_only_exact_bytes_and_capture_limits(self):
        from scripts.validation_ownership.make_probe import _capture_runtime_input
        path = self.runtime_data_path()
        expected = Path(path).read_bytes()
        self.assertGreater(len(expected), 0)
        self.add("Makefile", f"VALUE := $(file <{path})\n$(info $(VALUE))\nall: ;\n")
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        )
        with self.session() as session:
            with self.assertRaisesRegex(
                MakeProbeError, "uncaptured Make runtime access: read " + re.escape(path),
            ):
                session.make("all", variables=("VALUE",))
        self.assert_clean(session)
        with self.session(runtime_files=(path,)) as session:
            captured, = session.runtime_inputs
            self.assertEqual(captured.data, expected)
            result = session.make("all", variables=("VALUE",))
            self.assertEqual(result.stdout, ordinary.stdout)
        self.assert_clean(session)
        for limits, message in (
            ({"control_bytes": len(expected) - 1}, "aggregate control byte"),
            ({"file_bytes": len(expected) - 1}, "exceeds byte bound"),
        ):
            with self.subTest(limits=limits):
                budget = ProbeBudget(Limits(**limits))
                with self.assertRaisesRegex(MakeProbeError, message):
                    _capture_runtime_input(path, budget)
                self.assertTrue(budget.failed)
                self.assertEqual(budget.runs, 0)
                self.assertFalse(budget.children)
        with self.assertRaisesRegex(MakeProbeError, "count/duplicates"):
            self.session(runtime_files=(path, "/usr/include/stdio.h"), pending=1)
        self.assertFalse(self.scratch.exists())

    def test_runtime_inputs_keep_mandatory_closure_and_late_loader_guards(self):
        for expression, error in (
            ("$(wildcard /etc/ld.so.cache)", "uncaptured Make runtime access: metadata /etc/ld.so.cache"),
            ("$(file </etc/ld.so.preload)", "uncaptured Make runtime access: read /etc/ld.so.preload"),
            ("$(wildcard /usr/lib/x86_64-linux-gnu/glibc-hwcaps/x86-64-v3/libc.so.6)",
             "uncaptured Make runtime access: metadata /usr/lib/x86_64-linux-gnu/glibc-hwcaps/x86-64-v3/libc.so.6"),
        ):
            with self.subTest(expression=expression):
                self.add("Makefile", f"VALUE := {expression}\nall: ;\n")
                with self.session(runtime_files=("/usr/include/stdio.h", "/bin/env")) as session:
                    with self.assertRaisesRegex(MakeProbeError, re.escape(error)):
                        session.make("all")
                self.assert_clean(session)
        self.add("Makefile", "VALUE := $(wildcard /usr/include/stdio.h)\nall: ;\n")
        with self.session(runtime_files=("/usr/include/stdio.h",)) as session:
            self.assertGreaterEqual(len(session.make_runtime), 3)
            self.assertEqual(session.make("all", variables=("VALUE",)).semantics["domains"]["VALUE"]["value"],
                             "/usr/include/stdio.h")
        self.assert_clean(session)
        closure = _make_runtime(ProbeBudget())
        interpreter = _make_interpreter(dict(closure)["/usr/bin/make"])
        for image in ("/usr/bin/make", str(Path(interpreter).resolve())):
            with self.subTest(image=image):
                with self.assertRaisesRegex(MakeProbeError, "execution image"):
                    with self.session(runtime_files=("/usr/include/stdio.h", image)):
                        self.fail("optional input replaced mandatory closure")
                self.assertFalse(self.scratch.exists())

    def test_runtime_inputs_do_not_admit_unrequested_paths_writes_or_enumeration(self):
        missing = "/usr/include/ownership-unrequested-" + secrets.token_hex(12)
        self.assertFalse(Path(missing).exists())
        self.assertTrue(Path("/usr/include/stdlib.h").is_file())
        for expression, expected in (
            ("$(wildcard /usr/include/stdlib.h)", "uncaptured Make runtime access: metadata /usr/include/stdlib.h"),
            (f"$(wildcard {missing})", "uncaptured Make runtime access: metadata " + missing),
            ("$(file </usr/include/stdlib.h)", "uncaptured Make runtime access: read /usr/include/stdlib.h"),
            ("$(wildcard /usr/include/*)", "uncaptured Make runtime access: read /usr/include"),
            ("$(file >/usr/include/stdio.h,changed)", "write outside private command output"),
        ):
            with self.subTest(expression=expression):
                self.add("Makefile", f"VALUE := {expression}\nall: ;\n")
                session = self.session(runtime_files=("/usr/include/stdio.h",))
                with self.assertRaisesRegex(MakeProbeError, re.escape(expected)):
                    with session:
                        session.make("all")
                self.assert_clean(session)
        for paths in (
            ("/usr/include/stdio.h", "/usr/include/stdio.h"), ("/etc/passwd",),
            ("/usr/include/../include/stdio.h",), ("/usr/include/*",),
            ("/usr/include",), ("/usr/bin/make",), ("relative",),
            ("/usr/include/stdio.h", "/usr/include/stdio.h/child"),
        ):
            with self.subTest(paths=paths):
                with self.assertRaises((MakeProbeError, OSError)):
                    with self.session(runtime_files=paths):
                        self.fail("invalid runtime input accepted")
                self.assertFalse(self.scratch.exists())

    def test_runtime_inputs_share_capture_and_control_quota_across_calls(self):
        self.add("Makefile", "HEADER := $(wildcard /usr/include/stdio.h)\nall: ;\n")
        with self.session(runtime_files=("/usr/include/stdio.h",)) as session:
            runtime, backing = session.runtime_inputs, session.runtime_root
            first = session.make("all", variables=("HEADER",))
            charged, deadline = session.budget.bytes["control"], session.budget.deadline
            with patch(
                "scripts.validation_ownership.make_probe._capture_runtime_input",
                side_effect=AssertionError("runtime was captured again"),
            ):
                second = session.make("all", variables=("HEADER",))
            self.assertEqual(first.semantic_digest, second.semantic_digest)
            self.assertIs(session.runtime_inputs, runtime)
            self.assertIs(session.runtime_root, backing)
            self.assertGreater(session.budget.bytes["control"], charged)
            self.assertEqual(session.budget.deadline, deadline)
            remaining = session.budget.limits.control_bytes - session.budget.bytes["control"]
            session.budget.charge("control", remaining - 1)
            runs = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "control byte"):
                session.make("all")
            self.assertEqual(session.budget.runs, runs)
        self.assert_clean(session)

    def test_runtime_inputs_share_capture_and_control_quota_across_views(self):
        budget = ProbeBudget()
        runtime = self.runtime_data_path()
        absent = "/usr/include/ownership-view-" + self.directory.name
        self.assertFalse(Path(absent).exists())
        self.add("data/value", "base")
        self.add("reader.py", (
            "import json,os\n"
            "with open('data/value') as source:\n"
            " value=source.read(); status=os.fstat(source.fileno())\n"
            f"print(json.dumps([value,status.st_ino,os.access({runtime!r},os.R_OK)]))\n"
        ))
        self.add("sentinel.py", "open('env-executed','w').write('unexpected payload')\n")
        self.add("Makefile", (
            "VALUE := $(shell python3 reader.py)\n"
            f"RUNTIME := $(wildcard {runtime})\nABSENT := $(wildcard {absent}/child.h)\n"
            "MKDIR := $(realpath /bin/mkdir)\nENV := $(realpath /bin/env)\n"
            "all:\n\t@env /usr/bin/python3 -I -S -B sentinel.py\n"
        ))
        base = self.capture_view(budget)
        self.add("data/value", "current")
        current = self.capture_view(budget)
        command = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), sources=("data/value",),
        )
        with ProbeSession(
            current, scratch_root=self.scratch, budget=budget,
            runtime_files=(runtime, absent, "/bin/mkdir", "/bin/env"),
        ) as session:
            captures, backing, deadline = session.runtime_inputs, session.runtime_root, budget.deadline
            tree, snapshot = session.tree, session.snapshot
            original_runtime = (backing / runtime.lstrip("/")).stat()

            def observe(expected):
                result = session.make(
                    "all", variables=("VALUE", "RUNTIME", "ABSENT", "MKDIR", "ENV"),
                    commands={"python3 reader.py": command},
                )
                values = result.semantics["domains"]
                value, inode, accessible = json.loads(values["VALUE"]["value"])
                self.assertEqual((value, inode, accessible),
                                 (expected, (session.tree / "data/value").stat().st_ino, True))
                self.assertEqual(values["RUNTIME"]["value"], runtime)
                self.assertEqual(values["ABSENT"]["value"], "")
                self.assertEqual(values["MKDIR"]["value"], "/usr/bin/mkdir")
                self.assertEqual(values["ENV"]["value"], "/usr/bin/env")
                self.assertTrue(result.events)
                self.assertTrue(all(event["match"] >= 0 for event in result.events))
                self.assertFalse((session.tree / "env-executed").exists())
                self.assertIs(session.runtime_inputs, captures)
                self.assertIs(session.runtime_root, backing)
                self.assertEqual((backing / runtime.lstrip("/")).stat(), original_runtime)
                self.assertEqual(budget.deadline, deadline)
                return (budget.runs, session.processes_used, session.syscalls_used,
                        session.observations_used, budget.bytes["control"])

            first = observe("current")
            with session.select_view(base):
                selected = session.tree
                self.assertIsNot(session.snapshot, snapshot)
                self.assertIn("reader.py", session.snapshot.reused_paths)
                second = observe("base")
            self.assertIs(session.tree, tree)
            self.assertIs(session.snapshot, snapshot)
            self.assertFalse(selected.exists())
            third = observe("current")
            self.assertTrue(all(a < b < c for a, b, c in zip(first, second, third)))
            remaining = budget.limits.control_bytes - budget.bytes["control"]
            budget.charge("control", remaining - 1)
            runs = budget.runs
            with self.assertRaisesRegex(MakeProbeError, "control byte"):
                session.make("all")
            self.assertEqual(budget.runs, runs)
        self.assert_clean(session)
        self.assertFalse(backing.exists())

    def test_runtime_inputs_metadata_uses_shared_guest_revalidation(self):
        present = "/usr/include/stdio.h"
        absent = "/usr/include/ownership-metadata-" + secrets.token_hex(12)
        library = self.runtime_data_path()
        self.assertTrue(Path(library).is_file())
        self.assertFalse(Path(absent).exists())
        self.add("data/value", "captured source")
        self.add("reader.py", (
            "import json,os\n"
            f"print(json.dumps([os.access({library!r},os.R_OK),os.stat('data/value').st_ino]))\n"
        ))
        self.add("Makefile", (
            f"PRESENT := $(wildcard {present})\nABSENT := $(wildcard {absent})\n"
            "CANON := $(realpath /bin/mkdir)\nVALUE := $(shell python3 reader.py)\nall: ;\n"
        ))
        command = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), sources=("data/value",),
        )
        with self.session(runtime_files=(present, absent, library, "/bin/mkdir")) as session:
            first = session.command(command)
            self.assertTrue(json.loads(first.stdout)[0])
            self.assertTrue(any(record[1] == library and record[6] == 0 for record in first.metadata))
            self.assertIs(session.command(command), first)
            reports, run = [], session._sandbox_run
            def record(root, **kwargs):
                completed, observed = run(root, **kwargs)
                if kwargs["mode"] == "make":
                    reports.append(observed)
                return completed, observed
            with patch.object(session, "_sandbox_run", record):
                result = session.make(
                    "all", variables=("PRESENT", "ABSENT", "CANON", "VALUE"),
                    commands={"python3 reader.py": command},
                )
            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], first.stdout.decode().strip())
            self.assertTrue(all(event["match"] >= 0 for event in result.events))
            metadata = reports[-1]["metadata"]
            self.assertTrue({present, absent, "/bin", "/usr/bin/mkdir"} <= {row[1] for row in metadata})
            header = next(row for row in metadata if row[1] == present and row[0] in {4, 262})
            missing = next(row for row in metadata if row[1] == absent and row[0] in {4, 262})
            self.assertEqual((header[4], header[6], len(bytes.fromhex(header[8]))), (144, 0, 144))
            self.assertEqual(missing[6], -errno.ENOENT)
            owned = session.runtime_root / present.lstrip("/")
            self.assertEqual(struct.unpack_from("<Q", bytes.fromhex(header[8]), 8)[0], owned.stat().st_ino)
            before = owned.stat()
            combined = (*first.metadata, *metadata)
            self.assertTrue(session._metadata_matches(combined))
            self.assertEqual(owned.stat(), before)
            os.utime(owned, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
            changed = owned.stat()
            self.assertFalse(session._metadata_matches(combined))
            self.assertEqual(owned.stat(), changed)
            with patch.object(session, "_sandbox_run", record):
                session.make("all", variables=("PRESENT",), commands={"python3 reader.py": command})
            self.assertNotEqual(reports[-1]["metadata"], metadata)
            self.assertTrue(session._metadata_matches(reports[-1]["metadata"]))
        self.assert_clean(session)

    def test_runtime_inputs_capture_full_optional_buffers_status_flags_and_masks(self):
        path = self.runtime_data_path()
        self.assertTrue(Path(path).is_file())
        self.add("reader.py", (
            "import ctypes,json,os\n"
            "libc=ctypes.CDLL(None,use_errno=True); libc.syscall.restype=ctypes.c_long\n"
            f"fd=os.open({path!r},os.O_RDONLY); path=ctypes.c_char_p({os.fsencode(path)!r}); results=[]\n"
            "for number,flags,mask,size in "
            "((4,0,0,144),(6,0,0,144),(5,0,0,144),(262,256,0,144),"
            "(332,256,2047,256),(332,0,8191,256),(138,0,0,120),"
            "(21,4,0,0),(269,0,4,0),(439,512,2,0),(89,0,0,32),(267,0,0,32)):\n"
            " buffer=ctypes.create_string_buffer(bytes([165])*size,size) if size else None\n"
            " target=ctypes.byref(buffer) if size else None\n"
            " if number in (4,6): args=(path,target)\n"
            " elif number in (5,138): args=(ctypes.c_long(fd),target)\n"
            " elif number==262: args=(ctypes.c_long(-100),path,target,ctypes.c_ulong(flags))\n"
            " elif number==332: args=(ctypes.c_long(-100),path,ctypes.c_ulong(flags),ctypes.c_ulong(mask),target)\n"
            " elif number==21: args=(path,ctypes.c_ulong(flags))\n"
            " elif number==269: args=(ctypes.c_long(-100),path,ctypes.c_ulong(mask))\n"
            " elif number==439: args=(ctypes.c_long(-100),path,ctypes.c_ulong(mask),ctypes.c_ulong(flags))\n"
            " elif number==89: args=(path,target,ctypes.c_ulong(size))\n"
            " else: args=(ctypes.c_long(-100),path,target,ctypes.c_ulong(size))\n"
            " ctypes.set_errno(0); result=libc.syscall(ctypes.c_long(number),*args)\n"
            " results.append([number,flags,mask,result if result>=0 else -ctypes.get_errno(),"
            " '' if buffer is None else buffer.raw.hex()])\n"
            "os.close(fd); print(json.dumps(results))\n"
        ))
        command = Command(("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",))
        with self.session(runtime_files=(path,)) as session:
            output = session.command(command)
            returned = json.loads(output.stdout)
            records = tuple(row for row in output.metadata if row[1] == path)
            for number, flags, mask, status, data in returned:
                with self.subTest(number=number, flags=flags, mask=mask):
                    self.assertTrue(any(
                        row[:4] == (number, path, flags, mask) and row[6] == status and row[8] == data
                        for row in records
                    ))
            self.assertEqual([row[3] for row in returned[:9]], [0]*9)
            self.assertIn(returned[9][3], (-errno.EACCES, -errno.EROFS))
            self.assertEqual([row[3] for row in returned[10:]], [-errno.EINVAL]*2)
            compatible = tuple(row for row in records if row[0] in {21, 269, 89, 267})
            self.assertTrue(session._metadata_matches(compatible))
            access = next(row for row in records if row[0] == 21)
            wrong_status = (*access[:6], -errno.ENOENT, *access[7:])
            write_access = (*access[:2], os.W_OK, *access[3:])
            effective = next(row for row in records if row[0] == 269)
            write_mask = (*effective[:3], os.W_OK, *effective[4:])
            for mutation in (wrong_status, write_access, write_mask):
                with self.subTest(mutation=mutation[:7]):
                    self.assertFalse(session._metadata_matches((mutation,)))
            # Existing command runtime rights still see their own real runtime,
            # not a forged stat result for Make's captured inode.
            self.assertNotEqual(
                struct.unpack_from("<Q", bytes.fromhex(returned[0][4]), 8)[0],
                (session.runtime_root / path.lstrip("/")).stat().st_ino,
            )
            self.assertFalse(session._metadata_matches(records))
            self.assertIsNot(session.command(command), output)
        self.assert_clean(session)

    def test_runtime_inputs_reject_nonregular_and_replaced_capture(self):
        from scripts.validation_ownership.make_probe import _capture_runtime_input
        descriptors = set(os.listdir("/proc/self/fd"))
        owned = self.directory / "runtime-input"
        owned.write_bytes(b"before")
        owned.chmod(0o644)
        # Keep the host-root trust check separate; mutate only this owned inode
        # while exercising the real bounded capture/read and replacement checks.
        def trusted(path, **kwargs):
            self.assertEqual(path, str(owned))
            return owned
        with patch("scripts.validation_ownership.make_probe._trusted_runtime_path", trusted):
            ordinary = _capture_runtime_input(str(owned), ProbeBudget())
            self.assertEqual((ordinary.data, ordinary.mode), (b"before", 0o644))
            inode = owned.stat().st_ino
            for special in (0o4000, 0o2000, 0o1000, 0o6000, 0o5000, 0o3000, 0o7000):
                with self.subTest(special=oct(special)):
                    budget = ProbeBudget()
                    with patch.object(budget, "read_bytes", wraps=budget.read_bytes) as read:
                        try:
                            owned.chmod(0o644 | special)
                            status = owned.lstat()
                            self.assertTrue(stat.S_ISREG(status.st_mode))
                            self.assertEqual(stat.S_IMODE(status.st_mode), 0o644 | special)
                            self.assertEqual(status.st_ino, inode)
                            with self.assertRaisesRegex(MakeProbeError, "ordinary regular file"):
                                _capture_runtime_input(str(owned), budget)
                            read.assert_not_called()
                            self.assertEqual(budget.bytes, {})
                        finally:
                            owned.chmod(0o644)
                        restored = _capture_runtime_input(str(owned), budget)
                        self.assertEqual((restored.data, restored.mode), (b"before", 0o644))
                        self.assertEqual(read.call_count, 1)
                        self.assertFalse(budget.children)
            for kind in ("directory", "symlink", "fifo"):
                owned.unlink()
                if kind == "directory":
                    owned.mkdir()
                elif kind == "symlink":
                    owned.symlink_to("absent")
                else:
                    os.mkfifo(owned)
                with self.subTest(kind=kind):
                    with self.assertRaisesRegex(MakeProbeError, "ordinary regular file"):
                        _capture_runtime_input(str(owned), ProbeBudget())
                if kind == "directory":
                    owned.rmdir()
                else:
                    owned.unlink()
                owned.write_bytes(b"before")
            budget = ProbeBudget()
            read = budget.read_bytes
            replacement = self.directory / "replacement"
            replacement.write_bytes(b"after!")
            def replaced(path, category):
                data = read(path, category)
                replacement.replace(owned)
                return data
            with patch.object(budget, "read_bytes", replaced):
                with self.assertRaisesRegex(MakeProbeError, "changed during capture"):
                    _capture_runtime_input(str(owned), budget)
        self.assertEqual(set(os.listdir("/proc/self/fd")), descriptors)

    def test_runtime_inputs_optional_image_mapping_is_read_only_at_make_entry(self):
        import mmap
        from scripts.validation_ownership.make_probe import ALIASES
        from scripts.validation_ownership.syscall_guard import Violation

        self.add("Makefile", "VALUE := mandatory Make loaded\nall: ;\n")
        descriptors = set(os.listdir("/proc/self/fd"))
        page = os.sysconf("SC_PAGE_SIZE")
        with self.session(runtime_files=("/usr/bin/cat",)) as session:
            captured, = session.runtime_inputs
            self.assertNotIn(captured.canonical, ALIASES)
            self.assertEqual(session.runtime_dispatch, ())
            self.assertEqual(captured.data[:4], b"\x7fELF")
            self.assertTrue(captured.mode & 0o111)
            self.assertGreaterEqual(len(captured.data), page)
            image = session.runtime_root / captured.canonical.lstrip("/")
            descriptor = os.open(image, os.O_RDONLY)
            try:
                self.assertEqual(os.fstat(descriptor).st_ino, image.stat().st_ino)
                for protection in (mmap.PROT_READ, mmap.PROT_READ | mmap.PROT_EXEC):
                    with self.subTest(kernel_protection=protection):
                        with mmap.mmap(descriptor, page, flags=mmap.MAP_PRIVATE, prot=protection) as mapped:
                            self.assertEqual(mapped[:32], captured.data[:32])
                transitions = []
                observed = self.traced_observation(
                    9, (0, page, mmap.PROT_READ, mmap.MAP_PRIVATE, descriptor, 0),
                    {descriptor: captured.canonical}, mode="make", root=session.runtime_root,
                    runtime_files=(captured.canonical,), observer_ready=True,
                    transitions=transitions, mapping_bytes=32, fresh_exec=True,
                )
                self.assertGreater(observed["result"], 0)
                self.assertEqual(observed["data"], captured.data[:32])
                self.assertEqual(observed["accessed"], {captured.canonical})
                self.assertEqual(transitions, ["entry", "resume", "exit"])
                self.assertGreater(observed["memory_limit"], 0)
                self.assertEqual(observed["kernel_memory_limit"], observed["memory_limit"])
                transitions = []
                # A stopped raw syscall models Make after observer readiness;
                # no instructions are injected into Make or the mapped image.
                with self.assertRaisesRegex(Violation, "^optional runtime image execution denied$"):
                    self.traced_observation(
                        9, (0, page, mmap.PROT_READ | mmap.PROT_EXEC, mmap.MAP_PRIVATE, descriptor, 0),
                        {descriptor: captured.canonical}, mode="make", root=session.runtime_root,
                        runtime_files=(captured.canonical,), observer_ready=True,
                        transitions=transitions, fresh_exec=True,
                    )
                self.assertEqual(transitions, ["entry"])
            finally:
                os.close(descriptor)
            interpreter = _make_interpreter(dict(session.make_runtime)["/usr/bin/make"])
            library = session.runtime_root / interpreter.lstrip("/")
            descriptor = os.open(library, os.O_RDONLY)
            try:
                transitions = []
                observed = self.traced_observation(
                    9, (0, page, mmap.PROT_READ | mmap.PROT_EXEC, mmap.MAP_PRIVATE, descriptor, 0),
                    {descriptor: interpreter}, root=session.runtime_root,
                    transitions=transitions, mapping_bytes=32, fresh_exec=True,
                )
                self.assertGreater(observed["result"], 0)
                self.assertEqual(observed["data"], dict(session.make_runtime)[interpreter][:32])
                self.assertEqual(transitions, ["entry", "resume", "exit"])
                self.assertGreater(observed["memory_limit"], 0)
                self.assertEqual(observed["kernel_memory_limit"], observed["memory_limit"])
            finally:
                os.close(descriptor)
            result = session.make("all", variables=("VALUE",))
            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "mandatory Make loaded")
        self.assert_clean(session)
        self.assertEqual(set(os.listdir("/proc/self/fd")), descriptors)

    def test_runtime_inputs_mapping_uses_fresh_vm_after_parent_growth(self):
        import mmap
        from scripts.validation_ownership.syscall_guard import Policy

        size = 256 * 1024 * 1024
        self.mapping_vm_measurements = []
        self.mapping_parent_before = Policy.virtual_memory(os.getpid())
        stopped = self.stopped_tracee

        @contextmanager
        def measured(setup):
            with stopped(setup) as pid:
                self.mapping_vm_measurements.append({
                    "parent_vm": Policy.virtual_memory(os.getpid()),
                    "tracee_vm": Policy.virtual_memory(pid),
                    "tracee_as_limit": resource.prlimit(pid, resource.RLIMIT_AS),
                })
                yield pid

        with mmap.mmap(-1, size, flags=mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS, prot=0) as reservation:
            self.mapping_parent_inflated = Policy.virtual_memory(os.getpid())
            self.assertGreaterEqual(self.mapping_parent_inflated, self.mapping_parent_before + size)
            with patch.object(self, "stopped_tracee", measured):
                self.test_runtime_inputs_optional_image_mapping_is_read_only_at_make_entry()
            self.assertEqual(len(self.mapping_vm_measurements), 3)
            for row in self.mapping_vm_measurements:
                self.assertGreater(row["parent_vm"], size)
                self.assertGreater(row["tracee_vm"], 0)
                self.assertLess(row["tracee_vm"], size)
                self.assertLess(row["tracee_vm"], row["parent_vm"])
                self.assertEqual(row["tracee_as_limit"], (size, size))
        self.assertTrue(reservation.closed)
        self.mapping_parent_after = Policy.virtual_memory(os.getpid())

    def test_runtime_inputs_owned_fixtures_ignore_unowned_host_shapes(self):
        for occupied in ("/usr/include/build", "/usr/include/.dep"):
            exists = Path.exists
            def occupied_name(path):
                return True if str(path) == occupied else exists(path)
            with self.subTest(occupied=occupied), patch.object(Path, "exists", occupied_name):
                self.test_runtime_inputs_native_newlib_discovery_and_include_search()
        for shape in ("regular-python", "absent-multiarch"):
            lstat, exists = Path.lstat, Path.exists
            python_status = Path("/usr/bin/python3").stat()
            def shaped_lstat(path):
                if shape == "regular-python" and str(path) == "/usr/bin/python3":
                    return python_status
                return lstat(path)
            def shaped_exists(path):
                if shape == "absent-multiarch" and str(path) == "/usr/include/x86_64-linux-gnu":
                    return False
                return exists(path)
            with self.subTest(shape=shape), patch.object(Path, "lstat", shaped_lstat), patch.object(
                Path, "exists", shaped_exists,
            ):
                self.test_runtime_inputs_reject_nonregular_and_replaced_capture()

    def runtime_spelling_fixture(self):
        data = Path(self.runtime_data_path())
        result = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", "-B", "-c", "import encodings; print(encodings.__file__)"],
            cwd="/", env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        )
        child = Path(result.stdout.decode("utf-8").strip())
        self.assertTrue(child.is_file())
        self.assertEqual(child.parent.parent, data.parent)
        missing = data.parent / ("ownership-spelling-" + self.directory.name)
        self.assertFalse(missing.exists())
        return data, child, missing

    def test_runtime_inputs_make_parent_spellings_require_optional_authority(self):
        data, child, missing = self.runtime_spelling_fixture()
        parent_data = str(child.parent) + "/../" + data.name
        parent_missing = str(child.parent) + "/../" + missing.name
        stock_missing = "/bin/ownership-spelling-" + self.directory.name
        self.assertFalse(Path(stock_missing).exists())
        requested = (str(data), str(child), str(missing), "/bin/env", "/bin/mkdir", stock_missing)
        for target in (str(data), parent_data, str(missing) + "/child.h", parent_missing + "/child.h"):
            self.add("Makefile", f"VALUE := $(wildcard {target})\nall:\n\t@printf '%s\\n' '$(VALUE)'\n")
            ordinary = subprocess.run(
                ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
                env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
            )
            self.assertEqual(ordinary.stdout.decode().strip(), target if target.endswith(data.name) else "")
        cases = (
            ("present-stat", f"$(wildcard {parent_data})"),
            ("present-read", f"$(file <{parent_data})"),
            ("absent-stat", f"$(wildcard {parent_missing})"),
            ("absent-child-stat", f"$(wildcard {parent_missing}/child.h)"),
            ("absent-read", f"$(file <{parent_missing})"),
            ("absent-child-read", f"$(file <{parent_missing}/child.h)"),
            ("multiple-parent", "$(wildcard " + str(child.parent) + "/../../" + data.parent.name + "/" + data.name + ")"),
            ("env-stat", "$(wildcard /usr/bin/../bin/env)"),
            ("mkdir-stat", "$(wildcard /usr/bin/../bin/mkdir)"),
            ("stock-env-stat", "$(wildcard /bin/../bin/env)"),
            ("canonical-stock-absent", f"$(wildcard /usr/bin/../bin/{Path(stock_missing).name}/child.h)"),
            ("stock-absent-stat", f"$(wildcard /bin/../bin/{Path(stock_missing).name}/child.h)"),
        )
        for name, expression in cases:
            with self.subTest(name=name):
                self.add("Makefile", f"VALUE := {expression}\nall: ;\n")
                with self.session(runtime_files=requested) as session:
                    self.assertTrue((session.runtime_root / str(child.parent).lstrip("/")).is_dir())
                    error = "unrequested stock runtime alias" if name.startswith("stock-") else "optional Make runtime parent spelling"
                    with self.assertRaisesRegex(MakeProbeError, error):
                        session.make("all")
                self.assert_clean(session)
        for program in ("/usr/bin/../bin/env", "/usr/bin/../bin/mkdir"):
            with self.subTest(dispatch=program):
                self.add("Makefile", f"all:\n\t@{program} /usr/bin/true\n")
                with self.session(runtime_files=requested) as session:
                    with self.assertRaisesRegex(MakeProbeError, "optional Make runtime parent spelling"):
                        session.make("all")
                self.assert_clean(session)
        self.add("Makefile", (
            f"PRESENT := $(wildcard {data})\nABSENT := $(wildcard {missing}/child.h)\n"
            f"CONTENT := $(file <{data})\nall: ;\n"
        ))
        with self.session(runtime_files=requested) as session:
            reports, run = [], session._sandbox_run
            def record(root, **kwargs):
                result, observed = run(root, **kwargs)
                if kwargs["mode"] == "make":
                    reports.append(observed["metadata"])
                return result, observed
            with patch.object(session, "_sandbox_run", record):
                output = session.make("all", variables=("PRESENT", "ABSENT", "CONTENT"))
            self.assertEqual(output.semantics["domains"]["PRESENT"]["value"], str(data))
            self.assertEqual(output.semantics["domains"]["ABSENT"]["value"], "")
            self.assertEqual(output.semantics["domains"]["CONTENT"]["value"], data.read_text().removesuffix("\n"))
            self.assertTrue(session._metadata_matches(reports[-1]))
        self.assert_clean(session)
        self.add("Makefile", f"VALUE := $(wildcard {parent_data})\nall: ;\n")
        with self.session(runtime_files=(str(child),)) as session:
            self.assertTrue((session.runtime_root / str(child.parent).lstrip("/")).is_dir())
            with self.assertRaisesRegex(MakeProbeError, "uncaptured Make runtime access: metadata"):
                session.make("all")
        self.assert_clean(session)

    def test_runtime_inputs_parent_scope_preserves_command_source_and_mandatory_grants(self):
        data = Path(self.runtime_data_path())
        self.add("data/intermediate/keep", "actual intermediate")
        self.add("data/value", "source parent remains authorized")
        self.add("reader.py", (
            "import json,os\n"
            f"directory=os.open({str(data.parent)!r},os.O_RDONLY|os.O_DIRECTORY)\n"
            f"name={'../' + data.parent.name + '/' + data.name!r}\n"
            "descriptor=os.open(name,os.O_RDONLY,dir_fd=directory)\n"
            "value=os.read(descriptor,32).hex(); status=os.stat(name,dir_fd=directory)\n"
            "os.close(descriptor); os.close(directory)\n"
            "print(json.dumps([value,status.st_ino,open('data/intermediate/../value').read()]))\n"
        ))
        self.add("Makefile", (
            "SOURCE := $(file <data/intermediate/../value)\n"
            "MAKE := $(wildcard /usr/bin/../bin/make)\n"
            "DIRECTORY := $(wildcard /usr/bin/../bin)\nall: ;\n"
        ))
        with self.session(runtime_files=(str(data), "/bin/env")) as session:
            output = session.command(Command(
                ("/usr/bin/python3", "/repo/reader.py"),
                code=("reader.py", "data/intermediate/keep"), sources=("data/value",),
            ))
            value, inode, source = json.loads(output.stdout)
            self.assertEqual(value, data.read_bytes()[:32].hex())
            self.assertEqual(inode, data.stat().st_ino)
            self.assertEqual(source, "source parent remains authorized")
            self.assertTrue(any(row[1] == str(data) and row[6] == 0 for row in output.metadata))
            result = session.make("all", variables=("SOURCE", "MAKE", "DIRECTORY"))
            self.assertEqual({name: row["value"] for name, row in result.semantics["domains"].items()}, {
                "SOURCE": source, "MAKE": "/usr/bin/../bin/make", "DIRECTORY": "/usr/bin/../bin",
            })
        self.assert_clean(session)

    def test_runtime_inputs_make_spelling_keeps_raw_dirfd_and_resets_per_syscall(self):
        from scripts.validation_ownership.syscall_guard import Policy, Process, Registers, Violation
        directory = self.directory / "usr/include/intermediate"
        directory.mkdir(parents=True)
        (directory.parent / "data").write_bytes(b"actual data")
        policy = Policy({
            "root": str(self.directory), "mode": "make", "code": [], "sources": [], "enumerations": [],
            "executables": ["/usr/bin/make"], "python_version": "3.12", "argv": [], "forbidden_paths": [],
            "runtime_files": ["/usr/include/data"], "runtime_parents": ["/usr", "/usr/include"],
            "observation_limit": 65536, "observation_count": 128, "syscall_limit": 100, "write_limit": 65536,
        })
        state = Process("make", observer_ready=True, fds={7: "/usr/include/intermediate", 8: "/usr/include/data"})
        with patch("scripts.validation_ownership.syscall_guard.cstring", return_value="../data"):
            path = policy.path(1, state, 1, 7)
        self.assertEqual(path, "/usr/include/data")
        self.assertEqual(state.path_context, ("../data", 7, "/usr/include/intermediate"))
        with self.assertRaisesRegex(Violation, "dirfd=7"):
            policy.check(state, path, "metadata")
        registers = Registers()
        registers.orig_rax, registers.rdi = 0, 8
        policy.entry(1, state, registers)
        self.assertIsNone(state.path_context)

    def test_runtime_inputs_interrupted_setup_and_metadata_remove_owned_state(self):
        self.add("Makefile", "all: ;\n")
        self.add("reader.py", "import os\nprint(os.stat('Makefile').st_ino)\n")
        for stage in ("runtime-setup", "metadata"):
            with self.subTest(stage=stage):
                session = self.session(runtime_files=("/usr/include/stdio.h", "/bin/env"))
                build_root = session._new_root
                descriptors = set(os.listdir("/proc/self/fd"))
                def interrupted_root(name, **kwargs):
                    root = build_root(name, **kwargs)
                    if name == "runtime" and stage == "runtime-setup":
                        raise KeyboardInterrupt("runtime setup interrupted")
                    return root
                with self.assertRaisesRegex(KeyboardInterrupt, "runtime .* interrupted"):
                    with patch.object(session, "_new_root", interrupted_root):
                        with session:
                            command = Command(
                                ("/usr/bin/python3", "/repo/reader.py"),
                                code=("reader.py",), sources=("Makefile",),
                            )
                            session.command(command)
                            run = session._sandbox_run
                            def interrupted_validation(root, **kwargs):
                                if kwargs.get("metadata_validation"):
                                    raise KeyboardInterrupt("runtime metadata interrupted")
                                return run(root, **kwargs)
                            with patch.object(session, "_sandbox_run", interrupted_validation):
                                session.command(command)
                self.assert_clean(session)
                self.assertEqual(set(os.listdir("/proc/self/fd")), descriptors)

    def test_runtime_inputs_reject_canonical_duplicates_and_overlaps_in_both_orders(self):
        original, canonical = "/bin/cat", "/usr/bin/cat"
        absent = "/bin/ownership-duplicate-" + secrets.token_hex(12)
        canonical_absent = "/usr/bin/" + Path(absent).name
        self.assertEqual(Path("/bin").resolve(), Path("/usr/bin"))
        self.assertTrue(stat.S_ISREG(Path(canonical).lstat().st_mode))
        self.assertEqual(Path(canonical).stat().st_uid, 0)
        self.assertFalse(Path(absent).exists())
        self.assertFalse(Path(canonical_absent).exists())
        self.add("Makefile", f"VALUE := $(wildcard {canonical})\nall: ;\n")
        for path in (original, canonical):
            with self.subTest(single=path):
                with self.session(runtime_files=(path,)) as session:
                    self.assertEqual(len(session.runtime_inputs), 1)
                    self.assertEqual(session.runtime_inputs[0].data, Path(canonical).read_bytes())
                    observed = session.make("all", variables=("VALUE",))
                    self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], canonical)
                self.assert_clean(session)
        for pair in (
            (original, canonical),
            (absent, canonical_absent),
            (absent, canonical_absent + "/child.h"),
            (canonical_absent, absent + "/child.h"),
        ):
            for requested in (pair, pair[::-1]):
                with self.subTest(requested=requested):
                    session = self.session(runtime_files=requested)
                    with self.assertRaisesRegex(MakeProbeError, "^duplicate/overlapping optional runtime inputs$"):
                        with session:
                            self.fail("ordinary duplicate/overlap was admitted")
                    self.assertEqual(session.processes_used, 0)
                    self.assert_clean(session)
        other = canonical_absent + "-other"
        self.assertFalse(Path(other).exists())
        self.add("Makefile", f"VALUE := $(wildcard {canonical_absent}/child.h {other}/child.h)\nall: ;\n")
        for requested in ((absent, other), (other, absent)):
            with self.subTest(distinct_components=requested):
                with self.session(runtime_files=requested) as session:
                    self.assertEqual(len(session.runtime_inputs), 2)
                    observed = session.make("all", variables=("VALUE",))
                    self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "")
                self.assert_clean(session)

    def test_stock_runtime_alias_preserves_absent_descendant_observations(self):
        original = "/bin/ownership-absent-" + secrets.token_hex(12)
        canonical = "/usr/bin/" + Path(original).name
        for path in (original, canonical):
            self.assertFalse(Path(path).exists())
        queries = [prefix + suffix for prefix in (original, canonical)
                   for suffix in ("", "/child.h", "/nested/child.h")]
        self.add("Makefile", (
            "MISSING := $(wildcard " + " ".join(queries) + ")\n"
            f"READ := $(file <{original}/child.h)\nREAL := $(realpath {original}/nested/child.h)\n"
            "all:\n\t@printf '%s|%s|%s\\n' '$(MISSING)' '$(READ)' '$(REAL)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(ordinary.stdout, b"||\n")
        with self.session(runtime_files=(original,)) as session:
            runtime, backing, deadline = session.runtime_inputs, session.runtime_root, session.budget.deadline
            item, = runtime
            self.assertIsNone(item.data)
            self.assertEqual(item.canonical, canonical)
            self.assertEqual(item.aliases, (("/bin", "usr/bin"),))
            reports, run = [], session._sandbox_run
            def record(root, **kwargs):
                result, observed = run(root, **kwargs)
                if kwargs["mode"] == "make":
                    reports.append(observed["metadata"])
                return result, observed
            with patch.object(session, "_sandbox_run", record):
                first = session.make("all", variables=("MISSING", "READ", "REAL"))
                used = session.budget.bytes["control"], session.processes_used
                second = session.make("all", variables=("MISSING", "READ", "REAL"))
            self.assertEqual(
                [first.semantics["domains"][name]["value"] for name in ("MISSING", "READ", "REAL")],
                ordinary.stdout.decode().strip().split("|"),
            )
            self.assertEqual(first.semantic_digest, second.semantic_digest)
            self.assertEqual(first.events, ())
            for suffix in ("", "/child.h", "/nested/child.h"):
                self.assertTrue(any(
                    row[1] == canonical + suffix and row[0] in {4, 6, 262}
                    and row[4] == 144 and row[6] == -errno.ENOENT
                    and len(bytes.fromhex(row[8])) == 144
                    for row in reports[-1]
                ), suffix)
            self.assertTrue(session._metadata_matches(reports[-1]))
            self.assertIs(session.runtime_inputs, runtime)
            self.assertIs(session.runtime_root, backing)
            self.assertEqual(session.budget.deadline, deadline)
            self.assertGreater(session.budget.bytes["control"], used[0])
            self.assertGreater(session.processes_used, used[1])
            self.assertFalse((session.runtime_root / canonical.lstrip("/")).exists())
        self.assert_clean(session)

    def test_stock_runtime_alias_absence_keeps_component_and_operation_boundaries(self):
        original = "/bin/ownership-absence-boundary-" + secrets.token_hex(12)
        canonical = "/usr/bin/" + Path(original).name
        self.assertFalse(Path(original).exists())
        self.assertFalse(Path(canonical).exists())
        self.assertFalse(Path(original + "-other").exists())
        self.assertTrue(Path("/bin/rm").is_file())
        for expression, requested, error in (
            (f"$(wildcard {original}-other/child.h)", (original,), "unrequested stock runtime alias spelling"),
            (f"$(wildcard {canonical}-other/child.h)", (original,), "uncaptured Make runtime access: metadata"),
            (f"$(wildcard {original}/../child.h)", (original,), "unrequested stock runtime alias spelling"),
            (f"$(wildcard /bin/../bin/{Path(original).name}/child.h)",
             (original,), "unrequested stock runtime alias spelling"),
            ("$(wildcard /bin/rm)", (original,), "unrequested stock runtime alias spelling"),
            (f"$(file >{original}/child.h,changed)", (original,), "write outside private command output"),
            ("$(wildcard /bin/*)", (original,), "uncaptured Make runtime access: read /usr/bin"),
            (f"$(wildcard {original}/child.h)",
             (canonical, "/bin/mkdir"), "unrequested stock runtime alias spelling"),
            ("$(wildcard /bin/cat/child.h)", ("/bin/cat",), "unrequested stock runtime alias spelling"),
        ):
            with self.subTest(expression=expression, requested=requested):
                self.add("Makefile", f"VALUE := {expression}\nall: ;\n")
                with self.session(runtime_files=requested) as session:
                    with self.assertRaisesRegex(MakeProbeError, re.escape(error)):
                        session.make("all")
                self.assert_clean(session)
        self.add("Makefile", f"VALUE := $(wildcard {canonical}/child.h)\nall: ;\n")
        with self.session(runtime_files=(canonical, "/bin/mkdir")) as session:
            observed = session.make("all", variables=("VALUE",))
            self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "")
        self.assert_clean(session)

    def test_stock_runtime_alias_preserves_root_path_and_native_dispatch(self):
        self.assertEqual(Path("/bin").resolve(), Path("/usr/bin"))
        self.add("Makefile", (
            "TOOLCHAIN ?= $(DEVKITARM)\nexport PATH := $(TOOLCHAIN)/bin:$(PATH)\n"
            "CANON := $(realpath /bin/mkdir)\n$(info $(PATH))\n$(info $(CANON))\n"
            "all:\n\t@mkdir -p build/owned\n"
        ))
        normal = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        ).stdout.decode().splitlines()
        self.assertTrue((self.root / "build/owned").is_dir())
        (self.root / "build/owned").rmdir()
        with self.session(runtime_files=("/bin/mkdir",)) as session:
            item, = session.runtime_inputs
            self.assertEqual(item.canonical, "/usr/bin/mkdir")
            self.assertIn(("/bin", "usr/bin"), item.aliases)
            result = session.make("all", variables=("PATH", "CANON"))
            self.assertEqual(result.semantics["domains"]["PATH"]["value"], normal[0])
            self.assertEqual(result.semantics["domains"]["CANON"]["value"], normal[1])
            self.assertEqual(result.events, ())
            self.assertFalse((session.tree / "build/owned").exists())
            self.assertFalse((self.root / "build/owned").exists())
        self.assert_clean(session)

    def test_stock_runtime_alias_rejects_unrequested_escape_read_and_collision(self):
        for expression, message in (
            ("$(wildcard /bin/rm)", "unrequested stock runtime alias"),
            ("$(file </bin/mkdir)", "metadata/dispatch only"),
            ("$(file >/bin/mkdir,changed)", "write outside"),
            ("$(wildcard /bin/../bin/mkdir)", "unrequested stock runtime alias"),
        ):
            with self.subTest(expression=expression):
                self.add("Makefile", f"VALUE := {expression}\nall: ;\n")
                with self.assertRaisesRegex(MakeProbeError, message):
                    with self.session(runtime_files=("/bin/mkdir",)) as session:
                        session.make("all")
                self.assert_clean(session)
        self.add("Makefile", "all: ;\n")
        with self.assertRaisesRegex(MakeProbeError, "execution image"):
            with self.session(runtime_files=("/bin/make",)):
                self.fail("stock alias replaced trusted Make")
        from scripts.validation_ownership.make_probe import _capture_runtime_input
        original_stat, original_resolve = Path.lstat, Path.resolve
        def mutable(path):
            info = original_stat(path)
            return SimpleNamespace(st_uid=1000, st_mode=info.st_mode) if path == Path("/bin") else info
        with patch.object(Path, "lstat", mutable):
            with self.assertRaisesRegex(MakeProbeError, "mutable/untrusted"):
                _capture_runtime_input("/bin/mkdir", ProbeBudget())
        with patch.object(Path, "resolve", lambda path, **kw: self.root if path == Path("/bin") else original_resolve(path, **kw)):
            with self.assertRaisesRegex(MakeProbeError, "nonstock/escaping"):
                _capture_runtime_input("/bin/mkdir", ProbeBudget())

    def env_recipe_fixture(self, program="env"):
        cleared = (
            "MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "ASSET_MANIFEST", "ASSET_OUTPUT_DIR",
            "EXPANSION_CUSTOM_SPELL_EFFECTS", "FE8_ITEM_ID_CAP",
        )
        recipe = program + " " + " ".join("-u " + name for name in cleared)
        recipe += " $(PYTHON) -I -S -B sentinel.py"
        self.add("sentinel.py", (
            "import json,os\n"
            "with open('env-executed','w') as output:\n"
            " json.dump({name:os.environ.get(name) for name in " + repr(cleared) + "},output)\n"
        ))
        names = ("PATH", "CANON", "PYTHON", "SHELL", "MAKEFLAGS", "MFLAGS")
        metadata = "".join(
            "$(info $(" + form + name + "))\n"
            for name in names for form in ("", "origin ", "flavor ")
        )
        path = "/bin/env" if program == "env" else program
        self.add("Makefile", (
            "TOOLCHAIN ?= $(DEVKITARM)\nexport PATH := $(TOOLCHAIN)/bin:$(PATH)\n"
            "PYTHON := /usr/bin/python3\nexport ASSET_MANIFEST := observed-input\n"
            "CANON := $(realpath " + path + ")\n" + metadata + "all:\n\t@" + recipe + "\n"
        ))
        return recipe, names, cleared

    def test_explicit_env_recipe_preserves_native_context_without_executing_payload(self):
        for program, requested in (
            ("env", ("/bin/env",)),
            ("/bin/env", ("/bin/env",)),
            ("/usr/bin/env", ("/usr/bin/env",)),
            ("env", ("/bin/env", "/usr/bin/env")),
            ("env", ("/usr/bin/env", "/bin/env")),
        ):
            with self.subTest(program=program, requested=requested):
                recipe, names, cleared = self.env_recipe_fixture(program)
                ordinary = subprocess.run(
                    ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
                    env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
                )
                effect = self.root / "env-executed"
                self.assertEqual(json.loads(effect.read_text()), {name: None for name in cleared})
                effect.unlink()
                values = ordinary.stdout.decode().splitlines()
                self.assertEqual(len(values), 3*len(names))
                expected = {
                    name: dict(zip(("value", "origin", "flavor"), values[index*3:index*3+3]))
                    for index, name in enumerate(names)
                }
                with self.session(runtime_files=requested) as session:
                    image = (session.runtime_root / "usr/bin/env").read_bytes()
                    self.assertEqual(image, (session.base / "interceptor").read_bytes())
                    self.assertTrue(all(image != item.data for item in session.runtime_inputs))
                    observed = session.make("all", variables=names)
                    self.assertEqual(observed.semantics["domains"], expected)
                    self.assertEqual(observed.semantics["files"][0]["recipe"], "@" + recipe + "\n")
                    self.assertEqual(observed.semantics["files"][0]["source"], "Makefile")
                    self.assertEqual(observed.events, ())
                    self.assertEqual(observed.semantics["dynamic_commands"], [])
                    self.assertFalse(effect.exists())
                    self.assertFalse((session.tree / "env-executed").exists())
                self.assert_clean(session)

    def test_explicit_env_does_not_grant_eager_or_public_execution(self):
        recipe, _, _ = self.env_recipe_fixture()
        actual = recipe.replace("$(PYTHON)", "/usr/bin/python3")
        for makefile in (
            "VALUE := $(shell " + actual + ")\nall: ;\n",
            "all:\n\t+@" + actual + "\n",
            "include generated.mk\ngenerated.mk:\n\t@" + actual + "\nall: ;\n",
        ):
            with self.subTest(makefile=makefile):
                self.add("Makefile", makefile)
                with self.session(runtime_files=("/bin/env", "/usr/bin/env")) as session:
                    with self.assertRaisesRegex(MakeProbeError, "unregistered eager/recursive"):
                        session.make("all")
                    self.assertFalse((session.tree / "env-executed").exists())
                self.assert_clean(session)
        for program in ("/bin/env", "/usr/bin/env"):
            with self.subTest(program=program):
                with self.session(runtime_files=("/bin/env", "/usr/bin/env")) as session:
                    runs = session.budget.runs
                    with self.assertRaisesRegex(MakeProbeError, "supported exact trusted argv"):
                        session.command(Command((program, "/usr/bin/python3", "/repo/sentinel.py")))
                    self.assertEqual(session.budget.runs, runs)
                self.assert_clean(session)

    def test_explicit_env_keeps_spelling_read_image_and_other_program_boundaries(self):
        self.assertTrue(Path("/usr/bin/cat").is_file())
        for content, requested, error in (
            ("all:\n\t@/usr/bin/env /usr/bin/true\n", (), "uncaptured Make runtime access: metadata /usr/bin/env"),
            ("all:\n\t@/bin/env /usr/bin/true\n",
             ("/bin/mkdir", "/usr/bin/env"), "unrequested stock runtime alias"),
            ("VALUE := $(file </bin/env)\nall: ;\n", ("/bin/env",), "metadata/dispatch only"),
            ("VALUE := $(file </usr/bin/env)\nall: ;\n", ("/bin/env",), "metadata/dispatch only"),
            ("VALUE := $(wildcard /bin/../bin/env)\nall: ;\n", ("/bin/env",), "unrequested stock runtime alias"),
            ("VALUE := $(file >/usr/bin/env,changed)\nall: ;\n", ("/bin/env",), "write outside"),
            ("all:\n\t@/usr/bin/cat Makefile\n", ("/bin/env", "/usr/bin/cat"), "untrusted executable dispatch"),
        ):
            with self.subTest(content=content, requested=requested):
                self.add("Makefile", content)
                with self.assertRaisesRegex(MakeProbeError, error):
                    with self.session(runtime_files=requested) as session:
                        session.make("all")
                self.assert_clean(session)
        self.add("Makefile", "all: ;\n")
        self.assertTrue(stat.S_ISREG(Path("/usr/bin/bash").lstat().st_mode))
        for collision in ("/usr/bin/python3", "/bin/make", "/usr/bin/bash"):
            for requested in (("/bin/env", collision), (collision, "/bin/env")):
                with self.subTest(requested=requested):
                    with self.assertRaisesRegex(MakeProbeError, "execution image|ordinary regular file"):
                        with self.session(runtime_files=requested):
                            self.fail("env request replaced an existing trusted image")
                    self.assertFalse(self.scratch.exists())
        from scripts.validation_ownership.make_probe import _capture_runtime_input
        original_stat, original_resolve = Path.lstat, Path.resolve
        def mutable(path):
            info = original_stat(path)
            return SimpleNamespace(st_uid=1000, st_mode=info.st_mode) if path == Path("/bin") else info
        with patch.object(Path, "lstat", mutable):
            with self.assertRaisesRegex(MakeProbeError, "mutable/untrusted"):
                _capture_runtime_input("/bin/env", ProbeBudget())
        with patch.object(Path, "resolve", lambda path, **kw: self.root if path == Path("/bin") else original_resolve(path, **kw)):
            with self.assertRaisesRegex(MakeProbeError, "nonstock/escaping"):
                _capture_runtime_input("/bin/env", ProbeBudget())

    def test_absent_captured_env_does_not_materialize_an_interceptor(self):
        from scripts.validation_ownership.make_probe import _capture_runtime_input
        self.add("Makefile", "ENV := $(wildcard /usr/bin/env)\nall: ;\n")
        def absent(path, budget):
            return replace(_capture_runtime_input(path, budget), data=None, mode=None)
        with patch("scripts.validation_ownership.make_probe._capture_runtime_input", absent):
            for requested in (
                ("/usr/bin/env",), ("/bin/env",),
                ("/bin/env", "/usr/bin/env"), ("/usr/bin/env", "/bin/env"),
            ):
                with self.subTest(requested=requested):
                    with self.session(runtime_files=requested) as session:
                        self.assertEqual(session.runtime_dispatch, ())
                        observed = session.make("all", variables=("ENV",))
                        self.assertEqual(observed.semantics["domains"]["ENV"]["value"], "")
                        self.assertFalse((session.runtime_root / "usr/bin/env").exists())
                    self.assert_clean(session)

    def gitlink_git(self, root, *args, input=None):
        return subprocess.run(
            ["/usr/bin/git", "-C", str(root), *args], env=ENVIRONMENT,
            input=input, capture_output=True, check=True, timeout=10,
        ).stdout.decode("ascii").strip()

    def gitlink_fixture(self):
        module = self.directory / "module"
        (module / "src").mkdir(parents=True)
        (module / "include").mkdir()
        self.gitlink_git(module, "init", "--quiet")
        self.gitlink_git(module, "config", "user.name", "Owned Fixture")
        self.gitlink_git(module, "config", "user.email", "fixture@example.invalid")
        (module / "src/tool.c").write_text(
            '#include "../include/value.h"\n#include <stdio.h>\nint main(void) { puts(VALUE); }\n',
        )
        pins = []
        for value in ("base", "current"):
            (module / "include/value.h").write_text(f'#define VALUE "{value}"\n')
            self.gitlink_git(module, "add", "--all")
            self.gitlink_git(module, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", value)
            pins.append(self.gitlink_git(module, "rev-parse", "HEAD"))
        self.gitlink_git(self.root, "init", "--quiet")
        self.add("Makefile", "FILES := $(sort $(wildcard module/src/*.c))\nall: ;\n")
        self.gitlink_git(self.root, "add", "Makefile")
        return module, pins

    def gitlink_loader(self, budget, database, pin, *, path="module", admit=True):
        self.gitlink_git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{pin},{path}")
        revision = self.gitlink_git(self.root, "write-tree")
        entries = git_tree_entries(
            self.root, revision, budget=budget,
            gitlinks=(GitlinkSource(path, database),) if admit else (),
        )
        return AuthorityLoader(self.root, entries, revision, budget=budget)

    def test_gitlink_sources_use_captured_commit_not_checkout_or_branch(self):
        module, (base, current) = self.gitlink_fixture()
        budget = ProbeBudget()
        loader = self.gitlink_loader(budget, module / ".git", base)
        (module / "include/value.h").write_text("substituted live checkout")
        self.add("module/include/value.h", "substituted superproject directory")
        source = "module/include/value.h"
        self.assertEqual(loader.entries["module"].object_id, base)
        self.assertNotEqual(base, current)
        self.assertEqual(loader.read_blob(source, "captured pin"), b'#define VALUE "base"\n')
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            observed = session.make("all", variables=("FILES",), owner_inputs=("module",))
            self.assertEqual(observed.semantics["domains"]["FILES"]["value"], "module/src/tool.c")
            self.assertIn(("module", "160000", base), observed.semantics["owner_inputs"])
            self.assertEqual(session.sources((source,)), (source,))
            output = session.command(Command(
                ("/usr/bin/python3", "-c", f"print(open({source!r}).read(),end='')"), sources=(source,),
            ))
            self.assertEqual(output.stdout, b'#define VALUE "base"\n')
            self.assertEqual(output.consumed, (source,))
            tool = session.compile_native(("module/src/tool.c",), headers=(source,))
            self.assertEqual(session.native(tool).stdout, b"base\n")
        self.assert_clean(session)
        self.assertEqual((module / "include/value.h").read_text(), "substituted live checkout")

    def test_gitlink_sources_require_explicit_regular_exact_available_pin(self):
        module, (base, current) = self.gitlink_fixture()
        budget = ProbeBudget()
        loader = self.gitlink_loader(budget, module / ".git", base, admit=False)
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            with self.assertRaisesRegex(MakeProbeError, "nonregular candidate source"):
                session.make("all")
        self.assert_clean(session)
        blob = self.gitlink_git(module, "rev-parse", current + ":include/value.h")
        for path, database, pin, expected in (
            ("module", module / ".git", "f"*40, "trusted Git failed"),
            ("module", module / ".git", blob, "not a commit"),
            ("module", self.directory / "missing", base, "unavailable"),
            ("module", module, base, "trusted Git failed"),
            ("../escape", module / ".git", base, "canonical"),
            ("Makefile", module / ".git", base, "unique captured gitlink"),
            ("unrequested", module / ".git", base, "unique captured gitlink"),
        ):
            with self.subTest(path=path, database=database, pin=pin):
                budget = ProbeBudget()
                self.gitlink_git(self.root, "update-index", "--cacheinfo", f"160000,{pin},module")
                revision = self.gitlink_git(self.root, "write-tree")
                with self.assertRaisesRegex(MakeProbeError, expected):
                    git_tree_entries(self.root, revision, budget=budget, gitlinks=(GitlinkSource(path, database),))
                budget.close()
                self.assertFalse(budget.children)
        budget = ProbeBudget()
        loader = self.gitlink_loader(budget, module / ".git", base)
        with self.assertRaisesRegex(MakeProbeError, "unique captured gitlink"):
            git_tree_entries(self.root, loader.revision, budget=budget, gitlinks=(
                GitlinkSource("module", module / ".git"), GitlinkSource("module", module / ".git"),
            ))
        with self.assertRaisesRegex(MakeProbeError, "immutable superproject"):
            AuthorityLoader(self.root, loader.entries, budget=budget)
        with self.assertRaisesRegex(MakeProbeError, "immutable superproject"):
            AuthorityLoader(
                self.root, GitTreeEntries(loader.entries, budget=budget), loader.revision, budget=budget,
            )
        budget.close()
        alias = self.directory / "database-alias"
        alias.symlink_to(module / ".git")
        with self.assertRaisesRegex(MakeProbeError, "symlink"):
            self.gitlink_loader(ProbeBudget(), alias, base)
        (module / "escape").symlink_to("../../outside")
        self.gitlink_git(module, "add", "escape")
        self.gitlink_git(module, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "nonregular")
        nonregular = self.gitlink_git(module, "rev-parse", "HEAD")
        with self.assertRaisesRegex(MakeProbeError, "nonregular/nested"):
            self.gitlink_loader(ProbeBudget(), module / ".git", nonregular)
        (module / "escape").unlink()
        self.gitlink_git(module, "add", "--all")
        self.gitlink_git(module, "update-index", "--add", "--cacheinfo", f"160000,{base},nested")
        self.gitlink_git(module, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "nested gitlink")
        nested = self.gitlink_git(module, "rev-parse", "HEAD")
        with self.assertRaisesRegex(MakeProbeError, "nonregular/nested"):
            self.gitlink_loader(ProbeBudget(), module / ".git", nested)

    def test_gitlink_entries_and_bytes_spend_existing_capture_bounds(self):
        module, (base, _) = self.gitlink_fixture()
        budget = ProbeBudget(Limits(entries=3))
        with self.assertRaisesRegex(MakeProbeError, "expanded source entry"):
            self.gitlink_loader(budget, module / ".git", base)
        budget.close()
        (module / "large.bin").write_bytes(b"x"*8192)
        self.gitlink_git(module, "add", "large.bin")
        self.gitlink_git(module, "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "bounded blob")
        pin = self.gitlink_git(module, "rev-parse", "HEAD")
        budget = ProbeBudget(Limits(snapshot_bytes=4096))
        loader = self.gitlink_loader(budget, module / ".git", pin)
        with self.assertRaisesRegex(MakeProbeError, "snapshot byte"):
            Snapshot(loader, budget)
        self.assertFalse(budget.children)
        budget.close()

    def test_gitlink_empty_pin_is_a_make_directory_not_extra_command_authority(self):
        module, _ = self.gitlink_fixture()
        tree = self.gitlink_git(module, "mktree", input=b"")
        pin = self.gitlink_git(module, "-c", "commit.gpgsign=false", "commit-tree", tree, "-m", "empty")
        budget = ProbeBudget()
        loader = self.gitlink_loader(budget, module / ".git", pin)
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            self.assertTrue((session.tree / "module").is_dir())
            result = session.make("all", variables=("FILES",), owner_inputs=("module",))
            self.assertEqual(result.semantics["domains"]["FILES"]["value"], "")
            self.assertIn(("module", "160000", pin), result.semantics["owner_inputs"])
            output = session.command(Command(
                ("/usr/bin/python3", "-c", "import os; print(' '.join(os.listdir('.')))"),
                directories=(".",),
            ))
            self.assertEqual(set(output.stdout.split()), {b"Makefile", b"module"})
            with self.assertRaisesRegex(MakeProbeError, "undeclared source read: /repo/Makefile"):
                session.command(Command(
                    ("/usr/bin/python3", "-c", "print(open('Makefile').read())"), directories=(".",),
                ))
        self.assert_clean(session)
        budget = ProbeBudget()
        loader = self.gitlink_loader(budget, module / ".git", pin)
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            with self.assertRaisesRegex(MakeProbeError, "undeclared source directory enumeration: /repo"):
                session.command(Command(("/usr/bin/python3", "-c", "import os; print(os.listdir('.'))")))
        self.assert_clean(session)

    def test_authority_composition_rejects_foreign_and_detached_budgets(self):
        self.add("Makefile", "all: ;\n")
        budget = ProbeBudget()
        entries, revision = self.capture_tree(budget)
        loader = AuthorityLoader(self.root, entries, revision, budget=budget)
        self.assertIs(entries.budget, budget)
        self.assertIs(loader.budget, budget)
        before = (budget.started, budget.deadline, budget.runs, dict(budget.bytes))
        for wrong in (None, object(), ProbeBudget()):
            for stage, operation in (
                ("loader", lambda: AuthorityLoader(self.root, entries, revision, budget=wrong)),
                ("snapshot", lambda: Snapshot(loader, wrong)),
                ("session", lambda: ProbeSession(loader, scratch_root=self.scratch, budget=wrong)),
            ):
                with self.subTest(stage=stage, budget=wrong):
                    with self.assertRaisesRegex(MakeProbeError, "report budget"):
                        operation()
        for wrong in (None, object()):
            with self.subTest(capture_budget=wrong):
                with self.assertRaisesRegex(MakeProbeError, "explicit report budget"):
                    git_tree_entries(self.root, revision, budget=wrong)
        for detached in (dict(entries), entries.copy()):
            with self.assertRaisesRegex(MakeProbeError, "capture's report budget"):
                AuthorityLoader(self.root, detached, revision, budget=budget)
        self.assertEqual((budget.started, budget.deadline, budget.runs, budget.bytes), before)
        self.assertFalse(budget.children)
        self.assertFalse(self.scratch.exists())

    def test_authority_chain_keeps_capture_reads_snapshot_and_execution_on_one_budget(self):
        self.add("Makefile", "all: ;\n")
        budget = ProbeBudget()
        entries, revision = self.capture_tree(budget)
        started, deadline = budget.started, budget.deadline
        self.assertEqual(budget.runs, 1)
        capture_bytes = dict(budget.bytes)
        loader = AuthorityLoader(self.root, entries, revision, budget=budget)
        self.assertEqual(loader.read_blob("Makefile", "owned input"), b"all: ;\n")
        self.assertEqual(budget.runs, 2)
        self.assertGreater(budget.bytes["output"], capture_bytes["output"])
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            self.assertIs(session.budget, budget)
            self.assertIs(session.snapshot.budget, budget)
            self.assertIs(loader.budget, budget)
            self.assertIs(entries.budget, budget)
            self.assertEqual(session.snapshot.files["Makefile"], b"all: ;\n")
            self.assertEqual((budget.started, budget.deadline), (started, deadline))
            self.assertGreater(budget.runs, 2)
            self.assertGreater(budget.bytes["snapshot"], len(b"all: ;\n"))
            runs, charged = budget.runs, dict(budget.bytes)
            with self.assertRaisesRegex(MakeProbeError, "snapshot's report budget"):
                session.snapshot.materialize(self.directory / "unfunded", ["Makefile"], ProbeBudget())
            self.assertEqual((budget.runs, budget.bytes), (runs, charged))
            self.assertFalse((self.directory / "unfunded").exists())
            observed = session.make("all")
            self.assertEqual(observed.semantics["files"][0]["target"], "all")
            self.assertGreater(budget.runs, runs)
            self.assertGreater(session.processes_used, 0)
        self.assert_clean(session)
        self.assertIs(loader.budget, budget)
        self.assertTrue(budget.closed)
        self.assertEqual((budget.started, budget.deadline), (started, deadline))

    def test_capture_run_quota_cannot_be_reset_by_reads_snapshot_or_session(self):
        self.add("Makefile", "all: ;\n")
        for stage in ("read", "snapshot", "session"):
            with self.subTest(stage=stage):
                budget = ProbeBudget(Limits(runs=1))
                entries, revision = self.capture_tree(budget)
                loader = AuthorityLoader(self.root, entries, revision, budget=budget)
                session = ProbeSession(loader, scratch_root=self.scratch, budget=budget)
                with self.assertRaisesRegex(MakeProbeError, "aggregate process-launch budget"):
                    if stage == "read":
                        loader.read_blob("Makefile", "owned input")
                    elif stage == "snapshot":
                        Snapshot(loader, budget)
                    else:
                        with session:
                            self.fail("capture quota was reset")
                budget.close()
                self.assertEqual(budget.runs, 2)
                self.assertTrue(budget.failed)
                self.assert_clean(session)

    def test_direct_live_and_immutable_authority_reads_share_the_byte_quota(self):
        self.add("Makefile", "all: ;\n")
        self.add("input.bin", b"x"*2048)
        for immutable in (False, True):
            with self.subTest(immutable=immutable):
                budget = ProbeBudget(Limits(total_bytes=4096, file_bytes=4096))
                entries, revision = self.capture_tree(budget)
                loader = AuthorityLoader(
                    self.root, entries, revision if immutable else None, budget=budget,
                )
                captured = sum(budget.bytes.values())
                self.assertEqual(loader.read_blob("input.bin", "owned input"), b"x"*2048)
                self.assertGreaterEqual(sum(budget.bytes.values()), captured + 2048)
                with self.assertRaisesRegex(MakeProbeError, "aggregate .*byte budget"):
                    loader.read_blob("input.bin", "owned repeated input")
                budget.close()
                self.assertTrue(budget.failed)
                self.assertFalse(budget.children)
                self.assertFalse(self.scratch.exists())

    def test_expired_capture_cannot_start_another_authority_stage(self):
        self.add("Makefile", "all: ;\n")
        budget = ProbeBudget(Limits(seconds=1))
        entries, revision = self.capture_tree(budget)
        loader = AuthorityLoader(self.root, entries, revision, budget=budget)
        before = (budget.started, budget.deadline, budget.runs, dict(budget.bytes))
        time.sleep(max(0, budget.deadline - time.monotonic()) + 0.02)
        for stage, operation in (
            ("capture", lambda: git_tree_entries(self.root, revision, budget=budget)),
            ("loader", lambda: AuthorityLoader(self.root, entries, revision, budget=budget)),
            ("read", lambda: loader.read_blob("Makefile", "expired input")),
            ("snapshot", lambda: Snapshot(loader, budget)),
            ("session", lambda: ProbeSession(loader, scratch_root=self.scratch, budget=budget)),
        ):
            with self.subTest(stage=stage):
                with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline/budget"):
                    operation()
        self.assertEqual((budget.started, budget.deadline, budget.runs, budget.bytes), before)
        self.assertFalse(budget.children)
        self.assertFalse(self.scratch.exists())

    def test_report_budget_binds_one_terminal_session_lifetime(self):
        self.add("Makefile", "all: ;\n")
        budget = ProbeBudget()
        entries, revision = self.capture_tree(budget)
        loader = AuthorityLoader(self.root, entries, revision, budget=budget)
        second_loader = AuthorityLoader(self.root, entries, revision, budget=budget)
        second = ProbeSession(second_loader, scratch_root=self.scratch, budget=budget)
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            for another in (session, second):
                with self.subTest(owner=another.loader is loader):
                    with self.assertRaisesRegex(MakeProbeError, "already owns a probe session lifetime"):
                        with another:
                            self.fail("report acquired a second session")
            self.assertFalse(budget.closed)
            self.assertEqual(session.command(Command(("/usr/bin/printf", "original owner"))).stdout, b"original owner")
        self.assert_clean(session)
        before = (budget.started, budget.deadline, budget.runs, dict(budget.bytes))
        for stage, operation in (
            ("capture", lambda: git_tree_entries(self.root, revision, budget=budget)),
            ("loader", lambda: AuthorityLoader(self.root, entries, revision, budget=budget)),
            ("read", lambda: loader.read_blob("Makefile", "closed input")),
            ("snapshot", lambda: Snapshot(loader, budget)),
            ("session", lambda: ProbeSession(loader, scratch_root=self.scratch, budget=budget)),
        ):
            with self.subTest(closed_stage=stage):
                with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline/budget"):
                    operation()
        budget.close()
        self.assertTrue(budget.closed)
        self.assertEqual((budget.started, budget.deadline, budget.runs, budget.bytes), before)
        self.assertFalse(self.scratch.exists())

    def capture_view(self, budget):
        def git(*args):
            return subprocess.run(
                ["/usr/bin/git", "-C", str(self.root), *args], env=ENVIRONMENT,
                capture_output=True, check=True, timeout=10,
            ).stdout.decode("ascii").strip()
        git("init", "--quiet")
        git("add", "--all")
        revision = git("write-tree")
        return AuthorityLoader(
            self.root, git_tree_entries(self.root, revision, budget=budget), revision, budget=budget,
        )

    def deleted_source_views(self, budget):
        old, new = "src/data/deleted_generated.json", "src/data/current_generated.json"
        for name in ("schema.py", "diagnostics.py", "json_loader.py", "validators.py", "shops/schema.py"):
            path = "scripts/generated_data/" + name
            self.add(path, (ROOT / path).read_bytes())
        for path in ("scripts/__init__.py", "scripts/generated_data/__init__.py",
                     "scripts/generated_data/shops/__init__.py"):
            self.add(path, "")
        self.add("declarations.py", (
            "import json,sys\nsys.path.insert(0,'/repo')\n"
            "from scripts.generated_data.registry import REGISTRY\n"
            "declarations={name:REGISTRY.resolve(name).default_source for name in REGISTRY.all_names()}\n"
            f"owners={{path:[name for name,source in declarations.items() if source==path] "
            f"for path in ({old!r},{new!r})}}\n"
            "print(json.dumps({'declarations':declarations,'owners':owners},sort_keys=True))\n"
        ))
        self.add("Makefile", "DECLARATIONS := $(shell python3 declarations.py)\nall: ;\n")
        self.gitlink_git(self.root, "init", "--quiet")
        self.gitlink_git(self.root, "config", "user.name", "Owned View Fixture")
        self.gitlink_git(self.root, "config", "user.email", "fixture@example.invalid")
        loaders = []
        for source, count in ((old, 1), (new, 2)):
            other = new if source == old else old
            (self.root / other).unlink(missing_ok=True)
            self.entries.pop(other, None)
            self.add("scripts/generated_data/registry.py", (
                "from .schema import REGISTRY\nfrom .shops.schema import ShopsTableSchema\n"
                f"schema=ShopsTableSchema()\nschema.default_source={source!r}\nREGISTRY.register(schema)\n"
            ))
            self.add(source, json.dumps({
                "$schema": "fe8.shops.v1",
                "shops": [{"symbol": f"OwnedShop{index}", "items": ["ITEM_SWORD_IRON"]}
                          for index in range(count)],
            }) + "\n")
            self.gitlink_git(self.root, "add", "--all")
            self.gitlink_git(
                self.root, "-c", "commit.gpgsign=false", "commit", "--quiet",
                "-m", "owned BASE" if source == old else "owned CURRENT",
            )
            revision = self.gitlink_git(self.root, "rev-parse", "HEAD")
            loaders.append(AuthorityLoader(
                self.root, git_tree_entries(self.root, revision, budget=budget), revision, budget=budget,
            ))
        code = tuple(sorted(path for path in self.entries if path.endswith(".py")))
        directories = (".", "scripts", "scripts/generated_data", "scripts/generated_data/shops")
        driver = (TRUSTED_ROOT / "generated_registry_probe.py").read_text()
        commands = tuple(Command(
            ("/usr/bin/python3", "-c", driver, "shops", source), code=code, sources=(source,),
            directories=directories,
        ) for source in (old, new))
        return (*loaders, commands)

    def test_immutable_view_selects_real_deleted_base_registry_in_one_report(self):
        budget = ProbeBudget()
        base, current, (old_command, new_command) = self.deleted_source_views(budget)
        old, new = old_command.sources[0], new_command.sources[0]
        self.assertEqual(self.gitlink_git(self.root, "rev-parse", current.revision + "^"), base.revision)
        self.assertFalse((self.root / old).exists())
        started, deadline = budget.started, budget.deadline
        base_bytes = base.read_blob(old, "deleted BASE source")
        declarations = replace(new_command, argv=("/usr/bin/python3", "/repo/declarations.py"), sources=())
        with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
            original = session.loader, session.snapshot, session.tree, session.cache, session.native_tools
            report_root = session.base
            current_record = probe_generated_registry(current, command=new_command, session=session)
            current_owners = json.loads(session.command(declarations).stdout)
            self.assertEqual(current_record["record_count"], 2)
            self.assertEqual(current_owners["declarations"], {"shops": new})
            self.assertEqual(current_owners["owners"][old], [])
            with self.assertRaisesRegex(MakeProbeError, "resolves no regular inputs"):
                session.sources((old,))
            with self.assertRaisesRegex(MakeProbeError, "loader/budget differs"):
                probe_generated_registry(base, command=old_command, session=session)
            with self.assertRaisesRegex(MakeProbeError, "already owns a probe session lifetime"):
                with ProbeSession(base, scratch_root=self.scratch, budget=budget):
                    self.fail("a second report owner substituted for selection")
            before = budget.runs, budget.states, dict(budget.bytes), session.processes_used
            with session.select_view(base) as selected:
                self.assertIs(selected, session)
                self.assertIs(session.base, report_root)
                self.assertIs(session.loader, base)
                self.assertIs(session.snapshot.budget, budget)
                self.assertEqual((budget.started, budget.deadline), (started, deadline))
                self.assertNotIn(new, session.snapshot.files)
                self.assertFalse((session.tree / new).exists())
                record = probe_generated_registry(base, command=old_command, session=session)
                self.assertEqual(record, {
                    "name": "shops", "version": 1, "source_paths": [old], "record_count": 1,
                })
                actual_bytes = session.command(Command(
                    ("/usr/bin/python3", "-c", f"import sys;sys.stdout.buffer.write(open({old!r},'rb').read())"),
                    sources=(old,),
                ))
                self.assertEqual(actual_bytes.stdout, base_bytes)
                self.assertEqual(actual_bytes.consumed, (old,))
                actual = session.make(
                    "all", variables=("DECLARATIONS",), commands={"python3 declarations.py": declarations},
                )
                base_owners = json.loads(actual.semantics["domains"]["DECLARATIONS"]["value"])
                self.assertEqual(base_owners["declarations"], {"shops": old})
                self.assertEqual(base_owners["owners"], {old: ["shops"], new: []})
                self.assertNotEqual(base_owners["owners"][old], current_owners["owners"][old])
                with self.assertRaisesRegex(MakeProbeError, "loader/budget differs"):
                    probe_generated_registry(current, command=new_command, session=session)
                self.assertGreater(budget.runs, before[0])
                self.assertEqual(budget.states, before[1] + 2)
                self.assertGreater(budget.bytes["snapshot"], before[2]["snapshot"])
                self.assertGreater(session.processes_used, before[3])
                selected_root = session.tree.parent
            self.assertEqual(
                (session.loader, session.snapshot, session.tree, session.cache, session.native_tools), original,
            )
            self.assertFalse(selected_root.exists())
            self.assertEqual(probe_generated_registry(current, command=new_command, session=session), current_record)
            restored = session.make(
                "all", variables=("DECLARATIONS",), commands={"python3 declarations.py": declarations},
            )
            self.assertEqual(json.loads(restored.semantics["domains"]["DECLARATIONS"]["value"]), current_owners)
            self.assertEqual((budget.started, budget.deadline), (started, deadline))
        self.assert_clean(session)

    def test_immutable_view_real_repository_query_pair(self):
        from scripts.validation_ownership.authority import git
        from scripts.validation_ownership.consumer import registry_entries
        budget = ProbeBudget()
        lifetime = budget.started, budget.deadline
        scratch = self.directory / "real-repository-pair"
        try:
            revisions = git(ROOT, budget, "rev-parse", "HEAD", "HEAD^1").decode("ascii").splitlines()
            self.assertEqual(len(revisions), 2)
            current, base = (
                AuthorityLoader(ROOT, registry_entries(ROOT, revision, budget), revision, budget=budget)
                for revision in revisions
            )
            with ProbeSession(current, scratch_root=scratch, budget=budget) as session:
                report_root = session.base
                original = {name: getattr(session, name) for name in (
                    "loader", "snapshot", "tree", "cache", "mappings", "native_tools", "make_runtime",
                )}

                def counters():
                    return {
                        "runs": budget.runs, "states": budget.states, "processes": session.processes_used,
                        "syscalls": session.syscalls_used, "observations": session.observations_used,
                        "created_files": session.files_created,
                        **{"bytes:" + name: used for name, used in budget.bytes.items()},
                    }

                def progressed(before):
                    after = counters()
                    for name, used in before.items():
                        self.assertGreaterEqual(after.get(name, 0), used, name)
                    for name in ("runs", "states", "processes", "syscalls", "observations"):
                        self.assertGreater(after[name], before[name], name)
                    self.assertEqual((budget.started, budget.deadline), lifetime)
                    self.assertIs(session.budget, budget)
                    self.assertEqual(budget.limits, Limits())
                    return after

                def make():
                    observed = session.make(
                        "localization-check", makefile="localization.mk",
                        variables=("LOCALIZATION_OUT_DIR",), owner_inputs=("localization.mk",),
                    )
                    self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                        {"name": "localization-generate", "order_only": False},
                    ])
                    self.assertTrue(observed.semantics["domains"]["LOCALIZATION_OUT_DIR"]["value"])
                    self.assertEqual(observed.semantics["owner_inputs"], session.snapshot.owners(("localization.mk",)))
                    return observed

                def registry(loader):
                    self.assertIs(session.loader, loader)
                    self.assertIs(session.snapshot.loader, loader)
                    self.assertIs(session.snapshot.budget, budget)
                    self.assertIs(loader.entries.budget, budget)
                    self.assertEqual(set(session.snapshot.files), {
                        name for name, entry in loader.entries.items()
                        if entry.mode in {"100644", "100755"} and entry.object_type == "blob"
                    })
                    code = tuple(sorted(
                        path for path in session.snapshot.files
                        if path.endswith(".py") and path.startswith(("scripts/generated_data/", "scripts/assets/"))
                    ))
                    directories = tuple(sorted({".", "src/data", *(
                        parent.as_posix() for name in code for parent in Path(name).parents
                    )}))
                    observed = probe_generated_registry(loader, session=session, command=Command(
                        ("/usr/bin/python3", "-I", "-S", "-B", "-c",
                         (TRUSTED_ROOT / "generated_registry_probe.py").read_text(encoding="utf-8"),
                         "chapterbundle", "src/data"),
                        code=code, sources=("src/data/*_bundle.json",), directories=directories,
                    ))
                    self.assertEqual(observed["name"], "chapterbundle")
                    self.assertEqual(observed["source_paths"], list(session.sources(("src/data/*_bundle.json",))))
                    self.assertGreater(observed["record_count"], 0)

                before = counters()
                current_make = make()
                registry(current)
                current_counts = progressed(before)
                shared = {
                    name for name, entry in base.entries.items()
                    if name in original["snapshot"].files and current.entries.get(name) == entry
                }
                self.assertTrue(shared)
                with session.select_view(base) as selected:
                    self.assertIs(selected, session)
                    self.assertIs(session.base, report_root)
                    self.assertIsNot(session.cache, original["cache"])
                    self.assertIsNot(session.native_tools, original["native_tools"])
                    self.assertEqual(session.snapshot.reused_paths, shared)
                    for name in shared:
                        self.assertIs(session.snapshot.files[name], original["snapshot"].files[name], name)
                        selected_stat = (session.tree / name).stat()
                        current_stat = (original["tree"] / name).stat()
                        self.assertEqual(
                            (selected_stat.st_dev, selected_stat.st_ino),
                            (current_stat.st_dev, current_stat.st_ino), name,
                        )
                    make()
                    registry(base)
                    base_counts = progressed(current_counts)
                    self.assertGreater(budget.bytes["snapshot"], current_counts["bytes:snapshot"])
                    selected_root = session.tree.parent
                for name, value in original.items():
                    self.assertIs(getattr(session, name), value, name)
                self.assertFalse(selected_root.exists())
                restored = make()
                self.assertEqual(restored.semantics, current_make.semantics)
                self.assertEqual(restored.semantic_digest, current_make.semantic_digest)
                self.assertEqual(restored.execution_digest, current_make.execution_digest)
                progressed(base_counts)
                for name, used in budget.bytes.items():
                    self.assertLessEqual(used, getattr(budget.limits, name + "_bytes"), name)
                self.assertLessEqual(sum(budget.bytes.values()), budget.limits.total_bytes)
                self.assertFalse(budget.failed)
            self.assert_clean(session)
            self.assertTrue(budget.closed)
            self.assertFalse(scratch.exists())
        finally:
            budget.close()

    def test_immutable_view_rejects_wrong_foreign_mutable_and_closed_authority(self):
        budget, other_budget = ProbeBudget(), ProbeBudget()
        self.add("value", "base")
        base = self.capture_view(budget)
        self.add("value", "current")
        current = self.capture_view(budget)
        foreign_root = self.directory / "foreign"
        shutil.copytree(self.root, foreign_root)
        foreign = AuthorityLoader(
            foreign_root, git_tree_entries(foreign_root, base.revision, budget=budget),
            base.revision, budget=budget,
        )
        other = AuthorityLoader(
            self.root, git_tree_entries(self.root, base.revision, budget=other_budget),
            base.revision, budget=other_budget,
        )
        detached = AuthorityLoader(
            self.root, GitTreeEntries(base.entries, budget=budget), base.revision, budget=budget,
        )
        mutable = AuthorityLoader(self.root, current.entries, budget=budget)
        session = ProbeSession(current, scratch_root=self.scratch, budget=budget)
        with self.assertRaisesRegex(MakeProbeError, "not active"):
            with session.select_view(base):
                self.fail("inactive selector ran")
        with session:
            foreign_snapshot = Snapshot(foreign, budget)
            other_snapshot = Snapshot(other, other_budget)
            for invalid in (object(), foreign_snapshot, other_snapshot):
                with self.assertRaisesRegex(MakeProbeError, "snapshot reuse"):
                    Snapshot(current, budget, reuse=invalid)
            for root, revision in ((foreign_root, base.revision), (self.root, current.revision)):
                with self.assertRaisesRegex(MakeProbeError, "captured repository/revision"):
                    AuthorityLoader(root, base.entries, revision, budget=budget)
            before = budget.runs, budget.states, dict(budget.bytes)
            for loader in (None, object(), foreign, other, detached, mutable):
                with self.subTest(loader=type(loader).__name__):
                    with self.assertRaisesRegex(MakeProbeError, "immutable capture"):
                        with session.select_view(loader):
                            self.fail("invalid view selected")
                    self.assertIs(session.loader, current)
                    self.assertFalse(budget.failed)
                    self.assertEqual((budget.runs, budget.states, budget.bytes), before)
            self.assertEqual(session.command(Command(("/usr/bin/printf", "active"))).stdout, b"active")
            late = session.select_view(base)
        with self.assertRaisesRegex(MakeProbeError, "not active"):
            with late:
                self.fail("closed selector reopened the report")
        with self.assertRaisesRegex(MakeProbeError, "deadline/budget"):
            base.read_blob("value", "closed read")
        with self.assertRaisesRegex(MakeProbeError, "deadline/budget"):
            Snapshot(base, budget, reuse=foreign_snapshot)
        other_budget.close()
        self.assert_clean(session)

    def test_immutable_view_nested_restoration_and_terminal_error_cleanup(self):
        from scripts.validation_ownership import make_probe
        for failure in ("body", "snapshot", "materialize", "interrupt"):
            with self.subTest(failure=failure):
                budget = ProbeBudget()
                self.add("value", "base")
                base = self.capture_view(budget)
                self.add("value", "current")
                current = self.capture_view(budget)
                command = Command(
                    ("/usr/bin/python3", "-c", "print(open('value').read())"), sources=("value",),
                )
                with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
                    previous = session.loader, session.snapshot, session.tree, session.cache
                    with session.select_view(base):
                        base_state = session.loader, session.snapshot, session.tree, session.cache
                        with session.select_view(current):
                            self.assertEqual(session.command(command).stdout, b"current\n")
                        self.assertEqual((session.loader, session.snapshot, session.tree, session.cache), base_state)
                        self.assertEqual(session.command(command).stdout, b"base\n")
                    self.assertEqual((session.loader, session.snapshot, session.tree, session.cache), previous)
                    error = KeyboardInterrupt("selected interrupt") if failure == "interrupt" else RuntimeError(failure)
                    real_snapshot, real_materialize = Snapshot, Snapshot.materialize
                    def snapshot(*args, **kwargs):
                        self.assertEqual((session.loader, session.snapshot, session.tree, session.cache), previous)
                        if failure == "snapshot":
                            raise error
                        return real_snapshot(*args, **kwargs)
                    def materialize(snapshot, *args):
                        self.assertEqual((session.loader, session.snapshot, session.tree, session.cache), previous)
                        result = real_materialize(snapshot, *args)
                        if failure == "materialize":
                            raise error
                        return result
                    with patch.object(make_probe, "Snapshot", snapshot), patch.object(
                        Snapshot, "materialize", materialize,
                    ):
                        with self.assertRaises(type(error)) as caught:
                            with session.select_view(base):
                                raise error
                    self.assertIs(caught.exception, error)
                    self.assertEqual((session.loader, session.snapshot, session.tree, session.cache), previous)
                    self.assertTrue(budget.failed)
                    self.assertTrue(budget.closed)
                    self.assertFalse(list(session.base.glob("view-*")))
                    self.assertFalse(budget.children)
                    self.assertFalse(session._views)
                self.assert_clean(session)

    def test_immutable_views_isolate_cache_native_files_and_static_make(self):
        budget = ProbeBudget()
        self.add("value.txt", "base")
        self.add("reader.py", "print(open('value.txt').read())\n")
        self.add("native.c", '#include <stdio.h>\nint main(void) { puts("observed"); }\n')
        self.add("Makefile", "VALUE := $(shell python3 reader.py)\nall: ;\n")
        self.add("stable.mk", "stable: ;\n")
        base = self.capture_view(budget)
        self.add("value.txt", "current")
        current = self.capture_view(budget)
        command = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), sources=("value.txt",),
        )
        with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
            original = session.tree, session.cache, session.mappings, session.native_tools
            current_result = session.command(command)
            current_tool = session.compile_native(("native.c",))
            stable = session.make("stable", makefile="stable.mk")
            current_make = session.make("all", variables=("VALUE",), commands={"python3 reader.py": command})
            counts = budget.runs, session.files_created, session.processes_used
            with session.select_view(base):
                self.assertFalse(session.cache)
                self.assertFalse(session.mappings)
                self.assertFalse(session.native_tools)
                self.assertEqual(session.command(command).stdout, b"base\n")
                base_tool = session.compile_native(("native.c",))
                self.assertEqual(base_tool.digest, current_tool.digest)
                self.assertNotEqual(base_tool.path, current_tool.path)
                self.assertEqual(session.native(base_tool).stdout, b"observed\n")
                base_stable = session.make("stable", makefile="stable.mk")
                base_make = session.make("all", variables=("VALUE",), commands={"python3 reader.py": command})
                self.assertEqual(base_stable.semantic_digest, stable.semantic_digest)
                self.assertNotEqual(base_stable.execution_digest, stable.execution_digest)
                self.assertEqual(base_make.semantics["domains"]["VALUE"]["value"], "base")
                self.assertNotEqual(base_make.semantic_digest, current_make.semantic_digest)
                self.assertGreater(budget.runs, counts[0])
                self.assertGreater(session.files_created, counts[1])
                self.assertGreater(session.processes_used, counts[2])
            self.assertEqual((session.tree, session.cache, session.mappings, session.native_tools), original)
            self.assertFalse(base_tool.path.exists())
            self.assertTrue(current_tool.path.is_file())
            self.assertEqual(current_result.stdout, b"current\n")
            restored = session.command(command)
            self.assertEqual(restored.stdout, current_result.stdout)
            self.assertIs(session.command(command), restored)
            self.assertEqual(session.native(current_tool).stdout, b"observed\n")
            with self.assertRaisesRegex(MakeProbeError, "not issued"):
                session.native(base_tool)
        self.assert_clean(session)

    def test_immutable_view_rejects_a_suspended_native_handle_even_for_identical_bytes(self):
        for forged in (False, True):
            with self.subTest(forged=forged):
                self.add("native.c", "int main(void) { return 0; }\n")
                budget = ProbeBudget()
                loader = self.capture_view(budget)
                with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
                    tool = session.compile_native(("native.c",))
                    if forged:
                        tool = replace(tool)
                    with self.assertRaisesRegex(MakeProbeError, "not issued"):
                        with session.select_view(loader):
                            self.assertEqual(session.snapshot.digest, session._views[-1][1].digest)
                            session.native(tool)
                    self.assertIs(session.loader, loader)
                    self.assertTrue(budget.closed)
                    self.assertFalse(list(session.base.glob("view-*")))
                self.assert_clean(session)

    def test_immutable_view_reuses_only_identical_admitted_immutable_sources(self):
        budget = ProbeBudget(Limits(snapshot_bytes=384*1024))
        shared = b"x"*(128*1024)
        self.add("shared.bin", shared)
        self.add("changed.bin", b"base")
        self.add("mode.bin", b"same bytes, different mode")
        self.add("Makefile", "ATTACK := $(file >shared.bin,forged)\nall: ;\n")
        base = self.capture_view(budget)
        self.add("changed.bin", b"current")
        (self.root / "mode.bin").chmod(0o755)
        self.add("current-only.bin", b"current only")
        current = self.capture_view(budget)
        with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
            original_snapshot, original_tree = session.snapshot, session.tree
            original_links = (original_tree / "shared.bin").stat().st_nlink
            before = budget.bytes["snapshot"]
            with self.assertRaisesRegex(MakeProbeError, "write outside"):
                with session.select_view(base):
                    self.assertEqual(session.snapshot.reused_paths, {"Makefile", "shared.bin"})
                    self.assertIs(session.snapshot.files["shared.bin"], original_snapshot.files["shared.bin"])
                    self.assertEqual(
                        (session.tree / "shared.bin").stat().st_ino, (original_tree / "shared.bin").stat().st_ino,
                    )
                    self.assertLess(budget.bytes["snapshot"] - before, len(shared))
                    for name in ("changed.bin", "mode.bin"):
                        self.assertNotEqual(
                            (session.tree / name).stat().st_ino, (original_tree / name).stat().st_ino,
                        )
                    self.assertEqual((session.tree / "changed.bin").read_bytes(), b"base")
                    self.assertEqual(stat.S_IMODE((session.tree / "mode.bin").stat().st_mode), 0o644)
                    self.assertEqual(stat.S_IMODE((original_tree / "mode.bin").stat().st_mode), 0o755)
                    self.assertFalse((session.tree / "current-only.bin").exists())
                    session.make("all")
            self.assertEqual((original_tree / "shared.bin").read_bytes(), shared)
            self.assertEqual((original_tree / "shared.bin").stat().st_nlink, original_links)
            self.assertGreaterEqual(budget.bytes["snapshot"], before)
            self.assertFalse(list(session.base.glob("view-*")))
        self.assert_clean(session)

    def test_immutable_view_metadata_changes_do_not_hide_behind_shared_bytes(self):
        command, fields = self.static_metadata_fixture()
        budget = ProbeBudget()
        base = self.capture_view(budget)
        self.add("unrelated.txt", "current only")
        current = self.capture_view(budget)
        with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
            tree = session.tree
            first = session.command(command)
            original = json.loads(first.stdout)
            with session.select_view(base):
                self.assertIs(session.snapshot.files["data/value"], session._views[-1][1].files["data/value"])
                self.assertEqual((tree / "data/value").stat().st_nlink, original["data/value"]["st_nlink"] + 1)
                self.assertFalse(session._metadata_matches(first.metadata))
                selected = session.command(command)
                value = json.loads(selected.stdout)
                self.assertEqual(value["bytes"], original["bytes"])
                self.assertEqual(value["data/value"]["st_ino"], original["data/value"]["st_ino"])
                self.assertNotEqual(value["data"]["st_ino"], original["data"]["st_ino"])
                self.assertEqual(value["fstat"], value["data/value"])
                self.assertEqual(value["data/value"]["st_nlink"], original["data/value"]["st_nlink"] + 1)
                before = {name: (session.tree / name).stat() for name in ("code", "data", "data/value")}
                self.assertIs(session.command(command), selected)
                made = session.make("all", variables=("VALUE",), commands={"python3 reader.py": command})
                self.assertEqual(json.loads(made.semantics["domains"]["VALUE"]["value"]), value)
                self.assertEqual({name: (session.tree / name).stat() for name in before}, before)
            self.assertEqual((tree / "data/value").stat().st_nlink, original["data/value"]["st_nlink"])
            self.assertFalse(session._metadata_matches(first.metadata))
            fresh = session.command(command)
            self.assertIsNot(fresh, first)
            restored = json.loads(fresh.stdout)
            self.assertNotEqual(restored["data/value"]["st_ctime_ns"], original["data/value"]["st_ctime_ns"])
            self.assertEqual(restored["bytes"], original["bytes"])
            self.assertEqual(restored["data/value"]["st_ino"], original["data/value"]["st_ino"])
            self.assertIs(session.command(command), fresh)
            made = session.make("all", variables=("VALUE",), commands={"python3 reader.py": command})
            self.assertEqual(json.loads(made.semantics["domains"]["VALUE"]["value"]), restored)
        self.assert_clean(session)

    def test_immutable_view_restores_live_default_and_cannot_reactivate_closed_report(self):
        self.add("value", "base")
        budget = ProbeBudget()
        base = self.capture_view(budget)
        self.add("value", "current")
        current = self.capture_view(budget)
        live = AuthorityLoader(self.root, current.entries, budget=budget)
        command = Command(
            ("/usr/bin/python3", "-c", "print(open('value').read())"), sources=("value",),
        )
        with ProbeSession(live, scratch_root=self.scratch, budget=budget) as session:
            original = session.snapshot, session.cache, session.native_tools
            current_result = session.command(command)
            self.add("value", "mutated after capture")
            with session.select_view(base):
                self.assertFalse(session.snapshot.reused_paths)
                self.assertEqual(session.command(command).stdout, b"base\n")
            self.assertIs(session.loader, live)
            self.assertEqual((session.snapshot, session.cache, session.native_tools), original)
            self.assertIs(session.command(command), current_result)
            late = session.select_view(base)
            late.__enter__()
        self.assertFalse(live.live_modes)
        self.assertFalse(original[1])
        self.assertFalse(original[2])
        self.assert_clean(session)
        late.__exit__(None, None, None)
        self.assertIs(session.loader, live)
        self.assert_clean(session)

    def test_immutable_view_teardown_error_is_terminal_and_outer_cleanup_finishes(self):
        from scripts.validation_ownership import make_probe
        self.add("value", "base")
        budget = ProbeBudget()
        loader = self.capture_view(budget)
        remove = make_probe._remove_owned_tree
        failure = OSError(errno.EIO, "owned view teardown failure")
        def failing(path):
            if path.name.startswith("view-"):
                raise failure
            return remove(path)
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            with patch.object(make_probe, "_remove_owned_tree", failing):
                with self.assertRaises(OSError) as caught:
                    with session.select_view(loader):
                        selected_root = session.tree.parent
            self.assertIs(caught.exception, failure)
            self.assertIs(session.loader, loader)
            self.assertTrue(budget.failed)
            self.assertTrue(budget.closed)
            self.assertTrue(selected_root.exists())
            self.assertFalse(session._views)
        self.assert_clean(session)

    def test_immutable_view_shares_make_state_and_snapshot_byte_limits(self):
        for boundary in ("states", "snapshot"):
            with self.subTest(boundary=boundary):
                budget = ProbeBudget(Limits(**({"states": 2} if boundary == "states" else {"snapshot_bytes": 8192})))
                self.add("Makefile", "all: ;\n")
                self.add("value", b"b"*1024)
                base = self.capture_view(budget)
                self.add("value", b"c"*1024)
                current = self.capture_view(budget)
                with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
                    session.make("all")
                    if boundary == "states":
                        with session.select_view(base):
                            self.assertEqual(budget.states, 2)
                        runs = budget.runs
                        with self.assertRaisesRegex(MakeProbeError, "state budget"):
                            with session.select_view(base):
                                self.fail("view reset Make's state allowance")
                        self.assertEqual(budget.runs, runs)
                    else:
                        before = budget.bytes["snapshot"]
                        successful = 0
                        with self.assertRaisesRegex(MakeProbeError, "snapshot byte"):
                            for _ in range(8):
                                with session.select_view(base):
                                    self.assertGreater(budget.bytes["snapshot"], before)
                                    before = budget.bytes["snapshot"]
                                successful += 1
                        self.assertGreater(successful, 0)
                        self.assertLess(successful, 8)
                    self.assertIs(session.loader, current)
                    self.assertTrue(budget.failed)
                    self.assertTrue(budget.closed)
                    self.assertFalse(list(session.base.glob("view-*")))
                self.assert_clean(session)

    def test_immutable_view_shares_report_run_and_deadline_limits(self):
        for boundary in ("runs", "deadline"):
            with self.subTest(boundary=boundary):
                budget = ProbeBudget(Limits(runs=16))
                self.add("value", "base")
                base = self.capture_view(budget)
                self.add("value", "current")
                current = self.capture_view(budget)
                started, deadline = budget.started, budget.deadline
                with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
                    with session.select_view(base):
                        self.assertEqual(session.command(Command(("/usr/bin/printf", "base"))).stdout, b"base")
                        self.assertEqual((budget.started, budget.deadline), (started, deadline))
                    if boundary == "runs":
                        while budget.runs < budget.limits.runs:
                            budget.run(["/usr/bin/true"], env=ENVIRONMENT)
                        with self.assertRaisesRegex(MakeProbeError, "process-launch budget"):
                            with session.select_view(base):
                                self.fail("view reset the capture/execution launch quota")
                        self.assertEqual(budget.runs, budget.limits.runs + 1)
                    else:
                        runs = budget.runs
                        with patch("scripts.validation_ownership.budget.time.monotonic", return_value=deadline + 1):
                            with self.assertRaisesRegex(MakeProbeError, "deadline/budget"):
                                with session.select_view(base):
                                    self.fail("view restarted an expired deadline")
                        self.assertEqual(budget.runs, runs)
                    self.assertEqual((budget.started, budget.deadline), (started, deadline))
                    self.assertIs(session.loader, current)
                    self.assertTrue(budget.failed)
                    self.assertFalse(list(session.base.glob("view-*")))
                self.assert_clean(session)

    def test_immutable_view_cannot_reset_creation_or_borrow_current_command_cache(self):
        budget = ProbeBudget(Limits(created_files=1))
        self.add("value", "unchanged")
        loader = self.capture_view(budget)
        command = Command(("/usr/bin/python3", "-c", "open('/work/once','w').write('owned')"))
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            first = session.command(command)
            self.assertIs(session.command(command), first)
            self.assertEqual(session.files_created, 1)
            with self.assertRaisesRegex(MakeProbeError, "creation budget"):
                with session.select_view(loader):
                    session.command(command)
            self.assertGreater(session.files_created, 1)
            self.assertIs(session.loader, loader)
            self.assertFalse(list(session.base.glob("view-*")))
        self.assert_clean(session)

    def test_immutable_view_observation_totals_follow_selection_without_reset(self):
        command = self.observation_command_fixture()
        names = self.observation_reservoir()
        budget = ProbeBudget(Limits(entries=64))
        base = self.capture_view(budget)
        self.add("data/a", "current\n")
        current = self.capture_view(budget)
        started, deadline = budget.started, budget.deadline
        with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
            self.assertEqual(session.command(command).stdout, b"current\n")
            before = session.observations_used
            with session.select_view(base):
                self.assertEqual(session.command(command).stdout, b"observed\n")
                self.assertGreater(session.observations_used, before)
                self.spend_observation_remainder(session, names, keep=0)
                self.assertEqual(session.observations_used, 64)
            counts = budget.runs, session.processes_used
            with self.assertRaisesRegex(MakeProbeError, "filesystem-observation.*before launch"):
                session.command(command)
            self.assertIs(session.loader, current)
            self.assertEqual(session.observations_used, 64)
            self.assertEqual((budget.runs, session.processes_used), counts)
            self.assertEqual((budget.started, budget.deadline), (started, deadline))
            self.assertTrue(budget.closed)
        self.assert_clean(session)

    def test_immutable_view_process_totals_cross_capsules_replay_and_failure_without_reset(self):
        budget = ProbeBudget(Limits(processes=2, descendants=9))
        self.add("Makefile", "VALUE := $(shell printf %s genuine)\nall: ;\n")
        base = self.capture_view(budget)
        self.add("unrelated.txt", "current")
        current = self.capture_view(budget)
        started, deadline = budget.started, budget.deadline
        with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
            cached = Command(("/usr/bin/printf", "first"))
            first = session.command(cached)
            self.assertIs(session.command(cached), first)
            self.assertEqual(session.processes_used, 1)
            producer = Command(("/usr/bin/printf", "%s", "genuine"))
            produced = session.command(producer)
            self.assertEqual(produced.stdout, b"genuine")
            # Both slots belong to parked Make/helper processes. A real,
            # already completed pure result needs no third live process.
            observations = [
                session.make("all", variables=("VALUE",), commands={"printf %s genuine": producer})
                for _ in range(2)
            ]
            for observed in observations:
                self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "genuine")
                self.assertEqual(len(observed.events), 1)
            self.assertEqual(observations[0].semantics, observations[1].semantics)
            self.assertIs(session.command(producer), produced)
            self.assertEqual(session.processes_used, 6)
            with self.assertRaisesRegex(MakeProbeError, "descendant-process"):
                with session.select_view(base):
                    session.command(Command(("/usr/bin/printf", "second")))
                    self.assertEqual(session.processes_used, 7)
                    session.command(Command((
                        "/usr/bin/python3", "-c",
                        "import os\nfor n in range(2):\n"
                        " child=os.fork()\n if child==0: os._exit(0)\n os.waitpid(child,0)\n",
                    )))
            self.assertEqual(session.processes_used, 9)
            self.assertEqual(session.live_process_peak, 2)
            self.assertEqual((budget.started, budget.deadline), (started, deadline))
            self.assertIs(session.loader, current)
            self.assertTrue(budget.closed)
            self.assertFalse(budget.children)
            with self.assertRaisesRegex(MakeProbeError, "deadline/budget"):
                session.command(cached)
            self.assertEqual(session.processes_used, 9)
        self.assert_clean(session)

    def test_immutable_view_directory_types_and_file_sources_follow_selected_namespace(self):
        for current_directory in (False, True):
            with self.subTest(current_directory=current_directory):
                budget = ProbeBudget()
                self.add("data/base.txt", "base")
                directory_view = self.capture_view(budget)
                (self.root / "data/base.txt").unlink()
                (self.root / "data").rmdir()
                del self.entries["data/base.txt"]
                self.add("data", "regular file")
                file_view = self.capture_view(budget)
                current, base = (directory_view, file_view) if current_directory else (file_view, directory_view)
                listing = Command(
                    ("/usr/bin/python3", "-c", "import os;print(' '.join(os.listdir('data')))"),
                    directories=("data",),
                )
                read = Command(("/usr/bin/python3", "-c", "print(open('data').read())"), sources=("data",))
                with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
                    before = session.command(listing if current_directory else read)
                    self.assertEqual(before.stdout, b"base.txt\n" if current_directory else b"regular file\n")
                    with session.select_view(base):
                        good = session.command(read if current_directory else listing)
                        self.assertEqual(good.stdout, b"regular file\n" if current_directory else b"base.txt\n")
                        runs = budget.runs
                        with self.assertRaisesRegex(MakeProbeError, "not an active directory|resolves no regular inputs"):
                            session.command(listing if current_directory else read)
                        self.assertEqual(budget.runs, runs)
                    self.assertIs(session.loader, current)
                    self.assertTrue(budget.closed)
                self.assert_clean(session)
                (self.root / "data").unlink()
                del self.entries["data"]

    def test_immutable_view_explicit_enumeration_does_not_grant_member_contents(self):
        self.add("data/base.txt", "base")
        budget = ProbeBudget()
        base = self.capture_view(budget)
        (self.root / "data/base.txt").unlink()
        del self.entries["data/base.txt"]
        self.add("data/current.txt", "current")
        current = self.capture_view(budget)
        command = Command(
            ("/usr/bin/python3", "-c", "import os;print(' '.join(sorted(os.listdir('data'))))"),
            directories=("data",),
        )
        with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
            first = session.command(command)
            self.assertEqual(first.stdout, b"current.txt\n")
            with session.select_view(base):
                selected = session.command(command)
                self.assertEqual(selected.stdout, b"base.txt\n")
                self.assertEqual(selected.consumed, ())
                self.assertIs(session.command(command), selected)
            self.assertEqual(session.command(command).stdout, first.stdout)
            with self.assertRaisesRegex(MakeProbeError, "undeclared source read"):
                with session.select_view(base):
                    session.command(Command(
                        ("/usr/bin/python3", "-c", "print(open('data/base.txt').read())"), directories=("data",),
                    ))
            self.assertIs(session.loader, current)
        self.assert_clean(session)

    def test_immutable_view_python_negative_probes_follow_current_base_and_declared_code(self):
        self.add("reader.py", "import os\nprint(int(os.path.exists('__init__.py')))\n")
        self.add("__init__.py", "base")
        budget = ProbeBudget()
        base = self.capture_view(budget)
        (self.root / "__init__.py").unlink()
        del self.entries["__init__.py"]
        current = self.capture_view(budget)
        self.add("__init__.py", "live-only")
        command = Command(("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",))
        with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
            absent = session.command(command)
            self.assertEqual(absent.stdout, b"0\n")
            with session.select_view(base):
                allowed = session.command(replace(command, code=("reader.py", "__init__.py")))
                self.assertEqual(allowed.stdout, b"1\n")
                self.assertIn("__init__.py", allowed.code_consumed)
                with self.assertRaisesRegex(MakeProbeError, "undeclared source"):
                    session.command(command)
            self.assertIs(session.loader, current)
            self.assertTrue(budget.closed)
        self.assert_clean(session)

    def test_immutable_view_gitlink_pins_paths_and_empty_roots_keep_accounting(self):
        module, (old_pin, new_pin) = self.gitlink_fixture()
        budget = ProbeBudget()
        base = self.gitlink_loader(budget, module / ".git", old_pin, path="oldlib")
        self.gitlink_git(self.root, "update-index", "--force-remove", "oldlib")
        current = self.gitlink_loader(budget, module / ".git", new_pin, path="newlib")
        tree = self.gitlink_git(module, "mktree", input=b"")
        empty_pin = self.gitlink_git(module, "-c", "commit.gpgsign=false", "commit-tree", tree, "-m", "empty")
        self.gitlink_git(self.root, "update-index", "--force-remove", "newlib")
        empty = self.gitlink_loader(budget, module / ".git", empty_pin)
        started, deadline = budget.started, budget.deadline
        with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
            original = session.snapshot
            self.assertEqual(session.snapshot.files["newlib/include/value.h"], b'#define VALUE "current"\n')
            before = budget.runs, dict(budget.bytes), session.processes_used
            with session.select_view(base):
                self.assertFalse((session.tree / "newlib").exists())
                output = session.command(Command(
                    ("/usr/bin/python3", "-c", "print(open('oldlib/include/value.h').read(),end='')"),
                    sources=("oldlib/include/value.h",),
                ))
                self.assertEqual(output.stdout, b'#define VALUE "base"\n')
                self.assertEqual(output.consumed, ("oldlib/include/value.h",))
                self.assertEqual(session.snapshot.owners(("oldlib",)), [("oldlib", "160000", old_pin)])
                self.assertGreater(budget.runs, before[0])
                self.assertGreater(budget.bytes["snapshot"], before[1]["snapshot"])
                self.assertGreater(session.processes_used, before[2])
                with session.select_view(empty):
                    listing = session.command(Command(
                        ("/usr/bin/python3", "-c", "import os;print(' '.join(sorted(os.listdir('.'))))"),
                        directories=(".",),
                    ))
                    self.assertEqual(listing.stdout, b"Makefile module\n")
                    self.assertTrue((session.tree / "module").is_dir())
                    self.assertEqual(session.snapshot.owners(("module",)), [("module", "160000", empty_pin)])
                    self.assertFalse((session.tree / "oldlib").exists())
                self.assertIs(session.loader, base)
            self.assertIs(session.snapshot, original)
            self.assertFalse((session.tree / "oldlib").exists())
            self.assertEqual((budget.started, budget.deadline), (started, deadline))
        self.assert_clean(session)

    def test_immutable_view_revalidates_complete_guest_buffers_status_flags_and_namespace(self):
        self.add("data/module.py", "VALUE=1\n")
        self.add("reader.py", (
            "import ctypes,json\n"
            "libc=ctypes.CDLL(None,use_errno=True);libc.syscall.restype=ctypes.c_long\n"
            "path=ctypes.c_char_p(b'data/module.py');results=[]\n"
            "for number,flags,mask,size in ((332,256,8191,256),(439,512,2,0),(89,0,0,32)):\n"
            " buffer=ctypes.create_string_buffer(bytes([165])*size,size) if size else None\n"
            " target=ctypes.byref(buffer) if size else None\n"
            " if number==332: args=(ctypes.c_long(-100),path,ctypes.c_ulong(flags),ctypes.c_ulong(mask),target)\n"
            " elif number==439: args=(ctypes.c_long(-100),path,ctypes.c_ulong(mask),ctypes.c_ulong(flags))\n"
            " else: args=(path,target,ctypes.c_ulong(size))\n"
            " ctypes.set_errno(0);result=libc.syscall(ctypes.c_long(number),*args)\n"
            " results.append([number,flags,mask,result if result>=0 else -ctypes.get_errno(),"
            " '' if buffer is None else buffer.raw.hex()])\n"
            "print(json.dumps(results))\n"
        ))
        budget = ProbeBudget()
        loader = self.capture_view(budget)
        command = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py", "data/module.py"),
        )
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            first = session.command(command)
            with session.select_view(loader):
                selected = session.command(command)
                self.assertIsNot(selected, first)
                self.assertFalse(session._metadata_matches(first.metadata))
                returned = json.loads(selected.stdout)
                for number, flags, mask, status, data in returned:
                    with self.subTest(number=number):
                        record = next(row for row in selected.metadata
                                      if row[:4] == (number, "/repo/data/module.py", flags, mask))
                        self.assertEqual(record[6], status)
                        self.assertEqual(record[8], data)
                        self.assertEqual(record[7], "a5" * record[4])
                statx = bytes.fromhex(returned[0][4])
                self.assertEqual(returned[0][3], 0)
                self.assertTrue(int.from_bytes(statx[:4], "little") & 4096)
                self.assertGreater(int.from_bytes(statx[144:152], "little"), 0)
                self.assertEqual(int.from_bytes(statx[16:20], "little"), 2)
                self.assertIn(returned[1][3], (-errno.EACCES, -errno.EROFS))
                self.assertEqual(returned[2][3], -errno.EINVAL)
                record = next(row for row in selected.metadata if row[0] == 332 and row[3] == 8191)
                for offset in (20, 24, 144):
                    changed = bytearray.fromhex(record[8])
                    changed[offset] ^= 1
                    self.assertFalse(session._metadata_matches(((*record[:8], changed.hex()),)))
            self.assertFalse(session._metadata_matches(first.metadata))
            restored = session.command(command)
            self.assertEqual(int.from_bytes(bytes.fromhex(json.loads(restored.stdout)[0][4])[16:20], "little"), 1)
        self.assert_clean(session)

    def test_immutable_view_nonregular_namespaces_cannot_become_false_absence(self):
        for kind in ("symlink", "gitlink"):
            with self.subTest(kind=kind):
                budget = ProbeBudget()
                self.add("reader.py", "import os\nprint(int(os.path.exists('data/__init__.py')))\n")
                self.add("data/module.py", "value=1\n")
                current = self.capture_view(budget)
                (self.root / "data/module.py").unlink()
                (self.root / "data").rmdir()
                del self.entries["data/module.py"]
                if kind == "symlink":
                    (self.root / "data").symlink_to("missing")
                    base = self.capture_view(budget)
                else:
                    module, (pin, _) = self.gitlink_fixture()
                    self.gitlink_git(self.root, "update-index", "--force-remove", "data/module.py")
                    base = self.gitlink_loader(budget, module / ".git", pin, path="data", admit=False)
                command = Command(
                    ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",),
                )
                with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
                    self.assertEqual(
                        session.command(replace(command, code=("reader.py", "data/module.py"))).stdout, b"0\n",
                    )
                    with self.assertRaisesRegex(MakeProbeError, "nonregular candidate source"):
                        with session.select_view(base):
                            session.command(command)
                    self.assertIs(session.loader, current)
                    self.assertTrue(budget.closed)
                self.assert_clean(session)
                if kind == "symlink":
                    (self.root / "data").unlink()

    def test_immutable_view_failed_closed_and_misnested_scopes_never_reactivate_authority(self):
        for failure in ("closed", "failed", "misnested"):
            with self.subTest(failure=failure):
                self.add("value", "same")
                budget = ProbeBudget()
                loader = self.capture_view(budget)
                with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
                    original = session.snapshot, session.cache
                    command = Command(("/usr/bin/printf", "cached"))
                    first = session.command(command)
                    if failure == "misnested":
                        outer, inner = session.select_view(loader), session.select_view(loader)
                        outer.__enter__()
                        inner.__enter__()
                        with self.assertRaisesRegex(MakeProbeError, "nesting order"):
                            outer.__exit__(None, None, None)
                        self.assert_clean(session)
                        inner.__exit__(None, None, None)
                        self.assert_clean(session)
                        continue
                    with session.select_view(loader):
                        self.assertIsNot(session.command(command), first)
                        if failure == "closed":
                            budget.close()
                        else:
                            with self.assertRaisesRegex(MakeProbeError, "owned failure"):
                                budget.reject("owned failure")
                    self.assertEqual((session.snapshot, session.cache), original)
                    before = budget.runs, budget.states, dict(budget.bytes)
                    with self.assertRaisesRegex(MakeProbeError, "deadline/budget"):
                        with session.select_view(loader):
                            self.fail("failed or closed view reactivated")
                    with self.assertRaisesRegex(MakeProbeError, "deadline/budget"):
                        session.command(command)
                    self.assertEqual((budget.runs, budget.states, budget.bytes), before)
                self.assert_clean(session)

    def test_immutable_view_rejects_active_execution_and_other_workers(self):
        for boundary in ("execution", "worker"):
            with self.subTest(boundary=boundary):
                self.add("value", "same")
                budget = ProbeBudget()
                loader = self.capture_view(budget)
                with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
                    original = session.loader, session.snapshot, session.tree, session.cache
                    states = budget.states
                    if boundary == "execution":
                        remaining, checked = budget.remaining, []
                        def guarded_remaining():
                            if budget.children and not checked:
                                checked.append(True)
                                with self.assertRaisesRegex(MakeProbeError, "active report execution"):
                                    with session.select_view(loader):
                                        self.fail("view changed with a live owned child")
                            return remaining()
                        with patch.object(budget, "remaining", guarded_remaining):
                            self.assertEqual(budget.run(["/usr/bin/true"], env=ENVIRONMENT).returncode, 0)
                        self.assertEqual(checked, [True])
                        self.assertFalse(budget.failed)
                    else:
                        errors = []
                        def worker():
                            try:
                                with session.select_view(loader):
                                    self.fail("another worker selected a view")
                            except BaseException as error:
                                errors.append(error)
                        thread = threading.Thread(target=worker)
                        thread.start()
                        thread.join(timeout=5)
                        self.assertFalse(thread.is_alive())
                        self.assertEqual(len(errors), 1)
                        self.assertIsInstance(errors[0], MakeProbeError)
                        self.assertIn("one bounded execution worker", str(errors[0]))
                        self.assertTrue(budget.failed)
                    self.assertEqual((session.loader, session.snapshot, session.tree, session.cache), original)
                    self.assertEqual(budget.states, states)
                    self.assertFalse(session._views)
                self.assert_clean(session)

    def view_exit_observer(self, session, backing):
        state = {name: getattr(session, name) for name in (
            "loader", "snapshot", "tree", "cache", "mappings", "native_tools", "make_runtime", "base", "budget",
        )}
        cache = {key: tuple(values) for key, values in session.cache.items()}
        stack = tuple(session._views)
        files = {path: path.stat() for path in backing}
        children = tuple(session.budget.children)
        handlers = dict(session.handlers)
        signals = {sig: signal.getsignal(sig) for sig in handlers}
        counts = session.budget.runs, session.budget.states, dict(session.budget.bytes)
        def observe():
            return {
                "view_identity": all(getattr(session, name) is value for name, value in state.items()),
                "cache_contents": {key: tuple(values) for key, values in state["cache"].items()} == cache,
                "view_stack": tuple(session._views) == stack,
                "backing": all(path.is_file() and path.stat() == info for path, info in files.items()),
                "children": tuple(session.budget.children) == children,
                "handlers": session.handlers == handlers,
                "signals": {sig: signal.getsignal(sig) for sig in signals} == signals,
                "accounting": (session.budget.runs, session.budget.states, session.budget.bytes) == counts,
                "budget_failed": session.budget.failed,
                "budget_closed": session.budget.closed,
            }
        return observe

    def test_immutable_view_foreign_exit_preserves_correct_owner_unwind(self):
        for exit_kind in ("normal", "exceptional", "misnested"):
            with self.subTest(exit_kind=exit_kind):
                budget = ProbeBudget()
                self.add("value", "base")
                base = self.capture_view(budget)
                self.add("value", "current")
                current = self.capture_view(budget)
                reader = Command(
                    ("/usr/bin/python3", "-c", "print(open('value').read())"), sources=("value",),
                )
                errors, observed, contexts, unwind_errors = [], {}, [], []
                foreign_error = RuntimeError("foreign exception delivery")
                owner_error = RuntimeError("correct owner exception")
                with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
                    original = session.loader, session.snapshot, session.tree, session.cache
                    self.assertEqual(session.command(reader).stdout, b"current\n")
                    backing = [session.tree / "value"]
                    outer = session.select_view(base)
                    outer.__enter__()
                    contexts.append(outer)
                    self.assertEqual(session.command(reader).stdout, b"base\n")
                    backing.append(session.tree / "value")
                    if exit_kind == "misnested":
                        inner = session.select_view(current)
                        inner.__enter__()
                        contexts.append(inner)
                        self.assertEqual(session.command(reader).stdout, b"current\n")
                        backing.append(session.tree / "value")
                    selected_roots = [path.parent.parent for path in backing[1:]]
                    observe = self.view_exit_observer(session, backing)
                    def foreign():
                        try:
                            outer.__exit__(
                                *((RuntimeError, foreign_error, None)
                                  if exit_kind == "exceptional" else (None, None, None)),
                            )
                        except BaseException as error:
                            errors.append(error)
                        finally:
                            observed.update(observe())
                    thread = threading.Thread(target=foreign)
                    try:
                        thread.start()
                        thread.join(timeout=10)
                        self.assertFalse(thread.is_alive())
                    finally:
                        for context in reversed(contexts):
                            try:
                                if exit_kind == "exceptional" and context is outer:
                                    self.assertFalse(context.__exit__(RuntimeError, owner_error, None))
                                else:
                                    self.assertFalse(context.__exit__(None, None, None))
                            except BaseException as error:
                                unwind_errors.append(error)
                    restored = (
                        (session.loader, session.snapshot, session.tree, session.cache) == original
                        and all(not path.exists() for path in selected_roots)
                    )
                self.assert_clean(session)
                self.assertEqual(observed, {
                    "view_identity": True, "cache_contents": True, "view_stack": True,
                    "backing": True, "children": True, "handlers": True, "signals": True,
                    "accounting": True, "budget_failed": True, "budget_closed": False,
                }, {"exit_kind": exit_kind, "observed_before_owner_unwind": observed,
                    "foreign_errors": [str(error) for error in errors],
                    "foreign_cleanup_errors": [getattr(error, "cleanup_errors", ()) for error in errors]})
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], MakeProbeError)
                self.assertIn("one bounded execution worker", str(errors[0]))
                self.assertEqual(unwind_errors, [])
                self.assertTrue(restored)

    def test_immutable_view_foreign_exit_during_command_preserves_backing_and_cache_owner(self):
        budget = ProbeBudget()
        self.add("value", "base")
        base = self.capture_view(budget)
        self.add("value", "current")
        current = self.capture_view(budget)
        reader = Command(
            ("/usr/bin/python3", "-c", "print(open('value').read())"), sources=("value",),
        )
        command = Command((
            "/usr/bin/python3", "-c",
            "import os,time\nfrom pathlib import Path\nvalue=Path('value').read_text()\n"
            "Path('/work/start-value').write_text(value)\n"
            "os.link('/work/start-value','/work/started')\n"
            "while not Path('/work/release').exists(): time.sleep(0.01)\n"
            "print(value)\n",
        ), sources=("value",))
        paused, attacked = threading.Event(), threading.Event()
        work, observed, errors, coordination_errors = [], {}, [], []
        owner_error, output = None, None
        with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
            original = session.loader, session.snapshot, session.tree, session.cache
            self.assertEqual(session.command(reader).stdout, b"current\n")
            current_cache = session.cache
            current_contents = {key: tuple(values) for key, values in current_cache.items()}
            context = session.select_view(base)
            context.__enter__()
            self.assertEqual(session.command(reader).stdout, b"base\n")
            backing = (original[2] / "value", session.tree / "value")
            run, remaining = session._sandbox_run, budget.remaining
            def capture_work(root, **kwargs):
                self.assertEqual(kwargs["mode"], "command")
                self.assertEqual(kwargs["argv"][-1], command.argv[-1])
                work.append(Path(next(mount["source"] for mount in kwargs["mounts"] if mount["target"] == "/work")))
                return run(root, **kwargs)
            def pause_owner():
                if budget.children and not paused.is_set():
                    paused.set()
                    if not attacked.wait(timeout=15):
                        raise AssertionError("foreign exit did not finish while the owner was paused")
                return remaining()
            def foreign():
                try:
                    if not paused.wait(timeout=10):
                        raise AssertionError("owner did not reach an actual registered child")
                    deadline = time.monotonic() + 10
                    while not (work[0] / "started").is_file():
                        if time.monotonic() >= deadline:
                            raise AssertionError("real BASE command did not publish its start marker")
                        attacked.wait(timeout=0.01)
                    self.assertEqual((work[0] / "started").read_text(), "base")
                    children = tuple(budget.children)
                    self.assertEqual(len(children), 1)
                    self.assertIsNone(children[0].poll())
                    observe = self.view_exit_observer(session, backing)
                    try:
                        context.__exit__(None, None, None)
                    except BaseException as error:
                        errors.append(error)
                    observed.update(observe())
                    observed["registered_child_alive"] = children[0].poll() is None
                    observed["lifetime_pipe_open"] = not children[0].stdin.closed
                except BaseException as error:
                    coordination_errors.append(error)
                finally:
                    if work and work[0].is_dir():
                        (work[0] / "release").write_text("resume")
                    attacked.set()
            thread = threading.Thread(target=foreign)
            try:
                thread.start()
                with patch.object(session, "_sandbox_run", capture_work), patch.object(budget, "remaining", pause_owner):
                    try:
                        output = session.command(command)
                    except MakeProbeError as error:
                        owner_error = error
            finally:
                attacked.set()
                thread.join(timeout=15)
                context.__exit__(None, None, None)
            joined = not thread.is_alive()
            restored = (session.loader, session.snapshot, session.tree, session.cache) == original
            cache_unchanged = {key: tuple(values) for key, values in current_cache.items()} == current_contents
        self.assert_clean(session)
        self.assertTrue(joined)
        self.assertEqual(coordination_errors, [])
        self.assertEqual(observed, {
            "view_identity": True, "cache_contents": True, "view_stack": True,
            "backing": True, "children": True, "handlers": True, "signals": True,
            "accounting": True, "budget_failed": True, "budget_closed": False,
            "registered_child_alive": True, "lifetime_pipe_open": True,
        }, {"observed_before_owner_resumption": observed,
            "owner_output": None if output is None else output.stdout,
            "current_cache_unchanged": cache_unchanged})
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], MakeProbeError)
        self.assertIn("one bounded execution worker", str(errors[0]))
        self.assertIsInstance(owner_error, MakeProbeError)
        self.assertIn("deadline/budget", str(owner_error))
        self.assertIsNone(output)
        self.assertTrue(restored)
        self.assertTrue(cache_unchanged)

    def test_immutable_view_teardown_defers_signal_until_previous_state_is_restored(self):
        from scripts.validation_ownership import make_probe
        self.add("value", "same")
        budget = ProbeBudget()
        loader = self.capture_view(budget)
        remove = make_probe._remove_owned_tree
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            original = session.loader, session.snapshot, session.tree, session.cache
            def interrupted_remove(path):
                if path.name.startswith("view-"):
                    signal.raise_signal(signal.SIGTERM)
                return remove(path)
            with patch.object(make_probe, "_remove_owned_tree", interrupted_remove):
                with self.assertRaises(KeyboardInterrupt):
                    with session.select_view(loader):
                        selected_root = session.tree.parent
            self.assertFalse(selected_root.exists())
            self.assertEqual((session.loader, session.snapshot, session.tree, session.cache), original)
            self.assertFalse(session._views)
            self.assertTrue(budget.failed)
            self.assertTrue(budget.closed)
        self.assert_clean(session)

    def test_registered_python_reexec_rejects_before_replacement_startup(self):
        replacement = (
            "import json,sys\n"
            "open('/work/reexecuted','w').write(json.dumps("
            "[sys.flags.isolated,sys.flags.no_site,sys.flags.dont_write_bytecode]))\n"
        )
        operations = (
            ("first-launch", "", None),
            ("execve", "os.execve('/usr/bin/python3',argv,environment)", "post-bootstrap exec"),
            ("no-startup-flags", "os.execve('/usr/bin/python3',argv[0:1]+argv[2:],environment)", "post-bootstrap exec"),
            ("runtime-alias", "os.execve('/bin/python3',argv,environment)", "post-bootstrap exec"),
            ("raw-execve", "libc.syscall(59,b'/usr/bin/python3',vector,envp)", "post-bootstrap exec"),
            ("fork-execve",
             "child=os.fork()\n"
             "if child:\n"
             " _,status=os.waitpid(child,0)\n"
             " os._exit(os.waitstatus_to_exitcode(status))\n"
             "os.execve('/usr/bin/python3',argv,environment)", "post-bootstrap exec"),
            ("execveat-path", "libc.syscall(322,-100,b'/usr/bin/python3',vector,envp,0)", "unadmitted syscall 322"),
            ("execveat-fd",
             "descriptor=os.open('/usr/bin/python3',os.O_RDONLY)\n"
             "libc.syscall(322,descriptor,b'',vector,envp,0x1000)", "unadmitted syscall 322"),
        )
        for name, operation, rejected in operations:
            with self.subTest(entry=name):
                self.add("launch.py", (
                    "import ctypes,json,os,sys\n"
                    "flags=[sys.flags.isolated,sys.flags.no_site,sys.flags.dont_write_bytecode]\n"
                    "open('/work/initial','w').write(json.dumps(flags))\n"
                    "environment={key:value for key,value in os.environ.items() if key!='PYTHONDONTWRITEBYTECODE'}\n"
                    "argv=" + repr(["/usr/bin/python3", "-S", "-c", replacement]) + "\n"
                    "vector=(ctypes.c_char_p*(len(argv)+1))(*(value.encode() for value in argv),None)\n"
                    "envp=(ctypes.c_char_p*(len(environment)+1))("
                    "*(f'{key}={value}'.encode() for key,value in environment.items()),None)\n"
                    "libc=ctypes.CDLL(None)\n" + operation + "\n"
                ))
                markers = {}
                session = self.session()
                with session:
                    run = session._sandbox_run
                    def observing(root, **kwargs):
                        try:
                            return run(root, **kwargs)
                        finally:
                            for path in session.base.glob("command-*/output/*"):
                                markers[path.name] = json.loads(path.read_bytes())
                    with patch.object(session, "_sandbox_run", observing):
                        command = Command(("/usr/bin/python3", "/repo/launch.py"), code=("launch.py",))
                        if rejected is None:
                            self.assertEqual(session.command(command).stdout, b"")
                        else:
                            with self.assertRaisesRegex(MakeProbeError, rejected):
                                session.command(command)
                    self.assertEqual(markers, {"initial": [1, 1, 1]})
                self.assert_clean(session)

    def test_registered_native_reexec_and_inherited_entry_paths_reject(self):
        self.add("reexec.c", r'''
#define _GNU_SOURCE
#include <fcntl.h>
#include <sched.h>
#include <signal.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <unistd.h>
extern char **environ;
static int mark(const char *path) {
    int fd=open(path,O_CREAT|O_WRONLY,0600);
    if(fd<0) return 1;
    return write(fd,"owned",5)!=5 || close(fd);
}
static int launch(void *unused) {
    char *args[]={"/native/tool","child",0};
    (void)unused;
    execve(args[0],args,environ);
    _exit(7);
}
int main(int argc,char **argv) {
    int status,fd; pid_t child;
    char *args[]={"/native/tool","child",0};
    if(argc!=2) return 1;
    if(!strcmp(argv[1],"child")) return mark("/work/reexecuted");
    if(mark("/work/initial")) return 2;
    if(!strcmp(argv[1],"initial")) return 0;
    if(!strcmp(argv[1],"execve")) return launch(0);
    if(!strcmp(argv[1],"execveat")) {
        syscall(SYS_execveat,AT_FDCWD,args[0],args,environ,0); return 3;
    }
    if(!strcmp(argv[1],"execveat-fd")) {
        fd=open(args[0],O_RDONLY);
        if(fd<0) return 3;
        syscall(SYS_execveat,fd,"",args,environ,AT_EMPTY_PATH); return 3;
    }
    if(!strcmp(argv[1],"fork")) child=fork();
    else if(!strcmp(argv[1],"vfork")) child=vfork();
    else if(!strcmp(argv[1],"clone-vfork")) {
        void *stack=mmap(0,65536,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);
        if(stack==MAP_FAILED) return 4;
        child=clone(launch,(char *)stack+65536,CLONE_VM|CLONE_VFORK|SIGCHLD,0);
    } else return 4;
    if(child<0) return 5;
    if(!child) return launch(0);
    if(waitpid(child,&status,0)!=child || status) return 6;
    return 0;
}
''')
        for entry in ("initial", "execve", "fork", "vfork", "clone-vfork", "execveat", "execveat-fd"):
            with self.subTest(entry=entry):
                markers = {}
                session = self.session()
                with session:
                    tool = session.compile_native(("reexec.c",))
                    run = session._sandbox_run
                    def observing(root, **kwargs):
                        try:
                            return run(root, **kwargs)
                        finally:
                            for path in session.base.glob("command-*/output/*"):
                                markers[path.name] = path.read_bytes()
                    with patch.object(session, "_sandbox_run", observing):
                        if entry == "initial":
                            self.assertEqual(session.native(tool, (entry,)).stdout, b"")
                        else:
                            expected = "unadmitted syscall 322" if entry.startswith("execveat") else "post-bootstrap exec"
                            with self.assertRaisesRegex(MakeProbeError, expected):
                                session.native(tool, (entry,))
                    self.assertEqual(markers, {"initial": b"owned"})
                self.assert_clean(session)

    def test_trusted_startup_vectors_exclude_owned_prefix_hooks(self):
        prefix = self.directory / "owned-python"
        venv.EnvBuilder(with_pip=False, symlinks=True).create(prefix)
        site = next(prefix.glob("lib/python*/site-packages"))
        markers = [self.directory / "pth-ran", self.directory / "sitecustomize-ran"]
        (site / "owned_hook.pth").write_text(
            "import sys,builtins; builtins.open(" + repr(str(markers[0]))
            + ",'w').write('owned pth'); sys.path.insert(0," + repr(str(site)) + ")\n"
        )
        (site / "sitecustomize.py").write_text(
            "open(" + repr(str(markers[1])) + ",'w').write('owned sitecustomize')\n"
        )
        recipe = subprocess.check_output(
            ["/usr/bin/make", "--no-print-directory", "-n", "-f",
             "scripts/validation_ownership/foundation.mk", "ownership-probe-check"],
            cwd=ROOT, env=ENVIRONMENT, text=True,
        )
        vectors = {"make-entry": shlex.split(recipe.strip())}
        documentation = (ROOT / "docs/ownership-probe-foundation.md").read_text()
        vectors["documented-entry"] = shlex.split(next(
            line for line in documentation.splitlines()
            if line.startswith("/usr/bin/python3 ") and "isolated_launcher.py" in line
        ))
        original = subprocess.Popen
        launched = []
        def recording(argv, *args, **kwargs):
            launched.append(list(argv))
            return original(argv, *args, **kwargs)
        self.add("Makefile", "all: ;\n")
        with patch("subprocess.Popen", recording):
            with self.session() as session:
                run = session._sandbox_run
                def capsule(root, **kwargs):
                    if kwargs["argv"][0] == "/usr/bin/python3":
                        vectors["registered-python"] = list(kwargs["argv"])
                    return run(root, **kwargs)
                with patch.object(session, "_sandbox_run", capsule):
                    session.command(Command(("/usr/bin/python3", "-c", "print('registered')")))
        self.assert_clean(session)
        for filename in ("lifecycle.py", "sandbox_exec.py"):
            path = str(TRUSTED_ROOT / filename)
            argv = next(argv for argv in launched if path in argv)
            script = argv.index(path)
            interpreter = max(index for index in range(script) if argv[index] == "/usr/bin/python3")
            vectors[filename] = argv[interpreter:script + 1]
        version = next(argv for argv in launched if any(
            argument.startswith("import sys; print('%d.%d'") for argument in argv
        ))
        query = next(index for index, argument in enumerate(version) if argument.startswith("import sys; print('%d.%d'"))
        interpreter = max(index for index in range(query) if version[index] == "/usr/bin/python3")
        vectors["version-query"] = version[interpreter:query + 1]
        for name, argv in vectors.items():
            with self.subTest(entry=name):
                tail = argv[1:] + (["--help"] if name in {"make-entry", "documented-entry"} else [])
                for no_site in (False, True):
                    for marker in markers:
                        marker.unlink(missing_ok=True)
                    options = tail if no_site else [argument for argument in tail if argument != "-S"]
                    result = subprocess.run(
                        [str(prefix / "bin/python"), *options], cwd=ROOT, env=ENVIRONMENT,
                        capture_output=True, timeout=15,
                    )
                    self.assertEqual([marker.exists() for marker in markers], [not no_site]*2, result.stderr)
                    if no_site and name not in {"lifecycle.py", "sandbox_exec.py"}:
                        self.assertEqual(result.returncode, 0, result.stderr)
                    if not no_site and name in {"make-entry", "documented-entry", "lifecycle.py", "sandbox_exec.py"}:
                        self.assertNotEqual(result.returncode, 0)

    def test_session_teardown_defers_signals_until_owned_state_is_removed(self):
        from scripts.validation_ownership import make_probe
        self.add("Makefile", "all: ;\n")
        for signum in (signal.SIGINT, signal.SIGTERM):
            for has_primary in (False, True):
                with self.subTest(signal=signum, primary=has_primary):
                    session = self.session()
                    primary = MakeProbeError("owned primary failure") if has_primary else None
                    seen = []
                    base = None
                    previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
                    previous_handler = signal.getsignal(signum)
                    def caller_handler(received, frame):
                        seen.append((
                            received, session.base is None, not self.scratch.exists(),
                            not session.cache, not session.budget.children,
                            signal.pthread_sigmask(signal.SIG_BLOCK, ()) == previous_mask,
                        ))
                        raise RuntimeError("owned deferred termination")
                    signal.signal(signum, caller_handler)
                    remove = make_probe._remove_owned_tree
                    def interrupted_remove(path, *args, **kwargs):
                        if base is not None and Path(path) == base:
                            os.kill(os.getpid(), signum)
                        return remove(path, *args, **kwargs)
                    try:
                        with self.assertRaises(MakeProbeError if has_primary else RuntimeError) as caught:
                            with patch.object(make_probe, "_remove_owned_tree", interrupted_remove):
                                with session:
                                    base = session.base
                                    session.command(Command(("/usr/bin/printf", "cached")))
                                    if primary is not None:
                                        raise primary
                        if has_primary:
                            self.assertIs(caught.exception, primary)
                            self.assertTrue(primary.cleanup_errors)
                        self.assertEqual(seen, [(signum, True, True, True, True, True)])
                        self.assertIs(signal.getsignal(signum), caller_handler)
                        self.assert_clean(session)
                    finally:
                        signal.signal(signum, previous_handler)

    def test_cleanup_finishes_all_actions_and_replays_pending_signals_afterward(self):
        from scripts.validation_ownership.lifecycle import finish_cleanup
        files = [self.directory / "first", self.directory / "second"]
        for path in files:
            path.touch()
        primary = MakeProbeError("original operation failure")
        seen = []
        previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
        def handler(signum, frame):
            seen.append((signum, not any(path.exists() for path in files)))
            raise KeyboardInterrupt("deferred handler")
        for signum in previous:
            signal.signal(signum, handler)
        def first():
            files[0].unlink()
            for signum in previous:
                os.kill(os.getpid(), signum)
            raise OSError(errno.EIO, "owned cleanup diagnostic")
        try:
            finish_cleanup([first, files[1].unlink], primary=primary)
            self.assertEqual(set(seen), {(signal.SIGINT, True), (signal.SIGTERM, True)})
            self.assertEqual(len(primary.cleanup_errors), 3)
            self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, ()), previous_mask)
        finally:
            for signum, value in previous.items():
                signal.signal(signum, value)

    def test_per_call_teardown_defers_signals_and_preserves_primary_errors(self):
        from scripts.validation_ownership import make_probe
        self.add("Makefile", "all: ;\n")
        for boundary in ("report", "command-tree", "make-tree"):
            for failed in (False, True):
                with self.subTest(boundary=boundary, failed=failed):
                    self.add("Makefile", (
                        "$(error owned primary failure)\nall: ;\n"
                        if boundary == "make-tree" and failed else "all: ;\n"
                    ))
                    session = self.session()
                    sent = False
                    with session:
                        base = session.base
                        remove, unlink = make_probe._remove_owned_tree, Path.unlink
                        def signal_once():
                            nonlocal sent
                            if not sent:
                                sent = True
                                os.kill(os.getpid(), signal.SIGTERM)
                        def removing(path, *args, **kwargs):
                            name = Path(path).name
                            if boundary == "command-tree" and name.startswith("command-"):
                                signal_once()
                            if boundary == "make-tree" and name.startswith("control-"):
                                signal_once()
                            return remove(path, *args, **kwargs)
                        def unlinking(path, *args, **kwargs):
                            if boundary == "report" and path.parent == base and path.name.startswith("report-"):
                                signal_once()
                            return unlink(path, *args, **kwargs)
                        with patch.object(make_probe, "_remove_owned_tree", removing), patch.object(Path, "unlink", unlinking):
                            with self.assertRaises(MakeProbeError if failed else KeyboardInterrupt) as caught:
                                if boundary == "make-tree":
                                    session.make("all")
                                else:
                                    session.command(Command((
                                        "/usr/bin/python3", "-c", "import os; os._exit(7)" if failed else "print('ok')",
                                    )))
                        self.assertTrue(sent)
                        self.assertFalse(list(base.glob("report-*.json")))
                        self.assertFalse(list(base.glob("launch-*.json")))
                        self.assertFalse(list(base.glob("command-*")))
                        self.assertFalse(list(base.glob("make-root-*")))
                        self.assertFalse(list(base.glob("control-*")))
                        self.assertFalse(session.budget.children)
                        if failed:
                            self.assertIn(
                                "owned primary failure" if boundary == "make-tree" else "confined",
                                str(caught.exception),
                            )
                            self.assertTrue(caught.exception.cleanup_errors)
                    self.assert_clean(session)

    def test_partial_call_setup_interruption_removes_preallocated_owned_paths(self):
        self.add("Makefile", "all: ;\n")
        for make in (False, True):
            with self.subTest(make=make):
                session = self.session()
                with session:
                    def partial(name, **kwargs):
                        (session.base / name).mkdir()
                        os.kill(os.getpid(), signal.SIGINT)
                    with patch.object(session, "_new_root", partial):
                        with self.assertRaises(KeyboardInterrupt):
                            session.make("all") if make else session.command(Command(("/usr/bin/printf", "ok")))
                    self.assertFalse(list(session.base.glob("*root-*")))
                    self.assertFalse(list(session.base.glob("command-*")))
                    self.assertFalse(list(session.base.glob("control-*")))
                self.assert_clean(session)

    def test_setup_failure_remains_primary_during_deferred_exit_signal(self):
        from scripts.validation_ownership import make_probe
        self.add("Makefile", "all: ;\n")
        session = self.session()
        primary = MakeProbeError("owned setup failure")
        seen = []
        previous = signal.getsignal(signal.SIGTERM)
        def handler(signum, frame):
            seen.append(session.base is None and not self.scratch.exists())
            raise KeyboardInterrupt("deferred setup exit")
        signal.signal(signal.SIGTERM, handler)
        remove = make_probe._remove_owned_tree
        def removing(path, *args, **kwargs):
            if session.base is not None and Path(path) == session.base:
                os.kill(os.getpid(), signal.SIGTERM)
            return remove(path, *args, **kwargs)
        try:
            with patch.object(session, "_tools", side_effect=primary), patch.object(make_probe, "_remove_owned_tree", removing):
                with self.assertRaises(MakeProbeError) as caught:
                    with session:
                        self.fail("failed setup entered")
            self.assertIs(caught.exception, primary)
            self.assertEqual(seen, [True])
            self.assertTrue(primary.cleanup_errors)
            self.assert_clean(session)
        finally:
            signal.signal(signal.SIGTERM, previous)

    def test_budget_cleanup_defers_signal_until_children_and_pipes_are_closed(self):
        for has_primary in (False, True):
            with self.subTest(primary=has_primary):
                budget = ProbeBudget(Limits(process_output_bytes=8 if has_primary else 1024))
                stopped = []
                observed = []
                descriptors = set(os.listdir("/proc/self/fd"))
                previous = signal.getsignal(signal.SIGINT)
                def handler(signum, frame):
                    child = stopped[0]
                    observed.append((
                        not budget.children, child.returncode is not None,
                        child.stdin.closed and child.stdout.closed and child.stderr.closed,
                        set(os.listdir("/proc/self/fd")) == descriptors,
                    ))
                    raise KeyboardInterrupt("owned budget teardown signal")
                signal.signal(signal.SIGINT, handler)
                stop = budget._terminate
                def stopping(child, privileged=False):
                    stopped.append(child)
                    os.kill(os.getpid(), signal.SIGINT)
                    stop(child, privileged)
                try:
                    with patch.object(budget, "_terminate", stopping):
                        with self.assertRaises(MakeProbeError if has_primary else KeyboardInterrupt) as caught:
                            budget.run(
                                ["/usr/bin/python3", "-I", "-S", "-c",
                                 "import os; os.read(0,10); os.write(1,b'x'*100)"],
                                env=ENVIRONMENT, input_data=b"owned",
                            )
                    self.assertEqual(observed, [(True, True, True, True)])
                    self.assertTrue(budget.failed)
                    if has_primary:
                        self.assertIn("output exceeds", str(caught.exception))
                        self.assertTrue(caught.exception.cleanup_errors)
                finally:
                    signal.signal(signal.SIGINT, previous)

    def test_cleanup_preserves_a_callers_already_blocked_signal_mask(self):
        from scripts.validation_ownership.lifecycle import finish_cleanup
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        previous_handler = signal.getsignal(signal.SIGTERM)
        seen = []
        signal.signal(signal.SIGTERM, lambda signum, frame: seen.append(signum))
        path = self.directory / "owned"
        path.touch()
        def removing():
            os.kill(os.getpid(), signal.SIGTERM)
            path.unlink()
        try:
            finish_cleanup([removing])
            self.assertFalse(path.exists())
            self.assertEqual(seen, [])
            self.assertIn(signal.SIGTERM, signal.sigpending())
            self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, ()), previous_mask | {signal.SIGTERM})
        finally:
            signal.sigtimedwait({signal.SIGTERM}, 0)
            signal.signal(signal.SIGTERM, previous_handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    def test_budget_payload_does_not_inherit_the_temporary_setup_mask(self):
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, ())
        try:
            for added in (set(), {signal.SIGUSR1}):
                expected = previous_mask | added
                signal.pthread_sigmask(signal.SIG_SETMASK, expected)
                budget = ProbeBudget()
                result = budget.run(
                    ["/usr/bin/python3", "-I", "-S", "-c",
                     "import json,signal; print(json.dumps(sorted(signal.pthread_sigmask(signal.SIG_BLOCK,()))))"],
                    env=ENVIRONMENT,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), sorted(expected))
                self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, ()), expected)
                self.assertFalse(budget.children)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)

    def test_watchdog_teardown_delivers_signal_after_sole_reaping(self):
        program = r'''
import os,signal,sys,time
sys.path.insert(0,sys.argv[1])
from scripts.validation_ownership import lifecycle
seen=[]
state={}
def handler(signum,frame):
    assert state["child"].returncode == 0
    seen.append(signum)
    raise RuntimeError("deferred caller signal")
signal.signal(signal.SIGTERM,handler)
original=lifecycle.terminate
def terminating(child):
    state["child"]=child
    os.kill(os.getpid(),signal.SIGTERM)
    original(child)
lifecycle.terminate=terminating
try:
    lifecycle.run(["/usr/bin/true"],time.monotonic()+5)
except RuntimeError as error:
    assert str(error) == "deferred caller signal"
else:
    raise AssertionError("termination was ignored")
assert seen == [signal.SIGTERM]
assert lifecycle.owned_children() == []
print("delivered after reaping")
'''
        with self.owned_process(["/usr/bin/python3", "-I", "-S", "-c", program, str(ROOT)]) as (child, descriptor):
            child.wait(timeout=10)
            self.assertEqual(child.returncode, 0, child.stderr.read())
            self.assertEqual(child.stdout.read(), b"delivered after reaping\n")

    def test_default_termination_is_delivered_only_after_owned_session_removal(self):
        self.add("Makefile", "all: ;\n")
        program = r'''
import os,signal,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from scripts.validation_ownership.authority import AuthorityLoader,GitTreeEntries,GitTreeEntry
from scripts.validation_ownership.budget import ProbeBudget
from scripts.validation_ownership.make_probe import ProbeSession
from scripts.validation_ownership import make_probe
root,scratch=map(Path,sys.argv[2:4])
budget=ProbeBudget()
entries=GitTreeEntries({"Makefile":GitTreeEntry("Makefile","100644","blob","0"*40)},budget=budget)
session=ProbeSession(AuthorityLoader(root,entries,budget=budget),scratch_root=scratch,budget=budget)
# This control targets real file/state teardown and the default OS signal
# action; tool/namespace execution is exercised by the other process cases.
session._tools=lambda:None
session._compile_interceptor=lambda:None
signal.signal(signal.SIGTERM,signal.SIG_DFL)
original=make_probe._remove_owned_tree
def removing(path,*args,**kwargs):
    if session.base is not None and Path(path)==session.base:
        os.kill(os.getpid(),signal.SIGTERM)
    return original(path,*args,**kwargs)
make_probe._remove_owned_tree=removing
with session:
    pass
raise AssertionError("default termination was lost")
'''
        with self.owned_process([
            "/usr/bin/python3", "-I", "-S", "-c", program, str(ROOT), str(self.root), str(self.scratch),
        ]) as (child, descriptor):
            child.wait(timeout=10)
            self.assertEqual(child.returncode, -signal.SIGTERM, child.stderr.read())
        self.assertFalse(self.scratch.exists())
        self.assertEqual((self.root / "Makefile").read_text(), "all: ;\n")

    def test_directory_declaration_cannot_authorize_regular_file_content(self):
        self.add("secret.txt", "undeclared fixture bytes")
        for boundary in ("admission", "capsule"):
            with self.subTest(boundary=boundary):
                with self.session() as session:
                    runs = session.budget.runs
                    admission = session._directories if boundary == "admission" else lambda paths: paths
                    with patch.object(session, "_directories", admission):
                        with self.assertRaisesRegex(MakeProbeError, "director"):
                            session.command(Command(
                                ("/usr/bin/python3", "-c", "print(open('secret.txt').read())"),
                                directories=("secret.txt",),
                            ))
                    self.assertEqual(session.budget.runs == runs, boundary == "admission")
                self.assert_clean(session)

    def static_metadata_fixture(self):
        fields = (
            "st_mode", "st_ino", "st_dev", "st_nlink", "st_uid", "st_gid", "st_size",
            "st_atime_ns", "st_mtime_ns", "st_ctime_ns", "st_rdev", "st_blksize", "st_blocks",
        )
        self.add("code/module.py", "VALUE=1\n")
        self.add("data/value", b"actual source")
        self.add("reader.py", (
            "import json,os\n"
            f"fields={fields!r}\n"
            "result={name:{field:getattr(os.stat(name),field) for field in fields}"
            " for name in ('code','data','data/value')}\n"
            "with open('data/value','rb') as source:\n"
            " result['bytes']=source.read().hex()\n"
            " result['fstat']={field:getattr(os.fstat(source.fileno()),field) for field in fields}\n"
            "print(json.dumps(result,sort_keys=True,separators=(',',':')))\n"
        ))
        self.add("Makefile", "VALUE := $(shell python3 reader.py)\nall: ;\n")
        return Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py", "code/module.py"),
            sources=("data/value",),
        ), fields

    def test_static_metadata_uses_persistent_objects_for_cache_and_native_make(self):
        command, fields = self.static_metadata_fixture()
        with self.session() as session:
            before = {
                name: {field: getattr((session.tree / name).stat(), field) for field in fields}
                for name in ("code", "data", "data/value")
            }
            first = session.command(command)
            actual = json.loads(first.stdout)
            self.assertEqual(actual["code"]["st_ino"], before["code"]["st_ino"])
            self.assertEqual(actual["data"]["st_ino"], before["data"]["st_ino"])
            self.assertEqual(actual["data/value"]["st_ino"], before["data/value"]["st_ino"])
            self.assertEqual(actual["fstat"], actual["data/value"])
            self.assertEqual(first.consumed, ("data/value",))
            self.assertTrue({"/repo/code", "/repo/data", "/repo/data/value"} <= {
                record[1] for record in first.metadata
            })
            runs, charged = session.budget.runs, session.budget.bytes["control"]
            self.assertIs(session.command(command), first)
            self.assertGreater(session.budget.runs, runs)
            self.assertGreater(session.budget.bytes["control"], charged)
            observed = session.make("all", variables=("VALUE",), commands={"python3 reader.py": command})
            self.assertEqual(json.loads(observed.semantics["domains"]["VALUE"]["value"]), actual)
            self.assertTrue(all(event["match"] >= 0 for event in observed.events))
            after = {
                name: {field: getattr((session.tree / name).stat(), field) for field in fields}
                for name in before
            }
            self.assertEqual(after, before)
        self.assert_clean(session)

    def test_metadata_captures_complete_syscall_buffers_status_flags_and_masks(self):
        self.add("data/module.py", "VALUE=1\n")
        self.add("reader.py", (
            "import ctypes,json,os\n"
            "libc=ctypes.CDLL(None,use_errno=True); libc.syscall.restype=ctypes.c_long\n"
            "fd=os.open('data',os.O_RDONLY|os.O_DIRECTORY)\nresults=[]\n"
            "path=ctypes.c_char_p(b'data/module.py')\n"
            "for number,flags,mask,size in "
            "((4,0,0,144),(6,0,0,144),(5,0,0,144),(262,256,0,144),"
            "(332,256,2047,256),(332,0,8191,256),(138,0,0,120),"
            "(21,0,0,0),(269,0,4,0),(439,512,2,0),(89,0,0,32),(267,0,0,32)):\n"
            " buffer=ctypes.create_string_buffer(bytes([165])*size,size) if size else None\n"
            " target=ctypes.byref(buffer) if size else None\n"
            " if number in (4,6): args=(path,target)\n"
            " elif number in (5,138): args=(ctypes.c_long(fd),target)\n"
            " elif number==262: args=(ctypes.c_long(-100),path,target,ctypes.c_ulong(flags))\n"
            " elif number==332: args=(ctypes.c_long(-100),path,ctypes.c_ulong(flags),ctypes.c_ulong(mask),target)\n"
            " elif number==21: args=(path,ctypes.c_ulong(flags))\n"
            " elif number==269: args=(ctypes.c_long(-100),path,ctypes.c_ulong(mask))\n"
            " elif number==439: args=(ctypes.c_long(-100),path,ctypes.c_ulong(mask),ctypes.c_ulong(flags))\n"
            " elif number==89: args=(path,target,ctypes.c_ulong(size))\n"
            " else: args=(ctypes.c_long(-100),path,target,ctypes.c_ulong(size))\n"
            " ctypes.set_errno(0); result=libc.syscall(ctypes.c_long(number),*args)\n"
            " results.append([number,flags,mask,result if result>=0 else -ctypes.get_errno(),"
            " '' if buffer is None else buffer.raw.hex()])\n"
            "os.close(fd); print(json.dumps(results))\n"
        ))
        command = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py", "data/module.py"),
        )
        with self.session() as session:
            output = session.command(command)
            returned = json.loads(output.stdout)
            for number, flags, mask, status, data in returned:
                path = "/repo/data" if number in {5, 138} else "/repo/data/module.py"
                with self.subTest(number=number, flags=flags, mask=mask):
                    self.assertTrue(any(
                        record[:4] == (number, path, flags, mask)
                        and record[6] == status and record[8] == data
                        for record in output.metadata
                    ))
            self.assertEqual([row[3] for row in returned[:9]], [0]*9)
            self.assertIn(returned[9][3], (-errno.EACCES, -errno.EROFS))
            self.assertEqual([row[3] for row in returned[10:]], [-errno.EINVAL]*2)
            run, diagnostics = session._sandbox_run, []
            def capture(root, **kwargs):
                result = run(root, **kwargs)
                if kwargs.get("metadata_validation"):
                    diagnostics.append(result[0].stderr)
                return result
            with patch.object(session, "_sandbox_run", capture):
                stable = tuple(record for record in output.metadata if record[0] not in {138, 332})
                self.assertTrue(session._metadata_matches(stable), diagnostics)
                # statx mount IDs belong to each guest namespace, not the
                # persistent source inode. Full returned buffers stay checked.
                namespace = tuple(record for record in output.metadata if record[0] == 332)
                self.assertEqual(len(namespace), 2)
                for record in namespace:
                    with self.subTest(statx_flags=record[2], statx_mask=record[3]):
                        data = bytearray.fromhex(record[8])
                        self.assertNotEqual(data[144:152], b"\xff"*8)
                        data[144:152] = b"\xff"*8
                        changed = record[:8] + (data.hex(),) + record[9:]
                        self.assertFalse(session._metadata_matches((changed,)))
                # fstatfs includes shared filesystem capacity, not immutable
                # source-only state. Production cache validation keeps this row.
                filesystem = tuple(record for record in output.metadata if record[0] == 138)
                self.assertEqual(len(filesystem), 1)
                with (self.directory / "filesystem-change").open("wb") as allocation:
                    os.posix_fallocate(allocation.fileno(), 0, 1024*1024)
                    os.fsync(allocation.fileno())
                    self.assertFalse(session._metadata_matches(filesystem))
                    fresh = session.command(command)
                    self.assertIsNot(fresh, output)
                    self.assertNotEqual(
                        next(row[4] for row in json.loads(fresh.stdout) if row[0] == 138),
                        next(row[4] for row in returned if row[0] == 138),
                    )
        self.assert_clean(session)

    def test_metadata_transport_preserves_mixed_syscalls_cache_and_replay(self):
        self.add("data/module.py", "VALUE=1\n")
        self.add("reader.py", (
            "import ctypes,json,os\n"
            "libc=ctypes.CDLL(None,use_errno=True); libc.syscall.restype=ctypes.c_long\n"
            "fd=os.open('data',os.O_RDONLY|os.O_DIRECTORY)\nresults=[]\n"
            "path=ctypes.c_char_p(b'data/module.py')\n"
            "for number,flags,mask,size in "
            "((4,0,0,144),(6,0,0,144),(5,0,0,144),(262,256,0,144),"
            "(332,256,2047,256),(332,0,8191,256),(138,0,0,120),"
            "(21,0,0,0),(269,0,4,0),(439,512,2,0),(89,0,0,32),(267,0,0,32)):\n"
            " buffer=ctypes.create_string_buffer(bytes([165])*size,size) if size else None\n"
            " target=ctypes.byref(buffer) if size else None\n"
            " if number in (4,6): args=(path,target)\n"
            " elif number in (5,138): args=(ctypes.c_long(fd),target)\n"
            " elif number==262: args=(ctypes.c_long(-100),path,target,ctypes.c_ulong(flags))\n"
            " elif number==332: args=(ctypes.c_long(-100),path,ctypes.c_ulong(flags),ctypes.c_ulong(mask),target)\n"
            " elif number==21: args=(path,ctypes.c_ulong(flags))\n"
            " elif number==269: args=(ctypes.c_long(-100),path,ctypes.c_ulong(mask))\n"
            " elif number==439: args=(ctypes.c_long(-100),path,ctypes.c_ulong(mask),ctypes.c_ulong(flags))\n"
            " elif number==89: args=(path,target,ctypes.c_ulong(size))\n"
            " else: args=(ctypes.c_long(-100),path,target,ctypes.c_ulong(size))\n"
            " ctypes.set_errno(0); result=libc.syscall(ctypes.c_long(number),*args)\n"
            " results.append([number,flags,mask,result if result>=0 else -ctypes.get_errno(),"
            " '' if buffer is None else buffer.raw.hex()])\n"
            "os.close(fd); print(json.dumps(results))\n"
        ))
        command = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py", "data/module.py"),
        )
        with self.session() as session:
            output, report, report_bytes, charges = self.capture_supervisor_report(
                session, lambda: session.command(command),
            )
            returned = json.loads(output.stdout)
            for number, flags, mask, status, data in returned:
                path = "/repo/data" if number in {5, 138} else "/repo/data/module.py"
                with self.subTest(number=number, flags=flags, mask=mask):
                    self.assertTrue(any(
                        record[:4] == (number, path, flags, mask)
                        and record[6] == status and record[8] == data
                        for record in output.metadata
                    ))
            sizes = self.assert_metadata_transport(session, report, report_bytes, charges, output.metadata)
            self.assertGreater(sizes["control_saving_bytes"], 0)
            self.assertLess(sizes["envelope_bytes"], len(encoded(output.metadata)))
            stable = tuple(record for record in output.metadata if record[0] not in {138, 332})
            self.assertTrue(session._metadata_matches(stable))
            self.assertIsNot(session.command(command), output)
        self.assert_clean(session)

    def test_metadata_transport_preserves_directory_enumeration_offsets_and_savings(self):
        for index in range(40):
            self.add(f"texts/file{index:02d}.txt", f"value-{index}\n")
        self.add("reader.py", (
            "import ctypes,json,os\n"
            "libc=ctypes.CDLL(None,use_errno=True); libc.syscall.restype=ctypes.c_long\n"
            "fd=os.open('texts',os.O_RDONLY|os.O_DIRECTORY)\nresults=[]\n"
            "while True:\n"
            " buffer=ctypes.create_string_buffer(bytes([165])*4096,4096)\n"
            " ctypes.set_errno(0)\n"
            " result=libc.syscall(ctypes.c_long(217),ctypes.c_long(fd),ctypes.byref(buffer),ctypes.c_ulong(4096))\n"
            " results.append([result if result>=0 else -ctypes.get_errno(),buffer.raw.hex()])\n"
            " if result<=0:\n"
            "  break\n"
            "os.close(fd); print(json.dumps(results))\n"
        ))
        command = Command(("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), directories=("texts",))
        with self.session() as session:
            output, report, report_bytes, charges = self.capture_supervisor_report(
                session, lambda: session.command(command),
            )
            records = tuple(record for record in output.metadata if record[0] == 217)
            returned = json.loads(output.stdout)
            self.assertEqual(len(records), len(returned))
            self.assertEqual(records[0][1], "/repo/texts")
            self.assertEqual(records[0][5], 0)
            self.assertTrue(all(record[4] == 4096 for record in records))
            self.assertEqual([record[6] for record in records], [row[0] for row in returned])
            self.assertEqual(records[-1][6], 0)
            self.assertGreater(records[-1][5], 0)
            self.assertTrue(all(len(record[7]) == len(record[8]) == 2*record[4] for record in records))
            sizes = self.assert_metadata_transport(session, report, report_bytes, charges, output.metadata)
            self.assertGreater(sizes["control_saving_bytes"], 0)
            self.assertTrue(session._metadata_matches(output.metadata))
            self.assertIs(session.command(command), output)
        self.assert_clean(session)

    def test_metadata_transport_keeps_selected_view_metadata_boundaries(self):
        command, _ = self.static_metadata_fixture()
        budget = ProbeBudget()
        base = self.capture_view(budget)
        self.add("unrelated.txt", "current only")
        current = self.capture_view(budget)
        with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
            first = session.command(command)
            with session.select_view(base):
                self.assertFalse(session._metadata_matches(first.metadata))
                selected, report, report_bytes, charges = self.capture_supervisor_report(
                    session, lambda: session.command(command),
                )
                self.assert_metadata_transport(session, report, report_bytes, charges, selected.metadata)
                self.assertTrue(session._metadata_matches(selected.metadata))
                self.assertIs(session.command(command), selected)
            self.assertFalse(session._metadata_matches(first.metadata))
        self.assert_clean(session)

    def test_changed_metadata_forces_real_execution_without_mutating_validation(self):
        command, _ = self.static_metadata_fixture()
        with self.session() as session:
            first = session.command(command)
            path = session.tree / "data/value"
            status = path.stat()
            os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns + 1000000000))
            changed = path.stat()
            self.assertFalse(session._metadata_matches(first.metadata))
            self.assertEqual(path.stat(), changed)
            fresh = session.command(command)
            self.assertIsNot(fresh, first)
            self.assertEqual(json.loads(fresh.stdout)["data/value"]["st_mtime_ns"], changed.st_mtime_ns)
            self.assertIs(session.command(command), fresh)
            observed = session.make("all", variables=("VALUE",), commands={"python3 reader.py": command})
            self.assertEqual(json.loads(observed.semantics["domains"]["VALUE"]["value"]), json.loads(fresh.stdout))
            self.assertEqual(path.stat(), changed)
        self.assert_clean(session)

    def test_live_make_executes_uncomparable_metadata_instead_of_reusing_it(self):
        self.add("data/module.py", "VALUE=1\n")
        self.add("reader.py", (
            "import ctypes,json\nlibc=ctypes.CDLL(None,use_errno=True); libc.syscall.restype=ctypes.c_long\n"
            "ctypes.set_errno(0)\n"
            "result=libc.syscall(ctypes.c_long(4),ctypes.c_char_p(b'data/module.py'),ctypes.c_void_p(1))\n"
            "print(json.dumps([result,ctypes.get_errno()]))\n"
        ))
        self.add("Makefile", "VALUE := $(shell python3 reader.py)\nall: ;\n")
        command = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py", "data/module.py"),
        )
        with self.session() as session:
            first = session.command(command)
            self.assertEqual(json.loads(first.stdout), [-1, errno.EFAULT])
            self.assertTrue(any(record[0] == 4 and record[6] == -errno.EFAULT for record in first.metadata))
            self.assertIsNot(session.command(command), first)
            executed = []
            original = session._sandbox_run
            def recording(root, **kwargs):
                if kwargs["mode"] == "command" and "/repo/reader.py" in kwargs["argv"]:
                    executed.append(kwargs["argv"])
                return original(root, **kwargs)
            with patch.object(session, "_sandbox_run", recording):
                observed = session.make("all", variables=("VALUE",), commands={"python3 reader.py": command})
            self.assertEqual(json.loads(observed.semantics["domains"]["VALUE"]["value"]), [-1, errno.EFAULT])
            self.assertEqual(len(executed), 1)
        self.assert_clean(session)

    def test_public_live_and_immutable_controls_use_distinct_actual_source_bytes(self):
        self.add("localization.mk", (
            "LOCALIZATION_OUT_DIR := pinned\nlocalization-check: localization-generate\n"
            "localization-generate: ;\n"
        ))
        self.add("src/data/ch2_bundle.json", '[{"value":"pinned"}]')
        self.add("scripts/generated_data/registry.py", (
            "import json\nfrom pathlib import Path\n"
            "class Records(list): pass\n"
            "class Schema:\n name='chapterbundle'\n version=1\n"
            " def source_paths(self, source): return [str(Path(source)/'ch2_bundle.json')]\n"
            " def load_records(self, source):\n"
            "  path=Path(source)/'ch2_bundle.json'\n"
            "  records=Records(json.loads(path.read_text()))\n"
            "  records.source_paths=[str(path)]\n  return records\n"
            " def manifest_record_count(self, records): return len(records)\n"
            "class Registry:\n"
            " def resolve(self,name): return Schema()\n"
            "REGISTRY=Registry()\n"
        ))
        self.gitlink_git(self.root, "init", "--quiet")
        self.gitlink_git(self.root, "add", "--all")
        self.gitlink_git(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                         "commit", "--quiet", "-m", "pinned fixture")
        revision = self.gitlink_git(self.root, "rev-parse", "HEAD")
        self.add("localization.mk", (
            "LOCALIZATION_OUT_DIR := actual-live\nlocalization-check: localization-generate\n"
            "localization-generate: ;\n"
        ))
        self.add("src/data/ch2_bundle.json", '[{"value":"live"},{"value":"added"}]')
        results = []
        for arguments in (("--revision", revision), ("--worktree",)):
            result = subprocess.run(
                ["/usr/bin/python3", "-I", "-S", "-B", str(TRUSTED_ROOT / "isolated_launcher.py"),
                 "--repository-root", str(self.root), *arguments],
                cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            results.append(json.loads(result.stdout))
        self.assertEqual([result["generated_registry"]["record_count"] for result in results], [1, 2])
        self.assertEqual([
            result["make"]["semantics"]["domains"]["LOCALIZATION_OUT_DIR"]["value"] for result in results
        ], ["pinned", "actual-live"])
        self.assertEqual(results[1]["generated_registry"]["source_paths"], ["src/data/ch2_bundle.json"])
        self.assertNotEqual(results[0]["execution_snapshot"], results[1]["execution_snapshot"])

    def test_live_gitlink_capture_reads_initialized_live_bytes_without_head_substitution(self):
        module, pins = self.gitlink_fixture()
        self.gitlink_git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{pins[-1]},module")
        revision = self.gitlink_git(self.root, "write-tree")
        self.gitlink_git(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                         "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "admitted module")
        self.gitlink_git(self.root, "clone", "--quiet", "--no-hardlinks", str(module), "module")
        live_source = self.root / "module/include/value.h"
        live_source.write_text('#define VALUE "changed-live"\n')
        result = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", "-B", str(TRUSTED_ROOT / "isolated_launcher.py"),
             "--repository-root", str(self.root), "--worktree"],
            cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=30,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"nonempty live gitlink requires explicit source-path admission", result.stderr)
        budget = ProbeBudget()
        with self.assertRaisesRegex(MakeProbeError, "explicit source-path admission"):
            git_tree_entries(self.root, None, budget=budget)
        budget.close()
        budget = ProbeBudget()
        live_entries = GitTreeEntries({
            "module/include/value.h": GitTreeEntry("module/include/value.h", "100644", "blob", "0"*40),
        }, budget=budget)
        live = AuthorityLoader(self.root, live_entries, budget=budget)
        immutable_entries = git_tree_entries(
            self.root, revision, budget=budget,
            gitlinks=(GitlinkSource("module", module / ".git"),),
        )
        immutable = AuthorityLoader(self.root, immutable_entries, revision, budget=budget)
        self.assertEqual(live.read_blob("module/include/value.h", "live"), live_source.read_bytes())
        self.assertEqual(immutable.read_blob("module/include/value.h", "immutable"), b'#define VALUE "current"\n')
        with ProbeSession(live, scratch_root=self.scratch, budget=budget) as session:
            observed = session.command(Command(
                ("/usr/bin/python3", "-c", "print(open('module/include/value.h').read(),end='')"),
                sources=("module/include/value.h",),
            ))
            self.assertEqual(observed.stdout, live_source.read_bytes())
            self.assertEqual(observed.consumed, ("module/include/value.h",))
        self.assert_clean(session)

    def test_public_worktree_consumer_preserves_the_requested_live_mode(self):
        revision = self.gitlink_git(ROOT, "rev-parse", "HEAD")
        self.gitlink_git(ROOT, "clone", "--quiet", "--shared", "--no-checkout", str(ROOT), str(self.root))
        self.gitlink_git(self.root, "-c", "submodule.recurse=false",
                         "checkout", "--quiet", "--detach", revision)
        localization = self.root / "localization.mk"
        localization.write_text(
            localization.read_text() + "\nLOCALIZATION_OUT_DIR := live-candidate-proof\n",
        )
        result = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", "-B",
             str(TRUSTED_ROOT / "isolated_launcher.py"), "--repository-root", str(self.root), "--worktree"],
            cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["generated_registry"]["name"], "chapterbundle")
        self.assertEqual(
            observed["make"]["semantics"]["domains"]["LOCALIZATION_OUT_DIR"]["value"],
            "live-candidate-proof",
        )

    def test_live_inventory_keeps_head_admission_with_actual_deletions_and_modes(self):
        self.add(".gitignore", "ignored.txt\nbuild/\n")
        self.add("Makefile", (
            "VALUE := $(file <data/admitted.txt)\n"
            "EXCLUDED := $(wildcard data/new.txt data/staged.txt ignored.txt)\nall: ;\n"
        ))
        self.add("data/deleted.txt", "removed")
        self.add("data/admitted.txt", "pinned")
        self.capture_tree(ProbeBudget())
        self.gitlink_git(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                         "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "admitted paths")
        (self.root / "data/deleted.txt").unlink()
        (self.root / "data/new.txt").write_text("actual untracked bytes")
        (self.root / "data/staged.txt").write_text("actual staged bytes")
        self.gitlink_git(self.root, "add", "data/staged.txt")
        (self.root / "data/admitted.txt").write_text("actual admitted live bytes")
        (self.root / "data/admitted.txt").chmod(0o755)
        (self.root / "ignored.txt").write_text("not a source input")
        budget = ProbeBudget()
        entries = git_tree_entries(self.root, None, budget=budget)
        self.assertEqual(set(entries), {".gitignore", "Makefile", "data/deleted.txt", "data/admitted.txt"})
        loader = AuthorityLoader(self.root, entries, budget=budget)
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            listing = session.command(Command(
                ("/usr/bin/python3", "-c", "import os; print(' '.join(os.listdir('data')))"),
                directories=("data",),
            ))
            self.assertEqual(listing.stdout, b"admitted.txt\n")
            self.assertEqual(listing.consumed, ())
            self.assertEqual(session.snapshot.absent_paths, {"data/deleted.txt"})
            self.assertEqual(session.snapshot.modes["data/admitted.txt"], "100755")
            read = session.command(Command(
                ("/usr/bin/python3", "-c", "print(open('data/admitted.txt').read())"),
                sources=("data/admitted.txt",),
            ))
            self.assertEqual(read.stdout, b"actual admitted live bytes\n")
            self.assertEqual(read.consumed, ("data/admitted.txt",))
            self.assertTrue({"data/new.txt", "data/staged.txt", "ignored.txt"}.isdisjoint(session.snapshot.files))
            observed = session.make("all", variables=("VALUE", "EXCLUDED"))
            self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "actual admitted live bytes")
            self.assertEqual(observed.semantics["domains"]["EXCLUDED"]["value"], "")
        self.assert_clean(session)

    def test_live_admitted_type_changes_are_not_silently_regularized(self):
        self.add("input", "original")
        self.capture_tree(ProbeBudget())
        self.gitlink_git(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                         "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "admitted input")
        path = self.root / "input"
        path.unlink()
        for kind in ("directory", "fifo", "symlink"):
            with self.subTest(kind=kind):
                if kind == "directory":
                    path.mkdir()
                elif kind == "fifo":
                    os.mkfifo(path)
                else:
                    path.symlink_to(self.directory)
                budget = ProbeBudget()
                try:
                    if kind == "symlink":
                        entries = git_tree_entries(self.root, None, budget=budget)
                        self.assertEqual(entries["input"].mode, "120000")
                        loader = AuthorityLoader(self.root, entries, budget=budget)
                        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
                            with self.assertRaisesRegex(MakeProbeError, "nonregular"):
                                session.command(Command(("/usr/bin/python3", "-c", "open('input').read()")))
                        self.assert_clean(session)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "unsupported nonregular type"):
                            git_tree_entries(self.root, None, budget=budget)
                finally:
                    budget.close()
                    path.rmdir() if kind == "directory" else path.unlink()

    def test_live_gitlink_absence_empty_and_unsafe_namespaces_are_not_pin_placeholders(self):
        module, pins = self.gitlink_fixture()
        self.gitlink_git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{pins[-1]},module")
        self.gitlink_git(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                         "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "admitted module")
        budget = ProbeBudget()
        entries = git_tree_entries(self.root, None, budget=budget)
        absent = Snapshot(AuthorityLoader(self.root, entries, budget=budget), budget)
        self.assertIn("module", absent.absent_paths)
        self.assertNotIn("module", absent.gitlink_roots)
        budget.close()
        live = self.root / "module"
        live.mkdir()
        budget = ProbeBudget()
        entries = git_tree_entries(self.root, None, budget=budget)
        self.assertEqual(entries.live_directories, {"module"})
        loader = AuthorityLoader(self.root, entries, budget=budget)
        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
            listing = session.command(Command(
                ("/usr/bin/python3", "-c", "import os; print(os.listdir('module'))"),
                directories=("module",),
            ))
            self.assertEqual(listing.stdout, b"[]\n")
        self.assert_clean(session)
        (live / "unexpected.txt").write_text("not an initialized gitlink repository")
        budget = ProbeBudget()
        with self.assertRaisesRegex(MakeProbeError, "explicit source-path admission"):
            git_tree_entries(self.root, None, budget=budget)
        budget.close()
        (live / "unexpected.txt").unlink()
        live.rmdir()
        live.symlink_to(module)
        budget = ProbeBudget()
        with self.assertRaisesRegex(MakeProbeError, "not an actual directory"):
            git_tree_entries(self.root, None, budget=budget)
        with self.assertRaisesRegex(MakeProbeError, "does not substitute immutable"):
            git_tree_entries(
                self.root, None, budget=budget, gitlinks=(GitlinkSource("module", module / ".git"),),
            )
        budget.close()

    def test_live_gitlink_snapshot_rechecks_presence_type_and_empty_admission(self):
        module, pins = self.gitlink_fixture()
        self.add("Makefile", "VALUE := $(if $(wildcard module),present,absent)\nall: ;\n")
        self.gitlink_git(self.root, "add", "Makefile")
        self.gitlink_git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{pins[-1]},module")
        self.gitlink_git(self.root, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                         "-c", "commit.gpgsign=false", "commit", "--quiet", "-m", "admitted module")
        live = self.root / "module"
        for transition in ("unchanged", "absent", "file", "symlink", "nonempty", "appeared"):
            with self.subTest(transition=transition):
                budget = ProbeBudget(Limits(seconds=30))
                if transition != "appeared":
                    live.mkdir()
                try:
                    entries = git_tree_entries(self.root, None, budget=budget)
                    if transition == "appeared":
                        live.mkdir()
                    elif transition != "unchanged":
                        live.rmdir()
                        if transition == "file":
                            live.write_text("not a directory")
                        elif transition == "symlink":
                            live.symlink_to(module)
                        elif transition == "nonempty":
                            live.mkdir()
                            (live / "unadmitted.txt").write_text("not an admitted source")
                    loader = AuthorityLoader(self.root, entries, budget=budget)
                    if transition not in ("unchanged", "absent"):
                        with self.assertRaises(MakeProbeError):
                            Snapshot(loader, budget)
                    else:
                        with ProbeSession(loader, scratch_root=self.scratch, budget=budget) as session:
                            absent = transition == "absent"
                            self.assertEqual("module" in session.snapshot.absent_paths, absent)
                            self.assertEqual("module" in session.snapshot.gitlink_roots, not absent)
                            self.assertEqual((session.tree / "module").exists(), not absent)
                            result = session.make("all", variables=("VALUE",))
                            self.assertEqual(result.semantics["domains"]["VALUE"]["value"],
                                             "absent" if absent else "present")
                        self.assert_clean(session)
                finally:
                    budget.close()
                    if live.is_symlink() or live.is_file():
                        live.unlink()
                    elif live.is_dir():
                        unexpected = live / "unadmitted.txt"
                        if unexpected.exists():
                            unexpected.unlink()
                        live.rmdir()

    def test_make_uncaptured_runtime_file_cannot_select_a_false_absence_branch(self):
        path = f"/usr/lib/python{sys.version_info.major}.{sys.version_info.minor}/os.py"
        self.assertTrue(Path(path).is_file())
        self.add("Makefile", "VALUE := $(if $(wildcard " + path + "),selected,omitted)\n"
                 "all:\n\t@printf '%s\\n' '$(VALUE)'\n")
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(ordinary.stdout, b"selected\n")
        with self.session() as session:
            with self.assertRaisesRegex(
                MakeProbeError, "uncaptured Make runtime access: metadata " + re.escape(path),
            ):
                session.make("all", variables=("VALUE",))
        self.assert_clean(session)
        with self.session(runtime_files=(path,)) as session:
            observed = session.make("all", variables=("VALUE",))
            self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "selected")
        self.assert_clean(session)

    def test_make_runtime_probe_permissions_end_at_native_bootstrap(self):
        from scripts.validation_ownership.syscall_guard import Policy, Process, Violation
        closure = [path for path, _ in _make_runtime(ProbeBudget())]
        config = {
            "root": str(self.directory), "mode": "make", "code": [], "sources": [],
            "enumerations": [], "executables": ["/usr/bin/make"], "python_version": "3.12",
            "argv": [], "runtime_closure": closure, "forbidden_paths": [],
        }
        policy = Policy(config)
        state = Process("make")
        probes = (
            "/etc/ld.so.cache", "/etc/ld.so.preload",
            "/lib/x86_64-linux-gnu/glibc-hwcaps/x86-64-v3/libc.so.6",
        )
        for path in probes:
            policy.check(state, path, "metadata")
            policy.check(state, path, "read")
        state.observer_ready = True
        for path in probes:
            for operation in ("read", "metadata", "directory", "write"):
                with self.subTest(path=path, operation=operation):
                    with self.assertRaises(Violation):
                        policy.check(state, path, operation)
        for path in closure:
            policy.check(state, path, "metadata")
        for ready in (False, True):
            state.observer_ready = ready
            for path in ("/usr/lib/unrequested", "/usr/lib64/unrequested", "/lib/unrequested"):
                with self.assertRaisesRegex(Violation, "uncaptured Make runtime"):
                    policy.check(state, path, "read")
        state.observer_ready = False
        present = self.directory / probes[0].lstrip("/")
        present.parent.mkdir()
        present.write_text("not captured")
        with self.assertRaisesRegex(Violation, "uncaptured Make runtime"):
            policy.check(state, probes[0], "read")
        self.add("Makefile", "VALUE := $(wildcard /etc/ld.so.cache)\nall: ;\n")
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "uncaptured Make runtime"):
                session.make("all", variables=("VALUE",))
        self.assert_clean(session)

    def test_command_root_enumeration_requires_an_explicit_faithful_directory(self):
        self.add("hidden.txt", "exists")
        self.add("reader.py", (
            "import json,os\n"
            "print(json.dumps({'name':'selected' if 'hidden.txt' in os.listdir('.') else 'omitted',"
            "'version':1,'record_count':0,'source_paths':[]}))\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", "-B", "reader.py"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(json.loads(ordinary.stdout)["name"], "selected")
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "enumerat|directory"):
                session.registry(Command(("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",)))
        self.assert_clean(session)
        command = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), directories=(".",),
        )
        with self.session() as session:
            result = session.registry(command)
            self.assertEqual(result["name"], "selected")
            self.assertEqual(result["source_paths"], [])
        self.assert_clean(session)
        self.add("reader.py", "open('hidden.txt').read()\n")
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "undeclared source read"):
                session.command(command)
        self.assert_clean(session)

    def test_explicit_enumeration_rejects_incomplete_sparse_backing_and_undeclared_ancestors(self):
        self.add("hidden.txt", "exists")
        self.add("reader.py", "import os\nprint(os.listdir('.'))\n")
        with self.session() as session:
            run = session._sandbox_run
            def incomplete(root, **kwargs):
                work = root.parent / root.name.replace("command-root-", "command-")
                kwargs["mounts"][0] = session._mount(work / "tree", "/repo")
                return run(root, **kwargs)
            with patch.object(session, "_sandbox_run", incomplete):
                with self.assertRaisesRegex(MakeProbeError, "source backing"):
                    session.command(Command(
                        ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), directories=(".",),
                    ))
        self.assert_clean(session)
        self.add("pkg/code.py", "VALUE=7\n")
        self.add("reader.py", "import os\nprint(os.listdir('pkg'))\n")
        for directories in ((), (".",)):
            with self.subTest(directories=directories):
                with self.session() as session:
                    with self.assertRaisesRegex(MakeProbeError, "undeclared source directory"):
                        session.command(Command(
                            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py", "pkg/code.py"),
                            directories=directories,
                        ))
                self.assert_clean(session)
        with self.session() as session:
            result = session.command(Command(
                ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py", "pkg/code.py"),
                directories=("pkg",),
            ))
            self.assertEqual(result.stdout, b"['code.py']\n")
            self.assertIn("pkg/code.py", result.code_consumed)
        self.assert_clean(session)

    def test_explicit_enumeration_rejects_nonregular_namespaces(self):
        self.add("reader.py", "import os\nprint(os.listdir('data'))\n")
        self.add("data/real.txt", "real")
        (self.root / "data/link").symlink_to(self.directory)
        self.entries["data/link"] = GitTreeEntry("data/link", "120000", "blob", "0"*40)
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "nonregular namespace"):
                session.command(Command(
                    ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), directories=("data",),
                ))
        self.assert_clean(session)

    def test_python_existing_initializer_cannot_be_reported_as_sparse_absence(self):
        self.add("__init__.py", "")
        self.add("reader.py", (
            "import json,os\n"
            "print(json.dumps({'name':'selected' if os.path.exists('__init__.py') else 'omitted',"
            "'version':1,'record_count':0,'source_paths':[]}))\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", "-B", "reader.py"],
            cwd=self.root, env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(json.loads(ordinary.stdout)["name"], "selected")
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "undeclared source"):
                session.registry(Command(
                    ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",),
                ))
        self.assert_clean(session)

    def test_python_module_and_cache_probes_distinguish_real_absence_from_undeclared_inputs(self):
        paths = (
            "__init__.py", "__init__.pyc", "__init__.abi3.so",
            "__init__.cpython-312-x86_64-linux-gnu.so", "reader.pyc", "reader.abi3.so",
            "reader.cpython-312-x86_64-linux-gnu.so", "__pycache__",
            "__pycache__/reader.cpython-312.pyc", "__pycache__/reader.cpython-312.opt-1.pyc",
        )
        self.add("reader.py", (
            "import os,sys\nprint(int(os.path.exists(sys.argv[1])))\n"
        ))
        with self.session() as session:
            for path in paths:
                result = session.command(Command(
                    ("/usr/bin/python3", "/repo/reader.py", path), code=("reader.py",),
                ))
                self.assertEqual(result.stdout, b"0\n")
                self.assertEqual(result.consumed, ())
        self.assert_clean(session)
        for path in paths:
            with self.subTest(path=path):
                leaf = path + "/kept" if path == "__pycache__" else path
                self.add(leaf, "present")
                with self.session() as session:
                    with self.assertRaisesRegex(MakeProbeError, "undeclared source"):
                        session.command(Command(
                            ("/usr/bin/python3", "/repo/reader.py", path), code=("reader.py",),
                        ))
                self.assert_clean(session)
                (self.root / leaf).unlink()
                del self.entries[leaf]
                if "/" in leaf:
                    (self.root / leaf).parent.rmdir()
        self.add("reader.py", "import os\nos.path.exists('unrelated.py')\n")
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "undeclared source"):
                session.command(Command(("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",)))
        self.assert_clean(session)

    def test_python_negative_probes_reject_nonregular_namespaces(self):
        self.add("reader.py", "import os,sys\nprint(int(os.path.exists(sys.argv[1])))\n")
        for name, probe in (
            ("__init__.py", "__init__.py"),
            ("__pycache__", "__pycache__/reader.cpython-312.pyc"),
        ):
            with self.subTest(name=name):
                (self.root / name).symlink_to(self.directory)
                self.entries[name] = GitTreeEntry(name, "120000", "blob", "0"*40)
                with self.session() as session:
                    with self.assertRaisesRegex(MakeProbeError, "nonregular"):
                        session.command(Command(
                            ("/usr/bin/python3", "/repo/reader.py", probe), code=("reader.py",),
                        ))
                self.assert_clean(session)
                (self.root / name).unlink()
                del self.entries[name]

    def test_owned_cleanup_removes_deep_admissible_tree_without_recursion(self):
        from scripts.validation_ownership.make_probe import _remove_owned_tree
        path = self.directory / "deep"
        path.mkdir()
        created = [path]
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for _ in range(1050):
                os.mkdir("a", 0o700, dir_fd=descriptor)
                following = os.open("a", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = following
                path /= "a"
                created.append(path)
        finally:
            os.close(descriptor)
        self.assertLess(len(os.fsencode(path)), 4096)
        self.assertLess(len(created), Limits().created_files)
        recursion_limit = sys.getrecursionlimit()
        descriptors = set(os.listdir("/proc/self/fd"))
        opened = set()
        peak = 0
        original_open, original_close = os.open, os.close
        def opening(*args, **kwargs):
            nonlocal peak
            result = original_open(*args, **kwargs)
            opened.add(result)
            peak = max(peak, len(opened))
            return result
        def closing(descriptor):
            try:
                return original_close(descriptor)
            finally:
                opened.discard(descriptor)
        try:
            with patch("os.open", opening), patch("os.close", closing):
                _remove_owned_tree(created[0])
            self.assertFalse(created[0].exists())
            self.assertEqual(sys.getrecursionlimit(), recursion_limit)
            self.assertEqual(opened, set())
            self.assertLessEqual(peak, 2)
            self.assertEqual(set(os.listdir("/proc/self/fd")), descriptors)
        finally:
            for path in reversed(created):
                try:
                    os.rmdir(path)
                except FileNotFoundError:
                    pass

    def test_owned_cleanup_preserves_symlink_targets_permissions_and_replaced_entries(self):
        from scripts.validation_ownership.make_probe import _remove_owned_tree
        outside = self.directory / "outside"
        outside.mkdir()
        (outside / "kept").write_bytes(b"unrelated")
        root = self.directory / "owned"
        root.mkdir()
        (root / "link").symlink_to(outside, target_is_directory=True)
        (root / "file").write_bytes(b"owned")
        _remove_owned_tree(root)
        self.assertEqual((outside / "kept").read_bytes(), b"unrelated")
        alias = self.directory / "alias"
        alias.symlink_to(outside, target_is_directory=True)
        for path in (alias, alias / "nested"):
            with self.subTest(path=path):
                with self.assertRaises(OSError):
                    _remove_owned_tree(path)
                self.assertEqual((outside / "kept").read_bytes(), b"unrelated")
        alias.unlink()
        root.mkdir()
        (root / "child").mkdir()
        original = os.open
        swapped = False
        def replacing(path, *args, **kwargs):
            nonlocal swapped
            descriptor = original(path, *args, **kwargs)
            if path == "child" and not swapped:
                swapped = True
                (root / "child").rename(root / "moved")
                outside.rename(root / "child")
            return descriptor
        with patch("os.open", replacing):
            with self.assertRaisesRegex(OSError, "cleanup entry changed"):
                _remove_owned_tree(root)
        self.assertEqual((root / "child/kept").read_bytes(), b"unrelated")
        (root / "child").rename(outside)
        _remove_owned_tree(root)
        if os.geteuid() != 0:
            root.mkdir()
            locked = root / "locked"
            locked.mkdir()
            locked.chmod(0)
            try:
                with self.assertRaises(PermissionError):
                    _remove_owned_tree(root)
                self.assertEqual(stat.S_IMODE(locked.stat().st_mode), 0)
            finally:
                locked.chmod(0o700)
                _remove_owned_tree(root)

    def test_owned_cleanup_completes_actual_deep_native_output(self):
        from scripts.validation_ownership import make_probe
        self.add("deep.c", (
            "#include <stdio.h>\n#include <sys/stat.h>\n#include <unistd.h>\n"
            "int main(void) {\n int depth;\n if(chdir(\"/work\")) return 1;\n"
            " for(depth=0;depth<1050;++depth) {\n"
            "  if(mkdir(\"a\",0700)||chdir(\"a\")) return 2;\n }\n"
            " printf(\"%d\\n\",depth);\n return 0;\n}\n"
        ))
        observed = []
        with self.session() as session:
            tool = session.compile_native(("deep.c",))
            original = make_probe._remove_owned_tree
            def removing(path):
                leaf = Path(path) / "output" / Path(*(["a"]*1050))
                if Path(path).name.startswith("command-") and leaf.is_dir():
                    observed.append(len(os.fsencode(leaf)))
                return original(path)
            with patch.object(make_probe, "_remove_owned_tree", removing):
                result = session.native(tool)
            self.assertEqual(result.stdout, b"1050\n")
            self.assertEqual(len(observed), 1)
            self.assertLess(observed[0], 4096)
            self.assertLessEqual(session.files_created, session.budget.limits.created_files)
        self.assert_clean(session)

    def test_registry_driver_normalizes_only_repository_source_paths(self):
        self.add("data/value.json", "[1]")
        self.add("value.json", "[1,2]")
        self.add("scripts/generated_data/registry.py", (
            "import json\nfrom pathlib import Path\n"
            "class Records(list): pass\n"
            "class Schema:\n"
            " version=1\n"
            " def __init__(self,name): self.name=name\n"
            " def load_records(self,source):\n"
            "  path=Path(source)/'value.json'\n"
            "  records=Records(json.loads(path.read_text()))\n"
            "  reported=path\n"
            "  if self.name=='relative': reported=path.relative_to('/repo')\n"
            "  elif self.name=='parent': reported='/repo/../outside/value.json'\n"
            "  elif self.name=='outside': reported='/outside/value.json'\n"
            "  records.source_paths=[str(reported)]\n"
            "  return records\n"
            " def manifest_record_count(self,records): return len(records)\n"
            "class Registry:\n"
            " def resolve(self,name): return Schema(name)\n"
            "REGISTRY=Registry()\n"
        ))
        driver = (TRUSTED_ROOT / "generated_registry_probe.py").read_text()
        for name, source, accepted in (
            ("absolute", "data", True), ("relative", "data", True),
            ("relative", ".", True),
            ("parent", "data", False), ("outside", "data", False),
            ("absolute", "/repo/data", False), ("absolute", "data/../data", False),
        ):
            with self.subTest(name=name, source=source):
                with self.session() as session:
                    source_path = "value.json" if source == "." else "data/value.json"
                    command = Command(
                        ("/usr/bin/python3", "-I", "-S", "-B", "-c", driver, name, source),
                        code=("scripts/generated_data/registry.py",), sources=(source_path,),
                        directories=(".", "scripts", "scripts/generated_data"),
                    )
                    if accepted:
                        observed = session.registry(command)
                        self.assertEqual(observed, {
                            "name": name, "version": 1, "record_count": 2 if source == "." else 1,
                            "source_paths": [source_path],
                        })
                    else:
                        with self.assertRaises(MakeProbeError):
                            session.registry(command)
                self.assert_clean(session)

    def test_registry_gitlinks_share_only_the_common_directory_lookup(self):
        from scripts.validation_ownership.consumer import registry_entries

        module, pins = self.gitlink_fixture()
        for name, pin in zip(("first", "second"), pins):
            self.gitlink_git(self.root, "update-index", "--add", "--cacheinfo", f"160000,{pin},{name}")
            self.gitlink_git(
                self.root, "clone", "--quiet", "--bare", "--shared", str(module),
                str(self.root / ".git/modules" / name),
            )
        revision = self.gitlink_git(self.root, "write-tree")
        budget = ProbeBudget()
        run, lookups = budget.run, []

        def observe(argv, **kwargs):
            if tuple(argv[-2:]) == ("rev-parse", "--git-common-dir"):
                lookups.append(tuple(argv))
            return run(argv, **kwargs)

        try:
            with patch.object(budget, "run", observe):
                entries = registry_entries(self.root, revision, budget)
            loader = AuthorityLoader(self.root, entries, revision, budget=budget)
            self.assertEqual(loader.read_blob("first/include/value.h", "first"), b'#define VALUE "base"\n')
            self.assertEqual(loader.read_blob("second/include/value.h", "second"), b'#define VALUE "current"\n')
            self.assertEqual([entries[name].object_id for name in ("first", "second")], pins)
            self.assertEqual(len(lookups), 1)
        finally:
            budget.close()
        self.assertFalse(budget.children)

    def test_generated_registry_uses_actual_structured_and_sequence_schema_counts(self):
        from scripts.generated_data.registry import REGISTRY
        from scripts.validation_ownership.consumer import registry_entries
        budget = ProbeBudget()
        scratch = self.directory / "registry-real"
        scratch.mkdir()
        loader = AuthorityLoader(
            ROOT, registry_entries(ROOT, "HEAD", budget), "HEAD",
            scratch_root=scratch, budget=budget,
        )
        driver = (TRUSTED_ROOT / "generated_registry_probe.py").read_text()
        with ProbeSession(loader, scratch_root=scratch, budget=budget) as session:
            code = tuple(sorted(
                path for path in session.snapshot.files
                if path.endswith(".py") and path.startswith(("scripts/generated_data/", "scripts/assets/"))
            ))
            directories = tuple(sorted({".", *(
                parent.as_posix() for name in code for parent in Path(name).parents
            )}))
            for name in ("autoplaystrategies", "shops"):
                with self.subTest(schema=name):
                    schema = REGISTRY.resolve(name)
                    source = schema.default_source
                    records = schema.load_records(str(ROOT / source))
                    expected = schema.manifest_record_count(records)
                    if name == "autoplaystrategies":
                        self.assertIsInstance(records, dict)
                        self.assertNotEqual(len(records), expected)
                    observed = session.registry(Command(
                        ("/usr/bin/python3", "-c", driver, name, source),
                        code=code, sources=(source,), directories=directories,
                    ))
                    self.assertEqual(observed["record_count"], expected)
                    self.assertEqual(observed["source_paths"], [source])
        self.assertFalse(session.cache)
        self.assertFalse(budget.children)
        self.assertEqual(list(scratch.iterdir()), [])
        scratch.rmdir()

    def test_python_command_root_enumeration_requires_declaration(self):
        self.add("data/value.txt", "captured")
        body = "import os;print(','.join(sorted(os.listdir('.'))))"
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "undeclared source directory enumeration"):
                session.command(python_command(session, body))
        self.assert_clean(session)
        with self.session() as session:
            result = session.command(directory_python_command(session, body, directories=(".",)))
            self.assertEqual(result.stdout, b"data\n")
        self.assert_clean(session)

    def test_python_command_tracks_repository_packages_outside_scripts(self):
        self.add("tools/pkg/helper.py", "VALUE=7\n")
        self.add("tools/pkg/producer.py", "from .helper import VALUE\n")
        self.add("tools/pkg/unrelated.py", "raise AssertionError('not imported')\n")
        with self.session() as session:
            command = python_command(
                session, "from tools.pkg.producer import VALUE;print(VALUE)",
                code=("tools/pkg/producer.py",),
            )
            result = session.command(command)
            self.assertEqual(result.stdout, b"7\n")
            self.assertNotIn("tools/pkg/unrelated.py", command.code)
        self.assert_clean(session)

    def test_registry_command_does_not_admit_unrelated_test_code(self):
        self.add("data/a_bundle.json", "{}")
        self.add("scripts/generated_data/tests/unrelated.py", "VALUE=1\n")
        source = (
            "import glob\nfrom pathlib import Path\n"
            "class Schema:\n"
            " def source_paths(self, source):\n"
            "  Path('/repo/scripts/generated_data/tests/unrelated.py').read_text()\n"
            "  return sorted(glob.glob(source + '/*_bundle.json'))\n"
            "class Registry:\n"
            " def resolve(self, name): return Schema()\n"
            "REGISTRY=Registry()\n"
        )
        self.add("scripts/generated_data/registry.py", source)
        with self.session() as session:
            command = generated_registry_source_paths_command(session, "chapterbundle", "data")
            with self.assertRaises(MakeProbeError):
                session.command(command)
        self.assert_clean(session)
        self.add(
            "scripts/generated_data/registry.py",
            source.replace("  Path('/repo/scripts/generated_data/tests/unrelated.py').read_text()\n", ""),
        )
        with self.session() as session:
            command = generated_registry_source_paths_command(session, "chapterbundle", "data")
            result = session.command(command)
            self.assertEqual(json.loads(result.stdout), ["data/a_bundle.json"])
            self.assertNotIn("scripts/generated_data/tests/unrelated.py", command.code)
        self.assert_clean(session)

    def test_directory_python_command_preserves_root_marker_and_rejects_aliases(self):
        self.add("data/value.txt", "captured")
        with self.session() as session:
            command = directory_python_command(
                session,
                "import json,os;from pathlib import Path;"
                "print(json.dumps([sorted(os.listdir('.')),Path('data/value.txt').read_text()]))",
                sources=("data/value.txt",), directories=(".", "data"),
            )
            result = session.command(command)
            self.assertEqual(json.loads(result.stdout), [["data"], "captured"])
            self.assertEqual(result.consumed, ("data/value.txt",))
            for path in ("/repo", "../outside", "./data", "data/../data"):
                with self.subTest(path=path), self.assertRaises(MakeProbeError):
                    directory_python_command(session, "print('not run')", directories=(path,))
        self.assert_clean(session)

    def test_standard_python_command_requires_complete_gitlink_capture_for_namespace_imports(self):
        module, (base, _current) = self.gitlink_fixture()
        self.add("scripts/generated_data/demo/helper.py", "VALUE='fixture'\n")
        self.add("scripts/generated_data/demo/tool.py", "from .helper import VALUE\n")
        self.gitlink_git(self.root, "add", "--all")
        body = (
            "import json, scripts\n"
            "from scripts.generated_data.demo import tool\n"
            "print(json.dumps({"
            "'namespace_origin':scripts.__spec__.origin,"
            "'namespace_loader':type(scripts.__spec__.loader).__name__,"
            "'namespace_paths':list(scripts.__path__),"
            "'value':tool.VALUE"
            "},sort_keys=True))\n"
        )
        raw_budget = ProbeBudget()
        raw_loader = self.gitlink_loader(raw_budget, module / ".git", base, admit=False)
        with ProbeSession(raw_loader, scratch_root=self.scratch, budget=raw_budget) as session:
            with self.assertRaisesRegex(
                MakeProbeError, "nonregular namespace in source enumeration: /repo",
            ):
                session.command(python_command(
                    session, body, code=("scripts/generated_data/demo/tool.py",),
                ))
        self.assert_clean(session)
        raw_budget.close()

        complete_budget = ProbeBudget()
        complete_loader = self.gitlink_loader(complete_budget, module / ".git", base)
        with ProbeSession(complete_loader, scratch_root=self.scratch, budget=complete_budget) as session:
            result = session.command(python_command(
                session, body, code=("scripts/generated_data/demo/tool.py",),
            ))
            self.assertEqual(json.loads(result.stdout), {
                "namespace_loader": "NamespaceLoader",
                "namespace_origin": None,
                "namespace_paths": ["/repo/scripts"],
                "value": "fixture",
            })
        self.assert_clean(session)
        complete_budget.close()

    def test_generated_registry_source_paths_uses_schema_selector_without_loading_records(self):
        self.add("data/a_bundle.json", "{}")
        self.add("data/b_bundle.json", "{}")
        self.add("data/ignored.json", "{}")
        self.add("scripts/generated_data/registry.py", (
            "import glob\n"
            "class Schema:\n"
            " name='chapterbundle'\n version=1\n"
            " def source_paths(self, source):\n"
            "  return sorted(glob.glob(source + '/*_bundle.json'))\n"
            " def load_records(self, source):\n"
            "  raise AssertionError('load_records must not run for --source-paths')\n"
            " def manifest_record_count(self, records): return 0\n"
            "class Registry:\n"
            " def resolve(self, name): return Schema()\n"
            "REGISTRY=Registry()\n"
        ))
        with self.session() as session:
            paths = generated_registry_source_paths(session, "chapterbundle", "data")
            self.assertEqual(paths, ("data/a_bundle.json", "data/b_bundle.json"))
        self.assert_clean(session)

    def test_python_command_selected_view_uses_same_path_changed_code_and_source_bytes(self):
        self.add("data/value.txt", "old\n")
        self.add("scripts/generated_data/demo/helper.py", "VALUE = 'base'\n")
        self.add(
            "scripts/generated_data/demo/tool.py",
            "from scripts.generated_data.demo.helper import VALUE\n"
            "print(VALUE + ':' + open('data/value.txt').read().strip())\n",
        )
        budget = ProbeBudget()
        base_entries, base_revision = self.capture_tree(budget)
        base_loader = AuthorityLoader(self.root, base_entries, base_revision, budget=budget)
        self.add("data/value.txt", "current\n")
        self.add("scripts/generated_data/demo/helper.py", "VALUE = 'current'\n")
        current_entries, current_revision = self.capture_tree(budget)
        current_loader = AuthorityLoader(self.root, current_entries, current_revision, budget=budget)
        with ProbeSession(current_loader, scratch_root=self.scratch, budget=budget) as session:
            command = python_command(
                session,
                "from scripts.generated_data.demo.tool import VALUE\n",
                code=("scripts/generated_data/demo/tool.py",),
                sources=("data/value.txt",),
                directories=("data",),
            )
            self.assertEqual(session.command(command).stdout, b"current:current\n")
            with session.select_view(base_loader) as selected:
                self.assertIs(selected, session)
                base = python_command(
                    session,
                    "from scripts.generated_data.demo.tool import VALUE\n",
                    code=("scripts/generated_data/demo/tool.py",),
                    sources=("data/value.txt",),
                    directories=("data",),
                )
                self.assertEqual(session.command(base).stdout, b"base:old\n")
            restored = python_command(
                session,
                "from scripts.generated_data.demo.tool import VALUE\n",
                code=("scripts/generated_data/demo/tool.py",),
                sources=("data/value.txt",),
                directories=("data",),
            )
            self.assertEqual(session.command(restored).stdout, b"current:current\n")
        self.assert_clean(session)

    def test_generated_registry_requires_the_existing_schema_count_contract(self):
        self.add("data/source.json", "[]")
        self.add("scripts/generated_data/registry.py", (
            "import json\nfrom pathlib import Path\n"
            "class Schema:\n name='missing'\n version=1\n"
            " def load_records(self,path): return json.loads(Path(path).read_text())\n"
            "class Registry:\n"
            " def resolve(self,name): return Schema()\n"
            "REGISTRY=Registry()\n"
        ))
        driver = (TRUSTED_ROOT / "generated_registry_probe.py").read_text()
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "manifest_record_count"):
                session.registry(Command(
                    ("/usr/bin/python3", "-c", driver, "missing", "data/source.json"),
                    code=("scripts/generated_data/registry.py",), sources=("data/source.json",),
                    directories=(".", "scripts", "scripts/generated_data"),
                ))
        self.assert_clean(session)

    def resolution_batch_fixture(self, count=40):
        self.add("data/prefix", "p")
        self.add("worker.py", (
            "import sys\nassert sys.argv[2:]==['word value','']\n"
            "sys.stdout.write(open('data/prefix').read()+sys.argv[1]+'\\n')\n"
        ))
        order = [*reversed(range(count)), count - 1, 0]
        self.add("Makefile", (
            "VALUES := $(foreach n," + " ".join(map(str, order)) + ","
            "$(shell python3 worker.py $(n) 'word value' \"\"))\n"
            "all: $(VALUES)\n\t@printf '%s\\n' '$(VALUES)' '$+'\np%: ;\n"
        ))
        registrations = {
            f"python3 worker.py {index} 'word value' \"\"": Command(
                ("/usr/bin/python3", "/repo/worker.py", str(index), "word value", ""),
                code=("worker.py",), sources=("data/prefix",),
            )
            for index in range(count)
        }
        return registrations, order

    def test_serial_resolution_handles_large_batch_with_one_pending_item(self):
        registrations, order = self.resolution_batch_fixture()
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env=ENVIRONMENT, capture_output=True, check=True, timeout=20,
        ).stdout.decode().splitlines()
        requested, capsules = [], []
        class Commands:
            def __contains__(self, command):
                return command in registrations
            def __getitem__(self, command):
                requested.append(command)
                return registrations[command]
        with self.session(pending=1) as session:
            original = session._sandbox_run
            def record(root, **kwargs):
                result, observed = original(root, **kwargs)
                capsules.append((kwargs["mode"], session.pending_commands, kwargs.get("metadata_validation", False)))
                return result, observed
            with patch.object(session, "_sandbox_run", record):
                result = session.make("all", variables=("VALUES",), commands=Commands())
            self.assertEqual(result.semantics["domains"]["VALUES"]["value"], ordinary[0])
            self.assertEqual(
                [item["name"] for item in result.semantics["files"][0]["prerequisites"]],
                ordinary[1].split(),
            )
            self.assertEqual(requested, [
                f"python3 worker.py {index} 'word value' \"\"" for index in order
            ])
            self.assertEqual(len(result.events), len(order))
            self.assertTrue(all(event["match"] >= 0 for event in result.events))
            self.assertEqual(len(result.semantics["dynamic_commands"]), len(registrations))
            self.assertEqual(sum(mode == "make" for mode, _, _ in capsules), 1)
            self.assertEqual(sum(mode == "command" and not validation for mode, _, validation in capsules), len(registrations))
            self.assertTrue(all(pending == 1 for mode, pending, _ in capsules if mode == "command"))
            self.assertEqual(session.pending_commands, 0)
            self.assertEqual(session.pending_commands_peak, 1)
            self.assertEqual(session.budget.states, 1)
            self.assertGreater(session.budget.bytes["mapping"], 0)
            self.assertGreater(session.budget.bytes["cache"], 0)
        self.assertEqual(session.pending_commands, 0)
        self.assert_clean(session)

    def test_pending_request_bytes_accumulate_after_completed_commands(self):
        self.add("Makefile", "all: ;\n")
        program = "import sys;print(sum(map(ord,sys.argv[1])))"
        with self.session(pending_bytes=32*1024) as session:
            first = session.command(Command(("/usr/bin/python3", "-c", program, "A"*20000)))
            self.assertEqual(first.stdout, str(65*20000).encode() + b"\n")
            self.assertFalse(session.budget.children)
            charged = session.budget.bytes["pending"]
            self.assertGreater(charged, 20000)
            runs = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "aggregate pending byte budget"):
                session.command(Command(("/usr/bin/python3", "-c", program, "B"*20000)))
            self.assertEqual(session.budget.bytes["pending"], charged)
            self.assertEqual(session.budget.runs, runs)
            self.assertTrue(session.budget.closed)
        self.assert_clean(session)

    def test_live_completed_transcript_corruption_cannot_report_success(self):
        registrations, order = self.resolution_batch_fixture(count=3)
        for defect in ("truncated", "hash", "mapping-count", "matched-unknown", "missing-context"):
            with self.subTest(defect=defect):
                requested = []
                class Commands:
                    def __contains__(self, command):
                        return command in registrations
                    def __getitem__(self, command):
                        requested.append(command)
                        return registrations[command]
                with self.session(pending=1) as session:
                    original = session.budget.read_bytes
                    def corrupt(path, category):
                        raw = original(path, category)
                        if category != "event":
                            return raw
                        last, offset = 0, 0
                        while offset < len(raw):
                            last = offset
                            count = int.from_bytes(raw[offset+16:offset+20], "little")
                            offset += 20
                            for _ in range(count):
                                size = int.from_bytes(raw[offset:offset+4], "little")
                                offset += 4 + size
                        data = bytearray(raw)
                        if defect == "truncated":
                            return bytes(data[:-1])
                        if defect == "hash":
                            data[last+8] ^= 1
                        elif defect == "mapping-count":
                            data[last+4:last+8] = (99).to_bytes(4, "little")
                        elif defect == "missing-context":
                            data[last:last+4] = (-2).to_bytes(4, "little", signed=True)
                        else:
                            data[last:last+4] = (0).to_bytes(4, "little")
                        return bytes(data)
                    with patch.object(session.budget, "read_bytes", corrupt):
                        with self.assertRaisesRegex(MakeProbeError, "frame|hash|unknown mapping"):
                            session.make("all", commands=Commands())
                    self.assertEqual(requested, [
                        f"python3 worker.py {index} 'word value' \"\"" for index in order
                    ])
                    self.assertEqual(session.pending_commands, 0)
                    self.assertEqual(session.pending_commands_peak, 1)
                self.assert_clean(session)

    def test_metadata_report_validation_rejects_malformed_buffers_and_native_writes(self):
        self.add("data/a", "observed")
        command = Command(
            ("/usr/bin/python3", "-c", "import os; print(os.listdir('data'))"), directories=("data",),
        )
        for field, value in (
            ("metadata", None),
            ("metadata", [[1, "/repo/data", 0, 0, 0, 0, 0, "", ""]]),
            ("metadata", encode_metadata_transport([[1, "/repo/data", 0, 0, 0, 0, 0, "", ""]])),
            ("metadata", encode_metadata_transport([
                [262, "/repo/../data", 0, 0, 144, 0, 0, "00"*144, "00"*144],
            ])),
            ("metadata", {**encode_metadata_transport([]), "record_count": True}),
            ("metadata", encode_metadata_transport([[262, "/repo/data", 0, 0, 1, 0, 0, "00", "00"]])),
            ("metadata", encode_metadata_transport([
                [262, "/repo/data", 0, 0, 144, 0, 0, "00"*143, "00"*144],
            ])),
            ("metadata", {**encode_metadata_transport([]), "payload": "bm90LXpsaWI="}),
            ("events", None),
            ("events", [True]),
            ("events", ["00"]),
        ):
            with self.subTest(field=field, value=value):
                with self.session() as session:
                    original = session.budget.read_bytes
                    def malformed(path, category):
                        data = original(path, category)
                        if path.name.startswith("report-"):
                            observed = json.loads(data)
                            if value is None:
                                del observed[field]
                            else:
                                observed[field] = value
                            return json.dumps(observed).encode()
                        return data
                    with patch.object(session.budget, "read_bytes", malformed):
                        with self.assertRaises(MakeProbeError):
                            session.command(command)
                self.assert_clean(session)

    def test_serial_resolution_rejects_known_mapping_miss_without_rerunning_worker(self):
        registrations, order = self.resolution_batch_fixture(count=2)
        requested = []
        class Commands:
            def __contains__(self, command):
                return command in registrations
            def __getitem__(self, command):
                requested.append(command)
                return registrations[command]
        with self.session(pending=1) as session:
            original = session.budget.read_bytes
            def corrupt(path, category):
                raw = original(path, category)
                if category == "event" and len(requested) == len(order):
                    return b"\xff\xff\xff\xff" + raw[4:]
                return raw
            with patch.object(session.budget, "read_bytes", corrupt):
                with self.assertRaisesRegex(MakeProbeError, "invalid trusted interceptor frame"):
                    session.make("all", commands=Commands())
            self.assertEqual(requested, [
                f"python3 worker.py {index} 'word value' \"\"" for index in order
            ])
            self.assertEqual(session.pending_commands, 0)
            self.assertEqual(session.pending_commands_peak, 1)
        self.assert_clean(session)

    def test_serial_resolution_pending_depth_is_bounded_and_restored(self):
        outer = "printf %s outer"
        inner = "printf %s inner"
        self.add("Makefile", "VALUE := $(shell " + outer + ")\nall: ;\n")
        self.add("inner.mk", "VALUE := $(shell " + inner + ")\ninner: ;\n")
        for limit in (1, 2):
            with self.subTest(limit=limit):
                with self.session(pending=limit) as session:
                    class Commands:
                        def __contains__(self, command):
                            return command == outer
                        def __getitem__(self, command):
                            session.make("inner", makefile="inner.mk", commands={
                                inner: Command(("/usr/bin/printf", "%s", "inner")),
                            })
                            return Command(("/usr/bin/printf", "%s", "outer"))
                    if limit == 1:
                        with self.assertRaisesRegex(MakeProbeError, "pending producer capacity"):
                            session.make("all", commands=Commands())
                    else:
                        result = session.make("all", variables=("VALUE",), commands=Commands())
                        self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "outer")
                    self.assertEqual(session.pending_commands, 0)
                    self.assertEqual(session.pending_commands_peak, limit)
                self.assertEqual(session.pending_commands, 0)
                self.assert_clean(session)

    def test_serial_resolution_cleans_after_worker_failure_interrupt_and_overflow(self):
        for case, expected in (
            ("failure", "unsuccessfully: 7"), ("interrupt", None), ("overflow", "streaming byte bound"),
        ):
            with self.subTest(case=case):
                registrations, _ = self.resolution_batch_fixture(count=3)
                action = {
                    "failure": "sys.exit(7)",
                    "interrupt": "print('ready',flush=True); time.sleep(20)",
                    "overflow": "sys.stdout.write('X'*2048)",
                }[case]
                self.add("worker.py", (
                    "import sys,time\nprefix=open('data/prefix').read()\n"
                    "if sys.argv[1]=='1':\n " + action + "\nelse:\n print(prefix+sys.argv[1])\n"
                ))
                requested = []
                class Commands:
                    def __contains__(self, command):
                        return command in registrations
                    def __getitem__(self, command):
                        requested.append(command)
                        return registrations[command]
                with self.session(pending=1, process_output_bytes=1024) as session:
                    original_run, original_charge = session._sandbox_run, session.budget.charge
                    in_worker, interrupted = False, False
                    def running(root, **kwargs):
                        nonlocal in_worker
                        in_worker = kwargs["mode"] == "command" and "1" in kwargs["argv"]
                        try:
                            return original_run(root, **kwargs)
                        finally:
                            in_worker = False
                    def charging(category, size):
                        nonlocal interrupted
                        result = original_charge(category, size)
                        if case == "interrupt" and in_worker and category == "output" and size and not interrupted:
                            interrupted = True
                            os.kill(os.getpid(), signal.SIGTERM)
                        return result
                    with patch.object(session, "_sandbox_run", running), patch.object(session.budget, "charge", charging):
                        if case == "interrupt":
                            with self.assertRaises(KeyboardInterrupt):
                                session.make("all", commands=Commands())
                            self.assertTrue(interrupted)
                        else:
                            with self.assertRaisesRegex(MakeProbeError, expected):
                                session.make("all", commands=Commands())
                    self.assertEqual(requested, [
                        "python3 worker.py 2 'word value' \"\"",
                        "python3 worker.py 1 'word value' \"\"",
                    ])
                    self.assertEqual(session.pending_commands, 0)
                    self.assertEqual(session.pending_commands_peak, 1)
                    self.assertTrue(session.budget.closed)
                self.assertEqual(session.pending_commands, 0)
                self.assert_clean(session)

    def observation_command_fixture(self):
        self.add("data/a", "observed\n")
        self.add("reader.py", (
            "import os,sys\nfor index in range(8): os.stat('data/a')\n"
            "sys.stdout.write(open('data/a').read())\n"
        ))
        return Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",), sources=("data/a",),
        )

    def observation_reservoir(self):
        names = tuple(f"reservoir/{index}" for index in range(62))
        for name in names:
            self.add(name, "x")
        return names

    def spend_observation_remainder(self, session, names, keep):
        count = session.budget.limits.entries - session.observations_used - keep
        self.assertGreater(count, 0)
        self.assertLessEqual(count, len(names))
        selected = names[:count]
        before = session.observations_used
        result = session.command(Command(
            ("/usr/bin/python3", "-c",
             "import os,sys\nfor name in sys.argv[1:]:\n"
             " fd=os.open(name,os.O_RDONLY)\n os.read(fd,1)\n os.close(fd)\n", *selected),
            sources=selected,
        ))
        self.assertEqual(result.consumed, tuple(sorted(selected)))
        self.assertEqual(session.observations_used - before, count)

    def test_observation_total_exact_limit_cache_and_next_capsule(self):
        command = self.observation_command_fixture()
        names = self.observation_reservoir()
        with self.session(entries=64) as session:
            self.assertEqual(len(session.snapshot.files), 64)
            first = session.command(command)
            self.assertEqual(first.stdout, b"observed\n")
            self.assertIs(session.command(command), first)
            self.spend_observation_remainder(session, names, keep=0)
            runs = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "filesystem-observation.*before launch"):
                session.command(command)
            self.assertEqual(session.observations_used, 64)
            self.assertEqual(session.budget.runs, runs)
            self.assertTrue(session.budget.closed)
            with self.assertRaisesRegex(MakeProbeError, "deadline/budget"):
                session.command(command)
            self.assertEqual(session.observations_used, 64)
        self.assertEqual(session.observations_used, 64)
        self.assert_clean(session)
        session = self.session(entries=1)
        with self.assertRaisesRegex(MakeProbeError, "snapshot entry count"):
            with session:
                self.fail("source capture exceeded its independent entry limit")
        self.assertEqual(session.observations_used, 0)
        self.assert_clean(session)

    def test_observation_remaining_count_reaches_next_capsule_guard(self):
        command = self.observation_command_fixture()
        names = self.observation_reservoir()
        with self.session(entries=64) as session:
            self.assertEqual(session.command(command).stdout, b"observed\n")
            self.spend_observation_remainder(session, names, keep=1)
            runs = session.budget.runs
            processes = session.processes_used
            with self.assertRaisesRegex(MakeProbeError, "confined command.*filesystem-observation"):
                session.command(replace(command, argv=(*command.argv, "second")))
            self.assertEqual(session.observations_used, 64)
            self.assertEqual(session.budget.runs, runs + 1)
            self.assertEqual(session.processes_used, processes + 1)
            self.assertTrue(session.budget.failed)
            self.assertTrue(session.budget.closed)
        self.assertEqual(session.observations_used, 64)
        self.assert_clean(session)

    def test_observation_totals_charge_failed_deferred_and_terminal_processes(self):
        for exit_code, expected in ((0, "declared/consumed"), (7, "unsuccessfully: 7")):
            with self.subTest(exit_code=exit_code):
                command = self.observation_command_fixture()
                self.add("reader.py", (
                    "import os,sys\n"
                    "if len(sys.argv)>1:\n"
                    " for index in range(8):\n"
                    "  try: os.open('data/a',os.O_RDONLY|os.O_DIRECTORY)\n"
                    "  except OSError: pass\n"
                    f" sys.exit({exit_code})\n"
                    "sys.stdout.write(open('data/a').read())\n"
                ))
                with self.session(entries=64) as session:
                    reports = []
                    read = session.budget.read_bytes
                    def record(path, category):
                        data = read(path, category)
                        if path.name.startswith("report-"):
                            reports.append(json.loads(data))
                        return data
                    with patch.object(session.budget, "read_bytes", record):
                        session.command(command)
                        used = session.observations_used
                        self.assertGreater(used, 2)
                        with self.assertRaisesRegex(MakeProbeError, expected):
                            session.command(replace(command, argv=(*command.argv, "fail")))
                    total = session.observations_used
                    self.assertGreater(total, used)
                    self.assertEqual(total, sum(report["observations"] for report in reports))
                    self.assertEqual(reports[-1]["consumed"], [])
                    self.assertTrue(session.budget.failed)
                    self.assertTrue(session.budget.closed)
                    with self.assertRaisesRegex(MakeProbeError, "deadline/budget"):
                        session.command(command)
                    self.assertEqual(session.observations_used, total)
                self.assertEqual(session.observations_used, total)
                self.assert_clean(session)

    def test_observation_totals_cover_compiler_native_static_make_and_cache(self):
        reader = self.observation_command_fixture()
        self.add("native.c", (
            "#include <stdio.h>\nint main(void) {\n"
            " FILE *file=fopen(\"data/a\",\"r\"); int value;\n"
            " if(!file) return 1;\n"
            " while((value=fgetc(file))!=EOF) putchar(value);\n return fclose(file);\n}\n"
        ))
        self.add("producer.py", (
            "value=open('data/a').read().strip()\n"
            "print(value)\n"
        ))
        self.add("Makefile", (
            "SELECTED := $(shell python3 producer.py)\n"
            "all: $(SELECTED)\nobserved: ;\n"
        ))
        producer = Command(
            ("/usr/bin/python3", "/repo/producer.py"), code=("producer.py",),
            sources=("data/a",),
        )
        configs, reports = [], []
        with self.session() as session:
            original_run, original_capsule = session.budget.run, session._sandbox_run
            def record_config(argv, **kwargs):
                if len(argv) >= 2 and argv[-2] == str(TRUSTED_ROOT / "sandbox_exec.py"):
                    config = json.loads(Path(argv[-1]).read_bytes())
                    configs.append((config["mode"], config["observation_count"], session.observations_used))
                return original_run(argv, **kwargs)
            def record_capsule(root, **kwargs):
                result, observed = original_capsule(root, **kwargs)
                reports.append(observed)
                return result, observed
            with patch.object(session.budget, "run", record_config), patch.object(
                session, "_sandbox_run", record_capsule,
            ):
                session.command(reader)
                tool = session.compile_native(("native.c",))
                native = session.native(tool, sources=("data/a",))
                self.assertEqual(native.stdout, b"observed\n")
                used, runs = session.observations_used, session.budget.runs
                self.assertIs(session.native(tool, sources=("data/a",)), native)
                self.assertGreater(session.observations_used, used)
                self.assertGreater(session.budget.runs, runs)
                result = session.make(
                    "all", variables=("MAKE_RESTARTS",), commands={"python3 producer.py": producer},
                )
                self.assertEqual(result.semantics["domains"]["MAKE_RESTARTS"]["value"], "")
                self.assertEqual(result.semantics["files"][0]["prerequisites"][0]["name"], "observed")
            self.assertEqual(len(configs), len(reports))
            self.assertEqual({mode for mode, _, _ in configs}, {"command", "compile", "make"})
            for _, remaining, used_at_launch in configs:
                self.assertEqual(remaining, session.budget.limits.entries - used_at_launch)
            used = sum(observed["observations"] for observed in reports)
            self.assertEqual(session.observations_used, used)
            self.assertGreater(used, len(session.snapshot.files))
            self.assertLessEqual(used, session.budget.limits.entries)
        self.assert_clean(session)

    def test_observation_accounting_rejects_malformed_closed_reports(self):
        command = self.observation_command_fixture()
        for case, value in (
            ("missing", None), ("null", None), ("false", False), ("true", True),
            ("negative", -1), ("string", "2"), ("float", 2.0), ("array", []),
            ("object", {}), ("inconsistent-zero", 0), ("above-remaining", None),
            ("above-byte-evidence", None),
        ):
            with self.subTest(case=case):
                with self.session() as session:
                    session.command(command)
                    used = session.observations_used
                    original = session.budget.read_bytes
                    def malformed(path, category):
                        data = original(path, category)
                        if path.name.startswith("report-"):
                            report = json.loads(data)
                            self.assertGreaterEqual(report["observations"], 2)
                            if case == "missing":
                                del report["observations"]
                            elif case == "above-remaining":
                                report["observations"] = session.budget.limits.entries - used + 1
                            elif case == "above-byte-evidence":
                                report["observations"] = report["observation_bytes"] // 128 + 1
                            else:
                                report["observations"] = value
                            return json.dumps(report).encode("utf-8")
                        return data
                    with patch.object(session.budget, "read_bytes", malformed):
                        with self.assertRaisesRegex(MakeProbeError, "malformed supervisor"):
                            session.command(replace(command, argv=(*command.argv, "second")))
                    self.assertEqual(session.observations_used, used)
                    self.assertTrue(session.budget.failed)
                    self.assertTrue(session.budget.closed)
                    runs = session.budget.runs
                    with self.assertRaisesRegex(MakeProbeError, "deadline/budget"):
                        session.command(command)
                    self.assertEqual((session.observations_used, session.budget.runs), (used, runs))
                self.assert_clean(session)

    def test_observation_accounting_ignores_candidate_stdout_and_accepts_real_zero(self):
        command = self.observation_command_fixture()
        self.add("reader.py", (
            "import json\nopen('data/a').read()\n"
            "print(json.dumps({'observations':0,'consumed':[],'code_consumed':[],'accessed':[]}))\n"
        ))
        with self.session() as session:
            session.command(Command(("/usr/bin/printf", "%s", "no source inputs")))
            self.assertEqual(session.observations_used, 0)
            result = session.command(command)
            self.assertEqual(json.loads(result.stdout)["observations"], 0)
            self.assertEqual(result.consumed, ("data/a",))
            self.assertEqual(result.code_consumed, ("reader.py",))
            self.assertGreater(session.observations_used, 2)
            self.assertTrue(result.metadata)
        self.assert_clean(session)

    def observation_policy(self, *, count=3, byte_limit=1024*1024, mode="command"):
        from scripts.validation_ownership.syscall_guard import Policy
        return Policy({
            "root": str(self.directory), "mode": mode, "code": [], "sources": [],
            "enumerations": [], "executables": [], "python_version": "3.12", "argv": [],
            "observation_count": count, "observation_limit": byte_limit,
        })

    def test_observation_record_limit_is_aggregate_across_collections(self):
        from scripts.validation_ownership.syscall_guard import Violation
        records = (
            ("consumed", "data/a"), ("code_consumed", "reader.py"),
            ("accessed", "/repo/missing"),
        )
        for extra_collection, _ in records:
            with self.subTest(extra_collection=extra_collection):
                policy = self.observation_policy()
                for collection, value in records:
                    policy.observe(collection, value)
                charged = sum(len(value.encode("utf-8")) + 128 for _, value in records)
                self.assertEqual(policy.observation_bytes, charged)
                self.assertEqual(sum(map(len, policy.observation_attempts.values())), 3)
                for collection, value in records:
                    policy.observe(collection, value)
                    self.assertEqual(getattr(policy, collection), {value})
                self.assertEqual(policy.observation_bytes, charged)
                with self.assertRaisesRegex(Violation, "aggregate filesystem-observation budget exhausted"):
                    policy.observe(extra_collection, "extra")
                self.assertEqual(sum(map(len, policy.observation_attempts.values())), 3)
                self.assertNotIn("extra", policy.observation_attempts[extra_collection])
                self.assertNotIn("extra", getattr(policy, extra_collection))

    def test_failed_observations_spend_aggregate_records_not_consumption(self):
        from scripts.validation_ownership.syscall_guard import Process, Registers, Violation
        policy = self.observation_policy()
        state = Process("command")
        records = (
            ("consumed", "data/a"), ("code_consumed", "reader.py"),
            ("accessed", "/repo/missing"),
        )
        for collection, value in records:
            policy.defer_observation(state, collection, value)
            policy.leave(0, state, Registers(rax=-errno.ENOENT))
            self.assertEqual(getattr(policy, collection), set())
            self.assertEqual(policy.observation_attempts[collection], {value})
            self.assertEqual(state.observations, [])
        charged = policy.observation_bytes
        for collection, value in records:
            policy.defer_observation(state, collection, value)
            policy.leave(0, state, Registers(rax=0))
            self.assertEqual(getattr(policy, collection), {value})
            policy.defer_observation(state, collection, value)
            policy.leave(0, state, Registers(rax=-errno.EFAULT))
            self.assertEqual(getattr(policy, collection), {value})
        self.assertEqual(policy.observation_bytes, charged)
        with self.assertRaisesRegex(Violation, "aggregate filesystem-observation budget exhausted"):
            policy.defer_observation(state, "accessed", "/repo/another")
        self.assertEqual(state.observations, [])
        self.assertEqual(policy.accessed, {"/repo/missing"})
        self.assertEqual(sum(map(len, policy.observation_attempts.values())), 3)

    def test_observation_bytes_remain_an_independent_aggregate_bound(self):
        from scripts.validation_ownership.syscall_guard import Violation
        value = "data/a"
        charge = len(value.encode("utf-8")) + 128
        policy = self.observation_policy(count=10, byte_limit=2*charge)
        for collection in ("consumed", "code_consumed"):
            policy.observe(collection, value)
            policy.observe(collection, value)
        self.assertEqual(policy.observation_bytes, 2*charge)
        self.assertEqual(sum(map(len, policy.observation_attempts.values())), 2)
        with self.assertRaisesRegex(Violation, "aggregate filesystem-observation budget exhausted"):
            policy.observe("accessed", value)
        self.assertEqual(policy.observation_bytes, 3*charge)
        self.assertFalse(policy.observation_attempts["accessed"])
        self.assertFalse(policy.accessed)

    def test_compile_executable_metadata_exception_is_narrow(self):
        from scripts.validation_ownership.syscall_guard import Process, Registers, Violation
        states = (("compile", "compiler", False), ("command", "command", False),
                  ("make", "make", False), ("make", "make", True))
        def denial(mode, ready, path, operation):
            if mode == "make" and not ready:
                return f"uncaptured Make runtime access: {operation} {path}"
            if mode == "make" and operation == "directory":
                return "Make runtime directory enumeration denied"
            return f"descriptor/device namespace denied: {path}"
        for mode, role, ready in states:
            with self.subTest(mode=mode, observer_ready=ready):
                policy = self.observation_policy(mode=mode)
                state = Process(role, bootstrap=False, observer_ready=ready)
                if mode == "compile":
                    policy.check(state, "/proc/self/exe", "metadata")
                else:
                    with self.assertRaisesRegex(Violation, re.escape(denial(mode, ready, "/proc/self/exe", "metadata"))):
                        policy.check(state, "/proc/self/exe", "metadata")
                for operation in ("read", "write", "directory", "exec"):
                    with self.subTest(operation=operation):
                        with self.assertRaisesRegex(
                            Violation, re.escape(denial(mode, ready, "/proc/self/exe", operation)),
                        ):
                            policy.check(state, "/proc/self/exe", operation)
                for path in (
                    "/proc", "/proc/self", "/proc/self/exe/extra", "/proc/self/exe-other",
                    "/proc/1/exe", "/proc/thread-self/exe", "/proc/self/maps",
                    "/proc/self/fd", "/proc/self/fd/0", "/sys/kernel", "/dev/mem", "/dev/fd/0",
                ):
                    with self.subTest(path=path):
                        with self.assertRaisesRegex(Violation, re.escape(denial(mode, ready, path, "metadata"))):
                            policy.check(state, path, "metadata")
                for operation in ("read", "metadata"):
                    with self.assertRaisesRegex(Violation, "unavailable inherited/unknown descriptor"):
                        policy.check_fd(state, 3, operation, Registers())
                self.assertFalse(policy.consumed | policy.code_consumed | policy.accessed)
        path = ctypes.create_string_buffer(b"/proc/self/exe")
        with self.assertRaisesRegex(Violation, "untrusted executable dispatch"):
            self.traced_observation(59, (ctypes.addressof(path), 0, 0), {}, mode="compile")

    def test_compile_executable_metadata_probe_reports_capsule_absence(self):
        self.add("probe.c", (
            "#define _GNU_SOURCE\n#include <errno.h>\n#include <stdio.h>\n"
            "#include <sys/stat.h>\n#include <unistd.h>\n"
            "int main(void) {\n"
            " struct stat data; char target[4096]; int result, error;\n"
            " errno=0; result=stat(\"/proc/self/exe\",&data); error=errno;\n"
            " printf(\"%d %d\\n\",result,error);\n"
            " errno=0; result=lstat(\"/proc/self/exe\",&data); error=errno;\n"
            " printf(\"%d %d\\n\",result,error);\n"
            " errno=0; result=access(\"/proc/self/exe\",F_OK); error=errno;\n"
            " printf(\"%d %d\\n\",result,error);\n"
            " errno=0; result=readlink(\"/proc/self/exe\",target,sizeof(target)); error=errno;\n"
            " printf(\"%d %d\\n\",result,error);\n return 0;\n}\n"
        ))
        with self.session() as session:
            tool = session.compile_native(("probe.c",))
            run = session._sandbox_run
            def compiler_probe(root, **kwargs):
                # Test-owned driver, as in the compiler vfork/exec control.
                self.assertEqual(kwargs["mode"], "command")
                self.assertEqual(kwargs["argv"], ["/native/tool"])
                self.assertFalse((root / "proc").exists())
                kwargs["mode"] = "compile"
                return run(root, **kwargs)
            with patch.object(session, "_sandbox_run", compiler_probe):
                result = session.native(tool)
            self.assertEqual(
                [tuple(map(int, line.split())) for line in result.stdout.splitlines()],
                [(-1, errno.ENOENT)]*4,
            )
            self.assertEqual(result.stderr, b"")
            self.assertEqual(result.consumed, ())
            with self.assertRaisesRegex(MakeProbeError, "descriptor/device namespace denied"):
                session.native(tool, ("normal-command",))
        self.assert_clean(session)

    def traced_observation(
        self, number, arguments, descriptors, *, buffer=None, mode="command",
        observation_limit=1024*1024, directories=("data",), root=None,
        runtime_files=(), observer_ready=False, transitions=None, mapping_bytes=0, fresh_exec=False,
    ):
        from scripts.validation_ownership.syscall_guard import (
            GETREGS, SETOPTIONS, SYSCALL, Policy, Process, Registers, memory, ptrace, signed, trace_me,
        )
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        memory_limit = 256*1024*1024
        if fresh_exec:
            self.assertEqual(number, 9)
            self.assertEqual(len(arguments), 6)
            self.assertEqual(arguments[0], 0)
            self.assertIsNone(buffer)
        inherited = {descriptor: os.get_inheritable(descriptor) for descriptor in descriptors} if fresh_exec else {}
        def operation():
            os.chdir(self.root)
            if fresh_exec:
                for name in os.listdir("/proc/self/fd"):
                    descriptor = int(name)
                    if descriptor > 2 and descriptor not in descriptors:
                        try:
                            os.close(descriptor)
                        except OSError as error:
                            if error.errno != errno.EBADF:
                                raise
                for descriptor in descriptors:
                    os.set_inheritable(descriptor, True)
                program = (
                    "import ctypes,resource,sys\n"
                    "sys.path.insert(0,sys.argv[1])\n"
                    "from syscall_guard import trace_me\n"
                    "limit=int(sys.argv[2]); resource.setrlimit(resource.RLIMIT_AS,(limit,limit))\n"
                    "number=int(sys.argv[3]); arguments=tuple(map(int,sys.argv[4:]))\n"
                    "libc=ctypes.CDLL(None,use_errno=True); libc.syscall.restype=ctypes.c_long\n"
                    "trace_me(lambda:None)\n"
                    "libc.syscall(ctypes.c_long(number),"
                    "*(ctypes.c_ulong(value & ((1<<64)-1)) for value in arguments))\n"
                )
                os.execve(
                    "/usr/bin/python3",
                    ["/usr/bin/python3", "-I", "-S", "-B", "-c", program,
                     str(TRUSTED_ROOT), str(memory_limit), str(number), *(str(value) for value in arguments)],
                    ENVIRONMENT,
                )
            trace_me(lambda: None)
            libc.syscall(ctypes.c_long(number), *(ctypes.c_ulong(value & ((1 << 64)-1)) for value in arguments))
        with self.stopped_tracee(operation) as pid:
            if fresh_exec:
                self.assertEqual({int(name) for name in os.listdir(f"/proc/{pid}/fd")},
                                 {0, 1, 2, *descriptors})
                for descriptor, inheritable in inherited.items():
                    self.assertEqual(os.get_inheritable(descriptor), inheritable)
                    expected = os.fstat(descriptor)
                    actual = os.stat(f"/proc/{pid}/fd/{descriptor}")
                    self.assertEqual((actual.st_dev, actual.st_ino), (expected.st_dev, expected.st_ino))
            ptrace(SETOPTIONS, pid, 0, 0x100001)
            policy = Policy({
                "root": str(self.directory if root is None else root), "mode": mode, "code": ["code.bin"],
                "sources": ["data/a", "data/b"], "enumerations": list(directories),
                "source_view": str(self.root),
                "deadline": time.monotonic() + 30,
                "executables": [], "python_version": "3.12", "argv": [],
                "memory_limit": memory_limit, "syscall_limit": 1024,
                "write_limit": 1024*1024, "observation_count": 1024,
                "observation_limit": observation_limit, "forbidden_paths": [],
                "runtime_files": list(runtime_files),
            })
            state = Process(
                "make" if mode == "make" else "command", memory_group=pid,
                observer_ready=observer_ready, bootstrap=not observer_ready,
            )
            state.fds.update(descriptors)
            policy.processes[pid] = state
            directory_bytes = []
            observe_directory = policy.observe_directory
            def record_directory(*values):
                directory_bytes.append(policy.observation_bytes)
                return observe_directory(*values)
            policy.observe_directory = record_directory
            entered = False
            for _ in range(256):
                if entered and transitions is not None:
                    transitions.append("resume")
                ptrace(SYSCALL, pid)
                waited, status = os.waitpid(pid, 0)
                self.assertEqual(waited, pid)
                self.assertTrue(os.WIFSTOPPED(status), status)
                registers = Registers()
                ptrace(GETREGS, pid, 0, ctypes.byref(registers))
                information = (ctypes.c_ubyte * 128)()
                ptrace(0x420E, pid, len(information), ctypes.byref(information))
                actual = (registers.rdi, registers.rsi, registers.rdx, registers.r10, registers.r8, registers.r9)
                expected = tuple(value & ((1 << 64)-1) for value in arguments)
                if information[0] == 1 and registers.orig_rax == number and actual[:len(expected)] == expected:
                    if transitions is not None:
                        transitions.append("entry")
                    policy.entry(pid, state, registers)
                    entry_observation_bytes = policy.observation_bytes
                    self.assertFalse(policy.consumed)
                    self.assertFalse(policy.code_consumed)
                    self.assertFalse(policy.accessed)
                    entered = True
                elif information[0] == 2 and entered:
                    result = signed(registers.rax)
                    policy.leave(pid, state, registers)
                    if transitions is not None:
                        transitions.append("exit")
                    return {
                        "result": result, "consumed": set(policy.consumed),
                        "code": set(policy.code_consumed), "accessed": set(policy.accessed),
                        "data": (memory(pid, result, mapping_bytes) if mapping_bytes and result > 0
                                 else memory(pid, buffer, result) if buffer is not None and result > 0 else b""),
                        "fds": dict(state.fds),
                        "entry_observation_bytes": entry_observation_bytes,
                        "directory_observation_bytes": directory_bytes[0] if directory_bytes else None,
                        "memory_limit": state.memory_limit,
                        "kernel_memory_limit": resource.prlimit(pid, resource.RLIMIT_AS)[0],
                    }
            self.fail("owned syscall did not reach its exit")

    def test_failed_open_and_metadata_cannot_forge_registry_consumption(self):
        self.add("data/a", b"owned")
        operations = (
            "os.open('data/a',os.O_RDONLY|os.O_DIRECTORY)",
            "libc.syscall(2,b'data/a',os.O_RDONLY|os.O_DIRECTORY,0)",
            "libc.syscall(257,-100,b'data/a',os.O_RDONLY|os.O_DIRECTORY,0)",
            "libc.syscall(4,b'data/a',ctypes.c_void_p(1))",
            "libc.syscall(6,b'data/a',ctypes.c_void_p(1))",
            "libc.syscall(262,-100,b'data/a',ctypes.c_void_p(1),0)",
            "libc.syscall(332,-100,b'data/a',0,0x7ff,ctypes.c_void_p(1))",
            "libc.syscall(21,b'data/a',os.W_OK)",
            "libc.syscall(269,-100,b'data/a',os.W_OK)",
            "libc.syscall(439,-100,b'data/a',os.W_OK,0)",
            "os.readlink('data/a')",
        )
        for operation in operations:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes,json,os\nlibc=ctypes.CDLL(None,use_errno=True)\n"
                    "try:\n result=" + operation + "\n assert result == -1\nexcept OSError:\n pass\n"
                    "print(json.dumps({'name':'forged','version':1,'record_count':1,'source_paths':['data/a']}))\n"
                ))
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "declared/consumed source mismatch"):
                    with session:
                        session.registry(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                            code=("reader.py",), sources=("data/a",),
                        ))
                self.assert_clean(session)

    def test_successful_metadata_open_and_content_remain_source_observations(self):
        self.add("data/a", b"owned")
        operations = (
            "os.close(os.open('data/a',os.O_RDONLY))",
            "os.close(os.open('data/a',os.O_PATH))",
            "assert os.stat('data/a').st_size == 5",
            "assert os.lstat('data/a').st_size == 5",
            "assert os.access('data/a',os.R_OK)",
            "assert open('data/a','rb').read(1) == b'o'",
            "fd=os.open('data/a',os.O_RDONLY)\nassert os.fstat(fd).st_size == 5\nos.close(fd)",
            "fd=os.open('data/a',os.O_RDONLY)\nview=mmap.mmap(fd,0,access=mmap.ACCESS_READ)\n"
            "assert view[:1] == b'o'\nview.close()\nos.close(fd)",
        )
        for operation in operations:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import json,mmap,os\n" + operation + "\n"
                    "print(json.dumps({'name':'observed','version':1,'record_count':1,'source_paths':['data/a']}))\n"
                ))
                with self.session() as session:
                    self.assertEqual(session.registry(Command(
                        ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                        code=("reader.py",), sources=("data/a",),
                    ))["source_paths"], ["data/a"])
                self.assert_clean(session)

    def test_failed_and_partial_getdents_cannot_credit_unreturned_siblings(self):
        self.add("data/a", b"a")
        self.add("data/b", b"b")
        for number in (78, 217):
            for count in (1, 24):
                with self.subTest(number=number, count=count):
                    self.add("reader.py", (
                        "import ctypes,json,os\nlibc=ctypes.CDLL(None)\n"
                        "buffer=ctypes.create_string_buffer(24)\n"
                        "directory=os.open('data',os.O_RDONLY|os.O_DIRECTORY)\n"
                        f"size=libc.syscall({number},directory,buffer,{count})\n"
                        "os.write(2,buffer.raw[:size].hex().encode() if size>0 else b'')\n"
                        "os.close(directory)\n"
                        "print(json.dumps({'name':'forged','version':1,'record_count':2,"
                        "'source_paths':['data/a','data/b']}))\n"
                    ))
                    session = self.session()
                    reports = []
                    with session:
                        original = session._sandbox_run
                        def recording(root, **kwargs):
                            result, observed = original(root, **kwargs)
                            reports.append((result, observed))
                            return result, observed
                        with patch.object(session, "_sandbox_run", recording):
                            with self.assertRaisesRegex(MakeProbeError, "declared/consumed source mismatch"):
                                session.registry(Command(
                                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                                    code=("reader.py",), sources=("data/a", "data/b"), directories=("data",),
                                ))
                        raw = bytes.fromhex(reports[-1][0].stderr.decode())
                        names = set()
                        if raw:
                            self.assertEqual(len(raw), 24)
                            name = raw[19 if number == 217 else 18:].split(b"\0", 1)[0].decode()
                            if name in {"a", "b"}:
                                names.add("data/" + name)
                        self.assertEqual(set(reports[-1][1]["consumed"]), names)
                        self.assertLess(len(names), 2)
                    self.assert_clean(session)

    def test_fd_read_metadata_mmap_and_dup_credit_only_successful_exits(self):
        self.add("data/a", b"alpha")
        buffer = ctypes.create_string_buffer(256)
        address = ctypes.addressof(buffer)
        descriptor = os.open(self.root / "data/a", os.O_RDONLY)
        try:
            class Vector(ctypes.Structure):
                _fields_ = [("address", ctypes.c_void_p), ("length", ctypes.c_size_t)]
            vector = Vector(address, 1)
            failures = (
                (0, (descriptor, 1, 1)), (0, (descriptor, address, 0)),
                (17, (descriptor, address, 1, 1000)), (19, (descriptor, 0, 1)),
                (5, (descriptor, 1)), (138, (descriptor, 1)),
                (8, (descriptor, -1, os.SEEK_SET)), (292, (descriptor, descriptor, 0)),
                (72, (descriptor, 0, 0x7fffffff)),
                (9, (0, 0, 1, 2, descriptor, 0)), (9, (0, 4096, 1, 2, descriptor, 1)),
            )
            for number, arguments in failures:
                with self.subTest(number=number, arguments=arguments):
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    observed = self.traced_observation(number, arguments, {descriptor: "/repo/data/a"})
                    self.assertLessEqual(observed["result"], 0)
                    self.assertEqual(observed["consumed"], set())
            successes = (
                (0, (descriptor, address, 1)), (17, (descriptor, address, 1, 0)),
                (19, (descriptor, ctypes.addressof(vector), 1)),
                (5, (descriptor, address)), (138, (descriptor, address)),
                (8, (descriptor, 0, os.SEEK_END)), (32, (descriptor,)),
                (72, (descriptor, 0, 10)), (9, (0, 4096, 1, 2, descriptor, 0)),
            )
            for number, arguments in successes:
                with self.subTest(number=number, arguments=arguments):
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    observed = self.traced_observation(number, arguments, {descriptor: "/repo/data/a"})
                    self.assertGreaterEqual(observed["result"], 0)
                    self.assertEqual(observed["consumed"], {"data/a"})
            closed = self.traced_observation(3, (descriptor,), {descriptor: "/repo/data/a"})
            self.assertEqual(closed["result"], 0)
            self.assertNotIn(descriptor, closed["fds"])
            self.assertEqual(closed["consumed"], set())
        finally:
            os.close(descriptor)

    def test_getdents_credits_returned_names_and_not_failure_eof_or_tail(self):
        from scripts.validation_ownership.syscall_guard import directory_entries
        self.add("data/a", b"a")
        self.add("data/b", b"b")
        libc = ctypes.CDLL(None)
        buffer = ctypes.create_string_buffer(4096)
        address = ctypes.addressof(buffer)
        for number in (78, 217):
            descriptor = os.open(self.root / "data", os.O_RDONLY | os.O_DIRECTORY)
            try:
                failures = ((descriptor, address, 1), (descriptor, 1, 4096))
                for arguments in failures:
                    observed = self.traced_observation(number, arguments, {descriptor: "/repo/data"})
                    self.assertLess(observed["result"], 0)
                    self.assertEqual(observed["consumed"], set())
                os.lseek(descriptor, 0, os.SEEK_SET)
                partial = self.traced_observation(
                    number, (descriptor, address, 24), {descriptor: "/repo/data"}, buffer=address,
                )
                expected = {"data/" + name for name in directory_entries(partial["data"], wide=number == 217)}
                self.assertEqual(partial["consumed"], expected)
                self.assertLess(len(expected), 2)
                os.lseek(descriptor, 0, os.SEEK_SET)
                complete = self.traced_observation(
                    number, (descriptor, address, 4096), {descriptor: "/repo/data"}, buffer=address,
                )
                self.assertEqual(complete["consumed"], {"data/a", "data/b"})
                # Populate the caller buffer with old valid records, but set the
                # shared directory offset to EOF before observation begins.
                ctypes.memmove(address, complete["data"], len(complete["data"]))
                while libc.syscall(number, descriptor, buffer, 4096) > 0:
                    pass
                eof = self.traced_observation(
                    number, (descriptor, address, 4096), {descriptor: "/repo/data"}, buffer=address,
                )
                self.assertEqual(eof["result"], 0)
                self.assertEqual(eof["consumed"], set())
            finally:
                os.close(descriptor)

    def test_code_and_make_path_observations_wait_for_success(self):
        self.add("code.bin", b"code")
        buffer = ctypes.create_string_buffer(256)
        descriptor = os.open(self.root / "code.bin", os.O_RDONLY)
        try:
            for mode, collection, expected in (
                ("command", "code", {"code.bin"}),
                ("make", "accessed", {"/repo/code.bin"}),
            ):
                with self.subTest(mode=mode):
                    failed = self.traced_observation(
                        5, (descriptor, 1), {descriptor: "/repo/code.bin"}, mode=mode,
                    )
                    self.assertLess(failed["result"], 0)
                    self.assertEqual(failed[collection], set())
                    success = self.traced_observation(
                        5, (descriptor, ctypes.addressof(buffer)), {descriptor: "/repo/code.bin"}, mode=mode,
                    )
                    self.assertEqual(success["result"], 0)
                    self.assertEqual(success[collection], expected)
        finally:
            os.close(descriptor)

    def test_directory_names_do_not_credit_nested_or_symlink_referents(self):
        self.add("code.bin", b"code")
        self.add("data/a", b"a")
        self.add("data/b", b"b")
        (self.root / "alias").symlink_to("data/a")
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        buffer = ctypes.create_string_buffer(4096)
        try:
            observed = self.traced_observation(
                217, (descriptor, ctypes.addressof(buffer), 4096),
                {descriptor: "/repo"}, buffer=ctypes.addressof(buffer), directories=(".",),
            )
            self.assertGreater(observed["result"], 0)
            self.assertEqual(observed["code"], {"code.bin"})
            self.assertEqual(observed["consumed"], set())
        finally:
            os.close(descriptor)

    def test_directory_observation_transfer_and_buffer_limits_are_bounded(self):
        from scripts.validation_ownership.syscall_guard import SYSCALL_MEMORY_LIMIT, Violation
        self.add("data/a", b"a")
        self.add("data/b", b"b")
        buffer = ctypes.create_string_buffer(SYSCALL_MEMORY_LIMIT + 1)
        descriptor = os.open(self.root / "data", os.O_RDONLY | os.O_DIRECTORY)
        try:
            with self.assertRaisesRegex(Violation, "syscall memory bound"):
                self.traced_observation(
                    217, (descriptor, ctypes.addressof(buffer), len(buffer)),
                    {descriptor: "/repo/data"},
                )
            observed = self.traced_observation(
                217, (descriptor, ctypes.addressof(buffer), 4096),
                {descriptor: "/repo/data"},
            )
            self.assertGreater(observed["result"], 1)
            os.lseek(descriptor, 0, os.SEEK_SET)
            with self.assertRaisesRegex(Violation, "directory-observation byte budget"):
                self.traced_observation(
                    217, (descriptor, ctypes.addressof(buffer), 4096),
                    {descriptor: "/repo/data"},
                    observation_limit=observed["directory_observation_bytes"] + observed["result"] - 1,
                )
        finally:
            os.close(descriptor)

    def test_directory_parser_rejects_malformed_actual_records(self):
        from scripts.validation_ownership.syscall_guard import Violation, directory_entries
        self.add("data/a", b"a")
        self.add("data/b", b"b")
        buffer = ctypes.create_string_buffer(4096)
        for number in (78, 217):
            descriptor = os.open(self.root / "data", os.O_RDONLY | os.O_DIRECTORY)
            try:
                observed = self.traced_observation(
                    number, (descriptor, ctypes.addressof(buffer), 4096),
                    {descriptor: "/repo/data"}, buffer=ctypes.addressof(buffer),
                )
            finally:
                os.close(descriptor)
            data = observed["data"]
            self.assertEqual(set(directory_entries(data, wide=number == 217)), {"a", "b"})
            invalid_length = bytearray(data)
            invalid_length[16:18] = b"\0\0"
            invalid_name = bytearray(data)
            invalid_name[19 if number == 217 else 18] = 0xff
            for malformed in (data[:-1], bytes(invalid_length), bytes(invalid_name)):
                with self.assertRaises(Violation):
                    directory_entries(malformed, wide=number == 217)

    @staticmethod
    def signal_sender_source():
        return r'''
#define _GNU_SOURCE
#include <signal.h>
#include <stdlib.h>
#include <string.h>
#include <sys/syscall.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
static long send_signal(int operation, pid_t target) {
    siginfo_t info;
    memset(&info,0,sizeof(info));
    info.si_signo=SIGUSR1; info.si_code=SI_QUEUE;
    info.si_pid=getpid(); info.si_uid=getuid(); info.si_value.sival_int=42;
    switch(operation) {
    case SYS_kill: return syscall(SYS_kill,target,SIGUSR1);
    case SYS_tkill: return syscall(SYS_tkill,target,SIGUSR1);
    case SYS_tgkill: return syscall(SYS_tgkill,target,target,SIGUSR1);
    case SYS_rt_sigqueueinfo: return syscall(SYS_rt_sigqueueinfo,target,SIGUSR1,&info);
    case SYS_rt_tgsigqueueinfo: return syscall(SYS_rt_tgsigqueueinfo,target,target,SIGUSR1,&info);
    default: return -1;
    }
}
static int receive_signal(int operation, const sigset_t *mask) {
    siginfo_t info; struct timespec timeout={2,0};
    if(sigtimedwait(mask,&info,&timeout)!=SIGUSR1) return 4;
    if((operation==SYS_rt_sigqueueinfo || operation==SYS_rt_tgsigqueueinfo)
       && info.si_value.sival_int!=42) return 5;
    return write(1,"delivered\n",10)==10 ? 0 : 6;
}
int main(int argc,char **argv) {
    int operation, ready[2], status; char byte; sigset_t mask;
    if(argc!=3) return 1;
    operation=atoi(argv[1]); sigemptyset(&mask); sigaddset(&mask,SIGUSR1);
    if(sigprocmask(SIG_BLOCK,&mask,0)) return 2;
    if(!strcmp(argv[2],"self")) {
        if(send_signal(operation,getpid())) return 3;
        return receive_signal(operation,&mask);
    }
    if(pipe(ready)) return 7;
    pid_t child=fork(); if(child<0) return 8;
    if(!child) {
        if(write(ready[1],"R",1)!=1) _exit(9);
        _exit(receive_signal(operation,&mask));
    }
    if(read(ready[0],&byte,1)!=1 || send_signal(operation,child)) return 10;
    if(waitpid(child,&status,0)!=child || status) return 11;
    return 0;
}
'''

    def test_pid_signal_families_allow_self_delivery_but_reject_owned_siblings(self):
        self.add("signals.c", self.signal_sender_source())
        operations = (62, 129, 200, 234, 297)
        with self.session() as session:
            tool = session.compile_native(("signals.c",))
            for operation in operations:
                with self.subTest(operation=operation, target="self"):
                    self.assertEqual(session.native(tool, (str(operation), "self")).stdout, b"delivered\n")
        self.assert_clean(session)
        for operation in operations:
            with self.subTest(operation=operation, target="owned sibling"):
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "cross-process signal target"):
                    with session:
                        tool = session.compile_native(("signals.c",))
                        session.native(tool, (str(operation), "other"))
                self.assert_clean(session)

    def test_signal_policy_rejects_group_broadcast_and_mixed_thread_targets(self):
        from scripts.validation_ownership.syscall_guard import Process, Registers, Violation
        with self.owned_process([
            "/usr/bin/python3", "-I", "-S", "-c", "import os; os.read(0,1)",
        ]) as (recipient, descriptor):
            sender = os.getpid()
            policy = self.memory_policy(64*1024*1024)
            policy.config.update(syscall_limit=100, write_limit=1024)
            state = Process("command")
            for operation in (62, 129, 200, 234, 297):
                targets = [(target, target) for target in (0, -1, -recipient.pid, recipient.pid)]
                if operation in (234, 297):
                    targets += [(sender, recipient.pid), (recipient.pid, sender), (sender, 0), (0, sender)]
                for group, thread in targets:
                    with self.subTest(operation=operation, group=group, thread=thread):
                        registers = Registers(
                            orig_rax=operation, rdi=group & ((1 << 64)-1),
                            rsi=thread & ((1 << 64)-1),
                        )
                        # Exercise real policy only; group/broadcast requests
                        # never reach a kernel, even in the negative control.
                        with self.assertRaisesRegex(Violation, "cross-process signal target"):
                            policy.entry(sender, state, registers)
            self.assertIsNone(recipient.poll())

    def test_descriptor_async_and_timer_signal_routes_remain_unadmitted(self):
        controls = [
            ("libc.syscall(424,-1,signal.SIGUSR1,0,0)", "pidfd signal"),
            ("libc.syscall(434,-1,0)", "unadmitted syscall"),
            ("libc.syscall(438,-1,-1,0)", "unadmitted syscall"),
            ("libc.syscall(222,-1,0,0)", "unadmitted syscall"),
            ("libc.syscall(223,-1,0,0,0)", "unadmitted syscall"),
            ("libc.syscall(244,-1,0)", "unadmitted syscall"),
            ("fcntl.fcntl(pipe[0],fcntl.F_SETOWN,os.getpid())", "unknown fcntl"),
            ("fcntl.fcntl(pipe[0],10,signal.SIGUSR1)", "unknown fcntl"),
            ("fcntl.fcntl(pipe[0],15,struct.pack('ii',1,os.getpid()))", "unknown fcntl"),
            ("fcntl.fcntl(pipe[0],1026,0)", "unknown fcntl"),
            ("libc.syscall(16,pipe[0],0x40045436,signal.SIGUSR1)", "unknown ioctl"),
        ]
        for operation, expected in controls:
            with self.subTest(operation=operation):
                program = (
                    "import ctypes,fcntl,os,signal,struct\nlibc=ctypes.CDLL(None)\npipe=os.pipe()\n"
                    "try:\n " + operation + "\nexcept OSError:\n pass\n"
                )
                self.add("signal_route.py", program)
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, expected):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-S", "/repo/signal_route.py"),
                            code=("signal_route.py",),
                        ))
                self.assert_clean(session)

    def registry_fixture(self, name):
        self.add("registry.py", (
            "import json,sys\nopen('/work/entry','wb').close()\n"
            "print(json.dumps({'name':sys.argv[1],'version':1,"
            "'record_count':1,'source_paths':[]}))\n"
        ))
        return Command(
            ("/usr/bin/python3", "-I", "-B", "/repo/registry.py", name), code=("registry.py",),
        )

    def test_registry_helper_requires_an_explicit_active_report_session(self):
        command = self.registry_fixture("first")
        inactive = self.session()
        loader = inactive.loader
        with patch.object(ProbeSession, "__enter__", side_effect=AssertionError("implicit session")) as enter:
            with self.assertRaises(TypeError):
                probe_generated_registry(loader, command=command)
            with self.assertRaises(TypeError):
                probe_generated_registry(loader, scratch_root=self.scratch, command=command)
            for session in (None, object(), inactive):
                with self.subTest(session=session):
                    with self.assertRaisesRegex(MakeProbeError, "active ProbeSession"):
                        probe_generated_registry(loader, command=command, session=session)
            enter.assert_not_called()
        self.assertFalse(self.scratch.exists())
        with self.session() as closed:
            pass
        self.assert_clean(closed)
        with self.assertRaisesRegex(MakeProbeError, "active ProbeSession"):
            probe_generated_registry(closed.loader, command=command, session=closed)

    def test_registry_helper_rejects_foreign_loader_or_budget_without_launch(self):
        command = self.registry_fixture("first")
        with self.session() as session:
            runs = session.budget.runs
            foreign = self.session().loader
            with self.assertRaisesRegex(MakeProbeError, "loader/budget differs"):
                probe_generated_registry(foreign, command=command, session=session)
            with patch.object(session.loader, "budget", ProbeBudget()):
                with self.assertRaisesRegex(MakeProbeError, "loader/budget differs"):
                    probe_generated_registry(session.loader, command=command, session=session)
            self.assertEqual(session.budget.runs, runs)
            self.assertFalse(session.cache)
        self.assert_clean(session)

    def test_registry_helper_calls_share_cache_deadline_and_creation_quota(self):
        first = self.registry_fixture("first")
        second = self.registry_fixture("second")
        with self.session(created_files=1) as session:
            budget, started, deadline = session.budget, session.budget.started, session.budget.deadline
            observed = probe_generated_registry(session.loader, command=first, session=session)
            self.assertEqual(observed["name"], "first")
            runs, processes = budget.runs, session.processes_used
            self.assertEqual(session.files_created, 1)
            self.assertEqual(probe_generated_registry(session.loader, command=first, session=session), observed)
            self.assertGreater(budget.runs, runs)
            self.assertGreater(session.processes_used, processes)
            self.assertEqual(session.files_created, 1)
            self.assertEqual(len(session.cache), 1)
            self.assertIs(session.budget, budget)
            self.assertEqual((budget.started, budget.deadline), (started, deadline))
            with self.assertRaisesRegex(MakeProbeError, "file-creation budget"):
                probe_generated_registry(session.loader, command=second, session=session)
            with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline/budget"):
                probe_generated_registry(session.loader, command=first, session=session)
        self.assert_clean(session)

    def test_registry_helper_cannot_reuse_cache_past_the_report_deadline(self):
        command = self.registry_fixture("cached")
        with self.session() as session:
            probe_generated_registry(session.loader, command=command, session=session)
            budget = session.budget
            started, runs = budget.started, budget.runs
            budget.limits = replace(budget.limits, seconds=time.monotonic() - started + 0.02)
            deadline = budget.deadline
            time.sleep(max(0, deadline - time.monotonic()) + 0.03)
            with self.assertRaisesRegex(MakeProbeError, "aggregate probe deadline"):
                probe_generated_registry(session.loader, command=command, session=session)
            self.assertIs(session.budget, budget)
            self.assertIs(session.loader.budget, budget)
            self.assertEqual((budget.started, budget.deadline, budget.runs), (started, deadline, runs))
        self.assert_clean(session)

    def test_make_and_registry_helper_consume_the_same_creation_budget(self):
        command = self.registry_fixture("registry")
        self.add("make_data.py", "open('/work/make-entry','wb').close()\nprint('dep')\n")
        self.add("Makefile", "VALUE := $(shell python3 -I -B make_data.py)\nall: $(VALUE)\ndep: ;\n")
        with self.session(created_files=1) as session:
            session.make("all", commands={
                "python3 -I -B make_data.py": Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/make_data.py"), code=("make_data.py",),
                ),
            })
            self.assertEqual(session.files_created, 1)
            with self.assertRaisesRegex(MakeProbeError, "file-creation budget"):
                probe_generated_registry(session.loader, command=command, session=session)
        self.assert_clean(session)

    def test_recursive_bind_restricts_submounts_and_preserves_explicit_exceptions(self):
        self.add("Makefile", "all: ;\n")
        fixture = self.directory / "mount fixture"
        for name in ("source/nested", "restricted", "runtime", "root/inherited", "root/repo", "root/control"):
            (fixture / name).mkdir(parents=True, exist_ok=True)
        (fixture / "source/value").write_bytes(b"owned")
        (fixture / "source/helper").touch()
        shutil.copyfile("/usr/bin/true", fixture / "source/program")
        (fixture / "source/program").chmod(0o755)
        program = r'''
import ctypes,errno,json,os,shutil,subprocess,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from sandbox_exec import bind
fixture=Path(sys.argv[2])
source=fixture/"source"
root=fixture/"root"
libc=ctypes.CDLL(None,use_errno=True)
def tmpfs(path,flags=0):
    assert libc.mount(b"tmpfs",os.fsencode(path),b"tmpfs",flags,b"size=1m,mode=0755")==0,ctypes.get_errno()
    (path/"value").write_bytes(b"owned")
    shutil.copyfile("/usr/bin/true",path/"program")
    (path/"program").chmod(0o755)
tmpfs(source/"nested")
(source/"nested/deep").mkdir()
tmpfs(source/"nested/deep",os.ST_NOEXEC)
tmpfs(root/"inherited")
relative=("", "nested", "nested/deep")
original={name:os.statvfs(source/name).f_flag for name in relative}
def readonly(path):
    try: (path/"value").write_bytes(b"forged")
    except OSError as error: assert error.errno==errno.EROFS,error
    else: raise AssertionError("readonly mount accepted a write")
def executable(path,allowed):
    try: result=subprocess.run([str(path)],check=False)
    except OSError as error:
        assert not allowed and error.errno in (errno.EACCES,errno.EPERM),error
    else: assert allowed and result.returncode==0,result
restricted=fixture/"restricted"
bind(source,restricted)
for name in relative:
    path=restricted/name
    assert os.statvfs(path).f_flag & 15 == 15
    readonly(path); executable(path/"program",False)
runtime=fixture/"runtime"
bind(source,runtime,executable=True)
for name in relative:
    path=runtime/name
    assert os.statvfs(path).f_flag & 7 == 7
    readonly(path)
    executable(path/"program",not bool(original[name] & os.ST_NOEXEC))
bind(root,root,executable=True)
assert os.statvfs(root/"inherited").f_flag & 7 == 7
readonly(root/"inherited")
bind(source,root/"repo")
bind(source,root/"control",writable=True)
for name in relative:
    assert os.statvfs(root/"repo"/name).f_flag & 15 == 15
    path=root/"control"/name
    assert os.statvfs(path).f_flag & 15 == 14
    (path/"value").write_bytes(b"explicit writable exception")
    executable(path/"program",False)
bind(source/"nested/program",root/"control/helper",executable=True)
assert os.statvfs(root/"control/helper").f_flag & 15 == 7
executable(root/"control/helper",True)
assert {name:os.statvfs(source/name).f_flag for name in relative} == original
for name in relative: (source/name/"value").write_bytes(b"source remains writable")
print(json.dumps({"submount_levels":3,"source_flags_unchanged":True,
                  "root_sealed":True,"writable_exception":True,"executable_exception":True}))
'''
        with self.session() as session:
            result = session.budget.run(
                [*session.launcher, "/usr/bin/python3", "-I", "-S", "-c", program,
                 str(TRUSTED_ROOT), str(fixture)],
                env=ENVIRONMENT, privileged=session.sudo_drop,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {
                "submount_levels": 3, "source_flags_unchanged": True,
                "root_sealed": True, "writable_exception": True, "executable_exception": True,
            })
        self.assert_clean(session)
        self.assertFalse((fixture / "source/nested/value").exists())
        self.assertFalse((fixture / "root/inherited/value").exists())

    def test_recursive_mount_attribute_failure_has_no_top_only_fallback(self):
        from scripts.validation_ownership import sandbox_exec
        target = self.directory / "attribute target"
        target.mkdir()
        calls = []
        def unavailable(number, descriptor, path, flags, attributes, size):
            value = ctypes.cast(attributes, ctypes.POINTER(sandbox_exec.MountAttributes)).contents
            calls.append((number.value, path.value, flags.value, value.attr_set, value.attr_clr,
                          value.propagation, value.userns_fd, size.value))
            self.assertEqual(os.fstat(descriptor.value).st_ino, target.stat().st_ino)
            ctypes.set_errno(errno.ENOSYS)
            return -1
        library = SimpleNamespace(syscall=unavailable)
        descriptors = set(os.listdir("/proc/self/fd"))
        with patch.object(sandbox_exec, "mount") as mount, patch.object(
            sandbox_exec.ctypes, "CDLL", return_value=library,
        ):
            with self.assertRaises(OSError) as caught:
                sandbox_exec.bind(self.root, target)
        self.assertEqual(caught.exception.errno, errno.ENOSYS)
        mount.assert_called_once_with(self.root, target, sandbox_exec.MS_BIND | sandbox_exec.MS_REC)
        self.assertEqual(calls, [(442, b"", 0x9000, 15, 0, 0, 0, 32)])
        self.assertEqual(set(os.listdir("/proc/self/fd")), descriptors)

    def test_root_setup_rejects_unsupported_recursive_attributes_before_supervision(self):
        from scripts.validation_ownership import sandbox_exec
        config = self.directory / "mount-config.json"
        config.write_text(json.dumps({"root": str(self.root), "mounts": []}))
        supervise = Mock(return_value=0)
        flags = Mock(wraps=sys.flags, isolated=True, no_site=True)
        with patch.object(sys, "path", list(sys.path)), patch.object(
            sys, "argv", ["sandbox_exec.py", str(config)],
        ), patch.object(
            sys, "flags", flags,
        ), patch.object(sandbox_exec, "mount"), patch.object(
            sandbox_exec, "recursive_attributes", side_effect=OSError(errno.ENOSYS, "unsupported"),
        ), patch.dict(sys.modules, {"syscall_guard": SimpleNamespace(supervise=supervise)}):
            with self.assertRaises(OSError) as caught:
                sandbox_exec.main()
        self.assertEqual(caught.exception.errno, errno.ENOSYS)
        supervise.assert_not_called()

    @contextmanager
    def owned_process(self, argv):
        child = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, env=ENVIRONMENT,
        )
        descriptor = os.pidfd_open(child.pid)
        try:
            yield child, descriptor
        finally:
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            os.close(descriptor)
            for stream in (child.stdin, child.stdout, child.stderr):
                stream.close()

    def test_reaped_process_handles_never_signal_an_unrelated_owned_canary(self):
        from scripts.validation_ownership import lifecycle
        for terminate in (ProbeBudget._terminate, lifecycle.terminate):
            with self.subTest(terminate=terminate):
                with self.owned_process([
                    "/usr/bin/python3", "-I", "-S", "-c",
                    "import os; os.write(1,b'ready\\n'); os.read(0,1)",
                ]) as (canary, descriptor):
                    self.assertEqual(canary.stdout.readline(), b"ready\n")
                    completed = subprocess.Popen(["/usr/bin/true"], start_new_session=True)
                    completed.wait()
                    # Model kernel reuse without exhausting PID space or
                    # addressing anything except this test-owned canary.
                    completed.pid = canary.pid
                    terminate(completed)
                    self.assertIsNone(canary.poll())
                    canary.stdin.write(b"x")
                    canary.stdin.flush()
                    self.assertEqual(canary.wait(timeout=5), 0)

    def test_tracee_cleanup_uses_pinned_identity_after_numeric_pid_reuse(self):
        from scripts.validation_ownership.syscall_guard import Process, signal_tracees
        with self.owned_process([
            "/usr/bin/python3", "-I", "-S", "-c",
            "import os; os.write(1,b'ready\\n'); os.read(0,1)",
        ]) as (canary, descriptor):
            self.assertEqual(canary.stdout.readline(), b"ready\n")
            with self.owned_process(["/usr/bin/true"]) as (completed, dead_identity):
                completed.wait()
                signal_tracees({canary.pid: Process("command", pidfd=dead_identity)})
                self.assertIsNone(canary.poll())
                canary.stdin.write(b"x")
                canary.stdin.flush()
                self.assertEqual(canary.wait(timeout=5), 0)

    def test_budget_normal_completion_reaps_group_and_escaped_descendants(self):
        for escaped in (False, True):
            with self.subTest(escaped=escaped):
                natural = self.directory / ("natural-exit-" + str(escaped))
                program = (
                    "import os,time\nready=os.pipe()\nchild=os.fork()\n"
                    "if child==0:\n"
                    + (" os.setsid()\n" if escaped else "")
                    + " os.write(ready[1],b'x')\n time.sleep(2)\n"
                    f" open({str(natural)!r},'wb').write(b'not terminated')\n"
                    " os._exit(0)\n"
                    "os.read(ready[0],1)\nos.write(1,str(child).encode()+b'\\n')\nos._exit(0)\n"
                )
                budget = ProbeBudget(Limits(seconds=5))
                result = budget.run(
                    ["/usr/bin/python3", "-I", "-S", "-c", program], env=ENVIRONMENT,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                descendant = int(result.stdout)
                self.assertFalse(natural.exists())
                self.assertFalse(Path(f"/proc/{descendant}").exists())
                self.assertFalse(budget.children)

    def test_watchdog_payload_stdin_is_binary_and_separate_from_lifetime(self):
        data = bytes(range(256)) * 400
        program = (
            "import os\n"
            "for descriptor in range(3,32):\n"
            " try: os.fstat(descriptor)\n"
            " except OSError: pass\n"
            " else: raise SystemExit(7)\n"
            "while True:\n"
            " data=os.read(0,4096)\n"
            " if not data: break\n"
            " os.write(1,data)\n"
        )
        budget = ProbeBudget()
        output = budget.run(
            ["/usr/bin/python3", "-I", "-S", "-c", program],
            env=ENVIRONMENT, input_data=data,
        )
        self.assertEqual(output.returncode, 0, output.stderr)
        self.assertEqual(output.stdout, data)
        self.assertFalse(budget.children)

    def test_budget_launch_interruption_waits_for_handle_ownership(self):
        original = subprocess.Popen
        launched = []
        def interrupted(signum, frame):
            raise KeyboardInterrupt("owned launch interruption")
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            for payload in (None, b"bounded input"):
                with self.subTest(payload=payload):
                    budget = ProbeBudget(Limits(seconds=5))
                    def starting(argv, **kwargs):
                        child = original(argv, **kwargs)
                        launched.append(child)
                        os.kill(os.getpid(), signal.SIGTERM)
                        return child
                    with patch("subprocess.Popen", starting):
                        with self.assertRaises(KeyboardInterrupt):
                            budget.run(
                                ["/usr/bin/python3", "-I", "-S", "-c", "import time; time.sleep(2)"],
                                env=ENVIRONMENT, input_data=payload,
                            )
                    self.assertFalse(budget.children)
                    self.assertIsNotNone(launched[-1].poll())
        finally:
            signal.signal(signal.SIGTERM, previous)
            for child in launched:
                child.stdin.close()
                child.wait(timeout=5)
                child.stdout.close()
                child.stderr.close()

    def test_missing_pidfd_support_rejects_before_payload_or_tracee_launch(self):
        from scripts.validation_ownership import lifecycle, syscall_guard
        read, write = os.pipe()
        before = set(os.listdir("/proc/self/fd"))
        try:
            with patch("signal.pidfd_send_signal", side_effect=OSError(errno.ENOSYS, "unsupported")), patch(
                "subprocess.Popen",
            ) as launch, patch("os.fork") as fork:
                with self.assertRaises(OSError):
                    lifecycle.run(["/usr/bin/true"], time.monotonic() + 5, lifetime=read)
                with self.assertRaises(OSError):
                    syscall_guard.supervise({}, None)
                launch.assert_not_called()
                fork.assert_not_called()
            self.assertEqual(set(os.listdir("/proc/self/fd")), before)
        finally:
            os.close(read)
            os.close(write)

    @staticmethod
    def stack_growth_source():
        return r'''
#include <signal.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/wait.h>
static __attribute__((noinline)) void grow(int ready, int finish) {
    volatile unsigned char stack[2*1024*1024];
    unsigned i; char byte;
    for (i=0;i<sizeof(stack);i+=4096) stack[i]=7;
    if (ready<0) {
        if (write(1,"STACKS_HELD\n",12)!=12) _exit(3);
    } else {
        if (write(ready,"G",1)!=1 || read(finish,&byte,1)!=1) _exit(3);
    }
    if (stack[4096]!=7) _exit(4);
}
int main(int argc, char **argv) {
    int start[2], ready[2], finish[2], i, status, count; char byte;
    if(argc!=2) return 1;
    count=atoi(argv[1]);
    if(count==0) { raise(SIGSTOP); grow(-1,-1); return 0; }
    if(count<1 || count>8 || pipe(start)||pipe(ready)||pipe(finish)) return 1;
    for (i=0;i<count;++i) {
        pid_t child=fork();
        if (child<0) return 2;
        if (!child) { if(read(start[0],&byte,1)!=1) _exit(2); grow(ready[1],finish[0]); _exit(0); }
    }
    if(write(start[1],"XXXXXXXX",count)!=count) return 3;
    for(i=0;i<count;++i) if(read(ready[0],&byte,1)!=1) return 4;
    if(write(1,"STACKS_HELD\n",12)!=12) return 5;
    if(write(finish[1],"XXXXXXXX",count)!=count) return 6;
    for(i=0;i<count;++i) if(wait(&status)<0 || status) return 7;
    return 0;
}
'''

    def memory_policy(self, limit):
        from scripts.validation_ownership.syscall_guard import Policy
        return Policy({
            "root": str(self.root), "mode": "command", "code": [], "sources": [],
            "enumerations": [], "executables": [], "python_version": "3.12",
            "argv": [], "memory_limit": limit,
        })

    def test_post_fork_stack_growth_is_funded_before_execution(self):
        self.add("stack.c", self.stack_growth_source())
        with self.session() as session:
            tool = session.compile_native(("stack.c",))
            session.budget.limits = replace(session.budget.limits, address_space_bytes=64*1024*1024)
            self.assertEqual(session.native(tool, ("2",)).stdout, b"STACKS_HELD\n")
        self.assert_clean(session)
        session = self.session()
        with session:
            tool = session.compile_native(("stack.c",))
            session.budget.limits = replace(session.budget.limits, address_space_bytes=32*1024*1024)
            with self.assertRaisesRegex(MakeProbeError, "aggregate address-space"):
                session.native(tool, ("8",))
        self.assert_clean(session)

    def test_kernel_virtual_credits_block_stack_faults_without_sampling(self):
        from scripts.validation_ownership.syscall_guard import Process
        self.add("stack.c", self.stack_growth_source())
        with self.session() as session:
            tool = session.compile_native(("stack.c",))
            for funded in (False, True):
                with self.subTest(funded=funded):
                    with self.owned_process([str(tool.path), "0"]) as (child, descriptor):
                        waited, status = os.waitpid(child.pid, os.WUNTRACED)
                        self.assertEqual(waited, child.pid)
                        self.assertTrue(os.WIFSTOPPED(status))
                        resource.prlimit(child.pid, resource.RLIMIT_CORE, (0, 0))
                        policy = self.memory_policy(64*1024*1024)
                        size = policy.virtual_memory(child.pid)
                        if funded:
                            policy.config["memory_limit"] = size + 128*1024
                            record = Process("command", memory_group=child.pid)
                            policy.processes[child.pid] = record
                            policy.reserve_memory(child.pid, record, 0)
                            self.assertEqual(resource.prlimit(child.pid, resource.RLIMIT_AS)[0], record.memory_limit)
                            self.assertLessEqual(record.memory_limit, policy.config["memory_limit"])
                        signal.pidfd_send_signal(descriptor, signal.SIGCONT)
                        child.wait(timeout=5)
                        self.assertEqual(child.returncode, -signal.SIGSEGV if funded else 0)
                        output = child.stdout.read()
                        self.assertEqual(output, b"" if funded else b"STACKS_HELD\n")
        self.assert_clean(session)

    def test_kernel_limits_are_funded_by_one_aggregate_virtual_pool(self):
        from scripts.validation_ownership.syscall_guard import Process, Violation
        program = [
            "/usr/bin/python3", "-I", "-S", "-c",
            "import os,signal; os.kill(os.getpid(),signal.SIGSTOP); os.read(0,1)",
        ]
        with self.owned_process(program) as (first, first_fd), self.owned_process(program) as (second, second_fd):
            for child in (first, second):
                _, status = os.waitpid(child.pid, os.WUNTRACED)
                self.assertTrue(os.WIFSTOPPED(status))
            policy = self.memory_policy(64*1024*1024)
            records = {
                child.pid: Process("command", memory_group=child.pid)
                for child in (first, second)
            }
            policy.processes = records
            policy.reserve_memory(first.pid, records[first.pid], 4*1024*1024)
            policy.reserve_memory(second.pid, records[second.pid], 0)
            limits = {
                pid: resource.prlimit(pid, resource.RLIMIT_AS)[0] for pid in records
            }
            self.assertEqual(limits, {pid: record.memory_limit for pid, record in records.items()})
            self.assertLessEqual(sum(limits.values()), policy.config["memory_limit"])
            with self.assertRaisesRegex(Violation, "aggregate address-space"):
                policy.reserve_memory(second.pid, records[second.pid], policy.config["memory_limit"])
            with self.assertRaisesRegex(Violation, "before kernel grant"):
                policy.assign_memory(records[first.pid], policy.config["memory_limit"])
            self.assertEqual(limits, {
                pid: resource.prlimit(pid, resource.RLIMIT_AS)[0] for pid in records
            })
            policy.reserve_memory(first.pid, records[first.pid], 0, copies=1)
            self.assertGreater(records[first.pid].memory_reservation, 0)
            self.assertLessEqual(sum(
                record.memory_limit + record.memory_reservation for record in records.values()
            ), policy.config["memory_limit"])
            records[first.pid].memory_reservation = 0
            policy.reserve_exec(second.pid, records[second.pid])
            self.assertLessEqual(sum(
                resource.prlimit(pid, resource.RLIMIT_AS)[0] for pid in records
            ), policy.config["memory_limit"])
            policy.finish_exec(second.pid, records[second.pid])
            self.assertLessEqual(policy.memory_peak, policy.config["memory_limit"])

    def test_repeated_vfork_exec_preserves_virtual_credit_ownership(self):
        self.add("exec.c", (
            "#define _GNU_SOURCE\n#include <sys/wait.h>\n#include <unistd.h>\n"
            "int main(int argc,char **argv) {\n"
            " int i,status; (void)argv;\n"
            " if(argc>1) return write(1,\"child\\n\",6)==6 ? 0 : 1;\n"
            " for(i=0;i<4;++i) {\n"
            "  pid_t child=vfork();\n"
            "  if(child<0) return 2;\n"
            "  if(!child) { execl(\"/native/tool\",\"/native/tool\",\"child\",(char *)0); _exit(3); }\n"
            "  if(waitpid(child,&status,0)!=child || status) return 4;\n"
            " }\n return write(1,\"parent\\n\",7)==7 ? 0 : 5;\n}\n"
        ))
        with self.session() as session:
            tool = session.compile_native(("exec.c",))
            session.budget.limits = replace(session.budget.limits, address_space_bytes=32*1024*1024)
            run = session._sandbox_run
            def compiler_driver(root, **kwargs):
                # This owned driver exercises the compiler's supported repeated
                # exec domain; registered commands now deliberately deny reexec.
                self.assertEqual(kwargs["mode"], "command")
                self.assertEqual(kwargs["argv"], ["/native/tool"])
                kwargs["mode"] = "compile"
                return run(root, **kwargs)
            with patch.object(session, "_sandbox_run", compiler_driver):
                self.assertEqual(session.native(tool).stdout, b"child\n"*4 + b"parent\n")
        self.assert_clean(session)

    def test_shared_vm_growth_counts_every_live_member(self):
        self.add("shared.c", (
            "#define _GNU_SOURCE\n#include <sched.h>\n#include <signal.h>\n"
            "#include <stdlib.h>\n#include <sys/mman.h>\n#include <sys/wait.h>\n#include <unistd.h>\n"
            "static int child(void *argument) {\n"
            " size_t size=(size_t)argument;\n"
            " void *area=mmap(0,size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);\n"
            " if(area==MAP_FAILED) _exit(3);\n"
            " if(write(1,\"shared\\n\",7)!=7 || munmap(area,size)) _exit(4);\n _exit(0);\n}\n"
            "int main(int argc,char **argv) {\n"
            " if(argc!=2) return 1;\n"
            " void *stack=mmap(0,65536,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANONYMOUS,-1,0);\n"
            " if(stack==MAP_FAILED) return 2;\n"
            " int status; size_t size=strtoul(argv[1],0,10)*1024*1024;\n"
            " pid_t pid=clone(child,(char *)stack+65536,CLONE_VM|CLONE_VFORK|SIGCHLD,(void *)size);\n"
            " if(pid<0 || waitpid(pid,&status,0)!=pid || status) return 3;\n"
            " return munmap(stack,65536)!=0;\n}\n"
        ))
        for size in (4, 16):
            with self.subTest(size=size):
                session = self.session()
                with session:
                    tool = session.compile_native(("shared.c",))
                    session.budget.limits = replace(session.budget.limits, address_space_bytes=32*1024*1024)
                    if size == 4:
                        self.assertEqual(session.native(tool, (str(size),)).stdout, b"shared\n")
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "aggregate address-space"):
                            session.native(tool, (str(size),))
                self.assert_clean(session)

    def test_make_option_channels_cannot_inject_unrequested_evaluation(self):
        self.add("Makefile", (
            "ifeq ($(INJECTED),yes)\nDEP = injected\nelse\nDEP = ordinary\nendif\n"
            "all: $(DEP)\n\t@printf '%s' '$^'\ninjected ordinary: ;\n"
        ))
        self.add("other.mk", "INJECTED := yes\n")
        before = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env={**ENVIRONMENT, "GNUMAKEFLAGS": "--eval=INJECTED=yes"},
            capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(before.stdout, b"injected")
        for name in ("GNUMAKEFLAGS", "MAKEFLAGS"):
            for origin in ("environment", "command-line"):
                for value in ("--eval=INJECTED=yes", "-f other.mk"):
                    with self.subTest(name=name, origin=origin, value=value):
                        session = self.session()
                        with session:
                            runs = session.budget.runs
                            with self.assertRaisesRegex(MakeProbeError, "execution-authority Make assignment"):
                                session.make("all", assignments=((origin, name, value),))
                            self.assertEqual(session.budget.runs, runs)
                        self.assert_clean(session)

    def ordinary_assignment_context(self, assignments, names):
        environment, cli = dict(ENVIRONMENT), []
        for origin, name, value in assignments:
            if origin == "environment":
                environment[name] = value
            else:
                cli.append(name + "=" + value)
        normal = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", *cli, "all"],
            cwd=self.root, env=environment, capture_output=True, check=True, timeout=10,
        )
        lines = normal.stdout.decode("utf-8").splitlines()
        self.assertEqual(len(lines), 1 + 3*len(names), normal.stdout)
        return (
            [{"name": name, "order_only": False} for name in lines[0].split()],
            {
                name: dict(zip(("value", "origin", "flavor"), lines[1 + index*3:4 + index*3]))
                for index, name in enumerate(names)
            },
        )

    def test_equivalent_assignment_order_preserves_actual_make_identity(self):
        self.add("Makefile", (
            "all: $(B)\n"
            "\t@printf '%s\\n' '$^' '$(A)' '$(origin A)' '$(flavor A)' "
            "'$(B)' '$(origin B)' '$(flavor B)'\n"
            "one-two: ;\n"
        ))
        for origins in (
            ("environment", "environment"), ("command-line", "command-line"),
            ("environment", "command-line"), ("command-line", "environment"),
        ):
            with self.subTest(origins=origins):
                assignments = ((origins[0], "A", "one"), (origins[1], "B", "$(A)-two"))
                normal, observed = [], []
                with self.session() as session:
                    for order in (assignments, tuple(reversed(assignments))):
                        context = self.ordinary_assignment_context(order, ("A", "B"))
                        result = session.make("all", variables=("A", "B"), assignments=order)
                        self.assertEqual(result.semantics["files"][0]["prerequisites"], context[0])
                        self.assertEqual(result.semantics["domains"], context[1])
                        self.assertEqual(result.events, ())
                        normal.append(context)
                        observed.append(result)
                self.assert_clean(session)
                self.assertEqual(normal[0], normal[1])
                self.assertEqual(observed[0].execution_digest, observed[1].execution_digest)
                self.assertEqual(observed[0].semantic_digest, observed[1].semantic_digest)
                self.assertEqual(observed[0].semantics, observed[1].semantics)

    def test_assignment_identity_preserves_values_origins_and_observed_order(self):
        names = ("A", "B", "STATE")
        recipe = (
            "\t@printf '%s\\n' '$^' '$(A)' '$(origin A)' '$(flavor A)' "
            "'$(B)' '$(origin B)' '$(flavor B)' '$(STATE)' '$(origin STATE)' '$(flavor STATE)'\n"
        )
        self.add("Makefile", "all: $(B)\n" + recipe + "one-two other-two: ;\n")
        states = (
            (("command-line", "A", "one"), ("command-line", "B", "$(A)-two")),
            (("command-line", "A", "other"), ("command-line", "B", "$(A)-two")),
            (("environment", "A", "one"), ("command-line", "B", "$(A)-two")),
        )
        with self.session() as session:
            results = []
            for state in states:
                normal = self.ordinary_assignment_context(state, names)
                result = session.make("all", variables=names, assignments=state)
                self.assertEqual(result.semantics["files"][0]["prerequisites"], normal[0])
                self.assertEqual(result.semantics["domains"], normal[1])
                results.append(result)
            self.assertEqual(len({result.semantic_digest for result in results}), len(states))
            self.assertEqual(len({result.execution_digest for result in results}), 1)
        self.assert_clean(session)
        self.add("Makefile", (
            "STATE = $(MAKEOVERRIDES)\n"
            "all: $(if $(filter B=%,$(firstword $(MAKEOVERRIDES))),right,left)\n"
            + recipe + "right left: ;\n"
        ))
        with self.session() as session:
            results = []
            for state in (states[0], tuple(reversed(states[0]))):
                normal = self.ordinary_assignment_context(state, names)
                result = session.make("all", variables=names, assignments=state)
                self.assertEqual(result.semantics["files"][0]["prerequisites"], normal[0])
                self.assertEqual(result.semantics["domains"], normal[1])
                results.append(result)
            self.assertNotEqual(
                results[0].semantics["files"][0]["prerequisites"],
                results[1].semantics["files"][0]["prerequisites"],
            )
            self.assertNotEqual(results[0].semantics["domains"], results[1].semantics["domains"])
            self.assertNotEqual(results[0].semantic_digest, results[1].semantic_digest)
        self.assert_clean(session)

    def test_conditional_graphs_match_ordinary_make_not_probe_markers(self):
        controls = (
            "ifeq ($(SHELL),/bin/vo-shell)",
            "ifeq ($(origin SHELL),command line)",
            "ifeq ($(MAKE),/bin/vo-make)",
            "ifeq ($(origin MAKE),command line)",
            "ifeq ($(origin .SHELLFLAGS),command line)",
            "ifneq ($(findstring n,$(firstword $(MAKEFLAGS))),)",
            "ifneq ($(findstring B,$(firstword $(MAKEFLAGS))),)",
            "ifneq ($(findstring j1,$(MAKEFLAGS)),)",
            "ifneq ($(findstring --no-print-directory,$(MAKEFLAGS)),)",
            "ifneq ($(origin LD_PRELOAD),undefined)",
            "ifneq ($(origin VO_OBSERVE_TARGET),undefined)",
            "ifneq ($(origin SOURCE_DATE_EPOCH),undefined)",
        )
        for condition in controls:
            with self.subTest(condition=condition):
                self.add("Makefile", (
                    condition + "\nDEP = hidden\nelse\nDEP = genuine\nendif\n"
                    "all: $(DEP)\n\t@printf '%s' '$^'\nhidden genuine: ;\n"
                ))
                normal = subprocess.run(
                    ["/usr/bin/make", "-f", "Makefile", "all"],
                    cwd=self.root, env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
                )
                self.assertEqual(normal.stdout, b"genuine")
                with self.session() as session:
                    observed = session.make("all")
                    self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                        {"name": normal.stdout.decode("ascii"), "order_only": False},
                    ])
                    self.assertEqual(observed.events, ())
                self.assert_clean(session)

    def test_default_file_and_requested_domains_preserve_production_make_context(self):
        names = (
            "SHELL", "MAKE", "MAKE_COMMAND", "MAKEFLAGS", "MFLAGS", "MAKELEVEL",
            "LD_PRELOAD", "GNUMAKEFLAGS", "MODE", "FLAGS", "FLAGS_ORIGIN", "FLAGS_FLAVOR",
        )
        aliases = "".join(
            f"CTX_{index}_{field} = $({form}{name})\n"
            for index, name in enumerate(names)
            for field, form in (("value", ""), ("origin", "origin "), ("flavor", "flavor "))
        )
        arguments = " ".join(
            f"'$(CTX_{index}_{field})'"
            for index in range(len(names)) for field in ("value", "origin", "flavor")
        )
        for prefix, assignments in (
            ("MODE ?= file\n", ()),
            ("SHELL := /bin/bash\nMODE ?= file\n", (("environment", "MODE", "environment"),)),
            (".POSIX:\nMODE ?= file\n", (("command-line", "MODE", "command"),)),
        ):
            with self.subTest(prefix=prefix, assignments=assignments):
                self.add("Makefile", (
                    prefix + "FLAGS = $(.SHELLFLAGS)\nFLAGS_ORIGIN = $(origin .SHELLFLAGS)\n"
                    "FLAGS_FLAVOR = $(flavor .SHELLFLAGS)\n" + aliases
                    + "ifeq ($(MODE),command)\nDEP = command\nelse\nDEP = ordinary\nendif\n"
                    + f"all: $(DEP)\n\t@printf '%s\\n' '$^' {arguments}\n"
                    + "command ordinary: ;\n"
                ))
                environment = dict(ENVIRONMENT)
                cli = []
                for origin, name, value in assignments:
                    if origin == "environment":
                        environment[name] = value
                    else:
                        cli.append(name + "=" + value)
                normal = subprocess.run(
                    ["/usr/bin/make", "-f", "Makefile", *cli, "all"],
                    cwd=self.root, env=environment, capture_output=True, check=True, timeout=10,
                )
                lines = normal.stdout.decode("ascii").splitlines()
                self.assertEqual(len(lines), 1 + 3 * len(names), normal.stdout)
                expected = {
                    name: dict(zip(("value", "origin", "flavor"), lines[1 + index * 3:4 + index * 3]))
                    for index, name in enumerate(names)
                }
                with self.session() as session:
                    observed = session.make("all", variables=names, assignments=assignments)
                    self.assertEqual(observed.semantics["domains"], expected)
                    self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                        {"name": lines[0], "order_only": False},
                    ])
                    self.assertEqual(observed.events, ())
                self.assert_clean(session)

    def test_secondary_expansion_keeps_target_specific_shell_context(self):
        self.add("Makefile", (
            ".SECONDEXPANSION:\nall: SHELL := /bin/bash\n"
            "all: $$(if $$(filter /bin/bash,$$(SHELL)),file-shell,wrong-context)\n"
            "\t@printf '%s' '$^'\nfile-shell wrong-context: ;\n"
        ))
        normal = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        )
        self.assertEqual(normal.stdout, b"file-shell")
        with self.session() as session:
            observed = session.make("all", variables=("SHELL",))
            self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                {"name": "file-shell", "order_only": False},
            ])
            self.assertEqual(observed.semantics["files"][0]["variables"]["SHELL"], {
                "value": "/bin/bash", "origin": "file", "flavor": "simple",
            })
            self.assertEqual(observed.semantics["domains"]["SHELL"]["value"], "/bin/sh")
        self.assert_clean(session)

    def test_recipe_commands_are_metadata_but_make_expansion_effects_still_reject(self):
        self.add("Makefile", "all:\n\t@printf '%s' recipe > recipe-effect\n")
        subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
            env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
        )
        effect = self.root / "recipe-effect"
        self.assertEqual(effect.read_bytes(), b"recipe")
        effect.unlink()
        with self.session() as session:
            observed = session.make("all")
            self.assertFalse(effect.exists())
            self.assertFalse((session.tree / "recipe-effect").exists())
            self.assertEqual(observed.events, ())
            self.assertIn("recipe-effect", observed.semantics["files"][0]["recipe"])
        self.assert_clean(session)
        self.add("Makefile", "all:\n\t$(file >recipe-effect,forged)\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "write outside"):
            with session:
                session.make("all")
        self.assertFalse(effect.exists())
        self.assert_clean(session)

    def test_dispatch_classifies_identical_recipe_and_expansion_by_native_context(self):
        for prefix in ("", ".POSIX:\n"):
            for command in ("printf %s dynamic", "printf '%s' dynamic; printf ''"):
                with self.subTest(prefix=prefix, command=command):
                    self.add("Makefile", (
                        prefix + f"VALUE := $(shell {command})\nall: $(VALUE)\n"
                        f"\t@{command}\ndynamic: ;\n"
                    ))
                    with self.session() as session:
                        observed = session.make(
                            "all", variables=("VALUE",),
                            commands={command: Command(("/usr/bin/printf", "%s", "dynamic"))},
                        )
                        self.assertEqual(observed.semantics["domains"]["VALUE"]["value"], "dynamic")
                        self.assertEqual(len(observed.events), 1)
                        self.assertEqual(observed.events[0]["match"], 0)
                        self.assertEqual(observed.stdout, b"")
                    self.assert_clean(session)

    def test_recursive_and_makefile_remake_dispatch_still_requires_real_mappings(self):
        self.add("Makefile", "all:\n\t+@printf %s recursive\n")
        for registered in (False, True):
            with self.subTest(registered=registered):
                session = self.session()
                with session:
                    if registered:
                        observed = session.make("all", commands={
                            "printf %s recursive": Command(("/usr/bin/printf", "%s", "recursive")),
                        })
                        self.assertEqual(observed.stdout, b"recursive")
                        self.assertEqual(len(observed.events), 1)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "unregistered eager/recursive"):
                            session.make("all")
                self.assert_clean(session)
        self.add("Makefile", "include missing.mk\nmissing.mk:\n\t@printf missing\nall: ;\n")
        session = self.session()
        with self.assertRaisesRegex(MakeProbeError, "unregistered eager/recursive"):
            with session:
                session.make("all")
        self.assert_clean(session)

    def test_private_dispatch_and_observer_inputs_cannot_be_candidate_authority(self):
        for operation, expected in (
            ("$(file </control/interceptor)", "supervisor channel denied: read /control/interceptor"),
            ("$(file </lib/vo-observer.so)", "supervisor observer image access denied"),
            ("$(wildcard /lib/*)", "uncaptured Make runtime access: read /lib"),
        ):
            with self.subTest(operation=operation):
                self.add("Makefile", f"VALUE := {operation}\nall: ;\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, re.escape(expected)):
                    with session:
                        session.make("all")
                self.assert_clean(session)
        self.add("Makefile", "VALUE := $(shell printf %s observed)\nall: ;\n")
        with self.session() as session:
            result = session.make("all", variables=("VALUE",), commands={
                "printf %s observed": Command(("/usr/bin/printf", "%s", "observed")),
            })
            self.assertEqual(result.semantics["domains"]["VALUE"]["value"], "observed")
            self.assertTrue(all(event["match"] >= 0 for event in result.events))
        self.assert_clean(session)
        from scripts.validation_ownership.syscall_guard import VO_READY, VO_DISPATCH, VO_QUERY_KIND, VO_METADATA
        for marker in (VO_READY, VO_DISPATCH, VO_QUERY_KIND, VO_METADATA):
            with self.subTest(marker=marker):
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "unauthenticated"):
                    with session:
                        session.command(Command((
                            "/usr/bin/python3", "-I", "-c",
                            "import ctypes; ctypes.CDLL(None).syscall(39, ctypes.c_ulong("
                            + str(marker) + "), 0, 0)",
                        )))
                self.assert_clean(session)

    def test_partial_scratch_setup_releases_created_parents_and_descriptors(self):
        self.add("Makefile", "all: ;\n")
        original_open, original_mkdir = os.open, os.mkdir
        failures = ["tracked", "open", "mkdir", "long-name", "interrupt"]
        if os.geteuid() != 0:
            failures.append("inaccessible")
        for failure in failures:
            with self.subTest(failure=failure):
                self.scratch = self.root / "partial" / "parents" / ("x" * 300 if failure == "long-name" else "leaf")
                entries = dict(self.entries)
                if failure == "tracked":
                    entries["partial/parents/leaf"] = GitTreeEntry(
                        "partial/parents/leaf", "100644", "blob", "0" * 40,
                    )
                primary = OSError(errno.EIO, "owned scratch setup failure")
                def opening(path, flags, *args, **kwargs):
                    if path == "leaf" and kwargs.get("dir_fd") is not None:
                        if failure == "open":
                            raise primary
                        if failure == "interrupt":
                            raise KeyboardInterrupt("owned scratch interruption")
                    return original_open(path, flags, *args, **kwargs)
                def making(path, mode=0o777, *, dir_fd=None):
                    if path == "leaf" and failure == "mkdir":
                        raise primary
                    result = original_mkdir(path, mode, dir_fd=dir_fd)
                    if path == "leaf" and failure == "inaccessible":
                        os.chmod(path, 0, dir_fd=dir_fd)
                    return result
                descriptors = set(os.listdir("/proc/self/fd"))
                budget = ProbeBudget()
                session = ProbeSession(
                    AuthorityLoader(self.root, GitTreeEntries(entries, budget=budget), budget=budget),
                    scratch_root=self.scratch, budget=budget,
                )
                with patch("os.open", opening), patch("os.mkdir", making):
                    with self.assertRaises(KeyboardInterrupt if failure == "interrupt" else MakeProbeError) as caught:
                        with session:
                            self.fail("unsafe scratch setup was admitted")
                if failure in {"open", "mkdir"}:
                    self.assertIs(caught.exception.__cause__, primary)
                self.assertEqual(set(os.listdir("/proc/self/fd")), descriptors)
                self.assertFalse((self.root / "partial").exists())
                self.assert_clean(session)

    def test_scratch_setup_interruption_waits_for_resource_ownership(self):
        self.add("Makefile", "all: ;\n")
        original = os.mkdir
        sent = False
        def creating(path, mode=0o777, *, dir_fd=None):
            nonlocal sent
            result = original(path, mode, dir_fd=dir_fd)
            if dir_fd is not None and str(path).startswith("probe-"):
                os.kill(os.getpid(), signal.SIGTERM)
                sent = True
            return result
        session = self.session()
        with patch("os.mkdir", creating):
            with self.assertRaises(KeyboardInterrupt):
                with session:
                    self.fail("setup interruption was lost")
        self.assertTrue(sent)
        self.assert_clean(session)

    def test_scratch_cleanup_preserves_existing_parents_and_primary_failure(self):
        self.add("Makefile", "all: ;\n")
        existing = self.root / "existing"
        existing.mkdir()
        (existing / "keep").write_bytes(b"not allocator-owned")
        self.scratch = existing / "created" / "leaf"
        original_open, original_remove = os.open, os.rmdir
        primary = OSError(errno.EIO, "primary setup failure")
        def opening(path, flags, *args, **kwargs):
            if path == "leaf":
                raise primary
            return original_open(path, flags, *args, **kwargs)
        session = self.session()
        with patch("os.open", opening):
            with self.assertRaises(MakeProbeError) as caught:
                with session:
                    self.fail("setup failure disappeared")
        self.assertIs(caught.exception.__cause__, primary)
        self.assertEqual((existing / "keep").read_bytes(), b"not allocator-owned")
        self.assertFalse((existing / "created").exists())
        self.assert_clean(session)

        def removing(path, *args, **kwargs):
            if path == "leaf":
                raise PermissionError("modeled owned cleanup failure")
            return original_remove(path, *args, **kwargs)
        primary = OSError(errno.EIO, "primary setup failure")
        session = self.session()
        with patch("os.open", opening), patch("os.rmdir", removing):
            with self.assertRaises(MakeProbeError) as caught:
                with session:
                    self.fail("setup failure disappeared")
        self.assertIs(caught.exception.__cause__, primary)
        if hasattr(primary, "__notes__"):
            self.assertTrue(any("owned scratch cleanup failed" in note for note in primary.__notes__))
        self.assertEqual((existing / "keep").read_bytes(), b"not allocator-owned")

    def test_privileged_cleanup_delegates_before_wait_and_never_hides_permission_errors(self):
        child = SimpleNamespace(pid=999999999, stdin=Mock(), wait=Mock(), returncode=None)
        with patch("os.killpg", side_effect=PermissionError("modeled root-owned group")) as kill:
            ProbeBudget._terminate(child)
            ProbeBudget._terminate(child, privileged=True)
            kill.assert_not_called()
            self.assertEqual(child.stdin.close.call_count, 2)
            self.assertEqual(child.wait.call_count, 2)
        from scripts.validation_ownership import lifecycle
        child.wait.reset_mock()
        with patch("os.waitid", return_value=None), patch(
            "os.killpg", side_effect=PermissionError("owned watchdog signal denied"),
        ):
            with self.assertRaises(PermissionError):
                lifecycle.terminate(child)
            child.wait.assert_not_called()
        for argv, supplied in ((["/usr/bin/true"], None), ([*NAMESPACE_LAUNCHER, "/usr/bin/true"], b"input")):
            with patch("subprocess.Popen") as launch:
                with self.assertRaisesRegex(MakeProbeError, "guarded PID-namespace lifecycle"):
                    ProbeBudget().run(argv, env=ENVIRONMENT, privileged=True, input_data=supplied)
                launch.assert_not_called()
        mode = os.stat("/usr/bin/unshare")
        elevated = list(mode)
        elevated[0] |= stat.S_ISUID
        with patch.object(lifecycle.os, "stat", return_value=os.stat_result(elevated)), patch(
            "subprocess.Popen",
        ) as launch:
            with self.assertRaisesRegex(MakeProbeError, "unsupported privileged namespace lifecycle"):
                ProbeBudget().run([*NAMESPACE_LAUNCHER, "/usr/bin/true"], env=ENVIRONMENT, privileged=True)
            launch.assert_not_called()
        with patch.object(lifecycle.os, "getxattr", return_value=b"modeled file capabilities"), patch(
            "subprocess.Popen",
        ) as launch:
            with self.assertRaisesRegex(MakeProbeError, "file capabilities"):
                ProbeBudget().run([*NAMESPACE_LAUNCHER, "/usr/bin/true"], env=ENVIRONMENT, privileged=True)
            launch.assert_not_called()

    def test_privileged_budget_uses_real_watchdog_without_running_sudo(self):
        budget = ProbeBudget()
        original = subprocess.Popen
        invocations = []
        def same_uid_fixture(argv, **kwargs):
            self.assertEqual(argv[:7], [
                "/usr/bin/sudo", "-n", "--", "/usr/bin/python3", "-I", "-S", "-B",
            ])
            self.assertEqual(Path(argv[7]), TRUSTED_ROOT / "lifecycle.py")
            self.assertEqual(float(argv[8]), budget.deadline)
            self.assertEqual(argv[9], "--")
            self.assertEqual(tuple(argv[10:10 + len(NAMESPACE_LAUNCHER)]), NAMESPACE_LAUNCHER)
            self.assertEqual(kwargs["stdin"], subprocess.PIPE)
            self.assertTrue(kwargs["close_fds"])
            invocations.append(argv)
            # Keep the real watchdog/payload, but not the privilege or namespace
            # launcher. These fixtures must work where user namespaces reject.
            owned = [
                *argv[3:10], *argv[10 + len(NAMESPACE_LAUNCHER):],
            ]
            return original(owned, **kwargs)
        with patch("subprocess.Popen", same_uid_fixture), patch(
            "scripts.validation_ownership.budget.os.killpg",
            side_effect=PermissionError("outer caller cannot signal privileged groups"),
        ) as kill:
            result = budget.run(
                [*NAMESPACE_LAUNCHER, "/usr/bin/printf", "%s", "guarded payload"],
                env=ENVIRONMENT, privileged=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"guarded payload")
            kill.assert_not_called()
        self.assertEqual(len(invocations), 1)
        self.assertFalse(budget.children)

    def test_sudo_preflight_and_capsules_share_the_privileged_lifecycle_contract(self):
        self.add("Makefile", "all: ;\n")
        session = self.session()
        original_run = session.budget.run
        original_file = Path.is_file
        guarded = []
        def fake_namespace(argv, **kwargs):
            if argv[0] == "/usr/bin/unshare" and "--user" in argv:
                return subprocess.CompletedProcess(argv, 1, b"", b"modeled user namespace denial")
            if kwargs.get("privileged"):
                self.assertEqual(tuple(argv[:len(NAMESPACE_LAUNCHER)]), NAMESPACE_LAUNCHER)
                guarded.append(argv)
                if argv[-1] != "/usr/bin/true":
                    config = json.loads(Path(argv[-1]).read_bytes())
                    self.assertTrue(config["sudo_drop"])
                    Path(config["report"]).write_text(json.dumps({
                        "ok": True, "returncode": 0, "error": None,
                        "consumed": [], "code_consumed": [], "accessed": [],
                        "processes": 1, "live_process_peak": 1, "syscalls": 1, "written_bytes": 0,
                        "created_files": 0, "memory_peak": 1, "observation_bytes": 0, "observations": 0,
                        "metadata": encode_metadata_transport([]), "events": [],
                    }))
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            return original_run(argv, **kwargs)
        with patch.object(session.budget, "run", fake_namespace), patch.object(
            Path, "is_file", lambda path: str(path) == "/usr/bin/sudo" or original_file(path),
        ), patch("os.getuid", return_value=1000), patch("os.getgid", return_value=1000):
            with session:
                self.assertTrue(session.sudo_drop)
                session._sandbox_run(
                    session._new_root("lifecycle-contract"), mode="command",
                    argv=["/usr/bin/true"], environment=ENVIRONMENT,
                    mounts=[session._mount(session.tree, "/repo")],
                )
        self.assertEqual(len(guarded), 2)
        self.assertEqual(guarded[0][-1], "/usr/bin/true")
        self.assertEqual(Path(guarded[1][-2]).name, "sandbox_exec.py")
        self.assert_clean(session)

    def test_privileged_lifetime_closes_on_budget_rejection_and_interruption(self):
        original = subprocess.Popen
        def same_uid_fixture(argv, **kwargs):
            self.assertEqual(argv[:3], ["/usr/bin/sudo", "-n", "--"])
            self.assertEqual(tuple(argv[10:10 + len(NAMESPACE_LAUNCHER)]), NAMESPACE_LAUNCHER)
            return original([*argv[3:10], *argv[10 + len(NAMESPACE_LAUNCHER):]], **kwargs)
        for action in ("deadline", "output", "interrupt"):
            with self.subTest(action=action):
                budget = ProbeBudget(Limits(
                    seconds=0.5 if action == "deadline" else 10,
                    process_output_bytes=32 if action == "output" else 1024,
                ))
                charge = budget.charge
                def interrupt_output(category, size):
                    if action == "interrupt" and category == "output":
                        raise KeyboardInterrupt("modeled caller interruption")
                    charge(category, size)
                ready = self.directory / ("watchdog-ready-" + action)
                program = (
                    "import os,time\n"
                    f"with open({str(ready)!r}, 'wb') as marker: marker.write(b'payload started')\n"
                    "os.fork()\n"
                )
                if action != "deadline":
                    program += "os.write(1, b'x'*100)\n"
                program += "time.sleep(20)\n"
                with patch("subprocess.Popen", same_uid_fixture), patch.object(
                    budget, "charge", interrupt_output,
                ), patch("os.killpg", side_effect=PermissionError("outer caller lacks permission")) as kill:
                    expected = {
                        "deadline": "aggregate probe deadline",
                        "output": "process output exceeds streaming byte bound",
                        "interrupt": "modeled caller interruption",
                    }
                    with self.assertRaisesRegex(
                        KeyboardInterrupt if action == "interrupt" else MakeProbeError, expected[action],
                    ):
                        budget.run(
                            [*NAMESPACE_LAUNCHER, "/usr/bin/python3", "-I", "-c", program],
                            env=ENVIRONMENT, privileged=True,
                        )
                    kill.assert_not_called()
                self.assertEqual(ready.read_bytes(), b"payload started")
                self.assertFalse(budget.children)
                self.assertLess(time.monotonic() - budget.started, 5)

    def test_watchdog_rejects_missing_lifetime_and_kernel_support_before_launch(self):
        from scripts.validation_ownership import lifecycle
        read, write = os.pipe()
        try:
            with patch.object(lifecycle, "prctl", side_effect=OSError("unsupported kernel")), patch(
                "subprocess.Popen",
            ) as launch:
                with self.assertRaisesRegex(OSError, "unsupported kernel"):
                    lifecycle.run(["/usr/bin/true"], time.monotonic() + 5, lifetime=read)
                launch.assert_not_called()
            os.close(write)
            write = None
            with patch.object(lifecycle, "prctl"), patch("subprocess.Popen") as launch:
                with self.assertRaisesRegex(BrokenPipeError, "before namespace launch"):
                    lifecycle.run(["/usr/bin/true"], time.monotonic() + 5, lifetime=read)
                launch.assert_not_called()
            with open("/dev/null", "rb") as nonpipe, patch("subprocess.Popen") as launch:
                with self.assertRaisesRegex(ValueError, "lifetime pipe"):
                    lifecycle.run(["/usr/bin/true"], time.monotonic() + 5, lifetime=nonpipe.fileno())
                launch.assert_not_called()
        finally:
            os.close(read)
            if write is not None:
                os.close(write)

    def test_watchdog_reaps_owned_orphans_on_completion_eof_deadline_and_signal(self):
        program = (
            "import ctypes,json,os,signal,sys,time\n"
            "death = ctypes.c_int()\n"
            "assert ctypes.CDLL(None).prctl(2, ctypes.byref(death), 0, 0, 0) == 0\n"
            "assert death.value == signal.SIGKILL\n"
            "child = os.fork()\n"
            "if child == 0:\n"
            " signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            " time.sleep(20)\n os._exit(0)\n"
            "print(json.dumps([os.getpid(), child]), flush=True)\n"
            "if sys.argv[1] != 'complete': time.sleep(20)\n"
        )
        for action in ("complete", "eof", "deadline", "signal"):
            with self.subTest(action=action):
                started = time.monotonic()
                watchdog = subprocess.Popen(
                    ["/usr/bin/python3", "-I", "-S", "-B", str(TRUSTED_ROOT / "lifecycle.py"),
                     str(started + (1 if action == "deadline" else 10)), "--",
                     "/usr/bin/python3", "-I", "-c", program, action],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    close_fds=True, start_new_session=True, env=ENVIRONMENT,
                )
                pids = []
                try:
                    with selectors.DefaultSelector() as ready:
                        ready.register(watchdog.stdout, selectors.EVENT_READ)
                        self.assertTrue(ready.select(3), "owned payload did not start")
                    line = watchdog.stdout.readline()
                    if not line:
                        self.fail(watchdog.stderr.read())
                    pids = json.loads(line)
                    if action == "eof":
                        watchdog.stdin.close()
                    elif action == "signal":
                        os.kill(watchdog.pid, signal.SIGTERM)
                    watchdog.wait(timeout=5)
                    self.assertEqual(watchdog.returncode, 0 if action == "complete" else 125)
                    self.assertLess(time.monotonic() - started, 5)
                    for pid in pids:
                        self.assertFalse(Path(f"/proc/{pid}").exists(), f"owned PID {pid} was not reaped")
                finally:
                    if watchdog.poll() is None:
                        watchdog.stdin.close()
                        watchdog.wait(timeout=5)
                    watchdog.stdin.close()
                    watchdog.stdout.close()
                    watchdog.stderr.close()

    def test_make_runtime_uses_captured_non_multiarch_closure_and_real_make(self):
        trusted = dict(_make_runtime(ProbeBudget()))
        interpreter = _make_interpreter(trusted["/usr/bin/make"])
        relocated = {
            name if name in {"/usr/bin/make", interpreter} else "/usr/lib/" + Path(name).name: data
            for name, data in trusted.items()
        }
        listing = "\tlinux-vdso.so.1 (0x1)\n" + "".join(
            f"\t{Path(name).name} => {name} (0x2)\n"
            for name in relocated if name not in {"/usr/bin/make", interpreter}
        ) + f"\t{interpreter} (0x3)\n"
        self.add("Makefile", "VALUE := captured-runtime\nall: dependency\ndependency: ;\n")
        session = self.session()
        original_run = session.budget.run
        def runtime_listing(argv, **kwargs):
            if argv == [interpreter, "--list", "/usr/bin/make"]:
                self.assertEqual(kwargs["env"], ENVIRONMENT)
                self.assertEqual(kwargs["cwd"], Path("/"))
                return subprocess.CompletedProcess(argv, 0, listing.encode("ascii"), b"")
            return original_run(argv, **kwargs)
        def captured_runtime(name, budget):
            budget.charge("control", len(relocated[name]))
            return relocated[name]
        with patch(
            "scripts.validation_ownership.make_probe._trusted_runtime_bytes", captured_runtime,
        ), patch.object(session.budget, "run", runtime_listing):
            with session:
                captured = dict(session.make_runtime)
                self.assertEqual(captured, relocated)
                relocated.clear()
                with patch(
                    "scripts.validation_ownership.make_probe._trusted_runtime_bytes",
                    side_effect=AssertionError("captured runtime was read again"),
                ):
                    root = session._new_root("inspect-runtime", make=True)
                    for name, data in captured.items():
                        target = root / name.lstrip("/")
                        self.assertEqual(target.read_bytes(), data)
                        self.assertFalse(target.stat().st_mode & 0o222)
                    self.assertFalse((root / "lib/x86_64-linux-gnu/libc.so.6").exists())
                    self.assertTrue((root / "usr/lib/libc.so.6").is_file())
                    observation = session.make("all", variables=("VALUE",))
                    self.assertEqual(observation.semantics["domains"]["VALUE"]["value"], "captured-runtime")
                    self.assertEqual(observation.semantics["files"][0]["prerequisites"], [
                        {"name": "dependency", "order_only": False},
                    ])
        self.assert_clean(session)

    def test_runtime_capture_rejects_mutable_aliases_and_malformed_elf_or_listing(self):
        alias = self.directory / "make"
        alias.symlink_to("/usr/bin/make")
        with self.assertRaisesRegex(MakeProbeError, "trusted system"):
            _trusted_runtime_bytes(str(alias), ProbeBudget())
        original = Path.lstat
        resolved = Path("/usr/bin/make").resolve()
        def mutable(path):
            value = original(path)
            if path == resolved:
                fields = list(value)
                fields[0] |= stat.S_IWGRP
                return os.stat_result(fields)
            return value
        with patch.object(Path, "lstat", mutable):
            with self.assertRaisesRegex(MakeProbeError, "mutable/untrusted"):
                _trusted_runtime_bytes("/usr/bin/make", ProbeBudget())
        binary = Path("/usr/bin/make").read_bytes()
        invalid_headers = bytearray(binary)
        invalid_headers[56:58] = b"\0\0"
        for data in (b"", b"\x7fELF", bytes(invalid_headers)):
            with self.assertRaises(MakeProbeError):
                _make_interpreter(data)
        for output in (
            b"", b"\tlibc.so.6 => not found\n",
            b"\tlibc.so.6 => /work/libc.so.6 (0x1)\n",
            b"\tlibc.so.6 => /usr/lib/../bin/make (0x1)\n",
        ):
            with self.subTest(output=output):
                budget = ProbeBudget()
                with patch.object(budget, "run", return_value=subprocess.CompletedProcess([], 0, output, b"")):
                    with self.assertRaises(MakeProbeError):
                        _make_runtime(budget)

    def test_directory_permission_attacks_reject_without_masked_errors_or_residue(self):
        controls = [
            ("os.chmod('/work', 0)", "pathname permission loss"),
            ("os.chmod('/work/nested', 0)", "pathname permission loss"),
            ("os.chmod('nested', 0, dir_fd=directory)", "pathname permission loss"),
            ("os.fchmod(directory, 0)", "directory permission changes"),
            ("os.fchmod(os.dup(directory), 0)", "directory permission changes"),
            ("ctypes.CDLL(None).syscall(452, directory, b'nested', 0, 0)", "unadmitted syscall"),
            ("os.mkdir('/work/locked', 0)", "untraversable directory creation"),
            ("os.mkdir('locked', 0, dir_fd=directory)", "untraversable directory creation"),
            ("os.umask(0o700)\nos.mkdir('/work/locked')", "owner permission masking"),
        ]
        for operation, expected in controls:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes,os\nos.mkdir('/work/nested', 0o700)\n"
                    "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
                    "try:\n " + operation.replace("\n", "\n ") + "\n"
                    "except OSError:\n pass\nos._exit(7)\n"
                ))
                session = self.session()
                descriptors = []
                with session:
                    original = session._sandbox_run
                    def retain_owned_directory(root, **kwargs):
                        for mount in kwargs["mounts"]:
                            if mount["target"] == "/work":
                                descriptors.append(os.open(
                                    mount["source"], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                ))
                        return original(root, **kwargs)
                    try:
                        with patch.object(session, "_sandbox_run", retain_owned_directory):
                            with self.assertRaisesRegex(MakeProbeError, expected):
                                session.command(Command(
                                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                                ))
                        for descriptor in descriptors:
                            self.assertEqual(os.fstat(descriptor).st_mode & 0o700, 0o700)
                    finally:
                        # Regression controls remain safe even against the old
                        # guard: restore only retained, explicitly owned fixtures.
                        for descriptor in descriptors:
                            os.fchmod(descriptor, 0o700)
                            for name in ("nested", "locked"):
                                try:
                                    os.chmod(name, 0o700, dir_fd=descriptor, follow_symlinks=False)
                                except FileNotFoundError:
                                    pass
                            os.close(descriptor)
                self.assert_clean(session)

    def test_safe_output_permissions_and_regular_file_fchmod_remain_supported(self):
        self.add("reader.py", (
            "import os,stat\nos.umask(0o077)\nos.mkdir('/work/owned', 0o700)\n"
            "descriptor = os.open('/work/owned/file', os.O_CREAT | os.O_WRONLY, 0o600)\n"
            "os.fchmod(descriptor, 0)\nos.fchmod(descriptor, 0o644)\nos.close(descriptor)\n"
            "os.chmod('/work/owned/file', 0o755)\nos.chmod('/work/owned', 0o700)\n"
            "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
            "os.chmod('owned', 0o700, dir_fd=directory)\nos.close(directory)\n"
            "assert stat.S_IMODE(os.stat('/work/owned/file').st_mode) == 0o755\n"
            "print('safe permissions')\n"
        ))
        with self.session() as session:
            output = session.command(Command(
                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
            ))
            self.assertEqual(output.stdout, b"safe permissions\n")
        self.assert_clean(session)

    @contextmanager
    def stopped_tracee(self, setup):
        from scripts.validation_ownership.syscall_guard import ptrace, SETOPTIONS
        child = os.fork()
        if child == 0:
            try:
                setup()
                os._exit(0)
            except BaseException:
                os._exit(125)
        stopped = False
        try:
            waited, status = os.waitpid(child, 0)
            self.assertEqual(waited, child)
            stopped = os.WIFSTOPPED(status)
            self.assertTrue(stopped, status)
            ptrace(SETOPTIONS, child, 0, 0x100000)
            yield child
        finally:
            if stopped:
                os.kill(child, signal.SIGKILL)
                os.waitpid(child, 0)

    def test_ptrace_bootstrap_restores_post_drop_memory_observation(self):
        from scripts.validation_ownership.syscall_guard import memory, ptrace, trace_me, TRACEME
        libc = ctypes.CDLL(None, use_errno=True)
        buffer = ctypes.create_string_buffer(b"owned")
        def drop_dumpability():
            if libc.prctl(4, 0, 0, 0, 0):
                raise OSError(ctypes.get_errno(), "cannot model post-setuid dumpability")
        def previous_bootstrap():
            drop_dumpability()
            ptrace(TRACEME, 0)
            os.kill(os.getpid(), signal.SIGSTOP)
        with self.stopped_tracee(previous_bootstrap) as child:
            with self.assertRaises(OSError) as caught:
                memory(child, ctypes.addressof(buffer), 6)
            self.assertEqual(caught.exception.errno, errno.EIO)
        with self.stopped_tracee(lambda: trace_me(drop_dumpability)) as child:
            self.assertEqual(memory(child, ctypes.addressof(buffer), 6), b"owned\0")

    def test_ptrace_pathname_stops_at_nul_before_an_unmapped_page(self):
        from scripts.validation_ownership.syscall_guard import cstring, memory, trace_me, Violation
        libc = ctypes.CDLL(None, use_errno=True)
        libc.mmap.restype = ctypes.c_void_p
        libc.mmap.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_long,
        ]
        libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        page = os.sysconf("SC_PAGE_SIZE")
        address = libc.mmap(None, page * 2, 3, 0x22, -1, 0)
        self.assertNotIn(address, (None, ctypes.c_void_p(-1).value))
        try:
            for payload, expected in (
                (b"end\0", "end"), (b"\xc3\xa9\0", "é"), (b"\0", ""),
                (b"\xff\0", "strict UTF-8"), (b"x" * 4096, "pathname exceeds bound"),
            ):
                with self.subTest(payload_length=len(payload), expected=expected):
                    start = address + page - len(payload)
                    ctypes.memmove(start, payload, len(payload))
                    def unmap_guard_page():
                        if libc.munmap(address + page, page):
                            raise OSError(ctypes.get_errno(), "cannot unmap owned guard page")
                    with self.stopped_tracee(lambda: trace_me(unmap_guard_page)) as child:
                        with self.assertRaises(OSError) as caught:
                            memory(child, address + page - 4, 8)
                        self.assertEqual(caught.exception.errno, errno.EIO)
                        if payload in (b"\xff\0", b"x" * 4096):
                            with self.assertRaisesRegex(Violation, expected):
                                cstring(child, start)
                        else:
                            self.assertEqual(cstring(child, start), expected)
        finally:
            self.assertEqual(libc.munmap(address, page * 2), 0)

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
            self.assertTrue(all(event["match"] >= 0 for event in result.events))
            self.assertEqual(len(session.cache), 1)
        self.assert_clean(session)

    def test_make_identity_excludes_discarded_replay_inputs(self):
        self.add("choice.txt", "genuine first")
        self.add("choose.py", "import os\nos.write(1, open('choice.txt','rb').read().split()[0])\n")
        self.add("discarded.txt", "first unused result")
        self.add("discarded.py", "import os\nos.write(1, open('discarded.txt','rb').read())\n")
        selected = "python3 -I -S -B choose.py"
        discarded = "python3 -I -S -B discarded.py"
        self.add("Makefile", (
            f"SELECT := $(shell {selected})\nifeq ($(SELECT),)\n"
            f"UNUSED := $(shell {discarded})\nendif\n"
            "all: $(SELECT)\n\t@printf '%s' '$^'\ngenuine: ;\n"
        ))
        commands = {
            selected: Command(
                ("/usr/bin/python3", "/repo/choose.py"),
                code=("choose.py",), sources=("choice.txt",),
            ),
            discarded: Command(
                ("/usr/bin/python3", "/repo/discarded.py"),
                code=("discarded.py",), sources=("discarded.txt",),
            ),
        }
        observations = []
        for label, path, value in (
            ("baseline", None, None),
            ("discarded-source-output", "discarded.txt", "second unused result"),
            ("discarded-code", "discarded.py",
             "import os\nos.write(1, open('discarded.txt','rb').read().upper())\n"),
            ("selected-source", "choice.txt", "genuine second"),
            ("selected-code-output", "choose.py",
             "import os\nos.write(1, open('choice.txt','rb').read().split()[0] + b'\\n')\n"),
        ):
            with self.subTest(change=label):
                if path:
                    self.add(path, value)
                normal = subprocess.run(
                    ["/usr/bin/make", "-f", "Makefile", "all"], cwd=self.root,
                    env=ENVIRONMENT, capture_output=True, check=True, timeout=10,
                )
                self.assertEqual(normal.stdout, b"genuine")
                with self.session() as session:
                    observed = session.make(
                        "all", variables=("SELECT",), owner_inputs=("Makefile",), commands=commands,
                    )
                    observations.append(observed)
                    self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                        {"name": normal.stdout.decode("ascii"), "order_only": False},
                    ])
                    self.assertEqual([_event_command(event) for event in observed.events], [selected])
                    dynamic = observed.semantics["dynamic_commands"]
                    self.assertEqual(len(dynamic), 1)
                    self.assertEqual(dynamic[0]["command"]["argv"], list(commands[selected].argv))
                    self.assertEqual(
                        {item[0] for item in dynamic[0]["command"]["inputs"]},
                        {"choose.py", "choice.txt"},
                    )
                    self.assertEqual(
                        {result.consumed for variants in session.cache.values() for result in variants},
                        {("choice.txt",)},
                    )
                self.assert_clean(session)
        self.assertEqual(len({item.semantic_digest for item in observations[:3]}), 1)
        self.assertEqual(len({item.execution_digest for item in observations}), 5)
        self.assertNotEqual(observations[2].semantic_digest, observations[3].semantic_digest)
        self.assertNotEqual(observations[3].semantic_digest, observations[4].semantic_digest)
        outputs = [item.semantics["dynamic_commands"][0]["output_sha256"] for item in observations]
        self.assertEqual(len(set(outputs[:4])), 1)
        self.assertNotEqual(outputs[3], outputs[4])

    def test_make_identity_follows_the_last_late_resolved_branch(self):
        outer = "printf %s enabled"
        inner = "printf %s genuine"
        discarded = "printf %s unused"
        self.add("Makefile", (
            f"OUTER := $(shell {outer})\nifeq ($(OUTER),enabled)\n"
            f"INNER := $(shell {inner})\nifeq ($(INNER),)\n"
            f"UNUSED := $(shell {discarded})\nendif\nendif\nall: $(INNER)\ngenuine: ;\n"
        ))
        commands = {
            key: Command(("/usr/bin/printf", "%s", value))
            for key, value in ((outer, "enabled"), (inner, "genuine"), (discarded, "unused"))
        }
        with self.session() as session:
            observed = session.make(
                "all", variables=("OUTER", "INNER"), owner_inputs=("Makefile",), commands=commands,
            )
            self.assertEqual({_event_command(event) for event in observed.events}, {outer, inner})
            self.assertTrue(all(event["match"] >= 0 for event in observed.events))
            self.assertEqual(
                {tuple(item["command"]["argv"]) for item in observed.semantics["dynamic_commands"]},
                {commands[outer].argv, commands[inner].argv},
            )
            self.assertEqual(
                {item.stdout for variants in session.cache.values() for item in variants},
                {b"enabled", b"genuine"},
            )
        self.assert_clean(session)

    def test_make_final_command_identity_deduplicates_aliases_and_declarations(self):
        self.add("left/value.txt", "left")
        self.add("right/value.txt", "right")
        self.add("reader.py", (
            "import os\nfor path in ('left/value.txt','right/value.txt'):\n"
            " os.write(1, open(path,'rb').read())\n"
        ))
        direct = "python3 -I -S -B reader.py"
        compound = direct + "; printf ''"
        self.add("Makefile", (
            f"FIRST := $(shell {direct})\nSECOND := $(shell {compound})\n"
            f"AGAIN := $(shell {direct})\nall: ;\n"
        ))
        original = Command(
            ("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",),
            sources=("left/*.txt", "right/*.txt"), directories=("left", "right"),
        )
        equivalent = replace(
            original, code=("reader.py", "reader.py"),
            sources=("right/value.txt", "left/value.txt"), directories=("right", "left"),
        )
        observations = []
        with self.session() as session:
            for commands in (
                {direct: original, compound: original},
                {compound: equivalent, direct: original},
            ):
                observed = session.make(
                    "all", variables=("FIRST", "SECOND", "AGAIN"),
                    owner_inputs=("Makefile",), commands=commands,
                )
                observations.append(observed)
                self.assertEqual(len(observed.events), 3)
                self.assertEqual({_event_command(event) for event in observed.events}, {direct, compound})
                self.assertEqual(len(observed.semantics["dynamic_commands"]), 1)
                self.assertEqual(
                    {item["value"] for item in observed.semantics["domains"].values()}, {"leftright"},
                )
            self.assertEqual(observations[0].semantic_digest, observations[1].semantic_digest)
        self.assert_clean(session)

    def test_actual_dispatched_commands_remain_authorized_and_aggregate_charged(self):
        selected = "printf %s genuine"
        discarded = "python3 -I -S -B discarded.py"
        self.add("declared.txt", "unused")
        self.add("Makefile", (
            f"SELECT := $(shell {selected})\nifeq ($(SELECT),genuine)\n"
            f"UNUSED := $(shell {discarded})\nendif\nall: $(SELECT)\ngenuine: ;\n"
        ))
        commands = {
            selected: Command(("/usr/bin/printf", "%s", "genuine")),
            discarded: Command(
                ("/usr/bin/python3", "/repo/discarded.py"),
                code=("discarded.py",), sources=("declared.txt",),
            ),
        }
        self.add("baseline.mk", f"SELECT := $(shell {selected})\nall: $(SELECT)\ngenuine: ;\n")
        with self.session() as baseline:
            baseline.make("all", makefile="baseline.mk", commands={selected: commands[selected]})
            first_mapping_bytes = baseline.budget.bytes["mapping"]
        self.assert_clean(baseline)
        for boundary, expected in (
            ("unregistered", "unregistered eager/recursive"),
            ("failed-source", "declared/consumed source mismatch"),
            ("mapping-quota", "mapping"),
        ):
            with self.subTest(boundary=boundary):
                self.add("discarded.py", (
                    "import os\ntry: os.open('declared.txt', os.O_RDONLY | os.O_DIRECTORY)\n"
                    "except OSError: pass\nprint('unused')\n"
                    if boundary == "failed-source"
                    else "print(open('declared.txt').read())\n"
                ))
                limits = {"mapping_bytes": first_mapping_bytes}
                session = self.session(**(limits if boundary == "mapping-quota" else {}))
                with self.assertRaisesRegex(MakeProbeError, expected):
                    with session:
                        session.make("all", commands=(
                            {selected: commands[selected]} if boundary == "unregistered" else commands
                        ))
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

    def test_candidate_symlink_and_relocated_directory_aliases_reject(self):
        controls = [
            "os.symlink('../../repo', '/work/alias')\n"
            "assert os.access('/work/alias/reader.py', os.R_OK)\n"
            "assert not os.access('/work/alias/undeclared', os.R_OK)\n",
            "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
            "os.symlink('../../repo', 'alias', dir_fd=directory)\n",
            "os.rename('/work/a/b', '/work/b')\n"
            "assert os.access('../../../repo/reader.py', os.R_OK)\n"
            "assert not os.access('../../../repo/undeclared', os.R_OK)\n",
            "directory = os.open('.', os.O_RDONLY | os.O_DIRECTORY)\n"
            "os.rename('/work/a/b', '/work/b')\n"
            "assert os.access('../../../repo/reader.py', os.R_OK, dir_fd=directory)\n",
            "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
            "os.rename('a/b', 'b', src_dir_fd=directory, dst_dir_fd=directory)\n"
            "assert os.access('../../../repo/reader.py', os.R_OK)\n",
            "assert ctypes.CDLL(None).syscall(316, -100, b'/work/a/b', -100, b'/work/b', 0) == 0\n"
            "assert os.access('../../../repo/reader.py', os.R_OK)\n",
        ]
        for operation in controls:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes, os\nos.makedirs('/work/a/b/c')\nos.chdir('/work/a/b/c')\n"
                    + operation + "print('alias admitted')\n"
                ))
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "symlink creation|directory-entry relocation"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_trusted_runtime_symlinks_use_the_authorized_source_destination(self):
        runtime = self.directory / "runtime"
        runtime.mkdir()
        (runtime / "alias").symlink_to("../../../../repo")
        (runtime / "absolute").symlink_to("/repo")
        (runtime / "file").symlink_to("/repo/data/admitted")
        self.add("data/admitted", b"owned")
        prefix = "/usr/lib/x86_64-linux-gnu/gconv/"
        for name in ("alias", "absolute", "alias/../repo"):
            for operation, accepted in (
                (
                    "descriptor = os.open(alias + '/data/admitted', os.O_RDONLY)\n"
                    "descriptor = os.dup(descriptor)\n"
                    "with mmap.mmap(descriptor, 0, access=mmap.ACCESS_READ) as view:\n"
                    " os.write(1, view[:])\n", True,
                ),
                ("os.access(alias + '/undeclared', os.R_OK)\n", False),
                ("os.chdir(alias)\nos.access('undeclared', os.R_OK)\n", False),
                (
                    "directory = os.open(alias, os.O_RDONLY | os.O_DIRECTORY)\n"
                    "os.access('undeclared', os.R_OK, dir_fd=directory)\n", False,
                ),
            ):
                with self.subTest(alias=name, operation=operation):
                    self.add("reader.py", (
                        "import mmap, os\nalias = " + repr(prefix + name) + "\n" + operation
                    ))
                    session = self.session()
                    with session:
                        run = session._sandbox_run
                        def with_runtime_alias(root, **kwargs):
                            kwargs["mounts"].append(session._mount(runtime, prefix.rstrip("/")))
                            return run(root, **kwargs)
                        with patch.object(session, "_sandbox_run", with_runtime_alias):
                            command = Command(
                                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                                code=("reader.py",), sources=("data/admitted",) if accepted else (),
                            )
                            if accepted:
                                result = session.command(command)
                                self.assertEqual(result.stdout, b"owned")
                                self.assertEqual(result.consumed, ("data/admitted",))
                            else:
                                with self.assertRaisesRegex(MakeProbeError, "undeclared source"):
                                    session.command(command)
                    self.assert_clean(session)
        self.add("reader.py", (
            "import os, stat\nlink = " + repr(prefix + "file") + "\n"
            "assert stat.S_ISLNK(os.lstat(link).st_mode)\n"
            "assert os.readlink(link) == '/repo/data/admitted'\n"
            "descriptor = os.open(link, os.O_PATH | os.O_NOFOLLOW)\n"
            "assert stat.S_ISLNK(os.fstat(descriptor).st_mode)\n"
            "assert os.readlink('', dir_fd=descriptor) == '/repo/data/admitted'\n"
            "os.close(descriptor)\nprint('nofollow metadata')\n"
        ))
        with self.session() as session:
            run = session._sandbox_run
            def with_runtime_alias(root, **kwargs):
                kwargs["mounts"].append(session._mount(runtime, prefix.rstrip("/")))
                return run(root, **kwargs)
            with patch.object(session, "_sandbox_run", with_runtime_alias):
                result = session.command(Command(
                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                ))
                self.assertEqual(result.stdout, b"nofollow metadata\n")
                self.assertEqual(result.consumed, ())
        self.assert_clean(session)

    @staticmethod
    def mapping_program(operation):
        return (
            "import ctypes, mmap, os\n"
            "libc = ctypes.CDLL(None, use_errno=True)\n"
            "libc.mmap.restype = ctypes.c_void_p\n"
            "libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, "
            "ctypes.c_int, ctypes.c_int, ctypes.c_long]\n"
            "libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]\n"
            "libc.mremap.restype = ctypes.c_void_p\n"
            "libc.mremap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, "
            "ctypes.c_size_t, ctypes.c_int, ctypes.c_void_p]\n"
            "libc.munmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t]\n"
            "def mapping(protection, flags, descriptor=-1):\n"
            " address = libc.mmap(None, 4096, protection, flags, descriptor, 0)\n"
            " assert address not in (None, ctypes.c_void_p(-1).value), ctypes.get_errno()\n"
            " return address\n"
            "def child_write(address):\n"
            " child = os.fork()\n"
            " if child == 0:\n"
            "  ctypes.memmove(address, b'child\\0', 6)\n"
            "  os._exit(0)\n"
            " assert os.waitpid(child, 0)[1] == 0\n"
            + operation
        )

    def test_shared_mapping_protection_upgrade_and_fork_reject(self):
        for protection in (0, 1):
            with self.subTest(protection=protection):
                self.add("reader.py", self.mapping_program(
                    f"address = mapping({protection}, mmap.MAP_SHARED | mmap.MAP_ANONYMOUS)\n"
                    "assert libc.mprotect(address, 4096, 3) == 0\n"
                    "ctypes.memmove(address, b'parent\\0', 7)\n"
                    "child_write(address)\n"
                    "assert ctypes.string_at(address, 6) == b'child\\0'\n"
                    "class Vector(ctypes.Structure):\n"
                    " _fields_ = [('base', ctypes.c_void_p), ('length', ctypes.c_size_t)]\n"
                    "vector = Vector.from_address(address + 128)\n"
                    "child = os.fork()\n"
                    "if child == 0:\n"
                    " ctypes.memmove(address, b'reader.py\\0', 10)\n"
                    " ctypes.memmove(address + 256, b'child\\n', 6)\n"
                    " vector.base, vector.length = address + 256, 6\n"
                    " os._exit(0)\n"
                    "assert os.waitpid(child, 0)[1] == 0\n"
                    "assert libc.access(ctypes.c_void_p(address), os.R_OK) == 0\n"
                    "assert libc.writev(1, ctypes.byref(vector), 1) == 6\n"
                    "assert libc.munmap(address, 4096) == 0\nprint('shared across fork')\n"
                ))
                before = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(self.root / "reader.py")],
                    cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=10,
                )
                self.assertEqual(before.returncode, 0, before.stderr)
                self.assertEqual(before.stdout, b"child\nshared across fork\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "shared anonymous"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_mutable_backing_file_mappings_reject_even_readonly_private_aliases(self):
        for flags in ("mmap.MAP_PRIVATE", "mmap.MAP_SHARED"):
            with self.subTest(flags=flags):
                self.add("reader.py", self.mapping_program(
                    "path = os.environ.get('MAPPING_FIXTURE', '/work/backing')\n"
                    "with open(path, 'wb') as stream: stream.write(b'parent\\0' + b'\\0'*4089)\n"
                    "original = os.open(path, os.O_RDONLY)\n"
                    "descriptor = os.dup(original)\nos.close(original)\n"
                    f"address = mapping(1, {flags}, descriptor)\nos.close(descriptor)\n"
                    "child = os.fork()\n"
                    "if child == 0:\n"
                    " descriptor = os.open(path, os.O_WRONLY)\n"
                    " assert os.pwrite(descriptor, b'child\\0', 0) == 6\n"
                    " os.close(descriptor)\n os._exit(0)\n"
                    "assert os.waitpid(child, 0)[1] == 0\n"
                    "assert ctypes.string_at(address, 6) == b'child\\0'\n"
                    "assert libc.munmap(address, 4096) == 0\nprint('mutable backing observed')\n"
                ))
                before = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(self.root / "reader.py")],
                    cwd=self.root, env={
                        **ENVIRONMENT, "MAPPING_FIXTURE": str(self.directory / "backing"),
                    }, capture_output=True, timeout=10,
                )
                self.assertEqual(before.returncode, 0, before.stderr)
                self.assertEqual(before.stdout, b"mutable backing observed\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "mutable backing"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_protection_and_remap_alias_families_reject(self):
        self.add("data/page", b"immutable" + b"\0" * (4096 - 9))
        controls = [
            (
                "address = mapping(1, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)\n"
                "assert libc.mprotect(address, 4096, 3) == 0\n",
                (), "writable memory protection",
            ),
            (
                "descriptor = os.open('data/page', os.O_RDONLY)\n"
                "address = mapping(1, mmap.MAP_SHARED, descriptor)\n"
                "alias = libc.mremap(address, 0, 4096, 1, None)\n"
                "assert alias != ctypes.c_void_p(-1).value\n"
                "assert ctypes.string_at(alias, 9) == b'immutable'\n",
                ("data/page",), "remap alias",
            ),
        ]
        for flags in (3, 5, 7):
            controls.append((
                "address = mapping(3, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)\n"
                "destination = mapping(3, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)\n"
                "ctypes.memmove(address, b'private\\0', 8)\n"
                f"alias = libc.mremap(address, 4096, 4096, {flags}, destination)\n"
                "assert alias != ctypes.c_void_p(-1).value\n"
                "assert ctypes.string_at(alias, 8) == b'private\\0'\n",
                (), "remap alias",
            ))
        for operation, sources, expected in controls:
            with self.subTest(operation=operation):
                self.add("reader.py", self.mapping_program(operation + "print('upgrade admitted')\n"))
                before = subprocess.run(
                    ["/usr/bin/python3", "-I", "-B", str(self.root / "reader.py")],
                    cwd=self.root, env=ENVIRONMENT, capture_output=True, timeout=10,
                )
                self.assertEqual(before.returncode, 0, before.stderr)
                self.assertEqual(before.stdout, b"upgrade admitted\n")
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, expected):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"),
                            code=("reader.py",), sources=sources,
                        ))
                self.assert_clean(session)

    def test_private_mapping_resize_fork_and_read_protection_stay_supported(self):
        self.add("reader.py", self.mapping_program(
            "address = mapping(3, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS)\n"
            "ctypes.memmove(address, b'parent\\0', 7)\nchild_write(address)\n"
            "assert ctypes.string_at(address, 7) == b'parent\\0'\n"
            "address = libc.mremap(address, 4096, 8192, 1, None)\n"
            "assert address != ctypes.c_void_p(-1).value\n"
            "assert ctypes.string_at(address, 7) == b'parent\\0'\n"
            "assert libc.mprotect(address, 8192, 1) == 0\n"
            "assert libc.munmap(address, 8192) == 0\n"
            "print('private fork and resize')\n"
        ))
        with self.session() as session:
            result = session.command(Command(
                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
            ))
            self.assertEqual(result.stdout, b"private fork and resize\n")
        self.assert_clean(session)

    def test_shared_clone_state_rejects_but_suspended_parent_spawn_stays_supported(self):
        self.add("native.c", (
            "#define _GNU_SOURCE\n#include <sched.h>\n#include <signal.h>\n"
            "#include <stdio.h>\n#include <stdlib.h>\n#include <sys/mman.h>\n"
            "#include <sys/wait.h>\n#include <unistd.h>\n"
            "static int child(void *argument) { (void)argument; _exit(0); }\n"
            "int main(int argc, char **argv) {\n"
            " if(argc != 2) return 2;\n"
            " void *stack = mmap(NULL, 16384, PROT_READ | PROT_WRITE,\n"
            "  MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);\n"
            " if(stack == MAP_FAILED) return 3;\n"
            " int flags = (int)strtoul(argv[1], NULL, 0) | SIGCHLD;\n"
            " pid_t pid = clone(child, (char *)stack + 16384, flags, NULL);\n"
            " int status;\n"
            " if(pid < 0 || waitpid(pid, &status, 0) != pid || status != 0) return 4;\n"
            " if(munmap(stack, 16384)) return 5;\n"
            " puts(\"owned child reaped\"); return 0;\n}\n"
        ))
        with self.session() as session:
            tool = session.compile_native(("native.c",))
            for flags in (0, 0x100 | 0x4000):
                self.assertEqual(session.native(tool, (str(flags),)).stdout, b"owned child reaped\n")
        self.assert_clean(session)
        for flags in (0x100, 0x200, 0x400):
            with self.subTest(flags=flags):
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, "shared-memory candidate threads|shared-state clone"):
                    with session:
                        tool = session.compile_native(("native.c",))
                        session.native(tool, (str(flags),))
                self.assert_clean(session)

    def test_alternate_memory_alias_and_creation_interfaces_remain_fail_closed(self):
        # Invalid IDs/addresses keep these calls harmless even if an admission
        # regression lets the kernel see them. No global IPC object is created.
        for number in (29, 30, 31, 67, 133, 216, 259, 310, 311, 319, 323, 329, 425, 437, 440):
            with self.subTest(syscall=number):
                self.add("reader.py", (
                    "import ctypes\n"
                    f"ctypes.CDLL(None).syscall({number}, -1, -1, -1, -1, -1, -1)\n"
                ))
                session = self.session()
                with self.assertRaisesRegex(MakeProbeError, f"unadmitted syscall {number}"):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_all_supported_creation_attempts_reserve_the_aggregate_quota(self):
        controls = [
            ("", "os.close(os.open('/work/'+str(index), os.O_CREAT | os.O_WRONLY, 0o600))"),
            ("", "os.close(libc.syscall(85, ('/work/'+str(index)).encode(), 0o600))"),
            ("", "os.mkdir('/work/'+str(index))"),
            ("", "os.mkdir(str(index), dir_fd=directory)"),
            ("", "os.close(os.open('/work', os.O_TMPFILE | os.O_RDWR, 0o600))"),
            ("", "os.close(libc.syscall(2, b'/work', os.O_TMPFILE | os.O_RDWR, 0o600))"),
            ("open('/work/seed', 'wb').close()\n", "os.link('/work/seed', '/work/'+str(index))"),
            ("open('/work/seed', 'wb').close()\n",
             "os.link('seed', str(index), src_dir_fd=directory, dst_dir_fd=directory)"),
        ]
        for setup, operation in controls:
            for allowed in (True, False):
                with self.subTest(operation=operation, allowed=allowed):
                    self.add("reader.py", (
                        "import ctypes, os\nlibc = ctypes.CDLL(None)\nos.chdir('/work')\n"
                        "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
                        + setup + "for index in range(2):\n " + operation + "\nprint('created')\n"
                    ))
                    creations = 2 + bool(setup)
                    session = self.session(created_files=creations if allowed else 1)
                    if allowed:
                        with session:
                            output = session.command(Command(
                                ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                            ))
                            self.assertEqual(output.stdout, b"created\n")
                            self.assertEqual(session.files_created, creations)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "file-creation budget"):
                            with session:
                                session.command(Command(
                                    ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                                ))
                        self.assertEqual(session.files_created, 2)
                    self.assert_clean(session)

    def test_unsupported_creation_and_empty_path_link_cannot_bypass_low_quota(self):
        controls = [
            ("os.symlink('missing', '/work/link')", "symlink creation"),
            ("os.symlink('missing', 'link', dir_fd=directory)", "symlink creation"),
            ("os.mkfifo('/work/fifo')", "unadmitted syscall"),
            (
                "descriptor = os.open('/work', os.O_TMPFILE | os.O_RDWR, 0o600)\n"
                "libc.syscall(265, descriptor, b'', directory, b'link', 0x1000)",
                "file-creation budget",
            ),
        ]
        for operation, expected in controls:
            with self.subTest(operation=operation):
                self.add("reader.py", (
                    "import ctypes, os\nlibc = ctypes.CDLL(None)\nos.chdir('/work')\n"
                    "directory = os.open('/work', os.O_RDONLY | os.O_DIRECTORY)\n"
                    + operation + "\nprint('unsupported creation admitted')\n"
                ))
                session = self.session(created_files=1)
                with self.assertRaisesRegex(MakeProbeError, expected):
                    with session:
                        session.command(Command(
                            ("/usr/bin/python3", "-I", "-B", "/repo/reader.py"), code=("reader.py",),
                        ))
                self.assert_clean(session)

    def test_creation_quota_is_not_reset_between_commands(self):
        self.add("Makefile", "all: ;\n")
        with self.session(created_files=1) as session:
            for index in range(2):
                command = Command((
                    "/usr/bin/python3", "-I", "-B", "-c",
                    "import os; os.close(os.open('/work', os.O_TMPFILE | os.O_RDWR, 0o600))",
                    str(index),
                ))
                if index == 0:
                    session.command(command)
                    self.assertEqual(session.files_created, 1)
                else:
                    with self.assertRaisesRegex(MakeProbeError, "file-creation budget"):
                        session.command(command)
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

    def test_process_sequential_make_exceeds_thirty_two_total_with_bounded_live_work(self):
        self.add("Makefile", (
            "VALUE := $(foreach n," + " ".join(map(str, range(40))) + ",$(shell printf %s real))\n"
            "all: ;\n"
        ))
        with self.session() as session:
            observation = session.make("all", variables=("VALUE",), commands={
                "printf %s real": Command(("/usr/bin/printf", "%s", "real")),
            })
            self.assertEqual(observation.semantics["domains"]["VALUE"]["value"], " ".join(["real"]*40))
            self.assertEqual(len(observation.events), 40)
            self.assertEqual(len(observation.semantics["dynamic_commands"]), 1)
            self.assertGreater(session.processes_used, 32)
            self.assertLessEqual(session.processes_used, session.budget.limits.descendants)
            self.assertGreaterEqual(session.live_process_peak, 2)
            self.assertLessEqual(session.live_process_peak, session.budget.limits.processes)
        self.assert_clean(session)

    def test_process_live_capacity_rejects_before_excess_child_creation(self):
        self.add("reader.py", (
            "import os\npipes=[]\n"
            "for n in range(5):\n"
            " r,w=os.pipe()\n"
            " if os.fork()==0:\n"
            "  os.close(w); os.read(r,1); os._exit(0)\n"
            " os.close(r); pipes.append(w)\n"
            "for descriptor in pipes: os.close(descriptor)\n"
            "for n in pipes: os.wait()\n"
        ))
        with self.session(processes=2, descendants=128) as session:
            with self.assertRaisesRegex(MakeProbeError, "live .*process|live process"):
                session.command(Command(("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",)))
            self.assertEqual(session.processes_used, 2)
            self.assertEqual(session.live_process_peak, 2)
            self.assertTrue(session.budget.closed)
        self.assert_clean(session)

    def test_process_total_allowance_exhausts_with_sequential_low_live_work(self):
        self.add("reader.py", (
            "import os\n"
            "for n in range(10):\n"
            " child=os.fork()\n"
            " if child==0: os._exit(0)\n"
            " os.waitpid(child,0)\n"
        ))
        with self.session(processes=8, descendants=4) as session:
            with self.assertRaisesRegex(MakeProbeError, "descendant-process"):
                session.command(Command(("/usr/bin/python3", "/repo/reader.py"), code=("reader.py",)))
            self.assertEqual(session.processes_used, 4)
            self.assertEqual(session.live_process_peak, 2)
            self.assertTrue(session.budget.closed)
        self.assert_clean(session)

    def test_process_totals_cross_capsules_replay_and_failure_without_reset(self):
        budget = ProbeBudget(Limits(processes=3, descendants=7))
        self.add("Makefile", "VALUE := $(shell printf %s genuine)\nall: ;\n")
        self.add("unrelated.txt", "current")
        current = self.capture_view(budget)
        started, deadline = budget.started, budget.deadline
        with ProbeSession(current, scratch_root=self.scratch, budget=budget) as session:
            cached = Command(("/usr/bin/printf", "first"))
            session.command(cached)
            session.command(cached)
            self.assertEqual(session.processes_used, 1)
            session.make("all", commands={"printf %s genuine": Command(("/usr/bin/printf", "%s", "genuine"))})
            self.assertEqual(session.processes_used, 4)
            with self.assertRaisesRegex(MakeProbeError, "descendant-process"):
                session.command(Command(("/usr/bin/printf", "second")))
                self.assertEqual(session.processes_used, 5)
                session.command(Command((
                    "/usr/bin/python3", "-c",
                    "import os\nfor n in range(2):\n"
                    " child=os.fork()\n if child==0: os._exit(0)\n os.waitpid(child,0)\n",
                )))
            self.assertEqual(session.processes_used, 7)
            self.assertEqual(session.live_process_peak, 3)
            self.assertEqual((budget.started, budget.deadline), (started, deadline))
            self.assertIs(session.loader, current)
            self.assertTrue(budget.closed)
            self.assertFalse(budget.children)
            with self.assertRaisesRegex(MakeProbeError, "deadline/budget"):
                session.command(cached)
            self.assertEqual(session.processes_used, 7)
        self.assert_clean(session)

    def test_process_newborn_reservations_count_once_and_failures_release_capacity(self):
        from scripts.validation_ownership.syscall_guard import Policy, Process, Registers, Violation
        def policy(live, total):
            return Policy({
                "root": str(self.root), "mode": "make", "code": [], "sources": [],
                "enumerations": [], "executables": [], "python_version": "3.12",
                "argv": [], "process_limit": live, "descendant_limit": total,
            })
        for live, total, rejection in ((3, 128, "live descendant"), (32, 3, "aggregate descendant")):
            with self.subTest(live=live, total=total):
                observed = policy(live, total)
                parent, child = Process("make", memory_group=1), Process("helper", memory_group=1)
                observed.processes = {1: parent}
                observed.total_processes = 1
                observed.account_processes()
                observed.reserve_process(parent)
                self.assertEqual(observed.live_process_peak, 1)
                observed.newborn_stops[2] = -1
                observed.total_processes += 1
                observed.account_processes()
                self.assertEqual(observed.live_process_peak, 2)
                self.assertEqual(observed.total_processes, 2)
                observed.processes[2] = child
                observed.newborn_stops.pop(2)
                parent.process_reservation = False
                observed.account_processes()
                self.assertEqual(observed.live_process_peak, 2)
                observed.reserve_process(parent)
                with self.assertRaisesRegex(Violation, rejection):
                    observed.reserve_process(child)
                observed.leave(1, parent, Registers(orig_rax=435, rax=(-errno.ENOSYS) & ((1 << 64)-1)))
                self.assertFalse(parent.process_reservation)
                self.assertEqual(observed.total_processes, 2)
                observed.reserve_process(child)
                observed.leave(2, child, Registers(orig_rax=56, rax=(-errno.EAGAIN) & ((1 << 64)-1)))
                self.assertFalse(child.process_reservation)
                self.assertEqual(observed.live_process_peak, 2)
                observed.newborn_stops[3] = -1
                observed.total_processes += 1
                with self.assertRaisesRegex(Violation, "unreserved newborn"):
                    observed.account_processes()
                self.assertEqual(observed.live_process_peak, 3)
                self.assertEqual(observed.total_processes, 3)

    def test_process_root_failure_retains_actual_creation_and_measured_peak(self):
        self.add("Makefile", "all: ;\n")
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "unsuccessfully: 7"):
                session.command(Command(("/usr/bin/python3", "-c", "import os; os._exit(7)")))
            self.assertEqual(session.processes_used, 1)
            self.assertEqual(session.live_process_peak, 1)
            self.assertTrue(session.budget.closed)
            self.assertFalse(session.budget.children)
        self.assert_clean(session)

    def test_strict_named_protocols_reject_binary_and_truncated_frames(self):
        from scripts.validation_ownership.syscall_guard import Policy, Process, Registers, Violation
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
        policy = self.observation_policy(mode="make")
        state = Process("helper")
        state.pending = ("event", b"x" * 20)
        with self.assertRaisesRegex(Violation, "partial native event write"):
            policy.leave(0, state, Registers(rax=19))
        state.pending = ("event", b"x" * 20)
        policy.leave(0, state, Registers(rax=20))
        self.assertEqual(policy.events, [(b"x" * 20).hex()])

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
        from scripts.validation_ownership import consumer
        sessions, budgets, registry_sessions = [], [], []
        initialize = consumer.ProbeSession.__init__
        entries, registry = consumer.git_tree_entries, consumer.probe_generated_registry
        def creating(session, *args, **kwargs):
            initialize(session, *args, **kwargs)
            sessions.append(session)
        def loading(*args, **kwargs):
            budgets.append(kwargs["budget"])
            return entries(*args, **kwargs)
        def discovering(loader, *, command, session):
            self.assertIs(loader, session.loader)
            self.assertIs(session.budget, budgets[0])
            self.assertGreater(session.budget.states, 0)
            registry_sessions.append(session)
            return registry(loader, command=command, session=session)
        with patch.object(consumer.ProbeSession, "__init__", creating), patch.object(
            consumer, "git_tree_entries", loading,
        ), patch.object(consumer, "probe_generated_registry", discovering):
            result = consumer.check(ROOT, "HEAD")
        self.assertEqual(len(sessions), 1)
        self.assertEqual(registry_sessions, sessions)
        self.assertTrue(budgets)
        self.assertTrue(all(budget is sessions[0].budget for budget in budgets))
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
