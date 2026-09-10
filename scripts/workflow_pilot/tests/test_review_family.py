"""Focused reducer, role and independent public-schema regressions for #179."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import copy
from dataclasses import FrozenInstanceError, replace
import json
import unittest
from unittest.mock import patch

from scripts.workflow_pilot import review_family as model
from scripts.workflow_pilot import trusted_review_gate as gate
from scripts.workflow_pilot.tests import review_support as support
from scripts.workflow_pilot.tests.review_support import ROOT, Runtime, git, request, snapshot


class RequestTests(unittest.TestCase):
    def test_public_request_positive_and_closed_negative_controls(self):
        valid = request()
        self.assertEqual(model.validate_request(model.parse_json(json.dumps(valid).encode())), valid)
        for key in ("pass", "program", "module", "members", "trusted", "receipt",
                    "cleanup_confirmed", "_on_cleanup",
                    "execution_inputs", "blocked_by", "evidence"):
            with self.subTest(key=key), self.assertRaises(model.ReviewError):
                model.validate_request({**valid, key: True})
        for key in valid:
            changed = copy.deepcopy(valid)
            del changed[key]
            with self.subTest(key=key), self.assertRaises(model.ReviewError):
                model.validate_request(changed)
        for raw in (b'{"a":1,"a":2}', b'{"x":NaN}', b'{"x":Infinity}'):
            with self.assertRaises(model.ReviewError):
                model.parse_json(raw)
        for value in (True, 0, "1"):
            with self.assertRaises(model.ReviewError):
                model.validate_request({**valid, "pull_request": value})

    def test_schema_parity_uses_inherited_locked_interpreter(self):
        from scripts import host_python
        from jsonschema import Draft202012Validator

        host_python.check_environment()
        schema = json.loads((ROOT / "scripts/workflow_pilot/review_family.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        cases = [(request(), True), ({**request(), "pull_request": 1.0}, True),
                 ({**request(), "schema_version": 1.0}, True)]
        for key, value in (
            ("schema_version", 2), ("schema_version", True), ("pull_request", False),
            ("pull_request", 0), ("candidate_sha", "HEAD"), ("base_sha", "A" * 40),
            ("subjects", []), ("subjects", request()["subjects"] * 2), ("findings", "pass"),
            ("subjects", [{"case_id": "unknown", "subject": "fixture"}]),
            ("candidate_sha", "b" * 40 + "\n"), ("repository", "owner/repo\n"),
        ):
            cases.append(({**request(), key: value}, False))
        for extra in ("program", "expected_members", "pass", "trusted",
                      "cleanup_confirmed", "_on_cleanup",
                      "execution_inputs", "blocked_by", "evidence"):
            cases.append(({**request(), extra: "injected"}, False))
        for data, expected in cases:
            with self.subTest(data=data):
                try:
                    model.validate_request(data)
                    accepted = True
                except (ValueError, TypeError):
                    accepted = False
                self.assertEqual(accepted, expected)
                self.assertEqual(validator.is_valid(data), expected)

    def test_finding_and_scope_semantic_joins(self):
        valid = request()
        valid["findings"] = [{
            "finding_id": "finding-1", **valid["subjects"][0],
            "family": "wire", "reported_member": "validators:review-session",
        }]
        model.validate_request(valid)
        for field, value in (("subject", "other"), ("family", "other"),
                             ("family", []), ("family", {}), ("family", None),
                             ("reported_member", ""), ("case_id", "TC-OTHER-001")):
            data = copy.deepcopy(valid)
            data["findings"][0][field] = value
            with self.assertRaises(model.ReviewError):
                model.validate_request(data)
        valid["findings"] *= 2
        with self.assertRaisesRegex(model.ReviewError, "duplicate"):
            model.validate_request(valid)


def fact(number, head="b" * 40):
    return model.ReviewFact(str(number), head, "actual-bot", "COMMENTED",
                            f"2026-01-01T00:00:{number + 10:02d}Z",
                            "Complete review, including suppressed findings.", ())


def candidate_fixture(repo, *, support_paths=0, extra_head_paths=0, include_symlink=False):
    prefix = "candidate-review-paths"
    base_changes = {
        f"{prefix}/modify.txt": "base revision\n",
        f"{prefix}/delete.txt": "delete from base\n",
        f"{prefix}/mode.sh": "#!/bin/sh\necho shared\n",
    }
    support = []
    for number in range(support_paths):
        path = f"{prefix}/support-{number:03d}.txt"
        base_changes[path] = f"support {number}\n"
        support.append(path)
    base = repo.commit(base_changes, parent=repo.base)
    git(repo.root, "reset", "--hard", base)
    root = repo.root / prefix
    (root / "modify.txt").write_text("head revision\n")
    (root / "delete.txt").unlink()
    (root / "added.txt").write_text("added in head\n")
    extra = []
    for number in range(extra_head_paths):
        path = root / f"capacity-{number:03d}.txt"
        path.write_text(f"capacity {number}\n")
        extra.append(f"{prefix}/{path.name}")
    if include_symlink:
        os.symlink("modify.txt", root / "unsupported-link")
    os.chmod(root / "mode.sh", 0o755)
    git(repo.root, "add", "-A")
    git(repo.root, "update-index", "--chmod=+x", f"{prefix}/mode.sh")
    git(repo.root, "commit", "-qm", "candidate path coverage fixture")
    head = git(repo.root, "rev-parse", "HEAD")
    git(repo.root, "reset", "--hard", repo.base)
    return {
        "base": base,
        "head": head,
        "paths": {
            "added": f"{prefix}/added.txt",
            "deleted": f"{prefix}/delete.txt",
            "mode": f"{prefix}/mode.sh",
            "modified": f"{prefix}/modify.txt",
            "symlink": f"{prefix}/unsupported-link" if include_symlink else None,
        },
        "support_paths": tuple(support),
        "extra_paths": tuple(extra),
    }


class WrappedCandidateReader:
    review_candidate_reader = True

    def __init__(self, delegate, *, mutate=None, failure=None):
        self.delegate = delegate
        self.base_tree = delegate.base_tree
        self.head_tree = delegate.head_tree
        self.candidate_binding = dict(delegate.candidate_binding)
        self.mutate = mutate
        self.failure = failure
        self.calls = 0

    @property
    def root(self):
        return Path(self.candidate_binding["resolved_root"])

    @property
    def base(self):
        return self.candidate_binding["base"]

    @property
    def head(self):
        return self.candidate_binding["head"]

    def preview(self, *args, **kwargs):
        return self.delegate.preview(*args, **kwargs)

    def describe(self, *args, **kwargs):
        return self.delegate.describe(*args, **kwargs)

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        result = dict(self.delegate(*args, **kwargs))
        return self.mutate(result) if self.mutate is not None else result


class CandidateCoverageTests(unittest.TestCase):
    def start_session(self, tools, base, head, *, max_files=200, readers=None):
        data = request(base=base, head=head)
        scope = frozenset({tools.model.subject_key(data["subjects"][0])})
        session = tools.model.ReviewSession(
            "coordinator", "implementer", scope, head,
            identity=("owner/repo", 1, base),
            owners=tools.model.ReviewOwnership(),
            readers=readers or {"read-candidate": tools.candidate_reader(base, head)})
        runtime = Runtime(head, scope)
        session.begin(runtime, "reviewer", max_files=max_files)
        return session, runtime

    def test_real_git_candidate_path_coverage_tracks_exact_bytes_and_immutable_summaries(self):
        with snapshot() as repo:
            fixture = candidate_fixture(repo, support_paths=1)
            tools = gate.ReviewTools(gate.GitTree(repo.root, fixture["head"]), repo.root)
            readers = {
                "read-candidate": tools.candidate_reader(fixture["base"], fixture["head"]),
                "read-evidence": lambda: {"reviewed_paths": ["invented"]},
            }
            session, runtime = self.start_session(
                tools, fixture["base"], fixture["head"], max_files=5, readers=readers)
            git(repo.root, "reset", "--hard", fixture["head"])
            (repo.root / fixture["paths"]["modified"]).write_text("working tree drift\n")
            git(repo.root, "add", fixture["paths"]["modified"])
            modified_base = session.read_action("read-candidate", fixture["paths"]["modified"], "base")
            modified_head = session.read_action("read-candidate", fixture["paths"]["modified"])
            added_head = session.read_action("read-candidate", fixture["paths"]["added"])
            deleted_base = session.read_action("read-candidate", fixture["paths"]["deleted"], "base")
            mode_base = session.read_action("read-candidate", fixture["paths"]["mode"], "base")
            mode_head = session.read_action("read-candidate", fixture["paths"]["mode"])
            support_head = session.read_action("read-candidate", fixture["support_paths"][0])
            self.assertEqual(modified_base.data, b"base revision\n")
            self.assertEqual(modified_head.data, b"head revision\n")
            self.assertEqual(added_head.data, b"added in head\n")
            self.assertEqual(deleted_base.data, b"delete from base\n")
            self.assertEqual(mode_base.data, mode_head.data)
            self.assertEqual(mode_base.oid, mode_head.oid)
            self.assertEqual((mode_base.mode, mode_head.mode), ("100644", "100755"))
            self.assertEqual(support_head.data, b"support 0\n")
            runtime.result.files = 5
            runtime.result.reviewed_paths = [fixture["paths"]["added"]]
            report = session.finish(runtime)
            session.candidate_reads.clear()
            runtime.result.reviewed_paths = list(fixture["support_paths"])
            changes = tools.candidate_changes(
                fixture["base"], fixture["head"],
                paths=[fixture["paths"]["added"], fixture["paths"]["modified"],
                       fixture["paths"]["deleted"], fixture["paths"]["mode"]],
                require_both=(fixture["paths"]["modified"],))
            self.assertTrue(type(changes) is tools.model.CandidateRequirements)
            self.assertEqual(
                (changes.resolved_root, changes.base, changes.head),
                (str(repo.root.resolve()), fixture["base"], fixture["head"]),
            )
            coverage = tools.model.require_candidate_path_coverage(report, changes)
            self.assertEqual(
                (coverage.resolved_root, coverage.base, coverage.head),
                (str(repo.root.resolve()), fixture["base"], fixture["head"]),
            )
            self.assertEqual(
                {(item.path, item.side, item.mode, item.oid) for item in coverage.reads},
                {(item.path, item.side, item.mode, item.oid) for item in report.candidate_reads},
            )
            self.assertEqual(report.candidate_root, str(repo.root.resolve()))
            self.assertEqual(report.candidate_base, fixture["base"])
            self.assertEqual(len(report.candidate_reads), 7)
            self.assertTrue(all(isinstance(item, tools.model.CandidateReadSummary)
                                for item in report.candidate_reads))
            with self.assertRaises(FrozenInstanceError):
                report.candidate_reads = ()
            with self.assertRaises(FrozenInstanceError):
                report.candidate_reads[0].path = "changed"

    def test_marked_candidate_readers_require_both_tree_bindings_before_launch(self):
        with snapshot() as repo:
            fixture = candidate_fixture(repo)
            tools = gate.ReviewTools(gate.GitTree(repo.root, fixture["head"]), repo.root)
            delegate = tools.candidate_reader(fixture["base"], fixture["head"])
            for missing in ({"base_tree"}, {"head_tree"}, {"base_tree", "head_tree"}):
                class IncompleteReader:
                    review_candidate_reader = True

                    def __getattr__(self, name):
                        if name in missing:
                            raise AttributeError(name)
                        return getattr(delegate, name)

                    def __call__(self, *args, **kwargs):
                        return delegate(*args, **kwargs)

                with self.subTest(missing=missing), patch.object(Runtime, "start", return_value="task-1") as start:
                    with self.assertRaisesRegex(tools.model.ReviewError, "Git tree"):
                        self.start_session(
                            tools, fixture["base"], fixture["head"],
                            readers={"read-candidate": IncompleteReader()})
                    start.assert_not_called()

    def test_modified_path_default_coverage_needs_only_its_head_read(self):
        with snapshot() as repo:
            fixture = candidate_fixture(repo)
            tools = gate.ReviewTools(gate.GitTree(repo.root, fixture["head"]), repo.root)
            session, runtime = self.start_session(
                tools, fixture["base"], fixture["head"], max_files=1)
            path = fixture["paths"]["modified"]
            self.assertEqual(session.read_action("read-candidate", path).data, b"head revision\n")
            runtime.result.files = 1
            report = session.finish(runtime)
            coverage = tools.model.require_candidate_path_coverage(
                report, tools.candidate_changes(fixture["base"], fixture["head"], paths=[path]))
            self.assertEqual([(item.path, item.side) for item in coverage.reads], [(path, "head")])

    def test_counts_runtime_claims_and_read_evidence_do_not_supply_coverage(self):
        with snapshot() as repo:
            fixture = candidate_fixture(repo, support_paths=4)
            tools = gate.ReviewTools(gate.GitTree(repo.root, fixture["head"]), repo.root)
            changes = tools.candidate_changes(
                fixture["base"], fixture["head"],
                paths=[fixture["paths"]["added"], fixture["paths"]["modified"],
                       fixture["paths"]["deleted"], fixture["paths"]["mode"]])
            runtime_claim = [
                fixture["paths"]["added"], fixture["paths"]["modified"],
                fixture["paths"]["deleted"], fixture["paths"]["mode"],
            ]
            session, runtime = self.start_session(tools, fixture["base"], fixture["head"], max_files=4)
            runtime.result.files = len(runtime_claim)
            runtime.result.reviewed_paths = list(runtime_claim)
            report = session.finish(runtime)
            self.assertEqual(tools.model.candidate_coverage(report).reads, ())
            with self.assertRaisesRegex(
                    tools.model.ReviewError, "missing candidate path coverage"):
                tools.model.require_candidate_path_coverage(report, changes)
            readers = {
                "read-candidate": tools.candidate_reader(fixture["base"], fixture["head"]),
                "read-evidence": lambda: {"reviewed_paths": list(runtime_claim)},
            }
            session, runtime = self.start_session(
                tools, fixture["base"], fixture["head"], max_files=4, readers=readers)
            session.read_action("read-evidence")
            for path in fixture["support_paths"]:
                session.read_action("read-candidate", path)
            runtime.result.files = len(runtime_claim)
            runtime.result.reviewed_paths = list(runtime_claim)
            report = session.finish(runtime)
            self.assertEqual({item.path for item in tools.model.candidate_coverage(report).reads},
                             set(fixture["support_paths"]))
            with self.assertRaisesRegex(
                    tools.model.ReviewError, "missing candidate path coverage"):
                tools.model.require_candidate_path_coverage(report, changes)
            fabricated = [{
                "path": fixture["support_paths"][0],
                "base_present": False,
                "base_mode": None,
                "base_kind": None,
                "base_oid": None,
                "head_present": True,
                "head_mode": next(item.mode for item in report.candidate_reads
                                  if item.path == fixture["support_paths"][0] and item.side == "head"),
                "head_kind": "blob",
                "head_oid": next(item.oid for item in report.candidate_reads
                                 if item.path == fixture["support_paths"][0] and item.side == "head"),
                "required_sides": [],
            }]
            with self.assertRaisesRegex(
                    tools.model.ReviewError, "immutable candidate path requirements"):
                tools.model.require_candidate_path_coverage(report, fabricated)
            stale_fixture = candidate_fixture(repo)
            stale = tools.candidate_changes(
                stale_fixture["base"], stale_fixture["head"], paths=[stale_fixture["paths"]["added"]])
            with self.assertRaisesRegex(tools.model.ReviewError, "pair mismatch"):
                tools.model.require_candidate_path_coverage(report, stale)

    def test_empty_path_requirements_cannot_qualify_a_review(self):
        with snapshot() as repo:
            fixture = candidate_fixture(repo)
            tools = gate.ReviewTools(gate.GitTree(repo.root, fixture["head"]), repo.root)
            for read_path in (None, fixture["paths"]["modified"]):
                with self.subTest(read_path=read_path):
                    session, runtime = self.start_session(tools, fixture["base"], fixture["head"])
                    if read_path is not None:
                        session.read_action("read-candidate", read_path)
                    report = session.finish(runtime)
                    self.assertEqual(len(report.candidate_reads), int(read_path is not None))
                    with self.assertRaisesRegex(tools.model.ReviewError, "nonempty"):
                        tools.model.require_candidate_path_coverage(
                            report, tools.model.CandidateRequirements(
                                str(repo.root.resolve()), fixture["base"], fixture["head"], ()))

    def test_deleted_head_absence_one_sided_mode_and_generic_reads_remain_uncovered(self):
        with snapshot() as repo:
            fixture = candidate_fixture(repo)
            tools = gate.ReviewTools(gate.GitTree(repo.root, fixture["head"]), repo.root)
            changes = tools.candidate_changes(
                fixture["base"], fixture["head"],
                paths=[fixture["paths"]["deleted"], fixture["paths"]["mode"]],
                require_both=(fixture["paths"]["mode"],))
            session, runtime = self.start_session(tools, fixture["base"], fixture["head"], max_files=2)
            deleted_head = session.read_action("read-candidate", fixture["paths"]["deleted"])
            self.assertFalse(deleted_head.present)
            session.read_action("read-candidate", fixture["paths"]["mode"])
            runtime.result.files = 2
            report = session.finish(runtime)
            with self.assertRaisesRegex(
                    tools.model.ReviewError, "missing candidate path coverage"):
                tools.model.require_candidate_path_coverage(report, changes)
            data = request(base=fixture["base"], head=fixture["head"])
            scope = frozenset({tools.model.subject_key(data["subjects"][0])})
            generic = tools.model.ReviewSession(
                "coordinator", "implementer", scope, fixture["head"],
                identity=("owner/repo", 1, fixture["base"]),
                owners=tools.model.ReviewOwnership(),
                readers={"read-candidate": lambda *args, **kwargs: {"generic": True}})
            runtime = Runtime(fixture["head"], scope)
            generic.begin(runtime, "reviewer")
            self.assertEqual(generic.read_action("read-candidate", fixture["paths"]["added"]),
                             {"generic": True})
            report = generic.finish(runtime)
            self.assertIsNone(tools.model.candidate_coverage(report))
            with self.assertRaisesRegex(
                    tools.model.ReviewError, "no trusted candidate path coverage"):
                tools.model.require_candidate_path_coverage(report, changes)

    def test_only_actual_immutable_reports_and_exact_summaries_count_for_coverage(self):
        with snapshot() as repo:
            fixture = candidate_fixture(repo)
            tools = gate.ReviewTools(gate.GitTree(repo.root, fixture["head"]), repo.root)
            session, runtime = self.start_session(tools, fixture["base"], fixture["head"], max_files=1)
            session.read_action("read-candidate", fixture["paths"]["added"])
            runtime.result.files = 1
            report = session.finish(runtime)
            changes = tools.candidate_changes(
                fixture["base"], fixture["head"], paths=[fixture["paths"]["added"]])
            tools.model.require_candidate_path_coverage(report, changes)
            namespace = SimpleNamespace(
                head=report.head,
                candidate_root=report.candidate_root,
                candidate_base=report.candidate_base,
                candidate_reads=report.candidate_reads,
                completed=report.completed,
                read_only=report.read_only,
                role=report.role,
            )
            with self.assertRaisesRegex(tools.model.ReviewError, "immutable review report"):
                tools.model.require_candidate_path_coverage(namespace, changes)
            raw = [{
                "path": fixture["paths"]["added"],
                "base_present": False,
                "base_mode": None,
                "base_kind": None,
                "base_oid": None,
                "head_present": True,
                "head_mode": report.candidate_reads[0].mode,
                "head_kind": "blob",
                "head_oid": report.candidate_reads[0].oid,
                "required_sides": [],
            }]
            with self.assertRaisesRegex(tools.model.ReviewError, "immutable candidate path requirements"):
                tools.model.require_candidate_path_coverage(report, raw)
            with self.assertRaisesRegex(tools.model.ReviewError, "immutable candidate path requirements"):
                tools.model.require_candidate_path_coverage(
                    report, SimpleNamespace(
                        resolved_root=str(repo.root.resolve()),
                        base=fixture["base"], head=fixture["head"], changes=changes.changes))
            with self.assertRaises(FrozenInstanceError):
                changes.base = fixture["head"]
            with self.assertRaises(FrozenInstanceError):
                changes.changes[0].path = "changed"
            summary = report.candidate_reads[0]
            object.__setattr__(report, "candidate_reads", [summary])
            with self.assertRaisesRegex(tools.model.ReviewError, "immutable tuple"):
                tools.model.candidate_coverage(report)
            object.__setattr__(report, "candidate_reads", ({
                "path": summary.path,
                "side": summary.side,
                "revision": summary.revision,
                "present": summary.present,
                "mode": summary.mode,
                "oid": summary.oid,
            },))
            with self.assertRaisesRegex(tools.model.ReviewError, "exact candidate read summaries"):
                tools.model.candidate_coverage(report)

    def test_candidate_root_binding_freezes_begin_checkout_and_rejects_same_sha_other_root(self):
        with snapshot() as repo:
            fixture = candidate_fixture(repo)
            tools = gate.ReviewTools(gate.GitTree(repo.root, fixture["head"]), repo.root)
            changes = tools.candidate_changes(
                fixture["base"], fixture["head"], paths=[fixture["paths"]["added"]])
            session, runtime = self.start_session(tools, fixture["base"], fixture["head"], max_files=1)
            session.read_action("read-candidate", fixture["paths"]["added"])
            runtime.result.files = 1
            report = session.finish(runtime)
            coverage = tools.model.require_candidate_path_coverage(report, changes)
            self.assertEqual(coverage.resolved_root, str(repo.root.resolve()))
            with tempfile.TemporaryDirectory(prefix="review-root-binding-", dir=ROOT / "build") as directory:
                other_root = Path(directory) / "clone"
                subprocess.run(["git", "clone", "--quiet", str(repo.root), str(other_root)], check=True)
                other_tools = gate.ReviewTools(gate.GitTree(other_root, fixture["head"]), other_root)
                other_changes = other_tools.candidate_changes(
                    fixture["base"], fixture["head"], paths=[fixture["paths"]["added"]])
                with self.assertRaisesRegex(
                        tools.model.ReviewError, "immutable candidate path requirements"):
                    tools.model.require_candidate_path_coverage(report, other_changes)
                wrong_root = tools.model.CandidateRequirements(
                    str(other_root.resolve()), fixture["base"], fixture["head"],
                    tuple(tools.model.validate_candidate_change(item)
                          for item in other_changes.changes))
                with self.assertRaisesRegex(tools.model.ReviewError, "root mismatch"):
                    tools.model.require_candidate_path_coverage(report, wrong_root)
                for field, value in (
                    ("resolved_root", str(other_root.resolve())),
                    ("base", fixture["head"]),
                    ("head", fixture["base"]),
                ):
                    reader = WrappedCandidateReader(tools.candidate_reader(
                        fixture["base"], fixture["head"]))
                    session, _ = self.start_session(
                        tools, fixture["base"], fixture["head"], max_files=1,
                        readers={"read-candidate": reader})
                    reader.candidate_binding[field] = value
                    with self.subTest(field=field), self.assertRaisesRegex(
                            tools.model.ReviewError, "changed after review start"):
                        session.read_action("read-candidate", fixture["paths"]["added"])
                    self.assertEqual(reader.calls, 0)

    def test_prebegin_candidate_binding_tamper_is_rejected_or_assignment_raises(self):
        with snapshot() as repo:
            fixture = candidate_fixture(repo)
            tools = gate.ReviewTools(gate.GitTree(repo.root, fixture["head"]), repo.root)
            scratch = repo.root / "build"
            scratch.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="review-root-prebegin-", dir=scratch) as directory:
                other_root = Path(directory) / "clone"
                subprocess.run(["git", "clone", "--quiet", str(repo.root), str(other_root)], check=True)

                reader = tools.candidate_reader(fixture["base"], fixture["head"])
                original = (reader.root, reader.base, reader.head)
                for field, value in (
                    ("root", other_root.resolve()),
                    ("base", fixture["head"]),
                    ("head", fixture["base"]),
                ):
                    with self.subTest(actual_reader_field=field):
                        with self.assertRaises(AttributeError):
                            setattr(reader, field, value)
                        self.assertEqual((reader.root, reader.base, reader.head), original)

                reader = WrappedCandidateReader(tools.candidate_reader(
                    fixture["base"], fixture["head"]))
                for field, value in (
                    ("resolved_root", str(other_root.resolve())),
                    ("base", fixture["head"]),
                    ("head", fixture["base"]),
                ):
                    data = request(base=fixture["base"], head=fixture["head"])
                    scope = frozenset({tools.model.subject_key(data["subjects"][0])})
                    session = tools.model.ReviewSession(
                        "coordinator", "implementer", scope, fixture["head"],
                        identity=("owner/repo", 1, fixture["base"]),
                        owners=tools.model.ReviewOwnership(),
                        readers={"read-candidate": reader},
                    )
                    runtime = Runtime(fixture["head"], scope)
                    reader.candidate_binding = dict(tools.candidate_reader(
                        fixture["base"], fixture["head"]).candidate_binding)
                    reader.candidate_binding[field] = value
                    with self.subTest(wrapped_binding_field=field), self.assertRaisesRegex(
                            tools.model.ReviewError, "immutable Git tree binding"):
                        session.begin(runtime, "reviewer", max_files=1)
                    self.assertEqual(runtime.calls, [])

    def test_distinct_failed_describe_paths_spend_capacity_before_describe_and_allow_retry(self):
        with snapshot() as repo:
            fixture = candidate_fixture(repo, support_paths=1)
            tools = gate.ReviewTools(gate.GitTree(repo.root, fixture["head"]), repo.root)
            delegate = tools.candidate_reader(fixture["base"], fixture["head"])
            reader = WrappedCandidateReader(delegate)
            session, runtime = self.start_session(
                tools, fixture["base"], fixture["head"], max_files=2,
                readers={"read-candidate": reader})
            failing = {fixture["paths"]["modified"], fixture["paths"]["added"]}
            describe_calls = []

            def describe(path, side="head"):
                describe_calls.append((path, side))
                if path in failing:
                    raise OSError("describe failed")
                return delegate.describe(path, side)

            with patch.object(reader, "describe", side_effect=describe):
                with self.assertRaisesRegex(OSError, "describe failed"):
                    session.read_action("read-candidate", fixture["paths"]["modified"])
                self.assertEqual(reader.calls, 0)
                self.assertEqual(session.candidate_reads, {})
                self.assertEqual(session.attempted_candidate_paths, {fixture["paths"]["modified"]})
                with self.assertRaisesRegex(OSError, "describe failed"):
                    session.read_action("read-candidate", fixture["paths"]["modified"])
                self.assertEqual(session.attempted_candidate_paths, {fixture["paths"]["modified"]})
                with self.assertRaisesRegex(OSError, "describe failed"):
                    session.read_action("read-candidate", fixture["paths"]["added"])
                self.assertEqual(
                    session.attempted_candidate_paths,
                    {fixture["paths"]["modified"], fixture["paths"]["added"]},
                )
                with self.assertRaisesRegex(tools.model.ReviewError, "budget exceeded"):
                    session.read_action("read-candidate", fixture["support_paths"][0])
                self.assertEqual(len(describe_calls), 3)
                failing.remove(fixture["paths"]["modified"])
                observed = session.read_action("read-candidate", fixture["paths"]["modified"])
                self.assertEqual(observed.data, b"head revision\n")
                self.assertEqual(session.attempted_candidate_paths,
                                 {fixture["paths"]["modified"], fixture["paths"]["added"]})
                self.assertEqual(len(session.candidate_reads), 1)
                runtime.result.files = 1
                report = session.finish(runtime)
                self.assertEqual(len(report.candidate_reads), 1)

    def test_distinct_failed_backend_paths_spend_capacity_before_backend_and_allow_retry(self):
        with snapshot() as repo:
            fixture = candidate_fixture(repo, support_paths=1)
            tools = gate.ReviewTools(gate.GitTree(repo.root, fixture["head"]), repo.root)
            delegate = tools.candidate_reader(fixture["base"], fixture["head"])
            reader = WrappedCandidateReader(delegate)
            session, runtime = self.start_session(
                tools, fixture["base"], fixture["head"], max_files=2,
                readers={"read-candidate": reader})
            failing = {fixture["paths"]["modified"], fixture["paths"]["added"]}
            describe_calls = []

            def describe(path, side="head"):
                describe_calls.append((path, side))
                return delegate.describe(path, side)

            def backend(row):
                if row["path"] in failing:
                    raise OSError("backend failed")
                return row

            reader.mutate = backend
            with patch.object(reader, "describe", side_effect=describe):
                with self.assertRaisesRegex(OSError, "backend failed"):
                    session.read_action("read-candidate", fixture["paths"]["modified"])
                self.assertEqual(reader.calls, 1)
                self.assertEqual(session.candidate_reads, {})
                self.assertEqual(session.attempted_candidate_paths, {fixture["paths"]["modified"]})
                with self.assertRaisesRegex(OSError, "backend failed"):
                    session.read_action("read-candidate", fixture["paths"]["modified"])
                self.assertEqual(reader.calls, 2)
                with self.assertRaisesRegex(OSError, "backend failed"):
                    session.read_action("read-candidate", fixture["paths"]["added"])
                self.assertEqual(reader.calls, 3)
                self.assertEqual(
                    session.attempted_candidate_paths,
                    {fixture["paths"]["modified"], fixture["paths"]["added"]},
                )
                with self.assertRaisesRegex(tools.model.ReviewError, "budget exceeded"):
                    session.read_action("read-candidate", fixture["support_paths"][0])
                self.assertEqual(reader.calls, 3)
                self.assertEqual(len(describe_calls), 3)
                failing.remove(fixture["paths"]["modified"])
                observed = session.read_action("read-candidate", fixture["paths"]["modified"])
                self.assertEqual(reader.calls, 4)
                self.assertEqual(observed.data, b"head revision\n")
                self.assertEqual(len(session.candidate_reads), 1)
                runtime.result.files = 1
                report = session.finish(runtime)
                self.assertEqual(len(report.candidate_reads), 1)

    def test_same_side_repeats_preserve_coverage_and_reject_changed_duplicates(self):
        with snapshot() as repo:
            fixture = candidate_fixture(repo, support_paths=1)
            tools = gate.ReviewTools(gate.GitTree(repo.root, fixture["head"]), repo.root)
            delegate = tools.candidate_reader(fixture["base"], fixture["head"])
            reader = WrappedCandidateReader(delegate)
            session, runtime = self.start_session(
                tools, fixture["base"], fixture["head"], max_files=2,
                readers={"read-candidate": reader})
            path = fixture["paths"]["modified"]
            first = session.read_action("read-candidate", path)
            self.assertEqual(session.read_action("read-candidate", path, "head"), first)
            self.assertEqual(len(session.candidate_reads), 1)
            session.read_action("read-candidate", fixture["paths"]["added"])
            self.assertEqual(reader.calls, 3)
            with self.assertRaisesRegex(tools.model.ReviewError, "budget exceeded"):
                session.read_action("read-candidate", fixture["support_paths"][0])
            self.assertEqual(reader.calls, 3)
            runtime.result.files = 2
            report = session.finish(runtime)
            self.assertEqual(len(report.candidate_reads), 2)
            self.assertEqual(sum(item.path == path and item.side == "head"
                                 for item in report.candidate_reads), 1)

            reader = WrappedCandidateReader(delegate)
            session, _ = self.start_session(
                tools, fixture["base"], fixture["head"], max_files=1,
                readers={"read-candidate": reader})
            original = session.read_action("read-candidate", path)
            reader.mutate = lambda row: {**row, "mode": "100755"}
            with patch.object(reader, "describe", side_effect=lambda *args, **kwargs: {
                **delegate.describe(*args, **kwargs), "mode": "100755",
            }):
                with self.assertRaisesRegex(tools.model.ReviewError, "changed across duplicate"):
                    session.read_action("read-candidate", path)
            self.assertEqual(reader.calls, 2)
            self.assertEqual(session.candidate_reads, {(path, "head"): original.summary()})

    def test_candidate_reads_reject_invalid_results_and_budget_before_backend_read(self):
        with snapshot() as repo:
            fixture = candidate_fixture(repo, extra_head_paths=201, include_symlink=True)
            tools = gate.ReviewTools(gate.GitTree(repo.root, fixture["head"]), repo.root)
            delegate = tools.candidate_reader(fixture["base"], fixture["head"])
            budget_reader = WrappedCandidateReader(delegate)
            session, _ = self.start_session(
                tools, fixture["base"], fixture["head"], max_files=200,
                readers={"read-candidate": budget_reader})
            for path in fixture["extra_paths"][:200]:
                session.read_action("read-candidate", path)
            self.assertEqual(budget_reader.calls, 200)
            with self.assertRaisesRegex(tools.model.ReviewError, "budget exceeded"):
                session.read_action("read-candidate", fixture["extra_paths"][200])
            self.assertEqual(budget_reader.calls, 200)
            session, _ = self.start_session(
                tools, fixture["base"], fixture["head"], max_files=1,
                readers={"read-candidate": WrappedCandidateReader(delegate)})
            session.read_action("read-candidate", fixture["paths"]["modified"])
            session.read_action("read-candidate", fixture["paths"]["modified"], "base")
            with self.assertRaisesRegex(tools.model.ReviewError, "budget exceeded"):
                session.read_action("read-candidate", fixture["paths"]["added"])
            for path in ("/absolute.txt", "../escape.txt",
                         "candidate-review-paths/./added.txt", "candidate-review-paths//added.txt",
                         "candidate-review-paths/bad\0.txt"):
                with self.subTest(boundary="model", path=path), self.assertRaises(ValueError):
                    tools.model.repo_path(path, "candidate path")
                reader = WrappedCandidateReader(delegate)
                session, _ = self.start_session(
                    tools, fixture["base"], fixture["head"], max_files=1,
                    readers={"read-candidate": reader})
                with self.subTest(path=path), self.assertRaisesRegex(
                        ValueError, "candidate path"):
                    session.read_action("read-candidate", path)
                self.assertEqual(reader.calls, 0)
            for label, reader, error in (
                ("failure", WrappedCandidateReader(delegate, failure=OSError("backend failed")),
                 OSError),
                ("wrong-path", WrappedCandidateReader(
                    delegate, mutate=lambda row: {**row, "path": fixture["paths"]["modified"]}),
                 tools.model.ReviewError),
                ("wrong-revision", WrappedCandidateReader(
                    delegate, mutate=lambda row: {**row, "revision": fixture["base"]}),
                 tools.model.ReviewError),
                ("wrong-mode", WrappedCandidateReader(
                    delegate, mutate=lambda row: {**row, "mode": "100755"}),
                 tools.model.ReviewError),
                ("wrong-oid", WrappedCandidateReader(
                    delegate, mutate=lambda row: {**row, "oid": "0" * 40}),
                 tools.model.ReviewError),
                ("wrong-bytes", WrappedCandidateReader(
                    delegate, mutate=lambda row: {**row, "data": b"tampered\n"}),
                 tools.model.ReviewError),
                ("false-absence", WrappedCandidateReader(
                    delegate, mutate=lambda row: {**row, "present": False, "mode": None,
                                                  "kind": None, "oid": None, "data": None}),
                 tools.model.ReviewError),
            ):
                session, _ = self.start_session(
                    tools, fixture["base"], fixture["head"], max_files=1,
                    readers={"read-candidate": reader})
                with self.subTest(label=label), self.assertRaises(error):
                    session.read_action("read-candidate", fixture["paths"]["added"])
                self.assertEqual(session.candidate_reads, {})
                self.assertEqual(session.attempted_candidate_paths, {fixture["paths"]["added"]})
            session, _ = self.start_session(
                tools, fixture["base"], fixture["head"], max_files=1,
                readers={"read-candidate": WrappedCandidateReader(delegate)})
            with self.assertRaisesRegex(
                    tools.model.ReviewError, "unsupported candidate summary mode"):
                session.read_action("read-candidate", fixture["paths"]["symlink"])
            self.assertEqual(session.candidate_reads, {})
            self.assertEqual(session.attempted_candidate_paths, {fixture["paths"]["symlink"]})


class ReviewFixtureGitTests(unittest.TestCase):
    def test_review_support_git_disables_background_maintenance_and_still_runs_real_git(self):
        with tempfile.TemporaryDirectory(prefix="review-support-git-", dir=ROOT / "build") as directory:
            repo = Path(directory)
            commands = []
            run = support.subprocess.run

            def observe(command, **kwargs):
                if command and command[0] == "/usr/bin/git":
                    commands.append(tuple(command))
                return run(command, **kwargs)

            with patch.object(support.subprocess, "run", side_effect=observe):
                support.git(repo, "init", "-q")
                support.git(repo, "config", "user.email", "fixture@example.invalid")
                support.git(repo, "config", "user.name", "Fixture Test")
                (repo / "tracked.txt").write_text("fixture\n")
                support.git(repo, "add", "tracked.txt")
                support.git(repo, "commit", "-qm", "fixture commit")
                head = support.git(repo, "rev-parse", "HEAD")

            self.assertRegex(head, r"^[0-9a-f]{40}$")
            expected_prefix = (
                "/usr/bin/git", "--no-optional-locks",
                "-c", "core.fsmonitor=false",
                "-c", "core.hooksPath=/dev/null",
                "-c", "gc.auto=0",
                "-c", "maintenance.auto=0",
                "-c", "gc.autoDetach=false",
                "-c", "maintenance.autoDetach=false",
                "-C", str(repo),
            )
            for command in commands:
                self.assertEqual(command[:len(expected_prefix)], expected_prefix)
            without_maintenance = tuple(
                item for item in expected_prefix if item != "maintenance.auto=0"
            )
            self.assertNotEqual(commands[0][:len(expected_prefix)], without_maintenance)


class RoundTests(unittest.TestCase):
    def test_formal_provisional_review_can_finish_once_without_an_extra_round(self):
        state = model.RoundState()
        observed = replace(fact(1), state="CHANGES_REQUESTED")
        finding = model.Finding(
            "finding", "case/subject", "wire", "validators:member",
            observed.head, "source.py", observed.id)
        state.observe(model.Triage(observed, "untriaged"))
        self.assertEqual(state.consecutive, 1)
        final = model.Triage(observed, "changes-requested", (finding,))
        state.observe(final)
        self.assertEqual((len(state.events), len(state.seen), state.consecutive), (1, 1, 1))
        self.assertEqual(state.handoffs[0]["findings"], ["finding"])
        with self.assertRaises(ValueError):
            state.observe(final)
        for changed in (replace(observed, head="c" * 40), replace(observed, actor="other"),
                        replace(observed, submitted_at="2026-01-01T00:00:59Z")):
            with self.assertRaises(ValueError):
                state.observe(model.Triage(changed, "untriaged"))

    def test_refresh_preserves_hold_and_disposition_count_boundary(self):
        state = model.RoundState()
        for number in (1, 2, 3):
            state.observe(model.Triage(fact(number), "changes-requested"))
        state.observe(model.Triage(replace(fact(1), body="edited"), "untriaged"))
        self.assertEqual(state.hold, ("3", "b" * 40))
        self.assertNotIn("1", {item["review_id"] for item in state.handoffs})
        state.observe(model.Triage(replace(fact(3), state="DISMISSED"), "dismissed"))
        self.assertEqual(state.hold, ("3", "b" * 40))
        state.observe(model.Triage(fact(4), "clean"))
        state.dispose(model.Disposition("3", "b" * 40, "coordinator", "redesign", "new plan"),
                      "coordinator")
        state.observe(model.Triage(fact(5), "changes-requested"))
        changed = replace(fact(2), body="historical edit")
        state.observe(model.Triage(changed, "untriaged"))
        state.observe(model.Triage(changed, "changes-requested"))
        self.assertEqual(state.consecutive, 1)
        self.assertIsNone(state.hold)

    def test_formal_change_requests_hold_even_before_full_content_triage(self):
        state = model.RoundState()
        for number in (1, 2, 3):
            state.observe(model.Triage(replace(fact(number), state="CHANGES_REQUESTED"), "untriaged"))
        self.assertEqual(state.hold, ("3", "b" * 40))

    def test_first_second_handoffs_and_sticky_third_hold(self):
        state = model.RoundState()
        for number in (1, 2, 3):
            state.observe(model.Triage(fact(number), "changes-requested"))
        self.assertEqual([item["consecutive"] for item in state.handoffs], [1, 2])
        self.assertEqual(state.hold, ("3", "b" * 40))
        state.observe(model.Triage(fact(4, "c" * 40), "clean"))
        state.observe(model.Triage(fact(5, "d" * 40), "changes-requested"))
        self.assertEqual(state.hold, ("3", "b" * 40))
        good = model.Disposition("3", "b" * 40, "coordinator", "redesign", "bounded replacement")
        for bad in (
            replace(good, held_head="c" * 40), replace(good, review_id="2"),
            replace(good, coordinator="implementer"), replace(good, action="patch-again"),
            replace(good, reason=""),
        ):
            with self.assertRaises(model.ReviewError):
                state.dispose(bad, "coordinator")
        state.dispose(good, "coordinator")
        self.assertIsNone(state.hold)
        with self.assertRaises(model.ReviewError):
            state.dispose(good, "coordinator")

    def test_clean_before_hold_resets_but_untriaged_does_not(self):
        state = model.RoundState()
        state.observe(model.Triage(fact(1), "changes-requested"))
        state.observe(model.Triage(fact(2), "untriaged"))
        self.assertEqual(state.consecutive, 1)
        state.observe(model.Triage(fact(3), "clean"))
        self.assertEqual(state.consecutive, 0)
        with self.assertRaises(model.ReviewError):
            state.observe(model.Triage(fact(3), "clean"))

    def test_no_natural_language_approval_inference(self):
        for state in ("COMMENTED", "APPROVED"):
            model.Triage(replace(fact(1), state=state), "clean").validate()
        for body in ("No issues found.", "### 🟢 Approval recommended",
                     "### 🔵 Needs a closer look", "", "zero new inline comments"):
            review = replace(fact(1), body=body)
            state = model.RoundState()
            state.observe(model.Triage(review, "untriaged"))
            self.assertEqual(state.consecutive, 0)
        with self.assertRaises(model.ReviewError):
            model.Triage(replace(fact(1), state="CHANGES_REQUESTED"), "clean").validate()
        with self.assertRaises(model.ReviewError):
            model.Triage(replace(fact(1), unresolved_threads=("thread",)), "clean").validate()


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.scope = frozenset({"TC-WORKFLOW-REVIEW-FAMILY-001/review-session"})
        self.runtime = Runtime("b" * 40, self.scope)
        self.time = 0
        self.effects = []
        self.session = model.ReviewSession(
            "coordinator", "implementer", self.scope, "b" * 40,
            clock=lambda: self.time, readers={"read-candidate": lambda: self.effects.append("read")})

    def test_coordinator_implemented_candidate_requires_independent_reviewer(self):
        owners = model.ReviewOwnership()
        session = model.ReviewSession(
            "coordinator", "coordinator", self.scope, "b" * 40,
            identity=("owner/repo", 1, "a" * 40), owners=owners, clock=lambda: self.time)
        with self.assertRaises(model.ReviewError):
            session.begin(self.runtime, "coordinator", duration=10)
        self.assertEqual(self.runtime.calls, [])
        self.assertEqual(owners.records, {})
        session.begin(self.runtime, "reviewer", duration=10)
        report = session.finish(self.runtime)
        self.assertEqual(report.owner, "reviewer")
        self.assertTrue(report.read_only)
        self.assertTrue(report.completed)
        self.assertEqual(session.lease.outcome, "completed")
        self.assertFalse(owners.records[id(session)][3])
        self.assertEqual([call[0] for call in self.runtime.calls], ["start", "read"])

    def test_existing_runtime_task_and_read_tool_boundary(self):
        self.session.begin(self.runtime, "reviewer", duration=30)
        self.session.read_action("read-candidate")
        for action in ("write", "bash", "push", "comment", "request-review", "dispatch-CI",
                       "merge", "stop", "abort", None, [], {}):
            with self.subTest(action=action), self.assertRaises(model.ReviewError):
                self.session.read_action(action)
        self.assertEqual(self.effects, ["read"])
        self.assertEqual(self.runtime.calls[0][1]["role"], "code-review")
        self.assertEqual(set(self.runtime.calls[0][1]["actions"]),
                         {"read-candidate", "read-evidence", "emit-report"})
        report = self.session.finish(self.runtime)
        self.assertIs(report, self.session.report)
        self.assertIsNot(report, self.runtime.result)
        self.assertEqual((report.task, report.head, report.owner),
                         (self.runtime.result.task, self.runtime.result.head, self.runtime.result.owner))
        self.assertEqual(self.session.lease.outcome, "completed")
        self.assertEqual([row[0] for row in self.runtime.calls], ["start", "read"])
        with self.assertRaises(model.ReviewError):
            self.session.read_action("read-candidate")

    def test_overlap_bounds_and_no_waiting(self):
        for coordinator, implementer, scope in (
            (None, "implementer", self.scope), ("coordinator", [], self.scope),
            (" ", "implementer", self.scope),
            *(("coordinator", "implementer", scope)
              for scope in (None, {}, "scope", ["scope"], frozenset({1}))),
        ):
            with self.subTest(scope=scope), self.assertRaises(model.ReviewError):
                model.ReviewSession(coordinator, implementer, scope, "b" * 40)
        for owner in ("implementer", "coordinator", None, True, 1, [], {}, "", " "):
            with self.assertRaises(model.ReviewError):
                self.session.begin(self.runtime, owner)
        self.session.begin(self.runtime, "reviewer", duration=10)
        with self.assertRaises(model.ReviewError):
            self.session.begin(self.runtime, "second-reviewer")
        self.assertEqual(len(self.runtime.calls), 1)
        self.time = 11
        with self.assertRaises(model.ReviewError):
            self.session.finish(self.runtime)
        self.assertEqual(len(self.runtime.calls), 2)
        self.assertTrue(self.session.lease.finished)
        self.assertEqual(self.session.lease.outcome, "timed-out")
        self.assertIsNone(self.session.report)

    def test_invalid_started_task_identity_unwinds_without_admitting_a_local_report(self):
        for task in (None, "", " \t\n", False, True, 0, 1, 1.0,
                     b"task", [], ["task"], {}, {"task": "id"}, ("task",)):
            with self.subTest(task=task):
                owners = model.ReviewOwnership()
                identity = ("owner/repo", 1, "a" * 40)
                session = model.ReviewSession(
                    "coordinator", "implementer", self.scope, "b" * 40,
                    identity=identity, owners=owners, clock=lambda: self.time)
                runtime = Runtime(session.head, session.scope)
                runtime.result.task = task
                finding = model.Finding(
                    "finding", next(iter(self.scope)), "wire", "validators:review-session",
                    session.head, "scripts/workflow_pilot/review_family.py", "local:" + str(task))
                runtime.result.findings = (finding,)
                try:
                    session.begin(runtime, "reviewer")
                except model.ReviewError:
                    pass
                else:
                    session.finish(runtime)
                    session.triage_local(finding.id, accepted=True, reason="Returned task finding")
                    self.fail("invalid task identity admitted " + repr(session.accepted[finding.id].review_id))
                self.assertEqual([call[0] for call in runtime.calls], ["start"])
                self.assertIsNone(session.lease)
                self.assertIsNone(session.report)
                self.assertEqual(session.accepted, {})
                self.assertEqual(owners.records, {})
                actual_task = "actual-runtime-task:42 "
                runtime.result.task = actual_task
                runtime.result.findings = (replace(finding, review_id="local:" + actual_task),)
                self.assertEqual(session.begin(runtime, "reviewer"), actual_task)
                self.assertEqual(session.lease.task, actual_task)
                self.assertEqual(session.finish(runtime).task, actual_task)
                session.triage_local(finding.id, accepted=True, reason="Actual task finding")
                session.validate_local_triage()
                fresh = model.ReviewSession(
                    "coordinator", "next-implementer", self.scope, session.head,
                    identity=identity, owners=owners)
                replacement = Runtime(fresh.head, fresh.scope)
                fresh.begin(replacement, "reviewer")
                fresh.finish(replacement)

    def owned_runtime(self, *, completed):
        self.time = 0
        owners = model.ReviewOwnership()
        identity = ("owner/repo", 1, "a" * 40)
        session = model.ReviewSession(
            "coordinator", "implementer", self.scope, "b" * 40,
            identity=identity, owners=owners, clock=lambda: self.time)
        runtime = Runtime(session.head, self.scope)
        runtime.result.completed = completed
        session.begin(runtime, "reviewer", duration=10)
        fresh = model.ReviewSession(
            "coordinator", "next-implementer", self.scope, session.head,
            identity=identity, owners=owners, clock=lambda: self.time)
        return session, runtime, fresh

    def test_finished_report_snapshots_runtime_values_and_nested_collections(self):
        for mutate_on_release in (False, True):
            with self.subTest(mutate_on_release=mutate_on_release):
                session, runtime, fresh = self.owned_runtime(completed=True)
                finding = model.Finding(
                    "finding", next(iter(self.scope)), "wire", "validators:review-session",
                    session.head, "scripts/workflow_pilot/review_family.py", "local:task-1")
                runtime.result.subjects = set(self.scope)
                runtime.result.actions = ["read-candidate", "emit-report"]
                runtime.result.findings = [finding]
                original = copy.deepcopy(runtime.result)

                def mutate():
                    runtime.result.subjects.clear()
                    runtime.result.actions[:] = ["push"]
                    runtime.result.findings[:] = [replace(finding, id="invented")]
                    runtime.result.completed_at = "2026-01-01T00:00:59Z"
                    runtime.result.started_at = "2026-01-01T00:00:58Z"
                    runtime.result.head = "c" * 40
                    runtime.result.owner = "implementer"
                    runtime.result.completed = False
                    runtime.result.read_only = False
                    runtime.result.files = 999

                release = session.owners.finish

                def finish_owner(owner):
                    release(owner)
                    if mutate_on_release:
                        mutate()

                with patch.object(session.owners, "finish", side_effect=finish_owner):
                    report = session.finish(runtime)
                if not mutate_on_release:
                    mutate()
                self.assertEqual(report.completed_at, original.completed_at)
                self.assertEqual(report.started_at, original.started_at)
                self.assertEqual(report.head, original.head)
                self.assertEqual(report.owner, original.owner)
                self.assertIs(report.completed, True)
                self.assertIs(report.read_only, True)
                self.assertEqual(report.files, original.files)
                self.assertEqual(report.subjects, frozenset(original.subjects))
                self.assertEqual(report.actions, frozenset(original.actions))
                self.assertEqual(report.findings, (finding,))
                self.assertIsNot(report, runtime.result)
                self.assertIs(report, session.report)
                session.triage_local(finding.id, accepted=True, reason="Observed original finding")
                session.validate_local_triage()
                self.assertEqual(session.accepted, {finding.id: finding})
                with self.assertRaises(FrozenInstanceError):
                    report.completed_at = runtime.result.completed_at
                with self.assertRaises(AttributeError):
                    report.actions.add("write")
                with self.assertRaises(AttributeError):
                    report.subjects.clear()
                with self.assertRaises(AttributeError):
                    report.findings.append(finding)
                with self.assertRaises(FrozenInstanceError):
                    report.findings[0].id = "changed"
                replacement = Runtime(fresh.head, fresh.scope)
                fresh.begin(replacement, "reviewer")
                fresh.finish(replacement)

    def test_head_advance_requires_actual_terminal_lease_release(self):
        for terminal in ("completed", "aborted", "timed-out"):
            with self.subTest(terminal=terminal):
                session, runtime, fresh = self.owned_runtime(completed=False)
                old_head = session.head
                for expired in (False, True):
                    self.time = 11 if expired else 1
                    with self.assertRaises(model.ReviewError):
                        session.advance("c" * 40)
                    self.assertEqual((session.head, session.lease.head), (old_head, old_head))
                    with self.assertRaises(model.ReviewError):
                        fresh.begin(Runtime(fresh.head, fresh.scope), "reviewer")
                self.time = 11 if terminal == "timed-out" else 1
                if terminal == "aborted":
                    stop = runtime.stop
                    runtime.stop = lambda task: runtime.calls.append(("stop", task)) or True
                    with self.assertRaises(model.ReviewError):
                        session.abort(runtime)
                    with self.assertRaises(model.ReviewError):
                        session.advance("c" * 40)
                    runtime.stop = stop
                    session.abort(runtime)
                else:
                    runtime.result.completed = True
                    if terminal == "timed-out":
                        with self.assertRaises(model.ReviewError):
                            session.finish(runtime)
                    else:
                        session.finish(runtime)
                self.assertEqual(session.lease.outcome, terminal)
                session.advance("c" * 40)
                self.assertEqual(session.head, "c" * 40)
                self.assertEqual(session.lease.head, old_head)
                replacement = Runtime(fresh.head, fresh.scope)
                fresh.begin(replacement, "reviewer")
                fresh.finish(replacement)

    def test_completed_review_requires_candidate_read_and_report_observations(self):
        for actions, accepted in (
            ((), False), (("read-evidence",), False),
            (("read-candidate",), False), (("emit-report",), False),
            (("read-candidate", "read-evidence"), False),
            (("emit-report", "read-evidence"), False),
            (("read-candidate", "emit-report"), True),
            (("emit-report", "read-evidence", "read-candidate"), True),
        ):
            with self.subTest(actions=actions):
                session, runtime, fresh = self.owned_runtime(completed=True)
                runtime.result.actions = actions
                if accepted:
                    self.assertIs(session.finish(runtime), session.report)
                    self.assertEqual(session.report.actions, frozenset(actions))
                    self.assertEqual(session.lease.outcome, "completed")
                else:
                    with self.assertRaises(model.ReviewError):
                        session.finish(runtime)
                    self.assertFalse(session.lease.finished)
                    self.assertIsNone(session.report)
                    self.assertEqual(session.local_findings, {})
                    session.abort(runtime)
                    self.assertEqual(session.lease.outcome, "aborted")
                    self.assertIsNone(session.report)
                    replacement = Runtime(session.head, self.scope)
                    fresh.begin(replacement, "reviewer")
                    fresh.finish(replacement)

    def test_expired_lease_retires_only_after_observed_terminal_completion(self):
        session, runtime, fresh = self.owned_runtime(completed=False)
        self.time = 11
        with self.assertRaises(model.ReviewError):
            session.finish(runtime)
        self.assertFalse(session.lease.finished)
        self.assertIsNone(session.report)
        with self.assertRaises(model.ReviewError):
            fresh.begin(runtime, "reviewer")
        runtime.result.completed = True
        with self.assertRaisesRegex(model.ReviewError, "duration bound"):
            session.finish(runtime)
        self.assertTrue(session.lease.finished)
        self.assertEqual(session.lease.outcome, "timed-out")
        self.assertIsNone(session.report)
        self.assertEqual(session.local_findings, {})
        self.assertEqual([row[0] for row in runtime.calls], ["start", "read", "read"])
        replacement = Runtime(session.head, self.scope)
        fresh.begin(replacement, "reviewer")
        fresh.finish(replacement)
        self.assertEqual(fresh.lease.outcome, "completed")

    def test_deadline_crossing_during_runtime_read_cannot_accept_a_report(self):
        session, runtime, _ = self.owned_runtime(completed=True)
        self.time = 9
        read = runtime.read

        def late_read(task):
            self.time = 11
            return read(task)

        runtime.read = late_read
        with self.assertRaisesRegex(model.ReviewError, "duration bound"):
            session.finish(runtime)
        self.assertTrue(session.lease.finished)
        self.assertEqual(session.lease.outcome, "timed-out")
        self.assertIsNone(session.report)

    def test_abort_requires_terminal_evidence_and_runtime_failures_retain_ownership(self):
        for case in ("delayed-stop", "stop-failure", "read-failure", "missing-stop", "unknown",
                     "wrong-head", "malformed-completion", "terminal-chronology", "failed-report"):
            with self.subTest(case=case):
                session, runtime, fresh = self.owned_runtime(completed=case == "failed-report")
                original = copy.copy(runtime.result)
                read, stop = runtime.read, runtime.stop
                self.time = 11 if case != "failed-report" else 1
                if case == "delayed-stop":
                    runtime.stop = lambda task: runtime.calls.append(("stop", task)) or True
                elif case == "stop-failure":
                    def fail_stop(task):
                        raise OSError("runtime stop failed")
                    runtime.stop = fail_stop
                elif case == "read-failure":
                    def fail_read(task):
                        raise OSError("runtime observation failed")
                    runtime.read = fail_read
                elif case == "missing-stop":
                    runtime.stop = None
                elif case == "unknown":
                    runtime.result = None
                elif case == "wrong-head":
                    runtime.result.head = "c" * 40
                    runtime.result.completed = True
                elif case == "malformed-completion":
                    runtime.result.completed = "stopped"
                elif case == "terminal-chronology":
                    runtime.result.completed = True
                    runtime.result.completed_at = None
                else:
                    runtime.result.read_only = False
                expected = OSError if case in {"stop-failure", "read-failure"} else model.ReviewError
                with self.assertRaises(expected):
                    session.finish(runtime) if case == "failed-report" else session.abort(runtime)
                self.assertFalse(session.lease.finished)
                self.assertIsNone(session.report)
                with self.assertRaises(model.ReviewError):
                    fresh.begin(Runtime(session.head, self.scope), "reviewer")
                runtime.read, runtime.stop = read, stop
                if case != "failed-report":
                    runtime.result = original
                    runtime.result.completed = case != "delayed-stop"
                session.abort(runtime)
                self.assertTrue(session.lease.finished)
                self.assertEqual(session.lease.outcome, "aborted")
                self.assertIsNone(session.report)
                self.assertEqual(session.local_findings, {})
                if case == "delayed-stop":
                    self.assertEqual([row[0] for row in runtime.calls[-3:]], ["read", "stop", "read"])
                if case == "failed-report":
                    self.assertNotIn("stop", [row[0] for row in runtime.calls])
                with self.assertRaises(model.ReviewError):
                    session.abort(runtime)
                with self.assertRaises(model.ReviewError):
                    session.finish(runtime)
                fresh.begin(Runtime(session.head, self.scope), "reviewer")

    def test_wrong_actual_task_results_are_rejected(self):
        for field, wrong in (("head", "c" * 40), ("task", "other"), ("owner", "implementer"),
                             ("role", "general-purpose"), ("completed", False),
                             ("subjects", ("other",)), ("actions", ("push",))):
            session = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40)
            runtime = Runtime("b" * 40, self.scope)
            session.begin(runtime, "reviewer")
            setattr(runtime.result, field, wrong)
            with self.subTest(field=field), self.assertRaises(model.ReviewError):
                session.finish(runtime)

    def test_runtime_completion_requires_actual_true_flags(self):
        missing = object()
        for field in ("completed", "read_only"):
            for value in (False, None, 0, 1, "", "incomplete", "true", [], {}, [True], missing):
                with self.subTest(field=field, value=value):
                    session = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40)
                    runtime = Runtime(session.head, self.scope)
                    session.begin(runtime, "reviewer")
                    if value is missing:
                        delattr(runtime.result, field)
                    else:
                        setattr(runtime.result, field, value)
                    with self.assertRaises(model.ReviewError):
                        session.finish(runtime)
                    self.assertFalse(session.lease.finished)
                    self.assertIsNone(session.report)
                    setattr(runtime.result, field, True)
                    session.finish(runtime)
                    self.assertTrue(session.lease.finished)

    def test_requested_and_returned_file_bounds_are_strict_and_retained(self):
        for keyword, ceiling in (("duration", 3600), ("max_files", 200)):
            for value in (-1, 0, ceiling + 1, True, False, 1.0, 1.5, "10", None, [], {}):
                with self.subTest(requested=keyword, value=value):
                    session = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40)
                    runtime = Runtime(session.head, self.scope)
                    with self.assertRaises(model.ReviewError):
                        session.begin(runtime, "reviewer", **{keyword: value})
                    self.assertEqual(runtime.calls, [])
                    self.assertIsNone(session.lease)
        for value in (-1, 11, 200, 201, True, False, 0.0, 3.0, 3.5, "3", None, [], {}):
            with self.subTest(returned=value):
                session = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40)
                runtime = Runtime(session.head, self.scope)
                session.begin(runtime, "reviewer", max_files=10)
                self.assertEqual(runtime.calls[0][1]["max_files"], 10)
                runtime.result.files = value
                with self.assertRaises(model.ReviewError):
                    session.finish(runtime)
                self.assertFalse(session.lease.finished)
        for bound, files in ((1, 0), (1, 1), (10, 10), (200, 200)):
            with self.subTest(bound=bound, files=files):
                session = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40)
                runtime = Runtime(session.head, self.scope)
                session.begin(runtime, "reviewer", max_files=bound)
                runtime.result.files = files
                session.finish(runtime)
                self.assertEqual(session.lease.max_files, bound)

    def test_malformed_runtime_reports_do_not_release_ownership(self):
        finding = model.Finding(
            "finding", next(iter(self.scope)), "wire", "validators:review-session",
            "b" * 40, "scripts/workflow_pilot/review_family.py", "local:task-1")
        malformed = [
            ("subjects", None), ("subjects", next(iter(self.scope))), ("subjects", ([],)),
            ("actions", None), ("actions", ""), ("actions", ([],)),
            ("findings", None), ("findings", {}), ("findings", "none"),
            ("findings", (replace(finding, family=[]),)),
            ("findings", (replace(finding, subject=[]),)),
            ("started_at", []), ("completed_at", "2025-01-01T00:00:00Z"),
        ]
        missing = object()
        malformed.extend((field, missing) for field in vars(self.runtime.result))
        malformed.extend((None, value) for value in (None, {}, "incomplete"))
        for field, value in malformed:
            with self.subTest(field=field, value=value):
                owners = model.ReviewOwnership()
                identity = ("owner/repo", 1, "a" * 40)
                session = model.ReviewSession(
                    "coordinator", "implementer", self.scope, "b" * 40,
                    identity=identity, owners=owners)
                other = model.ReviewSession(
                    "coordinator", "implementer", self.scope, "b" * 40,
                    identity=identity, owners=owners)
                runtime = Runtime(session.head, self.scope)
                valid = copy.copy(runtime.result)
                session.begin(runtime, "reviewer")
                if field is None:
                    runtime.result = value
                elif value is missing:
                    delattr(runtime.result, field)
                else:
                    setattr(runtime.result, field, value)
                with self.assertRaises(model.ReviewError):
                    session.finish(runtime)
                self.assertFalse(session.lease.finished)
                self.assertIsNone(session.report)
                self.assertEqual(session.local_findings, {})
                with self.assertRaises(model.ReviewError):
                    other.begin(runtime, "other-reviewer")
                runtime.result = valid
                session.finish(runtime)
                other.begin(runtime, "other-reviewer")

    def test_local_report_findings_are_typed_unique_and_task_bound(self):
        finding = model.Finding(
            "finding-1", next(iter(self.scope)), "wire", "validators:review-session",
            "b" * 40, "scripts/workflow_pilot/review_family.py", "local:task-1")
        for findings in (
            ({},), (replace(finding, id=""),), (finding, finding),
            (replace(finding, origin="c" * 40),),
            (replace(finding, subject="other"),),
            (replace(finding, review_id="other-task"),),
        ):
            with self.subTest(findings=findings):
                session = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40)
                runtime = Runtime("b" * 40, self.scope)
                runtime.result.findings = findings
                session.begin(runtime, "reviewer")
                with self.assertRaises(ValueError):
                    session.finish(runtime)

    def test_local_decisions_are_complete_reasoned_and_preserve_observed_findings(self):
        finding = model.Finding(
            "finding-1", next(iter(self.scope)), "wire", "validators:review-session",
            "b" * 40, "scripts/workflow_pilot/review_family.py", "local:task-1")
        self.runtime.result.findings = (finding,)
        self.session.begin(self.runtime, "reviewer")
        self.session.finish(self.runtime)
        self.runtime.result.findings = ()
        with self.assertRaisesRegex(ValueError, "local.*triage"):
            self.session.validate_local_triage()
        for finding_id, accepted, reason in (
            ("other", False, "unknown finding"), (finding.id, "yes", "not a boolean"),
            (finding.id, False, " "),
        ):
            with self.assertRaises(ValueError):
                self.session.triage_local(finding_id, accepted=accepted, reason=reason)
        self.session.triage_local(finding.id, accepted=False, reason="Coordinator rejected the finding")
        self.session.validate_local_triage()
        self.assertEqual(self.session.accepted, {})

    def test_existing_commit_publication_does_not_clear_hold(self):
        self.session.rounds.hold = ("3", "b" * 40)
        self.session.advance("c" * 40)
        self.assertEqual(self.session.head, "c" * 40)
        self.assertEqual(self.session.rounds.hold, ("3", "b" * 40))

    def test_shared_coordinator_index_prevents_overlapping_sessions(self):
        owners = model.ReviewOwnership()
        identity = ("owner/repo", 1, "a" * 40)
        first = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40,
                                    identity=identity, owners=owners)
        second = model.ReviewSession("coordinator", "other-implementer", self.scope, "b" * 40,
                                     identity=identity, owners=owners)
        first.begin(self.runtime, "reviewer")
        with self.assertRaisesRegex(model.ReviewError, "overlapping"):
            second.begin(Runtime("b" * 40, self.scope), "second-reviewer")
        first.finish(self.runtime)
        second.begin(Runtime("b" * 40, self.scope), "second-reviewer")

    def test_one_active_pr_head_owner_ignores_scope_but_releases_completed_work(self):
        identity = ("owner/repo", 1, "a" * 40)
        disjoint = frozenset({"TC-CORE-004/generated-eventlists"})
        for next_identity, head in (
            (identity, "b" * 40), (identity, "c" * 40),
            (("owner/repo", 2, "a" * 40), "b" * 40),
            (("other/repo", 1, "a" * 40), "b" * 40),
            (("owner/repo", 1, "d" * 40), "c" * 40),
        ):
            with self.subTest(identity=next_identity, head=head):
                owners = model.ReviewOwnership()
                first = model.ReviewSession(
                    "coordinator", "implementer", self.scope, "b" * 40,
                    identity=identity, owners=owners)
                runtime = Runtime(first.head, self.scope)
                first.begin(runtime, "reviewer")
                second = model.ReviewSession(
                    "coordinator", "other-implementer", disjoint, head,
                    identity=next_identity, owners=owners)
                next_runtime = Runtime(head, disjoint)
                with self.assertRaises(model.ReviewError):
                    second.begin(next_runtime, "reviewer")
                self.assertEqual(next_runtime.calls, [])
                self.assertIsNone(second.lease)
                first.finish(runtime)
                second.begin(next_runtime, "reviewer")
                second.finish(next_runtime)
        owners = model.ReviewOwnership()
        first = model.ReviewSession("coordinator", "implementer", self.scope, "b" * 40,
                                    identity=identity, owners=owners)
        first.begin(self.runtime, "reviewer")
        for other_identity, head in ((("owner/repo", 2, "a" * 40), "c" * 40),
                                     (("other/repo", 1, "a" * 40), "d" * 40)):
            independent = model.ReviewSession(
                "coordinator", "other-implementer", self.scope, head,
                identity=other_identity, owners=owners)
            runtime = Runtime(head, self.scope)
            independent.begin(runtime, "reviewer")
            independent.finish(runtime)
        first.finish(self.runtime)


class MemberTests(unittest.TestCase):
    def test_every_family_requires_all_roles(self):
        for family, roles in model.FAMILIES.items():
            members = tuple(model.Obligation(
                "case/subject", family, role + ":member", role, "producer", "consumer",
                "typed representation", "revalidate", "probe-" + role, "host",
                ("positive", "adversarial"), ("source.c",)) for role in roles)
            model.validate_members(members)
            for index in range(len(members)):
                with self.subTest(family=family, missing=roles[index]), self.assertRaises(model.ReviewError):
                    model.validate_members(members[:index] + members[index + 1:])
            with self.assertRaises(model.ReviewError):
                model.validate_members(members + members[:1])


class ExistingMetricTests(unittest.TestCase):
    def test_v1_baseline_metrics_and_actual_coordination_event_delta(self):
        from scripts.workflow_pilot import reporter

        fixture = reporter.load_json(ROOT / reporter.BASELINE_FIXTURE_PATH)
        self.assertEqual(fixture["schema_version"], 1)
        before = reporter.validate_fixture(fixture)
        original = reporter.report_efficiency(before)
        start, end = (reporter.parse_time(fixture["window"][key], key) for key in ("start", "end"))
        observed = next(item for item in fixture["events"] if "pr_number" in item
                        and start <= reporter.parse_time(item["occurred_at"], "event") <= end)
        event = {"id": "synthetic:coordination-observation", "type": "pilot_coordination",
                 "pr_number": observed["pr_number"], "occurred_at": observed["occurred_at"],
                 "minutes": 7}
        events = reporter.validate_events({"events": [event]}, before["pull_requests"])
        after = {**before, "events": {**before["events"], **events}}
        measured = reporter.report_efficiency(after)
        self.assertEqual(measured["pilot_coordination_minutes"],
                         original["pilot_coordination_minutes"] + 7)
        self.assertEqual(measured["net_saved_minutes"], original["net_saved_minutes"] - 7)
        self.assertEqual(reporter.report_reviews(before), reporter.report_reviews(after))
        event["minutes"] = -1
        with self.assertRaises(reporter.PilotDataError):
            reporter.validate_events({"events": [event]}, before["pull_requests"])


if __name__ == "__main__":
    unittest.main()
