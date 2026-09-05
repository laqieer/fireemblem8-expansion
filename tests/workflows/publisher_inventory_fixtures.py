"""One adversarial corpus exercised by both production semantic consumers."""

from contextlib import contextmanager
from unittest import mock

from scripts.workflow_pilot import publisher_shell_contract as contract


COMPOSED_PYTHON_READER = (
    """/usr/bin/python3 -c 'open("/mnt/supervisor/cgroup/"+"""
    """"cgroup."+"procs","rb").read()'"""
)


def builder(workflow: str) -> str:
    return contract.builder_isolation_shell_source(
        contract.publisher_run_script(workflow), label="inventory fixture",
    )


def replace_builder(workflow: str, source: str) -> str:
    opener = "<<'BUILDER_ISOLATION'\n"
    start = workflow.index(opener) + len(opener)
    end = workflow.index("        BUILDER_ISOLATION\n", start)
    indented = "".join("        " + line if line.strip() else line for line in source.splitlines(keepends=True))
    return workflow[:start] + indented + workflow[end:]


def replace_step(workflow: str, name: str, source: str) -> str:
    original = contract.publisher_run_script(workflow, name)

    def indent(text):
        return "".join("        " + line if line.strip() else line for line in text.splitlines(keepends=True))

    assert indent(original) in workflow
    return workflow.replace(indent(original), indent(source), 1)


def context_and_producer_workflows(workflow: str):
    source = builder(workflow)
    for operator in ("&", "&&", "||", "|", "|&"):
        yield "builder-operator-" + operator, replace_builder(
            workflow, source.replace("cd /\n", f"cd / {operator}\n", 1),
        )
    for name, target in (
        ("branch", 'exit "$candidate_status"'),
        ("loop", 'test -d "$hidden"'),
    ):
        yield "builder-relocated-" + name, replace_builder(
            workflow, source.replace("cd /\n", "", 1).replace(target, "cd /; " + target, 1),
        )
    yield "registered-failure-operator", replace_builder(
        workflow, source.replace("|| return 125", "&& return 125", 1),
    )
    old = 'path="$(/usr/bin/mktemp "/mnt/supervisor/$1.XXXXXXXXXX")" || return 125'
    yield "registered-operator-operand", replace_builder(
        workflow, source.replace(old, 'return 125 || ' + old.split(" || ")[0], 1),
    )
    for name in (
        "Verify exact candidate and stage trusted producer",
        "Build candidate in isolated namespace and stage public inputs",
    ):
        run = contract.publisher_run_script(workflow, name)
        unset_end = "GIT_OBJECT_DIRECTORY GIT_REPLACE_REF_BASE GIT_WORK_TREE\n"
        for mutation, changed in (
            ("after-unset", run.replace(unset_end, unset_end + "/unregistered/producer-command\n", 1)),
            ("tail", run + "/unregistered/producer-command\n"),
            ("substitution-tail", run + "extra=$(/unregistered/producer-command)\n"),
            ("helper-tail", run + "extra() { /unregistered/producer-command; }\nextra\n"),
        ):
            assert changed != run
            yield name + "-" + mutation, replace_step(workflow, name, changed)
        if name.startswith("Verify"):
            changed = run.replace('test "$ACTUAL_SHA" = "$PATCH_COMMIT"\n', "", 1).replace(
                'test -f "$source"', 'test "$ACTUAL_SHA" = "$PATCH_COMMIT"; test -f "$source"', 1,
            )
            yield "producer-registered-branch", replace_step(workflow, name, changed)
            yield "producer-quoted-regex-anchor", replace_step(
                workflow, name, run.replace('=~ ^[0-9a-f]', '=~ "^"[0-9a-f]', 1),
            )
            yield "producer-quoted-keyword", replace_step(
                workflow, name, run.replace('[[ "$PATCH_COMMIT"', '\'[[\' "$PATCH_COMMIT"', 1),
            )
        else:
            changed = run.replace(" < /dev/null > /dev/null 2>&1 &\n", " < /dev/null > /dev/null 2>&1\n", 1)
            assert changed != run
            yield "producer-required-background", replace_step(workflow, name, changed)
            changed = run.replace('builder_gid=""\n', "", 1).replace(
                "builder_launch_detail=session-query",
                'builder_gid=""; builder_launch_detail=session-query', 1,
            )
            yield "producer-registered-existing-branch", replace_step(workflow, name, changed)


