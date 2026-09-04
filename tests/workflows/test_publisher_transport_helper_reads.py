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
                "          local -n inspect_ref="
                "checked_supervisor_transport_output\n"
                '          printf "%s\\n" "$inspect_ref" > /dev/null\n',
                1,
            ),
        )
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

        literal_changed = original.replace(
            "          local signature\n",
            "          local signature\n"
            "          inspect_name=checked_supervisor_transport_output\n"
            '          printf "%s\\n" "$inspect_name" > /dev/null\n',
            1,
        )
        builder = builder_isolation_shell_source(literal_changed)
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
                    literal_changed
                )
            )
            self.assertNotIn(
                "raw builder cgroup membership read differs",
                publisher_boundary_errors(literal_changed),
            )
            upstream_verify._parse_workflow_structure_text(literal_changed)


if __name__ == "__main__":
    unittest.main()
