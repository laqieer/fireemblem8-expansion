"""The bounded HOST C dependency action above live producer publication."""

import hashlib
import json
import shlex
import subprocess
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.validation_ownership.authority import ENVIRONMENT
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.make_probe import Command, NativeTool
from scripts.validation_ownership import make_probe
from scripts.validation_ownership.tests import test_foundation as foundation


class DependencyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = foundation.FoundationTests()
        self.fixture.setUp()
        self.root = self.fixture.root

    def tearDown(self):
        self.fixture.tearDown()

    def assert_clean(self, session):
        self.fixture.assert_clean(session)
        self.assertIsNone(session.dependency_compiler)
        self.assertIsNone(session.dependency_runtime)
        self.assertFalse(session.published_sources)
        self.assertFalse(session.parked_capsules)

    def dependency(self, *, macros=("-DENABLED=1",), includes=("first", "second")):
        add = self.fixture.add
        add("src/query.c", (
            '#include "recursive.h"\n#include <priority.h>\n'
            '#if ENABLED\n#include "enabled.h"\n#else\n#include "disabled.h"\n#endif\n'
            '#include "future/generated.h"\n'
        ))
        add("quote/recursive.h", (
            '#ifndef RECURSIVE_H\n#define RECURSIVE_H\n#include "inner/next.h"\n#endif\n'
        ))
        add("quote/inner/next.h", '#include "../recursive.h"\n')
        add("quote/enabled.h", "#define VALUE 1\n")
        add("quote/disabled.h", "#define VALUE 0\n")
        add("first/priority.h", "#define PRIORITY 1\n")
        add("second/priority.h", "#define PRIORITY 2\n")
        return Command(
            (
                "/usr/bin/cc", "-E", "-I", includes[0], "-I" + includes[1],
                "-iquote", "quote", "-nostdinc", "-undef", *macros,
                "src/query.c", "-MM", "-MG", "-MT", "query.o",
            ),
            code=tuple(sorted(name for name in self.fixture.entries if name.endswith(".h"))),
            sources=("src/query.c",), outputs=("out/query.d",), dependency_only=True,
        )

    def ordinary(self, command):
        return subprocess.run(
            command.argv, cwd=self.root,
            env={**ENVIRONMENT, "SOURCE_DATE_EPOCH": "0", "TMPDIR": str(self.fixture.directory)},
            capture_output=True, check=True, timeout=10,
        ).stdout

    def test_frozen_case_is_indexed_with_its_real_document_and_automation(self):
        from scripts import check_docs

        registry, errors = check_docs.parse_test_case_registry(str(foundation.ROOT))
        self.assertEqual(errors, [])
        case_id = "TC-WORKFLOW-PROBE-DEPENDENCY-001"
        case, = [item for item in registry["cases"] if item["id"] == case_id]
        feature, = [item for item in registry["features"] if item["id"] == case["feature_id"]]
        self.assertIn(case_id, feature["required_cases"])
        self.assertIn(case["feature_id"], registry["coverage"]["expected_feature_ids"])
        self.assertEqual(case["issue_urls"], ["https://github.com/laqieer/fireemblem8-expansion/issues/228"])
        selected = {
            **registry,
            "features": [{**feature, "required_cases": [case_id]}],
            "cases": [case],
            "coverage": {**registry["coverage"], "expected_feature_ids": [feature["id"]]},
        }
        with patch.object(check_docs, "parse_test_case_registry", return_value=(selected, [])):
            self.assertEqual(check_docs.check_test_case_registry(str(foundation.ROOT)), [])

    def test_real_driver_cc1_bytes_ordered_options_and_recursive_missing_headers(self):
        for macros, includes, selected in (
            (("-DENABLED=1",), ("first", "second"), "enabled"),
            (("-D", "ENABLED=1", "-UENABLED", "-DENABLED=0"), ("second", "first"), "disabled"),
            (("-DENABLED=0", "-U", "ENABLED", "-D", "ENABLED=1"), ("first", "second"), "enabled"),
        ):
            with self.subTest(macros=macros, includes=includes):
                command = self.dependency(macros=macros, includes=includes)
                expected = self.ordinary(command)
                used = {
                    "src/query.c", "quote/recursive.h", "quote/inner/next.h",
                    "quote/" + selected + ".h", includes[0] + "/priority.h",
                }
                with self.fixture.session(seconds=45) as session:
                    result = session.command(command)
                    generated, = result.generated
                    self.assertEqual(generated.path, "out/query.d")
                    self.assertEqual(generated.data, expected)
                    self.assertTrue(generated.data)
                    self.assertIn(b"future/generated.h", generated.data)
                    self.assertEqual(generated.mode, 0o644)
                    self.assertEqual(result.stdout, b"")
                    self.assertEqual(result.stderr, b"")
                    self.assertIsNone(result.artifact)
                    self.assertEqual(result.consumed, ("src/query.c",))
                    self.assertEqual(set(result.consumed) | set(result.code_consumed), used)
                    self.assertEqual(result.input_identities, tuple(session.snapshot.owners(used)))
                    self.assertEqual(result.executed, session.dependency_compiler)
                    self.assertEqual(len(result.executed), 2)
                    self.assertEqual(result.executed[0], str(Path("/usr/bin/cc").resolve()))
                    self.assertEqual(Path(result.executed[1]).name, "cc1")
                    self.assertFalse(session.native_tools)
                self.assert_clean(session)

    def test_exact_source_union_and_repeated_effects_keep_one_compiler_profile(self):
        command = self.dependency()
        expected = self.ordinary(command)
        with self.fixture.session(seconds=45) as session:
            resolve = session._compiler_tools
            with patch.object(session, "_compiler_tools", wraps=resolve) as captured:
                first = session.command(command)
                second = session.command(command)
                union = tuple(sorted(set(first.consumed) | set(first.code_consumed)))
                exact = session.command(replace(command, code=(), sources=union))
            self.assertEqual(captured.call_count, 1)
            captured.assert_called_once_with(False, ("cc1",))
            self.assertIsNot(first, second)
            self.assertEqual(first.generated, second.generated)
            self.assertEqual(first.input_identities, second.input_identities)
            self.assertEqual(exact.consumed, union)
            self.assertEqual(exact.code_consumed, ())
            self.assertEqual(exact.generated[0].data, expected)
            self.assertEqual(exact.executed, first.executed)
            self.assertFalse(session.native_tools)
        self.assert_clean(session)

    def test_iquote_order_and_joined_values_select_the_actual_quoted_header(self):
        for order in (("alternative", "quote"), ("quote", "alternative")):
            with self.subTest(order=order):
                command = self.dependency()
                self.fixture.add("alternative/recursive.h", "#define ALTERNATIVE 1\n")
                arguments = list(command.argv)
                index = arguments.index("-iquote")
                arguments[index:index + 2] = ["-iquote", order[0], "-iquote" + order[1]]
                command = replace(
                    command, argv=tuple(arguments),
                    code=tuple(sorted(set(command.code) | {"alternative/recursive.h"})),
                )
                expected = self.ordinary(command)
                with self.fixture.session(seconds=45) as session:
                    result = session.command(command)
                    self.assertEqual(result.generated[0].data, expected)
                    self.assertIn(order[0] + "/recursive.h", result.code_consumed)
                    self.assertNotIn(order[1] + "/recursive.h", result.code_consumed)
                self.assert_clean(session)

    def test_observed_worldmap_dependency_recipe_uses_real_repository_sources(self):
        source = "src/worldmap_tm_confront.c"
        self.fixture.add(source, (foundation.ROOT / source).read_bytes())
        headers = []
        for path in sorted((foundation.ROOT / "include").rglob("*.h")):
            name = path.relative_to(foundation.ROOT).as_posix()
            self.fixture.add(name, path.read_bytes())
            headers.append(name)
        command = Command(
            (
                "/usr/bin/cc", "-E", "-I", "tools/agbcc/include", "-iquote", "include", "-iquote", ".",
                "-nostdinc", "-undef", "-DFE8_ARCHIVAL_BUILD=1",
                "-Ibuild/generated/assets", "-Ibuild/generated/assets/banim",
                "-Ibuild/generated/assets/custom_spell", source, "-MM", "-MG",
                "-MT", "src/worldmap_tm_confront.o",
            ),
            code=tuple(headers), sources=(source,),
            outputs=(".dep/src/worldmap_tm_confront.d",), dependency_only=True,
        )
        expected = self.ordinary(command)
        with self.fixture.session(seconds=45) as session:
            result = session.command(command)
            self.assertEqual(result.generated[0].data, expected)
            self.assertEqual(result.consumed, (source,))
            self.assertIn("include/global.h", result.code_consumed)
            self.assertGreater(len(result.code_consumed), 1)
            self.assertEqual(
                result.input_identities,
                tuple(session.snapshot.owners({source, *result.code_consumed})),
            )
            self.assertEqual(result.executed, session.dependency_compiler)
        self.assert_clean(session)

    def test_default_and_unsupported_profiles_reject_before_payload_launch(self):
        command = self.dependency()
        cases = [
            replace(command, dependency_only=False),
            *(replace(command, dependency_only=value) for value in (1, None, "true")),
            replace(command, native_tool=NativeTool(self.root / "not-issued", "0" * 64)),
            replace(command, argv=("/usr/bin/gcc", *command.argv[1:])),
            replace(command, outputs=()),
            replace(command, outputs=("one.d", "two.d")),
            replace(command, outputs=("out/not-dependencies",)),
            replace(command, outputs=("../escape.d",)),
            replace(command, outputs=("src/query.c",)),
            *(replace(command, argv=tuple(value for value in command.argv if value != mode))
              for mode in ("-E", "-MM", "-nostdinc", "-undef")),
            *(replace(command, argv=(*command.argv, *extra)) for extra in (
                ("-c",), ("-S",), ("-M",), ("-MD",), ("-MMD",), ("-MP",),
                ("-MF", "/work/other.d"), ("-o", "/work/escape"),
                ("-fplugin=plugin.so",), ("-specs=specs",), ("@response",),
                ("-B", "tools"), ("-wrapper", "wrapper"), ("-x", "c++"),
                ("-include", "secret.h"), ("-imacros", "secret.h"), ("-Wp,-MD,escape",),
                ("--sysroot=owned",), ("--sysroot", "owned"),
                ("-I", "/etc"), ("-I../outside",), ("-I", ""), ("-iquote", "-MM"),
                ("-UENABLED=1",), ("-D", "NAME\n=1"), ("-MT", "another"),
                ("-I",), ("-D",), ("-U",), ("-MT",), ("second.c",),
            )),
        ]
        for changed in cases:
            with self.subTest(command=changed):
                with self.fixture.session(seconds=30) as session:
                    launches = session.budget.runs
                    with self.assertRaises(MakeProbeError):
                        session.command(changed)
                    self.assertEqual(session.budget.runs, launches)
                self.assert_clean(session)

    def test_sysroot_special_include_operands_reject_before_compiler_launch(self):
        command = self.dependency()
        for value in ("=", "=include", "=/include", "$SYSROOT", "$SYSROOTinclude", "$SYSROOT/include"):
            for prefix, joined in (("-I", False), ("-I", True), ("-iquote", False), ("-iquote", True)):
                option = (prefix + value,) if joined else (prefix, value)
                with self.subTest(option=option):
                    changed = replace(command, argv=(*command.argv, *option))
                    with self.fixture.session(seconds=30) as session:
                        runs = session.budget.runs
                        with self.assertRaisesRegex(MakeProbeError, "sysroot-special"):
                            session.command(changed)
                        self.assertEqual(session.budget.runs, runs)
                    self.assert_clean(session)
        for value in ("./=include", "./$SYSROOTinclude", "../include", "a/../include"):
            with self.subTest(noncanonical=value):
                with self.assertRaisesRegex(MakeProbeError, "canonical and repository-relative"):
                    make_probe.ProbeSession._dependency_options(
                        replace(command, argv=(*command.argv, "-I", value)),
                        command.sources, command.outputs,
                    )

    def test_internal_sysroot_characters_are_ordinary_literal_include_names(self):
        for directory, prefix, joined in (
            ("local/=headers", "-I", False),
            ("local/$SYSROOTheaders", "-I", True),
            ("headers=literal", "-iquote", False),
            ("headers$SYSROOTliteral", "-iquote", True),
        ):
            with self.subTest(directory=directory, prefix=prefix):
                command = self.dependency()
                self.fixture.add("src/query.c", '#include "literal.h"\n')
                header = directory + "/literal.h"
                self.fixture.add(header, "#define LITERAL 1\n")
                option = (prefix + directory,) if joined else (prefix, directory)
                command = replace(
                    command,
                    argv=("/usr/bin/cc", "-E", "-nostdinc", "-undef", *option,
                          "src/query.c", "-MM", "-MG", "-MT", "query.o"),
                    code=(header,),
                )
                expected = self.ordinary(command)
                with self.fixture.session(seconds=45) as session:
                    result = session.command(command)
                    self.assertEqual(result.generated[0].data, expected)
                    self.assertEqual(result.code_consumed, (header,))
                self.assert_clean(session)

    def compiler_path(self, query):
        result = subprocess.run(
            ["/usr/bin/cc", query], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        )
        path = Path(result.stdout.decode("utf-8").strip())
        self.assertTrue(path.is_absolute())
        return path.resolve()

    def test_host_header_and_has_include_cannot_escape_source_provenance(self):
        command = self.dependency()
        host = "/usr/include/linux/version.h"
        self.assertTrue(Path(host).is_file())
        for source in (
            '#include "' + host + '"\n',
            '#if __has_include("' + host + '")\n#include "enabled.h"\n'
            '#else\n#include "disabled.h"\n#endif\n',
        ):
            with self.subTest(source=source):
                self.fixture.add("src/query.c", source)
                expected = self.ordinary(command)
                self.assertTrue(expected)
                with self.fixture.session(seconds=45) as session:
                    with self.assertRaisesRegex(MakeProbeError, "undeclared dependency host"):
                        session.command(command)
                self.assert_clean(session)
        self.fixture.add("src/query.c", '#include "enabled.h"\n')
        self.fixture.add("quote/enabled.h", '#include "' + host + '"\n')
        with self.fixture.session(seconds=45) as session:
            with self.assertRaisesRegex(MakeProbeError, "undeclared dependency host"):
                session.command(command)
        self.assert_clean(session)

    def test_host_source_family_rejects_existing_missing_alias_and_type_probes(self):
        command = self.dependency()
        install = self.compiler_path("-print-file-name=.")
        frontend = self.compiler_path("-print-prog-name=cc1")
        linker = self.compiler_path("-print-prog-name=collect2")
        library_script = self.compiler_path("-print-file-name=libc.so")
        python_data = Path(self.fixture.runtime_data_path())
        missing = "ownership-" + self.fixture.directory.name
        candidates = (
            install / "include/stddef.h",
            install / "include-fixed" / missing,
            frontend.parent / missing,
            library_script,
            Path("/lib") / library_script.relative_to("/usr/lib"),
            python_data,
            linker,
            Path("/usr/local/include") / missing,
            Path("/usr/x86_64-linux-gnu/include") / missing,
            Path("/usr/include/linux/../linux/version.h"),
            Path("/usr/include"),
            Path("/dev/null"),
        )
        for path in candidates:
            with self.subTest(host_path=str(path)):
                self.fixture.add("src/query.c", (
                    '#if __has_include("' + str(path) + '")\n#include "enabled.h"\n'
                    '#else\n#include "disabled.h"\n#endif\n'
                ))
                with self.fixture.session(seconds=45) as session:
                    with self.assertRaisesRegex(MakeProbeError, "undeclared dependency host"):
                        session.command(command)
                self.assert_clean(session)
        self.fixture.add("src/query.c", '#if __has_include("' + str(library_script / "child") + '")\n#endif\n')
        with self.fixture.session(seconds=45) as session:
            with self.assertRaisesRegex(MakeProbeError, "dependency runtime path has an unsupported type"):
                session.command(command)
        self.assert_clean(session)

    def test_runtime_decision_mutation_reproduces_the_original_host_branch_bypass(self):
        command = self.dependency()
        host = "/usr/include/linux/version.h"
        self.fixture.add("src/query.c", (
            '#if __has_include("' + host + '")\n#include "enabled.h"\n'
            '#else\n#include "disabled.h"\n#endif\n'
        ))
        expected = self.ordinary(command)
        proxy = self.fixture.directory / "mutated-runtime-boundary.py"
        proxy.write_text(
            "import sys\n"
            f"sys.path.insert(0,{str(make_probe.TRUSTED_ROOT)!r})\n"
            "import syscall_guard as guard,sandbox_exec\n"
            "original=guard.Policy.check\n"
            "def without_dependency_decision(self,state,path,operation,**kwargs):\n"
            " if path in ('/repo','/work') or path.startswith(('/repo/','/work/')):\n"
            "  return original(self,state,path,operation,**kwargs)\n"
            " dependency=self.config.pop('dependency',None)\n"
            " try:\n  return original(self,state,path,operation,**kwargs)\n"
            " finally:\n"
            "  if dependency is not None: self.config['dependency']=dependency\n"
            "guard.Policy.check=without_dependency_decision\n"
            "raise SystemExit(sandbox_exec.main())\n"
        )
        run = ProbeBudget.run
        def mutate(budget, argv, **kwargs):
            if len(argv) >= 2 and argv[-2] == str(make_probe.TRUSTED_ROOT / "sandbox_exec.py"):
                config = json.loads(Path(argv[-1]).read_bytes())
                if config.get("dependency"):
                    argv = [*argv[:-2], str(proxy), argv[-1]]
            return run(budget, argv, **kwargs)
        with self.fixture.session(seconds=45) as session:
            with self.assertRaisesRegex(MakeProbeError, "undeclared dependency host"):
                session.command(command)
        self.assert_clean(session)
        with patch.object(ProbeBudget, "run", mutate):
            with self.fixture.session(seconds=45) as session:
                result = session.command(command)
                self.assertEqual(result.generated[0].data, expected)
                self.assertIn("quote/enabled.h", result.code_consumed)
                self.assertNotIn(host, set(result.consumed) | set(result.code_consumed))
                self.assertNotIn(host, {item[0] for item in result.input_identities})
                self.assertEqual(result.executed, session.dependency_compiler)
        self.assert_clean(session)

    def test_existing_undeclared_header_cannot_be_reported_as_missing(self):
        command = self.dependency()
        for source in (
            '#include "enabled.h"\n',
            '#if __has_include("enabled.h")\n#include "enabled.h"\n'
            '#else\n#include "future/generated.h"\n#endif\n',
        ):
            with self.subTest(source=source):
                self.fixture.add("src/query.c", source)
                changed = replace(command, code=tuple(name for name in command.code if name != "quote/enabled.h"))
                with self.fixture.session(seconds=30) as session:
                    self.assertEqual(session.command(command).generated[0].data, self.ordinary(command))
                    before = session.budget.runs
                    with self.assertRaisesRegex(MakeProbeError, "undeclared source"):
                        session.command(changed)
                    self.assertGreater(session.budget.runs, before)
                self.assert_clean(session)

    def test_missing_without_MG_and_unused_declared_sources_reject(self):
        command = self.dependency()
        for changed, expected in (
            (replace(command, argv=tuple(arg for arg in command.argv if arg != "-MG")), "unsuccessfully"),
            (replace(command, sources=("src/query.c", "quote/disabled.h")), "declared/consumed"),
        ):
            with self.subTest(command=changed):
                with self.fixture.session(seconds=30) as session:
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        session.command(changed)
                    self.assertFalse(session.published_sources)
                self.assert_clean(session)

    def test_search_directories_are_metadata_not_content_or_enumeration_grants(self):
        command = self.dependency()
        self.fixture.add("quote/private.h", "#define PRIVATE 1\n")
        self.fixture.add("src/query.c", '#include "private.h"\n')
        with self.fixture.session(seconds=30) as session:
            with self.assertRaisesRegex(MakeProbeError, "undeclared source"):
                session.command(command)
        self.assert_clean(session)
        self.fixture.add("src/query.c", '#include "not-a-directory/header.h"\n')
        self.fixture.add("quote/not-a-directory", "a regular file, not a missing directory")
        with self.fixture.session(seconds=30) as session:
            with self.assertRaises(MakeProbeError):
                session.command(command)
        self.assert_clean(session)
        self.fixture.add("quote/alias.h", "private.h", mode="120000")
        (self.root / "quote/alias.h").unlink()
        (self.root / "quote/alias.h").symlink_to("private.h")
        self.fixture.add("src/query.c", '#include "alias.h"\n')
        with self.fixture.session(seconds=30) as session:
            with self.assertRaisesRegex(MakeProbeError, "nonregular candidate source"):
                session.command(command)
        self.assert_clean(session)

    def test_source_bytes_and_receipts_are_from_the_captured_execution(self):
        command = self.dependency()
        expected = self.ordinary(command)
        with self.fixture.session(seconds=45) as session:
            self.fixture.add("quote/enabled.h", '#include "/etc/passwd"\n')
            result = session.command(command)
            self.assertEqual(result.generated[0].data, expected)
            receipt = next(item for item in result.input_identities if item[0] == "quote/enabled.h")
            self.assertEqual(receipt[2], hashlib.sha256(b"#define VALUE 1\n").hexdigest())
            self.assertNotEqual(receipt[2], hashlib.sha256((self.root / "quote/enabled.h").read_bytes()).hexdigest())
        self.assert_clean(session)

    def test_generated_header_replacement_keeps_each_dependency_receipt_and_reader_current(self):
        command = self.dependency(macros=())
        self.fixture.add("src/query.c", (
            '#include "selection.h"\n#if ENABLED\n#include "enabled.h"\n'
            '#else\n#include "disabled.h"\n#endif\n'
        ))
        self.fixture.add("state/original", "original")
        self.fixture.add("writer.py", (
            "from pathlib import Path\nimport os,sys\n"
            "path=Path(sys.argv[1])/'quote/selection.h'; path.parent.mkdir(parents=True,exist_ok=True)\n"
            "path.write_text('#define ENABLED '+str(int(len(os.listdir('state'))==1))+'\\n')\n"
        ))
        self.fixture.add("marker.py", (
            "from pathlib import Path\nimport sys\n"
            "path=Path(sys.argv[1])/'state/new'; path.parent.mkdir(parents=True,exist_ok=True)\n"
            "path.write_text('new')\n"
        ))
        self.fixture.add("reader.py", (
            "import base64\nprint(base64.b64encode(open('out/query.d','rb').read()).decode('ascii'))\n"
        ))
        command = replace(command, code=(*command.code, "quote/selection.h"))
        event = "mkdir -p out && " + shlex.join(command.argv) + " > out/query.d"
        self.fixture.add("Makefile", (
            "WRITE1 := $(shell python3 writer.py .)\nDEP1 := $(shell " + event + ")\n"
            "FIRST := $(shell python3 reader.py)\nMARK := $(shell python3 marker.py .)\n"
            "WRITE2 := $(shell python3 writer.py .)\nDEP2 := $(shell " + event + ")\n"
            "SECOND := $(shell python3 reader.py)\ninclude out/query.d\n"
            "query.o:\n\t@printf '%s\\n' '$(FIRST)' '$(SECOND)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "query.o"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        ).stdout.decode("ascii").splitlines()
        self.assertEqual(len(ordinary), 2)
        self.assertNotEqual(*ordinary)
        for name in ("quote/selection.h", "state/new", "out/query.d"):
            (self.root / name).unlink()
        (self.root / "out").rmdir()
        registrations = {
            event: command,
            "python3 writer.py .": Command(
                ("/usr/bin/python3", "/repo/writer.py", "/work"), code=("writer.py",),
                directories=("state",), outputs=("quote/selection.h",),
            ),
            "python3 marker.py .": Command(
                ("/usr/bin/python3", "/repo/marker.py", "/work"),
                code=("marker.py",), outputs=("state/new",),
            ),
            "python3 reader.py": Command(
                ("/usr/bin/python3", "/repo/reader.py"),
                code=("reader.py",), sources=("out/query.d",),
            ),
        }
        with self.fixture.session(seconds=45) as session:
            execute, results = session.command, []
            def capture(value):
                result = execute(value)
                if value.dependency_only:
                    results.append(result)
                return result
            with patch.object(session, "command", capture):
                observed = session.make("query.o", variables=("FIRST", "SECOND"), commands=registrations)
            self.assertEqual([observed.semantics["domains"][name]["value"] for name in ("FIRST", "SECOND")], ordinary)
            self.assertEqual(len(results), 2)
            for result, selected, data in zip(
                results, ("enabled", "disabled"), (b"#define ENABLED 1\n", b"#define ENABLED 0\n"),
            ):
                self.assertIn("quote/" + selected + ".h", result.code_consumed)
                identity = next(item for item in result.input_identities if item[0] == "quote/selection.h")
                self.assertEqual(identity, ("quote/selection.h", "100644", hashlib.sha256(data).hexdigest()))
            self.assertNotEqual(results[0].generated[0].data, results[1].generated[0].data)
            self.assertEqual(len([item for item in observed.semantics["dynamic_commands"]
                                  if item["command"].get("dependency_only")]), 2)
        self.assert_clean(session)

    def test_aggregate_output_exhaustion_rejects_a_real_repeated_compiler_result(self):
        command = self.dependency()
        with self.fixture.session(seconds=45) as session:
            first = session.command(command)
            size = len(first.generated[0].data)
            session.budget.charge(
                "output", session.budget.limits.output_bytes - session.budget.bytes["output"] - size + 1,
            )
            launches = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "aggregate output byte budget exhausted"):
                session.command(command)
            self.assertGreater(session.budget.runs, launches)
            self.assertFalse(session.published_sources)
        self.assert_clean(session)

    def test_private_dependency_directories_spend_creation_credits_before_launch(self):
        command = replace(self.dependency(), outputs=("one/two/query.d",))
        with self.fixture.session(seconds=45) as session:
            run, created = session._sandbox_run, []
            def capture(root, **kwargs):
                result, observed = run(root, **kwargs)
                created.append(observed["created_files"])
                return result, observed
            with patch.object(session, "_sandbox_run", capture):
                self.assertTrue(session.command(command).generated[0].data)
            self.assertEqual(session.files_created, 2 + sum(created))
            session.budget.limits = replace(session.budget.limits, created_files=session.files_created)
            launches = session.budget.runs
            with self.assertRaisesRegex(MakeProbeError, "dependency directory-creation budget exhausted"):
                session.command(command)
            self.assertEqual(session.budget.runs, launches)
        self.assert_clean(session)

    def test_parked_make_and_driver_cannot_spend_the_same_live_capacity(self):
        command = self.dependency()
        event = "mkdir -p out && " + shlex.join(command.argv) + " > out/query.d"
        self.fixture.add("Makefile", (
            "include out/query.d\nout/query.d: src/query.c\n\t@" + event
            + "\nfuture/generated.h: ;\nquery.o: ;\n"
        ))
        with self.fixture.session(seconds=45, processes=3) as session:
            self.assertTrue(session.command(command).generated[0].data)
            run, nested = session._sandbox_run, []
            def record(root, **kwargs):
                if kwargs.get("dependency") and session.parked_capsules:
                    nested.append([dict(item) for item in session.parked_capsules])
                return run(root, **kwargs)
            with patch.object(session, "_sandbox_run", record):
                with self.assertRaises(MakeProbeError):
                    session.make("query.o", commands={event: command})
            self.assertEqual(len(nested), 1)
            self.assertGreaterEqual(sum(item["processes"] for item in nested[0]), 2)
            self.assertLessEqual(session.live_process_peak, 3)
            self.assertFalse((session.tree / "out").exists())
        self.assert_clean(session)

    def test_incomplete_execution_receipt_and_cleanup_failure_are_terminal(self):
        command = self.dependency()
        with self.fixture.session(seconds=45) as session:
            read = session.budget.read_bytes
            def corrupt(path, category):
                data = read(path, category)
                if path.parent == session.base and path.name.startswith("report-"):
                    record = json.loads(data)
                    if "executed" in record:
                        record["executed"].pop()
                        data = json.dumps(record).encode()
                return data
            with patch.object(session.budget, "read_bytes", corrupt):
                with self.assertRaisesRegex(MakeProbeError, "actual driver/cc1 execution"):
                    session.command(command)
        self.assert_clean(session)
        remove, failed = make_probe._remove_owned_tree, []
        def fail_once(path):
            if Path(path).name.startswith("command-root-") and not failed:
                failed.append(path)
                raise PermissionError("dependency cleanup sentinel")
            return remove(path)
        with self.fixture.session(seconds=45) as session:
            with patch.object(make_probe, "_remove_owned_tree", fail_once):
                with self.assertRaisesRegex(PermissionError, "dependency cleanup sentinel"):
                    session.command(command)
                self.assertTrue(failed)
        self.assert_clean(session)

    def test_escaped_dependency_names_are_decoded_only_by_real_make(self):
        command = self.dependency()
        self.fixture.add("quote/two words.h", b"/* raw comment \xff */\n")
        self.fixture.add("src/query.c", '#include "two words.h"\n')
        command = replace(command, code=("quote/two words.h",))
        expected = self.ordinary(command)
        self.assertIn(b"quote/two\\ words.h", expected)
        event = "mkdir -p out && " + shlex.join(command.argv) + " > out/query.d"
        self.fixture.add("Makefile", (
            "include out/query.d\nout/query.d: src/query.c\n\t@" + event
            + "\nquery.o:\n\t@printf '%s\\n' '$+' '$(MAKE_RESTARTS)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "query.o"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        ).stdout.decode("utf-8").splitlines()
        (self.root / "out/query.d").unlink()
        (self.root / "out").rmdir()
        with self.fixture.session(seconds=45) as session:
            result = session.make("query.o", variables=("MAKE_RESTARTS",), commands={event: command})
            prerequisites = [item["name"] for item in result.semantics["files"][0]["prerequisites"]]
            self.assertEqual(prerequisites, ["src/query.c", "quote/two words.h"])
            self.assertEqual(" ".join(prerequisites), ordinary[0])
            self.assertEqual(result.semantics["domains"]["MAKE_RESTARTS"]["value"], ordinary[1])
            self.assertEqual(ordinary[1], "1")
            record, = result.semantics["dynamic_commands"]
            self.assertEqual(record["generated_outputs"][0][2], hashlib.sha256(expected).hexdigest())
        self.assert_clean(session)

    def test_live_header_and_dependency_publication_use_one_authentic_make(self):
        command = self.dependency()
        self.fixture.add("header.in", "#define GENERATED 1\n")
        self.fixture.add("writer.py", (
            "from pathlib import Path\nimport sys\n"
            "destination=Path(sys.argv[2]); destination.parent.mkdir(parents=True,exist_ok=True)\n"
            "destination.write_bytes(Path(sys.argv[1]).read_bytes())\n"
        ))
        command = replace(command, code=(*command.code, "quote/future/generated.h"))
        dependency_event = "mkdir -p out && " + shlex.join(command.argv) + " > out/query.d"
        header_event = "python3 writer.py header.in quote/future/generated.h"
        self.fixture.add("Makefile", (
            "ifeq ($(wildcard quote/future/generated.h),)\n"
            "HEADER := $(shell " + header_event + ")\nendif\n"
            "include out/query.d\nout/query.d: src/query.c quote/future/generated.h\n\t@"
            + dependency_event + "\n"
            "query.o:\n\t@printf '%s\\n' '$+' '$(MAKEFILE_LIST)' '$(MAKE_RESTARTS)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "-f", "Makefile", "query.o"], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, check=True, timeout=10,
        ).stdout.decode("utf-8").splitlines()
        expected = (self.root / "out/query.d").read_bytes()
        for name in ("out/query.d", "quote/future/generated.h"):
            (self.root / name).unlink()
        for name in ("out", "quote/future"):
            (self.root / name).rmdir()
        registrations = {
            dependency_event: command,
            header_event: Command(
                ("/usr/bin/python3", "/repo/writer.py", "header.in", "/work/quote/future/generated.h"),
                code=("writer.py",), sources=("header.in",), outputs=("quote/future/generated.h",),
            ),
        }
        with self.fixture.session(seconds=45) as session:
            run, calls, dependency_results = session._sandbox_run, [], []
            execute = session.command
            def record_capsule(root, **kwargs):
                calls.append((kwargs["mode"], kwargs.get("dependency")))
                return run(root, **kwargs)
            def record_command(value):
                result = execute(value)
                if value.dependency_only:
                    dependency_results.append(result)
                return result
            with patch.object(session, "_sandbox_run", record_capsule), patch.object(
                session, "command", record_command,
            ):
                result = session.make(
                    "query.o", variables=("MAKEFILE_LIST", "MAKE_RESTARTS"), commands=registrations,
                )
            self.assertEqual([item["name"] for item in result.semantics["files"][0]["prerequisites"]], ordinary[0].split())
            self.assertEqual(result.semantics["domains"]["MAKEFILE_LIST"]["value"], ordinary[1])
            self.assertEqual(result.semantics["domains"]["MAKE_RESTARTS"]["value"], ordinary[2])
            self.assertEqual(ordinary[2], "1")
            self.assertEqual(sum(mode == "make" for mode, _ in calls), 1)
            produced, = dependency_results
            self.assertEqual(produced.generated[0].data, expected)
            self.assertIn("quote/future/generated.h", produced.code_consumed)
            self.assertNotIn("quote/disabled.h", {item[0] for item in produced.input_identities})
            record, = [item for item in result.semantics["dynamic_commands"]
                       if item["command"].get("dependency_only")]
            self.assertEqual(record["command"]["inputs"], list(produced.input_identities))
            self.assertEqual(record["command"]["executed"], list(produced.executed))
            self.assertEqual(record["generated_outputs"][0][2], hashlib.sha256(expected).hexdigest())
            self.assertFalse((session.tree / "out").exists())
            self.assertFalse((session.tree / "quote/future").exists())
        self.assert_clean(session)


if __name__ == "__main__":
    unittest.main()