def adversarial_commands():
    return (
        ("composed-python", COMPOSED_PYTHON_READER),
        ("isolated-python-code", COMPOSED_PYTHON_READER.replace("python3 -c", "python3 -I -S -c")),
        ("python-heredoc", "/usr/bin/python3 -I -S - <<'PY'\nopen('/mnt/supervisor/cgroup/' + 'cgroup.' + 'procs').read()\nPY"),
        ("awk", """/usr/bin/awk 'BEGIN { n="cgroup." "procs"; getline x < ("/mnt/supervisor/cgroup/" n) }'"""),
        ("perl", """/usr/bin/perl -e 'open my $f, "<", "/mnt/supervisor/cgroup/" . "cgroup." . "procs"; <$f>'"""),
        ("shell-code", """/bin/bash -c 'leaf=cgroup.; leaf+=procs; /bin/cat "/mnt/supervisor/cgroup/$leaf"'"""),
        ("split-command", '''/usr/bin/python3 -c 'import sys; open("".join(sys.argv[1:])).read()' /mnt/supervisor/cgroup/ cgroup. procs'''),
        ("dynamic-executable", '''runner=/usr/bin/python3\n"$runner" -c 'open("/mnt/supervisor/cgroup/"+"cgroup."+"procs").read()' '''),
        ("dynamic-path", '''"/usr/bin/$interpreter" -c 'print(1)' '''),
        ("alternate-absolute", "/custom/reader /mnt/supervisor/cgroup/ cgroup. procs"),
        ("timeout-wrapper", "/usr/bin/timeout 1 " + COMPOSED_PYTHON_READER),
        ("env-wrapper", "/usr/bin/env -i " + COMPOSED_PYTHON_READER),
        ("env-split", '''/usr/bin/env -S 'python3 -c pass' '''),
        ("command-wrapper", "command -- " + COMPOSED_PYTHON_READER),
        ("builtin-wrapper", 'builtin printf -v cgroup_path %s /untrusted'),
        ("time-wrapper", "time -p " + COMPOSED_PYTHON_READER),
        ("assignment-prefix", "LC_ALL=C " + COMPOSED_PYTHON_READER),
        ("unknown-helper", '''leak() { /bin/cat "$1/$2$3"; }\nleak /mnt/supervisor/cgroup cgroup. procs'''),
        ("captured-helper", '''leaf=cgroup.\nleak() { /bin/cat "/mnt/supervisor/cgroup/$leaf$1"; }\nleak procs'''),
        ("split-function-shadow", "test()\n{\n  return 0\n}\n"),
        ("function-keyword-shadow", "function test { return 0; }"),
        ("alias", "alias test=true"),
        ("command-hash", "BASH_CMDS[test]=/candidate/test"),
        ("dispatch-options", "shopt -s expand_aliases"),
        ("source", "source /candidate/helper"),
        ("eval", "eval \"$program\""),
        ("substitution", "result=$(" + COMPOSED_PYTHON_READER + ")"),
        ("backtick", "result=`/custom/reader`"),
        ("process-substitution", "mapfile -t leaked < <(/custom/reader)"),
        ("read-redirection", 'read cgroup_path<<<"/untrusted"'),
        ("callback", """mapfile -C 'printf -v cgroup_path /untrusted' -c 1 -t values < /dev/null"""),
        ("debug-trap", """trap 'printf -v cgroup_path /untrusted' DEBUG"""),
        ("err-trap", "trap true ERR"),
        ("positional-reset", "set -- /untrusted"),
        ("wait-output", "wait -p cgroup_path"),
        ("getopts-output", "getopts x cgroup_path -x"),
        ("arithmetic-write", "((cgroup_path=0))"),
        ("indirect-write", 'printf -v "$destination" %s /untrusted'),
        ("coprocess", "coproc CHECK { /custom/reader; }"),
        ("comment-heredoc", "# <<'IGNORED'\n" + COMPOSED_PYTHON_READER),
        ("quoted-comment-data", """printf '%s' '# <<EOF'\n""" + COMPOSED_PYTHON_READER),
        ("new-redirect", "/usr/bin/stat -c %u /mnt/supervisor > /unregistered"),
        ("empty-redirect", "/usr/bin/stat -c %u /mnt/supervisor >"),
    )


def adversarial_workflows(workflow: str):
    source = builder(workflow)
    for name, command in adversarial_commands():
        assert "cgroup.procs" not in command, name
        changed = source.replace("cd /\n", "cd /\n" + command.rstrip() + "\n", 1)
        yield name, replace_builder(workflow, changed)
    yield "helper-recomposition", replace_builder(
        workflow, source.replace(
            '/usr/bin/umount --recursive "$1"',
            '/bin/cat "$1/$2$3"', 1,
        ),
    )
    yield "helper-argument-fragments", replace_builder(
        workflow, source.replace(
            "unmount_if_mounted /sys",
            "unmount_if_mounted /mnt/supervisor/cgroup cgroup. procs", 1,
        ),
    )
    yield "mutated-python-mode", replace_builder(
        workflow, source.replace("publisher-programs.py dev-mount-targets", "publisher-programs.py membership", 1),
    )
    yield "mutated-python-program", replace_builder(
        workflow, source.replace("/mnt/control/publisher-programs.py", "/candidate/publisher-programs.py"),
    )


@contextmanager
def refreshed_boundary_identities(workflow: str):
    """Prove the semantic guard, not an earlier raw-identity mismatch, rejects."""
    run = contract.publisher_run_script(workflow)
    with (
        mock.patch.object(contract, "REVIEWED_PATCH_RELEASE_RUN_SHA256", contract.reviewed_patch_release_run_sha256(run)),
        mock.patch.object(contract, "REVIEWED_BUILDER_ISOLATION_SHA256", contract.reviewed_builder_isolation_sha256(builder(workflow))),
    ):
        yield
