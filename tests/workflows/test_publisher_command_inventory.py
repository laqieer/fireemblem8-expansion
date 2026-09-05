"""TC-WORKFLOW-PUBLISHER-COMMAND-INVENTORY-001, real production consumers."""

from dataclasses import replace
import builtins
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import py_compile
import shlex
import shutil
import stat
import subprocess
import sys
from types import ModuleType
import unittest
from unittest import mock
import uuid

from scripts.upstream_port import verify
from scripts.workflow_pilot import publisher_inventory as authority
from scripts.workflow_pilot import publisher_programs as programs
from scripts.workflow_pilot import publisher_shell as shell
from scripts.workflow_pilot import publisher_shell_contract as contract
from tests.workflows import publisher_inventory_fixtures as fixtures
from tests.workflows import test_patch_release_workflow as publisher


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/build.yml"


class PublisherCommandInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text()
        cls.source = fixtures.builder(cls.workflow)
        cls.inventory = authority.reviewed_inventory()
        cls.sources = {
            "entry": cls.source,
            "producer": contract.publisher_run_script(cls.workflow, "Verify exact candidate and stage trusted producer"),
            "staging": contract.publisher_run_script(cls.workflow),
        }

    def test_actual_production_inventory_is_complete_and_typed(self):
        result = authority.validate_workflow(self.workflow)
        self.assertTrue(result.commands)
        self.assertTrue(result.events)
        self.assertEqual(
            {s.family for s in self.inventory.signatures}, set(authority.Family),
        )
        actual = {item.signature.name for item in result.commands}
        expected = {
            signature.name for signature in self.inventory.signatures
            if signature.occurrences and self.inventory.entry_scope(signature.scope) == "entry"
        }
        self.assertEqual(actual, expected)
        self.assertEqual(
            {event.kind for event in result.events} & {
                authority.EventKind.CANDIDATE_LAUNCH,
                authority.EventKind.LEGACY_MEMBERSHIP,
                authority.EventKind.EXPORT_OPEN,
                authority.EventKind.EXPORT_FILE,
                authority.EventKind.EXPORT_CLOSE,
            },
            {
                authority.EventKind.CANDIDATE_LAUNCH,
                authority.EventKind.LEGACY_MEMBERSHIP,
                authority.EventKind.EXPORT_OPEN,
                authority.EventKind.EXPORT_FILE,
                authority.EventKind.EXPORT_CLOSE,
            },
        )
        self.assertNotIn(authority.EventKind.MEMBERSHIP_VERIFIED, {event.kind for event in result.events})
        self.assertTrue(any(len(event.call_stack) > 2 for event in result.events))
        self.assertTrue(any(context.kind == "substitution" for event in result.events for context in event.context))
        self.assertTrue(any(context.kind == "loop" for event in result.events for context in event.context))
        for signature in self.inventory.signatures:
            with self.subTest(signature=signature.name):
                for placement in signature.placements:
                    self.assertEqual(
                        self.inventory.authorize(signature.form, signature.scope, placement.context),
                        signature,
                    )
                self.assertTrue(all(isinstance(a.resource, authority.Resource) and isinstance(a.access, authority.Access) for a in signature.accesses))
                self.assertEqual(signature.evidence, authority.CASE_ID)
                if signature.program is not None:
                    self.assertIn(signature.program.source_path, authority.authority_paths())

    def test_both_production_consumers_reject_the_complete_adversarial_corpus(self):
        self.assertEqual(publisher.publisher_boundary_errors(self.workflow), [])
        verify._parse_workflow_structure_text(self.workflow)
        for name, changed in fixtures.adversarial_workflows(self.workflow):
            with self.subTest(case=name), fixtures.refreshed_boundary_identities(changed):
                self.assertTrue(publisher.publisher_boundary_errors(changed))
                with self.assertRaises(ValueError):
                    verify._parse_workflow_structure_text(changed)

    def test_composed_reader_regression_fails_if_shared_authority_is_removed(self):
        command = fixtures.COMPOSED_PYTHON_READER
        self.assertNotIn("cgroup.procs", command)
        changed = fixtures.replace_builder(
            self.workflow, self.source.replace("cd /\n", "cd /\n" + command + "\n", 1),
        )
        with fixtures.refreshed_boundary_identities(changed):
            self.assertTrue(publisher.publisher_boundary_errors(changed))
            with self.assertRaises(ValueError):
                verify._parse_workflow_structure_text(changed)
            with mock.patch.object(contract, "validate_builder_command_inventory"):
                self.assertEqual(publisher.publisher_boundary_errors(changed), [])
                verify._parse_workflow_structure_text(changed)

    def test_all_command_families_deny_argument_environment_redirect_and_executable_mutations(self):
        for signature in self.inventory.signatures:
            form = signature.form
            mutations = {
                "argument": replace(form, argv=form.argv + (shell.command("unregistered").argv[0],)),
                "environment": replace(form, environment=form.environment + shell.command("UNREGISTERED=yes").environment),
                "redirect": replace(form, redirects=form.redirects + shell.command("true > /unregistered").redirects),
            }
            if form.argv:
                mutations["executable"] = replace(
                    form, argv=(shell.command("/unregistered/executable").argv[0],) + form.argv[1:],
                )
                mutations["deletion"] = replace(form, argv=form.argv[:-1])
            for name, changed in mutations.items():
                with self.subTest(signature=signature.name, mutation=name):
                    with self.assertRaises(ValueError):
                        self.inventory.authorize(changed, signature.scope, signature.placements[0].context)

    def test_each_registry_deletion_and_form_drift_fails_closed(self):
        for index, signature in enumerate(self.inventory.signatures):
            without = replace(self.inventory, signatures=self.inventory.signatures[:index] + self.inventory.signatures[index + 1:])
            root = self.inventory.entry_scope(signature.scope)
            with self.subTest(signature=signature.name, mutation="delete"):
                if signature.occurrences == 0:
                    with self.assertRaises(ValueError):
                        without.authorize(signature.form, signature.scope, signature.placements[0].context)
                else:
                    with self.assertRaises(ValueError):
                        without.validate(self.sources[root], entry_scope=root)
            changed = replace(signature, occurrences=signature.occurrences + 1)
            mutated = replace(self.inventory, signatures=self.inventory.signatures[:index] + (changed,) + self.inventory.signatures[index + 1:])
            with self.subTest(signature=signature.name, mutation="cardinality"):
                with self.assertRaises(ValueError):
                    mutated.validate(self.sources[root], entry_scope=root)
        with self.assertRaises(ValueError):
            replace(self.inventory, signatures=self.inventory.signatures + (self.inventory.signatures[0],))

    def test_inventory_rejects_untyped_or_incomplete_signature_metadata(self):
        original = next(s for s in self.inventory.signatures if s.program and s.program.name == "membership")
        for changed in (
            replace(original, family="python"),
            replace(original, events=("membership-verified",)),
            replace(original, accesses=("cgroup",)),
            replace(original, form=replace(original.form, argv=original.form.argv[:4])),
        ):
            with self.subTest(signature=changed), self.assertRaises(ValueError):
                replace(self.inventory, signatures=tuple(
                    changed if signature == original else signature for signature in self.inventory.signatures
                ))
        with self.assertRaises(ValueError):
            self.inventory.validate("", entry_scope="unregistered")
        for control in (
            replace(self.inventory.controls[0], context=("untyped",)),
            replace(self.inventory.controls[0], occurrences=True),
        ):
            with self.assertRaises(ValueError):
                replace(self.inventory, controls=(control,) + self.inventory.controls[1:])

    def test_program_signatures_and_events_include_every_declared_access(self):
        for signature in self.inventory.signatures:
            if signature.program is None:
                continue
            for required in signature.program.inputs + signature.program.outputs:
                with self.subTest(signature=signature.name, access=required):
                    self.assertIn(required, signature.accesses)
                    changed = replace(
                        signature,
                        accesses=tuple(access for access in signature.accesses if access != required),
                    )
                    with self.assertRaises(ValueError):
                        replace(self.inventory, signatures=tuple(
                            changed if row == signature else row for row in self.inventory.signatures
                        ))
        launch = next(
            event for event in self.inventory.validate(self.source).events
            if event.kind == authority.EventKind.CANDIDATE_LAUNCH
        )
        self.assertIn(
            authority.ResourceAccess(authority.Resource.CONTROL, authority.Access.READ),
            launch.accesses,
        )

    def test_each_production_family_rejects_addition_deletion_and_comment_spoof(self):
        analysis = self.inventory.validate(self.source)
        selected = {}
        for item in analysis.commands:
            if item.scope == "builder_main" and not item.context and not item.nested:
                selected.setdefault(item.signature.family, item.command)
        self.assertEqual(set(selected), set(authority.Family))
        for family, command in selected.items():
            text = self.source[command.offset:command.end]
            self.assertEqual(shell.command(text), command)
            commented = "\n".join("# " + line for line in text.splitlines())
            mutations = {
                "delete": "",
                "comment-spoof": commented,
                "add": text + "; " + text,
            }
            for kind, replacement in mutations.items():
                with self.subTest(family=family, mutation=kind):
                    changed = self.source[:command.offset] + replacement + self.source[command.end:]
                    syntax = subprocess.run(
                        ["/bin/bash", "-n"], input=changed, text=True,
                        capture_output=True, check=False,
                    )
                    self.assertEqual(syntax.returncode, 0, syntax.stderr)
                    with self.assertRaises(ValueError):
                        self.inventory.validate(changed)

    def test_controls_and_helper_declarations_have_complete_inventory(self):
        for index, control in enumerate(self.inventory.controls):
            with self.subTest(control=control.name):
                changed = replace(self.inventory, controls=self.inventory.controls[:index] + self.inventory.controls[index + 1:])
                with self.assertRaises(ValueError):
                    root = self.inventory.entry_scope(control.scope)
                    changed.validate(self.sources[root], entry_scope=root)
        for index, scope in enumerate(self.inventory.scopes):
            with self.subTest(scope=scope.name):
                with self.assertRaises(ValueError):
                    changed = replace(self.inventory, scopes=self.inventory.scopes[:index] + self.inventory.scopes[index + 1:])
                    root = self.inventory.entry_scope(scope.name)
                    changed.validate(self.sources[root], entry_scope=root)
        old = "unmount_if_mounted /home/runner\n"
        changed = self.source.replace(old, "", 1).replace("unmount_if_mounted() {", old + "unmount_if_mounted() {", 1)
        with self.assertRaisesRegex(ValueError, "before its definition"):
            self.inventory.validate(changed)
        changed = self.source.replace("unmount_if_mounted() {", "if true; then\nunmount_if_mounted() {", 1)
        changed = changed.replace("unmount_if_mounted /home/runner", "fi\nunmount_if_mounted /home/runner", 1)
        with self.assertRaises(ValueError):
            self.inventory.validate(changed)

    def test_parser_preserves_expansion_and_normalizes_safe_spelling(self):
        original = self.inventory.validate(self.source)
        changed = self.source.replace('cgroup_path="$1"', 'cgroup_path="${1}"').replace("cd /", """'cd' '/' # harmless <<'NOT_A_HEREDOC'""", 1)
        changed = changed.replace(
            'builder_uid="$2"\nbuilder_gid="$3"',
            'builder_gid="${3}"\n# safe independent initialization reorder\nbuilder_uid="${2}"',
        )
        result = self.inventory.validate(changed)
        self.assertEqual(result.signatures, original.signatures)
        self.assertNotEqual(shell.command("test '$x' = x"), shell.command('test "$x" = x'))
        self.assertEqual(shell.command('test "$x" = x'), shell.command('test "${x}" = "x"'))
        self.assertNotEqual(shell.command("test '*' = x"), shell.command('test * = x'))
        self.assertEqual(shell.command("exec 2>&1"), shell.command("exec 2>& 1"))
        self.assertNotEqual(shell.command("exec 2>&1"), shell.command("exec 2>1"))
        for command in (
            'cgroup_path="/untrusted"', 'cgroup_path=\'$1\'',
            'test "$cgroup_members" = \'$$\'',
        ):
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    self.inventory.authorize(shell.command(command), "builder_main")

    def test_regex_and_conditional_keyword_provenance_matches_bash_behavior(self):
        original = '[[ "$PATCH_COMMIT" =~ ^[0-9a-f]{40}$ ]]'
        changed = '[[ "$PATCH_COMMIT" =~ "^"[0-9a-f]{40}$ ]]'
        for source, status in ((original, 0), (changed, 1)):
            completed = subprocess.run(
                ["/bin/bash", "--noprofile", "--norc", "-c", source],
                env={"PATH": "/usr/bin:/bin", "PATCH_COMMIT": "a" * 40},
                capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, status, completed.stderr)
        self.assertNotEqual(shell.command(original), shell.command(changed))
        self.assertNotEqual(shell.command('[[ "$state" = T* ]]'), shell.command('\'[[\' "$state" = T* ]]'))
        self.assertEqual(shell.command('[[ "$state" =~ ^a+$ ]]'), shell.command('[[ "$state" =~ ^"a"+$ ]]'))
        self.inventory.authorize(shell.command(original), "producer")
        with self.assertRaises(ValueError):
            self.inventory.authorize(shell.command(changed), "producer")

    def test_even_registered_recursive_helper_graphs_are_rejected(self):
        access = (authority.ResourceAccess(authority.Resource.CONTROL, authority.Access.EXECUTE),)
        signatures = tuple(
            authority.Signature(
                name, scope, shell.command("helper"), authority.Family.HELPER,
                1, access, (authority.EventKind.HELPER_CALL,),
            )
            for name, scope in (("entry.call", "entry"), ("helper.call", "helper"))
        )
        inventory = authority.Inventory(
            signatures, (authority.Scope("helper", "entry", ()),), (),
        )
        with self.assertRaisesRegex(ValueError, "recursive"):
            inventory.validate("helper() { helper; }\nhelper\n")

    def test_normalized_wrappers_never_erase_execution_or_option_identity(self):
        for source, kind in (
            ("builtin test -f /mnt/handoff/target.gba", authority.WrapperKind.BUILTIN),
            ("command -- /usr/bin/stat -c %u /mnt/supervisor", authority.WrapperKind.COMMAND),
            ("/usr/bin/env -i LC_ALL=C /usr/bin/stat -c %u /mnt/supervisor", authority.WrapperKind.ENVIRONMENT),
            ("time -p /usr/bin/stat -c %u /mnt/supervisor", authority.WrapperKind.TIME),
        ):
            with self.subTest(source=source):
                command = shell.command(source)
                self.assertEqual(authority.normalize_invocation(command).wrappers[0].kind, kind)
                with self.assertRaises(ValueError):
                    self.inventory.authorize(command, "builder_main")

    def test_checker_is_one_exact_available_signature_not_an_extra_production_reader(self):
        signature = next(s for s in self.inventory.signatures if s.program and s.program.name == "membership")
        self.assertEqual(signature.occurrences, 0)
        self.assertEqual(signature.events, (authority.EventKind.MEMBERSHIP_VERIFIED,))
        self.assertEqual(signature.program.outputs, ())
        self.assertEqual(signature.program.runtime_path, authority.PROGRAM_RUNTIME_PATH)
        for command in (
            '/usr/bin/python3 -I -S /mnt/control/publisher-programs.py membership "$$"',
        ):
            self.assertEqual(self.inventory.authorize(shell.command(command), "builder_main"), signature)
            with self.assertRaisesRegex(ValueError, "multiplicity"):
                self.inventory.validate(self.source.replace("cd /\n", "cd /\n" + command + "\n", 1))
        for source in (
            '/usr/bin/python -I -S /mnt/control/publisher-programs.py membership "$$"',
            '/usr/bin/python3 -S /mnt/control/publisher-programs.py membership "$$"',
            '/usr/bin/python3 -I -S /candidate/publisher-programs.py membership "$$"',
            '/usr/bin/python3 -I -S /mnt/control/publisher-programs.py membership "$1"',
            '/usr/bin/python3 -I -S /mnt/control/publisher-programs.py membership "$$" /another',
            '/usr/bin/python3 -I -S /mnt/control/publisher-programs.py membership "$$" > /dev/null',
            '/usr/bin/python3 -I -S /mnt/control/publisher-programs.py membership "$$" 2>&1',
            '/usr/bin/python3 -I -S /mnt/control/publisher-programs.py membership "$$" < /unregistered',
        ):
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.inventory.authorize(shell.command(source), "builder_main")
        for replacement in (replace(signature, program=None), replace(signature, accesses=())):
            with self.assertRaises(ValueError):
                replace(self.inventory, signatures=tuple(replacement if s == signature else s for s in self.inventory.signatures))

    def test_missing_or_conditional_preflight_rejects_both_consumers(self):
        command = (
            "        /usr/bin/python3 -I -S scripts/workflow_pilot/publisher_inventory.py \\\n"
            '          --repository-root . --commit "$PATCH_COMMIT"\n'
        )
        self.assertIn(command, self.workflow)
        for changed in (
            self.workflow.replace(command, "", 1),
            self.workflow.replace(command, "        if false; then\n" + command + "        fi\n", 1),
            self.workflow.replace(command, command.replace("-I -S", "-S"), 1),
        ):
            with self.subTest(workflow=changed[:30]):
                self.assertTrue(publisher.publisher_boundary_errors(changed))
                with self.assertRaises(ValueError):
                    verify._parse_workflow_structure_text(changed)

    def test_staging_prologue_is_typed_in_both_production_consumers(self):
        run = contract.publisher_run_script(self.workflow)
        lines = contract.bash_logical_lines(run, label="program staging")
        environment, stage = lines[:2]
        original = environment + "\n" + stage
        for name, changed in (
            ("missing-sanitization", stage),
            ("missing-git-variable", original.replace("GIT_DIR ", "", 1)),
            ("reordered", stage + "\n" + environment),
            ("conditional", environment + "\nif false; then\n" + stage + "\nfi"),
            ("source", original.replace("publisher_programs.py", "publisher_shell.py")),
            ("commit", original.replace("$PATCH_COMMIT:", "HEAD:")),
            ("output", original.replace("$PATCH_RUNTIME_ROOT/", "$BUILDER_ROOT/")),
            ("command", original.replace("/usr/bin/git show", "/usr/bin/git cat-file -p")),
            ("extra-argument", original + " --"),
            ("redirect", original + " 2>/dev/null"),
            ("missing-stage", environment),
        ):
            with self.subTest(mutation=name):
                changed_run = changed + "\n" + "\n".join(lines[2:]) + "\n"
                changed_workflow = self.workflow.replace(
                    "".join("        " + line if line.strip() else line for line in run.splitlines(keepends=True)),
                    "".join("        " + line if line.strip() else line for line in changed_run.splitlines(keepends=True)),
                    1,
                )
                self.assertNotEqual(changed_workflow, self.workflow)
                with fixtures.refreshed_boundary_identities(changed_workflow):
                    self.assertTrue(publisher.publisher_boundary_errors(changed_workflow))
                    with self.assertRaises(ValueError):
                        verify._parse_workflow_structure_text(changed_workflow)

    def test_context_and_complete_producer_mutations_reject_both_consumers(self):
        for name, changed in fixtures.context_and_producer_workflows(self.workflow):
            with self.subTest(mutation=name), fixtures.refreshed_boundary_identities(changed):
                with self.assertRaises(ValueError):
                    authority.validate_workflow(changed)
                self.assertTrue(publisher.publisher_boundary_errors(changed))
                with self.assertRaises(ValueError):
                    verify._parse_workflow_structure_text(changed)

    def test_registered_placements_are_required_before_counting_commands(self):
        for root, source in self.sources.items():
            original = self.inventory.validate(source, entry_scope=root)
            for item in original.commands:
                if item.nested:
                    continue
                with self.subTest(root=root, signature=item.signature.name):
                    with self.assertRaises(ValueError):
                        self.inventory.authorize(
                            item.command, item.scope,
                            item.context + (authority.Context("background", "&"),),
                        )
        changed = fixtures.replace_builder(
            self.workflow, self.source.replace("cd /\n", "cd / &\n", 1),
        )
        signature = next(s for s in self.inventory.signatures if s.name == "builder_main.root-directory")
        enabled = replace(signature, placements=(
            authority.Placement((authority.Context("background", "&"),)),
        ))
        mutation = replace(self.inventory, signatures=tuple(
            enabled if s == signature else s for s in self.inventory.signatures
        ))
        with fixtures.refreshed_boundary_identities(changed):
            self.assertTrue(publisher.publisher_boundary_errors(changed))
            with mock.patch.object(authority, "reviewed_inventory", return_value=mutation):
                self.assertEqual(publisher.publisher_boundary_errors(changed), [])
                verify._parse_workflow_structure_text(changed)

    def test_complete_steps_preserve_semantics_preserving_spelling_and_order(self):
        changed = self.workflow.replace(
            '        ACTUAL_SHA="$(/usr/bin/git rev-parse HEAD)"',
            '        # inert producer comment\n        ACTUAL_SHA="$(/usr/bin/git rev-parse HEAD)"',
        ).replace(
            '        builder_uid=""\n        builder_gid=""',
            '        builder_gid=\'\'\n        builder_uid=\'\'',
        )
        with fixtures.refreshed_boundary_identities(changed):
            self.assertEqual(publisher.publisher_boundary_errors(changed), [])
            verify._parse_workflow_structure_text(changed)
            self.assertEqual(
                authority.validate_workflow(changed).signatures,
                authority.validate_workflow(self.workflow).signatures,
            )

    def test_step_discovery_diagnostics_name_the_requested_step_and_count(self):
        name = "Verify exact candidate and stage trusted producer"
        for changed, count in (
            (self.workflow.replace("- name: " + name, "- name: Renamed producer", 1), 0),
            (self.workflow.replace(
                "    - name: Install trusted isolated-build dependencies",
                "    - name: " + name + "\n      run: |\n        true\n\n"
                "    - name: Install trusted isolated-build dependencies", 1,
            ), 2),
        ):
            with self.subTest(count=count), self.assertRaises(ValueError) as raised:
                contract.publisher_run_script(changed, name)
            self.assertIn(repr(name), str(raised.exception))
            self.assertIn(f"found {count}", str(raised.exception))

    def test_parser_bounds_and_unknown_grammar_are_fail_closed(self):
        for source in (
            "x" * (128 * 1024 + 1), "echo \0", "echo \r",
            "echo " + "$(" * 60 + "true" + ")" * 60,
            "cat <<'EOF'\nignored\nEOF\n",
            "echo > ;", "echo >&", "echo <& | true",
            "echo $'\\x41'", 'echo "${x:-${y}}"', "echo >(true)",
            "echo 'unfinished", "echo \\", "(echo true)",
            "function helper { true; }",
        ):
            with self.subTest(source=source[:50]), self.assertRaises(ValueError):
                parsed = shell.parse(source)
                if parsed:
                    self.inventory.validate(source)

    def test_tester_case_is_indexed_and_maps_to_executable_automation(self):
        catalog = json.loads((ROOT / "docs/test-cases/registry.json").read_text())
        cases = [case for case in catalog["cases"] if case["id"] == authority.CASE_ID]
        self.assertEqual(len(cases), 1)
        case = cases[0]
        feature = next(item for item in catalog["features"] if item["id"] == case["feature_id"])
        self.assertIn(authority.CASE_ID, feature["required_cases"])
        self.assertIn("https://github.com/laqieer/fireemblem8-expansion/issues/200", case["issue_urls"])
        self.assertTrue((ROOT / case["document"]).is_file())
        selectors = set()
        for mapping in case["automation"]:
            argv = shlex.split(mapping["command"])
            self.assertEqual(argv[:3], ["python3", "-m", "unittest"])
            self.assertTrue((ROOT / mapping["evidence"]).is_file())
            for selector in (item for item in argv[3:] if not item.startswith("-")):
                loader = unittest.TestLoader()
                self.assertGreater(loader.loadTestsFromName(selector).countTestCases(), 0)
                self.assertEqual(loader.errors, [])
                selectors.add(selector)
        self.assertIn("tests.workflows.test_publisher_command_inventory", selectors)
        self.assertIn(
            "tests.upstream_port.test_verify.VerifyGatesMirrorWorkflowTests.test_publisher_command_inventory_uses_shared_closed_authority",
            selectors,
        )


