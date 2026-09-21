"""Domain adapters; execution and all resource authority belong to ProbeSession."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil

from scripts.bash_parser import normalize_bash_script_commands, tokenize_bash_command

from . import python_commands as shared_python_commands
from . import arm_headers, header_effects, toolchain_runtime
from .authority import ENVIRONMENT, encoded, parse_json, relative_path
from .budget import MakeProbeError, text
from .make_probe import Command, ProbeSession
from .graph_lifecycle import _startup_environment
from .graph_regex import CommandPatterns
from .python_commands import GENERATED_DEPENDENCY_MODULES, generated_dependency_command


PYTHON = "/usr/bin/python3"
SCANINC_SOURCES = tuple(
    f"tools/scaninc/{name}.{extension}"
    for name in ("asm_file", "c_file", "scaninc", "source_file") for extension in ("cpp", "h")
)
SCANINC_WRAPPER = "scripts/validation_ownership/scaninc_sources.cpp"
SCANINC_MAKEFILE = "tools/scaninc/Makefile"
CODE_PREFIXES = (
    "scripts/assets/", "scripts/generated_data/", "scripts/modernize/",
    "scripts/localization/",
)
ROOT_RUNTIME_FILES = (
    "/usr/include/newlib/stdlib.h", "/usr/include/build", "/usr/include/.dep",
    "/bin/mkdir", "/bin/env", "/usr/bin/env", "/bin/arm-none-eabi-gcc",
    "/bin/sed",
)
HEADER_STEPS = {
    "modern-output-dry-run-directory": 1,
    "modern-header-scan-dry-run-recipes": 2,
    "modern-header-filter-dry-run-recipes": 3,
    "modern-header-clean-dry-run-recipes": 4,
    "modern-header-move-dry-run-recipes": 5,
}
MODERN_ARCH_QUERY_FLAGS = ("-mcpu=arm7tdmi", "-mthumb", "-mthumb-interwork")
MODERN_COMPILER_NAMES = frozenset(("arm-none-eabi-gcc", "arm-none-eabi-gcc.exe"))
MODERN_DIRECTORY_CONTRACTS = {
    "modern-libgcc-directory": "-print-libgcc-file-name",
    "modern-libc-directory": "-print-file-name=libc.a",
}
FIND_DIRECTORY_BODY = r"""
import ctypes
import errno
import fnmatch
import os
import stat
import sys

BUFFER_SIZE = 4096
GETDENTS64 = 217
DT_UNKNOWN = 0
DT_DIR = 4
DT_REG = 8
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long


def entries(descriptor, path):
    buffer = ctypes.create_string_buffer(BUFFER_SIZE)
    while True:
        ctypes.set_errno(0)
        count = libc.syscall(
            ctypes.c_long(GETDENTS64), ctypes.c_int(descriptor),
            ctypes.c_void_p(ctypes.addressof(buffer)), ctypes.c_size_t(BUFFER_SIZE),
        )
        if count < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), path)
        if count == 0:
            return
        if count > BUFFER_SIZE:
            raise OSError(errno.EIO, "oversized getdents64 result", path)
        data = buffer.raw[:count]
        offset = 0
        while offset < count:
            if count - offset < 20:
                raise OSError(errno.EIO, "truncated getdents64 record", path)
            length = int.from_bytes(data[offset + 16:offset + 18], "little")
            if length < 20 or length > count - offset:
                raise OSError(errno.EIO, "invalid getdents64 record length", path)
            record = data[offset:offset + length]
            end = record.find(b"\0", 19)
            if end <= 19:
                raise OSError(errno.EIO, "unterminated getdents64 name", path)
            name = record[19:end].decode("utf-8", "strict")
            kind = record[18]
            offset += length
            if name not in {".", ".."}:
                yield name, kind


