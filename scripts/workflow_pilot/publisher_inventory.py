"""One default-deny command authority for trusted publisher shell consumers."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
import re
import stat
import subprocess
import sys
from types import ModuleType

if __package__ in {None, ""}:
    _root = Path(__file__).resolve().parents[2]
    for _name, _path in (
        ("scripts", _root / "scripts"),
        ("scripts.workflow_pilot", _root / "scripts/workflow_pilot"),
    ):
        _package = ModuleType(_name)
        _package.__path__ = [str(_path)]
        sys.modules[_name] = _package
    __package__ = "scripts.workflow_pilot"
    sys.modules[__package__ + ".publisher_inventory"] = sys.modules[__name__]

from . import publisher_shell as shell


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

    def validate_preflight(self, source: str) -> Signature:
        from .publisher_shell_contract import bash_logical_lines
        lines = bash_logical_lines(source, label="publisher authority preflight")
        commands = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
        if len(commands) < 2:
            raise InventoryError("missing publisher authority preflight")
        setup, signature = (
            self.authorize(shell.command(line), "producer") for line in commands[:2]
        )
        expected = Counter({s.name: s.occurrences for s in self.signatures if s.scope == "producer"})
        if (
            Counter((setup.name, signature.name)) != expected
            or setup.events != (EventKind.STATE_WRITE,)
            or signature.program is None
            or signature.program.name != "authority-preflight"
        ):
            raise InventoryError("publisher authority preflight multiplicity differs")
        return signature

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
    return reviewed_inventory().validate(source)


def validate_workflow(workflow: str) -> Analysis:
    from . import publisher_shell_contract
    reviewed_inventory().validate_preflight(
        publisher_shell_contract.publisher_run_script(
            workflow, "Verify exact candidate and stage trusted producer"
        )
    )
    return validate_builder_script(publisher_shell_contract.builder_isolation_shell_source(
        publisher_shell_contract.publisher_run_script(workflow), label="publisher inventory"
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


def authority_paths() -> tuple[str, ...]:
    """Compute the local static import closure, never import a target checkout."""
    pending = [
        "scripts.workflow_pilot.publisher_inventory",
        "scripts.workflow_pilot.publisher_signatures",
        "scripts.workflow_pilot.publisher_programs",
        "scripts.workflow_pilot.publisher_shell_contract",
        "scripts.upstream_port.verify",
    ]
    paths: set[str] = {WORKFLOW_PATH}
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
        source = SOURCE_ROOT / path
        if (
            not source.is_file()
            or source.is_symlink()
            or any((SOURCE_ROOT / parent).is_symlink() for parent in Path(path).parents)
            or source.stat().st_nlink != 1
            or source.stat().st_size > 1024 * 1024
        ):
            raise InventoryError("publisher authority module is missing or redirected")
        paths.add(path)
        components = name.split(".")
        for index in range(1, len(components)):
            package_path = "/".join(components[:index]) + "/__init__.py"
            if (SOURCE_ROOT / package_path).exists():
                pending.append(".".join(components[:index]))
        tree = ast.parse(source.read_text(encoding="utf-8"))
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
    return tuple(sorted(paths))


def bind_exact_tree(repository_root: Path | str, commit: str) -> tuple[str, ...]:
    """Bind loaded authority and target worktree to Git, without stored hashes."""
    root = Path(repository_root).resolve()
    if re.fullmatch(r"[0-9a-f]{40}", commit) is None or set(commit) == {"0"}:
        raise InventoryError("publisher authority requires a full immutable commit")
    if _git(root, "rev-parse", "--verify", commit + "^{commit}").decode().strip() != commit:
        raise InventoryError("publisher commit does not resolve exactly")
    if Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise InventoryError("publisher repository is not the exact Git root")
    paths = authority_paths()
    for path in paths:
        entry = _git(root, "ls-tree", "-z", commit, "--", path).split(b"\0")
        if len(entry) != 2 or not entry[0]:
            raise InventoryError(f"publisher exact-tree module missing: {path}")
        metadata, actual_path = entry[0].split(b"\t", 1)
        mode, kind, oid = metadata.split()
        if actual_path.decode() != path or kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise InventoryError(f"publisher exact-tree module redirected: {path}")
        expected = _git(root, "cat-file", "blob", oid.decode())
        for base in {root, SOURCE_ROOT}:
            actual = base / path
            try:
                for parent in actual.relative_to(base).parents:
                    if (base / parent).is_symlink():
                        raise InventoryError(f"publisher module parent redirected: {path}")
                status = actual.lstat()
                if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
                    raise InventoryError(f"publisher module is not a regular file: {path}")
                executable = bool(status.st_mode & 0o111)
                if executable != (mode == b"100755") or actual.read_bytes() != expected:
                    raise InventoryError(f"publisher authority differs from exact tree: {path}")
            except OSError as error:
                raise InventoryError(f"cannot read publisher module: {path}") from error
    return paths


def validate_exact_tree(repository_root: Path | str, commit: str) -> Analysis:
    bind_exact_tree(repository_root, commit)
    workflow = _git(Path(repository_root), "show", commit + ":" + WORKFLOW_PATH).decode("utf-8")
    return validate_workflow(workflow)


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args(arguments)
    try:
        analysis = validate_exact_tree(args.repository_root, args.commit)
    except (InventoryError, shell.ShellSyntaxError, OSError, ValueError) as error:
        print(f"publisher command authority: {error}", file=sys.stderr)
        return 1
    print(f"publisher command authority: {len(analysis.signatures)} reviewed commands")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
