"""Bounded review convergence. Tool provenance belongs to the coordinator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any


FAMILIES = {
    "action": ("actions", "items", "targets"),
    "lifecycle": ("entries", "preservation", "resets", "terminals"),
    "wire": ("producers", "consumers", "validators", "replay", "stale-bindings"),
    "generated": ("owners", "outputs", "consumers", "drift-checks"),
    "resource": ("enabled", "disabled"),
}
READ_ACTIONS = frozenset({"read-candidate", "read-evidence", "emit-report"})
SHA_PATTERN = r"[0-9a-f]{40}"
NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}"
CASE_PATTERN = r"TC-[A-Z0-9]+(?:-[A-Z0-9]+)+"
MAX_SUBJECTS = 40
MAX_FINDINGS = 50
MAX_MEMBERS = 250
MAX_REQUEST_BYTES = 1024 * 1024
MAX_DETAIL = 2000
MAX_REVIEW_FILES = 200
MAX_REVIEW_SECONDS = 3600
EVIDENCE_KINDS = frozenset({"native", "arm-object", "parsed", "host"})
REVIEW_SIDES = ("base", "head")
REVIEW_BLOB_MODES = frozenset({"100644", "100755"})
_MISSING = object()


class ReviewError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReviewError(message)


def keys(value: Any, expected: tuple[str, ...], label: str) -> dict:
    require(isinstance(value, dict), f"{label} must be an object")
    require(set(value) == set(expected), f"{label} has missing or unknown fields")
    return value


def field_value(record: Any, name: str):
    if isinstance(record, dict):
        return record.get(name, _MISSING)
    return getattr(record, name, _MISSING)


def identifier(value: Any, pattern: str, label: str) -> str:
    require(isinstance(value, str) and re.fullmatch(pattern, value) is not None,
            f"invalid {label}")
    return value


def sha(value: Any) -> str:
    return identifier(value, SHA_PATTERN, "full lowercase Git SHA")


def integer(value):
    return type(value) is int or (type(value) is float and value.is_integer())


def repo_path(value: Any, label: str) -> str:
    require(isinstance(value, str) and bool(value), f"invalid {label}")
    candidate = PurePosixPath(value)
    require("\\" not in value and not candidate.is_absolute() and
            value not in {"", "."} and
            "." not in candidate.parts and ".." not in candidate.parts and
            candidate.as_posix() == value, f"invalid {label}")
    return value


def review_side(value: Any, label: str = "candidate side") -> str:
    require(isinstance(value, str) and value in REVIEW_SIDES, f"invalid {label}")
    return value


def git_mode(value: Any, label: str, *, regular_only=False) -> str:
    require(isinstance(value, str) and re.fullmatch(r"[0-7]{6}", value) is not None,
            f"invalid {label}")
    if regular_only:
        require(value in REVIEW_BLOB_MODES, f"unsupported {label}")
    return value


def resolved_repo_root(value: Any, label: str, *, must_exist: bool) -> str:
    require(isinstance(value, (str, os.PathLike)) and not isinstance(value, bytes),
            f"invalid {label}")
    try:
        candidate = Path(value).resolve(strict=must_exist)
    except (OSError, RuntimeError, TypeError) as error:
        raise ReviewError(f"invalid {label}") from error
    require(candidate.is_absolute() and (not must_exist or candidate.is_dir()),
            f"invalid {label}")
    return str(candidate)


def unique(values, label: str) -> None:
    require(len(values) == len(set(values)), f"duplicate {label}")


def timestamp(value):
    require(isinstance(value, str) and value.endswith("Z"), "UTC timestamp required")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise ReviewError("invalid UTC timestamp") from error


def parse_json(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for name, value in items:
            require(name not in result, f"duplicate JSON field {name}")
            result[name] = value
        return result

    def invalid(value):
        raise ReviewError(f"non-finite JSON value {value}")

    require(len(raw) <= MAX_REQUEST_BYTES, "review request exceeds size bound")
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ReviewError(f"invalid review JSON: {error}") from error


def subject_key(value: dict) -> str:
    return value["case_id"] + "/" + value["subject"]


def validate_request(value: Any) -> dict:
    keys(value, ("schema_version", "repository", "pull_request", "base_sha",
                 "candidate_sha", "subjects", "findings"), "request")
    require(integer(value["schema_version"]) and value["schema_version"] == 1,
            "unsupported review request schema")
    identifier(value["repository"], r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", "repository")
    require(integer(value["pull_request"]) and value["pull_request"] > 0,
            "invalid pull request")
    sha(value["base_sha"])
    sha(value["candidate_sha"])
    subjects = value["subjects"]
    findings = value["findings"]
    require(isinstance(subjects, list) and 0 < len(subjects) <= MAX_SUBJECTS,
            "subject bound exceeded or empty scope")
    require(isinstance(findings, list) and len(findings) <= MAX_FINDINGS,
            "finding bound exceeded")
    for subject in subjects:
        keys(subject, ("case_id", "subject"), "subject")
        identifier(subject["case_id"], CASE_PATTERN, "case ID")
        identifier(subject["subject"], NAME_PATTERN, "subject")
    scope = [subject_key(item) for item in subjects]
    unique(scope, "subjects")
    for finding in findings:
        keys(finding, ("finding_id", "case_id", "subject", "family",
                       "reported_member"), "finding")
        identifier(finding["finding_id"], NAME_PATTERN, "finding ID")
        identifier(finding["case_id"], CASE_PATTERN, "case ID")
        identifier(finding["subject"], NAME_PATTERN, "subject")
        identifier(finding["reported_member"], NAME_PATTERN, "member ID")
        require(subject_key(finding) in scope, "finding is outside accepted subject scope")
        require(isinstance(finding["family"], str) and finding["family"] in FAMILIES,
                "unknown family")
    unique([item["finding_id"] for item in findings], "findings")
    return {**value, "schema_version": 1, "pull_request": int(value["pull_request"])}


@dataclass(frozen=True)
class CandidateReadRequest:
    path: str
    side: str
    revision: str


@dataclass(frozen=True)
class CandidateReadSummary:
    path: str
    side: str
    revision: str
    present: bool
    mode: str | None
    oid: str | None


@dataclass(frozen=True)
class CandidateRead:
    path: str
    side: str
    revision: str
    present: bool
    mode: str | None
    kind: str | None
    oid: str | None
    data: bytes | None

    def summary(self) -> CandidateReadSummary:
        return CandidateReadSummary(
            self.path, self.side, self.revision, self.present, self.mode, self.oid)


@dataclass(frozen=True)
class CandidatePathChange:
    path: str
    base_present: bool
    base_mode: str | None
    base_kind: str | None
    base_oid: str | None
    head_present: bool
    head_mode: str | None
    head_kind: str | None
    head_oid: str | None
    required_sides: tuple[str, ...] = ()

    def _present(self, side: str) -> bool:
        return self.base_present if side == "base" else self.head_present

    def _mode(self, side: str) -> str | None:
        return self.base_mode if side == "base" else self.head_mode

    def _kind(self, side: str) -> str | None:
        return self.base_kind if side == "base" else self.head_kind

    def _supports_blob_read(self, side: str) -> bool:
        return self._present(side) and self._kind(side) == "blob" and self._mode(side) in REVIEW_BLOB_MODES

    def _default_required_sides(self) -> tuple[str, ...]:
        changed = (self.base_present != self.head_present or
                   self.base_mode != self.head_mode or
                   self.base_kind != self.head_kind or
                   self.base_oid != self.head_oid)
        require(changed, "candidate path requirement is unchanged")
        if not self.base_present:
            require(self._supports_blob_read("head"), "unsupported added candidate object")
            return ("head",)
        if not self.head_present:
            require(self._supports_blob_read("base"), "unsupported deleted candidate object")
            return ("base",)
        require(self._supports_blob_read("head"), "unsupported candidate head object")
        if self.base_kind != "blob" or self.base_mode not in REVIEW_BLOB_MODES:
            raise ReviewError("unsupported candidate base object")
        if self.base_mode != self.head_mode and self.base_oid == self.head_oid:
            return ("base", "head")
        return ("head",)

    def required_reads(self) -> tuple[str, ...]:
        default = self._default_required_sides()
        if not self.required_sides:
            return default
        requested = tuple(side for side in REVIEW_SIDES if side in set(self.required_sides))
        require(set(default) <= set(requested), "candidate path coverage weakened")
        require(requested == default or requested == ("base", "head"),
                "unsupported explicit candidate path coverage")
        for side in requested:
            require(self._supports_blob_read(side), "unsupported explicitly required candidate object")
        return requested


@dataclass(frozen=True)
class CandidateBinding:
    resolved_root: str
    base: str
    head: str


@dataclass(frozen=True)
class CandidateCoverage:
    resolved_root: str
    base: str
    head: str
    reads: tuple[CandidateReadSummary, ...]


def validate_candidate_read_request(value: Any, *, base_sha: str, head_sha: str) -> CandidateReadRequest:
    base_sha = sha(base_sha)
    head_sha = sha(head_sha)
    path = repo_path(field_value(value, "path"), "candidate path")
    side = review_side(field_value(value, "side"))
    revision = sha(field_value(value, "revision"))
    require(revision == (base_sha if side == "base" else head_sha), "wrong candidate revision")
    return CandidateReadRequest(path, side, revision)


def validate_candidate_read(value: Any, *, base_sha: str, head_sha: str) -> CandidateRead:
    request = validate_candidate_read_request(value, base_sha=base_sha, head_sha=head_sha)
    present = field_value(value, "present")
    require(type(present) is bool, "candidate presence must be Boolean")
    mode = field_value(value, "mode")
    kind = field_value(value, "kind")
    oid = field_value(value, "oid")
    data = field_value(value, "data")
    if present:
        mode = git_mode(mode, "candidate Git mode", regular_only=True)
        require(kind == "blob", "candidate source must be a regular Git blob")
        oid = sha(oid)
        require(isinstance(data, (bytes, bytearray)), "candidate source bytes are required")
        data = bytes(data)
        actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        require(actual == oid, "candidate source bytes differ from the selected object")
    else:
        require(mode is None and kind is None and oid is None and data is None,
                "candidate absence cannot invent mode, kind, object or bytes")
    return CandidateRead(request.path, request.side, request.revision,
                         present, mode, kind, oid, data)


def validate_candidate_read_summary(value: Any, *, base_sha: str, head_sha: str) -> CandidateReadSummary:
    request = validate_candidate_read_request(value, base_sha=base_sha, head_sha=head_sha)
    present = field_value(value, "present")
    require(type(present) is bool, "candidate summary presence must be Boolean")
    mode = field_value(value, "mode")
    oid = field_value(value, "oid")
    if present:
        mode = git_mode(mode, "candidate summary mode", regular_only=True)
        oid = sha(oid)
    else:
        require(mode is None and oid is None,
                "candidate summary absence cannot invent mode or object")
    return CandidateReadSummary(request.path, request.side, request.revision, present, mode, oid)


def validate_candidate_change(value: Any) -> CandidatePathChange:
    path = repo_path(field_value(value, "path"), "candidate changed path")

    def parse(prefix: str):
        present = field_value(value, prefix + "_present")
        require(type(present) is bool, f"{prefix} candidate presence must be Boolean")
        mode = field_value(value, prefix + "_mode")
        kind = field_value(value, prefix + "_kind")
        oid = field_value(value, prefix + "_oid")
        if present:
            mode = git_mode(mode, prefix + " candidate mode")
            require(isinstance(kind, str) and bool(kind.strip()),
                    f"invalid {prefix} candidate kind")
            oid = sha(oid)
        else:
            require(mode is None and kind is None and oid is None,
                    f"{prefix} candidate absence cannot invent identity")
        return present, mode, kind, oid

    required_sides = field_value(value, "required_sides")
    if required_sides is _MISSING:
        expected = ()
    else:
        require(isinstance(required_sides, (tuple, list)), "candidate required_sides must be a list")
        expected = tuple(review_side(item, "candidate required side") for item in required_sides)
        unique(expected, "candidate required sides")
    base_present, base_mode, base_kind, base_oid = parse("base")
    head_present, head_mode, head_kind, head_oid = parse("head")
    return CandidatePathChange(
        path, base_present, base_mode, base_kind, base_oid,
        head_present, head_mode, head_kind, head_oid, expected)


def candidate_coverage(report) -> CandidateCoverage | None:
    require(type(report) is _ReviewReport, "actual immutable review report required")
    require(report.completed is True and report.read_only is True and report.role == "code-review",
            "review report is not a completed immutable review")
    root = report.candidate_root
    base = report.candidate_base
    reads = report.candidate_reads
    if base is None:
        require(root is None and reads == (), "review report has unexpected candidate coverage state")
        return None
    root = resolved_repo_root(root, "candidate coverage root", must_exist=False)
    base = sha(base)
    head = sha(report.head)
    require(type(reads) is tuple, "candidate coverage must be an immutable tuple")
    require(all(type(item) is CandidateReadSummary for item in reads),
            "candidate coverage rows must be exact candidate read summaries")
    validated = tuple(validate_candidate_read_summary(item, base_sha=base, head_sha=head) for item in reads)
    unique([(item.path, item.side) for item in validated], "candidate read coverage")
    return CandidateCoverage(root, base, head, validated)


def require_candidate_path_coverage(report, changes, *, base_sha: str, head_sha: str,
                                    resolved_root: str) -> CandidateCoverage:
    base_sha = sha(base_sha)
    head_sha = sha(head_sha)
    resolved_root = resolved_repo_root(
        resolved_root, "expected candidate coverage root", must_exist=True)
    coverage = candidate_coverage(report)
    require(coverage is not None, "review report has no trusted candidate path coverage")
    require(coverage.resolved_root == resolved_root,
            "review candidate path coverage root mismatch")
    require((coverage.base, coverage.head) == (base_sha, head_sha),
            "review candidate path coverage pair mismatch")
    require(isinstance(changes, (tuple, list)), "candidate path requirements must be a list")
    expected = tuple(validate_candidate_change(item) for item in changes)
    unique([item.path for item in expected], "candidate path requirements")
    observed = {(item.path, item.side): item for item in coverage.reads}
    for change in expected:
        for side in change.required_reads():
            item = observed.get((change.path, side))
            require(item is not None and item.present is True,
                    f"missing candidate path coverage: {change.path} [{side}]")
            mode = change.base_mode if side == "base" else change.head_mode
            oid = change.base_oid if side == "base" else change.head_oid
            require((item.mode, item.oid) == (mode, oid),
                    f"candidate path coverage drifted: {change.path} [{side}]")
    return coverage


@dataclass(frozen=True)
class Obligation:
    subject: str
    family: str
    member: str
    role: str
    producer: str
    consumer: str
    representation: str
    revalidation: str
    probe: str
    profile: str
    evidence: tuple[str, ...]
    inputs: tuple[str, ...]
    kind: str = "host"
    execution_inputs: tuple[str, ...] = ()

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.subject, self.family, self.member

    @property
    def source_inputs(self) -> tuple[str, ...]:
        return self.execution_inputs or self.inputs


def validate_members(members: tuple[Obligation, ...]) -> None:
    require(0 < len(members) <= MAX_MEMBERS, "finite member budget exceeded or empty")
    roles = {}
    for member in members:
        require(isinstance(member, Obligation), "invalid obligation")
        require(all(isinstance(value, str) and bool(value) for value in (
            member.subject, member.family, member.member, member.role, member.producer,
            member.consumer, member.representation, member.revalidation,
            member.probe, member.profile, member.kind)),
            "incomplete production/evidence mapping")
        require(member.family in FAMILIES and member.role in FAMILIES[member.family],
                "unknown member role")
        require(member.kind in EVIDENCE_KINDS, "unknown obligation evidence kind")
        require(member.evidence and member.inputs, "incomplete production/evidence mapping")
        require(all(isinstance(paths, tuple) and
                    all(isinstance(path, str) and bool(path) for path in paths)
                    for paths in (member.inputs, member.execution_inputs)),
                "invalid immutable candidate inputs")
        require(set(member.inputs) <= set(member.source_inputs),
                "execution closure omits semantic candidate inputs")
        roles.setdefault((member.subject, member.family), set()).add(member.role)
    unique([member.identity for member in members], "members")
    for (_, family), found in roles.items():
        require(found == set(FAMILIES[family]), f"incomplete {family} roles")


@dataclass(frozen=True)
class Observation:
    obligation: Obligation
    revision: str
    tool_revision: str
    source_objects: tuple[tuple[str, str], ...]
    verdict: str
    evidence: tuple[str, ...]
    detail: str
    checks: int
    kind: str | None
    blocked_by: tuple[str, ...] = ()

    def validate(self) -> None:
        sha(self.revision)
        sha(self.tool_revision)
        require(isinstance(self.verdict, str)
                and self.verdict in {"satisfied", "contract-violation", "unavailable"},
                "unknown observation verdict")
        require(type(self.checks) is int and self.checks >= 0, "invalid check count")
        require(isinstance(self.obligation, Obligation), "invalid observation obligation")
        require((self.kind is None and self.verdict == "unavailable") or
                (isinstance(self.kind, str) and self.kind in EVIDENCE_KINDS
                 and self.kind == self.obligation.kind),
                "wrong or unknown evidence kind for obligation")
        require(isinstance(self.detail, str) and bool(self.detail.strip())
                and len(self.detail) <= MAX_DETAIL, "invalid observation detail")
        require(isinstance(self.evidence, (tuple, list))
                and all(isinstance(item, str) for item in self.evidence),
                "invalid evidence classes")
        unique(self.evidence, "evidence classes")
        require(set(self.evidence) == set(self.obligation.evidence),
                "missing or unrelated evidence class")
        require(isinstance(self.source_objects, (tuple, list)) and all(
            isinstance(item, (tuple, list)) and len(item) == 2 and isinstance(item[0], str)
            for item in self.source_objects), "invalid source objects")
        unique([path for path, _ in self.source_objects], "source objects")
        require(set(path for path, _ in self.source_objects) == set(self.obligation.source_inputs),
                "missing or unrelated source evidence")
        for _, oid in self.source_objects:
            sha(oid)
        require(self.checks == 0 if self.verdict == "unavailable" else self.checks > 0,
                "unavailable evidence requires zero checks; a contract requires executed checks")
        require(isinstance(self.blocked_by, (tuple, list)) and len(self.blocked_by) <= MAX_MEMBERS
                and all(isinstance(item, str) and bool(item.strip()) for item in self.blocked_by),
                "invalid prerequisite members")
        unique(self.blocked_by, "prerequisite members")
        require(not self.blocked_by or
                (self.verdict == "unavailable" and self.kind == "parsed"
                 and self.obligation.family == "generated"),
                "prerequisite failure must be an unavailable generated observation")


@dataclass(frozen=True)
class Finding:
    id: str
    subject: str
    family: str
    member: str
    origin: str
    source_path: str
    review_id: str


@dataclass(frozen=True)
class ReviewFact:
    id: str
    head: str
    actor: str
    state: str
    submitted_at: str
    body: str
    comments: tuple[tuple[str, str, str], ...]
    unresolved_threads: tuple[str, ...] = ()


@dataclass(frozen=True)
class Triage:
    """A coordinator decision over a complete, actually observed review."""
    fact: ReviewFact
    outcome: str
    findings: tuple[Finding, ...] = ()

    def validate(self) -> None:
        require(self.outcome in {"clean", "changes-requested", "untriaged", "dismissed"},
                "unknown coordinator triage")
        require(len(self.findings) <= MAX_FINDINGS, "review finding budget exceeded")
        sha(self.fact.head)
        timestamp(self.fact.submitted_at)
        unique([item.id for item in self.findings], "accepted findings")
        for finding in self.findings:
            require(finding.origin == self.fact.head and finding.review_id == self.fact.id,
                    "finding has wrong review origin")
        if self.outcome == "clean":
            require(not self.findings and not self.fact.unresolved_threads
                    and self.fact.state in {"COMMENTED", "APPROVED"},
                    "clean triage contradicts review facts")
        if self.outcome == "dismissed":
            require(self.fact.state == "DISMISSED", "dismissed triage requires an actual dismissal")


@dataclass(frozen=True)
class Disposition:
    review_id: str
    held_head: str
    coordinator: str
    action: str
    reason: str


@dataclass(frozen=True)
class _ReviewReport:
    """Validated local values, not a runtime-owned record or signed receipt."""
    task: str
    owner: str
    role: str
    head: str
    subjects: frozenset[str]
    completed: bool
    read_only: bool
    actions: frozenset[str]
    files: int
    findings: tuple[Finding, ...]
    started_at: str
    completed_at: str
    candidate_root: str | None = None
    candidate_base: str | None = None
    candidate_reads: tuple[CandidateReadSummary, ...] = ()


@dataclass
class RoundState:
    consecutive: int = 0
    seen: set[str] = field(default_factory=set)
    hold: tuple[str, str] | None = None
    handoffs: list[dict] = field(default_factory=list)
    dispositions: set[tuple[str, str]] = field(default_factory=set)
    events: list[Triage] = field(default_factory=list)
    count_from: int = 0

    def observe(self, review: Triage) -> None:
        review.validate()
        if review.fact.id in self.seen:
            index = next(i for i, item in enumerate(self.events) if item.fact.id == review.fact.id)
            previous = self.events[index]
            require((review.fact.head, review.fact.actor, timestamp(review.fact.submitted_at))
                    == (previous.fact.head, previous.fact.actor, timestamp(previous.fact.submitted_at)),
                    "review identity changed")
            require(review.fact != previous.fact or
                    (previous.outcome == "untriaged" and review.outcome != "untriaged"),
                    "duplicate finalized review or unchanged provisional replay")
            self.events[index] = review
        else:
            if self.events:
                previous = self.events[-1].fact
                require((timestamp(review.fact.submitted_at), review.fact.id)
                        >= (timestamp(previous.submitted_at), previous.id),
                        "review chronology moved backwards")
            self.events.append(review)
            self.seen.add(review.fact.id)
        self._refresh()

    def _refresh(self) -> None:
        prior = {item["review_id"]: item for item in self.handoffs}
        self.handoffs[:] = [
            {**prior[item.fact.id], "findings": [finding.id for finding in item.findings]}
            for item in self.events[:self.count_from]
            if item.fact.id in prior and item.outcome == "changes-requested"
        ]
        held = self.hold
        consecutive = 0
        for item in self.events[self.count_from:]:
            if item.outcome == "clean":
                consecutive = 0
            elif item.outcome == "changes-requested" or self.fact_requests(item):
                consecutive += 1
                if consecutive == 3:
                    if self.hold is None:
                        self.hold = item.fact.id, item.fact.head
                    break
                if item.outcome != "untriaged":
                    self.handoffs.append({
                        "review_id": item.fact.id, "head": item.fact.head,
                        "consecutive": consecutive,
                        "findings": [finding.id for finding in item.findings],
                    })
            if held is not None and item.fact.id == held[0]:
                break
        self.consecutive = 3 if held is not None else consecutive

    @staticmethod
    def fact_requests(review):
        return review.fact.state == "CHANGES_REQUESTED"

    def dispose(self, decision: Disposition, coordinator: str) -> None:
        binding = decision.review_id, decision.held_head
        require(self.hold == binding and binding not in self.dispositions,
                "stale, wrong-head or reused architecture disposition")
        require(decision.coordinator == coordinator, "wrong disposition owner")
        require(decision.action in {"redesign", "decompose", "retain-with-evidence"}
                and isinstance(decision.reason, str) and bool(decision.reason.strip()),
                "architecture decision requires an action and reason")
        self.dispositions.add(binding)
        self.hold = None
        self.consecutive = 0
        self.count_from = len(self.events)


@dataclass
class ReviewLease:
    task: str
    owner: str
    head: str
    scope: frozenset[str]
    started: float
    deadline: float
    max_files: int
    finished: bool = False
    outcome: str | None = None


class ReviewOwnership:
    """Small in-process index owned/shared by the existing coordinator."""

    def __init__(self):
        self.records = {}

    def reserve(self, session):
        require(session.identity is not None, "ownership requires frozen PR identity")
        for identity, head, scope, active in self.records.values():
            require(not (active and
                         (identity[:2] == session.identity[:2] or head == session.head)),
                    "duplicate/overlapping review ownership")
        self.records[id(session)] = session.identity, session.head, session.scope, True

    def finish(self, session):
        identity, head, scope, _ = self.records[id(session)]
        self.records[id(session)] = identity, head, scope, False


class ReviewSession:
    """Adapter around the coordinator's existing task/tool calls, not a backend.

    runtime.start/read/stop are the actual CLI task operations.
    They are supplied by trusted orchestration code, never by request JSON.
    There is no polling: finish is called after the task-completion event.
    Coordinator-authored work can have one actual coordinator/implementer.
    The reviewer must still be independent of both roles.
    """

    def __init__(self, coordinator: str, implementer: str, scope: frozenset[str],
                 head: str, *, identity=None, owners=None, clock=time.monotonic, readers=None):
        require(all(isinstance(owner, str) and bool(owner.strip())
                    for owner in (coordinator, implementer)),
                "invalid coordinator or implementer identity")
        sha(head)
        require(isinstance(scope, (set, frozenset)) and 0 < len(scope) <= MAX_SUBJECTS
                and all(isinstance(item, str) and bool(item.strip()) for item in scope),
                "invalid accepted review scope")
        if identity is not None:
            require(isinstance(identity, tuple) and len(identity) == 3,
                    "invalid frozen PR identity")
            identifier(identity[0], r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", "repository")
            require(type(identity[1]) is int and identity[1] > 0, "invalid PR identity")
            sha(identity[2])
        self.coordinator = coordinator
        self.implementer = implementer
        self.scope = frozenset(scope)
        self.head = head
        self.identity = identity
        self.owners = owners
        self.clock = clock
        self.readers = dict(readers or {})
        require(set(self.readers) <= READ_ACTIONS, "unapproved reviewer tool binding")
        self.lease = None
        self.report = None
        self.local_findings: dict[str, Finding] = {}
        self.local_triage: dict[str, tuple[bool, str]] = {}
        self.rounds = RoundState()
        self.accepted: dict[str, Finding] = {}
        self.candidate_reads: dict[tuple[str, str], CandidateReadSummary] = {}
        self.candidate_binding: CandidateBinding | None = None

    def _candidate_reader(self):
        reader = self.readers.get("read-candidate")
        if reader is None or getattr(reader, "review_candidate_reader", False) is not True:
            return None
        preview = getattr(reader, "preview", None)
        describe = getattr(reader, "describe", None)
        require(callable(preview), "trusted candidate reader preview is unavailable")
        require(callable(describe), "trusted candidate reader description is unavailable")
        binding = CandidateBinding(
            resolved_repo_root(getattr(reader, "root", None),
                               "trusted candidate reader root", must_exist=True),
            sha(getattr(reader, "base", None)),
            sha(getattr(reader, "head", None)),
        )
        if self.candidate_binding is not None:
            require(binding == self.candidate_binding,
                    "trusted candidate reader root/base/head changed after review start")
        return reader, preview, describe, binding

    def begin(self, runtime, owner: str, *, duration=1200, max_files=MAX_REVIEW_FILES):
        require(self.lease is None, "duplicate or overlapping reviewer ownership")
        require(isinstance(owner, str) and bool(owner.strip())
                and owner not in {self.coordinator, self.implementer},
                "reviewer ownership overlaps")
        require(type(duration) is int and 0 < duration <= MAX_REVIEW_SECONDS
                and type(max_files) is int and 0 < max_files <= MAX_REVIEW_FILES,
                "invalid review bounds")
        candidate_reader = self._candidate_reader()
        self.candidate_binding = None
        if candidate_reader is not None:
            _, _, _, binding = candidate_reader
            require(binding.head == self.head,
                    "trusted candidate reader head differs from the active review")
            if self.identity is not None:
                require(binding.base == self.identity[2],
                        "trusted candidate reader base differs from the frozen review identity")
            self.candidate_binding = binding
        if self.owners is not None:
            self.owners.reserve(self)
        try:
            task = runtime.start(
                role="code-review", owner=owner, candidate=self.head,
                subjects=tuple(sorted(self.scope)), actions=tuple(sorted(READ_ACTIONS)),
                duration=duration, max_files=max_files, max_findings=MAX_FINDINGS,
            )
            require(isinstance(task, str) and bool(task.strip()),
                    "runtime task identity must be a nonblank string")
        except Exception:
            if self.owners is not None:
                self.owners.records.pop(id(self), None)
            raise
        started = self.clock()
        self.lease = ReviewLease(task, owner, self.head, self.scope,
                                 started, started + duration, max_files)
        return task

    def read_action(self, action: str, *args, **kwargs):
        require(isinstance(action, str) and action in READ_ACTIONS, "reviewer action is prohibited")
        require(self.lease is not None and not self.lease.finished
                and self.clock() <= self.lease.deadline, "review lease is not active")
        require(action in self.readers, "reviewer tool is not bound")
        reader = self.readers[action]
        if action != "read-candidate":
            return reader(*args, **kwargs)
        candidate_reader = self._candidate_reader()
        if candidate_reader is None:
            return reader(*args, **kwargs)
        _, preview, describe, binding = candidate_reader
        require(binding.head == self.head,
                "trusted candidate reader head differs from the active review")
        if self.identity is not None:
            require(binding.base == self.identity[2],
                    "trusted candidate reader base differs from the frozen review identity")
        request = validate_candidate_read_request(
            preview(*args, **kwargs), base_sha=binding.base, head_sha=binding.head)
        logical_paths = {item.path for item in self.candidate_reads.values()}
        require(request.path in logical_paths or len(logical_paths) < self.lease.max_files,
                "review file budget exceeded")
        expected = validate_candidate_read_summary(
            describe(*args, **kwargs), base_sha=binding.base, head_sha=binding.head)
        observed = validate_candidate_read(
            reader(*args, **kwargs), base_sha=binding.base, head_sha=binding.head)
        require((observed.path, observed.side, observed.revision)
                == (request.path, request.side, request.revision),
                "candidate read result differs from the requested path or side")
        key = (observed.path, observed.side)
        summary = observed.summary()
        require(summary == expected, "candidate read result differs from the immutable selected object")
        require(key not in self.candidate_reads or self.candidate_reads[key] == summary,
                "candidate read result changed across duplicate observations")
        self.candidate_reads[key] = summary
        return observed

    def _read_task(self, runtime):
        lease = self.lease
        require(lease is not None and not lease.finished, "no active review task")
        result = runtime.read(lease.task)
        require(all(hasattr(result, field) for field in (
            "task", "owner", "role", "head", "subjects", "completed")),
            "incomplete runtime review record")
        require(isinstance(result.subjects, (tuple, list, set, frozenset))
                and len(result.subjects) == len(lease.scope)
                and all(isinstance(item, str) for item in result.subjects),
                "invalid runtime review scope")
        require(result.task == lease.task and result.owner == lease.owner
                and result.role == "code-review" and result.head == lease.head
                and frozenset(result.subjects) == lease.scope
                and type(result.completed) is bool, "wrong or stale runtime task observation")
        return result

    def _retire(self, result, outcome):
        require(result.completed is True, "runtime task is not terminal")
        require(timestamp(getattr(result, "started_at", None))
                <= timestamp(getattr(result, "completed_at", None)),
                "review task chronology is invalid")
        if outcome == "completed" and self.clock() > self.lease.deadline:
            outcome = "timed-out"
        if self.owners is not None:
            self.owners.finish(self)
        self.lease.finished = True
        self.lease.outcome = outcome
        return outcome

    def abort(self, runtime):
        result = self._read_task(runtime)
        if not result.completed:
            stop = getattr(runtime, "stop", None)
            require(callable(stop), "runtime stop capability is unavailable")
            stop(self.lease.task)
            result = self._read_task(runtime)
        return self._retire(result, "aborted")

    def finish(self, runtime) -> _ReviewReport:
        result = self._read_task(runtime)
        lease = self.lease
        require(result.completed is True, "runtime task is not terminal")
        if self.clock() > lease.deadline:
            self._retire(result, "timed-out")
            raise ReviewError("review exceeded duration bound")
        require(all(hasattr(result, field) for field in (
            "read_only", "actions", "files", "findings")),
            "incomplete runtime review record")
        require(result.read_only is True, "writable task result")
        require(isinstance(result.actions, (tuple, list, set, frozenset))
                and all(isinstance(action, str) and action in READ_ACTIONS
                        for action in result.actions)
                and {"read-candidate", "emit-report"} <= set(result.actions)
                and type(result.files) is int and 0 <= result.files <= lease.max_files <= MAX_REVIEW_FILES
                and isinstance(result.findings, (tuple, list))
                and len(result.findings) <= MAX_FINDINGS, "review action/budget violation")
        findings = tuple(result.findings)
        for finding in findings:
            require(isinstance(finding, Finding)
                    and all(isinstance(value, str) and bool(value.strip()) for value in (
                        finding.id, finding.subject, finding.family, finding.member,
                        finding.origin, finding.source_path, finding.review_id))
                    and finding.subject in self.scope
                    and finding.family in FAMILIES and finding.origin == lease.head
                    and finding.review_id == "local:" + str(lease.task),
                    "local finding has wrong task, head or scope")
        unique([finding.id for finding in findings], "local report findings")
        candidate_reader = self._candidate_reader()
        binding = self.candidate_binding if candidate_reader is not None else None
        report = _ReviewReport(
            str(lease.task), lease.owner, "code-review", lease.head, lease.scope, True, True,
            frozenset(result.actions), result.files,
            tuple(Finding(item.id, item.subject, item.family, item.member,
                          item.origin, item.source_path, item.review_id) for item in findings),
            getattr(result, "started_at", None), getattr(result, "completed_at", None),
            None if binding is None else binding.resolved_root,
            None if binding is None else binding.base,
            tuple(self.candidate_reads[key] for key in sorted(self.candidate_reads)))
        require(self._retire(report, "completed") == "completed", "review exceeded duration bound")
        self.local_findings = {finding.id: finding for finding in report.findings}
        self.report = report
        return report

    def triage_local(self, finding_id: str, *, accepted: bool, reason: str) -> None:
        require(self.report is not None and finding_id in self.local_findings
                and finding_id not in self.local_triage, "unknown or duplicate local finding triage")
        require(type(accepted) is bool and isinstance(reason, str) and bool(reason.strip()),
                "local finding triage requires a decision and reason")
        if accepted:
            self.accept(self.local_findings[finding_id])
        else:
            require(finding_id not in self.accepted, "rejected local finding was accepted")
        self.local_triage[finding_id] = accepted, reason

    def validate_local_triage(self) -> None:
        require(set(self.local_triage) == set(self.local_findings),
                "local report findings require complete triage")
        for finding_id, finding in self.local_findings.items():
            accepted, reason = self.local_triage[finding_id]
            require(type(accepted) is bool and isinstance(reason, str) and bool(reason.strip()),
                    "local finding triage requires a decision and reason")
            require(self.accepted.get(finding_id) == finding if accepted
                    else finding_id not in self.accepted,
                    "local finding triage differs from accepted sibling sweep")

    def advance(self, observed_head: str) -> None:
        """Preserve existing work without clearing a review or architecture hold."""
        require(self.lease is None or self.lease.finished,
                "cannot advance head during an active review lease")
        self.head = sha(observed_head)

    def accept(self, finding: Finding) -> None:
        require(finding.id not in self.accepted, "duplicate accepted finding")
        require(finding.subject in self.scope and finding.family in FAMILIES,
                "finding is outside accepted scope")
        sha(finding.origin)
        self.accepted[finding.id] = finding

    def triage(self, decision: Triage) -> None:
        for finding in decision.findings:
            require(finding.subject in self.scope and finding.family in FAMILIES,
                    "finding is outside accepted scope")
            require(finding.id not in self.accepted or self.accepted[finding.id] == finding,
                    "accepted finding binding changed")
        self.rounds.observe(decision)
        for finding in decision.findings:
            if finding.id not in self.accepted:
                self.accept(finding)
        for handoff in self.rounds.handoffs:
            handoff["findings"] = sorted(finding.id for finding in self.accepted.values()
                                         if finding.review_id == handoff["review_id"])

    def refresh_reviews(self, facts: tuple[ReviewFact, ...]) -> None:
        current = {item.fact.id: item.fact for item in self.rounds.events}
        for fact in facts:
            if fact.id in current and current[fact.id] != fact:
                self.triage(Triage(fact, "untriaged"))

    def review_state(self, facts, triage, *, pre_review_required):
        require(len({fact.id for fact in facts}) == len(facts), "duplicate remote review")
        self.refresh_reviews(facts)
        self.validate_local_triage()
        require(tuple(item.fact for item in triage) == facts,
                "missing triage or changed review content")
        require(tuple(self.rounds.events) == triage, "round state differs from observed triage")
        for item in triage:
            item.validate()
        if pre_review_required:
            require(self.report is not None and self.lease.finished
                    and self.lease.outcome == "completed",
                    "actual independent task observation required")
            require(self.owners is not None, "coordinator review ownership index required")
            if facts:
                require(timestamp(self.report.completed_at) < min(
                    timestamp(item.submitted_at) for item in facts),
                    "fresh pre-review did not precede the first remote review")
        ready = all(item.outcome != "untriaged" and not item.fact.unresolved_threads for item in triage)
        latest = triage[-1] if triage else None
        return ready, bool(ready and latest and latest.fact.head == self.head
                           and latest.outcome == "clean")


def assess_handoff(request: dict, members: tuple[Obligation, ...],
                   observations: tuple[Observation, ...], session: ReviewSession,
                   *, tool_revision: str, remote_reviews: tuple[ReviewFact, ...],
                   triage: tuple[Triage, ...], pre_review_required: bool) -> dict:
    request = validate_request(request)
    validate_members(members)
    sha(tool_revision)
    require(session.identity == (request["repository"], request["pull_request"], request["base_sha"]),
            "request repository/PR/base differs from the coordinator's frozen identity")
    require({subject_key(item) for item in request["subjects"]} == session.scope,
            "request omitted or added an accepted subject")
    require(request["candidate_sha"] == session.head, "stale candidate session")
    reviews_ready, clean = session.review_state(
        remote_reviews, triage, pre_review_required=pre_review_required)
    proposed = {item["finding_id"]: item for item in request["findings"]}
    require(set(proposed) == set(session.accepted), "missing or invented accepted finding")
    for finding in session.accepted.values():
        item = proposed[finding.id]
        require((subject_key(item), item["family"], item["reported_member"])
                == (finding.subject, finding.family, finding.member),
                "finding subject/member classification drift")
    index = {}
    for observation in observations:
        observation.validate()
        key = observation.obligation.identity, observation.revision
        require(key not in index, "duplicate member observation")
        require(observation.tool_revision == tool_revision, "wrong reviewed tool revision")
        index[key] = observation
    evidence_refs = {key: number for number, key in enumerate(sorted(index))}
    required = set()
    outcomes = []
    for member in members:
        relevant = [item for item in session.accepted.values()
                    if (item.subject, item.family) == (member.subject, member.family)]
        revisions = {request["candidate_sha"], *(item.origin for item in relevant)}
        for revision in revisions:
            required.add((member.identity, revision))
        after = index.get((member.identity, request["candidate_sha"]))
        require(after is not None and after.obligation == member,
                f"missing or wrong member evidence: {member.member}")
        require(after.verdict == "satisfied", f"candidate obligation failed: {member.member}")
        for finding in relevant:
            before = index.get((member.identity, finding.origin))
            require(before is not None and before.obligation == member, "origin evidence unavailable")
            if before.verdict == "unavailable":
                require(bool(before.blocked_by), "origin evidence unavailable")
                for blocker in before.blocked_by:
                    prerequisite = index.get(((member.subject, member.family, blocker), finding.origin))
                    require(prerequisite is not None and prerequisite.obligation.role == "owners"
                            and prerequisite.verdict == "contract-violation",
                            "origin prerequisite failure was not observed")
            require(finding.source_path in member.inputs or finding.member != member.member,
                    "reported finding has unrelated source evidence")
            outcomes.append({
                "finding_id": finding.id, "subject": member.subject,
                "family": member.family, "member": member.member,
                "origin_evidence": evidence_refs[(member.identity, finding.origin)],
                "candidate_evidence": evidence_refs[(member.identity, request["candidate_sha"])],
                "outcome": ("affected-fixed" if before.verdict == "contract-violation"
                            else "prerequisite-fixed" if before.blocked_by else "verified-unaffected"),
            })
    require(set(index) == required, "missing or unrelated test evidence")
    for finding in session.accepted.values():
        matching = [row for row in outcomes if row["finding_id"] == finding.id]
        require(any(row["member"] == finding.member for row in matching),
                "unknown reported member")
        require(any(row["member"] == finding.member and row["outcome"] == "affected-fixed"
                    for row in matching),
                "reported member has no affected-fixed origin evidence")
    held = session.rounds.hold
    source_refs, source_sets, evidence = {}, [], []
    for key in evidence_refs:
        item = index[key]
        objects = tuple(sorted((path, oid) for path, oid in item.source_objects))
        source_key = item.revision, item.tool_revision, objects
        if source_key not in source_refs:
            source_refs[source_key] = len(source_sets)
            source_sets.append({"revision": item.revision, "tool_revision": item.tool_revision,
                                "objects": objects})
        evidence.append({
            "subject": item.obligation.subject, "family": item.obligation.family,
            "member": item.obligation.member, "probe": item.obligation.probe,
            "profile": item.obligation.profile, "revision": item.revision,
            "tool_revision": item.tool_revision, "source_set": source_refs[source_key],
            "evidence": list(item.evidence), "kind": item.kind, "checks": item.checks,
            "verdict": item.verdict, "detail": item.detail, "blocked_by": list(item.blocked_by),
        })
    handoffs = [{
        **handoff, "findings": list(handoff["findings"]),
        "candidate_sha": session.head, "tool_revision": tool_revision,
        "outcome_refs": [number for number, row in enumerate(outcomes)
                         if row["finding_id"] in handoff["findings"]],
    } for handoff in session.rounds.handoffs]
    return {
        "schema_version": 1,
        "candidate_sha": session.head,
        "tool_revision": tool_revision,
        "scope": sorted(session.scope),
        "members": len(members),
        "outcomes": outcomes,
        "evidence": evidence,
        "source_sets": source_sets,
        "round_handoffs": handoffs,
        "architecture_hold": None if held is None else {"review_id": held[0], "head": held[1]},
        "new_narrow_work_allowed": held is None,
        "handoff_eligible": held is None and reviews_ready,
        "exact_head_review_clean": clean,
        "required_final_gates": ["copilot", "security", "candidate-Build", "master-Build",
                                 "remote-completion"],
        "publication": "persist-assigned-branch-as-ineligible-WIP" if held else "immediate",
        "merge_permission": False,
    }