def visit(path):
    descriptor = os.open(
        path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    try:
        for name, kind in entries(descriptor, path):
            child = path + "/" + name
            if kind == DT_UNKNOWN:
                mode = os.stat(name, dir_fd=descriptor, follow_symlinks=False).st_mode
                directory = stat.S_ISDIR(mode)
                regular = stat.S_ISREG(mode)
            else:
                directory = kind == DT_DIR
                regular = kind == DT_REG
            if directory:
                visit(child)
            elif regular and fnmatch.fnmatchcase(name, sys.argv[2]):
                print(child)
    finally:
        os.close(descriptor)


visit(sys.argv[1])
"""


python_import_directories = shared_python_commands.python_import_directories
python_code_closure = shared_python_commands.python_code_closure
python_command = shared_python_commands.python_command
_available_python_paths = shared_python_commands._available_python_paths
_python_module_paths = shared_python_commands._python_module_paths
_python_source_paths = shared_python_commands._python_source_paths


def asset_discovery_command(session: ProbeSession, source: str, logical_output: str):
    relative_path(source)
    relative_path(logical_output)
    discovery = python_command(
        session,
        "import json;from scripts.assets.manifest import load_manifest,discovery_sources;"
        "print(json.dumps(discovery_sources(load_manifest(sys.argv[1]))))",
        (source,), sources=(source,),
        code=("scripts/assets/manifest.py",),
    )
    paths = parse_json(session.command(discovery).stdout, "asset discovery sources")
    if (
        not isinstance(paths, list) or not paths
        or any(not isinstance(path, str) for path in paths)
        or paths != sorted(set(paths))
    ):
        raise MakeProbeError("asset discovery returned an invalid concrete source list")
    sources = session.sources(tuple([source, *paths]))
    identities = session.source_owners(sources)
    return python_command(
        session,
        "import json;from pathlib import Path;"
        "from scripts.assets.manifest import render_discovery_artifact;"
        "logical,content=render_discovery_artifact(sys.argv[1],sys.argv[2],"
        "tracked_sources=frozenset(json.loads(sys.argv[3])),"
        "source_identities=json.loads(sys.argv[4]));"
        "out=Path('/work')/logical;out.parent.mkdir(parents=True,exist_ok=True);"
        "out.write_text(content)",
        (source, logical_output, json.dumps(sources), json.dumps(identities)),
        sources=sources, outputs=(logical_output,), code=("scripts/assets/manifest.py",),
    )


def _normalized_shell_commands(command, label):
    try:
        return normalize_bash_script_commands(command, label)
    except ValueError:
        return ()


def _shell_tokens(command, label):
    try:
        commands = normalize_bash_script_commands(command, label)
        if len(commands) != 1:
            raise MakeProbeError(f"{label} uses an unsupported multi-command shell shape")
        return list(tokenize_bash_command(commands[0]))
    except ValueError as error:
        raise MakeProbeError(str(error)) from error


def _operator(token, value):
    return token.operator and token.value == value


def _simple_words(tokens, label):
    if any(token.operator or token.io_number for token in tokens):
        raise MakeProbeError(f"{label} has unconsumed active shell syntax")
    words = []
    for token in tokens:
        if not token.raw:
            raise MakeProbeError(f"{label} lacks original shell word roles")
        quote, index, value = None, 0, []
        while index < len(token.raw):
            character = token.raw[index]
            if quote == "'":
                if character == "'":
                    quote = None
                else:
                    value.append(character)
            elif character == "\\":
                following = token.raw[index + 1:index + 2]
                if not following:
                    raise MakeProbeError(f"{label} has an incomplete shell escape")
                if quote != '"' or following in '$`"\\\n':
                    if following != "\n":
                        value.append(following)
                    index += 1
                else:
                    value.append(character)
            elif character == quote:
                quote = None
            elif character in "'\"" and quote is None:
                quote = character
            elif character in "$`" or quote is None and character in "*?[]{}~":
                raise MakeProbeError(f"{label} contains unproven active shell expansion")
            elif quote is None and character in " \t\n;&|<>()":
                raise MakeProbeError(f"{label} has malformed shell word roles")
            else:
                value.append(character)
            index += 1
        if quote is not None:
            raise MakeProbeError(f"{label} has incomplete shell quoting")
        words.append("".join(value))
    return words


def _literal_header_words(tokens):
    values = _simple_words(tokens, "literal header producer")
    for token in tokens:
        quote, index = None, 0
        while index < len(token.raw):
            character = token.raw[index]
            if quote == "'":
                if character == "'":
                    quote = None
            elif character == "\\":
                if quote == '"' and token.raw[index + 1:index + 2] in {"$", "`"}:
                    raise MakeProbeError("header producer has an unsupported quoted shell escape")
                if quote != '"' or token.raw[index + 1:index + 2] in {"$", "`", '"', "\\"}:
                    index += 1
            elif character == quote:
                quote = None
            elif character in "'\"" and quote is None:
                quote = character
            elif character in "$`" or quote is None and character in "*?[]{}~":
                raise MakeProbeError("header producer contains active shell expansion")
            index += 1
    return values


def _stderr_redirections(tokens):
    consumed = []
    while len(tokens) >= 3:
        descriptor, operator, destination = tokens[-3:]
        if not descriptor.io_number or descriptor.value != "2" or destination.operator:
            break
        target, = _simple_words((destination,), "stderr redirection")
        if _operator(operator, ">&") and target == "1":
            consumed.append("stdout")
        elif _operator(operator, ">") and target == "/dev/null":
            consumed.append("null")
        else:
            break
        tokens = tokens[:-3]
    return tokens, tuple(reversed(consumed))


def _environment_assignments(tokens):
    environment = {}
    while tokens and tokens[0].assignment:
        assignment, = _simple_words((tokens.pop(0),), "producer environment")
        name, value = assignment.split("=", 1)
        if name != "FE8_ITEM_ID_CAP":
            raise MakeProbeError(f"unsupported domain environment input: {name}")
        environment[name] = value
    return environment


def _python_environment(session, program):
    if not session._live_dispatches:
        return
    context = session._require_live_dispatch()
    environment = dict(context.environment)
    _startup_environment(
        {"environment": environment}, (program,),
        shell=context.arguments[0] in {"/bin/sh", "/bin/bash"},
    )
    controls = {
        name for name, value in environment.items()
        if name.startswith("PYTHON") and name != "PYTHON"
        and (name not in ENVIRONMENT or value != ENVIRONMENT[name])
    }
    if controls:
        raise MakeProbeError(
            "registered Python producer has unsupported startup controls: " + ", ".join(sorted(controls))
        )


def _long_option_values(arguments, label):
    values = {}
    if len(arguments) % 2:
        raise MakeProbeError(f"{label} has an unsupported option shape")
    pairs = iter(arguments)
    for option, value in zip(pairs, pairs):
        if not option.startswith("--") or option in values or not value or "\n" in value or "\r" in value:
            raise MakeProbeError(f"{label} has an unsupported option shape")
        values[option] = value
    return values


def _supported_modern_toolchain_roots():
    return (Path("/usr/bin"), Path("/bin"))


def _resolve_modern_compiler(session, requested):
    if not isinstance(requested, str) or not requested:
        raise MakeProbeError("modern toolchain query requires one supported compiler")
    allowed = tuple(path.resolve() for path in _supported_modern_toolchain_roots())

    def supported(path):
        return (
            path.is_file()
            and os.access(path, os.X_OK)
            and path.name in MODERN_COMPILER_NAMES
            and any(path.parent == directory for directory in allowed)
        )

    if "/" in requested:
        candidate = Path(requested)
        if not candidate.is_absolute():
            candidate = session.loader.root / candidate
        candidate = candidate.resolve()
        if supported(candidate):
            return session.runtime_tool(str(candidate))
    else:
        found = shutil.which(requested, path=ENVIRONMENT["PATH"])
        if found:
            candidate = Path(found).resolve()
            if supported(candidate):
                return session.runtime_tool(str(candidate))
    local_roots = (
        session.loader.root / "build/toolchain-root/usr/bin",
        session.loader.root / ".deps/arm-toolchain-root/usr/bin",
    )
    requested_path = Path(requested)
    if not requested_path.is_absolute():
        requested_path = session.loader.root / requested_path
    if any(
        requested_path.resolve() == (directory / Path(requested).name).resolve()
        for directory in local_roots
    ) or "/" not in requested and any((directory / requested).exists() for directory in local_roots):
        raise MakeProbeError(
            "checkout-local modern compiler lacks a trusted installed-tool identity"
        )
    raise MakeProbeError("modern toolchain query requires one supported arm-none-eabi-gcc compiler")


def _resolve_modern_binutils_flag(argument):
    if not argument.startswith("-B") or len(argument) <= 2:
        raise MakeProbeError("modern toolchain query has an invalid -B binutils directory")
    value = argument[2:]
    directory = Path(value)
    if not directory.is_absolute():
        raise MakeProbeError("modern toolchain query escaped the supported binutils roots")
    directory = directory.resolve()
    allowed = {path.resolve() for path in _supported_modern_toolchain_roots()}
    if directory not in allowed:
        raise MakeProbeError("modern toolchain query escaped the supported binutils roots")
    return argument


def modern_toolchain_directory_command(session, command, contract):
    prefix = "p=$("
    suffix = ') && dirname "$p"'
    if not command.startswith(prefix) or not command.endswith(suffix):
        raise MakeProbeError("modern toolchain directory query differs from its declared command")
    inner = command[len(prefix):-len(suffix)]
    if not inner.endswith(" 2>/dev/null"):
        raise MakeProbeError("modern toolchain directory query must suppress stderr explicitly")
    tokens, redirections = _stderr_redirections(_shell_tokens(inner, "modern toolchain directory query"))
    if redirections != ("null",):
        raise MakeProbeError("modern toolchain directory query must suppress stderr explicitly")
    tokens = _simple_words(tokens, "modern toolchain directory query")
    expected = MODERN_DIRECTORY_CONTRACTS[contract["id"]]
    if len(tokens) not in {len(MODERN_ARCH_QUERY_FLAGS) + 2, len(MODERN_ARCH_QUERY_FLAGS) + 3}:
        raise MakeProbeError("modern toolchain directory query differs from its declared flags")
    flags = tokens[1:-1]
    binutils = ()
    if flags and flags[0].startswith("-B"):
        binutils = (_resolve_modern_binutils_flag(flags[0]),)
        flags = flags[1:]
    if tuple(flags) != MODERN_ARCH_QUERY_FLAGS or tokens[-1] != expected:
        raise MakeProbeError("modern toolchain directory query differs from its declared flags")
    compiler = _resolve_modern_compiler(session, tokens[0])
    return Command(
        (compiler.path, *binutils, *flags, expected),
        code=(contract["tool"],), runtime_tool=compiler, stdout_transform="dirname",
    )


class MakeCommands:
    """Resolve only commands matching one existing sealed domain contract."""

    def __init__(self, session: ProbeSession, contracts):
        self.session = session
        self.contracts = tuple(contracts.values())
        self.patterns = CommandPatterns(
            session.budget, tuple(contract.get("command_regex", r"(?!)") for contract in self.contracts),
        )
        self.requests = []
        self.registrations = {}
        self.scanner = None
        self.scanner_directories = None
        self.includes = {}

    def _matches(self, command):
        matches = [self.contracts[index] for index in self.patterns.fullmatch(command)]
        if matches:
            return matches
        normalized = _normalized_shell_commands(command, "registered command")
        if len(normalized) == 1 and normalized[0] != command:
            return [self.contracts[index] for index in self.patterns.fullmatch(normalized[0])]
        return matches

    def __contains__(self, command):
        return len(self._matches(command)) == 1

    def __getitem__(self, command):
        self.session.budget.remaining()
        matches = self._matches(command)
        if len(matches) != 1:
            raise MakeProbeError(f"command lacks exactly one sealed domain: {command!r}")
        contract = matches[0]
        if (command in self.registrations and contract["id"] not in {
            "legacy-text-dry-run-recipe", toolchain_runtime.CONTRACT, *HEADER_STEPS,
        } and id(self.registrations[command]) not in self.session._native_context_commands):
            return self.registrations[command]
        self.session.budget.charge("cache", len(encoded([contract["id"], command])))
        self.requests.append({"id": contract["id"], "command": command})
        registration = self._register(command, contract)
        self.session.budget.charge("cache", len(encoded([
            registration.argv, registration.code, registration.sources,
            registration.directories, registration.outputs, registration.dependency_only,
            None if registration.runtime_tool is None else (
                registration.runtime_tool.path, registration.runtime_tool.canonical,
                registration.runtime_tool.mode, registration.runtime_tool.digest,
            ),
            registration.stdout_transform,
        ])))
        self.registrations[command] = registration
        return registration

    def _scaninc_candidate(self, spelling):
        if (
            not spelling or spelling.startswith("/") or "\\" in spelling
            or len(spelling.encode("utf-8")) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in spelling)
        ):
            raise MakeProbeError("scaninc include must be canonical and repository-relative after search resolution")
        parts = []
        components = spelling.split("/")
        for index, component in enumerate(components):
            if component in {"", "."}:
                continue
            if component == "..":
                if not parts:
                    raise MakeProbeError("scaninc include must be canonical and repository-relative after search resolution")
                parts.pop()
                continue
            parts.append(component)
            path = "/".join(parts)
            entry = self.session.loader.entries.get(path)
            if entry is not None and (
                entry.mode == "120000"
                or entry.mode not in {"100644", "100755"} and path not in self.scanner_directories
            ):
                raise MakeProbeError("scaninc include resolves to an unadmitted source")
            if index < len(components) - 1 and path not in self.scanner_directories:
                return None
        if not parts:
            raise MakeProbeError("scaninc include resolves to an unadmitted source")
        path = relative_path("/".join(parts))
        entry = self.session.loader.entries.get(path)
        if entry is not None and path not in self.session.snapshot.files:
            raise MakeProbeError("scaninc include resolves to an unadmitted source")
        return path if path in self.session.snapshot.files else None

    def scaninc(self, source):
        source = relative_path(source)
        if self.scanner is None:
            self.scanner = self.session.compile_native(
                (*(path for path in SCANINC_SOURCES if path.endswith(".cpp")), SCANINC_WRAPPER),
                headers=tuple(path for path in SCANINC_SOURCES if path.endswith(".h")),
                cxx=True, defines=("SCANINC_NO_MAIN",),
            )
            self.scanner_directories = {
                parent.as_posix() for path in self.session.loader.entries
                for parent in PurePosixPath(path).parents
            }
            self.session.budget.charge("cache", len(encoded(sorted(self.scanner_directories))))
        pending = [(source, source)]
        sources = set()
        spellings = {source}
        visited = set()
        while pending:
            self.session.budget.remaining()
            if len(visited) >= 4096:
                self.session.budget.reject("scaninc source closure exceeds declaration bound")
            spelling, path = pending.pop()
            if spelling in visited:
                continue
            visited.add(spelling)
            sources.add(path)
            if path not in self.includes:
                result = self.session.native(self.scanner, ("includes", path), sources=(path,))
                includes = text(result.stdout, "scaninc include names").splitlines()
                if includes != sorted(set(includes)):
                    raise MakeProbeError("scaninc returned an invalid include set")
                self.includes[path] = tuple(includes)
                self.session.budget.charge("cache", len(encoded([path, includes])))
            for include in self.includes[path]:
                source_directory = spelling.rpartition("/")[0]
                for directory in ("include", "", source_directory):
                    candidate = directory + "/" + include if directory else include
                    resolved = self._scaninc_candidate(candidate)
                    if resolved is not None:
                        if candidate not in spellings:
                            self.session.budget.charge("cache", len(encoded([candidate, resolved])))
                            spellings.add(candidate)
                            pending.append((candidate, resolved))
                        break
        return Command(
            ("/native/tool", "scan", source, *sorted(spellings)),
            sources=tuple(sorted(sources)), native_tool=self.scanner,
        )

    def dependency(self, command):
        tokens = _shell_tokens(command, "dependency producer")
        if (
            len(tokens) < 8 or [token.value for token in tokens[:2]] != ["mkdir", "-p"]
            or not _operator(tokens[3], "&&") or not _operator(tokens[-2], ">")
        ):
            raise MakeProbeError("dependency producer differs from its declared command")
        prefix = _simple_words(tokens[:3], "dependency producer")
        output = relative_path(_simple_words(tokens[-1:], "dependency producer")[0])
        directory = relative_path(prefix[2].rstrip("/"))
        if directory != str(PurePosixPath(output).parent):
            raise MakeProbeError("dependency producer directory differs from its output")
        arguments = _simple_words(tokens[4:-2], "dependency producer")
        if arguments[0] != "cc":
            raise MakeProbeError("dependency producer requires the supported host C driver")
        arguments[0] = "/usr/bin/cc"
        sources = [argument for argument in arguments
                   if argument.startswith("src/") and argument.endswith(".c")]
        if len(sources) != 1:
            raise MakeProbeError("dependency producer must identify exactly one C source")
        headers = tuple(sorted(
            path for path in self.session.snapshot.files
            if path.startswith(("include/", "src/")) and path.endswith((".h", ".inc"))
        ))
        return Command(
            tuple(arguments), sources=tuple(sources), code=headers,
            outputs=(output,), dependency_only=True,
        )

    def header_step(self, command, step):
        context = self.session._require_live_dispatch()
        target = header_effects.header_target(context.job[1])
        tokens = _shell_tokens(command, "header pipeline")
        if step in {1, 4, 5}:
            return self.session._header_step_command(
                Command(tuple(_literal_header_words(tokens))), step,
            )
        divisions = [index for index, token in enumerate(tokens) if _operator(token, "||")]
        if len(divisions) != 1:
            raise MakeProbeError("header producer lost its exact failure branch")
        main, failure = tokens[:divisions[0]], tokens[divisions[0] + 1:]
        output = target + (".tmp" if step == 2 else ".tmp2")
        if len(main) < 4 or not _operator(main[-2], ">") or _literal_header_words(main[-1:]) != [output]:
            raise MakeProbeError("header producer redirected a different output")
        arguments = _literal_header_words(main[:-2])
        _literal_header_words([token for token in failure if not token.operator][1:-1])
        if step == 2:
            source = relative_path(arguments[-1])
            if (
                not source.endswith(".c") or len(arguments) < 7
                or arguments[-5:-1] != ["-MM", "-MG", "-MT", target[:-len(".headers.d")] + ".o"]
            ):
                raise MakeProbeError("header scan differs from its actual target/source grammar")
            tool = _resolve_modern_compiler(self.session, arguments[0])
            registration = Command(
                (tool.path, *arguments[1:]), sources=(source,), outputs=(output,),
                dependency_only=True, runtime_tool=tool,
            )
            includes, _, _ = arm_headers.options(registration, registration.sources, registration.outputs)
            roots = {"include", "src", str(PurePosixPath(source).parent), *(path for path in includes if path != ".")}
            headers = tuple(sorted(
                path for path in set(self.session.snapshot.files) | set(self.session.published_sources)
                if path.endswith((".h", ".inc")) and any(path.startswith(root + "/") for root in roots)
            ))
            registration = replace(registration, code=headers)
            removed = [output]
            message = "error: failed to pre-scan " + source + " for generated header dependencies"
        else:
            if arguments[:2] != ["sed", "-E"] or len(arguments) != 4 or arguments[3] != target + ".tmp":
                raise MakeProbeError("header filter differs from its actual input grammar")
            arm_headers.filter_expression(arguments[2])
            removed = [target + ".tmp", output]
            message_tokens = [token.value for token in failure]
            if len(message_tokens) < 10 or not message_tokens[8].startswith(
                "error: failed to filter generated header dependencies for "
            ):
                raise MakeProbeError("header filter lost its original failure diagnostic")
            source = relative_path(message_tokens[8].removeprefix(
                "error: failed to filter generated header dependencies for ",
            ))
            pipeline = self.session._header_pipelines.get((context.scope, target))
            if not source.endswith(".c") or pipeline is None or source != pipeline.source:
                raise MakeProbeError("header filter failure branch has an invalid original source")
            message = "error: failed to filter generated header dependencies for " + source
            tool = self.session.runtime_tool("/usr/bin/sed")
            registration = Command(
                (tool.path, "-E", arguments[2], "/repo/" + target + ".tmp"),
                sources=(target + ".tmp",), outputs=(output,), runtime_tool=tool,
            )
        expected = [
            ("{", False), ("rm", False), ("-f", False),
            *((path, False) for path in removed), (";", True),
            ("printf", False), ("%s\\n", False), (message, False),
            (">&", True), ("2", False), (";", True),
            ("exit", False), ("1", False), (";", True), ("}", False),
        ]
        if [(token.value, token.operator) for token in failure] != expected or any(token.io_number for token in failure):
            raise MakeProbeError("header producer has an unsupported failure action or operand")
        return self.session._header_step_command(self.session._native_context_command(registration), step)

    def _register(self, command, contract):
        if contract["id"] == toolchain_runtime.CONTRACT:
            recipe = toolchain_runtime.parse_recipe(command)
            compiler = _resolve_modern_compiler(self.session, recipe.compiler)
            return self.session._toolchain.register(command, compiler)
        if contract["id"] in HEADER_STEPS:
            return self.header_step(command, HEADER_STEPS[contract["id"]])
        if contract["id"] == "legacy-text-dry-run-recipe":
            arguments = _simple_words(_shell_tokens(command, "text producer"), "text producer")
            if (
                len(arguments) != 7 or arguments[:2] != ["python3", "scripts/texttools/textprocess.py"]
            ):
                raise MakeProbeError("text producer differs from its sealed invocation")
            return shared_python_commands.text_generation_command(self.session, *arguments[2:])
        if contract["id"] == "host-uname":
            if _simple_words(_shell_tokens(command, "uname producer"), "uname producer") != ["uname"]:
                raise MakeProbeError("uname producer differs from its declared command")
            return Command(("/usr/bin/uname",))
        if contract["id"] == "legacy-dependency-dry-run-recipes":
            return self.dependency(command)
        if contract["id"] in MODERN_DIRECTORY_CONTRACTS:
            return modern_toolchain_directory_command(self.session, command, contract)
        tokens = _shell_tokens(command, "registered command")
        tokens, redirections = _stderr_redirections(tokens)
        environment = _environment_assignments(tokens)
        if not tokens:
            raise MakeProbeError("empty registered command")
        stdin = None
        if len(tokens) > 4 and [token.value for token in tokens[:2]] == ["printf", "%s\\n"] and _operator(tokens[3], "|"):
            if environment:
                raise MakeProbeError("printf pipeline has an unsupported producer environment")
            left = _simple_words(tokens[:3], "printf pipeline")
            stdin = left[2] + "\n"
            tokens = tokens[4:]
            environment = _environment_assignments(tokens)
        tokens = _simple_words(tokens, "registered command")
        if not tokens:
            raise MakeProbeError("empty registered command")
        if tokens[0] not in {"python3", PYTHON} and (environment or stdin is not None or redirections):
            raise MakeProbeError("registered native command has an unsupported shell wrapper")
        if contract["id"] == "banim-scaninc-inputs":
            if tokens[:5] != ["tools/scaninc/scaninc", "-I", "include", "-I", ""] or len(tokens) != 6:
                raise MakeProbeError("scaninc command differs from its declared include search")
            return self.scaninc(tokens[5])
        if contract["id"] == "asset-discovery-include-remake":
            if environment or stdin is not None or redirections:
                raise MakeProbeError("asset discovery has an unsupported shell wrapper")
            source = tokens[tokens.index("--manifest") + 1]
            destination = tokens[tokens.index("--discovery-makefile") + 1]
            return asset_discovery_command(self.session, source, destination)
        if contract["id"] == "banim-compressing-linker-inputs":
            path = "scripts/arm_compressing_linker.py"
            if tokens[:2] not in (["python3", path], [PYTHON, path]) or environment or stdin is not None or redirections:
                raise MakeProbeError("compressing linker has an unsupported shell wrapper")
            return python_command(
                self.session,
                "import runpy;sys.argv=" + repr([path, *tokens[2:]]) + ";"
                "runpy.run_path('/repo/'+" + repr(path) + ",run_name='__main__')",
                sources=("linker_script_banim.txt",), code=(path,),
            )
        if tokens[0] == "find" and contract["id"] in {
            "legacy-text-source-discovery", "asset-tool-source-discovery",
        }:
            tail = ["-print"] if contract["id"] == "asset-tool-source-discovery" else []
            if (len(tokens) != 6 + len(tail) or tokens[2:5] != ["-type", "f", "-name"]
                    or tokens[6:] != tail):
                raise MakeProbeError("find producer differs from its declared grammar")
            root = relative_path(tokens[1])
            pattern = tokens[tokens.index("-name") + 1]
            sources = tuple(sorted(
                path for path in self.session.snapshot.files if path.startswith(root + "/")
            ))
            directories = sorted({
                root, *(str(parent) for path in sources for parent in PurePosixPath(path).parents
                        if str(parent) != "."),
            })
            return Command(
                (PYTHON, "-I", "-S", "-B", "-c", FIND_DIRECTORY_BODY, root, pattern),
                sources=sources, directories=tuple(directories),
            )
        if tokens[:2] == ["mkdir", "-p"] and contract["id"] in {
            "asset-include-remake-directory", "generated-include-remake-directory",
        }:
            if len(tokens) != 3:
                raise MakeProbeError("directory producer differs from its declared grammar")
            path = relative_path(tokens[2].rstrip("/"))
            if not path.startswith("build/"):
                raise MakeProbeError("producer directory escapes the logical build root")
            return Command((
                PYTHON, "-I", "-S", "-B", "-c",
                "from pathlib import Path;import sys;"
                "(Path('/work')/sys.argv[1]).mkdir(parents=True,exist_ok=True)",
                path,
            ))
        if tokens[0] not in {"python3", PYTHON}:
            raise MakeProbeError(
                f"graph domain needs a typed command adapter: {contract['id']}: {command!r}"
            )
        _python_environment(self.session, tokens[0])
        if "null" in redirections:
            raise MakeProbeError("registered Python stderr discard lacks admitted null-device authority")
        prefix = "import os;os.environ.update(" + repr(environment) + ");"
        for redirect in redirections:
            if redirect != "stdout":
                raise MakeProbeError("registered Python has an unsupported stderr effect")
            prefix += "os.dup2(1,2);"
        if stdin is not None:
            prefix += "import io;sys.stdin=io.StringIO(" + repr(stdin) + ");"
        arguments = tokens[1:]
        python_code = tuple(
            path for path in contract["input_files"]
            if path.endswith(".py")
        )
        if arguments[:1] == ["-c"]:
            if len(arguments) < 2:
                raise MakeProbeError("registered Python -c requires its program")
            python_code = (*python_code, *_python_source_paths(self.session, arguments[1]))
            body = prefix + "sys.argv=['-c']+" + repr(arguments[2:]) + ";exec(" + repr(arguments[1]) + ")"
        elif arguments[:1] == ["-m"]:
            if len(arguments) < 2:
                raise MakeProbeError("registered Python -m requires its module")
            if arguments[1] in GENERATED_DEPENDENCY_MODULES:
                if environment or stdin is not None or redirections:
                    raise MakeProbeError("generated dependency producer uses an unsupported shell wrapper")
                values = _long_option_values(arguments[2:], arguments[1])
                if not {"--make-target", "--depfile"} <= values.keys():
                    raise MakeProbeError("generated dependency producer requires target and depfile")
                make_target, depfile = values.pop("--make-target"), values.pop("--depfile")
                return generated_dependency_command(
                    self.session,
                    arguments[1],
                    option_values=values,
                    make_target=make_target,
                    depfile=depfile,
                    code=python_code,
                )
            python_code = (*python_code, *_python_module_paths(
                _available_python_paths(self.session),
                arguments[1],
                main=True,
            ))
            body = (
                prefix + "import runpy;sys.argv=" + repr(arguments[1:]) + ";"
                "runpy.run_module(" + repr(arguments[1]) + ",run_name='__main__')"
            )
        elif arguments and arguments[0].endswith(".py"):
            path = relative_path(arguments[0])
            python_code = (*python_code, path)
            body = (
                prefix + "import runpy;sys.argv=" + repr(arguments) + ";"
                "runpy.run_path('/repo/'+" + repr(path) + ",run_name='__main__')"
            )
        else:
            raise MakeProbeError(f"unsupported registered Python invocation: {command!r}")
        sources = [
            path for path in contract["input_files"]
            if not path.endswith((".py", ".mk")) and PurePosixPath(path).name != "Makefile"
        ]
        if contract["id"] == "modern-expansion-config-resolution":
            sources.append("config.mk")
        return self.session._native_context_command(python_command(
            self.session, body, sources=tuple(sorted(set(sources))), code=python_code,
        ))
