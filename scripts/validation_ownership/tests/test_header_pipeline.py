"""Actual original ARM/sed header steps, not a substitute full-root census."""

import copy
import json
from dataclasses import replace
from pathlib import Path
import shlex
import subprocess
import unittest
from unittest.mock import patch

from scripts.validation_ownership import arm_headers, header_runtime
from scripts.validation_ownership.authority import ENVIRONMENT
from scripts.validation_ownership.budget import MakeProbeError
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


if __name__ == "__main__":
    unittest.main()
