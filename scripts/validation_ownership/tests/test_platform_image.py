"""Complete independent sealed platform body and materialization controls."""

import errno
import fcntl
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.validation_ownership.authority import ENVIRONMENT
from scripts.validation_ownership.budget import Limits, MakeProbeError, ProbeBudget
from scripts.validation_ownership.runtime_image import (
    BLOCK_BYTES, IMAGE_SEALS, RuntimeImage, close_images, image_digest, materialize_image,
)


class PlatformImageTests(unittest.TestCase):
    def assert_source_observation_refused(self, phase, field, value):
        budget = ProbeBudget()
        descriptors = []
        source_descriptor = None
        source_observations = 0
        original_open, original_memfd = os.open, os.memfd_create
        original_stat, original_lstat = os.fstat, Path.lstat
        source = Path("/usr/bin/make").resolve()
        fields = (
            "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size",
            "st_mtime_ns", "st_ctime_ns",
        )

        def changed(info):
            values = {name: getattr(info, name) for name in fields}
            values[field] = value(info)
            return SimpleNamespace(**values)

        def open_source(*args, **kwargs):
            nonlocal source_descriptor
            source_descriptor = original_open(*args, **kwargs)
            descriptors.append(source_descriptor)
            return source_descriptor

        def backing(*args, **kwargs):
            descriptor = original_memfd(*args, **kwargs)
            descriptors.append(descriptor)
            return descriptor

        def fstat(descriptor):
            nonlocal source_observations
            info = original_stat(descriptor)
            if descriptor == source_descriptor:
                source_observations += 1
                if phase == "admission" or phase == "descriptor" and source_observations > 1:
                    return changed(info)
            return info

        def lstat(path, *args, **kwargs):
            info = original_lstat(path, *args, **kwargs)
            return changed(info) if phase == "path" and path == source else info

        try:
            with patch("scripts.validation_ownership.runtime_image.os.open", open_source), patch(
                "scripts.validation_ownership.runtime_image.os.memfd_create", backing,
            ), patch(
                "scripts.validation_ownership.runtime_image.os.fstat", fstat,
            ), patch.object(Path, "lstat", lstat):
                with self.assertRaisesRegex(
                    MakeProbeError,
                    "untrusted backing" if phase == "admission" else "changed during complete capture",
                ):
                    RuntimeImage(source, budget)
            self.assertEqual(len(descriptors), 1 if phase == "admission" else 2)
            for descriptor in descriptors:
                with self.assertRaises(OSError) as closed:
                    original_stat(descriptor)
                self.assertEqual(closed.exception.errno, errno.EBADF)
        finally:
            for descriptor in descriptors:
                try:
                    original_stat(descriptor)
                except OSError as error:
                    if error.errno != errno.EBADF:
                        raise
                else:
                    os.close(descriptor)
            budget.close()

    def test_source_admission_rejects_each_untrusted_observation_before_backing(self):
        for field, value in (
            ("st_mode", lambda info: stat.S_IFIFO | 0o444),
            ("st_size", lambda info: 0),
            ("st_size", lambda info: -1),
            ("st_uid", lambda info: 1),
            ("st_mode", lambda info: info.st_mode | stat.S_IWGRP),
            ("st_mode", lambda info: info.st_mode | stat.S_IWOTH),
            ("st_mode", lambda info: info.st_mode | stat.S_ISUID),
            ("st_mode", lambda info: info.st_mode | stat.S_ISGID),
            ("st_mode", lambda info: info.st_mode | stat.S_ISVTX),
        ):
            with self.subTest(field=field, value=value):
                self.assert_source_observation_refused("admission", field, value)

    def test_capture_rejects_each_changed_descriptor_and_path_identity(self):
        for phase in ("descriptor", "path"):
            for field in (
                "st_dev", "st_ino", "st_mode", "st_uid", "st_gid", "st_size",
                "st_mtime_ns", "st_ctime_ns",
            ):
                with self.subTest(phase=phase, field=field):
                    self.assert_source_observation_refused(
                        phase, field, lambda info: getattr(info, field) + 1,
                    )

    def test_slices_enforce_actual_workspace_and_key_boundaries(self):
        budget = ProbeBudget()
        source = Path("/usr/bin/make").resolve()
        expected = source.read_bytes()
        self.assertGreater(len(expected), BLOCK_BYTES)
        image = RuntimeImage(source, budget)
        original_read = os.pread
        try:
            for key in (
                slice(0, 0), slice(0, BLOCK_BYTES - 1), slice(0, BLOCK_BYTES),
                slice(-BLOCK_BYTES, None), slice(len(expected), len(expected) + 1),
                slice(17, 81, 1), slice(81, 17), slice(-10, -2),
            ):
                with self.subTest(key=key), patch(
                    "scripts.validation_ownership.runtime_image.os.pread", wraps=original_read,
                ) as read:
                    self.assertEqual(image[key], expected[key])
                    self.assertEqual(read.call_count, 1)
                    self.assertLessEqual(read.call_args.args[1], BLOCK_BYTES)
            for key in (0, False, None, "body", slice(0, 17, 0), slice(0, 17, 2), slice(17, 0, -1)):
                with self.subTest(key=key), patch(
                    "scripts.validation_ownership.runtime_image.os.pread", wraps=original_read,
                ) as read:
                    with self.assertRaisesRegex(MakeProbeError, "bounded .* slice"):
                        image[key]
                    read.assert_not_called()
            for key in (slice(0, BLOCK_BYTES + 1), slice(None, None), slice(-BLOCK_BYTES - 1, None)):
                with self.subTest(key=key), patch(
                    "scripts.validation_ownership.runtime_image.os.pread", wraps=original_read,
                ) as read:
                    with self.assertRaisesRegex(MakeProbeError, "workspace bound"):
                        image[key]
                    read.assert_not_called()
            for size in (1, BLOCK_BYTES):
                with self.subTest(short_read=size), patch(
                    "scripts.validation_ownership.runtime_image.os.pread",
                    side_effect=lambda descriptor, amount, offset: original_read(
                        descriptor, amount - 1, offset,
                    ),
                ):
                    with self.assertRaisesRegex(MakeProbeError, "slice ended unexpectedly"):
                        image[:size]
            image.close()
            with self.assertRaisesRegex(MakeProbeError, "owned complete sealed body"):
                image[:1]
        finally:
            image.close()
            budget.close()

    def test_slice_quota_and_deadline_refuse_before_reading_owned_body(self):
        for fault in ("control", "deadline"):
            budget = ProbeBudget()
            image = RuntimeImage(Path("/usr/bin/make").resolve(), budget)
            descriptor = image.descriptor
            try:
                if fault == "control":
                    budget.limits = Limits(
                        control_bytes=budget.bytes["control"] + 128 + sys.getsizeof(b"") + 64 - 1,
                    )
                else:
                    budget.started = 0
                with self.subTest(fault=fault), patch(
                    "scripts.validation_ownership.runtime_image.os.pread", wraps=os.pread,
                ) as read:
                    with self.assertRaisesRegex(
                        MakeProbeError,
                        "control byte budget exhausted" if fault == "control" else "deadline/budget exhausted",
                    ):
                        image[:64]
                    read.assert_not_called()
                    self.assertTrue(budget.failed)
            finally:
                image.close()
                with self.assertRaises(OSError) as closed:
                    os.fstat(descriptor)
                self.assertEqual(closed.exception.errno, errno.EBADF)
                budget.close()

    def test_capture_preserves_primary_with_source_and_backing_close_failures(self):
        for fault in ("read", "backing-write", "deadline", "source-close"):
            budget = ProbeBudget()
            descriptors = []
            primary = OSError(errno.EIO, "capture operation failed")
            source_close = OSError(errno.EIO, "source close failed")
            backing_close = OSError(errno.EIO, "backing close failed")
            original_fdopen, original_memfd = os.fdopen, os.memfd_create
            original_write, original_close = os.write, os.close
            backing = None

            class Source:
                def __init__(self, stream):
                    self.stream = stream
                    descriptors.append(stream.fileno())

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.close()

                def fileno(self):
                    return self.stream.fileno()

                def readinto(self, data):
                    nonlocal primary
                    if fault == "read":
                        raise primary
                    if fault == "deadline":
                        budget.started = 0
                        try:
                            budget.remaining()
                        except MakeProbeError as error:
                            primary = error
                            raise
                    return self.stream.readinto(data)

                def close(self):
                    self.stream.close()
                    raise source_close

            def memfd(*args, **kwargs):
                nonlocal backing
                backing = original_memfd(*args, **kwargs)
                descriptors.append(backing)
                return backing

            def write(descriptor, data):
                if fault == "backing-write":
                    raise primary
                return original_write(descriptor, data)

            def close(descriptor):
                original_close(descriptor)
                if descriptor == backing:
                    raise backing_close

            try:
                with self.subTest(fault=fault), patch(
                    "scripts.validation_ownership.runtime_image.os.fdopen",
                    side_effect=lambda *args, **kwargs: Source(original_fdopen(*args, **kwargs)),
                ), patch(
                    "scripts.validation_ownership.runtime_image.os.memfd_create", memfd,
                ), patch(
                    "scripts.validation_ownership.runtime_image.os.write", write,
                ), patch(
                    "scripts.validation_ownership.runtime_image.os.close", close,
                ):
                    with self.assertRaises(BaseException) as raised:
                        RuntimeImage(Path("/usr/bin/make").resolve(), budget)
                    self.assertIs(raised.exception, source_close if fault == "source-close" else primary)
                    diagnostics = raised.exception.cleanup_errors
                    self.assertTrue(any("source close failed" in item for item in diagnostics))
                    self.assertTrue(any("backing close failed" in item for item in diagnostics))
                    self.assertEqual(len(descriptors), 2)
                    for descriptor in descriptors:
                        with self.assertRaises(OSError) as closed:
                            os.fstat(descriptor)
                        self.assertEqual(closed.exception.errno, errno.EBADF)
            finally:
                for descriptor in descriptors:
                    try:
                        os.fstat(descriptor)
                    except OSError as error:
                        if error.errno != errno.EBADF:
                            raise
                    else:
                        original_close(descriptor)
                budget.close()

    def test_real_frontend_body_uses_snapshot_not_source_file_allowance(self):
        selected = subprocess.run(
            ["/usr/bin/cc", "-print-prog-name=cc1"], env=ENVIRONMENT, cwd="/",
            capture_output=True, check=True, timeout=30,
        )
        path = Path(selected.stdout.decode().strip()).resolve(strict=True)
        expected = path.read_bytes()
        extent = len(expected)
        budget = ProbeBudget(Limits(file_bytes=min(Limits().file_bytes, extent - 1)))
        image = RuntimeImage(path, budget)
        try:
            self.assertEqual(len(image), extent)
            self.assertEqual(image_digest(image), hashlib.sha256(expected).hexdigest())
            self.assertEqual(budget.bytes["snapshot"], extent)
            self.assertEqual(fcntl.fcntl(image.descriptor, fcntl.F_GET_SEALS), IMAGE_SEALS)
            with tempfile.TemporaryDirectory() as name:
                output = Path(name) / "frontend"
                self.assertEqual(materialize_image(output, image), extent)
                self.assertEqual(output.read_bytes(), expected)
        finally:
            image.close()
            budget.close()
        before = set(os.listdir("/proc/self/fd"))
        rejected = ProbeBudget(Limits(snapshot_bytes=extent - 1))
        try:
            with self.assertRaisesRegex(MakeProbeError, "snapshot byte budget exhausted"):
                RuntimeImage(path, rejected)
            self.assertTrue(rejected.failed)
            self.assertEqual(set(os.listdir("/proc/self/fd")), before)
        finally:
            rejected.close()

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
