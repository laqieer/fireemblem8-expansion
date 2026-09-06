"""TC-WORKFLOW-PUBLISHER-PHASE-001: semantic and live Linux process controls."""

from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import unittest
from unittest import mock
import uuid

from scripts.upstream_port import verify
from scripts.workflow_pilot import publisher_inventory as authority
from scripts.workflow_pilot import publisher_phase as phase
from scripts.workflow_pilot import publisher_candidate_signatures as candidate_registry
from scripts.workflow_pilot import publisher_shell as shell
from scripts.workflow_pilot import publisher_programs as programs
from tests.workflows import publisher_inventory_fixtures as inventory
from tests.workflows import publisher_phase_fixtures as fixtures
from tests.workflows import test_patch_release_workflow as publisher


ROOT = Path(__file__).resolve().parents[2]


def run_phase_namespace(script, *arguments):
    return subprocess.run(
        [
            "/usr/bin/unshare", "--user", "--map-root-user", "--mount",
            "--pid", "--fork", "--kill-child=KILL", "--mount-proc",
            "/bin/bash", "--noprofile", "--norc", "-euo", "pipefail",
            "-c", script, "--", *arguments,
        ],
        stdin=subprocess.DEVNULL, capture_output=True, check=False, timeout=15,
    )


def require_phase_namespace():
    script = r'''
test "$$" = 1
test "$(/usr/bin/id -u)" = 0
test "$(/usr/bin/id -g)" = 0
test -r /proc/1/stat
/usr/bin/mount --make-rprivate /
/usr/bin/mount -t tmpfs -o size=1m,nosuid,nodev phase-preflight /mnt
/usr/bin/mkdir /mnt/export
/usr/bin/mount --bind /mnt/export /mnt/export
/usr/bin/mount -o remount,bind,ro,nosuid,nodev,noexec /mnt/export
/usr/bin/setpriv --reuid=0 --regid=0 --keep-groups --no-new-privs \
  --bounding-set=-all --inh-caps=-all --ambient-caps=-all /bin/true
'''
    try:
        completed = run_phase_namespace(script)
    except (FileNotFoundError, PermissionError) as error:
        raise unittest.SkipTest(
            "publisher phase namespace capability unavailable: " + str(error)[:180]
        ) from error
    if completed.returncode != 0:
        raise unittest.SkipTest(
            "publisher phase namespace capability unavailable: "
            + publisher._bounded_process_diagnostic(completed, total_limit=180)
        )
    if completed.stdout or completed.stderr:
        raise AssertionError("publisher phase namespace preflight produced unexpected output")


class PublisherPhaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / authority.WORKFLOW_PATH).read_text()
        cls.source = inventory.builder(cls.workflow)

    def test_production_checker_is_active_and_success_path_is_complete(self):
        analysis = authority.validate_workflow(self.workflow)
        transitions = phase.validate(analysis)
        self.assertEqual(tuple(step.after for step in transitions), (
            phase.Phase.LAUNCH_STARTED, phase.Phase.LAUNCH_REAPED,
            phase.Phase.MEMBERSHIP_VERIFIED, phase.Phase.EXPORT_STARTED,
            phase.Phase.EXPORT_COMMITTED, phase.Phase.POST_CHECKED,
        ))
        self.assertEqual(publisher.publisher_boundary_errors(self.workflow), [])
        verify._parse_workflow_structure_text(self.workflow)
        checker = [e for e in analysis.events if e.kind == authority.EventKind.MEMBERSHIP_VERIFIED]
        self.assertEqual(len(checker), 1)
        self.assertEqual(checker[0].context, ())
        self.assertEqual(checker[0].call_stack, ("builder_main",))
        self.assertFalse(any(e.kind == authority.EventKind.LEGACY_MEMBERSHIP for e in analysis.events))
        for step in transitions:
            self.assertEqual(step.event.call_stack, ("builder_main",))
        self.assertIn("scripts/workflow_pilot/publisher_phase.py", authority.authority_paths())
        self.assertIn("scripts/workflow_pilot/publisher_candidate.py", authority.authority_paths())

    def test_both_real_consumers_reject_every_phase_context_mutation(self):
        cases = list(fixtures.adversarial_workflows(self.workflow))
        self.assertGreaterEqual(len(cases), 30)
        for name, changed in cases:
            with self.subTest(case=name), inventory.refreshed_boundary_identities(changed):
                self.assertNotEqual(changed, self.workflow)
                syntax = subprocess.run(
                    ["/bin/bash", "-n"], input=inventory.builder(changed),
                    text=True, capture_output=True, check=False,
                )
                self.assertEqual(syntax.returncode, 0, syntax.stderr)
                self.assertTrue(publisher.publisher_boundary_errors(changed))
                with self.assertRaises(ValueError):
                    verify._parse_workflow_structure_text(changed)

    def test_inventory_alone_reproduces_phase_bypass_in_both_consumers(self):
        witnessed = set()
        for name, changed in fixtures.adversarial_workflows(self.workflow):
            if name not in fixtures.PHASE_ONLY_CASES:
                continue
            with self.subTest(case=name), inventory.refreshed_boundary_identities(changed):
                analysis = authority.reviewed_inventory().validate(inventory.builder(changed))
                with self.assertRaises(phase.PhaseError):
                    phase.validate(analysis)
                with mock.patch.object(phase, "validate"):
                    self.assertEqual(publisher.publisher_boundary_errors(changed), [])
                    verify._parse_workflow_structure_text(changed)
                witnessed.add(name)
        self.assertEqual(witnessed, fixtures.PHASE_ONLY_CASES)

    def test_preparation_and_transport_prerequisites_reject_in_both_consumers(self):
        cases = list(fixtures.prerequisite_workflows(self.workflow))
        self.assertGreaterEqual(len(cases), 35)
        for name, changed in cases:
            with self.subTest(case=name), inventory.refreshed_boundary_identities(changed):
                analysis = authority.reviewed_inventory().validate(inventory.builder(changed))
                # Exact forms/counts still authorize: the phase policy must be
                # the rejection, not an outer raw-identity mismatch.
                with self.assertRaises(phase.PhaseError):
                    phase.validate(analysis)
                self.assertTrue(publisher.publisher_boundary_errors(changed))
                with self.assertRaises(ValueError):
                    verify._parse_workflow_structure_text(changed)
                with mock.patch.object(phase, "validate"):
                    self.assertEqual(publisher.publisher_boundary_errors(changed), [])
                    verify._parse_workflow_structure_text(changed)

    def test_prerequisites_allow_independent_initializers_and_checks(self):
        changed = fixtures.move_command(self.source, "cgroup-view-name", "cgroup-bind")
        changed = fixtures.move_command(changed, "cgroup-options", "cgroup-inode")
        changed = fixtures.move_command(changed, "remaining-dev-create", "dev-remove")
        changed = changed.replace(
            '  mount_target="${writable_mount_records[index]}"\n'
            '  mount_options="${writable_mount_records[index + 1]}"',
            '  mount_options="${writable_mount_records[index + 1]}"\n'
            '  mount_target="${writable_mount_records[index]}"',
        )
        changed = changed.replace(
            'local path="$1"\n  local size_limit="$2"',
            'local size_limit="${2}"\n  local path="${1}"',
        ).replace(
            'builder_uid="$2"\nbuilder_gid="$3"',
            'builder_gid="${3}"\nbuilder_uid="${2}"',
        )
        workflow = inventory.replace_builder(self.workflow, changed)
        with inventory.refreshed_boundary_identities(workflow):
            self.assertEqual(publisher.publisher_boundary_errors(workflow), [])
            verify._parse_workflow_structure_text(workflow)
        self.assertEqual(
            [(step.before, step.after) for step in phase.validate(authority.validate_builder_script(changed))],
            [(step.before, step.after) for step in phase.validate(authority.validate_builder_script(self.source))],
        )

    def test_helper_events_cannot_be_removed_duplicated_or_rebound(self):
        analysis = authority.validate_builder_script(self.source)
        event = next(e for e in analysis.events if e.kind == authority.EventKind.TRANSPORT_READ)
        index = analysis.events.index(event)
        variants = (
            analysis.events[:index] + analysis.events[index + 1:],
            analysis.events[:index] + (event,) + analysis.events[index:],
            analysis.events[:index] + (replace(event, call_stack=("builder_main",)),) + analysis.events[index + 1:],
            analysis.events[:index] + (replace(event, context=()),) + analysis.events[index + 1:],
        )
        for events in variants:
            with self.subTest(events=len(events)), self.assertRaises(phase.PhaseError):
                phase.validate(replace(analysis, events=events))

    def test_shared_context_authority_rejects_former_phase_only_bypasses(self):
        witnessed = set()
        for name, changed in fixtures.adversarial_workflows(self.workflow):
            if name not in fixtures.INVENTORY_CONTEXT_CASES:
                continue
            with (
                self.subTest(case=name), inventory.refreshed_boundary_identities(changed),
                mock.patch.object(phase, "validate"),
            ):
                with self.assertRaises(authority.InventoryError):
                    authority.reviewed_inventory().validate(inventory.builder(changed))
                self.assertTrue(publisher.publisher_boundary_errors(changed))
                with self.assertRaises(ValueError):
                    verify._parse_workflow_structure_text(changed)
                witnessed.add(name)
        self.assertEqual(witnessed, fixtures.INVENTORY_CONTEXT_CASES)
        self.assertFalse(fixtures.INVENTORY_CONTEXT_CASES & fixtures.PHASE_ONLY_CASES)

    def test_complete_producer_authorizes_child_staging_transport_and_diagnostics(self):
        self.assertEqual(publisher.publisher_boundary_errors(self.workflow), [])
        verify._parse_workflow_structure_text(self.workflow)
        for name, changed in fixtures.producer_workflows(self.workflow):
            with self.subTest(case=name), inventory.refreshed_boundary_identities(changed):
                self.assertNotEqual(changed, self.workflow)
                self.assertTrue(publisher.publisher_boundary_errors(changed))
                with self.assertRaises(ValueError):
                    verify._parse_workflow_structure_text(changed)

    def test_candidate_and_host_diagnostic_mutations_reject_in_both_consumers(self):
        cases = list(fixtures.diagnostic_workflows(self.workflow))
        self.assertGreaterEqual(len(cases), 30)
        for name, changed in cases:
            with (
                self.subTest(case=name), fixtures.captured_programs(changed),
                inventory.refreshed_boundary_identities(changed),
            ):
                self.assertNotEqual(changed, self.workflow)
                analysis = authority.reviewed_inventory().validate(
                    inventory.contract.publisher_run_script(changed), entry_scope="staging",
                )
                with self.assertRaises(authority.InventoryError):
                    candidate = candidate_registry.analyze_payload(authority.reviewed_inventory(), analysis)
                    phase.validate_producer(analysis, candidate)
                self.assertTrue(publisher.publisher_boundary_errors(changed))
                with self.assertRaises(ValueError):
                    verify._parse_workflow_structure_text(changed)

    def test_every_unclassified_candidate_statement_rejects_in_both_consumers(self):
        cases = list(fixtures.unclassified_candidate_workflows(self.workflow))
        self.assertGreaterEqual(len(cases), 16)
        for name, changed in cases:
            with (
                self.subTest(case=name), fixtures.captured_programs(changed),
                inventory.refreshed_boundary_identities(changed),
            ):
                self.assertNotEqual(changed, self.workflow)
                analysis = authority.reviewed_inventory().validate(
                    inventory.contract.publisher_run_script(changed), entry_scope="staging",
                )
                with self.assertRaises(authority.InventoryError):
                    candidate = candidate_registry.analyze_payload(authority.reviewed_inventory(), analysis)
                    phase.validate_producer(analysis, candidate)
                self.assertTrue(publisher.publisher_boundary_errors(changed))
                with self.assertRaises(ValueError):
                    verify._parse_workflow_structure_text(changed)

    def test_legitimate_nested_preflight_and_connected_local_bindings_remain_registered(self):
        changed = fixtures.diagnostic_spelling_control(self.workflow)
        body = fixtures.candidate_script(changed).replace("readonly_path", "readonly_directory")
        changed = fixtures.replace_candidate(changed, body)
        with fixtures.captured_programs(changed), inventory.refreshed_boundary_identities(changed):
            analysis = authority.validate_workflow(changed)
            self.assertEqual(tuple(step.after for step in phase.validate(analysis))[-1], phase.Phase.POST_CHECKED)
            self.assertEqual(publisher.publisher_boundary_errors(changed), [])
            verify._parse_workflow_structure_text(changed)

    def test_removing_only_diagnostic_phase_reproduces_the_review_bypasses(self):
        selected = {
            "candidate-assignments-make-preflight",
            "late-assignment-make", "host-diagnostic-before-map",
            "host-exit-before-map", "host-exit-before-diagnostic",
        }
        witnessed = set()
        for name, changed in fixtures.diagnostic_workflows(self.workflow):
            if name not in selected:
                continue
            with (
                self.subTest(case=name), fixtures.captured_programs(changed),
                inventory.refreshed_boundary_identities(changed),
                mock.patch.object(phase, "validate_producer"),
            ):
                self.assertEqual(publisher.publisher_boundary_errors(changed), [])
                verify._parse_workflow_structure_text(changed)
                witnessed.add(name)
        self.assertEqual(witnessed, selected)

    def test_diagnostic_policy_accepts_connected_renaming_quoting_and_independent_order(self):
        changed = fixtures.diagnostic_spelling_control(self.workflow)
        self.assertNotEqual(changed, self.workflow)
        with fixtures.captured_programs(changed), inventory.refreshed_boundary_identities(changed):
            self.assertEqual(publisher.publisher_boundary_errors(changed), [])
            verify._parse_workflow_structure_text(changed)

    def test_diagnostic_events_cannot_hide_a_wrong_frame_or_reordered_mapping(self):
        analysis = authority.reviewed_inventory().validate(
            inventory.contract.publisher_run_script(self.workflow), entry_scope="staging",
        )
        candidate = candidate_registry.analyze_payload(authority.reviewed_inventory(), analysis)
        diagnostic = next(event for event in analysis.events if event.signature == "staging.command-125")
        variants = [
            tuple(event for event in analysis.events if event is not diagnostic),
            tuple(
                replace(event, call_stack=("callback",)) if event is diagnostic else event
                for event in analysis.events
            ),
        ]
        first_map = next(event for event in analysis.events if event.signature.startswith("staging.isolated-"))
        early = list(analysis.events)
        early.remove(diagnostic)
        early.insert(early.index(first_map), diagnostic)
        variants.append(tuple(early))
        for events in variants:
            with self.subTest(events=events.index(diagnostic) if diagnostic in events else None):
                with self.assertRaises(phase.PhaseError):
                    phase.validate_producer(replace(analysis, events=events), candidate)

    def test_spelling_and_independent_work_preserve_semantic_phase_result(self):
        changed = self.source.replace(
            'builder_uid="$2"\nbuilder_gid="$3"',
            'builder_gid="${3}"\nbuilder_uid="${2}"',
        ).replace(
            'membership "$$"', "'membership' \"${$}\"",
        ).replace("cd /", "'cd' '/' # spelling does not establish authority")
        changed = fixtures.move_command(changed, "limit-v", "limit-c")
        first, second = (
            fixtures.command(changed, "export-" + name)
            for name in ("target.gba", "metadata.json")
        )
        first_text, second_text = changed[first.offset:first.end], changed[second.offset:second.end]
        changed = changed[:second.offset] + first_text + changed[second.end:]
        changed = changed[:first.offset] + second_text + changed[first.end:]
        original_trace = phase.validate(authority.validate_builder_script(self.source))
        changed_trace = phase.validate(authority.validate_builder_script(changed))
        self.assertEqual(
            [(step.before, step.after) for step in original_trace],
            [(step.before, step.after) for step in changed_trace],
        )

    def test_namespace_setup_keeps_its_reserved_failure_substage(self):
        changed = fixtures.move_command(self.source, "stage-mount-audit", "readonly-control")
        missing = ROOT / "build/test-artifacts" / ("missing-hidden-" + uuid.uuid4().hex)
        self.assertFalse(missing.exists())
        selected = {
            "strict-shell", "stage-namespace", "stage-trap",
            "stage-mount-audit", "hidden-directory",
        }
        for label, source, failure_status in (
            ("original", self.source, 81), ("relocated", changed, 82),
        ):
            start = source.index("isolated_stage_failure() {")
            end = source.index("\n}", start) + 2
            hidden = fixtures.command(source, "hidden-directory")
            statements = [
                item.command for item in authority.reviewed_inventory().validate(source).commands
                if not item.nested and item.scope == "builder_main"
                and item.signature.name.removeprefix("builder_main.") in selected
                and item.command.offset <= hidden.offset
            ]
            script = source[start:end] + '\nhidden="$1"\n' + "\n".join(
                source[node.offset:node.end] for node in statements
            )
            for directory, expected in ((ROOT, 0), (missing, failure_status)):
                with self.subTest(order=label, directory=directory):
                    completed = subprocess.run(
                        ["/bin/bash", "--noprofile", "--norc", "-c", script, "--", str(directory)],
                        capture_output=True, check=False,
                    )
                    self.assertEqual(completed.returncode, expected, completed.stderr)
                    self.assertEqual(completed.stdout, b"")
                    self.assertEqual(completed.stderr, b"")
        authority.validate_builder_script(self.source)
        with self.assertRaises(phase.PhaseError):
            authority.validate_builder_script(changed)

    def test_failure_only_operations_keep_their_reserved_substages(self):
        analysis = authority.validate_workflow(self.workflow)
        stage = None
        witnessed = set()
        for event in analysis.events:
            if event.scope != "builder_main":
                continue
            name = event.signature.removeprefix("builder_main.")
            if name.startswith("stage-") and event.kind == authority.EventKind.STATE_WRITE:
                stage = name.removeprefix("stage-")
            machine = phase._Machine()
            if name not in machine.error_only:
                continue
            witnessed.add(stage)
            machine.stage = stage
            machine.setup.update(machine.setup_required)
            machine.consume(event)
            self.assertEqual(machine.transitions, [])
            for wrong_stage in {"namespace", "mount-audit", "candidate-preflight"} - {stage}:
                with self.subTest(signature=event.signature, stage=wrong_stage):
                    machine.stage = wrong_stage
                    with self.assertRaises(phase.PhaseError):
                        machine.consume(event)
        self.assertEqual(witnessed, {"namespace", "mount-audit", "candidate-preflight"})

    def test_events_cannot_fabricate_completion_or_hide_a_wrong_call_frame(self):
        analysis = authority.validate_builder_script(self.source)
        event = next(e for e in analysis.events if e.kind == authority.EventKind.MEMBERSHIP_VERIFIED)
        for replacement in (
            None, replace(event, call_stack=("builder_main", "callback")),
            replace(event, context=(authority.Context("background", "&"),)),
            replace(event, kind=authority.EventKind.CANDIDATE_STATUS),
        ):
            with self.subTest(replacement=replacement):
                events = tuple(
                    replacement if e is event else e for e in analysis.events
                    if e is not event or replacement is not None
                )
                with self.assertRaises(phase.PhaseError):
                    phase.validate(replace(analysis, events=events))
        with self.assertRaises(phase.PhaseError):
            phase.validate(replace(analysis, events=analysis.events + (event,)))

    def test_actual_program_rejects_foreign_parent_session_and_process_group(self):
        for kwargs in (
            {"getppid": 99}, {"getsid": (1, 2)}, {"getpgid": 2},
        ):
            with self.subTest(frame=kwargs):
                with (
                    mock.patch.object(programs.os, "getpid", return_value=42),
                    mock.patch.object(programs.os, "getppid", return_value=kwargs.get("getppid", 41)),
                    mock.patch.object(programs.os, "getsid", side_effect=kwargs.get("getsid", (1, 1))),
                    mock.patch.object(programs.os, "getpgrp", return_value=1),
                    mock.patch.object(programs.os, "getpgid", return_value=kwargs.get("getpgid", 1)),
                    mock.patch("builtins.open") as opened,
                    mock.patch("sys.stderr", io.StringIO()),
                ):
                    self.assertEqual(programs.main(["membership", "41"]), 125)
                    opened.assert_not_called()

    def test_post_check_validates_real_export_metadata_and_links(self):
        directory = ROOT / "build/test-artifacts" / ("publisher-post-check-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, directory)
        target, metadata = directory / "target.gba", directory / "metadata.json"

        def reset():
            for path in directory.iterdir():
                if path.is_dir():
                    path.rmdir()
                else:
                    path.unlink()
            with target.open("wb") as handle:
                handle.truncate(33554432)
            metadata.write_bytes(b"{}\n")
            target.chmod(0o400)
            metadata.chmod(0o400)

        args = ["post-check", str(os.getppid()), str(os.getuid()), str(os.getgid())]
        with (
            mock.patch.object(programs, "EXPORT_PATH", str(directory)),
            mock.patch.object(programs.os, "statvfs", return_value=type("Mount", (), {"f_flag": os.ST_RDONLY})()),
            mock.patch("sys.stderr", io.StringIO()),
        ):
            reset()
            self.assertEqual(programs.main(args), 0)
            mutations = {
                "missing": lambda: metadata.unlink(),
                "extra": lambda: (directory / "extra").touch(),
                "mode": lambda: target.chmod(0o600),
                "hardlink": lambda: (metadata.unlink(), os.link(target, metadata)),
                "symlink": lambda: (metadata.unlink(), metadata.symlink_to(target)),
                "directory": lambda: (metadata.unlink(), metadata.mkdir()),
                "empty": lambda: (metadata.chmod(0o600), metadata.write_bytes(b""), metadata.chmod(0o400)),
                "oversize": lambda: (metadata.chmod(0o600), metadata.write_bytes(b"X" * 1048577), metadata.chmod(0o400)),
                "short-rom": lambda: (target.chmod(0o600), target.write_bytes(b"X"), target.chmod(0o400)),
            }
            for name, mutate in mutations.items():
                with self.subTest(case=name):
                    reset()
                    mutate()
                    self.assertEqual(programs.main(args), 125)
            reset()
            for bad in (
                args[:-1], args + ["extra"], args[:2] + ["+0", args[3]],
                args[:2] + [str(os.getuid() + 1), args[3]],
                args[:3] + [str(os.getgid() + 1)],
            ):
                with self.subTest(arguments=bad):
                    self.assertEqual(programs.main(bad), 125)

    def test_case_catalog_resolves_focused_automation_and_dependency(self):
        catalog = json.loads((ROOT / "docs/test-cases/registry.json").read_text())
        case, = [entry for entry in catalog["cases"] if entry["id"] == phase.CASE_ID]
        feature = next(entry for entry in catalog["features"] if entry["id"] == case["feature_id"])
        self.assertIn(phase.CASE_ID, feature["required_cases"])
        self.assertIn("https://github.com/laqieer/fireemblem8-expansion/issues/201", case["issue_urls"])
        self.assertIn("https://github.com/laqieer/fireemblem8-expansion/issues/200", case["issue_urls"])
        self.assertTrue((ROOT / case["document"]).is_file())
        for mapping in case["automation"]:
            argv = shlex.split(mapping["command"])
            self.assertEqual(argv[:3], ["python3", "-m", "unittest"])
            self.assertTrue((ROOT / mapping["evidence"]).is_file())
            for selector in (value for value in argv[3:] if not value.startswith("-")):
                loader = unittest.TestLoader()
                self.assertGreater(loader.loadTestsFromName(selector).countTestCases(), 0)
                self.assertEqual(loader.errors, [])


class PublisherCandidateRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = (ROOT / authority.WORKFLOW_PATH).read_text()
        cls.source = fixtures.candidate_script(cls.workflow)
        cls.registry = authority.reviewed_inventory()
        cls.bound = candidate_registry.bind_names(cls.registry, cls.source)
        cls.analysis = cls.bound.validate(cls.source, entry_scope="candidate")
        cls.rows = {
            row.name: row for row in cls.registry.signatures
            if cls.registry.entry_scope(row.scope) == "candidate"
        }

    def updated(self, row):
        return replace(self.registry, signatures=tuple(
            row if previous.name == row.name else previous for previous in self.registry.signatures
        ))

    def test_every_actual_invocation_and_each_full_form_dimension_is_exact(self):
        self.assertEqual(len(self.analysis.signatures), len(self.rows))
        self.assertGreaterEqual(len(self.rows), 50)
        self.assertTrue(any(item.nested for item in self.analysis.commands))
        for row in self.rows.values():
            for placement in row.placements:
                self.assertEqual(self.registry.authorize(row.form, row.scope, placement.context), row)
            mutations = [
                replace(row.form, argv=row.form.argv + shell.command("unregistered").argv),
                replace(row.form, environment=row.form.environment + shell.command("UNREGISTERED=yes").environment),
                replace(row.form, redirects=row.form.redirects + shell.command("true > unregistered-output").redirects),
                replace(row.form, conditional=not row.form.conditional),
            ]
            for index in range(len(row.form.argv)):
                mutations.extend((
                    replace(row.form, argv=row.form.argv[:index] + shell.command("unregistered").argv + row.form.argv[index + 1:]),
                    replace(row.form, argv=row.form.argv[:index] + row.form.argv[index + 1:]),
                ))
            for index in range(len(row.form.environment)):
                mutations.append(replace(
                    row.form, environment=row.form.environment[:index]
                    + shell.command("UNREGISTERED=yes").environment + row.form.environment[index + 1:],
                ))
            for index, changed in enumerate(mutations):
                with self.subTest(signature=row.name, mutation=index), self.assertRaises(authority.InventoryError):
                    self.registry.authorize(changed, row.scope, row.placements[0].context)
            with self.subTest(context=row.name), self.assertRaises(authority.InventoryError):
                self.registry.authorize(
                    row.form, row.scope, row.placements[0].context + (authority.Context("background", "&"),),
                )
            without = replace(self.registry, signatures=tuple(item for item in self.registry.signatures if item is not row))
            with self.subTest(deleted=row.name), self.assertRaises(authority.InventoryError):
                without.validate(self.source, entry_scope="candidate")

    def test_workflow_only_argument_program_and_redirection_changes_reject(self):
        for name, changed in fixtures.exact_candidate_workflows(self.workflow):
            with (
                self.subTest(case=name), fixtures.captured_programs(changed),
                inventory.refreshed_boundary_identities(changed),
            ):
                self.assertNotEqual(changed, self.workflow)
                with self.assertRaises(authority.InventoryError):
                    candidate_registry.bind_names(self.registry, fixtures.candidate_script(changed)).validate(
                        fixtures.candidate_script(changed), entry_scope="candidate",
                    )
                self.assertTrue(publisher.publisher_boundary_errors(changed))
                with self.assertRaises(ValueError):
                    verify._parse_workflow_structure_text(changed)

    def test_candidate_controls_and_statement_cardinalities_are_independently_registered(self):
        for item in self.analysis.commands:
            if item.nested:
                continue
            node = item.command
            text = self.source[node.offset:node.end]
            for label, replacement in (("missing", ""), ("duplicate", text + "\n" + text)):
                changed = self.source[:node.offset] + replacement + self.source[node.end:]
                with self.subTest(signature=item.signature.name, mutation=label), self.assertRaises(ValueError):
                    self.bound.validate(changed, entry_scope="candidate")
        for control in self.registry.controls:
            if self.registry.entry_scope(control.scope) != "candidate":
                continue
            changed = replace(self.registry, controls=tuple(item for item in self.registry.controls if item is not control))
            with self.subTest(control=control.name), self.assertRaises(authority.InventoryError):
                changed.validate(self.source, entry_scope="candidate")

    def test_permissions_do_not_read_the_submitted_canonical_payload(self):
        with mock.patch.object(authority, "authority_source_bytes", side_effect=AssertionError("self-derived permissions")):
            result = self.bound.validate(self.source, entry_scope="candidate")
            self.assertEqual(result.signatures, self.analysis.signatures)
            changed = self.source.replace("make expansion-modern-map-menu-presentation-check -j1",
                                          "make unregistered-target -j1")
            with self.assertRaises(authority.InventoryError):
                self.bound.validate(changed, entry_scope="candidate")

    def test_independent_registry_updates_authorize_only_the_new_complete_forms(self):
        make = self.rows["candidate.make.run"]
        fd = self.rows["candidate.preflight.fd-check"]
        venv = self.rows["candidate.venv.create"]
        examples = (
            (
                self.source.replace("make expansion-modern-map-menu-presentation-check -j1", "make reviewed-target -j1"),
                replace(make, form=shell.command("make reviewed-target -j1")),
            ),
            (
                self.source.replace("import errno,fcntl;", "pass; import errno,fcntl;", 1),
                replace(
                    fd, form=shell.command("/usr/bin/python3 -I -S -c " + shlex.quote("pass; " + fd.program.text)),
                    program=replace(fd.program, text="pass; " + fd.program.text),
                ),
            ),
            (
                self.source.replace('/usr/bin/python3 -m venv "$HOME/venv"', '/usr/bin/python3 -I -m venv "$HOME/venv"'),
                replace(
                    venv, form=shell.command('/usr/bin/python3 -I -m venv "$HOME/venv"'),
                    program=replace(venv.program, startup=("-I",)),
                ),
            ),
        )
        for source, row in examples:
            changed = fixtures.replace_candidate(self.workflow, source)
            with fixtures.captured_programs(changed), inventory.refreshed_boundary_identities(changed):
                self.assertTrue(publisher.publisher_boundary_errors(changed))
                with self.assertRaises(ValueError):
                    verify._parse_workflow_structure_text(changed)
                enabled = self.updated(row)
                with mock.patch.object(authority, "reviewed_inventory", return_value=enabled):
                    self.assertEqual(publisher.publisher_boundary_errors(changed), [])
                    verify._parse_workflow_structure_text(changed)
                with self.assertRaises(authority.InventoryError):
                    enabled.validate(self.source, entry_scope="candidate")
        for row, form in (
            (fd, examples[1][1].form), (venv, examples[2][1].form),
        ):
            with self.assertRaises(authority.InventoryError):
                self.updated(replace(row, form=form))

    def test_literal_notice_needs_an_explicit_registry_record(self):
        changed = fixtures.diagnostic_spelling_control(self.workflow)
        body = fixtures.candidate_script(changed).replace(
            "trap 'report_candidate_failure' 'ERR'\n",
            "trap 'report_candidate_failure' 'ERR'\nprintf '%s\\n' failure_phase=debug\n",
        )
        changed = fixtures.replace_candidate(changed, body)
        form = shell.command("printf '%s\\n' failure_phase=debug")
        row = authority.Signature(
            "candidate.preflight.notice", "candidate", form, authority.Family.BUILTIN, 1,
            (authority.ResourceAccess(authority.Resource.NULL, authority.Access.WRITE),),
        )
        enabled = replace(self.registry, signatures=self.registry.signatures + (row,))
        with fixtures.captured_programs(changed), inventory.refreshed_boundary_identities(changed):
            self.assertTrue(publisher.publisher_boundary_errors(changed))
            with mock.patch.object(authority, "reviewed_inventory", return_value=enabled):
                self.assertEqual(publisher.publisher_boundary_errors(changed), [])
                verify._parse_workflow_structure_text(changed)

    def test_removing_phase_policy_does_not_remove_exact_candidate_authority(self):
        with mock.patch.object(phase, "validate_producer"):
            for name, changed in fixtures.exact_candidate_workflows(self.workflow):
                with (
                    self.subTest(case=name), fixtures.captured_programs(changed),
                    inventory.refreshed_boundary_identities(changed),
                ):
                    self.assertTrue(publisher.publisher_boundary_errors(changed))
                    with self.assertRaises(ValueError):
                        verify._parse_workflow_structure_text(changed)

    def test_owned_runtime_uses_registered_fd_module_tools_make_and_handoff_forms(self):
        directory = ROOT / "build/test-artifacts" / ("candidate-registered-" + uuid.uuid4().hex)
        directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, directory)
        for name in ("home", "handoff", "wheels"):
            (directory / name).mkdir()
        environment = {
            "PATH": "/usr/bin:/bin", "LC_ALL": "C", "HOME": str(directory / "home"),
            "GITHUB_WORKSPACE": str(directory), "WHEELHOUSE": str(directory / "wheels"),
            "HANDOFF": str(directory / "handoff"),
        }
        commands = {item.signature.name: item.command for item in self.analysis.commands if not item.nested}

        def run(name, **options):
            node = commands[name]
            self.registry.authorize(node, "candidate")
            return subprocess.run(
                ["/bin/bash", "--noprofile", "--norc", "-c", self.source[node.offset:node.end]],
                cwd=directory, env=environment, capture_output=True, check=False, timeout=30, **options,
            )

        result = run("candidate.preflight.fd-check")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
        with (directory / "owned-fd").open("wb") as handle:
            result = run("candidate.preflight.fd-check", pass_fds=(handle.fileno(),))
        self.assertEqual((result.returncode, result.stdout, result.stderr), (125, b"", b""))
        result = run("candidate.venv.create")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((directory / "home/venv/pyvenv.cfg").is_file())
        (directory / "pip").mkdir()
        (directory / "pip/__init__.py").write_text("")
        (directory / "pip/__main__.py").write_text(
            'import json,sys\nprint(json.dumps({"argv":sys.argv[1:],'
            '"prefix":sys.prefix,"isolated":sys.flags.isolated,"no_site":sys.flags.no_site}))\n'
        )
        result = run("candidate.pip.install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {
            "argv": [
                "install", "--no-index", "--find-links=" + environment["WHEELHOUSE"],
                "--require-hashes", "--only-binary=:all:", "--no-deps", "-r",
                environment["GITHUB_WORKSPACE"] + "/.github/requirements/build.txt",
            ],
            "prefix": str(directory / "home/venv"), "isolated": 0, "no_site": 0,
        })
        (directory / "build_tools.sh").write_text("#!/bin/sh\nprintf 'owned-tools\\n'\n")
        (directory / "build_tools.sh").chmod(0o755)
        (directory / "Makefile").write_text(
            "expansion-modern-map-menu-presentation-check:\n\t@printf 'owned-make\\n'\n"
        )
        for name, expected in (("candidate.build-tools.run", b"owned-tools\n"), ("candidate.make.run", b"owned-make\n")):
            result = run(name)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, expected, b""))
        source = directory / "build/expansion-modern-all-locales-all-features/release/aapcs"
        (source / "generated").mkdir(parents=True)
        (source / "fireemblem8.gba").write_bytes(b"owned synthetic test data")
        (source / "generated/expansion_build_metadata.json").write_text('{"owned":true}\n')
        for name in ("candidate.handoff.target", "candidate.handoff.metadata"):
            result = run(name)
            self.assertEqual((result.returncode, result.stdout, result.stderr), (0, b"", b""))
        self.assertEqual((directory / "handoff/target.gba").read_bytes(), b"owned synthetic test data")
        self.assertEqual(json.loads((directory / "handoff/metadata.json").read_text()), {"owned": True})
        self.assertTrue(all(path.stat().st_mode & 0o777 == 0o400 for path in (directory / "handoff").iterdir()))


