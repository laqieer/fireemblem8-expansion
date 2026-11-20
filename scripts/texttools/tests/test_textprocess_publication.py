"""Atomic text output publication through the real generator and host readers."""

import importlib.util
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
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

    def inputs(self, count=32, text="[Y][X]"):
        self.source.write_text(
            "".join(f"#{index:04X}\n{text}\n" for index in range(count)), encoding="utf-8")

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

    def decoded_messages(self):
        generated = self.data.read_text(encoding="utf-8")
        table = re.search(r"gMsgHuffmanTable\[\] = \{([^}]*)\}", generated).group(1)
        nodes = [int(value, 16) for value in re.findall(r"0x[0-9A-Fa-f]+", table)]
        arrays = {
            name: bytes(int(value, 16) for value in re.findall(r"0x[0-9A-Fa-f]+", values))
            for name, values in re.findall(
                r"CompressedText_(\w+)\[\] = \{([^}]*)\}", generated
            )
        }
        pointers = re.search(r"gMsgTable\[\] = \{([^}]*)\}", generated).group(1)
        names = re.findall(r"CompressedText_(\w+)", pointers)
        spec = importlib.util.spec_from_file_location(
            "publication_textdecoder", SCRIPT.with_name("textdecoder.py")
        )
        decoder = importlib.util.module_from_spec(spec)
        with mock.patch.object(sys, "path", [str(SCRIPT.parent), *sys.path]):
            spec.loader.exec_module(decoder)
        with mock.patch.object(decoder, "ROOT_NODE", len(nodes) - 1):
            return [decoder.decode_data(arrays[name], nodes, 0) for name in names]

    def decoded_payloads(self):
        decoded = []
        for message in self.decoded_messages():
            raw = bytearray()
            for value in message:
                raw.append(value & 0xff)
                if value >> 8:
                    raw.append(value >> 8)
            decoded.append(bytes(raw))
        return decoded

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
                self.inputs(text="日本[Y][X]")
                expected = self.reference(encoding)
                self.generate(encoding=encoding)
                self.assertEqual(self.pair(), expected)
                self.header.chmod(0o640)
                self.data.chmod(0o604)
                before = [(path.stat().st_ino, path.stat().st_mtime_ns) for path in (self.header, self.data)]
                self.generate(encoding=encoding)
                self.assertEqual(before, [(path.stat().st_ino, path.stat().st_mtime_ns) for path in (self.header, self.data)])
                self.inputs(40, text="日本[Y][X]")
                self.generate(encoding=encoding)
                self.assertEqual([stat.S_IMODE(path.stat().st_mode) for path in (self.header, self.data)], [0o640, 0o604])
                self.assertEqual(self.pair(), self.reference(encoding))
                self.assert_no_staging()

    def test_make_rebuilds_both_shared_text_goals_when_generator_changes(self):
        parent = ROOT / "build/test-artifacts"
        for legacy in (False, True):
            for dependency in ("textprocess.py", "huffman.py"):
                with self.subTest(legacy=legacy, dependency=dependency), \
                     tempfile.TemporaryDirectory(prefix="text-make-", dir=parent) as directory:
                    work = Path(directory)
                    tools = work / "tools"
                    tools.mkdir()
                    for name in ("textprocess.py", "huffman.py", "legacy_text_source.py"):
                        shutil.copy2(SCRIPT.parent / name, tools / name)
                    source = work / "texts.txt"
                    definitions = work / "textdefs.txt"
                    default_dir, legacy_dir = work / "default", work / "legacy"
                    default_dir.mkdir()
                    legacy_dir.mkdir()
                    include_dir = work / "query-include"
                    include_dir.mkdir()
                    legacy_source = legacy_dir / "texts.txt"
                    source.write_text("#0000\nA[Y][X]\n", encoding="utf-8")
                    definitions.write_text("[X] = 0\n[Y] = 1\n", encoding="utf-8")
                    legacy_source.write_bytes(source.read_bytes())
                    output_dir = legacy_dir if legacy else default_dir
                    output, header = output_dir / "msg_data.c", output_dir / "msg.h"
                    stamp = output_dir / ".text.stamp"
                    consumer, header_consumer = output_dir / "consumer.c", output_dir / "consumer.h"
                    consumer_rules = work / "consumers.mk"
                    consumer_rules.write_text(
                        f'{consumer}: {output}\n\t@cp "$<" "$@"\n'
                        f'{header_consumer}: {header}\n\t@cp "$<" "$@"\n',
                        encoding="utf-8",
                    )
                    counter = work / "producer-calls"
                    wrapper = work / "run-producer.py"
                    wrapper.write_text(
                        "import runpy, sys\n"
                        "from pathlib import Path\n"
                        "script, counter = map(Path, sys.argv[1:3])\n"
                        "with counter.open('a', encoding='ascii') as stream:\n"
                        "    stream.write('1\\n')\n"
                        "sys.argv = [str(script), *sys.argv[3:]]\n"
                        "sys.path.insert(0, str(script.parent))\n"
                        "runpy.run_path(str(script), run_name='__main__')\n",
                        encoding="utf-8",
                    )
                    common = [
                        "make", "--no-print-directory", "-j2", "-f", str(ROOT / "Makefile"),
                        "-f", str(consumer_rules), "NODEP=1",
                        "FE8_PROJECT=default", "FE8_PROJECT_PROFILE=default",
                        "MODERN_CONFIG=release", "MODERN_ABI=aapcs",
                        f"MODERN_BUILD_ROOT={work / 'modern'}",
                        f"PYTHON={sys.executable}", f"TEXT_TOOLS={tools}",
                        f"TEXT_PROCESS={sys.executable} {wrapper} {tools / 'textprocess.py'} {counter}",
                        f"TEXT_MAIN={source}", f"TEXT_SRC={source}", f"TEXT_DEFS={definitions}",
                        f"MSG_LIST={default_dir / 'msg_data.c'}",
                        f"TEXT_HEADER={default_dir / 'msg.h'}",
                        f"TEXT_STAMP={default_dir / '.text.stamp'}",
                        f"LEGACY_TEXT_MAIN={legacy_source}",
                        f"LEGACY_TEXT_FILTER={tools / 'legacy_text_source.py'}",
                        f"LEGACY_MSG_LIST={legacy_dir / 'msg_data.c'}",
                        f"LEGACY_TEXT_HEADER={legacy_dir / 'msg.h'}",
                        f"LEGACY_TEXT_STAMP={legacy_dir / '.text.stamp'}",
                    ]
                    # The unrelated source must not cause the archival filter to run.
                    base = time.time_ns() - 10_000_000_000
                    for path in (source, definitions, *tools.iterdir()):
                        os.utime(path, ns=(base, base))
                    os.utime(legacy_source, ns=(base + 1_000_000_000,) * 2)

                    def run_make(*targets):
                        return subprocess.run(
                            [*common, *(str(target) for target in targets or (output,))], cwd=ROOT,
                            capture_output=True, text=True, timeout=30,
                        )

                    def question(*options):
                        return subprocess.run(
                            [*common, "MAKE_RESTARTS=1", "-q", *options, str(output)], cwd=ROOT,
                            capture_output=True, text=True, timeout=30,
                        )

                    def calls():
                        return len(counter.read_text(encoding="ascii").splitlines())

                    def change_after_stamp(path):
                        now = time.time_ns()
                        for item in (source, definitions, *tools.iterdir(), legacy_source):
                            os.utime(item, ns=(now - 4_000_000_000,) * 2)
                        os.utime(stamp, ns=(now - 3_000_000_000,) * 2)
                        os.utime(path, ns=(now - 2_000_000_000,) * 2)

                    first = run_make()
                    self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
                    self.assertEqual(calls(), 1)
                    previous = output.read_bytes(), header.read_bytes()
                    times = output.stat().st_mtime_ns, header.stat().st_mtime_ns
                    initial_consumers = run_make(consumer, header_consumer)
                    self.assertEqual(
                        initial_consumers.returncode, 0,
                        initial_consumers.stdout + initial_consumers.stderr,
                    )
                    consumer_times = consumer.stat().st_mtime_ns, header_consumer.stat().st_mtime_ns
                    self.assertEqual(run_make(consumer, header_consumer).returncode, 0)
                    self.assertEqual(calls(), 1)
                    self.assertEqual(question().returncode, 0)
                    for flag_override in (
                        "MAKEFLAGS=--no-print-directory",
                        "MFLAGS=--no-print-directory",
                    ):
                        with self.subTest(flag_override=flag_override):
                            result = question(flag_override, f"-I{include_dir}")
                            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    ambiguous_question = question(
                        "MAKEFLAGS=--no-print-directory", "MFLAGS=--no-print-directory")
                    self.assertEqual(ambiguous_question.returncode, 1)
                    for flags in (
                        "MAKEFLAGS=--question",
                        f"MAKEFLAGS=-qsI{include_dir}",
                        f"MAKEFLAGS=-qsf{include_dir}",
                        "MAKEFLAGS=-qsOtarget",
                    ):
                        with self.subTest(question_origin=flags):
                            implicit_question = subprocess.run(
                                [*common, "MAKE_RESTARTS=1", flags, str(output)],
                                cwd=ROOT, capture_output=True, text=True, timeout=30,
                            )
                            self.assertEqual(
                                implicit_question.returncode, 0,
                                implicit_question.stdout + implicit_question.stderr,
                            )

                    tool = tools / dependency
                    tool.write_text(tool.read_text(encoding="utf-8") + "\n# fixture refresh\n",
                                    encoding="utf-8")
                    change_after_stamp(tool)
                    refreshed = run_make()
                    self.assertEqual(refreshed.returncode, 0, refreshed.stdout + refreshed.stderr)
                    self.assertEqual(calls(), 2)
                    self.assertEqual((output.read_bytes(), header.read_bytes()), previous)
                    self.assertEqual((output.stat().st_mtime_ns, header.stat().st_mtime_ns), times)
                    self.assertEqual(run_make(consumer, header_consumer).returncode, 0)
                    self.assertEqual(
                        (consumer.stat().st_mtime_ns, header_consumer.stat().st_mtime_ns),
                        consumer_times,
                    )
                    self.assertEqual(calls(), 2)
                    self.assertEqual(question().returncode, 0)

                    selected_source = legacy_source if legacy else source
                    selected_source.write_text("#0000\nB[Y][X]\n## EXTRA\nC[X]\n", encoding="utf-8")
                    change_after_stamp(selected_source)
                    updated = run_make(consumer, header_consumer)
                    self.assertEqual(updated.returncode, 0, updated.stdout + updated.stderr)
                    self.assertEqual(calls(), 3)
                    self.assertNotEqual(output.read_bytes(), previous[0])
                    self.assertNotEqual(header.read_bytes(), previous[1])
                    self.assertEqual(consumer.read_bytes(), output.read_bytes())
                    self.assertEqual(header_consumer.read_bytes(), header.read_bytes())
                    self.assertEqual(run_make(consumer, header_consumer).returncode, 0)
                    self.assertEqual(calls(), 3)

                    for missing in (output, header):
                        missing.unlink()
                        self.assertEqual(question().returncode, 1)
                        restored = run_make()
                        self.assertEqual(restored.returncode, 0, restored.stdout + restored.stderr)
                        self.assertTrue(
                            output.is_file() and header.is_file(),
                            f"failed to restore {missing}: {restored.stdout}{restored.stderr}",
                        )
                    self.assertEqual(calls(), 5)

                    stamp.unlink()
                    self.assertEqual(question().returncode, 1)
                    restored_stamp = run_make(consumer, header_consumer)
                    self.assertEqual(
                        restored_stamp.returncode, 0,
                        restored_stamp.stdout + restored_stamp.stderr,
                    )
                    self.assertTrue(stamp.is_file())
                    self.assertEqual(calls(), 5)

                    output.unlink()
                    header.unlink()
                    restored_pair = run_make(output, header)
                    self.assertEqual(
                        restored_pair.returncode, 0, restored_pair.stdout + restored_pair.stderr,
                    )
                    self.assertTrue(output.is_file() and header.is_file())
                    self.assertEqual(calls(), 6)
                    self.assertEqual(run_make().returncode, 0)
                    self.assertEqual(calls(), 6)
                    synchronized = run_make(consumer, header_consumer)
                    self.assertEqual(
                        synchronized.returncode, 0, synchronized.stdout + synchronized.stderr,
                    )
                    self.assertEqual(calls(), 6)

                    before = output.read_bytes()
                    before_times = (
                        output.stat().st_mtime_ns, header.stat().st_mtime_ns,
                        consumer.stat().st_mtime_ns, header_consumer.stat().st_mtime_ns,
                    )
                    self.assertEqual(question("-W", str(definitions)).returncode, 1)
                    self.assertEqual(calls(), 6)
                    forced = subprocess.run(
                        [*common, "-W", str(definitions), str(consumer), str(header_consumer)],
                        cwd=ROOT, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(forced.returncode, 0, forced.stdout + forced.stderr)
                    self.assertEqual(calls(), 7)
                    self.assertEqual(
                        (output.stat().st_mtime_ns, header.stat().st_mtime_ns,
                         consumer.stat().st_mtime_ns, header_consumer.stat().st_mtime_ns),
                        before_times,
                    )

                    definition_time = definitions.stat().st_mtime_ns
                    definitions.write_text("[X] = 0\n[Y] = 2\n", encoding="utf-8")
                    os.utime(definitions, ns=(definition_time, definition_time))
                    forced = subprocess.run(
                        [*common, f"-I{include_dir}", "-W", str(definitions),
                         str(consumer), str(header_consumer)],
                        cwd=ROOT, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(forced.returncode, 0, forced.stdout + forced.stderr)
                    self.assertEqual(calls(), 8)
                    self.assertNotEqual(output.read_bytes(), before)
                    self.assertEqual(consumer.read_bytes(), output.read_bytes())
                    self.assertEqual(header_consumer.read_bytes(), header.read_bytes())

                    before = output.read_bytes()
                    definitions.write_text("[X] = 0\n[Y] = 3\n", encoding="utf-8")
                    os.utime(definitions, ns=(definition_time, definition_time))
                    ambiguous_build = subprocess.run(
                        [*common, "MAKE_RESTARTS=1", "MAKEFLAGS=--no-print-directory",
                         "MFLAGS=--no-print-directory", f"-I{include_dir}",
                         "-W", str(definitions), str(consumer), str(header_consumer)],
                        cwd=ROOT, capture_output=True, text=True, timeout=30,
                    )
                    self.assertEqual(
                        ambiguous_build.returncode, 0,
                        ambiguous_build.stdout + ambiguous_build.stderr,
                    )
                    self.assertEqual(calls(), 9)
                    self.assertNotEqual(output.read_bytes(), before)
                    self.assertEqual(consumer.read_bytes(), output.read_bytes())
                    self.assertEqual(header_consumer.read_bytes(), header.read_bytes())

                    flag_options = (
                        ("--eval=MFLAGS=-q",),
                        ("MFLAGS=-q",),
                        ("--eval=MFLAGS=-q", "MAKEFLAGS=q=1"),
                        *((f"MAKEFLAGS=-s{flag}{include_dir}",)
                          for flag in ("I", "C", "f", "o", "W")),
                        ("MAKEFLAGS=-sEq:",),
                        ("MAKEFLAGS=-sj2q",),
                        ("MAKEFLAGS=-sl2q",),
                    )
                    for value, options in enumerate(flag_options, 4):
                        with self.subTest(flag_injection=options):
                            before = output.read_bytes()
                            definitions.write_text(
                                f"[X] = 0\n[Y] = {value}\n", encoding="utf-8"
                            )
                            os.utime(definitions, ns=(definition_time, definition_time))
                            rebuilt = subprocess.run(
                                [*common, *options, "-W", str(definitions),
                                 str(consumer), str(header_consumer)],
                                cwd=ROOT, capture_output=True, text=True, timeout=30,
                            )
                            self.assertEqual(
                                rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr
                            )
                            self.assertNotEqual(output.read_bytes(), before)
                            self.assertEqual(consumer.read_bytes(), output.read_bytes())
                            self.assertEqual(header_consumer.read_bytes(), header.read_bytes())
                    self.assertEqual(calls(), 20)

                    previous = output.read_bytes(), header.read_bytes()
                    selected_source.write_text("#0000\n[MISSING][X]\n", encoding="utf-8")
                    change_after_stamp(selected_source)
                    stamp_time = stamp.stat().st_mtime_ns
                    failed = run_make()
                    self.assertNotEqual(failed.returncode, 0)
                    self.assertEqual((output.read_bytes(), header.read_bytes()), previous)
                    self.assertEqual(stamp.stat().st_mtime_ns, stamp_time)

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

    def test_invalid_message_ids_and_names_reject_before_publication(self):
        previous = self.seed()
        included = self.work / "selected messages.txt"
        for encoding in ("utf8", "cp932"):
            for use_include in (False, True):
                for source, diagnostic in (
                    ("#0001\n[Y][X]\n", "missing message ID 0x0000"),
                    ("#0000\n[Y][X]\n#0002\n[X]\n", "missing message ID 0x0001"),
                    ("#0000\n[Y][X]\n#0000\n[X]\n", "duplicate message ID 0x0000"),
                    (
                        "#0000\n[X]\n## SAME\n[Y][X]\n## SAME\n[X]\n",
                        "duplicate message definition SAME",
                    ),
                    ("#0000\n[X]\n## MSG_H\n[Y][X]\n", "reserved message definition MSG_H"),
                    (
                        "#0000\n[X]\n## MSG_COUNT\n[Y][X]\n",
                        "reserved message definition MSG_COUNT",
                    ),
                ):
                    with self.subTest(encoding=encoding, include=use_include, source=source):
                        selected = included if use_include else self.source
                        selected.write_text(source, encoding="utf-8")
                        if use_include:
                            self.source.write_text(f'#include "{included.name}"\n', encoding="utf-8")
                        self.header.write_bytes(previous[0])
                        self.data.write_bytes(previous[1])
                        result = subprocess.run(
                            [sys.executable, str(SCRIPT), *self.arguments(encoding)],
                            cwd=self.work, capture_output=True, text=True, timeout=15,
                        )
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertIn(diagnostic, result.stderr)
                        self.assertIn(str(selected), result.stderr)
                        self.assertEqual(self.pair(), previous)
                        self.assert_no_staging()

    def test_duplicates_between_main_source_and_include_reject(self):
        previous = self.seed()
        included = self.work / "selected messages.txt"
        for encoding in ("utf8", "cp932"):
            for main, child, diagnostic in (
                ("#0000\n[X]\n", "#0000\n[Y][X]\n", "duplicate message ID 0x0000"),
                ("## SAME\n[X]\n", "## SAME\n[Y][X]\n", "duplicate message definition SAME"),
            ):
                with self.subTest(encoding=encoding, diagnostic=diagnostic):
                    self.source.write_text(main + f'#include "{included.name}"\n', encoding="utf-8")
                    included.write_text(child, encoding="utf-8")
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), *self.arguments(encoding)],
                        cwd=self.work, capture_output=True, text=True, timeout=15,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn(diagnostic, result.stderr)
                    self.assertIn(str(included), result.stderr)
                    self.assertEqual(self.pair(), previous)
                    self.assert_no_staging()

    def test_out_of_order_message_ids_decode_at_their_declared_indexes(self):
        included = self.work / "selected messages.txt"
        for encoding in ("utf8", "cp932"):
            with self.subTest(encoding=encoding):
                self.source.write_text("#0000\n[Y][X]\n#0001\n[X]\n", encoding="utf-8")
                self.generate(encoding=encoding)
                expected = self.pair()
                self.assertEqual(self.decoded_messages(), [[1, 0], [0]])
                for use_include in (False, True):
                    if use_include:
                        included.write_text("#0000\n[Y][X]\n", encoding="utf-8")
                        tail = f'#include "{included.name}"\n'
                    else:
                        tail = "#0000\n[Y][X]\n"
                    self.source.write_text("#0001\n[X]\n" + tail, encoding="utf-8")
                    self.generate(encoding=encoding)
                    self.assertEqual(self.decoded_messages(), [[1, 0], [0]])
                    self.assertEqual(self.pair(), expected)

    def test_utf8_odd_length_literals_decode_with_a_real_terminator(self):
        self.definitions.write_text(
            "[X] = 0\n[Y] = 1\n[LOAD] = 16\n[FACE] = 0, 1\n",
            encoding="utf-8",
        )
        self.source.write_text(
            "#0000\nA[X]\n#0001\nAB[X]\n#0002\n日[X]\n#0003\n[X]\n"
            "#0004\n[LOAD][FACE][X]\n#0005\nA[LOAD][FACE][X]\n"
            "#0006\n日[LOAD][FACE][X]\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *self.arguments()],
            cwd=self.work, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.decoded_payloads(), [
            b"A\0", b"AB\0", "日".encode("utf-8") + b"\0", b"\0",
            b"\x10\x00\x01\x00", b"A\x10\x00\x01\x00",
            "日".encode("utf-8") + b"\x10\x00\x01\x00",
        ])

    def test_utf8_control_operands_and_literal_boundaries_round_trip(self):
        self.definitions.write_text(
            "[X] = 0\n[LOAD] = 16\n[LOW_ZERO] = 0, 1\n"
            "[HIGH_ZERO] = 16, 0\n[MOVE] = 0x80, 0x10\n"
            "[COLOR_ZERO] = 0x80, 0, 1\n[COLOR_ARG_ZERO] = 0x80, 1, 0\n"
            "[NL] = 1\n[GLYPH2] = 0xC4, 0x80\n"
            "[GLYPH3] = 0xE4, 0xB8, 0x80\n"
            "[GLYPH4] = 0xF0, 0x90, 0x80, 0x80\n"
            "[LEAD] = 0xC4\n[TAIL] = 0x80\n"
            "[TAB] = 0x81, 0x40\n[LQUOTE] = 0x93\n[RQUOTE] = 0x94\n",
            encoding="utf-8",
        )
        cases = (
            ("一[LOAD][LOW_ZERO][X]", "一".encode("utf-8") + b"\x10\x00\x01\x00"),
            ("A[LOAD][HIGH_ZERO]B[X]", b"A\x10\x10\x00B\x00"),
            ("A[MOVE]B[X]", b"A\x80\x10B\x00"),
            ("一[MOVE]B[X]", "一".encode("utf-8") + b"\x80\x10B\x00"),
            ("[COLOR_ZERO][X]", b"\x80\x00\x01\x00"),
            ("[COLOR_ARG_ZERO]B[X]", b"\x80\x01\x00B\x00"),
            ("[GLYPH2][NL][X]", b"\xC4\x80\x01\x00"),
            ("[GLYPH3][NL][X]", b"\xE4\xB8\x80\x01\x00"),
            ("[GLYPH4][NL][X]", b"\xF0\x90\x80\x80\x01\x00"),
            ("[LEAD][TAIL][NL][X]", b"\xC4\x80\x01\x00"),
            ("[GLYPH4][MOVE][NL][X]", b"\xF0\x90\x80\x80\x80\x10\x01\x00"),
            ("[TAB][MOVE]B[X]", b"\x81\x40\x80\x10B\x00"),
            ("[LQUOTE][MOVE][RQUOTE][X]", b"\x93\x80\x10\x94\x00"),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.source.write_text(f"#0000\n{text}\n", encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), *self.arguments()],
                    cwd=self.work, capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.decoded_payloads(), [expected])

    def test_utf8_missing_control_terminators_and_unencodable_zeros_reject(self):
        previous = self.seed()
        self.definitions.write_text(
            "[X] = 0\n[LOAD] = 16\n[BOTH_ZERO] = 0, 0\n"
            "[EXT] = 0x80\n[COLOR_ARG_ZERO] = 0x80, 1, 0\n",
            encoding="utf-8",
        )
        for text in ("[EXT][X]", "[COLOR_ARG_ZERO]", "[LOAD][BOTH_ZERO]B[X]"):
            with self.subTest(text=text):
                self.source.write_text(f"#0000\n{text}\n", encoding="utf-8")
                self.header.write_bytes(previous[0])
                self.data.write_bytes(previous[1])
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), *self.arguments()],
                    cwd=self.work, capture_output=True, text=True, timeout=15,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn(str(self.source), result.stderr)
                self.assertIn("0x0000", result.stderr)
                self.assertEqual(self.pair(), previous)
                self.assert_no_staging()

    def test_all_empty_messages_have_decodable_huffman_streams(self):
        self.source.write_text("#0000\n[X]\n#0001\n[X]\n", encoding="utf-8")
        for encoding in ("utf8", "cp932"):
            with self.subTest(encoding=encoding):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), *self.arguments(encoding)],
                    cwd=self.work, capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.decoded_messages(), [[0], [0]])

    def test_missing_or_embedded_terminators_reject_before_publication(self):
        previous = self.seed()
        included = self.work / "selected messages.txt"
        for encoding in ("utf8", "cp932"):
            for use_include in (False, True):
                for body in ("[Y]", "日本", "[Y][X][Y]", "[Y][X][X]", "[X]日本"):
                    with self.subTest(encoding=encoding, include=use_include, body=body):
                        selected = included if use_include else self.source
                        selected.write_text(
                            f"#0000\n{body}\n#0001\n[Y][X]\n", encoding="utf-8"
                        )
                        if use_include:
                            self.source.write_text(
                                f'#include "{included.name}"\n', encoding="utf-8"
                            )
                        self.header.write_bytes(previous[0])
                        self.data.write_bytes(previous[1])
                        result = subprocess.run(
                            [sys.executable, str(SCRIPT), *self.arguments(encoding)],
                            cwd=self.work, capture_output=True, text=True, timeout=15,
                        )
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertIn("message must end with exactly one terminator", result.stderr)
                        self.assertIn(str(selected), result.stderr)
                        self.assertIn("0x0000", result.stderr)
                        self.assertEqual(self.pair(), previous)
                        self.assert_no_staging()

    def test_unknown_and_malformed_controls_reject_before_publication(self):
        previous = self.seed()
        included = self.work / "selected messages.txt"
        for encoding in ("utf8", "cp932"):
            for text, diagnostic in (
                ("[DOES_NOT_EXIST][Y][X]", "unknown text control"),
                ("// note\n[DOES_NOT_EXIST][Y][X]", "unknown text control"),
                ("/\n* [DOES_NOT_EXIST] */[Y][X]", "unknown text control"),
                ("/* c */#z\n[DOES_NOT_EXIST][Y][X]", "unknown text control"),
                ("/* c\n#000F */#z\n[DOES_NOT_EXIST][Y][X]", "unknown text control"),
                ("[BROKEN[Y][X]", "malformed text control"),
                ("[][Y][X]", "malformed text control"),
                ("[Y][X][[", "malformed text control"),
                ("[Y][X]]]", "malformed text control"),
            ):
                for use_include in (False, True):
                    with self.subTest(encoding=encoding, text=text, include=use_include):
                        selected = included if use_include else self.source
                        self.header.write_bytes(previous[0])
                        self.data.write_bytes(previous[1])
                        selected.write_text(
                            f"#0000\n{text}\n#0001\n[Y][X]\n", encoding="utf-8"
                        )
                        if use_include:
                            self.source.write_text(
                                f'#include "{included.name}"\n', encoding="utf-8"
                            )
                        result = subprocess.run(
                            [sys.executable, str(SCRIPT), *self.arguments(encoding)],
                            cwd=self.work, capture_output=True, text=True, timeout=15,
                        )
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertIn(diagnostic, result.stderr)
                        self.assertIn(str(selected), result.stderr)
                        self.assertIn("0x0000", result.stderr)
                        self.assertEqual(self.pair(), previous)
                        self.assert_no_staging()

    def test_controls_inside_comments_do_not_change_generated_output(self):
        for encoding in ("utf8", "cp932"):
            with self.subTest(encoding=encoding):
                self.source.write_text("#0000\n[Y][X]\n", encoding="utf-8")
                self.generate(encoding=encoding)
                expected = self.pair()
                self.source.write_text(
                    "#0000\n/* [NOT_A_CONTROL] */[Y][X]\n", encoding="utf-8"
                )
                self.generate(encoding=encoding)
                self.assertEqual(self.pair(), expected)

    def test_comments_preserve_message_boundaries_and_valid_output(self):
        included = self.work / "selected messages.txt"
        comments = (
            "// note\n[Y][X]",
            "[Y][X]\n// [NOT_A_CONTROL]",
            "/* comment\n#000F\n[NOT_A_CONTROL]\n*/\n[Y][X]",
        )
        for encoding in ("utf8", "cp932"):
            for use_include in (False, True):
                for directive in ("#0000", "## CUSTOM"):
                    selected = included if use_include else self.source
                    if use_include:
                        self.source.write_text(f'#include "{included.name}"\n', encoding="utf-8")

                    def run(body):
                        selected.write_text(
                            f"{directive}\n{body}\n#0001\n[Y][X]\n", encoding="utf-8"
                        )
                        return subprocess.run(
                            [sys.executable, str(SCRIPT), *self.arguments(encoding)],
                            cwd=self.work, capture_output=True, text=True, timeout=15,
                        )

                    baseline = run("[Y][X]")
                    self.assertEqual(baseline.returncode, 0, baseline.stderr)
                    expected = self.pair()
                    for body in comments:
                        with self.subTest(
                            encoding=encoding, include=use_include,
                            directive=directive, body=body,
                        ):
                            result = run(body)
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(self.pair(), expected)
                            self.assert_no_staging()

    def test_unknown_directives_reject_direct_and_included_sources(self):
        included = self.work / "selected messages.txt"
        invalid = (
            "#0000\n[Y][X]\n"
            "#UNKNOWN\n[MISSING][X]\n"
            "#0001\n[Y][X]\n"
        )
        for encoding in ("utf8", "cp932"):
            previous = self.seed()
            for use_include in (False, True):
                with self.subTest(encoding=encoding, include=use_include):
                    selected = included if use_include else self.source
                    selected.write_text(invalid, encoding="utf-8")
                    if use_include:
                        self.source.write_text(
                            f'#include "{included.name}"\n', encoding="utf-8"
                        )
                    self.header.write_bytes(previous[0])
                    self.data.write_bytes(previous[1])
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), *self.arguments(encoding)],
                        cwd=self.work, capture_output=True, text=True, timeout=15,
                    )
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn("unknown text directive", result.stderr)
                    self.assertIn(str(selected), result.stderr)
                    self.assertEqual(self.pair(), previous)
                    self.assert_no_staging()

    def test_include_path_can_contain_comment_like_characters(self):
        included = self.work / "dir" / "*draft*" / "message.txt"
        included.parent.mkdir(parents=True)
        included.write_text("#0000\n[Y][X]\n", encoding="utf-8")
        for encoding in ("utf8", "cp932"):
            for indentation in ("", "  "):
                with self.subTest(encoding=encoding, indentation=indentation):
                    self.source.write_text("#0000\n[Y][X]\n", encoding="utf-8")
                    baseline = subprocess.run(
                        [sys.executable, str(SCRIPT), *self.arguments(encoding)],
                        cwd=self.work, capture_output=True, text=True, timeout=15,
                    )
                    self.assertEqual(baseline.returncode, 0, baseline.stderr)
                    expected = self.pair()
                    self.source.write_text(
                        f'{indentation}#include "dir/*draft*/message.txt"\n', encoding="utf-8"
                    )
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), *self.arguments(encoding)],
                        cwd=self.work, capture_output=True, text=True, timeout=15,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.pair(), expected)
                    self.assert_no_staging()

    def test_comments_do_not_join_directive_fragments(self):
        for encoding in ("utf8", "cp932"):
            for plain, commented, should_reject in (
                ("#0000", "#0000/*note*/2", False),
                ("## CUSTOM", "## CUSTOM/*note*/_SUFFIX", False),
                ("#0000", '#incl/*note*/ude "missing.txt"\n#0000', True),
            ):
                with self.subTest(encoding=encoding, directive=commented):
                    self.source.write_text(f"{plain}\n[Y][X]\n", encoding="utf-8")
                    self.generate(encoding=encoding)
                    expected = self.pair()
                    self.source.write_text(f"{commented}\n[Y][X]\n", encoding="utf-8")
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), *self.arguments(encoding)],
                        cwd=self.work, capture_output=True, text=True, timeout=15,
                    )
                    if should_reject:
                        self.assertNotEqual(result.returncode, 0, result.stdout)
                        self.assertIn("unknown text directive", result.stderr)
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(self.pair(), expected)
                    self.assert_no_staging()

    def test_indented_include_like_message_text_still_removes_comments(self):
        for encoding in ("utf8", "cp932"):
            with self.subTest(encoding=encoding):
                self.source.write_text('#0000\n #include ""[Y][X]\n', encoding="utf-8")
                self.generate(encoding=encoding)
                expected = self.pair()
                self.source.write_text(
                    '#0000\n #include "/*[MISSING]*/"[Y][X]\n', encoding="utf-8"
                )
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), *self.arguments(encoding)],
                    cwd=self.work, capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.pair(), expected)
                self.assert_no_staging()

    def test_cp932_cli_preserves_literal_characters_and_controls(self):
        self.source.write_text("#0000\n日本[Y]語[X]\n", encoding="utf-8")
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *self.arguments("cp932")],
            cwd=self.work, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.decoded_messages(), [[0xFA93, 0x7B96, 1, 0xEA8C, 0]])

    def test_cp932_controls_require_a_distinct_terminal_symbol(self):
        previous = self.seed()
        self.definitions.write_text(
            "[X] = 0\n[LOAD] = 16\n[SHORT_FACE] = 1\n"
            "[FACE_ZERO_LOW] = 0x0100\n[PACKED_ZERO] = 0x0100\n"
            "[SHORT_COLOR] = 0x80, 1\n[COLOR_ZERO] = 0x80, 0x0100\n"
            "[COLOR_ARG_ZERO] = 0x80, 1, 0x4200\n[GLYPH] = 0x8094\n"
            "[MOVE] = 0x80, 0x10\n",
            encoding="utf-8",
        )
        valid = (
            ("[LOAD][FACE_ZERO_LOW][X]", b"\x10\x00\x01\x00"),
            ("[COLOR_ZERO][X]", b"\x80\x00\x01\x00"),
            ("[COLOR_ARG_ZERO][X]", b"\x80\x01\x00B\x00"),
            ("[GLYPH][X]", b"\x94\x80\x00"),
            ("[GLYPH][MOVE][X]", b"\x94\x80\x80\x10\x00"),
        )
        for text, expected in valid:
            with self.subTest(valid=text):
                self.source.write_text(f"#0000\n{text}\n", encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), *self.arguments("cp932")],
                    cwd=self.work, capture_output=True, text=True, timeout=15,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.decoded_payloads(), [expected])

        for text in (
            "[SHORT_COLOR][X]", "[LOAD][SHORT_FACE][X]", "[PACKED_ZERO][X]",
            "[GLYPH][SHORT_COLOR][X]", "[GLYPH][LOAD][SHORT_FACE][X]",
        ):
            with self.subTest(invalid=text):
                self.source.write_text(f"#0000\n{text}\n", encoding="utf-8")
                self.header.write_bytes(previous[0])
                self.data.write_bytes(previous[1])
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), *self.arguments("cp932")],
                    cwd=self.work, capture_output=True, text=True, timeout=15,
                )
                self.assertNotEqual(result.returncode, 0, result.stdout)
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
