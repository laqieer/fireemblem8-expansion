"""Success-path proof over the closed publisher inventory, not a Bash runner.

Events describe syntax. Completion is credited only at the success edge of a
foreground launch with an immediate status capture and a closed failure guard.
Bash waits/reaps the exec-only launcher; membership and post-check additionally
authenticate their real parent/session and inspect kernel/filesystem state.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum

from . import publisher_shell as shell
from .publisher_inventory import (
    Analysis, Context, Event, EventKind, InventoryError, control_header,
    reviewed_inventory,
)


CASE_ID = "TC-WORKFLOW-PUBLISHER-PHASE-001"
CANDIDATE_STAGES = ("preflight", "venv", "pip", "build-tools", "make", "handoff")


class Phase(str, Enum):
    PREPARING = "preparing"
    LAUNCH_STARTED = "launch-started"
    LAUNCH_REAPED = "launch-completed-and-reaped"
    MEMBERSHIP_VERIFIED = "membership-verified"
    EXPORT_STARTED = "export-started"
    EXPORT_COMMITTED = "export-committed"
    POST_CHECKED = "post-check-completed"


class PhaseError(InventoryError):
    pass


@dataclass(frozen=True)
class Transition:
    before: Phase
    after: Phase
    event: Event


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise PhaseError("publisher phase: " + reason)


def _context(kind: str, identity: str, branch: str = "") -> Context:
    return Context(kind, "builder_main." + identity, branch)


def _nodes(block: shell.Block) -> tuple:
    _require(block is not None, "missing mandatory control body")
    result = []
    for chain in block.items:
        _require(
            not chain.background and not chain.operators and len(chain.nodes) == 1,
            "mandatory block has an execution operator",
        )
        result.append(chain.nodes[0])
    return tuple(result)


def _walk(value):
    yield value
    if is_dataclass(value):
        for field in fields(value):
            yield from _walk(getattr(value, field.name))
    elif isinstance(value, tuple):
        for item in value:
            yield from _walk(item)


def _literal_command(node, *arguments):
    return (
        isinstance(node, shell.Command) and not node.environment
        and not node.redirects and not node.conditional
        and tuple(word.literal for word in node.argv) == arguments
    )


def _candidate_codes() -> dict[str, int]:
    registry = reviewed_inventory()
    control, = (c for c in registry.controls if c.name == "staging.isolated-result")
    result = {}
    for signature in registry.signatures:
        if not signature.name.startswith("staging.isolated-candidate-"):
            continue
        placement, = signature.placements
        context, = (c for c in placement.context if c.kind == "case" and c.identity == control.name)
        pattern, = control.header[2][int(context.branch)]
        result[signature.name.removeprefix("staging.isolated-candidate-")] = int(pattern.literal)
    _require(set(result) == set(CANDIDATE_STAGES) | {"unknown"}, "incomplete candidate diagnostic contract")
    return result


def _candidate_operation(command: shell.Command) -> str | None:
    """Identify phase effects, not a second command/argument permission list."""
    if not command.argv:
        return None
    executable = command.argv[0].literal
    arguments = tuple(word.literal for word in command.argv[1:])
    if executable == "/usr/bin/python3":
        if arguments[:2] == ("-m", "venv"):
            return "venv"
        if arguments[:3] == ("-I", "-S", "-c"):
            return "fd-check"
    if (
        command.argv[0] == shell.command('run "$HOME/venv/bin/python3"').argv[1]
        and arguments[:3] == ("-m", "pip", "install")
    ):
        return "pip"
    return {
        "test": "check", "cd": "chdir", "./build_tools.sh": "build-tools",
        "make": "make", "/usr/bin/make": "make", "/usr/bin/install": "handoff",
    }.get(executable)


def _candidate_program(source: str) -> None:
    """Prove stage dataflow within the authority's canonical, data-only payload."""
    tree = shell.parse(source)
    root = _nodes(tree)
    commands = tuple(node for node in _walk(tree) if isinstance(node, shell.Command))
    root_commands = tuple(node for node in root if isinstance(node, shell.Command))
    traps = tuple(node for node in commands if node.argv and node.argv[0].literal == "trap")
    armed = tuple(node for node in root_commands if node in traps)
    _require(len(armed) == 1 and len(armed[0].argv) == 3, "candidate ERR callback is not unique")
    trap = armed[0]
    handler_name = trap.argv[1].literal
    _require(_literal_command(trap, "trap", handler_name, "ERR"), "candidate ERR callback differs")
    definitions = tuple(
        node for node in _walk(tree) if isinstance(node, shell.Function) and node.name == handler_name
    )
    _require(len(definitions) == 1 and definitions[0] in root, "candidate callback is not a root definition")
    handler = definitions[0]
    body = _nodes(handler.body)
    _require(
        len(body) == 2 and _literal_command(body[0], "trap", "-", "ERR")
        and isinstance(body[1], shell.Case) and len(traps) == 2,
        "candidate callback must disable recursion and select one fixed exit",
    )
    case = body[1]
    _require(
        len(case.subject.parts) == 1 and case.subject.parts[0].kind == "parameter",
        "candidate callback must read its stage variable",
    )
    variable = case.subject.parts[0].value
    expected = _candidate_codes()
    wildcard = shell.command("pattern *").argv[1]
    labels = []
    for arm in case.arms:
        _require(len(arm.patterns) == 1, "candidate exit stages must be disjoint")
        label = "*" if arm.patterns[0] == wildcard else arm.patterns[0].literal
        _require(label in set(CANDIDATE_STAGES) | {"*"}, "unknown candidate exit stage")
        labels.append(label)
        exits = _nodes(arm.body)
        _require(
            len(exits) == 1 and _literal_command(
                exits[0], "exit", str(expected["unknown" if label == "*" else label]),
            ),
            "candidate stage-to-exit mapping differs from the registered host diagnostic",
        )
    _require(
        len(labels) == len(expected) and set(labels) == set(CANDIDATE_STAGES) | {"*"}
        and labels[-1] == "*" and case.arms[-1].patterns[0] == wildcard,
        "candidate unknown-stage exit must be the final unconditional fallback",
    )
    writes = []
    for node in commands:
        assignment_words = node.environment
        if node.argv and node.argv[0].literal in {"declare", "export", "local", "readonly", "typeset"}:
            assignment_words += node.argv[1:]
        if any(word.assignment == variable for word in assignment_words):
            writes.append(node)
    root_writes = tuple(node for node in root_commands if any(node is item for item in writes))
    _require(len(writes) == len(root_writes), "candidate stage write escaped its foreground root frame")
    stages = {}
    for node in root_writes:
        _require(
            len(node.environment) == 1 and not node.argv and not node.redirects
            and not node.conditional and node.environment[0].literal is not None,
            "candidate stage must be one literal standalone assignment",
        )
        stages[id(node)] = node.environment[0].literal.partition("=")[2]
    _require(tuple(stages.values()) == CANDIDATE_STAGES, "candidate stage assignments are missing or reordered")
    _require(
        all(not isinstance(node, shell.For) or node.variable != variable for node in _walk(tree)),
        "candidate loop overwrites the diagnostic stage",
    )
    _require(
        len(root_commands) >= 4 and _literal_command(root_commands[0], "set", "-Eeuo", "pipefail")
        and _literal_command(root_commands[1], "umask", "077")
        and stages.get(id(root_commands[2])) == "preflight" and root_commands[3] is trap
        and root.index(handler) < root.index(trap),
        "candidate strict mode, initial stage and defined ERR callback must dominate execution",
    )
    _require(
        sum(bool(node.argv and node.argv[0].literal == "set") for node in commands) == 1,
        "candidate strict error handling changes during execution",
    )
    expected_stage = {
        "fd-check": "preflight", "check": "preflight", "venv": "venv",
        "pip": "pip", "chdir": "pip", "build-tools": "build-tools",
        "make": "make", "handoff": "handoff",
    }
    observed = Counter()
    accounted = set()
    stage = None
    for node in root:
        if isinstance(node, shell.Function):
            continue
        if id(node) in stages:
            stage = stages[id(node)]
            continue
        if any(node is prelude for prelude in root_commands[:4]):
            continue
        for command in (item for item in _walk(node) if isinstance(item, shell.Command)):
            operation = _candidate_operation(command)
            if operation is None:
                _require(
                    not command.argv or command.argv[0].literal not in {"exit", "return", "exec", "trap"},
                    "candidate execution terminates or replaces its diagnostic callback early",
                )
                continue
            _require(stage == expected_stage[operation], "candidate operation is outside its diagnostic stage")
            _require(
                operation == "check" or command is node,
                "candidate phase operation is conditional, asynchronous or in the wrong frame",
            )
            observed[operation] += 1
            accounted.add(id(command))
    _require(
        accounted == {id(node) for node in commands if _candidate_operation(node) is not None},
        "candidate phase operation is hidden in a helper or callback",
    )
    _require(observed.pop("check", 0) > 0, "candidate preflight checks are missing")
    _require(
        observed == Counter({
            "fd-check": 1, "venv": 1, "pip": 1, "chdir": 1,
            "build-tools": 1, "make": 1, "handoff": 2,
        }),
        "candidate diagnostic operations are missing or duplicated",
    )


