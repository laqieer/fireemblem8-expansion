"""Domain adapters; execution and all resource authority belong to ProbeSession."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
import re
import shlex

from .authority import encoded, parse_json, relative_path
from .budget import MakeProbeError, text
from .make_probe import Command, ProbeSession
from .graph_regex import CommandPatterns


PYTHON = "/usr/bin/python3"
CODE_PREFIXES = (
    "scripts/assets/", "scripts/generated_data/", "scripts/modernize/",
    "scripts/localization/",
)
ROOT_RUNTIME_FILES = (
    "/usr/include/newlib/stdlib.h", "/usr/include/build", "/usr/include/.dep",
    "/bin/mkdir", "/bin/env", "/usr/bin/env",
)


def python_import_directories(code):
    return tuple(sorted({
        ".", *(parent.as_posix() for path in code for parent in PurePosixPath(path).parents),
    }))


def python_command(session, body, arguments=(), *, sources=(), outputs=(), directories=(), code=()):
    modules = tuple(sorted({
        *code, *(path for path in session.snapshot.files
                 if path.endswith(".py") and path.startswith(CODE_PREFIXES)),
    }))
    return Command(
        (PYTHON, "-I", "-S", "-B", "-c",
         "import sys;sys.path.insert(0,'/repo');" + body, *arguments),
        code=modules, sources=tuple(sources), outputs=tuple(outputs),
        directories=tuple(sorted(set(directories) | set(python_import_directories(modules)))),
    )


def asset_discovery_command(session: ProbeSession, source: str, logical_output: str):
    relative_path(source)
    relative_path(logical_output)
    discovery = python_command(
        session,
        "import json;from scripts.assets.manifest import load_manifest,discovery_sources;"
        "print(json.dumps(discovery_sources(load_manifest(sys.argv[1]))))",
        (source,), sources=(source,),
    )
    paths = parse_json(session.command(discovery).stdout, "asset discovery sources")
    if (
        not isinstance(paths, list) or not paths
        or any(not isinstance(path, str) for path in paths)
        or paths != sorted(set(paths))
    ):
        raise MakeProbeError("asset discovery returned an invalid concrete source list")
    sources = session.sources(tuple([source, *paths]))
    return python_command(
        session,
        "import json;from pathlib import Path;"
        "from scripts.assets.manifest import render_discovery_artifact;"
        "logical,content=render_discovery_artifact(sys.argv[1],sys.argv[2],"
        "tracked_sources=frozenset(json.loads(sys.argv[3])));"
        "out=Path('/work')/logical;out.parent.mkdir(parents=True,exist_ok=True);"
        "out.write_text(content)",
        (source, logical_output, json.dumps(sources)),
        sources=sources, outputs=(logical_output,),
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
        self.includes = {}

    def _matches(self, command):
        return [self.contracts[index] for index in self.patterns.fullmatch(command)]

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
        ])))
        self.registrations[command] = registration
        return registration

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
        pending = [source]
        sources = set()
        while pending:
            self.session.budget.remaining()
            if len(sources) >= 4096:
                self.session.budget.reject("scaninc source closure exceeds declaration bound")
            path = pending.pop()
            if path in sources:
                continue
            sources.add(path)
            if path not in self.includes:
                result = self.session.native(self.scanner, ("includes", path), sources=(path,))
                includes = text(result.stdout, "scaninc include names").splitlines()
                if includes != sorted(set(includes)):
                    raise MakeProbeError("scaninc returned an invalid include set")
                self.includes[path] = tuple(relative_path(name) for name in includes)
                self.session.budget.charge("cache", len(encoded([path, includes])))
            for include in self.includes[path]:
                for directory in ("include", "", str(PurePosixPath(path).parent)):
                    candidate = include if directory in {"", "."} else directory + "/" + include
                    entry = self.session.loader.entries.get(candidate)
                    if entry is not None and candidate not in self.session.snapshot.files:
                        raise MakeProbeError("scaninc include resolves to an unadmitted source")
                    if candidate in self.session.snapshot.files:
                        if candidate not in sources:
                            pending.append(candidate)
                        break
        return Command(
            ("/native/tool", "scan", source, *sorted(sources)),
            sources=tuple(sorted(sources)), native_tool=self.scanner,
        )

    def dependency(self, command):
        tokens = shlex.split(command)
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
        tokens = shlex.split(command)
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
            body = (
                "import fnmatch,os,sys\n"
                "def visit(path):\n"
                "    with os.scandir(path) as entries:\n"
                "        for entry in entries:\n"
                "            if entry.is_dir(follow_symlinks=False): visit(entry.path)\n"
                "            elif entry.is_file(follow_symlinks=False) and "
                "fnmatch.fnmatchcase(entry.name,sys.argv[2]): print(entry.path)\n"
                "visit(sys.argv[1])"
            )
            return Command(
                (PYTHON, "-I", "-S", "-B", "-c", body, root, pattern),
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
        if arguments[:1] == ["-c"]:
            body = prefix + "sys.argv=['-c']+" + repr(arguments[2:]) + ";exec(" + repr(arguments[1]) + ")"
        elif arguments[:1] == ["-m"]:
            body = (
                prefix + "import runpy;sys.argv=" + repr(arguments[1:]) + ";"
                "runpy.run_module(" + repr(arguments[1]) + ",run_name='__main__')"
            )
        elif arguments and arguments[0].endswith(".py"):
            path = relative_path(arguments[0])
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
        return python_command(self.session, body, sources=tuple(sorted(set(sources))))
