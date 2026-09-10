"""The ownership verifier's existing #178 coordinator-check integration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from scripts.workflow_pilot import agent_handoff, raw_diff_check
from scripts.workflow_pilot.review_family import MAX_REVIEW_FILES
from .authority import parse_json
from .budget import MakeProbeError


CHECK_ID = "validation-ownership"
REVIEW_CASE_ID = "TC-WORKFLOW-GATE-OWNERSHIP-001"
REVIEW_CHECKER_PATHS = (
    "scripts/validation_ownership/ci_verifier.py",
    "scripts/validation_ownership/coordinator_capture.py",
)
REVIEW_SCOPE_DOMAIN = b"fe8-validation-ownership-reviewed-scope-v1\0"
REVIEWED_EVIDENCE_PREFIX = "ownership-reviewed-"


def _sorted_scope(values, label, maximum=256, *, minimum=1):
    values = tuple(values)
    if (
        not minimum <= len(values) <= maximum
        or any(not isinstance(value, str) or not value for value in values)
        or tuple(sorted(set(values))) != values
    ):
        raise MakeProbeError(f"reviewed evolution requires a sorted exact {label} scope")
    return values


def reviewed_evolution_scope(checker_revision, paths, edge_ids, consumer_ids):
    if not re.fullmatch(r"[0-9a-f]{40}", checker_revision):
        raise MakeProbeError("reviewed evolution scope requires an exact checker revision")
    members = (
        ("paths", _sorted_scope(paths, "changed path", MAX_REVIEW_FILES)),
        ("edges", _sorted_scope(edge_ids, "changed edge", minimum=0)),
        ("consumers", _sorted_scope(consumer_ids, "affected consumer", minimum=0)),
    )
    subjects = {f"{REVIEW_CASE_ID}/checker:{checker_revision}"}
    for domain, values in members:
        payload = json.dumps(
            list(values),
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii")
        digest = hashlib.sha256(
            REVIEW_SCOPE_DOMAIN + domain.encode("ascii") + b"\0" + payload
        ).hexdigest()
        subjects.add(f"{REVIEW_CASE_ID}/{domain}-sha256:{digest}")
    return frozenset(subjects)


def reviewed_evolution_context(checker_revision, paths, edge_ids, consumer_ids, *,
                               repository, pull_request, base_sha, candidate_sha, worktree):
    return {
        "case_id": REVIEW_CASE_ID, "repository": repository, "pull_request": pull_request,
        "base_sha": base_sha, "candidate_sha": candidate_sha, "worktree": str(worktree),
        "checker_revision": checker_revision,
        "changed_paths": list(_sorted_scope(paths, "changed path", MAX_REVIEW_FILES)),
        "changed_edge_ids": list(_sorted_scope(edge_ids, "changed edge", minimum=0)),
        "affected_consumers": list(_sorted_scope(consumer_ids, "affected consumer", minimum=0)),
    }


@dataclass(frozen=True)
class ReviewedEvolutionQualification:
    repository: str
    pull_request: int
    base_sha: str
    candidate_sha: str
    worktree: Path
    checker_revision: str
    changed_paths: tuple[str, ...]
    changed_edge_ids: tuple[str, ...]
    affected_consumers: tuple[str, ...]
    coordinator_id: str
    git_identity: dict[str, Any]
    session: Any
    review_tools: Any

    def selection(self):
        return {
            "repository": self.repository,
            "pull_request": self.pull_request,
            "changed_paths": list(self.changed_paths),
            "changed_edge_ids": list(self.changed_edge_ids),
            "affected_consumers": list(self.affected_consumers),
        }

    def review_scope(self):
        return reviewed_evolution_scope(
            self.checker_revision,
            self.changed_paths,
            self.changed_edge_ids,
            self.affected_consumers,
        )

    def review_context(self):
        return reviewed_evolution_context(
            self.checker_revision, self.changed_paths, self.changed_edge_ids, self.affected_consumers,
            repository=self.repository, pull_request=self.pull_request, base_sha=self.base_sha,
            candidate_sha=self.candidate_sha, worktree=self.worktree,
        )

    def record(self):
        self.validate_review()
        report = self.session.report
        return {
            "schema_version": 1,
            "case_id": REVIEW_CASE_ID,
            **self.selection(),
            "base_sha": self.base_sha,
            "candidate_sha": self.candidate_sha,
            "worktree": str(self.worktree),
            "checker_revision": self.checker_revision,
            "review_scope": sorted(self.review_scope()),
            "review_task": str(report.task),
            "reviewer": report.owner,
            "review_started_at": report.started_at,
            "review_completed_at": report.completed_at,
            "coordinator_id": self.coordinator_id,
            "git_identity": self.git_identity,
        }

    def evidence_id(self):
        payload = self.record()
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        return REVIEWED_EVIDENCE_PREFIX + digest

    def validate_review(self):
        from scripts.workflow_pilot.review_family import encoded_review_context

        if (
            not isinstance(self.repository, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository)
            or type(self.pull_request) is not int
            or self.pull_request < 1
            or not isinstance(self.worktree, Path)
            or not self.worktree.is_absolute()
            or not isinstance(self.coordinator_id, str)
            or not self.coordinator_id
        ):
            raise MakeProbeError("reviewed evolution qualification has invalid coordinator identity")
        session = self.session
        report = getattr(session, "report", None)
        lease = getattr(session, "lease", None)
        owners = getattr(session, "owners", None)
        ownership = None if owners is None else owners.records.get(id(session))
        try:
            session.validate_local_triage()
        except (AttributeError, ValueError) as error:
            raise MakeProbeError("reviewed evolution requires complete independent review triage") from error
        if (
            session.identity != (self.repository, self.pull_request, self.base_sha)
            or session.head != self.candidate_sha
            or session.scope != self.review_scope()
            or report is None
            or lease is None
            or not lease.finished
            or lease.outcome != "completed"
            or report.subjects != self.review_scope()
            or report.owner in {session.coordinator, session.implementer}
            or report.files < len(self.changed_paths)
            or (lease.task, lease.owner, lease.head, lease.scope)
            != (report.task, report.owner, report.head, report.subjects)
            or ownership is None
            or ownership[3]
            or (ownership[0], ownership[1], ownership[2])
            != (session.identity, self.candidate_sha, self.review_scope())
        ):
            raise MakeProbeError("reviewed evolution lacks its actual independent review observation")
        context = encoded_review_context(self.review_context())
        if lease.context != context or report.context != context:
            raise MakeProbeError("reviewed evolution lacks its dispatched explicit review context")
        tools = self.review_tools
        if (
            getattr(getattr(tools, "tool_tree", None), "revision", None) != self.checker_revision
            or getattr(getattr(tools, "tool_tree", None), "root", None) != self.worktree
            or getattr(tools, "subject_root", None) != self.worktree
        ):
            raise MakeProbeError("reviewed evolution checker differs from the immutable review tool binding")
        cases = [
            case
            for case in getattr(tools, "catalog", {}).get("cases", ())
            if case.get("id") == REVIEW_CASE_ID
        ]
        if len(cases) != 1 or not cases[0].get("automation"):
            raise MakeProbeError("reviewed evolution lacks its immutable tester-case binding")
        for path in REVIEW_CHECKER_PATHS:
            tools.tool_tree.oid(path)
        try:
            tools.model.require_candidate_path_coverage(
                report, tools.candidate_changes(self.base_sha, self.candidate_sha, paths=self.changed_paths),
            )
        except (AttributeError, ValueError) as error:
            raise MakeProbeError(f"reviewed evolution lacks actual changed-path coverage: {error}") from error

    def validate_binding(self, state, record, pr):
        from scripts.workflow_pilot import adaptive_gate

        self.validate_review()
        if (
            state.get("coordinator_id") != self.coordinator_id
            or state.get("repository") != self.repository
            or adaptive_gate.find_candidate(state, adaptive_gate.candidate_identity(record)) is not record
            or adaptive_gate.candidate_identity(record)
            != (self.pull_request, self.candidate_sha, self.base_sha, pr.base_ref)
            or (pr.repository, pr.number, pr.head_sha)
            != (self.repository, self.pull_request, self.candidate_sha)
        ):
            raise MakeProbeError("reviewed evolution qualification differs from the actual candidate record")
        current = adaptive_gate._coordinator_git(state, record, pr, self.worktree)
        if current["identity"] != self.git_identity:
            raise MakeProbeError("reviewed evolution worktree identity changed")

    def validate_assignment(self, assignment, result_sha):
        self.validate_review()
        if (
            result_sha != self.candidate_sha
            or assignment.get("repository") != self.repository
            or assignment.get("pull_request") != self.pull_request
            or assignment.get("assigned_parent_sha") != self.base_sha
            or Path(assignment.get("allowed_worktree", "")).resolve() != self.worktree
            or assignment.get("review_qualification") != self.record()
        ):
            raise MakeProbeError("reviewed evolution capture differs from its actual assignment")


def qualify_reviewed_evolution(
    state,
    record,
    pr,
    worktree,
    session,
    review_tools,
    *,
    checker_revision,
    changed_paths,
    changed_edge_ids,
    affected_consumers,
):
    from scripts.workflow_pilot import adaptive_gate

    worktree = Path(worktree).resolve(strict=True)
    paths = _sorted_scope(changed_paths, "changed path")
    edges = _sorted_scope(changed_edge_ids, "changed edge", minimum=0)
    consumers = _sorted_scope(affected_consumers, "affected consumer", minimum=0)
    current = adaptive_gate._coordinator_git(state, record, pr, worktree)
    qualification = ReviewedEvolutionQualification(
        repository=pr.repository,
        pull_request=pr.number,
        base_sha=record["base_sha"],
        candidate_sha=pr.head_sha,
        worktree=worktree,
        checker_revision=checker_revision,
        changed_paths=paths,
        changed_edge_ids=edges,
        affected_consumers=consumers,
        coordinator_id=state["coordinator_id"],
        git_identity=current["identity"],
        session=session,
        review_tools=review_tools,
    )
    qualification.validate_binding(state, record, pr)
    return qualification


@dataclass(frozen=True)
class VerifierExpectation:
    repository_root: Path
    trusted_root: Path
    base_sha: str
    candidate_sha: str
    trusted_sha: str
    mode: str
    qualification: ReviewedEvolutionQualification | None = None

    def reviewed_evolution(self):
        return None if self.qualification is None else self.qualification.selection()

    def evidence_id(self):
        if self.mode != "reviewed-evolution":
            return "ownership"
        return self.qualification.evidence_id()

    def check_definition(self):
        return {"contract": "coordinator-check", "evidence_id": self.evidence_id(), "inputs": []}

    def validate(self):
        for value in (self.base_sha, self.candidate_sha, self.trusted_sha):
            if not re.fullmatch(r"[0-9a-f]{40}", value):
                raise MakeProbeError("verifier expectation requires exact SHA identities")
        if self.mode not in {"exact-base-pinned", "foundation-introduction", "reviewed-evolution"}:
            raise MakeProbeError("non-authoritative ownership result mode is not admissible")
        if self.mode == "exact-base-pinned" and self.trusted_sha != self.base_sha:
            raise MakeProbeError("exact-base source expectation differs from BASE")
        if self.mode == "reviewed-evolution":
            if not isinstance(self.qualification, ReviewedEvolutionQualification):
                raise MakeProbeError("reviewed evolution requires actual independent qualification")
            self.qualification.validate_review()
            if (
                self.base_sha != self.qualification.base_sha
                or self.candidate_sha != self.qualification.candidate_sha
                or self.trusted_sha != self.qualification.checker_revision
                or self.repository_root.resolve() != self.qualification.worktree
            ):
                raise MakeProbeError("reviewed evolution expectation differs from its qualification")
        elif self.qualification is not None:
            raise MakeProbeError("non-reviewed ownership expectations cannot declare reviewed evolution scope")
        if self.repository_root.resolve() == self.trusted_root.resolve():
            raise MakeProbeError("coordinator verifier source must be separate from candidate data")


def trusted_executor(expectation: VerifierExpectation):
    expectation.validate()
    def execute(current_assignment, result_sha):
        if current_assignment is not assignment or result_sha != expectation.candidate_sha:
            raise MakeProbeError("ownership capture changed its head/assignment binding")
        if expectation.qualification is not None:
            expectation.qualification.validate_assignment(current_assignment, result_sha)
        argv = [
            "/usr/bin/python3", "-I", "-S", "-B",
            str(expectation.trusted_root / "scripts/validation_ownership/ci_verifier.py"),
            "--trusted-root", str(expectation.trusted_root),
            "--repository-root", str(expectation.repository_root),
            "--base-sha", expectation.base_sha,
            "--candidate-sha", expectation.candidate_sha,
            "--trusted-sha", expectation.trusted_sha,
            "--expected-mode", expectation.mode,
        ]
        if expectation.mode == "reviewed-evolution":
            reviewed = expectation.reviewed_evolution()
            argv.extend(("--reviewed-repository", reviewed["repository"],
                         "--reviewed-pull-request", str(reviewed["pull_request"])))
            for key, flag in (
                ("changed_paths", "--reviewed-path"), ("changed_edge_ids", "--reviewed-edge"),
                ("affected_consumers", "--reviewed-consumer"),
            ):
                argv.extend(item for value in reviewed[key] for item in (flag, value))
        actual = raw_diff_check.run_process(
            argv, cwd=expectation.trusted_root, env=raw_diff_check.git_environment(),
            timeout=min(3600, current_assignment.get("max_lifetime_seconds", 3600)),
            max_bytes=raw_diff_check.MAX_BYTES,
        )
        if actual.returncode == 0:
            result = parse_json(actual.stdout, "captured ownership verifier result")
            expected = {
                "base_sha": expectation.base_sha, "candidate_sha": expectation.candidate_sha,
                "trusted_sha": expectation.trusted_sha, "mode": expectation.mode,
                "authority": {
                    "exact-base-pinned": "exact-base",
                    "foundation-introduction": "explicit-introduction",
                    "reviewed-evolution": "reviewed-evolution",
                }[expectation.mode],
            }
            if not isinstance(result, dict) or any(result.get(key) != value for key, value in expected.items()):
                raise MakeProbeError("captured verifier result differs from coordinator expectation")
            if result.get("candidate_trusted_changes") != []:
                raise MakeProbeError("captured candidate differs from its selected trusted source")
            if any(type(result.get(name)) is not int or result[name] < 1 for name in (
                "coverage_paths", "evidence_authorities", "trusted_package_files",
            )):
                raise MakeProbeError("captured ownership verification is incomplete")
            if expectation.mode == "reviewed-evolution":
                source_changes = result.get("trusted_source_changes")
                invalidation = result.get("review_invalidation")
                if not isinstance(source_changes, list) or not isinstance(invalidation, dict):
                    raise MakeProbeError("captured reviewed evolution lacks its actual change boundaries")
                source_changes = _sorted_scope(source_changes, "trusted source change", MAX_REVIEW_FILES, minimum=0)
                if (
                    result.get("reviewed_evolution") != expectation.reviewed_evolution()
                    or type(invalidation.get("invalidated")) is not bool
                    or invalidation["invalidated"] != bool(reviewed["changed_edge_ids"])
                    or not invalidation["invalidated"] and not source_changes
                    or invalidation.get("changed_edge_ids") != reviewed["changed_edge_ids"]
                    or not set(source_changes) <= set(reviewed["changed_paths"])
                ):
                    raise MakeProbeError("captured reviewed evolution result differs from coordinator expectation")
        return actual, dict.fromkeys(agent_handoff.METRICS)
    assignment = None

    def executor(current_assignment, result_sha):
        nonlocal assignment
        assignment = current_assignment
        return execute(current_assignment, result_sha)

    return executor


def capture(entry, expectation: VerifierExpectation):
    """Coordinator supplies the definition/source/BASE expectations, not candidate YAML."""
    expectation.validate()
    assignment = entry["assignment"]
    if expectation.qualification is not None:
        expectation.qualification.validate_assignment(assignment, expectation.candidate_sha)
    definition = assignment["required_checks"].get(CHECK_ID)
    if (
        definition is None
        or definition != expectation.check_definition()
        or Path(assignment["allowed_worktree"]).resolve() != expectation.repository_root.resolve()
    ):
        raise MakeProbeError("managed ownership admission lacks its coordinator check assignment")

    return agent_handoff.capture_check(
        entry, CHECK_ID, expectation.candidate_sha, trusted_executor=trusted_executor(expectation),
    )


def validate_handoff(state, result, expectation: VerifierExpectation):
    """Managed ownership handoff always captures the verifier before admission."""
    entry = agent_handoff.find_entry(state, result["assignment_id"])
    if result["result_sha"] != expectation.candidate_sha:
        raise MakeProbeError("ownership handoff differs from the expected candidate")
    capture(entry, expectation)
    return agent_handoff.validate_handoff(
        state, result, worktree=expectation.repository_root,
    )