def validate_producer(analysis: Analysis) -> None:
    """Bind canonical candidate diagnostics and the host failure continuation."""
    commands = {
        id(item.command): item for item in analysis.commands
        if not item.nested and item.scope == "staging"
    }
    events = tuple(event for event in analysis.events if id(event.command) in commands)
    for event in events:
        item = commands[id(event.command)]
        _require(
            event.signature == item.signature.name and event.kind in item.signature.events
            and event.context == item.context and event.scope == item.scope
            and event.accesses == item.signature.accesses and event.call_stack == (),
            "diagnostic event is not from the authorized staging frame",
        )
    _require(
        Counter((id(event.command), event.kind) for event in events)
        == Counter((identity, kind) for identity, item in commands.items() for kind in item.signature.events),
        "missing or repeated staging diagnostic event",
    )
    payloads = [
        redirect for item in commands.values()
        if any(payload.delimiter == "CANDIDATE_BUILD" and payload.language == "shell"
               for payload in item.signature.payloads)
        for redirect in item.command.redirects if redirect.target.literal == "CANDIDATE_BUILD"
    ]
    _require(len(payloads) == 1 and isinstance(payloads[0].body, str), "missing canonical candidate phase payload")
    _candidate_program(payloads[0].body)

    controls = {control.name: control for control in reviewed_inventory().controls}
    guards = [
        node for chain in analysis.tree.items for node in chain.nodes
        if isinstance(node, shell.If) and control_header(node) == controls["staging.control-19"].header
    ]
    _require(len(guards) == 1 and guards[0].failure is None, "host result guard differs")
    sequence = _nodes(guards[0].success)
    _require(
        len(sequence) == 3 and isinstance(sequence[0], shell.Case)
        and control_header(sequence[0]) == controls["staging.isolated-result"].header
        and all(isinstance(node, shell.Command) and id(node) in commands for node in sequence[1:])
        and tuple(commands[id(node)].signature.name for node in sequence[1:])
        == ("staging.command-125", "staging.command-111"),
        "host failure must map its detail, emit the fixed diagnostic, then exit",
    )
    positions = {id(event.command): index for index, event in enumerate(events)}
    mapping = [positions[id(node)] for node in _walk(sequence[0]) if isinstance(node, shell.Command)]
    _require(
        mapping and max(mapping) < positions[id(sequence[1])] < positions[id(sequence[2])],
        "host diagnostic events are reordered before mapping or after exit",
    )