class PublisherProgramTests(unittest.TestCase):
    def test_fixed_membership_function_runs_complete_snapshot_matrix(self):
        for data in (b"41\n42\n", b"42\n41\n"):
            programs.validate_membership_snapshot(data, 41, 42)
            handle = mock.MagicMock()
            handle.__enter__.return_value = handle
            handle.read.return_value = data
            with mock.patch("builtins.open", return_value=handle) as opened, mock.patch.object(programs.os, "getpid", return_value=42):
                output = io.StringIO()
                with mock.patch("sys.stdout", output):
                    self.assertEqual(programs.main(["membership", "41"]), 0)
                self.assertEqual(output.getvalue(), "")
                opened.assert_called_once_with("/mnt/supervisor/cgroup/cgroup.procs", "rb")
                handle.read.assert_called_once_with(1025)
        for data in (
            b"", b"\n", b"41\n", b"41\n41\n", b"42\n42\n",
            b"41\n43\n", b"41\n42\n43\n", b"41\n42", b"041\n42\n",
            b"+41\n42\n", b"-41\n42\n", b"0\n42\n", b"41 \n42\n",
            b"41\n 42\n", b"41\n42\n\n", b"\xff\n42\n",
            b"1" * 1025 + b"\n42\n",
        ):
            with self.subTest(data=data[:24]), self.assertRaises(programs.ProgramError):
                programs.validate_membership_snapshot(data, 41, 42)
        for wrapper, checker in ((0, 42), (41, 0), (41, 41), (True, 42), ("41", 42)):
            with self.subTest(wrapper=wrapper, checker=checker), self.assertRaises(programs.ProgramError):
                programs.validate_membership_snapshot(b"41\n42\n", wrapper, checker)

    def test_canonical_mount_programs_execute_only_the_reviewed_findmnt_signatures(self):
        for function, stdout, argv in (
            (
                programs.dev_mount_targets,
                b'{"filesystems":[{"target":"/dev"}]}',
                ["/usr/bin/findmnt", "--json", "--submounts", "--output", "TARGET", "/dev"],
            ),
            (
                programs.writable_mount_records,
                b'{"filesystems":[{"target":"/","options":"ro"}]}',
                ["/usr/bin/findmnt", "--json", "--list", "--uniq", "--output", "TARGET,OPTIONS", "-R", "/"],
            ),
        ):
            with self.subTest(program=function.__name__):
                completed = subprocess.CompletedProcess(argv, 0, stdout, b"")
                with mock.patch.object(programs.subprocess, "run", return_value=completed) as run:
                    self.assertTrue(function().endswith(b"\0"))
                    run.assert_called_once_with(argv, check=False, capture_output=True)
        with mock.patch("builtins.open", return_value=io.BytesIO(b"41\n42\n")), mock.patch.object(programs.os, "getpid", return_value=42):
            with self.assertRaises(programs.ProgramError):
                programs.membership("43")

    def test_fixed_program_rejects_wrong_modes_arguments_and_path_parameters(self):
        for arguments in (
            [], ["--help"], ["membership"], ["membership", "41", "/alternate"],
            ["membership", "0"], ["membership", "041"], ["membership", "+41"],
            ["membership", "41 "], ["membership", "４１"],
            ["dev-mount-targets", "/alternate"], ["writable-mount-records", "-c", "pass"],
        ):
            with self.subTest(arguments=arguments), mock.patch("sys.stderr", io.StringIO()), mock.patch("builtins.open") as opened:
                self.assertEqual(programs.main(arguments), 125)
                opened.assert_not_called()

    def test_real_isolated_canonical_program_emits_decoded_mount_records(self):
        completed = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", str(ROOT / authority.PROGRAM_PATH), "dev-mount-targets"],
            env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "/unregistered", "PYTHONSTARTUP": "/unregistered"},
            capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, b"")
        self.assertTrue(completed.stdout.endswith(b"\0"))
        records = completed.stdout.split(b"\0")[:-1]
        self.assertEqual(records[0], b"/dev")
        self.assertEqual(len(records), len(set(records)))
        self.assertTrue(all(record == b"/dev" or record.startswith(b"/dev/") for record in records))


