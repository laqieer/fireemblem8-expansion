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