def _frames(analysis: Analysis) -> None:
    """Execution policy refers to registry identities, never command spellings."""
    host_error = (_context("case", "host-temp-boundary", "1"),)
    option_error = (
        _context("loop", "required-cgroup-options"),
        _context("case", "cgroup-option", "1"),
    )
    dev_loop = (_context("loop", "dev-descendants"),)
    runtime_loop = (_context("loop", "runtime-records"),)
    runtime_error = runtime_loop + (
        _context("case", "runtime-writable", "0"),
        _context("case", "runtime-write-boundary", "1"),
    )
    failure = (_context("if", "candidate-result", "success"),)
    contexts = {
        "invalid-host-root": (host_error,),
        "stage-failure": (
            host_error, option_error,
            dev_loop + (_context("case", "dev-target-boundary", "2"),),
            runtime_error,
        ),
        "dev-target": (dev_loop,),
        "dev-unmount": (dev_loop + (_context("case", "dev-target-boundary", "1"),),),
        "runtime-target": (runtime_loop,),
        "runtime-options": (runtime_loop,),
        "runtime-rejection": (runtime_error,),
        "candidate-launch": ((_context("if", "candidate-launch", "condition"),),),
        "candidate-success": ((_context("if", "candidate-launch", "success"),),),
        "candidate-status": ((_context("if", "candidate-launch", "failure"),),),
        "candidate-failed": ((_context("if", "candidate-result", "condition"),),),
        "candidate-exit": tuple(
            failure + (_context("case", "candidate-failure", arm),)
            for arm in ("0", "1")
        ),
        "candidate-unknown": (failure + (_context("case", "candidate-failure", "2"),),),
    }
    for name in ("hidden-directory", "mount-masked-host", "readonly-hidden"):
        contexts[name] = ((_context("loop", "hidden-host-paths"),),)
    for name in ("regular", "not-symlink", "type", "links", "owner"):
        contexts["handoff-" + name] = ((_context("loop", "handoff-files"),),)
    for item in analysis.commands:
        if item.nested:
            continue
        if item.scope == "builder_main":
            expected = contexts.get(item.signature.name.removeprefix("builder_main."), ((),))
            _require(item.context in expected, "operation is conditional, asynchronous or in the wrong frame")
        elif item.scope in {"entry", "list_dev_mount_targets", "list_writable_mount_records"}:
            _require(not item.context, "entry or fixed program is not unconditional")
    protected = {
        EventKind.CANDIDATE_LAUNCH, EventKind.CANDIDATE_STATUS,
        EventKind.MEMBERSHIP_VERIFIED, EventKind.EXPORT_OPEN,
        EventKind.EXPORT_FILE, EventKind.EXPORT_CLOSE, EventKind.POST_CHECK,
    }
    for event in analysis.events:
        _require(event.kind != EventKind.LEGACY_MEMBERSHIP, "legacy membership is not a completion proof")
        if event.kind in protected:
            _require(
                event.scope == "builder_main" and event.call_stack == ("builder_main",),
                "phase event escaped the exact builder call frame",
            )


