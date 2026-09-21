from __future__ import annotations

import hashlib
import json
import builtins
from contextlib import contextmanager
from dataclasses import replace
import fnmatch
import io
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
from threading import get_ident
from types import SimpleNamespace
import unittest
from unittest import mock
import weakref

from scripts.bash_parser import (
    BashToken, normalize_bash_script_commands, parse_bash_script_commands, tokenize_bash_command,
)
from scripts.validation_ownership.authority import (
    AuthorityLoader, ENVIRONMENT, GitTreeEntries, GitTreeEntry, encoded, git_tree_entries,
)
from scripts.validation_ownership.budget import MakeProbeError, ProbeBudget
from scripts.validation_ownership.graph_commands import (
    CODE_PREFIXES, FIND_DIRECTORY_BODY, ROOT_RUNTIME_FILES, MakeCommands,
    asset_discovery_command, python_command,
)
from scripts.validation_ownership import make_probe
from scripts.validation_ownership import graph_commands
from scripts.validation_ownership.make_probe import Command, ProbeSession
from scripts.validation_ownership.graph_probe import run_probe


ROOT = Path(__file__).resolve().parents[3]


class CommandSemanticsTests(unittest.TestCase):
    """Inert API compositions; no session setup, subprocess or native execution."""

    def session(self):
        session = object.__new__(ProbeSession)
        session.base, session.tree = Path("/model"), Path("/model/repo")
        session.owner_thread = get_ident()
        session.snapshot = SimpleNamespace(files={}, digest="model-snapshot")
        session.budget = SimpleNamespace(remaining=lambda: None, charge=lambda *args: None)
        session._namespace_epoch = 1
        session._native_context_commands = {}
        session._issued_context_commands = weakref.WeakSet()
        session._live_dispatches, session._command_dispatches = [], []
        session._issued_dispatches = weakref.WeakSet()
        session.published_sources = {}
        session._toolchain = SimpleNamespace(commands={}, require_step=lambda command: None)
        session._header_runtime_kind = lambda command: None
        session._directories = session._output_paths = lambda paths: tuple(paths)
        session.source_owners = lambda paths: ()
        session._metadata_matches = lambda metadata: metadata == ()
        return session

    def commands(self, session, command, *, contract=None):
        class Patterns:
            def __init__(self, budget, patterns):
                self.patterns = patterns

            def fullmatch(self, value):
                return tuple(index for index, pattern in enumerate(self.patterns)
                             if re.fullmatch(pattern, value, re.DOTALL))

        contract = contract or {
            "id": "generic-semantics-fixture", "command_regex": re.escape(command), "input_files": [],
        }
        with mock.patch.object(graph_commands, "CommandPatterns", Patterns):
            return MakeCommands(session, {"fixture": contract})

    @contextmanager
    def dispatch(self, session, environment):
        context = make_probe._LiveDispatch(
            "modeled-native-dispatch", 1, ("/bin/sh", "-c", "modeled"),
            "/repo", tuple(sorted(environment.items())), False, ("expansion", None, None),
            session.snapshot, session.tree, session._namespace_epoch,
        )
        session._issued_dispatches.add(context)
        session._live_dispatches.append(context)
        try:
            yield context
        finally:
            session._live_dispatches.pop()
            session._issued_dispatches.discard(context)

    @contextmanager
    def consuming(self, session, command, context):
        session._command_dispatches.append((command, context))
        try:
            yield session._command_environment(command)
        finally:
            session._command_dispatches.pop()

    def execute(self, command, environment, *, redirects=()):
        sinks = {"stdout": bytearray(), "stderr": bytearray(), "null": bytearray()}
        descriptors, operations = {1: "stdout", 2: "stderr"}, []

        def dup2(source, destination):
            self.assertEqual((source, destination), (1, 2))
            descriptors[destination] = descriptors[source]
            operations.append(("dup2", source, destination))

        def write(descriptor, data):
            operations.append(("write", descriptor, bytes(data)))
            if descriptors[descriptor] != "null":
                sinks[descriptors[descriptor]].extend(data)
            return len(data)

        class Stream:
            def __init__(self, descriptor):
                self.descriptor, self.pending = descriptor, bytearray()

            def write(self, value):
                self.pending.extend(value.encode())
                return len(value)

            def flush(self):
                if self.pending:
                    write(self.descriptor, self.pending)
                    self.pending.clear()

        model_sys = SimpleNamespace(stdout=Stream(1), stderr=Stream(2), argv=[], path=[])
        model_os = SimpleNamespace(environ=dict(environment), dup2=dup2, write=write)
        modules = {"sys": model_sys, "os": model_os, "io": io, "json": json}

        def importer(name, *args, **kwargs):
            if name not in modules:
                raise AssertionError("unmodeled producer import: " + name)
            return modules[name]

        def print_value(*values, sep=" ", end="\n", file=None, flush=False):
            stream = model_sys.stdout if file is None else file
            stream.write(sep.join(str(value) for value in values) + end)
            if flush:
                stream.flush()

        for redirect in redirects:
            if redirect == "stdout":
                dup2(1, 2)
            else:
                self.assertEqual(redirect, "null")
                descriptors[2] = "null"
        namespace = {"__builtins__": {
            "__import__": importer, "print": print_value, "exec": builtins.exec,
            "str": str, "bytes": bytes,
        }}
        exec(command.argv[command.argv.index("-c") + 1], namespace)
        model_sys.stdout.flush()
        model_sys.stderr.flush()
        return bytes(sinks["stdout"]), bytes(sinks["stderr"]), operations

    def test_generic_adapter_uses_each_issued_environment_and_keeps_helpers_canonical(self):
        program = (
            "import json,os;print(json.dumps({name:os.environ.get(name,'absent') "
            "for name in ('SWITCH','FE8_ITEM_ID_CAP','SOURCE_DATE_EPOCH')}))"
        )
        command = "python3 -c " + shlex.quote(program)
        session = self.session()
        commands = self.commands(session, command)
        for value in ("first", "second", "first"):
            original = {**ENVIRONMENT, "SWITCH": value, "FE8_ITEM_ID_CAP": "271",
                        "SOURCE_DATE_EPOCH": "123", "PYTHON": "python3"}
            with self.dispatch(session, original) as context:
                registered = commands[command]
                with self.consuming(session, registered, context) as environment:
                    self.assertEqual(environment, original)
                    self.assertEqual(json.loads(self.execute(registered, environment)[0]), {
                        "SWITCH": value, "FE8_ITEM_ID_CAP": "271", "SOURCE_DATE_EPOCH": "123",
                    })
                canonical = session._command_environment(registered)
                self.assertEqual(json.loads(self.execute(registered, canonical)[0]), {
                    "SWITCH": "absent", "FE8_ITEM_ID_CAP": "absent", "SOURCE_DATE_EPOCH": "0",
                })
        plain = python_command(session, program)
        with self.dispatch(session, original) as context, self.consuming(session, plain, context) as environment:
            self.assertNotIn("SWITCH", environment)

    def test_generic_registration_rebinds_source_epoch_but_not_an_old_handle(self):
        command = "python3 -c 'print(1)'"
        session = self.session()
        commands = self.commands(session, command)
        with self.dispatch(session, ENVIRONMENT) as context:
            previous = commands[command]
            with self.consuming(session, previous, context) as environment:
                self.assertEqual(environment, ENVIRONMENT)
        session.snapshot = SimpleNamespace(files={}, digest="second-model-snapshot")
        session.tree, session._namespace_epoch = Path("/model/other"), 2
        with self.dispatch(session, ENVIRONMENT) as context:
            current = commands[command]
            with self.consuming(session, current, context) as environment:
                self.assertEqual(environment, ENVIRONMENT)
            with self.assertRaises(MakeProbeError), self.consuming(session, previous, context):
                pass

    def test_real_command_cache_boundary_includes_the_effective_environment(self):
        class CacheMiss(Exception):
            pass

        class Cache(dict):
            def __contains__(self, key):
                self.last_key = key
                if not super().__contains__(key):
                    raise CacheMiss
                return True

        command = "python3 -c 'print(1)'"
        session = self.session()
        session.cache = Cache()
        commands = self.commands(session, command)
        keys = []
        for value in ("first", "second"):
            original = {**ENVIRONMENT, "SWITCH": value}
            with self.dispatch(session, original) as context:
                registered = commands[command]
                with self.consuming(session, registered, context):
                    with self.assertRaises(CacheMiss):
                        session._command(registered)
                    key = session.cache.last_key
                    keys.append(key)
                    result = SimpleNamespace(metadata=(), stdout=value.encode())
                    session.cache[key] = [result]
                    self.assertIs(session._command(registered), result)
        self.assertNotEqual(keys[0], keys[1])
        session.snapshot = SimpleNamespace(files={}, digest="different-source-snapshot")
        session._namespace_epoch += 1
        with self.dispatch(session, original) as context:
            registered = commands[command]
            with self.consuming(session, registered, context), self.assertRaises(CacheMiss):
                session._command(registered)
            self.assertNotEqual(session.cache.last_key, keys[-1])

    def test_generic_startup_controls_reject_even_after_a_cached_registration(self):
        command = "python3 -c 'print(1)'"
        for name, value in (
            ("LD_PRELOAD", "/foreign.so"), ("LD_LIBRARY_PATH", "/foreign"),
            ("MALLOC_TRACE", "/foreign"), ("GLIBC_TUNABLES", "foreign"),
            ("GCONV_PATH", "/foreign"), ("LOCPATH", "/foreign"), ("NLSPATH", "/foreign"),
            ("BASH_ENV", "/foreign"), ("ENV", "/foreign"), ("SHELLOPTS", "xtrace"),
            ("BASHOPTS", "expand_aliases"), ("BASH_FUNC_foreign%%", "() { :; }"),
            ("PYTHONPATH", "/foreign"), ("PYTHONHOME", "/foreign"),
            ("PYTHONSTARTUP", "/foreign"), ("PYTHONUNBUFFERED", "1"),
            ("PYTHONDONTWRITEBYTECODE", "0"), ("PATH", "/foreign"),
        ):
            with self.subTest(name=name):
                session = self.session()
                commands = self.commands(session, command)
                commands[command]
                with self.dispatch(session, {**ENVIRONMENT, name: value}), self.assertRaises(MakeProbeError):
                    commands[command]

    def test_generic_redirects_apply_before_real_two_stream_writes(self):
        program = (
            "import os,sys;print('err-first',file=sys.stderr,flush=True);"
            "print('out',flush=True);os.write(2,b'raw-err\\n');print('last',flush=True)"
        )
        prefix = "python3 -c " + shlex.quote(program)
        for suffix, expected, count in (
            ("", (b"out\nlast\n", b"err-first\nraw-err\n"), 0),
            (" 2>&1", (b"err-first\nout\nraw-err\nlast\n", b""), 1),
            (" 2>&1 2>&1", (b"err-first\nout\nraw-err\nlast\n", b""), 2),
        ):
            command = prefix + suffix
            session = self.session()
            commands = self.commands(session, command)
            for _ in range(2):
                stdout, stderr, operations = self.execute(commands[command], ENVIRONMENT)
                with self.subTest(suffix=suffix):
                    self.assertEqual((stdout, stderr), expected)
                    self.assertEqual(operations[:count], [("dup2", 1, 2)] * count)
        for suffix, redirects, expected in (
            (" 2>/dev/null", ("null",), (b"out\nlast\n", b"")),
            (" 2>&1 2>/dev/null", ("stdout", "null"), (b"out\nlast\n", b"")),
            (" 2>/dev/null 2>&1", ("null", "stdout"), (b"err-first\nout\nraw-err\nlast\n", b"")),
        ):
            session = self.session()
            ordinary = python_command(session, program)
            self.assertEqual(self.execute(ordinary, ENVIRONMENT, redirects=redirects)[:2], expected)
            with self.subTest(suffix=suffix), self.assertRaisesRegex(MakeProbeError, "stderr discard"):
                self.commands(session, prefix + suffix)[prefix + suffix]

    def test_literal_words_preserve_quotes_escapes_and_reject_active_roles(self):
        for raw, value in (
            ("'*.txt'", "*.txt"), ('"*.txt"', "*.txt"), (r"\*.txt", "*.txt"),
            ("'*'.txt", "*.txt"), ("''", ""), ("'a b'", "a b"),
            ("'$NAME'", "$NAME"), (r"\$NAME", "$NAME"), (r'"\$NAME"', "$NAME"),
            (r'"\`name\`"', "`name`"), (r'"a\q"', r"a\q"), (r"a\ b", "a b"),
            ("'a'\"b\"c", "abc"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(graph_commands._simple_words(tokenize_bash_command(raw), "fixture"), [value])
        for raw in ("*.txt", "?.txt", "[ab].txt", "$NAME", '"$NAME"', '"${NAME}"',
                    '"$(printf value)"', "`value`", "~", "{a,b}", "'literal'$NAME"):
            with self.subTest(raw=raw), self.assertRaises(MakeProbeError):
                graph_commands._simple_words(tokenize_bash_command(raw), "fixture")
        for command in ("find texts -name '*.txt", 'find texts -name "*.txt', "find texts -name \\"):
            with self.assertRaises(MakeProbeError):
                graph_commands._shell_tokens(command, "fixture")
        with self.assertRaises(MakeProbeError):
            graph_commands._simple_words((BashToken("*.txt", False),), "fixture")

    def test_assignment_values_keep_literal_roles(self):
        program = 'import os;print(os.environ["FE8_ITEM_ID_CAP"])'
        for value in ("'$CAP'", r"\$CAP", r'"\$CAP"'):
            command = "FE8_ITEM_ID_CAP=" + value + " python3 -c " + shlex.quote(program)
            session = self.session()
            registered = self.commands(session, command)[command]
            self.assertEqual(self.execute(registered, ENVIRONMENT)[:2], (b"$CAP\n", b""))
        for value in ("$CAP", '"$CAP"', "`value`"):
            command = "FE8_ITEM_ID_CAP=" + value + " python3 -c " + shlex.quote(program)
            with self.assertRaises(MakeProbeError):
                self.commands(self.session(), command)[command]
        session = self.session()
        for cap in ("271", "512"):
            command = f"FE8_ITEM_ID_CAP={cap} python3 -c " + shlex.quote(program)
            with self.dispatch(session, {**ENVIRONMENT, "FE8_ITEM_ID_CAP": "original"}) as context:
                registered = self.commands(session, command)[command]
                with self.consuming(session, registered, context) as environment:
                    self.assertEqual(environment["FE8_ITEM_ID_CAP"], "original")
                    self.assertEqual(self.execute(registered, environment)[:2], ((cap + "\n").encode(), b""))

    def test_find_rejects_unproved_globs_for_zero_one_and_multiple_cwd_matches(self):
        contracts = json.loads((ROOT / ".github/validation-ownership-make-dynamics.json").read_bytes())["contracts"]
        for identity, root, extension, tail in (
            ("legacy-text-source-discovery", "texts", "txt", ""),
            ("asset-tool-source-discovery", "scripts/assets", "py", " -print"),
        ):
            contract = next(item for item in contracts if item["id"] == identity)
            pattern = "*." + extension
            for cwd in ((), ("one." + extension,), ("one." + extension, "two." + extension)):
                session = self.session()
                session.snapshot.files = {path: b"" for path in (
                    *cwd, root + "/one." + extension, root + "/two." + extension, root + "/skip.bin",
                )}
                expanded = [path for path in cwd if fnmatch.fnmatchcase(path, pattern)] or [pattern]
                ordinary = None if len(expanded) != 1 else [
                    name for name in ("one." + extension, "two." + extension)
                    if fnmatch.fnmatchcase(name, expanded[0])
                ]
                self.assertEqual(ordinary, {
                    0: ["one." + extension, "two." + extension],
                    1: ["one." + extension], 2: None,
                }[len(cwd)])
                command = f"find {root} -type f -name {pattern}{tail}"
                commands = self.commands(session, command, contract=contract)
                with self.subTest(identity=identity, cwd=cwd), self.assertRaises(MakeProbeError):
                    commands[command]
                for literal in ("'" + pattern + "'", '"' + pattern + '"'):
                    safe = f"find {root} -type f -name {literal}{tail}"
                    registered = commands[safe]
                    self.assertEqual(registered.argv[-2:], (root, pattern))
                    self.assertEqual(set(registered.sources), {
                        root + "/one." + extension, root + "/two." + extension, root + "/skip.bin",
                    })
                escaped = f"find {root} -type f -name \\{pattern}{tail}"
                self.assertEqual(
                    self.commands(session, escaped, contract={
                        **contract, "command_regex": re.escape(escaped),
                    })[escaped].argv[-2:], (root, pattern),
                )


class GraphCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "build/test-artifacts/ownership-domain-tests" / secrets.token_hex(12)
        self.root = self.directory / "repo"
        self.root.mkdir(parents=True)
        self.entries = {}
        self.contracts = {
            entry["expression"]: entry
            for entry in json.loads((ROOT / ".github/validation-ownership-make-dynamics.json").read_bytes())["contracts"]
        }
        for path in (ROOT / "tools/scaninc").iterdir():
            if path.suffix in {".h", ".cpp"}:
                self.add(path.relative_to(ROOT).as_posix(), path.read_bytes())
        self.add("scripts/validation_ownership/scaninc_sources.cpp",
                 (ROOT / "scripts/validation_ownership/scaninc_sources.cpp").read_bytes())

    def tearDown(self):
        shutil.rmtree(self.directory)

    def add(self, name, data, mode="100644"):
        if isinstance(data, str):
            data = data.encode()
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o755 if mode == "100755" else 0o644)
        self.entries[name] = GitTreeEntry(name, mode, "blob", hashlib.sha1(data).hexdigest())

    def session(self, *, runtime_files=()):
        budget = ProbeBudget()
        loader = AuthorityLoader(self.root, GitTreeEntries(self.entries, budget=budget), budget=budget)
        return ProbeSession(
            loader, scratch_root=self.root / "build/scratch", budget=budget,
            runtime_files=runtime_files,
        )

    def capture_loader(self, budget):
        def git(*arguments):
            return subprocess.run(
                ["/usr/bin/git", "-C", str(self.root), *arguments],
                env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                capture_output=True, check=True, timeout=15,
            ).stdout
        git("init", "--quiet")
        git("add", "-A", "--", ".")
        revision = git("write-tree").decode().strip()
        entries = git_tree_entries(self.root, revision, budget=budget)
        self.assertEqual(set(entries), set(self.entries))
        return AuthorityLoader(self.root, entries, revision, budget=budget)

    def ordinary_scaninc(self, source):
        output = self.directory / "scaninc"
        environment = {**ENVIRONMENT, "TMPDIR": str(self.directory)}
        built = subprocess.run(
            ["/usr/bin/g++", "-std=c++11", "-O2", "-Wall", "-Wextra", "-Werror",
             *[str(self.root / path) for path in sorted(self.entries)
               if path.startswith("tools/scaninc/") and path.endswith(".cpp")],
             "-o", str(output)],
            cwd=self.root, env=environment, capture_output=True, timeout=30,
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        return subprocess.run(
            [str(output), "-I", "include", "-I", "", source],
            cwd=self.root, env=environment, capture_output=True, check=True, timeout=10,
        ).stdout

    def add_repo_file(self, path):
        source = ROOT / path
        mode = "100755" if source.stat().st_mode & 0o111 else "100644"
        self.add(path, source.read_bytes(), mode)

    def add_generated_dependency_fixture(self):
        if (ROOT / "scripts/__init__.py").is_file():
            self.add_repo_file("scripts/__init__.py")
        for path in (ROOT / "scripts" / "generated_data").rglob("*.py"):
            if "tests" not in path.relative_to(ROOT).parts:
                self.add_repo_file(path.relative_to(ROOT).as_posix())
        for name in ("scripts/assets/__init__.py", "scripts/assets/tmx.py"):
            if (ROOT / name).is_file():
                self.add_repo_file(name)

        fixture_root = ROOT / "scripts" / "generated_data" / "tests" / "fixtures"
        chapterbundle = fixture_root / "chapterbundle"
        self.add("include/constants/characters.h", "#define CHARACTER_EIRIKA 1\n")
        self.add("include/constants/event-flags.h", "#define EVFLAG_TMP(n) (n)\n")
        self.add("include/bmunit.h", "#define FACTION_ID_BLUE 0\n")
        self.add("include/constants/chapters.h", (chapterbundle / "chapters.h").read_bytes())
        self.add("src/data/chapter_settings.json", (chapterbundle / "chapter_settings.json").read_bytes())
        self.add("src/data/data_8B363C.c", (chapterbundle / "data_8B363C.c").read_bytes())
        self.add("assets/manifest.json", "{}\n")
        self.add("assets/tmx/Example.tmx", "<map width='1' height='1'/>\n")
        self.add("assets/tmx/placeholder.txt", "keep\n")
        self.add("graphics/map/layout/Example.json", "{}\n")

        for name in (
            "deps_units.json",
            "deps_shops.json",
            "deps_traps.json",
            "deps_eventscripts.json",
            "deps_eventlists.json",
            "deps_supports.json",
        ):
            self.add("testdata/deps/" + name, (chapterbundle / name).read_bytes())
        self.add(
            "testdata/objectives/el_objectives.json",
            (chapterbundle / "deps_chapterobjectives.json").read_bytes(),
        )
        self.add(
            "testdata/strategies/el_strategies.json",
            (chapterbundle / "deps_autoplaystrategies.json").read_bytes(),
        )
        bundle = json.loads((chapterbundle / "valid.json").read_text(encoding="utf-8"))
        for table in bundle["tables"].values():
            table["source"] = "testdata/deps/" + Path(table["source"]).name
        bundle["supportOwners"]["source"] = "testdata/deps/deps_supports.json"
        self.add("testdata/bundles/el_bundle.json", json.dumps(bundle, indent=2) + "\n")
        return [
            {
                "name": "chapterobjectives",
                "command": (
                    'python3 -m scripts.generated_data.chapterobjectives.deps \\\n'
                    '\t--source "testdata/objectives" \\\n'
                    '\t--bundle-source "testdata/bundles" \\\n'
                    '\t--make-target "build/generated/data/data_chapter_objectives.c" \\\n'
                    '\t--depfile "build/generated/data/chapterobjectives.inputs.mk"'
                ),
                "make_command": (
                    'python3 -m scripts.generated_data.chapterobjectives.deps --source '
                    '"testdata/objectives" --bundle-source "testdata/bundles" '
                    '--make-target "build/generated/data/data_chapter_objectives.c" '
                    '--depfile "build/generated/data/chapterobjectives.inputs.mk"'
                ),
                "output": "build/generated/data/chapterobjectives.inputs.mk",
                "target": "build/generated/data/data_chapter_objectives.c",
            },
            {
                "name": "autoplaystrategies",
                "command": (
                    'python3 -m scripts.generated_data.autoplaystrategies.deps \\\n'
                    '\t--source "testdata/strategies/el_strategies.json" \\\n'
                    '\t--objectives-source "testdata/objectives/el_objectives.json" \\\n'
                    '\t--bundle-source "testdata/bundles/el_bundle.json" \\\n'
                    '\t--make-target "build/generated/data/data_autoplay_strategies.c" \\\n'
                    '\t--depfile "build/generated/data/autoplaystrategies.inputs.mk"'
                ),
                "reordered": (
                    'python3 -m scripts.generated_data.autoplaystrategies.deps \\\n'
                    '\t--bundle-source "testdata/bundles/el_bundle.json" \\\n'
                    '\t--make-target "build/generated/data/data_autoplay_strategies.c" \\\n'
                    '\t--source "testdata/strategies/el_strategies.json" \\\n'
                    '\t--objectives-source "testdata/objectives/el_objectives.json" \\\n'
                    '\t--depfile "build/generated/data/autoplaystrategies.inputs.mk"'
                ),
                "output": "build/generated/data/autoplaystrategies.inputs.mk",
                "target": "build/generated/data/data_autoplay_strategies.c",
            },
            {
                "name": "eventlists",
                "command": (
                    'python3 -m scripts.generated_data.eventlists.deps \\\n'
                    '\t--strategy-source "testdata/strategies/el_strategies.json" \\\n'
                    '\t--bundle-source "testdata/bundles/el_bundle.json" \\\n'
                    '\t--make-target "build/generated/data/.ch2-eventlists.validated" \\\n'
                    '\t--depfile "build/generated/data/eventlists.inputs.mk"'
                ),
                "make_command": (
                    'python3 -m scripts.generated_data.eventlists.deps --strategy-source '
                    '"testdata/strategies/el_strategies.json" --bundle-source '
                    '"testdata/bundles/el_bundle.json" '
                    '--make-target "build/generated/data/.ch2-eventlists.validated" '
                    '--depfile "build/generated/data/eventlists.inputs.mk"'
                ),
                "output": "build/generated/data/eventlists.inputs.mk",
                "target": "build/generated/data/.ch2-eventlists.validated",
            },
        ]

    def rewrite_generated_dependency_bundle(self, *, units_source):
        bundle_path = self.root / "testdata/bundles/el_bundle.json"
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        bundle["tables"]["units"]["source"] = units_source
        self.add("testdata/bundles/el_bundle.json", json.dumps(bundle, indent=2) + "\n")

    def normalize_repo_bytes(self, data):
        return data.replace(os.fsencode(str(self.root)), b"/repo")

    def test_real_voice_source_and_native_make_agree_with_ordinary_scanner(self):
        source = "sound/voicegroups/voicegroup038.s"
        included = "asm/macros/music_voice.inc"
        for path in (source, included):
            self.add(path, (ROOT / path).read_bytes())
        command = f'tools/scaninc/scaninc -I include -I "" {source}'
        self.add("Makefile", f"INPUTS := $(shell {command})\nall: $(INPUTS)\n\t@:\n")
        ordinary = self.ordinary_scaninc(source)
        self.assertEqual(ordinary, (included + "\n").encode())
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            registered = commands[command]
            result = probe.command(registered)
            self.assertEqual(result.stdout, ordinary)
            self.assertEqual(result.consumed, tuple(sorted((source, included))))
            observed = probe.make("all", variables=("INPUTS",), commands=commands)
            self.assertEqual(observed.semantics["domains"]["INPUTS"]["value"], included)
            self.assertEqual(observed.semantics["files"][0]["prerequisites"], [
                {"name": included, "order_only": False},
            ])
            self.assertEqual(len(observed.semantics["dynamic_commands"]), 1)
            self.assertTrue(observed.semantics["dynamic_commands"][0]["command"]["native_tool"])
            self.assertTrue(all(event["match"] == 0 for event in observed.events))
        self.assertIsNone(probe.base)
        self.assertFalse(probe.runtime_tools)
        self.assertFalse(probe.runtime_query_profiles)
        self.assertFalse(probe.budget.children)

    def test_shell_tokens_keep_real_operators_separate_from_literal_arguments(self):
        program = '/usr/bin/python3 -I -S -B -c "import json,sys;print(json.dumps(sys.argv[1:]))"'
        for literal in ("'>'", '">"', r"\>", "'>'\"\"", r"''\>"):
            command = program + " " + literal + " /dev/null"
            actual = subprocess.run(
                ["/bin/sh", "-c", command], cwd=self.root, env=ENVIRONMENT,
                capture_output=True, check=True, timeout=15,
            )
            tokens = tokenize_bash_command(command)
            self.assertEqual(json.loads(actual.stdout), [">", "/dev/null"])
            self.assertEqual(tuple((token.value, token.operator) for token in tokens[-2:]),
                             ((">", False), ("/dev/null", False)))
            self.assertFalse(any(token.operator for token in tokens))
        for suffix in ("> /dev/null", "> '/dev/null'", '>"/dev/null"', ">''/dev/null"):
            command = program + " " + suffix + " # real comment"
            actual = subprocess.run(
                ["/bin/sh", "-c", command], cwd=self.root, env=ENVIRONMENT,
                capture_output=True, check=True, timeout=15,
            )
            self.assertEqual(actual.stdout, b"")
            self.assertEqual(tuple((token.value, token.operator) for token in tokenize_bash_command(command)[-2:]),
                             ((">", True), ("/dev/null", False)))
        for literal in ("'||'", "'&&'", "';'", "'('", "')'", "'<'", "'>>'"):
            command = program + " " + literal
            actual = subprocess.run(
                ["/bin/sh", "-c", command], cwd=self.root, env=ENVIRONMENT,
                capture_output=True, check=True, timeout=15,
            )
            tokens = tokenize_bash_command(command)
            self.assertEqual([tokens[-1].value], json.loads(actual.stdout))
            self.assertFalse(tokens[-1].operator)

    def shell_argv(self, command):
        return subprocess.run(
            ["/bin/sh", "-c", command], cwd=self.root, env=ENVIRONMENT,
            capture_output=True, timeout=15,
        )

    def generic_commands(self, probe, command, *, identity="generic-lexical-fixture", inputs=()):
        contract = {"id": identity, "command_regex": re.escape(command), "input_files": list(inputs)}
        return MakeCommands(probe, {"fixture": contract})

    def generic_registration(self, probe, command, **options):
        return self.generic_commands(probe, command, **options)[command]

    def test_live_generic_python_preserves_original_environment_and_cache_receipts(self):
        body = (
            "import json,os;print(json.dumps({name:os.environ.get(name,'absent') "
            "for name in ('SWITCH','FE8_ITEM_ID_CAP','SOURCE_DATE_EPOCH')},sort_keys=True))"
        )
        command = "python3 -c " + shlex.quote(body)
        self.add("Makefile", f"RESULT := $(shell {command})\n$(info $(RESULT))\nall: ;\n")
        with self.session() as probe:
            commands = self.generic_commands(probe, command)
            direct = probe.command(python_command(probe, body))
            self.assertEqual(json.loads(direct.stdout), {
                "SWITCH": "absent", "FE8_ITEM_ID_CAP": "absent", "SOURCE_DATE_EPOCH": "0",
            })
            results = []
            for value in ("first", "second", "first"):
                inputs = {"SWITCH": value, "FE8_ITEM_ID_CAP": "271", "SOURCE_DATE_EPOCH": "123"}
                ordinary = subprocess.run(
                    ["/usr/bin/make", "--no-print-directory", "-s", "-f", "Makefile", "all"],
                    cwd=self.root, env={**ENVIRONMENT, **inputs},
                    capture_output=True, check=True, timeout=15,
                )
                observed = probe.make(
                    "all", variables=("RESULT",), commands=commands,
                    assignments=tuple(("environment", name, content) for name, content in inputs.items()),
                )
                actual = json.loads(observed.semantics["domains"]["RESULT"]["value"])
                self.assertEqual(actual, inputs)
                self.assertEqual(actual, json.loads(ordinary.stdout))
                dispatch, = observed.semantics["native_dispatches"]
                receipt, = observed.semantics["dynamic_commands"]
                self.assertEqual(receipt["command"]["environment"], dispatch["environment"])
                self.assertEqual(receipt["output_sha256"], hashlib.sha256(ordinary.stdout).hexdigest())
                results.append(receipt["output_sha256"])
                self.assertEqual(probe.command(commands[command]).stdout, direct.stdout)
            self.assertNotEqual(results[0], results[1])
            self.assertEqual(results[0], results[2])
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)
        self.assertFalse(probe._issued_context_commands)

    def test_live_generic_script_and_module_keep_actual_recipe_exports(self):
        body = (
            "import json,os\n"
            "print(json.dumps({name:os.environ.get(name,'absent') "
            "for name in ('SWITCH','FE8_ITEM_ID_CAP','PYTHON')},sort_keys=True))\n"
        )
        self.add("fixture.py", body)
        for command in ("python3 fixture.py", "python3 -m fixture"):
            self.add("Makefile", (
                "export PYTHON := python3\nall: first second\n"
                "first: export SWITCH := first\nfirst: export FE8_ITEM_ID_CAP := 271\n"
                "second: export SWITCH := second\nsecond: export FE8_ITEM_ID_CAP := 512\n"
                f"first second:\n\t+@{command}\n"
            ))
            for supplied in ((), (("command-line", "SWITCH", "command-line"),)):
                with self.subTest(command=command, supplied=supplied):
                    ordinary = subprocess.run(
                        ["/usr/bin/make", "--no-print-directory", "-s", "-f", "Makefile", "all",
                         *(name + "=" + value for _, name, value in supplied)],
                        cwd=self.root, env=ENVIRONMENT, capture_output=True, check=True, timeout=15,
                    )
                    expected = [json.loads(line) for line in ordinary.stdout.splitlines()]
                    self.assertEqual(expected, [
                        {"SWITCH": "command-line" if supplied else "first",
                         "FE8_ITEM_ID_CAP": "271", "PYTHON": "python3"},
                        {"SWITCH": "command-line" if supplied else "second",
                         "FE8_ITEM_ID_CAP": "512", "PYTHON": "python3"},
                    ])
                    with self.session() as probe:
                        commands = self.generic_commands(probe, command, inputs=("fixture.py",))
                        observed = probe.make("all", commands=commands, assignments=supplied)
                        dispatches = observed.semantics["native_dispatches"]
                        self.assertEqual([item["job"]["target"] for item in dispatches], ["first", "second"])
                        receipts = observed.semantics["dynamic_commands"]
                        self.assertEqual(len(receipts), 2)
                        for dispatch, value in zip(dispatches, expected):
                            receipt, = [item for item in receipts
                                        if item["command"]["environment"] == dispatch["environment"]]
                            self.assertEqual({name: dispatch["environment"][name] for name in value}, value)
                            output = (json.dumps(value, sort_keys=True) + "\n").encode()
                            self.assertEqual(receipt["output_sha256"], hashlib.sha256(output).hexdigest())
                            self.assertEqual(receipt["command"]["inputs"], [
                                ("fixture.py", "100644", hashlib.sha256(body.encode()).hexdigest()),
                            ])
                    self.assertIsNone(probe.base)
                    self.assertFalse(probe.budget.children)

    def test_live_generic_registration_tracks_selected_source_views_and_restoration(self):
        command = "python3 fixture.py"
        self.add("Makefile", f"RESULT := $(shell {command})\nall: ;\n")
        old_body = "import os\nprint(os.environ['SWITCH']+':old')\n"
        new_body = "import os\nprint(os.environ['SWITCH']+':new')\n"
        self.add("fixture.py", old_body)
        budget = ProbeBudget()
        old_loader = self.capture_loader(budget)
        self.add("fixture.py", new_body)
        current_loader = self.capture_loader(budget)
        with ProbeSession(current_loader, scratch_root=self.root / "build/scratch", budget=budget) as probe:
            commands = self.generic_commands(probe, command, inputs=("fixture.py",))

            def observe(body, expected):
                result = probe.make(
                    "all", variables=("RESULT",), commands=commands,
                    assignments=(("environment", "SWITCH", "selected"),),
                )
                self.assertEqual(result.semantics["domains"]["RESULT"]["value"], expected)
                receipt, = result.semantics["dynamic_commands"]
                self.assertEqual(receipt["command"]["environment"]["SWITCH"], "selected")
                self.assertEqual(receipt["command"]["inputs"], [
                    ("fixture.py", "100644", hashlib.sha256(body.encode()).hexdigest()),
                ])
                self.assertEqual(receipt["output_sha256"], hashlib.sha256((expected + "\n").encode()).hexdigest())

            observe(new_body, "selected:new")
            with probe.select_view(old_loader):
                observe(old_body, "selected:old")
            observe(new_body, "selected:new")
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)
        self.assertFalse(probe._issued_context_commands)

    def test_live_generic_python_rejects_original_startup_controls(self):
        command = "python3 -c 'print(1)'"
        self.add("Makefile", f"RESULT := $(shell {command})\nall: ;\n")
        with self.session() as probe:
            commands = self.generic_commands(probe, command)
            commands[command]
            with mock.patch.object(probe, "command", side_effect=AssertionError("producer must not start")):
                with self.assertRaisesRegex(MakeProbeError, "unsupported startup controls"):
                    probe.make(
                        "all", commands=commands,
                        assignments=(("environment", "PYTHONPATH", "/unsupported-input"),),
                    )
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)

    def test_live_registered_python_stderr_merge_supplies_the_complete_make_value(self):
        for name in ("scripts/__init__.py", "scripts/generated_data/__init__.py",
                     "scripts/generated_data/chapterobjectives/__init__.py"):
            self.add(name, "")
        self.add("scripts/generated_data/chapterobjectives/enabled.py", (
            "import json,os,sys\n"
            "with open(sys.argv[sys.argv.index('--source')+1]) as source:\n"
            " selected=json.load(source)['selected']\n"
            "print('warn',file=sys.stderr,flush=True)\n"
            "print(selected,flush=True)\n"
            "os.write(2,b'tail\\n')\n"
        ))
        self.add("src/data/chapter_objectives.json", '{"selected":"selected"}\n')
        command = (
            'python3 -m scripts.generated_data.chapterobjectives.enabled '
            '--source "src/data/chapter_objectives.json" 2>&1'
        )
        contract = next(item for item in self.contracts.values()
                        if item["id"] == "generated-chapter-objectives-enablement")
        self.add("Makefile", (
            f"FIRST := $(shell {command})\nSECOND := $(shell {command})\n"
            "all: $(FIRST)\nwarn selected tail: ;\n"
        ))
        ordinary = self.shell_argv(command)
        self.assertEqual((ordinary.returncode, ordinary.stdout, ordinary.stderr),
                         (0, b"warn\nselected\ntail\n", b""))
        with self.session() as probe:
            commands = MakeCommands(probe, {contract["expression"]: contract})
            direct = probe.command(commands[command])
            self.assertEqual((direct.stdout, direct.stderr), (ordinary.stdout, ordinary.stderr))
            observed = probe.make("all", variables=("FIRST", "SECOND"), commands=commands)
            for name in ("FIRST", "SECOND"):
                self.assertEqual(observed.semantics["domains"][name]["value"], "warn selected tail")
            self.assertEqual([item["name"] for item in observed.semantics["files"][0]["prerequisites"]],
                             ["warn", "selected", "tail"])
            self.assertEqual(len(observed.events), 2)
            for receipt in observed.semantics["dynamic_commands"]:
                self.assertEqual(receipt["output_sha256"], hashlib.sha256(ordinary.stdout).hexdigest())
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)

    def test_shared_tokens_preserve_assignment_and_io_number_roles(self):
        for source, assigned in (
            ("FE8_ITEM_ID_CAP=271", True), ("FE8_ITEM_ID_CAP='1||true'", True),
            ("'FE8_ITEM_ID_CAP=271'", False), ('"FE8_ITEM_ID_CAP"=271', False),
            (r"FE8_ITEM_ID_CAP\=271", False), ("''FE8_ITEM_ID_CAP=271", False),
        ):
            with self.subTest(source=source):
                token, = tokenize_bash_command(source)
                self.assertIsInstance(token, BashToken)
                self.assertEqual(token.assignment, assigned)
                self.assertEqual(token.raw, source[token.start:token.end])
                self.assertFalse(token.operator)
        for source, is_descriptor in (("2>&1", True), ("2 > /dev/null", False),
                                      ("'2'>/dev/null", False), (r"\2>/dev/null", False)):
            with self.subTest(source=source):
                tokens = tokenize_bash_command(source)
                self.assertEqual(tokens[0].io_number, is_descriptor)
                self.assertTrue(tokens[1].operator)
                self.assertEqual(tokens[0].end == tokens[1].start, source != "2 > /dev/null")
        tokens = tokenize_bash_command("cc -DUNUSED=1||true input.c")
        self.assertEqual([(token.value, token.operator) for token in tokens], [
            ("cc", False), ("-DUNUSED=1", False), ("||", True), ("true", False), ("input.c", False),
        ])
        quoted = tokenize_bash_command("cc -DUNUSED='1||true' input.c")
        self.assertEqual([token.value for token in quoted], ["cc", "-DUNUSED=1||true", "input.c"])
        self.assertFalse(any(token.operator for token in quoted))

    def test_live_compiler_depfile_cannot_replace_the_invocation_primary_source(self):
        self.add("src/input.c", '#include "header.h"\nint fixture;\n')
        self.add("src/header.h", "#define FIXTURE 1\n")
        prefix = (
            "MODE ?= first\ninclude .dep/src/input.d\n.dep/src/input.d: src/input.c\n"
            "\tmkdir -p .dep/src/ && cc -E -nostdinc -undef src/input.c -MM -MG -MT src/input.o > .dep/src/input.d\n"
        )
        suffix = "all: $(MODE)\n\t@echo $(MODE)\nfirst second: ;\nsrc/input.o: ;\n"
        for mutation in (
            "MAKEFILE_LIST := .dep/src/input.d\n",
            "$(eval MAKEFILE_LIST := .dep/src/input.d)\n",
            "define HIDE\nMAKEFILE_LIST := .dep/src/input.d\nendef\n$(eval $(HIDE))\n",
        ):
            with self.subTest(mutation=mutation):
                self.add("Makefile", prefix + mutation + suffix)
                with self.session() as probe:
                    native = probe.make("all", makefile="Makefile", variables=("MAKEFILE_LIST", "MAKE_RESTARTS"),
                                        definitions=("MODE",), commands=MakeCommands(probe, self.contracts))
                    self.assertEqual(native.semantics["domains"]["MAKEFILE_LIST"]["value"], ".dep/src/input.d")
                    self.assertEqual(native.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
                    self.assertEqual(native.semantics["definitions"]["global"]["MODE"]["value"], "first")
                    self.assertEqual({resolved for resolved, _ in native.file_open_attempts},
                                     {"/repo/Makefile", "/repo/.dep/src/input.d"})
                    self.assertEqual(native.generated[0].data, b"src/input.o: src/input.c src/header.h\n")
                    self.assertTrue(any(
                        path.endswith("/cc1") for record in native.semantics["dynamic_commands"]
                        for path in record["command"].get("executed", ())
                    ))
                    with self.assertRaisesRegex(MakeProbeError, "source-read evidence"):
                        run_probe(probe.loader, {"all"}, {}, self.contracts, session=probe)
        self.add("Makefile", prefix + suffix)
        with self.session() as probe:
            with self.assertRaisesRegex(MakeProbeError, "unsealed external defaults"):
                run_probe(probe.loader, {"all"}, {}, self.contracts, session=probe)
        with self.session() as probe:
            result = run_probe(
                probe.loader, {"all"}, {"MODE": {"kind": "explicit", "values": ["first", "second"]}},
                self.contracts, session=probe, declared_external_names={"MODE"},
            )["all"]
        self.assertEqual(result["record"]["includes"], [".dep/src/input.d", "Makefile"])
        self.assertEqual(result["variable_census"]["defaults"], ["MODE"])
        self.assertEqual(result["prerequisite_domain_census"]["enumerated"], ["MODE"])
        self.assertEqual({
            variant["record"]["files"][0]["prerequisites"][0]["name"] for variant in result["record"]["variants"]
        }, {"first", "second"})

    def test_live_dependency_contract_rejects_embedded_operators_and_keeps_literals(self):
        self.add("src/input.c", '#include "header.h"\n')
        self.add("include/header.h", "#define INPUT 1\n")
        prefix = "mkdir -p .dep/src/ && cc -E -Iinclude -nostdinc -undef "
        suffix = " src/input.c -MM -MG -MT src/input.o > .dep/src/input.d"
        bad = prefix + "-DUNUSED=1||true" + suffix
        ordinary = self.shell_argv(bad)
        self.assertEqual(ordinary.returncode, 0)
        self.assertIn(b"no input files", ordinary.stderr)
        self.assertEqual((self.root / ".dep/src/input.d").read_bytes(), b"")
        self.add("Makefile", "include .dep/src/input.d\n.dep/src/input.d: src/input.c\n\t" + bad + "\nsrc/input.o: ;\n")
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            self.assertIn(bad, commands)
            with self.assertRaisesRegex(MakeProbeError, "unconsumed active shell syntax"):
                commands[bad]
        with self.session() as probe:
            with self.assertRaisesRegex(MakeProbeError, "unconsumed active shell syntax"):
                probe.make("src/input.o", commands=MakeCommands(probe, self.contracts))
        for argument in ("-DUNUSED='1||true'", r"-DUNUSED=1\|\|true", '-DUNUSED="1&&true"'):
            with self.subTest(argument=argument):
                command = prefix + argument + suffix
                ordinary = self.shell_argv(command)
                self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
                expected = (self.root / ".dep/src/input.d").read_bytes()
                self.add("Makefile", "include .dep/src/input.d\n.dep/src/input.d: src/input.c\n\t" + command + "\nsrc/input.o: ;\n")
                with self.session() as probe:
                    commands = MakeCommands(probe, self.contracts)
                    registration = commands[command]
                    produced = probe.command(registration)
                    self.assertEqual(produced.generated[0].data, expected)
                    self.assertEqual(produced.consumed, ("src/input.c",))
                    self.assertEqual(produced.code_consumed, ("include/header.h",))
                    native = probe.make("src/input.o", commands=commands)
                    self.assertEqual(native.semantics["files"][0]["prerequisites"], [
                        {"name": "src/input.c", "order_only": False},
                        {"name": "include/header.h", "order_only": False},
                    ])
                    dynamic, = native.semantics["dynamic_commands"]
                    self.assertEqual(dynamic["command"]["argv"], list(registration.argv))
                    self.assertTrue(any(path.endswith("/cc1") for path in dynamic["command"]["executed"]))
        for spelling in ("'&&'", '"&&"', r"\&\&"):
            command = (prefix + "-DUNUSED=1" + suffix).replace("&&", spelling, 1)
            with self.session() as probe:
                self.assertNotIn(command, MakeCommands(probe, self.contracts))
                with self.assertRaisesRegex(MakeProbeError, "declared command"):
                    MakeCommands(probe, self.contracts).dependency(command)

    def test_generic_pipeline_and_assignment_grammar_keeps_original_roles(self):
        for literal in ("'|'", '"|"', r"\|"):
            command = "printf '%s\\n' payload " + literal + " python3 -c 'print(\"unexpected\")'"
            ordinary = self.shell_argv(command)
            self.assertEqual(ordinary.returncode, 0)
            self.assertIn(b"|\npython3\n", ordinary.stdout)
            with self.session() as probe:
                with self.assertRaises(MakeProbeError):
                    self.generic_registration(probe, command)
        for assignment in ("'FE8_ITEM_ID_CAP=271'", '"FE8_ITEM_ID_CAP=271"', r"FE8_ITEM_ID_CAP\=271"):
            command = assignment + " python3 -c 'import os;print(os.environ[\"FE8_ITEM_ID_CAP\"])'"
            self.assertEqual(self.shell_argv(command).returncode, 127)
            with self.session() as probe:
                with self.assertRaises(MakeProbeError):
                    self.generic_registration(probe, command)
        for command in (
            "FE8_ITEM_ID_CAP='1||true' python3 -c 'import os;print(os.environ[\"FE8_ITEM_ID_CAP\"])'",
            "printf '%s\\n' 'a|b'|FE8_ITEM_ID_CAP=271 python3 -c 'import os,sys;print(os.environ[\"FE8_ITEM_ID_CAP\"]+\":\"+sys.stdin.read().strip())'",
        ):
            ordinary = self.shell_argv(command)
            self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
            with self.session() as probe:
                actual = probe.command(self.generic_registration(probe, command))
            self.assertEqual(actual.stdout, ordinary.stdout)
        command = "FE8_ITEM_ID_CAP=271 printf '%s\\n' value|python3 -c 'print(\"unexpected\")'"
        with self.session() as probe:
            with self.assertRaisesRegex(MakeProbeError, "producer environment"):
                self.generic_registration(probe, command)

    def test_generic_fd_literals_and_real_redirections_remain_distinct(self):
        program = "python3 -c 'import json,sys;print(json.dumps(sys.argv[1:]))'"
        for suffix in ("'2>&1'", r"2\>\&1", "'2>&1' 2>&1", "'2>/dev/null' 2>&1"):
            with self.subTest(suffix=suffix):
                command = program + " " + suffix
                ordinary = self.shell_argv(command)
                self.assertEqual(ordinary.returncode, 0)
                with self.session() as probe:
                    actual = probe.command(self.generic_registration(probe, command))
                self.assertEqual(actual.stdout, ordinary.stdout)
        program = (
            "python3 -c 'import os,sys;print(\"err-first\",file=sys.stderr,flush=True);"
            "print(\"out\",flush=True);os.write(2,b\"raw-err\\n\");print(\"last\",flush=True)'"
        )
        for suffix in ("", "2>&1", "2>&1 2>&1"):
            with self.subTest(suffix=suffix):
                command = program + " " + suffix
                ordinary = self.shell_argv(command)
                self.assertEqual(ordinary.returncode, 0)
                with self.session() as probe:
                    actual = probe.command(self.generic_registration(probe, command))
                self.assertEqual((actual.stdout, actual.stderr), (ordinary.stdout, ordinary.stderr))
        for suffix in ("2>/dev/null", "2> '/dev/null'", "2>&1 2>/dev/null", "2>/dev/null 2>&1"):
            command = program + " " + suffix
            ordinary = self.shell_argv(command)
            expected = b"err-first\nout\nraw-err\nlast\n" if suffix.endswith(" 2>&1") else b"out\nlast\n"
            self.assertEqual((ordinary.returncode, ordinary.stdout, ordinary.stderr), (0, expected, b""))
            self.add("Makefile", f"VALUE := $(shell {command})\nall: ;\n")
            with self.subTest(suffix=suffix), self.session() as probe:
                commands = self.generic_commands(probe, command)
                with self.assertRaisesRegex(MakeProbeError, "stderr discard"):
                    commands[command]
                with mock.patch.object(probe, "command", side_effect=AssertionError("producer must not start")):
                    with self.assertRaisesRegex(MakeProbeError, "stderr discard"):
                        probe.make("all", commands=commands)
        for suffix in ("'2'>/dev/null", r"\2>/dev/null", "2 >/dev/null", "2>&1>elsewhere", "2>>/dev/null"):
            with self.session() as probe:
                with self.assertRaisesRegex(MakeProbeError, "unconsumed active shell syntax"):
                    self.generic_registration(probe, program + " " + suffix)

    def test_simple_adapter_branches_reject_unconsumed_active_operators(self):
        self.add("scripts/fixture.py", "print('fixture')\n")
        self.add("linker_script_banim.txt", "")
        cases = (
            ("generic-lexical-fixture", "python3 -c 'print(\"ok\")'", ()),
            ("generic-lexical-fixture", "python3 scripts/fixture.py", ("scripts/fixture.py",)),
            ("asset-discovery-include-remake", "python3 -m scripts.assets.manifest --manifest assets/manifest.json --discovery-makefile build/assets.mk", ()),
            ("banim-compressing-linker-inputs", "python3 scripts/arm_compressing_linker.py --inputs linker_script_banim.txt", ()),
            ("legacy-text-source-discovery", "find texts -type f -name '*.txt'", ()),
            ("generated-include-remake-directory", "mkdir -p build/generated", ()),
            ("banim-scaninc-inputs", 'tools/scaninc/scaninc -I include -I "" src/input.s', ()),
        )
        for identity, command, inputs in cases:
            for tail in ("||true", "&&true", ";true", "|cat", ">elsewhere"):
                with self.subTest(identity=identity, tail=tail), self.session() as probe:
                    with self.assertRaisesRegex(MakeProbeError, "unconsumed active shell syntax"):
                        self.generic_registration(probe, command + tail, identity=identity, inputs=inputs)

    def shell_decodings(self, command):
        normalized = normalize_bash_script_commands(command, "fixture")
        words = parse_bash_script_commands(command, "fixture")
        typed = tuple(tuple(token.value for token in tokenize_bash_command(line)) for line in normalized)
        self.assertEqual(words, typed)
        return words

    def test_shell_comment_boundaries_match_both_consumers_and_actual_argv(self):
        prefix = ("/usr/bin/python3", "-I", "-S", "-B", "-c",
                  "import json,sys;print(json.dumps(sys.argv[1:]))")
        program = shlex.join(prefix)
        cases = (
            ("ok # note", ["ok"]),
            ("a#b", ["a#b"]),
            ("'#' note", ["#", "note"]),
            (r"\# note", ["#", "note"]),
            ("'ok'#tail # note", ["ok#tail"]),
            ("lo\\\nng # note", ["long"]),
            ("lo\\\n#ng # note", ["lo#ng"]),
            ("ok \\\n # note", ["ok"]),
            ("ok # ignored ' \" && trailing \\", ["ok"]),
        )
        for suffix, expected in cases:
            with self.subTest(suffix=suffix):
                command = program + " " + suffix
                actual = self.shell_argv(command)
                self.assertEqual(actual.returncode, 0, actual.stderr)
                self.assertEqual(json.loads(actual.stdout), expected)
                words, = self.shell_decodings(command)
                self.assertEqual(words[:len(prefix)], prefix)
                self.assertEqual(list(words[len(prefix):]), expected)

    def test_shell_non_lf_separators_remain_literal_argument_data(self):
        prefix = ("/usr/bin/python3", "-I", "-S", "-B", "-c",
                  "import json,sys;print(json.dumps(sys.argv[1:]))")
        program = shlex.join(prefix)
        for separator in ("\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            for quoted in (False, True):
                with self.subTest(separator=ord(separator), quoted=quoted):
                    argument = "." + separator + ("tail" if quoted else "")
                    command = program + " " + (shlex.quote(argument) if quoted else argument)
                    actual = self.shell_argv(command)
                    self.assertEqual(actual.returncode, 0, actual.stderr)
                    words, = self.shell_decodings(command)
                    self.assertEqual(words[:len(prefix)], prefix)
                    self.assertEqual(list(words[len(prefix):]), json.loads(actual.stdout))
                    self.assertEqual(list(words[len(prefix):]), [argument])

    def test_non_ascii_comment_prefixes_and_exec_names_are_not_discarded(self):
        program = "/usr/bin/python3 -I -S -B -c 'print(1)'"
        for prefix in ("\r", "\u00a0", "\u2003", "\u3000"):
            with self.subTest(prefix=ord(prefix)):
                leading = prefix + program
                actual = self.shell_argv(leading)
                self.assertEqual(actual.returncode, 127)
                words, = self.shell_decodings(leading)
                self.assertEqual(words[0], prefix + "/usr/bin/python3")
                extra = program + "\n" + prefix + "# not a comment"
                actual = self.shell_argv(extra)
                self.assertEqual(actual.returncode, 127)
                words = self.shell_decodings(extra)
                self.assertEqual(len(words), 2)
                self.assertEqual(words[1][0], prefix + "#")

    def test_lf_continuation_eof_and_quoted_newlines_match_actual_shell(self):
        prefix = ("/usr/bin/python3", "-I", "-S", "-B", "-c",
                  "import json,sys;print(json.dumps(sys.argv[1:]))")
        program = shlex.join(prefix)
        for command in (
            program + " .\\\n",
            program + " '.\\\ntail'",
            program + "\t.",
            " \t# ordinary comment\n" + program + " .\n",
            program + " a\\\n#'b\nc'",
            program + ' "a\\\nb"',
            program + " .\\\n\t",
        ):
            with self.subTest(command=command):
                actual = self.shell_argv(command)
                self.assertEqual(actual.returncode, 0, actual.stderr)
                words, = self.shell_decodings(command)
                self.assertEqual(words[:len(prefix)], prefix)
                self.assertEqual(list(words[len(prefix):]), json.loads(actual.stdout))
        with self.assertRaisesRegex(ValueError, "bare backslash at EOF"):
            normalize_bash_script_commands(program + " .\\", "fixture")

    def test_bash_parser_matches_shell_continuations_and_rejects_multi_command_registration(self):
        script = (
            'python3 -c "import json,sys;print(json.dumps(sys.argv[1:]))" \\\n'
            '\talph\\\n'
            'a \\\n'
            '\t"double\\\n'
            'quoted" \'single\\\n'
            'quoted\' "slash\\\\keep"'
        )
        ordinary = subprocess.run(
            ["/bin/sh", "-c", script],
            cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        )
        parsed, = parse_bash_script_commands(script, "fixture")
        self.assertEqual(json.loads(ordinary.stdout), list(parsed[3:]))
        self.assertEqual(parsed[3:], (
            "alpha", "doublequoted", "single\\\nquoted", "slash\\keep",
        ))
        contract = {
            "multi-python": {
                "id": "multi-python",
                "command_regex": r"(?s)^python3.*$",
                "input_files": [],
                "input_variables": [],
                "automatic_inputs": [],
                "resolved_value": None,
                "owning_evidence_ids": ["owner"],
            },
        }
        with self.session() as probe:
            with self.assertRaisesRegex(
                MakeProbeError, "unsupported multi-command shell shape",
            ):
                MakeCommands(probe, contract)['python3 -c "print(1)"\npython3 -c "print(2)"']

    def test_standard_python_command_preserves_namespace_import_behavior(self):
        self.add("scripts/generated_data/demo/helper.py", "VALUE = 'helper'\n")
        self.add(
            "scripts/generated_data/demo/tool.py",
            "from .helper import VALUE\nRESULT = VALUE + ':ok'\n",
        )
        ordinary = subprocess.run(
            [
                "/usr/bin/python3", "-I", "-S", "-B", "-c",
                "import json,sys;sys.path.insert(0,'.');import scripts;"
                "from scripts.generated_data.demo import tool;"
                "print(json.dumps({'loader':type(scripts.__spec__.loader).__name__,"
                "'origin':scripts.__spec__.origin,'paths':list(scripts.__path__),"
                "'result':tool.RESULT},sort_keys=True))",
            ],
            cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        )
        expected = json.loads(ordinary.stdout)
        expected["paths"] = [path.replace(str(self.root), "/repo") for path in expected["paths"]]
        with self.session() as probe:
            actual = probe.command(python_command(
                probe,
                "import json, scripts;"
                "from scripts.generated_data.demo import tool;"
                "print(json.dumps({'loader':type(scripts.__spec__.loader).__name__,"
                "'origin':scripts.__spec__.origin,'paths':list(scripts.__path__),"
                "'result':tool.RESULT},sort_keys=True))",
                code=(
                    "scripts/generated_data/demo/helper.py",
                    "scripts/generated_data/demo/tool.py",
                ),
            ))
            self.assertEqual(json.loads(actual.stdout), expected)
            self.assertEqual(
                set(actual.code_consumed),
                {
                    "scripts/generated_data/demo/helper.py",
                    "scripts/generated_data/demo/tool.py",
                },
            )
        self.assertFalse(probe.budget.children)

    def test_scaninc_uses_real_parser_search_order_and_recursive_include_closure(self):
        self.add("data/root.s", '.include "leaf.inc"\n.include "missing.inc"\n.incbin "asset.bin"\n')
        self.add("include/leaf.inc", '.include "nested.inc"\n')
        self.add("include/nested.inc", '.incbin "nested.bin"\n')
        self.add("leaf.inc", '.incbin "wrong-root.bin"\n')
        self.add("data/leaf.inc", '.incbin "wrong-local.bin"\n')
        ordinary = self.ordinary_scaninc("data/root.s")
        self.add("tools/scaninc/unlisted.cpp", "#error unlisted source entered compilation\n")
        self.add("tools/scaninc/unlisted.h", "#error unlisted header entered compilation\n")
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            result = probe.command(commands.scaninc("data/root.s"))
            compiler_inputs = {path for path, _, _ in commands.scanner.inputs}
            self.assertIn("tools/scaninc/scaninc.cpp", compiler_inputs)
            self.assertNotIn("tools/scaninc/unlisted.cpp", compiler_inputs)
            self.assertNotIn("tools/scaninc/unlisted.h", compiler_inputs)
            self.assertEqual(result.stdout, ordinary)
            self.assertEqual(result.consumed, (
                "data/root.s", "include/leaf.inc", "include/nested.inc",
            ))
            self.assertEqual(result.stdout.splitlines(), [
                b"asset.bin", b"include/leaf.inc", b"include/nested.inc", b"nested.bin",
            ])

    def test_real_banim_parent_includes_preserve_ordinary_search_spelling(self):
        source = "banim/banim_lorm_sp1_motion.s"
        includes = ("include/banim_sheet.inc", "include/banim_code.inc", "include/banim_code_frame.inc")
        for path in (source, *includes):
            self.add(path, (ROOT / path).read_bytes())
        ordinary = self.ordinary_scaninc(source)
        command = f'tools/scaninc/scaninc -I include -I "" {source}'
        self.add("Makefile", f"INPUTS := $(shell {command})\nall: ;\n")
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            output = probe.command(commands[command])
            self.assertEqual(output.stdout, ordinary)
            self.assertEqual(set(output.consumed), {source, *includes})
            observed = probe.make("all", variables=("INPUTS",), commands=commands)
            self.assertEqual(
                observed.semantics["domains"]["INPUTS"]["value"],
                " ".join(ordinary.decode().splitlines()),
            )
        self.assertFalse(probe.budget.children)

    def test_scaninc_does_not_collapse_an_absent_intermediate_directory(self):
        self.add("src/root.s", '.include "../include/leaf.inc"\n.include "missing/../leaf.inc"\n')
        self.add("include/leaf.inc", '.incbin "first.bin"\n')
        self.add("src/leaf.inc", '.incbin "second.bin"\n')
        self.add("src/missing/anchor", "directory member\n")
        ordinary = self.ordinary_scaninc("src/root.s")
        with self.session() as probe:
            output = probe.command(MakeCommands(probe, self.contracts).scaninc("src/root.s"))
            self.assertEqual(output.stdout, ordinary)
            self.assertIn(b"src/missing/../leaf.inc", output.stdout.splitlines())
            self.assertEqual(set(output.consumed), {"src/root.s", "include/leaf.inc", "src/leaf.inc"})
        self.assertFalse(probe.budget.children)

    def test_scaninc_rejects_escaping_and_unadmitted_sources(self):
        for content, expected in (
            ('.include "../outside.inc"\n', "canonical and repository-relative"),
            ('.include "link.inc"\n', "unadmitted source"),
        ):
            with self.subTest(content=content):
                self.add("root.s", content)
                self.add("link.inc", "untracked.inc", mode="120000")
                (self.root / "link.inc").unlink()
                (self.root / "link.inc").symlink_to("untracked.inc")
                with self.session() as probe:
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        MakeCommands(probe, self.contracts).scaninc("root.s")
        with self.session() as probe:
            with self.assertRaisesRegex(MakeProbeError, "resolves no regular inputs"):
                MakeCommands(probe, self.contracts).scaninc("untracked.s")

    def test_command_contract_mismatch_never_uses_resolved_value_as_output(self):
        self.add("Makefile", "all: ;\n")
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            with self.assertRaisesRegex(MakeProbeError, "exactly one sealed domain"):
                commands["printf fabricated"]
            for entry in commands.contracts:
                entry["resolved_value"] = "counterfeit"
            self.assertEqual(probe.command(commands["uname"]).stdout, b"Linux\n")

    def test_dependency_adapter_preserves_cc_order_output_closure_and_make_restart(self):
        self.add("src/input.c", '#include "config.h"\n#if ACTIVE\n#include <selected.h>\n#endif\n')
        self.add("include/config.h", "#define CONFIGURED 1\n")
        self.add("include/first/selected.h", '#include "deep.h"\n')
        self.add("include/first/deep.h", "#define SELECTED 1\n")
        self.add("include/second/selected.h", "#error wrong search order\n")
        command = (
            "mkdir -p .dep/src/ && cc -E -Iinclude/first \\\n"
            "\t-I include/second -iquote include -nostdinc -undef \\\n"
            "\t-DACTIVE=1 src/input.c -MM -MG -MT src/input.o > .dep/src/input.d"
        )
        make_command = (
            "mkdir -p .dep/src/ && cc -E -Iinclude/first -I include/second "
            "-iquote include -nostdinc -undef -DACTIVE=1 "
            "src/input.c -MM -MG -MT src/input.o > .dep/src/input.d"
        )
        tokens, = parse_bash_script_commands(command, "dependency fixture")
        arguments = tokens[4:-2]
        ordinary = subprocess.run(
            ["/usr/bin/cc", *arguments[1:]], cwd=self.root,
            env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        )
        self.add("archival_dependencies.mk", (
            "include .dep/src/input.d\n"
            ".dep/src/input.d: src/input.c\n\t" + make_command + "\n"
        ))
        self.add("Makefile", "include archival_dependencies.mk\nsrc/input.o: ;\n")
        with self.session() as probe:
            registration = MakeCommands(probe, self.contracts)[command]
            self.assertTrue(registration.dependency_only)
            self.assertEqual(registration.argv, ("/usr/bin/cc", *arguments[1:]))
            produced = probe.command(registration)
            self.assertIsNone(produced.artifact)
            self.assertEqual(produced.stdout, b"")
            self.assertEqual(produced.generated[0].data, ordinary.stdout)
            self.assertEqual(produced.consumed, ("src/input.c",))
            self.assertEqual(set(produced.code_consumed), {
                "include/config.h", "include/first/selected.h", "include/first/deep.h",
            })
            observed = probe.make(
                "src/input.o", variables=("MAKEFILE_LIST", "MAKE_RESTARTS"),
                commands={make_command: registration},
            )
            self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
            self.assertIn(
                "archival_dependencies.mk",
                observed.semantics["domains"]["MAKEFILE_LIST"]["value"].split(),
            )
            self.assertEqual(
                [entry["name"] for entry in observed.semantics["files"][0]["prerequisites"]],
                ["src/input.c", "include/config.h", "include/first/selected.h", "include/first/deep.h"],
            )
            dynamic, = observed.semantics["dynamic_commands"]
            self.assertTrue(dynamic["command"]["dependency_only"])
            self.assertEqual({item[0] for item in dynamic["command"]["inputs"]}, {
                "src/input.c", "include/config.h", "include/first/selected.h", "include/first/deep.h",
            })
            self.assertEqual(dynamic["generated_outputs"][0][0], ".dep/src/input.d")

    def test_dependency_adapter_rejects_directory_driver_and_forwards_invalid_flags(self):
        self.add("src/input.c", "int value;\n")
        command = (
            "mkdir -p .dep/src/ && cc -E -nostdinc -undef "
            "src/input.c -MM -MG -MT src/input.o > .dep/src/input.d"
        )
        for bad, expected in (
            (command.replace("mkdir -p .dep/src/", "mkdir -p .dep/other/"), "directory differs"),
            (command.replace("&& cc ", "&& clang "), "supported host C driver"),
        ):
            with self.subTest(command=bad), self.session() as probe:
                before = probe.budget.runs
                capsule_processes = probe.processes_used
                with self.assertRaisesRegex(MakeProbeError, expected):
                    MakeCommands(probe, self.contracts)[bad]
                self.assertEqual(probe.budget.runs, before + 1)
                self.assertEqual(probe.processes_used, capsule_processes)
        with self.session() as probe:
            bad = command.replace(" -undef ", " -undef -fplugin=evil.so ")
            registration = MakeCommands(probe, self.contracts).dependency(bad)
            self.assertIn("-fplugin=evil.so", registration.argv)
            before = probe.budget.runs
            with self.assertRaises(MakeProbeError):
                probe.command(registration)
            self.assertEqual(probe.budget.runs, before)

    def test_graph_uses_actual_published_bytes_without_preexecuting_the_producer(self):
        self.add("src/input.c", "int input;\n")
        self.add("static.mk", "all: ;\n")
        command = (
            "mkdir -p .dep/src/ && cc -E -nostdinc -undef "
            "src/input.c -MM -MG -MT src/input.o > .dep/src/input.d"
        )
        self.add("Makefile", (
            "include .dep/src/input.d\n"
            ".dep/src/input.d: src/input.c\n\t" + command + "\n"
            "src/input.o: ;\n"
        ))
        with self.session() as probe:
            outputs = []
            observations = []
            execute = probe.command
            make = probe.make

            def observe_command(registration):
                result = execute(registration)
                if registration.outputs:
                    outputs.append(result)
                return result

            def observe_make(*arguments, **options):
                result = make(*arguments, **options)
                observations.append(result)
                return result

            with mock.patch.object(probe, "command", new=observe_command):
                with mock.patch.object(probe, "make", new=observe_make):
                    result = run_probe(
                        probe.loader, {"src/input.o"}, {}, self.contracts, session=probe,
                    )
            self.assertEqual(len(outputs), 1)
            self.assertEqual(len(observations), 1)
            self.assertEqual(observations[0].generated, outputs[0].generated)
            self.assertEqual(outputs[0].generated[0].data, b"src/input.o: src/input.c\n")
            self.assertEqual(
                result["src/input.o"]["record"]["includes"], [".dep/src/input.d", "Makefile"],
            )
            self.assertFalse((probe.tree / ".dep/src/input.d").exists())
            self.assertEqual(probe.make("all", makefile="static.mk").generated, ())
        self.assertIsNone(probe.base)
        self.assertFalse(probe.runtime_tools)
        self.assertFalse(probe.runtime_query_profiles)
        self.assertFalse(probe.budget.children)

    def test_real_linker_discovery_uses_explicit_python_and_reaches_make(self):
        path = "scripts/arm_compressing_linker.py"
        self.add(path, (ROOT / path).read_bytes(), "100755")
        self.add("linker_script_banim.txt", (ROOT / "linker_script_banim.txt").read_bytes())
        contract = next(item for item in self.contracts.values()
                        if item["id"] == "banim-compressing-linker-inputs")
        expression = contract["expression"]
        self.add("Makefile", "PYTHON := python3\nINPUTS := " + expression
                 + "\nall:\n\t@printf '%s\\n' '$(INPUTS)'\nmeasure-inputs:\n")
        ordinary = subprocess.run(
            ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
            cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        ).stdout
        self.assertGreater(len(ordinary), 4096)
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            actual = probe.make("measure-inputs", variables=("INPUTS",), commands=commands)
            self.assertEqual(actual.semantics["domains"]["INPUTS"]["value"],
                             ordinary.decode().removesuffix("\n"))
            self.assertTrue(all(item["kind"] == "value" for item in actual.semantics["native_dispatches"]))
            self.assertEqual(actual.stderr, b"")
            self.assertEqual(len(actual.semantics["dynamic_commands"]), 1)
            self.assertTrue(actual.events)
            self.assertTrue(all(event["match"] == 0 for event in actual.events))
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            with self.assertRaisesRegex(MakeProbeError, "pathname exceeds bound"):
                probe.make("all", variables=("INPUTS",), commands=commands)
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)

    def test_registered_find_preserves_implicit_and_explicit_print_grammars(self):
        cases = (
            ("legacy-text-source-discovery", "texts", "txt",
             'find texts -type f -name "*.txt"'),
            ("asset-tool-source-discovery", "scripts/assets", "py",
             "find scripts/assets -type f -name '*.py' -print"),
        )
        for identity, root, extension, command in cases:
            with self.subTest(identity=identity):
                expected = (f"{root}/a.{extension}\n{root}/nested/b.{extension}\n").encode()
                self.add(f"{root}/a.{extension}", "first\n")
                self.add(f"{root}/nested/b.{extension}", "second\n")
                self.add(f"{root}/ignored.bin", "not matched\n")
                contract = next(item for item in self.contracts.values() if item["id"] == identity)
                self.add("Makefile", (
                    "TEXT_DIR := texts\n"
                    f"FOUND := {contract['expression']}\n"
                    "all: ;\n"
                ))
                ordinary = self.shell_argv(command)
                self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
                self.assertEqual(ordinary.stdout, expected)
                with self.session() as probe:
                    commands = MakeCommands(probe, {contract["expression"]: contract})
                    registration = commands[command]
                    actual = probe.command(registration)
                    self.assertEqual(actual.stdout, ordinary.stdout)
                    self.assertEqual(actual.consumed, registration.sources)
                    observed = probe.make("all", variables=("FOUND",), commands=commands)
                    self.assertEqual(
                        observed.semantics["domains"]["FOUND"]["value"],
                        " ".join(expected.decode().splitlines()),
                    )
                    for tail in (" -print", " -delete", " extra", "||true", "&&true", "|cat"):
                        with self.subTest(tail=tail), self.assertRaises(MakeProbeError):
                            commands[command + tail]
                self.assertIsNone(probe.base)
                self.assertFalse(probe.budget.children)

    def test_live_find_rejects_active_cwd_globs_and_preserves_literal_spellings(self):
        for root, extension, tail in (("texts", "txt", ""), ("scripts/assets", "py", " -print")):
            self.add(f"{root}/one.{extension}", "first\n")
            self.add(f"{root}/two.{extension}", "second\n")
            self.add(f"{root}/ignored.bin", "not selected\n")
            pattern = "*." + extension
            for count in range(3):
                if count:
                    self.add(("one" if count == 1 else "two") + "." + extension, "cwd match\n")
                command = f"find {root} -type f -name {pattern}{tail}"
                ordinary = self.shell_argv(command)
                with self.subTest(extension=extension, cwd_matches=count):
                    if count == 2:
                        self.assertNotEqual(ordinary.returncode, 0)
                        self.assertTrue(ordinary.stderr)
                    else:
                        self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
                        expected = [f"{root}/one.{extension}"]
                        if count == 0:
                            expected.append(f"{root}/two.{extension}")
                        self.assertEqual(ordinary.stdout.decode().splitlines(), expected)
                    self.add("Makefile", f"FOUND := $(shell {command})\nall: ;\n")
                    with self.session() as probe:
                        commands = MakeCommands(probe, self.contracts)
                        self.assertIn(command, commands)
                        with self.assertRaisesRegex(MakeProbeError, "active shell expansion"):
                            probe.make("all", variables=("FOUND",), commands=commands)
                    self.assertIsNone(probe.base)
                    self.assertFalse(probe.budget.children)
                    for spelling in ("'" + pattern + "'", '"' + pattern + '"', "\\" + pattern):
                        literal = f"find {root} -type f -name {spelling}{tail}"
                        expected = self.shell_argv(literal)
                        self.assertEqual(expected.returncode, 0, expected.stderr)
                        self.assertEqual(expected.stdout.decode().splitlines(),
                                         [f"{root}/one.{extension}", f"{root}/two.{extension}"])
                        self.add("Makefile", f"FOUND := $(shell {literal})\nall: ;\n")
                        with self.session() as probe:
                            # The sealed registry's source uses quotes; an exact fixture also
                            # admits the equivalent escaped spelling for a shell dispatch.
                            identity = ("legacy-text-source-discovery" if extension == "txt"
                                        else "asset-tool-source-discovery")
                            contract = next(item for item in self.contracts.values() if item["id"] == identity)
                            commands = MakeCommands(probe, {"fixture": {
                                **contract, "command_regex": (
                                    "(?:" + contract["command_regex"] + "|" + re.escape(literal) + ")"
                                ),
                            }})
                            direct = probe.command(commands[literal])
                            self.assertEqual(direct.stdout, expected.stdout)
                            observed = probe.make("all", variables=("FOUND",), commands=commands)
                            self.assertEqual(observed.semantics["domains"]["FOUND"]["value"],
                                             " ".join(expected.stdout.decode().splitlines()))
                        self.assertIsNone(probe.base)
                        self.assertFalse(probe.budget.children)

    def test_registered_find_matches_real_find_with_nested_unicode_and_multiple_batches(self):
        descriptors = set(os.listdir("/proc/self/fd"))
        paths = [
            "texts/a.txt",
            "texts/empty/nonmatching.bin",
            "texts/nested/deep.txt",
            "texts/nested/ignored.md",
            "texts/nested/" + "x" * 240 + ".txt",
            "texts/日本語.txt",
            *(f"texts/many/{index:03d}.txt" for index in range(256)),
        ]
        for path in sorted(paths):
            self.add(path, path + "\n")
        command = 'find texts -type f -name "*.txt"'
        contract = next(
            item for item in self.contracts.values()
            if item["id"] == "legacy-text-source-discovery"
        )
        self.add("Makefile", (
            "TEXT_DIR := texts\n"
            f"TEXTS := {contract['expression']}\n"
            "all: ;\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/find", "texts", "-type", "f", "-name", "*.txt"],
            cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        ).stdout
        with self.session() as probe:
            commands = MakeCommands(probe, {contract["expression"]: contract})
            registration = commands[command]
            result = probe.command(registration)
            self.assertEqual(result.stdout, ordinary)
            self.assertEqual(result.consumed, registration.sources)
            unknown_types = Command(
                (
                    *registration.argv[:5],
                    registration.argv[5].replace(
                        "kind = record[18]", "kind = DT_UNKNOWN",
                    ),
                    *registration.argv[6:],
                ),
                sources=registration.sources, directories=registration.directories,
            )
            fallback = probe.command(unknown_types)
            self.assertEqual(fallback.stdout, ordinary)
            self.assertEqual(fallback.consumed, registration.sources)
            fallback_status_paths = {
                record[1] for record in fallback.metadata
                if record[0] == 262 and record[2] & 0x100 and record[6] == 0
            }
            self.assertTrue(
                {"/repo/" + path for path in paths} <= fallback_status_paths,
                "unknown entry types must execute no-follow metadata lookup for every file",
            )
            records = [record for record in result.metadata if record[0] == 217]
            self.assertTrue(records)
            self.assertTrue(all(record[4] == 4096 for record in records))
            self.assertTrue(all(
                len(record[7]) == len(record[8]) == 2 * record[4]
                for record in records
            ))
            root_batches = [
                record for record in records
                if record[1] == "/repo/texts/many" and record[6] > 0
            ]
            self.assertGreater(len(root_batches), 1)
            self.assertIn("texts/日本語.txt\n".encode(), result.stdout)
            self.assertNotIn(b"nonmatching.bin", result.stdout)
            self.assertNotIn(b"ignored.md", result.stdout)
            observed = probe.make("all", variables=("TEXTS",), commands=commands)
            self.assertEqual(
                observed.semantics["domains"]["TEXTS"]["value"],
                " ".join(ordinary.decode().splitlines()),
            )
            self.assertEqual(len(observed.semantics["dynamic_commands"]), 1)
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)
        self.assertEqual(set(os.listdir("/proc/self/fd")), descriptors)

    def test_generated_dependency_remakes_publish_real_depfiles_and_restart_make(self):
        cases = self.add_generated_dependency_fixture()
        lines = [f"-include {case['output']}" for case in cases]
        lines.extend((".PHONY: all force", "ifeq ($(MAKE_RESTARTS),)"))
        for case in cases:
            lines.append(f"{case['output']}: force\n\t{case.get('make_command', case['command'].replace(' \\\n\t', ' '))}")
        lines.append("else")
        for case in cases:
            lines.append(f"{case['output']}: ;")
        lines.append("endif")
        for case in cases:
            lines.append(f"{case['target']}: ;")
        lines.append("all: " + " ".join(case["target"] for case in cases))
        lines.append("force: ;")
        self.add("Makefile", "\n".join(lines) + "\n")
        ordinary = {}
        for case in cases:
            completed = subprocess.run(
                ["/bin/sh", "-c", case["command"]],
                cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                capture_output=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            ordinary[case["output"]] = self.normalize_repo_bytes(
                (self.root / case["output"]).read_bytes()
            )
        commands = {}
        for case in cases:
            with self.session() as probe:
                commands[case.get("make_command", case["command"].replace(" \\\n\t", " "))] = (
                    MakeCommands(probe, self.contracts)[case["command"]]
                )
            self.assertIsNone(probe.base)
            self.assertFalse(probe.budget.children)
        with self.session() as probe:
            observed = probe.make("all", variables=("MAKE_RESTARTS",), commands=commands)
            self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
            self.assertEqual(
                {
                    output[0]: output[2]
                    for record in observed.semantics["dynamic_commands"]
                    for output in record["generated_outputs"]
                },
                {
                    path: hashlib.sha256(data).hexdigest()
                    for path, data in ordinary.items()
                },
            )
            self.assertEqual(
                {
                    output[0]
                    for record in observed.semantics["dynamic_commands"]
                    for output in record["generated_outputs"]
                },
                {case["output"] for case in cases},
            )
            self.assertEqual(len(observed.semantics["dynamic_commands"]), 3)
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)

    def test_generated_dependency_uses_named_option_semantics_and_rejects_key_drift(self):
        cases = {case["name"]: case for case in self.add_generated_dependency_fixture()}
        canonical = cases["autoplaystrategies"]["command"]
        reordered = cases["autoplaystrategies"]["reordered"]
        ordinary = {}
        for label, command in (("canonical", canonical), ("reordered", reordered)):
            completed = subprocess.run(
                ["/bin/sh", "-c", command],
                cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                capture_output=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            ordinary[label] = (self.root / cases["autoplaystrategies"]["output"]).read_bytes()
        self.assertEqual(ordinary["reordered"], ordinary["canonical"])
        actual = []
        for spelling in (canonical, reordered):
            with self.session() as probe:
                registration = MakeCommands(probe, self.contracts)[spelling]
                output, = probe.command(registration).generated
                self.assertEqual(output.data, self.normalize_repo_bytes(ordinary["canonical"]))
                actual.append((registration.sources, registration.directories, output))
        self.assertEqual(actual[0], actual[1])
        for bad, expected in (
            (
                canonical.replace(
                    '\t--objectives-source "testdata/objectives/el_objectives.json" \\\n', "",
                ),
                "missing --objectives-source",
            ),
            (
                canonical.replace(
                    '\t--depfile "build/generated/data/autoplaystrategies.inputs.mk"',
                    '\t--extra "ignored" \\\n\t--depfile "build/generated/data/autoplaystrategies.inputs.mk"',
                ),
                "extra --extra",
            ),
        ):
            with self.subTest(command=bad), self.session() as probe:
                with self.assertRaisesRegex(MakeProbeError, expected):
                    MakeCommands(probe, self.contracts)[bad]

    def test_generated_dependency_adapter_respects_selected_views_and_missing_companions(self):
        cases = {case["name"]: case for case in self.add_generated_dependency_fixture()}
        removed = "assets/tmx/Example.tmx"
        select_budget = ProbeBudget()
        base_again = self.capture_loader(select_budget)
        (self.root / removed).unlink()
        del self.entries[removed]
        current_budget = ProbeBudget()
        current_loader = self.capture_loader(current_budget)
        current_for_base = self.capture_loader(select_budget)
        command = cases["chapterobjectives"]["command"]
        marker = f"/repo/{removed}"
        with ProbeSession(
            current_loader, scratch_root=self.root / "build/scratch", budget=current_budget,
        ) as probe:
            current = MakeCommands(probe, self.contracts)[command]
            output, = probe.command(current).generated
            reported = output.data.decode("utf-8").split()
            self.assertNotIn(marker, reported)
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)
        with ProbeSession(
            current_for_base, scratch_root=self.root / "build/scratch", budget=select_budget,
        ) as probe:
            with probe.select_view(base_again) as selected:
                self.assertIs(selected, probe)
                base = MakeCommands(probe, self.contracts)[command]
                output, = probe.command(base).generated
                reported = output.data.decode("utf-8").split()
                self.assertIn(marker, reported)
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)
        current_budget.close()
        missing = "assets/manifest.json"
        (self.root / missing).unlink()
        del self.entries[missing]
        with self.session() as probe:
            with self.assertRaisesRegex(
                MakeProbeError,
                r"(missing declared owner input 'assets/manifest\.json'|source declaration resolves no regular inputs: assets/manifest\.json)",
            ):
                MakeCommands(probe, self.contracts)[command]
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)

    def test_registered_find_reduces_actual_directory_observation_traffic(self):
        self.add("Makefile", "all: ;\n")
        for index in range(21):
            path = f"texts/d{index:02d}/entry.txt"
            self.add(path, path + "\n")
        command = 'find texts -type f -name "*.txt"'
        contract = next(
            item for item in self.contracts.values()
            if item["id"] == "legacy-text-source-discovery"
        )
        scandir_body = (
            "import fnmatch,os,sys\n"
            "def visit(path):\n"
            "    with os.scandir(path) as entries:\n"
            "        for entry in entries:\n"
            "            if entry.is_dir(follow_symlinks=False): visit(entry.path)\n"
            "            elif entry.is_file(follow_symlinks=False) and "
            "fnmatch.fnmatchcase(entry.name,sys.argv[2]): print(entry.path)\n"
            "visit(sys.argv[1])"
        )
        observations = {}
        for name in ("scandir", "getdents4096"):
            with self.session() as probe:
                registration = MakeCommands(
                    probe, {contract["expression"]: contract},
                )[command]
                selected = registration if name == "getdents4096" else Command(
                    ("/usr/bin/python3", "-I", "-S", "-B", "-c",
                     scandir_body, "texts", "*.txt"),
                    sources=registration.sources, directories=registration.directories,
                )
                before = probe.budget.bytes.get("control", 0)
                result = probe.command(selected)
                observations[name] = {
                    "output": result.stdout,
                    "control": probe.budget.bytes["control"] - before,
                    "metadata": result.metadata,
                }
            self.assertIsNone(probe.base)
            self.assertFalse(probe.budget.children)
        self.assertEqual(observations["getdents4096"]["output"],
                         observations["scandir"]["output"])
        old_records = [
            record for record in observations["scandir"]["metadata"] if record[0] == 217
        ]
        new_records = [
            record for record in observations["getdents4096"]["metadata"]
            if record[0] == 217
        ]
        self.assertEqual(len(old_records), 44)
        self.assertEqual(len(new_records), 44)
        self.assertTrue(all(record[4] == 32768 for record in old_records))
        self.assertTrue(all(record[4] == 4096 for record in new_records))
        for records in (old_records, new_records):
            self.assertTrue(all(
                len(record[7]) == len(record[8]) == 2 * record[4]
                for record in records
            ))
        old_control = observations["scandir"]["control"]
        new_control = observations["getdents4096"]["control"]
        self.assertLessEqual(2 * new_control, old_control)
        self.assertLess(
            len(encoded(observations["getdents4096"]["metadata"])),
            len(encoded(observations["scandir"]["metadata"])) // 2,
        )

    def test_registered_find_rejects_escaping_missing_and_nonregular_roots(self):
        self.add("Makefile", "all: ;\n")
        contract = next(
            item for item in self.contracts.values()
            if item["id"] == "legacy-text-source-discovery"
        )
        with self.session() as probe:
            with self.assertRaisesRegex(MakeProbeError, "exactly one sealed domain"):
                MakeCommands(probe, {contract["expression"]: contract})[
                    'find ../texts -type f -name "*.txt"'
                ]
        self.assertIsNone(probe.base)
        for shape, expected in (
            ("missing", "directory declaration is absent"),
            ("nonregular", "nonregular source namespace"),
        ):
            if shape == "nonregular":
                self.add("texts", "target", mode="120000")
                (self.root / "texts").unlink()
                (self.root / "texts").symlink_to("target")
            command = 'find texts -type f -name "*.txt"'
            with self.session() as probe:
                registration = MakeCommands(
                    probe, {contract["expression"]: contract},
                )[command]
                with self.assertRaisesRegex(MakeProbeError, expected):
                    probe.command(registration)
            self.assertIsNone(probe.base)
            self.assertFalse(probe.budget.children)

    def test_registered_find_does_not_follow_nonregular_descendants(self):
        self.add("Makefile", "all: ;\n")
        self.add("texts/regular.txt", "regular\n")
        self.add("outside.txt", "outside\n")
        self.add("texts/link.txt", "../outside.txt", mode="120000")
        link = self.root / "texts/link.txt"
        link.unlink()
        link.symlink_to("../outside.txt")
        ordinary = subprocess.run(
            ["/usr/bin/find", "texts", "-type", "f", "-name", "*.txt"],
            cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, check=True, timeout=15,
        )
        self.assertEqual(ordinary.stdout, b"texts/regular.txt\n")
        contract = next(
            item for item in self.contracts.values()
            if item["id"] == "legacy-text-source-discovery"
        )
        command = 'find texts -type f -name "*.txt"'
        with self.session() as probe:
            registration = MakeCommands(
                probe, {contract["expression"]: contract},
            )[command]
            with self.assertRaisesRegex(
                MakeProbeError, "nonregular namespace in source enumeration",
            ):
                probe.command(registration)
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)

    @unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "requires arm-none-eabi-gcc")
    def test_modern_toolchain_directory_queries_match_ordinary_shell_and_make(self):
        compiler = str(Path(shutil.which("arm-none-eabi-gcc")).resolve())
        self.add("scripts/shiftcheck/modern_toolchain.sh",
                 (ROOT / "scripts/shiftcheck/modern_toolchain.sh").read_bytes(), "100755")
        contracts = {
            item["id"]: item for item in self.contracts.values()
            if item["id"] in {"modern-libgcc-directory", "modern-libc-directory"}
        }
        for label, binutils in (("no-B", ""), ("valid-B", '"-B/usr/bin/"')):
            with self.subTest(profile=label):
                queries = {
                    "LIBGCC": (
                        "modern-libgcc-directory", "-print-libgcc-file-name",
                    ),
                    "LIBC": (
                        "modern-libc-directory", "-print-file-name=libc.a",
                    ),
                }
                makefile = [
                    f"MODERN_CC := {compiler}",
                    f"MODERN_BINUTILS_FLAG := {binutils}",
                    "MODERN_ARCH_FLAGS := -mcpu=arm7tdmi -mthumb -mthumb-interwork",
                ]
                expected = {}
                for variable, (contract_id, query) in queries.items():
                    makefile.append(f"{variable} := {contracts[contract_id]['expression']}")
                    makefile.append(f"{variable}_STATUS := $(.SHELLSTATUS)")
                    command = (
                        f'p=$("{compiler}" {binutils + " " if binutils else ""}'
                        '-mcpu=arm7tdmi \\\n\t'
                        f'-mthumb -mthumb-interwork {query} '
                        '2>/dev/null) && dirname "$p"'
                    )
                    ordinary = subprocess.run(
                        ["/bin/sh", "-c", command], cwd=self.root,
                        env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                        capture_output=True, timeout=15,
                    )
                    self.assertEqual(ordinary.returncode, 0, ordinary.stderr)
                    expected[variable] = ordinary.stdout.decode().strip()
                makefile.append("all: ;")
                self.add("Makefile", "\n".join(makefile) + "\n")
                ordinary_make = subprocess.run(
                    ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
                    cwd=self.root, env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
                    capture_output=True, timeout=15,
                )
                self.assertEqual(ordinary_make.returncode, 0, ordinary_make.stderr)
                with self.session(runtime_files=("/bin/arm-none-eabi-gcc",)) as probe:
                    commands = MakeCommands(
                        probe, {item["expression"]: item for item in contracts.values()},
                    )
                    for variable, expected_value in expected.items():
                        _, query = queries[variable]
                        command = (
                            f'p=$("{compiler}" {binutils + " " if binutils else ""}'
                            '-mcpu=arm7tdmi \\\n\t'
                            f'-mthumb -mthumb-interwork {query} 2>/dev/null) && dirname "$p"'
                        )
                        registration = commands[command]
                        self.assertEqual(
                            probe.command(registration).stdout.decode().strip(),
                            expected_value,
                        )
                    observed = probe.make(
                        "all", variables=("LIBGCC", "LIBGCC_STATUS", "LIBC", "LIBC_STATUS"),
                        commands=commands,
                    )
                    self.assertEqual(
                        observed.semantics["domains"]["LIBGCC"]["value"], expected["LIBGCC"],
                    )
                    self.assertEqual(
                        observed.semantics["domains"]["LIBC"]["value"], expected["LIBC"],
                    )
                    self.assertEqual(observed.semantics["domains"]["LIBGCC_STATUS"]["value"], "0")
                    self.assertEqual(observed.semantics["domains"]["LIBC_STATUS"]["value"], "0")
                    self.assertEqual(len(observed.semantics["dynamic_commands"]), 2)
                    for record in observed.semantics["dynamic_commands"]:
                        command = record["command"]
                        self.assertEqual(command["argv"][0], compiler)
                        self.assertEqual(command["argv"][1:-1], (
                            ([] if not binutils else ["-B/usr/bin/"])
                            + ["-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork"]
                        ))
                        self.assertEqual(command["stdout_transform"], "dirname")
                        self.assertEqual(command["runtime_tool"]["path"], compiler)
                        self.assertEqual(command["runtime_tool"]["canonical"], compiler)
                        self.assertEqual(
                            command["runtime_tool"]["mode"],
                            Path(compiler).stat().st_mode & 0o777,
                        )
                        self.assertRegex(command["runtime_tool"]["sha256"], r"^[0-9a-f]{64}$")
                    self.assertEqual(observed.stderr, b"")
                self.assertIsNone(probe.base)
                self.assertFalse(probe.runtime_tools)
                self.assertFalse(probe.runtime_query_profiles)
                self.assertFalse(probe.budget.children)

    def test_modern_toolchain_directory_queries_reject_unsupported_driver_and_binutils_paths(self):
        self.add("scripts/shiftcheck/modern_toolchain.sh",
                 (ROOT / "scripts/shiftcheck/modern_toolchain.sh").read_bytes(), "100755")
        cases = (
            ('p=$("/usr/bin/cc" -mcpu=arm7tdmi -mthumb -mthumb-interwork '
             '-print-libgcc-file-name 2>/dev/null) && dirname "$p"',
             "supported arm-none-eabi-gcc compiler"),
            ('p=$("arm-none-eabi-gcc" -v -mcpu=arm7tdmi -mthumb -mthumb-interwork '
             '-print-libgcc-file-name 2>/dev/null) && dirname "$p"',
             "declared flags"),
            ('p=$("arm-none-eabi-gcc" -mthumb -mcpu=arm7tdmi -mthumb-interwork '
             '-print-libgcc-file-name 2>/dev/null) && dirname "$p"',
             "declared flags"),
        )
        with self.session() as probe:
            commands = MakeCommands(probe, self.contracts)
            for command, expected in cases:
                with self.subTest(command=command):
                    with self.assertRaisesRegex(MakeProbeError, expected):
                        commands[command]
            bad_binutils = (
                'p=$("arm-none-eabi-gcc" "-B/opt/toolchain/bin/" '
                '-mcpu=arm7tdmi -mthumb -mthumb-interwork '
                '-print-file-name=libc.a 2>/dev/null) && dirname "$p"'
            )
            with mock.patch.object(shutil, "which", return_value=None):
                with self.assertRaisesRegex(MakeProbeError, "supported binutils roots"):
                    commands[bad_binutils]

    def test_root_runtime_capture_preserves_actual_absent_compiler_alias(self):
        self.add("Makefile", "all: ;\n")
        capture = make_probe._capture_runtime_input

        def absent_compiler(path, budget):
            item = capture(path, budget)
            if path == "/bin/arm-none-eabi-gcc":
                return replace(item, data=None, mode=None)
            return item

        with mock.patch.object(make_probe, "_capture_runtime_input", absent_compiler):
            with self.session(runtime_files=ROOT_RUNTIME_FILES) as probe:
                compiler = next(
                    item for item in probe.runtime_inputs
                    if item.path == "/bin/arm-none-eabi-gcc"
                )
                self.assertIsNone(compiler.data)
                self.assertIsNone(compiler.mode)
                self.assertEqual(compiler.canonical, "/usr/bin/arm-none-eabi-gcc")
                self.assertEqual(compiler.aliases, (("/bin", "usr/bin"),))
                self.assertNotIn(compiler.path, probe.runtime_dispatch)
                self.assertFalse(
                    (probe.runtime_root / compiler.canonical.lstrip("/")).exists()
                )
                observed = probe.make("all")
                self.assertEqual(observed.stderr, b"")
        self.assertIsNone(probe.base)
        self.assertFalse(probe.runtime_inputs)
        self.assertFalse(probe.budget.children)

    def test_modern_toolchain_directory_query_never_executes_checkout_local_name_match(self):
        tool = self.root / "build/toolchain-root/usr/bin/arm-none-eabi-gcc"
        marker = self.root / "candidate-executed"
        self.add(
            tool.relative_to(self.root).as_posix(),
            f"#!/bin/sh\nprintf executed > {marker}\nexit 7\n",
            "100755",
        )
        self.add("scripts/shiftcheck/modern_toolchain.sh",
                 (ROOT / "scripts/shiftcheck/modern_toolchain.sh").read_bytes(), "100755")
        command = (
            f'p=$("{tool}" -mcpu=arm7tdmi -mthumb -mthumb-interwork '
            '-print-libgcc-file-name 2>/dev/null) && dirname "$p"'
        )
        contract = next(
            item for item in self.contracts.values() if item["id"] == "modern-libgcc-directory"
        )
        self.add("Makefile", (
            f"MODERN_CC := {tool}\n"
            "MODERN_BINUTILS_FLAG :=\n"
            "MODERN_ARCH_FLAGS := -mcpu=arm7tdmi -mthumb -mthumb-interwork\n"
            f"VALUE := {contract['expression']}\n"
            "STATUS := $(.SHELLSTATUS)\n"
            "all:\n\t@printf 'VALUE=%s\\nSTATUS=%s\\n' '$(VALUE)' '$(STATUS)'\n"
        ))
        ordinary = subprocess.run(
            ["/usr/bin/make", "--no-print-directory", "-f", "Makefile", "all"],
            cwd=self.root,
            env={**ENVIRONMENT, "TMPDIR": str(self.directory)},
            capture_output=True, timeout=15,
        )
        self.assertEqual(ordinary.returncode, 0)
        self.assertEqual(ordinary.stdout, b"VALUE=\nSTATUS=7\n")
        self.assertTrue(marker.is_file())
        marker.unlink()
        with self.session() as probe:
            commands = MakeCommands(probe, {contract["expression"]: contract})
            with self.assertRaisesRegex(
                MakeProbeError, "checkout-local modern compiler lacks a trusted installed-tool identity",
            ):
                commands[command]
            self.assertFalse(marker.exists())
        self.assertIsNone(probe.base)
        self.assertFalse(probe.runtime_tools)
        self.assertFalse(probe.runtime_query_profiles)
        self.assertFalse(probe.budget.children)

    @unittest.skipUnless(shutil.which("arm-none-eabi-gcc"), "requires arm-none-eabi-gcc")
    def test_modern_toolchain_runtime_receipt_cannot_authorize_other_compiler_modes(self):
        compiler = str(Path(shutil.which("arm-none-eabi-gcc")).resolve())
        self.add("scripts/shiftcheck/modern_toolchain.sh",
                 (ROOT / "scripts/shiftcheck/modern_toolchain.sh").read_bytes(), "100755")
        with self.session(runtime_files=("/bin/arm-none-eabi-gcc",)) as probe:
            tool = probe.runtime_tool(compiler)
            with self.assertRaisesRegex(
                MakeProbeError, "metadata query escaped its exact profile",
            ):
                probe.command(Command(
                    (compiler, "--version"), runtime_tool=tool, stdout_transform="dirname",
                ))
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)

    def test_modern_toolchain_directory_query_rejects_shell_injection_before_execution(self):
        self.add("scripts/shiftcheck/modern_toolchain.sh",
                 (ROOT / "scripts/shiftcheck/modern_toolchain.sh").read_bytes(), "100755")
        marker = self.root / "injected"
        command = (
            'p=$("arm-none-eabi-gcc" -mcpu=arm7tdmi -mthumb -mthumb-interwork '
            '-print-libgcc-file-name 2>/dev/null) && dirname "$p"; '
            f'printf injected > "{marker}"'
        )
        with self.session() as probe:
            with self.assertRaisesRegex(MakeProbeError, "exactly one sealed domain"):
                MakeCommands(probe, self.contracts)[command]
            self.assertFalse(marker.exists())
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)

    def test_python_adapters_track_only_their_actual_import_closure(self):
        for name, data in (
            ("scripts/__init__.py", ""),
            ("scripts/generated_data/__init__.py", ""),
            ("scripts/generated_data/helper.py", "VALUE = 7\n"),
            ("scripts/generated_data/unrelated.py", "VALUE = 99\n"),
            ("scripts/generated_data/tool.py",
             "from scripts.generated_data import helper\nprint(helper.VALUE)\n"),
        ):
            self.add(name, data)
        inline = {
            "inline-python": {
                "id": "inline-python",
                "command_regex": r'^python3 -c "import scripts\.generated_data\.helper as h; print\(h\.VALUE\)"$',
                "input_files": [],
                "input_variables": [],
                "automatic_inputs": [],
                "resolved_value": None,
                "owning_evidence_ids": ["owner"],
            },
        }
        script = {
            "script-python": {
                "id": "script-python",
                "command_regex": r"^python3 scripts/generated_data/tool\.py$",
                "input_files": [],
                "input_variables": [],
                "automatic_inputs": [],
                "resolved_value": None,
                "owning_evidence_ids": ["owner"],
            },
        }
        with self.session() as probe:
            inline_registration = MakeCommands(probe, inline)[
                'python3 -c "import scripts.generated_data.helper as h; print(h.VALUE)"'
            ]
            self.assertIn("scripts/generated_data/helper.py", inline_registration.code)
            self.assertNotIn("scripts/generated_data/unrelated.py", inline_registration.code)
            self.assertEqual(probe.command(inline_registration).stdout, b"7\n")

            script_registration = MakeCommands(probe, script)["python3 scripts/generated_data/tool.py"]
            self.assertIn("scripts/generated_data/tool.py", script_registration.code)
            self.assertIn("scripts/generated_data/helper.py", script_registration.code)
            self.assertNotIn("scripts/generated_data/unrelated.py", script_registration.code)
            inputs = {item[0] for item in probe.command(script_registration).input_identities}
            self.assertIn("scripts/generated_data/tool.py", inputs)
            self.assertIn("scripts/generated_data/helper.py", inputs)
            self.assertNotIn("scripts/generated_data/unrelated.py", inputs)

    def test_three_record_asset_discovery_reaches_make_and_restarts_once(self):
        from scripts.assets.manifest import discovery_sources, load_manifest

        source = "assets/manifest.json"
        records = load_manifest(str(ROOT / source))
        self.assertEqual(len(records), 3)
        inputs = (source, *discovery_sources(records))
        self.assertEqual(len(inputs), 15)
        for path in inputs:
            self.add(path, (ROOT / path).read_bytes())
        for prefix in CODE_PREFIXES:
            for path in (ROOT / prefix).rglob("*.py"):
                self.add(path.relative_to(ROOT).as_posix(), path.read_bytes())
        output = "build/generated/asset-discovery/current.mk"
        command = (
            'python3 -m scripts.assets --custom-spell-effects "0" --item-id-cap "0xCD" '
            f'--manifest "{source}" --discovery-makefile "{output}" discovery-makefile'
        )
        self.add("Makefile", (
            f"-include {output}\n"
            ".PHONY: all force\n"
            "ifeq ($(MAKE_RESTARTS),)\n"
            f"{output}: force\n\t{command}\n"
            "else\n"
            f"{output}: ;\n"
            "endif\n"
            "all: ;\n"
        ))
        names = (
            "ASSET_PORTRAIT_INCBIN_CONSUMERS", "ASSET_TMX_INCBIN_CONSUMERS",
            "ASSET_BANIM_INCBIN_CONSUMERS", "ASSET_CUSTOM_SPELL_INCBIN_CONSUMERS",
        )
        budget = ProbeBudget()
        base_loader = self.capture_loader(budget)
        manifest = json.loads((self.root / source).read_bytes())
        manifest["assets"] = [
            record for record in manifest["assets"] if record["id"] != "LORM_SP1_PROOF"
        ]
        self.add(source, json.dumps(manifest))
        deleted = "assets/banim/lorm_sp1/script.txt"
        (self.root / deleted).unlink()
        del self.entries[deleted]
        current_loader = self.capture_loader(budget)
        current_inputs = (source, *discovery_sources(load_manifest(str(self.root / source))))
        self.asset_view_evidence = {"scope": "asset producer views, not graph ownership", "views": {}}
        with ProbeSession(
            current_loader, scratch_root=self.root / "build/scratch", budget=budget,
        ) as probe:
            def observe(view, expected_inputs, banim):
                registration = asset_discovery_command(probe, source, output)
                produced = probe.command(registration)
                self.assertEqual(set(produced.consumed), set(expected_inputs))
                self.assertEqual([item.path for item in produced.generated], [output])
                actual = probe.make(
                    "all", variables=(*names, "MAKE_RESTARTS"),
                    commands={command: registration},
                )
                values = {name: actual.semantics["domains"][name]["value"] for name in names}
                self.assertEqual(values, {
                    "ASSET_PORTRAIT_INCBIN_CONSUMERS": "EIRIKA_FORMATTED_PORTRAIT",
                    "ASSET_TMX_INCBIN_CONSUMERS": "CH2_MAIN_MAP",
                    "ASSET_BANIM_INCBIN_CONSUMERS": banim,
                    "ASSET_CUSTOM_SPELL_INCBIN_CONSUMERS": "",
                })
                self.assertEqual(actual.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
                self.assertEqual(len(actual.semantics["dynamic_commands"]), 1)
                self.assertEqual(
                    actual.semantics["dynamic_commands"][0]["generated_outputs"][0][0], output,
                )
                self.asset_view_evidence["views"][view] = {
                    "consumed": list(produced.consumed),
                    "values": values,
                    "make_restarts": actual.semantics["domains"]["MAKE_RESTARTS"]["value"],
                    "generated_outputs": actual.semantics["dynamic_commands"][0]["generated_outputs"],
                }
            observe("current", current_inputs, "")
            self.assertNotIn(deleted, probe.snapshot.files)
            previous_runs = budget.runs
            previous_states = budget.states
            deadline = budget.deadline
            with probe.select_view(base_loader) as selected:
                self.assertIs(selected, probe)
                self.assertIn(deleted, probe.snapshot.files)
                observe("base", inputs, "LORM_SP1_PROOF")
                self.assertEqual(budget.deadline, deadline)
            self.assertNotIn(deleted, probe.snapshot.files)
            self.assertGreater(budget.runs, previous_runs)
            self.assertGreater(budget.states, previous_states)
            self.asset_view_evidence["accounting"] = {
                "runs": budget.runs, "states": budget.states, "bytes": dict(budget.bytes),
                "processes": probe.processes_used, "live_process_peak": probe.live_process_peak,
                "syscalls": probe.syscalls_used, "deadline": budget.deadline,
            }
        self.assertIsNone(probe.base)
        self.assertFalse(probe.budget.children)
        self.asset_view_evidence["cleanup"] = probe.base is None and not probe.budget.children

    def test_asset_publication_is_stable_across_independent_materializations(self):
        from scripts.assets.manifest import discovery_sources, load_manifest

        source = "assets/manifest.json"
        inputs = (source, *discovery_sources(load_manifest(str(ROOT / source))))
        for path in inputs:
            self.add(path, (ROOT / path).read_bytes())
        for prefix in CODE_PREFIXES:
            for path in (ROOT / prefix).rglob("*.py"):
                self.add(path.relative_to(ROOT).as_posix(), path.read_bytes())
        output = "build/generated/asset-discovery/current.mk"
        command = (
            'python3 -m scripts.assets --custom-spell-effects "0" --item-id-cap "0xCD" '
            f'--manifest "{source}" --discovery-makefile "{output}" discovery-makefile'
        )
        self.add("Makefile", f"include {output}\n{output}:\n\t{command}\nall: ;\n")
        results = []
        identities = []
        timestamps = []
        for change in (None, None, "content", "mode"):
            if change is not None:
                path = "assets/portrait_registry.json"
                data = (self.root / path).read_bytes()
                self.add(
                    path, data + b"\n" if change == "content" else data,
                    "100755" if change == "mode" else "100644",
                )
            budget = ProbeBudget()
            loader = self.capture_loader(budget)
            with ProbeSession(loader, scratch_root=self.root / "build/scratch", budget=budget) as probe:
                identities.append(probe.source_owners(inputs))
                before = {path: (probe.tree / path).stat().st_mtime_ns for path in inputs}
                observed = probe.make(
                    "all", variables=("MAKE_RESTARTS",),
                    commands={command: asset_discovery_command(probe, source, output)},
                )
                self.assertEqual(observed.semantics["domains"]["MAKE_RESTARTS"]["value"], "1")
                self.assertEqual(
                    {path: (probe.tree / path).stat().st_mtime_ns for path in inputs}, before,
                )
                timestamps.append(before)
                results.append(observed)
            self.assertIsNone(probe.base)
            self.assertFalse(budget.children)
        self.assertEqual(identities[0], identities[1])
        self.assertNotEqual(timestamps[0], timestamps[1])
        self.assertEqual(results[0].generated, results[1].generated)
        self.assertEqual(results[0].semantics["dynamic_commands"], results[1].semantics["dynamic_commands"])
        self.assertEqual(results[0].semantic_digest, results[1].semantic_digest)
        for before, after in ((1, 2), (2, 3)):
            self.assertNotEqual(identities[before], identities[after])
            self.assertNotEqual(results[before].generated, results[after].generated)
            self.assertNotEqual(results[before].semantic_digest, results[after].semantic_digest)


if __name__ == "__main__":
    unittest.main()
