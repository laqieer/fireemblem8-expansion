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
        inert = (
            "inspect() {\n"
            "  printf '%s\\n' "
            "'${checked_supervisor_transport_output[0]}'\n"
            "}\n"
            "inspect\n"
        )
        self.assertFalse(
            publisher_shell_contract.has_forbidden_raw_builder_cgroup_membership_read(
                inert,
                label="literal transport helper data",
            )
        )

    def test_publisher_and_upstream_reject_reviewed_helper_reads(self):
        original = WORKFLOW.read_text(encoding="utf-8")
        marker = "          local signature\n"
        mutations = (
            '          printf "%s\\n" '
            '"${checked_supervisor_transport_output[0]}" > /dev/null\n',
            "          inspect_name=checked_supervisor_transport_output\n"
            '          printf "%s\\n" "${!inspect_name}" > /dev/null\n',
        )
        for insertion in mutations:
            changed = original.replace(marker, marker + insertion, 1)
            builder = builder_isolation_shell_source(changed)
            records = publisher_shell_contract.split_bash_command_records(
                builder,
                label="mutated builder",
            )
            _, inventory = publisher_shell_contract._shell_function_definitions(
                records
            )
            with (
                self.subTest(insertion=insertion),
                mock.patch.object(
                    publisher_shell_contract,
                    "REVIEWED_BUILDER_HELPER_INVENTORY",
                    set(inventory.elements()),
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


if __name__ == "__main__":
    unittest.main()