def _structure(analysis: Analysis) -> None:
    commands = {id(item.command): item for item in analysis.commands if not item.nested}
    controls = {(c.scope, c.header): c.name for c in reviewed_inventory().controls}

    def names(block: shell.Block) -> tuple[str, ...]:
        result = []
        for node in _nodes(block):
            _require(isinstance(node, shell.Command) and id(node) in commands, "expected exact foreground command")
            result.append(commands[id(node)].signature.name)
        return tuple(result)

    def control_name(node, scope="builder_main") -> str:
        result = controls.get((scope, control_header(node)))
        _require(result is not None, "unregistered control frame")
        return result

    entry = _nodes(analysis.tree)
    _require(
        len(entry) == 2 and isinstance(entry[0], shell.Function)
        and entry[0].name == "builder_main"
        and isinstance(entry[1], shell.Command)
        and commands[id(entry[1])].signature.name == "entry.invoke",
        "builder must be called exactly once as a foreground entry, not as a condition",
    )
    main_nodes = _nodes(entry[0].body)
    units = []
    definitions = {}
    for node in main_nodes:
        if isinstance(node, shell.Function):
            definitions[node.name] = node
        elif isinstance(node, shell.Command):
            units.append((commands[id(node)].signature.name, node))
        else:
            units.append((control_name(node), node))
    order = tuple(name for name, _node in units)
    _require(
        order[:3] == (
            "builder_main.strict-shell", "builder_main.stage-namespace",
            "builder_main.stage-trap",
        ),
        "strict error handling must dominate every phase",
    )
    trap_node = units[2][1]
    _require(
        main_nodes.index(definitions["isolated_stage_failure"]) < main_nodes.index(trap_node),
        "failure handler is not defined before its trap is armed",
    )
    launch_index = order.index("builder_main.candidate-launch")
    _require(
        order[launch_index - 1:launch_index + 4] == (
            "builder_main.stage-candidate-preflight", "builder_main.candidate-launch",
            "builder_main.candidate-result", "builder_main.stage-output-validate",
            "builder_main.membership-check",
        ),
        "launch/reap/status guard/checker is not one mandatory control sequence",
    )
    launch = units[launch_index][1]
    _require(isinstance(launch, shell.If), "launch must be a foreground if condition")
    _require(
        names(launch.condition) == ("builder_main.candidate-launch",)
        and names(launch.success) == ("builder_main.candidate-success",)
        and names(launch.failure) == ("builder_main.candidate-status",),
        "launcher result is not captured on both exact completion edges",
    )
    result = units[launch_index + 1][1]
    _require(isinstance(result, shell.If) and result.failure is None, "candidate result guard differs")
    _require(names(result.condition) == ("builder_main.candidate-failed",), "wrong process result")
    failed_nodes = _nodes(result.success)
    _require(
        len(failed_nodes) == 1 and isinstance(failed_nodes[0], shell.Case)
        and control_name(failed_nodes[0]) == "builder_main.candidate-failure",
        "nonzero candidate result can continue or impersonate an isolated stage",
    )
    _require(
        tuple(names(arm.body) for arm in failed_nodes[0].arms) == (
            ("builder_main.candidate-exit",), ("builder_main.candidate-exit",),
            ("builder_main.candidate-unknown",),
        ),
        "candidate failure exits are missing or reordered",
    )
    stage_nodes = _nodes(definitions["isolated_stage_failure"].body)
    _require(
        len(stage_nodes) == 1 and isinstance(stage_nodes[0], shell.Case)
        and control_name(stage_nodes[0], "isolated_stage_failure") == "isolated_stage_failure.result",
        "failure trap must select only a fixed isolated substage",
    )
    _require(
        tuple(names(arm.body) for arm in stage_nodes[0].arms) == tuple(
            (f"isolated_stage_failure.exit-{status}",) for status in (81, 82, 83, 84, 85, 125)
        ),
        "isolated failure substages differ",
    )
    _require(order[-2:] == ("builder_main.post-check", "builder_main.success"), "success bypasses final post-check")


