"""Atomic text output publication through the real generator and host readers."""

import importlib.util
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import selectors
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/texttools/textprocess.py"


def load_generator(path=SCRIPT):
    huffman_spec = importlib.util.spec_from_file_location("huffman", path.with_name("huffman.py"))
    huffman = importlib.util.module_from_spec(huffman_spec)
    huffman_spec.loader.exec_module(huffman)
    spec = importlib.util.spec_from_file_location("publication_textprocess", path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"huffman": huffman}):
        spec.loader.exec_module(module)
    return module


class ObserveWrites:
    def __init__(self, stream, observe):
        self.stream = stream
        self.observe = observe
        self.observed = False

    def write(self, text):
        count = self.stream.write(text)
        if not self.observed and os.fstat(self.stream.fileno()).st_size:
            self.observed = True
            self.observe(self.stream)
        return count

    def __getattr__(self, name):
        return getattr(self.stream, name)


PRODUCER = r"""
import importlib.util, json, os, socket, sys
from pathlib import Path
script = Path(sys.argv[1])
channel = socket.socket(fileno=int(sys.argv[2]))
channel.settimeout(15)
sys.path.insert(0, str(script.parent))
spec = importlib.util.spec_from_file_location("publication_producer", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
opened_paths = {}
opening = module.os.open
def record_open(path, *args, **kwargs):
    descriptor = opening(path, *args, **kwargs)
    opened_paths[descriptor] = os.fsdecode(path)
    return descriptor
module.os.open = record_open
reading_link = module.os.readlink
def without_proc(path, *args, **kwargs):
    if os.fsdecode(path).startswith("/proc/"):
        raise FileNotFoundError("procfs is unavailable in this portability control")
    return reading_link(path, *args, **kwargs)
module.os.readlink = without_proc
arguments = sys.argv[3:]
sys.argv = [str(script)]
def event(phase, **fields):
    channel.sendall((json.dumps(dict(phase=phase, **fields)) + "\n").encode())
    if channel.recv(1) != b"G":
        raise RuntimeError("publication barrier closed")
class Observed:
    def __init__(self, stream, phase):
        self.stream, self.phase, self.sent = stream, phase, False
    def write(self, text):
        count = self.stream.write(text)
        if not self.sent and os.fstat(self.stream.fileno()).st_size:
            self.sent = True
            event(self.phase, size=os.fstat(self.stream.fileno()).st_size,
                  staging=opened_paths[self.stream.fileno()])
        return count
header = module.write_header
compressed = module.write_all_compressed_data
module.write_header = lambda messages, stream: header(messages, Observed(stream, "header"))
module.write_all_compressed_data = lambda messages, codes, stream: compressed(messages, codes, Observed(stream, "data"))
replacing = module.os.replace
def replace(source, target):
    event("before-replace", target=str(target))
    replacing(source, target)
    event("after-replace", target=str(target))
module.os.replace = replace
module.main(arguments)
event("done")
channel.close()
"""


class TextPublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cc = shutil.which("cc")
        if cls.cc is None:
            raise RuntimeError("the required text publication suite needs a host C compiler")

    def setUp(self):
        parent = ROOT / "build/test-artifacts"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="text publication ", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.source = self.work / "input text.txt"
        self.definitions = self.work / "control chars.txt"
        self.header = self.work / "msg.h"
        self.data = self.work / "msg_data.c"
        self.definitions.write_text("[X] = 0\n[Y] = 1\n", encoding="utf-8")
        (self.work / "global.h").write_text("typedef unsigned char u8;\ntypedef unsigned int u32;\n")

    def inputs(self, count=32, text="[X][Y][X]"):
        self.source.write_text(
            "".join(f"#{index:04X}\n{text}\n" for index in range(1, count + 1)), encoding="utf-8")

    def arguments(self, encoding="utf8"):
        return [str(self.source), str(self.definitions), str(self.data), str(self.header), encoding]

    def generate(self, module=None, encoding="utf8"):
        module = load_generator() if module is None else module
        with mock.patch.object(sys, "argv", [str(SCRIPT)]):
            module.main(self.arguments(encoding))

    def reference(self, encoding="utf8"):
        module = load_generator()
        controls = module.load_control_chars(self.definitions)
        messages = module.process_file(self.source, controls, encoding)
        tree = module.huffman.BuildHuffmanTree(module.GenerateFreqTable(module.all_data))
        table = module.huffman.BuildHuffmanTable()
        codes = module.huffman.build_code_table(tree)
        header, data = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", [str(SCRIPT)]):
            module.write_header(messages, header)
        # Preserve the original serial main's format as an independent oracle.
        data.write('#include "global.h"\n\n')
        module.write_all_compressed_data(messages, codes, data)
        data.write("\n")
        module.write_huffman_table(table, data)
        data.write("\n")
        module.write_text_table(messages, data)
        return header.getvalue().encode("utf-8"), data.getvalue().encode("utf-8")

    def pair(self):
        return self.header.read_bytes(), self.data.read_bytes()

    def seed(self):
        self.inputs(3)
        previous = self.reference()
        self.header.write_bytes(previous[0])
        self.data.write_bytes(previous[1])
        return previous

    def assert_no_staging(self):
        self.assertEqual(list(self.work.glob(".textprocess-*.tmp")), [])

    def readers(self):
        header = subprocess.run([self.cc, "-E", "-x", "c", str(self.header)],
                                capture_output=True, text=True, timeout=10)
        data = subprocess.run(
            [self.cc, "-fsyntax-only", "-std=c99", "-I", str(self.work), "-x", "c", "-"],
            input=(
                f'#include "{self.data.name}"\n'
                'void text_publication_reader(void) {\n'
                '    (void) gMsgTable;\n'
                '    (void) gMsgHuffmanTable;\n'
                '    (void) gMsgHuffmanTableRoot;\n'
                '}\n'
            ),
            capture_output=True, text=True, timeout=10,
        )
        return header, data

    def observe_buffered_output(self, which):
        previous = self.seed()
        self.inputs(5000)
        expected = self.reference()
        module = load_generator()
        observations = []
        def observe(stream):
            results = self.readers()
            observations.append((os.fstat(stream.fileno()).st_size, self.pair()))
            for result in results:
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.pair(), previous)
        if which == "header":
            writer = module.write_header
            module.write_header = lambda messages, stream: writer(messages, ObserveWrites(stream, observe))
        else:
            writer = module.write_all_compressed_data
            module.write_all_compressed_data = lambda messages, codes, stream: writer(messages, codes, ObserveWrites(stream, observe))
        self.generate(module)
        self.assertEqual(len(observations), 1)
        self.assertGreater(observations[0][0], 0)
        self.assertEqual(self.pair(), expected)
        for result in self.readers():
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_staging()

    def test_header_reader_never_observes_natural_buffered_partial_output(self):
        self.observe_buffered_output("header")

    def test_data_reader_never_observes_natural_buffered_partial_output(self):
        self.observe_buffered_output("data")

    def test_direct_writer_control_exposes_partial_header_and_data_to_real_readers(self):
        self.seed()
        self.inputs(5000)
        expected = self.reference()
        module = load_generator()
        observations = {}

        @contextmanager
        def direct_output(path, writer, *arguments):
            rendered = io.StringIO()
            writer(*arguments, rendered)

            def observe(stream):
                observations[Path(path).name] = (
                    os.fstat(stream.fileno()).st_size, self.readers(),
                )

            # The broken publication control is independent of renderer batching.
            with open(path, "w", encoding="utf-8") as stream:
                output = ObserveWrites(stream, observe)
                for line in rendered.getvalue().splitlines(keepends=True):
                    output.write(line)
            yield path, path

        with mock.patch.object(module, "_staged_output", side_effect=direct_output):
            self.generate(module)
        self.assertEqual(set(observations), {"msg.h", "msg_data.c"})
        header_size, header_readers = observations["msg.h"]
        data_size, data_readers = observations["msg_data.c"]
        self.assertGreater(header_size, 0)
        self.assertLess(header_size, len(expected[0]))
        self.assertGreater(data_size, 0)
        self.assertLess(data_size, len(expected[1]))
        self.assertNotEqual(header_readers[0].returncode, 0)
        self.assertNotEqual(data_readers[1].returncode, 0)
        self.assertEqual(self.pair(), expected)
        for result in self.readers():
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_no_staging()

    def test_encodings_serial_bytes_permissions_and_unchanged_regeneration(self):
        for encoding in ("utf8", "cp932"):
            with self.subTest(encoding=encoding):
                self.inputs(text="日本[X][Y][X]")
                expected = self.reference(encoding)
                self.generate(encoding=encoding)
                self.assertEqual(self.pair(), expected)
                self.header.chmod(0o640)
                self.data.chmod(0o604)
                before = [(path.stat().st_ino, path.stat().st_mtime_ns) for path in (self.header, self.data)]
                self.generate(encoding=encoding)
                self.assertEqual(before, [(path.stat().st_ino, path.stat().st_mtime_ns) for path in (self.header, self.data)])
                self.inputs(40, text="日本[X][Y][X]")
                self.generate(encoding=encoding)
                self.assertEqual([stat.S_IMODE(path.stat().st_mode) for path in (self.header, self.data)], [0o640, 0o604])
                self.assertEqual(self.pair(), self.reference(encoding))
                self.assert_no_staging()

    def test_actual_cli_other_cwd_spaces_and_new_file_umask(self):
        self.inputs()
        expected = self.reference()
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *self.arguments()], cwd=self.work,
            umask=0o027, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.pair(), expected)
        self.assertEqual([stat.S_IMODE(path.stat().st_mode) for path in (self.header, self.data)], [0o640, 0o640])
        output = self.work / "message objects.o"
        compiled = subprocess.run([self.cc, "-std=c99", "-c", str(self.data), "-o", str(output)],
                                  capture_output=True, text=True, timeout=10)
        self.assertEqual(compiled.returncode, 0, compiled.stderr)
        self.assertTrue(output.is_file())
        self.assert_no_staging()

    def test_existing_output_symlink_keeps_its_target_and_mode(self):
        self.inputs()
        target = self.work / "real header.h"
        target.write_text("old")
        target.chmod(0o640)
        self.header.symlink_to(target.name)
        self.generate()
        self.assertTrue(self.header.is_symlink())
        self.assertEqual(self.pair(), self.reference())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
        self.assertEqual(list(self.work.glob(".*.tmp")), [])

    def test_long_valid_output_names_do_not_overflow_staging_names(self):
        self.inputs()
        self.header = self.work / ("h" * 240 + ".h")
        self.data = self.work / ("d" * 240 + ".c")
        self.generate()
        self.assertEqual(self.pair(), self.reference())
        self.assert_no_staging()

    def test_parse_render_and_second_stage_failures_preserve_previous_pair(self):
        previous = self.seed()
        self.source.write_text('#include "missing input.txt"\n')
        with self.assertRaises(FileNotFoundError):
            self.generate()
        self.assertEqual(self.pair(), previous)
        for writer_name in ("write_header", "write_all_compressed_data", "write_text_table"):
            with self.subTest(writer=writer_name):
                self.inputs(5000)
                module = load_generator()
                def fail(*args):
                    args[-1].write("incomplete staged output")
                    raise ValueError("controlled renderer failure")
                with mock.patch.object(module, writer_name, side_effect=fail):
                    with self.assertRaisesRegex(ValueError, "controlled renderer failure"):
                        self.generate(module)
                self.assertEqual(self.pair(), previous)
                self.assert_no_staging()
        module = load_generator()
        opening = os.open
        staged = []
        def fail_second(path, flags, *args, **kwargs):
            if Path(path).name.startswith(".textprocess-"):
                staged.append(path)
                if len(staged) == 2:
                    raise OSError("controlled second staging failure")
            return opening(path, flags, *args, **kwargs)
        with mock.patch.object(module.os, "open", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "second staging"):
                self.generate(module)
        self.assertEqual(self.pair(), previous)
        self.assert_no_staging()

    def test_actual_write_and_close_failures_do_not_publish(self):
        previous = self.seed()
        self.inputs(5000)
        for target in ("msg.h", "msg_data.c"):
            for operation in ("write", "close"):
                with self.subTest(target=target, operation=operation):
                    module = load_generator()
                    fdopen = os.fdopen
                    handles = []
                    class Faulted:
                        def __init__(self, stream, selected):
                            self.stream, self.selected, self.failed = stream, selected, False
                        def __getattr__(self, name):
                            return getattr(self.stream, name)
                        def __enter__(self):
                            return self
                        def __exit__(self, *args):
                            self.close()
                        def write(self, text):
                            if self.selected and operation == "write" and not self.failed:
                                self.failed = True
                                raise OSError("controlled write failure")
                            return self.stream.write(text)
                        def close(self):
                            if self.selected and operation == "close" and not self.failed:
                                self.failed = True
                                raise OSError("controlled close failure")
                            self.stream.close()
                    def wrap(descriptor, *args, **kwargs):
                        selected = len(handles) == (0 if target == "msg.h" else 1)
                        handle = Faulted(fdopen(descriptor, *args, **kwargs), selected)
                        handles.append(handle)
                        return handle
                    with mock.patch.object(module.os, "fdopen", side_effect=wrap):
                        with self.assertRaisesRegex(OSError, "controlled"):
                            self.generate(module)
                    self.assertTrue(all(handle.closed for handle in handles))
                    self.assertEqual(self.pair(), previous)
                    self.assert_no_staging()

    def test_absent_outputs_stay_absent_on_render_failure_and_both_stages_close_before_replace(self):
        self.inputs(5000)
        module = load_generator()
        with mock.patch.object(module, "write_text_table", side_effect=ValueError("late render failure")):
            with self.assertRaisesRegex(ValueError, "late render"):
                self.generate(module)
        self.assertFalse(self.header.exists())
        self.assertFalse(self.data.exists())
        self.assert_no_staging()
        handles, published = [], []
        fdopen, replacing = os.fdopen, os.replace
        def track(descriptor, *args, **kwargs):
            handle = fdopen(descriptor, *args, **kwargs)
            handles.append(handle)
            return handle
        def replace(source, target):
            self.assertEqual(len(handles), 2)
            self.assertTrue(all(handle.closed for handle in handles))
            published.append(Path(target))
            replacing(source, target)
        with mock.patch("os.fdopen", side_effect=track), mock.patch("os.replace", side_effect=replace):
            self.generate()
        self.assertEqual(published, [self.header, self.data])
        self.assertEqual(self.pair(), self.reference())
        self.assert_no_staging()

    def test_replace_failure_is_per_file_and_never_rolls_back_another_writer(self):
        previous = self.seed()
        self.inputs(5000)
        module = load_generator()
        with mock.patch.object(module.os, "replace", side_effect=OSError("first replace failed")):
            with self.assertRaisesRegex(OSError, "first replace"):
                self.generate(module)
        self.assertEqual(self.pair(), previous)
        self.assert_no_staging()
        competing = b"#ifndef MSG_H\n#define MSG_H\n#define OTHER_WRITER 1\n#endif\n"
        replacing = os.replace
        def replace(source, target):
            if Path(target) == self.header:
                replacing(source, target)
                self.header.write_bytes(competing)
            else:
                raise OSError("second replace failed")
        with mock.patch.object(module.os, "replace", side_effect=replace):
            with self.assertRaisesRegex(OSError, "second replace"):
                self.generate()
        self.assertEqual(self.pair(), (competing, previous[1]))
        self.assert_no_staging()

    def test_only_owned_staging_is_cleaned_and_cleanup_errors_propagate(self):
        self.inputs()
        sentinel = self.work / ".unrelated.tmp"
        sentinel.write_text("retain")
        unlink = os.unlink
        selected = []
        def fail(path, *args, **kwargs):
            if Path(path).name.startswith(".textprocess-") and not selected:
                selected.append(Path(path))
                raise PermissionError("controlled staging cleanup failure")
            return unlink(path, *args, **kwargs)
        self.generate()
        with mock.patch("os.unlink", side_effect=fail):
            with self.assertRaisesRegex(PermissionError, "cleanup failure"):
                self.generate()
        self.assertEqual(len(selected), 1)
        self.assertTrue(selected[0].is_file())
        self.assertEqual(sentinel.read_text(), "retain")
        selected[0].unlink()
        self.assert_no_staging()

    def test_two_real_producers_overlap_at_buffered_writes_and_publish_complete_files(self):
        previous = self.seed()
        self.inputs(5000)
        expected = self.reference()
        processes, channels, streams = [], [], []
        try:
            for _ in range(2):
                parent, child = socket.socketpair()
                parent.settimeout(15)
                channels.append(parent)
                try:
                    process = subprocess.Popen(
                        [sys.executable, "-I", "-c", PRODUCER, str(SCRIPT), str(child.fileno()), *self.arguments()],
                        cwd=self.work, pass_fds=(child.fileno(),), stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True,
                    )
                    processes.append(process)
                finally:
                    child.close()
                streams.append(parent.makefile("rb"))
            stages = []
            for phase in ("header", "data"):
                for stream in streams:
                    event = json.loads(stream.readline())
                    self.assertEqual(event["phase"], phase)
                    self.assertGreater(event["size"], 0)
                    stages.append(event["staging"])
                self.assertEqual(self.pair(), previous)
                for result in self.readers():
                    self.assertEqual(result.returncode, 0, result.stderr)
                for channel in channels:
                    channel.sendall(b"G")
            self.assertEqual(len(set(stages)), 4)
            self.assertTrue(all(Path(path).parent == self.work for path in stages))
            phases = []
            with selectors.DefaultSelector() as selector:
                for index, channel in enumerate(channels):
                    selector.register(channel, selectors.EVENT_READ, index)
                while selector.get_map():
                    ready = selector.select(15)
                    self.assertTrue(ready, "producer did not reach its next publication barrier")
                    for key, _ in ready:
                        event = json.loads(streams[key.data].readline())
                        phases.append(event["phase"])
                        pair = self.pair()
                        self.assertIn(pair[0], (previous[0], expected[0]))
                        self.assertIn(pair[1], (previous[1], expected[1]))
                        for result in self.readers():
                            self.assertEqual(result.returncode, 0, result.stderr)
                        key.fileobj.sendall(b"G")
                        if event["phase"] == "done":
                            selector.unregister(key.fileobj)
            self.assertIn("before-replace", phases)
            self.assertIn("after-replace", phases)
            for process in processes:
                stdout, stderr = process.communicate(timeout=15)
                self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertEqual(self.pair(), expected)
            self.assert_no_staging()
        finally:
            for process in processes:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=15)
                process.stdout.close()
                process.stderr.close()
            for stream in streams:
                stream.close()
            for channel in channels:
                channel.close()


if __name__ == "__main__":
    unittest.main()
