"""Tests for the trusted patch publisher phase machine."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.workflow_pilot import publisher_command_signatures
from scripts.workflow_pilot import publisher_phase_machine
from scripts.workflow_pilot import publisher_shell_contract


class PublisherPhaseMachineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = publisher_command_signatures.WORKFLOW_PATH.read_text(
            encoding="utf-8"
        )
        cls.run_script = publisher_command_signatures.publisher_builder_run_script(
            cls.workflow
        )
        cls.signatures = publisher_command_signatures.build_command_signatures(
            cls.run_script
        )
        cls.events = publisher_phase_machine.publisher_phase_events(cls.signatures)
        cls.generation = publisher_phase_machine.phase_generation(cls.signatures)
        cls.sources = tuple(event.source_signature for event in cls.events)

    def evaluate(self, events):
        return publisher_phase_machine.evaluate_phase_events(
            events,
            expected_generation=self.generation,
            expected_sources=self.sources,
        )

    def assert_rejected(self, events):
        result = self.evaluate(events)
        self.assertFalse(result.accepted)
        self.assertTrue(result.errors)

    def test_exact_production_transition_reaches_complete(self):
        result = self.evaluate(self.events)
        self.assertTrue(result.accepted, result.errors)
        self.assertEqual(
            result.state,
            publisher_phase_machine.PublisherPhaseState.COMPLETE,
        )
        self.assertEqual(
            publisher_phase_machine.phase_machine_errors(self.signatures),
            (),
        )

    def test_missing_duplicate_early_late_and_reordered_events_fail(self):
        kinds = publisher_phase_machine.PublisherPhaseEventKind

        def position(kind, occurrence=0):
            matches = [
                index
                for index, event in enumerate(self.events)
                if event.kind is kind
            ]
            return matches[occurrence]

        checker = position(kinds.MEMBERSHIP_COMPLETED)
        candidate_launch = position(kinds.CANDIDATE_LAUNCH_STARTED)
        candidate_complete = position(kinds.CANDIDATE_COMPLETED)
        export_start = position(kinds.EXPORT_STARTED)
        export_commit = position(kinds.EXPORT_COMMITTED)
        final_post = position(kinds.FINAL_POST_CHECK)
        cases = {}

        missing = list(self.events)
        missing.pop(checker)
        cases["missing-checker"] = missing

        duplicate = list(self.events)
        duplicate.insert(checker, self.events[checker])
        cases["duplicate-checker"] = duplicate

        for label, destination in (
            ("checker-before-launch", candidate_launch),
            ("checker-before-completion", candidate_complete),
            ("checker-after-export-start", export_start + 1),
            ("checker-after-export-commit", export_commit + 1),
        ):
            moved = list(self.events)
            event = moved.pop(checker)
            if destination > checker:
                destination -= 1
            moved.insert(destination, event)
            cases[label] = moved

        export_first = list(self.events)
        event = export_first.pop(export_start)
        export_first.insert(checker, event)
        cases["export-before-checker"] = export_first

        reordered_completion = list(self.events)
        event = reordered_completion.pop(candidate_complete)
        reordered_completion.insert(candidate_launch, event)
        cases["candidate-completion-reordered"] = reordered_completion

        omitted_post = list(self.events)
        omitted_post.pop(final_post)
        cases["post-check-omitted"] = omitted_post

        for label, events in cases.items():
            with self.subTest(case=label):
                self.assert_rejected(events)

    def test_identity_frame_generation_result_and_terminal_mutations_fail(self):
        kinds = publisher_phase_machine.PublisherPhaseEventKind
        candidate = next(
            index
            for index, event in enumerate(self.events)
            if event.kind is kinds.CANDIDATE_COMPLETED
        )
        checker = next(
            index
            for index, event in enumerate(self.events)
            if event.kind is kinds.MEMBERSHIP_COMPLETED
        )
        export = next(
            index
            for index, event in enumerate(self.events)
            if event.kind is kinds.EXPORT_COMMITTED
        )
        cases = {
            "stale-generation": (candidate, {"generation": "0" * 64}),
            "wrong-source-signature": (
                candidate,
                {"source_signature": "1" * 64},
            ),
            "wrong-control-frame": (checker, {"frame_id": "wrong-frame"}),
            "wrong-process": (candidate, {"process_id": "other-process"}),
            "wrong-session": (candidate, {"session_id": "other-session"}),
            "wrong-result": (candidate, {"result": "isolated-exit-7"}),
            "candidate-nonterminal": (candidate, {"terminal": False}),
            "candidate-not-reaped": (candidate, {"reaped": False}),
            "checker-nonterminal": (checker, {"terminal": False}),
            "export-nonterminal": (export, {"terminal": False}),
        }
        for label, (index, changes) in cases.items():
            with self.subTest(case=label):
                events = list(self.events)
                events[index] = replace(events[index], **changes)
                self.assert_rejected(events)

    def test_conditional_helper_async_and_nested_phase_events_fail(self):
        kinds = publisher_phase_machine.PublisherPhaseEventKind
        checker = next(
            index
            for index, event in enumerate(self.events)
            if event.kind is kinds.MEMBERSHIP_COMPLETED
        )
        mutations = {
            "conditional": {"frame_id": "conditional-frame"},
            "skipped": {"result": "skipped"},
            "helper": {"owner": "late_membership_helper"},
            "callback": {"owner": "membership_callback"},
            "trap": {"owner": "cleanup_trap"},
            "background": {"synchronous": False},
            "list": {"synchronous": False},
            "pipeline": {"synchronous": False},
            "subshell": {"frame_id": "subshell-frame"},
            "substitution": {"frame_id": "substitution-frame"},
        }
        for label, changes in mutations.items():
            with self.subTest(case=label):
                events = list(self.events)
                events[checker] = replace(events[checker], **changes)
                self.assert_rejected(events)

    def test_break_continue_return_exit_and_exec_bypasses_fail(self):
        kinds = publisher_phase_machine.PublisherPhaseEventKind
        checker = next(
            index
            for index, event in enumerate(self.events)
            if event.kind is kinds.MEMBERSHIP_COMPLETED
        )
        template = next(
            event
            for event in self.events
            if event.kind is kinds.CONTROL_BYPASS
        )
        for bypass in ("break", "continue", "return", "exit", "exec"):
            with self.subTest(bypass=bypass):
                events = list(self.events)
                events.insert(
                    checker,
                    replace(
                        template,
                        result=bypass,
                        source_signature=(
                            hashlib.sha256(bypass.encode("ascii")).hexdigest()
                        ),
                    ),
                )
                self.assert_rejected(events)

    def test_control_transfer_drift_fails_after_registry_refresh(self):
        launcher = "/usr/bin/python3 -I -S /mnt/control/candidate-launcher.py"
        for bypass in (
            "break",
            "continue",
            "return 0",
            "exit 0",
            "exec /bin/true",
            "trap ':' EXIT",
        ):
            with self.subTest(bypass=bypass):
                mutated = self.run_script.replace(
                    launcher,
                    bypass + "\n" + launcher,
                    1,
                )
                self.assertNotEqual(mutated, self.run_script)
                self.assertTrue(
                    self._phase_errors_with_refreshed_registry(mutated)
                )

    def _phase_errors_with_refreshed_registry(self, run_script):
        signatures = publisher_command_signatures.build_command_signatures(
            run_script
        )
        document = publisher_command_signatures.registry_document(signatures)
        with tempfile.TemporaryDirectory() as temporary:
            registry = Path(temporary) / "registry.json"
            registry.write_bytes(
                publisher_command_signatures.render_registry(document)
            )
            inventory_errors = (
                publisher_command_signatures.semantic_command_inventory_errors(
                    run_script,
                    registry_path=registry,
                    require_authority_path=False,
                    require_reviewed_digest=False,
                )
            )
            phase_errors = publisher_phase_machine.publisher_phase_errors(
                run_script,
                registry_path=registry,
                require_authority_path=False,
                require_reviewed_digest=False,
            )
        self.assertEqual(inventory_errors, ())
        return phase_errors

    def test_checker_source_order_mutations_fail_after_registry_refresh(self):
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
        without = self.run_script[: match.start()] + self.run_script[match.end() :]
        launch = "/usr/bin/python3 -I -S /mnt/control/candidate-launcher.py"
        completion = 'candidate_status="$?"'
        export_start = (
            "/usr/bin/mount -o remount,bind,rw,nosuid,nodev,noexec /mnt/export"
        )
        export_commit = (
            "/usr/bin/mount -o remount,bind,ro,nosuid,nodev,noexec /mnt/export"
        )
        before_seal, separator, after_seal = without.rpartition(export_commit)
        self.assertTrue(separator)
        mutations = {
            "before-launch": without.replace(launch, checker + launch, 1),
            "before-completion": without.replace(
                completion,
                checker + completion,
                1,
            ),
            "after-export-start": without.replace(
                export_start,
                export_start + "\n" + checker.rstrip("\n"),
                1,
            ),
            "after-export-seal": (
                before_seal
                + separator
                + "\n"
                + checker.rstrip("\n")
                + after_seal
            ),
        }
        for label, mutated in mutations.items():
            with self.subTest(case=label):
                self.assertNotEqual(mutated, self.run_script)
                self.assertTrue(
                    self._phase_errors_with_refreshed_registry(mutated)
                )

    def test_export_writer_drift_fails_after_command_registry_refresh(self):
        writer = (
            "/usr/bin/install -m 0400 /mnt/handoff/target.gba \\\n"
            "  /mnt/export/target.gba"
        )
        additions = {
            "copy": (
                "/bin/cp -a -- /mnt/handoff/target.gba "
                "/mnt/export/unregistered.gba"
            ),
            "install": (
                "/usr/bin/install -m 0400 /mnt/handoff/target.gba "
                "/mnt/export/unregistered.gba"
            ),
            "move": (
                "/bin/mv -- /mnt/handoff/target.gba "
                "/mnt/export/unregistered.gba"
            ),
            "archive": (
                "/usr/bin/tar -cf /mnt/export/unregistered.tar "
                "/mnt/handoff/target.gba"
            ),
        }
        for label, command in additions.items():
            with self.subTest(writer=label):
                mutated = self.run_script.replace(
                    writer,
                    writer + "\n" + command,
                    1,
                )
                self.assertNotEqual(mutated, self.run_script)
                self.assertTrue(
                    self._phase_errors_with_refreshed_registry(mutated)
                )

        deleted = self.run_script.replace(writer + "\n", "", 1)
        self.assertNotEqual(deleted, self.run_script)
        self.assertTrue(self._phase_errors_with_refreshed_registry(deleted))

        python_signature = next(
            signature
            for signature in self.signatures
            if signature.kind == "python"
            and "cgroup-membership-check" in signature.events
        )
        mutated_signatures = tuple(
            replace(
                signature,
                writes=("/mnt/export/unregistered.gba",),
            )
            if signature is python_signature
            else signature
            for signature in self.signatures
        )
        self.assertTrue(
            publisher_phase_machine.phase_machine_errors(mutated_signatures)
        )

    def test_retained_candidate_descendant_cannot_reach_export(self):
        builder_shell = publisher_shell_contract.builder_isolation_shell_source(
            self.run_script,
            label="publisher builder isolation shell",
        )
        _name, source = (
            publisher_shell_contract.raw_patch_release_membership_checker_source(
                builder_shell
            )
        )
        stream = mock.mock_open(read_data=b"1234\n5678\n9012\n")
        with (
            mock.patch.object(sys, "argv", ["checker", "1234"]),
            mock.patch("os.getpid", return_value=5678),
            mock.patch("builtins.open", stream),
        ):
            with self.assertRaises(SystemExit) as context:
                exec(compile(source, "<membership-checker>", "exec"), {})
        self.assertEqual(context.exception.code, 125)

        membership = next(
            index
            for index, event in enumerate(self.events)
            if event.kind
            is publisher_phase_machine.PublisherPhaseEventKind.MEMBERSHIP_COMPLETED
        )
        without_membership = list(self.events)
        without_membership.pop(membership)
        self.assert_rejected(without_membership)

    def test_formatting_and_unrelated_order_do_not_change_phase_result(self):
        formatted = self.run_script.replace(
            "/usr/bin/python3 -I -S /mnt/control/candidate-launcher.py",
            "/usr/bin/python3    -I -S /mnt/control/candidate-launcher.py",
            1,
        )
        first = 'host_uid="$(/usr/bin/id -u)"'
        second = 'host_gid="$(/usr/bin/id -g)"'
        reordered = self.run_script.replace(
            first + "\n" + second,
            second + "\n" + first,
            1,
        )
        for label, mutated in (("formatting", formatted), ("unrelated-order", reordered)):
            with self.subTest(case=label):
                self.assertNotEqual(mutated, self.run_script)
                self.assertEqual(
                    self._phase_errors_with_refreshed_registry(mutated),
                    (),
                )


if __name__ == "__main__":
    unittest.main()
