"""Independent candidate records contributed to the shared publisher registry."""

from dataclasses import replace
import re
import shlex

from . import publisher_shell as shell
from .publisher_inventory import (
    Access, Context, EventKind, InventoryError, Program, ProgramKind, Resource,
    ResourceAccess, Scope, WORKFLOW_PATH,
)


STAGES = ("preflight", "venv", "pip", "build-tools", "make", "handoff")
FD_CHECK_TEXT = (
    'import errno,fcntl; bad=[]; exec("for fd in range(3, 1024):\\n try: '
    'fcntl.fcntl(fd, fcntl.F_GETFD)\\n except OSError as error:\\n  if error.errno '
    '!= errno.EBADF: raise\\n else: bad.append(fd)"); raise SystemExit(125 if bad else 0)'
)


def register(add, control, scopes):
    scopes.append(Scope("candidate_stage_failure", "candidate", ()))
    read = (ResourceAccess(Resource.CANDIDATE, Access.READ),)
    write = (ResourceAccess(Resource.CANDIDATE, Access.WRITE),)
    inspect = (ResourceAccess(Resource.PROCESS, Access.INSPECT),)
    fd_check = Program(
        "candidate-fd-check", WORKFLOW_PATH, "-c", None, inspect, (),
        kind=ProgramKind.INLINE, text=FD_CHECK_TEXT,
    )
    venv = Program(
        "candidate-venv", WORKFLOW_PATH, "-m", "venv", read, write,
        startup=(), kind=ProgramKind.MODULE,
    )
    pip = Program(
        "candidate-pip", WORKFLOW_PATH, "-m", "pip", read, write,
        interpreter=shell.command('"$HOME/venv/bin/python3"').argv[0],
        startup=(), kind=ProgramKind.MODULE,
    )

    def row(name, source, resource=Resource.CANDIDATE, access=Access.INSPECT, **options):
        add("candidate", name, source, resource, access, **options)

    row("strict-shell", "set -Eeuo pipefail", Resource.SHELL, Access.WRITE)
    row("umask", "umask 077", Resource.SHELL, Access.WRITE)
    for stage in STAGES:
        row("stage-" + stage, "candidate_stage=" + stage, Resource.SHELL, Access.WRITE,
            event=EventKind.STATE_WRITE)
    row("arm-trap", "trap candidate_stage_failure ERR", Resource.PROCESS, Access.WRITE)
    add("candidate_stage_failure", "disable-trap", "trap - ERR", Resource.PROCESS, Access.WRITE)
    control(
        "candidate_stage_failure", "stages",
        'case "$candidate_stage" in preflight) ;; venv) ;; pip) ;; build-tools) ;; make) ;; handoff) ;; *) ;; esac',
    )
    for index, stage in enumerate((*STAGES, "unknown")):
        add(
            "candidate_stage_failure", "exit-" + stage, "exit " + str(71 + index),
            Resource.PROCESS, Access.EXECUTE,
            context=(Context("case", "candidate_stage_failure.stages", str(index)),),
        )
    row(
        "preflight.fd-check", "/usr/bin/python3 -I -S -c " + shlex.quote(FD_CHECK_TEXT),
        Resource.PROCESS, Access.INSPECT, program=fd_check,
    )
    for variable in (
        "BASH_ENV", "BASH_XTRACEFD", "ENV", "GITHUB_ENV", "GITHUB_OUTPUT",
        "GITHUB_PATH", "GITHUB_STEP_SUMMARY", "LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONPATH",
    ):
        row("preflight.empty-" + variable, 'test -z "${' + variable + '-}"', Resource.SHELL)
    control(
        "candidate", "readonly-paths",
        "for readonly_path in / /etc /opt /usr /usr/share /usr/share/dbus-1/system-services; do :; done",
    )
    readonly = (Context("loop", "candidate.readonly-paths"),)
    row("preflight.readonly-exists", 'test -e "$readonly_path"', context=readonly)
    row("preflight.readonly-not-writable", 'test ! -w "$readonly_path"', context=readonly)
    for name, source, resource in (
        ("wheelhouse-readonly", 'test ! -w "$WHEELHOUSE"', Resource.CANDIDATE),
        ("host-temp-readonly", 'test ! -w "$HOST_RUNNER_TEMP"', Resource.HOST),
        ("export-readonly", "test ! -w /mnt/export", Resource.EXPORT),
        ("supervisor-unreadable", "test ! -r /mnt/supervisor", Resource.SUPERVISOR),
        ("supervisor-readonly", "test ! -w /mnt/supervisor", Resource.SUPERVISOR),
        ("supervisor-unsearchable", "test ! -x /mnt/supervisor", Resource.SUPERVISOR),
        ("membership-unreadable", "test ! -r /mnt/supervisor/cgroup/cgroup.procs", Resource.CGROUP_VIEW),
    ):
        row("preflight." + name, source, resource)
    control(
        "candidate", "writable-paths",
        'for writable_path in "$GITHUB_WORKSPACE" "$HOME" "$RUNNER_TEMP" "$HANDOFF"; do :; done',
    )
    writable = (Context("loop", "candidate.writable-paths"),)
    row("preflight.writable-directory", 'test -d "$writable_path"', context=writable)
    row("preflight.writable-permission", 'test -w "$writable_path"', context=writable)
    row("preflight.no-console", "test ! -e /dev/console", Resource.PROCESS)
    row("preflight.no-kmsg", "test ! -e /dev/kmsg", Resource.PROCESS)
    control(
        "candidate", "socket-paths",
        "for socket_path in /run/dbus/system_bus_socket /run/docker.sock /run/containerd/containerd.sock "
        "/run/systemd/private /run/snapd.socket /run/podman/podman.sock /var/run/docker.sock "
        "/var/run/dbus/system_bus_socket; do :; done",
    )
    row(
        "preflight.no-host-socket", 'test ! -e "$socket_path"', Resource.HOST,
        context=(Context("loop", "candidate.socket-paths"),),
    )
    row("preflight.no-raw-cgroup", "test ! -e /sys/fs/cgroup/cgroup.procs", Resource.CGROUP_RAW)
    row(
        "preflight.socket-scan",
        'test -z "$(/usr/bin/find / -xdev -type s -print -quit 2>/dev/null)"', Resource.HOST,
    )
    row("venv.create", '/usr/bin/python3 -m venv "$HOME/venv"', access=Access.READ,
        program=venv, extra=write)
    row(
        "pip.install",
        '"$HOME/venv/bin/python3" -m pip install --no-index --find-links="$WHEELHOUSE" '
        '--require-hashes --only-binary=:all: --no-deps -r "$GITHUB_WORKSPACE/.github/requirements/build.txt"',
        access=Access.READ, program=pip, extra=write,
    )
    row("pip.directory", 'cd "$GITHUB_WORKSPACE"')
    row("build-tools.run", "./build_tools.sh", access=Access.EXECUTE,
        executable=shell.command("./build_tools.sh").argv[0])
    row("make.run", "make expansion-modern-map-menu-presentation-check -j1",
        access=Access.EXECUTE, executable=shell.command("make").argv[0])
    for name, source in (
        ("target", 'build/expansion-modern-all-locales-all-features/release/aapcs/fireemblem8.gba "$HANDOFF/target.gba"'),
        ("metadata", 'build/expansion-modern-all-locales-all-features/release/aapcs/generated/expansion_build_metadata.json "$HANDOFF/metadata.json"'),
    ):
        row("handoff." + name, "/usr/bin/install -m 0400 " + source,
            Resource.HANDOFF, Access.WRITE, extra=read)


