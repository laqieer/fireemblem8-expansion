"""Shared phase mutations; edits select already-authorized parsed command spans."""

from contextlib import contextmanager
import shlex
from unittest import mock

from scripts.workflow_pilot import publisher_inventory as authority
from scripts.workflow_pilot import publisher_shell as shell
from scripts.workflow_pilot import publisher_shell_contract as contract
from tests.workflows import publisher_inventory_fixtures as inventory


def command(source, name, occurrence=0):
    matches = [
        item.command for item in authority.reviewed_inventory().validate(source).commands
        if not item.nested and item.signature.name == "builder_main." + name
    ]
    return matches[occurrence]


def replace_command(source, name, replacement, occurrence=0):
    item = command(source, name, occurrence)
    return source[:item.offset] + replacement + source[item.end:]


def move_command(source, name, target, *, after=False, occurrence=0, target_occurrence=0):
    item = command(source, name, occurrence)
    destination = command(source, target, target_occurrence)
    text = source[item.offset:item.end]
    position = destination.end if after else destination.offset
    edits = [(item.offset, item.end, ""), (position, position, "\n" + text + "\n")]
    for start, end, replacement in sorted(edits, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def adversarial_builders(source):
    checker_node = command(source, "membership-check")
    checker = source[checker_node.offset:checker_node.end]
    yield "early-before-launch", move_command(source, "membership-check", "stage-candidate-preflight")
    yield "late-after-export", move_command(source, "membership-check", "export-owner", after=True)
    yield "late-after-post-check", move_command(source, "membership-check", "post-check", after=True)
    yield "missing-checker", replace_command(source, "membership-check", "")
    yield "duplicate-checker", replace_command(source, "membership-check", checker + "\n" + checker)
    yield "skipped-failure-arm", move_command(source, "membership-check", "candidate-exit")
    yield "conditional-launch-arm", move_command(source, "membership-check", "candidate-success", after=True)
    yield "background-checker", replace_command(source, "membership-check", checker + " &")
    yield "pipeline-checker", replace_command(source, "membership-check", checker + " |")
    yield "subshell-checker", replace_command(source, "membership-check", "(" + checker + ")")
    yield "substitution-checker", replace_command(source, "membership-check", 'unused="$(' + checker + ')"')
    yield "helper-checker", replace_command(
        source, "membership-check", "check_membership() { " + checker + "; }\ncheck_membership",
    )
    yield "trap-checker", replace_command(source, "membership-check", "trap '" + checker + "' EXIT")
    yield "callback-checker", replace_command(
        source, "membership-check", "mapfile -C '" + checker + "' -c 1 -t unused < /dev/null",
    )
    entry = source.rindex('builder_main "$@"')
    without = source[:checker_node.offset] + source[checker_node.end:]
    yield "wrong-entry-frame", without[:entry - (checker_node.end - checker_node.offset)] + "\n" + checker + "\n" + without[entry - (checker_node.end - checker_node.offset):]
    yield "background-builder-frame", source[:entry] + source[entry:].replace('builder_main "$@"', 'builder_main "$@" &', 1)
    yield "conditional-initialization", move_command(source, "initialize-cgroup_path", "candidate-exit")
    yield "early-export-open", move_command(source, "export-open", "membership-check")
    yield "early-export-file", move_command(source, "export-target.gba", "membership-check")
    yield "early-owner", move_command(source, "export-owner", "export-target.gba")
    yield "early-post-check", move_command(source, "post-check", "export-open")
    yield "conditional-post-check", move_command(source, "post-check", "candidate-exit")
    yield "late-initial-seal", move_command(source, "readonly-export", "membership-check")
    yield "early-final-seal", move_command(source, "readonly-export", "export-open", occurrence=1)
    yield "missing-final-seal", replace_command(source, "readonly-export", "", occurrence=1)
    yield "missing-post-check", replace_command(source, "post-check", "")
    yield "early-success", move_command(source, "success", "membership-check")
    yield "wrong-failure-frame", move_command(source, "stage-output-validate", "export-owner")
    yield "early-mount-audit", move_command(source, "stage-mount-audit", "readonly-control")
    yield "namespace-after-mount-audit", move_command(source, "readonly-control", "runtime-create")
    yield "audit-limit-in-namespace", move_command(source, "limit-c", "readonly-control")
    yield "namespace-limit-in-audit", move_command(source, "runtime-limit", "runtime-create")
    yield "audit-output-in-namespace", move_command(source, "suppress-output", "readonly-control", occurrence=1)
    yield "errexit-disabled", replace_command(source, "strict-shell", "set -Euo pipefail")
    yield "wrong-trap", replace_command(source, "stage-trap", "trap isolated_stage_failure EXIT")
    launcher_node = command(source, "candidate-launch")
    launcher = source[launcher_node.offset:launcher_node.end]
    yield "incomplete-background-launch", replace_command(source, "candidate-launch", launcher + " &")
    yield "captured-launch", replace_command(source, "candidate-launch", 'captured="$(' + launcher + ')"')
    yield "false-success-capture", replace_command(source, "candidate-status", "candidate_status=0")
    yield "skipped-result-guard", replace_command(source, "candidate-failed", "candidate_status=0")

    helper_start = source.index("isolated_stage_failure() {")
    helper_end = source.index("\n}", helper_start) + 2
    helper = source[helper_start:helper_end]
    rest = source[:helper_start] + source[helper_end:]
    target = rest.index('cgroup_path="$1"')
    yield "late-error-handler", rest[:target] + helper + "\n" + rest[target:]

    success = command(source, "candidate-success")
    failure = command(source, "candidate-status")
    success_text, failure_text = source[success.offset:success.end], source[failure.offset:failure.end]
    changed = source[:failure.offset] + success_text + source[failure.end:]
    changed = changed[:success.offset] + failure_text + changed[success.end:]
    yield "wrong-result-edges", changed
    # Preserve all signatures/counts while selecting the wrong diagnostic arm.
    yield "wrong-isolated-substage", source.replace(
        "namespace) exit 81", "namespace) exit 85",
    ).replace("post-check) exit 85", "post-check) exit 81")


def adversarial_workflows(workflow):
    for name, source in adversarial_builders(inventory.builder(workflow)):
        yield name, inventory.replace_builder(workflow, source)


def prerequisite_builders(source):
    for name, moved, target in (
        ("cgroup-use-before-input", "initialize-cgroup_path", "join-cgroup"),
        ("cgroup-bind-before-owner", "cgroup-owner", "cgroup-bind"),
        ("cgroup-readonly-before-bind", "cgroup-bind", "cgroup-readonly"),
        ("cgroup-inode-before-readonly", "cgroup-readonly", "cgroup-inode"),
        ("cgroup-inode-before-alias", "cgroup-view-name", "cgroup-inode"),
        ("cgroup-options-before-readonly", "cgroup-readonly", "cgroup-options"),
        ("cgroup-options-before-alias", "cgroup-view-name", "cgroup-options"),
        ("dev-read-before-produce", "dev-produce", "dev-read"),
        ("dev-produce-before-create", "dev-create", "dev-produce"),
        ("dev-remove-before-read", "dev-read", "dev-remove"),
        ("dev-read-before-limit", "dev-limit", "dev-read"),
        ("remaining-read-before-produce", "remaining-dev-produce", "remaining-dev-read"),
        ("remaining-produce-before-create", "remaining-dev-create", "remaining-dev-produce"),
        ("remaining-remove-before-read", "remaining-dev-read", "remaining-dev-remove"),
        ("remaining-count-before-read", "remaining-dev-read", "dev-remaining-count"),
        ("remaining-root-before-read", "remaining-dev-read", "dev-remaining-root"),
        ("runtime-read-before-produce", "runtime-produce", "runtime-read"),
        ("runtime-produce-before-create", "runtime-create", "runtime-produce"),
        ("runtime-remove-before-read", "runtime-read", "runtime-remove"),
        ("runtime-count-before-read", "runtime-read", "runtime-count"),
    ):
        yield name, move_command(source, moved, target, after=True)

    for kind in ("supervisor", "runtime"):
        helper = f"read_checked_{kind}_transport_file"
        analyzed = authority.reviewed_inventory().validate(source)
        commands = {
            item.signature.name.removeprefix(helper + "."): item.command
            for item in analyzed.commands if not item.nested and item.scope == helper
        }
        for moved, target in (
            ("before", "read"), ("after", "before"),
            ("local-path", "before"), ("local-limit", "before"),
            ("local-output", "read"), ("local-signature", "before"),
        ):
            item, destination = commands[moved], commands[target]
            # Move the whole checked chain, not just one operand of ||.
            start = source.rfind("\n", 0, item.offset) + 1
            end = source.index("\n", item.end) + 1
            position = source.index("\n", destination.end) + 1
            edits = ((start, end, ""), (position, position, source[start:end]))
            changed = source
            for left, right, replacement in sorted(edits, reverse=True):
                changed = changed[:left] + replacement + changed[right:]
            yield f"{kind}-{moved}-after-{target}", changed

    for name, initial, boundary in (
        ("cgroup-options-after-check", "cgroup-options", 'for option in ro nosuid nodev noexec; do'),
        ("dev-target-after-use", "dev-target", 'case "$dev_mount" in'),
        ("runtime-target-after-use", "runtime-target", 'case ",$mount_options," in'),
        ("runtime-options-after-use", "runtime-options", 'case ",$mount_options," in'),
    ):
        item = command(source, initial)
        start = source.rfind("\n", 0, item.offset) + 1
        end = source.index("\n", item.end) + 1
        case_start = source.index(boundary)
        closing = "done" if name.startswith("cgroup-") else "esac"
        position = source.index("\n", source.index(closing, case_start)) + 1
        if name.startswith("runtime-"):
            position = source.index("\n", source.index("esac", position)) + 1
        changed = source
        for left, right, replacement in sorted(
            ((start, end, ""), (position, position, source[start:end])), reverse=True,
        ):
            changed = changed[:left] + replacement + changed[right:]
        yield name, changed

    start = source.index('remaining_dev_mounts_file="$(create_supervisor_transport_file')
    end_node = command(source, "remaining-dev-remove")
    end = source.index("\n", end_node.end) + 1
    position = source.index("for ((index=${#dev_mounts[@]}")
    yield "remaining-snapshot-before-unmount", (
        source[:position] + source[start:end] + source[position:start] + source[end:]
    )


def prerequisite_workflows(workflow):
    for name, source in prerequisite_builders(inventory.builder(workflow)):
        yield name, inventory.replace_builder(workflow, source)


def producer_workflows(workflow):
    name = "Build candidate in isolated namespace and stage public inputs"
    source = contract.publisher_run_script(workflow, name)
    analysis = authority.reviewed_inventory().validate(source, entry_scope="staging")
    commands = {
        item.signature.name: item.command for item in analysis.commands
        if not item.nested and item.scope == "staging"
    }

    def statement(key):
        node = commands["staging." + key]
        return source[node.offset:node.end]

    def replace_statement(key, replacement):
        node = commands["staging." + key]
        return source[:node.offset] + replacement + source[node.end:]

    launcher = statement("candidate-source")
    delayed = replace_statement("candidate-source", "")
    initialized = statement("command-3")
    yield "late-launcher-staging", inventory.replace_step(
        workflow, name, delayed.replace(initialized, initialized + "\n" + launcher, 1),
    )
    yield "tail-duplicate-launcher-staging", inventory.replace_step(
        workflow, name, source + "\n" + launcher + "\n",
    )
    for case, key, replacement in (
        ("launcher-source-ref", "candidate-source", launcher.replace("$PATCH_COMMIT:", "HEAD:")),
        ("launcher-chmod-path", "command-54",
         statement("command-54").replace("$PATCH_RUNTIME_ROOT/", "$BUILDER_ROOT/control/")),
        ("launcher-transport-path", "command-80",
         statement("command-80").replace("$PATCH_RUNTIME_ROOT/candidate-launcher.py", "$BUILDER_ROOT/control/candidate-launcher.py")),
        ("wrong-report-substage", "isolated-namespace", "builder_isolated_detail=post-check"),
        ("missing-report-detail", "command-125",
         r"""printf 'candidate build failed: stage=isolated exit=%d\n' "$builder_status" >&2"""),
    ):
        yield case, inventory.replace_step(workflow, name, replace_statement(key, replacement))


@contextmanager
def captured_programs(workflow):
    """Model a changed committed payload, not a dirty text/canonical mismatch."""
    reader = authority.authority_source_bytes
    with mock.patch.object(
        authority, "authority_source_bytes",
        side_effect=lambda path: workflow.encode() if path == authority.WORKFLOW_PATH else reader(path),
    ):
        yield


def candidate_script(workflow):
    source = contract.publisher_run_script(workflow)
    bodies = [
        redirect.body for chain in shell.parse(source).items for node in chain.nodes
        if isinstance(node, shell.Command)
        for redirect in node.redirects
        if redirect.operator == "<<" and redirect.target.literal == "CANDIDATE_BUILD"
    ]
    body, = bodies
    return body


def replace_candidate(workflow, body):
    source = contract.publisher_run_script(workflow)
    original = candidate_script(workflow)
    assert source.count(original) == 1
    return inventory.replace_step(
        workflow, "Build candidate in isolated namespace and stage public inputs",
        source.replace(original, body, 1),
    )


def root_commands(source):
    return [
        node for chain in shell.parse(source).items for node in chain.nodes
        if isinstance(node, shell.Command)
    ]


def move_lines(source, node, position):
    start = source.rfind("\n", 0, node.offset) + 1
    end = source.index("\n", node.end) + 1
    edits = ((start, end, ""), (position, position, source[start:end]))
    for left, right, replacement in sorted(edits, reverse=True):
        source = source[:left] + replacement + source[right:]
    return source


def host_failure_script(workflow):
    source = contract.publisher_run_script(workflow)
    start = source.index('if [ "$builder_status" -ne 0 ]; then')
    end = source.index("printf 'candidate build status: success", start)
    return source[start:end]


def diagnostic_workflows(workflow):
    body = candidate_script(workflow)
    stages = ("preflight", "venv", "pip", "build-tools", "make", "handoff")
    commands = root_commands(body)
    yield "candidate-exits-make-preflight", replace_candidate(
        workflow, body.replace("preflight) exit 71", "preflight) exit 75").replace(
            "make) exit 75", "make) exit 71",
        ),
    )
    yield "candidate-assignments-make-preflight", replace_candidate(
        workflow, body.replace("candidate_stage=preflight\n", "candidate_stage=placeholder\n").replace(
            "candidate_stage=make\n", "candidate_stage=preflight\n",
        ).replace("candidate_stage=placeholder\n", "candidate_stage=make\n"),
    )
    for index, stage in enumerate(stages):
        following = stages[(index + 1) % len(stages)]
        status, next_status = 71 + index, 71 + (index + 1) % len(stages)
        changed = body.replace(f"{stage}) exit {status}", f"{stage}) exit {next_status}")
        changed = changed.replace(f"{following}) exit {next_status}", f"{following}) exit {status}")
        yield "candidate-exits-" + stage, replace_candidate(workflow, changed)
        yield "candidate-assignment-" + stage, replace_candidate(
            workflow, body.replace(f"candidate_stage={stage}\n", f"candidate_stage={following}\n"),
        )
        assignment = next(
            node for node in commands
            if any(word.literal == f"candidate_stage={stage}" for word in node.environment)
        )
        action = next(
            node for node in commands if node.offset > assignment.offset and node.argv
            and node.argv[0].literal != "trap"
        )
        yield "late-assignment-" + stage, replace_candidate(
            workflow, move_lines(body, assignment, body.index("\n", action.end) + 1),
        )
    yield "candidate-default-exit", replace_candidate(workflow, body.replace("*) exit 77", "*) exit 71"))
    yield "candidate-literal-default", replace_candidate(workflow, body.replace("*) exit 77", "'*') exit 77"))
    first_arm = "    preflight) exit 71 ;;\n"
    last_arm = "    *) exit 77 ;;\n"
    yield "candidate-early-default", replace_candidate(
        workflow, body.replace(last_arm, "").replace(first_arm, last_arm + first_arm),
    )
    yield "candidate-conditional-assignment", replace_candidate(
        workflow, body.replace("candidate_stage=make\n", "if true; then candidate_stage=make; fi\n"),
    )
    yield "candidate-background-make", replace_candidate(
        workflow, body.replace("make expansion-modern-map-menu-presentation-check -j1\n",
                               "make expansion-modern-map-menu-presentation-check -j1 &\n"),
    )
    yield "candidate-conditional-make", replace_candidate(
        workflow, body.replace("make expansion-modern-map-menu-presentation-check -j1\n",
                               "if true; then make expansion-modern-map-menu-presentation-check -j1; fi\n"),
    )
    yield "candidate-hidden-make-helper", replace_candidate(
        workflow, body.replace("candidate_stage=make\n",
                               "hidden_build() { make expansion-modern-map-menu-presentation-check -j1; }\n"
                               "hidden_build\ncandidate_stage=make\n"),
    )
    yield "candidate-wrong-trap", replace_candidate(
        workflow, body.replace("trap candidate_stage_failure ERR", "trap candidate_stage_failure EXIT"),
    )
    yield "candidate-errexit-disabled", replace_candidate(
        workflow, body.replace("set -Eeuo pipefail", "set -Euo pipefail"),
    )
    source = contract.publisher_run_script(workflow)
    analysis = authority.reviewed_inventory().validate(source, entry_scope="staging")
    root = [item for item in analysis.commands if not item.nested and item.scope == "staging"]
    diagnostic = next(item.command for item in root if item.signature.name == "staging.command-125")
    guard = next(item.command for item in root if item.signature.name == "staging.command-124")
    exit_node = next(
        item.command for item in root if item.signature.name == "staging.command-111"
        and item.command.offset > guard.offset
    )
    mapping = source.rfind("\n", 0, source.index('case "$builder_status" in', guard.end)) + 1
    diagnostic_start = source.rfind("\n", 0, diagnostic.offset) + 1
    for name, node, position in (
        ("host-diagnostic-before-map", diagnostic, mapping),
        ("host-exit-before-map", exit_node, mapping),
        ("host-exit-before-diagnostic", exit_node, diagnostic_start),
    ):
        yield name, inventory.replace_step(
            workflow, "Build candidate in isolated namespace and stage public inputs",
            move_lines(source, node, position),
        )


def diagnostic_spelling_control(workflow):
    body = candidate_script(workflow)
    body = body.replace("candidate_stage_failure", "report_candidate_failure")
    body = body.replace("candidate_stage", "failure_phase")
    for stage in ("preflight", "venv", "pip", "build-tools", "make", "handoff"):
        body = body.replace("failure_phase=" + stage, "failure_phase='" + stage + "'")
    body = body.replace("trap report_candidate_failure ERR", "trap 'report_candidate_failure' 'ERR'")
    body = body.replace('case "$failure_phase" in', 'case "${failure_phase}" in')
    body = body.replace(
        "    preflight) exit 71 ;;\n    venv) exit 72 ;;",
        "    'venv') exit '72' ;;\n    'preflight') exit '71' ;;",
    )
    body = body.replace("make expansion-modern", "'make' expansion-modern")
    installations = [
        node for node in root_commands(body)
        if node.argv and node.argv[0].literal == "/usr/bin/install"
    ]
    body = move_lines(body, installations[1], installations[0].offset)
    return replace_candidate(workflow, body)


def unclassified_candidate_workflows(workflow):
    body = candidate_script(workflow)
    yield "handler-shadows-make", replace_candidate(
        workflow, body.replace("candidate_stage_failure", "make"),
    )
    python = """/usr/bin/python3 -c 'open("runtime-marker", "w").write("executed")'"""
    for name, statement in (
        ("root-python", python),
        ("called-python-helper", "candidate_extension() { " + python + "; }\ncandidate_extension"),
        ("uncalled-python-helper", "candidate_extension() { " + python + "; }"),
        ("makeflags-assignment", "MAKEFLAGS=-n"),
        ("exported-makeflags", "export MAKEFLAGS=-n"),
        ("printf-variable-write", "printf -v MAKEFLAGS '%s' -n"),
        ("unknown-executable", "/unregistered/candidate-command"),
        ("root-socket-scan", "/usr/bin/find / -xdev -type s -print -quit 2>/dev/null"),
        ("nested-python", 'test -z "$(' + python + ')"'),
        ("nested-assignment", 'test -z "$(MAKEFLAGS=-n)"'),
        ("nested-format-python", "printf '%s\\n' \"$(" + python + ")\""),
        ("unclassified-if", "if test -z ''; then test -z ''; fi"),
        ("extra-preflight-loop", "for extra_path in /; do test -d \"$extra_path\"; done"),
    ):
        yield name, replace_candidate(
            workflow, body.replace("trap candidate_stage_failure ERR\n",
                                   "trap candidate_stage_failure ERR\n" + statement + "\n"),
        )
    yield "environment-on-make", replace_candidate(
        workflow, body.replace("make expansion-modern", "MAKEFLAGS=-n make expansion-modern"),
    )
    yield "altered-nested-socket-scan", replace_candidate(
        workflow, body.replace("/usr/bin/find / -xdev -type s", "/usr/bin/find / -xdev -type f"),
    )
    yield "assignment-in-preflight-loop", replace_candidate(
        workflow, body.replace('  test -e "$readonly_path"',
                               '  MAKEFLAGS=-n\n  test -e "$readonly_path"'),
    )


def exact_candidate_workflows(workflow):
    body = candidate_script(workflow)
    fd_check = next(
        node for node in root_commands(body)
        if tuple(word.literal for word in node.argv[:4]) == ("/usr/bin/python3", "-I", "-S", "-c")
    )
    program = 'open("candidate-marker", "w").write("unregistered"); ' + fd_check.argv[4].literal
    changed = body[:fd_check.offset] + "/usr/bin/python3 -I -S -c " + shlex.quote(program) + body[fd_check.end:]
    yield "unregistered-inline-program", replace_candidate(workflow, changed)
    for name, original, changed in (
        ("unregistered-make-target", "make expansion-modern-map-menu-presentation-check -j1", "make unregistered-target -j1"),
        ("inert-export-preflight", "test ! -w /mnt/export", "test 1 = 1"),
        ("unregistered-make-redirection", "make expansion-modern-map-menu-presentation-check -j1",
         "make expansion-modern-map-menu-presentation-check -j1 > unregistered-output"),
        ("unregistered-venv-startup", '/usr/bin/python3 -m venv "$HOME/venv"', '/usr/bin/python3 -I -m venv "$HOME/venv"'),
        ("unregistered-pip-module", "-m pip install", "-m pip.__main__ install"),
        ("unregistered-pip-interpreter", '"$HOME/venv/bin/python3"', '"$HOME/other/bin/python3"'),
        ("unregistered-pip-variable", '"$HOME/venv/bin/python3"', '"$OTHER/venv/bin/python3"'),
        ("unregistered-pip-hashes", "--require-hashes --only-binary", "--only-binary"),
        ("unregistered-tools-argument", "./build_tools.sh\n", "./build_tools.sh unregistered\n"),
        ("unregistered-handoff-mode", "/usr/bin/install -m 0400", "/usr/bin/install -m 0600"),
        ("unregistered-readonly-header", "readonly_path in / /etc", "readonly_path in /unregistered /etc"),
        ("capturing-loop-binding", "readonly_path", "PATH"),
        ("unregistered-fd-environment", "/usr/bin/python3 -I -S -c", "UNREGISTERED=yes /usr/bin/python3 -I -S -c"),
    ):
        assert original in body
        yield name, replace_candidate(workflow, body.replace(original, changed))


INVENTORY_CONTEXT_CASES = frozenset({
    "skipped-failure-arm", "conditional-launch-arm", "background-checker", "pipeline-checker",
    "background-builder-frame", "conditional-initialization", "conditional-post-check",
    "wrong-result-edges", "wrong-isolated-substage",
})


PHASE_ONLY_CASES = frozenset({
    "early-before-launch", "late-after-export", "late-after-post-check",
    "early-export-open", "early-export-file", "early-owner", "early-post-check",
    "late-initial-seal", "early-final-seal", "early-success",
    "wrong-failure-frame", "late-error-handler",
    "early-mount-audit", "namespace-after-mount-audit",
    "audit-limit-in-namespace", "namespace-limit-in-audit",
    "audit-output-in-namespace",
})
