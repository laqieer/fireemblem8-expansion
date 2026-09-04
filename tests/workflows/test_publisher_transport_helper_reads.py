import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from scripts.upstream_port import verify as upstream_verify
from scripts.workflow_pilot import publisher_shell_contract
from tests.workflows.test_patch_release_workflow import (
    builder_isolation_shell_source,
    publisher_boundary_errors,
    workflow_has_raw_builder_cgroup_membership_read,
)


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "build.yml"


class PublisherTransportHelperReadTests(unittest.TestCase):
    def test_active_helper_consumers_reject_only_when_invoked(self):
        cases = (
            (
                "inspect() {\n"
                '  printf "%s\\n" '
                '"${checked_supervisor_transport_output[0]}"\n'
                "}\n"
                "inspect\n"
            ),
            (
                "outer() {\n"
                "  inner() {\n"
                '    printf "%s\\n" '
                '"${checked_runtime_transport_output[0]}"\n'
                "  }\n"
                "  inner\n"
                "}\n"
                "outer\n"
            ),
            (
                "inspect() {\n"
                "  name=checked_supervisor_transport_output\n"
                '  printf "%s\\n" "${!name}"\n'
                "}\n"
                "call=inspect\n"
                '"$call"\n'
            ),
            (
                "inspect() {\n"
                '  result="$(printf "%s" '
                '"${checked_runtime_transport_output[0]}")"\n'
                "}\n"
                "inspect\n"
            ),
            (
                "inspect() {\n"
                '  printf "%s\\n" "${!1}"\n'
                "}\n"
                "inspect checked_runtime_transport_output\n"
            ),
            (
                "inspect() {\n"
                '  local -n ref="$1"\n'
                '  printf "%s\\n" "$ref"\n'
                "}\n"
                "inspect checked_supervisor_transport_output\n"
            ),
            (
                "inspect() {\n"
                '  printf "%s\\n" "${!1}"\n'
                "}\n"
                'inspect "$unknown"\n'
            ),
            (
                "inspect() {\n"
                '  local -n ref="$1"\n'
                '  printf "%s\\n" "$ref"\n'
                "}\n"
                'inspect "$unknown"\n'
            ),
            (
                "inspect() {\n"
                '  printf "%s %s %s\\n" '
                '"${#checked_runtime_transport_output[@]}" '
                '"${!checked_runtime_transport_output[@]}" '
                '"${checked_runtime_transport_output[@]}"\n'
                "}\n"
                "inspect\n"
            ),
            (
                "inspect() {\n"
                '  printf "%s\\n" '
                '"${checked_supervisor_transport_output[0]}"\n'
                "}\n"
                "trap inspect ERR\n"
                "false\n"
            ),
            (
                "inspect() {\n"
                '  printf "%s\\n" '
                '"${checked_runtime_transport_output[0]}"\n'
                "}\n"
                "mapfile -C inspect -c 1 -t ordinary <<< x\n"
            ),
        )
        for script in cases:
            with self.subTest(script=script.splitlines()[0]):
                completed = subprocess.run(
                    ["/bin/bash", "-u", "-c", script],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertTrue(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        script,
                        label="invoked transport helper",
                    )
                )

        initialized_reads = (
            "inspect() {\n"
            '  printf "%s\\n" "${!1}"\n'
            "}\n"
            "inspect checked_runtime_transport_output\n",
            "inspect() {\n"
            '  local -n ref="$1"\n'
            '  printf "%s\\n" "$ref"\n'
            "}\n"
            "inspect checked_runtime_transport_output\n",
        )
        environment = dict(os.environ)
        environment["checked_runtime_transport_output"] = "trusted-value"
        for script in initialized_reads:
            completed = subprocess.run(
                ["/bin/bash", "-u", "-c", script],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, "trusted-value\n")
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    script,
                    label="initialized indirect transport read",
                )
            )

        active_definition = cases[0].rsplit("inspect\n", 1)[0]
        self.assertFalse(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                active_definition,
                label="uninvoked transport helper",
            )
        )
        default_read = (
            "inspect() {\n"
            '  printf "%s\\n" '
            '"${checked_runtime_transport_output[0]:-/missing}"\n'
            "}\n"
            "inspect\n"
        )
        completed = subprocess.run(
            ["/bin/bash", "-u", "-c", default_read],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "/missing\n")
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                default_read,
                label="default transport helper read",
            )
        )
        arithmetic_read = (
            "inspect() {\n"
            "  name=checked_runtime_transport_output\n"
            '  printf "%s\\n" "$((name))"\n'
            "}\n"
            "inspect\n"
        )
        completed = subprocess.run(
            ["/bin/bash", "-u", "-c", arithmetic_read],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                arithmetic_read,
                label="arithmetic transport helper read",
            )
        )

    def test_literal_transport_names_remain_data(self):
        cases = (
            (
                "inspect() {\n"
                '  printf "%s\\n" "$1"\n'
                "}\n"
                "name=checked_supervisor_transport_output\n"
                'inspect "$name"\n',
                "checked_supervisor_transport_output\n",
            ),
            (
                "inner() {\n"
                '  printf "%s\\n" "$1"\n'
                "}\n"
                "outer() {\n"
                '  inner "$1"\n'
                "}\n"
                "name=checked_runtime_transport_output\n"
                'outer "$name"\n',
                "checked_runtime_transport_output\n",
            ),
            (
                "inspect() {\n"
                "  printf '%s\\n' "
                "'${checked_supervisor_transport_output[0]}'\n"
                "}\n"
                "inspect\n",
                "${checked_supervisor_transport_output[0]}\n",
            ),
            (
                "inspect() {\n"
                '  test "$1" = checked_runtime_transport_output\n'
                '  printf "%s\\n" "$1"\n'
                "}\n"
                "name=checked_runtime_transport_output\n"
                'inspect "$name"\n',
                "checked_runtime_transport_output\n",
            ),
        )
        for script, expected in cases:
            with self.subTest(script=script.splitlines()[0]):
                completed = subprocess.run(
                    ["/bin/bash", "-u", "-c", script],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, expected)
                self.assertFalse(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        script,
                        label="literal transport helper data",
                    )
                )
        self.assertFalse(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                "inspect() {\n"
                '  printf "%s\\n" "$unknown"\n'
                "}\n"
                "inspect\n",
                label="dynamic plain helper data",
            )
        )

    def test_prefix_enumeration_and_associative_keys_remain_data(self):
        environment = dict(os.environ)
        environment["checked_supervisor_transport_output"] = "1"
        cases = (
            (
                "prefix=checked_supervisor_transport_output\n"
                'printf "%s %s\\n" "${!prefix*}" "${!prefix@}"\n',
                "prefix prefix\n",
            ),
            (
                'printf "%s\\n" "${!checked_supervisor_transport_output*}"\n',
                "checked_supervisor_transport_output\n",
            ),
            (
                "show() {\n"
                "  local -A table=()\n"
                "  local key=checked_supervisor_transport_output\n"
                '  table["$key"]=value\n'
                '  printf "%s %s\\n" "${table[$key]}" "${!table[@]}"\n'
                "}\n"
                "show\n",
                "value checked_supervisor_transport_output\n",
            ),
        )
        for script, expected in cases:
            with self.subTest(script=script.splitlines()[0]):
                completed = subprocess.run(
                    ["/bin/bash", "-u", "-c", script],
                    cwd=ROOT,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stdout, expected)
                self.assertFalse(
                    publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                        script,
                        label="non-dereferencing Bash array syntax",
                    )
                )

        indexed = (
            "declare -A table=([global]=value)\n"
            "show() {\n"
            "  local -a table=(zero one)\n"
            "  local key=checked_supervisor_transport_output\n"
            '  printf "%s\\n" "${table[$key]}"\n'
            "}\n"
            "show\n"
        )
        completed = subprocess.run(
            ["/bin/bash", "-u", "-c", indexed],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "one\n")
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                indexed,
                label="indexed arithmetic recursive dereference",
            )
        )
        whole_unset = (
            "declare -A table=()\n"
            "unset table\n"
            "table=(zero one)\n"
            "key=checked_supervisor_transport_output\n"
            'printf "%s\\n" "${table[$key]}"\n'
        )
        for indexed_after in (
            whole_unset,
            "show() {\n"
            "  local key=checked_supervisor_transport_output\n"
            '  printf "%s\\n" "${unknown_table[$key]}"\n'
            "}\n"
            "show\n",
        ):
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    indexed_after,
                    label="unknown or reset indexed array",
                )
            )
        completed = subprocess.run(
            ["/bin/bash", "-u", "-c", whole_unset],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "one\n")

    def test_associative_type_persists_until_whole_unset(self):
        helper = (
            "show() {\n"
            "  local -A table=([old]=gone)\n"
            "  local key=checked_supervisor_transport_output\n"
            '  table=([$key]=compound)\n'
            '  printf "%s " "${table[$key]}"\n'
            "  table=scalar\n"
            '  printf "%s " "${table[$key]}"\n'
            "  table+=(tail)\n"
            '  printf "%s " "${table[$key]}"\n'
            '  unset "table[@]"\n'
            '  printf "%s\\n" "${table[$key]}"\n'
            "}\n"
            "show\n"
        )
        redeclare = (
            "declare -A table=([checked_supervisor_transport_output]=value)\n"
            "declare -a table\n"
            "readonly -a table\n"
            "key=checked_supervisor_transport_output\n"
            'printf "%s\\n" "${table[$key]}"\n'
        )
        cases = [
            (
                helper.replace("local -A", f"{declaration} -A", 1),
                "compound compound compound compound\n",
            )
            for declaration in ("local", "declare", "typeset")
        ]
        cases.append((redeclare, "value\n"))
        for script, expected in cases:
            completed = subprocess.run(
                ["/bin/bash", "-u", "-c", script],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, expected)
            self.assertFalse(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    script,
                    label="persistent associative array type",
                )
            )

    def test_readonly_array_state_survives_rejected_mutations(self):
        prefix = (
            "declare -A table="
            "([checked_supervisor_transport_output]=value)\n"
            "readonly table\n"
        )
        suffix = (
            "key=checked_supervisor_transport_output\n"
            'printf "%s\\n" "${table[$key]}"\n'
        )
        operations = (
            ("unset table || true", "0:value\n"),
            ('unset "table[other]" || true', "0:value\n"),
            ('unset "table[@]" || true', "0:value\n"),
            ("table=scalar", "1:value\n"),
            ("table=([other]=replacement)", "1:value\n"),
            ("table[other]=replacement", "1:value\n"),
            ("table+=(tail)", "1:value\n"),
            ("declare -a table", "1:value\n"),
            ("declare +r table", "1:value\n"),
        )
        for operation, expected in operations:
            script = prefix + operation + "\n" + suffix
            self.assertFalse(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    script,
                    label="rejected readonly mutation",
                )
            )
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-u",
                    "-c",
                    prefix
                    + 'eval "$1" 2>/dev/null\n'
                    + 'status="$?"\n'
                    + suffix.replace(
                        'printf "%s\\n"',
                        'printf "%s:%s\\n" "$status"',
                    ),
                    "--",
                    operation,
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, expected)

        local_shadow = (
            prefix
            + "show() {\n"
            + "  local -a table=(zero one)\n"
            + "  local key=checked_supervisor_transport_output\n"
            + '  printf "%s\\n" "${table[$key]}"\n'
            + "}\nshow\n"
        )
        self.assertFalse(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                local_shadow,
                label="readonly global rejects local shadow",
            )
        )
        subshell = (
            "declare -A table=([key]=value)\n"
            "( readonly table )\n"
            "unset table\n"
            "table=(zero one)\n"
            "key=checked_supervisor_transport_output\n"
            'printf "%s\\n" "${table[$key]}"\n'
        )
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                subshell,
                label="subshell readonly does not escape",
            )
        )

    def test_readonly_alias_attributes_do_not_hide_semantic_values(self):
        cases = (
            (
                "name=checked_runtime_transport_output\n"
                "readonly name\n"
                'printf "%s\\n" "${!name}"\n',
                {"checked_runtime_transport_output": "value"},
                "value\n",
            ),
            (
                "name=checked_runtime_transport_output\n"
                "readonly name\n"
                'printf "%s\\n" "${!name:=set}"\n'
                'printf "%s\\n" "$checked_runtime_transport_output"\n',
                {},
                "set\nset\n",
            ),
            (
                "name=checked_runtime_transport_output\n"
                "readonly name\n"
                'printf "%s\\n" "$((name))"\n',
                {"checked_runtime_transport_output": "7"},
                "7\n",
            ),
            (
                "first=checked_runtime_transport_output\n"
                "readonly first\n"
                "second=$first\n"
                "readonly second\n"
                'printf "%s\\n" "${!second:+present}"\n',
                {"checked_runtime_transport_output": "value"},
                "present\n",
            ),
            (
                "name=checked_runtime_transport_output\n"
                "readonly name\n"
                "inspect() {\n"
                '  printf "%s\\n" "${!name}"\n'
                "}\n"
                "inspect\n",
                {"checked_runtime_transport_output": "value"},
                "value\n",
            ),
        )
        for script, additions, expected in cases:
            environment = dict(os.environ)
            environment.update(additions)
            completed = subprocess.run(
                ["/bin/bash", "-u", "-c", script],
                cwd=ROOT,
                env=environment,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, expected)
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    script,
                    label="readonly alias dereference",
                )
            )
        for operator in (":-fallback", ":?missing", "=set", "+present"):
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    "name=checked_runtime_transport_output\n"
                    "readonly name\n"
                    f'printf "%s\\n" "${{!name{operator}}}"\n',
                    label="readonly indirect parameter operator",
                )
            )
        self.assertTrue(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                'raw="$1"\n'
                "readonly raw\n"
                '/bin/cat "$raw/cgroup.procs"\n',
                label="readonly raw path alias",
            )
        )
        self.assertFalse(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                "printf '%s\\n' "
                "'readonly-alias checked_runtime_transport_output'\n",
                label="readonly marker spelling literal",
            )
        )

    def test_arithmetic_writes_cannot_mutate_protected_state(self):
        writes = (
            "(( target = 2 ))",
            "let 'target+=2'",
            'printf "%s" "$((target++))"',
            'printf "%s" "$[target=4]"',
            "for ((target=0; target<1; target++)); do :; done",
            "declare -i other='target=5'",
            "(( other=1, other ? target=6 : other++ ))",
            "(( values[target++]=1 ))",
        )
        for write in writes:
            protected = write.replace("target", "cgroup_path")
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    protected,
                    label="protected arithmetic write",
                )
            )
            completed = subprocess.run(
                ["/bin/bash", "-c", "target=1\n" + write + "\n"],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
        for protected in (
            "(( checked_runtime_transport_output[0] = 1 ))",
            "name=checked_runtime_transport_output\n(( $name = 1 ))",
            "(( ordinary[checked_supervisor_transport_output++] = 1 ))",
            "(( $unknown = 1 ))",
            'raw="$1"\n(( raw = 0 ))',
            "change() {\n"
            '  local -n ref="$1"\n'
            "  (( ref = 0 ))\n"
            "}\n"
            "change cgroup_path\n",
        ):
            self.assertTrue(
                publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                    protected,
                    label="transport arithmetic write",
                )
            )
        self.assertFalse(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                "ordinary=1\n(( ordinary += 2 ))\n",
                label="unrelated arithmetic write",
            )
        )
        self.assertFalse(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                'raw="$1"\nreadonly raw\n(( raw = 0 )) || true\n',
                label="rejected readonly arithmetic write",
            )
        )

    def test_publisher_and_upstream_enforce_helper_dereferences(self):
        original = WORKFLOW.read_text(encoding="utf-8")
        producer = (
            '        read_checked_supervisor_transport_file \\\n'
            '          "$dev_mounts_file" "$dev_mount_targets_max_bytes"\n'
        )
        second_producer = (
            '        read_checked_supervisor_transport_file \\\n'
            '          "$remaining_dev_mounts_file" \\\n'
            '          "$dev_mount_targets_max_bytes"\n'
        )
        helper = (
            "        inspect_transport_output() {\n"
            '          printf "%s\\n" '
            '"${checked_supervisor_transport_output[0]}" > /dev/null\n'
            "        }\n"
            "        inspect_transport_output\n"
        )
        arithmetic_mutations = tuple(
            original.replace(
                "          local signature\n",
                "          local signature\n" + f"          {write}\n",
                1,
            )
            for write in (
                "(( cgroup_path = 0 ))",
                "let 'checked_supervisor_transport_output[0]=0'",
                'printf "%s" "$((supervisor_cgroup++))" > /dev/null',
            )
        )
        mutations = (
            original.replace(producer, helper + producer, 1),
            original.replace(producer, producer + helper, 1),
            original.replace(second_producer, helper + second_producer, 1),
            original.replace(
                "          local signature\n",
                "          local signature\n"
                "          inspect_name=checked_supervisor_transport_output\n"
                '          printf "%s\\n" "${!inspect_name}" > /dev/null\n',
                1,
            ),
            original.replace(
                "          local signature\n",
                "          local signature\n"
                "          local inspect_name="
                "checked_supervisor_transport_output\n"
                "          readonly inspect_name\n"
                '          printf "%s\\n" '
                '"${!inspect_name:=replacement}" > /dev/null\n',
                1,
            ),
            original.replace(
                "          local signature\n",
                "          local signature\n"
                "          local first="
                "checked_supervisor_transport_output\n"
                "          readonly first\n"
                "          local second=$first\n"
                '          printf "%s\\n" "$((second))" > /dev/null\n',
                1,
            ),
            original.replace(
                "          local signature\n",
                "          local signature\n"
                "          local -n inspect_ref="
                "checked_supervisor_transport_output\n"
                '          printf "%s\\n" "$inspect_ref" > /dev/null\n',
                1,
            ),
            original.replace(
                "          local signature\n",
                "          local signature\n"
                "          local -a inspect_table=(zero one)\n"
                "          local inspect_key="
                "checked_supervisor_transport_output\n"
                '          printf "%s\\n" '
                '"${inspect_table[$inspect_key]}" > /dev/null\n',
                1,
            ),
        ) + arithmetic_mutations
        for changed in mutations:
            builder = builder_isolation_shell_source(changed)
            semantic_builder = (
                publisher_shell_contract._strip_patch_release_parser_heredoc_bodies(
                    builder
                )
            )
            records = publisher_shell_contract._annotate_command_control_scopes(
                publisher_shell_contract._normalize_split_function_command_records(
                    publisher_shell_contract.split_bash_command_records(
                        semantic_builder,
                        label="mutated builder",
                    )
                )
            )
            _, inventory = publisher_shell_contract._shell_function_definitions(
                records
            )
            with (
                self.subTest(mutation=changed != original),
                mock.patch.object(
                    publisher_shell_contract,
                    "REVIEWED_BUILDER_HELPER_INVENTORY",
                    dict(inventory),
                ),
                mock.patch.object(
                    publisher_shell_contract,
                    "assert_reviewed_patch_release_run_script_identity",
                ),
                mock.patch.object(
                    publisher_shell_contract,
                    "assert_reviewed_builder_isolation_shell_identity",
                ),
            ):
                self.assertTrue(
                    workflow_has_raw_builder_cgroup_membership_read(changed)
                )
                self.assertIn(
                    "raw builder cgroup membership read differs",
                    publisher_boundary_errors(changed),
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "isolated candidate build differs",
                ):
                    upstream_verify._parse_workflow_structure_text(changed)

        safe_changes = (
            ("literal", original.replace(
                "          local signature\n",
                "          local signature\n"
                "          inspect_name=checked_supervisor_transport_output\n"
                '          printf "%s\\n" "$inspect_name" > /dev/null\n',
                1,
            )),
            ("prefix", original.replace(
                "          local signature\n",
                "          local signature\n"
                "          local inspect_prefix="
                "checked_supervisor_transport_output\n"
                '          printf "%s\\n" '
                '"${!inspect_prefix*}" > /dev/null\n',
                1,
            )),
            ("associative", original.replace(
                "          local signature\n",
                "          local signature\n"
                "          local -A inspect_table="
                "([checked_supervisor_transport_output]=value)\n"
                '          printf "%s\\n" '
                '"${inspect_table[checked_supervisor_transport_output]}" '
                "> /dev/null\n",
                1,
            )),
            ("associative-reassignment", original.replace(
                "          local signature\n",
                "          local signature\n"
                "          local -A inspect_table=()\n"
                "          inspect_table="
                "([checked_supervisor_transport_output]=value)\n"
                '          printf "%s\\n" '
                '"${inspect_table[checked_supervisor_transport_output]}" '
                "> /dev/null\n",
                1,
            )),
            ("readonly-associative", original.replace(
                "          local signature\n",
                "          local signature\n"
                "          local -Ar inspect_table="
                "([checked_supervisor_transport_output]=value)\n"
                "          unset inspect_table || true\n"
                '          printf "%s\\n" '
                '"${inspect_table[checked_supervisor_transport_output]}" '
                "> /dev/null\n",
                1,
            )),
        )
        for safe_label, safe_changed in safe_changes:
            builder = builder_isolation_shell_source(safe_changed)
            semantic_builder = (
                publisher_shell_contract._strip_patch_release_parser_heredoc_bodies(
                    builder
                )
            )
            records = publisher_shell_contract._annotate_command_control_scopes(
                publisher_shell_contract._normalize_split_function_command_records(
                    publisher_shell_contract.split_bash_command_records(
                        semantic_builder,
                        label="literal-name builder",
                    )
                )
            )
            _, inventory = publisher_shell_contract._shell_function_definitions(
                records
            )
            with (
                self.subTest(safe_change=safe_label),
                mock.patch.object(
                    publisher_shell_contract,
                    "REVIEWED_BUILDER_HELPER_INVENTORY",
                    dict(inventory),
                ),
                mock.patch.object(
                    publisher_shell_contract,
                    "assert_reviewed_patch_release_run_script_identity",
                ),
                mock.patch.object(
                    publisher_shell_contract,
                    "assert_reviewed_builder_isolation_shell_identity",
                ),
            ):
                self.assertFalse(
                    workflow_has_raw_builder_cgroup_membership_read(
                        safe_changed
                    )
                )
                self.assertNotIn(
                    "raw builder cgroup membership read differs",
                    publisher_boundary_errors(safe_changed),
                )
                upstream_verify._parse_workflow_structure_text(safe_changed)


if __name__ == "__main__":
    unittest.main()
