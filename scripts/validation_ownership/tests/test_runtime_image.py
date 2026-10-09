"""Actual complete sealed bodies, materialization, admission and cleanup."""

import errno
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.validation_ownership import make_probe
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