class PublisherExactTreeTests(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "build/test-artifacts" / ("publisher-inventory-" + uuid.uuid4().hex)
        self.directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.directory)
        self.paths = authority.authority_paths()
        for path in self.paths:
            target = self.directory / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / path, target)
        self.git("init", "-q")
        self.git("add", "--", *self.paths)
        self.git("-c", "user.name=Publisher test", "-c", "user.email=publisher-test@example.invalid", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "Exact authority fixture")
        self.commit = self.git("rev-parse", "HEAD").decode().strip()

    def git(self, *arguments):
        return subprocess.run(
            ["/usr/bin/git", "-C", str(self.directory), *arguments],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        ).stdout

    def cli(self, *flags):
        return subprocess.run(
            [
                "/usr/bin/python3", "-I", "-S", *flags,
                str(self.directory / "scripts/workflow_pilot/publisher_inventory.py"),
                "--repository-root", str(self.directory), "--commit", self.commit,
            ],
            cwd=self.directory, capture_output=True, check=False,
        )

    def snapshot(self):
        self.git("add", "--", *self.paths)
        self.git("-c", "user.name=Publisher test", "-c", "user.email=publisher-test@example.invalid", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "Authority regression fixture")
        self.commit = self.git("rev-parse", "HEAD").decode().strip()

    def test_real_cli_rejects_context_and_complete_producer_mutations(self):
        path = self.directory / authority.WORKFLOW_PATH
        original = path.read_text()
        for name, changed in fixtures.context_and_producer_workflows(original):
            with self.subTest(mutation=name):
                path.write_text(changed)
                self.snapshot()
                completed = self.cli()
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertIn(b"publisher command authority:", completed.stderr)
                self.assertNotIn(b"authority differs from exact tree", completed.stderr)
                self.assertNotIn(b"raw identity", completed.stderr)

    def test_real_cli_enforces_dynamic_import_set_not_call_spelling(self):
        package = self.directory / "scripts/workflow_pilot/__init__.py"
        original = package.read_text()
        ambient = self.directory / "unregistered_package.py"
        ambient.write_text("raise RuntimeError('ambient package executed')\n")
        for call in (
            "builtins.__import__('unregistered_package')",
            "getattr(builtins, '__import__')('unregistered_package')",
            "getattr(importlib, 'import_module')('unregistered_package')",
            "load('unregistered_package')",
        ):
            with self.subTest(call=call):
                package.write_text(
                    original + "\nimport builtins, importlib, sys\n"
                    "from builtins import __import__ as load\n"
                    f"sys.path.insert(0, {str(self.directory)!r})\n" + call + "\n"
                )
                self.snapshot()
                completed = self.cli()
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertIn(b"import outside publisher authority: unregistered_package", completed.stderr)
                self.assertNotIn(b"ambient package executed", completed.stderr)
                self.assertNotIn(b"dynamic publisher authority import", completed.stderr)
        control = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import scripts.workflow_pilot",
             str(self.directory)], capture_output=True, check=False,
        )
        self.assertNotEqual(control.returncode, 0)
        self.assertIn(b"ambient package executed", control.stderr)

    def test_import_boundary_rejects_cached_external_and_stdlib_shadow_modules(self):
        sources = authority._bind_exact_sources(self.directory, self.commit)
        original_import = builtins.__import__
        original_import_module = importlib.import_module
        for name in ("unregistered_package", "fractions"):
            fake = ModuleType(name)
            fake.__spec__ = importlib.util.spec_from_file_location(name, self.directory / (name + ".py"))
            with self.subTest(module=name), mock.patch.dict(sys.modules, {name: fake}):
                importers = (
                    ("builtin", lambda: builtins.__import__(name)),
                    ("import-module", lambda: importlib.import_module(name)),
                    ("importlib-compat", lambda: importlib.__import__(name)),
                    ("prebound-builtin", lambda: original_import(name)),
                )
                for label, importer in importers:
                    with self.subTest(importer=label):
                        with authority._source_only_authority(sources):
                            if name == "unregistered_package":
                                with self.assertRaises(authority.InventoryError):
                                    importer()
                            else:
                                actual = importer()
                                self.assertIsNot(actual, fake)
                                self.assertEqual(actual.Fraction(1, 2) + actual.Fraction(1, 3),
                                                 actual.Fraction(5, 6))
                        self.assertIs(sys.modules[name], fake)
        self.assertIs(builtins.__import__, original_import)
        self.assertIs(importlib.import_module, original_import_module)

    def test_prebound_from_import_cannot_reuse_a_quarantined_package_attribute(self):
        sources = authority._bind_exact_sources(self.directory, self.commit)
        package = importlib.import_module("email")
        original_import = builtins.__import__
        for name, cached in (("email.parser", True), ("email.parser", False),
                             ("ambient_parser", True)):
            fake = ModuleType(name)
            fake.__spec__ = importlib.util.spec_from_file_location(
                name, self.directory / "ambient-email-parser.py",
            )
            with mock.patch.dict(sys.modules), \
                 mock.patch.object(package, "parser", fake, create=True):
                for importer in (original_import, importlib.__import__):
                    for fail in (False, True):
                        with self.subTest(name=name, cached=cached,
                                          importer=importer.__module__, failure=fail):
                            sys.modules.pop("email.parser", None)
                            if cached:
                                sys.modules[name] = fake
                            else:
                                sys.modules.pop(name, None)

                            def exercise():
                                with authority._source_only_authority(sources):
                                    actual = importer("email", fromlist=("parser",)).parser
                                    self.assertIsNot(actual, fake)
                                    message = actual.Parser().parsestr("Subject: captured\n\n")
                                    self.assertEqual(message["Subject"], "captured")
                                    if fail:
                                        raise authority.InventoryError("fixture failure")

                            if fail:
                                with self.assertRaisesRegex(authority.InventoryError, "fixture failure"):
                                    exercise()
                            else:
                                exercise()
                            if cached:
                                self.assertIs(sys.modules[name], fake)
                            else:
                                self.assertIsNot(sys.modules.get(name), fake)
                            self.assertIs(package.parser, fake)

    def test_exact_tree_api_cannot_import_cached_external_module_via_importlib(self):
        package = self.directory / "scripts/workflow_pilot/__init__.py"
        package.write_text(package.read_text() + "\nimport importlib\n"
                           "assert importlib.__import__('unregistered_package').VALUE == 7\n")
        self.snapshot()
        fake = ModuleType("unregistered_package")
        fake.VALUE = 7
        fake.__spec__ = importlib.util.spec_from_file_location(
            fake.__name__, self.directory / "ambient-package.py",
        )
        with mock.patch.object(authority, "SOURCE_ROOT", self.directory), \
             mock.patch.dict(sys.modules, {fake.__name__: fake}):
            with self.assertRaisesRegex(authority.InventoryError, "outside publisher authority"):
                authority.validate_exact_tree(self.directory, self.commit)
            self.assertIs(sys.modules[fake.__name__], fake)

    def test_real_cli_loads_system_stdlib_not_ambient_finders_or_package_shadows(self):
        package = self.directory / "scripts/workflow_pilot/__init__.py"
        package.write_text(
            package.read_text() + "\nimport builtins, sys\n"
            f"sys.path.insert(0, {str(self.directory)!r})\n"
            "class AmbientFinder:\n"
            "    def find_spec(self, fullname, path=None, target=None):\n"
            "        raise RuntimeError('ambient finder executed')\n"
            "sys.meta_path.insert(1, AmbientFinder())\n"
            "fractions = builtins.__import__('fractions')\n"
            "assert fractions.Fraction(1, 2) + fractions.Fraction(1, 3) == fractions.Fraction(5, 6)\n"
        )
        (self.directory / "fractions.py").write_text("raise RuntimeError('ambient stdlib shadow executed')\n")
        self.snapshot()
        completed = self.cli()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, b"")

    def test_authority_blob_limit_precedes_content_read(self):
        path = "scripts/workflow_pilot/__init__.py"
        source = self.directory / path
        original = source.read_bytes()
        for size in (authority.MAX_AUTHORITY_BYTES, authority.MAX_AUTHORITY_BYTES + 1):
            source.write_bytes(original + b"\n#" + b"x" * (size - len(original) - 3) + b"\n")
            self.snapshot()
            oid = self.git("rev-parse", self.commit + ":" + path).decode().strip()
            with self.subTest(size=size), mock.patch.object(authority, "SOURCE_ROOT", self.directory):
                with mock.patch.object(authority, "_git", wraps=authority._git) as git:
                    if size <= authority.MAX_AUTHORITY_BYTES:
                        self.assertEqual(authority.bind_exact_tree(self.directory, self.commit), self.paths)
                    else:
                        with self.assertRaisesRegex(ValueError, "blob exceeds bounds"):
                            authority.bind_exact_tree(self.directory, self.commit)
                reads = [
                    call for call in git.call_args_list
                    if call.args[1:] == ("cat-file", "blob", oid)
                ]
                self.assertEqual(len(reads), int(size <= authority.MAX_AUTHORITY_BYTES))
                if reads:
                    self.assertEqual(reads[0].kwargs, {"max_bytes": size})
                    with self.assertRaisesRegex(ValueError, "blob exceeds bounds"):
                        authority._git(self.directory, "cat-file", "blob", oid, max_bytes=16)
                    with self.assertRaisesRegex(ValueError, "complete publisher authority blob"):
                        authority._git(self.directory, "cat-file", "blob", oid, max_bytes=size + 1)
            completed = self.cli()
            self.assertEqual(completed.returncode, int(size > authority.MAX_AUTHORITY_BYTES), completed.stderr)
    def inert_cache(self, path):
        alternative = self.directory / "inert-cache.py"
        alternative.write_text("raise RuntimeError('inert unchecked-hash cache executed')\n")
        source = self.directory / path
        cache = Path(importlib.util.cache_from_source(str(source)))
        cache.parent.mkdir(parents=True, exist_ok=True)
        py_compile.compile(
            str(alternative), cfile=str(cache), dfile=str(source), doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
        )

    def test_exact_tree_real_cli_and_import_closure_positive(self):
        self.assertEqual(authority.bind_exact_tree(self.directory, self.commit), self.paths)
        result = authority.validate_exact_tree(self.directory, self.commit)
        self.assertTrue(result.events)
        expected = authority.validate_workflow(WORKFLOW.read_text())
        self.assertIsInstance(result, authority.Analysis)
        self.assertEqual(result, expected)
        self.assertTrue(all(isinstance(event, authority.Event) for event in result.events))
        self.assertTrue(all(isinstance(command.command, shell.Command) for command in result.commands))
        self.assertTrue(
            {id(event.command) for event in result.events}
            <= {id(command.command) for command in result.commands}
        )
        for path in (
            authority.PROGRAM_PATH, "scripts/workflow_pilot/publisher_signatures.py",
            "scripts/workflow_pilot/publisher_shell.py",
            "scripts/workflow_pilot/publisher_shell_contract.py", "scripts/upstream_port/verify.py",
            "scripts/workflow_pilot/__init__.py",
        ):
            self.assertIn(path, self.paths)
        completed = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", str(ROOT / "scripts/workflow_pilot/publisher_inventory.py"),
             "--repository-root", str(self.directory), "--commit", self.commit],
            capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_fresh_staging_uses_complete_workflow_environment(self):
        import yaml

        workflow = yaml.safe_load(WORKFLOW.read_text())
        job = workflow["jobs"]["patch-release"]
        step = next(
            step for step in job["steps"]
            if step.get("name") == "Build candidate in isolated namespace and stage public inputs"
        )
        environment = {**workflow.get("env", {}), **job["env"], **step["env"]}
        replacements = {
            "${{ needs.event-identity.outputs.fallback_sha }}": self.commit,
            "${{ runner.temp }}": str(self.directory / "runner"),
            "${{ github.workspace }}": str(self.directory),
        }
        for key, value in environment.items():
            for expression, replacement in replacements.items():
                value = value.replace(expression, replacement)
            self.assertNotIn("${{", value)
            environment[key] = value
        runtime = Path(environment["PATCH_RUNTIME_ROOT"])
        runtime.mkdir(parents=True)
        commands = contract.bash_logical_lines(step["run"], label="fresh staging")
        shell_argv = shlex.split(step["shell"])[:-1]
        environment_line, git_line = commands[:2]
        for source, expected in ((git_line, 128), (environment_line + "\n" + git_line, 0)):
            completed = subprocess.run(
                [*shell_argv, "-c", source],
                cwd=self.directory, env=environment, capture_output=True, check=False,
            )
            self.assertEqual(completed.returncode, expected, completed.stderr)
            staged = runtime / "publisher-programs.py"
            if expected:
                self.assertEqual(staged.read_bytes(), b"")
            else:
                self.assertEqual(staged.read_bytes(), self.git("show", self.commit + ":" + authority.PROGRAM_PATH))
                installs = [
                    signature for signature in authority.reviewed_inventory().signatures
                    if signature.form.argv
                    and signature.form.argv[0].literal == "/usr/bin/install"
                    and signature.form.argv[-1].literal == authority.PROGRAM_RUNTIME_PATH
                ]
                self.assertEqual(len(installs), 1)
                parameters = {"host_runner_temp": str(self.directory / "runner")}
                installed_source = "".join(
                    parameters[part.value] if part.kind == "parameter" else part.value
                    for part in installs[0].form.argv[-2].parts
                )
                self.assertEqual(Path(installed_source), staged)

    def test_real_cli_ignores_unchecked_hash_caches_for_entire_authority_closure(self):
        for path in self.paths:
            if path.endswith(".py"):
                self.inert_cache(path)
        source = self.directory / "scripts/workflow_pilot/publisher_shell.py"
        control = subprocess.run(
            [
                "/usr/bin/python3", "-I", "-S", "-B", "-c",
                "import importlib.util,sys; "
                "spec=importlib.util.spec_from_file_location('inert_control',sys.argv[1]); "
                "spec.loader.exec_module(importlib.util.module_from_spec(spec))",
                str(source),
            ],
            capture_output=True, check=False,
        )
        self.assertNotEqual(control.returncode, 0)
        self.assertIn(b"inert unchecked-hash cache executed", control.stderr)
        self.assertEqual(authority.bind_exact_tree(self.directory, self.commit), self.paths)
        for flags in ((), ("-B",)):
            with self.subTest(flags=flags):
                completed = self.cli(*flags)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, b"")
                expected = len(authority.validate_workflow(WORKFLOW.read_text()).signatures)
                self.assertEqual(
                    completed.stdout,
                    f"publisher command authority: {expected} reviewed commands\n".encode(),
                )

    def test_real_cli_rejects_changed_authority_before_any_local_import(self):
        path = self.directory / "scripts/workflow_pilot/__init__.py"
        path.write_text("raise RuntimeError('unverified package executed')\n")
        completed = self.cli()
        self.assertEqual(completed.returncode, 1)
        self.assertIn(b"authority differs from exact tree", completed.stderr)
        self.assertNotIn(b"unverified package executed", completed.stderr)

    def test_both_consumers_execute_captured_sources_without_reopening_paths(self):
        sources = authority._bind_exact_sources(self.directory, self.commit)
        for path in self.paths:
            if path.endswith(".py"):
                self.inert_cache(path)
            (self.directory / path).write_bytes(b"raise RuntimeError('reopened authority source')\n")
        previous_modules = {
            name: value for name, value in sys.modules.items()
            if name == "scripts" or name.startswith("scripts.")
        }
        with mock.patch.object(authority, "SOURCE_ROOT", self.directory):
            with authority._source_only_authority(sources):
                from scripts.workflow_pilot import publisher_inventory as checked
                from scripts.workflow_pilot import publisher_shell_contract as checked_contract
                from scripts.upstream_port import verify as checked_verify

                workflow = sources[authority.WORKFLOW_PATH].decode("utf-8")
                result = checked.validate_workflow(workflow)
                self.assertTrue(result.events)
                with mock.patch.object(publisher, "publisher_shell_contract", checked_contract):
                    self.assertEqual(publisher.publisher_boundary_errors(workflow), [])
                    checked_verify._parse_workflow_structure_text(workflow)
                    changed = workflow.replace(
                        "$PATCH_COMMIT:scripts/workflow_pilot/publisher_programs.py",
                        "HEAD:scripts/workflow_pilot/publisher_programs.py",
                    )
                    run = checked_contract.publisher_run_script(changed)
                    with mock.patch.object(
                        checked_contract, "REVIEWED_PATCH_RELEASE_RUN_SHA256",
                        checked_contract.reviewed_patch_release_run_sha256(run),
                    ):
                        self.assertTrue(publisher.publisher_boundary_errors(changed))
                        with self.assertRaises(ValueError):
                            checked_verify._parse_workflow_structure_text(changed)
                with self.assertRaisesRegex(ValueError, "outside publisher authority"):
                    __import__("scripts.unregistered_authority")
        self.assertEqual(previous_modules, {
            name: value for name, value in sys.modules.items()
            if name == "scripts" or name.startswith("scripts.")
        })

    def test_every_consumed_module_rejects_dirty_missing_mode_and_symlink_drift(self):
        for path in self.paths:
            target = self.directory / path
            source = ROOT / path
            for kind in ("dirty", "missing", "mode", "symlink"):
                with self.subTest(path=path, mutation=kind):
                    if kind == "dirty":
                        target.write_bytes(target.read_bytes() + b"\n# changed authority\n")
                    elif kind == "missing":
                        target.unlink()
                    elif kind == "mode":
                        target.chmod(target.stat().st_mode ^ stat.S_IXUSR)
                    else:
                        target.unlink()
                        target.symlink_to(source)
                    with self.assertRaises(ValueError):
                        authority.bind_exact_tree(self.directory, self.commit)
                    if target.is_symlink() or target.exists():
                        target.unlink()
                    shutil.copy2(source, target)

    def test_target_program_mutation_is_data_only_and_never_executed(self):
        marker = self.directory / "executed"
        target = self.directory / authority.PROGRAM_PATH
        target.write_text(f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n")
        with self.assertRaises(ValueError):
            authority.validate_exact_tree(self.directory, self.commit)
        self.assertFalse(marker.exists())

    def test_committed_program_import_is_data_only_and_never_executed(self):
        marker = self.directory / "program-executed"
        target = self.directory / authority.PROGRAM_PATH
        target.write_text(
            target.read_text()
            + f"\nfrom pathlib import Path\nPath({str(marker)!r}).write_text('inert program')\n"
        )
        package = self.directory / "scripts/workflow_pilot/__init__.py"
        package.write_text(package.read_text() + "\nfrom . import publisher_programs\n")
        self.snapshot()
        with mock.patch.object(authority, "SOURCE_ROOT", self.directory):
            self.assertEqual(authority.bind_exact_tree(self.directory, self.commit), self.paths)
        completed = self.cli()
        self.assertFalse(marker.exists(), completed.stderr)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertNotIn(b"authority differs from exact tree", completed.stderr)
        control = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", "-c",
             "import sys; sys.path.insert(0, sys.argv[1]); import scripts.workflow_pilot",
             str(self.directory)], capture_output=True, check=False,
        )
        self.assertEqual(control.returncode, 0, control.stderr)
        self.assertEqual(marker.read_text(), "inert program")

    def test_committed_authority_exit_cannot_satisfy_validation(self):
        package = self.directory / "scripts/workflow_pilot/__init__.py"
        original = package.read_text()
        for code in (0, None, 17):
            with self.subTest(code=code):
                package.write_text(original + f"\nraise SystemExit({code!r})\n")
                self.snapshot()
                completed = self.cli()
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertIn(b"authority terminated before validation completed", completed.stderr)
                self.assertNotIn(b"reviewed commands", completed.stdout)
                with mock.patch.object(authority, "SOURCE_ROOT", self.directory):
                    with self.assertRaisesRegex(authority.InventoryError, "terminated") as rejected:
                        authority.validate_exact_tree(self.directory, self.commit)
                self.assertIsInstance(rejected.exception.__cause__, SystemExit)
                self.assertEqual(rejected.exception.__cause__.code, code)

    def test_all_program_data_sources_are_captured_but_not_importable(self):
        sources = authority._bind_exact_sources(self.directory, self.commit)
        loader = authority._SourceOnlyAuthority(sources)
        for signature in authority.reviewed_inventory().signatures:
            program = signature.program
            if program is None:
                continue
            with self.subTest(program=program.name):
                self.assertEqual(
                    sources[program.source_path],
                    self.git("show", self.commit + ":" + program.source_path),
                )
                name = program.source_path.removesuffix(".py").replace("/", ".")
                if program.name == "authority-preflight":
                    self.assertIn(name, loader.modules)
                else:
                    self.assertNotIn(name, loader.modules)
                    self.assertNotIn(str(ROOT / program.source_path), loader.executable_sources)

    def test_committed_direct_and_aliased_loaders_cannot_execute_ambient_source(self):
        marker = self.directory / "loader-executed"
        ambient = self.directory / "ambient.py"
        ambient.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('inert loader')\n"
        )
        package = self.directory / "scripts/workflow_pilot/__init__.py"
        original = package.read_text()
        prologue = (
            "import importlib.machinery, types\n"
            f"loader = importlib.machinery.SourceFileLoader('ambient', {str(ambient)!r})\n"
            "module = types.ModuleType('ambient')\n"
        )
        for action in (
            "loader.exec_module(module)\n",
            "run = loader.exec_module\nrun(module)\n",
            "run = type(loader).exec_module\nrun(loader, module)\n",
            "read = loader.get_code\ncode = read('ambient')\nrun = exec\nrun(code, vars(module))\n",
        ):
            with self.subTest(action=action):
                source = prologue + action
                updated = original + "\n" + source
                if package.read_text() != updated:
                    package.write_text(updated)
                    self.snapshot()
                completed = self.cli()
                self.assertFalse(marker.exists(), completed.stderr)
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertNotIn(b"authority differs from exact tree", completed.stderr)
                self.assertNotIn(b"dynamic publisher authority import", completed.stderr)
                control = subprocess.run(
                    ["/usr/bin/python3", "-I", "-S", "-c", source],
                    capture_output=True, check=False,
                )
                self.assertEqual(control.returncode, 0, control.stderr)
                self.assertEqual(marker.read_text(), "inert loader")
                marker.unlink()

    def test_committed_cached_loaders_cannot_substitute_code_or_program_data(self):
        marker = self.directory / "cached-loader-executed"
        alternative = self.directory / "inert-cache.py"
        alternative.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('inert cache')\n"
        )
        ambient = self.directory / "ambient.py"
        ambient.write_text("VALUE = 1\n")
        captured = self.directory / "scripts/workflow_pilot/publisher_shell.py"
        program = self.directory / authority.PROGRAM_PATH
        package = self.directory / "scripts/workflow_pilot/__init__.py"
        original = package.read_text()
        for source_path, filename, loader_kind in (
            (ambient, str(ambient), "SourceFileLoader"),
            (captured, str(captured), "SourceFileLoader"),
            (program, str(program), "SourceFileLoader"),
            (ambient, str(captured), "SourcelessFileLoader"),
            (ambient, "<string>", "SourcelessFileLoader"),
        ):
            with self.subTest(source=source_path, filename=filename, loader=loader_kind):
                cache = Path(importlib.util.cache_from_source(str(source_path)))
                cache.parent.mkdir(parents=True, exist_ok=True)
                py_compile.compile(
                    str(alternative), cfile=str(cache), dfile=filename, doraise=True,
                    invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
                )
                path = cache if loader_kind == "SourcelessFileLoader" else source_path
                source = (
                    "import importlib.machinery, types\n"
                    f"loader = importlib.machinery.{loader_kind}('fractions', {str(path)!r})\n"
                    "run = loader.exec_module\nrun(types.ModuleType('fractions'))\n"
                )
                updated = original + "\n" + source
                if package.read_text() != updated:
                    package.write_text(updated)
                    self.snapshot()
                completed = self.cli()
                self.assertFalse(marker.exists(), completed.stderr)
                self.assertEqual(completed.returncode, 1, completed.stderr)
                self.assertNotIn(b"authority differs from exact tree", completed.stderr)
                self.assertNotIn(b"dynamic publisher authority import", completed.stderr)
                control = subprocess.run(
                    ["/usr/bin/python3", "-I", "-S", "-c", source],
                    capture_output=True, check=False,
                )
                self.assertEqual(control.returncode, 0, control.stderr)
                self.assertEqual(marker.read_text(), "inert cache")
                marker.unlink()
                cache.unlink()

    def test_prebound_loader_and_cached_code_cannot_bypass_execution_boundary(self):
        sources = authority._bind_exact_sources(self.directory, self.commit)
        marker = self.directory / "prebound-loader-executed"
        ambient = self.directory / "ambient.py"
        ambient.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('inert prebound')\n"
        )
        loader = importlib.machinery.SourceFileLoader("ambient", str(ambient))
        run = loader.exec_module
        code = loader.get_code("ambient")
        execute = builtins.exec
        cache = importlib.util.cache_from_source(str(ambient))
        cached_run = importlib.machinery.SourcelessFileLoader("ambient", cache).exec_module
        for action in (
            lambda: run(ModuleType("ambient")),
            lambda: cached_run(ModuleType("ambient")),
            lambda: execute(code, {}),
        ):
            with self.subTest(action=action):
                with authority._source_only_authority(sources), self.assertRaises(ValueError):
                    action()
                self.assertFalse(marker.exists())
                action()
                self.assertEqual(marker.read_text(), "inert prebound")
                marker.unlink()

    def test_execution_uses_captured_code_not_an_allowed_filename_alone(self):
        sources = authority._bind_exact_sources(self.directory, self.commit)
        path = "scripts/workflow_pilot/__init__.py"
        target = self.directory / path
        captured = compile(sources[path], str(target), "exec", dont_inherit=True)
        marker = self.directory / "reopened-source-executed"
        target.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('inert changed source')\n"
        )
        run = importlib.machinery.SourceFileLoader("captured", str(target)).exec_module
        expected = {}
        exec(captured, expected)
        with mock.patch.object(authority, "SOURCE_ROOT", self.directory):
            with authority._source_only_authority(sources):
                result = {}
                exec(captured, result)
                self.assertEqual(result, expected)
                with self.assertRaisesRegex(ValueError, "differs from allowed source"):
                    run(ModuleType("captured"))
        self.assertFalse(marker.exists())
        run(ModuleType("captured"))
        self.assertEqual(marker.read_text(), "inert changed source")

    def test_direct_native_loader_requires_a_system_stdlib_origin(self):
        sources = authority._bind_exact_sources(self.directory, self.commit)
        system = importlib.import_module("_json")
        origin = Path(system.__spec__.origin)
        ambient = self.directory / origin.name
        shutil.copyfile(origin, ambient)
        make_module = importlib.util.module_from_spec
        allowed = importlib.util.spec_from_file_location("_json", origin)
        denied = importlib.util.spec_from_file_location("_json", ambient)
        with authority._source_only_authority(sources):
            module = make_module(allowed)
            allowed.loader.exec_module(module)
            self.assertEqual(module.scanstring('"ok"', 1), ("ok", 4))
            with self.assertRaisesRegex(ValueError, "native import origin"):
                make_module(denied)
        control = make_module(denied)
        denied.loader.exec_module(control)
        self.assertEqual(control.scanstring('"ok"', 1), ("ok", 4))

    def test_stdlib_loader_cannot_relabel_cached_code_as_generated(self):
        marker = self.directory / "anonymous-cache-executed"
        alternative = self.directory / "inert-cache.py"
        alternative.write_text(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('inert anonymous')\n"
        )
        cache = self.directory / "anonymous.pyc"
        py_compile.compile(str(alternative), cfile=str(cache), dfile="<string>", doraise=True)
        package = self.directory / "scripts/workflow_pilot/__init__.py"
        source = (
            "import importlib.machinery, runpy, types\n"
            "loader = importlib.machinery.SourceFileLoader('source_runpy', runpy.__file__)\n"
            "module = types.ModuleType('source_runpy')\n"
            "loader.exec_module(module)\n"
            f"module.run_path({str(cache)!r})\n"
        )
        package.write_text(package.read_text() + "\n" + source)
        self.snapshot()
        completed = self.cli()
        self.assertFalse(marker.exists(), completed.stderr)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        self.assertNotIn(b"authority differs from exact tree", completed.stderr)
        control = subprocess.run(
            ["/usr/bin/python3", "-I", "-S", "-c", source], capture_output=True, check=False,
        )
        self.assertEqual(control.returncode, 0, control.stderr)
        self.assertEqual(marker.read_text(), "inert anonymous")

    def test_real_cli_preserves_direct_stdlib_loading_and_generated_records(self):
        package = self.directory / "scripts/workflow_pilot/__init__.py"
        package.write_text(
            package.read_text()
            + "\nimport importlib.machinery, importlib, types\n"
            "from collections import namedtuple\n"
            "from dataclasses import asdict, make_dataclass\n"
            "fractions = importlib.import_module('fractions')\n"
            "loader = importlib.machinery.SourceFileLoader('allowed_copy', fractions.__file__)\n"
            "module = types.ModuleType('allowed_copy')\n"
            "run = loader.exec_module\nrun(module)\n"
            "assert module.Fraction(1, 2) + module.Fraction(1, 3) == module.Fraction(5, 6)\n"
            "Record = make_dataclass('Record', [('value', int)], frozen=True)\n"
            "assert asdict(Record(7)) == {'value': 7}\n"
            "Pair = namedtuple('Pair', 'first second')\n"
            "assert Pair(3, 4)._asdict() == {'first': 3, 'second': 4}\n"
        )
        self.snapshot()
        completed = self.cli()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, b"")

    def test_isolated_cli_does_not_enable_repository_stdlib_shadows(self):
        marker = self.directory / "shadow-executed"
        for name in ("json", "hashlib", "shlex", "typing"):
            (self.directory / (name + ".py")).write_text(
                f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\n"
            )
        completed = subprocess.run(
            [
                "/usr/bin/python3", "-I", "-S",
                str(self.directory / "scripts/workflow_pilot/publisher_inventory.py"),
                "--repository-root", str(self.directory), "--commit", self.commit,
            ],
            cwd=self.directory, capture_output=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(marker.exists())

    def test_added_import_and_package_helper_must_belong_to_exact_tree(self):
        package = self.directory / "scripts/workflow_pilot/__init__.py"
        package.write_text(package.read_text() + "\nfrom . import added_authority\n")
        added = self.directory / "scripts/workflow_pilot/added_authority.py"
        added.write_text("VALUE = 1\n")
        with mock.patch.object(authority, "SOURCE_ROOT", self.directory):
            self.assertIn("scripts/workflow_pilot/added_authority.py", authority.authority_paths())
            with self.assertRaises(ValueError):
                authority.bind_exact_tree(self.directory, self.commit)
        package.write_text("import unregistered_package\n")
        with mock.patch.object(authority, "SOURCE_ROOT", self.directory), self.assertRaises(ValueError):
            authority.authority_paths()

    def test_git_environment_and_commit_inputs_cannot_redirect_binding(self):
        with mock.patch.dict(os.environ, {
            "GIT_DIR": "/unregistered", "GIT_WORK_TREE": "/unregistered",
            "GIT_OBJECT_DIRECTORY": "/unregistered", "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "alias.rev-parse", "GIT_CONFIG_VALUE_0": "!false",
        }):
            self.assertEqual(authority.bind_exact_tree(self.directory, self.commit), self.paths)
        for commit in (self.commit[:12], "HEAD", self.commit.upper(), "0" * 40, "--help", self.commit + ":file"):
            with self.subTest(commit=commit), self.assertRaises(ValueError):
                authority.bind_exact_tree(self.directory, commit)
