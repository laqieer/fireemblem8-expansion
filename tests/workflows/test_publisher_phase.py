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
from scripts.workflow_pilot import publisher_programs as programs
from tests.workflows import publisher_inventory_fixtures as inventory
from tests.workflows import publisher_phase_fixtures as fixtures
from tests.workflows import test_patch_release_workflow as publisher


ROOT = Path(__file__).resolve().parents[2]


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
        directory = self.directory / case
        control = directory / "control"
        home = directory / "home"
        export = directory / "export"
        for path in (control, home, export):
            path.mkdir(parents=True)
        shutil.copyfile(ROOT / authority.PROGRAM_PATH, control / "publisher-programs.py")
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
            '    "/mnt/control/publisher-programs.py", *sys.argv[1:]], {"LC_ALL": "C", "PATH": "/usr/bin:/bin"})\n'
        )
        tail = self.tail.replace(
            "/mnt/control/publisher-programs.py membership",
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
        completed = subprocess.run(
            [
                "/usr/bin/unshare", "--user", "--map-root-user", "--mount",
                "--pid", "--fork", "--kill-child=KILL", "--mount-proc",
                "/bin/bash", "--noprofile", "--norc", "-euo", "pipefail",
                "-c", script, "--", str(directory),
            ],
            stdin=subprocess.DEVNULL, capture_output=True, check=False, timeout=15,
        )
        self.assertEqual(completed.stdout, b"")
        self.assertEqual(completed.stderr, b"", completed.stderr)
        observation = home / "observation.json"
        return completed.returncode, export, json.loads(observation.read_text()) if observation.exists() else None

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
