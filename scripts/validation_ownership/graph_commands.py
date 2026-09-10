"""Domain adapters; execution and all resource authority belong to ProbeSession."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil

from scripts.bash_parser import normalize_bash_script_commands, parse_bash_script_commands

from . import python_commands as shared_python_commands
from .authority import ENVIRONMENT, encoded, parse_json, relative_path
from .budget import MakeProbeError, text
from .make_probe import Command, ProbeSession
from .graph_regex import CommandPatterns
from .python_commands import GENERATED_DEPENDENCY_MODULES, generated_dependency_command


PYTHON = "/usr/bin/python3"
CODE_PREFIXES = (
    "scripts/assets/", "scripts/generated_data/", "scripts/modernize/",
    "scripts/localization/",
)
ROOT_RUNTIME_FILES = (
    "/usr/include/newlib/stdlib.h", "/usr/include/build", "/usr/include/.dep",
    "/bin/mkdir", "/bin/env", "/usr/bin/env", "/bin/arm-none-eabi-gcc",
)
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


def _shell_commands(command, label):
    try:
        return parse_bash_script_commands(command, label)
    except ValueError as error:
        raise MakeProbeError(str(error)) from error


def _normalized_shell_commands(command, label):
    try:
        return normalize_bash_script_commands(command, label)
    except ValueError:
        return ()


def _shell_tokens(command, label):
    commands = _shell_commands(command, label)
    if len(commands) != 1:
        raise MakeProbeError(f"{label} uses an unsupported multi-command shell shape")
    return list(commands[0])


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


def _generated_dependency_selector_options(module, details, values):
    expected = {option for option, _selector in details["selectors"]}
    expected.update(("--make-target", "--depfile"))
    actual = set(values)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("extra " + ", ".join(extra))
        raise MakeProbeError(
            f"{module} options differ from the declared command contract ({'; '.join(detail)})"
        )
    return (
        {option: values[option] for option, _selector in details["selectors"]},
        values["--make-target"],
        values["--depfile"],
    )


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
    tokens = _shell_tokens(inner[:-len(" 2>/dev/null")], "modern toolchain directory query")
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
        if command in self.registrations:
            return self.registrations[command]
        matches = self._matches(command)
        if len(matches) != 1:
            raise MakeProbeError(f"command lacks exactly one sealed domain: {command!r}")
        contract = matches[0]
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
            files = tuple(path for path in self.session.snapshot.files
                          if path.startswith("tools/scaninc/"))
            self.scanner = self.session.compile_native(
                (*sorted(path for path in files if path.endswith(".cpp")),
                 "scripts/validation_ownership/scaninc_sources.cpp"),
                headers=tuple(sorted(path for path in files if path.endswith(".h"))),
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
            tokens[:2] != ["mkdir", "-p"] or len(tokens) < 8
            or tokens[3] != "&&" or tokens[-2] != ">"
            or tokens.count("&&") != 1 or tokens.count(">") != 1
        ):
            raise MakeProbeError("dependency producer differs from its declared command")
        output = relative_path(tokens[-1])
        directory = relative_path(tokens[2].rstrip("/"))
        if directory != str(PurePosixPath(output).parent):
            raise MakeProbeError("dependency producer directory differs from its output")
        arguments = tokens[4:-2]
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

    def _register(self, command, contract):
        if contract["id"] == "host-uname":
            return Command(("/usr/bin/uname",))
        if contract["id"] == "legacy-dependency-dry-run-recipes":
            return self.dependency(command)
        if contract["id"] in MODERN_DIRECTORY_CONTRACTS:
            return modern_toolchain_directory_command(self.session, command, contract)
        tokens = _shell_tokens(command, "registered command")
        while tokens and tokens[-1] in {"2>&1", "2>/dev/null"}:
            tokens.pop()
        environment = {}
        while tokens and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", tokens[0], re.DOTALL):
            name, value = tokens.pop(0).split("=", 1)
            if name != "FE8_ITEM_ID_CAP":
                raise MakeProbeError(f"unsupported domain environment input: {name}")
            environment[name] = value
        if not tokens:
            raise MakeProbeError("empty registered command")
        if contract["id"] == "banim-scaninc-inputs":
            if tokens[:5] != ["tools/scaninc/scaninc", "-I", "include", "-I", ""] or len(tokens) != 6:
                raise MakeProbeError("scaninc command differs from its declared include search")
            return self.scaninc(tokens[5])
        if contract["id"] == "asset-discovery-include-remake":
            source = tokens[tokens.index("--manifest") + 1]
            destination = tokens[tokens.index("--discovery-makefile") + 1]
            return asset_discovery_command(self.session, source, destination)
        if contract["id"] == "banim-compressing-linker-inputs":
            path = "scripts/arm_compressing_linker.py"
            return python_command(
                self.session,
                "import runpy;sys.argv=" + repr([path, *tokens[2:]]) + ";"
                "runpy.run_path('/repo/'+" + repr(path) + ",run_name='__main__')",
                sources=("linker_script_banim.txt",), code=(path,),
            )
        if tokens[0] == "find" and contract["id"] in {
            "legacy-text-source-discovery", "asset-tool-source-discovery",
        }:
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
            path = relative_path(tokens[2].rstrip("/"))
            if not path.startswith("build/"):
                raise MakeProbeError("producer directory escapes the logical build root")
            return Command((
                PYTHON, "-I", "-S", "-B", "-c",
                "from pathlib import Path;import sys;"
                "(Path('/work')/sys.argv[1]).mkdir(parents=True,exist_ok=True)",
                path,
            ))
        stdin = None
        if tokens[:2] == ["printf", "%s\\n"] and len(tokens) > 4 and tokens[3] == "|":
            stdin = tokens[2] + "\n"
            tokens = tokens[4:]
        if tokens[0] not in {"python3", PYTHON}:
            raise MakeProbeError(
                f"graph domain needs a typed command adapter: {contract['id']}: {command!r}"
            )
        prefix = "import os;os.environ.update(" + repr(environment) + ");"
        if stdin is not None:
            prefix += "import io;sys.stdin=io.StringIO(" + repr(stdin) + ");"
        arguments = tokens[1:]
        python_code = tuple(
            path for path in contract["input_files"]
            if path.endswith(".py")
        )
        if arguments[:1] == ["-c"]:
            python_code = (*python_code, *_python_source_paths(self.session, arguments[1]))
            body = prefix + "sys.argv=['-c']+" + repr(arguments[2:]) + ";exec(" + repr(arguments[1]) + ")"
        elif arguments[:1] == ["-m"]:
            if arguments[1] in GENERATED_DEPENDENCY_MODULES:
                if environment or stdin is not None:
                    raise MakeProbeError("generated dependency producer uses an unsupported shell wrapper")
                values = _long_option_values(arguments[2:], arguments[1])
                option_values, make_target, depfile = _generated_dependency_selector_options(
                    arguments[1], GENERATED_DEPENDENCY_MODULES[arguments[1]], values,
                )
                return generated_dependency_command(
                    self.session,
                    arguments[1],
                    option_values=option_values,
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
        return python_command(
            self.session, body, sources=tuple(sorted(set(sources))), code=python_code,
        )
