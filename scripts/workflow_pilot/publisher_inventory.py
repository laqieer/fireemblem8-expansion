"""One default-deny command authority for trusted publisher shell consumers."""

from __future__ import annotations

import __future__
import argparse
import ast
import builtins
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from functools import lru_cache
import importlib.abc
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import sysconfig
from types import CodeType, MappingProxyType, ModuleType


CASE_ID = "TC-WORKFLOW-PUBLISHER-COMMAND-INVENTORY-001"
WORKFLOW_PATH = ".github/workflows/build.yml"
PROGRAM_PATH = "scripts/workflow_pilot/publisher_programs.py"
PROGRAM_RUNTIME_PATH = "/mnt/control/publisher-programs.py"
SOURCE_ROOT = Path(__file__).resolve().parents[2]
MAX_AUTHORITY_BYTES = 1024 * 1024
ENTRY_SCOPES = frozenset({"entry", "producer", "staging", "candidate"})
STDLIB_ROOTS = tuple({
    Path(path).resolve() for path in (
        sysconfig.get_path("stdlib"), sysconfig.get_path("platstdlib"),
        sysconfig.get_config_var("DESTSHARED"),
    ) if path
})
FUTURE_FLAGS = sum(
    getattr(__future__, name).compiler_flag for name in __future__.all_feature_names
)
_ACTIVE_SOURCE_AUTHORITY = None
_EXECUTION_AUDIT_INSTALLED = False


class InventoryError(ValueError):
    pass


class Family(str, Enum):
    ASSIGNMENT = "assignment"
    BUILTIN = "builtin"
    EXECUTABLE = "executable"
    HELPER = "helper"
    PYTHON = "python"


class ProgramKind(str, Enum):
    FILE = "file"
    INLINE = "inline"
    MODULE = "module"


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
    NEGATION = "negation"


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
    conditional: bool = False


