"""One default-deny command authority for trusted publisher shell consumers."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from functools import lru_cache
import importlib.abc
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import MappingProxyType


CASE_ID = "TC-WORKFLOW-PUBLISHER-COMMAND-INVENTORY-001"
WORKFLOW_PATH = ".github/workflows/build.yml"
PROGRAM_PATH = "scripts/workflow_pilot/publisher_programs.py"
PROGRAM_RUNTIME_PATH = "/mnt/control/publisher-programs.py"
SOURCE_ROOT = Path(__file__).resolve().parents[2]


class InventoryError(ValueError):
    pass


class Family(str, Enum):
    ASSIGNMENT = "assignment"
    BUILTIN = "builtin"
    EXECUTABLE = "executable"
    HELPER = "helper"
    PYTHON = "python"


class Resource(str, Enum):
    SHELL = "shell-state"
    CGROUP_RAW = "raw-cgroup"
    CGROUP_VIEW = "supervisor-cgroup"
    MOUNT_GRAPH = "mount-graph"
    HOST = "host-tree"
    CONTROL = "trusted-control"
    SUPERVISOR = "supervisor-transport"
    RUNTIME = "runtime-transport"
    CANDIDATE = "candidate"
    HANDOFF = "candidate-handoff"
    EXPORT = "public-export"
    NULL = "null-device"
    PROCESS = "process"


class Access(str, Enum):
    READ = "read"
    WRITE = "write"
    INSPECT = "inspect"
    EXECUTE = "execute"
    MOUNT = "mount"
    CREATE = "create"
    REMOVE = "remove"


class EventKind(str, Enum):
    COMMAND = "command"
    STATE_WRITE = "state-write"
    HELPER_CALL = "helper-call"
    CGROUP_JOIN = "cgroup-join"
    CGROUP_BIND = "cgroup-bind"
    CGROUP_READONLY = "cgroup-readonly"
    LEGACY_MEMBERSHIP = "legacy-membership-observation"
    MEMBERSHIP_VERIFIED = "membership-verified"
    MOUNT_AUDIT = "mount-audit"
    TRANSPORT_READ = "transport-read"
    CANDIDATE_LAUNCH = "candidate-launch"
    CANDIDATE_STATUS = "candidate-status"
    EXPORT_OPEN = "export-open"
    EXPORT_FILE = "export-file"
    EXPORT_CLOSE = "export-close"
    POST_CHECK = "post-check"


class WrapperKind(str, Enum):
    BUILTIN = "builtin"
    COMMAND = "command"
    ENVIRONMENT = "environment"
    TIME = "time"


@dataclass(frozen=True)
class Wrapper:
    kind: WrapperKind
    arguments: tuple[shell.Word, ...]


@dataclass(frozen=True)
class Invocation:
    executable: shell.Word | None
    arguments: tuple[shell.Word, ...]
    environment: tuple[shell.Word, ...]
    wrappers: tuple[Wrapper, ...]
    redirects: tuple[shell.Redirect, ...]


def normalize_invocation(command: shell.Command) -> Invocation:
    """Normalize only explicit wrapper grammar; never discard a prefix."""
    argv = list(command.argv)
    wrappers: list[Wrapper] = []
    while argv:
        name = argv[0].literal
        if name == "builtin":
            wrappers.append(Wrapper(WrapperKind.BUILTIN, (argv.pop(0),)))
        elif name == "command":
            prefix = [argv.pop(0)]
            while argv and argv[0].literal in {"-p", "--"}:
                prefix.append(argv.pop(0))
            wrappers.append(Wrapper(WrapperKind.COMMAND, tuple(prefix)))
        elif argv[0].keyword("time"):
            prefix = [argv.pop(0)]
            while argv and argv[0].literal in {"-p", "--"}:
                prefix.append(argv.pop(0))
            wrappers.append(Wrapper(WrapperKind.TIME, tuple(prefix)))
        elif name == "/usr/bin/env":
            prefix = [argv.pop(0)]
            if not argv or argv[0].literal != "-i":
                raise InventoryError("unregistered environment wrapper grammar")
            prefix.append(argv.pop(0))
            while argv and argv[0].assignment:
                prefix.append(argv.pop(0))
            wrappers.append(Wrapper(WrapperKind.ENVIRONMENT, tuple(prefix)))
        else:
            break
        if len(wrappers) > 8:
            raise InventoryError("publisher wrapper nesting exceeds bounds")
    if command.argv and (
        not argv or argv[0].literal is None or argv[0].literal.startswith("-")
    ):
        raise InventoryError("dynamic or unmatched publisher executable")
    return Invocation(
        argv[0] if argv else None, tuple(argv[1:]), command.environment,
        tuple(wrappers), command.redirects,
    )


@dataclass(frozen=True)
class ResourceAccess:
    resource: Resource
    access: Access


@dataclass(frozen=True)
class Program:
    name: str
    source_path: str
    runtime_path: str
    mode: str | None
    inputs: tuple[ResourceAccess, ...]
    outputs: tuple[ResourceAccess, ...]


@dataclass(frozen=True)
class Signature:
    name: str
    scope: str
    form: shell.Command
    family: Family
    occurrences: int
    accesses: tuple[ResourceAccess, ...]
    events: tuple[EventKind, ...] = (EventKind.COMMAND,)
    program: Program | None = None
    evidence: str = CASE_ID

    @property
    def invocation(self) -> Invocation:
        return normalize_invocation(self.form)


@dataclass(frozen=True)
class Scope:
    name: str
    parent: str
    parameters: tuple[Resource, ...]


@dataclass(frozen=True)
class Control:
    name: str
    scope: str
    header: tuple
    occurrences: int = 1


@dataclass(frozen=True)
class Context:
    kind: str
    identity: str
    branch: str = ""


@dataclass(frozen=True)
class AuthorizedCommand:
    signature: Signature
    command: shell.Command
    scope: str
    context: tuple[Context, ...]
    nested: bool = False


@dataclass(frozen=True)
class Event:
    kind: EventKind
    signature: str
    scope: str
    context: tuple[Context, ...]
    call_stack: tuple[str, ...]
    accesses: tuple[ResourceAccess, ...]
    command: shell.Command


@dataclass(frozen=True)
class Analysis:
    tree: shell.Block
    commands: tuple[AuthorizedCommand, ...]
    events: tuple[Event, ...]
    signatures: tuple[str, ...]


def control_header(node: object) -> tuple:
    if isinstance(node, shell.If):
        return ("if", node.condition, node.failure is not None)
    if isinstance(node, shell.For):
        return ("for", node.variable, node.values, node.arithmetic)
    if isinstance(node, shell.Case):
        return ("case", node.subject, tuple(arm.patterns for arm in node.arms))
    raise InventoryError("unregistered publisher control node")


def nested_commands(command: shell.Command):
    for word in (*command.environment, *command.argv, *(r.target for r in command.redirects)):
        for part in word.parts:
            if part.kind == "substitution":
                for chain in part.value.items:
                    for node in chain.nodes:
                        if not isinstance(node, shell.Command):
                            raise InventoryError("control inside command substitution")
                        yield node, chain
                        yield from nested_commands(node)


@dataclass(frozen=True)
class Inventory:
    signatures: tuple[Signature, ...]
    scopes: tuple[Scope, ...]
    controls: tuple[Control, ...]

    def __post_init__(self):
        names = [item.name for item in self.signatures]
        scope_names = [item.name for item in self.scopes]
        control_names = [item.name for item in self.controls]
        if (
            len(names) != len(set(names))
            or len(scope_names) != len(set(scope_names))
            or len(control_names) != len(set(control_names))
            or len({(s.scope, s.invocation) for s in self.signatures}) != len(names)
            or len({(c.scope, c.header) for c in self.controls}) != len(control_names)
        ):
            raise InventoryError("duplicate command, helper or control signature")
        scope_set = set(scope_names) | {"entry", "producer"}
        for signature in self.signatures:
            if (
                signature.scope not in scope_set
                or type(signature.occurrences) is not int
                or signature.occurrences < 0
                or not signature.accesses
                or not signature.events
                or signature.evidence != CASE_ID
                or not isinstance(signature.family, Family)
                or any(not isinstance(event, EventKind) for event in signature.events)
                or any(
                    not isinstance(item, ResourceAccess)
                    or not isinstance(item.resource, Resource)
                    or not isinstance(item.access, Access)
                    for item in signature.accesses
                )
            ):
                raise InventoryError("incomplete command signature")
            if signature.family == Family.PYTHON:
                argv = signature.form.argv
                program = signature.program
                if (
                    not isinstance(program, Program)
                    or not program.inputs
                    or tuple(word.literal for word in argv[:4])
                    != ("/usr/bin/python3", "-I", "-S", program.runtime_path)
                    or signature.form.environment
                    or signature.form.redirects
                    or (program.mode is not None and (len(argv) <= 4 or argv[4].literal != program.mode))
                    or not set(program.inputs + program.outputs) <= set(signature.accesses)
                ):
                    raise InventoryError("Python signature must select an exact isolated program")
            elif signature.program is not None:
                raise InventoryError("program metadata on a non-Python signature")
        if any(
            scope.parent not in scope_set
            or any(not isinstance(parameter, Resource) for parameter in scope.parameters)
            for scope in self.scopes
        ):
            raise InventoryError("unknown helper parent")
        if any(c.scope not in scope_set or c.occurrences < 1 for c in self.controls):
            raise InventoryError("incomplete control signature")

    def authorize(self, command: shell.Command, scope: str) -> Signature:
        invocation = normalize_invocation(command)
        matches = [s for s in self.signatures if s.scope == scope and s.invocation == invocation]
        if len(matches) != 1:
            executable = command.argv[0].literal if command.argv else "(assignment)"
            raise InventoryError(f"unregistered publisher command in {scope}: {executable!r}")
        return matches[0]

    def _producer_prefix(self, source: str, count: int) -> tuple[Signature, ...]:
        from .publisher_shell_contract import bash_logical_lines
        lines = bash_logical_lines(source, label="publisher authority preflight")
        commands = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
        if len(commands) < count:
            raise InventoryError("missing publisher producer command")
        prefix = tuple(
            self.authorize(shell.command(line), "producer") for line in commands[:count]
        )
        if len(commands) > count:
            try:
                self.authorize(shell.command(commands[count]), "producer")
            except ValueError:
                pass
            else:
                raise InventoryError("publisher producer prologue exceeds reviewed multiplicity")
        return prefix

    def validate_preflight(self, source: str) -> Signature:
        setup, signature, environment = self._producer_prefix(source, 3)
        if (
            setup.form.argv[0].literal != "set"
            or setup.events != (EventKind.STATE_WRITE,)
            or signature.program is None
            or signature.program.name != "authority-preflight"
            or signature.occurrences != 1
            or setup.occurrences != 1
            or environment.form.argv[0].literal != "unset"
            or environment.occurrences != 2
        ):
            raise InventoryError("publisher authority preflight multiplicity differs")
        return signature

    def validate_producer(self, preflight: str, staging: str) -> None:
        """Bind both fresh-step prologues to the same typed producer inventory."""
        self.validate_preflight(preflight)
        first = self._producer_prefix(preflight, 3)
        second = self._producer_prefix(staging, 3)
        expected = Counter({
            s.name: s.occurrences for s in self.signatures if s.scope == "producer"
        })
        if second[0] != first[2] or Counter(s.name for s in first + second) != expected:
            raise InventoryError("publisher program staging inventory differs")

    def validate(self, source: str) -> Analysis:
        tree = shell.parse(source)
        command_index = {(s.scope, s.invocation): s for s in self.signatures}
        control_index = {(c.scope, c.header): c for c in self.controls}
        scope_index = {s.name: s for s in self.scopes}
        counts: Counter[str] = Counter()
        control_counts: Counter[str] = Counter()
        definitions: dict[str, shell.Function] = {}
        authorized: list[AuthorizedCommand] = []
        by_node: dict[int, AuthorizedCommand] = {}
        call_graph: dict[str, set[str]] = {name: set() for name in scope_index}
        call_graph["entry"] = set()

        def record(command: shell.Command, scope: str, context: tuple[Context, ...], visible: set[str]):
            signature = command_index.get((scope, normalize_invocation(command)))
            if signature is None:
                return self.authorize(command, scope)
            counts[signature.name] += 1
            item = AuthorizedCommand(signature, command, scope, context)
            authorized.append(item)
            by_node[id(command)] = item
            all_commands = [(command, False, ())]
            all_commands += [
                (nested, True, (Context("substitution", signature.name),
                    *chain_context(chain)))
                for nested, chain in nested_commands(command)
            ]
            for current, nested, extra in all_commands:
                if current.argv and current.argv[0].literal in scope_index:
                    callee = current.argv[0].literal
                    if callee not in visible:
                        raise InventoryError(f"helper {callee} used before its definition")
                    call_graph[scope].add(callee)
                if nested:
                    nested_item = AuthorizedCommand(signature, current, scope, context + extra, True)
                    authorized.append(nested_item)
                    by_node[id(current)] = nested_item

        def chain_context(chain: shell.Chain) -> tuple[Context, ...]:
            result: tuple[Context, ...] = ()
            if chain.operators:
                result += (Context("operators", " ".join(chain.operators)),)
            if chain.background:
                result += (Context("background", "&"),)
            return result

        def walk(block: shell.Block, scope: str, context: tuple[Context, ...], visible: set[str]):
            visible = set(visible)
            for chain in block.items:
                execution = context + chain_context(chain)
                for node in chain.nodes:
                    if isinstance(node, shell.Command):
                        record(node, scope, execution, visible)
                    elif isinstance(node, shell.Function):
                        expected = scope_index.get(node.name)
                        if (
                            expected is None
                            or expected.parent != scope
                            or node.name in definitions
                            or execution
                        ):
                            raise InventoryError("unknown, duplicate or conditional helper definition")
                        definitions[node.name] = node
                        visible.add(node.name)
                        walk(node.body, node.name, (), visible)
                    else:
                        rule = control_index.get((scope, control_header(node)))
                        if rule is None:
                            raise InventoryError(f"unregistered publisher control in {scope}")
                        control_counts[rule.name] += 1
                        if isinstance(node, shell.If):
                            walk(node.condition, scope, execution + (Context("if", rule.name, "condition"),), visible)
                            walk(node.success, scope, execution + (Context("if", rule.name, "success"),), visible)
                            if node.failure is not None:
                                walk(node.failure, scope, execution + (Context("if", rule.name, "failure"),), visible)
                        elif isinstance(node, shell.For):
                            walk(node.body, scope, execution + (Context("loop", rule.name),), visible)
                        else:
                            for index, arm in enumerate(node.arms):
                                walk(arm.body, scope, execution + (Context("case", rule.name, str(index)),), visible)

        walk(tree, "entry", (), set())
        if set(definitions) != set(scope_index):
            raise InventoryError("publisher helper inventory is incomplete")
        expected_counts = Counter({
            s.name: s.occurrences for s in self.signatures
            if s.occurrences and s.scope != "producer"
        })
        if counts != expected_counts:
            raise InventoryError("publisher command inventory multiplicity differs")
        if control_counts != Counter({c.name: c.occurrences for c in self.controls}):
            raise InventoryError("publisher control inventory multiplicity differs")

        def acyclic(name: str, ancestors: tuple[str, ...]):
            if name in ancestors:
                raise InventoryError("recursive publisher helper")
            for callee in call_graph[name]:
                acyclic(callee, ancestors + (name,))

        acyclic("entry", ())
        events: list[Event] = []

        def emit(block: shell.Block, stack: tuple[str, ...], inherited: tuple[Context, ...]):
            for chain in block.items:
                for node in chain.nodes:
                    if isinstance(node, shell.Function):
                        continue
                    if isinstance(node, shell.Command):
                        for nested, _chain in nested_commands(node):
                            emit_command(nested, stack, inherited)
                        emit_command(node, stack, inherited)
                    elif isinstance(node, shell.If):
                        emit(node.condition, stack, inherited)
                        emit(node.success, stack, inherited)
                        if node.failure is not None:
                            emit(node.failure, stack, inherited)
                    elif isinstance(node, shell.For):
                        emit(node.body, stack, inherited)
                    else:
                        for arm in node.arms:
                            emit(arm.body, stack, inherited)

        def emit_command(node: shell.Command, stack: tuple[str, ...], inherited: tuple[Context, ...]):
            if len(events) > 8192 or len(stack) > 32:
                raise InventoryError("publisher helper expansion exceeds bounds")
            item = by_node[id(node)]
            context = inherited + item.context
            kinds = (EventKind.COMMAND,) if item.nested else item.signature.events
            for kind in kinds:
                events.append(Event(
                    kind, item.signature.name, item.scope, context, stack,
                    item.signature.accesses, node,
                ))
            if node.argv and node.argv[0].literal in definitions:
                callee = node.argv[0].literal
                emit(definitions[callee].body, stack + (callee,), context)

        emit(tree, (), ())
        return Analysis(tree, tuple(authorized), tuple(events), tuple(sorted(counts.elements())))


@lru_cache(maxsize=1)
def reviewed_inventory() -> Inventory:
    from .publisher_signatures import inventory
    return inventory()


def validate_builder_script(source: str) -> Analysis:
    from . import publisher_phase
    analysis = reviewed_inventory().validate(source)
    publisher_phase.validate(analysis)
    return analysis


def validate_workflow(workflow: str) -> Analysis:
    from . import publisher_shell_contract
    staging = publisher_shell_contract.publisher_run_script(workflow)
    reviewed_inventory().validate_producer(
        publisher_shell_contract.publisher_run_script(
            workflow, "Verify exact candidate and stage trusted producer"
        ), staging,
    )
    return validate_builder_script(publisher_shell_contract.builder_isolation_shell_source(
        staging, label="publisher inventory"
    ))


def _git(root: Path, *arguments: str) -> bytes:
    environment = {
        "PATH": "/usr/bin:/bin", "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        return subprocess.run(
            ["/usr/bin/git", "--no-replace-objects", "-C", str(root), *arguments],
            env=environment, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise InventoryError("cannot bind publisher authority to the exact Git tree") from error


def _read_authority_file(base: Path, path: str) -> tuple[bytes, bool]:
    actual = base / path
    try:
        if any((base / parent).is_symlink() for parent in Path(path).parents):
            raise InventoryError(f"publisher module parent redirected: {path}")
        descriptor = os.open(actual, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as handle:
            status = os.fstat(handle.fileno())
            if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
                raise InventoryError(f"publisher module is not a regular file: {path}")
            source = handle.read(1024 * 1024 + 1)
            if len(source) > 1024 * 1024:
                raise InventoryError(f"publisher authority source exceeds bounds: {path}")
            return source, bool(status.st_mode & 0o111)
    except OSError as error:
        raise InventoryError(f"cannot read publisher module: {path}") from error


def authority_source_bytes(path: str) -> bytes:
    """Use the captured source in an exact-tree execution, never reopen its path."""
    sources = globals().get("_VERIFIED_AUTHORITY_SOURCES")
    if sources is not None:
        try:
            return sources[path]
        except KeyError as error:
            raise InventoryError(f"source outside publisher authority: {path}") from error
    return _read_authority_file(SOURCE_ROOT, path)[0]


def _authority_sources(read_source) -> dict[str, bytes]:
    """Derive the static import closure from captured source, without importing it."""
    pending = [
        "scripts.workflow_pilot.publisher_inventory",
        "scripts.workflow_pilot.publisher_signatures",
        "scripts.workflow_pilot.publisher_programs",
        "scripts.workflow_pilot.publisher_candidate",
        "scripts.workflow_pilot.publisher_shell_contract",
        "scripts.upstream_port.verify",
    ]
    sources = {WORKFLOW_PATH: read_source(WORKFLOW_PATH)}
    modules: set[str] = set()
    while pending:
        if len(modules) > 128 or len(pending) > 256:
            raise InventoryError("publisher import closure exceeds bounds")
        name = pending.pop()
        if name in modules:
            continue
        modules.add(name)
        path = name.replace(".", "/") + ".py"
        if not (SOURCE_ROOT / path).is_file():
            path = name.replace(".", "/") + "/__init__.py"
        sources[path] = read_source(path)
        components = name.split(".")
        for index in range(1, len(components)):
            package_path = "/".join(components[:index]) + "/__init__.py"
            if (SOURCE_ROOT / package_path).exists():
                pending.append(".".join(components[:index]))
        tree = ast.parse(sources[path], filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                package = name if path.endswith("/__init__.py") else ".".join(components[:-1])
                if node.level:
                    package = ".".join(package.split(".")[:len(package.split(".")) - node.level + 1])
                    base = package + ("." + node.module if node.module else "")
                else:
                    base = node.module or ""
                targets = [base + "." + alias.name for alias in node.names]
                if (SOURCE_ROOT / (base.replace(".", "/") + ".py")).is_file():
                    targets = [base]
            else:
                continue
            for target in targets:
                if target.startswith("scripts."):
                    candidate = SOURCE_ROOT / (target.replace(".", "/") + ".py")
                    if candidate.is_file():
                        pending.append(target)
                    elif (SOURCE_ROOT / target.replace(".", "/") / "__init__.py").is_file():
                        pending.append(target)
                    else:
                        raise InventoryError("unresolved publisher authority import")
                elif target.split(".", 1)[0] not in sys.stdlib_module_names | {"__future__"}:
                    raise InventoryError("unregistered external publisher authority import")
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == "__import__"
                or isinstance(node.func, ast.Attribute)
                and node.func.attr in {"import_module", "spec_from_file_location", "exec_module"}
            ):
                raise InventoryError("dynamic publisher authority import")
    return dict(sorted(sources.items()))


def authority_paths() -> tuple[str, ...]:
    """Compute the local static import closure, never import a target checkout."""
    return tuple(_authority_sources(authority_source_bytes))


def _bind_exact_sources(repository_root: Path | str, commit: str):
    """Capture and verify every source before any local authority import."""
    root = Path(repository_root).resolve()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None or set(commit) == {"0"}:
        raise InventoryError("publisher authority requires a full immutable commit")
    if _git(root, "rev-parse", "--verify", commit + "^{commit}").decode().strip() != commit:
        raise InventoryError("publisher commit does not resolve exactly")
    if Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise InventoryError("publisher repository is not the exact Git root")

    def read_source(path: str) -> bytes:
        entry = _git(root, "ls-tree", "-z", commit, "--", path).split(b"\0")
        if len(entry) != 2 or not entry[0]:
            raise InventoryError(f"publisher exact-tree module missing: {path}")
        metadata, actual_path = entry[0].split(b"\t", 1)
        mode, kind, oid = metadata.split()
        if actual_path.decode() != path or kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise InventoryError(f"publisher exact-tree module redirected: {path}")
        expected = _git(root, "cat-file", "blob", oid.decode())
        for base in {root, SOURCE_ROOT}:
            actual, executable = _read_authority_file(base, path)
            if executable != (mode == b"100755") or actual != expected:
                raise InventoryError(f"publisher authority differs from exact tree: {path}")
        return expected

    return MappingProxyType(_authority_sources(read_source))


def bind_exact_tree(repository_root: Path | str, commit: str) -> tuple[str, ...]:
    """Bind trusted source and target worktree to Git, without stored hashes."""
    return tuple(_bind_exact_sources(repository_root, commit))


class _SourceOnlyAuthority(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """The sealed-capsule source-only loader pattern, without a capsule dependency."""

    def __init__(self, sources):
        self.sources = sources
        self.modules = {}
        for path in sources:
            if not path.endswith(".py"):
                continue
            package = path.endswith("/__init__.py")
            name = path.removesuffix("/__init__.py") if package else path[:-3]
            name = name.replace("/", ".")
            self.modules[name] = (path, package)
            components = name.split(".")
            for index in range(1, len(components)):
                self.modules.setdefault(".".join(components[:index]), (None, True))

    def find_spec(self, fullname, path=None, target=None):
        if fullname != "scripts" and not fullname.startswith("scripts."):
            return None
        if fullname not in self.modules:
            raise InventoryError(f"import outside publisher authority: {fullname}")
        source_path, package = self.modules[fullname]
        return importlib.util.spec_from_loader(
            fullname, self, origin="publisher-exact:" + (source_path or fullname),
            is_package=package,
        )

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        path, package = self.modules[module.__spec__.name]
        if package:
            module.__path__ = []
        if path is not None:
            module.__file__ = str(SOURCE_ROOT / path)
            module.__dict__["_VERIFIED_AUTHORITY_SOURCES"] = self.sources
            exec(compile(self.sources[path], module.__file__, "exec", dont_inherit=True), module.__dict__)


@contextmanager
def _source_only_authority(sources):
    """Exclude both on-disk caches and previously imported repository modules."""
    loader = _SourceOnlyAuthority(sources)
    previous = {
        name: module for name, module in sys.modules.copy().items()
        if name == "scripts" or name.startswith("scripts.")
    }
    for name in previous:
        del sys.modules[name]
    sys.meta_path.insert(0, loader)
    try:
        yield
    finally:
        sys.meta_path.remove(loader)
        for name in tuple(sys.modules):
            if name == "scripts" or name.startswith("scripts."):
                del sys.modules[name]
        sys.modules.update(previous)


def validate_exact_tree(repository_root: Path | str, commit: str) -> Analysis:
    sources = _bind_exact_sources(repository_root, commit)
    with _source_only_authority(sources):
        from scripts.workflow_pilot import publisher_inventory as verified
        analysis = verified.validate_workflow(sources[WORKFLOW_PATH].decode("utf-8"))
    if __name__ == "__main__":
        return analysis
    return _public_analysis(analysis)


def _public_analysis(analysis) -> Analysis:
    """Preserve public record types and shared AST references across import isolation."""
    copied = {}

    def copy(value):
        if not isinstance(value, (tuple, Enum)) and not is_dataclass(value):
            return value
        if id(value) not in copied:
            if isinstance(value, tuple):
                result = tuple(copy(item) for item in value)
            elif isinstance(value, Enum):
                result = globals()[type(value).__name__](value.value)
            else:
                namespace = vars(shell) if type(value).__module__.endswith(".publisher_shell") else globals()
                record = namespace[type(value).__name__]
                result = record(**{field.name: copy(getattr(value, field.name)) for field in fields(value)})
            copied[id(value)] = result
        return copied[id(value)]

    return copy(analysis)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args(arguments)
    try:
        analysis = validate_exact_tree(args.repository_root, args.commit)
    except (OSError, ValueError) as error:
        print(f"publisher command authority: {error}", file=sys.stderr)
        return 1
    print(f"publisher command authority: {len(analysis.signatures)} reviewed commands")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
else:
    from . import publisher_shell as shell
