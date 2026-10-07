import json
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from scripts.validation_ownership.authority import ENVIRONMENT
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.graph_commands import MakeCommands
from scripts.validation_ownership.make_probe import Command
from scripts.validation_ownership.producer_channel import ChannelError, validate_dispatch_context, validate_job_context
from scripts.validation_ownership.python_commands import text_generation_command
from scripts.validation_ownership.tests import test_content_publication as content_tests


ROOT = Path(__file__).resolve().parents[3]
TEXT_COMMAND = (
    "python3 scripts/texttools/textprocess.py texts/texts.txt texts/textdefs.txt "
    "src/msg_data.c include/constants/msg.h utf8"
)


class TextProducerTests(unittest.TestCase):
    def setUp(self):
        self.support = content_tests.ContentPublicationTests()
        self.support.setUp()
        self.fixture = self.support.fixture
        self.root = self.fixture.root
        for path in ("scripts/texttools/textprocess.py", "scripts/texttools/huffman.py"):
            self.fixture.add(path, (ROOT / path).read_bytes())
        self.fixture.add("texts/textdefs.txt", "[X] = 0\n[Y] = 1\n")
        self.fixture.add("texts/texts.txt", "#0001\n[X][Y][X]\n#0002\n[Y][X][Y]\n")

    def tearDown(self):
        self.support.tearDown()

    def oracle(self):
        output = self.fixture.directory / "ordinary.c"
        header = self.fixture.directory / "ordinary.h"
        budget = ProbeBudget()
        try:
            result = budget.run(
                ["/usr/bin/python3", "-B", "scripts/texttools/textprocess.py", "texts/texts.txt",
                 "texts/textdefs.txt", str(output), str(header), "utf8"],
                cwd=self.root, env=ENVIRONMENT,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = output.read_bytes()
            self.fixture.add("include/constants/msg.h", header.read_bytes())
            return expected
        finally:
            budget.close()
            self.assertFalse(budget.children)

    def command(self, session):
        return text_generation_command(
            session, "texts/texts.txt", "texts/textdefs.txt", "src/msg_data.c", "include/constants/msg.h", "utf8",
        )

    def contracts(self):
        registry = json.loads((ROOT / ".github/validation-ownership-make-dynamics.json").read_text())
        return {item["expression"]: item for item in registry["contracts"]
                if item["id"] == "legacy-text-dry-run-recipe"}

    def test_actual_text_header_equality_precedes_c_only_live_publication(self):
        expected = self.oracle()
        header = (self.root / "include/constants/msg.h").read_bytes()
        self.fixture.add("Makefile", "TEXT := $(shell " + TEXT_COMMAND + ")\nall: ;\n")
        with self.fixture.session() as session:
            effects = []
            with self.support.capture_effects(session, effects):
                result = session.make(
                    "all", assignments=(("environment", "TEXT_CONTEXT", "actual"),),
                    commands=MakeCommands(session, self.contracts()),
                )
            self.assertEqual([(item.path, item.data) for item in result.generated], [("src/msg_data.c", expected)])
            self.assertEqual([item["path"] for item in effects], ["src/msg_data.c"])
            self.assertEqual([item["effect"] for item in effects], ["created"])
            record, = result.semantics["dynamic_commands"]
            self.assertEqual(record["command"]["environment"]["TEXT_CONTEXT"], "actual")
            self.assertEqual(result.semantics["native_dispatches"][0]["environment"]["TEXT_CONTEXT"], "actual")
            self.assertFalse(result.semantics["native_dispatches"][0]["rebuilding_makefiles"])
            self.assertEqual((session.tree / "include/constants/msg.h").read_bytes(), header)
        self.fixture.assert_clean(session)

    def test_default_real_text_data_and_imports_match_ordinary_output(self):
        for path in ("texts/texts.txt", "texts/textdefs.txt"):
            self.fixture.add(path, (ROOT / path).read_bytes())
        expected = self.oracle()
        with self.fixture.session() as session:
            result = session.command(self.command(session))
            self.assertEqual([(item.path, item.data) for item in result.generated], [("src/msg_data.c", expected)])
            self.assertEqual(set(result.consumed),
                             {"texts/texts.txt", "texts/textdefs.txt", "include/constants/msg.h"})
            self.assertEqual(set(result.code_consumed),
                             {"scripts/texttools/textprocess.py", "scripts/texttools/huffman.py"})
        self.fixture.assert_clean(session)

    def test_text_prerequisite_uses_real_remake_role_and_recipe_environment(self):
        expected = self.oracle()
        self.fixture.add("ready.py", (
            "from pathlib import Path\n"
            "data=Path('/repo/src/msg_data.c').read_bytes()\n"
            "assert data.startswith(b'#include \"global.h\"')\n"
            "path=Path('/work/build/ready.mk');path.parent.mkdir(parents=True,exist_ok=True)\n"
            "path.write_text('# C-only producer component completed\\n')\n"
        ))
        self.fixture.add("Makefile", (
            ".DEFAULT_GOAL := all\nexport TEXT_CONTEXT = source-export\n"
            "src/msg_data.c:\n\t@" + TEXT_COMMAND + "\n"
            "build/ready.mk: src/msg_data.c\n\t@python3 ready.py\n"
            "include build/ready.mk\nall: ;\n"
        ))
        with self.fixture.session() as session:
            text_commands = MakeCommands(session, self.contracts())
            ready = Command(
                ("/usr/bin/python3", "/repo/ready.py"), code=("ready.py",),
                sources=("src/msg_data.c",), outputs=("build/ready.mk",),
            )
            class Commands:
                def __contains__(inner, command):
                    return command == "python3 ready.py" or command in text_commands
                def __getitem__(inner, command):
                    return ready if command == "python3 ready.py" else text_commands[command]
            observed = session.make("all", commands=Commands())
            generated = {item.path: item.data for item in observed.generated}
            self.assertEqual(generated["src/msg_data.c"], expected)
            self.assertEqual(set(generated), {"src/msg_data.c", "build/ready.mk"})
            contexts = observed.semantics["native_dispatches"]
            self.assertEqual(contexts[0]["environment"]["TEXT_CONTEXT"], "source-export")
            self.assertTrue(contexts[0]["rebuilding_makefiles"])
            self.assertEqual(contexts[0]["kind"], "value")
            self.assertEqual(contexts[0]["job"], {
                "sequence": contexts[0]["sequence"], "kind": "recipe",
                "target": "src/msg_data.c", "command_line": 1,
            })
            self.assertTrue(contexts[1]["rebuilding_makefiles"])
            self.assertFalse(session._live_dispatches)
            self.assertFalse(session._issued_dispatches)
            self.assertFalse(session._command_dispatches)
        self.fixture.assert_clean(session)

    def test_transitive_text_reads_are_exact_and_invalid_closures_reject(self):
        self.fixture.add("texts/texts.txt", '#include "one.txt"\n#include "nested/two.txt"\n')
        self.fixture.add("texts/one.txt", "#0001\n[X][Y][X]\n")
        self.fixture.add("texts/nested/two.txt", "#0002\n[Y][X][Y]\n")
        expected = self.oracle()
        with self.fixture.session() as session:
            command = self.command(session)
            self.assertEqual(set(command.sources), {
                "texts/texts.txt", "texts/one.txt", "texts/nested/two.txt",
                "texts/textdefs.txt", "include/constants/msg.h",
            })
            self.assertEqual(session.command(command).generated[0].data, expected)
        self.fixture.assert_clean(session)
        self.fixture.add("unused/extra.txt", "not consumed\n")
        for kind in ("under", "over"):
            with self.subTest(kind=kind), self.fixture.session() as session:
                command = self.command(session)
                sources = tuple(path for path in command.sources if path != "texts/one.txt") if kind == "under" else (
                    *command.sources, "unused/extra.txt",
                )
                changed = replace(command, sources=sources)
                session._private_install_command(changed, ("src/msg_data.c", "src/.msg_data.c.header-check"))
                with self.assertRaises(MakeProbeError):
                    session.command(changed)
            self.fixture.assert_clean(session)
        for content in (
            '#include "missing.txt"\n', '#include "../outside/missing.txt"\n',
            '#include "texts.txt"\n', '#include "nested/cycle.txt"\n',
            '#include "unterminated\n',
        ):
            with self.subTest(content=content):
                self.fixture.add("texts/texts.txt", content)
                self.fixture.add("texts/nested/cycle.txt", '#include "../texts.txt"\n')
                with self.fixture.session() as session:
                    before = session.budget.runs
                    with self.assertRaises(MakeProbeError):
                        self.command(session)
                    self.assertEqual(session.budget.runs, before)
                self.fixture.assert_clean(session)

    def test_mismatched_header_has_no_successful_public_c_receipt(self):
        self.oracle()
        self.fixture.add("include/constants/msg.h", (self.root / "include/constants/msg.h").read_bytes() + b"\n")
        self.fixture.add("Makefile", "TEXT := $(shell " + TEXT_COMMAND + ")\nall: ;\n")
        effects = []
        with self.fixture.session() as session:
            with self.support.capture_effects(session, effects):
                with self.assertRaises(MakeProbeError):
                    session.make("all", commands=MakeCommands(session, self.contracts()))
            self.assertFalse((session.tree / "src/msg_data.c").exists())
            self.assertEqual(effects, [])
        self.fixture.assert_clean(session)

    def test_native_replacements_preserve_actual_initial_mode_only_for_new_policy(self):
        self.fixture.add("writer.py", (
            "import os\nchanged='switch' in os.listdir('/repo')\n"
            "fd=os.open('/work/output.bin',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o644 if changed else 0o600)\n"
            "with os.fdopen(fd,'wb') as out: out.write(b'new' if changed else b'old')\n"
        ))
        self.fixture.add("marker.py", "open('/work/switch','wb').write(b'changed')\n")
        self.fixture.add("Makefile", (
            "ONE := $(shell python3 writer.py)\nSWITCH := $(shell python3 marker.py)\n"
            "TWO := $(shell python3 writer.py)\nTHREE := $(shell python3 writer.py)\nall: ;\n"
        ))
        for policy, modes in (
            ("if-content-changed", [0o600, 0o644, 0o644]),
            ("if-content-changed-preserve-mode", [0o600, 0o600, 0o600]),
        ):
            with self.subTest(policy=policy), self.fixture.session() as session:
                writer = Command(
                    ("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",),
                    directories=(".",), outputs=("output.bin",), publication_policy=policy,
                )
                marker = Command(("/usr/bin/python3", "/repo/marker.py"), code=("marker.py",), outputs=("switch",))
                effects = []
                with self.support.capture_effects(session, effects):
                    observed = session.make("all", commands={"python3 writer.py": writer, "python3 marker.py": marker})
                output = [item for item in effects if item["path"] == "output.bin"]
                self.assertEqual([item["effect"] for item in output], ["created", "replaced", "retained"])
                self.assertEqual([item["mode"] for item in output], modes)
                self.assertEqual(output[1]["identity"], output[2]["identity"])
                final = next(item for item in observed.generated if item.path == "output.bin")
                self.assertEqual((final.data, final.mode), (b"new", modes[-1]))
            self.fixture.assert_clean(session)

    def test_dispatch_environment_is_actual_and_does_not_leak_to_registration_helpers(self):
        self.fixture.add("writer.py", "import os\nprint(os.environ.get('CONTEXT_VALUE','absent'))\n")
        self.fixture.add("Makefile", (
            "ONE := $(shell python3 writer.py)\nall: ;\n"
        ))
        with self.fixture.session() as session:
            command = Command(("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",))
            session._native_context_command(command)
            helpers = []
            class Commands:
                def __contains__(inner, key):
                    return key == "python3 writer.py"
                def __getitem__(inner, key):
                    helpers.append(session.command(command).stdout)
                    return command
            first = session.make(
                "all", variables=("ONE",), commands=Commands(),
                assignments=(("environment", "CONTEXT_VALUE", "first"),),
            )
            second = session.make(
                "all", variables=("ONE",), commands=Commands(),
                assignments=(("environment", "CONTEXT_VALUE", "second"),),
            )
            self.assertEqual(helpers, [b"absent\n", b"absent\n"])
            self.assertEqual(first.semantics["domains"]["ONE"]["value"], "first")
            self.assertEqual(second.semantics["domains"]["ONE"]["value"], "second")
            self.assertEqual([
                observed.semantics["native_dispatches"][0]["environment"]["CONTEXT_VALUE"]
                for observed in (first, second)
            ], ["first", "second"])
            self.assertTrue(all(
                observed.semantics["native_dispatches"][0]["job"]["kind"] == "expansion"
                and observed.semantics["native_dispatches"][0]["job"]["target"] is None
                for observed in (first, second)
            ))
            self.assertEqual(session.command(command).stdout, b"absent\n")
        self.fixture.assert_clean(session)

    def test_dispatch_context_requires_exact_native_frame_fields(self):
        context = {
            "sequence": 1, "executable": "/usr/bin/python3", "arguments": ["python3", "writer.py"],
            "cwd": "/repo", "environment": {"VALUE": "actual"}, "rebuilding_makefiles": True,
        }
        self.assertEqual(validate_dispatch_context(context, context["arguments"]), context)
        for value in (
            {**context, "sequence": True}, {**context, "rebuilding_makefiles": 1},
            {**context, "environment": {"A=B": "x"}}, {**context, "extra": True},
            {**context, "arguments": ["other"]}, {**context, "environment": {"A": "\0"}},
        ):
            with self.assertRaises(ChannelError):
                validate_dispatch_context(value, context["arguments"])

    def test_actual_wire_context_mismatch_rejects_before_producer_registration(self):
        self.fixture.add("writer.py", "print('actual')\n")
        self.fixture.add("Makefile", "VALUE := $(shell python3 writer.py)\nall: ;\n")
        for mutation in ("missing", "arguments", "rebuilding"):
            with self.subTest(mutation=mutation), self.fixture.session() as session:
                registrations = []
                class Commands:
                    def __contains__(inner, command):
                        return command == "python3 writer.py"
                    def __getitem__(inner, command):
                        registrations.append(command)
                        return Command(("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",))
                run = session.budget.run
                def intercept(argv, **options):
                    handler = options.get("producer_handler")
                    if handler is not None:
                        def changed(packet):
                            request = json.loads(packet)
                            if request.get("kind") == "request":
                                if mutation == "missing":
                                    del request["dispatch"]
                                elif mutation == "arguments":
                                    request["dispatch"]["arguments"] = ["unrelated"]
                                else:
                                    request["dispatch"]["rebuilding_makefiles"] = 1
                            return handler(json.dumps(request, separators=(",", ":")).encode())
                        options["producer_handler"] = changed
                    return run(argv, **options)
                with patch.object(session.budget, "run", intercept):
                    with self.assertRaises(MakeProbeError):
                        session.make("all", commands=Commands())
                self.assertEqual(registrations, [])
            self.fixture.assert_clean(session)

    def test_copied_context_command_record_cannot_receive_native_environment(self):
        self.fixture.add("writer.py", "import os\nprint(os.environ.get('CONTEXT_VALUE','absent'))\n")
        self.fixture.add("Makefile", "VALUE := $(shell python3 writer.py)\nall: ;\n")
        with self.fixture.session() as session:
            class Commands:
                def __contains__(inner, command):
                    return command == "python3 writer.py"
                def __getitem__(inner, command):
                    registered = session._native_context_command(
                        Command(("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",)),
                    )
                    record = session._native_context_commands[id(registered)]
                    session._native_context_commands[id(registered)] = replace(record)
                    return registered
            with self.assertRaisesRegex(MakeProbeError, "binding|view"):
                session.make(
                    "all", commands=Commands(),
                    assignments=(("environment", "CONTEXT_VALUE", "native"),),
                )
        self.fixture.assert_clean(session)
        self.assertFalse(session._issued_context_commands)

    def test_native_job_target_and_ordinal_do_not_come_from_identical_argv(self):
        for parallel in (False, True):
            self.fixture.add("Makefile", (
                ("MAKEFLAGS += -j2\n" if parallel else "")
                + "all: one two\none two:\n\t@printf '%s\\n' same\n\t@printf '%s\\n' same\n"
            ))
            with self.subTest(parallel=parallel), self.fixture.session() as session:
                observed = session.make("all")
                contexts = observed.semantics["native_dispatches"]
                self.assertEqual(len(contexts), 4)
                self.assertEqual({tuple(item["arguments"]) for item in contexts}, {("printf", "%s\\n", "same")})
                self.assertEqual(
                    sorted((item["job"]["target"], item["job"]["command_line"]) for item in contexts),
                    [("one", 1), ("one", 2), ("two", 1), ("two", 2)],
                )
                self.assertTrue(all(item["job"]["kind"] == "recipe" for item in contexts))
            self.fixture.assert_clean(session)

    def test_job_context_schema_and_actual_wire_mismatch_reject(self):
        good = {"sequence": 1, "kind": "recipe", "target": "actual", "command_line": 1}
        self.assertEqual(validate_job_context(good, 1), good)
        for bad in (
            {**good, "sequence": True}, {**good, "sequence": 2},
            {**good, "kind": []}, {**good, "kind": "expansion"},
            {**good, "target": None}, {**good, "command_line": True},
            {**good, "command_line": -1}, {**good, "extra": 1},
        ):
            with self.assertRaises(ChannelError):
                validate_job_context(bad, 1)
        self.fixture.add("writer.py", "print('actual')\n")
        self.fixture.add("Makefile", "VALUE := $(shell python3 writer.py)\nall: ;\n")
        with self.fixture.session() as session:
            run = session.budget.run
            def intercept(argv, **options):
                handler = options.get("producer_handler")
                if handler is not None:
                    def changed(packet):
                        request = json.loads(packet)
                        if request.get("kind") == "request":
                            request["job"]["sequence"] += 1
                        return handler(json.dumps(request, separators=(",", ":")).encode())
                    options["producer_handler"] = changed
                return run(argv, **options)
            with patch.object(session.budget, "run", intercept):
                with self.assertRaises(MakeProbeError):
                    session.make("all", commands={"python3 writer.py": Command(
                        ("/usr/bin/python3", "/repo/writer.py"), code=("writer.py",),
                    )})
        self.fixture.assert_clean(session)

    def test_candidate_cannot_emit_a_native_job_context_notification(self):
        body = (
            "import ctypes,os\n"
            "record=(ctypes.c_uint64*3)(os.getpid(),0,0)\n"
            "ctypes.CDLL(None).syscall(39,ctypes.c_uint64(0x564f4d4b00000007),"
            "ctypes.byref(record),24)\n"
        )
        with self.fixture.session() as session:
            with self.assertRaisesRegex(MakeProbeError, "unauthenticated"):
                session.command(Command(("/usr/bin/python3", "-c", body)))
        self.fixture.assert_clean(session)


if __name__ == "__main__":
    unittest.main()