def normalize_invocation(
    command: shell.Command, *, executable: shell.Word | None = None,
) -> Invocation:
    """Normalize wrappers; a symbolic executable needs an exact registry-owned Word."""
    argv = list(command.argv)
    wrappers: list[Wrapper] = []
    while argv:
        name = argv[0].literal
        if name == "builtin":
            wrappers.append(Wrapper(WrapperKind.BUILTIN, (argv.pop(0),)))
        elif argv[0].keyword("!"):
            wrappers.append(Wrapper(WrapperKind.NEGATION, (argv.pop(0),)))
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
    if executable is not None and (
        not isinstance(executable, shell.Word)
        or not executable.parts or executable.assignment
        or not any(part.kind == "literal" and part.value for part in executable.parts)
        or any(
            part.kind != "literal" and not (
                part.kind == "parameter" and part.quoted
                and isinstance(part.value, str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", part.value)
            )
            for part in executable.parts
        )
        or not argv or argv[0] != executable
    ):
        raise InventoryError("publisher executable differs from its registered Word")
    if command.argv and (
        not argv or (argv[0].literal is None and executable is None)
        or (argv[0].literal is not None and (
            not argv[0].literal or argv[0].literal.startswith("-")
        ))
    ):
        raise InventoryError("dynamic or unmatched publisher executable")
    return Invocation(
        argv[0] if argv else None, tuple(argv[1:]), command.environment,
        tuple(wrappers), command.redirects,
        command.conditional,
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
    redirects: tuple[shell.Redirect, ...] = ()
    wrappers: tuple[Wrapper, ...] = ()
    interpreter: shell.Word = field(
        default_factory=lambda: shell.command("/usr/bin/python3").argv[0],
    )
    startup: tuple[str, ...] = ("-I", "-S")
    kind: ProgramKind = ProgramKind.FILE
    text: str | None = None
    environment: tuple[shell.Word, ...] = ()

    def invocation_prefix(self) -> tuple[shell.Word, ...]:
        """Describe Python dispatch honestly, independently of a submitted command."""
        if (
            not isinstance(self.kind, ProgramKind)
            or not isinstance(self.name, str) or not self.name
            or not isinstance(self.source_path, str) or not self.source_path
            or not isinstance(self.interpreter, shell.Word)
            or not isinstance(self.startup, tuple)
            or any(
                not isinstance(flag, str) or flag not in {"-I", "-S", "-E", "-s", "-B", "-u", "-P"}
                for flag in self.startup
            )
            or len(set(self.startup)) != len(self.startup)
            or not isinstance(self.runtime_path, str) or not self.runtime_path
            or (self.mode is not None and (not isinstance(self.mode, str) or not self.mode))
        ):
            raise InventoryError("incomplete Python program profile")
        target = shell.command("program " + self.runtime_path)
        if len(target.argv) != 2 or target.environment or target.redirects:
            raise InventoryError("Python program requires one exact target Word")
        selected = target.argv[1]
        suffix = ()
        if self.kind == ProgramKind.INLINE:
            if (
                selected.literal != "-c" or self.mode is not None
                or not isinstance(self.text, str) or not self.text
            ):
                raise InventoryError("inline Python requires independently registered program text")
            suffix = (shell.Word((shell.Part("literal", self.text),)),)
        elif self.kind == ProgramKind.MODULE:
            if (
                selected.literal != "-m" or self.text is not None or self.mode is None
                or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?:\.[A-Za-z_][A-Za-z_0-9]*)*", self.mode) is None
            ):
                raise InventoryError("module Python requires an exact module identity")
            suffix = (shell.Word((shell.Part("literal", self.mode),)),)
        else:
            if self.text is not None or (selected.literal is not None and (
                not selected.literal or selected.literal.startswith("-")
            )):
                raise InventoryError("file Python requires an exact program path")
            if self.mode is not None:
                suffix = (shell.Word((shell.Part("literal", self.mode),)),)
        startup = tuple(shell.Word((shell.Part("literal", flag),)) for flag in self.startup)
        return startup + (selected,) + suffix


@dataclass(frozen=True)
class Payload:
    delimiter: str
    language: str


@dataclass(frozen=True)
class Context:
    kind: str
    identity: str
    branch: str = ""


@dataclass(frozen=True)
class Placement:
    context: tuple[Context, ...] = ()
    occurrences: int = 1


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
    placements: tuple[Placement, ...] = (Placement(),)
    payloads: tuple[Payload, ...] = ()
    executable: shell.Word | None = None

    @property
    def invocation(self) -> Invocation:
        executable = self.executable
        if executable is None and isinstance(self.program, Program):
            executable = self.program.interpreter
        return normalize_invocation(self.form, executable=executable)


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
    context: tuple[Context, ...] = ()


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
    if isinstance(node, shell.While):
        return ("while", node.condition)
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
            or set(scope_names) & ENTRY_SCOPES
            or len(control_names) != len(set(control_names))
            or len({(s.scope, s.invocation) for s in self.signatures}) != len(names)
            or len({(c.scope, c.header, c.context) for c in self.controls}) != len(control_names)
        ):
            raise InventoryError("duplicate command, helper or control signature")
        scope_set = set(scope_names) | ENTRY_SCOPES
        python_interpreters = {
            s.program.interpreter for s in self.signatures if isinstance(s.program, Program)
        }
        for signature in self.signatures:
            if (
                signature.scope not in scope_set
                or type(signature.occurrences) is not int
                or signature.occurrences < 0
                or not signature.placements
                or any(
                    not isinstance(p, Placement)
                    or type(p.occurrences) is not int or p.occurrences < 0
                    or not isinstance(p.context, tuple)
                    or any(not isinstance(c, Context) for c in p.context)
                    for p in signature.placements
                )
                or len({p.context for p in signature.placements}) != len(signature.placements)
                or any(
                    not isinstance(p, Payload)
                    or p.language not in {"builder", "shell", "python"}
                    for p in signature.payloads
                )
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
            invocation = signature.invocation
            if signature.family == Family.PYTHON:
                argv = invocation.arguments
                program = signature.program
                prefix = program.invocation_prefix() if isinstance(program, Program) else ()
                if (
                    not isinstance(program, Program)
                    or not program.inputs
                    or invocation.executable != program.interpreter
                    or argv[:len(prefix)] != prefix
                    or invocation.environment != program.environment
                    or invocation.redirects != program.redirects
                    or invocation.wrappers != program.wrappers
                    or not set(program.inputs + program.outputs) <= set(signature.accesses)
                ):
                    raise InventoryError("Python signature must select its exact registered program profile")
            elif signature.program is not None:
                raise InventoryError("program metadata on a non-Python signature")
            elif invocation.executable is not None and (
                invocation.executable.literal is None
                or invocation.executable.literal == "/usr/bin/python3"
                or invocation.executable in python_interpreters
            ):
                raise InventoryError("Python executables require a registered Program")
        if any(
            scope.parent not in scope_set
            or any(not isinstance(parameter, Resource) for parameter in scope.parameters)
            for scope in self.scopes
        ):
            raise InventoryError("unknown helper parent")
        if any(
            c.scope not in scope_set or type(c.occurrences) is not int or c.occurrences < 1
            or not isinstance(c.context, tuple)
            or any(not isinstance(context, Context) for context in c.context)
            for c in self.controls
        ):
            raise InventoryError("incomplete control signature")

    def _invocation(self, command: shell.Command, scope: str) -> Invocation:
        try:
            return normalize_invocation(command)
        except InventoryError:
            for signature in self.signatures:
                if signature.scope != scope or signature.program is None:
                    continue
                executable = signature.program.interpreter
                if executable.literal is None:
                    try:
                        return normalize_invocation(command, executable=executable)
                    except InventoryError:
                        pass
            raise

    def authorize(
        self, command: shell.Command, scope: str, context: tuple[Context, ...] = (),
    ) -> Signature:
        invocation = self._invocation(command, scope)
        matches = [
            s for s in self.signatures
            if s.scope == scope and s.invocation == invocation
            and any(p.context == context for p in s.placements)
        ]
        if len(matches) != 1:
            executable = command.argv[0].literal if command.argv else "(assignment)"
            raise InventoryError(
                f"unregistered publisher command/context in {scope}: {executable!r}, {context!r}"
            )
        return matches[0]

    def entry_scope(self, scope: str) -> str:
        parents = {item.name: item.parent for item in self.scopes}
        seen = set()
        while scope in parents:
            if scope in seen:
                raise InventoryError("recursive publisher scope")
            seen.add(scope)
            scope = parents[scope]
        return scope

    def validate_preflight(self, source: str) -> Signature:
        analysis = self.validate(source, entry_scope="producer")
        commands = [item for item in analysis.commands if not item.nested]
        if [item.signature.name for item in commands[:3]] != [
            "producer.strict-shell", "producer.authority-preflight", "producer.git-environment",
        ]:
            raise InventoryError("publisher authority preflight order differs")
        return commands[1].signature

    def validate_producer(self, preflight: str, staging: str) -> None:
        """Authorize both complete trusted steps, not just their required prologues."""
        self.validate_preflight(preflight)
        analysis = self.validate(staging, entry_scope="staging")
        commands = [item for item in analysis.commands if not item.nested]
        if [item.signature.name for item in commands[:2]] != [
            "staging.git-environment", "staging.program-source",
        ]:
            raise InventoryError("publisher program staging order differs")

    def validate(self, source: str, *, entry_scope: str = "entry") -> Analysis:
        if entry_scope not in ENTRY_SCOPES:
            raise InventoryError(f"unknown publisher entry scope: {entry_scope!r}")
        tree = shell.parse(source)
        active = {entry_scope}
        while True:
            added = {s.name for s in self.scopes if s.parent in active} - active
            if not added:
                break
            active.update(added)
        command_index = {
            (s.scope, s.invocation, p.context): s
            for s in self.signatures for p in s.placements
        }
        control_index = {(c.scope, c.header, c.context): c for c in self.controls}
        scope_index = {s.name: s for s in self.scopes if s.name in active}
        counts: Counter[str] = Counter()
        placements: Counter[tuple[str, tuple[Context, ...]]] = Counter()
        control_counts: Counter[str] = Counter()
        definitions: dict[str, shell.Function] = {}
        authorized: list[AuthorizedCommand] = []
        by_node: dict[int, AuthorizedCommand] = {}
        call_graph: dict[str, set[str]] = {name: set() for name in scope_index}
        call_graph[entry_scope] = set()
        payloads = None

        def check_payloads(command: shell.Command, signature: Signature):
            nonlocal payloads
            actual = tuple(r for r in command.redirects if r.operator == "<<")
            if tuple(r.target.literal for r in actual) != tuple(p.delimiter for p in signature.payloads):
                raise InventoryError("unregistered publisher program payload")
            if not actual:
                return
            from . import publisher_shell_contract as contract
            if payloads is None:
                canonical = shell.parse(contract.publisher_run_script(
                    authority_source_bytes(WORKFLOW_PATH).decode("utf-8"),
                ))
                payloads = {
                    r.target.literal: r.body
                    for chain in canonical.items for node in chain.nodes
                    if isinstance(node, shell.Command)
                    for r in node.redirects if r.operator == "<<"
                }
            for redirect, payload in zip(actual, signature.payloads):
                if payload.language == "builder":
                    contract.validate_builder_command_inventory(redirect.body)
                else:
                    expected = payloads.get(payload.delimiter)
                    if expected is None:
                        raise InventoryError(f"missing canonical publisher program: {payload.delimiter}")
                    normalize = ast.parse if payload.language == "python" else shell.parse
                    left, right = normalize(redirect.body), normalize(expected)
                    if payload.language == "python":
                        left, right = ast.dump(left), ast.dump(right)
                    if left != right:
                        raise InventoryError(f"publisher canonical program differs: {payload.delimiter}")

        def record(command: shell.Command, scope: str, context: tuple[Context, ...], visible: set[str]):
            signature = command_index.get((scope, self._invocation(command, scope), context))
            if signature is None:
                return self.authorize(command, scope, context)
            check_payloads(command, signature)
            counts[signature.name] += 1
            placements[signature.name, context] += 1
            item = AuthorizedCommand(signature, command, scope, context)
            authorized.append(item)
            by_node[id(command)] = item
            all_commands = [(command, False, ())]
            all_commands += [
                (nested, True, (Context("substitution", signature.name),
                    *chain_context(chain, next(i for i, node in enumerate(chain.nodes) if node is nested))))
                for nested, chain in nested_commands(command)
            ]
            for current, nested, extra in all_commands:
                executable = self._invocation(current, scope).executable
                if executable and executable.literal in scope_index:
                    callee = executable.literal
                    if callee not in visible:
                        raise InventoryError(f"helper {callee} used before its definition")
                    call_graph[scope].add(callee)
                if nested:
                    nested_item = AuthorizedCommand(signature, current, scope, context + extra, True)
                    authorized.append(nested_item)
                    by_node[id(current)] = nested_item

        def chain_context(chain: shell.Chain, index: int) -> tuple[Context, ...]:
            result: tuple[Context, ...] = ()
            if chain.operators:
                result += (Context("operators", " ".join(chain.operators), str(index)),)
            if chain.background:
                result += (Context("background", "&"),)
            return result

        def walk(block: shell.Block, scope: str, context: tuple[Context, ...], visible: set[str]):
            visible = set(visible)
            for chain in block.items:
                for index, node in enumerate(chain.nodes):
                    execution = context + chain_context(chain, index)
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
                        rule = control_index.get((scope, control_header(node), execution))
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
                        elif isinstance(node, shell.While):
                            walk(node.condition, scope, execution + (Context("while", rule.name, "condition"),), visible)
                            walk(node.body, scope, execution + (Context("while", rule.name, "body"),), visible)
                        else:
                            for index, arm in enumerate(node.arms):
                                walk(arm.body, scope, execution + (Context("case", rule.name, str(index)),), visible)

        walk(tree, entry_scope, (), set())
        if set(definitions) != set(scope_index):
            raise InventoryError("publisher helper inventory is incomplete")
        expected_counts = Counter({
            s.name: s.occurrences for s in self.signatures
            if s.occurrences and s.scope in active
        })
        if counts != expected_counts:
            raise InventoryError("publisher command inventory multiplicity differs")
        if placements != Counter({
            (s.name, p.context): p.occurrences
            for s in self.signatures if s.scope in active
            for p in s.placements if p.occurrences
        }):
            raise InventoryError("publisher command context multiplicity differs")
        if control_counts != Counter({c.name: c.occurrences for c in self.controls if c.scope in active}):
            raise InventoryError("publisher control inventory multiplicity differs")

        def acyclic(name: str, ancestors: tuple[str, ...]):
            if name in ancestors:
                raise InventoryError("recursive publisher helper")
            for callee in call_graph[name]:
                acyclic(callee, ancestors + (name,))

        acyclic(entry_scope, ())
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
                    elif isinstance(node, shell.While):
                        emit(node.condition, stack, inherited)
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
            executable = self._invocation(node, item.scope).executable
            if executable and executable.literal in definitions:
                callee = executable.literal
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
    staging = publisher_shell_contract.publisher_run_script(workflow)
    reviewed_inventory().validate_producer(
        publisher_shell_contract.publisher_run_script(
            workflow, "Verify exact candidate and stage trusted producer"
        ), staging,
    )
    return validate_builder_script(publisher_shell_contract.builder_isolation_shell_source(
        staging, label="publisher inventory"
    ))


def _git(root: Path, *arguments: str, max_bytes: int | None = None) -> bytes:
    environment = {
        "PATH": "/usr/bin:/bin", "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        if max_bytes is not None:
            with subprocess.Popen(
                ["/usr/bin/git", "--no-replace-objects", "-C", str(root), *arguments],
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            ) as process:
                data = process.stdout.read(max_bytes + 1)
                if len(data) > max_bytes:
                    process.kill()
                    raise InventoryError("publisher authority blob exceeds bounds")
                if process.wait() != 0 or len(data) != max_bytes:
                    raise InventoryError("cannot read complete publisher authority blob")
                return data
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
            source = handle.read(MAX_AUTHORITY_BYTES + 1)
            if len(source) > MAX_AUTHORITY_BYTES:
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
        size_text = _git(root, "cat-file", "-s", oid.decode()).strip()
        if not size_text.isdigit() or len(size_text) > 10:
            raise InventoryError(f"invalid publisher authority blob size: {path}")
        size = int(size_text)
        if size > MAX_AUTHORITY_BYTES:
            raise InventoryError(f"publisher authority blob exceeds bounds: {path}")
        expected = _git(root, "cat-file", "blob", oid.decode(), max_bytes=size)
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
            # Program payloads remain captured data, not validator dependencies.
            if path == PROGRAM_PATH or not path.endswith(".py"):
                continue
            package = path.endswith("/__init__.py")
            name = path.removesuffix("/__init__.py") if package else path[:-3]
            name = name.replace("/", ".")
            self.modules[name] = (path, package)
            components = name.split(".")
            for index in range(1, len(components)):
                self.modules.setdefault(".".join(components[:index]), (None, True))
        self.executable_sources = {
            str(SOURCE_ROOT / path): sources[path]
            for path, _package in self.modules.values() if path is not None
        }
        self.code_trees = {}
        self.generated_source = None
        self.checking_generated_source = False

    def code_tree(self, filename):
        """Derive executable code from the same captured/stdlib source authority."""
        if filename not in self.code_trees:
            if filename in self.executable_sources:
                code = compile(
                    self.executable_sources[filename], filename, "exec", dont_inherit=True,
                )
            elif filename.startswith("<frozen ") and filename.endswith(">"):
                name = filename[len("<frozen "):-1]
                if (
                    name.split(".", 1)[0] not in sys.stdlib_module_names
                    or importlib.machinery.FrozenImporter.find_spec(name) is None
                ):
                    raise InventoryError(f"untrusted publisher frozen code: {filename}")
                code = importlib.machinery.FrozenImporter.get_code(name)
            elif Path(filename).is_absolute() and self.trusted_path(filename):
                path = Path(filename)
                if path.suffix != ".py":
                    raise InventoryError(f"no publisher stdlib source for code: {filename}")
                code = compile(path.read_bytes(), filename, "exec", dont_inherit=True)
            else:
                raise InventoryError(f"executable outside publisher authority: {filename}")

            def descendants(value):
                yield value
                for constant in value.co_consts:
                    if isinstance(constant, CodeType):
                        yield from descendants(constant)

            self.code_trees[filename] = frozenset(descendants(code))
        return self.code_trees[filename]

    def record_compile(self, source, filename, caller):
        if (
            not self.checking_generated_source
            and isinstance(source, (str, bytes)) and isinstance(filename, str)
            and filename.startswith("<") and not filename.startswith("<frozen ")
        ):
            self.generated_source = (source, filename, caller)

    def check_execution(self, code, caller):
        filename = code.co_filename
        if filename.startswith("<") and not filename.startswith("<frozen "):
            generated = self.generated_source
            self.generated_source = None
            origin = caller.f_code.co_filename
            if (
                generated is not None and generated[1] == filename and generated[2] is caller
                and Path(origin).is_absolute() and self.trusted_path(origin)
                and caller.f_code in self.code_tree(origin)
            ):
                # Match the actual stdlib generation, not a loader's call stack
                # or an anonymous filename forged by cached bytecode.
                self.checking_generated_source = True
                try:
                    for mode in ("exec", "eval", "single"):
                        try:
                            expected = compile(
                                generated[0], filename, mode, dont_inherit=True,
                                flags=caller.f_code.co_flags & FUTURE_FLAGS,
                            )
                        except SyntaxError:
                            continue
                        if code == expected:
                            return
                finally:
                    self.checking_generated_source = False
        elif code in self.code_tree(filename):
            return
        raise InventoryError(f"publisher executable differs from allowed source: {filename}")

    def find_spec(self, fullname, path=None, target=None):
        self.check_name(fullname)
        if fullname in self.modules:
            source_path, package = self.modules[fullname]
            return importlib.util.spec_from_loader(
                fullname, self, origin="publisher-exact:" + (source_path or fullname),
                is_package=package,
            )
        for finder in (importlib.machinery.BuiltinImporter, importlib.machinery.FrozenImporter):
            spec = finder.find_spec(fullname)
            if spec is not None:
                return spec
        search = STDLIB_ROOTS if path is None else tuple(Path(item) for item in path)
        for directory in search:
            if not self.trusted_path(directory):
                raise InventoryError(f"untrusted publisher stdlib search path: {fullname}")
            finder = importlib.machinery.FileFinder(
                str(directory),
                (importlib.machinery.ExtensionFileLoader, importlib.machinery.EXTENSION_SUFFIXES),
                (importlib.machinery.SourceFileLoader, importlib.machinery.SOURCE_SUFFIXES),
            )
            spec = finder.find_spec(fullname)
            if spec is not None and self.trusted_spec(fullname, spec):
                return spec
        raise InventoryError(f"no trusted publisher stdlib source: {fullname}")

    @staticmethod
    def trusted_path(path):
        path = Path(path).resolve()
        return (
            any(path.is_relative_to(root) for root in STDLIB_ROOTS)
            and not {"site-packages", "dist-packages"} & set(path.parts)
        )

    def trusted_spec(self, name, spec):
        if spec is None:
            return False
        if name in self.modules:
            return spec.loader is self
        if name.split(".", 1)[0] not in sys.stdlib_module_names | {"__future__"}:
            return False
        if spec.origin in {"built-in", "frozen"}:
            finder = (
                importlib.machinery.BuiltinImporter if spec.origin == "built-in"
                else importlib.machinery.FrozenImporter
            )
            return spec.loader is finder and finder.find_spec(spec.name) is not None
        return (
            type(spec.loader) in {
                importlib.machinery.SourceFileLoader, importlib.machinery.ExtensionFileLoader,
            }
            and spec.origin is not None and self.trusted_path(spec.origin)
        )

    def check_name(self, name):
        if (
            name not in self.modules
            and name.split(".", 1)[0] not in sys.stdlib_module_names | {"__future__"}
        ):
            raise InventoryError(f"import outside publisher authority: {name}")
        components = name.split(".")
        for index in range(1, len(components) + 1):
            prefix = ".".join(components[:index])
            module = sys.modules.get(prefix)
            if module is not None and not self.trusted_spec(prefix, getattr(module, "__spec__", None)):
                raise InventoryError(f"untrusted publisher import origin: {prefix}")

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


def _audit_source_execution(event, arguments):
    loader = _ACTIVE_SOURCE_AUTHORITY
    if loader is None:
        return
    if event == "compile":
        loader.record_compile(*arguments, sys._getframe(1))
    elif event == "exec":
        loader.check_execution(arguments[0], sys._getframe(1))
    elif event == "import" and arguments[1] is not None:
        name, filename = arguments[:2]
        if (
            name.split(".", 1)[0] not in sys.stdlib_module_names
            or not loader.trusted_path(filename)
        ):
            raise InventoryError(f"untrusted publisher native import origin: {name}")


@contextmanager
def _source_only_authority(sources):
    """Exclude both on-disk caches and previously imported repository modules."""
    global _ACTIVE_SOURCE_AUTHORITY, _EXECUTION_AUDIT_INSTALLED
    loader = _SourceOnlyAuthority(sources)
    if not _EXECUTION_AUDIT_INSTALLED:
        sys.addaudithook(_audit_source_execution)
        _EXECUTION_AUDIT_INSTALLED = True
    previous_authority = _ACTIVE_SOURCE_AUTHORITY
    cached_modules = sys.modules.copy()
    previous = {
        name: module for name, module in cached_modules.items()
        if not loader.trusted_spec(name, getattr(module, "__spec__", None))
    }
    missing = object()
    package_bindings = {}
    # From-import can use a parent's cached attribute without consulting sys.modules.
    for name, module in previous.items():
        parent_name, _, child = name.rpartition(".")
        parent = cached_modules.get(parent_name)
        if parent is not None and parent_name not in previous:
            namespace = vars(parent)
            value = namespace.get(child, missing)
            package_bindings[id(parent), child] = (namespace, child, value, value is module)
    for name, module in cached_modules.items():
        if name in previous:
            continue
        namespace = vars(module)
        for child, value in namespace.copy().items():
            if isinstance(value, ModuleType) and not loader.trusted_spec(
                value.__name__, getattr(value, "__spec__", None),
            ):
                package_bindings[id(module), child] = (namespace, child, value, True)
    for namespace, child, value, remove in package_bindings.values():
        if remove:
            namespace.pop(child, None)
    for name in previous:
        del sys.modules[name]
    sys.meta_path.insert(0, loader)
    original_import = builtins.__import__
    original_import_module = importlib.import_module

    def checked_import(name, globals=None, locals=None, fromlist=(), level=0):
        absolute = name
        if level:
            absolute = importlib.util.resolve_name(
                "." * level + name, (globals or {}).get("__package__"),
            )
        loader.check_name(absolute)
        result = original_import(name, globals, locals, fromlist, level)
        for child in fromlist or ():
            fullname = absolute + "." + child
            if fullname in sys.modules:
                loader.check_name(fullname)
        return result

    def checked_import_module(name, package=None):
        absolute = importlib.util.resolve_name(name, package) if name.startswith(".") else name
        loader.check_name(absolute)
        return original_import_module(name, package)

    builtins.__import__ = checked_import
    importlib.import_module = checked_import_module
    _ACTIVE_SOURCE_AUTHORITY = loader
    try:
        yield
    except SystemExit as error:
        raise InventoryError("publisher authority terminated before validation completed") from error
    finally:
        _ACTIVE_SOURCE_AUTHORITY = previous_authority
        builtins.__import__ = original_import
        importlib.import_module = original_import_module
        sys.meta_path.remove(loader)
        for name in tuple(sys.modules):
            if name in previous or name == "scripts" or name.startswith("scripts."):
                del sys.modules[name]
        sys.modules.update(previous)
        for namespace, child, value, remove in package_bindings.values():
            if value is missing:
                namespace.pop(child, None)
            else:
                namespace[child] = value


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
