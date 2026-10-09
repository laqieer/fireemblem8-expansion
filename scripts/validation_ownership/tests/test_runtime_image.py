"""Actual complete sealed bodies, materialization, admission and cleanup."""

import errno
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.validation_ownership.budget import Limits, MakeProbeError, ProbeBudget
from scripts.validation_ownership.runtime_image import (
    IMAGE_SEALS, RuntimeImage, close_images, image_digest, materialize_image,
)
from scripts.validation_ownership.tests import test_foundation as foundation


class RuntimeImageTests(unittest.TestCase):
    def test_complete_body_and_materialization_use_actual_immutable_backing(self):
        path = Path("/usr/bin/python3").resolve()
        budget = ProbeBudget()
        image = RuntimeImage(path, budget)
        descriptor = image.descriptor
        try:
            expected = path.read_bytes()
            self.assertGreater(len(expected), 4 * 1024 * 1024)
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
            self.assertNotIn("/usr/bin/python3", session.native_runtime_inputs)
            self.assertEqual(set(os.listdir("/proc/self/fd")), before)
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
        budget = ProbeBudget(Limits(file_bytes=4 * 1024 * 1024))
        image = RuntimeImage(Path("/usr/bin/python3").resolve(), budget)
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