def bind_names(registry, source):
    """Alpha-bind local identifiers in existing records, never infer permissions."""
    tree = shell.parse(source)
    root = tuple(node for chain in tree.items for node in chain.nodes)
    traps = [
        node for node in root if isinstance(node, shell.Command)
        and len(node.argv) == 3 and node.argv[0].literal == "trap" and node.argv[2].literal == "ERR"
    ]
    if len(traps) != 1 or traps[0].argv[1].literal is None:
        raise InventoryError("candidate callback binding differs")
    helper = traps[0].argv[1].literal
    functions = [node for node in root if isinstance(node, shell.Function) and node.name == helper]
    if len(functions) != 1:
        raise InventoryError("candidate callback definition differs")
    cases = [
        node for chain in functions[0].body.items for node in chain.nodes
        if isinstance(node, shell.Case)
    ]
    if len(cases) != 1 or len(cases[0].subject.parts) != 1 or cases[0].subject.parts[0].kind != "parameter":
        raise InventoryError("candidate state binding differs")
    controls = {control.name: control for control in registry.controls}
    variables = {"candidate_stage": cases[0].subject.parts[0].value}
    for name in ("readonly-paths", "writable-paths", "socket-paths"):
        control = controls["candidate." + name]
        matches = [
            node for node in root if isinstance(node, shell.For)
            and (node.values, node.arithmetic) == control.header[2:]
        ]
        if len(matches) != 1:
            raise InventoryError("candidate preflight loop binding differs")
        variables[control.header[1]] = matches[0].variable
    if (
        any(not isinstance(name, str) or re.fullmatch(r"[a-z][a-z0-9_]*", name) is None
            for name in variables.values())
        or len(set(variables.values())) != len(variables)
        or re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", helper) is None
    ):
        raise InventoryError("candidate local bindings are not distinct safe identifiers")
    candidate = lambda scope: registry.entry_scope(scope) == "candidate"
    reserved = {
        signature.invocation.executable.literal for signature in registry.signatures
        if candidate(signature.scope) and signature.invocation.executable is not None
    }
    if helper in reserved or helper in variables.values():
        raise InventoryError("candidate callback binding shadows another role")
    original_patterns = controls["candidate_stage_failure.stages"].header[2]
    actual_patterns = tuple(arm.patterns for arm in cases[0].arms)
    if len(actual_patterns) != len(original_patterns) or set(actual_patterns) != set(original_patterns):
        raise InventoryError("candidate exit patterns differ")
    branches = {
        str(index): str(actual_patterns.index(patterns))
        for index, patterns in enumerate(original_patterns)
    }

    def word(value):
        parts = []
        for index, part in enumerate(value.parts):
            text = part.value
            if part.kind == "parameter":
                text = variables.get(text, text)
            elif part.kind == "literal":
                if index == 0 and value.assignment in variables:
                    text = variables[value.assignment] + text[len(value.assignment):]
                elif text == "candidate_stage_failure":
                    text = helper
            parts.append(replace(part, value=text))
        return replace(value, parts=tuple(parts), assignment=variables.get(value.assignment, value.assignment))

    def syntax(value):
        if isinstance(value, shell.Word):
            return word(value)
        if isinstance(value, tuple):
            return tuple(syntax(item) for item in value)
        if isinstance(value, (shell.Command, shell.Block, shell.Chain, shell.Part, shell.Redirect)):
            from dataclasses import fields
            return replace(value, **{field.name: syntax(getattr(value, field.name)) for field in fields(value)})
        return value

    def context(value):
        if value.kind == "case" and value.identity == "candidate_stage_failure.stages":
            return replace(value, branch=branches[value.branch])
        return value

    def scope(value):
        return helper if value == "candidate_stage_failure" else value

    signatures = tuple(
        replace(
            signature, scope=scope(signature.scope), form=syntax(signature.form),
            placements=tuple(replace(place, context=tuple(context(c) for c in place.context))
                             for place in signature.placements),
        ) if candidate(signature.scope) else signature
        for signature in registry.signatures
    )
    rebound_controls = []
    for control in registry.controls:
        if not candidate(control.scope):
            rebound_controls.append(control)
            continue
        header = syntax(control.header)
        if header[0] == "for":
            header = (header[0], variables.get(header[1], header[1]), *header[2:])
        elif control.name == "candidate_stage_failure.stages":
            header = (header[0], header[1], actual_patterns)
        rebound_controls.append(replace(
            control, scope=scope(control.scope), header=header,
            context=tuple(context(c) for c in control.context),
        ))
    return replace(
        registry, signatures=signatures, controls=tuple(rebound_controls),
        scopes=tuple(replace(item, name=scope(item.name), parent=scope(item.parent))
                     if candidate(item.name) else item for item in registry.scopes),
    )


def analyze_payload(registry, staging):
    payloads = [
        redirect.body for item in staging.commands
        if not item.nested and item.scope == "staging"
        and any(payload.delimiter == "CANDIDATE_BUILD" and payload.language == "shell"
                for payload in item.signature.payloads)
        for redirect in item.command.redirects if redirect.target.literal == "CANDIDATE_BUILD"
    ]
    if len(payloads) != 1 or not isinstance(payloads[0], str):
        raise InventoryError("missing registered candidate payload")
    return bind_names(registry, payloads[0]).validate(payloads[0], entry_scope="candidate")
