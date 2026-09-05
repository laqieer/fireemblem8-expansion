"""Tests for the patch publisher's closed command signature authority."""

from __future__ import annotations

import ast
import copy
import json
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import publisher_command_signatures
from tests.workflows import test_patch_release_workflow


ROOT = Path(__file__).resolve().parents[3]
publisher_shell_contract = publisher_command_signatures.publisher_shell_contract
ARTIFACT_ROOT = ROOT / "build" / "test-artifacts"


class PublisherCommandSignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        snapshot = publisher_command_signatures._authority_snapshot()
        cls.workflow = snapshot.files[
            publisher_command_signatures._WORKFLOW_AUTHORITY_PATH
        ].data.decode("utf-8")
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

    def test_exact_tree_bootstrap_runs_before_candidate_controlled_host_tests(self):
        command = '/usr/bin/python3 -I - "$EXPECTED_BUILD_SHA" <<\'PY\''
        self.assertIn(command, self.workflow)
        self.assertLess(
            self.workflow.index(command),
            self.workflow.index("- name: Run gba-playtest host test suite"),
        )
        self.assertIn(
            'commit = object_bytes(expected, "commit")',
            self.workflow,
        )
        self.assertIn('source = object_bytes(object_id, "blob")', self.workflow)
        self.assertIn(
            'exec(compile(source, path, "exec"), namespace)',
            self.workflow,
        )

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
        snapshot = publisher_command_signatures._authority_snapshot()
        self.assertEqual(
            publisher_shell_contract.__file__,
            publisher_command_signatures._PARSER_AUTHORITY_PATH,
        )
        self.assertEqual(set(snapshot.files), set(publisher_command_signatures._AUTHORITY_PATHS))
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
                "json",
                "os",
                "pathlib",
                "posixpath",
                "re",
                "shlex",
                "stat",
                "subprocess",
                "sys",
                "types",
                "typing",
            },
        )

    def authority_fixture(self, relative_path, data=b"trusted\n"):
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(
            prefix="publisher-authority-",
            dir=ARTIFACT_ROOT,
        )
        root = Path(temporary.name)
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return temporary, root, path

    def assert_authority_path_rejected(self, root, relative_path, data=b"trusted\n"):
        with self.assertRaisesRegex(ValueError, "publisher authority"):
            publisher_command_signatures._secure_read_authority_file(
                root,
                relative_path,
                expected_data=data,
                expected_mode="100644",
            )

    def test_every_authority_file_rejects_symlink_nonregular_and_content_drift(self):
        for relative_path in publisher_command_signatures._AUTHORITY_PATHS:
            with self.subTest(path=relative_path, mutation="symlink"):
                temporary, root, path = self.authority_fixture(relative_path)
                try:
                    target = root / "attacker"
                    target.write_bytes(b"trusted\n")
                    path.unlink()
                    path.symlink_to(target)
                    self.assert_authority_path_rejected(root, relative_path)
                finally:
                    temporary.cleanup()

            with self.subTest(path=relative_path, mutation="directory"):
                temporary, root, path = self.authority_fixture(relative_path)
                try:
                    path.unlink()
                    path.mkdir()
                    self.assert_authority_path_rejected(root, relative_path)
                finally:
                    temporary.cleanup()

            with self.subTest(path=relative_path, mutation="fifo"):
                temporary, root, path = self.authority_fixture(relative_path)
                try:
                    path.unlink()
                    os.mkfifo(path)
                    self.assert_authority_path_rejected(root, relative_path)
                finally:
                    temporary.cleanup()

            with self.subTest(path=relative_path, mutation="content"):
                temporary, root, _path = self.authority_fixture(
                    relative_path,
                    data=b"altered\n",
                )
                try:
                    self.assert_authority_path_rejected(root, relative_path)
                finally:
                    temporary.cleanup()

    def test_every_authority_parent_rejects_symlink_substitution(self):
        for relative_path in publisher_command_signatures._AUTHORITY_PATHS:
            parts = relative_path.split("/")
            for depth in range(1, len(parts)):
                with self.subTest(path=relative_path, parent_depth=depth):
                    ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
                    with tempfile.TemporaryDirectory(
                        prefix="publisher-parent-",
                        dir=ARTIFACT_ROOT,
                    ) as temporary:
                        root = Path(temporary)
                        link = root.joinpath(*parts[:depth])
                        link.parent.mkdir(parents=True, exist_ok=True)
                        target = root / "redirected-parent"
                        target.mkdir()
                        destination = target.joinpath(*parts[depth:])
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_bytes(b"trusted\n")
                        link.symlink_to(target, target_is_directory=True)
                        self.assert_authority_path_rejected(root, relative_path)

    def test_every_authority_file_rejects_path_swap_after_open(self):
        original_reader = publisher_command_signatures._read_all_from_fd
        for relative_path in publisher_command_signatures._AUTHORITY_PATHS:
            with self.subTest(path=relative_path):
                temporary, root, path = self.authority_fixture(relative_path)
                try:
                    old_path = path.with_name(path.name + ".opened")

                    def swap(descriptor, *, expected_size):
                        path.rename(old_path)
                        path.write_bytes(b"substitute\n")
                        return original_reader(
                            descriptor,
                            expected_size=expected_size,
                        )

                    with mock.patch.object(
                        publisher_command_signatures,
                        "_read_all_from_fd",
                        side_effect=swap,
                    ):
                        self.assert_authority_path_rejected(root, relative_path)
                finally:
                    temporary.cleanup()

    def test_every_authority_parent_rejects_path_swap_after_open(self):
        original_reader = publisher_command_signatures._read_all_from_fd
        for relative_path in publisher_command_signatures._AUTHORITY_PATHS:
            parts = relative_path.split("/")
            for depth in range(1, len(parts)):
                with self.subTest(path=relative_path, parent_depth=depth):
                    temporary, root, _path = self.authority_fixture(relative_path)
                    try:
                        parent = root.joinpath(*parts[:depth])
                        old_parent = parent.with_name(parent.name + ".opened")

                        def swap(descriptor, *, expected_size):
                            parent.rename(old_parent)
                            parent.mkdir()
                            replacement = parent.joinpath(*parts[depth:])
                            replacement.parent.mkdir(parents=True, exist_ok=True)
                            replacement.write_bytes(b"substitute\n")
                            return original_reader(
                                descriptor,
                                expected_size=expected_size,
                            )

                        with mock.patch.object(
                            publisher_command_signatures,
                            "_read_all_from_fd",
                            side_effect=swap,
                        ):
                            self.assert_authority_path_rejected(
                                root,
                                relative_path,
                            )
                    finally:
                        temporary.cleanup()

    def test_authority_rejects_root_symlink_modes_links_and_path_escape(self):
        temporary, root, path = self.authority_fixture("authority/file.py")
        try:
            linked_root = root.with_name(root.name + "-link")
            linked_root.symlink_to(root, target_is_directory=True)
            self.assert_authority_path_rejected(linked_root, "authority/file.py")
            linked_root.unlink()

            path.chmod(0o600)
            self.assert_authority_path_rejected(root, "authority/file.py")
            path.chmod(0o644)

            original_fstat = os.fstat

            def foreign_owner(descriptor):
                metadata = original_fstat(descriptor)
                if publisher_command_signatures.stat.S_ISREG(metadata.st_mode):
                    values = {
                        name: getattr(metadata, name)
                        for name in (
                            "st_dev",
                            "st_ino",
                            "st_mode",
                            "st_gid",
                            "st_nlink",
                            "st_size",
                            "st_mtime_ns",
                            "st_ctime_ns",
                        )
                    }
                    values["st_uid"] = metadata.st_uid + 1
                    return types.SimpleNamespace(**values)
                return metadata

            with mock.patch.object(os, "fstat", side_effect=foreign_owner):
                self.assert_authority_path_rejected(root, "authority/file.py")

            hardlink = path.with_name("hardlink.py")
            os.link(path, hardlink)
            self.assert_authority_path_rejected(root, "authority/file.py")
            hardlink.unlink()

            path.parent.chmod(0o777)
            self.assert_authority_path_rejected(root, "authority/file.py")

            with self.assertRaisesRegex(ValueError, "escapes"):
                publisher_command_signatures._secure_read_authority_file(
                    root,
                    "../outside",
                    expected_data=b"trusted\n",
                    expected_mode="100644",
                )
        finally:
            temporary.cleanup()

    def test_authority_rejects_repository_root_path_swap_after_open(self):
        original_reader = publisher_command_signatures._read_all_from_fd
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="publisher-root-swap-",
            dir=ARTIFACT_ROOT,
        ) as temporary:
            container = Path(temporary)
            root = container / "root"
            path = root / "authority" / "file.py"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"trusted\n")
            old_root = container / "opened-root"

            def swap(descriptor, *, expected_size):
                root.rename(old_root)
                replacement = root / "authority" / "file.py"
                replacement.parent.mkdir(parents=True)
                replacement.write_bytes(b"substitute\n")
                return original_reader(
                    descriptor,
                    expected_size=expected_size,
                )

            with mock.patch.object(
                publisher_command_signatures,
                "_read_all_from_fd",
                side_effect=swap,
            ):
                self.assert_authority_path_rejected(root, "authority/file.py")

    def test_authority_parser_ignores_import_cache_and_sys_path_substitution(self):
        poisoned = types.ModuleType(
            "scripts.workflow_pilot.publisher_shell_contract"
        )
        poisoned.split_bash_command_records = lambda *_args, **_kwargs: ()
        poisoned_shlex = types.ModuleType("shlex")
        with (
            mock.patch.dict(
                sys.modules,
                {
                    "scripts.workflow_pilot.publisher_shell_contract": poisoned,
                    "shlex": poisoned_shlex,
                },
            ),
            mock.patch.object(sys, "path", [str(ARTIFACT_ROOT), *sys.path]),
        ):
            snapshot = publisher_command_signatures._load_authority_snapshot()
        self.assertIsNot(snapshot.parser, poisoned)
        self.assertIsNot(snapshot.parser.shlex, poisoned_shlex)
        parser_file = snapshot.files[
            publisher_command_signatures._PARSER_AUTHORITY_PATH
        ]
        self.assertEqual(
            snapshot.parser.__authority_object_id__,
            parser_file.object_id,
        )

    def test_git_commit_tree_and_blob_objects_are_rehashed(self):
        root_descriptor = os.open(
            ROOT,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        )
        try:
            for object_type, data in (
                ("commit", b"tree " + b"1" * 40 + b"\n"),
                ("tree", b"100644 file.py\0" + b"\x11" * 20),
                ("blob", b"print('forged')\n"),
            ):
                with self.subTest(object_type=object_type):
                    object_id = "a" * 40

                    def forged_git(_descriptor, arguments):
                        if arguments[:2] == ["cat-file", "-s"]:
                            return str(len(data)).encode("ascii") + b"\n"
                        if arguments[:2] == ["cat-file", object_type]:
                            return data
                        raise AssertionError(arguments)

                    with mock.patch.object(
                        publisher_command_signatures,
                        "_run_git",
                        side_effect=forged_git,
                    ):
                        with self.assertRaisesRegex(
                            ValueError,
                            "object identity differs",
                        ):
                            publisher_command_signatures._verified_git_object(
                                root_descriptor,
                                object_id=object_id,
                                object_type=object_type,
                            )
        finally:
            os.close(root_descriptor)


if __name__ == "__main__":
    unittest.main()
