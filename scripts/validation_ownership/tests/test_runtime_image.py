"""Actual complete sealed bodies, materialization, admission and cleanup."""

import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.validation_ownership import make_probe, read_epochs
from scripts.validation_ownership.budget import Limits, MakeProbeError, ProbeBudget
from scripts.validation_ownership.runtime_image import (
    IMAGE_SEALS, RuntimeImage, close_images, image_digest, materialize_image,
)
from scripts.validation_ownership.tests import test_foundation as foundation


class RuntimeImageTests(unittest.TestCase):
    def test_source_descriptor_closes_if_fdopen_handoff_fails(self):
        for failure in (OSError(errno.EINTR, "interrupted fdopen"), KeyboardInterrupt(), SystemExit(7)):
            budget = ProbeBudget()
            before = set(os.listdir("/proc/self/fd"))
            opened = []
            original_open = os.open

            def capture_open(*args, **kwargs):
                descriptor = original_open(*args, **kwargs)
                opened.append(descriptor)
                return descriptor

            try:
                with self.subTest(failure=type(failure).__name__), patch(
                    "scripts.validation_ownership.runtime_image.os.open", capture_open,
                ), patch("scripts.validation_ownership.runtime_image.os.fdopen", side_effect=failure):
                    with self.assertRaises(type(failure)) as raised:
                        RuntimeImage(Path("/usr/bin/make").resolve(), budget)
                    self.assertIs(raised.exception, failure)
                    self.assertEqual(len(opened), 1)
                    with self.assertRaises(OSError) as closed:
                        os.fstat(opened[0])
                    self.assertEqual(closed.exception.errno, errno.EBADF)
                    self.assertEqual(set(os.listdir("/proc/self/fd")), before)
            finally:
                for descriptor in opened:
                    try:
                        os.fstat(descriptor)
                    except OSError as error:
                        if error.errno != errno.EBADF:
                            raise
                    else:
                        os.close(descriptor)
                budget.close()

    def test_complete_body_and_materialization_use_actual_immutable_backing(self):
        for name in ("/usr/bin/python3", "/usr/bin/make"):
            with self.subTest(source=name):
                self.assert_complete_body(Path(name).resolve())

    def assert_complete_body(self, path):
        budget = ProbeBudget()
        image = RuntimeImage(path, budget)
        descriptor = image.descriptor
        try:
            expected = path.read_bytes()
            self.assertEqual(len(image), len(expected))
            self.assertEqual(image[:64], expected[:64])
            self.assertEqual(image_digest(image), hashlib.sha256(expected).hexdigest())
            self.assertEqual(image, expected)
            self.assertEqual(fcntl.fcntl(descriptor, fcntl.F_GET_SEALS), IMAGE_SEALS)
            for action in (
                lambda: os.pwrite(descriptor, b"X", 0),
                lambda: os.ftruncate(descriptor, 0),
                lambda: os.ftruncate(descriptor, len(image) + 1),
            ):
                with self.subTest(action=action), self.assertRaises(OSError) as raised:
                    action()
                self.assertEqual(raised.exception.errno, errno.EPERM)
            with tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "runtime"
                self.assertEqual(materialize_image(destination, image), len(expected))
                self.assertEqual(destination.read_bytes(), expected)
            self.assertEqual(budget.bytes["snapshot"], 2 * len(expected))
            self.assertLess(budget.bytes["control"], 1024 * 1024)
        finally:
            images = {"python": image}
            close_images(images)
            self.assertEqual(images, {})
            self.assertEqual(image.descriptor, -1)
            with self.assertRaises(OSError):
                os.fstat(descriptor)
            budget.close()

    def test_complete_storage_and_work_quotas_fail_without_leaking_descriptors(self):
        path = Path("/usr/bin/python3").resolve()
        extent = path.stat().st_size
        for limits in (
            Limits(snapshot_bytes=extent - 1),
            Limits(total_bytes=extent - 1),
            Limits(control_bytes=32768),
        ):
            budget = ProbeBudget(limits)
            before = set(os.listdir("/proc/self/fd"))
            try:
                with self.subTest(limits=limits), self.assertRaises(MakeProbeError):
                    RuntimeImage(path, budget)
                self.assertEqual(set(os.listdir("/proc/self/fd")), before)
                self.assertTrue(budget.failed)
            finally:
                budget.close()
        budget = ProbeBudget(Limits(snapshot_bytes=extent))
        image = RuntimeImage(path, budget)
        try:
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "runtime"
                with self.assertRaises(MakeProbeError):
                    image.materialize(target)
                self.assertFalse(target.exists())
        finally:
            image.close()
            budget.close()

    def test_failed_materialization_removes_owned_file_and_preserves_failure(self):
        path = Path("/usr/bin/make").resolve()
        for sealed, faults in (
            (True, ("first-read", "late-read", "write", "interrupt", "system-exit",
                    "deadline", "backing", "digest", "close",
                    "zero-write", "none-write", "oversized-write", "negative-write")),
            (False, ("write", "interrupt", "system-exit", "close",
                     "zero-write", "none-write", "oversized-write", "negative-write")),
        ):
            for fault in faults:
                budget = ProbeBudget()
                image = RuntimeImage(path, budget) if sealed else path.read_bytes()
                opened = []
                original_open, original_read = Path.open, os.preadv
                failure = (
                    KeyboardInterrupt() if fault == "interrupt" else
                    SystemExit(7) if fault == "system-exit" else OSError(errno.EIO, "materialization I/O")
                )
                read_calls = 0
                written = 0

                class Writer:
                    def __init__(self, stream):
                        self.stream = stream
                        opened.append(stream.fileno())

                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        result = self.stream.__exit__(*args)
                        if fault == "close":
                            raise failure
                        return result

                    def close(self):
                        self.stream.close()
                        if fault == "close":
                            raise failure

                    def write(self, data):
                        nonlocal written
                        if fault == "zero-write":
                            return 0
                        if fault == "none-write":
                            return None
                        if fault == "oversized-write":
                            return len(data) + 1
                        if fault == "negative-write":
                            return -1
                        if fault in {"write", "interrupt", "system-exit"}:
                            self.stream.write(data[:17])
                            raise failure
                        count = self.stream.write(data)
                        written += count
                        if fault == "deadline":
                            budget.started = 0
                        if fault == "backing" and written == len(image):
                            image.close()
                        return count

                def read(descriptor, buffers, offset):
                    nonlocal read_calls
                    read_calls += 1
                    if fault == "first-read":
                        return 0
                    if fault == "late-read" and read_calls > 1:
                        raise failure
                    count = original_read(descriptor, buffers, offset)
                    if fault == "digest" and count:
                        buffers[0][0] ^= 1
                    return count

                try:
                    with self.subTest(sealed=sealed, fault=fault), tempfile.TemporaryDirectory() as directory:
                        target = Path(directory) / "runtime"

                        def open_target(destination, *args, **kwargs):
                            stream = original_open(destination, *args, **kwargs)
                            return Writer(stream) if destination == target else stream

                        expected = type(failure) if fault in {
                            "late-read", "write", "interrupt", "system-exit", "close",
                        } else MakeProbeError
                        with patch.object(Path, "open", open_target), patch(
                            "scripts.validation_ownership.runtime_image.os.preadv", read,
                        ), self.assertRaises(expected) as raised:
                            materialize_image(target, image)
                        if expected is type(failure):
                            self.assertIs(raised.exception, failure)
                        self.assertFalse(target.exists())
                        self.assertEqual(len(opened), 1)
                        with self.assertRaises(OSError) as closed:
                            os.fstat(opened[0])
                        self.assertEqual(closed.exception.errno, errno.EBADF)
                finally:
                    if sealed:
                        image.close()
                    budget.close()

    def test_materialization_handles_actual_short_writes_and_failed_open(self):
        path = Path("/usr/bin/make").resolve()
        expected = path.read_bytes()
        for sealed in (True, False):
            budget = ProbeBudget()
            image = RuntimeImage(path, budget) if sealed else expected
            original_open = Path.open
            calls = 0

            class Writer:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return self.stream.__exit__(*args)

                def close(self):
                    self.stream.close()

                def write(self, data):
                    nonlocal calls
                    calls += 1
                    return self.stream.write(data[:1] if calls == 1 else data)

            try:
                with self.subTest(sealed=sealed), tempfile.TemporaryDirectory() as directory:
                    target = Path(directory) / "runtime"

                    def open_target(destination, *args, **kwargs):
                        stream = original_open(destination, *args, **kwargs)
                        return Writer(stream) if destination == target else stream

                    with patch.object(Path, "open", open_target):
                        self.assertEqual(materialize_image(target, image), len(expected))
                    self.assertEqual(target.read_bytes(), expected)
                    failure = PermissionError(errno.EACCES, "cannot open materialization destination")
                    with patch.object(Path, "open", side_effect=failure), self.assertRaises(PermissionError) as raised:
                        materialize_image(target, image)
                    self.assertIs(raised.exception, failure)
                    self.assertEqual(target.read_bytes(), expected)
                    if sealed:
                        quota = ProbeBudget(Limits(snapshot_bytes=len(expected)))
                        limited = RuntimeImage(path, quota)
                        try:
                            with self.assertRaises(MakeProbeError):
                                materialize_image(target, limited)
                            self.assertEqual(target.read_bytes(), expected)
                        finally:
                            limited.close()
                            quota.close()
            finally:
                if sealed:
                    image.close()
                budget.close()

    def test_materialization_preserves_primary_with_combined_cleanup_failures(self):
        path = Path("/usr/bin/make").resolve()
        expected = path.read_bytes()
        for sealed in (True, False):
            for close_fault, unlink_fault in ((True, False), (False, True), (True, True)):
                for primary in (OSError(errno.EIO, "write failed"), KeyboardInterrupt(), SystemExit(7)):
                    budget = ProbeBudget()
                    image = RuntimeImage(path, budget) if sealed else expected
                    original_open, original_unlink = Path.open, Path.unlink
                    close_error = OSError(errno.ENOSPC, "close failed")
                    unlink_error = PermissionError(errno.EACCES, "unlink denied")
                    opened = []

                    class Writer:
                        def __init__(self, stream):
                            self.stream = stream
                            opened.append(stream.fileno())

                        def __enter__(self):
                            return self

                        def __exit__(self, *args):
                            self.close()

                        def close(self):
                            self.stream.close()
                            if close_fault:
                                raise close_error

                        def write(self, data):
                            self.stream.write(data[:17])
                            raise primary

                    try:
                        with self.subTest(sealed=sealed, close=close_fault, unlink=unlink_fault,
                                          primary=type(primary).__name__), tempfile.TemporaryDirectory() as directory:
                            target = Path(directory) / "runtime"
                            target.write_bytes(b"previous owned replacement")

                            def open_target(destination, *args, **kwargs):
                                stream = original_open(destination, *args, **kwargs)
                                return Writer(stream) if destination == target else stream

                            def unlink_target(destination, *args, **kwargs):
                                if destination == target and unlink_fault:
                                    raise unlink_error
                                return original_unlink(destination, *args, **kwargs)

                            with patch.object(Path, "open", open_target), patch.object(
                                Path, "unlink", unlink_target,
                            ), self.assertRaises(BaseException) as raised:
                                materialize_image(target, image)
                            self.assertIs(raised.exception, primary)
                            self.assertEqual(target.exists(), unlink_fault)
                            if unlink_fault:
                                self.assertEqual(target.read_bytes(), expected[:17])
                            errors = getattr(primary, "cleanup_errors", ())
                            self.assertEqual(len(errors), int(close_fault) + int(unlink_fault))
                            for error in (close_error if close_fault else None,
                                          unlink_error if unlink_fault else None):
                                if error is not None:
                                    self.assertTrue(any(str(error) in message for message in errors))
                            with self.assertRaises(OSError) as closed:
                                os.fstat(opened[0])
                            self.assertEqual(closed.exception.errno, errno.EBADF)
                    finally:
                        if sealed:
                            image.close()
                        budget.close()


class RuntimeImageSessionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.FoundationTests("runTest")
        self.fixture.setUp()
        self.fixture.add("Makefile", "all: ; @value=owned; printf '%s' \"$$value\"\n")

    def tearDown(self):
        self.fixture.tearDown()

    def test_derived_compiler_closure_owns_complete_sealed_images_without_exec_authority(self):
        session = self.fixture.session()
        with session:
            rows = session._sealed_dependency_runtime()
            self.assertEqual(tuple(path for path, _ in rows), tuple(session.dependency_runtime["runtime_files"]))
            self.assertTrue(set(session.dependency_compiler) <= dict(rows).keys())
            for path, image in rows:
                with self.subTest(path=path), Path(path).open("rb") as stream:
                    self.assertEqual(image_digest(image), hashlib.file_digest(stream, "sha256").hexdigest())
                    self.assertEqual(len(image), Path(path).stat().st_size)
                    self.assertEqual(fcntl.fcntl(image.descriptor, fcntl.F_GET_SEALS), IMAGE_SEALS)
            stored = session.budget.bytes["snapshot"]
            repeated = session._sealed_dependency_runtime()
            self.assertTrue(all(a is b for (_, a), (_, b) in zip(rows, repeated)))
            self.assertEqual(session.budget.bytes["snapshot"], stored)
            frontend = session.dependency_compiler[1]
            if frontend.startswith("/usr/libexec/"):
                with self.assertRaisesRegex(MakeProbeError, "outside the trusted system tool/library roots"):
                    session._captured_native_runtime_input(frontend, sealed=True)
            with self.assertRaisesRegex(MakeProbeError, "outside its issued runtime closure"):
                session._captured_native_runtime_input("/usr/bin/make", sealed=True, dependency=True)
            completed, _, _ = session._native_make_readonly("all")
            self.assertEqual((completed.returncode, completed.stdout, completed.stderr), (0, b"owned", b""))
        self.assertTrue(all(image.descriptor == -1 for _, image in rows))
        self.assertIsNone(session.dependency_compiler)
        self.assertIsNone(session.dependency_runtime)
        self.fixture.assert_clean(session)

    def test_derived_compiler_capture_failure_closes_partial_owned_closure(self):
        session = self.fixture.session()
        images = []
        with self.assertRaisesRegex(MakeProbeError, "read-only|regular|trusted"):
            with session:
                session._ensure_dependency_runtime()
                capture = session._captured_native_runtime_input

                def changed_input(path, **kwargs):
                    if images:
                        with patch.object(make_probe, "_trusted_runtime_path", return_value=self.fixture.root / "Makefile"):
                            return capture(path, **kwargs)
                    image = capture(path, **kwargs)
                    images.append(image)
                    return image

                with patch.object(session, "_captured_native_runtime_input", changed_input):
                    session._sealed_dependency_runtime()
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0].descriptor, -1)
        self.assertTrue(session.budget.failed)
        self.fixture.assert_clean(session)

    def test_sealed_compiler_root_preserves_issued_file_alias_identity(self):
        session = self.fixture.session()
        with session:
            root = session._sealed_dependency_root("compiler-alias-layout")
            rows = session._sealed_dependency_runtime()
            for path, image in rows:
                canonical = make_probe._trusted_runtime_path(path, compiler=True)
                source = root / str(canonical).lstrip("/")
                destination = root / path.lstrip("/")
                with self.subTest(path=path), destination.open("rb") as stream:
                    self.assertTrue(destination.samefile(source))
                    self.assertEqual(hashlib.file_digest(stream, "sha256").hexdigest(), image_digest(image))
                    self.assertEqual(destination.stat().st_size, len(image))
            interpreter = session.dependency_runtime["runtime_interpreter"]
            alias = "/lib64/ld-linux-x86-64.so.2"
            old = session._new_root("independent-body-layout", make=True, native_runtime=rows)
            self.assertFalse((old / alias.lstrip("/")).samefile(old / interpreter.lstrip("/")))
            driver, frontend = session.dependency_compiler
            for name, aliases in (
                ("different-body", ((driver, frontend),)),
                ("missing-target", ((alias, "/usr/lib/foreign-runtime.so"),)),
                ("escaping", ((alias, "/usr/../etc/foreign-runtime"),)),
                ("duplicate", ((alias, interpreter), (alias, interpreter))),
                ("cycle", ((alias, interpreter), (interpreter, alias))),
            ):
                with self.subTest(alias_mutation=name), self.assertRaises(MakeProbeError):
                    session._new_root(name, make=True, native_runtime=rows, native_aliases=aliases)
                self.assertFalse((session.base / name).exists())
        self.fixture.assert_clean(session)

    def test_actual_sealed_root_compiler_retains_closed_runtime_and_header_scope(self):
        from scripts.validation_ownership.authority import ENVIRONMENT
        self.fixture.add("src/query.c", '#include "query.h"\n')
        self.fixture.add("include/query.h", "#define VALUE 7\n")
        self.fixture.add("include/foreign.h", "#define FOREIGN 9\n")
        for case in ("positive", "separate-loader-copy", "missing-search-parents", "foreign-header"):
            if case == "foreign-header":
                self.fixture.add("include/query.h", '#include "foreign.h"\n')
            session = self.fixture.session()
            with self.subTest(case=case), session:
                rows = session._sealed_dependency_runtime()
                root = (
                    session._new_root("compiler-root", make=True, native_runtime=rows)
                    if case == "separate-loader-copy" else session._sealed_dependency_root("compiler-root")
                )
                output = session.base / "compiler-output"
                output.mkdir()
                driver, frontend = session.dependency_compiler
                profile = {
                    **session.dependency_runtime, "executables": [driver, frontend],
                    "include_dirs": ["/repo/include"],
                }
                if case == "missing-search-parents":
                    profile["runtime_directories"] = []

                def execute():
                    return session._sandbox_run(
                        root, mode="compile",
                        argv=[driver, "-E", "-MM", "-nostdinc", "-undef", "-MT",
                              "query.o", "-Iinclude", "src/query.c"],
                        environment={**ENVIRONMENT, "SOURCE_DATE_EPOCH": "0", "TMPDIR": "/work"},
                        mounts=[
                            session._mount(session.tree, "/repo"),
                            session._mount(output, "/work", writable=True),
                            session._mount(Path("/dev/null"), "/dev/null", writable=True),
                        ],
                        code=("include/query.h",), sources=("src/query.c",),
                        directories=("src", "include"), executables=(driver, frontend),
                        dependency=profile,
                    )

                if case == "positive":
                    completed, observed = execute()
                    self.assertEqual(
                        (completed.returncode, completed.stdout, completed.stderr),
                        (0, b"query.o: src/query.c include/query.h\n", b""),
                    )
                    self.assertEqual(tuple(observed["executed"]), (driver, frontend))
                    self.assertEqual(observed["consumed"], ["src/query.c"])
                    self.assertEqual(observed["code_consumed"], ["include/query.h"])
                else:
                    with self.assertRaisesRegex(MakeProbeError, "undeclared|unadmitted"):
                        execute()
            self.fixture.assert_clean(session)

    def test_issued_compiler_profile_and_finite_driver_at_fork_lineage(self):
        from scripts.validation_ownership.authority import ENVIRONMENT, encoded, native_command_owner
        self.fixture.add("src/query.c", '#include "query.h"\n')
        self.fixture.add("include/query.h", "#define VALUE 7\n")
        session = self.fixture.session()
        with session:
            value = session._native_compiler_profile(ENVIRONMENT)
            profile = read_epochs.native_compiler_profile(value, count_limit=32768)
            self.assertEqual(profile.environment, tuple(sorted(ENVIRONMENT.items())))
            images = dict(session._sealed_dependency_runtime())
            self.assertEqual(
                profile.files, tuple((path, len(image), image_digest(image)) for path, image in images.items()),
            )
            inputs = ["cc", "-E", "-MM", "-nostdinc", "-undef", "-MT", "query.o", "-Iinclude", "src/query.c"]
            scope = {"profile": profile.identity, "sources": ["src/query.c"],
                     "code": ["include/query.h"], "includes": ["include"]}

            def admission(sequence, argv, compiler):
                closure = hashlib.sha256(encoded([argv, compiler])).hexdigest()
                return {"sequence": sequence, "closure": closure,
                        "owner": native_command_owner(closure, [".dep/query.d"], ()),
                        "input_sha256": hashlib.sha256(encoded({"argv": argv, "cwd": "/repo"})).hexdigest(),
                        "outputs": [".dep/query.d"], "compiler": compiler}

            def execution(pid, parent, generation, path, argv, binding, sequence):
                return {"kind": "exec", "pid": pid, "parent": parent, "generation": generation,
                        "path": path, "argv": argv, "cwd": "/repo",
                        "admission": admission(sequence, argv, binding)}

            events = [
                execution(10, 1, 1, "/bin/sh", ["/bin/sh", "-c", "original recipe"], None, 1),
                {"kind": "fork", "pid": 10, "child": 11}, {"kind": "start", "pid": 11},
                execution(11, 10, 1, profile.driver, inputs, dict(scope, role="driver", driver=None), 2),
                {"kind": "fork", "pid": 11, "child": 12}, {"kind": "start", "pid": 12},
                execution(12, 11, 1, profile.frontend, [profile.frontend, "-E", "src/query.c"],
                          dict(scope, role="frontend", driver=[11, 1, 2]), 3),
                {"kind": "exit", "pid": 12, "status": 0}, {"kind": "exit", "pid": 11, "status": 0},
                {"kind": "exit", "pid": 10, "status": 0},
            ]
            for sequence, event in enumerate(events, 1):
                event["seq"] = sequence
            job = {"pid": 10, "sequence": 1, "terminal_status": 0, "executable": "/bin/sh",
                   "argv": events[0]["argv"], "cwd": "/repo"}
            ordinary = json.loads(json.dumps(events))
            for event in ordinary:
                if event["kind"] == "exec":
                    event["admission"].pop("compiler")
            job["admission"] = ordinary[0]["admission"]
            with self.assertRaisesRegex(MakeProbeError, "Command owner"):
                read_epochs.native_job_tree(
                    events, job, 1, ["/bin/sh", profile.driver, profile.frontend],
                    count_limit=32768, writable=True,
                )
            read_epochs.native_job_tree(
                ordinary, job, 1, ["/bin/sh", profile.driver, profile.frontend],
                count_limit=32768, writable=True,
            )
            validate = lambda rows: read_epochs.native_compiler_lineage(
                rows, job, profile, sources=session.snapshot.files, count_limit=32768,
                reserve=lambda size: session.budget.charge("control", size),
            )
            actors = validate(events)
            self.assertEqual([(actor.role, actor.pid, actor.driver) for actor in actors],
                             [("driver", 11, None), ("frontend", 12, (11, 1, 2))])
            redirected = json.loads(json.dumps(events))
            redirected.insert(6, execution(
                12, 11, 1, "/bin/sh", ["/bin/sh", "-c", ":"], None, 3,
            ))
            redirected[7]["generation"] = 2
            redirected[7]["admission"]["sequence"] = 4
            for sequence, event in enumerate(redirected, 1):
                event["seq"] = sequence
            ordinary_redirected = json.loads(json.dumps(redirected))
            for event in ordinary_redirected:
                if event["kind"] == "exec":
                    event["admission"].pop("compiler")
            read_epochs.native_job_tree(
                ordinary_redirected, job, 1, ["/bin/sh", profile.driver, profile.frontend],
                count_limit=32768, writable=True,
            )
            with self.assertRaisesRegex(MakeProbeError, "driver-at-fork"):
                validate(redirected)
            charges = []
            self.assertEqual(read_epochs.native_compiler_lineage(
                events, job, profile, sources=session.snapshot.files, count_limit=len(events),
                reserve=charges.append,
            ), actors)
            cost = sum(charges)
            for limit in (cost, cost - 1):
                budget = ProbeBudget(Limits(control_bytes=limit))
                try:
                    if limit == cost:
                        self.assertEqual(read_epochs.native_compiler_lineage(
                            events, job, profile, sources=session.snapshot.files, count_limit=len(events),
                            reserve=lambda size: budget.charge("control", size),
                        ), actors)
                        self.assertEqual(budget.bytes["control"], cost)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "budget exhausted"):
                            read_epochs.native_compiler_lineage(
                                events, job, profile, sources=session.snapshot.files, count_limit=len(events),
                                reserve=lambda size: budget.charge("control", size),
                            )
                        self.assertTrue(budget.failed)
                finally:
                    budget.close()
            for name, mutate in (
                ("driver-options", lambda rows: rows[3]["argv"].append("-fplugin=foreign")),
                ("profile", lambda rows: rows[6]["admission"]["compiler"].update(profile="0" * 64)),
                ("parent", lambda rows: rows[6].update(parent=10)),
                ("generation", lambda rows: rows[6]["admission"]["compiler"].update(driver=[11, 2, 2])),
                ("admission", lambda rows: rows[6]["admission"]["compiler"].update(driver=[11, 1, 1])),
                ("boolean-reference", lambda rows: rows[6]["admission"]["compiler"].update(driver=[11, True, 2])),
                ("scope", lambda rows: rows[6]["admission"]["compiler"]["code"].append("src/query.c")),
                ("direct-frontend", lambda rows: rows[3].update(path=profile.frontend)),
                ("unbound-frontend", lambda rows: rows[6]["admission"].update(compiler=None)),
                ("missing-terminal", lambda rows: rows.pop()),
                ("missing-frontend", lambda rows: rows[6].update(path="/bin/sh", admission=rows[0]["admission"])),
                ("retired-driver", lambda rows: rows.insert(6, {"kind": "exit", "pid": 11, "status": 0})),
                ("unrelated-driver-exec", lambda rows: rows.insert(
                    6, execution(11, 10, 2, "/bin/sh", ["/bin/sh", "-c", ":"], None, 4),
                )),
            ):
                changed = json.loads(json.dumps(events))
                mutate(changed)
                with self.subTest(lineage_mutation=name), self.assertRaises(MakeProbeError):
                    validate(changed)
            invalid = json.loads(json.dumps(value))
            invalid["files"][0][2] = "0" * 64
            with self.assertRaisesRegex(MakeProbeError, "issued identity"):
                read_epochs.native_compiler_profile(invalid, count_limit=32768)
            with self.assertRaisesRegex(MakeProbeError, "tree extent"):
                read_epochs.native_compiler_lineage(
                    events, job, profile, sources=session.snapshot.files, count_limit=len(events) - 1,
                )
        self.fixture.assert_clean(session)

    def test_sealed_capture_has_one_owned_body_and_preserves_legacy_cold_capture(self):
        session = self.fixture.session()
        images = []
        with session:
            legacy = session._captured_native_runtime_input("/usr/bin/find")
            self.assertIsInstance(legacy, bytes)
            before = session.budget.bytes.get("snapshot", 0)
            image = session._captured_native_runtime_input("/usr/bin/python3", sealed=True)
            images.append(image)
            self.assertIsInstance(image, RuntimeImage)
            self.assertEqual(
                session.budget.bytes["snapshot"] - before,
                Path("/usr/bin/python3").resolve().stat().st_size,
            )
            self.assertLess(session.budget.bytes.get("cache", 0), len(legacy) + 4096)
            self.assertIs(
                session._captured_native_runtime_input("/usr/bin/python3", sealed=True), image,
            )
            completed, _, _ = session._native_make_readonly("all")
            self.assertEqual((completed.stdout, completed.stderr), (b"owned", b""))
        self.assertTrue(all(image.descriptor == -1 for image in images))
        self.fixture.assert_clean(session)

    def test_sealed_cache_failure_retires_complete_body(self):
        session = self.fixture.session(cache_bytes=64)
        with session:
            before = set(os.listdir("/proc/self/fd"))
            with self.assertRaises(MakeProbeError):
                session._captured_native_runtime_input("/usr/bin/python3", sealed=True)
            self.assertFalse(session.native_runtime_inputs)
            self.assertEqual(set(os.listdir("/proc/self/fd")), before)
        self.fixture.assert_clean(session)

    def test_capture_mode_binds_both_input_and_closure_cache_call_orders(self):
        for closure in (False, True):
            for first in (False, True):
                session = self.fixture.session()
                images = []
                with self.subTest(closure=closure, first=first), session:
                    capture = (
                        session._captured_native_runtime if closure
                        else session._captured_native_runtime_input
                    )
                    captures = {}
                    for sealed in (first, not first, first, not first):
                        value = capture("/usr/bin/find", sealed=sealed)
                        rows = value if closure else (("/usr/bin/find", value),)
                        for path, body in rows:
                            self.assertTrue(isinstance(body, RuntimeImage if sealed else bytes))
                            if sealed:
                                images.append(body)
                        if sealed in captures:
                            self.assertIs(value, captures[sealed])
                        captures[sealed] = value
                    ordinary = dict(captures[False]) if closure else {"/usr/bin/find": captures[False]}
                    protected = dict(captures[True]) if closure else {"/usr/bin/find": captures[True]}
                    self.assertEqual(set(ordinary), set(protected))
                    for path in ordinary:
                        self.assertEqual(image_digest(ordinary[path]), image_digest(protected[path]))
                self.assertTrue(images)
                self.assertTrue(all(image.descriptor == -1 for image in images))
                self.fixture.assert_clean(session)

    def test_sealed_warm_capture_does_not_bypass_legacy_file_quota(self):
        original_path = make_probe._trusted_runtime_path
        for name in ("/usr/bin/python3", "/usr/bin/make"):
            source = Path(name).resolve()
            session = self.fixture.session()
            with self.subTest(source=name), session, patch.object(
                make_probe, "_trusted_runtime_path",
                lambda path, **kwargs: source if path == "/usr/bin/python3"
                else original_path(path, **kwargs),
            ):
                image = session._captured_native_runtime_input("/usr/bin/python3", sealed=True)
                limit = min(len(image) - 1, session.budget.limits.file_bytes)
                # The cold provider's quota must not constrain unrelated session-bootstrap files.
                cold_budget = ProbeBudget(Limits(file_bytes=limit))
                try:
                    def cold_source(path, budget):
                        self.assertIs(budget, session.budget)
                        return cold_budget.read_bytes(make_probe._trusted_runtime_path(path), "control")

                    with patch.object(make_probe, "_trusted_runtime_bytes", cold_source):
                        with self.assertRaisesRegex(MakeProbeError, "file.*byte|file exceeds"):
                            session._captured_native_runtime_input("/usr/bin/python3")
                    self.assertTrue(cold_budget.failed)
                    self.assertEqual(cold_budget.bytes.get("control", 0), 0)
                finally:
                    cold_budget.close()
            self.assertEqual(image.descriptor, -1)
            self.fixture.assert_clean(session)

    def test_alternate_capture_refuses_changed_complete_body_and_retires_descriptors(self):
        original_bytes = make_probe._trusted_runtime_bytes
        original_path = make_probe._trusted_runtime_path
        for first in (False, True):
            session = self.fixture.session()
            with self.subTest(first=first), session:
                session._captured_native_runtime_input("/usr/bin/find", sealed=first)
                descriptors = set(os.listdir("/proc/self/fd"))
                if first:
                    context = patch.object(
                        make_probe, "_trusted_runtime_bytes",
                        lambda path, budget: original_bytes("/usr/bin/printf", budget),
                    )
                else:
                    context = patch.object(
                        make_probe, "_trusted_runtime_path",
                        lambda path: original_path("/usr/bin/printf"),
                    )
                with context, self.assertRaisesRegex(MakeProbeError, "earlier captured runtime"):
                    session._captured_native_runtime_input("/usr/bin/find", sealed=not first)
                self.assertEqual(set(os.listdir("/proc/self/fd")), descriptors)
            self.fixture.assert_clean(session)

    def test_nested_view_shutdown_and_misnesting_close_actual_owned_descriptors(self):
        for shutdown in (True, False):
            budget = ProbeBudget()
            loader = self.fixture.capture_view(budget)
            session = foundation.ProbeSession(
                loader, scratch_root=self.fixture.scratch, budget=budget,
            )
            with self.subTest(shutdown=shutdown), session:
                session._native_make_readonly("all")
                maps = [session.native_runtime_inputs]
                outer, inner = session.select_view(loader), session.select_view(loader)
                outer.__enter__()
                session._native_make_readonly("all")
                maps.append(session.native_runtime_inputs)
                inner.__enter__()
                session._native_make_readonly("all")
                maps.append(session.native_runtime_inputs)
                images = [
                    image for bodies in maps for image in bodies.values()
                    if isinstance(image, RuntimeImage)
                ]
                self.assertGreaterEqual(len(images), 3)
                descriptors = [image.descriptor for image in images]
                try:
                    if shutdown:
                        session.__exit__(None, None, None)
                    else:
                        with self.assertRaisesRegex(MakeProbeError, "nesting order"):
                            outer.__exit__(None, None, None)
                    self.assertTrue(all(not bodies for bodies in maps))
                    self.assertTrue(all(image.descriptor == -1 for image in images))
                    for descriptor in descriptors:
                        with self.assertRaises(OSError):
                            os.fstat(descriptor)
                    self.fixture.assert_clean(session)
                finally:
                    inner.__exit__(None, None, None)
                    if shutdown:
                        outer.__exit__(None, None, None)

    def test_changed_source_and_failed_sealing_retire_created_backing(self):
        path = Path("/usr/bin/python3").resolve()
        original_lstat = Path.lstat

        def changed(source, *args, **kwargs):
            info = original_lstat(source, *args, **kwargs)
            if source == path:
                fields = list(info)
                fields[6] += 1
                return os.stat_result(fields)
            return info

        for failure in ("source", "seal", "interrupt"):
            budget = ProbeBudget()
            before = set(os.listdir("/proc/self/fd"))
            try:
                if failure == "source":
                    context = patch.object(Path, "lstat", changed)
                elif failure == "seal":
                    context = patch("scripts.validation_ownership.runtime_image.fcntl.fcntl",
                                    side_effect=OSError(errno.EIO, "modeled seal failure"))
                else:
                    context = patch("scripts.validation_ownership.runtime_image.os.write",
                                    side_effect=KeyboardInterrupt)
                with self.subTest(failure=failure), context:
                    with self.assertRaises((MakeProbeError, OSError, KeyboardInterrupt)):
                        RuntimeImage(path, budget)
                self.assertEqual(set(os.listdir("/proc/self/fd")), before)
            finally:
                budget.close()

    def test_generated_file_limit_remains_independent_and_bounded(self):
        for name in ("/usr/bin/python3", "/usr/bin/make"):
            with self.subTest(source=name):
                self.assert_generated_file_limit(Path(name).resolve())

    def assert_generated_file_limit(self, path):
        budget = ProbeBudget(Limits(file_bytes=min(path.stat().st_size - 1, Limits().file_bytes)))
        image = RuntimeImage(path, budget)
        try:
            self.assertGreater(len(image), budget.limits.file_bytes)
            with tempfile.TemporaryDirectory() as directory:
                generated = Path(directory) / "generated"
                with generated.open("wb") as stream:
                    stream.truncate(budget.limits.file_bytes + 1)
                with self.assertRaisesRegex(MakeProbeError, "exceeds byte bound"):
                    budget.read_bytes(generated, "output")
        finally:
            image.close()
            budget.close()