class PublisherPhaseRuntimeTests(unittest.TestCase):
    """Execute the real tail/programs in a disposable, rootless PID namespace.

    The host does not delegate a writable cgroup. A same-PID exec adapter writes
    the *live private /proc process set* into a read-only cgroup-view fixture.
    It neither decides success nor emits phase events: the unchanged canonical
    checker reads its literal path, authenticates its parent, and decides.
    Kernel cgroup join/kill and dedicated-UID isolation stay with the existing
    privileged publisher integration, not this unprivileged adapter.
    """

    def setUp(self):
        self.namespace_checked = False
        self.directory = ROOT / "build/test-artifacts" / ("publisher-phase-" + uuid.uuid4().hex)
        self.directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, self.directory)
        self.workflow = (ROOT / authority.WORKFLOW_PATH).read_text()
        self.source = inventory.builder(self.workflow)
        authority.validate_workflow(self.workflow)
        failure_start = self.source.index("isolated_stage_failure() {")
        failure_end = self.source.index("\n}", failure_start) + 2
        self.failure = self.source[failure_start:failure_end]
        start = fixtures.command(self.source, "stage-candidate-preflight").offset
        end = fixtures.command(self.source, "success").end
        self.tail = self.source[start:end]

    def run_namespace(self, case, *, candidate_exit=0, descendant=False, detached=False,
                      missing_output=False, export_failure=False, writable_export=False,
                      early_checker=False, background_launch=False):
        if not self.namespace_checked:
            require_phase_namespace()
            self.namespace_checked = True
        directory = self.directory / case
        control = directory / "control"
        home = directory / "home"
        export = directory / "export"
        for path in (control, home, export):
            path.mkdir(parents=True)
        shutil.copyfile(ROOT / authority.PROGRAM_PATH, control / "publisher-programs.py")
        shutil.copyfile(ROOT / "scripts/workflow_pilot/publisher_inventory.py", control / "publisher-inventory.py")
        launcher = (ROOT / "scripts/workflow_pilot/publisher_candidate.py").read_text()
        # A rootless single-ID user namespace denies setgroups even for UID 0.
        # Keep actual setpriv capability dropping, exec, FD closure and wait.
        launcher = launcher.replace('"--clear-groups"', '"--keep-groups"')
        (control / "candidate-launcher.py").write_text(launcher)
        candidate = (
            'printf "%s\\n" "$$" > /mnt/home/candidate.pid\n'
            "/usr/bin/python3 -I -S -c '"
            'open("/mnt/handoff/target.gba","wb").truncate(33554432); '
            'open("/mnt/handoff/metadata.json","w").write("{}\\n")'
            "'\n"
            'printf "UNTRUSTED-CANDIDATE-OUTPUT\\n"\n'
            'printf "UNTRUSTED-CANDIDATE-OUTPUT\\n" >&2\n'
        )
        if background_launch:
            candidate = "/bin/sleep 0.5\n" + candidate
        if descendant:
            candidate += ("/usr/bin/setsid " if detached else "") + "/bin/sleep 5 &\n"
        if missing_output:
            candidate += "/bin/rm /mnt/handoff/target.gba\n"
        candidate += f"exit {candidate_exit}\n"
        (control / "candidate-build.sh").write_text(candidate)
        (control / "membership-proc-adapter.py").write_text(
            'import json, os, sys\n'
            'members = []\n'
            'for name in os.listdir("/proc"):\n'
            '    if not name.isdecimal():\n'
            '        continue\n'
            '    try:\n'
            '        state = open("/proc/" + name + "/stat").read().rsplit(") ", 1)[1].split()[0]\n'
            '    except FileNotFoundError:\n'
            '        continue\n'
            '    if state != "Z":\n'
            '        members.append(int(name))\n'
            'with open("/mnt/supervisor/snapshot", "w") as output:\n'
            '    output.write("".join(str(pid) + "\\n" for pid in sorted(members)))\n'
            'try:\n'
            '    pid = int(open("/mnt/home/candidate.pid").read())\n'
            'except FileNotFoundError:\n'
            '    pid = None\n'
            'with open("/mnt/home/observation.json", "w") as output:\n'
            '    json.dump({"members": members, "wrapper": os.getppid(), "checker": os.getpid(),\n'
            '               "candidate": pid, "candidate_reaped": pid is not None and not os.path.exists("/proc/" + str(pid)),\n'
            '               "session": os.getsid(0), "wrapper_session": os.getsid(os.getppid())}, output)\n'
            'os.execve("/usr/bin/python3", ["/usr/bin/python3", "-I", "-S",\n'
            '    "/mnt/control/publisher-inventory.py", "--runtime-program", *sys.argv[1:]], {"LC_ALL": "C", "PATH": "/usr/bin:/bin"})\n'
        )
        tail = self.tail.replace(
            "/mnt/control/publisher-inventory.py --runtime-program membership",
            "/mnt/control/membership-proc-adapter.py membership",
        )
        if export_failure:
            tail = tail.replace("isolated_stage=export\n", "isolated_stage=export\nulimit -f 1\n")
        if writable_export:
            tail = tail.replace(
                "/usr/bin/mount -o remount,bind,ro,nosuid,nodev,noexec /mnt/export", ":",
            )
        if early_checker:
            checker = '/usr/bin/python3 -I -S /mnt/control/membership-proc-adapter.py membership "$$"'
            tail = checker + "\n" + tail.replace(checker, "", 1)
        if background_launch:
            tail = tail.replace(
                '/mnt/control/candidate-build.sh "$host_runner_temp"',
                '/mnt/control/candidate-build.sh "$host_runner_temp" &',
            )
        setup = r'''
/usr/bin/mount --make-rprivate /
/usr/bin/mount -t tmpfs -o size=80m,nosuid,nodev fixture /mnt
/usr/bin/mkdir -p /mnt/control /mnt/handoff /mnt/home /mnt/export /mnt/supervisor/cgroup
/bin/cp -- "$1/control/"* /mnt/control/
/bin/chmod 0555 /mnt/control/candidate-build.sh
/bin/chmod 0444 /mnt/control/*.py
/usr/bin/mount --bind /mnt/control /mnt/control
/usr/bin/mount -o remount,bind,ro,nosuid,nodev,noexec /mnt/control
/usr/bin/mount --bind "$1/home" /mnt/home
/usr/bin/mount --bind "$1/export" /mnt/export
/usr/bin/mount -o remount,bind,ro,nosuid,nodev,noexec /mnt/export
: > /mnt/supervisor/snapshot
: > /mnt/supervisor/cgroup/cgroup.procs
/usr/bin/mount --bind /mnt/supervisor/snapshot /mnt/supervisor/cgroup/cgroup.procs
/usr/bin/mount -o remount,bind,ro,nosuid,nodev,noexec /mnt/supervisor/cgroup/cgroup.procs
builder_uid=0
builder_gid=0
host_uid=0
host_gid=0
host_runner_temp=/home/runner/work/_temp
set -Eeuo pipefail
isolated_stage=output-validate
'''
        script = setup + "\nbuilder_main() {\n" + self.failure + "\ntrap isolated_stage_failure ERR\n"
        script += "exec < /dev/null > /dev/null 2>&1\n" + tail + '\n}\nbuilder_main "$@"\n'
        completed = run_phase_namespace(script, str(directory))
        self.assertEqual(completed.stdout, b"")
        self.assertEqual(completed.stderr, b"", completed.stderr)
        observation = home / "observation.json"
        return completed.returncode, export, json.loads(observation.read_text()) if observation.exists() else None

    def test_namespace_capability_denial_skips_before_candidate_with_bounded_diagnostic(self):
        completed = subprocess.CompletedProcess(
            ["/usr/bin/unshare"], 1, b"",
            b"unshare: write failed /proc/self/uid_map: Operation not permitted\n" * 40,
        )
        with mock.patch(f"{__name__}.run_phase_namespace", return_value=completed) as run:
            with self.assertRaises(unittest.SkipTest) as context:
                self.run_namespace("uid-map-denied")
        self.assertIn("namespace capability unavailable:", str(context.exception))
        self.assertIn("rc=1", str(context.exception))
        self.assertIn("Operation not permitted", str(context.exception))
        self.assertLessEqual(len(str(context.exception)), 240)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_live_failure_after_successful_namespace_preflight_is_not_skipped(self):
        with mock.patch(f"{__name__}.run_phase_namespace", side_effect=(
            subprocess.CompletedProcess(["/usr/bin/unshare"], 0, b"", b""),
            subprocess.CompletedProcess(["/usr/bin/unshare"], 1, b"", b"runtime setup failed\n"),
        )) as run:
            with self.assertRaisesRegex(AssertionError, "runtime setup failed"):
                self.run_namespace("runtime-failure")
        self.assertEqual(run.call_count, 2)
        self.assertTrue(self.namespace_checked)

    def test_live_launch_reap_membership_export_and_fixed_failure_substages(self):
        status, export, observed = self.run_namespace("positive")
        self.assertEqual(status, 0)
        self.assertTrue(observed["candidate_reaped"])
        self.assertEqual(set(observed["members"]), {observed["wrapper"], observed["checker"]})
        self.assertEqual(observed["session"], observed["wrapper_session"])
        self.assertEqual({path.name for path in export.iterdir()}, {"target.gba", "metadata.json"})
        self.assertEqual((export / "target.gba").stat().st_size, 33554432)
        self.assertEqual(json.loads((export / "metadata.json").read_text()), {})
        self.assertEqual((export / "target.gba").stat().st_mode & 0o777, 0o400)
        cases = (
            ("candidate-nonzero", {"candidate_exit": 37}, 77, False),
            ("candidate-reserved-code", {"candidate_exit": 81}, 77, False),
            ("candidate-fixed-stage", {"candidate_exit": 75}, 75, False),
            ("incomplete-reap", {"background_launch": True}, 83, True),
            ("live-descendant", {"descendant": True}, 83, True),
            ("detached-descendant", {"descendant": True, "detached": True}, 83, True),
            ("missing-output", {"missing_output": True}, 83, True),
            ("export-failure", {"export_failure": True}, 84, True),
            ("unsealed-post-check", {"writable_export": True}, 85, True),
        )
        for name, arguments, expected, checked in cases:
            with self.subTest(case=name):
                status, export, observed = self.run_namespace(name, **arguments)
                self.assertEqual(status, expected)
                self.assertEqual(observed is not None, checked)
                if observed is not None:
                    self.assertEqual(observed["candidate_reaped"], not arguments.get("background_launch", False))
                if arguments.get("descendant"):
                    self.assertGreater(len(observed["members"]), 2)
                if expected not in {84, 85}:
                    self.assertEqual(list(export.iterdir()), [])

    def test_successful_early_snapshot_is_not_candidate_completion(self):
        # The live checker itself succeeds before launch: its two-PID snapshot
        # cannot prove a future child has completed. The production phase
        # authority must reject this unchanged checker placement independently.
        status, _export, observed = self.run_namespace("early", early_checker=True)
        self.assertEqual(status, 0)
        self.assertIsNone(observed["candidate"])
        self.assertFalse(observed["candidate_reaped"])
        self.assertEqual(set(observed["members"]), {observed["wrapper"], observed["checker"]})
        changed = fixtures.move_command(self.source, "membership-check", "stage-candidate-preflight")
        analysis = authority.reviewed_inventory().validate(changed)
        with self.assertRaises(phase.PhaseError):
            phase.validate(analysis)

    def candidate_failure(self, workflow, stage, *, real_make=False):
        body = fixtures.candidate_script(workflow)
        commands = fixtures.root_commands(body)
        actions = {
            "preflight": lambda argv: argv[:4] == ("/usr/bin/python3", "-I", "-S", "-c"),
            "venv": lambda argv: argv[:3] == ("/usr/bin/python3", "-m", "venv"),
            "pip": lambda argv: argv[1:4] == ("-m", "pip", "install"),
            "build-tools": lambda argv: argv[:1] == ("./build_tools.sh",),
            "make": lambda argv: argv[:1] == ("make",),
            "handoff": lambda argv: argv[:1] == ("/usr/bin/install",),
        }
        action = next(node for node in commands if actions[stage](tuple(word.literal for word in node.argv)))
        assignment = [
            node for node in commands if node.offset < action.offset
            and len(node.environment) == 1 and not node.argv
        ][-1]
        trap = next(node for node in commands if node.argv and node.argv[0].literal == "trap")
        start = body.index(trap.argv[1].literal + "() {")
        end = body.index("\n}", start) + 2
        script = (
            body[commands[0].offset:commands[0].end] + "\n" + body[start:end] + "\n"
            + body[trap.offset:trap.end] + "\n"
            + body[assignment.offset:assignment.end] + "\n"
        )
        script += body[action.offset:action.end] if real_make else "/bin/false"
        marker = self.directory / "make-ran"
        if real_make:
            (self.directory / "Makefile").write_text(
                "expansion-modern-map-menu-presentation-check:\n"
                "\t@printf invoked > make-ran\n\t@false\n"
            )
        result = subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-c", script],
            cwd=self.directory, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, timeout=10,
        )
        if real_make:
            self.assertEqual(marker.read_text(), "invoked")
            marker.unlink()
        return result.returncode

    def host_failure(self, workflow, status):
        return subprocess.run(
            ["/bin/bash", "--noprofile", "--norc", "-euo", "pipefail", "-c",
             'unset builder_isolated_detail\nbuilder_status="$1"\n'
             + fixtures.host_failure_script(workflow), "--", str(status)],
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            text=True, capture_output=True, check=False, timeout=10,
        )

    def test_actual_err_trap_binds_every_candidate_stage_assignment(self):
        mutations = dict(fixtures.diagnostic_workflows(self.workflow))
        for stage, expected in (
            ("preflight", 71), ("venv", 72), ("pip", 73),
            ("build-tools", 74), ("make", 75), ("handoff", 76),
        ):
            with self.subTest(stage=stage):
                self.assertEqual(self.candidate_failure(self.workflow, stage), expected)
                changed = mutations["candidate-assignment-" + stage]
                self.assertNotEqual(self.candidate_failure(changed, stage), expected)
                with fixtures.captured_programs(changed), self.assertRaises(authority.InventoryError):
                    authority.validate_workflow(changed)

    def test_real_make_failure_and_host_diagnostic_expose_the_pre_fix_mutations(self):
        mutations = dict(fixtures.diagnostic_workflows(self.workflow))
        for name, workflow, expected in (
            ("canonical", self.workflow, 75),
            ("spelling", fixtures.diagnostic_spelling_control(self.workflow), 75),
            ("codes", mutations["candidate-exits-make-preflight"], 71),
            ("assignments", mutations["candidate-assignments-make-preflight"], 71),
            ("late-assignment", mutations["late-assignment-make"], 74),
        ):
            with self.subTest(case=name):
                status = self.candidate_failure(workflow, "make", real_make=True)
                self.assertEqual(status, expected)
                result = self.host_failure(workflow, status)
                self.assertEqual(result.returncode, expected)
                detail = {71: "preflight", 74: "build-tools", 75: "make"}[expected]
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, (
                    "candidate build failed: stage=isolated "
                    f"detail=candidate-{detail} exit={expected}\n"
                ))
        for name in ("host-diagnostic-before-map", "host-exit-before-map", "host-exit-before-diagnostic"):
            with self.subTest(case=name):
                changed = mutations[name]
                result = self.host_failure(changed, 75)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("candidate build failed: stage=isolated", result.stderr)
                if name == "host-diagnostic-before-map":
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("unbound variable", result.stderr)
                else:
                    self.assertEqual(result.returncode, 75)
                    self.assertEqual(result.stderr, "")
                with fixtures.captured_programs(changed), self.assertRaises(authority.InventoryError):
                    authority.validate_workflow(changed)

    def test_unknown_runtime_effects_are_confined_controls_not_valid_candidate_statements(self):
        mutations = dict(fixtures.unclassified_candidate_workflows(self.workflow))
        for name, expected_status, python_marker, recipe_ran, flags in (
            ("handler-shadows-make", 75, False, False, "unset"),
            ("root-python", 75, True, True, "unset"),
            ("called-python-helper", 75, True, True, "unset"),
            ("makeflags-assignment", 75, False, True, "-n"),
            ("exported-makeflags", 0, False, False, "-n"),
        ):
            with self.subTest(case=name):
                workflow = mutations[name]
                with fixtures.captured_programs(workflow), self.assertRaises(authority.InventoryError):
                    authority.validate_workflow(workflow)
                directory = self.directory / name
                directory.mkdir()
                marker = directory / "runtime-marker"
                self.assertFalse(marker.exists())
                (directory / "Makefile").write_text(
                    "expansion-modern-map-menu-presentation-check:\n"
                    "\t@printf ran > make-ran\n\t@false\n"
                )
                body = fixtures.candidate_script(workflow)
                commands = fixtures.root_commands(body)
                fd_check = next(
                    command for command in commands
                    if tuple(word.literal for word in command.argv[:4])
                    == ("/usr/bin/python3", "-I", "-S", "-c")
                )
                make = next(
                    command for command in commands
                    if command.argv and command.argv[0].literal == "make"
                )
                stage = next(
                    command for command in commands
                    if any(word.literal == "candidate_stage=make" for word in command.environment)
                )
                script = (
                    "unset MAKEFLAGS\n" + body[:fd_check.offset]
                    + body[stage.offset:stage.end] + "\n"
                    + 'printf "%s" "${MAKEFLAGS-unset}" > observed-flags\n'
                    + body[make.offset:make.end] + "\n"
                )
                completed = subprocess.run(
                    ["/bin/bash", "--noprofile", "--norc", "-c", script],
                    cwd=directory, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False, timeout=10,
                )
                self.assertEqual(completed.returncode, expected_status)
                self.assertEqual(marker.exists(), python_marker)
                if python_marker:
                    self.assertEqual(marker.read_text(), "executed")
                self.assertEqual((directory / "make-ran").exists(), recipe_ran)
                self.assertEqual((directory / "observed-flags").read_text(), flags)

    def test_actual_candidate_and_host_failure_handlers_preserve_fixed_protocol(self):
        run = inventory.contract.publisher_run_script(self.workflow)
        start = run.index('if [ "$builder_status" -ne 0 ]; then')
        end = run.index("printf 'candidate build status: success", start)
        handler = run[start:end]
        details = {
            71: "candidate-preflight", 72: "candidate-venv", 73: "candidate-pip",
            74: "candidate-build-tools", 75: "candidate-make", 76: "candidate-handoff",
            77: "candidate-unknown", 81: "namespace", 82: "mount-audit",
            83: "output-validate", 84: "export", 85: "post-check",
        }
        for status in (*details, 1, 80, 86, 125, 126):
            with self.subTest(host_status=status):
                expected = status if status in details else 125
                completed = subprocess.run(
                    ["/bin/bash", "-euo", "pipefail", "-c", 'builder_status="$1"\n' + handler, "--", str(status)],
                    capture_output=True, check=False,
                )
                self.assertEqual(completed.returncode, expected)
                self.assertEqual(completed.stdout, b"")
                self.assertEqual(completed.stderr.decode(), (
                    "candidate build failed: stage=isolated "
                    f"detail={details.get(status, 'transport')} exit={expected}\n"
                ))
        start = run.index("candidate_stage_failure() {")
        end = run.index("\n}", start) + 2
        handler = run[start:end]
        for stage, expected in (
            ("preflight", 71), ("venv", 72), ("pip", 73), ("build-tools", 74),
            ("make", 75), ("handoff", 76), ("unregistered", 77),
        ):
            with self.subTest(candidate_stage=stage):
                completed = subprocess.run(
                    ["/bin/bash", "-euo", "pipefail", "-c",
                     handler + '\ncandidate_stage="$1"\ncandidate_stage_failure', "--", stage],
                    capture_output=True, check=False,
                )
                self.assertEqual(completed.returncode, expected)
                self.assertEqual(completed.stdout, b"")
                self.assertEqual(completed.stderr, b"")
