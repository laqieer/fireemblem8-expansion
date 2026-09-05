"""Tests for the patch publisher's closed command signature authority."""

from __future__ import annotations

import ast
import copy
import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import publisher_command_signatures
from scripts.workflow_pilot import publisher_shell_contract
from tests.workflows import test_patch_release_workflow


ROOT = Path(__file__).resolve().parents[3]


class PublisherCommandSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = publisher_command_signatures.WORKFLOW_PATH.read_text(
            encoding="utf-8"
        )
        cls.run_script = publisher_command_signatures.publisher_builder_run_script(
            cls.workflow
        )
        cls.signatures = publisher_command_signatures.load_registry()

    def assert_registry_mutation_rejected(self, document):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.json"
            path.write_bytes(
                publisher_command_signatures.render_registry(document)
            )
            errors = publisher_command_signatures.command_inventory_errors(
                self.run_script,
                registry_path=path,
                require_authority_path=False,
                require_reviewed_digest=False,
            )
        self.assertTrue(errors)

    def workflow_with_run_script(self, run_script):
        block = test_patch_release_workflow.named_patch_release_step_block(
            self.workflow,
            publisher_command_signatures.BUILDER_STEP_NAME,
        )
        header = block.split("      run: |\n", 1)[0] + "      run: |\n"
        rendered = "".join(
            "        " + line
            for line in run_script.splitlines(keepends=True)
        )
        return self.workflow.replace(block, header + rendered, 1)

    def assert_injected_command_denied(self, script):
        mutated = self.run_script.replace(
            publisher_shell_contract.PATCH_RELEASE_MEMBERSHIP_CHECKER_INTRODUCER,
            script
            + "\n"
            + publisher_shell_contract.PATCH_RELEASE_MEMBERSHIP_CHECKER_INTRODUCER,
            1,
        )
        self.assertNotEqual(mutated, self.run_script)
        self.assertTrue(
            publisher_command_signatures.semantic_command_inventory_errors(
                mutated
            )
        )

    def test_real_builder_and_exact_membership_checker_pass(self):
        self.assertEqual(
            publisher_command_signatures.command_inventory_errors(
                self.run_script
            ),
            (),
        )
        checker = [
            signature
            for signature in self.signatures
            if "cgroup-membership-check" in signature.events
        ]
        self.assertEqual(len(checker), 1)
        checker = checker[0]
        self.assertEqual(checker.kind, "python")
        self.assertEqual(checker.executable, "/usr/bin/python3")
        self.assertEqual(checker.argv, ("-I", "-S", "-", "$$"))
        self.assertEqual(checker.stdin, "heredoc:PY")
        self.assertEqual(
            checker.accesses,
            (("read", "/mnt/supervisor/cgroup/cgroup.procs"),),
        )
        self.assertEqual(checker.writes, ())
        self.assertEqual(
            checker.program_sha256,
            publisher_command_signatures._sha256_text(
                publisher_shell_contract._PATCH_RELEASE_MEMBERSHIP_CHECKER_SOURCE
            ),
        )

    def test_exact_tree_cli_runs_before_candidate_controlled_host_tests(self):
        command = (
            "/usr/bin/python3 -I "
            "scripts/workflow_pilot/publisher_command_signatures.py --check"
        )
        self.assertIn(command, self.workflow)
        self.assertLess(
            self.workflow.index(command),
            self.workflow.index("- name: Run gba-playtest host test suite"),
        )
        completed = subprocess.run(
            [
                "/usr/bin/python3",
                "-I",
                str(publisher_command_signatures.__file__),
                "--check",
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_mirrored_publisher_validators_share_the_same_decision(self):
        self.assertEqual(
            test_patch_release_workflow.publisher_boundary_errors(self.workflow),
            [],
        )
        mutations = (
            self.run_script.replace(
                publisher_shell_contract.PATCH_RELEASE_MEMBERSHIP_CHECKER_INTRODUCER,
                "/usr/bin/python3 -I -S -c 'raise SystemExit(0)'",
                1,
            ),
            self.run_script.replace(
                "/usr/bin/mknod -m 0666 /dev/null c 1 3",
                "/usr/bin/touch /dev/null",
                1,
            ),
        )
        for mutated in mutations:
            with self.subTest(mutation=mutated[:80]):
                direct_denied = bool(
                    publisher_command_signatures.command_inventory_errors(mutated)
                )
                changed_workflow = self.workflow_with_run_script(mutated)
                boundary_denied = bool(
                    test_patch_release_workflow.publisher_boundary_errors(
                        changed_workflow
                    )
                )
                self.assertEqual(direct_denied, boundary_denied)
                self.assertTrue(direct_denied)

    def test_composed_reader_reproduces_substring_failure_and_is_denied(self):
        composed = (
            "/usr/bin/python3 -c "
            "'open(\"/mnt/supervisor/cgroup/\"+\"cgroup.\"+\"procs\",\"rb\").read()'"
        )
        self.assertFalse(
            publisher_shell_contract.has_forbidden_supervisor_parent_readonly_mount(
                composed,
                label="composed Python membership reader negative control",
            )
        )
        self.assert_injected_command_denied(composed)

    def test_checker_fragment_duplicate_and_recomposition_are_denied(self):
        checker_pattern = (
            publisher_shell_contract.PATCH_RELEASE_MEMBERSHIP_CHECKER_INTRODUCER
            + "\n"
            + publisher_shell_contract._PATCH_RELEASE_MEMBERSHIP_CHECKER_SOURCE
            + "PY"
        )
        fragment_only = self.run_script.replace(
            checker_pattern,
            publisher_shell_contract.PATCH_RELEASE_MEMBERSHIP_CHECKER_INTRODUCER
            + "\nPY",
            1,
        )
        self.assertNotEqual(fragment_only, self.run_script)
        self.assertTrue(
            publisher_command_signatures.semantic_command_inventory_errors(
                fragment_only
            )
        )

        exact_command = "/usr/bin/mount --make-rprivate /"
        duplicated = self.run_script.replace(
            exact_command,
            exact_command + "\n" + exact_command,
            1,
        )
        self.assertNotEqual(duplicated, self.run_script)
        self.assertTrue(
            publisher_command_signatures.semantic_command_inventory_errors(
                duplicated
            )
        )

        recomposed = self.run_script.replace(
            exact_command,
            exact_command
            + "\n/usr/bin/mount --bind / /\n"
            + "/usr/bin/mount -o remount,bind,ro /",
            1,
        )
        self.assertNotEqual(recomposed, self.run_script)
        self.assertTrue(
            publisher_command_signatures.semantic_command_inventory_errors(
                recomposed
            )
        )

    def test_pipeline_or_and_stderr_pipeline_remain_distinct(self):
        mutated = self.run_script.replace(" || ", " |& ", 1)
        self.assertNotEqual(mutated, self.run_script)
        self.assertTrue(
            publisher_command_signatures.semantic_command_inventory_errors(
                mutated
            )
        )

    def test_semantic_inventory_does_not_enforce_phase_event_order(self):
        checker_pattern = re.compile(
            re.escape(
                publisher_shell_contract.PATCH_RELEASE_MEMBERSHIP_CHECKER_INTRODUCER
            )
            + r"\n.*?\nPY\n",
            re.DOTALL,
        )
        match = checker_pattern.search(self.run_script)
        self.assertIsNotNone(match)
        checker = match.group(0)
        without_checker = self.run_script[: match.start()] + self.run_script[match.end() :]
        launcher = "/usr/bin/python3 -I -S /mnt/control/candidate-launcher.py"
        reordered = without_checker.replace(launcher, checker + launcher, 1)
        self.assertNotEqual(reordered, self.run_script)
        self.assertEqual(
            publisher_command_signatures.semantic_command_inventory_errors(
                reordered
            ),
            (),
        )
        self.assertTrue(
            publisher_command_signatures.command_inventory_errors(reordered)
        )

    def test_semantic_inventory_does_not_order_command_substitutions(self):
        first = 'host_uid="$(/usr/bin/id -u)"'
        second = 'host_gid="$(/usr/bin/id -g)"'
        pair = first + "\n" + second
        self.assertIn(pair, self.run_script)
        reordered = self.run_script.replace(pair, second + "\n" + first, 1)
        self.assertEqual(
            publisher_command_signatures.semantic_command_inventory_errors(
                reordered
            ),
            (),
        )
        self.assertTrue(
            publisher_command_signatures.command_inventory_errors(reordered)
        )

    def test_recursive_inventory_contains_real_commands_and_typed_resources(self):
        self.assertFalse(
            any(
                signature.executable in {"PY", "$2", " $2 "}
                for signature in self.signatures
            )
        )
        nested_ps = [
            signature
            for signature in self.signatures
            if signature.executable == "/usr/bin/ps"
            and any(
                frame.startswith("command-substitution:")
                for frame in signature.control_context
            )
        ]
        self.assertTrue(nested_ps)

        root_mount = next(
            signature
            for signature in self.signatures
            if signature.command == "/usr/bin/mount --make-rprivate /"
        )
        self.assertEqual(root_mount.accesses, (("mount-target", "/"),))
        self.assertEqual(root_mount.writes, ("/",))

        export = next(
            signature
            for signature in self.signatures
            if signature.command.startswith(
                "/usr/bin/install -m 0400 /mnt/handoff/target.gba"
            )
        )
        self.assertEqual(
            export.accesses,
            (
                ("read", "/mnt/handoff/target.gba"),
                ("write", "/mnt/export/target.gba"),
            ),
        )
        self.assertEqual(export.writes, ("/mnt/export/target.gba",))

        metadata_checkers = [
            signature
            for signature in self.signatures
            if signature.kind == "python"
            and signature.layer == "publisher-host"
            and signature.argv[:3] == ("-I", "-S", "-c")
            and "$PATCH_INPUT_ROOT/metadata.json" in signature.argv
        ]
        self.assertEqual(len(metadata_checkers), 1)
        metadata_checker = metadata_checkers[0]
        self.assertEqual(metadata_checker.executable, "/usr/bin/python3")
        self.assertEqual(
            metadata_checker.wrappers,
            (
                "/usr/bin/env",
                "-i",
                "HOME=$PATCH_RUNTIME_ROOT",
                "PATH=/usr/bin:/bin",
                "LC_ALL=C",
            ),
        )
        self.assertIn("python-invocation", metadata_checker.events)
        self.assertIsNotNone(metadata_checker.program_sha256)

    def test_adversarial_command_families_default_deny(self):
        cases = {
            "composed-python": (
                "/usr/bin/python3 -c "
                "'open(\"/mnt/supervisor/cgroup/\"+\"cgroup.\"+\"procs\").read()'"
            ),
            "shell-code-string": (
                "/bin/bash -c 'cat /mnt/supervisor/cgroup/cgroup.procs'"
            ),
            "awk": (
                "/usr/bin/awk '{print}' /mnt/supervisor/cgroup/cgroup.procs"
            ),
            "perl": (
                "/usr/bin/perl -e "
                "'open F,\"</mnt/supervisor/cgroup/cgroup.procs\";print <F>'"
            ),
            "split-string": (
                "root=/mnt/supervisor/cgroup; "
                "name=cgroup.procs; /bin/cat \"$root/$name\""
            ),
            "encoded": (
                "printf Y2F0IC9tbnQvc3VwZXJ2aXNvci9jZ3JvdXAvY2dyb3VwLnByb2Nz"
                " | /usr/bin/base64 -d | /bin/bash"
            ),
            "escaped": (
                "/bin/cat /mnt/supervisor/cgroup/cgroup\\.procs"
            ),
            "indirect-executable": (
                "reader=/bin/cat; \"$reader\" "
                "/mnt/supervisor/cgroup/cgroup.procs"
            ),
            "dynamic-executable": (
                "$(printf /bin/cat) /mnt/supervisor/cgroup/cgroup.procs"
            ),
            "alternate-python-flags": (
                "/usr/bin/python3 -Es -c "
                "'open(\"/mnt/supervisor/cgroup/cgroup.procs\").read()'"
            ),
            "python-stdin": (
                "/usr/bin/python3 -I -S - "
                "< /mnt/supervisor/cgroup/cgroup.procs"
            ),
            "python-file": (
                "/usr/bin/python3 -I -S /tmp/unreviewed.py"
            ),
            "callback": (
                "callback=/bin/cat; \"$callback\" "
                "/mnt/supervisor/cgroup/cgroup.procs"
            ),
            "trap": (
                "trap '/bin/cat /mnt/supervisor/cgroup/cgroup.procs' EXIT"
            ),
            "process-substitution": (
                "/bin/cat <(/bin/cat /mnt/supervisor/cgroup/cgroup.procs)"
            ),
            "redirection": (
                "/bin/cat < /mnt/supervisor/cgroup/cgroup.procs"
            ),
            "raw-fragment": "/mnt/supervisor/cgroup/cgroup.procs",
            "absolute-tool": "/usr/bin/sha256sum /etc/passwd",
            "shadowed-builtin": (
                "test() { /bin/cat "
                "/mnt/supervisor/cgroup/cgroup.procs; }; test -n x"
            ),
        }
        for label, script in cases.items():
            with self.subTest(case=label):
                self.assert_injected_command_denied(script)

    def test_recursive_helpers_cannot_recombine_authorized_fragments(self):
        helpers = {
            "helper-call": (
                "read_members() { /bin/cat "
                "/mnt/supervisor/cgroup/cgroup.procs; }; read_members"
            ),
            "outer-state": (
                "root=/mnt/supervisor; "
                "read_members() { /bin/cat \"$root/cgroup/cgroup.procs\"; }; "
                "read_members"
            ),
            "array": (
                "parts=(/mnt/supervisor cgroup cgroup.procs); "
                "/bin/cat \"${parts[0]}/${parts[1]}/${parts[2]}\""
            ),
            "alias": (
                "alias reader=/bin/cat; reader "
                "/mnt/supervisor/cgroup/cgroup.procs"
            ),
            "pieces": (
                "a=/mnt/supervisor/cgroup/cgroup.; b=procs; /bin/cat \"$a$b\""
            ),
        }
        for label, script in helpers.items():
            with self.subTest(case=label):
                self.assert_injected_command_denied(script)

    def test_membership_checker_signature_mutations_fail(self):
        mutations = {
            "executable": ("/usr/bin/python3 -I -S - \"$$\"", "/usr/bin/perl - \"$$\""),
            "flags": ("/usr/bin/python3 -I -S - \"$$\"", "/usr/bin/python3 -I - -S \"$$\""),
            "argv": ("/usr/bin/python3 -I -S - \"$$\"", "/usr/bin/python3 -I -S - \"1\""),
            "path": (
                'MEMBERSHIP_PATH = "/mnt/supervisor/cgroup/cgroup.procs"',
                'MEMBERSHIP_PATH = "/mnt/supervisor/cgroup/" + "cgroup.procs"',
            ),
            "program": (
                "checker_pid = os.getpid()",
                "checker_pid = os.getppid()",
            ),
            "output": (
                "if len(members) != 2 or members != {expected_pid, checker_pid}:",
                "print(members)\n"
                "if len(members) != 2 or members != {expected_pid, checker_pid}:",
            ),
        }
        for label, (old, new) in mutations.items():
            with self.subTest(case=label):
                mutated = self.run_script.replace(old, new, 1)
                self.assertNotEqual(mutated, self.run_script)
                self.assertTrue(
                    publisher_command_signatures.command_inventory_errors(mutated)
                )

    def test_production_command_add_remove_change_and_redirect_fail(self):
        command = "/usr/bin/mknod -m 0666 /dev/null c 1 3"
        mutations = {
            "add": self.run_script.replace(
                command,
                command + "\n/usr/bin/true",
                1,
            ),
            "remove": self.run_script.replace(command + "\n", "", 1),
            "change": self.run_script.replace(
                command,
                "/usr/bin/mknod -m 0600 /dev/null c 1 3",
                1,
            ),
            "redirect": self.run_script.replace(
                command,
                command + " > /tmp/mknod.log",
                1,
            ),
        }
        for label, mutated in mutations.items():
            with self.subTest(case=label):
                self.assertNotEqual(mutated, self.run_script)
                self.assertTrue(
                    publisher_command_signatures.command_inventory_errors(mutated)
                )

    def test_registry_deletion_duplicate_ambiguity_stale_and_modification_fail(self):
        original = publisher_command_signatures.registry_document(
            self.signatures
        )

        deleted = copy.deepcopy(original)
        deleted["signatures"].pop()
        self.assert_registry_mutation_rejected(deleted)

        duplicate = copy.deepcopy(original)
        duplicate["signatures"].append(
            copy.deepcopy(duplicate["signatures"][0])
        )
        self.assert_registry_mutation_rejected(duplicate)

        stale = copy.deepcopy(original)
        extra = copy.deepcopy(stale["signatures"][-1])
        extra["signature_id"] += "-stale"
        stale["signatures"].append(extra)
        self.assert_registry_mutation_rejected(stale)

        modified = copy.deepcopy(original)
        modified["signatures"][0]["argv"] = (
            *modified["signatures"][0]["argv"],
            "--unreviewed",
        )
        self.assert_registry_mutation_rejected(modified)

        reordered = copy.deepcopy(original)
        reordered["signatures"][0], reordered["signatures"][1] = (
            reordered["signatures"][1],
            reordered["signatures"][0],
        )
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.json"
            path.write_bytes(
                publisher_command_signatures.render_registry(reordered)
            )
            self.assertEqual(
                publisher_command_signatures.command_inventory_errors(
                    self.run_script,
                    registry_path=path,
                    require_authority_path=False,
                    require_reviewed_digest=False,
                ),
                (),
            )

    def test_registry_path_digest_schema_and_json_are_closed(self):
        original = publisher_command_signatures.registry_document(
            self.signatures
        )
        rendered = publisher_command_signatures.render_registry(original)
        self.assertEqual(
            json.loads(rendered.decode("ascii")),
            json.loads(
                publisher_command_signatures.REGISTRY_PATH.read_text(
                    encoding="ascii"
                )
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary) / "registry.json"
            copied.write_bytes(rendered)
            with self.assertRaisesRegex(ValueError, "path differs"):
                publisher_command_signatures.load_registry(copied)
            copied.write_bytes(rendered.replace(b'"schema_version": 1', b'"schema_version": 2', 1))
            with self.assertRaisesRegex(ValueError, "identity differs"):
                publisher_command_signatures.load_registry(
                    copied,
                    require_authority_path=False,
                )

            malformed = copy.deepcopy(original)
            malformed["authority"]["parser"] = "candidate/parser.py"
            copied.write_bytes(
                publisher_command_signatures.render_registry(malformed)
            )
            with self.assertRaisesRegex(ValueError, "authority differs"):
                publisher_command_signatures.load_registry(
                    copied,
                    require_authority_path=False,
                    require_reviewed_digest=False,
                )

            copied.write_text('{"schema_version":1,"schema_version":1}\n')
            with self.assertRaisesRegex(ValueError, "duplicate"):
                publisher_command_signatures.load_registry(
                    copied,
                    require_authority_path=False,
                    require_reviewed_digest=False,
                )
            copied.unlink()
            with self.assertRaises(FileNotFoundError):
                publisher_command_signatures.load_registry(
                    copied,
                    require_authority_path=False,
                    require_reviewed_digest=False,
                )

    def test_authority_imports_and_parser_api_are_canonical(self):
        self.assertEqual(
            Path(publisher_command_signatures.__file__).resolve(),
            ROOT
            / "scripts"
            / "workflow_pilot"
            / "publisher_command_signatures.py",
        )
        self.assertEqual(
            Path(publisher_shell_contract.__file__).resolve(),
            ROOT / "scripts" / "workflow_pilot" / "publisher_shell_contract.py",
        )
        authority = publisher_command_signatures.registry_document(())[
            "authority"
        ]
        for name in authority["parser_api"]:
            with self.subTest(parser_api=name):
                self.assertTrue(callable(getattr(publisher_shell_contract, name)))

        tree = ast.parse(
            Path(publisher_command_signatures.__file__).read_text(
                encoding="utf-8"
            )
        )
        imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertLessEqual(
            imports,
            {
                "__future__",
                "argparse",
                "ast",
                "collections",
                "dataclasses",
                "hashlib",
                "importlib",
                "json",
                "pathlib",
                "posixpath",
                "re",
                "scripts",
                "sys",
                "typing",
            },
        )
        with mock.patch.object(
            publisher_shell_contract,
            "__file__",
            str(ROOT / "candidate" / "publisher_shell_contract.py"),
        ):
            with self.assertRaisesRegex(ValueError, "module path differs"):
                publisher_command_signatures.load_registry()


if __name__ == "__main__":
    unittest.main()
