"""Reviewed publisher operations, not an executable or filename blacklist.

Every row binds a complete parsed command (including nested producers), scope,
cardinality, resources and events. The two transport families share their
definition; no alternate helper analyzer or Python-text exemption exists.
"""

from __future__ import annotations

from . import publisher_shell as shell
from .publisher_inventory import (
    Access, Context, Control, EventKind, Family, Inventory, Placement, Program, Resource,
    ResourceAccess, Scope, Signature, PROGRAM_PATH, PROGRAM_RUNTIME_PATH, AUTHORITY_RUNTIME_PATH,
    control_header, normalize_invocation,
)
from .publisher_producer_signatures import register as register_producer
from .publisher_candidate_signatures import register as register_candidate


def inventory() -> Inventory:
    scopes = [
        Scope("builder_main", "entry", (
            Resource.CGROUP_RAW, Resource.PROCESS, Resource.PROCESS,
            Resource.CONTROL, Resource.HOST, Resource.CANDIDATE,
            Resource.CONTROL, Resource.PROCESS, Resource.PROCESS,
        )),
        Scope("unmount_if_mounted", "builder_main", (Resource.HOST,)),
        Scope("list_dev_mount_targets", "builder_main", ()),
        Scope("list_writable_mount_records", "builder_main", ()),
        Scope("isolated_stage_failure", "builder_main", ()),
    ]
    for kind, resource in (("supervisor", Resource.SUPERVISOR), ("runtime", Resource.RUNTIME)):
        scopes.extend((
            Scope(f"create_{kind}_transport_file", "builder_main", (resource,)),
            Scope(f"checked_{kind}_transport_signature", "builder_main", (resource, Resource.SHELL)),
            Scope(f"read_checked_{kind}_transport_file", "builder_main", (resource, Resource.SHELL, Resource.SHELL)),
            Scope(f"remove_{kind}_transport_file", "builder_main", (resource,)),
        ))
    signatures: list[Signature] = []
    controls: list[Control] = []
    builtins = {":", "[", "[[", "break", "cd", "echo", "exec", "exit", "local", "mapfile", "printf", "return", "set", "test", "trap", "true", "ulimit", "umask", "unset", "wait"}

    def add(
        scope: str, name: str, source: str,
        resource: Resource = Resource.SHELL, access: Access = Access.INSPECT,
        *, count: int = 1, event: EventKind = EventKind.COMMAND,
        program: Program | None = None, extra: tuple[ResourceAccess, ...] = (),
        context: tuple[Context, ...] = (), placements: tuple[Placement, ...] | None = None,
        payloads: tuple = (), executable: shell.Word | None = None,
    ) -> None:
        form = shell.command(source)
        invocation = normalize_invocation(
            form, executable=program.interpreter if program is not None else executable,
        )
        name_word = invocation.executable
        literal = name_word.literal if name_word else None
        if not form.argv:
            family = Family.ASSIGNMENT
        elif program is not None:
            family = Family.PYTHON
        elif literal in {scope.name for scope in scopes}:
            family = Family.HELPER
        elif literal in builtins:
            family = Family.BUILTIN
        elif literal == "/usr/bin/python3":
            family = Family.PYTHON
        elif literal is not None and (literal.startswith("/") or executable == name_word):
            family = Family.EXECUTABLE
        else:
            raise ValueError("signature has no fixed executable")
        signatures.append(Signature(
            f"{scope}.{name}", scope, form, family, count,
            (ResourceAccess(resource, access),) + extra, (event,), program,
            placements=placements if placements is not None else (Placement(context, count),),
            payloads=payloads, executable=executable,
        ))

    def control(scope: str, name: str, source: str, context: tuple[Context, ...] = (), count: int = 1) -> None:
        parsed = shell.parse(source)
        if len(parsed.items) != 1 or len(parsed.items[0].nodes) != 1:
            raise ValueError("control signature must have one header")
        controls.append(Control(
            f"{scope}.{name}", scope, control_header(parsed.items[0].nodes[0]),
            context=context, occurrences=count,
        ))

    def main(name: str, source: str, resource=Resource.SHELL, access=Access.INSPECT, **options):
        add("builder_main", name, source, resource, access, **options)

    def loop(name: str) -> tuple[Context, ...]:
        return (Context("loop", "builder_main." + name),)

    def case(name: str, arm: int, scope: str = "builder_main") -> tuple[Context, ...]:
        return (Context("case", scope + "." + name, str(arm)),)

    checked = (Context("operators", "||", "0"),)
    rejected = (Context("operators", "||", "1"),)
    runtime_records = loop("runtime-records")
    runtime_writable = runtime_records + case("runtime-writable", 0)
    runtime_rejection = runtime_writable + case("runtime-write-boundary", 1)

    register_producer(add, control, scopes)
    register_candidate(add, control, scopes)
    add("entry", "invoke", 'builder_main "$@"', Resource.CONTROL, Access.EXECUTE, event=EventKind.HELPER_CALL)
    main("strict-shell", "set -Eeuo pipefail", access=Access.WRITE)
    for stage in ("namespace", "mount-audit", "candidate-preflight", "output-validate", "export", "post-check"):
        main(f"stage-{stage}", f"isolated_stage={stage}", access=Access.WRITE, event=EventKind.STATE_WRITE)
    main("stage-trap", "trap isolated_stage_failure ERR", Resource.PROCESS, Access.WRITE)
    main(
        "stage-failure", "isolated_stage_failure", Resource.PROCESS, Access.WRITE,
        count=4, event=EventKind.HELPER_CALL, placements=(
            Placement(case("host-temp-boundary", 1)),
            Placement(loop("required-cgroup-options") + case("cgroup-option", 1)),
            Placement(loop("dev-descendants") + case("dev-target-boundary", 2)),
            Placement(runtime_rejection),
        ),
    )
    control(
        "isolated_stage_failure", "result",
        'case "$isolated_stage" in namespace) ;; mount-audit) ;; output-validate) ;; export) ;; post-check) ;; *) ;; esac',
    )
    for arm, status in enumerate((81, 82, 83, 84, 85, 125)):
        add(
            "isolated_stage_failure", f"exit-{status}", f"exit {status}",
            Resource.PROCESS, Access.WRITE, context=case("result", arm, "isolated_stage_failure"),
        )
    for position, name, resource in (
        (1, "cgroup_path", Resource.CGROUP_RAW),
        (2, "builder_uid", Resource.PROCESS),
        (3, "builder_gid", Resource.PROCESS),
        (4, "builder_root", Resource.CONTROL),
        (5, "host_runner_temp", Resource.HOST),
        (6, "candidate_script", Resource.CANDIDATE),
        (7, "candidate_launcher", Resource.CONTROL),
        (8, "host_uid", Resource.PROCESS),
        (9, "host_gid", Resource.PROCESS),
    ):
        main(
            f"initialize-{name}", f'{name}="${position}"',
            resource, Access.WRITE, event=EventKind.STATE_WRITE,
        )
    main("suppress-output", "exec < /dev/null > /dev/null 2>&1", Resource.NULL, Access.WRITE, count=2)
    main(
        "join-cgroup", r'''printf '%s\n' "$$" > "$cgroup_path/cgroup.procs"''',
        Resource.CGROUP_RAW, Access.WRITE, event=EventKind.CGROUP_JOIN,
    )
    main("root-directory", "cd /", Resource.HOST, Access.INSPECT)
    for name, source in (
        ("private-propagation", "/usr/bin/mount --make-rprivate /"),
        ("bind-host-root", "/usr/bin/mount --bind / /"),
        ("readonly-host-root", "/usr/bin/mount -o remount,bind,ro /"),
    ):
        main(name, source, Resource.HOST, Access.MOUNT)
    main("invalid-host-root", 'echo "runner temp is outside the masked host tree" >&2', Resource.NULL, Access.WRITE, context=case("host-temp-boundary", 1))
    control("builder_main", "host-temp-boundary", 'case "$host_runner_temp" in /home/runner/*) ;; *) ;; esac')

    for name, options, device, target in (
        ("work", "nosuid,nodev,noexec,mode=0755,size=16m", "builder-work", "/mnt"),
        ("supervisor", "nosuid,nodev,noexec,mode=0700,size=1m", "builder-supervisor", "/mnt/supervisor"),
        ("source", "nosuid,nodev,mode=0755,size=6g", "builder-source", "/mnt/source"),
        ("home", "nosuid,nodev,mode=0700,size=1g", "builder-home", "/mnt/home"),
        ("temp", "nosuid,nodev,mode=0700,size=1g", "builder-temp", "/mnt/tmp"),
        ("handoff", "nosuid,nodev,noexec,mode=0700,size=40m", "builder-handoff", "/mnt/handoff"),
        ("masked-host", "nosuid,nodev,noexec,mode=0755,size=1m", "builder-mask", '"$hidden"'),
        ("tmp", "nosuid,nodev,noexec,mode=1777,size=256m", "builder-tmp", "/tmp"),
        ("dev", "nosuid,mode=0755,size=4m", "builder-dev", "/dev"),
        ("shm", "nosuid,nodev,noexec,mode=1777,size=64m", "builder-shm", "/dev/shm"),
    ):
        main(
            f"mount-{name}", f"/usr/bin/mount -t tmpfs -o {options} {device} {target}",
            Resource.MOUNT_GRAPH, Access.MOUNT,
            context=loop("hidden-host-paths") if name == "masked-host" else (),
        )
    main(
        "private-directories",
        "/usr/bin/mkdir -m 0755 /mnt/control /mnt/export /mnt/handoff /mnt/home /mnt/source /mnt/tmp /mnt/wheelhouse",
        Resource.CONTROL, Access.CREATE,
    )
    main("supervisor-directory", "/usr/bin/mkdir -m 0700 /mnt/supervisor", Resource.SUPERVISOR, Access.CREATE)
    main("supervisor-view-directory", "/usr/bin/mkdir -m 0700 /mnt/supervisor/cgroup", Resource.CGROUP_VIEW, Access.CREATE)
    main("supervisor-owner", 'test "$(/usr/bin/stat -c %u /mnt/supervisor)" = 0', Resource.SUPERVISOR)
    main("supervisor-mode", 'test "$(/usr/bin/stat -c %a /mnt/supervisor)" = 700', Resource.SUPERVISOR)
    main("cgroup-owner", 'test "$(/usr/bin/stat -c %u "$cgroup_path")" = 0', Resource.CGROUP_RAW)
    main(
        "cgroup-bind", '/usr/bin/mount --bind "$cgroup_path" /mnt/supervisor/cgroup',
        Resource.CGROUP_VIEW, Access.MOUNT, event=EventKind.CGROUP_BIND,
        extra=(ResourceAccess(Resource.CGROUP_RAW, Access.INSPECT),),
    )
    main(
        "cgroup-readonly", "/usr/bin/mount -o remount,bind,ro,nosuid,nodev,noexec /mnt/supervisor/cgroup",
        Resource.CGROUP_VIEW, Access.MOUNT, event=EventKind.CGROUP_READONLY,
    )
    main("cgroup-view-name", "supervisor_cgroup=/mnt/supervisor/cgroup", Resource.CGROUP_VIEW, Access.WRITE, event=EventKind.STATE_WRITE)
    main(
        "cgroup-inode",
        'test "$(/usr/bin/stat -Lc %d:%i "$cgroup_path/cgroup.procs")" = "$(/usr/bin/stat -Lc %d:%i "$supervisor_cgroup/cgroup.procs")"',
        Resource.CGROUP_RAW, extra=(ResourceAccess(Resource.CGROUP_VIEW, Access.INSPECT),),
    )
    main(
        "cgroup-options", 'supervisor_options="$(/usr/bin/findmnt -n -o OPTIONS --target "$supervisor_cgroup")"',
        Resource.CGROUP_VIEW,
    )
    control("builder_main", "required-cgroup-options", "for option in ro nosuid nodev noexec; do :; done")
    control("builder_main", "cgroup-option", 'case ",$supervisor_options," in *,"$option",*) ;; *) ;; esac', loop("required-cgroup-options"))
    main("copy-candidate", '/bin/cp -a -- "$builder_root/source/." /mnt/source/', Resource.CANDIDATE, Access.WRITE)
    for name, source, target in (
        ("export", '"$builder_root/handoff"', "/mnt/export"),
        ("wheelhouse", '"$builder_root/wheelhouse"', "/mnt/wheelhouse"),
        ("control", "/mnt/control", "/mnt/control"),
    ):
        main(f"bind-{name}", f"/usr/bin/mount --bind {source} {target}", Resource.MOUNT_GRAPH, Access.MOUNT)
        main(
            f"readonly-{name}", f"/usr/bin/mount -o remount,bind,ro,nosuid,nodev,noexec {target}",
            Resource.EXPORT if name == "export" else Resource.MOUNT_GRAPH,
            Access.MOUNT, count=2 if name == "export" else 1,
            event=EventKind.EXPORT_CLOSE if name == "export" else EventKind.COMMAND,
        )
    for name, mode, source, target in (
        ("candidate-script", "0555", '"$candidate_script"', "/mnt/control/candidate-build.sh"),
        ("candidate-launcher", "0444", '"$candidate_launcher"', "/mnt/control/candidate-launcher.py"),
        ("publisher-programs", "0444", '"$host_runner_temp/patch-runtime/publisher-programs.py"', PROGRAM_RUNTIME_PATH),
        ("publisher-authority", "0444", '"$host_runner_temp/patch-runtime/publisher-inventory.py"', AUTHORITY_RUNTIME_PATH),
    ):
        main(f"install-{name}", f"/usr/bin/install -m {mode} {source} {target}", Resource.CONTROL, Access.CREATE)
    main(
        "candidate-ownership",
        '/usr/bin/chown -R "$builder_uid:$builder_gid" /mnt/handoff /mnt/home /mnt/source /mnt/tmp',
        Resource.CANDIDATE, Access.WRITE,
    )
    main("readonly-mnt", "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec /mnt", Resource.MOUNT_GRAPH, Access.MOUNT)
    add("unmount_if_mounted", "probe", '/usr/bin/mountpoint -q "$1"', Resource.HOST,
        context=(Context("if", "unmount_if_mounted.mounted", "condition"),))
    add("unmount_if_mounted", "unmount", '/usr/bin/umount --recursive "$1"', Resource.HOST, Access.MOUNT,
        context=(Context("if", "unmount_if_mounted.mounted", "success"),))
    control("unmount_if_mounted", "mounted", 'if /usr/bin/mountpoint -q "$1"; then :; fi')
    for name, target in (
        ("runner", "/home/runner"), ("root", "/root"), ("var", "/var"),
        ("run", "/run"), ("sys", "/sys"), ("tmp", "/tmp"),
    ):
        main(f"unmount-{name}", f"unmount_if_mounted {target}", Resource.HOST, Access.MOUNT, event=EventKind.HELPER_CALL)
    control("builder_main", "hidden-host-paths", "for hidden in /home/runner /root /var /run /sys; do :; done")
    main("hidden-directory", 'test -d "$hidden"', Resource.HOST, context=loop("hidden-host-paths"))
    main("readonly-hidden", '/usr/bin/mount -o remount,ro,nosuid,nodev,noexec "$hidden"', Resource.HOST, Access.MOUNT, context=loop("hidden-host-paths"))

    mount_program_input = (ResourceAccess(Resource.MOUNT_GRAPH, Access.READ),)
    for helper, mode, resource in (
        ("list_dev_mount_targets", "dev-mount-targets", Resource.SUPERVISOR),
        ("list_writable_mount_records", "writable-mount-records", Resource.RUNTIME),
    ):
        program = Program(
            mode, PROGRAM_PATH, AUTHORITY_RUNTIME_PATH, "--runtime-program", mount_program_input,
            (ResourceAccess(resource, Access.WRITE),),
        )
        add(
            helper, "program", f"/usr/bin/python3 -I -S {AUTHORITY_RUNTIME_PATH} --runtime-program {mode}",
            Resource.MOUNT_GRAPH, Access.READ, program=program,
            event=EventKind.MOUNT_AUDIT, extra=program.outputs,
        )
    for kind, directory, resource in (
        ("supervisor", "/mnt/supervisor", Resource.SUPERVISOR),
        ("runtime", "/dev/shm", Resource.RUNTIME),
    ):
        creator = f"create_{kind}_transport_file"
        checker = f"checked_{kind}_transport_signature"
        reader = f"read_checked_{kind}_transport_file"
        remover = f"remove_{kind}_transport_file"
        for scope in (creator, checker):
            if scope == creator:
                add(scope, "local-path", "local path", Resource.SHELL, Access.WRITE)
                add(scope, "create", f'path="$(/usr/bin/mktemp "{directory}/$1.XXXXXXXXXX")"', resource, Access.CREATE, context=checked)
                add(scope, "emit-path", r'''printf '%s\n' "$path"''', resource, Access.READ)
            else:
                add(scope, "local-path", 'local path="$1"', Resource.SHELL, Access.WRITE)
                add(scope, "local-limit", 'local size_limit="$2"', Resource.SHELL, Access.WRITE)
                add(scope, "local-size", "local size", Resource.SHELL, Access.WRITE)
                add(scope, "size", 'size="$(/usr/bin/stat -c %s "$path")"', resource, context=checked)
                add(scope, "size-limit", 'test "$size" -le "$size_limit"', resource, context=checked)
                add(scope, "signature", r'''/usr/bin/stat -Lc '%d:%i:%f:%u:%g:%s:%h:%a' "$path"''', resource)
            add(scope, "local-type", "local file_type", Resource.SHELL, Access.WRITE)
            add(scope, "regular", 'test -f "$path"', resource, context=checked)
            add(scope, "not-symlink", 'test ! -L "$path"', resource, context=checked)
            add(scope, "file-type", 'file_type="$(/usr/bin/stat -c %F "$path")"', resource, context=checked)
            control(scope, "regular-type", 'case "$file_type" in "regular file"|"regular empty file") ;; *) ;; esac')
            for name, fmt, value in (("owner", "%u", "0"), ("mode", "%a", "600"), ("links", "%h", "1")):
                add(scope, name, f'test "$(/usr/bin/stat -c {fmt} "$path")" = {value}', resource, context=checked)
            add(scope, "reject", "return 125", Resource.PROCESS, Access.WRITE,
                count=8 if scope == creator else 9, placements=(
                    Placement(rejected, 7 if scope == creator else 8),
                    Placement(case("regular-type", 1, scope)),
                ))
        add(reader, "local-path", 'local path="$1"', Resource.SHELL, Access.WRITE)
        add(reader, "local-limit", 'local size_limit="$2"', Resource.SHELL, Access.WRITE)
        add(reader, "local-output", 'local -n output_ref="$3"', Resource.SHELL, Access.WRITE)
        add(reader, "local-signature", "local signature", Resource.SHELL, Access.WRITE)
        add(reader, "before", f'signature="$({checker} "$path" "$size_limit")"', resource, context=checked)
        add(reader, "read", '''mapfile -d '' -t output_ref < "$path"''', resource, Access.READ, event=EventKind.TRANSPORT_READ, context=checked)
        add(reader, "after", f'test "$({checker} "$path" "$size_limit")" = "$signature"', resource, context=checked)
        add(reader, "reject", "return 125", Resource.PROCESS, Access.WRITE, count=3, context=rejected)
        add(remover, "local-path", 'local path="$1"', Resource.SHELL, Access.WRITE)
        add(remover, "remove", '/bin/rm -f -- "$path"', resource, Access.REMOVE, context=checked)
        add(remover, "absent", 'test ! -e "$path"', resource, context=checked)
        add(remover, "reject", "return 125", Resource.PROCESS, Access.WRITE, count=2, context=rejected)

    main("dev-limit", "dev_mount_targets_max_bytes=1048576", access=Access.WRITE)
    for name, variable, prefix, output in (
        ("dev", "dev_mounts_file", "dev-mount-targets", "dev_mounts"),
        ("remaining-dev", "remaining_dev_mounts_file", "remaining-dev-mount-targets", "remaining_dev_mounts"),
    ):
        main(f"{name}-create", f'{variable}="$(create_supervisor_transport_file {prefix})"', Resource.SUPERVISOR, Access.CREATE)
        main(f"{name}-produce", f'list_dev_mount_targets > "${variable}"', Resource.SUPERVISOR, Access.WRITE, event=EventKind.HELPER_CALL)
        main(
            f"{name}-read",
            f'read_checked_supervisor_transport_file "${variable}" "$dev_mount_targets_max_bytes" {output}',
            Resource.SUPERVISOR, Access.READ, event=EventKind.HELPER_CALL,
        )
        main(f"{name}-remove", f'remove_supervisor_transport_file "${variable}"', Resource.SUPERVISOR, Access.REMOVE, event=EventKind.HELPER_CALL)
    control("builder_main", "dev-descendants", "for ((index=${#dev_mounts[@]} - 1; index >= 0; index--)); do :; done")
    main("dev-target", 'dev_mount="${dev_mounts[index]}"', Resource.SUPERVISOR, Access.READ, context=loop("dev-descendants"))
    control("builder_main", "dev-target-boundary", 'case "$dev_mount" in /dev) ;; /dev/*) ;; *) ;; esac', loop("dev-descendants"))
    main("dev-unmount", '/usr/bin/umount -- "$dev_mount"', Resource.MOUNT_GRAPH, Access.MOUNT, context=loop("dev-descendants") + case("dev-target-boundary", 1))
    main("dev-remaining-count", 'test "${#remaining_dev_mounts[@]}" -eq 1', Resource.SUPERVISOR)
    main("dev-remaining-root", 'test "${remaining_dev_mounts[0]}" = /dev', Resource.SUPERVISOR)
    for name, minor in (("null", 3), ("zero", 5), ("random", 8), ("urandom", 9)):
        main(f"device-{name}", f"/usr/bin/mknod -m 0666 /dev/{name} c 1 {minor}", Resource.RUNTIME, Access.CREATE)
    main("shared-memory", "/usr/bin/mkdir -m 1777 /dev/shm", Resource.RUNTIME, Access.CREATE)
    for target, source in (("fd", "/proc/self/fd"), ("stdin", "/proc/self/fd/0"), ("stdout", "/proc/self/fd/1"), ("stderr", "/proc/self/fd/2")):
        main(f"fd-{target}", f"/usr/bin/ln -s {source} /dev/{target}", Resource.RUNTIME, Access.CREATE)
    main("readonly-dev", "/usr/bin/mount -o remount,ro,nosuid /dev", Resource.MOUNT_GRAPH, Access.MOUNT)
    main("readonly-proc", "/usr/bin/mount -o remount,ro,nosuid,nodev,noexec,hidepid=2 /proc", Resource.MOUNT_GRAPH, Access.MOUNT)
    main("runtime-limit", "writable_mount_records_max_bytes=1048576", access=Access.WRITE)
    main("runtime-create", 'writable_mount_records_file="$(create_runtime_transport_file writable-mount-records)"', Resource.RUNTIME, Access.CREATE)
    main("runtime-produce", 'list_writable_mount_records > "$writable_mount_records_file"', Resource.RUNTIME, Access.WRITE, event=EventKind.HELPER_CALL)
    main("runtime-read", 'read_checked_runtime_transport_file "$writable_mount_records_file" "$writable_mount_records_max_bytes" writable_mount_records', Resource.RUNTIME, Access.READ, event=EventKind.HELPER_CALL)
    main("runtime-remove", 'remove_runtime_transport_file "$writable_mount_records_file"', Resource.RUNTIME, Access.REMOVE, event=EventKind.HELPER_CALL)
    main("runtime-count", 'test "$(( ${#writable_mount_records[@]} % 2 ))" -eq 0', Resource.RUNTIME)
    control("builder_main", "runtime-records", "for ((index=0; index < ${#writable_mount_records[@]}; index+=2)); do :; done")
    main("runtime-target", 'mount_target="${writable_mount_records[index]}"', Resource.RUNTIME, Access.READ, context=runtime_records)
    main("runtime-options", 'mount_options="${writable_mount_records[index + 1]}"', Resource.RUNTIME, Access.READ, context=runtime_records)
    control("builder_main", "runtime-writable", 'case ",$mount_options," in *,rw,*) ;; esac', runtime_records)
    control("builder_main", "runtime-write-boundary", 'case "$mount_target" in /dev/shm|/mnt/handoff|/mnt/home|/mnt/source|/mnt/supervisor|/mnt/tmp|/tmp) ;; *) ;; esac', runtime_writable)
    main("runtime-rejection", 'echo "unexpected writable mount: $mount_target" >&2', Resource.NULL, Access.WRITE, context=runtime_rejection)
    for option, limit in (("c", 0), ("f", 131072), ("n", 128), ("u", 512), ("v", 8388608)):
        main(f"limit-{option}", f"ulimit -{option} {limit}", Resource.PROCESS, Access.WRITE)
    launcher = Program(
        "candidate-launcher", "scripts/workflow_pilot/publisher_candidate.py",
        AUTHORITY_RUNTIME_PATH, "--runtime-program",
        (ResourceAccess(Resource.CONTROL, Access.READ),),
        (ResourceAccess(Resource.CANDIDATE, Access.EXECUTE),),
    )
    launch = f'/usr/bin/python3 -I -S {AUTHORITY_RUNTIME_PATH} --runtime-program candidate-launcher "$builder_uid" "$builder_gid" /mnt/control/candidate-build.sh "$host_runner_temp"'
    main(
        "candidate-launch", launch,
        Resource.CANDIDATE, Access.EXECUTE, event=EventKind.CANDIDATE_LAUNCH,
        program=launcher, extra=launcher.inputs,
        context=(Context("if", "builder_main.candidate-launch", "condition"),),
    )
    control("builder_main", "candidate-launch", f"if {launch}; then :; else :; fi")
    main(
        "candidate-success", "candidate_status=0", Resource.PROCESS, Access.READ,
        event=EventKind.CANDIDATE_STATUS,
        context=(Context("if", "builder_main.candidate-launch", "success"),),
    )
    main(
        "candidate-status", 'candidate_status="$?"', Resource.PROCESS, Access.READ,
        event=EventKind.CANDIDATE_STATUS,
        context=(Context("if", "builder_main.candidate-launch", "failure"),),
    )
    control("builder_main", "candidate-result", 'if [ "$candidate_status" -ne 0 ]; then :; fi')
    main("candidate-failed", '[ "$candidate_status" -ne 0 ]', Resource.PROCESS, context=(Context("if", "builder_main.candidate-result", "condition"),))
    candidate_failure = (Context("if", "builder_main.candidate-result", "success"),)
    control(
        "builder_main", "candidate-failure",
        'case "$candidate_status" in 71|72|73|74|75|76) ;; 125|126) ;; *) ;; esac',
        candidate_failure,
    )
    main(
        "candidate-exit", 'exit "$candidate_status"', Resource.PROCESS, Access.WRITE,
        count=2, placements=tuple(
            Placement(candidate_failure + case("candidate-failure", arm)) for arm in (0, 1)
        ),
    )
    main(
        "candidate-unknown", "exit 77", Resource.PROCESS, Access.WRITE,
        context=candidate_failure + case("candidate-failure", 2),
    )
    membership = Program(
        "membership", PROGRAM_PATH, AUTHORITY_RUNTIME_PATH, "--runtime-program",
        (ResourceAccess(Resource.CGROUP_VIEW, Access.READ), ResourceAccess(Resource.PROCESS, Access.INSPECT)),
        (),
    )
    main(
        "membership-check",
        f'/usr/bin/python3 -I -S {AUTHORITY_RUNTIME_PATH} --runtime-program membership "$$"',
        Resource.CGROUP_VIEW, Access.READ,
        event=EventKind.MEMBERSHIP_VERIFIED, program=membership,
        extra=(ResourceAccess(Resource.PROCESS, Access.INSPECT),),
    )
    main(
        "handoff-names",
        r'''handoff_names="$(/usr/bin/find /mnt/handoff -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C /usr/bin/sort)"''',
        Resource.HANDOFF, Access.READ,
    )
    main("handoff-inventory", r'''test "$handoff_names" = "$(printf 'metadata.json\ntarget.gba')"''', Resource.HANDOFF)
    control("builder_main", "handoff-files", "for output in /mnt/handoff/target.gba /mnt/handoff/metadata.json; do :; done")
    for name, source in (
        ("regular", 'test -f "$output"'),
        ("not-symlink", 'test ! -L "$output"'),
        ("type", 'test "$(/usr/bin/stat -c %F "$output")" = "regular file"'),
        ("links", 'test "$(/usr/bin/stat -c %h "$output")" = 1'),
        ("owner", 'test "$(/usr/bin/stat -c %u "$output")" = "$builder_uid"'),
        ("rom-size", 'test "$(/usr/bin/stat -c %s /mnt/handoff/target.gba)" = 33554432'),
        ("metadata-size", 'metadata_size="$(/usr/bin/stat -c %s /mnt/handoff/metadata.json)"'),
        ("metadata-nonempty", 'test "$metadata_size" -gt 0'),
        ("metadata-bound", 'test "$metadata_size" -le 1048576'),
    ):
        main(f"handoff-{name}", source, Resource.HANDOFF,
            context=loop("handoff-files") if name in {"regular", "not-symlink", "type", "links", "owner"} else ())
    main(
        "export-open", "/usr/bin/mount -o remount,bind,rw,nosuid,nodev,noexec /mnt/export",
        Resource.EXPORT, Access.MOUNT, event=EventKind.EXPORT_OPEN,
    )
    for filename in ("target.gba", "metadata.json"):
        main(
            f"export-{filename}",
            f"/usr/bin/install -m 0400 /mnt/handoff/{filename} /mnt/export/{filename}",
            Resource.EXPORT, Access.WRITE, event=EventKind.EXPORT_FILE,
            extra=(ResourceAccess(Resource.HANDOFF, Access.READ),),
        )
    main("export-owner", '/usr/bin/chown "$host_uid:$host_gid" /mnt/export/target.gba /mnt/export/metadata.json', Resource.EXPORT, Access.WRITE)
    post_check = Program(
        "post-check", PROGRAM_PATH, AUTHORITY_RUNTIME_PATH, "--runtime-program",
        (
            ResourceAccess(Resource.EXPORT, Access.READ),
            ResourceAccess(Resource.MOUNT_GRAPH, Access.INSPECT),
            ResourceAccess(Resource.PROCESS, Access.INSPECT),
        ),
        (),
    )
    main(
        "post-check",
        f'/usr/bin/python3 -I -S {AUTHORITY_RUNTIME_PATH} --runtime-program post-check "$$" "$host_uid" "$host_gid"',
        Resource.EXPORT, Access.READ, event=EventKind.POST_CHECK, program=post_check,
        extra=post_check.inputs[1:],
    )
    main("success", "exit 0", Resource.PROCESS, Access.WRITE)
    return Inventory(tuple(signatures), tuple(scopes), tuple(controls))