class _Machine:
    def __init__(self):
        self.phase = Phase.PREPARING
        self.stage = None
        self.transitions = []
        self.setup = Counter()
        self.handoff = set()
        self.files = set()
        self.initial_seal = False
        self.owner = False
        self.success = False
        self.error_only = {
            "invalid-host-root", "runtime-rejection", "stage-failure",
            "candidate-exit", "candidate-unknown",
        }
        self.handoff_required = {
            s.name.removeprefix("builder_main.")
            for s in reviewed_inventory().signatures
            if s.scope == "builder_main" and s.name.startswith("builder_main.handoff-")
        }
        late = self.handoff_required | self.error_only | {
            "stage-output-validate", "stage-export", "stage-post-check",
            "candidate-launch", "candidate-success", "candidate-status",
            "candidate-failed", "membership-check", "export-open",
            "export-target.gba", "export-metadata.json", "export-owner",
            "post-check", "success",
        }
        self.setup_required = Counter({
            s.name.removeprefix("builder_main."): s.occurrences
            for s in reviewed_inventory().signatures
            if s.scope == "builder_main" and s.name.removeprefix("builder_main.") not in late
        })
        self.setup_required["readonly-export"] = 1

    def advance(self, before: Phase, after: Phase, event: Event) -> None:
        _require(self.phase == before, "missing, early, late or duplicate phase event")
        self.transitions.append(Transition(before, after, event))
        self.phase = after

    def consume(self, event: Event) -> None:
        name = event.signature.removeprefix("builder_main.")
        if name in self.error_only:
            if name in {"candidate-exit", "candidate-unknown"}:
                expected_stage = "candidate-preflight"
            elif name == "runtime-rejection" or _context("loop", "runtime-records") in event.context:
                expected_stage = "mount-audit"
            else:
                expected_stage = "namespace"
            _require(self.stage == expected_stage, "failure-only operation outside its reserved substage")
            return
        _require(not self.success, "operation after final success")
        if name.startswith("stage-") and name != "stage-trap":
            stage = name.removeprefix("stage-")
            expected = {
                "namespace": (None, Phase.PREPARING),
                "mount-audit": ("namespace", Phase.PREPARING),
                "candidate-preflight": ("mount-audit", Phase.PREPARING),
                "output-validate": ("candidate-preflight", Phase.LAUNCH_REAPED),
                "export": ("output-validate", Phase.MEMBERSHIP_VERIFIED),
                "post-check": ("export", Phase.EXPORT_STARTED),
            }
            _require((self.stage, self.phase) == expected[stage], "wrong failure-stage frame")
            if stage == "export":
                _require(self.handoff == self.handoff_required, "export before complete handoff validation")
            if stage == "post-check":
                _require(self.owner, "post-check before complete export")
            self.stage = stage
            if self.phase == Phase.PREPARING:
                self.setup[name] += 1
        elif event.kind == EventKind.CANDIDATE_LAUNCH:
            _require(self.setup == self.setup_required and self.initial_seal, "launch before complete isolation setup")
            self.advance(Phase.PREPARING, Phase.LAUNCH_STARTED, event)
        elif event.kind == EventKind.CANDIDATE_STATUS:
            _require(self.phase == Phase.LAUNCH_STARTED, "status without exact foreground launch")
        elif name == "candidate-failed":
            # _structure proves that nonzero edges exit. Only zero reaches here
            # on the modeled success path, after Bash has waited for that child.
            self.advance(Phase.LAUNCH_STARTED, Phase.LAUNCH_REAPED, event)
        elif event.kind == EventKind.MEMBERSHIP_VERIFIED:
            _require(self.stage == "output-validate", "checker in the wrong substage")
            self.advance(Phase.LAUNCH_REAPED, Phase.MEMBERSHIP_VERIFIED, event)
        elif name in self.handoff_required:
            _require(self.phase == Phase.MEMBERSHIP_VERIFIED and self.stage == "output-validate", "handoff read before verification or after export")
            _require(name not in self.handoff, "duplicate handoff check")
            self.handoff.add(name)
        elif event.kind == EventKind.EXPORT_OPEN:
            _require(self.stage == "export" and self.handoff == self.handoff_required, "export before validation")
            self.advance(Phase.MEMBERSHIP_VERIFIED, Phase.EXPORT_STARTED, event)
        elif event.kind == EventKind.EXPORT_FILE:
            _require(
                self.phase == Phase.EXPORT_STARTED and self.stage == "export"
                and not self.owner and name not in self.files,
                "export file outside its writable export frame",
            )
            self.files.add(name)
        elif name == "export-owner":
            _require(
                self.phase == Phase.EXPORT_STARTED and self.stage == "export"
                and self.files == {"export-target.gba", "export-metadata.json"}
                and not self.owner,
                "export ownership before both exact files",
            )
            self.owner = True
        elif event.kind == EventKind.EXPORT_CLOSE:
            if self.phase == Phase.PREPARING:
                _require(not self.initial_seal and self.stage == "namespace", "duplicate initial export sealing")
                self.initial_seal = True
                self.setup[name] += 1
            else:
                _require(self.stage == "post-check" and self.owner, "export close before committed files")
                self.advance(Phase.EXPORT_STARTED, Phase.EXPORT_COMMITTED, event)
        elif event.kind == EventKind.POST_CHECK:
            _require(self.stage == "post-check", "final post-check in the wrong substage")
            self.advance(Phase.EXPORT_COMMITTED, Phase.POST_CHECKED, event)
        elif name == "success":
            _require(self.phase == Phase.POST_CHECKED, "success before final post-check")
            self.success = True
        else:
            _require(self.phase == Phase.PREPARING, "isolation operation reordered across launch")
            if name == "strict-shell":
                expected_stage = None
            elif name == "suppress-output":
                expected_stage = "mount-audit" if self.setup[name] else "namespace"
            elif name.startswith("limit-") or (
                name.startswith("runtime-") and name != "runtime-limit"
            ):
                expected_stage = "mount-audit"
            else:
                expected_stage = "namespace"
            _require(self.stage == expected_stage, "isolation operation outside its reserved failure substage")
            self.setup[name] += 1


def validate(analysis: Analysis) -> tuple[Transition, ...]:
    """Validate the mandatory runtime success path of inventory-authorized AST."""
    _frames(analysis)
    _structure(analysis)
    primary = {
        id(item.command): item for item in analysis.commands
        if not item.nested and item.scope == "builder_main"
    }
    machine = _Machine()
    observed = Counter()
    for event in analysis.events:
        item = primary.get(id(event.command))
        if item is None:
            continue
        _require(
            event.signature == item.signature.name and event.kind in item.signature.events
            and event.context == item.context and event.scope == item.scope
            and event.call_stack == ("builder_main",),
            "event is not from the authorized builder control frame",
        )
        observed[id(event.command)] += 1
        machine.consume(event)
    _require(observed == Counter({identity: 1 for identity in primary}), "missing or repeated execution event")
    _require(machine.success and machine.phase == Phase.POST_CHECKED, "publisher phase sequence is incomplete")
    return tuple(machine.transitions)
