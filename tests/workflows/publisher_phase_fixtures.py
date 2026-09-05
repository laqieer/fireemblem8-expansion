"""Shared phase mutations; edits select already-authorized parsed command spans."""

from scripts.workflow_pilot import publisher_inventory as authority
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
