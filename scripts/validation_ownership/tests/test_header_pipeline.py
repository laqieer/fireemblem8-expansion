"""Actual original ARM/sed header steps, not a substitute full-root census."""

import copy
from contextlib import ExitStack
import errno
import hashlib
import json
import math
from dataclasses import replace
from pathlib import Path
import shlex
import subprocess
import threading
import tracemalloc
import unittest
from unittest.mock import patch

from scripts.validation_ownership import arm_headers, header_runtime, make_probe, syscall_guard
from scripts.validation_ownership.authority import ENVIRONMENT, encoded as canonical
from scripts.validation_ownership.budget import Limits, MakeProbeError, ProbeBudget
from scripts.validation_ownership.graph_commands import MakeCommands, ROOT_RUNTIME_FILES, _literal_header_words
from scripts.validation_ownership.make_probe import Command, RuntimeTool
from scripts.validation_ownership.producer_channel import ChannelError
from scripts.bash_parser import tokenize_bash_command
from scripts.validation_ownership.tests import test_foundation as foundation


class ArmHeaderPipelineTests(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.FoundationTests()
        self.fixture.setUp()
        self.target = "build/native/src/query.headers.d"
        modern = (foundation.ROOT / "modern.mk").read_text()
        begin = modern.index("$(MODERN_ALL_C_HEADER_DEPS): $(MODERN_OUTPUT_DIR)/%.headers.d: %.c\n")
        end = modern.index("\nexpansion-modern-clean:", begin)
        self.original_rule_and_include = modern[begin:end]
        self.fixture.add("src/query.c", (
            "#include <stdint.h>\n"
            "#ifdef __arm__\n#include \"arm-only.h\"\n"
            "#else\n#include \"host-only.h\"\n#endif\n"
            "#include \"missing.h\"\n#include \"also_missing.h\"\n"
        ))
        self.fixture.add("include/arm-only.h", "#define TARGET_IS_ARM 1\n")
        self.fixture.add("include/host-only.h", "#define TARGET_IS_ARM 0\n")
        self.fixture.add("Makefile", (
            ".DEFAULT_GOAL := expansion-modern-all\n"
            "MODERN_OUTPUT_DIR := build/native\n"
            "MODERN_CC := arm-none-eabi-gcc\n"
            "MODERN_CFLAGS := -mcpu=arm7tdmi -mthumb -mthumb-interwork -std=gnu11 "
            "-ffreestanding -isystem /usr/include/newlib -Iinclude -I.\n"
            f"MODERN_ALL_C_HEADER_DEPS := {self.target}\n"
            "MODERN_ALL_SOURCE_GOALS := expansion-modern-all\n"
            "MODERN_GENERATED_HEADER_BASENAME_RE := missing\\.h|also_missing\\.h\n"
            + self.original_rule_and_include + "\nexpansion-modern-all: ;\n"
        ))
        dynamics = json.loads((foundation.ROOT / ".github/validation-ownership-make-dynamics.json").read_text())
        self.contracts = {item["expression"]: item for item in dynamics["contracts"]}

    def tearDown(self):
        self.fixture.tearDown()

    def session(self):
        return self.fixture.session(runtime_files=ROOT_RUNTIME_FILES)

    def assert_clean(self, session):
        self.fixture.assert_clean(session)
        self.assertFalse(session._header_profiles)
        self.assertFalse(session._header_launches)
        self.assertFalse(session._issued_header_launches)
        self.assertFalse(session._native_returns)

    def ordinary(self):
        completed = subprocess.run(
            ["/usr/bin/make", "-rR", "--no-print-directory", "expansion-modern-all"],
            cwd=self.fixture.root, env=ENVIRONMENT, capture_output=True, timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        path = self.fixture.root / self.target
        result = path.read_bytes()
        path.unlink()
        for parent in path.parents:
            if parent == self.fixture.root:
                break
            parent.rmdir()
        return result

    def test_actual_arm_sdk_and_sed_keep_five_original_dispatches(self):
        expected = self.ordinary()
        self.assertIn(b"include/arm-only.h", expected)
        self.assertNotIn(b"include/host-only.h", expected)
        self.assertNotIn(b"missing.h", expected)
        with self.session() as session:
            result = session.make("expansion-modern-all", commands=MakeCommands(session, self.contracts))
            generated, = result.generated
            self.assertEqual((generated.path, generated.data, generated.mode), (self.target, expected, 0o644))
            jobs = result.semantics["native_dispatches"]
            self.assertEqual([item["job"]["command_line"] for item in jobs], [1, 2, 3, 4, 5])
            self.assertTrue(all(item["rebuilding_makefiles"] for item in jobs))
            commands = result.semantics["dynamic_commands"]
            scan, = [item["command"] for item in commands if item["command"].get("dependency_only")]
            self.assertEqual(Path(scan["executed"][0]).name, "arm-none-eabi-gcc")
            self.assertEqual(Path(scan["executed"][1]).name, "cc1")
            self.assertTrue(any(item[0].endswith("/stdint.h") for item in scan["runtime_inputs"]))
            self.assertTrue(any(
                item["command"].get("runtime_tool", {}).get("path") == "/usr/bin/sed"
                for item in commands
            ))
            filtered, = [item["command"] for item in commands if item["command"].get("runtime_tool", {}).get("path") == "/usr/bin/sed"]
            self.assertTrue(any(
                item["operation"] == "statfs" and item["path"] == "/sys/fs/selinux"
                for item in filtered["runtime_probes"]
            ))
            self.assertTrue(all(
                item["eof"] in {True, False} for item in filtered["runtime_probes"] if item["operation"] == "stream"
            ))
        self.assert_clean(session)

    def test_host_control_differs_and_ordered_macros_includes_use_actual_arm_driver(self):
        host = subprocess.run(
            ["/usr/bin/cc", "-std=gnu11", "-ffreestanding", "-Iinclude", "-I.", "-MM", "-MG",
             "-MT", "build/native/src/query.o", "src/query.c"],
            cwd=self.fixture.root, env=ENVIRONMENT, capture_output=True, timeout=10, check=True,
        ).stdout
        self.assertIn(b"include/host-only.h", host)
        self.assertNotIn(b"include/arm-only.h", host)
        original = (self.fixture.root / "src/query.c").read_text()
        self.fixture.add("src/query.c", "#if CHOICE == 2\n#include <choice.h>\n#endif\n" + original)
        self.fixture.add("alpha/choice.h", "#define CHOSEN 1\n")
        self.fixture.add("beta/choice.h", "#define CHOSEN 2\n")
        source = (self.fixture.root / "Makefile").read_text().replace(
            "-Iinclude -I.", "-Ialpha -Ibeta -Iinclude -I. -DCHOICE=1 -UCHOICE -D CHOICE=2",
        )
        self.fixture.add("Makefile", source)
        expected = self.ordinary()
        self.assertIn(b"alpha/choice.h", expected)
        self.assertNotIn(b"beta/choice.h", expected)
        with self.session() as session:
            result = session.make("expansion-modern-all", commands=MakeCommands(session, self.contracts))
            self.assertEqual(result.generated[0].data, expected)
        self.assert_clean(session)

    def test_unissued_runtime_fields_and_missing_copied_replayed_launches_reject(self):
        for executable, dependency in (("/usr/bin/arm-none-eabi-gcc", True), ("/usr/bin/sed", False)):
            with self.subTest(executable=executable), self.session() as session:
                tool = session.runtime_tool(executable)
                before = session.budget.runs
                with self.assertRaises(MakeProbeError):
                    session.command(Command((tool.path,), runtime_tool=tool, dependency_only=dependency))
                self.assertEqual(session.budget.runs, before)
            self.assert_clean(session)
        with self.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "unadmitted syscall 137"):
                session.command(Command((
                    "/usr/bin/python3", "-c",
                    "import ctypes;lib=ctypes.CDLL(None);buf=ctypes.create_string_buffer(120);"
                    "lib.syscall(137,ctypes.c_char_p(b'/sys/fs/selinux'),ctypes.byref(buf))",
                )))
        self.assert_clean(session)
        for defect in ("missing", "copied", "replay"):
            with self.subTest(defect=defect), self.session() as session:
                run, reached = session._sandbox_run, []
                def changed(root, **options):
                    if "header_runtime" not in options or reached:
                        return run(root, **options)
                    reached.append(defect)
                    before = session.budget.runs
                    if defect == "missing":
                        options.pop("header_runtime")
                    elif defect == "copied":
                        options["header_runtime"] = type(options["header_runtime"])()
                    else:
                        result = run(root, **options)
                        before = session.budget.runs
                        with self.assertRaises(MakeProbeError):
                            run(root, **options)
                        self.assertEqual(session.budget.runs, before)
                        return result
                    with self.assertRaises(MakeProbeError):
                        run(root, **options)
                    self.assertEqual(session.budget.runs, before)
                    raise MakeProbeError("expected unissued runtime refusal")
                with patch.object(session, "_sandbox_run", changed):
                    if defect == "replay":
                        result = session.make("expansion-modern-all", commands=MakeCommands(session, self.contracts))
                        self.assertTrue(result.generated)
                    else:
                        with self.assertRaises(MakeProbeError):
                            session.make("expansion-modern-all", commands=MakeCommands(session, self.contracts))
                self.assertEqual(reached, [defect])
            self.assert_clean(session)

    def test_native_sdk_reads_reject_changed_bytes_and_unadmitted_sources(self):
        with self.session() as session:
            capture = session._capture_header_sdk
            changed = []
            def corrupt(*args):
                backing, profile = capture(*args)
                index = next(i for i, (root, present) in enumerate(profile["roots"]) if root == arm_headers.NEWLIB and present)
                target = backing / str(index) / "stdint.h"
                data = target.read_bytes()
                target.write_bytes(b"!" + data[1:])
                changed.append(True)
                return backing, profile
            with patch.object(session, "_capture_header_sdk", corrupt):
                with self.assertRaises(MakeProbeError):
                    session.make("expansion-modern-all", commands=MakeCommands(session, self.contracts))
            self.assertEqual(changed, [True])
        self.assert_clean(session)
        plugin = subprocess.run(
            ["/usr/bin/arm-none-eabi-gcc", "-print-file-name=liblto_plugin.so"],
            env=ENVIRONMENT, capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()
        for source in (
            '#include "' + plugin + '"\n',
            '#include "unadmitted.data"\n',
        ):
            self.fixture.add("src/query.c", source)
            self.fixture.add("src/unadmitted.data", "#define NOT_HEADER_AUTHORITY 1\n")
            with self.subTest(source=source), self.session() as session:
                with self.assertRaises(MakeProbeError):
                    session.make("expansion-modern-all", commands=MakeCommands(session, self.contracts))
            self.assert_clean(session)

    def test_actual_kernel_receipts_reject_omission_and_forgery(self):
        for defect in ("missing", "count", "result", "extent"):
            with self.subTest(defect=defect), self.session() as session:
                read, changed = session.budget.read_bytes, []
                def corrupt(path, category):
                    data = read(path, category)
                    if category != "control" or not Path(path).name.startswith("report-"):
                        return data
                    record = json.loads(data)
                    if not any(item.startswith(header_runtime.KERNEL_END) for item in record["accessed"]):
                        return data
                    changed.append(defect)
                    if defect == "missing":
                        record["accessed"] = [item for item in record["accessed"] if not item.startswith("header-kernel")]
                    elif defect == "count":
                        record["accessed"] = [
                            header_runtime.KERNEL_END + "0" if item.startswith(header_runtime.KERNEL_END) else item
                            for item in record["accessed"]
                        ]
                    else:
                        index = next(i for i, item in enumerate(record["accessed"]) if item.startswith(header_runtime.KERNEL_PREFIX))
                        row = json.loads(record["accessed"][index][len(header_runtime.KERNEL_PREFIX):])
                        if defect == "result":
                            row["result"] = True
                        else:
                            row["data"] = "00"
                        record["accessed"][index] = header_runtime.KERNEL_PREFIX + json.dumps(row)
                    return json.dumps(record).encode()
                with patch.object(session.budget, "read_bytes", corrupt):
                    with self.assertRaises(MakeProbeError):
                        session.make("expansion-modern-all", commands=MakeCommands(session, self.contracts))
                self.assertEqual(changed, [defect])
            self.assert_clean(session)

    def test_unsupported_options_expressions_and_shell_expansions_fail_closed(self):
        tool = RuntimeTool("/usr/bin/arm-none-eabi-gcc", "/usr/bin/arm-none-eabi-gcc", 0o755, "0" * 64)
        good = Command(
            (tool.path, *arm_headers.ARCH_FLAGS, "-MM", "-MG", "-MT", "build/x.o", "src/query.c"),
            sources=("src/query.c",), outputs=("build/x.headers.d.tmp",), dependency_only=True, runtime_tool=tool,
        )
        arm_headers.options(good, good.sources, good.outputs)
        for extra in (
            ("-undef",), ("-nostdinc",), ("-fplugin=plugin.so",), ("-specs=specs",),
            ("@response",), ("-o", "/work/other"), ("-MF", "/work/other"), ("-x", "c++"),
            ("--sysroot=/usr",), ("-I", "=include"), ("-isystem", "/etc"), ("-B/tmp/",),
            ("-include", "hidden.h"), ("-imacros", "hidden.h"),
        ):
            with self.subTest(extra=extra), self.assertRaises(MakeProbeError):
                arm_headers.options(replace(good, argv=(*good.argv, *extra)), good.sources, good.outputs)
        expression = r"s/(^|[[:space:]])(missing\.h)([[:space:]]|$)/\1\3/g"
        self.assertEqual(arm_headers.filter_expression(expression), expression)
        for bad in (expression + ";e id", expression[:-1] + "e", expression.replace("missing", ".*"), "s/x/y/"):
            with self.subTest(expression=bad), self.assertRaises(MakeProbeError):
                arm_headers.filter_expression(bad)
        for bad in ('-DVALUE=$VALUE', '-DVALUE="$(printf changed)"', '-DVALUE=`id`', 'src/*.c', '-DVALUE={a,b}'):
            with self.subTest(shell=bad), self.assertRaises(MakeProbeError):
                _literal_header_words(tokenize_bash_command(bad))
        self.assertEqual(_literal_header_words(tokenize_bash_command("""-DVALUE='$VALUE' "src/query.c" """)),
                         ["-DVALUE=$VALUE", "src/query.c"])

    def test_native_modified_failure_branch_and_search_environment_reject(self):
        base = (self.fixture.root / "Makefile").read_text()
        for defect in ("failure-source", "search-environment", "filter-script"):
            if defect == "failure-source":
                source = base.replace("for $<", "for src/other.c")
            elif defect == "filter-script":
                source = base.replace(r"\1\3/g", r"\1\3/ge")
            else:
                source = "export CPATH := include\n" + base
            self.fixture.add("Makefile", source)
            with self.subTest(defect=defect), self.session() as session:
                with self.assertRaises(MakeProbeError):
                    session.make("expansion-modern-all", commands=MakeCommands(session, self.contracts))
            self.assert_clean(session)

    def test_supported_binutils_and_abi_flags_bind_real_search_profile(self):
        source = (self.fixture.root / "Makefile").read_text().replace(
            "-std=gnu11", "-B/usr/bin/ -std=gnu11 -mabi=apcs-gnu",
        )
        self.fixture.add("Makefile", source)
        expected = self.ordinary()
        with self.session() as session:
            result = session.make("expansion-modern-all", commands=MakeCommands(session, self.contracts))
            self.assertEqual(result.generated[0].data, expected)
        self.assert_clean(session)

    def test_sdk_schema_and_kernel_completion_keep_closed_shapes(self):
        captured = []
        with self.session() as session:
            create = session._capture_header_sdk
            def record(*args):
                result = create(*args)
                captured.append((copy.deepcopy(result[1]), args[0]))
                return result
            with patch.object(session, "_capture_header_sdk", record):
                result = session.make("expansion-modern-all", commands=MakeCommands(session, self.contracts))
            profile, executables = captured[0]
            for key, value in (
                ("version", True), ("roots", [["/etc", True]]), ("entries", []),
                ("excluded", ["/usr/include/newlib"]), ("files", []),
                ("aliases", [["/usr/lib/arm-none-eabi/include", "/etc", "/etc"]]),
                ("extra", True),
            ):
                with self.subTest(sdk_field=key), self.assertRaises(ChannelError):
                    arm_headers.validate_search(
                        {**profile, key: value}, executables,
                        count_limit=session.budget.limits.entries, file_limit=session.budget.limits.file_bytes,
                    )
            filtered, = [item["command"] for item in result.semantics["dynamic_commands"]
                         if item["command"].get("runtime_tool", {}).get("path") == "/usr/bin/sed"]
            records = filtered["runtime_probes"]
            kernel = {
                "version": 1,
                "statfs": [
                    [path, next(row["result"] == 0 for row in records if row["operation"] == "statfs" and row["path"] == path)]
                    for path in header_runtime.STATFS_PATHS
                ],
                "reads": list(header_runtime.READ_PATHS), "absent": list(header_runtime.ABSENT_PATHS),
            }
            encoded = [header_runtime.KERNEL_PREFIX + json.dumps(row) for row in records]
            encoded.append(header_runtime.KERNEL_END + str(len(records)))
            self.assertEqual(header_runtime.records(encoded, kernel, count_limit=100, file_limit=65536), tuple(records))
            for wrong in ([], encoded[:-1], encoded[1:], [*encoded, encoded[0]]):
                with self.assertRaises(ChannelError):
                    header_runtime.records(wrong, kernel, count_limit=100, file_limit=65536)
            with self.assertRaises(ChannelError):
                header_runtime.records(encoded, None, count_limit=100, file_limit=65536)
        self.assert_clean(session)

    def test_actual_default_text_c_and_tracked_header_compose_with_real_header_steps(self):
        self.target = "build/native/src/msg_data.headers.d"
        for name in ("scripts/texttools/textprocess.py", "scripts/texttools/huffman.py",
                     "texts/texts.txt", "texts/textdefs.txt"):
            self.fixture.add(name, (foundation.ROOT / name).read_bytes())
        for path in (foundation.ROOT / "include").rglob("*.h"):
            self.fixture.add(path.relative_to(foundation.ROOT).as_posix(), path.read_bytes())
        header = (self.fixture.root / "include/constants/msg.h").read_bytes()
        source = (self.fixture.root / "Makefile").read_text().replace(
            "build/native/src/query.headers.d", self.target,
        ).replace("-ffreestanding", "-ffreestanding -DMODERN=1 -DNONMATCHING=1 -DBUGFIX=1")
        source += (
            "src/msg_data.c:\n"
            "\tpython3 scripts/texttools/textprocess.py texts/texts.txt texts/textdefs.txt "
            "src/msg_data.c include/constants/msg.h utf8\n"
        )
        self.fixture.add("Makefile", source)
        expected_header_dependency = self.ordinary()
        message_c = self.fixture.root / "src/msg_data.c"
        expected_c = message_c.read_bytes()
        self.assertGreater(len(expected_c), 3_000_000)
        message_c.unlink()
        self.assertEqual((self.fixture.root / "include/constants/msg.h").read_bytes(), header)
        with self.session() as session:
            self.assertFalse((session.tree / "src/msg_data.c").exists())
            original_header = (session.tree / "include/constants/msg.h").stat()
            result = session.make("expansion-modern-all", commands=MakeCommands(session, self.contracts))
            generated = {item.path: item.data for item in result.generated}
            self.assertEqual(generated, {"src/msg_data.c": expected_c, self.target: expected_header_dependency})
            self.assertEqual((session.tree / "include/constants/msg.h").stat(), original_header)
            self.assertEqual((session.tree / "include/constants/msg.h").read_bytes(), header)
            dispatches = result.semantics["native_dispatches"]
            self.assertEqual([row["job"]["command_line"] for row in dispatches], [1, 1, 2, 3, 4, 5])
            self.assertTrue(all(row["rebuilding_makefiles"] for row in dispatches))
        self.assert_clean(session)

    def test_exact_native_owner_runtime_staging_and_human_case_are_wired(self):
        from scripts import check_docs
        from scripts.validation_ownership import ci_verifier, reporter
        from scripts.validation_ownership.authority import AuthorityLoader, git_tree_entries
        from scripts.validation_ownership.budget import ProbeBudget

        root = foundation.ROOT
        graph = reporter.load_json(root / reporter.GRAPH_PATH)
        budget = ProbeBudget()
        try:
            entries = git_tree_entries(root, budget=budget)
            sources = reporter._path_admission_sources(AuthorityLoader(root, entries, "HEAD", budget=budget), set())
            for path, expected in (
                ("scripts/validation_ownership/arm_headers.py", "paths.ownership"),
                ("scripts/validation_ownership/header_runtime.py", "paths.ownership"),
                ("scripts/validation_ownership/tests/test_header_pipeline.py", "paths.ownership-native"),
            ):
                rule, = [rule for rule in graph["path_rules"] if reporter._path_rule_matches(rule, path, set())]
                self.assertEqual(rule["id"], expected)
                self.assertEqual(reporter._path_admission(path, rule, sources), "exact-ownership-rule")
                if "/tests/" not in path:
                    self.assertIn(path, ci_verifier.TRUSTED_RUNTIME_PATHS)
                else:
                    changed = {**rule, "include": [
                        item for item in rule["include"] if item != {"kind": "exact", "path": path}
                    ]}
                    with self.assertRaises(reporter.OwnershipError):
                        reporter._path_admission(path, changed, sources)
        finally:
            budget.close()
        registry, errors = check_docs.parse_test_case_registry(str(root))
        self.assertEqual(errors, [])
        case, = [item for item in registry["cases"] if item["id"] == "TC-WORKFLOW-GATE-OWNERSHIP-001"]
        feature, = [item for item in registry["features"] if item["id"] == case["feature_id"]]
        selected = {
            **registry, "features": [{**feature, "required_cases": [case["id"]]}], "cases": [case],
            "coverage": {**registry["coverage"], "expected_feature_ids": [feature["id"]]},
        }
        self.assertIn(
            (("python3", "-m", "unittest", __name__, "-v"), "scripts/validation_ownership/tests/test_header_pipeline.py"),
            {(tuple(shlex.split(item["command"])), item["evidence"]) for item in case["automation"]},
        )
        with patch.object(check_docs, "parse_test_case_registry", return_value=(selected, [])):
            self.assertEqual(check_docs.check_test_case_registry(str(root)), [])


class HeaderReceiptInertTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "version": 1,
            "statfs": [[path, False] for path in header_runtime.STATFS_PATHS],
            "reads": list(header_runtime.READ_PATHS),
            "absent": list(header_runtime.ABSENT_PATHS),
        }
        self.key = bytes(range(32))
        self.scope = "session/command"
        self.binding = "1" * 64
        self.verifier = header_runtime._FilterLaunch(self.scope, self.binding, self.key)

    @staticmethod
    def row(sequence, operation="absent", path="/etc/selinux/config", **changes):
        value = {
            "sequence": sequence, "operation": operation, "path": path,
            "result": -errno.ENOENT, "data": None, "bytes": None,
            "sha256": None, "eof": None,
        }
        value.update(changes)
        return value

    def signed(self, rows):
        digest = hashlib.sha256(canonical(rows)).hexdigest()
        completion = header_runtime.completion(
            self.scope, self.binding, self.key, len(rows), digest,
        )
        return (
            [header_runtime.KERNEL_PREFIX + canonical(row).decode("ascii") for row in rows],
            completion,
        )

    def authenticate(self, rows, completion=None, *, order=None, reserve=lambda size: None):
        values, signed = self.signed(rows)
        if completion is not None:
            signed = completion
        values.append(header_runtime.KERNEL_END + canonical(signed).decode("ascii"))
        if order is not None:
            values = [values[index] for index in order]
        return header_runtime.authenticate_records(
            values, self.profile, self.verifier,
            count_limit=32, file_limit=65536, reserve=reserve,
        )

    def complete_rows(self):
        return [
            self.row(1, "statfs", "/sys/fs/selinux"),
            self.row(
                2, "stream", "/proc/filesystems", result=0, bytes=7,
                sha256="2" * 64, eof=True,
            ),
            self.row(
                3, "stream", "/proc/filesystems", result=0, bytes=7,
                sha256="2" * 64, eof=True,
            ),
            self.row(4),
        ]

    def receipt_policy(self, *, limit=1_000_000, count=3):
        policy = syscall_guard.Policy.__new__(syscall_guard.Policy)
        policy.config = {"observation_count": count, "observation_limit": limit, "file_limit": 64}
        policy.filter_kernel = self.profile
        policy.header_runtime = self.verifier
        policy.observation_attempts = {
            name: set() for name in ("consumed", "code_consumed", "accessed")
        }
        policy.observation_bytes = 0
        policy.accessed = set()
        policy.kernel_streams = {}
        policy.kernel_sequence = 0
        policy.kernel_has_first_statfs = False
        policy.kernel_manifest = policy.kernel_row_limit = policy.kernel_terminal_limit = None
        policy.kernel_terminal_reservation = None
        return policy

    def test_fingerprint_admits_each_allocation_interval_without_width_pending_actions(self):
        def measure(value):
            intervals = []
            previous = None
            def reserve(size):
                nonlocal previous
                current, peak = tracemalloc.get_traced_memory()
                if previous is not None:
                    grant, start = previous
                    intervals.append((grant, max(0, peak - start)))
                tracemalloc.reset_peak()
                previous = size, tracemalloc.get_traced_memory()[0]
            tracemalloc.start()
            try:
                make_probe._native_value_fingerprint(
                    value, reserve=reserve, remaining=lambda: None, node_limit=40001,
                )
                reserve(0)
            finally:
                tracemalloc.stop()
            return intervals

        # Inputs exist before tracing. Observe growth *between* actual admissions.
        for value in (
            [None] * 10000,
            {f"{index * 7919 % 10000:05d}": None for index in range(10000)},
        ):
            intervals = measure(value)
            with self.subTest(kind=type(value).__name__):
                for granted, allocated in intervals:
                    if granted >= 4096:
                        self.assertLessEqual(allocated, granted, (granted, allocated))
        narrow = measure([None] * 32)
        wide = measure([None] * 10000)
        self.assertLessEqual(max(size for _, size in wide), 2 * max(size for _, size in narrow))

    def test_calculator_admission_precedes_encoding_and_scope_escaping(self):
        for scope in (self.scope, "\U0001f600" * 4096):
            verifier = header_runtime._FilterLaunch(scope, self.binding, self.key)
            policy = self.receipt_policy(limit=0)
            policy.header_runtime = verifier
            with self.subTest(scope_length=len(scope)), patch.object(
                header_runtime, "encoded", wraps=header_runtime.encoded,
            ) as encode:
                with self.assertRaises(syscall_guard.Violation):
                    policy.reserve_header_completion()
                self.assertEqual(encode.call_count, 0)
            def refuse(size):
                raise MakeProbeError("no calculation workspace")
            with self.subTest(parent_scope_length=len(scope)), patch.object(
                header_runtime, "encoded", wraps=header_runtime.encoded,
            ) as encode:
                with self.assertRaises(MakeProbeError):
                    header_runtime.authenticate_records(
                        [], self.profile, verifier, count_limit=3, file_limit=64, reserve=refuse,
                    )
                self.assertEqual(encode.call_count, 0)

    def test_fingerprint_refuses_key_reference_and_sort_growth_before_admission(self):
        width = 10000
        value = {f"{index * 7919 % width:05d}": None for index in range(width)}
        reference_bytes = width * make_probe._NATIVE_POINTER_BYTES
        for stage in ("references", "sort"):
            baseline = None
            denied = False
            def reserve(size):
                nonlocal baseline, denied
                current, _ = tracemalloc.get_traced_memory()
                if baseline is None:
                    baseline = current
                if (
                    stage == "references" and size > reference_bytes // 2
                    or stage == "sort" and current - baseline >= reference_bytes
                ):
                    denied = True
                    raise MakeProbeError("inert key workspace refusal")
            tracemalloc.start()
            try:
                with self.assertRaises(MakeProbeError):
                    make_probe._native_value_fingerprint(
                        value, reserve=reserve, remaining=lambda: None, node_limit=40001,
                    )
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            with self.subTest(stage=stage):
                self.assertTrue(denied)
                # Only the already admitted key-reference array may be live.
                # An unadmitted sort of this disordered input needs ~40 KiB.
                self.assertLess(
                    peak - baseline, (reference_bytes if stage == "sort" else 0) + 8192,
                )

    def test_authentication_rejects_nonexact_nonascii_wire_before_copy_or_decode(self):
        class Wire(str):
            def __getitem__(self, index):
                raise AssertionError("unadmitted slice")
            def encode(self, *args, **kwargs):
                raise AssertionError("unadmitted encode")
        values, terminal = self.signed(self.complete_rows())
        row_limit = header_runtime.row_wire_limit(self.profile, count_limit=32, file_limit=65536)
        terminal_limit = header_runtime.terminal_wire_limit(self.scope, self.binding, count_limit=32)
        for value in (
            Wire(values[0]), Wire(header_runtime.KERNEL_END + canonical(terminal).decode("ascii")),
            values[0][:-1] + "\u00e9",
            header_runtime.KERNEL_END + "\u00e9",
            header_runtime.KERNEL_PREFIX + " " * row_limit,
            header_runtime.KERNEL_END + " " * terminal_limit,
        ):
            with self.subTest(kind=type(value).__name__), patch.object(
                header_runtime, "_reserve_decode", side_effect=AssertionError("decoder admitted invalid wire"),
            ), patch.object(header_runtime, "parse_json", side_effect=AssertionError("unadmitted parse")):
                with self.assertRaises(ChannelError):
                    header_runtime.authenticate_records(
                        [value], self.profile, self.verifier, count_limit=32, file_limit=65536,
                    )

    def test_payload_slice_is_not_allocated_before_decode_admission(self):
        scope = "x" * 32768
        verifier = header_runtime._FilterLaunch(scope, self.binding, self.key)
        terminal = header_runtime.completion(scope, self.binding, self.key, 1, "0" * 64)
        wire = header_runtime.KERNEL_END + canonical(terminal).decode("ascii")
        last = 0
        def reserve(size):
            nonlocal last
            last = tracemalloc.get_traced_memory()[0]
        growth = []
        def refuse(*args, **kwargs):
            growth.append(tracemalloc.get_traced_memory()[0] - last)
            raise MakeProbeError("decode capacity denied")
        with patch.object(header_runtime, "_reserve_decode", new=refuse):
            tracemalloc.start()
            try:
                with self.assertRaises(MakeProbeError):
                    header_runtime.authenticate_records(
                        [wire], self.profile, verifier, count_limit=3, file_limit=64, reserve=reserve,
                    )
            finally:
                tracemalloc.stop()
        self.assertEqual(len(growth), 1)
        self.assertLess(growth[0], len(scope), growth)

    def test_calculation_and_decoder_representations_fit_preadmitted_workspace(self):
        def funded(action):
            admitted = 0
            baseline = None
            unfunded = 0
            def reserve(size):
                nonlocal admitted, baseline, unfunded
                current, peak = tracemalloc.get_traced_memory()
                if baseline is None:
                    baseline = current
                else:
                    unfunded = max(unfunded, peak - baseline - admitted)
                admitted += size
            tracemalloc.start()
            try:
                action(reserve)
                reserve(0)
            finally:
                tracemalloc.stop()
            self.assertGreater(admitted, 0)
            self.assertEqual(unfunded, 0)

        funded(lambda reserve: header_runtime.row_wire_limit(
            self.profile, count_limit=32, file_limit=(1 << 64) - 1, reserve=reserve,
        ))
        scope = "\"\\\n\ud800\U0001f600" * 4096
        funded(lambda reserve: header_runtime.terminal_wire_limit(
            scope, self.binding, count_limit=32, reserve=reserve,
        ))
        values, terminal = self.signed(self.complete_rows())
        terminal["scope"] = scope
        wire = header_runtime.KERNEL_END + canonical(terminal).decode("ascii")
        nested = header_runtime.KERNEL_PREFIX + "[" * 80 + "0" + "]" * 80
        self.assertLessEqual(len(nested), header_runtime.row_wire_limit(
            self.profile, count_limit=32, file_limit=65536,
        ))
        for value, prefix in (
            (values[0], header_runtime.KERNEL_PREFIX),
            (wire, header_runtime.KERNEL_END),
            (nested, header_runtime.KERNEL_PREFIX),
        ):
            def decode(reserve):
                header_runtime._reserve_decode(
                    reserve, value, len(prefix), terminal=prefix == header_runtime.KERNEL_END,
                )
                return header_runtime.parse_json(value[len(prefix):].encode("ascii"), "inert decode")
            with self.subTest(length=len(value)):
                funded(decode)

    def test_observation_transfer_faults_never_refund_or_allow_completion(self):
        sites = ("attempted", "attempted-after", "accessed", "accessed-after", "retire", "retire-after")
        for phase, site in ((phase, site) for phase in ("row", "terminal") for site in sites):
            policy = self.receipt_policy()
            policy.reserve_header_completion()
            if phase == "terminal":
                policy.kernel_record("statfs", "/sys/fs/selinux", -errno.ENOENT)
            entered = []
            faulted = False
            class FaultSet(set):
                def add(self, value):
                    nonlocal faulted
                    if not faulted and type(value) is str and site.startswith(("attempted", "accessed")):
                        faulted = True
                        entered.append(sum(type(item) is not str for item in policy.observation_attempts["accessed"]))
                        if site.endswith("-after"):
                            super().add(value)
                        raise MemoryError(site)
                    return super().add(value)
                def remove(self, value):
                    nonlocal faulted
                    if faulted or site not in {"retire", "retire-after"}:
                        return super().remove(value)
                    faulted = True
                    entered.append(sum(type(item) is not str for item in self))
                    if site == "retire-after":
                        super().remove(value)
                    raise MemoryError(site)
            if site.startswith("accessed"):
                policy.accessed = FaultSet()
            else:
                policy.observation_attempts["accessed"] = FaultSet(policy.observation_attempts["accessed"])
            before = policy.observation_bytes
            with self.subTest(phase=phase, site=site):
                with self.assertRaises(MemoryError):
                    if phase == "row":
                        policy.kernel_record("statfs", "/sys/fs/selinux", -errno.ENOENT)
                    else:
                        policy.header_completion(0)
                spent = policy.observation_bytes
                if phase == "row":
                    self.assertGreater(spent, before)
                else:
                    self.assertEqual(spent, before)
                self.assertEqual(entered, [2 if phase == "row" else 1])
                self.assertGreaterEqual(len(policy.observation_attempts["accessed"]), 2)
                published = set(policy.accessed)
                with self.assertRaises(syscall_guard.Violation):
                    policy.header_completion(0)
                self.assertEqual(policy.observation_bytes, spent)
                self.assertIsNone(policy.header_runtime)
                self.assertEqual(policy.accessed, published)
                if phase == "row":
                    self.assertFalse(any(value.startswith(header_runtime.KERNEL_END) for value in policy.accessed))

    def test_later_row_construction_failure_cannot_sign_the_successful_prefix(self):
        for stage in ("encode", "hash"):
            policy = self.receipt_policy(count=4)
            policy.reserve_header_completion()
            policy.kernel_record("statfs", "/sys/fs/selinux", -errno.ENOENT)
            class FailedHash:
                def update(self, value):
                    raise MemoryError("hash")
            boundary = patch.object(
                syscall_guard, "encoded", side_effect=MemoryError("encode"),
            ) if stage == "encode" else patch.object(policy, "kernel_manifest", FailedHash())
            with self.subTest(stage=stage), boundary:
                with self.assertRaises(MemoryError):
                    policy.kernel_record("absent", "/etc/selinux/config", -errno.ENOENT)
            self.assertEqual(len(policy.observation_attempts["accessed"]), 3)
            self.assertEqual(len(policy.accessed), 1)
            with self.assertRaises(syscall_guard.Violation):
                policy.header_completion(0)
            self.assertEqual(len(policy.accessed), 1)

    def test_supervisor_releases_verifier_before_failed_report_construction(self):
        for mode in ("early", "nonzero", "permitted-nonzero", "success", "publish-fault", "sign-fault"):
            policy = self.receipt_policy()
            policy.reserve_header_completion()
            policy.kernel_record("statfs", "/sys/fs/selinux", -errno.ENOENT)
            policy.__dict__.update(
                consumed=set(), code_consumed=set(), toolchain=None, read_trace=None,
                source_effects=None, journal_receipts=None, directory_installs=None,
                private_install=None, file_cleanup_enabled=False, live_process_peak=0,
                calls=0, written=0, created=0, memory_peak=0, metadata=[], events=[],
                executed=[], producer_requests=[],
            )
            config = {
                **policy.config, "mode": "compile", "process_limit": 1, "descendant_limit": 1,
                "syscall_limit": 1, "write_limit": 1, "creation_limit": 1, "memory_limit": 1,
                "deadline": math.inf, "report": "/inert/header-report.json",
                "dependency": {"filter_kernel": self.profile},
            }
            if mode == "permitted-nonzero":
                config["metadata_validation"] = True
            policy.config = config
            pid = 12345
            status = 1 if "nonzero" in mode else 0
            waits = [
                (pid, syscall_guard.signal.SIGSTOP << 8 | 0x7f), (pid, status << 8),
            ]
            if mode == "early":
                waits = [syscall_guard.Violation("inert early failure"), (pid, 0)]
            stages = []
            reports = []
            original_completion = header_runtime.completion
            def signing(*args):
                stages.append(("sign", policy.header_runtime is self.verifier))
                if mode == "sign-fault":
                    raise MemoryError("sign")
                return original_completion(*args)
            def sorting(values, *args, **kwargs):
                stages.append(("construct", policy.header_runtime is None))
                return sorted(values, *args, **kwargs)
            def publishing(path, value, **kwargs):
                stages.append(("publish", policy.header_runtime is None))
                if mode == "publish-fault":
                    raise OSError("report publication")
                reports.append(json.loads(value))
            def forbidden(*args, **kwargs):
                self.fail("effectful boundary escaped inert supervisor control")
            with self.subTest(mode=mode), ExitStack() as stack:
                for target, name, replacement in (
                    (syscall_guard, "Policy", lambda config: policy),
                    (syscall_guard.os, "getpid", lambda: 10000),
                    (syscall_guard.os, "pidfd_open", lambda pid: 123),
                    (syscall_guard.os, "close", lambda descriptor: None),
                    (syscall_guard.os, "fork", lambda: pid),
                    (syscall_guard.signal, "pidfd_send_signal", lambda *args: None),
                    (syscall_guard.signal, "pthread_sigmask", lambda *args: set()),
                    (syscall_guard.signal, "sigpending", lambda: set()),
                    (syscall_guard, "ptrace", lambda *args: None),
                    (syscall_guard, "signal_tracees", lambda processes: None),
                    (policy, "pin_private_install_parents", lambda: None),
                    (policy, "close_private_install_parents", lambda: None),
                    (policy, "reserve_memory", lambda *args: None),
                    (syscall_guard.os, "execve", forbidden),
                    (syscall_guard.os, "chroot", forbidden),
                    (syscall_guard.os, "_exit", forbidden),
                    (syscall_guard.os, "chdir", forbidden),
                    (syscall_guard.os, "umask", forbidden),
                    (syscall_guard.os, "closerange", forbidden),
                    (syscall_guard.os, "pipe", forbidden),
                    (syscall_guard.os, "write", forbidden),
                    (syscall_guard.os, "dup2", forbidden),
                    (syscall_guard.os, "kill", forbidden),
                    (syscall_guard.os, "killpg", forbidden),
                    (syscall_guard.resource, "prlimit", forbidden),
                    (syscall_guard.resource, "setrlimit", forbidden),
                    (syscall_guard, "trace_me", forbidden),
                    (Path, "write_text", publishing),
                    (header_runtime, "completion", signing),
                ):
                    stack.enter_context(patch.object(target, name, replacement))
                stack.enter_context(patch.object(syscall_guard, "sorted", sorting, create=True))
                stack.enter_context(patch.object(Path, "read_text", return_value=""))
                stack.enter_context(patch.object(syscall_guard.os, "waitpid", side_effect=waits))
                if mode in {"publish-fault", "sign-fault"}:
                    with self.assertRaises((OSError, MemoryError)):
                        syscall_guard.supervise(config, forbidden)
                else:
                    result = syscall_guard.supervise(config, forbidden)
                    self.assertEqual(result, 125 if mode in {"early", "nonzero"} else 0)
            with self.subTest(completed_mode=mode):
                self.assertIsNone(policy.header_runtime)
                self.assertTrue(all(released for _, released in stages), stages)
                if mode in {"success", "publish-fault", "sign-fault"}:
                    self.assertEqual(stages[0], ("sign", True))
                else:
                    self.assertNotIn("sign", [stage for stage, _ in stages])
                if reports:
                    self.assertEqual(
                        any(value.startswith(header_runtime.KERNEL_END) for value in reports[0]["accessed"]),
                        mode == "success",
                    )

    def test_authenticated_actual_transcripts_preserve_order_repeats_and_short_paths(self):
        rows = self.complete_rows()
        transcript = self.authenticate(rows, order=(3, 1, 4, 0, 2))
        self.assertEqual([row.sequence for row in transcript.rows], [1, 2, 3, 4])
        self.assertEqual([row.path for row in transcript.rows[1:3]], ["/proc/filesystems"] * 2)
        short = self.authenticate([self.row(1, "statfs", "/sys/fs/selinux")])
        self.assertEqual(len(short.rows), 1)
        many = [self.row(1, "statfs", "/sys/fs/selinux")] + [
            self.row(
                sequence, "stream", "/proc/mounts", result=0, bytes=0,
                sha256="0" * 64, eof=False,
            )
            for sequence in range(2, 8)
        ]
        self.assertEqual(len(self.authenticate(many).rows), 7)

    def test_unchanged_tag_rejects_coherent_omission_insertion_resequence_and_payload_repairs(self):
        rows = self.complete_rows()
        _, original = self.signed(rows)
        mutations = []
        for omitted_sequence in range(1, len(rows) + 1):
            omitted = [dict(row) for row in rows if row["sequence"] != omitted_sequence]
            for sequence, row in enumerate(omitted, 1):
                row["sequence"] = sequence
            mutations.append(omitted)
        for inserted_sequence in range(1, len(rows) + 1):
            inserted = [dict(row) for row in rows]
            inserted.insert(inserted_sequence, dict(rows[inserted_sequence - 1]))
            for sequence, row in enumerate(inserted, 1):
                row["sequence"] = sequence
            mutations.append(inserted)
        resequenced = [dict(row) for row in rows]
        resequenced[1]["sequence"], resequenced[2]["sequence"] = 3, 2
        mutations.append(resequenced)
        for field, value in (
            ("operation", "unknown"), ("path", "/proc/mounts"), ("result", 1), ("data", "00"),
            ("bytes", 8), ("sha256", "3" * 64), ("eof", False),
        ):
            payload = [dict(row) for row in rows]
            payload[1][field] = value
            mutations.append(payload)
        for changed in mutations:
            repaired = {
                **original,
                "count": len(changed),
                "manifest_sha256": hashlib.sha256(canonical(changed)).hexdigest(),
            }
            with self.subTest(rows=changed), self.assertRaises(ChannelError):
                self.authenticate(changed, repaired)
        for changed in (
            {**original, "count": original["count"] - 1},
            {**original, "manifest_sha256": "3" * 64},
        ):
            with self.assertRaises(ChannelError):
                self.authenticate(rows, changed)

    def test_completion_and_existing_payload_shapes_remain_closed(self):
        rows = self.complete_rows()
        _, completion = self.signed(rows)
        bad = [
            {**completion, "version": 1},
            {**completion, "scope": "foreign"},
            {**completion, "binding": "2" * 64},
            {**completion, "status": 1},
            {**completion, "tag": completion["tag"].upper()},
            {**completion, "extra": None},
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ChannelError):
                self.authenticate(rows, value)
        values, completion = self.signed(rows)
        wire = values + [header_runtime.KERNEL_END + canonical(completion).decode("ascii")]
        with self.assertRaises(ChannelError):
            header_runtime.authenticate_records(
                wire, self.profile,
                header_runtime._FilterLaunch(self.scope, self.binding, b"x" * 32),
                count_limit=32, file_limit=65536,
            )
        for terminals in ([], [wire[-1], wire[-1]]):
            with self.assertRaises(ChannelError):
                header_runtime.authenticate_records(
                    [*values, *terminals], self.profile, self.verifier,
                    count_limit=32, file_limit=65536,
                )
        decimal = [
            *(header_runtime.KERNEL_PREFIX + canonical(row).decode("ascii") for row in rows),
            header_runtime.KERNEL_END + str(len(rows)),
        ]
        with self.assertRaises(ChannelError):
            header_runtime.authenticate_records(
                decimal, self.profile, self.verifier, count_limit=32, file_limit=65536,
            )
        without_first = [self.row(1)]
        with self.assertRaises(ChannelError):
            self.authenticate(without_first)
        malformed = [dict(row) for row in rows]
        malformed[1]["eof"] = 0
        with self.assertRaises(ChannelError):
            self.authenticate(malformed)

    def test_old_count_only_omission_preimage_is_the_restoration_negative(self):
        rows = self.complete_rows()[:2]
        old = [
            header_runtime.KERNEL_PREFIX + canonical(rows[0]).decode("ascii"),
            header_runtime.KERNEL_END + "1",
        ]
        self.assertEqual(
            header_runtime.records(old, self.profile, count_limit=32, file_limit=65536),
            (rows[0],),
        )
        _, original = self.signed(rows)
        repaired = {
            **original, "count": 1,
            "manifest_sha256": hashlib.sha256(canonical(rows[:1])).hexdigest(),
        }
        with self.assertRaises(ChannelError):
            self.authenticate(rows[:1], repaired)

    def test_launch_versions_and_private_key_consumption_are_exact(self):
        dependency = {"executables": ["/usr/bin/sed"], "filter_kernel": self.profile}
        config = {
            "root": "/root", "mode": "compile", "argv": ["/usr/bin/sed"],
            "environment": {}, "code": [], "sources": [], "enumerations": [],
            "executables": ["/usr/bin/sed"], "mounts": [], "dependency": dependency,
        }
        config["header_runtime"] = {
            "version": 2, "scope": "root", "binding": header_runtime.launch_binding(config),
            "receipt_key": self.key.hex(),
        }
        with patch.object(header_runtime, "launch_scope", return_value="root"):
            retained = copy.deepcopy(config)
            header_runtime.validate_launch(retained, consume_key=False)
            self.assertIn("receipt_key", retained["header_runtime"])
            launch = header_runtime.validate_launch(config)
        self.assertEqual(launch.receipt_key, self.key)
        self.assertNotIn("receipt_key", config["header_runtime"])

    def test_guard_reserves_then_commits_without_double_charge_or_refund(self):
        policy = syscall_guard.Policy.__new__(syscall_guard.Policy)
        policy.config = {
            "observation_count": 3, "observation_limit": 1_000_000, "file_limit": 65536,
        }
        policy.filter_kernel = self.profile
        policy.header_runtime = self.verifier
        policy.observation_attempts = {name: set() for name in ("consumed", "code_consumed", "accessed")}
        policy.observation_bytes = 0
        policy.accessed = set()
        policy.kernel_streams = {}
        policy.kernel_sequence = 0
        policy.kernel_has_first_statfs = False
        policy.kernel_manifest = policy.kernel_row_limit = policy.kernel_terminal_limit = None
        policy.kernel_terminal_reservation = None
        policy.reserve_header_completion()
        reserved = policy.observation_bytes
        self.assertEqual(len(policy.observation_attempts["accessed"]), 1)
        policy.kernel_record("statfs", "/sys/fs/selinux", -errno.ENOENT)
        row_charge = policy.observation_bytes - reserved
        self.assertGreater(row_charge, policy.kernel_row_limit + 128)
        self.assertEqual(len(policy.observation_attempts["accessed"]), 2)
        before_terminal = policy.observation_bytes
        policy.header_completion(0)
        self.assertEqual(policy.observation_bytes, before_terminal)
        self.assertEqual(len(policy.observation_attempts["accessed"]), 2)
        transcript = header_runtime.authenticate_records(
            policy.accessed, self.profile, self.verifier,
            count_limit=3, file_limit=65536,
        )
        self.assertEqual(len(transcript.rows), 1)
        self.assertIsNone(policy.header_runtime)
        failed_status = syscall_guard.Policy.__new__(syscall_guard.Policy)
        failed_status.__dict__.update({
            **policy.__dict__,
            "header_runtime": self.verifier,
            "kernel_manifest": hashlib.sha256(b"["),
            "kernel_terminal_reservation": object(),
        })
        with self.assertRaises(syscall_guard.Violation):
            failed_status.header_completion(1)
        self.assertIsNone(failed_status.header_runtime)
        open_stream = syscall_guard.Policy.__new__(syscall_guard.Policy)
        open_stream.__dict__.update({
            **policy.__dict__,
            "header_runtime": self.verifier,
            "kernel_streams": {(1, 2): {}},
            "kernel_manifest": hashlib.sha256(b"["),
            "kernel_terminal_reservation": object(),
        })
        with self.assertRaises(syscall_guard.Violation):
            open_stream.header_completion(0)
        self.assertIsNone(open_stream.header_runtime)

    def test_guard_rejects_before_growth_and_retains_failed_reservations(self):
        def policy(limit=1_000_000):
            value = syscall_guard.Policy.__new__(syscall_guard.Policy)
            value.config = {"observation_count": 3, "observation_limit": limit, "file_limit": 64}
            value.filter_kernel = self.profile
            value.header_runtime = self.verifier
            value.observation_attempts = {
                name: set() for name in ("consumed", "code_consumed", "accessed")
            }
            value.observation_bytes = 0
            value.accessed = set()
            value.kernel_streams = {}
            value.kernel_sequence = 0
            value.kernel_has_first_statfs = False
            value.kernel_manifest = value.kernel_row_limit = value.kernel_terminal_limit = None
            value.kernel_terminal_reservation = None
            return value

        exact = policy()
        calculations = []
        row_limit = header_runtime.row_wire_limit(
            self.profile, count_limit=3, file_limit=64, reserve=calculations.append,
        )
        terminal_limit = header_runtime.terminal_wire_limit(
            self.scope, self.binding, count_limit=3, reserve=calculations.append,
        )
        exact.config["observation_limit"] = (
            sum(calculations)
            + terminal_limit + 128 + header_runtime.terminal_private_reservation(terminal_limit)
            + row_limit + 128 + header_runtime.row_private_reservation(row_limit)
        )
        exact.reserve_header_completion()
        exact.kernel_record("statfs", "/sys/fs/selinux", -errno.ENOENT)
        self.assertEqual(exact.observation_bytes, exact.config["observation_limit"])
        too_small = policy(exact.config["observation_limit"] - 1)
        too_small.reserve_header_completion()
        with self.assertRaises(syscall_guard.Violation):
            too_small.kernel_record("statfs", "/sys/fs/selinux", -errno.ENOENT)
        invalid = policy()
        invalid.reserve_header_completion()
        before = invalid.observation_bytes, len(invalid.observation_attempts["accessed"])
        with self.assertRaises(syscall_guard.Violation):
            invalid.kernel_record(
                "stream", "/proc/filesystems", 0, count=65, digest="0" * 64, eof=False,
            )
        self.assertEqual(
            (invalid.observation_bytes, len(invalid.observation_attempts["accessed"])), before,
        )
        failed = policy()
        failed.reserve_header_completion()
        with patch.object(syscall_guard, "encoded", side_effect=RuntimeError("encode fault")):
            with self.assertRaises(RuntimeError):
                failed.kernel_record("statfs", "/sys/fs/selinux", -errno.ENOENT)
        self.assertEqual(len(failed.observation_attempts["accessed"]), 2)
        self.assertFalse(failed.accessed)
        with self.assertRaises(syscall_guard.Violation):
            failed.header_completion(0)
        self.assertIsNone(failed.header_runtime)

    def test_immutable_views_preserve_json_shape_and_reject_ordinary_mutation(self):
        transcript = self.authenticate(self.complete_rows())
        views = header_runtime.immutable_views(transcript)
        view = views[0]
        self.assertEqual(len(canonical(views)), transcript.semantic_size)
        self.assertEqual(json.loads(json.dumps(views)), [row.mapping() for row in transcript.rows])
        self.assertIs(copy.copy(view), view)
        self.assertIs(copy.deepcopy(view), view)
        mutations = [
            lambda: view.__setitem__("path", "changed"),
            lambda: view.__delitem__("path"),
            view.clear, lambda: view.pop("path"), view.popitem,
            lambda: view.setdefault("x", 1), lambda: view.update({"x": 1}),
            lambda: view.__ior__({"x": 1}), lambda: view.__init__({}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(TypeError):
                mutation()
        copied = dict(view)
        copied["path"] = "changed"
        self.assertNotEqual(copied, view)
        self.assertEqual(set(view), set(header_runtime.ROW_FIELDS))

    def test_native_value_fingerprint_exact_types_aliases_unicode_and_work(self):
        vectors = [
            (None, "78037ec9f39c31763d6434b47888cc98e7b330de8c60682363a9a4e6a6e13bb8"),
            (False, "a37f684c3af66ed546604ef16e342212af64c10f07ece520b2b79d0bb3f24f27"),
            (True, "93dc61a380aed9209cb3164cfa16f916d7ab645fa526297b5180a3715b1b65a4"),
            (0, "1320306fdee567d967b24a4768b18b3a9e7f1916f177cf1d4930c5229f136f37"),
            (-1, "e3bf2ae44b178dfff8e93380af451b4b75940937e4933b9878013a1f4736bf59"),
            (1.5, "37991ed4f7173ca59e3a77fc244a4d8bce1db15f0ca0e7da85bf3b1eaa5a43f2"),
            ("A\ud800😀", "8de7537b5a731b3b0fca3de92533e9c2a07d35beaadf2c6d190f897892cad983"),
            ([None, True, -1], "56455efbfc1c508737d00f42bc4bc82b255523f7594a93350288e85778c71081"),
            ({"z": 0, "a": "x"}, "f6bb635b90f749ed1334e47ebd23e8785a9701a745787f923e913a54390b61e4"),
        ]
        def fingerprint(value, charges=None):
            return make_probe._native_value_fingerprint(
                value, reserve=(lambda size: None if charges is None else charges.append(size)),
                remaining=lambda: None, node_limit=128,
            )
        for value, expected in vectors:
            self.assertEqual(fingerprint(value).hex(), expected)
        self.assertEqual(fingerprint([1, "x"]), fingerprint((1, "x")))
        self.assertEqual(fingerprint({"a": 1, "b": 2}), fingerprint({"b": 2, "a": 1}))
        self.assertNotEqual(fingerprint(True), fingerprint(1))
        self.assertNotEqual(fingerprint(0.0), fingerprint(-0.0))
        self.assertNotEqual(fingerprint("😀"), fingerprint("\ud83d\ude00"))
        alias = ["x"]
        self.assertEqual(fingerprint([alias, alias]), fingerprint([["x"], ["x"]]))
        cycle = []
        cycle.append(cycle)
        for value in (1 << 64, -(1 << 64), math.inf, math.nan, b"x", {1: "x"}, cycle):
            with self.subTest(value=type(value)), self.assertRaises(MakeProbeError):
                fingerprint(value)
        class String(str):
            pass
        with self.assertRaises(MakeProbeError):
            fingerprint(String("x"))
        with self.assertRaises(MakeProbeError):
            make_probe._native_value_fingerprint(
                [1, 2], reserve=lambda size: None, remaining=lambda: None, node_limit=2,
            )
        charges = []
        fingerprint({"a": [1, 2, 3]}, charges)
        first = sum(charges)
        fingerprint({"a": [1, 2, 3]}, charges)
        self.assertEqual(sum(charges), 2 * first)
        used = 0
        def bounded(size):
            nonlocal used
            if used + size > 600:
                raise MakeProbeError("exhausted")
            used += size
        with self.assertRaises(MakeProbeError):
            make_probe._native_value_fingerprint(
                "x" * 100, reserve=bounded, remaining=lambda: None, node_limit=128,
            )

    def test_shared_native_return_is_context_bound_one_use_and_freezes_values(self):
        budget = ProbeBudget(Limits(control_bytes=1024 * 1024))
        session = make_probe.ProbeSession.__new__(make_probe.ProbeSession)
        session.budget = budget
        session.owner_thread = threading.get_ident()
        session.snapshot = object()
        session.tree = Path("/source")
        session._namespace_epoch = 7
        session._native_issue_owner = None
        session._native_returns = {}
        owner = make_probe._HeaderRuntimeLaunch()
        command, live, step = object(), object(), object()
        context = (command, live, step)
        completed = subprocess.CompletedProcess(("sed",), 0, b"original", b"original-error")
        stdout, stderr = completed.stdout, completed.stderr
        observed = {
            "ok": True, "returncode": 0, "accessed": ["row"], "metadata": (),
            "consumed": [], "code_consumed": [], "executed": ["sed"],
        }
        payload = self.authenticate([self.row(1, "statfs", "/sys/fs/selinux")])
        with patch.object(session, "_native_return_context", return_value=context):
            session._native_issue_owner = owner
            session._issue_native_return(
                header_runtime.FILTER_PURPOSE, owner, completed, observed, payload,
            )
            claimed = session._claim_native_return(
                header_runtime.FILTER_PURPOSE, owner, completed, observed,
            )
            self.assertEqual((claimed.stdout, claimed.stderr, claimed.returncode),
                             (b"original", b"original-error", 0))
            self.assertIs(claimed.stdout, stdout)
            self.assertIs(claimed.stderr, stderr)
            completed.stdout = b"replacement"
            completed.stderr = b"replacement-error"
            observed["accessed"].clear()
            observed["executed"].clear()
            self.assertEqual(claimed.stdout, b"original")
            self.assertEqual(claimed.stderr, b"original-error")
            self.assertEqual(claimed.executed, ("sed",))
            self.assertEqual(len(claimed.payload.rows), 1)
            with self.assertRaises(MakeProbeError):
                session._claim_native_return(
                    header_runtime.FILTER_PURPOSE, owner, completed, observed,
                )
        self.assertFalse(session._native_returns)

    def test_shared_native_return_rejects_mutation_copy_purpose_context_and_retires(self):
        def session():
            value = make_probe.ProbeSession.__new__(make_probe.ProbeSession)
            value.budget = ProbeBudget(Limits(control_bytes=1024 * 1024))
            value.owner_thread = threading.get_ident()
            value.snapshot = object()
            value.tree = Path("/source")
            value._namespace_epoch = 2
            value._native_issue_owner = None
            value._native_returns = {}
            return value

        payload = self.authenticate([self.row(1, "statfs", "/sys/fs/selinux")])
        for defect in (
            "status", "stdout", "stderr", "report", "copy", "report-copy",
            "owner-copy", "purpose", "context", "snapshot", "epoch", "thread",
            "stdout-equal", "stderr-equal",
        ):
            current = session()
            owner = make_probe._HeaderRuntimeLaunch()
            issued_owner = owner
            completed = subprocess.CompletedProcess(("sed",), 0, b"out", b"err")
            observed = {
                "ok": True, "returncode": 0, "accessed": ["row"], "metadata": (),
                "consumed": [], "code_consumed": [], "executed": ["sed"],
            }
            context = (object(), object(), object())
            with self.subTest(defect=defect), patch.object(
                current, "_native_return_context", side_effect=[
                    context, (object(), context[1], context[2]) if defect == "context" else context,
                ],
            ):
                current._native_issue_owner = owner
                current._issue_native_return(
                    header_runtime.FILTER_PURPOSE, owner, completed, observed, payload,
                )
                claimed_completed, claimed_observed = completed, observed
                purpose = header_runtime.FILTER_PURPOSE
                if defect == "status":
                    completed.returncode = 1
                elif defect == "stdout":
                    completed.stdout = b"changed"
                elif defect == "stderr":
                    completed.stderr = b"changed"
                elif defect in {"stdout-equal", "stderr-equal"}:
                    name = defect.removesuffix("-equal")
                    original = getattr(completed, name)
                    replacement = bytes(bytearray(original))
                    self.assertEqual(replacement, original)
                    self.assertIsNot(replacement, original)
                    setattr(completed, name, replacement)
                elif defect == "report":
                    observed["accessed"].append("changed")
                elif defect == "copy":
                    claimed_completed = copy.copy(completed)
                elif defect == "report-copy":
                    claimed_observed = copy.deepcopy(observed)
                elif defect == "owner-copy":
                    owner = copy.copy(owner)
                elif defect == "purpose":
                    purpose = "toolchain-step-v2"
                elif defect == "snapshot":
                    current.snapshot = object()
                elif defect == "epoch":
                    current._namespace_epoch += 1
                thread = patch.object(
                    make_probe, "get_ident",
                    return_value=threading.get_ident() + 1,
                ) if defect == "thread" else patch.object(
                    make_probe, "get_ident", wraps=make_probe.get_ident,
                )
                with thread, self.assertRaises(MakeProbeError):
                    current._claim_native_return(purpose, owner, claimed_completed, claimed_observed)
                current._retire_native_return(
                    header_runtime.FILTER_PURPOSE, issued_owner, completed, observed,
                )
                self.assertFalse(current._native_returns)
        current = session()
        owner = make_probe._HeaderRuntimeLaunch()
        completed = subprocess.CompletedProcess(("sed",), 0, b"out", b"err")
        observed = {
            "ok": True, "returncode": 0, "accessed": [], "metadata": (),
            "consumed": [], "code_consumed": [], "executed": ["sed"],
        }
        context = (object(), object(), object())
        with patch.object(current, "_native_return_context", return_value=context):
            current._native_issue_owner = owner
            current._issue_native_return(
                header_runtime.FILTER_PURPOSE, owner, completed, observed, payload,
            )
            current._retire_native_return(
                header_runtime.FILTER_PURPOSE, owner, completed, observed,
            )
        self.assertFalse(current._native_returns)
        current = session()
        owner = make_probe._HeaderRuntimeLaunch()
        completed = subprocess.CompletedProcess(("sed",), 0, b"out", b"err")
        observed = {
            "ok": True, "returncode": 0, "accessed": [], "metadata": (),
            "consumed": [], "code_consumed": [], "executed": ["sed"],
        }
        current.budget = ProbeBudget(Limits(control_bytes=1))
        with patch.object(current, "_native_return_context", return_value=context):
            current._native_issue_owner = owner
            with self.assertRaises(MakeProbeError):
                current._issue_native_return(
                    header_runtime.FILTER_PURPOSE, owner, completed, observed, payload,
                )
        self.assertFalse(current._native_returns)


if __name__ == "__main__":
    unittest.main()
